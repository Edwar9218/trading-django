"""
dashboard/tasks.py
====================
Tarea periódica (Celery beat, cada 5 min — ver config/celery.py) que
recalcula el tablero S-P-N de CADA usuario, para CADA combinación de sus
divisas × temporalidades seguidas, y guarda el resultado en
TableroSnapshot. El navegador nunca dispara este cálculo — solo lee el
snapshot más reciente (dashboard/views.py), así la página carga rápido
sin importar cuánto tarde MT5 en responder.
"""
from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.engine import analysis
from .models import DivisaSeguida, TemporalidadSeguida, TableroSnapshot

User = get_user_model()


@shared_task(bind=True, ignore_result=True)
def refrescar_tablero_usuario(self, user_id):
    """Recalcula el tablero de UN usuario. Se puede llamar sola (ej. justo
    después de que el usuario cambia su watchlist, para no esperar los 5
    minutos) o desde refrescar_todos_los_tableros() en el barrido periódico."""
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    simbolos = list(DivisaSeguida.objects.filter(usuario=user).values_list("simbolo", flat=True))
    timeframes = list(TemporalidadSeguida.objects.filter(usuario=user).values_list("timeframe", flat=True))
    if not simbolos or not timeframes:
        return

    for simbolo in simbolos:
        resultado = analysis.compute_multi_timeframe_spn(symbol=simbolo, timeframes=timeframes)
        if "error" in resultado:
            TableroSnapshot.objects.update_or_create(
                usuario=user, simbolo=simbolo, timeframe="*",
                defaults={"datos": {}, "error": resultado["error"]},
            )
            continue

        # Un snapshot por (usuario, símbolo, timeframe) — así la vista puede
        # leer una fila puntual sin tener que filtrar un blob gigante.
        for fila in resultado.get("filas", []):
            tf = fila.get("timeframe")
            TableroSnapshot.objects.update_or_create(
                usuario=user, simbolo=simbolo, timeframe=tf,
                defaults={"datos": fila, "error": fila.get("error", "")},
            )


@shared_task(ignore_result=True)
def refrescar_todos_los_tableros():
    """El barrido cada 5 minutos: una sub-tarea por usuario, para que un
    usuario con muchas divisas no bloquee el refresco de los demás."""
    ids_usuarios = User.objects.filter(is_active=True).values_list("id", flat=True)
    for user_id in ids_usuarios:
        refrescar_tablero_usuario.delay(user_id)
