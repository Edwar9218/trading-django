from django.test import TestCase

# Create your tests here.


from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class SRFractalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("u", password="p")
        self.client.login(username="u", password="p")

    def test_grafico_incluye_toggle_y_script(self):
        html = self.client.get(reverse("chartview:grafico")).content.decode()
        self.assertIn("chk-srf", html)
        self.assertIn("chartview/fractal_sr.js", html)
        self.assertIn("srf-board", html)

    def test_preferencia_sr_fractal_se_guarda(self):
        r = self.client.post(reverse("chartview:api_preferencia_guardar"),
                             '{"campo": "sr_fractal", "valor": true}',
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.user.perfil.refresh_from_db()
        self.assertTrue(self.user.perfil.pref_sr_fractal)
        html = self.client.get(reverse("chartview:grafico")).content.decode()
        self.assertIn('class="chk-srf" checked', html)


class TZTTests(TestCase):
    """Tzotchev Trend: el botón, el panel y el script tienen que llegar al HTML
    (si falta el marcado, crearPanel() no encuentra el checkbox y se rompe el
    panel entero) y la preferencia se guarda por usuario."""

    def setUp(self):
        self.user = User.objects.create_user("u3", password="p")
        self.client.login(username="u3", password="p")

    def test_grafico_incluye_toggle_panel_y_script(self):
        html = self.client.get(reverse("chartview:grafico")).content.decode()
        self.assertIn("chk-tzt", html)
        self.assertIn("tzt-container", html)
        self.assertIn("chartview/tzotchev_trend.js", html)

    def test_boton_vive_dentro_de_la_barra_de_indicadores(self):
        # "Apagar todo" actúa sobre los checkboxes de esta barra: si TZT queda
        # afuera, el botón dejaría de apagarlo.
        html = self.client.get(reverse("chartview:grafico")).content.decode()
        barra = html.split('<div class="channel-toggles">')[1].split('<div class="channel-legend">')[0]
        self.assertIn("chk-tzt", barra)

    def test_apagado_por_defecto(self):
        self.assertFalse(self.user.perfil.pref_tzt)
        html = self.client.get(reverse("chartview:grafico")).content.decode()
        self.assertIn("chk-tzt", html)
        self.assertNotIn('class="chk-tzt" checked', html)

    def test_preferencia_tzt_se_guarda(self):
        r = self.client.post(reverse("chartview:api_preferencia_guardar"),
                             '{"campo": "tzt", "valor": true}',
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.user.perfil.refresh_from_db()
        self.assertTrue(self.user.perfil.pref_tzt)
        html = self.client.get(reverse("chartview:grafico")).content.decode()
        self.assertIn('class="chk-tzt" checked', html)

        r = self.client.post(reverse("chartview:api_preferencia_guardar"),
                             '{"campo": "tzt", "valor": false}',
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.user.perfil.refresh_from_db()
        self.assertFalse(self.user.perfil.pref_tzt)


class BotonTodosTests(TestCase):
    """El botón de apagar/encender todo tiene que llegar al HTML: si el
    marcado se cae, crearPanel() explota al no encontrarlo y se rompe el
    panel entero."""

    def setUp(self):
        self.user = User.objects.create_user("u2", password="p")
        self.client.login(username="u2", password="p")

    def test_boton_presente_dentro_de_la_barra(self):
        html = self.client.get(reverse("chartview:grafico")).content.decode()
        self.assertIn('class="btn-todos"', html)
        barra = html.split('<div class="channel-toggles">')[1].split("</div>")[0]
        self.assertIn("btn-todos", barra)
        # Pivot auto sigue siendo un checkbox más de la barra: el botón lo
        # excluye por su clase, así que esa clase no puede cambiar de nombre.
        self.assertIn("chk-auto-pivot", barra)
