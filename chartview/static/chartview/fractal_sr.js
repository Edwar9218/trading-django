/* ══════════════════════════════════════════════════════════════════
 * S/R FRACTAL — zonas de soporte/resistencia por agrupación de fractales
 *
 * Idea: los fractales (pivotes) marcan dónde el precio se detuvo y giró.
 * Donde se AGRUPAN varios fractales importantes hay una barrera fuerte.
 *
 *  1) Fractales multiescala: un pivote se evalúa con ventanas 2,3,5,8,
 *     13,21,34 velas a cada lado. Cuanto más grande la ventana en la que
 *     sigue siendo pivote, más "alto" en la jerarquía fractal está.
 *  2) Score de cada fractal (0-100), pesos según la revisión bibliográfica:
 *       35% confirmación en temporalidades superiores (x3 y x12)
 *       30% prominencia del giro medida en ATR
 *       25% jerarquía de escalas (orden máximo alcanzado)
 *     (El régimen DFA-Hurst se dejó fuera del gráfico: la evidencia de que
 *      mejore la calidad de los giros es débil — ver investigación.)
 *  3) Agrupación 1D de los precios de los fractales en ZONAS (tolerancia
 *     en ATR y ancho máximo), mezclando máximos y mínimos: una zona que
 *     fue resistencia y luego soporte (cambio de rol) suma polaridad.
 *  4) Reacciones de cada zona (rebotes / cruces) como CONTEXTO: no entran
 *     en la fuerza porque están sesgadas (la zona se dibuja donde el precio
 *     ya giró). Fuerza = 80% densidad de fractales + 20% cambio de rol.
 *
 * Todo es causal: cada fractal solo se usa a partir de su vela de
 * confirmación (pivote + k velas), y la confirmación HTF solo cuenta si la
 * vela superior ya estaba cerrada en ese momento. No repinta hacia atrás.
 * ══════════════════════════════════════════════════════════════════ */
(function (root) {
  "use strict";

  const DEFAULTS = {
    orders: [2, 3, 5, 8, 13, 21, 34],
    minOrder: 3,            // fractales de orden < 3 no forman zonas (ruido)
    atrLen: 14,
    promCap: 6,             // prominencia (en ATR) que da puntuación máxima
    htfMults: [3, 12],
    htfTolAtr: 0.10,
    weights: { htf: 0.35, prom: 0.30, scale: 0.25 },
    bandwidthAtr: 0.35,     // ancho de la campana de cada fractal
    zoneRadiusAtr: 0.60,    // fractales a más de esto del pico no entran a la zona
    minSepAtr: 1.00,        // separación mínima entre centros de zonas
    mergeGapAtr: 0.25,      // zonas que se tocan (hueco < esto) se unen en una sola
    maxMergedWidthAtr: 1.6, // ...siempre que la zona unida no quede demasiado ancha
    reactAtr: 0.50,         // rechazo = el precio se aleja al menos esto tras tocar la zona
    maxDistAtr: 12,         // zonas más lejos que esto no se muestran (no son accionables)
    minZoneWidthAtr: 0.20,
    halfLifeBars: 150,      // los fractales viejos pesan la mitad cada 150 velas
    minMembers: 2,          // una zona necesita al menos 2 fractales
    maxZonesPerSide: 3,
    minStrength: 35,
  };

  function atrWilder(h, l, c, len) {
    const n = h.length, out = new Array(n).fill(null);
    if (n < len) return out;
    const tr = new Array(n);
    tr[0] = h[0] - l[0];
    for (let i = 1; i < n; i++) {
      tr[i] = Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1]));
    }
    let s = 0;
    for (let i = 0; i < len; i++) s += tr[i];
    out[len - 1] = s / len;
    for (let i = len; i < n; i++) out[i] = (out[i - 1] * (len - 1) + tr[i]) / len;
    for (let i = 0; i < len - 1; i++) out[i] = out[len - 1];   // relleno inicial
    return out;
  }

  // Pivote de orden k: estricto a la izquierda, >= a la derecha (evita duplicados en empates).
  function isPivot(a, i, k, isHigh) {
    for (let j = 1; j <= k; j++) {
      if (isHigh) { if (!(a[i] > a[i - j]) || !(a[i] >= a[i + j])) return false; }
      else        { if (!(a[i] < a[i - j]) || !(a[i] <= a[i + j])) return false; }
    }
    return true;
  }

  function medianStep(times) {
    const d = [];
    for (let i = 1; i < times.length; i++) d.push(times[i] - times[i - 1]);
    d.sort((x, y) => x - y);
    return d.length ? d[Math.floor(d.length / 2)] : 0;
  }

  // Velas de temporalidad superior agrupando por bloques de tiempo.
  function buildHTF(candles, bucketSec) {
    const bars = [], barOf = new Array(candles.length);
    let cur = null, curId = null;
    candles.forEach((c, i) => {
      const id = Math.floor(c.time / bucketSec);
      if (id !== curId) {
        cur = { high: c.high, low: c.low, lastIdx: i };
        bars.push(cur); curId = id;
      } else {
        cur.high = Math.max(cur.high, c.high);
        cur.low = Math.min(cur.low, c.low);
        cur.lastIdx = i;
      }
      barOf[i] = bars.length - 1;
    });
    return { bars, barOf };
  }

  // ¿La vela HTF que contiene al pivote es un fractal 2+2 con el mismo
  // extremo, y ya estaba confirmada (vela b+2 cerrada) al confirmar el pivote?
  function htfConfirms(htf, i, isHigh, price, confirmIdx, tol, lastClosed) {
    const b = htf.barOf[i], B = htf.bars;
    if (b < 2 || b + 2 >= B.length) return false;
    const right = B[b + 2];
    // la última vela HTF puede estar incompleta: exigir que b+3 exista o que
    // right termine antes del final de los datos cerrados
    const rightClosed = (b + 3 < B.length) || right.lastIdx < lastClosed;
    if (!rightClosed || right.lastIdx > confirmIdx) return false;
    const v = isHigh ? B[b].high : B[b].low;
    if (Math.abs(v - price) > tol) return false;
    if (isHigh) return v > B[b - 1].high && v > B[b - 2].high && v >= B[b + 1].high && v >= B[b + 2].high;
    return v < B[b - 1].low && v < B[b - 2].low && v <= B[b + 1].low && v <= B[b + 2].low;
  }

  function tierOf(score) {
    if (score >= 80) return "MAYOR";
    if (score >= 60) return "IMPORTANTE";
    if (score >= 40) return "MENOR";
    return "BAJO";
  }

  function calcular(candles, userOpts) {
    const o = Object.assign({}, DEFAULTS, userOpts || {});
    o.weights = Object.assign({}, DEFAULTS.weights, (userOpts || {}).weights || {});
    const n = candles ? candles.length : 0;
    const orders = o.orders.slice().sort((a, b) => a - b);
    if (n < orders[0] * 2 + o.atrLen + 5) return null;

    const H = candles.map(c => c.high), L = candles.map(c => c.low), C = candles.map(c => c.close);
    const times = candles.map(c => c.time);
    const atr = atrWilder(H, L, C, o.atrLen);
    const last = n - 2;                     // la última vela puede estar en formación
    const step = medianStep(times);
    const htfs = step > 0 ? o.htfMults.map(m => buildHTF(candles, step * m)) : [];

    // ── 1+2) fractales multiescala con score ──
    const fractals = [];
    for (let i = 1; i <= last - orders[0]; i++) {
      for (const isHigh of [true, false]) {
        let kmax = 0, level = -1;
        for (let lv = 0; lv < orders.length; lv++) {
          const k = orders[lv];
          if (i - k < 0 || i + k > last) break;
          if (!isPivot(isHigh ? H : L, i, k, isHigh)) break;
          kmax = k; level = lv;
        }
        if (kmax === 0) continue;
        const price = isHigh ? H[i] : L[i];
        let opp = price;
        for (let j = i - kmax; j <= i + kmax; j++) opp = isHigh ? Math.min(opp, L[j]) : Math.max(opp, H[j]);
        const a = atr[i] || 0;
        const promAtr = a > 0 ? Math.abs(price - opp) / a : 0;
        const confirmIdx = i + kmax;
        const tol = Math.max(o.htfTolAtr * a, 1e-10);
        let conf = 0;
        htfs.forEach(h => { if (htfConfirms(h, i, isHigh, price, confirmIdx, tol, last)) conf++; });

        const w = o.weights;
        const sScale = (level + 1) / orders.length;
        const sProm = Math.min(promAtr / o.promCap, 1);
        const sHtf = htfs.length ? conf / htfs.length : 0;
        const wH = htfs.length ? w.htf : 0;
        const score = 100 * (w.scale * sScale + w.prom * sProm + wH * sHtf) / (w.scale + w.prom + wH);

        fractals.push({
          idx: i, time: times[i], price, isHigh, order: kmax, level,
          confirmIdx, promAtr, htfConf: conf, htfAvail: htfs.length,
          score, tier: tierOf(score),
        });
      }
    }

    // ── 3) agrupación en zonas: picos de densidad de fractales ──
    // Cada fractal aporta una campana gaussiana (ancho en ATR) pesada por su
    // score y su antigüedad. Los picos de esa densidad son los precios donde
    // más fractales importantes se amontonan; alrededor de cada pico se arma
    // la zona. Así las zonas no se "encadenan" unas con otras.
    const aNow = atr[last] || atr[n - 1] || 0;
    if (aNow <= 0) return { zones: [], fractals, atr: aNow };
    const pool = fractals.filter(f => f.order >= o.minOrder);
    pool.forEach(f => { f.peso = f.score * Math.pow(0.5, (last - f.idx) / o.halfLifeBars); });
    const bw = o.bandwidthAtr * aNow, radius = o.zoneRadiusAtr * aNow, minW = o.minZoneWidthAtr * aNow;
    const clusters = [];
    if (pool.length) {
      const pMin = Math.min(...pool.map(f => f.price)) - aNow, pMax = Math.max(...pool.map(f => f.price)) + aNow;
      const g = 0.05 * aNow, nG = Math.min(4000, Math.ceil((pMax - pMin) / g) + 1);
      const dens = new Array(nG).fill(0);
      for (let k = 0; k < nG; k++) {
        const x = pMin + k * (pMax - pMin) / (nG - 1);
        let d = 0;
        for (const f of pool) { const z = (x - f.price) / bw; if (z > -4 && z < 4) d += f.peso * Math.exp(-0.5 * z * z); }
        dens[k] = d;
      }
      const peaks = [];
      for (let k = 1; k < nG - 1; k++) if (dens[k] > 0 && dens[k] >= dens[k - 1] && dens[k] > dens[k + 1]) {
        peaks.push({ x: pMin + k * (pMax - pMin) / (nG - 1), d: dens[k] });
      }
      peaks.sort((a, b) => b.d - a.d);
      const accepted = [];
      for (const pk of peaks) if (accepted.every(a => Math.abs(a.x - pk.x) >= o.minSepAtr * aNow)) accepted.push(pk);
      accepted.forEach(pk => clusters.push({ center: pk.x, members: [] }));
      for (const f of pool) {
        let best = null, bd = Infinity;
        clusters.forEach(cl => { const d = Math.abs(cl.center - f.price); if (d < bd) { bd = d; best = cl; } });
        if (best && bd <= radius) best.members.push(f);
      }
    }

    // ── 3b) unir zonas que se solapan o se tocan: en el gráfico se leen como
    // una sola barrera y dos etiquetas pegadas solo confunden.
    const bandas = clusters.filter(cl => cl.members.length)
      .map(cl => ({ members: cl.members, lo: Math.min(...cl.members.map(f => f.price)), hi: Math.max(...cl.members.map(f => f.price)) }))
      .sort((a, b) => a.lo - b.lo);
    const unidas = [];
    bandas.forEach(b => {
      const u = unidas[unidas.length - 1];
      if (u && b.lo - u.hi <= o.mergeGapAtr * aNow && Math.max(u.hi, b.hi) - u.lo <= o.maxMergedWidthAtr * aNow) {
        u.members = u.members.concat(b.members); u.hi = Math.max(u.hi, b.hi);
      } else unidas.push({ members: b.members.slice(), lo: b.lo, hi: b.hi });
    });

    // ── 4) métricas de cada zona ──
    const close = C[last];
    let zones = unidas.filter(cl => cl.members.length >= o.minMembers).map(cluster => {
      const cl = cluster.members.sort((x, y) => x.price - y.price);
      let bottom = cl[0].price, top = cl[cl.length - 1].price;
      if (top - bottom < minW) { const mid = (top + bottom) / 2; top = mid + minW / 2; bottom = mid - minW / 2; }
      let raw = 0, nHi = 0, nLo = 0, firstIdx = Infinity, firstConfirm = Infinity, lastIdx = -1, best = 0;
      cl.forEach(f => {
        raw += f.peso;
        f.isHigh ? nHi++ : nLo++;
        firstIdx = Math.min(firstIdx, f.idx);
        firstConfirm = Math.min(firstConfirm, f.confirmIdx);
        lastIdx = Math.max(lastIdx, f.idx);
        best = Math.max(best, f.score);
      });

      // Reacciones de la zona (DESCRIPTIVO, no probabilidad). Ojo: la zona se
      // ubica justamente donde el precio giró, así que sus rebotes pasados
      // salen altos por construcción — en un paseo aleatorio también darían
      // ~70%. Por eso NO entran en la fuerza; solo se muestran como contexto.
      // Regla simétrica:
      //  - una visita empieza cuando el precio llega a la MITAD de la zona
      //    viniendo desde afuera (estando alejado al menos reactAtr);
      //  - termina cuando se aleja reactAtr más allá de un borde:
      //    por el lado del que vino = REBOTE, por el otro = CRUCE;
      //  - una visita todavía sin resolver al final no se cuenta.
      // Solo desde que la zona existe (primer fractal confirmado): sin mirar atrás.
      const midZ = (top + bottom) / 2;
      const lejos = j => {                       // lado si está lejos, 0 si no
        const r = o.reactAtr * (atr[j] || aNow);
        const arriba = L[j] >= top + r, abajo = H[j] <= bottom - r;
        const salioArriba = H[j] >= top + r, salioAbajo = L[j] <= bottom - r;
        return { arriba, abajo, salioArriba, salioAbajo };
      };
      let rej = 0, cross = 0, from = 0, enVisita = false;
      let lastTouch = firstIdx, ultimoEvento = null;
      for (let j = firstConfirm + 1; j <= last; j++) {
        const e = lejos(j);
        if (L[j] <= top && H[j] >= bottom) lastTouch = j;
        if (!enVisita) {
          if (e.arriba) from = 1;
          else if (e.abajo) from = -1;
          if (from !== 0 && L[j] <= midZ && H[j] >= midZ) enVisita = true;
          else continue;
        }
        // ¿por dónde salió? Si en la misma vela sale por los dos lados, decide el cierre.
        let salida = 0;
        if (e.salioArriba && e.salioAbajo) salida = C[j] > midZ ? 1 : -1;
        else if (e.salioArriba) salida = 1;
        else if (e.salioAbajo) salida = -1;
        if (salida === 0) continue;
        if (salida === from) { rej++; ultimoEvento = { tipo: "rechazo", idx: j }; }
        else                 { cross++; ultimoEvento = { tipo: "cruce", idx: j }; }
        enVisita = false; from = salida;
      }
      const tipo = close > top ? "S" : close < bottom ? "R" : "Z";
      return {
        top, bottom, mid: (top + bottom) / 2, raw, best,
        nFractales: cl.length, nAltos: nHi, nBajos: nLo, polaridad: nHi > 0 && nLo > 0,
        rechazos: rej, cruces: cross, visitaEnCurso: enVisita,
        ultimoEvento: ultimoEvento ? { tipo: ultimoEvento.tipo, hace: last - ultimoEvento.idx } : null,
        ultimoToqueHace: last - Math.max(lastTouch, lastIdx),
        startTime: times[firstIdx], confirmTime: times[Math.min(firstConfirm, n - 1)],
        lastTouchTime: times[Math.max(lastTouch, lastIdx)],
        tipo, distAtr: tipo === "Z" ? 0 : Math.min(Math.abs(close - top), Math.abs(close - bottom)) / aNow,
        miembros: cl.map(f => ({ time: f.time, price: f.price, isHigh: f.isHigh, score: f.score })),
      };
    });

    const maxRaw = zones.reduce((m, z) => Math.max(m, z.raw), 0) || 1;
    zones.forEach(z => {
      // 80% densidad de fractales importantes (recientes pesan más) + 20% si
      // cambió de rol. El historial de rebotes no entra (ver arriba).
      z.fuerza = 100 * (0.80 * (z.raw / maxRaw) + 0.20 * (z.polaridad ? 1 : 0));
      z.clase = tierOf(z.fuerza);
    });
    zones = zones.filter(z => z.fuerza >= o.minStrength && z.distAtr <= o.maxDistAtr);

    const pick = arr => arr.sort((a, b) => b.fuerza - a.fuerza).slice(0, o.maxZonesPerSide)
                           .sort((a, b) => a.distAtr - b.distAtr);
    const resist = pick(zones.filter(z => z.tipo === "R"));
    const soport = pick(zones.filter(z => z.tipo === "S"));
    const dentro = zones.filter(z => z.tipo === "Z");
    resist.forEach((z, i) => { z.etiqueta = `R${i + 1}`; });
    soport.forEach((z, i) => { z.etiqueta = `S${i + 1}`; });
    dentro.forEach(z => { z.etiqueta = "EN ZONA"; });

    return {
      zones: [...dentro, ...resist, ...soport],
      fractals,
      mayores: fractals.filter(f => f.score >= 80),
      atr: aNow, close, lastTime: times[n - 1],
    };
  }

  const api = { calcular, DEFAULTS, _internals: { atrWilder, isPivot, buildHTF, htfConfirms } };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.FractalSR = api;
})(typeof window !== "undefined" ? window : this);
