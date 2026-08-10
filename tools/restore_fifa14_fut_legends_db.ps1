param(
    [string]$GameRoot,
    [string]$GameExe
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
if (-not (Test-IsAdministrator)) {
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $PSCommandPath + '"')
    ) | Out-Null
    exit 0
}

$config = Resolve-Fifa14Paths -GameRoot $GameRoot -GameExe $GameExe
$GameRoot = $config.GameRoot
$projectDir = Get-ProjectRoot
$python = Resolve-ProjectPython
$patcher = Join-Path $PSScriptRoot "patch_fifa14_fut_legends_db.py"
$report = Join-Path $projectDir "artifacts\fifa14-legend-db-restore-v24022.json"
Write-Host "Restoring pre-Legend-injection FIFA archive backups used by the Legend DB injector..."
& $python $patcher --game-root $GameRoot --output-report $report --restore
if ($LASTEXITCODE -ne 0) { throw "Legend client-DB restore failed." }
Write-Host "Legend client-DB archive restore complete." -ForegroundColor Green
Write-Host "Run RUN_FIFA14_HUB_STORE.cmd afterward if you want the normal FUT runtime patches reapplied."
