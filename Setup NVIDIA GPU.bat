@echo off
rem Install (or switch to) the NVIDIA GPU runtime: PyTorch with CUDA 12.8. The download is about 3.2 GB.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
call scripts\windows-python.bat
if errorlevel 1 goto nopython
echo Installing the NVIDIA GPU runtime for SoundShredder. The download is about 3.2 GB.
echo.
%PY% setup_runtime.py --device cuda || goto failed
pause
exit /b 0

:nopython
echo Install Python 3.12 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer, then open this file again.
pause
exit /b 1

:failed
echo.
echo Setup did not finish. Check the message above, your NVIDIA driver and your internet connection, then retry.
pause
exit /b 1
