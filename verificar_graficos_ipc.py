"""Verifica el parser de IPC (formato largo de INDEC) y los gráficos nuevos de FX/salarios.

Corre en dos partes:
  1. Backend: parse_indec_csv contra los fixtures reales de INDEC (salarios + IPC).
  2. Frontend: el <script> real de templates/proyecciones.html, ejecutado contra un DOM
     simulado (jsdom) con esos mismos datos, para confirmar que dibujarGraficoFX() y
     dibujarGraficoSalarios() corren sin excepciones y arman las trazas de Plotly bien.

La parte 2 requiere `jsdom` (no es dependencia de producción, solo de testing):
    npm install jsdom --prefix /tmp/npmtest
    NODE_PATH=/tmp/npmtest/node_modules python3 verificar_graficos_ipc.py
Si node o jsdom no están disponibles, la parte 2 se saltea con un aviso (no hace fallar
la corrida) — la parte 1 igual corre siempre.
"""
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['DATABASE_URL'] = 'sqlite:////tmp/verif_graficos.db'
if os.path.exists('/tmp/verif_graficos.db'):
    os.remove('/tmp/verif_graficos.db')

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_fixtures')

fallos = []


def check(desc, cond, detalle=''):
    print(f'  [{"OK " if cond else "MAL"}] {desc}{(" — " + str(detalle)) if detalle and not cond else ""}')
    if not cond:
        fallos.append(desc)


# =========================================================================================
# 1. Backend: parse_indec_csv
# =========================================================================================
print('=== 1. Backend: parse_indec_csv contra los fixtures reales de INDEC ===')
from app import app, ensure_tables, parse_indec_csv  # noqa

with app.app_context():
    ensure_tables()

sal_path = os.path.join(FIXTURES, 'indice_salarios.csv')
ipc_path = os.path.join(FIXTURES, 'serie_ipc_divisiones.csv')

if os.path.exists(sal_path):
    with open(sal_path, 'rb') as f:
        regs, warns = parse_indec_csv(f, 'salario')
    check('Salarios: formato ancho detectado (no cae en el parser largo)', len(regs) > 0)
    check('Salarios: 5 sub-series', len({r['nombre'] for r in regs}) == 5,
          {r['nombre'] for r in regs})
    check('Salarios: sin warnings', not warns, warns)
else:
    print('  (fixture indice_salarios.csv no encontrado, se saltea)')

if os.path.exists(ipc_path):
    with open(ipc_path, 'rb') as f:
        regs, warns = parse_indec_csv(f, 'ipc')
    nombres = {r['nombre'] for r in regs}
    check('IPC: 18 sub-series (formato largo detectado)', len(nombres) == 18, nombres)
    check('IPC: "Nivel General" presente', 'Nivel General' in nombres)
    check('IPC: agregados especiales sin prefijo "Código" (B, S, Estacional...)',
          {'B', 'S', 'Estacional', 'Núcleo', 'Regulados'} <= nombres, nombres)
    check('IPC: sin caracteres corruptos (mojibake) en ningún nombre',
          not any('\ufffd' in n for n in nombres), nombres)
    check('IPC: sin warnings', not warns, warns)
else:
    print('  (fixture serie_ipc_divisiones.csv no encontrado, se saltea)')

# --- end-to-end vía HTTP, con idempotencia ---
print('\n--- Subida real vía /api/proyeccion/indices/upload')
c = app.test_client()
with c.session_transaction() as s:
    s['map_access'] = True

if os.path.exists(ipc_path):
    with open(ipc_path, 'rb') as f:
        r = c.post('/api/proyeccion/indices/upload',
                   data={'categoria': 'ipc', 'file': (f, 'serie_ipc_divisiones.csv')},
                   content_type='multipart/form-data')
    d = r.get_json()
    check('Upload IPC: status 200', r.status_code == 200)
    check('Upload IPC: inserta sin warnings', d.get('insertados', 0) > 0 and not d.get('warnings'), d)

    with open(ipc_path, 'rb') as f:
        r2 = c.post('/api/proyeccion/indices/upload',
                    data={'categoria': 'ipc', 'file': (f, 'serie_ipc_divisiones.csv')},
                    content_type='multipart/form-data')
    d2 = r2.get_json()
    check('Re-subida: idempotente (actualiza, no duplica)',
          d2.get('insertados') == 0 and d2.get('actualizados') == d.get('insertados'), d2)


# =========================================================================================
# 2. Frontend: el script real contra jsdom
# =========================================================================================
print('\n=== 2. Frontend: dibujarGraficoFX / dibujarGraficoSalarios contra jsdom ===')

node_ok = shutil.which('node') is not None
if not node_ok:
    print('  (node no disponible, se saltea la parte 2)')
else:
    try:
        subprocess.run(['node', '-e', "require('jsdom')"], check=True,
                       capture_output=True,
                       env={**os.environ, 'NODE_PATH': os.environ.get('NODE_PATH', '')})
        jsdom_ok = True
    except Exception:
        jsdom_ok = False

    if not jsdom_ok:
        print('  (jsdom no disponible — instalar con `npm install jsdom --prefix /tmp/npmtest` '
              'y correr con NODE_PATH=/tmp/npmtest/node_modules; se saltea la parte 2)')
    else:
        test_js = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_e2e_graficos.js')
        if not os.path.exists(test_js):
            print(f'  ({test_js} no encontrado, se saltea)')
        else:
            resultado = subprocess.run(['node', test_js], capture_output=True, text=True,
                                       env=os.environ, cwd=os.path.dirname(test_js))
            print(resultado.stdout)
            if resultado.returncode != 0:
                print(resultado.stderr)
            check('test_e2e_graficos.js sale con código 0 (todas sus propias verificaciones OK)',
                  resultado.returncode == 0)


# =========================================================================================
print('\n' + '=' * 70)
if fallos:
    print(f'{len(fallos)} CHEQUEO(S) FALLIDO(S):')
    for f in fallos:
        print('  -', f)
    sys.exit(1)
print('TODO OK')
