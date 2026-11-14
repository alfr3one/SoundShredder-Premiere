@echo off
rem Used by the Windows launchers: sets PY to a Python 3.10-3.13 command.
rem This folder's .venv comes first, then the py launcher, then python on PATH.
set "PY="
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
    exit /b 0
)
for %%V in (3.12 3.11 3.13 3.10) do (
    if not defined PY py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
)
if not defined PY (
    python -c "import sys; sys.exit(not ((3, 10) <= sys.version_info[:2] <= (3, 13)))" >nul 2>&1 && set "PY=python"
)
if not defined PY exit /b 1
exit /b 0
