from django.urls import path
from . import views

app_name = "chartview"

urlpatterns = [
    path("", views.grafico, name="grafico"),
    path("api/datos/", views.api_datos, name="api_datos"),
    path("api/dibujos/", views.api_dibujos_listar, name="api_dibujos_listar"),
    path("api/dibujos/guardar/", views.api_dibujos_guardar, name="api_dibujos_guardar"),
]
