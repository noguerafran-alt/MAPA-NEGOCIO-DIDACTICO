// Corre el <script> REAL de templates/proyecciones.html contra un DOM simulado, con datos
// realistas (2019-2027), para confirmar que:
//   1. La tabla de "Detalle mes a mes" sigue funcionando igual (no se rompió al envolverla
//      en el div con scroll).
//   2. Los selects "Desde"/"Hasta" se pueblan con TODOS los meses del rango real+proyectado.
//   3. aplicarRangoMeses() llama a Plotly.relayout con el rango correcto, incluso cuando
//      cruza años (ej. ago-2020 a mar-2023).
//   4. Si el usuario elige "Hasta" antes que "Desde", se intercambian solos.
//   5. Los botones de año (year-mosaic) SIGUEN estando y funcionando (no se borró nada).
//   6. "Limpiar rango" vuelve a la vista de "Proyección".
const fs = require('fs');
const { JSDOM } = require('jsdom');
const vm = require('vm');

const script = fs.readFileSync('/tmp/pn_script_test.js', 'utf8');
const ids = JSON.parse(fs.readFileSync('/tmp/pn_ids.json', 'utf8'));

const selectIds = new Set(['f-aeroplanta', 'fx-aeroplanta', 'fipc-division', 'fsal-serie',
                            'f-signo', 'rango-desde', 'rango-hasta']);
const checkboxIds = new Set(['f-excluir-covid']);
const numberIds = new Set(['f-horizonte', 'f-gcab', 'f-gintl', 'f-anios', 'f-pax', 'f-vuelos', 'fd-anio', 'fd-mes']);
const fileIds = new Set(['ffx-file', 'fsal-file', 'fipc-file', 'fm-file']);

function tagPara(id) {
  if (selectIds.has(id)) return `<select id="${id}"></select>`;
  if (checkboxIds.has(id)) return `<input type="checkbox" id="${id}" checked>`;
  if (fileIds.has(id)) return `<input type="file" id="${id}">`;
  if (numberIds.has(id)) return `<input type="number" id="${id}" value="0">`;
  if (id === 'preview' || id.endsWith('-note') || id.endsWith('-msg') || id.startsWith('tbody-')
      || id.startsWith('chart') || id === 'kpis' || id === 'kpis-mercado' || id === 'year-mosaic'
      || id === 'cobertura-anac' || id === 'covid-warn' || id === 'tabla-rutas') {
    return `<div id="${id}"></div>`;
  }
  return `<input type="text" id="${id}">`;
}

const domHtml = `<!DOCTYPE html><html><body>
  ${ids.map(tagPara).join('\n')}
  <button id="btn-cobertura"></button>
</body></html>`;

const dom = new JSDOM(domHtml, { url: 'http://localhost/proyecciones' });
global.window = dom.window;
global.document = dom.window.document;

let fallos = 0;
const check = (d, c) => { if (!c) fallos++; console.log(`  [${c ? 'OK ' : 'MAL'}] ${d}`); };

const llamadas = { relayout: [] };
global.Plotly = {
  newPlot: () => Promise.resolve({ on: () => {} }),
  purge: () => {},
  react: () => Promise.resolve({ on: () => {} }),
  relayout: (divId, upd) => { llamadas.relayout.push({ divId, upd }); },
};
global.fetch = () => Promise.reject(new Error('no debería llamarse en este test'));

const context = vm.createContext(global);
vm.runInContext(script, context, { filename: 'proyecciones.html:script' });

function correr(codigo) {
  vm.runInContext(`(function(){\n${codigo}\n})();`, context, { filename: 'test.js' });
}

// -------- construir una SERIE real-ish (2019-2025 real, 2026-2027 proyección) --------
correr(`
  SERIE = [];
  for (let a = 2019; a <= 2025; a++) {
    for (let m = 1; m <= 12; m++) {
      SERIE.push({ anio: a, mes: m, cabotaje_m3: 50000 + a*100, internacional_m3: 90000 + a*100 });
    }
  }
  EXCLUSIONES = [];
  RUTAS = [];
  CONFIG = { horizonte_meses: 24, g_cabotaje: 0, g_internacional: 0, anios_estacionalidad: 3 };
  MERCADO = [];
`);

console.log('--- construirProyeccion() + dibujarGrafico() con datos reales-ish (2019-2027)');
correr(`
  let ok = true;
  let proy;
  try {
    const r = construirProyeccion();
    proy = r.filas;
    dibujarGrafico(proy);
    dibujarDetalle(proy);
  } catch (e) { ok = false; console.log('    EXCEPCIÓN:', e.stack); }
  __RESULT_OK__ = ok;
`);
check('construirProyeccion + dibujarGrafico + dibujarDetalle corren sin excepción',
      vm.runInContext('__RESULT_OK__', context));

console.log('\n--- 1) Tabla de detalle: sigue funcionando dentro del contenedor con scroll');
correr(`
  const filas = document.getElementById('tbody-detalle').innerHTML;
  __check_detalle__ = filas.length > 100 && !filas.includes('Cargando');
`);
check('tbody-detalle tiene filas reales (no quedó en "Cargando…")',
      vm.runInContext('__check_detalle__', context));

console.log('\n--- 2) Selects Desde/Hasta poblados con todos los meses del rango');
correr(`
  __n_desde__ = document.getElementById('rango-desde').options.length;
  __n_hasta__ = document.getElementById('rango-hasta').options.length;
  __primero__ = document.getElementById('rango-desde').options[0].value;
  __ultimo__ = document.getElementById('rango-hasta').options[document.getElementById('rango-hasta').options.length-1].value;
`);
const nDesde = vm.runInContext('__n_desde__', context);
const nHasta = vm.runInContext('__n_hasta__', context);
check('Ambos selects tienen la misma cantidad de opciones', nDesde === nHasta, `${nDesde} vs ${nHasta}`);
check('Cubren un rango de años múltiplo de 12 meses', nDesde % 12 === 0, nDesde);
check('Arranca en el primer mes real (2019-01)', vm.runInContext('__primero__', context) === '2019-01');

console.log('\n--- 3) aplicarRangoMeses(): rango que cruza años (2020-08 a 2023-03)');
correr(`
  document.getElementById('rango-desde').value = '2020-08';
  document.getElementById('rango-hasta').value = '2023-03';
  aplicarRangoMeses();
`);
{
  const ult = llamadas.relayout.slice(-1)[0];
  check('Llamó a Plotly.relayout sobre "chart"', ult && ult.divId === 'chart', ult);
  check('xaxis.range arranca en 2020-08-01', ult.upd['xaxis.range'][0] === '2020-08-01', ult.upd);
  check('xaxis.range termina en 2023-04-01 (mes de "Hasta" completo, exclusivo)',
        ult.upd['xaxis.range'][1] === '2023-04-01', ult.upd);
}

console.log('\n--- 4) Si "Hasta" queda antes que "Desde", se intercambian solos');
llamadas.relayout.length = 0;
correr(`
  document.getElementById('rango-desde').value = '2022-05';
  document.getElementById('rango-hasta').value = '2021-01';
  aplicarRangoMeses();
  __desde_final__ = document.getElementById('rango-desde').value;
  __hasta_final__ = document.getElementById('rango-hasta').value;
`);
check('Los selects quedaron reordenados (Desde <= Hasta)',
      vm.runInContext('__desde_final__', context) === '2021-01' &&
      vm.runInContext('__hasta_final__', context) === '2022-05');
{
  const ult = llamadas.relayout.slice(-1)[0];
  check('El rango aplicado es el correcto tras el intercambio',
        ult.upd['xaxis.range'][0] === '2021-01-01' && ult.upd['xaxis.range'][1] === '2022-06-01',
        ult.upd);
}

console.log('\n--- 5) Los botones de año (mosaico) SIGUEN estando, ninguno se borró');
correr(`
  __tiles__ = document.querySelectorAll('#year-mosaic .year-tile').length;
  __tiene_todo__ = !!document.querySelector('#year-mosaic [data-id="todo"]');
  __tiene_proyeccion__ = !!document.querySelector('#year-mosaic [data-id="proyeccion"]');
  __tiene_2019__ = !!document.querySelector('#year-mosaic [data-id="y2019"]');
  __tiene_2027__ = !!document.querySelector('#year-mosaic [data-id="y2027"]');
`);
check('El mosaico tiene botones (no quedó vacío)', vm.runInContext('__tiles__', context) > 0);
check('Sigue el botón "Todo"', vm.runInContext('__tiene_todo__', context));
check('Sigue el botón "Proyección"', vm.runInContext('__tiene_proyeccion__', context));
check('Sigue el botón del año más viejo (2019)', vm.runInContext('__tiene_2019__', context));
check('Sigue el botón del año proyectado (2027)', vm.runInContext('__tiene_2027__', context));

console.log('\n--- Aplicar un rango de meses apaga cualquier tile de año que estuviera activo');
correr(`
  zoomAnioMosaico(MOSAICO_TILES.findIndex(t => t.id === 'y2020'));
  __activo_antes__ = document.querySelector('#year-mosaic .year-tile.active') !== null;
  document.getElementById('rango-desde').value = '2019-01';
  document.getElementById('rango-hasta').value = '2019-12';
  aplicarRangoMeses();
  __activo_despues__ = document.querySelector('#year-mosaic .year-tile.active') !== null;
`);
check('Antes de aplicar el rango, había un tile activo (2020)', vm.runInContext('__activo_antes__', context));
check('Después de aplicar el rango de meses, ningún tile queda marcado activo',
      !vm.runInContext('__activo_despues__', context));

console.log('\n--- 6) "Limpiar rango" vuelve a la vista de Proyección y reactiva ese tile');
llamadas.relayout.length = 0;
correr(`limpiarRangoMeses();`);
{
  const ult = llamadas.relayout.slice(-1)[0];
  check('relayout llamado de nuevo tras limpiar', !!ult, ult);
}
correr(`__proyeccion_activo__ = !!document.querySelector('#year-mosaic [data-id="proyeccion"].active');`);
check('El tile "Proyección" vuelve a quedar activo', vm.runInContext('__proyeccion_activo__', context));

console.log('\n--- El mosaico se reconstruye bien también cuando se llama de nuevo (ej. tras cambiar años de estacionalidad)');
correr(`
  let ok2 = true;
  try { dibujarMosaicoAnios(construirProyeccion().filas, new Date('2025-12-01'), new Date('2027-12-01')); }
  catch (e) { ok2 = false; console.log('    EXCEPCIÓN:', e.stack); }
  __ok2__ = ok2;
  __n_desde_2__ = document.getElementById('rango-desde').options.length;
`);
check('Reconstruir el mosaico no rompe nada', vm.runInContext('__ok2__', context));
check('Los selects se repueblan correctamente en la reconstrucción',
      vm.runInContext('__n_desde_2__', context) > 0);

console.log('\n' + '='.repeat(60));
console.log(fallos === 0 ? 'TODO OK — corrida real contra jsdom, sin excepciones' : `${fallos} FALLO(S)`);
process.exit(fallos ? 1 : 0);
