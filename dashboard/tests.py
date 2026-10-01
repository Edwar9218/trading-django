from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.engine import analysis
from dashboard.models import DivisaSeguida, TemporalidadSeguida


class _FakeMt5:
    """MT5 de mentira: solo conoce EURUSD y EURUSD.m, y las temporalidades
    que el motor mapea (TIMEFRAME_*)."""

    def __init__(self):
        for attr in analysis._TF_MAP.values():
            setattr(self, attr, 1)
        self.seleccionados = []

    def initialize(self):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (0, "ok")

    def symbol_info(self, nombre):
        if nombre in ("EURUSD", "EURUSD.m"):
            return SimpleNamespace(visible=False, name=nombre)
        return None

    def symbol_select(self, nombre, valor):
        self.seleccionados.append(nombre)
        return True

    def symbols_get(self):
        return [SimpleNamespace(name="EURUSD.m"), SimpleNamespace(name="XAUUSD")]


class ValidarItemTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("u", password="x")
        self.client.force_login(self.user)
        self.url = reverse("dashboard:validar_item")
        self.fake = _FakeMt5()
        patcher = mock.patch.object(analysis, "mt5", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, tipo, valor):
        return self.client.post(self.url, {"tipo": tipo, "valor": valor}, content_type="application/json")

    def test_divisa_existente_se_acepta_y_se_activa(self):
        r = self._post("divisa", "eurusd")
        self.assertTrue(r.json()["ok"])
        self.assertEqual(r.json()["valor"], "EURUSD")
        self.assertIn("EURUSD", self.fake.seleccionados)

    def test_divisa_inexistente_muestra_mensaje_y_sugiere(self):
        r = self._post("divisa", "EURUS")
        self.assertFalse(r.json()["ok"])
        self.assertIn("no existe en tu MT5", r.json()["mensaje"])
        r = self._post("divisa", "XXXYYY")
        self.assertFalse(r.json()["ok"])

    def test_divisa_parecida_a_las_del_broker_se_sugiere(self):
        # Escribió de menos (XAUUS) o de más (XAUUSD.PRO): se sugiere XAUUSD.
        for escrito in ("XAUUS", "XAUUSD.PRO"):
            r = self._post("divisa", escrito)
            self.assertFalse(r.json()["ok"], escrito)
            self.assertIn("XAUUSD", r.json()["sugerencias"], escrito)

    def test_simbolo_con_formato_invalido(self):
        r = self._post("divisa", "EUR USD;")
        self.assertFalse(r.json()["ok"])

    def test_temporalidad_valida_e_invertida(self):
        for entrada, esperado in [("h4", "H4"), ("M5", "M5"), ("4h", "H4"), ("15m", "M15"), ("mn1", "MN1")]:
            r = self._post("temporalidad", entrada)
            self.assertTrue(r.json()["ok"], entrada)
            self.assertEqual(r.json()["valor"], esperado)

    def test_temporalidad_inexistente(self):
        r = self._post("temporalidad", "H5")
        self.assertFalse(r.json()["ok"])
        self.assertIn("no existe en MT5", r.json()["mensaje"])

    def test_sin_mt5_responde_503_y_no_agrega(self):
        with mock.patch.object(analysis, "mt5", None):
            r = self._post("divisa", "EURUSD")
        self.assertEqual(r.status_code, 503)
        self.assertFalse(r.json()["ok"])
        self.assertTrue(r.json()["mt5_no_disponible"])

    def test_tipo_desconocido(self):
        self.assertEqual(self._post("otra", "x").status_code, 400)


class SincronizacionTableroGraficoTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("u", password="x")
        self.client.force_login(self.user)
        for i, s in enumerate(["EURUSD", "USDMXN"]):
            DivisaSeguida.objects.create(usuario=self.user, simbolo=s, orden=i)
        for i, t in enumerate(["M15", "M5"]):
            TemporalidadSeguida.objects.create(usuario=self.user, timeframe=t, orden=i)

    def test_tablero_muestra_la_temporalidad_agregada_en_orden(self):
        html = self.client.get(reverse("dashboard:home")).content.decode()
        self.assertIn('class="tf-chk" value="M5" checked', html)
        self.assertLess(html.index('value="M5"'), html.index('value="M15"'))

    def test_grafico_ofrece_las_mismas_divisas_y_temporalidades(self):
        html = self.client.get(reverse("chartview:grafico")).content.decode()
        self.assertIn('<option value="USDMXN"></option>', html)
        self.assertNotIn('<option value="GBPUSD"></option>', html)
        self.assertIn('<option value="M5">M5</option>', html)
        self.assertNotIn('<option value="W1">W1</option>', html)

    def test_grafico_sin_seleccion_usa_la_lista_fija(self):
        DivisaSeguida.objects.all().delete()
        TemporalidadSeguida.objects.all().delete()
        html = self.client.get(reverse("chartview:grafico")).content.decode()
        self.assertIn('<option value="GBPUSD"></option>', html)
        self.assertIn('<option value="W1">W1</option>', html)

    @mock.patch("dashboard.views.refrescar_tablero_usuario")
    def test_guardar_descarta_temporalidades_que_mt5_no_tiene(self, _tarea):
        self.client.post(reverse("dashboard:guardar_watchlist"),
                         {"simbolos": ["EURUSD"], "timeframes": ["H4", "H5", "m5"]},
                         content_type="application/json")
        tfs = set(TemporalidadSeguida.objects.filter(usuario=self.user).values_list("timeframe", flat=True))
        self.assertEqual(tfs, {"H4", "M5"})


class ApiWatchlistGraficoTests(TestCase):
    """El gráfico lee la selección del tablero por esta ruta para
    actualizarse sin recargar cuando se agrega una divisa/temporalidad."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("u", password="x")
        self.client.force_login(self.user)

    def test_devuelve_lo_que_sigue_el_tablero_ordenado(self):
        for i, s in enumerate(["USDMXN", "EURUSD"]):
            DivisaSeguida.objects.create(usuario=self.user, simbolo=s, orden=i)
        for i, t in enumerate(["H4", "M5", "D1"]):
            TemporalidadSeguida.objects.create(usuario=self.user, timeframe=t, orden=i)
        r = self.client.get(reverse("chartview:api_watchlist"))
        self.assertEqual(r.json(), {"divisas": ["USDMXN", "EURUSD"], "timeframes": ["M5", "H4", "D1"]})
        self.assertIn("no-store", r["Cache-Control"])

    def test_no_mezcla_datos_de_otro_usuario(self):
        otro = get_user_model().objects.create_user("otro", password="x")
        DivisaSeguida.objects.create(usuario=otro, simbolo="GBPUSD", orden=0)
        r = self.client.get(reverse("chartview:api_watchlist"))
        self.assertEqual(r.json()["divisas"], [])

    def test_requiere_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("chartview:api_watchlist")).status_code, 302)

    def test_grafico_incluye_el_codigo_de_sincronizacion(self):
        html = self.client.get(reverse("chartview:grafico")).content.decode()
        self.assertIn("sincronizarWatchlist", html)
        self.assertIn(reverse("chartview:api_watchlist"), html)
