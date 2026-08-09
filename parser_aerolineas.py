"""
Parser de la tabla "Vuelos [#], Pax [000], Ocupación y Mercado [Cabotaje|Internacional]
x Aerolínea" de los Informes Mensuales de ANAC (PDF).

Cada informe mensual trae, para esa tabla, los últimos 12 meses de historia
(Jul..Jun por ejemplo). Este parser extrae los 12 meses completos de cada
subida, no solo el mes "actual" — así, con cada PDF que subís, se backfillea
y corrige automáticamente cualquier mes que faltara o viniera provisorio en
una subida anterior (los últimos 3 meses de cada informe son "provisorios"
según ANAC, así que van a cambiar levemente en el informe del mes siguiente).

IMPORTANTE (memoria): el informe tiene ~90 páginas, varias con fotos/gráficos
pesados. Usamos `pypdf` en vez de `pdfplumber` porque `pdfplumber` carga por
debajo un motor completo de renderizado de PDF (PDFium) con overhead propio
de decenas de MB solo por importarlo, mientras que `pypdf` es puro Python sin
motor de renderizado y da exactamente el mismo texto para este informe. Este
parser además hace UN SOLO PASO por las páginas (no varios escaneos
separados) y corta la lectura apenas encontró todo lo que necesita — crítico
en hosting con poca RAM (ej. Render free tier, 512MB total): la primera
versión de este parser (con pdfplumber, escaneando las 89 páginas sin cortar)
hacía que el worker muriera por out-of-memory (SIGKILL) en Render.

Uso:
    from parser_aerolineas import extract_report
    airline_registros, lf_registros, warnings, (anio, mes) = extract_report("/ruta/al/Informe.pdf")
    # airline_registros -> {tipo, aerolinea, anio, mes, mes_label, vuelos, pax_000, ocupacion}
    # lf_registros      -> {tipo, aerolinea, anio, mes, lf_2025, lf_2026, variacion_pp}
"""
import re

import pypdf

MESES_ES = {
    "Ene": 1, "Feb": 2, "Mar": 3, "Abr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Ago": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dic": 12,
}
MESES_ES_FULL = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6,
    "JULIO": 7, "AGOSTO": 8, "SEPTIEMBRE": 9, "OCTUBRE": 10,
    "NOVIEMBRE": 11, "DICIEMBRE": 12,
}

NUM_PLAIN = re.compile(r'^-?\d{1,3}(?:\.\d{3})*$')   # "42.874", "109", "-24"
NUM_PCT = re.compile(r'^-?\d{1,3}(?:\.\d{3})*%$')     # "84%", "-10%"

TITLE_CABOTAJE = "Vuelos [#], Pax [000], Ocupación y Mercado Cabotaje"
TITLE_INTERNACIONAL = "Vuelos [#], Pax [000], Ocupación y Mercado Internacional"

# Tabla nueva: "Factor de Ocupación por Aerolínea del mes". A diferencia de la
# tabla de arriba (que agrupa a las aerolíneas chicas en "Otros"), esta trae
# desagregadas individualmente todas las aerolíneas internacionales (Copa,
# Iberia, Sky, Avianca, Lufthansa, United, Boliviana, Air Europa, etc.) para
# el mes del informe únicamente (no trae histórico de 12 meses, solo 2025 vs
# 2026 del mes actual + variación en puntos porcentuales).
TITLE_LOAD_FACTOR = "Factor de Ocupación por Aerolínea del mes"

# "Aerolíneas Argentinas 83% 84% 1" -> (nombre, LF 2025, LF 2026, variación pp)
_LOAD_FACTOR_ROW = re.compile(r'^(.+?)\s+(-?\d{1,3})%\s+(-?\d{1,3})%\s+(-?\d{1,3})\s*$')

# Cuántas páginas iniciales revisar para el mes/año del informe (está en las
# "Notas Generales", siempre cerca del principio).
MAX_PAGES_FOR_DATE = 6
# Límite de seguridad: si no encontramos ambas tablas antes de esta página,
# dejamos de buscar para no consumir memoria de más en un PDF con formato
# inesperado (mejor devolver un warning claro que colgar/OOM-ear el server).
MAX_PAGES_TO_SCAN = 80


def _dedup_letters(s: str) -> str:
    """Algunos nombres de aerolínea salen con letras duplicadas por un glitch
    de extracción en PDFs con fuentes bold (ej: "JJeettSSMMAARRTT Group")."""
    out = []
    i = 0
    while i < len(s):
        if i + 1 < len(s) and s[i] == s[i + 1] and s[i].isalpha():
            out.append(s[i])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _clean_airline_name(raw: str) -> str:
    raw = raw.strip()
    deduped = _dedup_letters(raw)
    if len(deduped) < len(raw) * 0.75:
        return deduped
    return raw


def _try_parse_report_date(text: str):
    """Busca 'la bajada del sistema del día 30/06/2026' (o, como respaldo,
    'Notas Junio 2026') en el texto de UNA página. Devuelve (anio, mes) o
    None si no aparece en esta página."""
    m = re.search(r'bajada del sistema del d[ií]a\s+\d{1,2}/(\d{2})/(\d{4})', text)
    if m:
        return int(m.group(2)), int(m.group(1))
    m2 = re.search(r'Notas\s+(' + "|".join(MESES_ES_FULL.keys()).title() + r')\s+(\d{4})',
                    text, re.IGNORECASE)
    if m2:
        return int(m2.group(2)), MESES_ES_FULL[m2.group(1).upper()]
    return None


def _parse_month_header(lines):
    """Busca la fila de encabezado ('Vuelos Cabotaje 2025 2026 Jul Ago ... Jun')
    y devuelve la lista de 12 abreviaturas de mes reales (sin los 2 acumulados)."""
    for line in lines[:6]:
        tokens = line.split()
        if tokens and tokens[0] == "Vuelos":
            month_tokens = [t for t in tokens if t in MESES_ES]
            if len(month_tokens) == 12:
                return month_tokens
    raise ValueError("No se encontró la fila de encabezado de meses en la tabla.")


def _months_with_years(month_abbrevs, report_year):
    """Los primeros 6 meses (Jul..Dic) son del año anterior; los últimos 6
    (Ene..Jun) son del año del informe."""
    out = []
    for idx, abbr in enumerate(month_abbrevs):
        anio = report_year - 1 if idx < 6 else report_year
        out.append((anio, MESES_ES[abbr], abbr))
    return out


def _extract_row_values(line: str, kind: str):
    tokens = line.split()
    pattern = NUM_PLAIN if kind == "plain" else NUM_PCT
    return [t for t in tokens if pattern.match(t)]


def _to_number(token: str):
    token = token.replace("%", "").replace(".", "")
    try:
        return int(token)
    except ValueError:
        return None


def _flush_airline(airline, tipo, months, registros, warnings):
    nombre = airline["nombre"]
    vuelos, pax, ocup = airline.get("vuelos"), airline.get("pax"), airline.get("ocupacion")
    if not (vuelos and pax and ocup):
        warnings.append(f"[{tipo}] '{nombre}': fila incompleta, se omite (vuelos/pax/ocupación faltante).")
        return
    for i, (anio, mes_num, mes_abbr) in enumerate(months):
        v = _to_number(vuelos[i]) if i < len(vuelos) else None
        p = _to_number(pax[i]) if i < len(pax) else None
        o = _to_number(ocup[i]) if i < len(ocup) else None
        if v is None or p is None or o is None:
            continue
        registros.append({
            "tipo": tipo, "aerolinea": nombre, "anio": anio, "mes": mes_num, "mes_label": mes_abbr,
            "vuelos": v, "pax_000": p, "ocupacion": round(o / 100.0, 4),
        })


def _parse_table_text(tipo, text, report_year, registros, warnings):
    lines = text.split("\n")
    month_abbrevs = _parse_month_header(lines)
    months = _months_with_years(month_abbrevs, report_year)

    current_airline = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("Vuelos [#], Pax", "Ene-Jun", "Vuelos Cabotaje",
                                 "Vuelos Internacionales")):
            continue
        if stripped in ("Aerolínea", "por Aerolínea"):
            continue  # continuación del título en 2 líneas
        if stripped.startswith("Vuelos [#]"):
            vals = _extract_row_values(stripped, "plain")
            monthly = vals[2:14] if len(vals) >= 14 else None
            if current_airline and monthly:
                current_airline["vuelos"] = monthly
            continue
        if stripped.startswith("Pax [000]"):
            vals = _extract_row_values(stripped, "plain")
            monthly = vals[2:14] if len(vals) >= 14 else None
            if current_airline and monthly:
                current_airline["pax"] = monthly
            continue
        if stripped.startswith("Ocupación [%]"):
            vals = _extract_row_values(stripped, "pct")
            monthly = vals[2:14] if len(vals) >= 14 else None
            if current_airline and monthly:
                current_airline["ocupacion"] = monthly
                if (current_airline["nombre"] == "TOTAL"
                        and current_airline.get("vuelos") and current_airline.get("pax")):
                    # TOTAL es siempre la última fila real de esta tabla. Todo lo
                    # que venga después son restos del título/encabezado que
                    # pypdf (a diferencia de pdfplumber) a veces deja al final
                    # del texto de la página — cortamos acá para no generar
                    # avisos falsos por esas líneas sueltas.
                    _flush_airline(current_airline, tipo, months, registros, warnings)
                    return
            continue
        if stripped.startswith("Cuota de Mercado"):
            continue  # no lo necesitamos
        if stripped == "TOTAL" or stripped.startswith("TOTAL"):
            if current_airline:
                _flush_airline(current_airline, tipo, months, registros, warnings)
            current_airline = {"nombre": "TOTAL", "vuelos": None, "pax": None, "ocupacion": None}
            continue
        if current_airline:
            _flush_airline(current_airline, tipo, months, registros, warnings)
        nombre_limpio = _clean_airline_name(stripped)
        if tipo == "internacional" and nombre_limpio == "JetSMART":
            # ANAC nombra a este segmento "JetSMART Group" en la tabla internacional
            # (distinto del "JetSMART" de cabotaje). pypdf a veces no extrae el
            # sufijo " Group" por cómo está renderizado en el PDF — lo restituimos.
            nombre_limpio = "JetSMART Group"
        current_airline = {"nombre": nombre_limpio, "vuelos": None, "pax": None, "ocupacion": None}

    if current_airline:
        _flush_airline(current_airline, tipo, months, registros, warnings)


def _parse_load_factor_text(text, report_year, report_month, registros, warnings):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    tipo = None  # 'cabotaje' | 'internacional', según el bloque en el que estemos

    for line in lines:
        if line == "Cabotaje":
            tipo = "cabotaje"
            continue
        if line.startswith("Internacional") and "2025" in line:
            tipo = "internacional"
            continue
        if line in ("Load Factor", "Variación", "LF [pp]") or line.startswith("Load Factor"):
            continue  # encabezados sueltos, sin datos

        m = _LOAD_FACTOR_ROW.match(line)
        if not m or tipo is None:
            continue

        nombre_raw, lf_2025, lf_2026, variacion = m.groups()
        nombre = _clean_airline_name(nombre_raw)
        if tipo == "internacional" and nombre == "JetSMART":
            nombre = "JetSMART Group"  # mismo criterio que en la otra tabla

        registros.append({
            "tipo": tipo,
            "aerolinea": nombre,
            "anio": report_year,
            "mes": report_month,
            "lf_2025": round(int(lf_2025) / 100.0, 4),
            "lf_2026": round(int(lf_2026) / 100.0, 4),
            "variacion_pp": int(variacion),
        })

        if nombre == "TOTAL" and tipo == "internacional":
            # TOTAL internacional es siempre la última fila de esta tabla
            # (viene después del bloque de cabotaje) -- cortamos acá por la
            # misma razón que en _parse_table_text: pypdf a veces deja restos
            # de texto de página después del final real de la tabla.
            return


def extract_report(pdf_path: str):
    """Extrae, en UN SOLO PASE por las páginas del PDF, tanto:
      - la tabla "Vuelos [#], Pax [000], Ocupación y Mercado x Aerolínea"
        (histórico de 12 meses, cabotaje + internacional), y
      - la tabla "Factor de Ocupación por Aerolínea del mes" (solo el mes
        del informe, pero con aerolíneas internacionales desagregadas).

    Antes esto eran dos funciones separadas, cada una abriendo y escaneando
    el PDF de punta a punta por su cuenta -- es decir, el mismo trabajo de
    lectura/decodificación de páginas se hacía DOS VECES por cada PDF subido.
    Unificarlo en un solo pase evita esa duplicación: para un informe de
    ~90 páginas, es la diferencia entre decodificar ~90 páginas una vez o
    ~180 veces (dos pasadas completas en el peor caso, cuando las 3 tablas
    buscadas están recién cerca del límite de escaneo).

    Devuelve: (airline_registros, load_factor_registros, warnings, (anio, mes))
    """
    airline_registros = []
    lf_registros = []
    warnings = []
    report_year = report_month = None
    cabotaje_text = internacional_text = load_factor_text = None

    # OJO memoria: si a pypdf.PdfReader() le pasás un path (string), internamente
    # hace `BytesIO(open(path, "rb").read())` -- es decir, copia el archivo
    # ENTERO a un buffer nuevo en memoria antes de tocar una sola página. Abrir
    # nosotros el archivo y pasarle el file object ya abierto evita esa copia
    # extra (pypdf lee directo del file object con su propio buffering, sin
    # duplicar el contenido). Para un informe de varios MB, esto ahorra
    # aproximadamente el tamaño del archivo en memoria.
    with open(pdf_path, "rb") as fh:
        reader = pypdf.PdfReader(fh)
        try:
            num_pages = len(reader.pages)

            for i in range(min(num_pages, MAX_PAGES_TO_SCAN)):
                page = reader.pages[i]
                text = page.extract_text() or ""

                if report_year is None and i < MAX_PAGES_FOR_DATE:
                    found = _try_parse_report_date(text)
                    if found:
                        report_year, report_month = found

                if cabotaje_text is None and TITLE_CABOTAJE in text and "Detalle" not in text:
                    cabotaje_text = text
                if internacional_text is None and TITLE_INTERNACIONAL in text and "Detalle" not in text:
                    internacional_text = text
                if load_factor_text is None and TITLE_LOAD_FACTOR in text:
                    load_factor_text = text

                # Soltar la referencia a la página y al texto de esta iteración
                # apenas terminamos de usarlos. NOTA: sacamos el gc.collect()
                # periódico que había acá antes -- Python libera por conteo de
                # referencias apenas hacemos `del` (sin necesidad de un ciclo
                # completo de garbage collection), y gc.collect() SÍ tiene un
                # costo real de CPU (recorre todos los objjetos vivos). Con
                # memoria ya bajísima (~48MB pico medido), ese costo de CPU no
                # se justificaba y probablemente contribuía al timeout en un
                # hosting con CPU compartida/limitada (Render free tier).
                del page, text

                if (report_year is not None and cabotaje_text is not None
                        and internacional_text is not None and load_factor_text is not None):
                    break  # ya tenemos todo lo que necesitamos, no seguir leyendo páginas
        finally:
            reader.close()
            del reader

    if report_year is None:
        raise ValueError("No se pudo determinar el mes/año del informe (revisá las Notas Generales del PDF).")

    for tipo, text in (("cabotaje", cabotaje_text), ("internacional", internacional_text)):
        if text is None:
            warnings.append(f"No se encontró la tabla de '{tipo}' en el PDF.")
            continue
        _parse_table_text(tipo, text, report_year, airline_registros, warnings)

    if load_factor_text is None:
        warnings.append(f"No se encontró la tabla '{TITLE_LOAD_FACTOR}' en el PDF.")
    else:
        _parse_load_factor_text(load_factor_text, report_year, report_month, lf_registros, warnings)

    return airline_registros, lf_registros, warnings, (report_year, report_month)
