param(
    [string]$GameRoot,
    [string]$GameExe,
    [switch]$SkipDependencies
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $PSScriptRoot

# v2.40.21.1: fixed install path; setup is intentionally non-interactive.
$GameRoot = "C:\Program Files\EA Games\FIFA 14\Game"
$GameExe = Join-Path $GameRoot "fifa14.exe"
if (-not (Test-Path -LiteralPath $GameRoot -PathType Container)) {
    throw "Hard-coded FIFA 14 game directory not found: $GameRoot"
}
if (-not (Test-Path -LiteralPath $GameExe -PathType Leaf)) {
    throw "Hard-coded FIFA 14 executable not found: $GameExe"
}

foreach ($required in @("cards0.big", "cards0.bh", "patch.big", "patch.bh", "data1.big", "data1.bh")) {
    $path = Join-Path $GameRoot $required
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required FIFA archive not found: $path"
    }
}

function Escape-Psd1String([string]$Value) {
    return $Value.Replace("'", "''")
}

$configPath = Join-Path $projectRoot "config.local.psd1"
$configText = @"
@{
    GameRoot = '$(Escape-Psd1String $GameRoot)'
    GameExe  = '$(Escape-Psd1String $GameExe)'
}
"@
Set-Content -LiteralPath $configPath -Value $configText -Encoding UTF8
Write-Host "Wrote local configuration: $configPath"

if (-not $SkipDependencies) {
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
}

Write-Host ""
Write-Host "Setup complete. Launch the FIFA 14 FUT hub + store build with:"
Write-Host ".\RUN_FIFA14_HUB_STORE.cmd"
