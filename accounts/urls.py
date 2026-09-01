from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginViewEs.as_view(), name="login"),
    path("logout/", views.LogoutViewEs.as_view(), name="logout"),
    path("registro/", views.RegistroView.as_view(), name="registro"),
]
