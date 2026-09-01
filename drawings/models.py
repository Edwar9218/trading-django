from django.conf import settings
from django.db import models


class Dibujo(models.Model):
    """
    Un dibujo (línea, línea horizontal, fibo, texto, trazo libre) hecho por
    el usuario en el gráfico, persistido para que al volver a abrir esa
    misma divisa/temporalidad los encuentre tal cual los dejó.

    El contenido geométrico (puntos, estilo) se guarda tal cual lo produce
    el editor de dibujo del front-end (mismo JSON que ya usaba el canvas
    de index.html) — no se reinterpreta acá, solo se persiste.
    """
    TIPO_CHOICES = [
        ("trendline", "Línea de tendencia"),
        ("hline", "Línea horizontal"),
        ("fib", "Retroceso de Fibonacci"),
        ("brush", "Dibujo libre"),
        ("text", "Texto"),
    ]

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="dibujos")
    simbolo = models.CharField(max_length=20, db_index=True)
    timeframe = models.CharField(max_length=6, db_index=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    datos = models.JSONField()          # puntos/estilo tal cual los arma el canvas
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["usuario", "simbolo", "timeframe"])]
        ordering = ["creado"]

    def __str__(self):
        return f"{self.usuario.username} · {self.simbolo} {self.timeframe} · {self.tipo}"
