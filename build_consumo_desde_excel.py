"""Genera consumo_rutas.json y flota.json a partir del Excel de consumos.

- Normaliza los nombres de ruta a los de geocode.COORDS
- Ajusta, por tipo de avion, consumo_toneladas = a + b * distancia_km
  (ajuste robusto: 3 pasadas descartando outliers > 25%)
- Marca y corrige las celdas con error evidente de OCR (corrimiento decimal)
- Escribe el catalogo de flota con asientos, alcance y umbral de pax/vuelo
"""
import json
import math
import os
import re
import sys
import unicodedata

import openpyxl

# Antes: ruta absoluta hardcodeada a un sandbox de desarrollo puntual, y el Excel de
# entrada apuntando a la carpeta de uploads de esa misma sesión — ninguna de las dos
# existe fuera de ahí. Ahora todo es relativo a dónde vive este script, y el Excel se
# puede pasar como argumento (por defecto busca el archivo al lado del script, con el
# nombre que se viene usando).
_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
from geocode import COORDS  # noqa

XLSX = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_AQUI, 'rutas_aereas_consumo_combustible.xlsx')
OUT_DIR = _AQUI


# ---------------------------------------------------------------- normalizacion

def _strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')


def _norm(s):
    s = _strip_accents(s or '').lower()
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return ' '.join(s.split())


ALIAS = {
    'buenos aires': 'Aeroparque',
    'buenos aires ezeiza': 'Ezeiza',
    'nos aires ezeiza': 'Ezeiza',
    'comodoro rivadavia': 'Comod. Rivadavia',
    'san carlos de bariloche': 'Bariloche',
    'san carlosde bariloche': 'Bariloche',
    'chapelco san martin de los andes': 'Chapelco',
    'chapelco san martin de losandes': 'Chapelco',
    'viedma carmen de patagones': 'Viedma',
    'san miguel de tucuman': 'Tucumán',
    'san salvador de jujuy': 'Jujuy',
    'termas de rio hondo': 'Termas Río Hondo',
    'puerto iguazu': 'Iguazú',
    'neuqueen': 'Neuquén',
    'malargue': 'Malargüe',
    'merlo': 'Santa Rosa de Conlara',
    'rawson': 'Trelew',
    'brasilia': 'Brasília',
    'sao paulo': 'San Pablo',
    'ciudad de mexico': 'México DF',
    'new york': 'Nueva York',
    'johannesburg': 'Johannesburgo',
    'frankfurt am main': 'Frankfurt',
    'santa cruz': 'Santa Cruz de la Sierra',
    'tocumen': 'Panamá',
    'ciudad del este': 'Minga Guazú',
    'ciudad de la costa': 'Montevideo',
    'oran': 'Orán',
    'medellin': 'Medellín',
    'mount pleasant': 'Mount Pleasant (Islas Malvinas)',
    'santo domingo': 'Santo Domingo',
    'oranjestad': 'Oranjestad',
    'lagos': 'Lagos',
}

COORDS_NORM = {_norm(k): k for k in COORDS}


def resolve(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    s = re.sub(r'\s*[-–]\s*[A-Z]{2}\s*$', '', raw)
    s = re.sub(r'\s*[-–]\s*$', '', s)
    for cand in (_norm(s), _norm(re.sub(r'\(.*?\)', '', s))):
        if cand in ALIAS and ALIAS[cand] in COORDS:
            return ALIAS[cand]
        if cand in COORDS_NORM:
            return COORDS_NORM[cand]
    return None


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- catalogo de flota
# asientos        -> configuracion tipica en el mercado argentino / regional
# alcance_km      -> alcance practico con carga tipica (no el maximo de folleto)
# pax_vuelo_max   -> maximo de pax POR VUELO (promedio mensual) que el tipo sostiene.
#                    Es el umbral que decide la escalera de asignacion.
#                    E190 = 100 por indicacion explicita: por debajo de 100 pax/vuelo
#                    no se asume un A320/737, se asume un Embraer 190.
FLOTA = [
    # code,  nombre,                     asientos, pax_vuelo_max, alcance_km, fuselaje, escalera
    ('CRJ2', 'Bombardier CRJ200',              50,   45,   3050, 'regional', True),
    ('E190', 'Embraer 190',                   100,  100,   4500, 'regional', True),
    ('B737', 'Boeing 737-700',                132,  120,   6000, 'narrow',   True),
    ('A319', 'Airbus A319',                   144,  130,   6900, 'narrow',   True),
    ('A320', 'Airbus A320ceo',                170,  155,   6100, 'narrow',   True),
    ('B738', 'Boeing 737-800',                170,  155,   5400, 'narrow',   True),
    ('A20N', 'Airbus A320neo',                180,  165,   6500, 'narrow',   True),
    ('B38M', 'Boeing 737 MAX 8',              178,  162,   6500, 'narrow',   True),
    ('B39M', 'Boeing 737 MAX 9',              193,  176,   6570, 'narrow',   True),
    ('A321', 'Airbus A321ceo',                200,  185,   5900, 'narrow',   True),
    ('A21N', 'Airbus A321neo',                220,  200,   7400, 'narrow',   True),
    ('A332', 'Airbus A330-200',               250,  225,  13400, 'wide',     True),
    ('B763', 'Boeing 767-300ER',              250,  225,  11000, 'wide',     True),
    ('B788', 'Boeing 787-8',                  250,  225,  13600, 'wide',     True),
    ('A339', 'Airbus A330-900neo',            290,  260,  13300, 'wide',     True),
    ('B789', 'Boeing 787-9',                  290,  260,  14100, 'wide',     True),
    ('B77L', 'Boeing 777-200LR',              300,  270,  15800, 'wide',     True),
    ('B772', 'Boeing 777-200',                310,  280,   9700, 'wide',     True),
    ('A359', 'Airbus A350-900',               325,  295,  15000, 'wide',     True),
    ('B77W', 'Boeing 777-300ER',              350,  315,  13600, 'wide',     True),
    ('B744', 'Boeing 747-400',                400,  360,  13400, 'wide',     True),
    ('B748', 'Boeing 747-8I',                 410,  370,  14300, 'wide',     True),
    # Fuera de la escalera: aviacion general, no sirve para asignar rutas comerciales
    ('SR22', 'Cirrus SR22',                     4,    4,   1900, 'piston',   False),
]


# ---------------------------------------------------------------- ajuste robusto

def fit_robust(points, passes=3, trim=0.25):
    """Ajusta y = a + b*x descartando iterativamente los puntos que se desvian mas
    de `trim` en terminos relativos. Devuelve (a, b, usados, descartados)."""
    used = list(points)
    a = b = 0.0
    dropped = []
    for _ in range(passes):
        n = len(used)
        if n == 0:
            break
        if n == 1:
            x, y = used[0]
            a, b = 0.0, (y / x if x else 0.0)
            break
        mx = sum(p[0] for p in used) / n
        my = sum(p[1] for p in used) / n
        sxx = sum((p[0] - mx) ** 2 for p in used)
        sxy = sum((p[0] - mx) * (p[1] - my) for p in used)
        b = sxy / sxx if sxx else 0.0
        a = my - b * mx
        if a < 0:
            den = sum(p[0] * p[0] for p in used)
            b = (sum(p[0] * p[1] for p in used) / den) if den else 0.0
            a = 0.0
        keep, out = [], []
        for p in used:
            pred = a + b * p[0]
            if p[1] > 0 and abs(pred - p[1]) / p[1] > trim:
                out.append(p)
            else:
                keep.append(p)
        if not out or len(keep) < 3:
            break
        dropped += out
        used = keep
    return a, b, used, dropped


def main():
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb['Rutas y Consumo']
    rows = list(ws.iter_rows(values_only=True))
    types = list(rows[0][1:])

    raw = {}
    unresolved = set()
    for r in rows[1:]:
        name = r[0]
        if not name or '//' not in name:
            continue
        a_raw, b_raw = [x.strip() for x in name.split('//', 1)]
        ra, rb = resolve(a_raw), resolve(b_raw)
        if ra is None:
            unresolved.add(a_raw)
        if rb is None:
            unresolved.add(b_raw)
        if not ra or not rb or ra == rb:
            continue
        key = '|'.join(sorted([ra, rb]))
        bucket = raw.setdefault(key, {})
        for j, t in enumerate(types, 1):
            v = r[j]
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v > 0:
                bucket.setdefault(t, []).append(v)

    matrix = {}
    for k, cells in raw.items():
        merged = {t: round(sum(vs) / len(vs), 3) for t, vs in cells.items() if vs}
        if merged:
            matrix[k] = merged

    dist_of = {}
    for k in matrix:
        o, d = k.split('|')
        if o in COORDS and d in COORDS:
            dist_of[k] = haversine(*COORDS[o], *COORDS[d])

    # --- ajuste por tipo ---
    by_type = {}
    for k, cells in matrix.items():
        if k not in dist_of:
            continue
        for t, tons in cells.items():
            by_type.setdefault(t, []).append((dist_of[k], tons, k))

    fits = {}
    print(f'{"tipo":6} {"n":>3} {"desc":>4} {"a (t)":>7} {"b (kg/km)":>10} {"MAPE":>7}')
    for t, pts in sorted(by_type.items(), key=lambda x: -len(x[1])):
        pairs = [(p[0], p[1]) for p in pts]
        a, b, used, dropped = fit_robust(pairs)
        mape = (sum(abs(a + b * x - y) / y for x, y in used) / len(used) * 100) if used else None
        fits[t] = {'a_t': round(a, 4), 'b_kg_km': round(b * 1000, 4), 'n': len(used)}
        print(f'{t:6} {len(used):>3} {len(dropped):>4} {a:7.3f} {b*1000:10.3f} '
              f'{(mape if mape is not None else 0):6.2f}%')

    # --- correccion de celdas con error evidente (corrimiento decimal) ---
    correcciones = []
    for k, cells in matrix.items():
        if k not in dist_of:
            continue
        d = dist_of[k]
        for t in list(cells):
            f = fits.get(t)
            if not f or f['n'] < 4:
                continue
            pred = f['a_t'] + f['b_kg_km'] * d / 1000.0
            real = cells[t]
            if pred <= 0:
                continue
            ratio = real / pred
            if ratio > 3 or ratio < 0.34:
                correcciones.append({'ruta': k, 'tipo': t, 'valor_excel': real,
                                     'valor_corregido': round(pred, 3),
                                     'motivo': 'desvio >3x contra el ajuste del tipo '
                                               '(probable error de OCR)'})
                cells[t] = round(pred, 3)

    flota = {}
    for code, nombre, asientos, pax_max, alcance, fus, escalera in FLOTA:
        f = fits.get(code, {})
        flota[code] = {
            'nombre': nombre,
            'asientos': asientos,
            'pax_vuelo_max': pax_max,
            'alcance_km': alcance,
            'fuselaje': fus,
            'en_escalera': escalera,
            'a_t': f.get('a_t'),
            'b_kg_km': f.get('b_kg_km'),
            'n_muestras': f.get('n', 0),
        }

    # tipos que aparecen en el Excel pero no en FLOTA -> avisar
    faltantes = set(fits) - set(flota)
    if faltantes:
        print('\nOJO: tipos del Excel sin ficha en FLOTA:', faltantes)

    # rellenar coeficientes de los tipos sin datos propios usando el vecino mas parecido
    VECINO = {'A319': 'A320', 'A321': 'A21N', 'B744': 'B748', 'B763': 'A332'}
    for code, src in VECINO.items():
        if flota[code]['a_t'] is None or flota[code]['n_muestras'] < 3:
            ref = flota.get(src, {})
            if ref.get('a_t') is not None:
                escala = flota[code]['asientos'] / max(1, ref['asientos'])
                flota[code]['a_t'] = round(ref['a_t'] * escala, 4)
                flota[code]['b_kg_km'] = round(ref['b_kg_km'] * escala, 4)
                flota[code]['coef_derivado_de'] = src

    out_matrix = {
        '_meta': {
            'descripcion': 'Consumo real por ruta y tipo de avion, en TONELADAS de Jet A-1 '
                           'por vuelo (un solo tramo).',
            'fuente': 'rutas_aereas_consumo_combustible.xlsx (planilla YPF Aviacion)',
            'rutas': len(matrix),
            'correcciones_aplicadas': correcciones,
            'endpoints_sin_mapear': sorted(x for x in unresolved if x),
        },
    }
    out_matrix.update({k: v for k, v in sorted(matrix.items())})

    with open(f'{OUT_DIR}/consumo_rutas.json', 'w', encoding='utf-8') as f:
        json.dump(out_matrix, f, ensure_ascii=False, indent=1)
    with open(f'{OUT_DIR}/flota.json', 'w', encoding='utf-8') as f:
        json.dump(flota, f, ensure_ascii=False, indent=1, sort_keys=True)

    print(f'\nrutas en la matriz: {len(matrix)}')
    print(f'correcciones: {len(correcciones)}')
    for c in correcciones:
        print(f'  {c["ruta"]:45} {c["tipo"]}  {c["valor_excel"]} -> {c["valor_corregido"]}')
    print(f'sin mapear: {sorted(x for x in unresolved if x)}')


if __name__ == '__main__':
    main()
