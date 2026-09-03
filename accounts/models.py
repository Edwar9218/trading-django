from django.conf import settings
from django.db import models


class PerfilUsuario(models.Model):
    """
    Extiende el User nativo de Django con preferencias generales (no
    ligadas a una divisa/temporalidad puntual, eso vive en dashboard y
    drawings). Se crea automáticamente vía señal post_save del User
    (ver accounts/signals.py).
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                 related_name="perfil")
    tema_oscuro = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    # ── Preferencias del gráfico (checkboxes de la barra de canales) ──
    # Se guardan por usuario para que no haya que volver a tildarlas cada
    # vez que se entra a /grafico/ — los defaults acá abajo coinciden con
    # los que ya tenía el HTML antes de que existiera esta persistencia.
    pref_largo = models.BooleanField(default=True)
    pref_mediano = models.BooleanField(default=True)
    pref_corto = models.BooleanField(default=True)
    pref_relleno = models.BooleanField(default=False)
    pref_kalman = models.BooleanField(default=True)
    pref_auto_pivot = models.BooleanField(default=False)
    pref_tablero_canales = models.BooleanField(default=False)

    # ── Generación del watchlist ──
    # Se incrementa cada vez que el usuario guarda una selección nueva o
    # aprieta "Recalcular ahora". Las tareas de Celery en curso chequean
    # este número contra el que tenían al arrancar — si cambió, significa
    # que el usuario ya pidió algo más nuevo, así que la tarea vieja se
    # aborta sin seguir calculando divisas que ya no importan, en vez de
    # terminar su recorrido completo y potencialmente pisar el resultado
    # más reciente.
    watchlist_generacion = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Perfil de {self.user.username}"
