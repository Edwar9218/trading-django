from django.conf import settings
from django.db import models


class DivisaSeguida(models.Model):
    """Una divisa que el usuario tildó para que aparezca en su tablero."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="divisas_seguidas")
    simbolo = models.CharField(max_length=20)
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = ("usuario", "simbolo")
        ordering = ["orden", "simbolo"]

    def __str__(self):
        return f"{self.usuario.username} · {self.simbolo}"


class TemporalidadSeguida(models.Model):
    """Una temporalidad que el usuario tildó para su tablero (M15, H1, H4, D1, ...)."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="temporalidades_seguidas")
    timeframe = models.CharField(max_length=6)
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = ("usuario", "timeframe")
        ordering = ["orden", "timeframe"]

    def __str__(self):
        return f"{self.usuario.username} · {self.timeframe}"


class TableroSnapshot(models.Model):
    """
    El resultado YA CALCULADO del tablero S-P-N para (usuario, divisa,
    timeframe) en un momento dado. Lo escribe la tarea periódica de Celery
    cada 5 minutos (dashboard/tasks.py); el navegador solo LEE el más
    reciente — nunca dispara el cálculo pesado al cargar la página.
    """
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="snapshots")
    simbolo = models.CharField(max_length=20, db_index=True)
    timeframe = models.CharField(max_length=6, db_index=True)
    datos = models.JSONField()          # la fila S-P-N completa (4 canales) ya serializada
    calculado_en = models.DateTimeField(auto_now=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("usuario", "simbolo", "timeframe")
        indexes = [models.Index(fields=["usuario", "calculado_en"])]

    def __str__(self):
        return f"{self.usuario.username} · {self.simbolo} {self.timeframe} · {self.calculado_en:%H:%M:%S}"
