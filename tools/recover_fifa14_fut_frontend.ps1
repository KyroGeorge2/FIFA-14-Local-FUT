param(
    [string]$GameRoot,
    [switch]$AllowMissingBackup
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")
$config = Resolve-Fifa14Paths -GameRoot $GameRoot -RequireExe:$false
$GameRoot = $config.GameRoot
$projectDir = Get-ProjectRoot
$python = Resolve-ProjectPython
$patcher = Join-Path $PSScriptRoot "patch_fifa14_fut_spinner.py"
$stateDir = Join-Path $projectDir "artifacts\fut-spinner-bypass"

if (Get-Process fifa14 -ErrorAction SilentlyContinue) {
    throw "Close FIFA before restoring the frontend archives."
}

$originalDir = Join-Path $stateDir "original"
$archives = @()
foreach ($name in @("patch", "data0")) {
    if ((Test-Path (Join-Path $originalDir "$name.record.bin")) -and (Test-Path (Join-Path $originalDir "$name.bh.bin"))) {
        $archives += $name
    }
}
if ($archives.Count -eq 0) {
    if ($AllowMissingBackup) { return }
    throw "No targeted retail backups were found under $originalDir."
}

$archiveArg = $archives -join ","
& $python $patcher --game-root $GameRoot --state-dir $stateDir --archives $archiveArg --restore
if ($LASTEXITCODE -ne 0) { throw "Retail frontend recovery failed." }
& $python $patcher --game-root $GameRoot --state-dir $stateDir --archives $archiveArg --verify
if ($LASTEXITCODE -ne 0) { throw "Retail frontend verification failed after recovery." }
Write-Host "Recovered and verified retail helperFunctions archives: $archiveArg"
