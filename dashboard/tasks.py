"""
dashboard/tasks.py
====================
Recalcula el tablero S-P-N de cada usuario, para cada combinación de sus
divisas × temporalidades seguidas, y guarda el resultado en
TableroSnapshot. El navegador nunca dispara este cálculo — solo lee el
snapshot más reciente (dashboard/views.py), así la página carga rápido
sin importar cuánto tarde MT5 en responder.

SISTEMA DE "GENERACIÓN" (cancelar lo viejo, priorizar lo del usuario):
Cada vez que el usuario guarda una selección nueva o aprieta "Recalcular
ahora", se incrementa PerfilUsuario.watchlist_generacion y la tarea nueva
se encola con ESE número. Cualquier tarea vieja que siga corriendo (o que
recién esté por escribir su resultado) chequea ese número contra el
actual en la base — si ya cambió, significa que el usuario pidió algo
más nuevo mientras tanto, así que la tarea vieja se aborta ahí mismo, sin
seguir calculando divisas que ya no le importan a nadie, y sin escribir
nada que pudiera pisar el resultado más reciente.

LÍMITE TÉCNICO HONESTO: con --pool=solo (obligatorio en Windows), el
cálculo de UNA divisa puntual no se puede interrumpir a la mitad sin
matar el worker entero — es indivisible. Lo que sí se logra es que, en
cuanto el chequeo de generación detecta que quedó vieja, la tarea NO
sigue con las divisas que faltaban: el "corte" ocurre entre una divisa y
la siguiente, que en la práctica es cuestión de segundos.

RELOJ DE 5 MINUTOS QUE CUENTA DESDE LA ÚLTIMA ACTUALIZACIÓN: en vez de un
barrido fijo del sistema cada 5 minutos en un reloj global, cada tarea,
al terminar con éxito, programa su propia continuación 5 minutos después
de ESE momento (con la misma generación). Si el usuario vuelve a guardar
antes de que pasen esos 5 minutos, esa continuación programada queda
obsoleta por el chequeo de generación y no hace nada cuando le toque
correr — la cadena la retoma la tarea nueva del usuario.
"""
import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.engine import analysis
from .models import DivisaSeguida, TemporalidadSeguida, TableroSnapshot

User = get_user_model()
logger = logging.getLogger(__name__)

SEGUNDOS_ENTRE_ACTUALIZACIONES = 300   # 5 minutos, contados desde que termina cada corrida


@shared_task(bind=True, ignore_result=True)
def refrescar_tablero_usuario(self, user_id, generacion_esperada=None):
    """Recalcula el tablero de UN usuario.

    generacion_esperada: el número de generación con el que se encoló
    esta tarea. Si es None, la tarea corre siempre sin chequear (se usa
    para el primer disparo manual, antes de que exista una generación
    previa que comparar). Si viene con un número, la tarea se aborta en
    cuanto detecta que la generación actual del usuario ya cambió."""
    try:
        user = User.objects.select_related("perfil").get(pk=user_id)
    except User.DoesNotExist:
        return

    generacion_actual = user.perfil.watchlist_generacion
    if generacion_esperada is not None and generacion_actual != generacion_esperada:
        logger.info("[refrescar_tablero_usuario] usuario=%s: tarea vieja (gen %s, ahora es gen %s) — abortada sin calcular nada",
                    user.username, generacion_esperada, generacion_actual)
        return
    generacion_esperada = generacion_actual   # a partir de acá, la referencia para los chequeos

    simbolos = list(DivisaSeguida.objects.filter(usuario=user).values_list("simbolo", flat=True))
    timeframes = list(TemporalidadSeguida.objects.filter(usuario=user).values_list("timeframe", flat=True))
    logger.info("[refrescar_tablero_usuario] usuario=%s (gen %s) | divisas=%s | temporalidades=%s",
                user.username, generacion_esperada, simbolos, timeframes)

    if not simbolos or not timeframes:
        with transaction.atomic():
            _borrar_snapshots_fuera_de_seleccion(user, simbolos, timeframes)
        return

    # ── FASE 1: calcular todo en memoria (esto es lo lento) ──
    resultados_por_simbolo = {}
    for simbolo in simbolos:
        # Chequeo de generación ANTES de cada divisa — acá es donde se
        # "corta" una tarea vieja: si ya hay un pedido más nuevo del
        # usuario, no tiene sentido seguir calculando divisas que ese
        # pedido nuevo va a recalcular de todos modos.
        user.perfil.refresh_from_db(fields=["watchlist_generacion"])
        if user.perfil.watchlist_generacion != generacion_esperada:
            logger.info("[refrescar_tablero_usuario] usuario=%s: generación cambió a mitad de camino (gen %s -> %s) — se corta acá, quedaban %d divisas sin calcular",
                        user.username, generacion_esperada, user.perfil.watchlist_generacion,
                        len(simbolos) - len(resultados_por_simbolo))
            return   # una tarea más nueva ya se está encargando

        logger.info("[refrescar_tablero_usuario] calculando %s...", simbolo)
        try:
            resultado = analysis.compute_multi_timeframe_spn(symbol=simbolo, timeframes=timeframes)
        except Exception as e:
            # Excepción NO ATRAPADA por analysis.py (distinto de un
            # {"error": "..."} normal) — el caso típico es una divisa
            # recién agregada que MT5 todavía no sincronizó del todo en
            # su Market Watch. Se registra COMPLETO (con traceback) para
            # poder ver la causa exacta, y se convierte en un error
            # "normal" para esta divisa, sin tumbar las demás.
            logger.exception("[refrescar_tablero_usuario] EXCEPCION calculando %s", simbolo)
            resultado = {"error": f"{type(e).__name__}: {e}"}
        resultados_por_simbolo[simbolo] = resultado
        if "error" in resultado:
            logger.warning("[refrescar_tablero_usuario] %s -> ERROR: %s", simbolo, resultado["error"])
        else:
            tfs_calculadas = [f.get("timeframe") for f in resultado.get("filas", [])]
            logger.info("[refrescar_tablero_usuario] %s -> OK, temporalidades: %s", simbolo, tfs_calculadas)

    # Último chequeo de generación antes de escribir — por si cambió
    # justo mientras se calculaba la ÚLTIMA divisa.
    user.perfil.refresh_from_db(fields=["watchlist_generacion"])
    if user.perfil.watchlist_generacion != generacion_esperada:
        logger.info("[refrescar_tablero_usuario] usuario=%s: generación cambió justo antes de escribir (gen %s -> %s) — se descarta todo el cálculo",
                    user.username, generacion_esperada, user.perfil.watchlist_generacion)
        return

    # ── FASE 2: escribir todo junto, de una — nadie ve un estado a medias ──
    with transaction.atomic():
        simbolos_actuales = set(DivisaSeguida.objects.filter(usuario=user).values_list("simbolo", flat=True))
        timeframes_actuales = set(TemporalidadSeguida.objects.filter(usuario=user).values_list("timeframe", flat=True))

        _borrar_snapshots_fuera_de_seleccion(user, list(simbolos_actuales), list(timeframes_actuales))

        for simbolo, resultado in resultados_por_simbolo.items():
            if simbolo not in simbolos_actuales:
                logger.info("[refrescar_tablero_usuario] %s ya no esta en el watchlist actual, se salta", simbolo)
                continue

            if "error" in resultado:
                TableroSnapshot.objects.update_or_create(
                    usuario=user, simbolo=simbolo, timeframe="*",
                    defaults={"datos": {}, "error": resultado["error"]},
                )
                continue

            for fila in resultado.get("filas", []):
                tf = fila.get("timeframe")
                if tf not in timeframes_actuales:
                    continue
                TableroSnapshot.objects.update_or_create(
                    usuario=user, simbolo=simbolo, timeframe=tf,
                    defaults={"datos": fila, "error": fila.get("error", "")},
                )

    logger.info("[refrescar_tablero_usuario] usuario=%s (gen %s): fase 2 (escritura) completada",
                user.username, generacion_esperada)

    # ── Reprogramar la PRÓXIMA actualización automática, 5 minutos
    # después de ESTE momento (no de un reloj fijo del sistema) — con la
    # MISMA generación. Si el usuario guarda de nuevo antes de esos 5
    # minutos, esta continuación va a abortar sola apenas le toque correr
    # (por el chequeo de generación de arriba), y la cadena la retoma la
    # tarea nueva del usuario. ──
    refrescar_tablero_usuario.apply_async(
        args=[user_id], kwargs={"generacion_esperada": generacion_esperada},
        countdown=SEGUNDOS_ENTRE_ACTUALIZACIONES, priority=9,
    )
    logger.info("[refrescar_tablero_usuario] usuario=%s: próxima actualización automática programada para dentro de %ss",
                user.username, SEGUNDOS_ENTRE_ACTUALIZACIONES)


def _borrar_snapshots_fuera_de_seleccion(usuario, simbolos, timeframes):
    """Borra los TableroSnapshot de este usuario que ya NO corresponden a
    ninguna combinación de la selección actual — "timeframe='*'" es la
    marca de error general de una divisa, no una temporalidad real, y se
    conserva mientras esa divisa siga en la lista."""
    TableroSnapshot.objects.filter(usuario=usuario).exclude(
        Q(simbolo__in=simbolos) & (Q(timeframe__in=timeframes) | Q(timeframe="*"))
    ).delete()


@shared_task(ignore_result=True)
def refrescar_todos_los_tableros():
    """Red de seguridad: barrido cada 5 minutos POR RELOJ FIJO del
    sistema (ver config/celery.py), además de la cadena de
    auto-reprogramación de arriba. Cubre el caso de que la cadena se
    corte por algún motivo (ej. el worker se reinició) — sin esto, un
    usuario que no vuelve a guardar nada podría quedarse sin
    actualizaciones automáticas para siempre. Se manda con
    generacion_esperada=None (corre siempre, sin chequear) — si en ese
    momento hay una tarea del usuario en curso más nueva, no hay
    conflicto real: como todo se escribe recién en la Fase 2 con una
    transacción atómica, en el peor caso se calcula dos veces seguidas,
    pero nunca se pisan resultados a medias."""
    ids_usuarios = User.objects.filter(is_active=True).values_list("id", flat=True)
    for user_id in ids_usuarios:
        refrescar_tablero_usuario.apply_async(args=[user_id], priority=9)
