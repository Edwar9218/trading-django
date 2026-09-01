"""
mt5_export_h4.py
=================
Descarga el histórico (temporalidad configurable, ver config.py) de un
símbolo directamente desde tu terminal de MetaTrader 5 y lo guarda en un
CSV compatible con auto_channels.py.

IMPORTANTE — requisitos antes de correr esto:
  1. El terminal de MetaTrader 5 debe estar ABIERTO en esta misma PC
     (con sesión iniciada en tu cuenta/bróker, para que tenga acceso
     al histórico del símbolo).
  2. Instalar el paquete oficial de Python para MT5:
         pip install MetaTrader5 pandas
  3. Ejecutar este script con el MISMO Python donde instalaste el paquete
     (en Windows, normalmente el Python de 64 bits que usa tu MT5).

Uso:
    python mt5_export_h4.py
    python mt5_export_h4.py --timeframe D1 --bars 3000 --out eurusd_d1.csv
    python mt5_export_h4.py --timeframe D1 --until 20/03/2026

El CSV resultante queda listo para: python auto_channels.py <csv>
"""

import argparse
import sys
import os
from datetime import datetime

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: falta el paquete 'MetaTrader5'. Instálalo con:")
    print("    pip install MetaTrader5")
    sys.exit(1)

import numpy as np
import pandas as pd

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)
try:
    import config
except ImportError:
    config = None


def cfg(name, fallback):
    return getattr(config, name, fallback) if config else fallback


_TF_MAP = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1", "H2": "TIMEFRAME_H2", "H3": "TIMEFRAME_H3", "H4": "TIMEFRAME_H4",
    "H6": "TIMEFRAME_H6", "H8": "TIMEFRAME_H8", "H12": "TIMEFRAME_H12",
    "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1", "MN1": "TIMEFRAME_MN1",
}


def parse_args():
    p = argparse.ArgumentParser(description="Exporta velas desde MT5 a CSV")
    p.add_argument("--symbol", default=cfg("SYMBOL", "EURUSD"),
                   help="Símbolo tal como aparece en tu Market Watch (default: nombre de esta carpeta, vía config.py)")
    p.add_argument("--timeframe", default=cfg("TIMEFRAME", "H4"),
                   help="Temporalidad: M1,M5,M15,M30,H1,H2,H3,H4,H6,H8,H12,D1,W1,MN1 (default: config.TIMEFRAME)")
    p.add_argument("--bars", type=int, default=cfg("BARS_CAP", 3000),
                   help="Tope de velas a descargar")
    p.add_argument("--from-date", default=cfg("START_DATE", None),
                   help="Descargar desde esta fecha en adelante (ej. 01/01/2010), formato DD/MM/AAAA. "
                        "Si se da, trae TODO ese rango (hasta --bars como tope de seguridad).")
    p.add_argument("--until", default=cfg("UNTIL_DATE", None),
                   help="Fecha límite (ej. 20/03/2026), formato DD/MM/AAAA. Si se omite, hasta hoy.")
    p.add_argument("--out", default=None, help="Nombre del CSV de salida (default: config.CSV_NAME o <symbol>_<timeframe>.csv)")
    return p.parse_args()


def main():
    args = parse_args()
    out_path = args.out or cfg("CSV_NAME", f"{args.symbol}_{args.timeframe}.csv")

    tf_attr = _TF_MAP.get(args.timeframe.upper())
    if tf_attr is None:
        print(f"ERROR: timeframe '{args.timeframe}' no reconocido. Usa uno de: {', '.join(_TF_MAP)}")
        sys.exit(1)
    mt5_tf = getattr(mt5, tf_attr)

    # ── 1. Conectar con el terminal MT5 ya abierto ──
    if not mt5.initialize():
        print(f"ERROR: no se pudo conectar a MT5. Código de error: {mt5.last_error()}")
        print("Verifica que el terminal MetaTrader 5 esté ABIERTO y con sesión iniciada.")
        sys.exit(1)

    info = mt5.terminal_info()
    acc = mt5.account_info()
    print(f"MT5 conectado — cuenta {acc.login if acc else '?'} ({acc.company if acc else '?'})")

    # ── 2. Verificar que el símbolo exista y esté visible en Market Watch ──
    symbol_info = mt5.symbol_info(args.symbol)
    if symbol_info is None:
        print(f"ERROR: el símbolo '{args.symbol}' no existe en tu Market Watch.")
        print("Revisa el nombre exacto (algunos brókers usan sufijos, ej. EURUSD.m, EURUSDpro, etc).")
        mt5.shutdown()
        sys.exit(1)

    if not symbol_info.visible:
        print(f"'{args.symbol}' no estaba visible en Market Watch, agregándolo...")
        mt5.symbol_select(args.symbol, True)

    # ── 3. Descargar velas ──
    from datetime import timedelta

    # Velas aproximadas por día calendario, según temporalidad (para estimar
    # cuántos días hacia atrás hay que pedir). No hace falta que sea exacto,
    # solo un punto de partida razonable — el bucle de abajo amplía si falta.
    _BARS_PER_DAY = {
        "M1": 1440, "M5": 288, "M15": 96, "M30": 48,
        "H1": 24, "H2": 12, "H3": 8, "H4": 6, "H6": 4, "H8": 3, "H12": 2,
        "D1": 1, "W1": 1 / 7, "MN1": 1 / 30,
    }
    bars_per_day = _BARS_PER_DAY.get(args.timeframe.upper(), 6)

    # Solo tiene sentido pedir la vela "en formación" (la más reciente,
    # todavía sin cerrar) cuando el rango pedido llega hasta HOY. Si el
    # usuario pidió un --until en el pasado (backtest puntual), la vela
    # más reciente de MT5 no pertenece a ese rango y no hay que agregarla.
    range_reaches_today = args.until is None

    if args.from_date:
        # Fecha de inicio explícita: traemos TODO ese rango de una vez
        # (esto es lo que usa config.START_DATE = "01/01/2010" por defecto).
        date_from = datetime.strptime(args.from_date, "%d/%m/%Y")
        date_to = (datetime.strptime(args.until, "%d/%m/%Y") + timedelta(days=1)
                   if args.until else datetime.now() + timedelta(days=1))
        rates = mt5.copy_rates_range(args.symbol, mt5_tf, date_from, date_to)
        if rates is not None and len(rates) > args.bars:
            rates = rates[-args.bars:]  # tope de seguridad

        # Fallback: si el servidor no tiene historial hasta esa fecha (común
        # en cuentas demo genéricas), en vez de fallar directo, traemos las
        # velas más recientes disponibles con copy_rates_from_pos.
        if rates is None or len(rates) == 0:
            print(f"AVISO: no hay historial disponible desde {args.from_date}. "
                  f"Intentando traer las {args.bars} velas más recientes en su lugar...")
            rates = mt5.copy_rates_from_pos(args.symbol, mt5_tf, 0, args.bars)

    elif args.until:
        date_to = datetime.strptime(args.until, "%d/%m/%Y")
        # copy_rates_range corta en el instante exacto (00:00), así que sumamos
        # un día para incluir todas las velas del día "until" indicado.
        date_to_inclusive = date_to + timedelta(days=1)

        # Sin fecha de inicio explícita: calculamos una ventana de días con
        # margen de sobra para cubrir 'args.bars' velas, y la ampliamos
        # solo si hace falta (en vez de pedir décadas de golpe, que puede
        # colgarse si el terminal no tenía ese histórico en caché).
        days_back = max(int(args.bars * 1.5 / max(bars_per_day, 0.01)), 30)
        max_days_back = 365 * 20
        rates = None

        while True:
            date_from = date_to_inclusive - timedelta(days=days_back)
            rates_range = mt5.copy_rates_range(args.symbol, mt5_tf, date_from, date_to_inclusive)

            if rates_range is not None and len(rates_range) >= args.bars:
                rates = rates_range[-args.bars:]
                break

            found = len(rates_range) if rates_range is not None else 0
            if days_back >= max_days_back:
                print(f"AVISO: solo se encontraron {found} velas incluso ampliando al máximo (20 años).")
                rates = rates_range
                break

            days_back = min(days_back * 3, max_days_back)
    else:
        rates = mt5.copy_rates_from_pos(args.symbol, mt5_tf, 0, args.bars)

    # ── 3.5 Asegurar que la vela MÁS RECIENTE (la actual, todavía en
    # formación) quede incluida ──
    # copy_rates_range no siempre trae esa última vela: su hora de apertura
    # puede caer justo en el borde del rango pedido, y MT5 a veces la deja
    # afuera aunque le pidamos "hasta mañana". copy_rates_from_pos(...,0,1)
    # SIEMPRE devuelve la vela más reciente que el terminal tiene disponible,
    # así que la usamos para tapar ese hueco si hiciera falta.
    if range_reaches_today and rates is not None and len(rates) > 0:
        latest = mt5.copy_rates_from_pos(args.symbol, mt5_tf, 0, 1)
        if latest is not None and len(latest) > 0 and latest[-1]["time"] > rates[-1]["time"]:
            rates = np.concatenate([rates, latest])
            if len(rates) > args.bars:
                rates = rates[-args.bars:]

    mt5.shutdown()

    if rates is None or len(rates) == 0:
        print(f"ERROR: MT5 no devolvió velas (last_error: {mt5.last_error()}).")
        print(f"Prueba abrir el gráfico {args.timeframe} de '{args.symbol}' manualmente en el terminal "
              "(y desplázate hacia atrás con la tecla Inicio/Home hasta que deje de cargar velas nuevas, "
              "eso fuerza la descarga del historial completo desde el servidor). Luego vuelve a correr este script.")
        print("Si tu cuenta es una demo genérica (MetaQuotes-Demo), es posible que su historial no llegue "
              "tan atrás como START_DATE en config.py — prueba con un rango más reciente, ej.:")
        print(f"    python {os.path.basename(__file__)} --from-date 01/01/2023 --bars 5000")
        sys.exit(1)

    # ── 4. Convertir a DataFrame y guardar en el formato esperado ──
    df = pd.DataFrame(rates)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    df["Date"] = df["datetime"].dt.strftime("%Y.%m.%d")
    df["Time"] = df["datetime"].dt.strftime("%H:%M")

    out_df = df[["Date", "Time"]].copy()
    out_df["Open"] = df["open"]
    out_df["High"] = df["high"]
    out_df["Low"] = df["low"]
    out_df["Close"] = df["close"]
    out_df["Volume"] = df["tick_volume"]

    out_df.to_csv(out_path, index=False)

    print(f"{len(out_df)} velas guardadas en {out_path} "
          f"({out_df['Date'].iloc[0]} -> {out_df['Date'].iloc[-1]})")


if __name__ == "__main__":
    main()