import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST

from core.engine import analysis
from .models import DivisaSeguida, TemporalidadSeguida, TableroSnapshot
from .tasks import refrescar_tablero_usuario


@login_required
def home(request):
    """
    Pantalla PRINCIPAL del sitio (LOGIN_REDIRECT_URL). Renderiza el
    watchlist del usuario + la tabla con los últimos snapshots ya
    calculados (sin recalcular nada acá — eso lo hace Celery cada 5 min).
    El template hace polling liviano a /dashboard/api/snapshot/ para
    refrescar la vista sin recargar la página.
    """
    divisas = list(DivisaSeguida.objects.filter(usuario=request.user).values_list("simbolo", flat=True))
    timeframes = list(TemporalidadSeguida.objects.filter(usuario=request.user).values_list("timeframe", flat=True))
    return render(request, "dashboard/home.html", {
        "divisas_sugeridas": analysis.DIVISAS_SUGERIDAS,
        "todos_los_timeframes": analysis.ALL_TIMEFRAMES,
        "mis_divisas": divisas,
        "mis_timeframes": timeframes,
    })


@login_required
def api_snapshot(request):
    """
    Lectura LIVIANA (solo SELECT a la base, sin tocar MT5 ni recalcular
    nada) — es lo que el front-end pollea cada cierto tiempo para
    refrescar la vista con lo último que haya calculado Celery.
    """
    snapshots = TableroSnapshot.objects.filter(usuario=request.user).order_by("simbolo", "timeframe")
    por_simbolo = {}
    for snap in snapshots:
        por_simbolo.setdefault(snap.simbolo, {"symbol": snap.simbolo, "filas": [], "error": None})
        if snap.error and snap.timeframe == "*":
            por_simbolo[snap.simbolo]["error"] = snap.error
        elif snap.datos:
            por_simbolo[snap.simbolo]["filas"].append(snap.datos)

    ultima = snapshots.order_by("-calculado_en").first()
    response = JsonResponse({
        "resultados": list(por_simbolo.values()),
        "ultima_actualizacion": ultima.calculado_en.isoformat() if ultima else None,
    })
    # Esta ruta se pollea cada 60s desde el navegador para reflejar lo
    # que Celery calculó en segundo plano — si el navegador la cachea,
    # el usuario ve datos viejos aunque el servidor ya tenga la
    # actualización lista. Headers explícitos para que ningún navegador
    # ni proxy intermedio la guarde en caché.
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response


@login_required
@require_POST
def guardar_watchlist(request):
    """
    Guarda las divisas/temporalidades tildadas por el usuario (checkboxes
    del tablero) y dispara un recálculo inmediato en segundo plano — así
    no hay que esperar hasta 5 minutos para ver la primera tabla.

    Si Celery/el broker no están corriendo (ej. en desarrollo, alguien
    todavía no levantó el worker), la selección se guarda igual — el
    próximo barrido de refrescar_todos_los_tableros() la va a recalcular
    en cuanto el worker esté disponible. No debe romper el guardado.
    """
    body = json.loads(request.body or "{}")
    simbolos = [s.strip().upper() for s in body.get("simbolos", []) if s.strip()]
    timeframes = [t.strip().upper() for t in body.get("timeframes", []) if t.strip()]

    DivisaSeguida.objects.filter(usuario=request.user).delete()
    DivisaSeguida.objects.bulk_create([
        DivisaSeguida(usuario=request.user, simbolo=s, orden=i) for i, s in enumerate(simbolos)
    ])

    TemporalidadSeguida.objects.filter(usuario=request.user).delete()
    TemporalidadSeguida.objects.bulk_create([
        TemporalidadSeguida(usuario=request.user, timeframe=t, orden=i) for i, t in enumerate(timeframes)
    ])

    recalculo_en_curso = True
    try:
        refrescar_tablero_usuario.delay(request.user.id)
    except Exception:
        # Sin broker disponible ahora mismo: no es un error del usuario,
        # el barrido periódico lo va a tomar apenas Celery esté arriba.
        recalculo_en_curso = False

    return JsonResponse({"ok": True, "recalculo_en_curso": recalculo_en_curso})


@login_required
@require_POST
def recalcular_ahora(request):
    """
    Botón "Recalcular ahora": dispara el mismo recálculo en segundo plano
    que guardar_watchlist(), pero sin tocar la selección — para cuando el
    usuario solo quiere forzar un refresco puntual (ej. "el precio se
    movió, quiero ver el estado actualizado ya") sin esperar los 5
    minutos del barrido automático de Celery.
    """
    recalculo_en_curso = True
    try:
        refrescar_tablero_usuario.delay(request.user.id)
    except Exception as e:
        recalculo_en_curso = False
        return JsonResponse({"ok": False, "recalculo_en_curso": False,
                              "error": f"No se pudo encolar el recálculo: {e}"}, status=503)

    return JsonResponse({"ok": True, "recalculo_en_curso": recalculo_en_curso})
