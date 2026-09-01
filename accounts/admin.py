from django.contrib import admin
from .models import PerfilUsuario


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = ("user", "tema_oscuro", "creado")
    list_filter = ("tema_oscuro",)
    search_fields = ("user__username", "user__email")
