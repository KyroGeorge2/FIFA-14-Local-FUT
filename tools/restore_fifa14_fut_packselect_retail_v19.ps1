param([string]$GameRoot = "", [switch]$AllowUnknown)
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

if ($AllowUnknown) {
    $temp = Join-Path $env:TEMP ("fifa14-v19-pre-restore-{0}.json" -f [Guid]::NewGuid().ToString("N"))
    try {
        $inspectArgs = @($runtime.Prefix) + @($recovery, "--game-root", $GameRoot, "--state-dir", $stateDir, "--inspect")
        & $runtime.FilePath @inspectArgs | Set-Content -LiteralPath $temp -Encoding UTF8
        if ($LASTEXITCODE -ne 0) { throw "Could not inspect futPackSelect before recovery." }
        $before = Get-Content -LiteralPath $temp -Raw | ConvertFrom-Json
        if ($before.install.status -eq "unknown") {
            Write-Warning ("Unrecognised futPackSelect package detected (APT " + $before.install.apt_sha256 + "). Leaving it untouched instead of aborting startup.")
            Write-Warning "If FUT pack selection itself is broken, repair/verify FIFA 14 in EA/Origin and run again."
            return
        }
    } finally {
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    }
}

$args = @($runtime.Prefix) + @($recovery, "--game-root", $GameRoot, "--state-dir", $stateDir, "--restore")
& $runtime.FilePath @args
if ($LASTEXITCODE -ne 0) { throw "Could not recover the exact retail futPackSelect package." }
