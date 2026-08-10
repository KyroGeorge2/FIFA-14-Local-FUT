param(
    [string]$GameRoot = "",
    [string]$Vp6Source = "",
    [switch]$NoDownload
)
$ErrorActionPreference = "Stop"
$toolsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $toolsDir
if ([string]::IsNullOrWhiteSpace($GameRoot)) { $GameRoot = $projectDir }
$GameRoot = [IO.Path]::GetFullPath($GameRoot)
$resolver = Join-Path $toolsDir "resolve_fifa14_python.ps1"
$installer = Join-Path $toolsDir "install_fifa14_fut_intro_vp6_v19.py"
. $resolver
$runtime = Resolve-FifaPython -ProjectDir $projectDir
$stateDir = Join-Path $projectDir "artifacts\fut-intro-vp6-v19"
$args = @($runtime.Prefix) + @($installer, "--game-root", $GameRoot, "--state-dir", $stateDir, "--install")
if (-not [string]::IsNullOrWhiteSpace($Vp6Source)) { $args += @("--vp6-source", [IO.Path]::GetFullPath($Vp6Source)) }
if ($NoDownload) { $args += "--no-download" }
& $runtime.FilePath @args
if ($LASTEXITCODE -ne 0) {
    throw "Could not install a valid retail captain intro VP6. Pass -Vp6Source with an existing EA/FIFA .vp6 file if automatic discovery and download both fail."
}
