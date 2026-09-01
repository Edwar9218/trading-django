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
