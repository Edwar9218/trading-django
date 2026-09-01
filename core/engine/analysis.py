"""
core/engine/analysis.py — lógica de negocio pura (framework-agnostic)
============================================================================
Port de servidor.py (el backend Flask original) a un módulo reutilizable
por Django: NADA de esto depende de Flask ni de Django — son funciones
Python puro (pandas/numpy) que reciben parámetros y devuelven dict/objetos
listos para serializar. Las vistas de Django (dashboard/views.py,
chartview/views.py) importan estas funciones y las envuelven en
JsonResponse.

IMPORTANTE: no reimplementa ni modifica ninguna fórmula. Importa
auto_channels.py, smart_money_flow.py y channel_breakout_status.py tal
cual estaban y llama a las mismas funciones que ya usaba el proyecto
original (simulate_incremental, run_auto_channels, compute_kalman_windowed,
smart_money_flow, evaluar_todos_spn, etc.).
"""

import os
import sys
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from . import auto_channels as ac        # noqa: E402  (lógica de canales + Kalman, SIN CAMBIOS)
try:
    from . import smart_money_flow as smf   # noqa: E402
except ImportError:
    smf = None

try:
    from . import channel_breakout_status as cbs   # noqa: E402
except ImportError:
    cbs = None

try:
    import MetaTrader5 as mt5      # noqa: E402  (solo funciona en Windows con el terminal MT5 abierto)
except ImportError:
    mt5 = None

# NOTA: NO importamos mt5_export.py como módulo, porque ese script hace
# sys.exit(1) al importarse si el paquete MetaTrader5 no está instalado
# (efecto colateral pensado para cuando se corre como script, no como
# librería). En cambio copiamos acá sus mismas dos tablas de mapeo
# (idénticas, cero cambio de comportamiento) para no depender de ese
# efecto colateral y poder caer a datos demo con gracia si algo falta.
_TF_MAP = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1", "H2": "TIMEFRAME_H2", "H3": "TIMEFRAME_H3", "H4": "TIMEFRAME_H4",
    "H6": "TIMEFRAME_H6", "H8": "TIMEFRAME_H8", "H12": "TIMEFRAME_H12",
    "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1", "MN1": "TIMEFRAME_MN1",
}
_BARS_PER_DAY = {
    "M1": 1440, "M5": 288, "M15": 96, "M30": 48,
    "H1": 24, "H2": 12, "H3": 8, "H4": 6, "H6": 4, "H8": 3, "H12": 2,
    "D1": 1, "W1": 1 / 7, "MN1": 1 / 30,
}

from . import trading_defaults   # noqa: E402  (defaults de canales/Kalman/SMF, ex config.py)

PORT = 5051


class Mt5Unavailable(Exception):
    """MT5 no está instalado, no está abierto, o no se pudo conectar."""


class Mt5DataError(Exception):
    """MT5 está conectado pero no devolvió datos válidos para el pedido."""


def cfg(name, fallback):
    return getattr(trading_defaults, name, fallback)


# ══════════════════════════════════════════════════════════
# Helpers de tiempo (bar_index -> timestamp unix)
# ══════════════════════════════════════════════════════════

def make_bar_to_ts(df: pd.DataFrame):
    """
    Equivalente exacto a la función bar_to_date() que auto_channels.py usa
    dentro de plot_result()/plot_smart_money_flow() para poner fechas en el
    eje X — incluidas las barras "proyectadas" más allá de la última vela
    real (donde se extienden los canales). Acá se devuelve timestamp unix
    (segundos) en vez de un string, para que el frontend pueda ubicar esas
    barras proyectadas en el eje de tiempo del gráfico.
    """
    n_bars = len(df)
    if n_bars >= 2:
        avg_delta = (df["datetime"].iloc[-1] - df["datetime"].iloc[0]) / (n_bars - 1)
    else:
        avg_delta = pd.Timedelta(days=1)
    last_dt = df["datetime"].iloc[-1]

    def bar_to_ts(idx):
        idx = int(round(idx))
        if idx < 0:
            idx = 0
        if idx < n_bars:
            dt = df["datetime"].iloc[idx]
        else:
            dt = last_dt + avg_delta * (idx - (n_bars - 1))
        return int(dt.timestamp())

    return bar_to_ts


# ══════════════════════════════════════════════════════════
# Serialización de un Channel a JSON (misma matemática que draw_channel())
# ══════════════════════════════════════════════════════════

def channel_payload(ch, color, label, bar_to_ts, x_lo, x_end, last_bar, linewidth=2):
    """
    IMPORTANTE: se manda UN PUNTO POR CADA BARRA REAL (no solo el par de
    puntos inicio/fin), porque lightweight-charts NO espacia su eje de
    tiempo de forma proporcional al calendario para velas diarias — usa
    espaciado por índice (salta fines de semana). Si le mandás una línea
    de 2 puntos con marcas de tiempo "reales" (con huecos de fin de
    semana), la recta que traza en PANTALLA entre esos 2 puntos NO cae
    exactamente sobre el mismo trazado que ch.base_at() calcula puramente
    en espacio de índice de barra — el segundo pivote queda "tocado" por
    el punto pero la línea que pasa por ahí se desvía un poco.

    Mandando un punto por cada índice de barra real (con la MISMA marca de
    tiempo exacta que usa la vela en esa posición), la línea queda
    perfectamente alineada sin importar cómo espacie el eje la librería,
    porque comparte exactamente los mismos timestamps que las velas.

    NO se dibuja el tramo proyectado a futuro (más allá de la última vela
    real): ese tramo usa un espaciado promedio (avg_delta) distinto al
    espaciado real con huecos de fin de semana del tramo histórico, y la
    unión entre ambos espaciados se ve como un quiebre/deformación justo
    en la vela actual — además de tapar las velas más recientes, que son
    las que más importa poder ver. La línea termina limpia en la última
    vela real.
    """
    if ch is None:
        return None
    x_start = max(ch.x1, x_lo)   # recorta el punto de inicio al tramo visible (como el xlim del PNG)

    base_points = [{"t": bar_to_ts(i), "y": float(ch.base_at(i))} for i in range(x_start, last_bar + 1)]

    par_points = [{"t": p["t"], "y": p["y"] + ch.offset} for p in base_points]
    mid_points = [{"t": p["t"], "y": p["y"] + ch.offset / 2} for p in base_points]

    return {
        "label": label,
        "color": color,
        "direction": ch.direction,
        "quality": round(float(ch.quality), 4),
        "linewidth": linewidth,   # 2 = canal largo, 1.6 = canal mediano/corto (igual que draw_channel() en auto_channels.py)
        "base_points": base_points,
        "par_points": par_points,
        "mid_points": mid_points,
        # Los 2 pivotes REALES que definen la línea base del canal (exactamente
        # el high/low de esas 2 velas) — se mandan aparte para poder marcarlos
        # en el gráfico y verificar a simple vista que la línea sí los toca.
        "pivot1": {"t": bar_to_ts(ch.x1), "y": float(ch.y1)},
        "pivot2": {"t": bar_to_ts(ch.x2), "y": float(ch.y2)},
    }


# ══════════════════════════════════════════════════════════
# Serialización del Kalman Flow (segmentos coloreados por tendencia,
# igual que la LineCollection de trend_colors() en plot_result())
# ══════════════════════════════════════════════════════════

def kalman_payload(kres, x_lo, last_bar, bar_to_ts, up_color, down_color, neutral_color="#787B86"):
    trend = kres.trend.to_numpy()
    level = kres.level.to_numpy()

    segments = []
    cur_color, cur_pts = None, []
    for i in range(x_lo, last_bar + 1):
        t = int(trend[i])
        color = up_color if t == 1 else (down_color if t == -1 else neutral_color)
        if color != cur_color:
            if cur_pts:
                segments.append({"color": cur_color, "points": cur_pts})
                cur_pts = [cur_pts[-1]]  # empalma con el segmento anterior (sin huecos)
            cur_color = color
        cur_pts.append({"time": bar_to_ts(i), "value": float(level[i])})
    if cur_pts:
        segments.append({"color": cur_color, "points": cur_pts})

    flips_up, flips_down = [], []
    fu_idx = np.where(kres.flip_up.to_numpy()[x_lo:last_bar + 1])[0] + x_lo
    fd_idx = np.where(kres.flip_down.to_numpy()[x_lo:last_bar + 1])[0] + x_lo
    for i in fu_idx:
        flips_up.append({"time": bar_to_ts(int(i)), "value": float(level[i])})
    for i in fd_idx:
        flips_down.append({"time": bar_to_ts(int(i)), "value": float(level[i])})

    return {"segments": segments, "flips_up": flips_up, "flips_down": flips_down}


# ══════════════════════════════════════════════════════════
# Pipeline principal: MISMA secuencia que el bloque main() de
# auto_channels.py, solo que en vez de argparse usa config.py como
# defaults, con overrides desde el HTML (símbolo, timeframe, fecha).
# ══════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════
# Cuántas velas hacen falta REALMENTE para el análisis en vivo
# ══════════════════════════════════════════════════════════

def live_bars_needed():
    """
    config.py trae un BARS_CAP pensado para exportar TODO el historial con
    mt5_export.py (ej. 20.000 velas en H4). Para el gráfico interactivo eso
    es mucho más de lo necesario: los canales solo miran hacia atrás hasta
    CHANNEL_LOOKBACK/_MED/_SHORT, el Kalman hasta KALMAN_LOOKBACK, y en
    pantalla se muestran PLOT_LAST velas. Pedirle a MT5 muchas más de las
    que este pipeline realmente usa solo hace todo más lento sin cambiar
    el resultado — así que acá calculamos el máximo que hace falta.
    """
    lookbacks = [
        cfg("PLOT_LAST", 900),
        cfg("EXTEND_BARS", 150),
        cfg("CHANNEL_LOOKBACK", 6000) or 0,
        cfg("CHANNEL_LOOKBACK_MED", 4000) or 0,
        cfg("CHANNEL_LOOKBACK_SHORT", 3000) or 0,
        cfg("KALMAN_LOOKBACK", None) or (cfg("PLOT_LAST", 900) * 3),
    ]
    return max(lookbacks) + 500   # margen de seguridad


def fetch_mt5_candles(symbol: str, timeframe: str, hasta=None):
    """
    Descarga velas EN VIVO desde el terminal MT5 abierto en esta PC.
    hasta: fecha límite "YYYY-MM-DD" (del selector de fecha del HTML) o
    None para traer hasta la vela más reciente (en formación) de hoy.

    Misma estrategia EXACTA que mt5_export.py (ventana progresiva x3 hasta
    cubrir bars_cap velas, + parche de la última vela en formación si el
    rango llega a hoy) — duplicada acá porque el main() de mt5_export.py
    no está pensado para importarse como función, pero es la MISMA lógica,
    sin ningún cambio de comportamiento.

    Devuelve (DataFrame [datetime, open, high, low, close, volume], texto de origen).
    """
    if mt5 is None:
        raise Mt5Unavailable("el paquete MetaTrader5 no está instalado en este Python "
                              "(pip install MetaTrader5 — solo funciona en Windows)")

    tf_attr = _TF_MAP.get(timeframe.upper())
    if tf_attr is None:
        raise Mt5DataError(f"timeframe '{timeframe}' no reconocido. "
                            f"Usa uno de: {', '.join(_TF_MAP)}")
    mt5_tf = getattr(mt5, tf_attr)

    if not mt5.initialize():
        raise Mt5Unavailable(f"no se pudo conectar a MT5 ({mt5.last_error()}) — "
                              f"¿está el terminal MetaTrader 5 abierto y con sesión iniciada?")

    try:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            raise Mt5DataError(f"el símbolo '{symbol}' no existe en tu Market Watch "
                                f"(revisa el nombre exacto — algunos brókers usan sufijos, "
                                f"ej. EURUSD.m, EURUSDpro).")
        if not symbol_info.visible:
            mt5.symbol_select(symbol, True)
            # Cuando un símbolo se agrega recién a Market Watch, el terminal
            # necesita un instante para sincronizar/descargar su historial.
            # Si se pide copy_rates_range() inmediatamente después, a veces
            # devuelve datos vacíos, parciales, o (más raro) todavía del
            # último símbolo activo. Esperamos hasta que symbol_info_tick()
            # devuelva un precio real para este símbolo (o hasta 3 segundos).
            for _ in range(15):
                tick = mt5.symbol_info_tick(symbol)
                if tick is not None and tick.bid > 0:
                    break
                time.sleep(0.2)

        bars_cap = getattr(trading_defaults, "_BARS_CAP_BY_TF", {}).get(timeframe.upper(), 20000)
        # Nunca pedimos más velas de las que el análisis en vivo realmente
        # necesita (aunque config.py pida un histórico completo enorme para
        # exportación offline) — esto es lo que hace lento el "Cargar".
        bars_cap = min(bars_cap, live_bars_needed())
        bars_per_day = _BARS_PER_DAY.get(timeframe.upper(), 6)

        range_reaches_today = hasta is None
        date_to = (datetime.strptime(hasta, "%Y-%m-%d") + timedelta(days=1)) if hasta \
            else (datetime.now() + timedelta(days=1))

        days_back = max(int(bars_cap * 1.5 / max(bars_per_day, 0.01)), 30)
        max_days_back = 365 * 20
        rates = None
        while True:
            date_from = date_to - timedelta(days=days_back)
            rates_range = mt5.copy_rates_range(symbol, mt5_tf, date_from, date_to)

            if rates_range is not None and len(rates_range) >= bars_cap:
                rates = rates_range[-bars_cap:]
                break
            if days_back >= max_days_back:
                rates = rates_range
                break
            days_back = min(days_back * 3, max_days_back)

        if range_reaches_today and rates is not None and len(rates) > 0:
            latest = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, 1)
            if latest is not None and len(latest) > 0 and latest[-1]["time"] > rates[-1]["time"]:
                rates = np.concatenate([rates, latest])
                if len(rates) > bars_cap:
                    rates = rates[-bars_cap:]

        if rates is None or len(rates) == 0:
            raise Mt5DataError(f"MT5 no devolvió velas para {symbol} {timeframe} "
                                f"(last_error: {mt5.last_error()}).")

        raw = pd.DataFrame(rates)
        raw["datetime"] = pd.to_datetime(raw["time"], unit="s")
        out = pd.DataFrame({
            "datetime": raw["datetime"],
            "open": raw["open"].astype(float),
            "high": raw["high"].astype(float),
            "low": raw["low"].astype(float),
            "close": raw["close"].astype(float),
            "volume": raw["tick_volume"].astype(float),
        }).reset_index(drop=True)

        etiqueta = f"MT5 en vivo — {symbol} {timeframe.upper()} ({len(out)} velas, hasta {out['datetime'].iloc[-1]})"
        return out, etiqueta
    finally:
        mt5.shutdown()


def fetch_mt5_older_candles(symbol: str, timeframe: str, antes_de_ts: int, cantidad: int = 500):
    """
    Trae un lote de velas MÁS ANTIGUAS que 'antes_de_ts' (timestamp unix de
    la vela más vieja que el navegador ya tiene cargada) — para el botón
    "Cargar más historial". A propósito NO recalcula canales/Kalman/SMF:
    esos quedan anclados al presente (última vela real), la paginación
    hacia atrás es solo para poder ver/analizar más velas visualmente.
    Devuelve una lista de dicts {time, open, high, low, close} (puede
    devolver una lista vacía si el bróker no tiene más historial atrás).
    """
    if mt5 is None:
        raise Mt5Unavailable("el paquete MetaTrader5 no está instalado en este Python.")

    tf_attr = _TF_MAP.get(timeframe.upper())
    if tf_attr is None:
        raise Mt5DataError(f"timeframe '{timeframe}' no reconocido.")
    mt5_tf = getattr(mt5, tf_attr)

    if not mt5.initialize():
        raise Mt5Unavailable(f"no se pudo conectar a MT5 ({mt5.last_error()}).")

    try:
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            raise Mt5DataError(f"el símbolo '{symbol}' no existe en tu Market Watch.")
        if not symbol_info.visible:
            mt5.symbol_select(symbol, True)
            for _ in range(15):
                tick = mt5.symbol_info_tick(symbol)
                if tick is not None and tick.bid > 0:
                    break
                time.sleep(0.2)

        date_to = datetime.fromtimestamp(antes_de_ts)   # exclusivo: no repetir la vela que ya tenían
        bars_per_day = _BARS_PER_DAY.get(timeframe.upper(), 6)
        days_back = max(int(cantidad * 1.5 / max(bars_per_day, 0.01)), 5)
        max_days_back = 365 * 20

        rates = None
        while True:
            date_from = date_to - timedelta(days=days_back)
            rates_range = mt5.copy_rates_range(symbol, mt5_tf, date_from, date_to)
            # copy_rates_range puede incluir la vela justo en date_to si coincide
            # exacto con el borde — la filtramos para no duplicar la que el
            # navegador ya tiene.
            if rates_range is not None:
                rates_range = rates_range[rates_range["time"] < antes_de_ts]
            if rates_range is not None and len(rates_range) >= cantidad:
                rates = rates_range[-cantidad:]
                break
            if days_back >= max_days_back:
                rates = rates_range
                break
            days_back = min(days_back * 3, max_days_back)

        if rates is None or len(rates) == 0:
            return []   # el bróker no tiene más historial hacia atrás

        raw = pd.DataFrame(rates)
        return [
            {"time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"])}
            for _, r in raw.iterrows()
        ]
    finally:
        mt5.shutdown()


def _long_channel(data, incremental, auto_pivot, show_both):
    pivot_len = cfg("PIVOT_LEN", 21)
    base_kw = dict(atr_len=cfg("ATR_LEN", 14), min_channel_bars=cfg("MIN_BARS", 10),
                    max_channel_bars=cfg("MAX_BARS", 400), quality_th=cfg("QUALITY", 0.55),
                    recent_n=cfg("RECENT_N", 8), lookback_pairs=cfg("LOOKBACK_PAIRS", 5))
    lookback = cfg("CHANNEL_LOOKBACK", None) or None

    if auto_pivot:
        cands = ac._pivot_len_candidates(cfg("PIVOT_LEN_RANGE", (10, 40, 2)))
        kw = dict(base_kw)
        if incremental:
            kw["replace_ratio"] = cfg("REPLACE_RATIO", 0.7)
        best_len, _, _ = ac.search_best_pivot_len(data, cands, incremental, kw,
                                                    lookback=lookback, both_directions=show_both)
        if best_len is not None:
            pivot_len = best_len

    if incremental:
        up_ch, dn_ch, sig, pivots = ac.simulate_incremental(
            data, pivot_len=pivot_len, replace_ratio=cfg("REPLACE_RATIO", 0.7),
            progress=False, lookback=lookback, **base_kw)
    else:
        up_ch, dn_ch, sig, pivots = ac.run_auto_channels(data, pivot_len=pivot_len, **base_kw)

    if not show_both:
        long_ch = ac.select_best_channel(up_ch, dn_ch)
        if long_ch is not None and long_ch.direction == "up":
            up_ch, dn_ch = long_ch, None
        elif long_ch is not None:
            up_ch, dn_ch = None, long_ch
        else:
            up_ch, dn_ch = None, None

    return up_ch, dn_ch, sig, pivots


def _extra_channel(data, incremental, auto_pivot, show_both, prefix, defaults, colors):
    """
    Reproduce el bloque 'Canal mediano' / 'Canal corto' de main(), que solo
    difieren en el prefijo de las claves de config.py y los colores. `prefix`
    es "MED" o "SHORT"; `defaults` son los fallback de cada clave.
    """
    pivot_len = cfg(f"PIVOT_LEN_{prefix}", defaults["pivot_len"])
    base_kw = dict(
        atr_len=cfg(f"ATR_LEN_{prefix}", defaults["atr_len"]),
        min_channel_bars=cfg(f"MIN_BARS_{prefix}", defaults["min_bars"]),
        max_channel_bars=cfg(f"MAX_BARS_{prefix}", defaults["max_bars"]),
        quality_th=cfg(f"QUALITY_{prefix}", defaults["quality"]),
        recent_n=cfg(f"RECENT_N_{prefix}", defaults["recent_n"]),
        lookback_pairs=cfg(f"LOOKBACK_PAIRS_{prefix}", defaults["lookback_pairs"]),
    )
    lookback = cfg(f"CHANNEL_LOOKBACK_{prefix}", None) or None
    replace_ratio = cfg(f"REPLACE_RATIO_{prefix}", defaults["replace_ratio"])

    if auto_pivot:
        cands = ac._pivot_len_candidates(cfg(f"PIVOT_LEN_{prefix}_RANGE", defaults["range"]))
        kw = dict(base_kw)
        if incremental:
            kw["replace_ratio"] = replace_ratio
        best_len, _, _ = ac.search_best_pivot_len(data, cands, incremental, kw,
                                                    lookback=lookback, both_directions=show_both)
        if best_len is not None:
            pivot_len = best_len

    if incremental:
        up_ch, dn_ch, _, _ = ac.simulate_incremental(
            data, pivot_len=pivot_len, replace_ratio=replace_ratio,
            progress=False, lookback=lookback, **base_kw)
    else:
        up_ch, dn_ch, _, _ = ac.run_auto_channels(data, pivot_len=pivot_len, **base_kw)

    extra = []
    up_color, dn_color = colors
    if show_both:
        if up_ch is not None:
            extra.append((up_ch, up_color, f"{defaults['label']} ascendente"))
        if dn_ch is not None:
            extra.append((dn_ch, dn_color, f"{defaults['label']} descendente"))
    else:
        best = ac.select_best_channel(up_ch, dn_ch)
        if best is not None:
            color = up_color if best.direction == "up" else dn_color
            extra.append((best, color, f"{defaults['label']} {'ascendente' if best.direction == 'up' else 'descendente'}"))
    return extra


MEDIUM_DEFAULTS = dict(pivot_len=18, atr_len=10, min_bars=8, max_bars=100, quality=0.45,
                        recent_n=12, lookback_pairs=8, replace_ratio=0.7,
                        range=(6, 30, 2), label="Canal mediano")
SHORT_DEFAULTS = dict(pivot_len=6, atr_len=8, min_bars=4, max_bars=80, quality=0.5,
                       recent_n=15, lookback_pairs=10, replace_ratio=0.7,
                       range=(3, 15, 1), label="Canal corto")


def compute_analysis(symbol=None, timeframe_override=None, hasta=None, auto_pivot_override=None):
    symbol = (symbol or cfg("SYMBOL", "EURUSD")).strip().upper()
    timeframe = (timeframe_override or cfg("TIMEFRAME", "H4")).upper()

    try:
        data, source = fetch_mt5_candles(symbol, timeframe, hasta)
    except Mt5Unavailable as e:
        # Sin MT5 disponible (ej. probando fuera de Windows): cae a datos
        # demo para que el resto del pipeline se pueda seguir probando.
        data = ac.make_demo_data()
        source = f"DEMO — MT5 no disponible ({e})"
    except Mt5DataError as e:
        return {"error": str(e)}

    if len(data) < 5:
        return {"error": "No hay suficientes velas para ese símbolo/timeframe/fecha."}

    incremental = bool(cfg("INCREMENTAL", True))
    show_both = bool(cfg("SHOW_BOTH_DIRECTIONS", True))
    # auto_pivot_override viene del checkbox "Pivot automático" del HTML
    # (True/False explícito). Si no se manda nada (None), se usa el default
    # de config.py como hasta ahora — así el checkbox no rompe nada para
    # quien no lo toque.
    auto_pivot = bool(cfg("PIVOT_LEN_AUTO", False)) if auto_pivot_override is None else bool(auto_pivot_override)

    up_ch, dn_ch, sig, pivots = _long_channel(data, incremental, auto_pivot, show_both)

    kalman_result = None
    if cfg("KALMAN_ENABLED", True):
        kalman_result = ac.compute_kalman_windowed(
            data, lookback=(cfg("KALMAN_LOOKBACK", None) or None),
            sensitivity=cfg("KALMAN_SENSITIVITY", 4.0),
            mad_multp=cfg("KALMAN_MAD_MULTP", 1.65),
            mad_multn=cfg("KALMAN_MAD_MULTN", 1.0),
            vol_len=cfg("KALMAN_VOL_LEN", 50),
        )
        sig = ac.combine_with_kalman(sig, kalman_result, len(data) - 1)

    channels_extra = []
    if cfg("SHOW_MEDIUM_CHANNEL", False):
        channels_extra += _extra_channel(data, incremental, auto_pivot, show_both, "MED",
                                          MEDIUM_DEFAULTS, ("#42A5F5", "#FFEE58"))
    if cfg("SHOW_SHORT_CHANNEL", False):
        channels_extra += _extra_channel(data, incremental, auto_pivot, show_both, "SHORT",
                                          SHORT_DEFAULTS, ("#FFA726", "#AB47BC"))

    smf_res = None
    if cfg("SMART_MONEY_FLOW_ENABLED", False) and smf is not None:
        try:
            smf_res = smf.smart_money_flow(
                data,
                momentum_channel_period=cfg("SMF_MOMENTUM_PERIOD", 10),
                trend_period=cfg("SMF_TREND_PERIOD", 21),
                mfi_period=cfg("SMF_MFI_PERIOD", 14),
                signal_smoothing=cfg("SMF_SIGNAL_SMOOTHING", 4),
                pivot_left_bars=cfg("SMF_PIVOT_LEFT", 3),
                pivot_right_bars=cfg("SMF_PIVOT_RIGHT", 3),
                pivot_sensitivity=cfg("SMF_PIVOT_SENSITIVITY", None),
                early_sensitivity=cfg("SMF_EARLY_SENSITIVITY", None),
                calibrate_percentile=cfg("SMF_CALIBRATE_PERCENTILE", 80.0),
            )
        except ValueError as e:
            sig["smf_warning"] = str(e)

    # ── Serialización ──
    plot_last = cfg("PLOT_LAST", 900) or None
    extend_bars = cfg("EXTEND_BARS", 150)
    last_bar = len(data) - 1
    x_end = last_bar + extend_bars
    x_lo = max(0, last_bar - plot_last) if plot_last else 0

    # Si algún canal activo tiene su pivote de origen (x1) más atrás que la
    # ventana de PLOT_LAST velas, ampliamos x_lo hacia atrás para que ESE
    # pivote real quede dentro de lo visible — si no, la línea del canal
    # "flota" en pantalla sin que se vea el punto exacto donde toca el
    # high/low real de la vela (aunque matemáticamente sí lo toca, fuera
    # de cámara). No cambia ningún cálculo, solo el recorte de qué mostrar.
    all_channels_raw = [c for c in (up_ch, dn_ch) if c is not None] + \
                        [c for c, _, _ in channels_extra if c is not None]
    if all_channels_raw:
        min_channel_x1 = min(c.x1 for c in all_channels_raw)
        x_lo = min(x_lo, max(0, min_channel_x1))

    # Red de seguridad: x_lo NUNCA puede quedar en un rango que deje 0 velas
    # para mostrar (por más raro/extremo que sea el caso). Si por lo que sea
    # terminara siendo inválido, mostramos igual las últimas velas en vez de
    # devolver un gráfico vacío sin avisar.
    x_lo = max(0, min(x_lo, last_bar))

    bar_to_ts = make_bar_to_ts(data)

    draw = data.iloc[x_lo:]
    if len(draw) == 0:
        x_lo = max(0, last_bar - min(300, last_bar))
        draw = data.iloc[x_lo:]

    candles = [
        {"time": int(row.datetime.timestamp()), "open": float(row.open),
         "high": float(row.high), "low": float(row.low), "close": float(row.close)}
        for row in draw.itertuples()
    ]

    channels_json = []
    for ch, color, label in (
        (up_ch, "#00E676", "Canal largo ascendente"),
        (dn_ch, "#FF5252", "Canal largo descendente"),
    ):
        j = channel_payload(ch, color, label, bar_to_ts, x_lo, x_end, last_bar, linewidth=2)
        if j:
            channels_json.append(j)
    for ch, color, label in channels_extra:
        j = channel_payload(ch, color, label, bar_to_ts, x_lo, x_end, last_bar, linewidth=1.6)
        if j:
            channels_json.append(j)

    kalman_json = None
    if kalman_result is not None and not cfg("HIDE_KALMAN_PLOT", False):
        kalman_json = kalman_payload(kalman_result, x_lo, last_bar, bar_to_ts,
                                      up_color="#30FDCF", down_color="#E117B7")

    pivots_json = []
    if not cfg("HIDE_PIVOTS", True):
        hi_idx, hi_val, lo_idx, lo_val = pivots
        pivots_json = (
            [{"time": bar_to_ts(i), "value": float(v), "type": "high"} for i, v in zip(hi_idx, hi_val) if i >= x_lo] +
            [{"time": bar_to_ts(i), "value": float(v), "type": "low"} for i, v in zip(lo_idx, lo_val) if i >= x_lo]
        )

    smf_json = None
    if smf_res is not None:
        wave = smf_res.composite.to_numpy()[x_lo:last_bar + 1]
        smooth = smf_res.smoothed.to_numpy()[x_lo:last_bar + 1]
        times = [bar_to_ts(i) for i in range(x_lo, last_bar + 1)]
        bars = [{"time": t, "value": float(w) if not np.isnan(w) else 0.0,
                 "color": "#00E5A0" if (not np.isnan(w) and w > 0) else "#FF5252"}
                for t, w in zip(times, wave)]
        line = [{"time": t, "value": float(s)} for t, s in zip(times, smooth) if not np.isnan(s)]
        smf_json = {"bars": bars, "smoothed": line}

    # ── Tablero S-P-N: para cada uno de los 4 canales (largo, largo
    # inverso, corto, corto inverso), estado (Soporte/Resistencia/Neutro),
    # distancia % al borde relevante y si ya rompió. Reutiliza los MISMOS
    # objetos Channel ya calculados arriba. Si "Corto" está desactivado
    # (SHOW_SHORT_CHANNEL=False), esas dos filas simplemente no aparecen. ──
    posicion_json = []
    if cbs is not None:
        short_up = next((c for c, _color, _lbl in channels_extra
                          if _lbl.startswith("Canal corto") and c.direction == "up"), None)
        short_down = next((c for c, _color, _lbl in channels_extra
                            if _lbl.startswith("Canal corto") and c.direction == "down"), None)
        canales_tablero = [
            ("Canal largo", up_ch),
            ("Canal largo inverso", dn_ch),
            ("Canal corto", short_up),
            ("Canal corto inverso", short_down),
        ]
        resultados = cbs.evaluar_todos_spn(
            data, canales_tablero,
            atr_length=cfg("BREAKOUT_ATR_LEN", 14),
            min_displacement_atr=cfg("BREAKOUT_MIN_DISPLACEMENT", 0.15),
            near_threshold_pct=cfg("SPN_NEAR_THRESHOLD_PCT", 30.0),
        )
        for r in resultados:
            posicion_json.append({
                "label": r.label,
                "estado_label": cbs.ESTADO_LABELS.get(r.estado, r.estado),
                "estado_color": cbs.ESTADO_COLORS.get(r.estado, "#848e9c"),
                "distancia_pct": r.distancia_pct,
                "rotura": r.rotura,
                "rotura_label": "Sí" if r.rotura else "No",
                "rotura_color": cbs.ROTURA_COLOR_SI if r.rotura else cbs.ROTURA_COLOR_NO,
                "lado_rotura": r.lado_rotura,
                "quality": r.quality,
                "confirmacion": {
                    "precio_actual": r.precio_actual,
                    "borde_top": r.borde_top,
                    "borde_bottom": r.borde_bottom,
                    "distancia_exacta_pct": r.distancia_exacta_pct,
                    "cuerpo_vela": r.cuerpo_vela,
                    "displacement": r.displacement,
                    "displacement_minimo": r.displacement_minimo,
                    "atr": r.atr,
                },
            })

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "source": source,
        "candles": candles,
        "channels": channels_json,
        "kalman": kalman_json,
        "pivots": pivots_json,
        "smf": smf_json,
        "posicion_canales": posicion_json,
        "signals": dict(sig),
        "n_candles_total": len(data),
        "auto_pivot": auto_pivot,
        "last_date": data["datetime"].iloc[-1].strftime("%Y-%m-%d %H:%M"),
        "first_date": data["datetime"].iloc[0].strftime("%Y-%m-%d %H:%M"),
    }


# ══════════════════════════════════════════════════════════
# Tablero MULTI-TEMPORALIDAD (S-P-N): la misma tabla de 4 canales, pero
# una fila por temporalidad, para las que el usuario elija con checkboxes
# en el front-end. Reutiliza EXACTAMENTE la misma lógica S-P-N que el
# tablero de un solo panel — nada de código nuevo de cálculo, solo se
# repite por cada timeframe pedido. Respeta `hasta` en cada una por
# separado (cada timeframe trae su propio historial recortado a esa
# fecha), igual que el resto del sistema.
# ══════════════════════════════════════════════════════════

ALL_TIMEFRAMES = ["M15", "M30", "H1", "H2", "H3", "H4", "H6", "H8", "H12", "D1", "W1", "MN1"]


def compute_multi_timeframe_spn(symbol=None, hasta=None, timeframes=None, auto_pivot_override=None):
    if cbs is None:
        return {"error": "channel_breakout_status.py no está disponible junto a servidor.py."}

    symbol = (symbol or cfg("SYMBOL", "EURUSD")).strip().upper()
    timeframes = [t for t in (timeframes or ALL_TIMEFRAMES) if t in ALL_TIMEFRAMES]
    if not timeframes:
        return {"error": "No se eligió ninguna temporalidad válida."}

    incremental = bool(cfg("INCREMENTAL", True))
    show_both = bool(cfg("SHOW_BOTH_DIRECTIONS", True))
    auto_pivot = bool(cfg("PIVOT_LEN_AUTO", False)) if auto_pivot_override is None else bool(auto_pivot_override)

    filas = []
    for tf in timeframes:
        try:
            data, source = fetch_mt5_candles(symbol, tf, hasta)
        except Mt5Unavailable as e:
            data = ac.make_demo_data()
            source = f"DEMO — MT5 no disponible ({e})"
        except Mt5DataError as e:
            filas.append({"timeframe": tf, "error": str(e)})
            continue

        if len(data) < 5:
            filas.append({"timeframe": tf, "error": "Sin velas suficientes."})
            continue

        try:
            up_ch, dn_ch, _sig, _pivots = _long_channel(data, incremental, auto_pivot, show_both)
            channels_extra = _extra_channel(data, incremental, auto_pivot, show_both, "SHORT",
                                             SHORT_DEFAULTS, ("#FFA726", "#AB47BC"))
        except Exception as e:  # una temporalidad con problemas no debe tumbar el tablero entero
            filas.append({"timeframe": tf, "error": f"Error al calcular canales: {e}"})
            continue

        short_up = next((c for c, _color, _lbl in channels_extra
                          if _lbl.startswith("Canal corto") and c.direction == "up"), None)
        short_down = next((c for c, _color, _lbl in channels_extra
                            if _lbl.startswith("Canal corto") and c.direction == "down"), None)
        canales_tablero = [
            ("Canal largo", up_ch),
            ("Canal largo inverso", dn_ch),
            ("Canal corto", short_up),
            ("Canal corto inverso", short_down),
        ]
        resultados = cbs.evaluar_todos_spn(
            data, canales_tablero,
            atr_length=cfg("BREAKOUT_ATR_LEN", 14),
            min_displacement_atr=cfg("BREAKOUT_MIN_DISPLACEMENT", 0.15),
            near_threshold_pct=cfg("SPN_NEAR_THRESHOLD_PCT", 30.0),
        )
        celdas = {}
        clave_por_label = {
            "Canal largo": "canal_largo",
            "Canal largo inverso": "canal_largo_inverso",
            "Canal corto": "canal_corto",
            "Canal corto inverso": "canal_corto_inverso",
        }
        for r in resultados:
            celdas[clave_por_label[r.label]] = {
                "estado_label": cbs.ESTADO_LABELS.get(r.estado, r.estado),
                "estado_color": cbs.ESTADO_COLORS.get(r.estado, "#848e9c"),
                "distancia_pct": r.distancia_pct,
                "rotura_label": "Sí" if r.rotura else "No",
                "rotura_color": cbs.ROTURA_COLOR_SI if r.rotura else cbs.ROTURA_COLOR_NO,
                "lado_rotura": r.lado_rotura,
                # Confirmación exacta para el tooltip — el número real
                # detrás del redondeo de "Distancia" y la razón exacta
                # por la que "Rotura" dio Sí o No.
                "confirmacion": {
                    "precio_actual": r.precio_actual,
                    "borde_top": r.borde_top,
                    "borde_bottom": r.borde_bottom,
                    "distancia_exacta_pct": r.distancia_exacta_pct,
                    "cuerpo_vela": r.cuerpo_vela,
                    "displacement": r.displacement,
                    "displacement_minimo": r.displacement_minimo,
                    "atr": r.atr,
                },
            }
        # Canales que no existen para esa temporalidad (ej. "Corto"
        # desactivado, o no se pudo formar un canal válido) quedan en "—".
        for clave in ("canal_largo", "canal_largo_inverso", "canal_corto", "canal_corto_inverso"):
            celdas.setdefault(clave, {"estado_label": "—", "estado_color": "#848e9c",
                                       "distancia_pct": None, "rotura_label": "—",
                                       "rotura_color": "#848e9c", "lado_rotura": None,
                                       "confirmacion": None})

        filas.append({
            "timeframe": tf,
            "last_date": data["datetime"].iloc[-1].strftime("%Y-%m-%d %H:%M"),
            **celdas,
        })

    return {"symbol": symbol, "hasta": hasta, "filas": filas}


# Lista curada de divisas comunes para los checkboxes de la página nueva
# (/tablero). No depende de que MT5 esté conectado para mostrar opciones —
# el usuario puede tildar cualquiera de estas, o escribir símbolos propios
# a mano en el campo de texto de esa página.
DIVISAS_SUGERIDAS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY", "EURCHF", "XAUUSD",
]


def compute_multi_symbol_spn(symbols, timeframes=None, hasta=None, auto_pivot_override=None):
    """Recorre varias divisas, y para cada una arma el mismo tablero
    multi-temporalidad S-P-N de arriba — reutilizado tal cual, sin
    duplicar ninguna lógica de cálculo."""
    symbols = [s.strip().upper() for s in (symbols or []) if s.strip()]
    if not symbols:
        return {"error": "No se eligió ninguna divisa."}

    resultados = []
    for sym in symbols:
        r = compute_multi_timeframe_spn(symbol=sym, hasta=hasta, timeframes=timeframes,
                                         auto_pivot_override=auto_pivot_override)
        if "error" in r:
            resultados.append({"symbol": sym, "error": r["error"]})
        else:
            resultados.append(r)
    return {"resultados": resultados}
