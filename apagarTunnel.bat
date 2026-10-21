@echo off
REM ============================================================
REM  Tablero de Canales - apagar todo de un clic
REM  Cierra las ventanas abiertas por arrancarTunnel.bat (servidor,
REM  worker, beat y tunel), identificandolas por su titulo.
REM ============================================================

echo Apagando el Tablero de Canales...

taskkill /FI "WINDOWTITLE eq Django - servidor web*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Celery - worker*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Celery - beat*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Tunel - devtunnel*" /T /F >nul 2>&1

echo Listo.
pause
