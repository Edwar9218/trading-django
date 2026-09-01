from django.contrib import admin
from .models import DivisaSeguida, TemporalidadSeguida, TableroSnapshot


@admin.register(DivisaSeguida)
class DivisaSeguidaAdmin(admin.ModelAdmin):
    list_display = ("usuario", "simbolo", "orden")
    search_fields = ("usuario__username", "simbolo")


@admin.register(TemporalidadSeguida)
class TemporalidadSeguidaAdmin(admin.ModelAdmin):
    list_display = ("usuario", "timeframe", "orden")
    search_fields = ("usuario__username",)


@admin.register(TableroSnapshot)
class TableroSnapshotAdmin(admin.ModelAdmin):
    list_display = ("usuario", "simbolo", "timeframe", "calculado_en", "tiene_error")
    list_filter = ("timeframe",)
    search_fields = ("usuario__username", "simbolo")
    readonly_fields = ("calculado_en",)

    @admin.display(boolean=True, description="Error")
    def tiene_error(self, obj):
        return bool(obj.error)
