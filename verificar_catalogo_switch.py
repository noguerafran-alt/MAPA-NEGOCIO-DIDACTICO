"""Verifica que el catálogo de admin se sincronice con flota.json y que el switch del mapa
(aviones regionales para <100 pax/vuelo) exista y filtre correctamente."""
import os
import sys

os.environ['DATABASE_URL'] = 'sqlite:////tmp/verif3.db'
# Antes: ruta absoluta hardcodeada a un sandbox de desarrollo puntual, que no existe fuera
# de esa máquina (rompía este script en cualquier otro lugar: Render, otra PC, CI). Se usa
# la carpeta donde vive el propio script, igual que el resto de los verificar_*.py.
_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
if os.path.exists('/tmp/verif3.db'):
    os.remove('/tmp/verif3.db')

from app import app, ensure_tables, ADMIN_PASSWORD  # noqa
from models import Aircraft  # noqa
import avion_model as am  # noqa

with app.app_context():
    ensure_tables()

fallos = []


def check(desc, cond, detalle=''):
    print(f'  [{"OK " if cond else "MAL"}] {desc}{(" — " + detalle) if detalle and not cond else ""}')
    if not cond:
        fallos.append(desc)


c = app.test_client()

print('--- Sincronización del catálogo con flota.json')
with app.app_context():
    nombres_db = {a.name for a in Aircraft.query.all()}
flota_nombres = {f['nombre'] for f in am.get_flota().values() if f.get('en_escalera')}
print(f'  filas en Aircraft: {len(nombres_db)} | tipos en escalera de flota.json: {len(flota_nombres)}')
check('Todos los tipos de la escalera están en el catálogo', flota_nombres.issubset(nombres_db))
check('Los 10 aviones legacy (DEFAULT_AIRCRAFT) siguen estando',
      {'Boeing 747', 'Airbus A320'}.issubset(nombres_db))
check('SR22 (piston, fuera de la escalera) NO se sincronizó', 'Cirrus SR22' not in nombres_db)

print('\n--- La sincronización es idempotente (no duplica en un segundo arranque)')
with app.app_context():
    n_antes = Aircraft.query.count()
    ensure_tables()
    n_despues = Aircraft.query.count()
check('Cantidad de filas no cambió en el segundo arranque', n_antes == n_despues,
      f'{n_antes} -> {n_despues}')

print('\n--- La sincronización no pisa ediciones del admin')
with c.session_transaction() as s:
    s['is_admin'] = True
r = c.post('/admin/aircraft/update', json={
    'original_name': 'Boeing 747', 'name': 'Boeing 747', 'tipo_fuselaje': 'wide',
    'consumo_hora_kg': 9999, 'velocidad_crucero_kmh': 900, 'asientos_default': 400,
})
check('Edición manual aceptada', r.status_code == 200, r.get_data(as_text=True))
with app.app_context():
    ensure_tables()  # correr la sincronización de nuevo
    b747 = Aircraft.query.get('Boeing 747')
check('El valor editado a mano sobrevive a un nuevo arranque', b747.consumo_hora_kg == 9999,
      f'quedó en {b747.consumo_hora_kg}')

print('\n--- El catálogo acepta fuselaje regional/piston')
r = c.post('/admin/aircraft/add', json={
    'name': 'Test Regional', 'tipo_fuselaje': 'regional',
    'consumo_hora_kg': 1500, 'velocidad_crucero_kmh': 700, 'asientos_default': 50,
})
check('Alta con tipo_fuselaje=regional aceptada', r.status_code == 200, r.get_data(as_text=True))

print('\n--- Admin muestra la columna Fuente y todos los tipos')
r = c.get('/admin')
html = r.get_data(as_text=True)
check('admin.html contiene la columna Fuente', '<th>Fuente</th>' in html)
check('El selector de fuselaje ofrece regional', 'value="regional"' in html)
check('El selector de fuselaje ofrece piston', 'value="piston"' in html)
check('El nombre completo del Embraer 190 aparece sin cortar', 'Embraer 190' in html)
check('El nombre completo del 777-300ER aparece sin cortar', 'Boeing 777-300ER' in html)
check('La tabla tiene ancho mínimo (no se aplasta)', 'min-width:980px' in html)
check('El contenedor tiene scroll horizontal', 'overflow: auto' in html)

print('\n--- El switch de aviones regionales está en el mapa')
with c.session_transaction() as s:
    s['map_access'] = True
html = c.get('/').get_data(as_text=True)
check('Existe el botón del switch', 'modelToggleBtn' in html)
check('Existe la función toggleAssignmentModel', 'function toggleAssignmentModel' in html)
check('elegirOpcionAvion filtra por fuselaje regional', "fuselaje !== 'regional'" in html)
check('El estado usarRegionales existe y arranca en true', 'let usarRegionales = true' in html)

print('\n--- Comportamiento del filtro (con las opciones REALES que manda /api/data)')
# Importante: hay que usar las mismas `opciones` que ve el navegador (metaEntry[12]), no un
# rango de ocupación inventado a mano. Con un rango muy angosto la ruta puede terminar con
# SOLO candidatos regionales en la lista recortada, lo cual no representa lo que pasa en
# producción (ahí el rango sale de percentiles 5/95 sobre TODO el historial de la ruta).


def elegir_js(opciones, ppv, usar_regionales):
    cand = opciones
    if not usar_regionales:
        no_reg = [o for o in opciones if am.get_flota().get(o[0], {}).get('fuselaje') != 'regional']
        if no_reg:
            cand = no_reg
    if ppv is None or ppv <= 0:
        return None
    return next((o for o in cand if o[1] >= ppv), cand[-1])


with c.session_transaction() as s:
    s['map_access'] = True
d = c.get('/api/data').get_json()
entry = next((m for m in d['cabotaje']['meta'] if m[0] == 'Aeroparque' and m[1] == 'Río Cuarto'), None)
check('Se encontró la ruta Aeroparque-Río Cuarto en /api/data', entry is not None)
ops = entry[12]
print(f'  opciones reales para esta ruta: {[o[0] for o in ops]}')

con_regional = elegir_js(ops, 50.1, True)
sin_regional = elegir_js(ops, 50.1, False)
print(f"  50,1 pax/vuelo, switch ON:  {con_regional[0] if con_regional else None}")
print(f"  50,1 pax/vuelo, switch OFF: {sin_regional[0] if sin_regional else None}")
check('Con el switch en Sí, asigna un tipo regional (E190/CRJ2)',
      con_regional and am.get_flota()[con_regional[0]]['fuselaje'] == 'regional')
check('Con el switch en No, NO asigna un tipo regional',
      sin_regional and am.get_flota()[sin_regional[0]]['fuselaje'] != 'regional')
check('El consumo con el switch en No es mayor (avión más grande)',
      con_regional and sin_regional and sin_regional[2] > con_regional[2],
      f'{con_regional[2]} vs {sin_regional[2]}')

print()
if fallos:
    print(f'RESULTADO: {len(fallos)} verificacion(es) fallaron')
    for f in fallos:
        print('  -', f)
    sys.exit(1)
print('RESULTADO: catálogo y switch OK')
