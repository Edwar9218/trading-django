/* ══════════════════════════════════════════════════════════════════
 * SECUENCIA ESTRUCTURAL
 *   FRACTAL → RUPTURA → DESPLAZAMIENTO → IMBALANCE → RETESTEO → CONTINUACIÓN
 *
 * Hermano de fractal_sr.js: aquel agrupa fractales en ZONAS de soporte y
 * resistencia (dónde está la barrera); éste sigue la SECUENCIA que ocurre
 * cuando esa barrera se rompe (qué está haciendo el precio ahora).
 *
 * Qué hace y qué NO hace
 * ──────────────────────
 * Detecta estructuras YA OCURRIDAS y les asigna una puntuación de
 * confluencia 0-100. No predice, no opera, no modifica órdenes. Un score
 * alto significa "la secuencia se completó", no "va a seguir subiendo":
 * eso hay que medirlo con las estadísticas que devuelve `stats`.
 *
 * Máquina de estados (una por fractal confirmado)
 * ──────────────────────────────────────────────
 *   FRACTAL → BREAKOUT → DISPLACEMENT → IMBALANCE → RETEST
 *           → CONFIRMATION → COMPLETED
 *   En cualquier punto: INVALIDADO (siempre con el motivo concreto).
 *
 * Causalidad (sin repintado) — la regla que manda sobre todo lo demás
 * ──────────────────────────────────────────────────────────────────
 *  · Un fractal de orden k existe recién en la vela i+k, nunca en la i.
 *  · Cada transición se evalúa con la vela j y con lo anterior, jamás con
 *    velas posteriores. Por eso el recorrido es una sola pasada temporal.
 *  · La última vela puede estar en formación: se ignora (ultimaEnFormacion).
 *  · Consecuencia comprobable: recortar la historia en la vela en la que
 *    apareció un evento reproduce exactamente el mismo evento (ver tests).
 *  · Las métricas de backtest (MFE/MAE) sí miran hacia adelante — están
 *    separadas en `medicion` y marcadas `completa:false` mientras faltan
 *    velas. Nunca alimentan el score ni el estado.
 *
 * Multitimeframe
 * ──────────────
 * Con htfMult > 1 la ESTRUCTURA (fractal→imbalance) se busca sobre velas
 * agregadas (p.ej. H1 desde M15 con htfMult 4) y el SEGUIMIENTO (retesteo
 * y continuación) se sigue en la temporalidad base. El seguimiento arranca
 * en la vela base siguiente al cierre de la vela superior, no antes.
 *
 * Cada fase es una función pura exportada en `_internals`, para poder
 * probarla por separado.
 * ══════════════════════════════════════════════════════════════════ */
(function (root) {
  "use strict";

  const DEFAULTS = {
    // ── 1) fractal ──
    fractalK: 2,              // velas a cada lado para confirmar el pivote
    ultimaEnFormacion: true,  // no usar la última vela (todavía puede cambiar)

    // ── 2) ruptura + desplazamiento ──
    rupturaPorCierre: true,   // true = cierre más allá del fractal; false = mecha
    maxBarrasRuptura: 40,     // si el fractal no se rompe en N velas, se descarta
    maxBarrasDespl: 3,        // velas para que la ruptura se vuelva impulso
    cuerpoMult: 1.5,          // cuerpo del impulso / cuerpo medio reciente
    cuerpoMedLen: 20,
    rangoAtrMin: 1.0,         // rango del impulso medido en ATR
    atrLen: 14,

    // ── 3) imbalance (FVG de 3 velas) ──
    fvgVentana: 3,            // velas tras el desplazamiento para encontrarlo
    fvgMinAtr: 0.08,          // huecos más chicos que esto son ruido

    // ── 4) retesteo ──
    maxBarrasRetest: 120,     // sin retesteo en N velas, la estructura expira
    pctParcial: 0.25,         // % del imbalance recorrido: toque → parcial
    pctProfundo: 0.50,        // parcial → profundo

    // ── 5) continuación ──
    maxBarrasConfirm: 60,
    cuerpoRechazoMult: 0.5,   // vela de rechazo mínima, en cuerpos medios

    // ── backtest ──
    barrasMedicion: 50,       // velas para medir recorrido/retroceso posterior

    // ── multitimeframe ──
    htfMult: 0,               // 0 o 1 = desactivado; 4 = estructura en x4

    // ── salida ──
    maxVisibles: 8,           // estructuras dibujables que se devuelven
    scoreVisible: 60,         // score mínimo para considerarla dibujable
  };

  // Puntuación fija y auditable: la suma de las etapas alcanzadas.
  const PUNTOS = {
    fractal: 20, ruptura: 20, desplazamiento: 20,
    imbalance: 20, retest: 10, continuacion: 10,
  };

  const ORDEN_ESTADOS = ["NONE", "FRACTAL", "BREAKOUT", "DISPLACEMENT",
                         "IMBALANCE", "RETEST", "CONFIRMATION", "COMPLETED"];

  // ─────────────────────────────────────────────────────────────
  // Utilidades de serie
  // ─────────────────────────────────────────────────────────────
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
    for (let i = 0; i < len - 1; i++) out[i] = out[len - 1];
    return out;
  }

  // Cuerpo medio causal: promedio de |close-open| de las últimas `len`
  // velas hasta i inclusive. Al principio usa las que haya.
  function mediaCuerpo(o, c, len) {
    const n = o.length, out = new Array(n).fill(0);
    let acum = 0;
    for (let i = 0; i < n; i++) {
      acum += Math.abs(c[i] - o[i]);
      if (i >= len) acum -= Math.abs(c[i - len] - o[i - len]);
      out[i] = acum / Math.min(i + 1, len);
    }
    return out;
  }

  // Pivote de orden k: estricto a la izquierda, >= a la derecha (mismo
  // criterio que fractal_sr.js — evita contar dos veces los empates).
  function esPivote(a, i, k, isHigh) {
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

  function contexto(candles, o) {
    const n = candles.length;
    const H = candles.map(c => c.high), L = candles.map(c => c.low);
    const C = candles.map(c => c.close), O = candles.map(c => c.open);
    const T = candles.map(c => c.time);
    return {
      H, L, C, O, T, n,
      atr: atrWilder(H, L, C, o.atrLen),
      cuerpoMed: mediaCuerpo(O, C, o.cuerpoMedLen),
      last: o.ultimaEnFormacion ? n - 2 : n - 1,
    };
  }

  // Velas de temporalidad superior por bloques de tiempo. `baseFin[p]` es
  // el índice de la última vela base que compone la vela superior p: la
  // vela superior recién se sabe cerrada en baseFin[p]+1.
  function agregarHTF(candles, mult) {
    const paso = medianStep(candles.map(c => c.time));
    if (!paso || mult < 2) return null;
    const bucket = paso * mult;
    const out = [], baseFin = [];
    let cur = null, curId = null;
    candles.forEach((c, i) => {
      const id = Math.floor(c.time / bucket);
      if (id !== curId) {
        cur = { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close };
        out.push(cur); baseFin.push(i); curId = id;
      } else {
        cur.high = Math.max(cur.high, c.high);
        cur.low = Math.min(cur.low, c.low);
        cur.close = c.close;
        baseFin[baseFin.length - 1] = i;
      }
    });
    return { candles: out, baseFin };
  }

  // ─────────────────────────────────────────────────────────────
  // 1) FRACTALES CONFIRMADOS
  // ─────────────────────────────────────────────────────────────
  function detectarFractales(ctx, k) {
    const out = [];
    for (let i = k; i + k <= ctx.last; i++) {
      if (esPivote(ctx.H, i, k, true)) {
        out.push({ idx: i, confirmIdx: i + k, time: ctx.T[i], price: ctx.H[i], isHigh: true });
      }
      if (esPivote(ctx.L, i, k, false)) {
        out.push({ idx: i, confirmIdx: i + k, time: ctx.T[i], price: ctx.L[i], isHigh: false });
      }
    }
    return out;
  }

  // ─────────────────────────────────────────────────────────────
  // 3) IMBALANCE / FAIR VALUE GAP de 3 velas, cerrado en la vela j
  //    Alcista: low[j] > high[j-2].  Bajista: high[j] < low[j-2].
  // ─────────────────────────────────────────────────────────────
  function buscarFVG(ctx, j, dir, o) {
    if (j < 2) return null;
    const a = ctx.atr[j] || 0;
    const top = dir > 0 ? ctx.L[j] : ctx.L[j - 2];
    const bottom = dir > 0 ? ctx.H[j - 2] : ctx.H[j];
    const tam = top - bottom;
    if (tam <= 0) return null;
    if (a > 0 && tam / a < o.fvgMinAtr) return null;
    return { top, bottom, mid: (top + bottom) / 2, tamAtr: a > 0 ? tam / a : 0 };
  }

  // ─────────────────────────────────────────────────────────────
  // Estructura (setup) y su puntuación
  // ─────────────────────────────────────────────────────────────
  let _seq = 0;

  function nuevoSetup(f, escala) {
    return {
      id: ++_seq,
      dir: f.isHigh ? 1 : -1,          // fractal máximo → alcista; mínimo → bajista
      escala,
      fractal: { idx: f.idx, time: f.time, price: f.price, confirmIdx: f.confirmIdx },
      ruptura: null, desplazamiento: null, imbalance: null,
      retest: null, continuacion: null,
      estado: "FRACTAL",
      estadoAlCerrar: null, motivo: null, porTiempo: false,
      cerrado: false, cierreIdx: null, cierreTime: null,
      extremo: f.price,                // extremo alcanzado a favor desde la ruptura
      extremoRef: null,                // congelado al empezar el retesteo
      eventos: [],
      medicion: null, retrocesoAtr: null, barrasHastaContinuacion: null,
    };
  }

  function score(s) {
    let p = 0;
    if (s.fractal) p += PUNTOS.fractal;
    if (s.ruptura) p += PUNTOS.ruptura;
    if (s.desplazamiento) p += PUNTOS.desplazamiento;
    if (s.imbalance) p += PUNTOS.imbalance;
    if (s.retest) p += PUNTOS.retest;
    if (s.continuacion) p += PUNTOS.continuacion;
    return p;
  }

  function nivelDe(sc) {
    if (sc >= 80) return "alta confluencia estructural";
    if (sc >= 60) return "estructura confirmada";
    if (sc >= 40) return "estructura en desarrollo";
    return "estructura débil";
  }

  function evento(s, tipo, j, ctx, extra) {
    s.eventos.push(Object.assign({
      tipo, idx: j, time: ctx.T[j], setupId: s.id, dir: s.dir, score: score(s),
    }, extra || {}));
  }

  function cerrar(s, j, ctx, motivo, porTiempo) {
    s.cerrado = true;
    s.estadoAlCerrar = s.estado;
    s.estado = "INVALIDADO";
    s.motivo = motivo;
    s.porTiempo = !!porTiempo;
    s.cierreIdx = j; s.cierreTime = ctx.T[j];
    if (s.imbalance && s.imbalance.estado !== "MITIGADO") s.imbalance.estado = "INVALIDADO";
    evento(s, "invalidacion", j, ctx, { motivo });
  }

  // ─────────────────────────────────────────────────────────────
  // 2) FASE ESTRUCTURA: FRACTAL → BREAKOUT → DISPLACEMENT → IMBALANCE
  // ─────────────────────────────────────────────────────────────
  function pasoEstructura(s, j, ctx, o) {
    const { H, L, C, O, T, atr, cuerpoMed } = ctx;
    const dir = s.dir;
    const ext = dir > 0 ? H : L;
    const mejor = (a, b) => (dir > 0 ? Math.max(a, b) : Math.min(a, b));
    const supera = (a, b) => (dir > 0 ? a > b : a < b);

    if (s.estado === "FRACTAL") {
      if (j <= s.fractal.confirmIdx) return;          // aún no está confirmado
      const precio = o.rupturaPorCierre ? C[j] : ext[j];
      if (supera(precio, s.fractal.price)) {
        s.ruptura = { idx: j, time: T[j], nivel: s.fractal.price, cierre: C[j] };
        s.extremo = mejor(s.extremo, ext[j]);
        s.estado = "BREAKOUT";
        evento(s, "ruptura", j, ctx);
        return;
      }
      if (j - s.fractal.confirmIdx >= o.maxBarrasRuptura) {
        cerrar(s, j, ctx, `el fractal no se rompió en ${o.maxBarrasRuptura} velas`, true);
      }
      return;
    }

    if (s.estado === "BREAKOUT") {
      s.extremo = mejor(s.extremo, ext[j]);
      // El impulso se mide sobre el tramo ruptura→j: cuerpo neto a favor
      // contra el cuerpo medio reciente, y rango del tramo contra el ATR.
      const d0 = s.ruptura.idx;
      const med = cuerpoMed[d0] || 0, a = atr[d0] || 0;
      let cuerpo = 0, hi = -Infinity, lo = Infinity;
      for (let k = d0; k <= j; k++) {
        cuerpo += (C[k] - O[k]) * dir;
        hi = Math.max(hi, H[k]); lo = Math.min(lo, L[k]);
      }
      const cuerpoRel = med > 0 ? cuerpo / med : 0;
      const rangoAtr = a > 0 ? (hi - lo) / a : 0;
      if (cuerpo > 0 && cuerpoRel >= o.cuerpoMult && rangoAtr >= o.rangoAtrMin) {
        s.desplazamiento = {
          desde: d0, hasta: j, time: T[j], velas: j - d0 + 1,
          cuerpoRel, rangoAtr, hi, lo,
        };
        s.estado = "DISPLACEMENT";
        evento(s, "desplazamiento", j, ctx);
        return;
      }
      if (j > d0 && !supera(C[j], s.fractal.price)) {
        cerrar(s, j, ctx, "ruptura fallida: el precio cerró de vuelta del otro lado del fractal");
        return;
      }
      if (j - d0 >= o.maxBarrasDespl) {
        cerrar(s, j, ctx, "la ruptura no produjo un desplazamiento significativo", true);
      }
      return;
    }

    if (s.estado === "DISPLACEMENT") {
      s.extremo = mejor(s.extremo, ext[j]);
      const limite = s.desplazamiento.hasta + o.fvgVentana;
      if (j <= limite && j - 1 >= s.ruptura.idx - 1) {
        const z = buscarFVG(ctx, j, dir, o);
        if (z) {
          s.imbalance = {
            top: z.top, bottom: z.bottom, mid: z.mid, tamAtr: z.tamAtr, dir,
            idx: j, time: T[j], estado: "ACTIVO",
            recorridoMax: 0, visitas: 0, ultimaVisitaIdx: null,
          };
          s.estado = "IMBALANCE";
          evento(s, "imbalance", j, ctx);
          return;
        }
      }
      if (!supera(C[j], s.fractal.price)) {
        cerrar(s, j, ctx, "estructura perdida: cierre de vuelta del otro lado del fractal");
        return;
      }
      if (j >= limite) {
        cerrar(s, j, ctx, "el desplazamiento no dejó ningún imbalance (FVG)", true);
      }
      return;
    }
  }

  function faseEstructura(ctx, o, escala) {
    const setups = [];
    const porBarra = new Map();
    detectarFractales(ctx, o.fractalK).forEach(f => {
      if (!porBarra.has(f.confirmIdx)) porBarra.set(f.confirmIdx, []);
      porBarra.get(f.confirmIdx).push(f);
    });

    let vivos = [];
    for (let j = 0; j <= ctx.last; j++) {
      (porBarra.get(j) || []).forEach(f => {
        const s = nuevoSetup(f, escala);
        evento(s, "fractal", f.confirmIdx, ctx, { precio: f.price });
        setups.push(s); vivos.push(s);
      });
      // Varias transiciones pueden ocurrir en la MISMA vela (la vela que
      // rompe suele ser también la del impulso, y a veces cierra el FVG).
      for (const s of vivos) {
        let antes;
        do { antes = s.estado; pasoEstructura(s, j, ctx, o); }
        while (!s.cerrado && s.estado !== antes);
      }
      vivos = vivos.filter(s => !s.cerrado && s.estado !== "IMBALANCE");
    }
    return setups;
  }

  // ─────────────────────────────────────────────────────────────
  // 4+5) FASE SEGUIMIENTO: IMBALANCE → RETEST → CONFIRMATION → COMPLETED
  // ─────────────────────────────────────────────────────────────
  function clasificarRetest(pct, o) {
    if (pct >= 0.999) return "completo";
    if (pct >= o.pctProfundo) return "profundo";
    if (pct >= o.pctParcial) return "parcial";
    return "toque";
  }

  // Cuánto de la zona recorrió el precio en la vela j, de 0 a 1. Se mide
  // desde el borde por el que el precio vuelve (arriba en alcista).
  function penetracion(z, j, ctx) {
    const alto = z.top - z.bottom;
    if (alto <= 0) return 0;
    const p = z.dir > 0
      ? (z.top - Math.min(ctx.L[j], z.top)) / alto
      : (Math.max(ctx.H[j], z.bottom) - z.bottom) / alto;
    return Math.max(0, Math.min(1, p));
  }

  function pasoSeguimiento(s, j, ctx, o) {
    const { H, L, C, O, T, cuerpoMed } = ctx;
    const dir = s.dir, z = s.imbalance;
    const ext = dir > 0 ? H : L;
    const mejor = (a, b) => (dir > 0 ? Math.max(a, b) : Math.min(a, b));
    const supera = (a, b) => (dir > 0 ? a > b : a < b);
    const fueraDeZona = dir > 0 ? C[j] < z.bottom : C[j] > z.top;
    const tocaZona = L[j] <= z.top && H[j] >= z.bottom;

    if (s.estado === "IMBALANCE") {
      s.extremo = mejor(s.extremo, ext[j]);
      if (fueraDeZona) {
        z.estado = "INVALIDADO";
        cerrar(s, j, ctx, "imbalance invalidado: cierre completamente del otro lado de la zona");
        return;
      }
      if (tocaZona) {
        const pct = penetracion(z, j, ctx);
        z.recorridoMax = Math.max(z.recorridoMax, pct);
        z.visitas++; z.ultimaVisitaIdx = j;
        z.estado = z.recorridoMax >= 0.999 ? "MITIGADO" : "RETESTEADO";
        s.extremoRef = s.extremo;                    // referencia congelada
        s.retest = {
          idx: j, time: T[j], pct,
          tipo: clasificarRetest(pct, o),
          desdeImbalance: j - (s.baseImbalanceIdx != null ? s.baseImbalanceIdx : z.idx),
        };
        s.estado = "RETEST";
        evento(s, "retest", j, ctx, { pct, tipo: s.retest.tipo });
        return;
      }
      const inicio = s.baseImbalanceIdx != null ? s.baseImbalanceIdx : z.idx;
      if (j - inicio >= o.maxBarrasRetest) {
        cerrar(s, j, ctx, `el precio no volvió a la zona en ${o.maxBarrasRetest} velas`, true);
      }
      return;
    }

    if (s.estado === "RETEST" || s.estado === "CONFIRMATION") {
      if (tocaZona) {
        const pct = penetracion(z, j, ctx);
        if (pct > z.recorridoMax) { z.recorridoMax = pct; s.retest.pct = pct; s.retest.tipo = clasificarRetest(pct, o); }
        z.ultimaVisitaIdx = j;
        if (z.recorridoMax >= 0.999 && z.estado !== "INVALIDADO") z.estado = "MITIGADO";
      }
      if (fueraDeZona) {
        z.estado = "INVALIDADO";
        cerrar(s, j, ctx, "zona perdida: cierre completamente del otro lado del imbalance");
        return;
      }
      // Continuación confirmada: nuevo extremo más allá del alcanzado por
      // el desplazamiento — el precio rechazó la zona y siguió su camino.
      if (supera(ext[j], s.extremoRef)) {
        if (!s.continuacion) {
          s.continuacion = {
            idx: j, time: T[j],
            tipo: dir > 0 ? "nuevo máximo tras el imbalance" : "nuevo mínimo tras el imbalance",
          };
        }
        s.completadoIdx = j;
        s.estado = "COMPLETED";
        s.cerrado = true;
        s.cierreIdx = j; s.cierreTime = T[j];
        evento(s, "completado", j, ctx);
        return;
      }
      if (s.estado === "RETEST") {
        // Rechazo: vela a favor que cierra de nuevo fuera de la zona.
        const cuerpo = (C[j] - O[j]) * dir;
        const cerroFuera = dir > 0 ? C[j] > z.top : C[j] < z.bottom;
        if (cerroFuera && cuerpo >= o.cuerpoRechazoMult * (cuerpoMed[j] || 0) && cuerpo > 0) {
          s.continuacion = { idx: j, time: T[j], tipo: "rechazo de la zona con vela a favor" };
          s.estado = "CONFIRMATION";
          evento(s, "confirmacion", j, ctx);
          return;
        }
      }
      if (j - s.retest.idx >= o.maxBarrasConfirm) {
        cerrar(s, j, ctx, `no hubo continuación en ${o.maxBarrasConfirm} velas tras el retesteo`, true);
      }
    }
  }

  function faseSeguimiento(s, ctx, o, desde) {
    if (!s.imbalance || s.cerrado) return s;
    s.baseImbalanceIdx = Math.max(0, desde - 1);
    for (let j = Math.max(0, desde); j <= ctx.last; j++) {
      let antes;
      do { antes = s.estado; pasoSeguimiento(s, j, ctx, o); }
      while (!s.cerrado && s.estado !== antes);
      if (s.cerrado) break;
    }
    return s;
  }

  // ─────────────────────────────────────────────────────────────
  // 14) MEDICIÓN PARA BACKTEST — separada del estado y del score.
  // Mira velas posteriores a propósito; por eso nunca toca la secuencia.
  // ─────────────────────────────────────────────────────────────
  function medir(s, ctx, o) {
    if (!s.continuacion || !s.imbalance) return;
    const dir = s.dir, z = s.imbalance, c0 = s.continuacion.idx;
    const a = ctx.atr[c0] || 0;
    const fin = Math.min(ctx.last, c0 + o.barrasMedicion);
    let mfe = 0, mae = 0;
    for (let j = c0; j <= fin; j++) {
      mfe = Math.max(mfe, dir > 0 ? ctx.H[j] - z.mid : z.mid - ctx.L[j]);
      mae = Math.max(mae, dir > 0 ? z.mid - ctx.L[j] : ctx.H[j] - z.mid);
    }
    s.medicion = {
      completa: c0 + o.barrasMedicion <= ctx.last,
      barras: fin - c0,
      recorridoAtr: a > 0 ? mfe / a : 0,
      retrocesoAtr: a > 0 ? mae / a : 0,
    };
    if (s.retest) {
      let contra = 0;
      for (let j = s.retest.idx; j <= c0; j++) {
        contra = Math.max(contra, dir > 0 ? z.mid - ctx.L[j] : ctx.H[j] - z.mid);
      }
      s.retrocesoAtr = a > 0 ? contra / a : 0;
      s.barrasHastaContinuacion = c0 - s.retest.idx;
    }
  }

  function sesionDe(time) {
    const h = Math.floor((time % 86400) / 3600);   // hora UTC
    if (h >= 0 && h < 7) return "asia";
    if (h < 12) return "londres";
    if (h < 21) return "ny";
    return "otra";
  }

  function mediana(v) {
    if (!v.length) return null;
    const a = v.slice().sort((x, y) => x - y);
    const m = Math.floor(a.length / 2);
    return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }

  function estadisticas(setups, meta) {
    const st = {
      fractales: setups.length, rupturas: 0, desplazamientos: 0, imbalances: 0,
      retesteos: 0, continuaciones: 0, completados: 0, invalidados: 0,
      pctRuptura: 0, pctDesplazamiento: 0, pctImbalance: 0, pctRetest: 0, pctContinuacion: 0,
      barrasHastaContinuacion: null, recorridoAtr: null, retrocesoAtr: null,
      porSesion: {}, porMotivo: {},
      simbolo: (meta && meta.symbol) || null,
      timeframe: (meta && meta.timeframe) || null,
      muestraCompleta: 0,
    };
    const barras = [], recorridos = [], retrocesos = [];
    setups.forEach(s => {
      if (s.ruptura) st.rupturas++;
      if (s.desplazamiento) st.desplazamientos++;
      if (s.imbalance) st.imbalances++;
      if (s.retest) st.retesteos++;
      if (s.continuacion) st.continuaciones++;
      if (s.estado === "COMPLETED") st.completados++;
      if (s.estado === "INVALIDADO") {
        st.invalidados++;
        st.porMotivo[s.motivo] = (st.porMotivo[s.motivo] || 0) + 1;
      }
      if (s.imbalance) {
        const ses = sesionDe(s.imbalance.time);
        const b = st.porSesion[ses] || (st.porSesion[ses] = { imbalances: 0, retesteos: 0, continuaciones: 0 });
        b.imbalances++;
        if (s.retest) b.retesteos++;
        if (s.continuacion) b.continuaciones++;
      }
      if (s.barrasHastaContinuacion != null) barras.push(s.barrasHastaContinuacion);
      if (s.retrocesoAtr != null) retrocesos.push(s.retrocesoAtr);
      if (s.medicion && s.medicion.completa) { recorridos.push(s.medicion.recorridoAtr); st.muestraCompleta++; }
    });
    const pct = (a, b) => (b > 0 ? (100 * a) / b : 0);
    st.pctRuptura = pct(st.rupturas, st.fractales);
    st.pctDesplazamiento = pct(st.desplazamientos, st.rupturas);
    st.pctImbalance = pct(st.imbalances, st.desplazamientos);
    st.pctRetest = pct(st.retesteos, st.imbalances);
    st.pctContinuacion = pct(st.continuaciones, st.retesteos);
    st.barrasHastaContinuacion = mediana(barras);
    st.recorridoAtr = mediana(recorridos);
    st.retrocesoAtr = mediana(retrocesos);
    Object.keys(st.porSesion).forEach(k => {
      const b = st.porSesion[k];
      b.pctContinuacion = pct(b.continuaciones, b.retesteos);
    });
    return st;
  }

  // ─────────────────────────────────────────────────────────────
  // Orquestador
  // ─────────────────────────────────────────────────────────────
  function calcular(candles, userOpts) {
    const o = Object.assign({}, DEFAULTS, userOpts || {});
    const n = candles ? candles.length : 0;
    if (n < o.atrLen + o.fractalK * 4 + 10) return null;

    const ctx = contexto(candles, o);
    if (ctx.last < o.fractalK * 2 + 2) return null;

    let setups;
    const htf = o.htfMult > 1 ? agregarHTF(candles, o.htfMult) : null;
    const usaHTF = !!(htf && htf.candles.length >= o.atrLen + o.fractalK * 4 + 10);

    if (usaHTF) {
      // Estructura en la temporalidad superior…
      const ctxH = contexto(htf.candles, o);
      setups = faseEstructura(ctxH, o, `HTF x${o.htfMult}`);
      // …seguimiento en la base, recién cuando la vela superior cerró.
      setups.forEach(s => {
        if (!s.imbalance || s.cerrado) return;
        const baseIdx = htf.baseFin[s.imbalance.idx];
        if (baseIdx == null) return;
        s.imbalance.baseIdx = baseIdx;
        faseSeguimiento(s, ctx, o, baseIdx + 1);
      });
    } else {
      setups = faseEstructura(ctx, o, "TF");
      setups.forEach(s => {
        if (s.imbalance && !s.cerrado) faseSeguimiento(s, ctx, o, s.imbalance.idx + 1);
      });
    }

    // Estructuras gemelas: dos fractales distintos que terminan en el MISMO
    // imbalance describen la misma cosa. Se queda la del fractal más viejo
    // (la barrera de más rango). Es causal: ambas ya existían en ese momento.
    const porZona = new Map();
    setups.forEach(s => {
      if (!s.imbalance) return;
      const clave = `${s.dir}|${s.imbalance.idx}`;
      const prev = porZona.get(clave);
      if (!prev || s.fractal.idx < prev.fractal.idx) porZona.set(clave, s);
    });
    const duplicadas = new Set();
    setups.forEach(s => {
      if (!s.imbalance) return;
      const clave = `${s.dir}|${s.imbalance.idx}`;
      if (porZona.get(clave) !== s) duplicadas.add(s);
    });
    const limpias = setups.filter(s => !duplicadas.has(s));

    limpias.forEach(s => {
      medir(s, ctx, o);
      s.score = score(s);
      s.nivel = nivelDe(s.score);
      s.etapaMax = s.estado === "INVALIDADO" ? s.estadoAlCerrar : s.estado;
    });

    // La estructura "actual" del panel: la viva más avanzada; si no hay
    // ninguna viva, la última que se cerró (para poder explicar por qué).
    const vivas = limpias.filter(s => !s.cerrado);
    const rank = s => ORDEN_ESTADOS.indexOf(s.estado) * 1e9 + (s.imbalance ? s.imbalance.time : s.fractal.time);
    vivas.sort((a, b) => rank(b) - rank(a));
    const cerradas = limpias.filter(s => s.cerrado)
      .sort((a, b) => (b.cierreTime || 0) - (a.cierreTime || 0));
    const actual = vivas[0] || cerradas[0] || null;

    // Dibujables: las que llegaron al imbalance, más recientes primero.
    const visibles = limpias
      .filter(s => s.imbalance && s.score >= o.scoreVisible)
      .sort((a, b) => b.imbalance.time - a.imbalance.time)
      .slice(0, o.maxVisibles);

    const eventos = [];
    limpias.forEach(s => s.eventos.forEach(e => eventos.push(e)));
    eventos.sort((a, b) => a.time - b.time || a.setupId - b.setupId);

    return {
      setups: limpias, vivas, visibles, actual, eventos,
      stats: estadisticas(limpias, userOpts),
      escala: usaHTF ? `estructura en x${o.htfMult}, seguimiento en la base` : "una sola temporalidad",
      atr: ctx.atr[ctx.last] || 0,
      close: ctx.C[ctx.last],
      lastIdx: ctx.last,
      lastTime: ctx.T[ctx.last],
      opciones: o,
    };
  }

  const api = {
    calcular, DEFAULTS, PUNTOS, ORDEN_ESTADOS, nivelDe,
    _internals: {
      atrWilder, mediaCuerpo, esPivote, medianStep, contexto, agregarHTF,
      detectarFractales, buscarFVG, faseEstructura, faseSeguimiento,
      penetracion, clasificarRetest, medir, estadisticas, score, sesionDe,
    },
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SecuenciaFractal = api;
})(typeof window !== "undefined" ? window : this);
