param([string]$GameRoot = "")
$ErrorActionPreference = "Stop"
$toolsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $toolsDir
if ([string]::IsNullOrWhiteSpace($GameRoot)) { $GameRoot = $projectDir }
$GameRoot = [IO.Path]::GetFullPath($GameRoot)
if (Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like "fifa14*" }) {
    throw "Close FIFA completely before restoring the retail futPackSelect package."
}
$resolver = Join-Path $toolsDir "resolve_fifa14_python.ps1"
$recovery = Join-Path $toolsDir "patch_fifa14_fut_packselect_force_dock_ready_v18.py"
foreach ($required in @($resolver, $recovery)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing v19 recovery dependency: $required" }
}
. $resolver
$runtime = Resolve-FifaPython -ProjectDir $projectDir
$stateDir = Join-Path $projectDir "artifacts\fut-packselect-retail-recovery-v19"
$args = @($runtime.Prefix) + @($recovery, "--game-root", $GameRoot, "--state-dir", $stateDir, "--restore")
& $runtime.FilePath @args
if ($LASTEXITCODE -ne 0) { throw "Could not recover the exact retail futPackSelect package." }
