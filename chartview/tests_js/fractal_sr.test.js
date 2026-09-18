// Pruebas del motor S/R Fractal. Ejecutar:  node chartview/tests_js/fractal_sr.test.js
const assert = require("assert");
const F = require("../static/chartview/fractal_sr.js");

function velas(n, seed) {
  let s = seed; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const out = []; let p = 1.085, t = 1_700_000_000;
  for (let i = 0; i < n; i++) {
    let d = (rnd() - 0.5) * 0.0012;
    if (p > 1.0900) d -= 0.0009;          // techo "natural" en 1.0900
    if (p < 1.0800) d += 0.0009;          // piso "natural" en 1.0800
    const o = p; p += d;
    out.push({ time: t, open: o, close: p,
               high: Math.max(o, p) + rnd() * 0.0004, low: Math.min(o, p) - rnd() * 0.0004 });
    t += 4 * 3600;
  }
  return out;
}

const c = velas(900, 7);
const r = F.calcular(c);

// 1) produce zonas y cada una está bien formada
assert.ok(r && r.zones.length > 0, "debe encontrar zonas");
r.zones.forEach(z => {
  assert.ok(z.top > z.bottom, "top > bottom");
  assert.ok(z.fuerza >= 0 && z.fuerza <= 100, "fuerza en 0-100");
  assert.ok(z.nFractales >= 2, "al menos 2 fractales por zona");
  if (z.tipo === "R") assert.ok(z.bottom > r.close, "resistencia arriba del precio");
  if (z.tipo === "S") assert.ok(z.top < r.close, "soporte abajo del precio");
});

// 2) sin look-ahead: el score de un fractal no cambia si se corta la historia en su confirmación
let revisados = 0;
r.fractals.filter((_, k) => k % 5 === 0).forEach(f => {
  const r2 = F.calcular(c.slice(0, f.confirmIdx + 2));
  if (!r2) return;
  const g = r2.fractals.find(x => x.idx === f.idx && x.isHigh === f.isHigh);
  assert.ok(g, "el fractal ya existe en su vela de confirmación");
  assert.strictEqual(g.order, f.order);
  assert.ok(Math.abs(g.score - f.score) < 1e-9, "mismo score");
  revisados++;
});
assert.ok(revisados > 10);

// 3) datos insuficientes -> null, sin romper
assert.strictEqual(F.calcular(c.slice(0, 10)), null);
assert.strictEqual(F.calcular([]), null);

console.log(`OK — ${r.zones.length} zonas, ${r.fractals.length} fractales, ${revisados} verificaciones de causalidad`);
