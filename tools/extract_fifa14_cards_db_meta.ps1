param(
    [string]$GameRoot,
    [string]$GameExe,
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$config = Resolve-Fifa14Paths -GameRoot $GameRoot -GameExe $GameExe
$projectDir = Get-ProjectRoot
$python = Resolve-ProjectPython
$extractor = Join-Path $PSScriptRoot "extract_fifa14_cards_db_meta.py"
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $projectDir "artifacts\cards-ng-db-descriptor"
}

& $python $extractor --game-root $config.GameRoot --output $OutputDirectory
if ($LASTEXITCODE -ne 0) {
    throw "FIFA 14 cards_ng_db descriptor extraction failed."
}