param(
    [ValidateSet("branch-saveload", "branch-offline")]
    [string]$Mode = "branch-offline",
    [string]$GameRoot,
    [switch]$DualArchive
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")
$config = Resolve-Fifa14Paths -GameRoot $GameRoot -RequireExe:$false
$GameRoot = $config.GameRoot
$projectDir = Get-ProjectRoot
$python = Resolve-ProjectPython
$patcher = Join-Path $PSScriptRoot "patch_fifa14_fut_spinner.py"
$recovery = Join-Path $PSScriptRoot "recover_fifa14_fut_frontend.ps1"
$stateDir = Join-Path $projectDir "artifacts\fut-spinner-bypass"
$archives = if ($DualArchive) { "patch,data0" } else { "patch" }

if (Get-Process fifa14 -ErrorAction SilentlyContinue) {
    throw "Close FIFA before patching the frontend archives."
}

& $recovery -GameRoot $GameRoot -AllowMissingBackup
& $python $patcher --game-root $GameRoot --state-dir $stateDir --archives $archives --apply $Mode
if ($LASTEXITCODE -ne 0) { throw "The branch-only FUT spinner patcher failed." }
& $python $patcher --game-root $GameRoot --archives $archives --verify
if ($LASTEXITCODE -ne 0) { throw "The branch-only FUT spinner verification failed." }

Write-Host "Applied branch-only FUT mode: $Mode"
Write-Host "Patched archive set: $archives"
