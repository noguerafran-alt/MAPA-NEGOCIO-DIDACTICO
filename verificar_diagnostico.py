"""Verifica /api/deploy_status y el manejo de error de /proyecciones cuando falta el template."""
import os
import shutil
import sys

os.environ['DATABASE_URL'] = 'sqlite:////tmp/verif2.db'
_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
if os.path.exists('/tmp/verif2.db'):
    os.remove('/tmp/verif2.db')

from app import app, ensure_tables  # noqa

with app.app_context():
    ensure_tables()

c = app.test_client()
with c.session_transaction() as s:
    s['map_access'] = True

print('--- /api/deploy_status con todo presente')
d = c.get('/api/deploy_status').get_json()
print(' ', d)
assert d['todo_ok'] is True, 'debería dar todo_ok=True con el repo completo'

print('\n--- Simulando el deploy roto: renombro el template ANTES del primer render')
# Importante: Jinja cachea el template compilado en memoria una vez que se renderiza con
# éxito, y no vuelve a chequear el disco (auto_reload solo está activo en debug=True). Por
# eso el archivo hay que sacarlo ANTES de la primera vez que /proyecciones se sirve, para
# simular el escenario real de un deploy donde el archivo nunca estuvo.
tpl = 'templates/proyecciones.html'
shutil.move(tpl, '/tmp/proyecciones_backup.html')
try:
    r = c.get('/proyecciones')
    html = r.get_data(as_text=True)
    print('  status', r.status_code)
    print('  muestra el mensaje autoexplicativo:', 'No se pudo cargar Proyecciones' in html)
    print('  menciona /api/deploy_status:', 'deploy_status' in html)
    assert r.status_code == 500
    assert 'No se pudo cargar Proyecciones' in html

    d = c.get('/api/deploy_status').get_json()
    print('\n  /api/deploy_status con el template faltante:')
    print('   todo_ok:', d['todo_ok'])
    print('   archivos:', d['archivos_encontrados'])
    assert d['todo_ok'] is False
    assert d['archivos_encontrados']['templates/proyecciones.html'] is False
finally:
    shutil.move('/tmp/proyecciones_backup.html', tpl)

print('\n--- Restaurado el archivo. Uso una app nueva para que Jinja lo recompile:')
import importlib
import app as app_module
importlib.reload(app_module)
c2 = app_module.app.test_client()
with c2.session_transaction() as s:
    s['map_access'] = True
with app_module.app.app_context():
    app_module.ensure_tables()
r = c2.get('/proyecciones')
print('  /proyecciones status', r.status_code)
assert r.status_code == 200

print('\nOK — el diagnóstico funciona en ambos casos')
