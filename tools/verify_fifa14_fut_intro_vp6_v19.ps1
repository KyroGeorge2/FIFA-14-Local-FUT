param([string]$GameRoot = "")
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
$args = @($runtime.Prefix) + @($installer, "--game-root", $GameRoot, "--state-dir", $stateDir, "--verify")
& $runtime.FilePath @args
if ($LASTEXITCODE -ne 0) { throw "The retail captain intro VP6 verification failed." }
