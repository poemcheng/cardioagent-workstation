param([switch]$WithVision, [switch]$Offline, [string]$WheelDir="wheelhouse")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& py -3.11 --version
if ($LASTEXITCODE -ne 0) { throw "Install Python 3.11 x64 first, including the Python launcher." }
if (!(Test-Path ".venv\Scripts\python.exe")) {
    & py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Cannot create virtual environment" }
}
$req = if ($WithVision) { "requirements-vision.txt" } else { "requirements.txt" }
if ($Offline) {
    & .\.venv\Scripts\python.exe -m pip install --no-index --find-links $WheelDir -r $req
} else {
    & .\.venv\Scripts\python.exe -m pip install -r $req
}
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
& .\.venv\Scripts\python.exe scripts\doctor.py --output evidence\target_doctor.json
Write-Host "Setup finished. Run start_windows.cmd to open http://127.0.0.1:8765"
