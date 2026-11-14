@echo off
rem Start SoundShredder on Windows. The first launch installs everything into this folder's .venv.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
title SoundShredder
echo.
echo   SOUNDSHREDDER / WINDOWS
echo   Dialogue. Music. Effects. Your mix.
echo.

call scripts\windows-python.bat
if errorlevel 1 goto nopython
if not exist ".venv\Scripts\python.exe" goto setup
%PY% setup_runtime.py --check >nul 2>&1 && goto run

:setup
echo Setting up SoundShredder. Internet is needed on first launch and it can take several minutes.
echo An NVIDIA GPU is used when one is found; otherwise the CPU.
echo.
%PY% setup_runtime.py --device auto || goto setupfailed

:run
echo.
echo Opening http://127.0.0.1:7860 in your browser.
echo Keep this window open. Press Ctrl+C to stop SoundShredder.
echo.
".venv\Scripts\python.exe" app.py || goto runfailed
exit /b 0

:nopython
echo Install Python 3.12 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer, then open this file again.
pause
exit /b 1

:setupfailed
echo.
echo Setup did not finish. Your files have been kept; open this file again to retry.
pause
exit /b 1

:runfailed
echo.
echo SoundShredder stopped with an error. See the message above.
pause
exit /b 1
