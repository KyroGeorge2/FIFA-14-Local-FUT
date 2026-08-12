param([string]$GameRoot = "", [switch]$AllowUnknown)
$ErrorActionPreference = "Stop"
$toolsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $toolsDir
if ([string]::IsNullOrWhiteSpace($GameRoot)) { $GameRoot = $projectDir }
$GameRoot = [IO.Path]::GetFullPath($GameRoot)
$resolver = Join-Path $toolsDir "resolve_fifa14_python.ps1"
$recovery = Join-Path $toolsDir "patch_fifa14_fut_packselect_force_dock_ready_v18.py"
. $resolver
$runtime = Resolve-FifaPython -ProjectDir $projectDir
$stateDir = Join-Path $projectDir "artifacts\fut-packselect-retail-recovery-v19"
$temp = Join-Path $env:TEMP ("fifa14-v19-retail-inspect-{0}.json" -f [Guid]::NewGuid().ToString("N"))
try {
    $args = @($runtime.Prefix) + @($recovery, "--game-root", $GameRoot, "--state-dir", $stateDir, "--inspect")
    & $runtime.FilePath @args | Set-Content -LiteralPath $temp -Encoding UTF8
    $inspectExit = $LASTEXITCODE
    $inspectRaw = if (Test-Path -LiteralPath $temp) { Get-Content -LiteralPath $temp -Raw } else { "" }
    if ($inspectExit -ne 0) {
        if ($AllowUnknown) {
            $detail = "archive layout could not be identified"
            try {
                $inspectFailure = $inspectRaw | ConvertFrom-Json
                if ($inspectFailure.error) { $detail = [string]$inspectFailure.error }
            } catch { }
            Write-Warning ("Skipping exact-retail futPackSelect verification in unverified compatibility mode: " + $detail)
            Write-Warning "No unknown archive bytes were overwritten."
            return
        }
        throw "Retail futPackSelect inspection failed."
    }
    try {
        $result = $inspectRaw | ConvertFrom-Json
    } catch {
        if ($AllowUnknown) {
            Write-Warning "Skipping exact-retail futPackSelect verification because the inspection result could not be parsed. No unknown archive bytes were overwritten."
            return
        }
        throw
    }
    if ($result.install.status -eq "unknown" -and $AllowUnknown) {
        Write-Warning "Skipping exact-retail futPackSelect verification because this install uses an unrecognised package. No unknown archive bytes were overwritten."
        return
    }
    if ($result.install.status -ne "retail-original") {
        throw "futPackSelect is not exact retail. Current status: $($result.install.status)"
    }
    if ($result.install.apt_sha256 -ne "2dc0096194db202875642930a73cb8098c83376853583f3b115547fdc0d3150d") {
        throw "Retail APT hash mismatch: $($result.install.apt_sha256)"
    }
    Write-Host "Exact retail futPackSelect verified: $($result.install.apt_sha256)"
} finally {
    Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
}
