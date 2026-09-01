# Tablero de Canales — proyecto Django

Migración del proyecto Flask a Django, con:
- Login multi-usuario (cada quien con su watchlist, dibujos y snapshots)
- Tablero S-P-N como pantalla principal, recalculado cada 5 min en
  segundo plano vía Celery (el navegador solo LEE snapshots, nunca
  recalcula al cargar la página — por eso "va más rápido")
- Persistencia de dibujos (líneas horizontales soporte/resistencia) por
  usuario + divisa + temporalidad
- Atajo: click en una fila del tablero abre el gráfico de esa divisa/
  temporalidad/fecha ya cargado

## Cómo correrlo (Windows, con MT5 instalado y abierto)

```bat
pip install -r requirements.txt

:: 1. Copiar la plantilla de variables de entorno
copy .env.example .env
:: (opcional pero recomendado: generar tu propia SECRET_KEY y pegarla en .env)
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

:: 2. Migraciones + superusuario
python manage.py migrate
python manage.py createsuperuser

:: 3. Tres procesos, cada uno en su propia terminal:
python manage.py runserver
celery -A config worker -l info --pool=solo
celery -A config beat -l info
```

**Sobre Redis**: por defecto, Celery usa un broker de **filesystem** (carpetas
`broker/in`, `broker/out`, `broker/processed`) — no hace falta instalar
Redis ni Docker para correr esto en una sola PC. Si más adelante se
necesita algo más robusto (varios workers, otra máquina), basta con
cambiar `CELERY_BROKER_URL` en `.env` a `redis://localhost:6379/0` y
tener Redis corriendo (nativo, WSL, o [Memurai](https://www.memurai.com/)
para Windows) — no hace falta tocar ningún otro archivo.

Entrar a http://localhost:8000/ — redirige a login. Después de loguearte,
el tablero es la pantalla principal.

## Estructura

- `core/engine/` — TODA la lógica de canales/Kalman/S-P-N, portada sin
  cambios de fórmula desde el proyecto Flask original.
- `accounts/` — login, registro, perfil de usuario.
- `dashboard/` — el tablero (watchlist + snapshots + tarea de Celery).
- `chartview/` — el gráfico detallado (equivalente al index.html viejo).
- `drawings/` — persistencia de líneas dibujadas por usuario.

## Qué es un prototipo funcional vs. producción

Este es un port funcional de punta a punta (probado: login, watchlist,
cálculo en Celery, snapshot, atajo al gráfico, guardado de dibujos — todo
end-to-end). Para producción real faltaría: SECRET_KEY fuera del código,
DEBUG=False + ALLOWED_HOSTS, HTTPS, y llevar las herramientas de dibujo
del gráfico (ahora solo línea horizontal) a la paridad completa con el
editor de canvas original (trendline/fibo/brush/texto con handles).
