param(
    [string]$GameRoot,
    [string]$GameExe,
    [switch]$SkipDependencies
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")
$projectRoot = Get-ProjectRoot

# One-time path setup. Explicit arguments win; otherwise config/environment and
# common EA/Origin/Steam library paths are checked before asking the user once.
$config = Resolve-Fifa14Paths -GameRoot $GameRoot -GameExe $GameExe -PromptIfMissing -PersistDetected
$GameRoot = $config.GameRoot
$GameExe = $config.GameExe

foreach ($required in @("cards0.big", "cards0.bh", "patch.big", "patch.bh", "data1.big", "data1.bh")) {
    $path = Join-Path $GameRoot $required
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required FIFA archive not found: $path"
    }
}

$configPath = Save-Fifa14Config -GameRoot $GameRoot
Write-Host ("FIFA 14 Game directory: " + $GameRoot) -ForegroundColor Green
Write-Host ("Wrote local configuration: " + $configPath)

if (-not $SkipDependencies) {
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
}

Write-Host ""
Write-Host "Setup complete. Launch with RUN_FIFA14_LOCAL_BETA.cmd."
