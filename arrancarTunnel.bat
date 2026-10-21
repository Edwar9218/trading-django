@echo off
REM ============================================================
REM  Tablero de Canales - arranque automatico (ventanas CMD)
REM  Abre PRIMERO una pantalla de bienvenida en el navegador
REM  (cargando.html) y mientras tanto levanta 4 ventanas:
REM    1) Django - servidor web
REM    2) Celery - worker
REM    3) Celery - beat
REM    4) Tunel - devtunnel (acceso privado desde otro equipo)
REM  Cuando el servidor esta listo, la pantalla de bienvenida
REM  entra sola al tablero.
REM
REM  Ubicar este archivo en la RAIZ del proyecto (junto a
REM  manage.py) y hacerle doble clic.
REM
REM  Para APAGAR todo: ejecuta apagarTunnel.bat (o cerra las ventanas).
REM
REM  El tunel (ID tablero.use2) es PRIVADO: solo entra tu cuenta
REM  de GitHub. Si no hay CLI devtunnel, se omite y el resto
REM  arranca igual. Para NO usar tunel, borrar el bloque "Tunel"
REM  de mas abajo.
REM ============================================================

setlocal
cd /d %~dp0

if not exist "venv\Scripts\activate.bat" (
    echo No se encontro venv\Scripts\activate.bat en esta carpeta.
    echo Este .bat tiene que estar en la raiz del proyecto ^(junto a manage.py^).
    pause
    exit /b 1
)

REM Tunel: al abrir un .bat con doble clic, Windows puede pasar un PATH viejo
REM (sin lo agregado despues de iniciar sesion). Se suma C:\devtunnel al PATH de
REM este proceso; las ventanas que se abren abajo lo heredan.
set "TUNEL=0"
if exist "C:\devtunnel\devtunnel.exe" set "PATH=%PATH%;C:\devtunnel"
where devtunnel >nul 2>&1
if not errorlevel 1 set "TUNEL=1"

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
timeout /t 1 /nobreak >nul

REM Tunel (devtunnel). NUNCA agregar --allow-anonymous: lo dejaria publico.
if "%TUNEL%"=="1" (
    start "Tunel - devtunnel" cmd /k "cd /d %~dp0 && devtunnel host tablero.use2"
)

echo.
if "%TUNEL%"=="1" (
    echo Listo - se abrieron 4 ventanas ^(servidor, worker, beat, tunel^) y el navegador.
    echo El link del tunel aparece en la ventana "Tunel - devtunnel".
) else (
    echo Listo - se abrieron 3 ventanas ^(servidor, worker, beat^) y el navegador.
    echo AVISO: no se encontro "devtunnel", el tunel NO arranco. Instalalo en
    echo C:\devtunnel o agregalo al PATH si lo quieres usar.
)
echo El navegador entrara solo al tablero cuando todo este listo.
echo Para APAGAR todo: ejecuta apagarTunnel.bat (o cerra las ventanas).
echo Esta ventana ya se puede cerrar.
echo.
pause
