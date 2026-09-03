"""
config/celery.py
==================
Configuración de Celery. La tarea periódica que recalcula el tablero de
cada usuario cada 5 minutos vive en dashboard/tasks.py y se registra acá
vía CELERY_BEAT_SCHEDULE (o, más flexible, vía django-celery-beat desde
el admin, que permite cambiar el intervalo sin tocar código).
"""
import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("trading_django")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# ── Prioridades ──
# Sin esto, Celery procesa las tareas en orden simple de llegada (FIFO) —
# si el barrido automático de los 5 minutos ya está en la cola cuando el
# usuario guarda un cambio manual, el cambio manual queda esperando
# detrás, aunque sea una acción explícita del usuario.
#
# Con esto: 0 = más urgente, 9 = menos urgente. El guardado manual
# ("Guardar selección" / "Recalcular ahora") se encola con prioridad 0,
# el barrido automático con prioridad 9 — así, si ambos están en cola al
# mismo tiempo, el manual se procesa primero.
#
# OJO — límite real: esto solo reordena tareas que todavía están
# ESPERANDO en la cola. Si el barrido automático ya arrancó a ejecutarse
# (con --pool=solo, un worker procesa una tarea a la vez), Celery no
# puede interrumpirlo a mitad de camino — el guardado manual va a tener
# que esperar a que esa tarea en curso termine, sin importar la
# prioridad. Ahí no hay mucho más para hacer sin perder el trabajo ya
# hecho de esa tarea.
app.conf.task_queue_max_priority = 10
app.conf.task_default_priority = 5
app.conf.broker_transport_options = {
    "priority_steps": list(range(10)),
    "sep": ":",
    "queue_order_strategy": "priority",
}

# Schedule por defecto (además de/en vez de lo que se configure en el
# admin vía django-celery-beat): recalcula cada 5 minutos.
app.conf.beat_schedule = {
    "refrescar-tableros-cada-5-min": {
        "task": "dashboard.tasks.refrescar_todos_los_tableros",
        "schedule": 300.0,  # segundos
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
