// Ejecuta el <script> REAL extraído de templates/proyecciones.html dentro de un DOM
// simulado (jsdom), con datos reales de salarios e IPC (los que subió el usuario) y una
// serie sintética de FX, para confirmar en runtime -- no solo por sintaxis -- que
// dibujarGraficoFX() y dibujarGraficoSalarios() corren sin excepciones y llaman a
// Plotly.newPlot con trazas coherentes.
const fs = require('fs');
const { JSDOM } = require('jsdom');

const dataset = JSON.parse(fs.readFileSync('/tmp/dataset_e2e.json', 'utf8'));
const script = fs.readFileSync('/tmp/proyecciones_script_test.js', 'utf8');

// Todos los IDs que el script referencia via getElementById, extraídos automáticamente
// del propio script (no a mano) para no tener que adivinar cuáles hacen falta.
const idMatches = [...script.matchAll(/getElementById\('([^']+)'\)/g)].map(m => m[1]);
const idsUnicos = [...new Set(idMatches)];
const selectIds = new Set(['f-aeroplanta', 'fx-aeroplanta', 'fipc-division', 'fsal-serie', 'f-signo']);
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

const html = `<!DOCTYPE html><html><body>
  ${idsUnicos.map(tagPara).join('\n')}
  <button id="btn-cobertura"></button>
</body></html>`;

// f-gcab / f-gintl / f-horizonte / f-anios con valores razonables (no 0, para que
// construirProyeccion() tenga con qué trabajar si algo del test la dispara indirectamente)


const dom = new JSDOM(html, { url: 'http://localhost/proyecciones' });
global.window = dom.window;
global.document = dom.window.document;

// -------- stub de Plotly: no renderiza de verdad, pero valida la forma de lo que se le pasa --------
const llamadas = { newPlot: [], purge: [] };
global.Plotly = {
  newPlot: (divId, traces, layout, opts) => {
    llamadas.newPlot.push({ divId, traces, layout, opts });
    return Promise.resolve({ on: () => {} });
  },
  purge: (divId) => { llamadas.purge.push(divId); },
  relayout: () => {},
  react: (divId, traces, layout, opts) => {
    llamadas.newPlot.push({ divId, traces, layout, opts });
    return Promise.resolve({ on: () => {} });
  },
};
global.fetch = () => Promise.reject(new Error('fetch no debería llamarse en este test'));

// -------- ejecutar el script real dentro del contexto global --------
const vm = require('vm');
const context = vm.createContext(global);
vm.runInContext(script, context, { filename: 'proyecciones.html:script' });

// IMPORTANTE: el script declara `let FX = []`, `let SALARIOS = []`, `let IPC = []` a nivel
// de módulo. Con vm.createContext(global), esas bindings de `let` viven en el entorno
// léxico global DE ESE CONTEXTO, que es distinto del "object environment record" del
// objeto global -- asignar `FX = ...` desde ESTE archivo (que corre en el realm normal de
// Node, no dentro de vm) actualiza una propiedad del objeto, pero no la variable léxica
// que el script en verdad lee. La única forma de tocar esas variables es seguir
// ejecutando código a través de vm.runInContext con el MISMO context, así se comparte el
// entorno léxico global entre llamadas sucesivas.
global.__DATASET__ = dataset;
global.__LLAMADAS__ = { newPlot: [], purge: [] };
Plotly.newPlot = (divId, traces, layout, opts) => {
  global.__LLAMADAS__.newPlot.push({ divId, traces, layout, opts });
  return Promise.resolve({ on: () => {} });
};
Plotly.purge = (divId) => { global.__LLAMADAS__.purge.push(divId); };

let fallos = 0;
global.__check__ = (d, c) => {
  if (!c) fallos++;
  console.log(`  [${c ? 'OK ' : 'MAL'}] ${d}`);
};

function correr(codigo) {
  // IIFE: cada llamada a correr() comparte el entorno léxico GLOBAL del script (necesario
  // para tocar `FX`, `SALARIOS`, `IPC` y las funciones definidas ahí), pero sin esto los
  // `const`/`let` declarados dentro de cada bloque de test chocarían entre sí al reusar
  // nombres como `call` de una llamada a la siguiente.
  vm.runInContext(`(function(){\n${codigo}\n})();`, context, { filename: 'test.js' });
}

// -------- inyectar los datos como si cargarTodo() ya hubiera corrido --------
console.log('--- Poblando estado global con datos reales (salarios+IPC) y FX sintético');
correr(`
  FX = __DATASET__.fx;
  SALARIOS = __DATASET__.salarios;
  IPC = __DATASET__.ipc;

  document.getElementById('fsal-serie').innerHTML =
    '<option value="indice_total">Índice general</option>' +
    '<option value="sector_privado_registrado">Sector privado registrado</option>' +
    '<option value="sector_publico">Sector público</option>' +
    '<option value="total_registrado">Total registrado</option>' +
    '<option value="sector_no_registrado">Sector no registrado</option>';
  document.getElementById('fsal-serie').value = 'sector_privado_registrado';
  document.getElementById('f-horizonte').value = 24;
  document.getElementById('f-gcab').value = 3;
  document.getElementById('f-gintl').value = 4;
  document.getElementById('f-anios').value = 3;

  const selIpc = document.getElementById('fipc-division');
  const divisiones = [...new Set(IPC.map(i => i.nombre))].sort();
  selIpc.innerHTML = divisiones.map(n => '<option value="' + n + '">' + n + '</option>').join('');
  selIpc.value = 'Nivel General';
  __check__('Selector de división IPC poblado con 18 opciones', divisiones.length === 18);
  __check__('"Nivel General" quedó seleccionado', selIpc.value === 'Nivel General');
`);

console.log('\n--- dibujarGraficoFX() con datos sintéticos');
correr(`
  let ok = true;
  try { dibujarGraficoFX(); } catch (e) { ok = false; console.log('    EXCEPCIÓN:', e.stack); }
  __check__('No tira excepción', ok);
  __check__('Llamó a Plotly.newPlot sobre "chart-fx"',
             __LLAMADAS__.newPlot.some(l => l.divId === 'chart-fx'));
  const call = __LLAMADAS__.newPlot.find(l => l.divId === 'chart-fx');
  if (call) {
    __check__('Una sola traza de línea', call.traces.length === 1 && call.traces[0].type === 'scatter');
    __check__('x e y del mismo largo que FX', call.traces[0].x.length === FX.length && call.traces[0].y.length === FX.length);
    __check__('Eje x es de tipo fecha', call.layout.xaxis.type === 'date');
    __check__('Tiene hovertext armado a mano', Array.isArray(call.traces[0].hovertext));
  }
`);

console.log('\n--- dibujarGraficoSalarios() con salario nominal + IPC real (deflactado)');
correr(`
  let ok = true;
  try { dibujarGraficoSalarios(); } catch (e) { ok = false; console.log('    EXCEPCIÓN:', e.stack); }
  __check__('No tira excepción', ok);
  __check__('Llamó a Plotly.newPlot sobre "chart-salarios"',
             __LLAMADAS__.newPlot.some(l => l.divId === 'chart-salarios'));
  const call = __LLAMADAS__.newPlot.filter(l => l.divId === 'chart-salarios').slice(-1)[0];
  if (call) {
    __check__('Al menos 2 trazas (nominal + hover invisible)', call.traces.length >= 2);
    const nominal = call.traces.find(t => t.name === 'Salario nominal');
    const real = call.traces.find(t => t.name && t.name.startsWith('Salario real'));
    __check__('Traza nominal presente', !!nominal);
    __check__('Traza de salario REAL presente (IPC sí se usó)', !!real);
    if (real) {
      __check__('La traza real usa el eje secundario y2', real.yaxis === 'y2');
      const primerValor = real.y.find(v => v != null);
      __check__('El primer punto rebasado da ~100', primerValor != null && Math.abs(primerValor - 100) < 1e-6);
      __check__('Hay variación real de verdad (no todo 100 plano)',
                 new Set(real.y.filter(v=>v!=null).map(v=>v.toFixed(2))).size > 5);
      __check__('layout.yaxis2 configurado', !!call.layout.yaxis2);
    }
  }
`);

console.log('\n--- "indice_total" (sub-serie real que arranca más tarde, oct-2016) debe graficar bien');
correr(`
  document.getElementById('fsal-serie').value = 'indice_total';
  __LLAMADAS__.newPlot.length = 0; __LLAMADAS__.purge.length = 0;
  let ok = true;
  try { dibujarGraficoSalarios(); } catch (e) { ok = false; console.log('    EXCEPCIÓN:', e.stack); }
  __check__('No tira excepción con una sub-serie que arranca más tarde', ok);
  __check__('Sí llamó a Plotly.newPlot (indice_total tiene datos reales)',
             __LLAMADAS__.newPlot.some(l => l.divId === 'chart-salarios'));
`);

console.log('\n--- Sub-serie sin ningún dato cargado (caso borde real: DB vacía para esa opción)');
correr(`
  document.getElementById('fsal-serie').innerHTML += '<option value="__inexistente__">Sin datos</option>';
  document.getElementById('fsal-serie').value = '__inexistente__';
  __LLAMADAS__.newPlot.length = 0; __LLAMADAS__.purge.length = 0;
  dibujarGraficoSalarios();
  __check__('Sin datos para esa sub-serie: purga el div en vez de romper',
             __LLAMADAS__.purge.includes('chart-salarios') && __LLAMADAS__.newPlot.length === 0);
  __check__('Muestra el mensaje de "sin datos"',
             document.getElementById('chart-salarios').innerHTML.includes('Sin datos'));
`);

console.log('\n--- Volver a "sector_privado_registrado" y quitar el deflactor (selector vacío)');
correr(`
  document.getElementById('fsal-serie').value = 'sector_privado_registrado';
  document.getElementById('fipc-division').value = '';
  __LLAMADAS__.newPlot.length = 0;
  dibujarGraficoSalarios();
  const call = __LLAMADAS__.newPlot.filter(l => l.divId === 'chart-salarios').slice(-1)[0];
  const real = call.traces.find(t => t.name && t.name.startsWith('Salario real'));
  __check__('Sin división elegida: NO se agrega la traza de salario real', !real);
  __check__('Pero la nominal sigue estando', call.traces.some(t => t.name === 'Salario nominal'));
`);

console.log('\n--- FX vacío: no debe romper, debe purgar y mostrar mensaje');
correr(`
  FX = [];
  __LLAMADAS__.purge.length = 0;
  dibujarGraficoFX();
  __check__('Purga "chart-fx"', __LLAMADAS__.purge.includes('chart-fx'));
  __check__('Muestra "Sin datos de tipo de cambio"',
             document.getElementById('chart-fx').innerHTML.includes('Sin datos de tipo de cambio'));
`);

console.log('\n' + '='.repeat(60));
console.log(fallos === 0 ? 'TODO OK — corrida real contra jsdom, sin excepciones' : `${fallos} FALLO(S)`);
process.exit(fallos ? 1 : 0);
