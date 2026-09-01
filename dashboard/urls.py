from django.urls import path
from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("api/snapshot/", views.api_snapshot, name="api_snapshot"),
    path("api/watchlist/", views.guardar_watchlist, name="guardar_watchlist"),
    path("api/recalcular/", views.recalcular_ahora, name="recalcular_ahora"),
]
