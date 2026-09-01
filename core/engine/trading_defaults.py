"""
trading_defaults.py  (ex config.py)
=====================================
Valores por defecto para canales/Kalman/Smart Money Flow/S-P-N. En el
proyecto original, SYMBOL se detectaba del nombre de la carpeta (una
carpeta por divisa) — en Django eso ya no aplica: la divisa y la
temporalidad SIEMPRE vienen del pedido (usuario, watchlist, URL), así que
acá solo queda un fallback fijo para cuando algo no especifica nada.
"""
import os

# ── Divisa por defecto (fallback si no se especifica ninguna) ──
SYMBOL = "EURUSD"

# ── Rango de fechas del historial ──
# Fecha desde la que se descarga y analiza (formato DD/MM/AAAA).
# Déjala en 2010 para traer "todo" el historial disponible, o cámbiala si
# solo te interesa desde una fecha más reciente en adelante.
START_DATE = "01/01/2010"

# Fecha límite del análisis. None = hasta hoy.
# Cámbiala (ej. "20/03/2026") si quieres un backtest puntual a esa fecha.
UNTIL_DATE = None

# ── Temporalidad (timeframe) de las velas ──
# Opciones válidas: "M1","M5","M15","M30","H1","H2","H3","H4","H6","H8","H12","D1","W1","MN1"
TIMEFRAME = "D1"

# Tope de velas a traer, por si START_DATE trajera un histórico enorme.
# H1 tiene ~4x más velas que H4 para el mismo rango de fechas, así que el
# tope se ajusta automáticamente según TIMEFRAME (ver BARS_CAP más abajo).
_BARS_CAP_BY_TF = {
    "M1": 2_000_000, "M5": 400_000, "M15": 140_000, "M30": 70_000,
    "H1": 80_000, "H2": 40_000, "H3": 27_000, "H4": 20_000,
    "H6": 14_000, "H8": 10_000, "H12": 7_000, "D1": 6_000, "W1": 1_200, "MN1": 300,
}
BARS_CAP = _BARS_CAP_BY_TF.get(TIMEFRAME, 20_000)

# ── Nombre del archivo CSV de esta divisa (se genera y se lee automático) ──
CSV_NAME = f"{SYMBOL}_{TIMEFRAME}.csv"

# ── Mostrar AMBAS direcciones (ascendente Y descendente) a la vez en cada
# horizonte, igual que el indicador de TradingView (que dibuja el canal
# ascendente Y el descendente en simultáneo si ambos están activados).
# True  = como TradingView: se dibujan los dos, cada uno independiente.
# False = modo "Auto" viejo: en cada horizonte se queda SOLO con el canal
#         de mejor calidad (ascendente o descendente, el que gane), y
#         descarta el otro aunque también sea válido.
SHOW_BOTH_DIRECTIONS = True

# ── Canal LARGO: valores calcados de tu indicador SRChannels en TradingView
# para H1 ("21 14 10 400 0.55" en el título del indicador = pivotLen 21,
# atrLen 14, minBars 10, maxBars 400, quality 0.55). Este es el canal de
# estructura más amplia/tendencia principal. ──
PIVOT_LEN = 21
ATR_LEN = 14
MIN_BARS = 10
MAX_BARS = 500
QUALITY = 0.55
RECENT_N = 8
LOOKBACK_PAIRS = 5
REPLACE_RATIO = 0.7

# ── Canal MEDIANO: mismo mecanismo, pivot_len intermedio entre el largo (21)
# y el corto (6), para ver la tendencia de plazo medio (días, no semanas). ──
SHOW_MEDIUM_CHANNEL = False
PIVOT_LEN_MED = 18
ATR_LEN_MED = 10
MIN_BARS_MED = 8
MAX_BARS_MED = 100
QUALITY_MED = 0.45
RECENT_N_MED = 12
LOOKBACK_PAIRS_MED = 8
REPLACE_RATIO_MED = 0.7

# ── Canal CORTO: estructura más reciente/pequeña (últimas velas de H1,
# horas/1-2 días), el más rápido en reaccionar de los tres. ──
SHOW_SHORT_CHANNEL = True
PIVOT_LEN_SHORT = 6
ATR_LEN_SHORT = 4
MIN_BARS_SHORT = 4
MAX_BARS_SHORT = 80
QUALITY_SHORT = 0.5
RECENT_N_SHORT = 8
LOOKBACK_PAIRS_SHORT = 5
REPLACE_RATIO_SHORT = 0.7

# ── Opciones del gráfico ──
PLOT_LAST = 300     # cuántas velas recientes se muestran (recorte por la izquierda)

# Espacio vacío (en barras) que se deja a la derecha de la última vela, donde
# se proyectan las líneas de los canales — el equivalente al margen que
# TradingView deja por defecto entre el precio actual y el borde derecho.
# Súbelo si quieres que las velas recientes ("lo que está pasando ahora")
# queden más corridas hacia la izquierda, con más aire a la derecha para
# analizar. Bájalo si quieres que las velas ocupen más espacio del gráfico.
EXTEND_BARS = 150

WIDTH = 20
HEIGHT = 8
HIDE_PIVOTS = True
INCREMENTAL = True  # modo "en vivo" (recomendado, es el que ya validamos contra TradingView)

# ── Modo incremental: ventana de "repaso" bar-por-bar ──
# INCREMENTAL=True repasa el historial vela por vela (fiel a como se vería
# el indicador corriendo en vivo). Por defecto repasa TODO lo descargado
# (80.000 velas en H1), lo cual es lo más lento del script — sobre todo el
# canal corto, que al tener pivotes muy frecuentes puede tardar 20+ segundos
# él solo con 80.000 velas.
# Como cada canal ya tiene un ancho máximo (MAX_BARS/_MED/_SHORT), no hace
# falta repasar 80.000 velas para un canal que como mucho mide 80 velas de
# ancho. Estos valores acotan el repaso a las N velas más recientes
# (con margen de sobra sobre el MAX_BARS de cada uno) y no cambian el canal
# resultante, solo lo calculan mucho más rápido.
# None = repasa todo el historial (comportamiento original, más lento).
CHANNEL_LOOKBACK = 6000        # canal largo  (MAX_BARS=500)
CHANNEL_LOOKBACK_MED = 4000    # canal mediano (MAX_BARS_MED=180)
CHANNEL_LOOKBACK_SHORT = 3000  # canal corto  (MAX_BARS_SHORT=80) — el que más tardaba

# ── Kalman Flow: activado/desactivado ──
# Desactivado por defecto: con el panel de Smart Money Flow + los 3 canales
# (largo/mediano/corto) ya hay bastante confluencia en el gráfico, y el
# Kalman superpuesto lo recargaba visualmente. Poné esto en True (o corré
# con --kalman) si lo querés de vuelta.
KALMAN_ENABLED = True

# ── Kalman Flow: ventana de cálculo ──
# Cuántas velas recientes se usan para calcular el Kalman Flow (línea base,
# bandas y la etiqueta "Kalman: Alcista/Bajista" que se imprime en consola).
# None = usa TODO el historial descargado (80.000 velas en H1), aunque solo
# se grafiquen las últimas PLOT_LAST.
# Ponle un número (recomendado: 2-3 veces PLOT_LAST, para darle al filtro un
# poco de "calentamiento" antes de la zona visible) para que la señal de
# consola quede acorde a las velas que realmente se muestran en el gráfico.
KALMAN_LOOKBACK = PLOT_LAST * 3   # ej. 900 con PLOT_LAST=300

# ── Smart Money Flow (panel aparte: momentum + Chaikin Money Flow + MFI) ──
# Requiere que el CSV tenga columna Volume (mt5_export_h4.py ya la incluye,
# usando el tick_volume de tu bróker). Desactivado por defecto porque no
# todos los CSV van a tener volumen.
SMART_MONEY_FLOW_ENABLED = False
SMART_MONEY_FLOW_OUT = None    # None = <SYMBOL>_<TIMEFRAME>_smf.png

SMF_MOMENTUM_PERIOD = 10
SMF_TREND_PERIOD = 21
SMF_MFI_PERIOD = 14
SMF_SIGNAL_SMOOTHING = 4
SMF_PIVOT_LEFT = 3
SMF_PIVOT_RIGHT = 3

# IMPORTANTE: el Pine original usaba 14/8 fijos, calibrados contra SU feed
# de volumen (probablemente TradingView). El tick volume de forex difiere
# entre brokers/plataformas (ver conversación), así que acá se dejan en
# None para que se AUTO-CALIBREN contra tu propio historial de MT5, en vez
# de copiar números pensados para otro feed de datos.
SMF_PIVOT_SENSITIVITY = None      # None = auto-calibrado (percentil de tu propio historial)
SMF_EARLY_SENSITIVITY = None      # None = auto-calibrado en base a SMF_PIVOT_SENSITIVITY
SMF_CALIBRATE_PERCENTILE = 80.0   # más alto = pivotes más estrictos/selectivos

# ── Tablero S-P-N (toggle, 4 canales: largo, largo inverso, corto, corto
# inverso). Para cada canal: Estado (Soporte/Resistencia/Neutro — según su
# rol natural: ascendente=soporte, descendente=resistencia), Distancia (%
# del ancho de la banda que falta para tocar su borde relevante) y Rotura
# (Sí/No, con un cuerpo de vela mínimo para filtrar ruido).
BREAKOUT_ATR_LEN = 14
BREAKOUT_MIN_DISPLACEMENT = 0.15   # cuerpo mínimo de la vela para contar como rotura real, en ATR
SPN_NEAR_THRESHOLD_PCT = 30.0      # por debajo de esta distancia (%) ya cuenta como Soporte/Resistencia, no Neutro

# ── Nombres de salida (usan el símbolo y el timeframe automáticamente) ──
CHART_OUT = f"{SYMBOL}_{TIMEFRAME}_chart.png"
EXCEL_OUT = f"eventos_{SYMBOL}_{TIMEFRAME}.xlsx"