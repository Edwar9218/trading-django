@echo off
REM ============================================================
REM  Tablero de Canales — apagar todo de un clic
REM  Cierra las 3 ventanas abiertas por arrancar.bat (servidor,
REM  worker, beat), identificándolas por su título.
REM ============================================================

echo Apagando el Tablero de Canales...

taskkill /FI "WINDOWTITLE eq Django - servidor web*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Celery - worker*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Celery - beat*" /T /F >nul 2>&1

echo Listo.
pause
