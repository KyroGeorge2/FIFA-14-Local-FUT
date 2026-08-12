param(
    [ValidateSet("branch-saveload", "branch-offline")]
    [string]$Mode = "branch-offline",
    [string]$GameRoot,
    [switch]$DualArchive,
    [switch]$AllowUnknown
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")
$config = Resolve-Fifa14Paths -GameRoot $GameRoot -RequireExe:$false
$GameRoot = $config.GameRoot
$projectDir = Get-ProjectRoot
$python = Resolve-ProjectPython
$patcher = Join-Path $PSScriptRoot "patch_fifa14_fut_spinner.py"
$recovery = Join-Path $PSScriptRoot "recover_fifa14_fut_frontend.ps1"
$stateDir = Join-Path $projectDir "artifacts\fut-spinner-bypass"

if (Get-Process fifa14 -ErrorAction SilentlyContinue) {
    throw "Close FIFA before patching the frontend archives."
}

& $recovery -GameRoot $GameRoot -AllowMissingBackup

function Invoke-SpinnerTool {
    param(
        [Parameter(Mandatory=$true)][string]$Archive,
        [Parameter(Mandatory=$true)][ValidateSet("apply", "verify")][string]$Operation
    )

    $arguments = @(
        $patcher,
        "--game-root", $GameRoot,
        "--state-dir", $stateDir,
        "--archives", $Archive
    )
    if ($Operation -eq "apply") {
        $arguments += @("--apply", $Mode)
    } else {
        $arguments += "--verify"
    }

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = @(& $python @arguments 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    [pscustomobject]@{
        Archive = $Archive
        Operation = $Operation
        ExitCode = $exitCode
        Output = $output
        Text = (($output | ForEach-Object { [string]$_ }) -join "`n")
    }
}

function Test-UnsupportedArchiveLayout {
    param([Parameter(Mandatory=$true)][string]$Text)

    $patterns = @(
        "does not contain record",
        "has .* records and no compatible helperFunctions payload was found",
        "helperFunctions record changed",
        "helperFunctions path hash changed",
        "helperFunctions stored payload mismatch",
        "helperFunctions decoded package mismatch",
        "unexpected helperFunctions APT layout",
        "APT verification failed",
        "payload is not chunkzip",
        "not a ViV4 BH index",
        "BH index is truncated",
        "not found under",
        "multiple compatible helperFunctions candidates",
        "already modified .* but no original backup exists"
    )
    foreach ($pattern in $patterns) {
        if ($Text -match $pattern) { return $true }
    }
    return $false
}

function Write-ToolDiagnostic {
    param([Parameter(Mandatory=$true)]$Result)
    foreach ($line in @($Result.Output)) {
        if (-not [string]::IsNullOrWhiteSpace([string]$line)) {
            Write-Host ([string]$line) -ForegroundColor DarkYellow
        }
    }
}

function Apply-And-VerifyArchive {
    param([Parameter(Mandatory=$true)][string]$Archive)

    $applyResult = Invoke-SpinnerTool -Archive $Archive -Operation "apply"
    if ($applyResult.ExitCode -ne 0) {
        return $applyResult
    }

    $verifyResult = Invoke-SpinnerTool -Archive $Archive -Operation "verify"
    if ($verifyResult.ExitCode -ne 0) {
        Write-ToolDiagnostic -Result $verifyResult
        throw "The branch-only FUT spinner verification failed for $Archive after a successful write."
    }

    # Keep successful JSON diagnostics available in the console for support logs.
    foreach ($line in @($applyResult.Output)) { Write-Host ([string]$line) }
    foreach ($line in @($verifyResult.Output)) { Write-Host ([string]$line) }
    return $applyResult
}

$requestedArchives = if ($DualArchive) { @("patch", "data0") } else { @("patch") }
$appliedArchives = @()
$unsupportedArchives = @()

foreach ($archive in $requestedArchives) {
    $result = Apply-And-VerifyArchive -Archive $archive
    if ($result.ExitCode -eq 0) {
        $appliedArchives += $archive
        continue
    }

    if (-not $AllowUnknown -or -not (Test-UnsupportedArchiveLayout -Text $result.Text)) {
        Write-ToolDiagnostic -Result $result
        throw "The branch-only FUT spinner patcher failed for $archive."
    }

    Write-Warning ("The " + $archive + " archive is not a reviewed helperFunctions layout for this install. The patcher will not write guessed offsets.")
    Write-ToolDiagnostic -Result $result
    $unsupportedArchives += $archive
}

# Public compatibility fallback: older/smaller title-update patch.bh files may
# not carry helperFunctions at all. In that case the game can inherit the base
# helperFunctions package from data0. Try that exact content-verified package
# before giving up. This is deliberately not a blind byte patch.
if ($AllowUnknown -and -not $DualArchive -and $appliedArchives.Count -eq 0) {
    Write-Warning "patch.big/patch.bh did not expose a compatible helperFunctions package. Trying the base data0 archive by verified content."
    $fallback = Apply-And-VerifyArchive -Archive "data0"
    if ($fallback.ExitCode -eq 0) {
        $appliedArchives += "data0"
        Write-Host "Compatibility fallback succeeded: branch-only FUT mode was applied to data0." -ForegroundColor Green
    } elseif (Test-UnsupportedArchiveLayout -Text $fallback.Text) {
        Write-Warning "Neither patch nor data0 contains a uniquely verified helperFunctions package that this build can patch safely."
        Write-ToolDiagnostic -Result $fallback
        $unsupportedArchives += "data0"
    } else {
        Write-ToolDiagnostic -Result $fallback
        throw "The branch-only FUT spinner data0 compatibility fallback failed unexpectedly."
    }
}

if ($appliedArchives.Count -eq 0) {
    if ($AllowUnknown) {
        Write-Warning "Skipping the branch-only FUT spinner archive patch for this unverified installation. Startup will continue; no unknown archive bytes were overwritten."
        $compatibilityState = [ordered]@{
            schema = 1
            status = "skipped-unsupported-layout"
            mode = $Mode
            gameRoot = $GameRoot
            unsupportedArchives = @($unsupportedArchives)
            safety = "no guessed offsets written"
        }
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
        $compatibilityState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $stateDir "fut-spinner-compatibility-state.json") -Encoding UTF8
        return
    }
    throw "The branch-only FUT spinner patcher did not apply to any archive."
}

Write-Host ("Applied branch-only FUT mode: " + $Mode) -ForegroundColor Green
Write-Host ("Patched archive set: " + ($appliedArchives -join ",")) -ForegroundColor Green
