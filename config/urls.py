"""
config/urls.py — enrutado raíz del proyecto.

"" (raíz) redirige al tablero, que es la pantalla principal según lo
pedido: el gráfico detallado (chartview) vive en /grafico/ y se llega ahí
por el menú o por el atajo desde una fila del tablero.
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('grafico/', include('chartview.urls')),
    path('', include('dashboard.urls')),  # el tablero es la pantalla principal
]
