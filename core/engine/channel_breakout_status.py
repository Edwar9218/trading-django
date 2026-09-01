"""
channel_breakout_status.py
============================
Sistema S-P-N (Soporte / Resistencia / Neutro) para saber, de un vistazo,
qué está haciendo el precio respecto a cada canal.

MISMA función para los 4 canales (largo, largo inverso, corto, corto
inverso) — simétrica, sin importar si el canal se construyó como
ascendente o descendente:
  - Si el precio está tocando/rompiendo el borde de ABAJO de la banda →
    "Soporte".
  - Si está tocando/rompiendo el borde de ARRIBA → "Resistencia".
  - Si está lejos de ambos → "Neutro".

Un canal ascendente puede perfectamente reportar "Resistencia" (si el
precio se disparó y rompió por arriba de toda la banda) y uno descendente
puede reportar "Soporte" (si el precio se hundió por debajo) — la
dirección con la que se construyó el canal no limita qué puede reportar.

Tres columnas por canal:
  - Estado (S-P-N): "Soporte" / "Resistencia" / "Neutro"
  - Distancia: % del ANCHO DE LA BANDA que le falta al precio para tocar
    el borde relevante (el que determina el Estado). 0% = tocando/rompió
    ese borde ahora mismo.
  - Rotura: Sí/No — si el cierre de la última vela ya está más allá de
    algún borde (con un cuerpo mínimo de vela para filtrar ruido), más el
    lado (arriba/abajo) para que quede explícito cuál se rompió.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List, Tuple


def _atr(df: pd.DataFrame, length: int) -> np.ndarray:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(length, min_periods=1).mean().to_numpy()


@dataclass
class ChannelSPN:
    label: str
    direction: str              # "up" / "down" — cómo se construyó el canal (informativo, no limita el estado)
    estado: str                  # "soporte" / "resistencia" / "neutro"
    distancia_pct: Optional[float]   # 0-100 REDONDEADO (para mostrar en la celda)
    rotura: bool
    lado_rotura: Optional[str] = None   # "arriba" / "abajo" / None
    quality: float = 0.0
    # ── Confirmación exacta (sin redondear) — para el tooltip, así se
    # puede verificar el número real detrás de un "0%" que en la celda
    # se ve igual aunque el precio esté apenas debajo o apenas arriba
    # del borde. ──
    precio_actual: Optional[float] = None
    borde_top: Optional[float] = None
    borde_bottom: Optional[float] = None
    distancia_exacta_pct: Optional[float] = None   # sin redondear
    cuerpo_vela: Optional[float] = None             # |close - open| de la última vela
    displacement: Optional[float] = None            # cuerpo_vela / ATR
    displacement_minimo: Optional[float] = None      # el umbral que hace falta superar
    atr: Optional[float] = None


ESTADO_LABELS = {"soporte": "Soporte", "resistencia": "Resistencia", "neutro": "Neutro"}
ESTADO_COLORS = {"soporte": "#00e676", "resistencia": "#ff5252", "neutro": "#848e9c"}
ROTURA_COLOR_SI = "#f7931a"
ROTURA_COLOR_NO = "#848e9c"


def evaluar_canal_spn(df: pd.DataFrame, channel, label: str,
                       atr_length: int = 14, min_displacement_atr: float = 0.15,
                       near_threshold_pct: float = 30.0) -> ChannelSPN:
    """
    near_threshold_pct: por debajo de esta distancia (%) al borde más
    cercano, el precio ya cuenta como "en" Soporte o Resistencia. Por
    encima de eso (y sin ninguna ruptura activa), es "Neutro".
    """
    n = len(df)
    if n == 0 or channel is None:
        return ChannelSPN(label=label, direction="", estado="neutro", distancia_pct=None,
                           rotura=False, quality=0.0)

    close = df["close"].to_numpy(dtype=float)
    open_ = df["open"].to_numpy(dtype=float)
    atr_arr = _atr(df, atr_length)
    last = n - 1

    base = channel.base_at(last)
    par = base + channel.offset
    top = max(base, par)
    bottom = min(base, par)
    width = top - bottom
    price = close[last]

    if width <= 0:
        dist_bottom_pct = dist_top_pct = 0.0
    else:
        dist_bottom_pct = max(0.0, min(100.0, (price - bottom) / width * 100.0))
        dist_top_pct = max(0.0, min(100.0, (top - price) / width * 100.0))

    atr_last = atr_arr[last]
    if np.isnan(atr_last) or atr_last == 0:
        finite = atr_arr[~np.isnan(atr_arr)]
        atr_last = float(np.mean(finite)) if finite.size else 1.0
    body = abs(close[last] - open_[last])
    disp = body / atr_last if atr_last else 0.0

    rotura_abajo = bool(price < bottom and disp >= min_displacement_atr)
    rotura_arriba = bool(price > top and disp >= min_displacement_atr)
    rotura = rotura_abajo or rotura_arriba
    lado_rotura = "abajo" if rotura_abajo else ("arriba" if rotura_arriba else None)

    # Estado: mismo criterio para los 4 canales, sin importar cómo se
    # construyeron. Una ruptura siempre "gana" (define el estado), y si no
    # hay ninguna, gana el borde más cercano si está dentro del umbral.
    if rotura_abajo:
        estado, distancia_exacta = "soporte", dist_bottom_pct
    elif rotura_arriba:
        estado, distancia_exacta = "resistencia", dist_top_pct
    elif dist_bottom_pct <= dist_top_pct and dist_bottom_pct <= near_threshold_pct:
        estado, distancia_exacta = "soporte", dist_bottom_pct
    elif dist_top_pct < dist_bottom_pct and dist_top_pct <= near_threshold_pct:
        estado, distancia_exacta = "resistencia", dist_top_pct
    else:
        estado, distancia_exacta = "neutro", min(dist_bottom_pct, dist_top_pct)

    return ChannelSPN(
        label=label,
        direction=str(getattr(channel, "direction", "")),
        estado=estado,
        distancia_pct=round(distancia_exacta, 0),
        rotura=rotura,
        lado_rotura=lado_rotura,
        quality=float(getattr(channel, "quality", 0.0)),
        precio_actual=float(price),
        borde_top=float(top),
        borde_bottom=float(bottom),
        distancia_exacta_pct=float(distancia_exacta),
        cuerpo_vela=float(body),
        displacement=float(disp),
        displacement_minimo=float(min_displacement_atr),
        atr=float(atr_last),
    )


def evaluar_todos_spn(df: pd.DataFrame, canales: List[Tuple[str, object]], **kwargs) -> List[ChannelSPN]:
    """canales: lista de (label, channel_obj) — channel_obj puede ser None (se ignora)."""
    return [evaluar_canal_spn(df, ch, label, **kwargs) for label, ch in canales if ch is not None]

