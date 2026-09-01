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

    def __str__(self):
        return f"Perfil de {self.user.username}"
