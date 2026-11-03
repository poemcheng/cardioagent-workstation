@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" goto install
".venv\Scripts\python.exe" -c "import sys,struct,flask,waitress,numpy,PIL,pydicom; assert sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8" >nul 2>&1
if not errorlevel 1 goto ready
:install
if not exist "wheelhouse\numpy-*-win_amd64.whl" goto online
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1" -Offline
if errorlevel 1 goto failed
goto ready
:online
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
if errorlevel 1 goto failed
:ready
".venv\Scripts\python.exe" scripts\prepare_demo_assets.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\prepare_aipc_data.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\accept_aipc.py %*
if errorlevel 1 goto failed
echo.
echo Acceptance completed. Open the report path shown above.
echo Use --llm --model MODEL_ID to include real local model requests.
pause
exit /b 0
:failed
echo.
echo Acceptance has a blocked item. Read acceptance.json and index.html in the printed evidence folder.
echo Python 3.11 x64, Lemonade model readiness and device execution are separate checks.
pause
exit /b 1
