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

function Save-Fifa14Config {
    param([Parameter(Mandatory=$true)][string]$GameRoot)
    $projectRoot = Get-ProjectRoot
    $GameRoot = [IO.Path]::GetFullPath($GameRoot.Trim().Trim('"'))
    $GameExe = Join-Path $GameRoot "fifa14.exe"
    $escape = { param([string]$Value) $Value.Replace("'", "''") }
    $configPath = Join-Path $projectRoot "config.local.psd1"
    $rootEscaped = & $escape $GameRoot
    $exeEscaped = & $escape $GameExe
    $content = "@{`r`n    GameRoot = '$rootEscaped'`r`n    GameExe  = '$exeEscaped'`r`n}`r`n"
    Set-Content -LiteralPath $configPath -Value $content -Encoding UTF8
    return $configPath
}

function Get-Fifa14AutoDetectCandidates {
    $candidates = New-Object System.Collections.Generic.List[string]
    foreach ($fixed in @(
        "C:\Program Files\EA Games\FIFA 14\Game",
        "C:\Program Files (x86)\Origin Games\FIFA 14\Game",
        "C:\Program Files\Origin Games\FIFA 14\Game"
    )) { $candidates.Add($fixed) }

    foreach ($drive in [IO.DriveInfo]::GetDrives()) {
        if (-not $drive.IsReady) { continue }
        $root = $drive.RootDirectory.FullName
        foreach ($relative in @(
            "EA Games\FIFA 14\Game",
            "Games\FIFA 14\Game",
            "Origin Games\FIFA 14\Game",
            "SteamLibrary\steamapps\common\FIFA 14\Game",
            "Program Files\EA Games\FIFA 14\Game",
            "Program Files (x86)\Origin Games\FIFA 14\Game"
        )) { $candidates.Add((Join-Path $root $relative)) }
    }
    return @($candidates | Select-Object -Unique)
}

function Resolve-Fifa14Paths {
    param(
        [string]$GameRoot,
        [string]$GameExe,
        [bool]$RequireExe = $true,
        [switch]$PromptIfMissing,
        [switch]$PersistDetected
    )

    $config = Get-LocalConfig
    $source = "command line"

    if ([string]::IsNullOrWhiteSpace($GameRoot) -and -not [string]::IsNullOrWhiteSpace($GameExe)) {
        $GameRoot = Split-Path -Parent $GameExe
    }
    if ([string]::IsNullOrWhiteSpace($GameRoot) -and $config.ContainsKey("GameRoot")) {
        $GameRoot = [string]$config.GameRoot
        $source = "config.local.psd1"
    }
    if ([string]::IsNullOrWhiteSpace($GameExe) -and $config.ContainsKey("GameExe")) {
        $GameExe = [string]$config.GameExe
    }
    if ([string]::IsNullOrWhiteSpace($GameRoot) -and -not [string]::IsNullOrWhiteSpace($env:FIFA14_GAME_ROOT)) {
        $GameRoot = $env:FIFA14_GAME_ROOT
        $source = "FIFA14_GAME_ROOT"
    }

    if ([string]::IsNullOrWhiteSpace($GameRoot)) {
        foreach ($candidate in Get-Fifa14AutoDetectCandidates) {
            if (Test-Path -LiteralPath (Join-Path $candidate "fifa14.exe") -PathType Leaf) {
                $GameRoot = $candidate
                $source = "auto-detected"
                break
            }
        }
    }

    if ([string]::IsNullOrWhiteSpace($GameRoot) -and $PromptIfMissing) {
        Write-Host "FIFA 14 was not found automatically." -ForegroundColor Yellow
        Write-Host "Paste your FIFA 14 Game folder (the folder containing fifa14.exe)."
        $GameRoot = Read-Host "FIFA 14 Game folder"
        $source = "interactive setup"
    }

    if ([string]::IsNullOrWhiteSpace($GameRoot)) {
        throw "FIFA 14 Game folder was not found. Run .\tools\setup.ps1 once, create config.local.psd1 from config.local.psd1.example, or set FIFA14_GAME_ROOT."
    }

    $GameRoot = [IO.Path]::GetFullPath($GameRoot.Trim().Trim('"'))
    if ([string]::IsNullOrWhiteSpace($GameExe)) { $GameExe = Join-Path $GameRoot "fifa14.exe" }
    $GameExe = [IO.Path]::GetFullPath($GameExe.Trim().Trim('"'))

    if (-not (Test-Path -LiteralPath $GameRoot -PathType Container)) {
        throw "FIFA 14 Game folder does not exist: $GameRoot"
    }
    if ($RequireExe -and -not (Test-Path -LiteralPath $GameExe -PathType Leaf)) {
        throw "fifa14.exe was not found at: $GameExe"
    }

    if ($PersistDetected -and $source -in @("auto-detected", "interactive setup", "FIFA14_GAME_ROOT")) {
        $saved = Save-Fifa14Config -GameRoot $GameRoot
        Write-Host ("Saved FIFA 14 path for future launches: " + $saved) -ForegroundColor DarkGray
    }

    return [pscustomobject]@{ GameRoot=$GameRoot; GameExe=$GameExe; Source=$source }
}

function Resolve-ProjectPython {
    $projectRoot = Get-ProjectRoot
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        return $venvPython
    }

    throw "Project Python environment is missing. Run .\tools\bootstrap.ps1 first."
}
