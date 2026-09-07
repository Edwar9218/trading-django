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
import os
import time
from concurrent.futures import ProcessPoolExecutor

# ── Límite de hilos de numpy/BLAS — DEBE ir antes de cualquier import
# que traiga numpy (como core.engine.analysis, unas líneas más abajo).
# numpy/pandas usan una librería interna (OpenBLAS/MKL) que decide
# cuántos hilos propios abrir la PRIMERA vez que se importa — después de
# eso, cambiar la variable de entorno ya no tiene efecto. Si esto se
# fija después (ej. como initializer del pool, llamado recién cuando el
# proceso ya arrancó), llega tarde: en Windows, ProcessPoolExecutor
# fuerza una re-importación completa de este archivo durante el
# bootstrap del proceso hijo, y la import de analysis (línea de más
# abajo) ya se ejecutó ANTES de que cualquier initializer corra.
#
# Sin este límite, cada uno de los SPN_CALC_WORKERS procesos del pool
# abre por su cuenta varios hilos internos de numpy — con 4 procesos ×
# varios hilos cada uno, se pisan entre sí peleando por los mismos
# núcleos en vez de sumar velocidad real.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

# ── Bootstrap de Django para los procesos hijos del pool de cálculo ──
# En Windows, ProcessPoolExecutor (spawn) vuelve a importar este mismo
# archivo desde cero en cada proceso nuevo del pool — y en ESE proceso
# Django todavía no está inicializado (nadie llamó django.setup() ahí,
# a diferencia del proceso principal de Celery, donde config/celery.py
# ya lo hace). Sin este bloque, la import de más abajo
# (`from .models import ...`) explota con "AppRegistryNotReady: Apps
# aren't loaded yet." apenas arranca cada proceso del pool, y el pool
# entero queda roto (BrokenProcessPool) — exactamente lo que se vio en
# el log: 30/30 cálculos fallando y "temporalidades: []" para todo.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
from django.apps import apps as _django_apps
if not _django_apps.ready:
    django.setup()

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

# Cuántos procesos en paralelo se usan para el CÁLCULO (canales + S-P-N) de
# cada (divisa, temporalidad) — no tiene nada que ver con la conexión a
# MT5 (esa sigue siendo una sola, serializada, ver mt5_session). Tope bajo
# a propósito: esta misma PC corre el terminal MT5 + Django + Redis, así
# que no conviene acaparar todos los núcleos disponibles.
SPN_CALC_WORKERS = min(4, os.cpu_count() or 4)


def _pool_warmup():
    """Tarea vacía — solo sirve para medir cuánto tarda un proceso del
    pool en arrancar (import de Django/numpy/pandas incluido) separado
    del tiempo del cálculo real."""
    return True


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

    # ── FASE 1a: traer TODAS las velas (esto ya es prácticamente gratis:
    # ver mt5_session — una sola conexión reutilizada para todo el lote,
    # en vez de que cada fetch abra/cierre el terminal por su cuenta). ──
    incremental = analysis.cfg("INCREMENTAL", True)
    show_both = analysis.cfg("SHOW_BOTH_DIRECTIONS", True)
    auto_pivot = bool(analysis.cfg("PIVOT_LEN_AUTO", False))

    trabajos = []          # [(simbolo, tf, data)] — listos para el pool de cálculo
    resultados_por_simbolo = {}
    filas_por_simbolo = {simbolo: [] for simbolo in simbolos}

    try:
        mt5_ctx = analysis.mt5_session()
        mt5_ctx.__enter__()
    except analysis.Mt5Unavailable as e:
        logger.warning("[refrescar_tablero_usuario] usuario=%s: MT5 no disponible (%s) — se marca error en todas las divisas",
                        user.username, e)
        mt5_ctx = None
        error_mt5 = str(e)
    else:
        error_mt5 = None

    try:
        for simbolo in simbolos:
            user.perfil.refresh_from_db(fields=["watchlist_generacion"])
            if user.perfil.watchlist_generacion != generacion_esperada:
                logger.info("[refrescar_tablero_usuario] usuario=%s: generación cambió durante el fetch (gen %s -> %s) — se corta acá",
                            user.username, generacion_esperada, user.perfil.watchlist_generacion)
                return

            if error_mt5 is not None:
                resultados_por_simbolo[simbolo] = {"error": error_mt5}
                continue

            for tf in timeframes:
                _t0 = time.perf_counter()
                try:
                    data, source = analysis.fetch_mt5_candles(simbolo, tf)
                except analysis.Mt5Unavailable as e:
                    filas_por_simbolo[simbolo].append({"timeframe": tf, "error": str(e)})
                    continue
                except analysis.Mt5DataError as e:
                    filas_por_simbolo[simbolo].append({"timeframe": tf, "error": str(e)})
                    continue
                except Exception as e:
                    logger.exception("[refrescar_tablero_usuario] EXCEPCION trayendo %s %s", simbolo, tf)
                    filas_por_simbolo[simbolo].append({"timeframe": tf, "error": f"{type(e).__name__}: {e}"})
                    continue
                logger.info("[refrescar_tablero_usuario] %s %s: fetch=%.3fs (%d velas)",
                            simbolo, tf, time.perf_counter() - _t0, len(data))
                trabajos.append((simbolo, tf, data))
    finally:
        if mt5_ctx is not None:
            mt5_ctx.__exit__(None, None, None)

    # Chequeo de generación entre fase de fetch y fase de cálculo — si
    # cambió, no tiene sentido gastar CPU calculando algo que ya quedó
    # obsoleto.
    user.perfil.refresh_from_db(fields=["watchlist_generacion"])
    if user.perfil.watchlist_generacion != generacion_esperada:
        logger.info("[refrescar_tablero_usuario] usuario=%s: generación cambió justo después del fetch (gen %s -> %s) — se descarta todo",
                    user.username, generacion_esperada, user.perfil.watchlist_generacion)
        return

    # ── FASE 1b: CÁLCULO (canales + S-P-N), repartido entre varios
    # procesos — esto es lo que en el log de fetch=/calc= resultó ser
    # ~99% del tiempo total. No toca MT5 para nada, así que acá sí
    # podemos paralelizar de verdad sin ningún riesgo de datos
    # corruptos (ver docstring de mt5_session). ──
    if trabajos:
        logger.info("[refrescar_tablero_usuario] usuario=%s: calculando %d (divisa, temporalidad) en paralelo con %d procesos...",
                     user.username, len(trabajos), SPN_CALC_WORKERS)
        _t_pool0 = time.perf_counter()
        with ProcessPoolExecutor(max_workers=SPN_CALC_WORKERS) as pool:
            _t_pool_creado = time.perf_counter()

            # Warm-up: fuerza a que los SPN_CALC_WORKERS procesos arranquen
            # (con su import completo de Django/numpy/pandas) ANTES de medir
            # el trabajo real — así separamos "arrancar procesos" de
            # "calcular" en el log de más abajo.
            warmup = [pool.submit(_pool_warmup) for _ in range(SPN_CALC_WORKERS)]
            for f in warmup:
                f.result()
            _t_warmup = time.perf_counter()

            futuros = {
                pool.submit(analysis.compute_tf_row, simbolo, tf, data, incremental, auto_pivot, show_both): simbolo
                for simbolo, tf, data in trabajos
            }
            _t_enviados = time.perf_counter()
            for futuro in futuros:
                simbolo = futuros[futuro]
                try:
                    fila = futuro.result()
                except Exception as e:
                    logger.exception("[refrescar_tablero_usuario] EXCEPCION calculando %s", simbolo)
                    fila = {"error": f"{type(e).__name__}: {e}"}
                filas_por_simbolo[simbolo].append(fila)
            _t_resultados = time.perf_counter()
        _t_pool_cerrado = time.perf_counter()
        logger.info(
            "[refrescar_tablero_usuario] usuario=%s: pool timing — crear_pool=%.3fs "
            "warmup_%d_procesos=%.3fs enviar_30_tareas=%.3fs esperar_resultados=%.3fs "
            "cerrar_pool=%.3fs (total=%.3fs)",
            user.username,
            _t_pool_creado - _t_pool0,
            SPN_CALC_WORKERS, _t_warmup - _t_pool_creado,
            _t_enviados - _t_warmup,
            _t_resultados - _t_enviados,
            _t_pool_cerrado - _t_resultados,
            _t_pool_cerrado - _t_pool0,
        )

    for simbolo in simbolos:
        if simbolo in resultados_por_simbolo:
            continue   # ya tiene un error de MT5 global asignado en fase 1a
        resultados_por_simbolo[simbolo] = {"symbol": simbolo, "filas": filas_por_simbolo[simbolo]}
        tfs_calculadas = [f.get("timeframe") for f in filas_por_simbolo[simbolo] if "error" not in f]
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
