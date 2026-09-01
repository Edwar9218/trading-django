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
