$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location $projectDir
$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Local Python environment is missing; creating it now."
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
}
$persistentDir = Join-Path $env:LOCALAPPDATA "FIFA14LocalFUTBeta"
$db = Join-Path $persistentDir "local-fut-beta-v2410.sqlite3"
& $venvPython (Join-Path $PSScriptRoot "prepare_fifa14_beta_state.py") --database $db --test-coins 100000000
if ($LASTEXITCODE -ne 0) { throw "Could not apply optional 100M test-coin grant." }
Write-Host "Optional 100M developer/test float processed. This grant is one-time/idempotent." -ForegroundColor Green
