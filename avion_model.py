"""
Modelo de asignación de avión + consumo de combustible por ruta.

CÓMO FUNCIONA (v2 — agosto 2026)
--------------------------------
Antes: la ruta tenía UN avión fijo, elegido solo por distancia, y el consumo salía de
multiplicar un "consumo por hora" genérico por la duración estimada. Eso hacía que una
ruta de bajo tráfico (60 pax/vuelo) se estimara con un A320 lleno de 170 asientos.

Ahora hay tres capas:

1) FLOTA CALIBRADA (flota.json)
   Para cada tipo de avión: asientos, alcance práctico, y los coeficientes (a, b) de
   consumo_toneladas = a_t + b_kg_km * distancia_km / 1000, ajustados por regresión sobre
   la planilla real de consumo. El ajuste da MAPE < 1% en los tipos con
   muestra suficiente, así que extrapolar a rutas nuevas es seguro.

2) MATRIZ REAL POR RUTA (consumo_rutas.json)
   Consumo real, en toneladas por vuelo, para 183 pares origen-destino y hasta 23 tipos de
   avión. Cuando la celda (ruta, tipo) existe se usa el dato real; si no, se cae a los
   coeficientes del tipo. Los tipos que aparecen en la matriz de una ruta son, además, la
   evidencia de qué aviones se operan realmente ahí.

3) ESCALERA POR OCUPACIÓN
   El avión se elige según los PASAJEROS POR VUELO del mes que se esté mirando, no según
   la distancia. Cada tipo tiene un `pax_vuelo_max` (el promedio mensual de pax por vuelo
   que ese tipo puede sostener). Se toma el tipo MÁS CHICO cuyo `pax_vuelo_max` alcance la
   ocupación observada, entre los que tienen alcance suficiente para la ruta.

   Regla clave: por debajo de ~100 pax/vuelo NO se asume un A320 o un 737, se asume un
   Embraer 190. El `pax_vuelo_max` del E190 está fijado en 100 exactamente para eso.

Sigue siendo compatible con las rutas manuales del panel admin (register_manual_route),
que pisan cualquier estimación.
"""
import json
import math
import os

_FLOTA = None
_MATRIZ = None
_PRECOMPUTED = None
_CACHE = {}          # key -> resultado "por defecto" (sin ocupación conocida)
_CACHE_OCUP = {}     # (key, bucket_pax) -> resultado con ocupación
_MANUAL = {}         # key -> resultado manual; pisa todo lo demás

JETA1_KG_PER_M3 = 804.0

# Factor de ocupación de referencia que asume la planilla de consumo. El consumo se corrige
# contra este valor cuando la ocupación observada es distinta.
LF_REFERENCIA = 0.82

# Elasticidad peso -> consumo: ~0.8% más consumo por cada 1% más de peso al despegue.
FUEL_WEIGHT_ELASTICITY = 0.8

# Fracción del peso de despegue que corresponde a pasajeros + equipaje.
PAX_WEIGHT_SHARE = {'regional': 0.16, 'narrow': 0.14, 'wide': 0.10, 'piston': 0.20}

_VEL_CRUCERO = {'regional': 800.0, 'narrow': 830.0, 'wide': 890.0, 'piston': 330.0}


def _base_dir():
    return os.path.dirname(__file__)


def _load_json(name, default):
    path = os.path.join(_base_dir(), name)
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (ValueError, OSError):
        return default


def get_flota():
    """{codigo: {nombre, asientos, pax_vuelo_max, alcance_km, fuselaje, a_t, b_kg_km, ...}}"""
    global _FLOTA
    if _FLOTA is None:
        _FLOTA = _load_json('flota.json', {})
    return _FLOTA


def get_matriz():
    """{"A|B": {codigo_avion: toneladas_por_vuelo}} (sin la clave _meta)"""
    global _MATRIZ
    if _MATRIZ is None:
        data = _load_json('consumo_rutas.json', {})
        data.pop('_meta', None)
        _MATRIZ = data
    return _MATRIZ


def _load_precomputed():
    global _PRECOMPUTED
    if _PRECOMPUTED is None:
        _PRECOMPUTED = _load_json('aviones_precomputado.json', {})
    return _PRECOMPUTED


def _key(o, d):
    return '|'.join(sorted([o.strip(), d.strip()]))


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ------------------------------------------------------------------ consumo por tipo

def consumo_toneladas(codigo, dist_km, key=None):
    """Consumo de UN tramo, en toneladas, para un tipo de avión y una ruta.
    Devuelve (toneladas, fuente) con fuente en {'real', 'ajuste'} o (None, None)."""
    matriz = get_matriz()
    if key and key in matriz and codigo in matriz[key]:
        try:
            return float(matriz[key][codigo]), 'real'
        except (TypeError, ValueError):
            pass
    ficha = get_flota().get(codigo)
    if not ficha or ficha.get('b_kg_km') is None:
        return None, None
    tons = (ficha.get('a_t') or 0.0) + (ficha['b_kg_km'] * dist_km / 1000.0)
    return max(tons, 0.0), 'ajuste'


# ------------------------------------------------------------------ escalera

# Margen dentro del cual dos tipos se consideran equivalentes en capacidad. Sirve para
# desempatar a favor del avión que la planilla registra como realmente operado en la ruta.
TOLERANCIA_CAPACIDAD = 0.15


def tipos_observados(key):
    """Tipos que la planilla real registra para esa ruta (evidencia de lo que se opera)."""
    matriz = get_matriz()
    flota = get_flota()
    if not key or key not in matriz:
        return set()
    return {c for c in matriz[key] if c in flota and flota[c].get('en_escalera')}


def _escalera(dist_km, key=None):
    """Tipos candidatos para la ruta, de menor a mayor capacidad.

    Candidata es TODA la flota con alcance suficiente, no solo lo que la planilla haya
    registrado en esa ruta. Si se restringiera a lo observado, una ruta que en la planilla
    solo aparece con B738 nunca podría bajar a un E190 aunque su ocupación real lo pida,
    que es justamente lo que este modelo tiene que poder hacer."""
    flota = get_flota()

    candidatos = [c for c, f in flota.items()
                  if f.get('en_escalera') and f.get('b_kg_km') is not None
                  and (f.get('alcance_km') or 0) >= dist_km * 1.05]

    # Si ningún avión de la flota declara alcance para la ruta, no filtrar por alcance
    # (mejor una estimación con el de mayor alcance que ninguna).
    if not candidatos:
        candidatos = [c for c, f in flota.items()
                      if f.get('en_escalera') and f.get('b_kg_km') is not None]

    # Los tipos observados en esta ruta entran siempre, aunque el alcance nominal no dé
    # (si volaron ahí, es que podían).
    for c in tipos_observados(key):
        if c not in candidatos and flota[c].get('b_kg_km') is not None:
            candidatos.append(c)

    return sorted(candidatos, key=lambda c: (flota[c]['pax_vuelo_max'], flota[c]['asientos']))


def seleccionar_avion(dist_km, pax_por_vuelo=None, key=None):
    """Código del tipo de avión que corresponde a esa ocupación y distancia.

    Toma el tipo MÁS CHICO cuyo pax_vuelo_max cubra la ocupación observada. Entre tipos de
    capacidad equivalente (dentro de TOLERANCIA_CAPACIDAD) prefiere el que la planilla
    registra como operado en esa ruta."""
    orden = _escalera(dist_km, key)
    if not orden:
        return None
    flota = get_flota()
    observados = tipos_observados(key)

    if not pax_por_vuelo or pax_por_vuelo <= 0:
        # Sin dato de ocupación: si hay evidencia real, el de capacidad mediana entre los
        # observados; si no, el más chico que pueda hacer la ruta.
        if observados:
            obs_orden = sorted(observados, key=lambda c: flota[c]['pax_vuelo_max'])
            return obs_orden[len(obs_orden) // 2]
        return orden[0]

    aptos = [c for c in orden if flota[c]['pax_vuelo_max'] >= pax_por_vuelo]
    if not aptos:
        return orden[-1]  # nadie alcanza: el más grande disponible

    minimo = flota[aptos[0]]['pax_vuelo_max']
    techo = minimo * (1 + TOLERANCIA_CAPACIDAD)
    empatados = [c for c in aptos if flota[c]['pax_vuelo_max'] <= techo]
    for c in empatados:
        if c in observados:
            return c
    return aptos[0]


# ------------------------------------------------------------------ ajuste por ocupación

def ajuste_por_ocupacion(codigo, pax_por_vuelo):
    """Corrección multiplicativa sobre el consumo, por diferencia de peso entre la
    ocupación observada y la de referencia. Devuelve (factor, load_factor_real)."""
    ficha = get_flota().get(codigo)
    if not ficha or not pax_por_vuelo or pax_por_vuelo <= 0:
        return 1.0, None
    asientos = ficha.get('asientos') or 0
    if asientos <= 0:
        return 1.0, None
    lf = min(pax_por_vuelo / asientos, 1.0)
    share = PAX_WEIGHT_SHARE.get(ficha.get('fuselaje'), 0.14)
    delta_peso = share * (lf - LF_REFERENCIA) / max(LF_REFERENCIA, 0.01)
    factor = 1.0 + FUEL_WEIGHT_ELASTICITY * delta_peso
    return max(0.75, min(1.25, factor)), lf


def _duracion_estimada(dist_km, ficha):
    """Duración bloque aproximada (h). Informativa: el consumo ya no depende de esto."""
    v = _VEL_CRUCERO.get((ficha or {}).get('fuselaje'), 830.0)
    return 0.35 + dist_km / v


# ------------------------------------------------------------------ cálculo principal

def _armar_resultado(dist, codigo, pax_por_vuelo, key):
    ficha = get_flota().get(codigo)
    if not ficha:
        return None
    tons, origen_dato = consumo_toneladas(codigo, dist, key)
    if tons is None:
        return None

    factor, lf = ajuste_por_ocupacion(codigo, pax_por_vuelo)
    kg = tons * 1000.0 * factor

    return {
        'avion': ficha['nombre'],
        'avion_codigo': codigo,
        'fuente': ('Real (planilla de consumo YPF)' if origen_dato == 'real'
                   else 'Estimado (curva calibrada del tipo)'),
        'asientos': ficha['asientos'],
        'distancia_km': round(dist, 1),
        'velocidad_crucero_kmh': _VEL_CRUCERO.get(ficha.get('fuselaje'), 830.0),
        'consumo_hora_kg': None,
        'duracion_h': round(_duracion_estimada(dist, ficha), 2),
        'consumo_total_kg': round(kg, 1),
        'consumo_total_m3': round(kg / JETA1_KG_PER_M3, 3),
        'consumo_base_t': round(tons, 3),
        'factor_ocupacion': round(factor, 4),
        'load_factor': round(lf, 3) if lf is not None else None,
        'pax_por_vuelo': round(pax_por_vuelo, 1) if pax_por_vuelo else None,
        'dato_consumo': origen_dato,
    }


def opciones_ruta(o, d, coords, dist=None, key=None):
    """Lista compacta de tipos candidatos para la ruta, para que el frontend elija en vivo
    según la ocupación del mes seleccionado, sin volver al servidor.

    Formato: [codigo, nombre, asientos, pax_vuelo_max, consumo_kg_base, fuselaje, es_real]
    ordenado de menor a mayor capacidad."""
    key = key or _key(o, d)
    if dist is None:
        if o not in coords or d not in coords:
            return []
        dist = haversine(*coords[o], *coords[d])
    flota = get_flota()
    out = []
    for c in _escalera(dist, key):
        tons, origen = consumo_toneladas(c, dist, key)
        if tons is None:
            continue
        f = flota[c]
        out.append([c, f['nombre'], f['asientos'], f['pax_vuelo_max'],
                    round(tons * 1000.0, 1), f.get('fuselaje', 'narrow'),
                    1 if origen == 'real' else 0])
    return out


def opciones_para_rango(o, d, coords, pax_min=None, pax_max=None, dist=None, key=None,
                        max_opciones=6):
    """Tipos que efectivamente podrían elegirse dado el rango de ocupación de esa ruta.

    Sin este recorte la lista traería los 22 tipos de la flota para cada una de las ~260
    rutas, inflando /api/data sin ningún uso real (un 747 nunca va a ser la respuesta para
    Aeroparque-Rosario).

    Se protegen de ese recorte, además de la muestra por ocupación:
    - un tipo no regional (para que el switch "avión regional si <100 pax/vuelo" del mapa
      siempre tenga con qué comparar, aunque la ruta nunca haya tenido esa ocupación);
    - TODOS los tipos que tienen dato real en la planilla para esta ruta específica. Si no
      se protegieran, el selector manual del panel de detalle podía terminar mostrando
      "estimado" para una combinación que en realidad tiene consumo medido — simplemente
      porque no entró en la muestra recortada por ocupación.

    Formato compacto: [codigo, pax_vuelo_max, consumo_kg_base, es_real].
    El nombre, los asientos y el fuselaje salen de flota.json, que el frontend ya recibe
    una sola vez — repetirlos por ruta serían ~150 KB de payload duplicado."""
    key = key or _key(o, d)
    if dist is None:
        if o not in coords or d not in coords:
            return []
        dist = haversine(*coords[o], *coords[d])

    flota = get_flota()
    orden = _escalera(dist, key)
    if not orden:
        return []

    # Muestreo: los bordes del rango y los saltos de la escalera que caen adentro.
    muestras = set()
    lo = pax_min if (pax_min and pax_min > 0) else None
    hi = pax_max if (pax_max and pax_max > 0) else None
    if lo:
        muestras.add(lo)
    if hi:
        muestras.add(hi)
    for c in orden:
        v = flota[c]['pax_vuelo_max']
        if (lo is None or v >= lo) and (hi is None or v <= hi):
            muestras.add(v)
    if not muestras:
        muestras.add(None)

    codigos = []
    for p in sorted(muestras, key=lambda x: (x is None, x)):
        c = seleccionar_avion(dist, p, key)
        if c and c not in codigos:
            codigos.append(c)
    c_def = seleccionar_avion(dist, None, key)
    if c_def and c_def not in codigos:
        codigos.append(c_def)

    # Garantizar al menos un tipo NO regional en la base, aunque la ocupación histórica de
    # la ruta nunca haya llegado a ese umbral. Sin esto, una ruta de tráfico bajo (ej.
    # Aeroparque-Río Cuarto, siempre por debajo de 100 pax/vuelo) le llegaría al frontend con
    # SOLO tipos regionales — y el switch "avión regional si <100 pax/vuelo" del mapa no
    # tendría con qué comparar al apagarlo, porque no existiría ninguna alternativa.
    if not any(flota[c].get('fuselaje') != 'regional' for c in codigos):
        no_regionales = [c for c in orden if flota[c].get('fuselaje') != 'regional']
        if no_regionales:
            codigos.append(no_regionales[0])  # el más chico no-regional que hace la ruta

    # Esta es la BASE: lo que decide la asignación automática según ocupación. Se recorta acá
    # (extremos + reparto parejo) ANTES de sumar los tipos con dato real, para que una ruta
    # con muchos años de datos reales (ej. Aeroparque-Córdoba, con 7 tipos medidos) nunca le
    # gane el lugar a CRJ2 o E190 en el recorte solo por tener más "peso documental".
    codigos.sort(key=lambda c: (flota[c]['pax_vuelo_max'], flota[c]['asientos']))
    if max_opciones and len(codigos) > max_opciones:
        paso = (len(codigos) - 1) / (max_opciones - 1)
        idx = sorted({int(round(i * paso)) for i in range(max_opciones)})
        codigos = [codigos[i] for i in idx]

    # Sumar, SIN volver a recortar, los tipos que tienen dato real medido para esta ruta
    # específica. Si no se agregaran, el selector manual del panel de detalle podía marcar
    # como "estimado" una combinación que en realidad tiene consumo real en la planilla,
    # simplemente porque no entró en la muestra recortada por ocupación. En rutas troncales
    # con mucha data esto hace que el total supere el `max_opciones` nominal — aceptable:
    # son pocas rutas (las de mayor tráfico) y el beneficio es mostrar la fuente correcta.
    tipos_reales = set(get_matriz().get(key, {})) & set(flota)
    for c in tipos_reales:
        if c not in codigos:
            codigos.append(c)
    codigos.sort(key=lambda c: (flota[c]['pax_vuelo_max'], flota[c]['asientos']))

    out = []
    for c in codigos:
        tons, origen = consumo_toneladas(c, dist, key)
        if tons is None:
            continue
        out.append([c, flota[c]['pax_vuelo_max'], round(tons * 1000.0, 1),
                    1 if origen == 'real' else 0])
    return out


def get_aircraft_info(o, d, tipo, coords, pax_por_vuelo=None):
    """Punto de entrada principal.

    o, d           nombres de lugar (como en COORDS)
    tipo           'cabotaje' | 'internacional' (se mantiene por compatibilidad)
    coords         dict {lugar: (lat, lon)}
    pax_por_vuelo  ocupación observada del mes; si viene, manda la elección del avión
    """
    key = _key(o, d)

    if key in _MANUAL:                 # las rutas del admin ganan siempre
        return _MANUAL[key]
    if o not in coords or d not in coords:
        return None

    bucket = None if not pax_por_vuelo else int(pax_por_vuelo // 5)
    if bucket is None:
        if key in _CACHE:
            return _CACHE[key]
    else:
        cached = _CACHE_OCUP.get((key, bucket))
        if cached is not None:
            return cached

    dist = haversine(*coords[o], *coords[d])
    codigo = seleccionar_avion(dist, pax_por_vuelo, key)
    result = _armar_resultado(dist, codigo, pax_por_vuelo, key) if codigo else None
    if result is None:
        # último recurso: lo precomputado por la versión anterior del modelo
        return _load_precomputed().get(key)

    if bucket is None:
        _CACHE[key] = result
    else:
        _CACHE_OCUP[(key, bucket)] = result
    return result


# ------------------------------------------------------------------ rutas manuales

def register_manual_route(o, d, tipo, avion, asientos, coords, consumo_kg_override=None,
                          aircraft_catalog=None):
    """Registra una ruta cargada manualmente desde el admin. Pisa cualquier estimación.

    Si `avion` coincide con un código o nombre de flota.json, usa la curva calibrada.
    Si coincide con un nombre del catálogo Aircraft, usa consumo_hora_kg y velocidad de
    ahí. Si se pasa consumo_kg_override, ese valor manda sobre todo lo demás."""
    if o not in coords or d not in coords:
        return None

    dist = haversine(*coords[o], *coords[d])
    key = _key(o, d)
    flota = get_flota()

    codigo = None
    if avion in flota:
        codigo = avion
    else:
        for c, f in flota.items():
            if f['nombre'] == avion:
                codigo = c
                break

    catalog_entry = (aircraft_catalog or {}).get(avion)

    if consumo_kg_override is not None:
        consumo_total_kg = float(consumo_kg_override)
        duracion_h = _duracion_estimada(dist, flota.get(codigo))
        fuente = 'Real (cargada manualmente, consumo manual)'
    elif codigo is not None:
        tons, _ = consumo_toneladas(codigo, dist, key)
        consumo_total_kg = (tons or 0.0) * 1000.0
        duracion_h = _duracion_estimada(dist, flota[codigo])
        fuente = 'Real (cargada manualmente)'
    elif catalog_entry is not None:
        consumo_hora = catalog_entry['consumo_hora_kg']
        velocidad = catalog_entry.get('velocidad_crucero_kmh') or 830.0
        duracion_h = 0.35 + dist / velocidad
        consumo_total_kg = consumo_hora * duracion_h
        fuente = 'Real (cargada manualmente)'
    else:
        duracion_h = 0.35 + dist / 830.0
        consumo_total_kg = 2600 * duracion_h
        fuente = 'Real (cargada manualmente, avión no reconocido)'

    result = {
        'avion': avion,
        'avion_codigo': codigo,
        'fuente': fuente,
        'asientos': asientos or (flota[codigo]['asientos'] if codigo else None),
        'distancia_km': round(dist, 1),
        'velocidad_crucero_kmh': _VEL_CRUCERO.get(
            (flota.get(codigo) or {}).get('fuselaje'), 830.0),
        'consumo_hora_kg': None,
        'duracion_h': round(duracion_h, 2),
        'consumo_total_kg': round(consumo_total_kg, 1),
        'consumo_total_m3': round(consumo_total_kg / JETA1_KG_PER_M3, 3),
        'consumo_base_t': round(consumo_total_kg / 1000.0, 3),
        'factor_ocupacion': 1.0,
        'load_factor': None,
        'pax_por_vuelo': None,
        'dato_consumo': 'manual',
    }
    _MANUAL[key] = result
    return result


def reset_caches():
    """Limpia los caches en memoria (usar después de cambiar rutas manuales)."""
    _CACHE.clear()
    _CACHE_OCUP.clear()
    _MANUAL.clear()
