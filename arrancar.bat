@echo off
REM ============================================================
REM  Tablero de Canales — arranque automático
REM  Abre 3 ventanas (servidor Django, worker de Celery, beat de
REM  Celery), cada una con el entorno virtual ya activado, y al
REM  final abre el navegador en el tablero.
REM
REM  Ubicar este archivo en la RAÍZ del proyecto (junto a
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

start "Django - servidor web" cmd /k "cd /d %~dp0 && call venv\Scripts\activate.bat && python manage.py runserver"
timeout /t 3 /nobreak >nul

start "Celery - worker" cmd /k "cd /d %~dp0 && call venv\Scripts\activate.bat && celery -A config worker -l info --pool=solo"
timeout /t 3 /nobreak >nul

start "Celery - beat" cmd /k "cd /d %~dp0 && call venv\Scripts\activate.bat && celery -A config beat -l info"
timeout /t 4 /nobreak >nul

start "" "http://localhost:8000/"

echo.
echo Listo — se abrieron 3 ventanas (servidor, worker, beat) y el navegador.
echo Para APAGAR todo: cerra las 3 ventanas que se abrieron (o Ctrl+C en cada una).
echo Esta ventana ya se puede cerrar.
echo.
pause
