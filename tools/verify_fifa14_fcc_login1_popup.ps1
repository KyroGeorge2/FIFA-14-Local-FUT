param([string]$GameRoot)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")
$config = Resolve-Fifa14Paths -GameRoot $GameRoot -RequireExe:$false
$GameRoot = $config.GameRoot
$python = Resolve-ProjectPython
$stateDir = Join-Path (Get-ProjectRoot) "artifacts\fcc-login1-popup-bypass"
& $python (Join-Path $PSScriptRoot "patch_fifa14_fcc_login1_popup.py") --game-root $GameRoot --state-dir $stateDir --verify
if ($LASTEXITCODE -ne 0) { throw "fcc_login1 popup-bypass verification failed." }
