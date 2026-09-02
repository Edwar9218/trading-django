from django.urls import path
from . import views

app_name = "chartview"

urlpatterns = [
    path("", views.grafico, name="grafico"),
    path("api/datos/", views.api_datos, name="api_datos"),
    path("api/velas_extra/", views.api_velas_extra, name="api_velas_extra"),
    path("api/config/", views.api_config, name="api_config"),
    path("api/ping/", views.api_ping, name="api_ping"),
    path("api/dibujos/", views.api_dibujos_listar, name="api_dibujos_listar"),
    path("api/dibujos/guardar/", views.api_dibujos_guardar, name="api_dibujos_guardar"),
    path("api/preferencia/guardar/", views.api_preferencia_guardar, name="api_preferencia_guardar"),
]
