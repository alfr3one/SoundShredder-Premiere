@echo off
rem Install (or switch to) the smaller CPU-only runtime. Works on any Windows PC.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
call scripts\windows-python.bat
if errorlevel 1 goto nopython
echo Installing the CPU runtime for SoundShredder. Internet is needed.
echo.
%PY% setup_runtime.py --device cpu || goto failed
pause
exit /b 0

:nopython
echo Install Python 3.12 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer, then open this file again.
pause
exit /b 1

:failed
echo.
echo Setup did not finish. Check the message above and your internet connection, then retry.
pause
exit /b 1
