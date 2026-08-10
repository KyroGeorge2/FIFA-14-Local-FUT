param(
    [switch]$Force,
    [switch]$IncludeDevDependencies
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvDir = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if ($Force -and (Test-Path -LiteralPath $venvDir)) {
    Remove-Item -LiteralPath $venvDir -Recurse -Force
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $pythonLauncher = $null
    $pythonPrefix = @()

    $py = Get-Command py -ErrorAction SilentlyContinue
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($py -and $py.Source -notlike "*\WindowsApps\*") {
        $pythonLauncher = $py.Source
        $pythonPrefix = @("-3")
    }
    elseif ($python -and $python.Source -notlike "*\WindowsApps\*") {
        $pythonLauncher = $python.Source
    }
    else {
        # An installer launched earlier in this same elevated PowerShell process
        # may not have refreshed PATH yet. Probe the standard CPython locations.
        $directCandidates = @(
            (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
            (Join-Path $env:ProgramFiles "Python313\python.exe"),
            (Join-Path $env:ProgramFiles "Python312\python.exe"),
            (Join-Path $env:ProgramFiles "Python311\python.exe")
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
        $pythonLauncher = $directCandidates | Select-Object -First 1
    }

    if (-not $pythonLauncher) {
        throw "Python 3.10+ was not found. Run INSTALL_PREREQUISITES.cmd and retry."
    }

    & $pythonLauncher @pythonPrefix -c "import sys; assert sys.version_info >= (3,10)"
    if ($LASTEXITCODE -ne 0) { throw "Python 3.10+ is required. Run INSTALL_PREREQUISITES.cmd and retry." }
    & $pythonLauncher @pythonPrefix -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the Python virtual environment." }
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip." }

$requirements = if ($IncludeDevDependencies) { "requirements-dev.txt" } else { "requirements.txt" }
& $venvPython -m pip install -r (Join-Path $projectRoot $requirements)
if ($LASTEXITCODE -ne 0) { throw "Failed to install $requirements." }

& $venvPython -c "import frida, cryptography; print('Environment ready:', __import__('sys').executable)"
if ($LASTEXITCODE -ne 0) { throw "Dependency import check failed." }
