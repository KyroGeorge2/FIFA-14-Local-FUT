$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location $projectDir

# Ensure the small production runtime compatibility layer under server\ is
# available to every Python process launched from this entry point, including
# the elevated relaunch below. Verifier scripts opt themselves out of the
# runtime-only adapter inside sitecustomize.py.
$runtimeServerPath = Join-Path $projectDir "server"
$pythonPathParts = @()
if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $pythonPathParts = @($env:PYTHONPATH -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}
if ($pythonPathParts -notcontains $runtimeServerPath) {
    $env:PYTHONPATH = (($runtimeServerPath) + @($pythonPathParts)) -join ';'
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    Write-Host "Administrator rights are required for the local hosts/certificate setup and FIFA archive patching. Requesting elevation..."
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $PSCommandPath + '"')
    ) | Out-Null
    exit 0
}

Write-Host "============================================================"
Write-Host " FIFA 14 LOCAL FUT v2.41.1 BETA 2.25.9 ISSUE HOTFIX"
Write-Host "============================================================"
Write-Host " BETA 2.25.9 FRIEND: clean local profile, starter bronze club, no bundled user save"
Write-Host ""

. (Join-Path $PSScriptRoot "common.ps1")
$resolvedGame = Resolve-Fifa14Paths -PromptIfMissing -PersistDetected
$GameRoot = $resolvedGame.GameRoot
$GameExe = $resolvedGame.GameExe
Write-Host ("FIFA 14 Game directory: " + $GameRoot + " [" + $resolvedGame.Source + "]") -ForegroundColor Green

# Friend-PC prerequisite preflight. This is cheap when everything is already
# installed; when Python or Git/OpenSSL is missing it installs only what is
# needed, then the normal local-venv bootstrap below handles pip packages.
$prerequisiteInstaller = Join-Path $PSScriptRoot "install_prerequisites.ps1"
if (-not (Test-Path -LiteralPath $prerequisiteInstaller -PathType Leaf)) {
    throw "Prerequisite installer is missing: $prerequisiteInstaller"
}
Write-Host "Checking friend-PC prerequisites (Python + Git/OpenSSL)..." -ForegroundColor Cyan
& $prerequisiteInstaller -SkipVenvBootstrap
if ($LASTEXITCODE -ne 0) {
    throw "Prerequisite setup failed. Run INSTALL_PREREQUISITES.cmd directly for the full diagnostic output."
}
Write-Host "Friend-PC prerequisites ready." -ForegroundColor Green

# Any existing FIFA process would keep the retail APT/DB archives locked. This
# launcher owns the FIFA session, so close only fifa14.exe before patching. EA
# App and every other process are left untouched.
$staleFifa = @(Get-Process -Name "fifa14" -ErrorAction SilentlyContinue)
if ($staleFifa.Count -gt 0) {
    Write-Host ("Closing stale FIFA 14 process(es) before archive/database patching: " + (($staleFifa | ForEach-Object { $_.Id }) -join ", ")) -ForegroundColor Yellow
    $staleFifa | Stop-Process -Force -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 250
        $stillRunning = @(Get-Process -Name "fifa14" -ErrorAction SilentlyContinue)
    } while ($stillRunning.Count -gt 0 -and (Get-Date) -lt $deadline)
    if ($stillRunning.Count -gt 0) {
        throw "Could not close stale fifa14.exe before on-disk patching."
    }
    Write-Host "Stale FIFA 14 process closed. Continuing with a clean patch/launch cycle." -ForegroundColor Green
}


$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Local Python environment is missing; creating it now."
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
} else {
    & $venvPython -c "import frida, cryptography" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Local Python dependencies are incomplete; repairing them now."
        & (Join-Path $PSScriptRoot "bootstrap.ps1")
    }
}

& (Join-Path $PSScriptRoot "run_fifa14_local_beta.ps1") -GameRoot $GameRoot -GameExe $GameExe
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
