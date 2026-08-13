param([string]$GameRoot)

# Deliberately standalone and defensive. This script has to run when the rest
# of the setup is broken, so it does not dot-source common.ps1 and does not use
# Resolve-ProjectPython, which throws when .venv is missing -- exactly the state
# a user needs diagnosed. It never throws; it reports.

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "diagnose_fifa14_local_fut.py"
$report = Join-Path $projectRoot "diagnostics-report.txt"

if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    Write-Output "Diagnostics script not found: $script"
    Write-Output "The release ZIP was probably extracted incompletely."
    exit 2
}

# Candidates are objects, not nested arrays: PowerShell flattens @(a, @())
# into a single element, which silently loses the argument prefix.
$candidates = New-Object System.Collections.Generic.List[object]

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $candidates.Add([pscustomobject]@{ FilePath = $venvPython; Prefix = @() })
}
foreach ($name in @("py", "python", "python3")) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $command -or -not $command.Source) { continue }
    if ($command.Source -like "*\WindowsApps\*") { continue }
    $prefix = @()
    if ($name -eq "py") { $prefix = @("-3") }
    $candidates.Add([pscustomobject]@{ FilePath = $command.Source; Prefix = $prefix })
}

if ($candidates.Count -eq 0) {
    Write-Output "No Python interpreter was found."
    Write-Output "Run INSTALL_PREREQUISITES.cmd as Administrator first."
    exit 2
}

$scriptArguments = @($script)
if ($GameRoot) { $scriptArguments += @("--game-root", $GameRoot) }

foreach ($candidate in $candidates) {
    $invocation = @($candidate.Prefix) + $scriptArguments
    $output = & $candidate.FilePath @invocation 2>&1
    $code = $LASTEXITCODE
    # 0 = clean, 1 = problems found. Anything else means this interpreter could
    # not run the script at all, so fall through to the next candidate.
    if ($code -eq 0 -or $code -eq 1) {
        $text = ($output | Out-String)
        Write-Output $text
        try {
            Set-Content -LiteralPath $report -Value $text -Encoding UTF8
        } catch {
            Write-Output "Could not write $report"
        }
        exit $code
    }
}

Write-Output "Every Python interpreter that was tried failed to run the diagnostics."
Write-Output "Run INSTALL_PREREQUISITES.cmd as Administrator, then try again."
exit 2
