// Pruebas del motor de secuencia estructural.
// Ejecutar:  node chartview/tests_js/secuencia_fractal.test.js
const assert = require("assert");
const S = require("../static/chartview/secuencia_fractal.js");

// ── Generador de velas con tendencia + ruido: produce rupturas, impulsos
// y huecos suficientes para ejercitar la secuencia completa. ──
function velas(n, seed) {
  let s = seed; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const out = []; let p = 1.0850, t = 1_700_000_000;
  for (let i = 0; i < n; i++) {
    const ciclo = Math.sin(i / 37) * 0.0006;               // idas y vueltas
    let d = (rnd() - 0.5) * 0.0010 + ciclo * 0.35;
    if (rnd() > 0.93) d *= 5;                              // velas de impulso
    const o = p; p += d;
    out.push({
      time: t, open: o, close: p,
      high: Math.max(o, p) + rnd() * 0.0003,
      low: Math.min(o, p) - rnd() * 0.0003,
    });
    t += 3600;
  }
  return out;
}

const c = velas(1200, 11);
const r = S.calcular(c);

// 1) devuelve algo coherente
assert.ok(r, "debe calcular con 1200 velas");
assert.ok(r.setups.length > 0, "debe encontrar estructuras");
assert.ok(r.stats.imbalances > 0, "debe encontrar imbalances");

// 2) la secuencia es un orden estricto: no hay etapa sin las anteriores
r.setups.forEach(s => {
  if (s.ruptura) assert.ok(s.fractal, "ruptura sin fractal");
  if (s.desplazamiento) assert.ok(s.ruptura, "desplazamiento sin ruptura");
  if (s.imbalance) assert.ok(s.desplazamiento, "imbalance sin desplazamiento");
  if (s.retest) assert.ok(s.imbalance, "retesteo sin imbalance");
  if (s.continuacion) assert.ok(s.retest, "continuación sin retesteo");
  // y el score es exactamente la suma de las etapas alcanzadas
  const esperado = 20 * [s.fractal, s.ruptura, s.desplazamiento, s.imbalance].filter(Boolean).length
                 + 10 * [s.retest, s.continuacion].filter(Boolean).length;
  assert.strictEqual(s.score, esperado, "score = suma de etapas");
  assert.ok(s.score >= 0 && s.score <= 100, "score en 0-100");
});

// 3) orden temporal de cada etapa (nada ocurre antes de lo que lo habilita)
r.setups.forEach(s => {
  assert.ok(s.fractal.confirmIdx > s.fractal.idx, "el fractal se confirma después");
  if (s.ruptura) assert.ok(s.ruptura.time > s.fractal.time, "ruptura posterior al fractal");
  if (s.imbalance) assert.ok(s.imbalance.time >= s.ruptura.time, "imbalance no anterior a la ruptura");
  if (s.retest) assert.ok(s.retest.time > s.imbalance.time, "retesteo posterior al imbalance");
  if (s.continuacion) assert.ok(s.continuacion.time >= s.retest.time, "continuación no anterior al retesteo");
});

// 4) geometría del imbalance: el hueco de 3 velas realmente existe
r.setups.filter(s => s.imbalance).forEach(s => {
  const z = s.imbalance, j = z.idx;
  assert.ok(z.top > z.bottom, "zona con altura positiva");
  if (s.escala === "TF") {           // en HTF los índices no son de `c`
    if (s.dir > 0) assert.ok(c[j].low > c[j - 2].high, "FVG alcista real");
    else assert.ok(c[j].high < c[j - 2].low, "FVG bajista real");
  }
});

// 5) invalidación siempre explicada
r.setups.filter(s => s.estado === "INVALIDADO").forEach(s => {
  assert.ok(s.motivo && s.motivo.length > 5, "toda invalidación dice por qué");
  assert.ok(s.estadoAlCerrar, "se recuerda la etapa alcanzada");
});

// 6) SIN REPINTADO: recortar la historia en la vela del evento reproduce
// exactamente el mismo evento. Es la prueba que importa de verdad.
let verificados = 0;
r.setups.filter(s => s.imbalance && s.escala === "TF").slice(0, 40).forEach(s => {
  const corte = c.slice(0, s.imbalance.idx + 2);     // +2: última vela en formación
  const r2 = S.calcular(corte);
  if (!r2) return;
  const g = r2.setups.find(x => x.fractal.idx === s.fractal.idx && x.dir === s.dir);
  assert.ok(g, "la estructura ya existía en su vela de confirmación");
  assert.ok(g.imbalance, "el imbalance ya estaba");
  assert.strictEqual(g.imbalance.idx, s.imbalance.idx, "mismo índice de imbalance");
  assert.ok(Math.abs(g.imbalance.top - s.imbalance.top) < 1e-12, "misma zona (top)");
  assert.ok(Math.abs(g.imbalance.bottom - s.imbalance.bottom) < 1e-12, "misma zona (bottom)");
  assert.strictEqual(g.ruptura.idx, s.ruptura.idx, "misma vela de ruptura");
  verificados++;
});
assert.ok(verificados >= 5, `pocas verificaciones de causalidad (${verificados})`);

// 7) un retesteo NO es "el precio se acercó": la vela debe tocar la zona
r.setups.filter(s => s.retest && s.escala === "TF").forEach(s => {
  const j = s.retest.idx, z = s.imbalance;
  assert.ok(c[j].low <= z.top && c[j].high >= z.bottom, "la vela del retesteo toca la zona");
  assert.ok(s.retest.pct >= 0 && s.retest.pct <= 1, "recorrido entre 0 y 1");
  assert.ok(["toque", "parcial", "profundo", "completo"].includes(s.retest.tipo));
});

// 8) fases aisladas: cada componente se puede probar por separado
const I = S._internals;
const ctx = I.contexto(c, S.DEFAULTS);
const fr = I.detectarFractales(ctx, 2);
assert.ok(fr.length > 20, "detectarFractales por sí solo");
fr.forEach(f => assert.strictEqual(f.confirmIdx, f.idx + 2, "confirmación = idx + k"));
const soloEstructura = I.faseEstructura(ctx, S.DEFAULTS, "TF");
assert.ok(soloEstructura.some(s => s.imbalance), "faseEstructura llega al imbalance");
assert.ok(soloEstructura.every(s => !s.retest), "faseEstructura NO sigue el retesteo");

// 9) penetración: 0 fuera, 1 cuando la atraviesa entera
const zonaFalsa = { top: 10, bottom: 8, dir: 1 };
const ctxFalso = { H: [12, 9.5, 7], L: [11, 9, 6] };
assert.strictEqual(I.penetracion(zonaFalsa, 0, ctxFalso), 0, "por encima: 0%");
assert.strictEqual(I.penetracion(zonaFalsa, 1, ctxFalso), 0.5, "hasta la mitad: 50%");
assert.strictEqual(I.penetracion(zonaFalsa, 2, ctxFalso), 1, "la atraviesa: 100%");

// 10) multitimeframe: la estructura sale del x4, el seguimiento de la base
const rH = S.calcular(c, { htfMult: 4 });
assert.ok(rH, "modo multitimeframe calcula");
assert.ok(rH.escala.includes("x4"));
rH.setups.filter(s => s.imbalance && s.retest).forEach(s => {
  assert.ok(s.imbalance.baseIdx != null, "el imbalance se mapea a la vela base");
  assert.ok(s.retest.idx > s.imbalance.baseIdx, "el retesteo empieza tras cerrar la vela superior");
});

// 11) datos insuficientes: null, sin romper
assert.strictEqual(S.calcular(c.slice(0, 15)), null);
assert.strictEqual(S.calcular([]), null);
assert.strictEqual(S.calcular(null), null);

// 12) las métricas de backtest no contaminan el estado
r.setups.forEach(s => {
  if (s.medicion) {
    assert.ok(s.continuacion, "solo se mide lo que continuó");
    assert.ok(typeof s.medicion.completa === "boolean", "se declara si la ventana está completa");
  }
});
assert.ok(r.stats.pctContinuacion >= 0 && r.stats.pctContinuacion <= 100);

console.log(
  `OK — ${r.setups.length} estructuras · ${r.stats.imbalances} imbalances · ` +
  `${r.stats.retesteos} retesteos · ${r.stats.continuaciones} continuaciones · ` +
  `${verificados} verificaciones de causalidad`
);
