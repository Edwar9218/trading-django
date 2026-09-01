"""
Auto S/R Channels + Kalman Flow — Python port
===============================================
Port conceptual de DOS indicadores Pine Script v6 combinados en un solo script:

  1. "Auto S/R Channels [WillyAlgoTrader]" -> canales de soporte/resistencia
     por pivotes + containment ratio (igual que antes).
  2. "Kalman Flow | Lyro RS" -> filtro de Kalman de 2 estados (nivel + velocidad)
     que genera una línea base adaptativa, bandas de ruido y señales de
     flip alcista/bajista. Se usa aquí como FILTRO DE CONFIRMACIÓN de
     tendencia sobre las rupturas de canal: una ruptura de canal solo se
     considera "confirmada" si el Kalman está de acuerdo con la dirección.

Lógica central (canales), sin cambios:
  1. Detección de pivotes (swing highs/lows) con ventana simétrica.
  2. Búsqueda de combinaciones de pares de pivotes como línea base candidata.
  3. Cálculo de la paralela (resistencia/soporte opuesto) usando el pivote
     opuesto con mayor desplazamiento perpendicular.
  4. Scoring por "containment ratio": % de velas contenidas dentro del canal
     (con tolerancia en ATR), sobre una ventana de hasta N barras.
  5. Selección del mejor candidato por dirección (ascendente / descendente).
  6. Detección de breakouts (cierre confirmado fuera del canal) y reacciones
     (mecha toca el límite pero el cierre se mantiene dentro).

Lógica nueva (Kalman Flow):
  - Filtro de Kalman de velocidad constante (2 estados: nivel y velocidad).
  - Bandas adaptativas = nivel Kalman ± (tracking error medio * multiplicador).
  - Flip de tendencia requiere DOS cosas a la vez, igual que en Pine:
      a) cierre más allá de la banda contraria
      b) velocidad Kalman de acuerdo con la nueva dirección
  - Esto agrega histéresis: no cualquier cruce de banda flipea la tendencia.

Uso:
    python auto_channels.py                  -> corre con datos sintéticos de demo
    python auto_channels.py mis_datos.csv     -> corre con un CSV propio

Formato de CSV esperado (encabezados, insensible a mayúsculas):
    Date,Open,High,Low,Close
(o el export estándar de MT4: Date,Time,Open,High,Low,Close,Volume)
"""

import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # backend no interactivo: el script solo hace savefig() (nunca plt.show()),
                        # así que Agg evita overhead de backends con GUI y es más consistente/rápido
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.collections import LineCollection, PolyCollection
from dataclasses import dataclass, replace
from typing import Optional

try:
    import smart_money_flow as smf
except ImportError:
    smf = None


# ══════════════════════════════════════════════════════════
# 1. CARGA DE DATOS
# ══════════════════════════════════════════════════════════

def load_csv(path: str) -> pd.DataFrame:
    """Carga un CSV de velas OHLC (acepta formato MT4 con columna Time separada).

    Si el CSV trae columna 'Volume' (como el que genera mt5_export_h4.py, con
    el tick_volume de tu bróker), se conserva como columna 'volume' — la
    necesita el indicador Smart Money Flow. Si no viene, queda en NaN.
    """
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]

    if "date" in df.columns and "time" in df.columns:
        df["datetime"] = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str),
                                         errors="coerce")
    elif "date" in df.columns:
        df["datetime"] = pd.to_datetime(df["date"], errors="coerce")
    else:
        df["datetime"] = pd.RangeIndex(len(df))

    df = df.rename(columns={"open": "open", "high": "high", "low": "low", "close": "close"})
    cols = ["datetime", "open", "high", "low", "close"]
    if "volume" in df.columns:
        cols.append("volume")
    else:
        df["volume"] = np.nan
        cols.append("volume")
    df = df[cols].dropna(subset=["datetime", "open", "high", "low", "close"]).reset_index(drop=True)
    return df


def make_demo_data(n=600, seed=7) -> pd.DataFrame:
    """Genera un random walk con drift para poder probar el script sin CSV propio."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(loc=0.0, scale=1.0, size=n)
    drift = np.sin(np.linspace(0, 3.5, n)) * 1.5  # ciclos para simular tendencias/canales
    close = 100 + np.cumsum(steps * 0.6 + drift * 0.05)
    high = close + rng.uniform(0.1, 0.6, n)
    low = close - rng.uniform(0.1, 0.6, n)
    open_ = close + rng.normal(0, 0.2, n)
    dates = pd.date_range("2025-01-01", periods=n, freq="4h")
    # Volumen sintético (tick volume simulado): más "actividad" cuando la
    # vela es más grande, para que el demo del Smart Money Flow tenga algo
    # coherente que mostrar sin necesidad de un CSV real.
    rng_len = rng.random(n)
    volume = (np.abs(close - open_) * 800 + rng_len * 300 + 200).round()
    return pd.DataFrame({"datetime": dates, "open": open_, "high": high, "low": low, "close": close,
                          "volume": volume})


# ══════════════════════════════════════════════════════════
# 2. INDICADORES BASE: ATR Y PIVOTES
# ══════════════════════════════════════════════════════════

def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(length, min_periods=1).mean()


def find_pivots(series: pd.Series, length: int, kind: str) -> pd.Series:
    """
    Replica ta.pivothigh()/ta.pivotlow(): un punto en i es pivote si es el
    máximo (o mínimo) estricto dentro de la ventana [i-length, i+length].
    Devuelve una Serie con NaN salvo en las barras pivote.

    Vectorizado con sliding_window_view (antes era un for-loop en Python):
    mismo resultado exacto (incluida la regla de desempate de "único máximo/
    mínimo en la ventana"), pero mucho más rápido en historiales largos.
    """
    n = len(series)
    result = pd.Series(np.nan, index=series.index)
    if n - 2 * length <= 0:
        return result

    values = series.to_numpy()
    w = 2 * length + 1
    windows = np.lib.stride_tricks.sliding_window_view(values, w)  # (n-2*length, w)
    centers = values[length:n - length]

    extreme = windows.max(axis=1) if kind == "high" else windows.min(axis=1)
    is_extreme = centers == extreme
    count_eq = (windows == centers[:, None]).sum(axis=1)  # cuántas veces aparece el valor "centro" en su ventana
    is_unique = count_eq == 1

    result.iloc[length:n - length] = np.where(is_extreme & is_unique, centers, np.nan)
    return result


# ══════════════════════════════════════════════════════════
# 3. BÚSQUEDA DE CANALES (containment-ratio scoring)
# ══════════════════════════════════════════════════════════

@dataclass
class Channel:
    x1: int
    y1: float
    x2: int
    y2: float
    offset: float        # desplazamiento de la línea base a la paralela
    quality: float
    direction: str        # "up" o "down"

    def base_at(self, x: int) -> float:
        if self.x2 == self.x1:
            return self.y1
        slope = (self.y2 - self.y1) / (self.x2 - self.x1)
        return self.y1 + slope * (x - self.x1)


def find_channel(low_arr: np.ndarray, high_arr: np.ndarray, base_idx: list, base_val: list,
                  opp_idx: list, opp_val: list, direction: str,
                  min_bars: int, max_bars: int, quality_th: float,
                  atr_arr: np.ndarray, last_bar: int,
                  recent_n: int = 8, lookback_pairs: int = 5,
                  check_len: int = 300, tol_pct: float = 0.05) -> Optional[Channel]:
    """
    direction: "up"  -> pares de mínimos con pendiente ascendente (base = soporte)
               "down" -> pares de máximos con pendiente descendente (base = resistencia)

    low_arr / high_arr / atr_arr: arrays de NumPy (NO Series/DataFrame de pandas).
    Se reciben ya extraídos porque esta función se llama muchísimas veces (una o
    dos por cada evento de pivote en simulate_incremental) y volver a hacer
    df["low"].to_numpy() en cada llamada tiene overhead de pandas que se nota
    en historiales largos — mejor extraerlo una sola vez afuera y reusarlo.
    """
    best: Optional[Channel] = None
    sz = len(base_idx)
    if sz < 2:
        return None

    opp_idx_arr = np.asarray(opp_idx, dtype=np.float64)
    opp_val_arr = np.asarray(opp_val, dtype=np.float64)

    i_range = range(sz - 1, max(-1, sz - 1 - recent_n), -1)
    for i in i_range:
        j_range = range(i - 1, max(-1, i - 1 - lookback_pairs), -1)
        for j in j_range:
            ix1, iy1 = base_idx[j], base_val[j]
            ix2, iy2 = base_idx[i], base_val[i]
            span = ix2 - ix1
            if span < min_bars or span > max_bars:
                continue
            if direction == "up" and iy2 <= iy1:
                continue
            if direction == "down" and iy2 >= iy1:
                continue

            slope = (iy2 - iy1) / (ix2 - ix1) if ix2 != ix1 else 0.0

            best_off = 0.0
            if opp_idx_arr.size:
                mask = (opp_idx_arr >= ix1) & (opp_idx_arr <= last_bar)
                if mask.any():
                    ob = opp_idx_arr[mask]
                    ov = opp_val_arr[mask]
                    diff = ov - (iy1 + slope * (ob - ix1))
                    if direction == "up":
                        best_off = max(best_off, diff.max())
                    else:
                        best_off = min(best_off, diff.min())

            if direction == "up" and best_off <= 0:
                continue
            if direction == "down" and best_off >= 0:
                continue

            total_bars = last_bar - ix1
            if total_bars <= 0:
                continue
            n_check = min(total_bars, check_len)

            # bx siempre cae en [ix1+1, last_bar] por cómo se calculó n_check arriba,
            # y last_bar < n_atr siempre (mismo largo que el df) -> no hace falta
            # clip/where defensivo, eso solo agregaba overhead sin cambiar nunca nada.
            bx = np.arange(last_bar - n_check + 1, last_bar + 1)
            base_line = iy1 + slope * (bx - ix1)
            par_line = base_line + best_off
            lo_b = np.minimum(base_line, par_line)
            hi_b = np.maximum(base_line, par_line)
            tol = atr_arr[bx] * tol_pct

            contained = int(np.count_nonzero(
                (low_arr[bx] >= lo_b - tol) & (high_arr[bx] <= hi_b + tol)
            ))

            quality = contained / n_check if n_check else 0.0
            if quality >= quality_th and (best is None or quality > best.quality):
                best = Channel(ix1, iy1, ix2, iy2, best_off, quality, direction)
    return best


# ══════════════════════════════════════════════════════════
# 4. KALMAN FLOW (port de "Kalman Flow | Lyro RS")
# ══════════════════════════════════════════════════════════

@dataclass
class KalmanResult:
    level: pd.Series      # línea base Kalman
    veloc: pd.Series      # velocidad estimada (drift por barra)
    upper: pd.Series      # banda superior adaptativa
    lower: pd.Series      # banda inferior adaptativa
    trend: pd.Series      # 1 = alcista, -1 = bajista, 0 = sin definir (solo warmup)
    flip_up: pd.Series    # True en la barra donde la tendencia pasa a alcista
    flip_down: pd.Series  # True en la barra donde la tendencia pasa a bajista


def kalman_flow(df: pd.DataFrame, src_col: str = "close",
                 sensitivity: float = 4.0, mad_multp: float = 1.65,
                 mad_multn: float = 1.0, vol_len: int = 50) -> KalmanResult:
    """
    Filtro de Kalman de 2 estados (nivel + velocidad constante), igual que el
    Pine original. Solo importa la razón ruido-de-proceso / ruido-de-medición
    para la ganancia del filtro, así que el comportamiento es independiente
    de la escala del precio (funciona igual en EURUSD que en BTC, por ej.).

    Reglas de flip (idénticas a Pine):
      - Sube a "alcista" (trend=1) si close > upperBand Y la velocidad > 0.
      - Baja a "bajista" (trend=-1) si close < lowerBand Y la velocidad < 0.
      - Si no se cumple ninguna, se mantiene el estado anterior (histéresis).
    """
    src = df[src_col].to_numpy(dtype=np.float64)
    n = len(src)

    kf_q = 10.0 ** (sensitivity / 2.0 - 6.0)   # ruido de proceso
    kf_r = 1.0                                  # ruido de medición

    kf_level = np.empty(n, dtype=np.float64)
    kf_veloc = np.zeros(n, dtype=np.float64)

    kf_level[0] = src[0]
    p11, p12, p21, p22 = 1.0, 0.0, 0.0, 1.0

    for i in range(1, n):
        # ── Predict ──
        pred_level = kf_level[i - 1] + kf_veloc[i - 1]
        pred_veloc = kf_veloc[i - 1]
        p11p = p11 + p12 + p21 + p22 + kf_q
        p12p = p12 + p22
        p21p = p21 + p22
        p22p = p22 + kf_q

        # ── Update ──
        s = p11p + kf_r
        k1 = p11p / s
        k2 = p21p / s
        innov = src[i] - pred_level

        kf_level[i] = pred_level + k1 * innov
        kf_veloc[i] = pred_veloc + k2 * innov
        p11 = (1 - k1) * p11p
        p12 = (1 - k1) * p12p
        p21 = p21p - k2 * p11p
        p22 = p22p - k2 * p12p

    level_s = pd.Series(kf_level, index=df.index)
    veloc_s = pd.Series(kf_veloc, index=df.index)

    track_err = (df[src_col] - level_s).abs().rolling(vol_len, min_periods=1).mean()
    upper = level_s + mad_multp * track_err
    lower = level_s - mad_multn * track_err

    close_arr = df["close"].to_numpy()
    upper_arr = upper.to_numpy()
    lower_arr = lower.to_numpy()
    veloc_arr = veloc_s.to_numpy()

    trend = np.zeros(n, dtype=np.int8)
    for i in range(1, n):
        t = trend[i - 1]
        if close_arr[i] > upper_arr[i] and veloc_arr[i] > 0:
            t = 1
        elif close_arr[i] < lower_arr[i] and veloc_arr[i] < 0:
            t = -1
        trend[i] = t

    trend_s = pd.Series(trend, index=df.index)
    flip_up = (trend_s == 1) & (trend_s.shift(1) != 1) & (trend_s.shift(1).notna())
    flip_down = (trend_s == -1) & (trend_s.shift(1) != -1) & (trend_s.shift(1).notna())
    # la primera barra nunca es un flip real (no hay estado previo)
    flip_up.iloc[0] = False
    flip_down.iloc[0] = False

    return KalmanResult(level_s, veloc_s, upper, lower, trend_s, flip_up, flip_down)


def compute_kalman_windowed(df: pd.DataFrame, lookback: Optional[int] = None,
                             **kalman_kwargs) -> KalmanResult:
    """
    Igual que kalman_flow(), pero si se indica `lookback`, el filtro se
    calcula usando SOLO las últimas `lookback` velas (en vez de las 80.000+
    del historial completo). Así la etiqueta "Kalman: Alcista/Bajista" que
    se imprime en consola queda calculada sobre el mismo tramo de velas que
    se ve en el gráfico (PLOT_LAST), en vez de "arrastrar" calentamiento de
    años de historial que ni siquiera se dibuja.

    El resultado se devuelve con la MISMA longitud e índice que `df`
    (las barras anteriores a la ventana quedan como NaN/0/False, que nunca
    se grafican porque quedan fuera de plot_last de todas formas).
    """
    n = len(df)
    if lookback is None or lookback >= n:
        return kalman_flow(df, **kalman_kwargs)

    lookback = max(lookback, 2)  # el filtro necesita al menos 2 barras
    sub = df.iloc[n - lookback:].reset_index(drop=True)
    kres_sub = kalman_flow(sub, **kalman_kwargs)

    pad = n - lookback

    def _pad(s: pd.Series, fill) -> pd.Series:
        head = pd.Series([fill] * pad)
        return pd.concat([head, s.reset_index(drop=True)], ignore_index=True).set_axis(df.index)

    return KalmanResult(
        level=_pad(kres_sub.level, np.nan),
        veloc=_pad(kres_sub.veloc, np.nan),
        upper=_pad(kres_sub.upper, np.nan),
        lower=_pad(kres_sub.lower, np.nan),
        trend=_pad(kres_sub.trend, 0).astype(np.int8),
        flip_up=_pad(kres_sub.flip_up, False).astype(bool),
        flip_down=_pad(kres_sub.flip_down, False).astype(bool),
    )


# ══════════════════════════════════════════════════════════
# 5. PIPELINE PRINCIPAL (canales)
# ══════════════════════════════════════════════════════════

def _is_inside(price: float, base: float, off: float) -> bool:
    """Igual que isInside() en Pine: ¿el precio está dentro del canal (entre base y base+off)?"""
    upper = max(base, base + off)
    lower = min(base, base + off)
    return lower <= price <= upper


def simulate_incremental(df: pd.DataFrame, pivot_len=21, atr_len=14,
                          min_channel_bars=10, max_channel_bars=400,
                          quality_th=0.55, recent_n=8, lookback_pairs=5,
                          replace_ratio=0.7, progress=True, lookback: Optional[int] = None):
    """
    Replica el comportamiento EN VIVO del Pine original, bar por bar:
      - Un canal solo se busca cuando se CONFIRMA un pivote nuevo (no en cada vela).
      - Una vez encontrado un canal, se queda "fijo" y extendido hacia la derecha.
      - Solo se reemplaza por uno nuevo si su calidad es > replace_ratio (0.7 = 70%)
        de la calidad del canal actual — igual que 'bq > chQual * 0.7' en Pine.
      - Si nunca aparece nada mejor, el canal viejo se queda pegado indefinidamente,
        aunque su calidad ya no refleje bien el precio reciente (esto es justo lo
        que se ve en TradingView con canales "viejos" que nunca se actualizaron).

    Además, TODA vela (no solo en eventos de pivote) se evalúa la lógica de
    ruptura/reacción de Pine, con las mismas 3 piezas que tenía el indicador
    original y que la primera versión de este port no replicaba:
      - GUARD 1 (expiración): un canal deja de generar señales si
        `bar_index - x2 > max_channel_bars` (quedó demasiado "extrapolado").
      - Flags de histéresis (fired): una ruptura solo dispara una vez; no
        se repite en cada vela mientras el precio se queda afuera del canal.
        Los flags se resetean cuando el precio vuelve a entrar al canal, o
        cuando el canal es reemplazado por uno nuevo (con la misma lógica de
        inicialización de Pine: si el precio ya está afuera del canal nuevo,
        el flag del lado correspondiente arranca en True para no re-disparar
        de inmediato).
      - Reacciones ("React"): la mecha toca el borde del canal pero el
        cierre se mantiene adentro — señal más débil que una ruptura franca.

    A diferencia de run_auto_channels() (que busca 'el mejor canal posible HOY'
    desde cero, sin este historial de estado), este modo es fiel a cómo se
    vería el indicador si lo hubieras tenido corriendo en tiempo real desde
    el principio del historial.

    `lookback`: si se indica (y es menor que len(df)), el repaso bar-por-bar
    solo recorre las últimas `lookback` velas en vez de TODO el historial
    descargado. Como max_channel_bars ya limita cuánto puede llegar a medir
    un canal (ej. 80 velas para el canal corto), repasar 80.000 velas desde
    2010 para un canal que como mucho abarca 80 no aporta nada, solo demora.
    Las coordenadas del canal resultante (y de los pivotes) se devuelven
    re-alineadas al índice del `df` ORIGINAL completo, para que encajen bien
    con el resto del pipeline y con el gráfico.
    """
    df_full_len = len(df)
    pad = 0
    if lookback is not None and lookback < df_full_len:
        pad = df_full_len - lookback
        df = df.iloc[pad:].reset_index(drop=True)

    df = df.reset_index(drop=True)
    atr_s = atr(df, atr_len)

    piv_hi_series = find_pivots(df["high"], pivot_len, "high")
    piv_lo_series = find_pivots(df["low"], pivot_len, "low")

    hi_positions = list(piv_hi_series.dropna().index)
    hi_values = list(piv_hi_series.dropna().values)
    lo_positions = list(piv_lo_series.dropna().index)
    lo_values = list(piv_lo_series.dropna().values)

    # Un pivote en la posición p solo se "confirma" pivot_len barras después
    # (necesita ver pivot_len velas a la derecha) — igual que bar_index-pivotLenInput en Pine.
    hi_confirm = {p + pivot_len: v for p, v in zip(hi_positions, hi_values)}
    lo_confirm = {p + pivot_len: v for p, v in zip(lo_positions, lo_values)}

    n = len(df)
    warmup = max(pivot_len * 4, 50)

    # Extraídos UNA sola vez: find_channel se llama hasta dos veces por cada
    # evento de pivote (pueden ser miles en un historial largo), así que
    # convertir df["low"]/df["high"]/atr_s a numpy afuera del loop evita
    # repetir esa conversión miles de veces.
    low_arr = df["low"].to_numpy()
    high_arr = df["high"].to_numpy()
    open_arr = df["open"].to_numpy()
    close_arr = df["close"].to_numpy()
    atr_arr = atr_s.to_numpy()

    up_state: Optional[Channel] = None
    dn_state: Optional[Channel] = None
    hi_idx_seen, hi_val_seen = [], []
    lo_idx_seen, lo_val_seen = [], []

    # ── Estado persistente de señales (equivalente a los "var" de Pine) ──
    up_bull_fired = up_bear_fired = False
    dn_bull_fired = dn_bear_fired = False

    # Últimos valores calculados bar-por-bar (se sobreescriben cada vela;
    # al terminar el loop contienen el estado de la ÚLTIMA vela, que es lo
    # que se usa para la señal final).
    up_break_sup = up_break_res = up_react_sup = up_react_res = False
    dn_break_sup = dn_break_res = dn_react_sup = dn_react_res = False
    up_bo_dir = dn_bo_dir = 0

    events = 0
    for bar_index in range(warmup, n):
        new_hi = bar_index in hi_confirm
        new_lo = bar_index in lo_confirm
        if new_hi:
            hi_idx_seen.append(bar_index - pivot_len)
            hi_val_seen.append(hi_confirm[bar_index])
        if new_lo:
            lo_idx_seen.append(bar_index - pivot_len)
            lo_val_seen.append(lo_confirm[bar_index])

        if new_hi or new_lo:
            events += 1

            if len(lo_idx_seen) >= 2:
                cand = find_channel(low_arr, high_arr, lo_idx_seen, lo_val_seen, hi_idx_seen, hi_val_seen, "up",
                                     min_channel_bars, max_channel_bars, quality_th,
                                     atr_arr, bar_index, recent_n=recent_n, lookback_pairs=lookback_pairs)
                if cand is not None:
                    old_q = up_state.quality if up_state else 0.0
                    if cand.quality > old_q * replace_ratio:
                        changed = (up_state is None) or (cand.x1 != up_state.x1) or (cand.x2 != up_state.x2)
                        up_state = cand
                        if changed:
                            # Re-inicialización de flags igual que el bloque "if upChanged" de Pine.
                            new_b = up_state.base_at(bar_index)
                            c = close_arr[bar_index]
                            if _is_inside(c, new_b, up_state.offset):
                                up_bull_fired = False
                                up_bear_fired = False
                            else:
                                up_bull_fired = c > max(new_b, new_b + up_state.offset)
                                up_bear_fired = c < min(new_b, new_b + up_state.offset)

            if len(hi_idx_seen) >= 2:
                cand = find_channel(low_arr, high_arr, hi_idx_seen, hi_val_seen, lo_idx_seen, lo_val_seen, "down",
                                     min_channel_bars, max_channel_bars, quality_th,
                                     atr_arr, bar_index, recent_n=recent_n, lookback_pairs=lookback_pairs)
                if cand is not None:
                    old_q = dn_state.quality if dn_state else 0.0
                    if cand.quality > old_q * replace_ratio:
                        changed = (dn_state is None) or (cand.x1 != dn_state.x1) or (cand.x2 != dn_state.x2)
                        dn_state = cand
                        if changed:
                            new_b = dn_state.base_at(bar_index)
                            c = close_arr[bar_index]
                            if _is_inside(c, new_b, dn_state.offset):
                                dn_bull_fired = False
                                dn_bear_fired = False
                            else:
                                dn_bull_fired = c > max(new_b, new_b + dn_state.offset)
                                dn_bear_fired = c < min(new_b, new_b + dn_state.offset)

        # ── Ruptura/reacción, TODA vela (igual que barstate.isconfirmed en Pine) ──
        up_break_sup = up_break_res = up_react_sup = up_react_res = False
        up_bo_dir = 0
        if up_state is not None and (bar_index - up_state.x2) <= max_channel_bars:  # GUARD 1
            b_now = up_state.base_at(bar_index)          # soporte
            p_now = b_now + up_state.offset               # resistencia (offset > 0)
            b_prev = up_state.base_at(bar_index - 1)
            p_prev = b_prev + up_state.offset
            tol = atr_arr[bar_index] * 0.12
            cross_tol = atr_arr[bar_index] * 1.5           # GUARD 2
            c_now, c_prev = close_arr[bar_index], close_arr[bar_index - 1]

            if b_now <= c_now <= p_now:
                up_bull_fired = False
                up_bear_fired = False
            if c_now > p_now and not up_bull_fired and c_prev <= p_prev + cross_tol:
                up_bull_fired = True
                up_break_res = True
                up_bo_dir = 1
            if c_now < b_now and not up_bear_fired and c_prev >= b_prev - cross_tol:
                up_bear_fired = True
                up_break_sup = True
                up_bo_dir = -1
            if not up_break_sup and low_arr[bar_index] <= b_now + tol and c_now > b_now and open_arr[bar_index] > b_now:
                up_react_sup = True
            if not up_break_res and high_arr[bar_index] >= p_now - tol and c_now < p_now and open_arr[bar_index] < p_now:
                up_react_res = True

        dn_break_sup = dn_break_res = dn_react_sup = dn_react_res = False
        dn_bo_dir = 0
        if dn_state is not None and (bar_index - dn_state.x2) <= max_channel_bars:  # GUARD 1
            b_now = dn_state.base_at(bar_index)          # resistencia (arriba)
            p_now = b_now + dn_state.offset                # soporte (abajo, offset < 0)
            b_prev = dn_state.base_at(bar_index - 1)
            p_prev = b_prev + dn_state.offset
            tol = atr_arr[bar_index] * 0.12
            cross_tol = atr_arr[bar_index] * 1.5           # GUARD 2
            c_now, c_prev = close_arr[bar_index], close_arr[bar_index - 1]

            if p_now <= c_now <= b_now:
                dn_bull_fired = False
                dn_bear_fired = False
            if c_now > b_now and not dn_bull_fired and c_prev <= b_prev + cross_tol:
                dn_bull_fired = True
                dn_break_res = True
                dn_bo_dir = 1
            if c_now < p_now and not dn_bear_fired and c_prev >= p_prev - cross_tol:
                dn_bear_fired = True
                dn_break_sup = True
                dn_bo_dir = -1
            if not dn_break_res and high_arr[bar_index] >= b_now - tol and c_now < b_now and open_arr[bar_index] < b_now:
                dn_react_res = True
            if not dn_break_sup and low_arr[bar_index] <= p_now + tol and c_now > p_now and open_arr[bar_index] > p_now:
                dn_react_sup = True

    if progress:
        print(f"Simulación incremental: {events} eventos de pivote procesados "
              f"({len(hi_idx_seen)} altos, {len(lo_idx_seen)} bajos confirmados"
              f"{' en la ventana de repaso' if pad else ' en todo el historial'})")

    # ── Señal agregada, igual que la fórmula de Pine ──
    #   bullSig = upReactSup or dnBreakRes   (rebote en soporte ascendente, o
    #                                          ruptura de la resistencia de un canal descendente)
    #   bearSig = upBreakSup or dnReactRes   (ruptura del soporte ascendente, o
    #                                          rechazo en la resistencia de un canal descendente)
    bull_sig = up_react_sup or dn_break_res
    bear_sig = up_break_sup or dn_react_res
    has_bull_breakout = (up_bo_dir == 1) or (dn_bo_dir == 1)
    has_bear_breakout = (up_bo_dir == -1) or (dn_bo_dir == -1)
    any_react = up_react_sup or up_react_res or dn_react_sup or dn_react_res

    breakout_label = None
    if has_bull_breakout:
        breakout_label = "Ruptura alcista (▲)"
    elif has_bear_breakout:
        breakout_label = "Ruptura bajista (▼)"

    react_label = None
    if any_react:
        if up_react_sup or dn_break_res:
            react_label = "Reacción alcista (rebote en soporte)"
        elif up_break_sup or dn_react_res:
            react_label = "Reacción bajista (rechazo en resistencia)"

    signals = {
        "signal": "Buy" if bull_sig else ("Sell" if bear_sig else "Wait"),
        "breakout": breakout_label,
        "react": react_label,
    }

    if pad:
        # Re-alinear todo al índice del df ORIGINAL (sin recortar), para que
        # las coordenadas de los canales y pivotes encajen con el resto del
        # pipeline (que sigue usando el df completo).
        if up_state is not None:
            up_state = replace(up_state, x1=up_state.x1 + pad, x2=up_state.x2 + pad)
        if dn_state is not None:
            dn_state = replace(dn_state, x1=dn_state.x1 + pad, x2=dn_state.x2 + pad)
        hi_idx_seen = [i + pad for i in hi_idx_seen]
        lo_idx_seen = [i + pad for i in lo_idx_seen]

    return up_state, dn_state, signals, (hi_idx_seen, hi_val_seen, lo_idx_seen, lo_val_seen)






def run_auto_channels(df: pd.DataFrame, pivot_len=21, atr_len=14,
                       min_channel_bars=10, max_channel_bars=400,
                       quality_th=0.55, recent_n=8, lookback_pairs=5):
    df = df.reset_index(drop=True)
    atr_s = atr(df, atr_len)

    piv_hi = find_pivots(df["high"], pivot_len, "high")
    piv_lo = find_pivots(df["low"], pivot_len, "low")

    hi_idx = list(piv_hi.dropna().index)
    hi_val = list(piv_hi.dropna().values)
    lo_idx = list(piv_lo.dropna().index)
    lo_val = list(piv_lo.dropna().values)

    last_bar = len(df) - 1
    low_arr = df["low"].to_numpy()
    high_arr = df["high"].to_numpy()
    atr_arr = atr_s.to_numpy()

    up_channel = find_channel(low_arr, high_arr, lo_idx, lo_val, hi_idx, hi_val, "up",
                               min_channel_bars, max_channel_bars, quality_th,
                               atr_arr, last_bar, recent_n=recent_n, lookback_pairs=lookback_pairs)
    down_channel = find_channel(low_arr, high_arr, hi_idx, hi_val, lo_idx, lo_val, "down",
                                 min_channel_bars, max_channel_bars, quality_th,
                                 atr_arr, last_bar, recent_n=recent_n, lookback_pairs=lookback_pairs)

    signals = detect_breakouts(df, up_channel, down_channel, atr_s, last_bar)
    return up_channel, down_channel, signals, (hi_idx, hi_val, lo_idx, lo_val)


def select_best_channel(up_ch: Optional[Channel], down_ch: Optional[Channel]) -> Optional[Channel]:
    """
    Modo 'Auto' (como el indicador original de TradingView): de los dos
    candidatos (ascendente y descendente) para un mismo horizonte, se
    queda solo con el de mayor containment ratio (quality). Si solo uno
    de los dos existe, se usa ese. Si ninguno existe, devuelve None.
    """
    if up_ch is None and down_ch is None:
        return None
    if up_ch is None:
        return down_ch
    if down_ch is None:
        return up_ch
    return up_ch if up_ch.quality >= down_ch.quality else down_ch


def _pivot_len_candidates(rng) -> list:
    """
    rng = (mínimo, máximo, paso). Devuelve la lista de pivot_len a probar
    en la búsqueda automática, saneando valores fuera de rango (mínimo >= 1,
    paso >= 1) para que un config.py mal escrito no rompa el script.
    """
    lo, hi, step = rng
    lo = max(1, int(lo))
    hi = max(lo, int(hi))
    step = max(1, int(step))
    return list(range(lo, hi + 1, step))


def search_best_pivot_len(df: pd.DataFrame, candidates, incremental: bool,
                           base_kwargs: dict, lookback: Optional[int] = None,
                           both_directions: bool = True):
    """
    Elige automáticamente el mejor pivot_len para UN horizonte (largo,
    mediano o corto), probando cada valor de `candidates` con el mismo
    pipeline que ya existía (simulate_incremental o run_auto_channels) y
    quedándose con el que da mejor calidad de canal — el mismo containment
    ratio ("Q=xx%") que ya se ve en el gráfico. Así ya no hace falta afinar
    PIVOT_LEN/_MED/_SHORT a mano en config.py: cada canal elige el suyo solo.

    Puntaje de cada candidato:
      - both_directions=True  (SHOW_BOTH_DIRECTIONS, se dibujan los dos):
        promedio de la calidad ascendente y descendente (si ambas existen),
        para no premiar un pivot_len que deja un lado muy bueno y el otro
        sin canal. Si solo aparece un lado, se usa ese.
      - both_directions=False (modo 'Auto', se queda solo el mejor de los
        dos): se usa el MÁXIMO de los dos, porque es el único que termina
        mostrándose (ver select_best_channel).
      - Si no aparece ningún canal para ese pivot_len, puntaje = 0.

    Desempate: la calidad tiende a formar "mesetas" — varios pivot_len
    seguidos que dan exactamente (o casi) el mismo puntaje máximo, típicamente
    un grupo chico (pivot_len bajo, canal muy local) y uno más grande
    (pivot_len alto, canal más estructural) que igualan el mismo containment
    ratio. Ante un empate en el puntaje, esta función se queda con el
    pivot_len MÁS GRANDE del grupo empatado (candidates se recorre de menor
    a mayor y se sigue actualizando mientras el puntaje sea >= al mejor
    encontrado hasta ahora). Esto no sacrifica nada de calidad — sigue siendo
    exactamente el máximo encontrado — pero evita que el canal LARGO termine
    eligiendo un pivot_len tan chico como el del canal CORTO solo porque
    ambos llegan al mismo % de containment; se prioriza el canal más ancho/
    estructural entre los que empatan en calidad.

    df, incremental, base_kwargs, lookback: mismos parámetros que ya se le
    pasaban a mano a simulate_incremental/run_auto_channels (base_kwargs NO
    debe incluir pivot_len, que es justo lo que se está barriendo).

    Devuelve (mejor_pivot_len, mejor_puntaje, scores) — `scores` es un dict
    {pivot_len: puntaje} con TODOS los candidatos probados (por si se quiere
    loguear o graficar la curva de calidad vs. pivot_len).
    Si `candidates` está vacío, devuelve (None, 0.0, {}).
    """
    best_len, best_score = None, -1.0
    scores = {}
    for p in candidates:
        if incremental:
            up_c, dn_c, _, _ = simulate_incremental(
                df, pivot_len=p, progress=False, lookback=lookback, **base_kwargs)
        else:
            up_c, dn_c, _, _ = run_auto_channels(df, pivot_len=p, **base_kwargs)

        quals = [c.quality for c in (up_c, dn_c) if c is not None]
        if not quals:
            score = 0.0
        elif both_directions:
            score = sum(quals) / len(quals)
        else:
            score = max(quals)
        scores[p] = score

        # >= (no >): entre empates se queda con el candidato más reciente del
        # recorrido, que al ir de menor a mayor pivot_len es el más grande.
        if score >= best_score:
            best_score, best_len = score, p

    if best_len is None:
        return None, 0.0, scores
    return best_len, best_score, scores


def detect_breakouts(df, up_channel, down_channel, atr_s, last_bar):
    """
    Replica la lógica de breakout/react, pero evaluada SOLO en el bar más
    reciente, sin memoria de barras anteriores. Se usa en el modo NO
    incremental (run_auto_channels), donde no hay un historial bar-por-bar
    del que sacar flags de histéresis (fired) — cada corrida es un
    'snapshot' del mejor canal posible hoy, no una réplica en vivo.

    En modo incremental (simulate_incremental) NO se usa esta función: ahí
    el breakout/react/histéresis/expiración de canal se calculan bar-por-bar
    dentro del propio loop, siguiendo la lógica completa de Pine (ver
    simulate_incremental para la versión fiel).
    """
    signals = {"breakout": None, "react": None, "signal": "Wait"}
    close = df["close"].iloc[last_bar]
    prev_close = df["close"].iloc[last_bar - 1]

    def check(ch, label):
        if ch is None:
            return
        base_now = ch.base_at(last_bar)
        par_now = base_now + ch.offset
        base_prev = ch.base_at(last_bar - 1)
        par_prev = base_prev + ch.offset
        upper, lower = max(base_now, par_now), min(base_now, par_now)
        upper_prev, lower_prev = max(base_prev, par_prev), min(base_prev, par_prev)
        cross_tol = atr_s.iloc[last_bar] * 1.5

        if close > upper and prev_close <= upper_prev + cross_tol:
            signals["breakout"] = f"{label}: ruptura alcista (▲)"
            signals["signal"] = "Buy"
        elif close < lower and prev_close >= lower_prev - cross_tol:
            signals["breakout"] = f"{label}: ruptura bajista (▼)"
            signals["signal"] = "Sell"

    check(up_channel, "Canal largo ascendente")
    check(down_channel, "Canal largo descendente")
    return signals


def combine_with_kalman(signals: dict, kres: KalmanResult, last_bar: int) -> dict:
    """
    Combina la señal de ruptura de canal con el estado de tendencia del
    Kalman Flow. Regla de confirmación:
      - "Buy" de canal + Kalman en tendencia alcista (trend==1)  -> CONFIRMADO
      - "Sell" de canal + Kalman en tendencia bajista (trend==-1) -> CONFIRMADO
      - Cualquier otra combinación -> la ruptura de canal se marca como
        "sin confirmar" (el Kalman todavía no está de acuerdo con la dirección).
    Esto no reemplaza la señal original del canal, solo la enriquece con un
    segundo filtro independiente, igual que usarías los dos indicadores juntos
    manualmente en TradingView.
    """
    k_trend = int(kres.trend.iloc[last_bar])
    k_label = {1: "Alcista", -1: "Bajista", 0: "Neutral"}[k_trend]
    signals["kalman_trend"] = k_label

    base_signal = signals["signal"]
    if base_signal == "Buy" and k_trend == 1:
        signals["confirmed_signal"] = "Buy (confirmado por Kalman)"
    elif base_signal == "Sell" and k_trend == -1:
        signals["confirmed_signal"] = "Sell (confirmado por Kalman)"
    elif base_signal in ("Buy", "Sell"):
        signals["confirmed_signal"] = f"{base_signal} (sin confirmar, Kalman={k_label})"
    else:
        signals["confirmed_signal"] = "Wait"

    # además, exponer si hubo un flip de Kalman justo en la última barra,
    # independiente de si hay ruptura de canal o no (útil como señal propia)
    if bool(kres.flip_up.iloc[last_bar]):
        signals["kalman_flip"] = "Kalman: flip alcista (Long)"
    elif bool(kres.flip_down.iloc[last_bar]):
        signals["kalman_flip"] = "Kalman: flip bajista (Short)"
    else:
        signals["kalman_flip"] = None

    return signals


# ══════════════════════════════════════════════════════════
# 6. VISUALIZACIÓN
# ══════════════════════════════════════════════════════════

def plot_result(df, up_channel, down_channel, signals, pivots, out_path,
                 extend_bars=150, dark=True, show_pivots=True, plot_last=None,
                 fig_width=14, fig_height=7, extra_channels=None,
                 kalman: Optional[KalmanResult] = None, show_kalman=True,
                 kalman_up_color="#30FDCF", kalman_down_color="#E117B7",
                 ax=None, show_xlabels=True):
    """
    extend_bars: cuántas barras se extienden las líneas hacia la derecha
                 más allá de la última vela, replicando el "Extend: Right"
                 de TradingView.
    show_pivots: si es False, no dibuja los triángulos de pivotes.
    plot_last:   si se indica, solo se muestra en el eje X las últimas N
                 velas (recorta el historial visible por la izquierda;
                 los canales se siguen calculando con todo el historial).
    fig_width / fig_height: tamaño de la figura en pulgadas. Sube fig_width
                 para que el gráfico se vea más ancho (velas más separadas).
    extra_channels: lista opcional de tuplas (Channel, color, label) para
                 dibujar canales adicionales (ej. el canal corto de menor
                 pivot_len), con el mismo estilo que los dos principales.
    kalman: resultado de kalman_flow() a superponer (línea base + bandas +
                 flips), coloreado según la tendencia vigente en cada tramo.
    show_kalman: si es False, se calcula igual (para la señal combinada)
                 pero no se dibuja en el gráfico.
    ax: si se pasa un Axes ya existente, se dibuja ahí en vez de crear una
                 figura nueva (para combinarlo con otro panel, ej. Smart
                 Money Flow, en una sola imagen vía plot_combined()). En ese
                 caso NO se guarda el archivo acá — lo hace el que llama.
    show_xlabels: si es False, no dibuja las etiquetas de fecha del eje X
                 (para usarlo como panel superior de una figura combinada,
                 donde solo el panel de abajo muestra las fechas).
    """
    hi_idx, hi_val, lo_idx, lo_val = pivots

    bg = "#131722" if dark else "#FFFFFF"      # mismo fondo oscuro de TradingView
    grid = "#2A2E39" if dark else "#E0E0E0"
    txt = "#D1D4DC" if dark else "#1A1A1A"
    wick = "#787B86"

    standalone = ax is None
    if standalone:
        fig, ax = plt.subplots(figsize=(fig_width, fig_height))
        fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    last_bar = len(df) - 1
    x_end = last_bar + extend_bars  # las líneas se proyectan más allá de la última vela

    x_lo = max(0, last_bar - plot_last) if plot_last is not None else 0
    draw_df = df.iloc[x_lo:] if x_lo > 0 else df

    # velas simplificadas (línea de cierre + mechas), colores estilo TradingView
    ax.vlines(draw_df.index, draw_df["low"], draw_df["high"], color=wick, linewidth=0.6, alpha=0.7)
    up = draw_df["close"] >= draw_df["open"]
    ax.vlines(draw_df.index[up], draw_df["open"][up], draw_df["close"][up], color="#26a69a", linewidth=2.2)
    ax.vlines(draw_df.index[~up], draw_df["close"][~up], draw_df["open"][~up], color="#ef5350", linewidth=2.2)

    def draw_channel(ch, color, label, linewidth=2):
        if ch is None:
            return
        xs = np.array([ch.x1, x_end])
        base_ys = ch.base_at(xs)
        par_ys = base_ys + ch.offset
        mid_ys = base_ys + ch.offset / 2
        ax.plot(xs, base_ys, color=color, linewidth=linewidth, label=f"{label} (Q={ch.quality:.0%})")
        ax.plot(xs, par_ys, color=color, linewidth=linewidth)
        ax.plot(xs, mid_ys, color=color, linewidth=1, linestyle="--", alpha=0.8)
        ax.fill_between(xs, base_ys, par_ys, color=color, alpha=0.08)

    if up_channel is not None:
        draw_channel(up_channel, "#00E676", "Canal largo ascendente")
    if down_channel is not None:
        draw_channel(down_channel, "#FF5252", "Canal largo descendente")

    for ch, color, label in (extra_channels or []):
        draw_channel(ch, color, label, linewidth=1.6)

    # ── Kalman Flow overlay ──
    if kalman is not None and show_kalman:
        k_level = kalman.level.iloc[x_lo:] if x_lo > 0 else kalman.level
        k_upper = kalman.upper.iloc[x_lo:] if x_lo > 0 else kalman.upper
        k_lower = kalman.lower.iloc[x_lo:] if x_lo > 0 else kalman.lower
        k_trend = kalman.trend.iloc[x_lo:] if x_lo > 0 else kalman.trend

        xs_k = k_level.index.to_numpy()
        trend_arr = k_trend.to_numpy()
        level_arr = k_level.to_numpy()
        upper_arr_k = k_upper.to_numpy()
        lower_arr_k = k_lower.to_numpy()

        def _trend_colors(tv):
            out = np.full(tv.shape, "#787B86", dtype=object)
            out[tv == 1] = kalman_up_color
            out[tv == -1] = kalman_down_color
            return out

        # Baseline + relleno coloreados por tramo de tendencia (igual efecto que
        # el "trendCol" dinámico de Pine), con UNA sola LineCollection/PolyCollection
        # en vez de un ax.plot()/fill_between() por cada cambio de tendencia.
        # Con historiales largos y muchos flips, esas cientos de llamadas
        # individuales a matplotlib eran justo lo que hacía lento el gráfico.
        if len(xs_k) > 1:
            points = np.column_stack([xs_k, level_arr])
            segments = np.stack([points[:-1], points[1:]], axis=1)
            seg_colors = _trend_colors(trend_arr[:-1])
            ax.add_collection(LineCollection(segments, colors=seg_colors, linewidths=1.8, zorder=4))

            verts = np.empty((len(xs_k) - 1, 4, 2))
            verts[:, 0, 0] = xs_k[:-1]; verts[:, 0, 1] = upper_arr_k[:-1]
            verts[:, 1, 0] = xs_k[1:];  verts[:, 1, 1] = upper_arr_k[1:]
            verts[:, 2, 0] = xs_k[1:];  verts[:, 2, 1] = lower_arr_k[1:]
            verts[:, 3, 0] = xs_k[:-1]; verts[:, 3, 1] = lower_arr_k[:-1]
            ax.add_collection(PolyCollection(verts, facecolors=seg_colors, edgecolors="none",
                                              alpha=0.08, zorder=2))
        ax.plot([], [], color=kalman_up_color, linewidth=1.8, label="Kalman baseline")

        ax.plot(xs_k, upper_arr_k, color=kalman_up_color, linewidth=0.8, alpha=0.6, linestyle=":")
        ax.plot(xs_k, lower_arr_k, color=kalman_down_color, linewidth=0.8, alpha=0.6, linestyle=":")

        # Marcadores Long/Short en los flips: un solo scatter por tipo (igual
        # que los triángulos de pivotes) en vez de un ax.annotate() con caja de
        # texto por cada flip individual — mucho más rápido si hay muchos flips.
        # Los últimos MAX_TEXT_LABELS sí llevan la cajita de texto, para no
        # perder la lectura rápida de la señal más reciente.
        flip_up_pts = kalman.flip_up.iloc[x_lo:] if x_lo > 0 else kalman.flip_up
        flip_dn_pts = kalman.flip_down.iloc[x_lo:] if x_lo > 0 else kalman.flip_down
        fu_idx = flip_up_pts[flip_up_pts].index.to_numpy()
        fd_idx = flip_dn_pts[flip_dn_pts].index.to_numpy()
        avg_range = (df["high"] - df["low"]).mean()
        low_arr_full = df["low"].to_numpy()
        high_arr_full = df["high"].to_numpy()

        if len(fu_idx):
            ax.scatter(fu_idx, low_arr_full[fu_idx] - avg_range * 0.6, marker="^", color=kalman_up_color,
                       s=45, zorder=6, edgecolors="#000000", linewidths=0.4, label="Kalman Long")
        if len(fd_idx):
            ax.scatter(fd_idx, high_arr_full[fd_idx] + avg_range * 0.6, marker="v", color=kalman_down_color,
                       s=45, zorder=6, edgecolors="#000000", linewidths=0.4, label="Kalman Short")

        MAX_TEXT_LABELS = 15
        recent_flips = sorted(
            [(int(i), "Long", low_arr_full[i] - avg_range * 1.2, kalman_up_color) for i in fu_idx] +
            [(int(i), "Short", high_arr_full[i] + avg_range * 1.2, kalman_down_color) for i in fd_idx],
            key=lambda t: t[0]
        )[-MAX_TEXT_LABELS:]
        for i, label, y, color in recent_flips:
            ax.annotate(label, xy=(i, y), ha="center", va="center", fontsize=8, fontweight="bold",
                        color="#000000",
                        bbox=dict(boxstyle="round,pad=0.25", facecolor=color, edgecolor="none"),
                        zorder=7)

    if show_pivots:
        if x_lo > 0:
            hi_pts = [(i, v) for i, v in zip(hi_idx, hi_val) if i >= x_lo]
            lo_pts = [(i, v) for i, v in zip(lo_idx, lo_val) if i >= x_lo]
            hi_idx_d, hi_val_d = map(list, zip(*hi_pts)) if hi_pts else ([], [])
            lo_idx_d, lo_val_d = map(list, zip(*lo_pts)) if lo_pts else ([], [])
        else:
            hi_idx_d, hi_val_d, lo_idx_d, lo_val_d = hi_idx, hi_val, lo_idx, lo_val
        ax.scatter(hi_idx_d, hi_val_d, marker="v", color="#FF5252", s=22, zorder=5, label="Pivote alto")
        ax.scatter(lo_idx_d, lo_val_d, marker="^", color="#00E676", s=22, zorder=5, label="Pivote bajo")

    if signals["breakout"]:
        ax.annotate(signals["breakout"], xy=(last_bar, df["close"].iloc[-1]),
                    xytext=(last_bar - 60, df["close"].iloc[-1]),
                    color="#FFEB3B", fontsize=10, fontweight="bold")

    ax.axvline(last_bar, color=wick, linewidth=0.8, linestyle=":", alpha=0.6)  # marca la última vela real

    if plot_last is not None:
        ax.set_xlim(x_lo, x_end + 5)
        visible = df.iloc[x_lo:last_bar + 1]
        y_min, y_max = visible["low"].min(), visible["high"].max()
        pad = (y_max - y_min) * 0.10
        ax.set_ylim(y_min - pad, y_max + pad)

    title_signal = signals.get("confirmed_signal", signals["signal"])
    ax.set_title(f"Auto S/R Channels + Kalman Flow — señal actual: {title_signal}", color=txt)
    legend = ax.legend(loc="upper left", fontsize=8, facecolor=bg, edgecolor=grid)
    for text in legend.get_texts():
        text.set_color(txt)

    # ── Eje X en fechas (DD/MM/AAAA) en vez de número de barra ──
    n_bars = len(df)
    if n_bars >= 2:
        avg_delta = (df["datetime"].iloc[-1] - df["datetime"].iloc[0]) / (n_bars - 1)
    else:
        avg_delta = pd.Timedelta(days=1)
    last_dt = df["datetime"].iloc[-1]

    def bar_to_date(x, pos=None):
        idx = int(round(x))
        if idx < 0:
            idx = 0
        if idx < n_bars:
            dt = df["datetime"].iloc[idx]
        else:
            dt = last_dt + avg_delta * (idx - (n_bars - 1))
        return dt.strftime("%d/%m/%Y")

    if show_xlabels:
        ax.xaxis.set_major_locator(mticker.MaxNLocator(nbins=10, integer=True))
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(bar_to_date))
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        ax.set_xlabel("Fecha", color=txt)
    else:
        ax.tick_params(labelbottom=False)

    ax.set_ylabel("Precio", color=txt)
    ax.tick_params(colors=txt)
    ax.grid(color=grid, linewidth=0.5, alpha=0.5)
    for spine in ax.spines.values():
        spine.set_color(grid)

    if standalone:
        fig.tight_layout()
        fig.savefig(out_path, dpi=140, facecolor=bg)
        plt.close(fig)
        print(f"Gráfico guardado en: {out_path}")


def plot_combined(df, up_channel, down_channel, signals, pivots, smf_res, out_path,
                   extend_bars=150, dark=True, show_pivots=True, plot_last=None,
                   fig_width=20, fig_height=10, extra_channels=None,
                   kalman: Optional[KalmanResult] = None, show_kalman=False,
                   overbought_level=60.0, oversold_level=-60.0,
                   bull_color="#00E5A0", bear_color="#FF5252",
                   on_main_chart=False, smf_overlay_alpha=0.35):
    """
    Une el gráfico de canales y el Smart Money Flow en UNA sola imagen, con
    el mismo eje X (bar por bar).

    on_main_chart: False (default) = igual que antes, dos paneles apilados
        (canales arriba, SMF abajo), compartiendo la misma escala temporal.
        True = un solo panel: el SMF se dibuja DIRECTAMENTE encima del
        gráfico de precio, en un eje Y secundario a la derecha (mismo
        mecanismo que el overlay del Kalman Flow). Las velas y los canales
        se dibujan siempre por encima de las barras del SMF (que quedan de
        fondo, semi-transparentes).
    smf_overlay_alpha: opacidad de las barras del SMF cuando on_main_chart
        es True (más baja que en modo panel aparte, para no tapar tanto las
        velas).
    """
    bg = "#131722" if dark else "#FFFFFF"

    if on_main_chart:
        fig, ax1 = plt.subplots(figsize=(fig_width, fig_height * 0.7))
        fig.patch.set_facecolor(bg)

        plot_result(df, up_channel, down_channel, signals, pivots, out_path=None,
                    extend_bars=extend_bars, dark=dark, show_pivots=show_pivots, plot_last=plot_last,
                    extra_channels=extra_channels, kalman=kalman, show_kalman=show_kalman,
                    ax=ax1, show_xlabels=True)

        # Eje Y secundario superpuesto sobre el mismo panel (mismo truco que
        # usa matplotlib para overlays tipo Kalman, pero con eje propio para
        # no mezclar la escala de precio con la del oscilador).
        ax1b = ax1.twinx()
        smf.plot_smart_money_flow(df, smf_res, out_path=None, plot_last=plot_last,
                                   overbought_level=overbought_level, oversold_level=oversold_level,
                                   bull_color=bull_color, bear_color=bear_color,
                                   ax=ax1b, show_xlabels=False, extend_bars=extend_bars,
                                   show_title=False, show_grid=False, legend_loc="upper right",
                                   bar_alpha=smf_overlay_alpha)

        # Las barras del SMF quedan DETRÁS de las velas/canales: bajamos el
        # zorder del eje del SMF por debajo del principal y ocultamos el
        # "fondo" del eje principal para que se vea a través de él.
        ax1b.set_zorder(ax1.get_zorder() - 1)
        ax1.patch.set_visible(False)
        ax1b.tick_params(axis="y", labelsize=8)

        fig.tight_layout()
        fig.savefig(out_path, dpi=140, facecolor=bg)
        plt.close(fig)
        print(f"Gráfico combinado (SMF sobre gráfico principal) guardado en: {out_path}")
        return

    # ── Modo panel aparte (comportamiento original) ──
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(fig_width, fig_height),
        gridspec_kw={"height_ratios": [3, 1]}, sharex=True,
    )
    fig.patch.set_facecolor(bg)

    plot_result(df, up_channel, down_channel, signals, pivots, out_path=None,
                extend_bars=extend_bars, dark=dark, show_pivots=show_pivots, plot_last=plot_last,
                extra_channels=extra_channels, kalman=kalman, show_kalman=show_kalman,
                ax=ax1, show_xlabels=False)

    smf.plot_smart_money_flow(df, smf_res, out_path=None, plot_last=plot_last,
                               overbought_level=overbought_level, oversold_level=oversold_level,
                               bull_color=bull_color, bear_color=bear_color,
                               ax=ax2, show_xlabels=True, extend_bars=extend_bars)

    fig.tight_layout()
    fig.subplots_adjust(hspace=0.05)
    fig.savefig(out_path, dpi=140, facecolor=bg)
    plt.close(fig)
    print(f"Gráfico combinado guardado en: {out_path}")


# ══════════════════════════════════════════════════════════
# 7. MAIN
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    import os

    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    try:
        import config
    except ImportError:
        config = None

    def cfg(name, fallback):
        return getattr(config, name, fallback) if config else fallback

    parser = argparse.ArgumentParser(description="Auto S/R Channels + Kalman Flow - port de Pine Script a Python")
    parser.add_argument("csv", nargs="?", default=None,
                         help="Ruta al CSV de velas. Si se omite, se busca automáticamente "
                              "el CSV de config.py (CSV_NAME) en esta carpeta; si no existe, usa datos demo.")
    parser.add_argument("--timeframe", "--tf", dest="timeframe", default=None,
                         help="Temporalidad a graficar (ej. M15, H1, H2, H4, D1), independiente de la "
                              "TIMEFRAME configurada en config.py. Busca el archivo "
                              "<SYMBOL>_<TIMEFRAME>.csv en esta carpeta (ej. USDJPY_H1.csv) — ese CSV "
                              "tiene que existir de antemano (expórtalo primero con mt5_export_h4.py "
                              "usando esa temporalidad). Si no se indica, usa el CSV_NAME de config.py "
                              "tal cual (el TIMEFRAME que tengas configurado ahí).")
    parser.add_argument("--pivot-len", type=int, default=cfg("PIVOT_LEN", 21))
    parser.add_argument("--auto-pivot", action=argparse.BooleanOptionalAction,
                         default=cfg("PIVOT_LEN_AUTO", False),
                         help="Prueba varios pivot_len para cada canal activo (largo/mediano/corto) "
                              "y se queda con el que da mejor calidad (containment ratio), en vez "
                              "de usar el pivot_len fijo de --pivot-len/--medium-pivot-len/"
                              "--short-pivot-len. Usa --no-auto-pivot para el modo manual de siempre.")
    _plr = cfg("PIVOT_LEN_RANGE", (10, 40, 2))
    parser.add_argument("--pivot-len-min", type=int, default=_plr[0],
                         help="[--auto-pivot] pivot_len mínimo a probar para el canal largo.")
    parser.add_argument("--pivot-len-max", type=int, default=_plr[1],
                         help="[--auto-pivot] pivot_len máximo a probar para el canal largo.")
    parser.add_argument("--pivot-len-step", type=int, default=_plr[2],
                         help="[--auto-pivot] paso entre valores de pivot_len probados (más chico = "
                              "búsqueda más fina pero más lenta).")
    parser.add_argument("--atr-len", type=int, default=cfg("ATR_LEN", 14))
    parser.add_argument("--min-bars", type=int, default=cfg("MIN_BARS", 10))
    parser.add_argument("--max-bars", type=int, default=cfg("MAX_BARS", 400))
    parser.add_argument("--quality", type=float, default=cfg("QUALITY", 0.55))
    parser.add_argument("--out", default=None,
                         help="Nombre del PNG de salida. Si no se indica, se arma automáticamente "
                              "como <SYMBOL>_<TIMEFRAME>_chart.png usando la temporalidad efectiva "
                              "(la de --timeframe si la pasaste, si no la de config.py).")
    parser.add_argument("--hide-pivots", action=argparse.BooleanOptionalAction, default=cfg("HIDE_PIVOTS", False),
                         help="Oculta los triángulos de pivotes. Usa --no-hide-pivots para mostrarlos.")
    parser.add_argument("--plot-last", type=int, default=cfg("PLOT_LAST", 1200),
                         help="Cuántas velas recientes mostrar (0 = todo el historial)")
    parser.add_argument("--width", type=float, default=cfg("WIDTH", 14))
    parser.add_argument("--height", type=float, default=cfg("HEIGHT", 7))
    parser.add_argument("--extend-bars", type=int, default=cfg("EXTEND_BARS", 150),
                         help="Cuántas barras de espacio vacío se dejan a la derecha de la última "
                              "vela (donde se proyectan las líneas de los canales). Súbelo para que "
                              "las velas recientes queden más corridas hacia la izquierda, como en "
                              "TradingView, y así tengas más aire para analizar hacia dónde apunta el canal.")
    parser.add_argument("--until", default=cfg("UNTIL_DATE", None),
                         help="Corta el historial en esta fecha (ej. 20/03/2026) para un backtest puntual "
                              "(descarta todo lo posterior a esa fecha).")
    parser.add_argument("--since", "--from", dest="since", default=cfg("START_DATE_VIEW", None),
                         help="Arranca el análisis/gráfico desde esta fecha (ej. 26/03/2020), "
                              "descartando todo lo anterior. Se puede combinar con --until para "
                              "acotar un rango exacto (ej. --since 26/03/2020 --until 20/03/2021).")
    parser.add_argument("--recent-n", type=int, default=cfg("RECENT_N", 8))
    parser.add_argument("--lookback-pairs", type=int, default=cfg("LOOKBACK_PAIRS", 5))
    parser.add_argument("--incremental", action=argparse.BooleanOptionalAction, default=cfg("INCREMENTAL", False),
                         help="Simula el indicador EN VIVO (recomendado). Usa --no-incremental para el modo snapshot.")
    parser.add_argument("--replace-ratio", type=float, default=cfg("REPLACE_RATIO", 0.7))
    parser.add_argument("--show-both-directions", action=argparse.BooleanOptionalAction,
                         default=cfg("SHOW_BOTH_DIRECTIONS", True),
                         help="Dibuja el canal ascendente Y descendente en simultáneo en cada "
                              "horizonte (largo/mediano/corto), igual que TradingView. "
                              "Usa --no-show-both-directions para el modo 'Auto' viejo, que se "
                              "queda solo con el de mejor calidad y descarta el otro.")
    parser.add_argument("--lookback", type=int, default=cfg("CHANNEL_LOOKBACK", None),
                         help="[Solo modo incremental] Cuántas velas recientes repasar bar-por-bar "
                              "para el canal LARGO, en vez de repasar TODO el historial descargado "
                              "(ej. 80.000 velas). None/0 = repasa todo. Acelera mucho el cálculo sin "
                              "cambiar el canal resultante, siempre que el valor sea bastante mayor "
                              "que --max-bars.")

    # ── Canal mediano (segundo horizonte, entre el largo y el corto) ──
    parser.add_argument("--show-medium-channel", action=argparse.BooleanOptionalAction,
                         default=cfg("SHOW_MEDIUM_CHANNEL", False),
                         help="Agrega un canal intermedio (ascendente y descendente) con un "
                              "pivot_len entre el largo y el corto, para ver la tendencia de plazo medio.")
    parser.add_argument("--medium-pivot-len", type=int, default=cfg("PIVOT_LEN_MED", 30))
    _plrm = cfg("PIVOT_LEN_MED_RANGE", (6, 30, 2))
    parser.add_argument("--medium-pivot-len-min", type=int, default=_plrm[0],
                         help="[--auto-pivot] pivot_len mínimo a probar para el canal mediano.")
    parser.add_argument("--medium-pivot-len-max", type=int, default=_plrm[1],
                         help="[--auto-pivot] pivot_len máximo a probar para el canal mediano.")
    parser.add_argument("--medium-pivot-len-step", type=int, default=_plrm[2],
                         help="[--auto-pivot] paso entre valores de pivot_len probados.")
    parser.add_argument("--medium-atr-len", type=int, default=cfg("ATR_LEN_MED", 10))
    parser.add_argument("--medium-min-bars", type=int, default=cfg("MIN_BARS_MED", 8))
    parser.add_argument("--medium-max-bars", type=int, default=cfg("MAX_BARS_MED", 250))
    parser.add_argument("--medium-quality", type=float, default=cfg("QUALITY_MED", 0.4))
    parser.add_argument("--medium-recent-n", type=int, default=cfg("RECENT_N_MED", 15))
    parser.add_argument("--medium-lookback-pairs", type=int, default=cfg("LOOKBACK_PAIRS_MED", 10))
    parser.add_argument("--medium-replace-ratio", type=float, default=cfg("REPLACE_RATIO_MED", 0.7))
    parser.add_argument("--medium-lookback", type=int, default=cfg("CHANNEL_LOOKBACK_MED", None),
                         help="Igual que --lookback, pero para el canal MEDIANO.")

    # ── Canal corto (tercer horizonte, estructura más reciente/pequeña) ──
    parser.add_argument("--show-short-channel", action=argparse.BooleanOptionalAction,
                         default=cfg("SHOW_SHORT_CHANNEL", False),
                         help="Agrega un canal corto (ascendente y descendente) calculado con "
                              "un pivot_len más pequeño, para estructura más reciente/corta.")
    parser.add_argument("--short-pivot-len", type=int, default=cfg("PIVOT_LEN_SHORT", 15))
    _plrs = cfg("PIVOT_LEN_SHORT_RANGE", (3, 15, 1))
    parser.add_argument("--short-pivot-len-min", type=int, default=_plrs[0],
                         help="[--auto-pivot] pivot_len mínimo a probar para el canal corto.")
    parser.add_argument("--short-pivot-len-max", type=int, default=_plrs[1],
                         help="[--auto-pivot] pivot_len máximo a probar para el canal corto.")
    parser.add_argument("--short-pivot-len-step", type=int, default=_plrs[2],
                         help="[--auto-pivot] paso entre valores de pivot_len probados.")
    parser.add_argument("--short-atr-len", type=int, default=cfg("ATR_LEN_SHORT", 10))
    parser.add_argument("--short-min-bars", type=int, default=cfg("MIN_BARS_SHORT", 5))
    parser.add_argument("--short-max-bars", type=int, default=cfg("MAX_BARS_SHORT", 150))
    parser.add_argument("--short-quality", type=float, default=cfg("QUALITY_SHORT", 0.5))
    parser.add_argument("--short-recent-n", type=int, default=cfg("RECENT_N_SHORT", 12))
    parser.add_argument("--short-lookback-pairs", type=int, default=cfg("LOOKBACK_PAIRS_SHORT", 8))
    parser.add_argument("--short-replace-ratio", type=float, default=cfg("REPLACE_RATIO_SHORT", 0.7))
    parser.add_argument("--short-lookback", type=int, default=cfg("CHANNEL_LOOKBACK_SHORT", None),
                         help="Igual que --lookback, pero para el canal CORTO (el que más tarda, "
                              "porque tiene muchos más eventos de pivote).")

    # ── Kalman Flow ──
    parser.add_argument("--kalman", action=argparse.BooleanOptionalAction, default=cfg("KALMAN_ENABLED", True),
                         help="Calcula y superpone el Kalman Flow como filtro de confirmación de tendencia. "
                              "Usa --no-kalman para desactivarlo por completo.")
    parser.add_argument("--kalman-sensitivity", type=float, default=cfg("KALMAN_SENSITIVITY", 4.0))
    parser.add_argument("--kalman-mad-multp", type=float, default=cfg("KALMAN_MAD_MULTP", 1.65))
    parser.add_argument("--kalman-mad-multn", type=float, default=cfg("KALMAN_MAD_MULTN", 1.0))
    parser.add_argument("--kalman-vol-len", type=int, default=cfg("KALMAN_VOL_LEN", 50))
    parser.add_argument("--kalman-lookback", type=int, default=cfg("KALMAN_LOOKBACK", None),
                         help="Cuántas velas recientes usar para calcular el Kalman Flow "
                              "(línea base, bandas y tendencia Alcista/Bajista). None/0 = usa "
                              "TODO el historial descargado. Ponle un número (ej. 2-3 veces "
                              "--plot-last) para que la señal impresa en consola sea coherente "
                              "con lo que realmente se ve en el gráfico.")
    parser.add_argument("--hide-kalman-plot", action=argparse.BooleanOptionalAction,
                         default=cfg("HIDE_KALMAN_PLOT", False),
                         help="Calcula el Kalman igual (para la señal combinada) pero no lo dibuja en el gráfico.")

    # ── Smart Money Flow (panel aparte, necesita columna 'volume' en el CSV) ──
    parser.add_argument("--smart-money-flow", action=argparse.BooleanOptionalAction,
                         default=cfg("SMART_MONEY_FLOW_ENABLED", False),
                         help="Agrega un panel con el oscilador Smart Money Flow "
                              "(momentum + Chaikin Money Flow + MFI) DEBAJO del gráfico de "
                              "canales, en la misma imagen. Requiere que el CSV tenga columna "
                              "Volume (mt5_export_h4.py ya la incluye).")
    parser.add_argument("--smf-momentum-period", type=int, default=cfg("SMF_MOMENTUM_PERIOD", 10))
    parser.add_argument("--smf-trend-period", type=int, default=cfg("SMF_TREND_PERIOD", 21))
    parser.add_argument("--smf-mfi-period", type=int, default=cfg("SMF_MFI_PERIOD", 14))
    parser.add_argument("--smf-signal-smoothing", type=int, default=cfg("SMF_SIGNAL_SMOOTHING", 4))
    parser.add_argument("--smf-pivot-left", type=int, default=cfg("SMF_PIVOT_LEFT", 3))
    parser.add_argument("--smf-pivot-right", type=int, default=cfg("SMF_PIVOT_RIGHT", 3))
    parser.add_argument("--smf-sensitivity", type=float, default=cfg("SMF_PIVOT_SENSITIVITY", None),
                         help="Umbral de 'fuerza' para confirmar un pivote del oscilador. "
                              "None (default) = se AUTO-CALIBRA contra tu propio historial "
                              "(percentil configurable), en vez de copiar el 14 fijo del Pine "
                              "original (calibrado contra OTRO feed de volumen).")
    parser.add_argument("--smf-early-sensitivity", type=float, default=cfg("SMF_EARLY_SENSITIVITY", None),
                         help="Igual que --smf-sensitivity pero para pivotes 'early'. "
                              "None = se auto-calibra en base a --smf-sensitivity.")
    parser.add_argument("--smf-calibrate-percentile", type=float, default=cfg("SMF_CALIBRATE_PERCENTILE", 80.0),
                         help="Percentil de la distribución de 'fuerza' de pivotes usado para "
                              "auto-calibrar la sensibilidad (más alto = más estricto/selectivo).")
    parser.add_argument("--smf-on-main-chart", action=argparse.BooleanOptionalAction,
                         default=cfg("SMART_MONEY_FLOW_ON_MAIN_CHART", False),
                         help="En vez de dibujar el Smart Money Flow en un panel aparte debajo "
                              "del gráfico de precio, lo superpone DIRECTAMENTE sobre el gráfico "
                              "principal (mismo panel), con su propio eje Y a la derecha — igual "
                              "que el overlay del Kalman Flow. Requiere --smart-money-flow activo.")
    args = parser.parse_args()

    _effective_tf = (args.timeframe.upper() if args.timeframe else cfg("TIMEFRAME", None))
    if not args.out:
        symbol = cfg("SYMBOL", "")
        if symbol and _effective_tf:
            args.out = f"{symbol}_{_effective_tf}_chart.png"
        else:
            args.out = cfg("CHART_OUT", "auto_channels_result.png")

    csv_path = args.csv

    if not csv_path and args.timeframe and config:
        symbol = cfg("SYMBOL", "")
        tf = args.timeframe.upper()
        candidate = os.path.join(_here, f"{symbol}_{tf}.csv")
        if symbol and os.path.isfile(candidate):
            csv_path = candidate
        else:
            print(f"ERROR: no encontré {symbol}_{tf}.csv en esta carpeta.")
            print(f"       Exportalo primero con mt5_export_h4.py usando TIMEFRAME = \"{tf}\" "
                  f"en config.py (o pasa la ruta directamente como primer argumento).")
            sys.exit(1)

    if not csv_path and config:
        candidate = os.path.join(_here, cfg("CSV_NAME", ""))
        if cfg("CSV_NAME", "") and os.path.isfile(candidate):
            csv_path = candidate

    if csv_path:
        data = load_csv(csv_path)
        print(f"CSV cargado: {os.path.basename(csv_path)}")
    else:
        data = make_demo_data()
        print("Sin CSV -> usando datos de demo")

    if args.since:
        start = pd.to_datetime(args.since, dayfirst=True)
        data = data[data["datetime"] >= start].reset_index(drop=True)
        if len(data) == 0:
            print(f"ERROR: no hay velas después de {args.since}")
            sys.exit(1)

    if args.until:
        cutoff = pd.to_datetime(args.until, dayfirst=True)
        data = data[data["datetime"] <= cutoff].reset_index(drop=True)
        if len(data) == 0:
            print(f"ERROR: no hay velas antes de {args.until}")
            sys.exit(1)

    if args.auto_pivot:
        _cands = _pivot_len_candidates((args.pivot_len_min, args.pivot_len_max, args.pivot_len_step))
        _base_kw = dict(atr_len=args.atr_len, min_channel_bars=args.min_bars,
                         max_channel_bars=args.max_bars, quality_th=args.quality,
                         recent_n=args.recent_n, lookback_pairs=args.lookback_pairs)
        if args.incremental:
            _base_kw["replace_ratio"] = args.replace_ratio
        _best_len, _best_score, _ = search_best_pivot_len(
            data, _cands, args.incremental, _base_kw,
            lookback=(args.lookback or None), both_directions=args.show_both_directions)
        if _best_len is not None:
            print(f"Auto-pivot (canal largo): pivot_len={_best_len} elegido entre "
                  f"{_cands[0]}-{_cands[-1]} (paso {args.pivot_len_step}) — calidad={_best_score:.0%}")
            args.pivot_len = _best_len

    if args.incremental:
        up_ch, down_ch, sig, pivots = simulate_incremental(
            data,
            pivot_len=args.pivot_len,
            atr_len=args.atr_len,
            min_channel_bars=args.min_bars,
            max_channel_bars=args.max_bars,
            quality_th=args.quality,
            recent_n=args.recent_n,
            lookback_pairs=args.lookback_pairs,
            replace_ratio=args.replace_ratio,
            progress=False,
            lookback=(args.lookback or None),
        )
    else:
        up_ch, down_ch, sig, pivots = run_auto_channels(
            data,
            pivot_len=args.pivot_len,
            atr_len=args.atr_len,
            min_channel_bars=args.min_bars,
            max_channel_bars=args.max_bars,
            quality_th=args.quality,
            recent_n=args.recent_n,
            lookback_pairs=args.lookback_pairs,
        )

    # `sig` ya viene calculado (con reacts, flags de histéresis y guard de
    # expiración si estamos en modo incremental) usando AMBOS canales (up y
    # down) tal como hace Pine — no lo recalculamos acá.
    #
    # SHOW_BOTH_DIRECTIONS=True (default, igual que TradingView): se dejan
    # `up_ch` y `down_ch` tal cual, para que se dibujen los DOS en simultáneo
    # (el ascendente y el descendente), como hace Pine cuando showUpInput y
    # showDownInput están ambos activados.
    #
    # SHOW_BOTH_DIRECTIONS=False (modo "Auto" viejo): se colapsan a uno solo,
    # el de mejor calidad, descartando el otro.
    if not args.show_both_directions:
        long_ch = select_best_channel(up_ch, down_ch)
        if long_ch is not None and long_ch.direction == "up":
            up_ch, down_ch = long_ch, None
        elif long_ch is not None:
            up_ch, down_ch = None, long_ch
        else:
            up_ch, down_ch = None, None

    kalman_result = None
    if args.kalman:
        kalman_lookback = args.kalman_lookback or None
        kalman_result = compute_kalman_windowed(
            data,
            lookback=kalman_lookback,
            sensitivity=args.kalman_sensitivity,
            mad_multp=args.kalman_mad_multp,
            mad_multn=args.kalman_mad_multn,
            vol_len=args.kalman_vol_len,
        )
        sig = combine_with_kalman(sig, kalman_result, len(data) - 1)

    extra = f" | {sig['breakout']}" if sig["breakout"] else (f" | {sig['react']}" if sig.get("react") else "")
    kalman_extra = ""
    if args.kalman:
        kalman_extra = f" | Kalman: {sig['kalman_trend']}"
        if sig.get("kalman_flip"):
            kalman_extra += f" | {sig['kalman_flip']}"

    if args.show_both_directions:
        long_parts = []
        if up_ch is not None:
            long_parts.append(f"↑{up_ch.quality:.0%}")
        if down_ch is not None:
            long_parts.append(f"↓{down_ch.quality:.0%}")
        long_txt = " / ".join(long_parts) if long_parts else "—"
    else:
        long_ch = up_ch or down_ch
        long_txt = f"{'↑' if long_ch and long_ch.direction == 'up' else '↓'}{long_ch.quality:.0%}" if long_ch else "—"
    status_parts = [f"Canal largo: {long_txt}"]
    extra_channels = []

    # ── Canal mediano (modo 'Auto': un solo canal, el de mejor calidad) ──
    if args.show_medium_channel:
        if args.auto_pivot:
            _cands_m = _pivot_len_candidates(
                (args.medium_pivot_len_min, args.medium_pivot_len_max, args.medium_pivot_len_step))
            _base_kw_m = dict(atr_len=args.medium_atr_len, min_channel_bars=args.medium_min_bars,
                               max_channel_bars=args.medium_max_bars, quality_th=args.medium_quality,
                               recent_n=args.medium_recent_n, lookback_pairs=args.medium_lookback_pairs)
            if args.incremental:
                _base_kw_m["replace_ratio"] = args.medium_replace_ratio
            _best_len_m, _best_score_m, _ = search_best_pivot_len(
                data, _cands_m, args.incremental, _base_kw_m,
                lookback=(args.medium_lookback or None), both_directions=args.show_both_directions)
            if _best_len_m is not None:
                print(f"Auto-pivot (canal mediano): pivot_len={_best_len_m} elegido entre "
                      f"{_cands_m[0]}-{_cands_m[-1]} (paso {args.medium_pivot_len_step}) — "
                      f"calidad={_best_score_m:.0%}")
                args.medium_pivot_len = _best_len_m

        if args.incremental:
            up_ch_m, dn_ch_m, _, _ = simulate_incremental(
                data,
                pivot_len=args.medium_pivot_len,
                atr_len=args.medium_atr_len,
                min_channel_bars=args.medium_min_bars,
                max_channel_bars=args.medium_max_bars,
                quality_th=args.medium_quality,
                recent_n=args.medium_recent_n,
                lookback_pairs=args.medium_lookback_pairs,
                replace_ratio=args.medium_replace_ratio,
                progress=False,
                lookback=(args.medium_lookback or None),
            )
        else:
            up_ch_m, dn_ch_m, _, _ = run_auto_channels(
                data,
                pivot_len=args.medium_pivot_len,
                atr_len=args.medium_atr_len,
                min_channel_bars=args.medium_min_bars,
                max_channel_bars=args.medium_max_bars,
                quality_th=args.medium_quality,
                recent_n=args.medium_recent_n,
                lookback_pairs=args.medium_lookback_pairs,
            )
        if args.show_both_directions:
            if up_ch_m is not None:
                extra_channels.append((up_ch_m, "#42A5F5", "Canal mediano ascendente"))
            if dn_ch_m is not None:
                extra_channels.append((dn_ch_m, "#FFEE58", "Canal mediano descendente"))
            m_parts = []
            if up_ch_m is not None:
                m_parts.append(f"↑{up_ch_m.quality:.0%}")
            if dn_ch_m is not None:
                m_parts.append(f"↓{dn_ch_m.quality:.0%}")
            m_txt = " / ".join(m_parts) if m_parts else "—"
        else:
            medium_ch = select_best_channel(up_ch_m, dn_ch_m)
            if medium_ch is not None:
                m_color = "#42A5F5" if medium_ch.direction == "up" else "#FFEE58"
                m_label = f"Canal mediano {'ascendente' if medium_ch.direction == 'up' else 'descendente'}"
                extra_channels.append((medium_ch, m_color, m_label))
            m_txt = f"{'↑' if medium_ch and medium_ch.direction == 'up' else '↓'}{medium_ch.quality:.0%}" if medium_ch else "—"
        status_parts.append(f"Mediano: {m_txt}")

    # ── Canal corto (modo 'Auto': un solo canal, el de mejor calidad) ──
    if args.show_short_channel:
        if args.auto_pivot:
            _cands_s = _pivot_len_candidates(
                (args.short_pivot_len_min, args.short_pivot_len_max, args.short_pivot_len_step))
            _base_kw_s = dict(atr_len=args.short_atr_len, min_channel_bars=args.short_min_bars,
                               max_channel_bars=args.short_max_bars, quality_th=args.short_quality,
                               recent_n=args.short_recent_n, lookback_pairs=args.short_lookback_pairs)
            if args.incremental:
                _base_kw_s["replace_ratio"] = args.short_replace_ratio
            _best_len_s, _best_score_s, _ = search_best_pivot_len(
                data, _cands_s, args.incremental, _base_kw_s,
                lookback=(args.short_lookback or None), both_directions=args.show_both_directions)
            if _best_len_s is not None:
                print(f"Auto-pivot (canal corto): pivot_len={_best_len_s} elegido entre "
                      f"{_cands_s[0]}-{_cands_s[-1]} (paso {args.short_pivot_len_step}) — "
                      f"calidad={_best_score_s:.0%}")
                args.short_pivot_len = _best_len_s

        if args.incremental:
            up_ch_s, dn_ch_s, _, _ = simulate_incremental(
                data,
                pivot_len=args.short_pivot_len,
                atr_len=args.short_atr_len,
                min_channel_bars=args.short_min_bars,
                max_channel_bars=args.short_max_bars,
                quality_th=args.short_quality,
                recent_n=args.short_recent_n,
                lookback_pairs=args.short_lookback_pairs,
                replace_ratio=args.short_replace_ratio,
                progress=False,
                lookback=(args.short_lookback or None),
            )
        else:
            up_ch_s, dn_ch_s, _, _ = run_auto_channels(
                data,
                pivot_len=args.short_pivot_len,
                atr_len=args.short_atr_len,
                min_channel_bars=args.short_min_bars,
                max_channel_bars=args.short_max_bars,
                quality_th=args.short_quality,
                recent_n=args.short_recent_n,
                lookback_pairs=args.short_lookback_pairs,
            )
        if args.show_both_directions:
            if up_ch_s is not None:
                extra_channels.append((up_ch_s, "#FFA726", "Canal corto ascendente"))
            if dn_ch_s is not None:
                extra_channels.append((dn_ch_s, "#AB47BC", "Canal corto descendente"))
            s_parts = []
            if up_ch_s is not None:
                s_parts.append(f"↑{up_ch_s.quality:.0%}")
            if dn_ch_s is not None:
                s_parts.append(f"↓{dn_ch_s.quality:.0%}")
            s_txt = " / ".join(s_parts) if s_parts else "—"
        else:
            short_ch = select_best_channel(up_ch_s, dn_ch_s)
            if short_ch is not None:
                s_color = "#FFA726" if short_ch.direction == "up" else "#AB47BC"
                s_label = f"Canal corto {'ascendente' if short_ch.direction == 'up' else 'descendente'}"
                extra_channels.append((short_ch, s_color, s_label))
            s_txt = f"{'↑' if short_ch and short_ch.direction == 'up' else '↓'}{short_ch.quality:.0%}" if short_ch else "—"
        status_parts.append(f"Corto: {s_txt}")

    status_line = " | ".join(status_parts)
    print(f"{len(data)} velas | {status_line} | "
          f"Señal: {sig.get('confirmed_signal', sig['signal'])}{extra}{kalman_extra}")

    plot_last = None if args.plot_last == 0 else args.plot_last

    # ── Smart Money Flow: se calcula ANTES de graficar, para poder combinarlo
    #    con el gráfico de canales en una sola imagen si está activo. ──
    smf_res = None
    if args.smart_money_flow:
        if smf is None:
            print("AVISO: no se encontró smart_money_flow.py junto a auto_channels.py — se omite el panel.")
        else:
            try:
                smf_res = smf.smart_money_flow(
                    data,
                    momentum_channel_period=args.smf_momentum_period,
                    trend_period=args.smf_trend_period,
                    mfi_period=args.smf_mfi_period,
                    signal_smoothing=args.smf_signal_smoothing,
                    pivot_left_bars=args.smf_pivot_left,
                    pivot_right_bars=args.smf_pivot_right,
                    pivot_sensitivity=args.smf_sensitivity,
                    early_sensitivity=args.smf_early_sensitivity,
                    calibrate_percentile=args.smf_calibrate_percentile,
                )
                smf_summary = smf.summarize_last_bar(smf_res)
                extra_signal = f" | {smf_summary['signal']}" if smf_summary["signal"] else ""
                print(f"Smart Money Flow: wave={smf_summary['composite']:.1f} "
                      f"(sensibilidad calibrada: pivot={smf_summary['pivot_sensitivity']:.1f}, "
                      f"early={smf_summary['early_sensitivity']:.1f}){extra_signal}")
            except ValueError as e:
                print(f"AVISO: no se pudo calcular Smart Money Flow — {e}")

    if smf_res is not None:
        # Una sola imagen: canales arriba, Smart Money Flow abajo.
        plot_combined(data, up_ch, down_ch, sig, pivots, smf_res, args.out,
                      extend_bars=args.extend_bars,
                      show_pivots=not args.hide_pivots, plot_last=plot_last,
                      fig_width=args.width, fig_height=args.height,
                      extra_channels=extra_channels,
                      kalman=kalman_result, show_kalman=(args.kalman and not args.hide_kalman_plot),
                      on_main_chart=args.smf_on_main_chart)
    else:
        plot_result(data, up_ch, down_ch, sig, pivots, args.out,
                    extend_bars=args.extend_bars,
                    show_pivots=not args.hide_pivots, plot_last=plot_last,
                    fig_width=args.width, fig_height=args.height,
                    extra_channels=extra_channels,
                    kalman=kalman_result, show_kalman=(args.kalman and not args.hide_kalman_plot))