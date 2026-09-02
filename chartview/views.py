import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.engine import analysis
from drawings.models import Dibujo


@login_required
def grafico(request):
    """
    La pantalla de análisis detallado (equivalente al index.html
    original). Se llega acá directo desde el menú, o vía el atajo del
    tablero con ?symbol=...&timeframe=...&hasta=... precargado.
    """
    return render(request, "chartview/grafico.html", {
        "symbol_inicial": request.GET.get("symbol", ""),
        "timeframe_inicial": request.GET.get("timeframe", ""),
        "hasta_inicial": request.GET.get("hasta", ""),
    })


@login_required
def api_datos(request):
    """Mismo contrato que /datos del servidor Flask original — el
    front-end no tiene que cambiar su forma de pedir datos, solo la URL."""
    symbol = request.GET.get("symbol", "").strip() or None
    timeframe = request.GET.get("timeframe", "").strip() or None
    hasta = request.GET.get("hasta", "").strip() or None

    auto_pivot_param = request.GET.get("auto_pivot", "").strip().lower()
    auto_pivot_override = None
    if auto_pivot_param in ("1", "true", "si", "sí"):
        auto_pivot_override = True
    elif auto_pivot_param in ("0", "false", "no"):
        auto_pivot_override = False

    try:
        result = analysis.compute_analysis(symbol=symbol, timeframe_override=timeframe, hasta=hasta,
                                            auto_pivot_override=auto_pivot_override)
    except Exception as e:
        return JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=500)

    if "error" in result:
        return JsonResponse(result, status=400)
    return JsonResponse(result)


@login_required
def api_velas_extra(request):
    """Botón 'Cargar más historial' — mismo contrato que /velas_extra del
    servidor Flask original: trae un lote de velas más viejas que las que
    ya tiene el navegador, SIN recalcular canales/Kalman/SMF."""
    symbol = request.GET.get("symbol", "").strip()
    timeframe = request.GET.get("timeframe", "H4").strip()
    antes_de = request.GET.get("antes_de", "").strip()
    cantidad = request.GET.get("cantidad", "500").strip()

    if not symbol or not antes_de:
        return JsonResponse({"error": "Faltan parámetros symbol/antes_de."}, status=400)
    try:
        antes_de_ts = int(antes_de)
        cantidad = max(50, min(int(cantidad), 5000))
    except ValueError:
        return JsonResponse({"error": "antes_de/cantidad deben ser números."}, status=400)

    try:
        candles = analysis.fetch_mt5_older_candles(symbol, timeframe, antes_de_ts, cantidad)
    except (analysis.Mt5Unavailable, analysis.Mt5DataError) as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"error": f"{type(e).__name__}: {e}"}, status=500)

    return JsonResponse({"candles": candles})


@login_required
def api_config(request):
    """Mismo contrato que /config del servidor Flask original — le dice
    al HTML cuáles son el símbolo/timeframe por defecto para precargar
    los selectores sin duplicar esos valores a mano en el frontend."""
    return JsonResponse({
        "symbol": analysis.cfg("SYMBOL", "EURUSD"),
        "timeframe": analysis.cfg("TIMEFRAME", "H4"),
    })


@login_required
def api_ping(request):
    """Mismo contrato que /ping — usado por el indicador de 'Servidor
    online' en la barra superior del gráfico."""
    return JsonResponse({"status": "ok"})


@login_required
def api_dibujos_listar(request):
    """Trae los dibujos guardados de ESTE usuario para esta divisa +
    temporalidad — así al volver a abrir el gráfico aparecen tal cual se
    dejaron la vez anterior."""
    simbolo = request.GET.get("symbol", "").strip().upper()
    timeframe = request.GET.get("timeframe", "").strip().upper()
    dibujos = Dibujo.objects.filter(usuario=request.user, simbolo=simbolo, timeframe=timeframe)
    return JsonResponse({
        "dibujos": [{"id": d.id, "tipo": d.tipo, "datos": d.datos} for d in dibujos]
    })


@login_required
@require_POST
def api_dibujos_guardar(request):
    """Guarda (o reemplaza por completo) el set de dibujos de esta divisa
    + temporalidad para este usuario. El front-end manda la lista
    completa cada vez que el usuario suelta el mouse tras dibujar/mover/
    borrar algo — más simple que sincronizar dibujo por dibujo."""
    body = json.loads(request.body or "{}")
    simbolo = body.get("symbol", "").strip().upper()
    timeframe = body.get("timeframe", "").strip().upper()
    dibujos = body.get("dibujos", [])

    Dibujo.objects.filter(usuario=request.user, simbolo=simbolo, timeframe=timeframe).delete()
    Dibujo.objects.bulk_create([
        Dibujo(usuario=request.user, simbolo=simbolo, timeframe=timeframe,
               tipo=d.get("tipo", "trendline"), datos=d.get("datos", {}))
        for d in dibujos
    ])
    return JsonResponse({"ok": True, "guardados": len(dibujos)})
