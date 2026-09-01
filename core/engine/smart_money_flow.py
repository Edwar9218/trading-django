"""
smart_money_flow.py
====================
Port a Python de "Smart Money Flow Signals [QuantAlgo] - Pivot Turns" (Pine v5).

Combina 3 osciladores clásicos en una sola "composite wave":
  - Momentum (tipo CCI, ponderado por volumen relativo)
  - Chaikin Money Flow (CMF)
  - Money Flow Index (MFI)

Y detecta pivotes SOBRE ese oscilador (no sobre el precio), clasificados en:
  - Confirmados (pico/valle con "fuerza total" — recorrido antes + después —
    por encima de un umbral de sensibilidad)
  - Tempranos ("early") — versión adelantada, menos confirmación, más rápida

DIFERENCIA CLAVE vs. el Pine original: los umbrales `pivot_sensitivity` (14) y
`early_sensitivity` (8) en Pine fueron calibrados por el autor contra SU feed
de datos (probablemente TradingView). El volumen de forex es tick volume, y
el tick volume de tu bróker en MT5 NO es el mismo que el de TradingView (cada
uno cuenta ticks de su propio feed) — así que copiar esos números fijos no
tiene sentido acá. En vez de eso, `calibrate_thresholds()` calcula los
umbrales como un percentil de la distribución real de "fuerza" de pivotes en
TU historial, para que se auto-ajusten a la escala de tick volume de tu
bróker/símbolo/timeframe.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional


# ══════════════════════════════════════════════════════════
# 1. PIVOTES ASIMÉTRICOS (left/right pueden ser distintos)
# ══════════════════════════════════════════════════════════

def _find_pivots_lr(series: pd.Series, left: int, right: int, kind: str) -> pd.Series:
    """
    Igual que find_pivots() de auto_channels.py, pero soporta left != right
    (ta.pivothigh/pivotlow en Pine aceptan leftbars y rightbars distintos).
    """
    n = len(series)
    result = pd.Series(np.nan, index=series.index)
    w = left + right + 1
    if n - w + 1 <= 0:
        return result

    values = series.to_numpy()
    windows = np.lib.stride_tricks.sliding_window_view(values, w)
    centers = values[left:n - right]

    extreme = windows.max(axis=1) if kind == "high" else windows.min(axis=1)
    is_extreme = centers == extreme
    count_eq = (windows == centers[:, None]).sum(axis=1)
    is_unique = count_eq == 1

    result.iloc[left:n - right] = np.where(is_extreme & is_unique, centers, np.nan)
    return result


# ══════════════════════════════════════════════════════════
# 2. CÁLCULO DEL OSCILADOR (vectorizado)
# ══════════════════════════════════════════════════════════

@dataclass
class SMFResult:
    composite: pd.Series
    smoothed: pd.Series
    money_flow_index: pd.Series
    chaikin_money_flow: pd.Series
    momentum_wave: pd.Series
    peak_value: pd.Series          # NaN salvo en bar del pivote (valor del pico)
    trough_value: pd.Series
    peak_total_strength: pd.Series
    trough_total_strength: pd.Series
    confirmed_positive_peak: pd.Series
    confirmed_negative_peak: pd.Series
    confirmed_negative_trough: pd.Series
    confirmed_positive_trough: pd.Series
    early_peak_event: pd.Series
    early_trough_event: pd.Series
    pivot_sensitivity_used: float
    early_sensitivity_used: float


def smart_money_flow(df: pd.DataFrame,
                      momentum_channel_period: int = 10,
                      trend_period: int = 21,
                      mfi_period: int = 14,
                      signal_smoothing: int = 4,
                      pivot_left_bars: int = 3,
                      pivot_right_bars: int = 3,
                      pivot_sensitivity: Optional[float] = None,
                      early_lookback: int = 4,
                      early_reversal_bars: int = 1,
                      early_sensitivity: Optional[float] = None,
                      calibrate_percentile: float = 80.0) -> SMFResult:
    """
    `pivot_sensitivity`/`early_sensitivity` en None (default) = se
    auto-calibran contra el propio historial de `df` (ver calibrate_thresholds).
    Si los pasás explícitos, se usan tal cual (para replicar el Pine 1:1 con
    los valores 14/8 originales, por ejemplo).
    """
    if "volume" not in df.columns or df["volume"].isna().all():
        raise ValueError(
            "Este indicador necesita la columna 'volume' (tick_volume de tu bróker). "
            "Volvé a exportar el CSV con mt5_export_h4.py (ya incluye Volume) o "
            "agregala manualmente a tu CSV."
        )

    high, low, close = df["high"], df["low"], df["close"]
    volume = df["volume"].fillna(0.0)
    source = (high + low + close) / 3.0

    # ── Money Flow Index ──
    raw_money_flow = source * volume
    positive_flow = np.where(source >= source.shift(1), raw_money_flow, 0.0)
    negative_flow = np.where(source < source.shift(1), raw_money_flow, 0.0)
    positive_money_flow = pd.Series(positive_flow, index=df.index).rolling(mfi_period).sum()
    negative_money_flow = pd.Series(negative_flow, index=df.index).rolling(mfi_period).sum()
    money_flow_index = np.where(
        negative_money_flow != 0,
        100 - 100 / (1 + positive_money_flow / negative_money_flow.replace(0, np.nan)),
        100.0,
    )
    money_flow_index = pd.Series(money_flow_index, index=df.index).fillna(100.0)

    # ── Chaikin Money Flow ──
    range_hl = (high - low).replace(0, np.nan)
    money_flow_multiplier = ((close - low - (high - close)) / range_hl).fillna(0.0)
    money_flow_volume = money_flow_multiplier * volume
    volume_sma = volume.rolling(trend_period).mean()
    chaikin_money_flow = (money_flow_volume.rolling(trend_period).mean() /
                           volume_sma.replace(0, np.nan)).fillna(0.0)

    # ── Momentum (CCI-like, ponderado por volumen relativo) ──
    volume_average = volume_sma  # mismo trend_period, igual que en Pine
    volume_strength = (volume / volume_average.replace(0, np.nan)).fillna(1.0)
    volume_weight = np.log(volume_strength + 1)

    ema_src = source.ewm(span=momentum_channel_period, adjust=False).mean()
    deviation = (source - ema_src).abs().ewm(span=momentum_channel_period, adjust=False).mean()
    channel_index = ((source - ema_src) / (0.015 * deviation.replace(0, np.nan)) *
                      (1 + volume_weight * 0.5)).fillna(0.0)
    momentum_wave = channel_index.ewm(span=trend_period, adjust=False).mean()

    # ── Composite wave ──
    money_flow_wave = (money_flow_index - 50) * 1.2
    chaikin_flow_wave = chaikin_money_flow * 100
    composite_wave = momentum_wave * 0.5 + chaikin_flow_wave * 0.3 + money_flow_wave * 0.2
    smoothed_wave = composite_wave.rolling(signal_smoothing).mean()

    # ── Pivotes confirmados (sobre composite_wave) ──
    peak_value = _find_pivots_lr(composite_wave, pivot_left_bars, pivot_right_bars, "high")
    trough_value = _find_pivots_lr(composite_wave, pivot_left_bars, pivot_right_bars, "low")

    cw = composite_wave.to_numpy()
    n = len(cw)
    peak_total_strength = pd.Series(np.nan, index=df.index)
    trough_total_strength = pd.Series(np.nan, index=df.index)

    peak_idx = np.where(~peak_value.isna().to_numpy())[0]
    for p in peak_idx:
        lo = max(0, p - pivot_left_bars)
        before = cw[p] - cw[lo:p + 1].min()
        hi = min(n, p + pivot_right_bars + 1)
        after = cw[p] - cw[p:hi].min()
        peak_total_strength.iloc[p] = before + after

    trough_idx = np.where(~trough_value.isna().to_numpy())[0]
    for p in trough_idx:
        lo = max(0, p - pivot_left_bars)
        before = cw[lo:p + 1].max() - cw[p]
        hi = min(n, p + pivot_right_bars + 1)
        after = cw[p:hi].max() - cw[p]
        trough_total_strength.iloc[p] = before + after

    # ── Calibración automática de sensibilidad ──
    if pivot_sensitivity is None:
        all_strengths = pd.concat([peak_total_strength, trough_total_strength]).dropna()
        pivot_sensitivity = (float(np.percentile(all_strengths, calibrate_percentile))
                              if len(all_strengths) else 14.0)
    if early_sensitivity is None:
        # Un poco más laxo que el confirmado, para que "early" dispare antes.
        early_sensitivity = pivot_sensitivity * (8.0 / 14.0)

    confirmed_positive_peak = (~peak_value.isna()) & (peak_value > 0) & (peak_total_strength >= pivot_sensitivity)
    confirmed_negative_peak = (~peak_value.isna()) & (peak_value < 0) & (peak_total_strength >= pivot_sensitivity)
    confirmed_negative_trough = (~trough_value.isna()) & (trough_value < 0) & (trough_total_strength >= pivot_sensitivity)
    confirmed_positive_trough = (~trough_value.isna()) & (trough_value > 0) & (trough_total_strength >= pivot_sensitivity)

    # ── Pivotes tempranos (vectorizado) ──
    N = early_lookback + early_reversal_bars
    roll_max = composite_wave.rolling(N).max()
    roll_min = composite_wave.rolling(N).min()
    shifted = composite_wave.shift(early_reversal_bars)

    is_recent_local_high = shifted == roll_max
    is_recent_local_low = shifted == roll_min
    early_peak_drop = shifted - composite_wave
    early_trough_bounce = composite_wave - shifted

    early_peak_event = (is_recent_local_high & (composite_wave < shifted) &
                         (early_peak_drop >= early_sensitivity)).fillna(False)
    early_trough_event = (is_recent_local_low & (composite_wave > shifted) &
                           (early_trough_bounce >= early_sensitivity)).fillna(False)

    return SMFResult(
        composite=composite_wave, smoothed=smoothed_wave,
        money_flow_index=money_flow_index, chaikin_money_flow=chaikin_money_flow,
        momentum_wave=momentum_wave,
        peak_value=peak_value, trough_value=trough_value,
        peak_total_strength=peak_total_strength, trough_total_strength=trough_total_strength,
        confirmed_positive_peak=confirmed_positive_peak,
        confirmed_negative_peak=confirmed_negative_peak,
        confirmed_negative_trough=confirmed_negative_trough,
        confirmed_positive_trough=confirmed_positive_trough,
        early_peak_event=early_peak_event, early_trough_event=early_trough_event,
        pivot_sensitivity_used=pivot_sensitivity, early_sensitivity_used=early_sensitivity,
    )


def summarize_last_bar(res: SMFResult) -> dict:
    """Señal 'de consola' equivalente a lo que Pine mostraría en la última vela."""
    last = -1
    value = float(res.composite.iloc[last])
    smooth = float(res.smoothed.iloc[last]) if not np.isnan(res.smoothed.iloc[last]) else None

    # Buscamos si en las últimas `pivot_right_bars` velas se confirmó algo
    # (el pivote se confirma 'right' velas después de ocurrir).
    recent = slice(-10, None)
    label = None
    if res.confirmed_negative_trough.iloc[recent].any():
        label = "Posible agotamiento bajista (rebote alcista)"
    elif res.confirmed_positive_peak.iloc[recent].any():
        label = "Posible agotamiento alcista (giro bajista)"
    elif res.early_trough_event.iloc[recent].any():
        label = "Early: posible giro alcista"
    elif res.early_peak_event.iloc[recent].any():
        label = "Early: posible giro bajista"

    return {
        "composite": value,
        "smoothed": smooth,
        "signal": label,
        "pivot_sensitivity": res.pivot_sensitivity_used,
        "early_sensitivity": res.early_sensitivity_used,
    }


# ══════════════════════════════════════════════════════════
# 3. GRÁFICO (panel aparte, mismo estilo oscuro que auto_channels.py)
# ══════════════════════════════════════════════════════════

def plot_smart_money_flow(df: pd.DataFrame, res: SMFResult, out_path: Optional[str] = None,
                           plot_last: Optional[int] = 300,
                           overbought_level: float = 60.0, oversold_level: float = -60.0,
                           bull_color: str = "#00E5A0", bear_color: str = "#FF5252",
                           fig_width: float = 20, fig_height: float = 4.5, dark: bool = True,
                           ax=None, show_xlabels: bool = True, extend_bars: int = 0,
                           show_title: bool = True, show_grid: bool = True,
                           legend_loc: str = "upper left", bar_alpha: float = 0.55):
    """Dibuja el panel del oscilador, con el mismo look que el gráfico principal.

    ax: si se pasa un Axes ya existente, se dibuja ahí en vez de crear una
        figura nueva (para combinarlo con el gráfico de canales en una sola
        imagen vía auto_channels.plot_combined()). En ese caso no se guarda
        el archivo acá — lo hace el que llama.
    show_xlabels: si es True, dibuja las fechas del eje X (formato DD/MM/AAAA)
        usando df['datetime']; en una figura combinada normalmente esto va
        solo en el panel de ABAJO.
    show_title / show_grid: ponelos en False cuando este panel se dibuja
        superpuesto (twinx) sobre OTRO gráfico ya existente — por ejemplo el
        gráfico principal de canales en modo "SMF sobre gráfico principal" —
        para no duplicar título ni grilla encima del otro panel.
    legend_loc: dónde poner la leyenda de este panel. Si se usa como overlay
        y el panel principal ya tiene leyenda en "upper left", conviene pasar
        "upper right" acá para que no se pisen.
    bar_alpha: opacidad de las barras del composite wave. En modo overlay
        conviene bajarla (ej. 0.35) para que no tape tanto las velas.
    """
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    bg = "#131722" if dark else "#FFFFFF"
    grid = "#2A2E39" if dark else "#E0E0E0"
    txt = "#D1D4DC" if dark else "#1A1A1A"

    n = len(df)
    last_bar = n - 1
    x_lo = max(0, last_bar - plot_last) if plot_last is not None else 0

    x = np.arange(x_lo, n)
    wave = res.composite.to_numpy()[x_lo:]
    smooth = res.smoothed.to_numpy()[x_lo:]

    standalone = ax is None
    if standalone:
        fig, ax = plt.subplots(figsize=(fig_width, fig_height))
        fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    if show_grid:
        ax.grid(color=grid, linewidth=0.5, alpha=0.5)

    colors = np.where(wave > 0, bull_color, bear_color)
    ax.bar(x, wave, color=colors, width=0.8, alpha=bar_alpha, linewidth=0)
    ax.plot(x, smooth, color="#FFFFFF", linewidth=1.2, label="Smoothed Trend")
    ax.axhline(0, color="#787B86", linewidth=1)
    ax.axhline(overbought_level, color="#787B86", linewidth=0.8, linestyle="--")
    ax.axhline(oversold_level, color="#787B86", linewidth=0.8, linestyle="--")

    # ── Marcadores de pivotes confirmados/tempranos ──
    def _mark(mask, color, marker, y_offset, label):
        idx = np.where(mask.to_numpy()[x_lo:])[0] + x_lo
        if len(idx):
            ax.scatter(idx, wave[idx - x_lo] + y_offset, color=color, marker=marker,
                       s=45, zorder=5, label=label, edgecolors="none")

    span = max(abs(np.nanmax(wave)) if len(wave) else 1, abs(np.nanmin(wave)) if len(wave) else 1, 1)
    off = span * 0.08
    _mark(res.confirmed_negative_trough, bull_color, "^", -off, "Trough confirmado (agot. bajista)")
    _mark(res.confirmed_positive_peak, bear_color, "v", off, "Peak confirmado (agot. alcista)")
    _mark(res.early_trough_event, "#00BCD4", "o", -off * 0.6, "Early trough")
    _mark(res.early_peak_event, "#FF9800", "o", off * 0.6, "Early peak")

    if standalone:
        ax.set_xlim(x_lo - 1, last_bar + 1)
    else:
        # IMPORTANTE: aunque ax1 y ax2 comparten eje X (sharex=True en
        # plot_combined), matplotlib re-autoescala cada Axes según SUS
        # PROPIOS artistas al momento de dibujar/guardar si no se fija un
        # xlim explícito en ÉL — no alcanza con fijarlo solo en ax1. Sin
        # este set_xlim acá, el autoscale de este panel (cuyos datos solo
        # llegan hasta la última vela) termina "recortando" el margen de
        # extend_bars que sí se fijó arriba, en el panel de precio.
        ax.set_xlim(x_lo - 1, last_bar + extend_bars + 1)
    ax.tick_params(colors=txt, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(grid)
    if show_title:
        ax.set_title("Smart Money Flow — composite wave", color=txt, fontsize=11)
    legend = ax.legend(loc=legend_loc, fontsize=7, facecolor=bg, edgecolor=grid, labelcolor=txt)

    if show_xlabels:
        n_bars = len(df)
        if n_bars >= 2:
            avg_delta = (df["datetime"].iloc[-1] - df["datetime"].iloc[0]) / (n_bars - 1)
        else:
            avg_delta = pd.Timedelta(days=1)
        last_dt = df["datetime"].iloc[-1]

        def bar_to_date(x_, pos=None):
            idx = int(round(x_))
            if idx < 0:
                idx = 0
            if idx < n_bars:
                dt = df["datetime"].iloc[idx]
            else:
                dt = last_dt + avg_delta * (idx - (n_bars - 1))
            return dt.strftime("%d/%m/%Y")

        ax.xaxis.set_major_locator(mticker.MaxNLocator(nbins=10, integer=True))
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(bar_to_date))
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        ax.set_xlabel("Fecha", color=txt)
    else:
        ax.tick_params(labelbottom=False)

    if standalone:
        fig.tight_layout()
        fig.savefig(out_path, facecolor=bg, dpi=130)
        plt.close(fig)