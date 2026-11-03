@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" goto install
".venv\Scripts\python.exe" -c "import sys,struct,flask,waitress,numpy,PIL,pydicom; assert sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 goto install
goto ready
:install
if not exist "wheelhouse\numpy-*-win_amd64.whl" goto onlineinstall
echo Preparing local Python dependencies from bundled wheels. No model or driver download.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1" -Offline
if errorlevel 1 goto failed
goto ready
:onlineinstall
echo Installing Python dependencies from PyPI. No model or driver download.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
if errorlevel 1 goto failed
:ready
".venv\Scripts\python.exe" scripts\prepare_demo_assets.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\demo_preflight.py --output evidence\demo_preflight.json
if errorlevel 1 goto failed
".venv\Scripts\python.exe" run_demo.py --open-browser %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Demo could not start. Read the error above. Python 3.11 x64 and the py launcher must be installed.
echo This script does not download Python, models, GPU/NPU drivers, or AMD runtimes.
pause
exit /b 1
