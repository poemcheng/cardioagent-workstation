param([switch]$PullModel, [string]$Model="qwen3.5-4b-FLM")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = "1"
if (!(Test-Path ".venv\Scripts\python.exe")) {
    if (Test-Path "wheelhouse\numpy-*-win_amd64.whl") {
        & .\setup_windows.ps1 -Offline
    } else {
        & .\setup_windows.ps1
    }
}
if (!(Test-Path ".venv\Scripts\python.exe")) { throw "Python setup did not finish." }
& .\.venv\Scripts\python.exe scripts\prepare_aipc_data.py
if ($LASTEXITCODE -ne 0) { throw "Dataset verification failed." }
if ($PullModel) {
    if (!(Get-Command lemonade -ErrorAction SilentlyContinue)) {
        throw "Install Lemonade from https://lemonade-server.ai and enable its FLM backend, then rerun this command."
    }
    & lemonade pull $Model
    if ($LASTEXITCODE -ne 0) { throw "Model download failed. Check the backend, available disk/RAM and current official model catalog." }
}
& .\.venv\Scripts\python.exe scripts\aipc_preflight.py --configure --model $Model --output evidence\aipc_preflight.json
if ($LASTEXITCODE -ne 0) { throw "Baseline data is not ready. Read the preflight JSON." }
Write-Host "Run start_aipc_windows.cmd. Model readiness and NPU execution evidence are separate checks."
Write-Host "No BIOS, power policy or GPU/NPU driver settings have been changed."
