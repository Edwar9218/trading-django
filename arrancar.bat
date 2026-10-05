@echo off
REM ============================================================
REM  Tablero de Canales - arranque automatico
REM  Abre PRIMERO una pantalla de bienvenida en el navegador
REM  (cargando.html) y mientras tanto levanta las 3 ventanas
REM  (servidor Django, worker de Celery, beat de Celery).
REM  Cuando el servidor esta listo, la pantalla de bienvenida
REM  entra sola al tablero.
REM
REM  Ubicar este archivo en la RAIZ del proyecto (junto a
REM  manage.py) y hacerle doble clic.
REM ============================================================

setlocal
cd /d %~dp0

if not exist "venv\Scripts\activate.bat" (
    echo No se encontro venv\Scripts\activate.bat en esta carpeta.
    echo Este .bat tiene que estar en la raiz del proyecto ^(junto a manage.py^).
    pause
    exit /b 1
)

echo Iniciando Tablero de Canales...
echo.

REM Pantalla de bienvenida: se abre ya, sin esperar al servidor
if exist "%~dp0cargando.html" (
    start "" "%~dp0cargando.html"
) else (
    start "" "http://localhost:8000/"
)

start "Django - servidor web" cmd /k "cd /d %~dp0 && call venv\Scripts\activate.bat && python manage.py runserver"
timeout /t 1 /nobreak >nul

start "Celery - worker" cmd /k "cd /d %~dp0 && call venv\Scripts\activate.bat && celery -A config worker -l info --pool=solo"
timeout /t 1 /nobreak >nul

start "Celery - beat" cmd /k "cd /d %~dp0 && call venv\Scripts\activate.bat && celery -A config beat -l info"

echo.
echo Listo - se abrieron 3 ventanas (servidor, worker, beat) y el navegador.
echo El navegador entrara solo al tablero cuando todo este listo.
echo Para APAGAR todo: ejecuta apagar.bat (o cerra las 3 ventanas).
echo Esta ventana ya se puede cerrar.
echo.
pause
