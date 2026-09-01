# Esto hace que Celery se cargue cuando arranca Django, así los
# decoradores @shared_task quedan siempre conectados a esta app.
from .celery import app as celery_app

__all__ = ("celery_app",)
