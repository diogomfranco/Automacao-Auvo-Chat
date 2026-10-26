@echo off
setlocal
pyinstaller --clean --noconfirm AuvoPlaywrightServer.spec
if errorlevel 1 goto :erro
echo.
echo EXE gerado em dist\AuvoPlaywrightServer.exe
echo O Microsoft Edge precisa estar instalado na maquina onde o servidor sera executado.
goto :fim
:erro
echo.
echo Falha na compilacao.
:fim
pause
