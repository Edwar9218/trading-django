from django.contrib import admin
from .models import Dibujo


@admin.register(Dibujo)
class DibujoAdmin(admin.ModelAdmin):
    list_display = ("usuario", "simbolo", "timeframe", "tipo", "actualizado")
    list_filter = ("tipo", "timeframe")
    search_fields = ("usuario__username", "simbolo")
