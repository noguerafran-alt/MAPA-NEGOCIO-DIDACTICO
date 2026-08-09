"""
Calibración de las curvas de consumo con OpenAP (uso OFFLINE, no en producción).

Genera, para cada tipo de avión, un perfil de vuelo completo (rodaje, despegue, ascenso,
crucero, descenso) a varias distancias, integra el flujo de combustible paso a paso, y
ajusta la misma recta que usa el modelo:

    consumo_toneladas = a_t + b_kg_km * distancia_km / 1000

El resultado se compara contra los coeficientes que salieron de la planilla de YPF. La idea
NO es reemplazar la planilla, sino tener una segunda opinión independiente y física sobre
cada tipo, sobre todo en los que la planilla tiene pocas muestras.

OpenAP arrastra numpy + scipy + pandas + matplotlib (~157 MB solo de importar), así que
NUNCA se importa desde la app: este script corre en una máquina de desarrollo y lo único que
viaja a producción es flota.json con los coeficientes ya calculados.

Uso:
    pip install openap
    python calibrar_con_openap.py
"""
import json
import math
import os
import sys
import warnings

warnings.filterwarnings('ignore')

import numpy as np
from openap import FlightGenerator, FuelFlow, prop

BASE = os.path.dirname(os.path.abspath(__file__))

# Mapeo código de flota.json -> código OpenAP. OpenAP no tiene todos los tipos, así que
# algunos usan el pariente más cercano y se anota la sustitución.
MAPEO_OPENAP = {
    'A20N': ('a20n', None),
    'A21N': ('a21n', None),
    'A319': ('a319', None),
    'A320': ('a320', None),
    'A321': ('a321', None),
    'A332': ('a332', None),
    'A339': ('a333', 'OpenAP no trae el A330-900neo; se usa A330-300'),
    'A359': ('a359', None),
    'B737': ('b737', None),
    'B738': ('b738', None),
    'B38M': ('b38m', None),
    'B39M': ('b39m', None),
    'B744': ('b744', None),
    'B748': ('b748', None),
    'B763': ('b763', None),
    'B772': ('b772', None),
    'B77L': ('b773', 'OpenAP no trae el 777-200LR; se usa 777-300'),
    'B77W': ('b77w', None),
    'B788': ('b788', None),
    'B789': ('b789', None),
    'E190': ('e190', None),
    'CRJ2': ('crj9', 'OpenAP no trae el CRJ200; se usa CRJ900 (bastante más grande)'),
}

# Altitud de crucero típica por categoría
ALT_CRUCERO_FT = {'regional': 33000, 'narrow': 35000, 'wide': 37000, 'piston': 12000}

# Combustible de rodaje: minutos a flujo de ralentí. El modelo de trayectoria de OpenAP
# arranca en la pista, así que el taxi hay que sumarlo aparte.
TAXI_MIN = 13.0
TAXI_FF_FRAC = 0.06  # fracción del flujo de despegue, aproximación de ralentí en tierra

# Distancias de muestreo (km). Se acotan al alcance de cada tipo.
DISTANCIAS = [200, 500, 900, 1500, 2500, 4000, 6000, 8000, 10000, 12000, 14000]


def masa_despegue(p, dist_km, lf=0.82):
    """Masa de despegue estimada: OEW + pasajeros + combustible de viaje aproximado.

    Se hace en dos pasos porque el combustible depende de la masa y la masa del
    combustible: primero se estima con una regla gruesa y después se corrige."""
    oew = p['limits']['OEW']
    mtow = p['limits']['MTOW']
    payload = (mtow - oew) * 0.42 * lf          # pax + equipaje a ocupación de referencia
    combustible = min((mtow - oew) * 0.58, 0.030 * oew * dist_km / 1000.0)
    return min(oew + payload + combustible, mtow * 0.98)


def consumo_vuelo(fg, ff, p, dist_km, alt_ft):
    """Integra el consumo de un vuelo completo.

    OJO con los nombres de los parametros de fg.complete(): son `range_cr` (en KILOMETROS)
    y `alt_cr` (en PIES). El metodo los toma por **kwargs, asi que si se le pasa cualquier
    otro nombre los ignora en silencio y devuelve siempre la misma trayectoria por defecto.

    `range_cr` es la distancia de CRUCERO, no la total: el ascenso y el descenso agregan
    distancia por arriba. Por eso se devuelve tambien la distancia realmente recorrida y el
    ajuste se hace contra esa, no contra la pedida.

    Devuelve (distancia_real_km, toneladas) o None si no converge."""
    masa = masa_despegue(p, dist_km)
    dt = 15.0
    total = 0.0
    s_real = None

    for _ in range(2):  # dos pasadas para que la masa converja con el combustible
        df = fg.complete(dt=dt, range_cr=dist_km, alt_cr=alt_ft)
        s_real = float(df['s'].max()) / 1000.0
        m = masa
        total = 0.0
        for alt, tas, vs in zip(df['altitude'].values,
                                df['groundspeed'].values,
                                df['vertical_rate'].values):
            f = ff.enroute(mass=m, tas=max(float(tas), 1.0), alt=float(alt), vs=float(vs))
            f = float(f)
            if not math.isfinite(f) or f < 0:
                continue
            quemado = f * dt
            total += quemado
            m -= quemado
        masa = min(p['limits']['OEW'] +
                   (p['limits']['MTOW'] - p['limits']['OEW']) * 0.42 * 0.82 + total,
                   p['limits']['MTOW'] * 0.98)

    # rodaje en origen y destino, que la trayectoria de OpenAP no incluye
    f_to = float(ff.takeoff(tas=100, alt=0, throttle=1))
    total += f_to * TAXI_FF_FRAC * TAXI_MIN * 60

    if not s_real or s_real <= 0:
        return None
    return s_real, total / 1000.0


def ajustar(puntos):
    """Mínimos cuadrados de y = a + b*x, con a forzada a >= 0."""
    n = len(puntos)
    if n < 2:
        return 0.0, (puntos[0][1] / puntos[0][0] if puntos else 0.0)
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in puntos) / sxx if sxx else 0.0
    a = my - b * mx
    if a < 0:
        a = 0.0
        b = sum(x * y for x, y in puntos) / sum(x * x for x in xs)
    return a, b


def main():
    with open(os.path.join(BASE, 'flota.json'), encoding='utf-8') as f:
        flota = json.load(f)

    print(f'{"tipo":6} {"OpenAP":7} {"n":>2} '
          f'{"a_YPF":>7} {"b_YPF":>7} {"a_OAP":>7} {"b_OAP":>7} {"dif_b":>7}  nota')
    print('-' * 84)

    salida = {}
    for codigo, ficha in sorted(flota.items()):
        par = MAPEO_OPENAP.get(codigo)
        if not par:
            print(f'{codigo:6} {"—":7} sin equivalente en OpenAP')
            continue
        oap, nota = par
        alt = ALT_CRUCERO_FT.get(ficha.get('fuselaje'), 35000)
        alcance = ficha.get('alcance_km') or 6000

        # use_synonym=True hace que OpenAP caiga al tipo mas parecido cuando le falta el
        # modelo cinematico o la polar de resistencia del tipo exacto (b763, b772, crj9...).
        try:
            fg = FlightGenerator(ac=oap, use_synonym=True)
            ff = FuelFlow(ac=oap, use_synonym=True)
            p = prop.aircraft(oap, use_synonym=True)
        except Exception as e:
            print(f'{codigo:6} {oap:7} no se pudo inicializar: {type(e).__name__}: {e}')
            continue

        puntos = []
        for d in DISTANCIAS:
            if d > alcance * 0.92:
                continue
            try:
                r = consumo_vuelo(fg, ff, p, d, alt)
                if r and math.isfinite(r[1]) and r[1] > 0:
                    puntos.append(r)   # (distancia REAL recorrida, toneladas)
            except Exception as e:
                print(f'   ({codigo} @ {d} km fallo: {type(e).__name__}: {e})')

        if len(puntos) < 2:
            print(f'{codigo:6} {oap:7} sin puntos suficientes')
            continue

        a, b = ajustar(puntos)
        b_kg_km = b * 1000
        a_ypf = ficha.get('a_t')
        b_ypf = ficha.get('b_kg_km')
        dif = (f'{(b_kg_km / b_ypf - 1) * 100:+6.1f}%'
               if (b_ypf and b_ypf > 0) else '   —')

        print(f'{codigo:6} {oap:7} {len(puntos):>2} '
              f'{(a_ypf if a_ypf is not None else 0):7.2f} '
              f'{(b_ypf if b_ypf is not None else 0):7.2f} '
              f'{a:7.2f} {b_kg_km:7.2f} {dif:>7}  {nota or ""}')

        salida[codigo] = {
            'openap_ac': oap,
            'a_t_openap': round(a, 4),
            'b_kg_km_openap': round(b_kg_km, 4),
            'n_puntos': len(puntos),
            'nota_sustitucion': nota,
            'puntos': [[d, round(t, 3)] for d, t in puntos],
        }

    destino = os.path.join(BASE, 'calibracion_openap.json')
    with open(destino, 'w', encoding='utf-8') as f:
        json.dump(salida, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f'\nEscrito: {destino}')
    print('Para adoptar estos coeficientes en producción, copiá a_t_openap / b_kg_km_openap\n'
          'a los campos a_t / b_kg_km de flota.json. La app NO importa openap en ningún\n'
          'momento: solo lee el JSON.')


if __name__ == '__main__':
    main()
