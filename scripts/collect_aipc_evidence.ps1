param([string]$Output="evidence\aipc_hardware.json", [switch]$ValidateFLM)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$ErrorActionPreference = "Continue"
$cpu = @(Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors)
$os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber
$ram = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
$gpu = @(Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion)
$npu = @(Get-CimInstance Win32_PnPSignedDriver | Where-Object {$_.DeviceName -match "NPU|IPU|Ryzen AI|AMD.*Neural"} | Select-Object DeviceName, DriverVersion, DriverProviderName)
$evidence = [ordered]@{
    schema="cardio-aipc-hardware/1"; collected_at=(Get-Date).ToUniversalTime().ToString("o"); cpu=$cpu; os=$os;
    physical_memory_bytes=$ram; display_controllers=$gpu; candidate_npu_drivers=$npu;
    device_execution_verified=$false; note="Driver presence and model recipe do not prove request-level NPU execution."
}
$directory = Split-Path $Output -Parent
if (!$directory) { $directory = "." }
New-Item -ItemType Directory -Force $directory | Out-Null
$evidence | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $Output
if (Get-Command lemonade -ErrorAction SilentlyContinue) {
    & lemonade --version 2>&1 | Out-File -Encoding utf8 (Join-Path $directory "lemonade_version.txt")
    & lemonade list --downloaded 2>&1 | Out-File -Encoding utf8 (Join-Path $directory "lemonade_models.txt")
    & lemonade status 2>&1 | Out-File -Encoding utf8 (Join-Path $directory "lemonade_status.txt")
}
if ($ValidateFLM -and (Get-Command flm -ErrorAction SilentlyContinue)) {
    & flm validate 2>&1 | Out-File -Encoding utf8 (Join-Path $directory "flm_validate.txt")
}
Write-Host "Saved read-only hardware/driver evidence. Also capture runtime device logs and Task Manager NPU activity during a real request."
