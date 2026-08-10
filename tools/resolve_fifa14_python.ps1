function Resolve-FifaPython {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectDir,

        [string[]]$RequiredModules = @()
    )

    $candidates = New-Object System.Collections.Generic.List[object]

    $projectPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    $userCodexPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    foreach ($path in @($projectPython, $userCodexPython)) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            $candidates.Add([pscustomobject]@{ FilePath = $path; Prefix = @() })
        }
    }

    $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $pyCommand) { $pyCommand = Get-Command py -ErrorAction SilentlyContinue }
    if ($pyCommand -and $pyCommand.Source) {
        $candidates.Add([pscustomobject]@{ FilePath = $pyCommand.Source; Prefix = @("-3") })
    }

    foreach ($commandName in @("python3.exe", "python.exe", "python3", "python")) {
        $commands = @(Get-Command $commandName -All -ErrorAction SilentlyContinue)
        foreach ($command in $commands) {
            $source = [string]$command.Source
            if (-not $source) { continue }
            if ($source -like "*\Microsoft\WindowsApps\*") { continue }
            if ($source -like "*\WindowsApps\*") { continue }
            $candidates.Add([pscustomobject]@{ FilePath = $source; Prefix = @() })
        }
    }

    $seen = @{}
    $imports = @("import sys")
    foreach ($module in $RequiredModules) {
        if ($module -notmatch '^[A-Za-z_][A-Za-z0-9_.]*$') {
            throw "Invalid Python module name requested by launcher: $module"
        }
        $imports += "import $module"
    }
    $testCode = ($imports -join "; ") + "; print(sys.executable)"

    foreach ($candidate in $candidates) {
        $key = ([string]$candidate.FilePath).ToLowerInvariant() + "|" + ((@($candidate.Prefix)) -join " ")
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true

        try {
            $testArgs = @()
            $testArgs += @($candidate.Prefix)
            $testArgs += @("-c", $testCode)
            $output = & $candidate.FilePath @testArgs 2>$null
            if ($LASTEXITCODE -eq 0) {
                return [pscustomobject]@{
                    FilePath = [string]$candidate.FilePath
                    Prefix = [string[]]@($candidate.Prefix)
                    Executable = (($output | Select-Object -Last 1) -as [string])
                }
            }
        }
        catch {
            # Try the next real interpreter. The Microsoft Store alias is excluded above.
        }
    }

    $moduleText = if ($RequiredModules.Count -gt 0) { " with modules: " + ($RequiredModules -join ", ") } else { "" }
    throw "A working Python 3 interpreter$moduleText was not found. Checked the project venv, Codex runtime, py launcher, python3, and non-WindowsApps python executables."
}
