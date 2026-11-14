@echo off
rem Install the SoundShredder panel for Premiere Pro (Windows). Double-click it once, then restart
rem Premiere Pro and open Window > Extensions > SoundShredder. Run it again after moving the folder.
setlocal
chcp 65001 >nul
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"
set "EXTENSIONS=%APPDATA%\Adobe\CEP\extensions"
set "TARGET=%EXTENSIONS%\com.soundshredder.premiere"
if not exist "%EXTENSIONS%" mkdir "%EXTENSIONS%"

rem A link left by an earlier install is replaced; a real folder is left alone.
dir /AL /B "%EXTENSIONS%" 2>nul | findstr /X /I /C:"com.soundshredder.premiere" >nul && rmdir "%TARGET%"
if exist "%TARGET%" goto occupied

rem A junction, not a copy: updating SoundShredder updates the panel too. No administrator rights needed.
mklink /J "%TARGET%" "%HERE%" >nul || goto linkfailed

rem The panel is not signed by Adobe, so Premiere must be allowed to load unsigned panels.
for %%V in (9 10 11 12 13) do reg add "HKCU\Software\Adobe\CSXS.%%V" /v PlayerDebugMode /t REG_SZ /d 1 /f >nul

echo Installed the SoundShredder panel.
echo Restart Premiere Pro, then open Window ^> Extensions ^> SoundShredder.
pause
exit /b 0

:occupied
echo Remove the existing folder first: %TARGET%
pause
exit /b 1

:linkfailed
echo Could not link the panel into %EXTENSIONS%.
pause
exit /b 1
