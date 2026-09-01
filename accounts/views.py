from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy
from django.views.generic import CreateView


class LoginViewEs(LoginView):
    template_name = "accounts/login.html"


class LogoutViewEs(LogoutView):
    next_page = reverse_lazy("accounts:login")


class RegistroView(CreateView):
    """Registro simple con el UserCreationForm nativo de Django. Cada
    usuario nuevo dispara la señal que le crea su PerfilUsuario y queda
    con su propio watchlist/dibujos vacíos, listos para usar."""
    form_class = UserCreationForm
    template_name = "accounts/registro.html"
    success_url = reverse_lazy("dashboard:home")

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        return response
