param([switch]$WithVision, [switch]$Offline, [string]$WheelDir="wheelhouse")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& py -3.11 --version
if ($LASTEXITCODE -ne 0) { throw "Install Python 3.11 x64 first, including the Python launcher." }
& py -3.11 -c "import struct; assert struct.calcsize('P') == 8, 'Python x64 is required'"
if ($LASTEXITCODE -ne 0) { throw "Install Python 3.11 x64, not the 32-bit build." }
if (Test-Path "RELEASE_INFO.json") {
    & py -3.11 scripts\verify_package.py
    if ($LASTEXITCODE -ne 0) { throw "Release file verification failed. Extract a fresh copy before installing." }
}
if (!(Test-Path ".venv\Scripts\python.exe")) {
    & py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Cannot create virtual environment" }
}
& .\.venv\Scripts\python.exe -c "import sys,struct; assert sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8"
if ($LASTEXITCODE -ne 0) { throw "The existing .venv is not CPython 3.11 x64. Use a fresh release folder." }
$req = if ($WithVision) { "requirements-vision.txt" } else { "requirements.txt" }
if ($Offline) {
    & .\.venv\Scripts\python.exe -m pip install --no-index --find-links $WheelDir -r $req
} else {
    & .\.venv\Scripts\python.exe -m pip install -r $req
}
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
& .\.venv\Scripts\python.exe scripts\doctor.py --output evidence\target_doctor.json
if ($LASTEXITCODE -ne 0) { throw "Software inspection failed. Read evidence\target_doctor.json." }
Write-Host "Setup finished. Run start_demo_windows.cmd for imaging, or start_aipc_windows.cmd for clinical text."
