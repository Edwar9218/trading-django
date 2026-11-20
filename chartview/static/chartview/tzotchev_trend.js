/* ══════════════════════════════════════════════════════════════════
 * TZOTCHEV TREND (TZT) — fuerza de la tendencia, de -1 a +1
 *
 * Port a JavaScript del indicador de TradingView "Tzotchev Trend
 * [MiesOnCharts]" (© MiesOnCharts, Mozilla Public License 2.0,
 * https://mozilla.org/MPL/2.0/), que a su vez implementa la medida de
 * fuerza de tendencia del paper de Tzotchev "Designing Robust
 * Trend-Following System". Por ser una obra derivada, este archivo
 * conserva esa atribución y esa licencia.
 *
 * Para cada ventana de T velas:
 *
 *     t  = sqrt(T) * retorno medio / desvío de los retornos
 *     ST = 2 * N(t) - 1
 *
 * El retorno medio y el desvío salen de los retornos logarítmicos DENTRO de
 * la ventana, y N es la probabilidad acumulada de la normal. O sea: t es el
 * estadístico t del retorno promedio (cuántos errores estándar está la
 * deriva respecto de cero), N lo convierte en "probabilidad de que la deriva
 * sea positiva" y el 2·N-1 lo lleva a la escala -1..+1.
 *
 * La línea final es el PROMEDIO de tres ventanas (rápida 20, principal 30,
 * lenta 60). Como cada una ya viene acotada por la curva normal, una sola
 * ventana estirada no puede pesar más que las otras dos.
 *
 * Estado (LONG / SHORT / NEUTRAL): la línea tiene que CERRAR por encima de
 * +0.2 para pasar a LONG, o por debajo de -0.2 para pasar a SHORT; entre los
 * dos niveles se mantiene el último estado, así una lectura que ronda el
 * cero no cambia de estado en cada vela.
 *
 * Sin repintado (regla 4 del proyecto): el estado solo se mueve con una vela
 * CERRADA. La línea de la vela en formación sí se mueve con el precio (es una
 * medición), pero esa vela hereda el estado de la última cerrada. Cada valor
 * usa solo velas hasta la suya: recortar la historia no cambia nada anterior.
 *
 * Módulo puro, sin DOM: se prueba con Node (tests_js/tzotchev_trend.test.js).
 * ══════════════════════════════════════════════════════════════════ */
(function (root) {
  "use strict";

  const DEFAULTS = {
    fuente: "ohlc4",       // precio sobre el que se miden los retornos
    lenM: 30,              // ventana principal (siempre activa)
    usarRapida: true,  lenF: 20,
    usarLenta: true,   lenS: 60,
    umbralLong: 0.2,       // la línea tiene que cerrar por encima para pasar a LONG
    umbralShort: -0.2,     // ...y por debajo de esto para pasar a SHORT
    // false = la ÚLTIMA vela todavía está en formación (Modo Online): su
    // estado no se actualiza, hereda el de la anterior. En Backtesting y con
    // el historial todo cerrado se deja en true.
    ultimaCerrada: true,
  };

  // Probabilidad acumulada de la normal. No hay una función nativa, así que
  // se usa la aproximación racional de Abramowitz y Stegun (26.2.17), con
  // error absoluto menor a ~1e-7 (igual que el indicador original).
  function phi(z) {
    const a = Math.abs(z);
    const k = 1 / (1 + 0.2316419 * a);
    const pdf = Math.exp(-0.5 * a * a) / Math.sqrt(2 * Math.PI);
    const cola = pdf * k * (0.319381530 + k * (-0.356563782 + k * (1.781477937 +
                 k * (-1.821255978 + k * 1.330274429))));
    return z >= 0 ? 1 - cola : cola;
  }

  function precioDe(c, fuente) {
    switch (fuente) {
      case "close": return c.close;
      case "hl2":   return (c.high + c.low) / 2;
      case "hlc3":  return (c.high + c.low + c.close) / 3;
      default:      return (c.open + c.high + c.low + c.close) / 4;   // ohlc4
    }
  }

  // Lectura ST de UNA ventana de t velas, para todas las velas. Devuelve null
  // donde todavía no hay historia suficiente (hacen falta t retornos, o sea
  // t+1 velas) o donde el desvío es cero (precio plano).
  //
  // La media y el desvío cubren exactamente las mismas velas: los t retornos
  // que terminan en la vela i son los que suman el cambio logarítmico entre
  // i-t e i, por eso la media se toma como (lnP[i] - lnP[i-t]) / t, igual que
  // el original. El desvío es el MUESTRAL (divide por t-1), como
  // ta.stdev(..., biased=false) de Pine.
  function fortaleza(lnP, t) {
    const n = lnP.length;
    const out = new Array(n).fill(null);
    const r = new Array(n).fill(NaN);
    for (let i = 1; i < n; i++) r[i] = lnP[i] - lnP[i - 1];

    for (let i = t; i < n; i++) {
      let suma = 0;
      for (let j = i - t + 1; j <= i; j++) suma += r[j];
      const media = suma / t;
      let q = 0;
      for (let j = i - t + 1; j <= i; j++) { const d = r[j] - media; q += d * d; }
      const sd = Math.sqrt(q / (t - 1));
      const rBar = (lnP[i] - lnP[i - t]) / t;
      // `!(sd > 0)` también atrapa NaN (precio no positivo → log inválido).
      if (!(sd > 0) || !Number.isFinite(rBar)) continue;
      out[i] = 2 * phi(Math.sqrt(t) * rBar / sd) - 1;
    }
    return out;
  }

  function validarLargo(nombre, v) {
    if (!Number.isInteger(v) || v < 2) throw new Error(`TZT: ${nombre} debe ser un entero >= 2`);
  }

  // candles: [{time, open, high, low, close}, ...] ordenadas de más vieja a
  // más nueva. Devuelve arrays alineados 1 a 1 con las velas (null = sin dato).
  function calcular(candles, opciones) {
    const o = Object.assign({}, DEFAULTS, opciones || {});
    validarLargo("lenM", o.lenM);
    if (o.usarRapida) validarLargo("lenF", o.lenF);
    if (o.usarLenta) validarLargo("lenS", o.lenS);

    const n = candles ? candles.length : 0;
    const nulos = () => new Array(n).fill(null);
    const lnP = new Array(n);
    for (let i = 0; i < n; i++) lnP[i] = Math.log(precioDe(candles[i], o.fuente));

    const stM = fortaleza(lnP, o.lenM);
    const stF = o.usarRapida ? fortaleza(lnP, o.lenF) : nulos();
    const stS = o.usarLenta ? fortaleza(lnP, o.lenS) : nulos();

    // Si una ventana ACTIVA todavía no tiene dato, la línea queda en blanco:
    // así siempre significa la misma mezcla de horizontes.
    const nOn = 1 + (o.usarRapida ? 1 : 0) + (o.usarLenta ? 1 : 0);
    const score = nulos();
    for (let i = 0; i < n; i++) {
      if (stM[i] === null) continue;
      if (o.usarRapida && stF[i] === null) continue;
      if (o.usarLenta && stS[i] === null) continue;
      score[i] = (stM[i] + (o.usarRapida ? stF[i] : 0) + (o.usarLenta ? stS[i] : 0)) / nOn;
    }

    // Estado: solo se mueve con una vela cerrada y, entre los dos niveles,
    // se mantiene. `state` arranca en 0 (nunca cruzó un nivel).
    const state = new Array(n).fill(0);
    const flipUp = new Array(n).fill(false);
    const flipDn = new Array(n).fill(false);
    let actual = 0;
    for (let i = 0; i < n; i++) {
      const cerrada = i < n - 1 || o.ultimaCerrada;
      if (cerrada && score[i] !== null) {
        if (score[i] > o.umbralLong) actual = 1;
        else if (score[i] < o.umbralShort) actual = -1;
      }
      state[i] = actual;
      if (i > 0) {
        flipUp[i] = actual === 1 && state[i - 1] !== 1;
        flipDn[i] = actual === -1 && state[i - 1] !== -1;
      }
    }

    const u = n - 1;
    return {
      n, opciones: o,
      stM, stF, stS, score, state, flipUp, flipDn,
      ultimo: n ? { idx: u, score: score[u], stM: stM[u], stF: stF[u], stS: stS[u], state: state[u] } : null,
    };
  }

  const api = { calcular, DEFAULTS, _internals: { phi, fortaleza, precioDe } };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.TzotchevTrend = api;
})(typeof window !== "undefined" ? window : this);
