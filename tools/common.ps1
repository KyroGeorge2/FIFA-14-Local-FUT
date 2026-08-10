Set-StrictMode -Version Latest

function Get-ProjectRoot {
    return (Split-Path -Parent $PSScriptRoot)
}

function Get-LocalConfig {
    $projectRoot = Get-ProjectRoot
    $configPath = Join-Path $projectRoot "config.local.psd1"
    if (Test-Path -LiteralPath $configPath) {
        return Import-PowerShellDataFile -LiteralPath $configPath
    }
    return @{}
}

function Resolve-Fifa14Paths {
    param(
        [string]$GameRoot,
        [string]$GameExe,
        [bool]$RequireExe = $true
    )

    # v2.40.21.1: hard-patched for this installation. Do not consult
    # config.local.psd1, environment variables, command-line overrides, or
    # interactive setup. Normal launches always use this exact FIFA 14 folder.
    $GameRoot = "C:\Program Files\EA Games\FIFA 14\Game"
    $GameExe = Join-Path $GameRoot "fifa14.exe"

    if (-not (Test-Path -LiteralPath $GameRoot -PathType Container)) {
        throw "Hard-coded FIFA 14 game directory does not exist: $GameRoot"
    }
    if ($RequireExe -and -not (Test-Path -LiteralPath $GameExe -PathType Leaf)) {
        throw "Hard-coded FIFA 14 executable does not exist: $GameExe"
    }

    return [pscustomobject]@{
        GameRoot = $GameRoot
        GameExe = $GameExe
    }
}

function Resolve-ProjectPython {
    $projectRoot = Get-ProjectRoot
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        return $venvPython
    }

    throw "Project Python environment is missing. Run .\tools\bootstrap.ps1 first."
}
