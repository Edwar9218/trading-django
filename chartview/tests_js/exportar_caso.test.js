// Prueba de los helpers puros de "Exportar caso" (limpiarParaExport, atrSimple,
// resultadoPosterior). Viven dentro de grafico.html, así que el test los extrae del
// propio archivo entre sus marcadores y los evalúa: no hay copia que se desactualice.
//
// Uso (desde la raíz del proyecto):
//   node chartview/tests_js/exportar_caso.test.js
//   node chartview/tests_js/exportar_caso.test.js ruta/a/grafico.html   (si está en otro lado)
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const candidatas = [
  process.argv[2],
  path.join(__dirname, '..', 'templates', 'chartview', 'grafico.html'),
  path.join(__dirname, '..', 'templates', 'grafico.html'),
  path.join(process.cwd(), 'grafico.html'),
].filter(Boolean);
const ruta = candidatas.find(p => fs.existsSync(p));
if (!ruta) { console.error('No encuentro grafico.html. Pásalo como argumento.'); process.exit(1); }

const html = fs.readFileSync(ruta, 'utf8').replace(/\r\n/g, '\n');
const ini = html.indexOf('// ── Exportar caso: helpers puros');
const fin = html.indexOf('// Fábrica de paneles');
assert.ok(ini >= 0 && fin > ini, 'No se encontraron los marcadores de los helpers en grafico.html');
const codigo = html.slice(ini, fin);
const { limpiarParaExport: L, atrSimple, resultadoPosterior } =
  new Function(codigo + '\nreturn { limpiarParaExport, atrSimple, resultadoPosterior };')();

// 1) limpiar: NaN/inf, funciones, ciclos, points, arreglos largos
const ciclo = { a: 1 }; ciclo.yo = ciclo;
const r = L({ x: NaN, y: Infinity, f() {}, ok: 1.5, s: 'hola', n: null, u: undefined,
  base_points: [{ t: 1, y: 2 }, { t: 2, y: 3 }], largo: Array.from({ length: 100 }, (_, i) => i),
  corto: [1, NaN, 3], ciclo });
assert.strictEqual(r.x, null); assert.strictEqual(r.y, null);
assert.ok(!('f' in r));
assert.strictEqual(r.u, null);   // undefined → null: la clave siempre existe
assert.deepStrictEqual(r.base_points, { n: 2, ultimo: { t: 2, y: 3 } });
assert.strictEqual(r.largo.n, 100); assert.strictEqual(r.largo.ultimos.length, 40); assert.strictEqual(r.largo.ultimos[39], 99);
assert.deepStrictEqual(r.corto, [1, null, 3]);
assert.strictEqual(r.ciclo.yo, '[ciclo]');
JSON.parse(JSON.stringify(r));   // serializable de punta a punta
const comp = { v: 1 };
assert.deepStrictEqual(L({ a: comp, b: comp }), { a: { v: 1 }, b: { v: 1 } });   // repetida no circular: se exporta
assert.strictEqual(L(undefined), null); assert.strictEqual(L(() => 1), undefined);

// 2) ATR simple
const velas = Array.from({ length: 30 }, (_, i) => ({ time: i, open: 100, high: 101, low: 99, close: 100 }));
assert.strictEqual(atrSimple(velas, 14), 2);
assert.strictEqual(atrSimple(velas.slice(0, 10), 14), null);
const v2 = velas.slice(); v2[29] = { time: 29, open: 104, high: 105, low: 104, close: 105 };
assert.strictEqual(atrSimple(v2, 14), (13 * 2 + 5) / 14);   // hueco: TR = 5

// 3) resultadoPosterior
const ref = { time: 100, open: 1.2, high: 1.21, low: 1.19, close: 1.2 };
const base = [ref];
const fut = Array.from({ length: 30 }, (_, i) => ({ time: 101 + i, open: 1.2, high: 1.2 + 0.001 * (i + 1), low: 1.2 - 0.002, close: 1.2 + 0.0005 * (i + 1) }));
const out = resultadoPosterior(base, fut, 0.001);
assert.strictEqual(out.disponible, true);
assert.deepStrictEqual(out.referencia, { time: 100, close: 1.2 });
const h = n => out.horizontes.find(x => x.velas === n);
assert.strictEqual(h(12).completo, true); assert.strictEqual(h(24).completo, true); assert.strictEqual(h(48).completo, false);
assert.strictEqual(h(12).cierre, fut[11].close);
assert.strictEqual(h(12).delta_cierre, 0.006);
assert.strictEqual(h(12).maximo_subio, 0.012);
assert.strictEqual(h(12).maximo_bajo, 0.002);
assert.strictEqual(h(12).delta_cierre_en_atr, 6);
assert.strictEqual(out.velas.length, 30);
assert.strictEqual(base.length, 1);   // no toca las velas del análisis
assert.ok(!('delta_cierre_en_atr' in resultadoPosterior(base, fut, null).horizontes[0]));

console.log('OK — exportar caso: limpiar, ATR y resultado posterior');
