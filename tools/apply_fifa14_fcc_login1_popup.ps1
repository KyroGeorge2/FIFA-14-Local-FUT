param([string]$GameRoot)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")
$config = Resolve-Fifa14Paths -GameRoot $GameRoot -RequireExe:$false
$GameRoot = $config.GameRoot
$projectDir = Get-ProjectRoot
$python = Resolve-ProjectPython
$patcher = Join-Path $PSScriptRoot "patch_fifa14_fcc_login1_popup.py"
$stateDir = Join-Path $projectDir "artifacts\fcc-login1-popup-bypass"
if (Get-Process fifa14 -ErrorAction SilentlyContinue) { throw "Close FIFA before patching the fcc_login1 APT package." }
if (-not (Test-Path -LiteralPath $patcher)) { throw "Popup-bypass patcher not found: $patcher" }
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
& $python $patcher --game-root $GameRoot --state-dir $stateDir --apply
if ($LASTEXITCODE -ne 0) { throw "The one-byte fcc_login1 Loading-popup bypass failed. See artifacts\fcc-login1-popup-bypass\fcc-login1-popup-bypass-scan.json for the exact cards0 record verification failure." }
Write-Host "Applied the verified one-byte fcc_login1 Loading-popup bypass."
Write-Host "Rollback data: $stateDir"
