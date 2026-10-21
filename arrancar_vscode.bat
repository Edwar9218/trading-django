@echo off
REM ============================================================
REM  Tablero de Canales - arranque dentro de VS Code
REM  Abre PRIMERO la pantalla de bienvenida en el navegador
REM  (cargando.html) y luego abre esta carpeta en VS Code, donde
REM  .vscode\tasks.json levanta solo el servidor Django, el worker
REM  y el beat de Celery en tres terminales integradas.
REM
REM  Es una alternativa a arrancar.bat (que abre 3 ventanas CMD);
REM  no lo reemplaza. No usar los dos a la vez: los puertos y la
REM  cola de Celery se pisarian.
REM
REM  Ubicar este archivo en la RAIZ del proyecto (junto a
REM  manage.py) y hacerle doble clic.
REM
REM  Para APAGAR: en VS Code, Terminal > Terminar tarea... > todas,
REM  o cerrar VS Code. (apagar.bat NO sirve aca: busca ventanas CMD
REM  por titulo y estos procesos viven dentro del editor.)
REM
REM  Nota tecnica: se lanza Code.exe DIRECTAMENTE y no el comando
REM  "code". Ese comando es un code.cmd que calcula donde esta
REM  Code.exe a partir de su propia ruta; invocado con
REM  call "code" entre comillas recibe la carpeta actual en su
REM  lugar y falla con "..\Code.exe no se reconoce".
REM ============================================================

setlocal
cd /d %~dp0

if not exist "venv\Scripts\python.exe" (
    echo No se encontro venv\Scripts\python.exe en esta carpeta.
    echo Este .bat tiene que estar en la raiz del proyecto ^(junto a manage.py^).
    pause
    exit /b 1
)

REM Buscar Code.exe. 1) a partir de "code" en el PATH: code.cmd vive en
REM ...\bin y Code.exe una carpeta mas arriba. 2) rutas de instalacion por defecto.
set "VSCODE="
for /f "delims=" %%i in ('where code.cmd 2^>nul') do if not defined VSCODE if exist "%%~dpi..\Code.exe" set "VSCODE=%%~dpi..\Code.exe"
if not defined VSCODE if exist "%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe" set "VSCODE=%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"
if not defined VSCODE if exist "%ProgramFiles%\Microsoft VS Code\Code.exe" set "VSCODE=%ProgramFiles%\Microsoft VS Code\Code.exe"

if not defined VSCODE (
    echo No se encontro VS Code ^(Code.exe^).
    echo Instalalo, o abrelo una vez a mano y vuelve a intentar.
    pause
    exit /b 1
)

echo Iniciando Tablero de Canales en VS Code...
echo Usando: %VSCODE%
echo.

REM Pantalla de bienvenida: se abre ya, sin esperar al servidor
if exist "%~dp0cargando.html" (
    start "" "%~dp0cargando.html"
) else (
    start "" "http://localhost:8000/"
)

REM Abre la carpeta del proyecto; tasks.json (runOn: folderOpen) levanta los
REM 3 procesos. Se pasa %CD% (sin barra final): una ruta entre comillas que
REM termina en "\" se interpreta mal.
start "" "%VSCODE%" "%CD%"

echo.
echo Se pidio abrir VS Code con el proyecto; las 3 terminales arrancan solas.
echo Si es la primera vez, VS Code pregunta "Permitir tareas automaticas": acepta.
echo El navegador entrara solo al tablero cuando el servidor este listo.
echo Esta ventana ya se puede cerrar.
echo.
pause
