// Pruebas del motor Tzotchev Trend. Ejecutar:  node chartview/tests_js/tzotchev_trend.test.js
const assert = require("assert");
const T = require("../static/chartview/tzotchev_trend.js");

// Velas sintéticas deterministas. `deriva` = retorno medio por vela.
function velas(n, seed, deriva, vol) {
  let s = seed; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const out = []; let p = 100, t = 1_700_000_000;
  for (let i = 0; i < n; i++) {
    const o = p;
    p *= Math.exp(deriva + (rnd() - 0.5) * 2 * vol);
    out.push({ time: t, open: o, close: p,
               high: Math.max(o, p) * (1 + rnd() * 0.002), low: Math.min(o, p) * (1 - rnd() * 0.002) });
    t += 4 * 3600;
  }
  return out;
}

// Normal acumulada por un camino INDEPENDIENTE (integración de Simpson de la
// densidad), para no validar la aproximación de A&S contra sí misma.
function phiExacta(z) {
  const f = x => Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
  const N = 2000, a = -10, h = (z - a) / N;
  let s = f(a) + f(z);
  for (let i = 1; i < N; i++) s += f(a + i * h) * (i % 2 ? 4 : 2);
  return s * h / 3;
}

// ST de una ventana, escrito aparte con bucles simples (sin compartir código con el motor).
function stDirecto(precios, i, t) {
  const rets = [];
  for (let j = i - t + 1; j <= i; j++) rets.push(Math.log(precios[j]) - Math.log(precios[j - 1]));
  const m = rets.reduce((a, b) => a + b, 0) / t;
  const sd = Math.sqrt(rets.reduce((a, b) => a + (b - m) * (b - m), 0) / (t - 1));
  const tv = Math.sqrt(t) * m / sd;
  return 2 * phiExacta(tv) - 1;
}

// 1) la normal acumulada: valores conocidos y contra la integración independiente
assert.ok(Math.abs(T._internals.phi(0) - 0.5) < 1e-6);
assert.ok(Math.abs(T._internals.phi(1) - 0.8413447) < 1e-6);
assert.ok(Math.abs(T._internals.phi(1.96) - 0.9750021) < 1e-6);
assert.ok(Math.abs(T._internals.phi(-1.96) - 0.0249979) < 1e-6);
[-4, -2.5, -0.7, 0.3, 1.4, 3.2].forEach(z =>
  assert.ok(Math.abs(T._internals.phi(z) - phiExacta(z)) < 1e-6, `phi(${z}) contra integración`));

const c = velas(600, 11, 0.0002, 0.004);
const r = T.calcular(c);
const ohlc4 = c.map(v => (v.open + v.high + v.low + v.close) / 4);

// 2) arranque: cada ventana empieza en la vela T; la línea, cuando ya están las tres (60)
assert.strictEqual(r.stF.findIndex(x => x !== null), 20);
assert.strictEqual(r.stM.findIndex(x => x !== null), 30);
assert.strictEqual(r.stS.findIndex(x => x !== null), 60);
assert.strictEqual(r.score.findIndex(x => x !== null), 60, "la línea espera a las tres ventanas");
assert.ok(r.score.slice(0, 60).every(x => x === null));

// 3) todo dato queda dentro de -1..+1, y el score es el promedio de las tres lecturas
r.score.forEach((v, i) => {
  if (v === null) return;
  assert.ok(v >= -1 && v <= 1, "score en -1..+1");
  const prom = (r.stM[i] + r.stF[i] + r.stS[i]) / 3;
  assert.ok(Math.abs(v - prom) < 1e-12, "score = promedio de las tres lecturas");
});

// 4) cada lectura coincide con el cálculo directo e independiente
[60, 61, 150, 333, 599].forEach(i => {
  assert.ok(Math.abs(r.stF[i] - stDirecto(ohlc4, i, 20)) < 1e-5, `ST(20) en ${i}`);
  assert.ok(Math.abs(r.stM[i] - stDirecto(ohlc4, i, 30)) < 1e-5, `ST(30) en ${i}`);
  assert.ok(Math.abs(r.stS[i] - stDirecto(ohlc4, i, 60)) < 1e-5, `ST(60) en ${i}`);
});

// 5) tendencia clara arriba → +1 y LONG; clara abajo → -1 y SHORT
const sube = T.calcular(velas(300, 5, 0.004, 0.002));
assert.ok(sube.ultimo.score > 0.95 && sube.ultimo.state === 1, "tendencia alcista fuerte");
const baja = T.calcular(velas(300, 5, -0.004, 0.002));
assert.ok(baja.ultimo.score < -0.95 && baja.ultimo.state === -1, "tendencia bajista fuerte");

// 6) regla de estado: cruza solo con el cierre más allá del nivel, y entre ambos mantiene
let vistoLong = false, vistoShort = false, vistoRetencion = false;
r.state.forEach((s, i) => {
  const v = r.score[i];
  if (v === null) { assert.strictEqual(s, i ? r.state[i - 1] : 0); return; }
  if (v > 0.2) { assert.strictEqual(s, 1); vistoLong = true; }
  else if (v < -0.2) { assert.strictEqual(s, -1); vistoShort = true; }
  else { assert.strictEqual(s, r.state[i - 1], "entre niveles mantiene"); if (s !== 0) vistoRetencion = true; }
});
assert.ok(vistoLong && vistoShort && vistoRetencion, "la serie de prueba ejercita las tres ramas");

// 7) los giros marcan exactamente el cambio de estado
r.state.forEach((s, i) => {
  assert.strictEqual(r.flipUp[i], i > 0 && s === 1 && r.state[i - 1] !== 1);
  assert.strictEqual(r.flipDn[i], i > 0 && s === -1 && r.state[i - 1] !== -1);
});
assert.ok(r.flipUp.some(Boolean) && r.flipDn.some(Boolean), "hay giros en ambos sentidos");

// 8) SIN LOOK-AHEAD: recortar la historia en cualquier vela no cambia nada de lo ya calculado
for (let k = 60; k < c.length; k += 17) {
  const corte = T.calcular(c.slice(0, k + 1));
  for (let i = 0; i <= k; i++) {
    assert.strictEqual(corte.score[i], r.score[i], `score estable en ${i} (corte ${k})`);
    assert.strictEqual(corte.state[i], r.state[i], `estado estable en ${i} (corte ${k})`);
    assert.strictEqual(corte.flipUp[i], r.flipUp[i]);
    assert.strictEqual(corte.flipDn[i], r.flipDn[i]);
  }
}

// 9) vela en formación: su estado NO se actualiza, hereda el de la anterior; el resto es idéntico
const viva = T.calcular(c, { ultimaCerrada: false });
const u = c.length - 1;
assert.strictEqual(viva.state[u], viva.state[u - 1], "la vela en formación hereda el estado");
assert.strictEqual(viva.score[u], r.score[u], "pero la línea sí mide la vela en formación");
for (let i = 0; i < u; i++) assert.strictEqual(viva.state[i], r.state[i]);
// ...y esa diferencia existe de verdad: en cada giro, la misma vela vista "en formación"
// NO gira (el estado se confirma recién al cerrar) y vista cerrada sí.
const giros = r.state.map((_, i) => i).filter(i => r.flipUp[i] || r.flipDn[i]);
assert.ok(giros.length >= 2, "la serie tiene giros para probar");
giros.forEach(k => {
  const cerrada = T.calcular(c.slice(0, k + 1));
  const enFormacion = T.calcular(c.slice(0, k + 1), { ultimaCerrada: false });
  assert.notStrictEqual(cerrada.state[k], cerrada.state[k - 1], "cerrada: gira");
  assert.strictEqual(enFormacion.state[k], cerrada.state[k - 1], "en formación: no gira todavía");
  assert.ok(!enFormacion.flipUp[k] && !enFormacion.flipDn[k], "sin giro hasta que cierre");
});

// 10) solo la ventana principal: la línea ES esa ventana y arranca en la vela 30
const sola = T.calcular(c, { usarRapida: false, usarLenta: false });
assert.strictEqual(sola.score.findIndex(x => x !== null), 30);
sola.score.forEach((v, i) => assert.strictEqual(v, sola.stM[i]));

// 11) precio plano → sin lectura (desvío cero), no NaN; entradas inválidas fallan claro
const plano = Array.from({ length: 100 }, (_, i) => ({ time: i, open: 5, high: 5, low: 5, close: 5 }));
const rp = T.calcular(plano);
assert.ok(rp.score.every(x => x === null) && rp.state.every(s => s === 0));
assert.throws(() => T.calcular(c, { lenM: 1 }), /lenM/);
assert.throws(() => T.calcular(c, { lenF: 2.5 }), /lenF/);
assert.strictEqual(T.calcular([]).n, 0);
assert.strictEqual(T.calcular([]).ultimo, null);

// 12) un precio inválido (<= 0) no contamina el resto con NaN: queda como "sin dato"
const sucio = velas(200, 3, 0.001, 0.003);
sucio[100] = { time: sucio[100].time, open: 0, high: 0, low: 0, close: 0 };
const rs = T.calcular(sucio);
rs.score.forEach(v => assert.ok(v === null || Number.isFinite(v), "sin NaN"));
rs.state.forEach(s => assert.ok([-1, 0, 1].includes(s)));

console.log("OK — Tzotchev Trend: normal acumulada, lecturas vs cálculo independiente, estado/retención, giros, causalidad, vela en formación, ventana única y casos límite");
