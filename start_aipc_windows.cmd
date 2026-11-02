@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" goto install
".venv\Scripts\python.exe" -c "import flask, waitress, numpy, PIL, pydicom" >nul 2>&1
if errorlevel 1 goto install
goto ready
:install
if not exist "wheelhouse\numpy-*-win_amd64.whl" goto onlineinstall
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1" -Offline
if errorlevel 1 goto failed
goto ready
:onlineinstall
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
if errorlevel 1 goto failed
:ready
".venv\Scripts\python.exe" scripts\prepare_demo_assets.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\prepare_aipc_data.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\aipc_preflight.py --output evidence\aipc_preflight.json
if errorlevel 1 goto failed
".venv\Scripts\python.exe" run_demo.py --workspace aipc --open-browser %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo AI PC demo could not start. Review the error above.
echo Python 3.11 x64 is required. Models and AMD drivers are installed separately.
pause
exit /b 1
