@echo off
setlocal
python -m playwright install chromium
if errorlevel 1 goto :erro
if not exist "%LOCALAPPDATA%\ms-playwright" goto :erro
pyinstaller --clean --noconfirm AuvoPlaywrightServer.spec
if errorlevel 1 goto :erro
echo.
echo EXE gerado em dist\AuvoPlaywrightServer.exe
goto :fim
:erro
echo.
echo Falha na compilacao.
:fim
pause
