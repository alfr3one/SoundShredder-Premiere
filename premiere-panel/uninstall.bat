@echo off
rem Remove the SoundShredder panel from Premiere Pro (Windows). The SoundShredder folder is kept.
setlocal
chcp 65001 >nul
set "EXTENSIONS=%APPDATA%\Adobe\CEP\extensions"
dir /AL /B "%EXTENSIONS%" 2>nul | findstr /X /I /C:"com.soundshredder.premiere" >nul || goto missing
rmdir "%EXTENSIONS%\com.soundshredder.premiere"
echo Removed the SoundShredder panel. Restart Premiere Pro.
pause
exit /b 0

:missing
echo The SoundShredder panel is not installed.
pause
exit /b 0
