param([switch]$PullModel, [string]$Model="Phi-4-mini-instruct-GGUF")
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
        throw "Install Lemonade from https://lemonade-server.ai and enable the matching model backend, then rerun this command."
    }
    & lemonade pull $Model
    if ($LASTEXITCODE -ne 0) { throw "Model download failed. Check the backend, available disk/RAM and current official model catalog." }
}
& .\.venv\Scripts\python.exe scripts\aipc_preflight.py --configure --model $Model --output evidence\aipc_preflight.json
if ($LASTEXITCODE -ne 0) { throw "Baseline data is not ready. Read the preflight JSON." }
if ($PullModel) {
    $preflight = Get-Content -Raw -Encoding UTF8 "evidence\aipc_preflight.json" | ConvertFrom-Json
    if (!$preflight.local_llm_ready) { throw "Data is ready, but the requested local model is not connected. Start Lemonade and check the selected model/backend." }
}
Write-Host "Run start_aipc_windows.cmd. Model readiness and NPU execution evidence are separate checks."
Write-Host "No BIOS, power policy or GPU/NPU driver settings have been changed."
