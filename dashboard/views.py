import json
import logging

from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST

from core.engine import analysis
from .models import DivisaSeguida, TemporalidadSeguida, TableroSnapshot
from .tasks import refrescar_tablero_usuario, _borrar_snapshots_fuera_de_seleccion

logger = logging.getLogger(__name__)


def _bump_generacion(usuario):
    """Incrementa el contador de generación del usuario (atómico, vía F())
    y devuelve el nuevo valor. Cualquier tarea de Celery que ya estuviera
    corriendo con la generación vieja se aborta apenas lo detecta — así
    una acción manual del usuario "corta" el trabajo viejo y pasa a
    tener prioridad, en vez de esperar a que termine algo que ya no
    importa."""
    perfil = usuario.perfil
    perfil.watchlist_generacion = F("watchlist_generacion") + 1
    perfil.save(update_fields=["watchlist_generacion"])
    perfil.refresh_from_db(fields=["watchlist_generacion"])
    return perfil.watchlist_generacion


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
    del tablero).

    OPTIMIZACIÓN — solo dispara Celery si hace falta traer algo NUEVO:
    si la nueva selección es un subconjunto de la anterior (o sea, el
    usuario solo destildó cosas, sin agregar nada), no hace falta pedirle
    nada a MT5 — los datos que quedan ya están calculados y vigentes, así
    que alcanza con borrar lo que sobra. Recién si aparece una divisa o
    temporalidad que ANTES no estaba, se dispara el recálculo — porque
    eso sí necesita datos que todavía no se calcularon.

    Si Celery/el broker no están corriendo (ej. en desarrollo, alguien
    todavía no levantó el worker), la selección se guarda igual — el
    próximo barrido de refrescar_todos_los_tableros() la va a recalcular
    en cuanto el worker esté disponible. No debe romper el guardado.
    """
    body = json.loads(request.body or "{}")
    simbolos = [s.strip().upper() for s in body.get("simbolos", []) if s.strip()]
    timeframes = [t.strip().upper() for t in body.get("timeframes", []) if t.strip()]

    # Se compara ANTES de tocar nada — necesito saber qué había para
    # decidir si esto es "solo quitar" o si hay algo nuevo.
    simbolos_antes = set(DivisaSeguida.objects.filter(usuario=request.user).values_list("simbolo", flat=True))
    timeframes_antes = set(TemporalidadSeguida.objects.filter(usuario=request.user).values_list("timeframe", flat=True))
    hay_novedades = bool(set(simbolos) - simbolos_antes) or bool(set(timeframes) - timeframes_antes)

    DivisaSeguida.objects.filter(usuario=request.user).delete()
    DivisaSeguida.objects.bulk_create([
        DivisaSeguida(usuario=request.user, simbolo=s, orden=i) for i, s in enumerate(simbolos)
    ])

    TemporalidadSeguida.objects.filter(usuario=request.user).delete()
    TemporalidadSeguida.objects.bulk_create([
        TemporalidadSeguida(usuario=request.user, timeframe=t, orden=i) for i, t in enumerate(timeframes)
    ])

    # Limpieza INMEDIATA de lo viejo: cualquier snapshot ya calculado que
    # corresponda a una divisa o temporalidad que se acaba de destildar
    # tiene que desaparecer ya, no quedar "fantasma" hasta que a Celery le
    # toque recalcular. "timeframe='*'" es un caso especial (marca de
    # error general de la divisa, no una temporalidad real) — se
    # conserva mientras la divisa siga en la lista.
    _borrar_snapshots_fuera_de_seleccion(request.user, simbolos, timeframes)

    if not hay_novedades:
        # Solo se quitó algo (o no cambió nada) — no hace falta pedirle
        # nada a Celery/MT5. Lo que queda en la tabla ya es correcto tal
        # cual está.
        logger.info("[guardar_watchlist] usuario=%s: solo quito cosas, sin recalculo", request.user.username)
        return JsonResponse({"ok": True, "recalculo_necesario": False, "recalculo_en_curso": False, "error": None})

    recalculo_en_curso = True
    error_encolado = None
    try:
        # Se incrementa la generación PRIMERO — cualquier tarea vieja que
        # siguiera corriendo (ej. el barrido automático anterior, o un
        # guardado previo) va a detectar en su próximo chequeo que ya
        # quedó obsoleta y se corta sola, cediéndole el paso a esta.
        generacion = _bump_generacion(request.user)
        # Prioridad ALTA (0) además de la generación — así, si esta
        # tarea todavía está ESPERANDO en la cola (no corriendo todavía),
        # también se procesa antes que cualquier tarea de baja prioridad
        # en espera.
        refrescar_tablero_usuario.apply_async(
            args=[request.user.id], kwargs={"generacion_esperada": generacion}, priority=0)
    except Exception as e:
        # Antes esto se tragaba el error en silencio ("no es un error del
        # usuario") — pero eso hacía IMPOSIBLE saber por qué "agregar" no
        # calculaba nada nuevo (a diferencia de "quitar", que no depende
        # de Celery para nada, por eso ese caso sí se veía andar bien).
        # Ahora el error queda BIEN VISIBLE en la terminal de
        # runserver, y también viaja al navegador.
        logger.exception("No se pudo encolar refrescar_tablero_usuario para el usuario %s", request.user.id)
        recalculo_en_curso = False
        error_encolado = f"{type(e).__name__}: {e}"

    return JsonResponse({"ok": True, "recalculo_necesario": True,
                          "recalculo_en_curso": recalculo_en_curso, "error": error_encolado})


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
        generacion = _bump_generacion(request.user)
        refrescar_tablero_usuario.apply_async(
            args=[request.user.id], kwargs={"generacion_esperada": generacion}, priority=0)
    except Exception as e:
        logger.exception("No se pudo encolar refrescar_tablero_usuario (recalcular_ahora) para el usuario %s",
                          request.user.id)
        recalculo_en_curso = False
        return JsonResponse({"ok": False, "recalculo_en_curso": False,
                              "error": f"No se pudo encolar el recálculo: {type(e).__name__}: {e}"}, status=503)

    return JsonResponse({"ok": True, "recalculo_en_curso": recalculo_en_curso})
