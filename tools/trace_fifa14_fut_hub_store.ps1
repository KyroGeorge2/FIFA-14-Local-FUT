param(
    [string]$GameExe,

    [ValidateRange(15, 180)]
    [int]$SecurityStartupDelaySeconds = 15,

    [string]$IdentityDb = ""
)

$ErrorActionPreference = "Stop"
$toolsDir = $PSScriptRoot
. (Join-Path $toolsDir "common.ps1")
$config = Resolve-Fifa14Paths -GameExe $GameExe
$GameExe = $config.GameExe
$projectDir = Get-ProjectRoot
$serverPath = Join-Path $projectDir "server\probe.py"
$helperPath = Join-Path $toolsDir "frida_pc_fut_nav_route_patch_trace.py"
$summaryScript = Join-Path $toolsDir "summarize_fifa14_fut_nav_route_patch_trace.py"
$routeWatcherPath = Join-Path $toolsDir "watch_fifa14_fut_v37_route.py"
$routePatcherPath = Join-Path $toolsDir "patch_fifa14_fut_dynamic_route.py"
$discordStatusPath = Join-Path $toolsDir "discord_status_publisher.py"
$artifactsDir = Join-Path $projectDir "artifacts"
$certDir = Join-Path $artifactsDir "local-old-protossl"
$caFile = Join-Path $certDir "old-protossl-otg3-ca.pem"
$probeLog = Join-Path $artifactsDir "redirect-probe.log"
$probeErr = Join-Path $artifactsDir "redirect-probe.err.log"
$helperLog = Join-Path $artifactsDir "frida-pc-fut-nav-route-patch.log"
$helperOut = Join-Path $artifactsDir "frida-pc-fut-nav-route-patch.out.log"
$helperErr = Join-Path $artifactsDir "frida-pc-fut-nav-route-patch.err.log"
$routeWatcherLog = Join-Path $artifactsDir "fifa14-fut-v237-route-watch.log"
$routeWatcherErr = Join-Path $artifactsDir "fifa14-fut-v237-route-watch.err.log"
$discordStatusLog = Join-Path $artifactsDir "discord-status-publisher.log"
$discordStatusErr = Join-Path $artifactsDir "discord-status-publisher.err.log"
$identityDb = if ([string]::IsNullOrWhiteSpace($IdentityDb)) { Join-Path $artifactsDir "local-fut-v237.sqlite3" } else { [IO.Path]::GetFullPath($IdentityDb) }
$summaryPath = Join-Path $artifactsDir "fifa14-fut-nav-route-patch-summary.json"
$processLog = Join-Path $artifactsDir "fifa14-process-handoff-v237.log"
$crashLog = Join-Path $artifactsDir "fifa14-windows-crash-events-v237.txt"
$locStateLog = Join-Path $artifactsDir "fifa14-localization-state-v237.txt"
$captureStart = Get-Date
$packSelectExtract = Join-Path $artifactsDir "fut-packselect-static-extract.zip"
$storeUiExtract = Join-Path $artifactsDir "fut-store-ui-static-extract.zip"
$competitionUiExtract = Join-Path $artifactsDir "fut-competition-ui-static-extract.zip"
$matchAssetReport = Join-Path $artifactsDir "fifa14-match-assets-v2411-beta222.json"
$popupScan = Join-Path $artifactsDir "fcc-login1-popup-bypass\fcc-login1-popup-bypass-scan.json"
$popupState = Join-Path $artifactsDir "fcc-login1-popup-bypass\fcc-login1-popup-bypass-state.json"
$clientDbScan = Join-Path $artifactsDir "fifa14-client-db-scan-v24022.json"
$legendDbReport = Join-Path $artifactsDir "fifa14-legend-db-patch-v24022.json"
$gameDir = Split-Path -Parent $GameExe
$clIni = Join-Path $gameDir "cl.ini"
$backupPath = Join-Path $artifactsDir ("cl.ini.before-fut-nav-route-patch.{0}.bak" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$markerPath = Join-Path $artifactsDir "fut-nav-route-patch-clini-written.txt"
$python = Resolve-ProjectPython
$captureSucceeded = $false

function Quote-Arg([string]$Value) {
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Stop-ProjectHelpers {
    # V2.36 only killed helpers whose command line contained *this* extracted
    # project directory. That left older v2.34/v2.35 probe.py processes alive
    # in other Downloads folders. Python's HTTPServer enables SO_REUSEADDR, and
    # on Windows the game could then be answered by the stale backend even
    # though the new backend had started successfully.
    $helperPatterns = @(
        "probe.py",
        "frida_pc_fut_nav_route_patch_trace.py",
        "frida_pc_fut_auth75_trace.py",
        "frida_pc_fut_native_login_gate_trace.py",
        "frida_pc_fut_first_use_platform_auth_trace.py",
        "frida_pc_fut_first_use_trace.py",
        "frida_pc_fut_returning_auth_trace.py",
        "frida_pc_fut_trusted_easfc_trace.py",
        "frida_pc_fut_unload_trigger_trace.py",
        "frida_pc_fut_frontend_abort_trace.py",
        "frida_pc_fut_auth_gate_trace.py",
        "frida_pc_fut_dynamic_messages_trace.py",
        "frida_pc_fut_security_challenge_success_trace.py",
        "frida_pc_fut_event_bridge_trace.py",
        "frida_pc_nav_action_trace.py",
        "frida_natural_fut_transport.py",
        "frida_enterfut2_game_thread.py",
        "frida_transport_compat.py",
        "read_live_fut_settings.py",
        "watch_fifa14_fut_v"
    )
    $projectMarkers = @(
        "fifa14-fut-hub-store-v2.",
        "FIFA-14-Ultimate-Team-Personal-Revival-Project",
        "fifa14-fut-apt-",
        "fifa14-blaze-server-"
    )

    $stopped = @()
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
        $command = [string]$_.CommandLine
        if ([string]::IsNullOrWhiteSpace($command)) { return }

        $matchesHelper = $false
        foreach ($pattern in $helperPatterns) {
            if ($command -like "*$pattern*") { $matchesHelper = $true; break }
        }
        if (-not $matchesHelper) { return }

        $belongsToFifaRevival = $false
        foreach ($marker in $projectMarkers) {
            if ($command -like "*$marker*") { $belongsToFifaRevival = $true; break }
        }
        # Also catch an older probe launched from a renamed folder if it is
        # explicitly holding the exact FUT ports used by this project.
        $usesKnownFutPorts = (
            ($command -like "*--fut-http-port*8099*") -or
            (($command -like "*--blaze-port*42127*") -or ($command -like "*--blaze-port*42129*")) -or
            ($command -like "*--main-blaze-port*42128*")
        )
        if (-not ($belongsToFifaRevival -or $usesKnownFutPorts)) { return }

        try {
            $stopped += [pscustomobject]@{
                Pid = [int]$_.ProcessId
                Name = [string]$_.Name
                CommandLine = $command
            }
            Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
        } catch {
            Write-Warning ("Could not stop stale FIFA-local helper PID {0}: {1}" -f $_.ProcessId, $_.Exception.Message)
        }
    }

    foreach ($entry in $stopped) {
        Write-Host ("Stopped stale FIFA-local helper PID {0}: {1}" -f $entry.Pid, $entry.Name)
        Write-ProcessHandoffLog ("stale-helper-stopped pid={0}, name={1}, command={2}" -f $entry.Pid, $entry.Name, $entry.CommandLine)
    }
    if ($stopped.Count -gt 0) {
        Start-Sleep -Milliseconds 750
    }
}

function Stop-StaleHealthBackend {
    # The private health route positively identifies one of our local FUT
    # backends even when Win32_Process command-line inspection cannot classify it.
    $uri = "http://127.0.0.1:8099/__fifa14_local_fut_health"
    try {
        $health = Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 1
    } catch {
        return
    }

    $probeName = [string]$health.probe
    $healthPid = 0
    try { $healthPid = [int]$health.pid } catch { $healthPid = 0 }
    if (($probeName -like "FIFA14LocalFUT/*") -and ($healthPid -gt 0)) {
        $proc = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $healthPid) -ErrorAction SilentlyContinue
        $name = if ($proc) { [string]$proc.Name } else { "<unknown>" }
        $command = if ($proc) { [string]$proc.CommandLine } else { "<unavailable>" }
        Write-Host ("Found stale Local FUT backend on 8099: PID {0} {1} ({2}). Stopping it." -f $healthPid, $name, $probeName) -ForegroundColor Yellow
        Write-ProcessHandoffLog ("stale-health-backend-found pid={0}, probe={1}, command={2}" -f $healthPid, $probeName, $command)
        try {
            Stop-Process -Id $healthPid -Force -ErrorAction Stop
            Start-Sleep -Milliseconds 750
            Write-ProcessHandoffLog ("stale-health-backend-stopped pid={0}" -f $healthPid)
        } catch {
            throw ("Port 8099 is owned by stale FIFA Local FUT backend PID {0}, but it could not be stopped: {1}. Re-run the launcher as Administrator or end that PID in Task Manager." -f $healthPid, $_.Exception.Message)
        }
    }
}

function Assert-FifaLocalPortsFree {
    $ports = @(42129, 42128, 8080, 8099, 8306, 44125)
    $conflicts = @()
    foreach ($port in $ports) {
        try {
            $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        } catch {
            $listeners = @()
        }
        foreach ($listener in $listeners) {
            $ownerPid = [int]$listener.OwningProcess
            $proc = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $ownerPid) -ErrorAction SilentlyContinue
            $conflicts += [pscustomobject]@{
                Port = $port
                Pid = $ownerPid
                Name = if ($proc) { [string]$proc.Name } else { "<unknown>" }
                CommandLine = if ($proc) { [string]$proc.CommandLine } else { "<unavailable>" }
            }
        }
    }
    if ($conflicts.Count -gt 0) {
        foreach ($conflict in $conflicts) {
            Write-Host ("PORT CONFLICT: {0} is owned by PID {1} {2}" -f $conflict.Port, $conflict.Pid, $conflict.Name) -ForegroundColor Red
            Write-Host ("  " + $conflict.CommandLine)
        }
        throw "A stale or unrelated process still owns one of FIFA Local FUT's required ports. Close it before launching; BETA refuses to share ports with another backend."
    }
}

function Assert-BetaBackendOwnership {
    param(
        [System.Diagnostics.Process]$ProbeProcess,
        [Parameter(Mandatory = $true)][string]$InstanceToken,
        [int]$Seconds = 15
    )
    $uri = "http://127.0.0.1:8099/__fifa14_local_fut_health"
    $deadline = (Get-Date).AddSeconds($Seconds)
    $lastError = ""
    while ((Get-Date) -lt $deadline) {
        # Verify the server instance itself before trusting Start-Process PID
        # lifetime. Some Python/venv launchers may hand execution to another PID.
        try {
            $health = Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 2
            if ($health.buildVersion -ne "2.41.1-beta2.25.9") {
                throw ("Port 8099 answered from the wrong backend version: " + [string]$health.buildVersion)
            }
            if ([string]$health.instanceToken -ne $InstanceToken) {
                throw ("Port 8099 answered from a different backend instance (PID {0}, token {1}); expected launch token {2}." -f $health.pid, [string]$health.instanceToken, $InstanceToken)
            }
            $sample = $health.samplePlayer
            foreach ($requiredKey in @("itemType", "cardsubtypeid", "nation", "leagueId", "resourceGameYear")) {
                if ($null -eq $sample.PSObject.Properties[$requiredKey]) {
                    throw ("BETA health response is missing ItemData key: " + $requiredKey)
                }
            }
            $actualBackendPid = [int]$health.pid
            Write-Host ("Backend ownership verified: v2.41.1-beta2.25.9 launch token matched. HTTP 8099 is served by PID {0} (Start-Process PID {1})." -f $actualBackendPid, $ProbeProcess.Id) -ForegroundColor Green
            Write-ProcessHandoffLog ("backend-health-ok pid={0}, startProcessPid={1}, build=2.41.1-beta2.25.9, token={2}, assetId={3}, resourceId={4}, nation={5}, leagueId={6}" -f `
                $actualBackendPid, $ProbeProcess.Id, $InstanceToken, $sample.assetId, $sample.resourceId, $sample.nation, $sample.leagueId)
            return $actualBackendPid
        } catch {
            $lastError = $_.Exception.Message
            try { $ProbeProcess.Refresh() } catch { }
            if ($ProbeProcess.HasExited) {
                $details = if (Test-Path -LiteralPath $probeErr) { (Get-Content -LiteralPath $probeErr -Raw).Trim() } else { "" }
                throw "The Start-Process PID exited and the v2.41.1-beta2.25.9 launch token never appeared on 8099. $details Last health error: $lastError"
            }
            Start-Sleep -Milliseconds 250
        }
    }
    throw "Could not verify the v2.41.1-beta2.25.9 backend instance on port 8099: $lastError"
}

function Write-ProcessHandoffLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffK"), $Message
    Add-Content -LiteralPath $processLog -Value $line -Encoding UTF8
}

function Write-Fifa14LocalizationState {
    $lines = @()
    $lines += "Captured: $(Get-Date -Format o)"
    $lines += "GameDir: $gameDir"
    foreach ($relative in @("Data\loc", "Data\loc-licensed", "Game\Data\loc", "Game\Data\loc-licensed")) {
        $candidate = Join-Path $gameDir $relative
        $exists = Test-Path -LiteralPath $candidate
        $lines += ""
        $lines += "[$relative] exists=$exists path=$candidate"
        if ($exists) {
            try {
                $files = Get-ChildItem -LiteralPath $candidate -File -ErrorAction Stop | Sort-Object Name | Select-Object -First 80
                foreach ($file in $files) {
                    $lines += ("  {0}  {1}" -f $file.Length, $file.Name)
                }
            } catch {
                $lines += "  ERROR: $($_.Exception.Message)"
            }
        }
    }
    $lines | Set-Content -LiteralPath $locStateLog -Encoding UTF8
}

function Get-Fifa14ProcessCandidates {
    $items = @()
    foreach ($process in @(Get-Process -Name "fifa14" -ErrorAction SilentlyContinue)) {
        try { $process.Refresh() } catch { continue }
        if ($process.HasExited) { continue }

        $started = $null
        try { $started = $process.StartTime } catch { $started = Get-Date }
        $path = ""
        try { $path = [string]$process.Path } catch { $path = "<unavailable>" }
        $windowHandle = 0
        $windowTitle = ""
        try {
            $windowHandle = [int64]$process.MainWindowHandle
            $windowTitle = [string]$process.MainWindowTitle
        } catch { }

        $items += [pscustomobject]@{
            Process = $process
            Id = [int]$process.Id
            StartTime = $started
            Path = $path
            MainWindowHandle = $windowHandle
            MainWindowTitle = $windowTitle
        }
    }
    return @($items)
}

function Wait-Fifa14GameplayProcess {
    param(
        [int]$LauncherPid,
        [int[]]$RejectedPids = @(),
        [int]$Seconds = 300
    )

    $firstSeen = @{}
    $lastSnapshot = ""
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $candidates = @(Get-Fifa14ProcessCandidates | Where-Object { $RejectedPids -notcontains $_.Id })
        $snapshot = ($candidates | ForEach-Object {
            "pid={0},window=0x{1:X},title={2},path={3}" -f $_.Id, $_.MainWindowHandle, $_.MainWindowTitle, $_.Path
        }) -join " | "
        if ($snapshot -ne $lastSnapshot) {
            if ($snapshot) { Write-ProcessHandoffLog "candidates: $snapshot" }
            else { Write-ProcessHandoffLog "candidates: none" }
            $lastSnapshot = $snapshot
        }

        foreach ($candidate in $candidates) {
            $key = [string]$candidate.Id
            if (-not $firstSeen.ContainsKey($key)) {
                $firstSeen[$key] = Get-Date
            }
        }

        $eligible = @()
        foreach ($candidate in $candidates) {
            if ($candidate.MainWindowHandle -eq 0) { continue }
            $key = [string]$candidate.Id
            $stableSeconds = ((Get-Date) - [datetime]$firstSeen[$key]).TotalSeconds
            # The process returned by Process.Start can be an EA handoff stub. Give a
            # replacement fifa14.exe time to appear before accepting the original PID.
            $requiredStableSeconds = if ($candidate.Id -eq $LauncherPid) { 12 } else { 3 }
            if ($stableSeconds -ge $requiredStableSeconds) {
                $eligible += $candidate
            }
        }

        if ($eligible.Count -gt 0) {
            $selected = $eligible |
                Sort-Object @{ Expression = { if ($_.Id -eq $LauncherPid) { 1 } else { 0 } } }, `
                            @{ Expression = { $_.StartTime }; Descending = $true } |
                Select-Object -First 1
            Write-ProcessHandoffLog ("selected gameplay process pid={0}, launcher_pid={1}, window=0x{2:X}, title={3}, path={4}" -f `
                $selected.Id, $LauncherPid, $selected.MainWindowHandle, $selected.MainWindowTitle, $selected.Path)
            return $selected.Process
        }

        Start-Sleep -Milliseconds 250
    }

    $rejected = if ($RejectedPids.Count -gt 0) { $RejectedPids -join "," } else { "none" }
    throw "Timed out waiting for the real FIFA 14 gameplay process (launcher PID $LauncherPid; rejected PIDs $rejected). See $processLog"
}

function Wait-LogPattern {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$LogPath,
        [string]$Pattern,
        [string]$ErrorPath,
        [int]$Seconds = 30
    )
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            $errorText = if (Test-Path -LiteralPath $ErrorPath) {
                (Get-Content -LiteralPath $ErrorPath -Raw).Trim()
            } else { "no error log was written" }
            throw "The helper exited before it was ready: $errorText"
        }
        if ((Test-Path -LiteralPath $LogPath) -and
            (Select-String -LiteralPath $LogPath -Pattern $Pattern -Quiet)) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Timed out waiting for helper pattern: $Pattern"
}

foreach ($required in @($GameExe, $serverPath, $helperPath, $summaryScript, $routeWatcherPath, $routePatcherPath, $python)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required path was not found: $required"
    }
}

New-Item -ItemType Directory -Path $artifactsDir -Force | Out-Null
Write-Fifa14LocalizationState
& $python -c "import frida, cryptography; print('Using Python:', __import__('sys').executable)"
if ($LASTEXITCODE -ne 0) {
    throw "The selected Python runtime cannot import frida and cryptography: $python"
}

# Clear prior capture logs before stale-helper cleanup so the cleanup/port ownership
# evidence itself is retained in the new v2.37 results ZIP. Persistent SQLite state
# is intentionally not deleted here.
foreach ($log in @($probeLog, $probeErr, $helperLog, $helperOut, $helperErr, $routeWatcherLog, $routeWatcherErr, $processLog, $crashLog)) {
    Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
}

Write-Host "Stopping stale FIFA-local helper processes from all prior revival builds."
Stop-ProjectHelpers
Stop-StaleHealthBackend
Assert-FifaLocalPortsFree
Get-Process fifa14 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

if (Test-Path -LiteralPath $clIni) {
    Copy-Item -LiteralPath $clIni -Destination $backupPath -Force
    Write-Host "Backed up cl.ini to $backupPath"
}

# Natural menu entry only. Direct boot and action-name assignments are absent.
[IO.File]::WriteAllText(
    $clIni,
    "FUT_ENABLE_MENU = 1`r`n",
    [Text.Encoding]::ASCII
)
Set-Content -LiteralPath $markerPath -Value $clIni -Encoding ASCII

$instanceToken = [Guid]::NewGuid().ToString("N")
Write-ProcessHandoffLog ("launch-instance-token={0}" -f $instanceToken)
$probeArgs = @(
    (Quote-Arg $serverPath),
    "--instance-token", (Quote-Arg $instanceToken),
    "--host", "127.0.0.1",
    "--blaze-port", "42129",
    "--main-blaze-port", "42128",
    "--http-port", "8080",
    "--fut-http-port", "8099",
    "--dynamic-http-port", "8306",
    "--fut-account-mode", "existing",
    "--identity-db", (Quote-Arg $identityDb),
    "--beta-mode",
    "--enable-gosca",
    "--gosca-port", "44125",
    "--gosca-reply", "xml",
    "--redirector-mode", "tls",
    "--redirector-reply", "local",
    "--cert-dir", (Quote-Arg $certDir),
    "--cert-hash", "old-protossl",
    "--origin-login-mode", "success",
    "--origin-login-delay-ms", "100",
    "--login-notification-delay-ms", "1500"
) -join " "

$probe = $null
$actualBackendPid = 0
$helper = $null
$routeWatcher = $null
$discordStatus = $null
try {
    $probe = Start-Process -FilePath $python -ArgumentList $probeArgs `
        -RedirectStandardOutput $probeLog -RedirectStandardError $probeErr `
        -PassThru -WindowStyle Hidden
    Write-Host "Started localhost FIFA services as PID $($probe.Id)."

    # V29 discovery: once a fresh first-use profile persists its club, restore
    # the retail returning-user NAV resource on disk immediately. This prevents
    # backing out/re-entering FUT in the same FIFA session from deliberately
    # replaying Icebreaker. Existing clubs cause this watcher to exit quickly.
    $routeStateDir = Join-Path $artifactsDir "fut-dynamic-route-v237"
    $routeWatcherArgs = @(
        (Quote-Arg $routeWatcherPath),
        "--database", (Quote-Arg $identityDb),
        "--game-root", (Quote-Arg $gameDir),
        "--state-dir", (Quote-Arg $routeStateDir),
        "--patcher", (Quote-Arg $routePatcherPath),
        "--timeout-seconds", "1800"
    ) -join " "
    $routeWatcher = Start-Process -FilePath $python -ArgumentList $routeWatcherArgs `
        -RedirectStandardOutput $routeWatcherLog -RedirectStandardError $routeWatcherErr `
        -PassThru -WindowStyle Hidden
    Write-Host "Started v2.37 club-save/returning-route watcher as PID $($routeWatcher.Id)."

    $caDeadline = (Get-Date).AddSeconds(20)
    while (-not (Test-Path -LiteralPath $caFile) -and (Get-Date) -lt $caDeadline) {
        $probe.Refresh()
        if ($probe.HasExited) {
            $details = if (Test-Path -LiteralPath $probeErr) { Get-Content -LiteralPath $probeErr -Raw } else { "" }
            throw "The local server exited before creating its CA. $details"
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Path -LiteralPath $caFile)) {
        throw "Timed out waiting for $caFile"
    }

    # Critical V2.37 regression guard: prove that the process we just started
    # is the process answering FIFA's FUT port and that it exposes the full
    # ItemData contract before FIFA is allowed to launch.
    $actualBackendPid = Assert-BetaBackendOwnership -ProbeProcess $probe -InstanceToken $instanceToken

    if (-not [string]::IsNullOrWhiteSpace($env:FIFA14_DISCORD_WEBHOOK_URL)) {
        $discordArgs = @((Quote-Arg $discordStatusPath), "--interval", "60") -join " "
        $discordStatus = Start-Process -FilePath $python -ArgumentList $discordArgs `
            -RedirectStandardOutput $discordStatusLog -RedirectStandardError $discordStatusErr `
            -PassThru -WindowStyle Hidden
        Write-Host ("Discord status publisher started as PID " + $discordStatus.Id + ".") -ForegroundColor Cyan
    } else {
        Write-Host "Discord status panel is available but not started (FIFA14_DISCORD_WEBHOOK_URL is not configured)." -ForegroundColor DarkGray
    }

    $gameInfo = [Diagnostics.ProcessStartInfo]::new()
    $gameInfo.FileName = $GameExe
    $gameInfo.WorkingDirectory = $gameDir
    $gameInfo.UseShellExecute = $false
    $launcher = [Diagnostics.Process]::Start($gameInfo)
    Write-Host "Started the configured FIFA executable as PID $($launcher.Id). Waiting for the real gameplay process after any EA App handoff."
    Write-ProcessHandoffLog "configured executable started pid=$($launcher.Id), path=$GameExe"

    $fifaProcess = Wait-Fifa14GameplayProcess -LauncherPid $launcher.Id -Seconds 300
    $fifaPid = [int]$fifaProcess.Id
    Write-Host "Gameplay fifa14.exe PID $fifaPid is stable and has a game window. Do not select Play FUT Now yet."
    Write-Host "During the next $SecurityStartupDelaySeconds seconds, pass language select and reach the normal main menu."
    Write-Host "The tracer follows EA App process handoffs and will re-acquire a replacement fifa14.exe if the initial PID closes."
    Write-Host "The localhost server is already recording title-screen accountinfo, so the tracer does not wait for accountinfo after attachment."

    $attachAt = (Get-Date).AddSeconds($SecurityStartupDelaySeconds)
    while ((Get-Date) -lt $attachAt) {
        $current = Get-Process -Id $fifaPid -ErrorAction SilentlyContinue
        if (-not $current) {
            Write-Host "FIFA PID $fifaPid closed during startup. Waiting for the replacement gameplay process from the EA App handoff."
            Write-ProcessHandoffLog "tracked gameplay pid=$fifaPid exited before attach; waiting for replacement"
            $fifaProcess = Wait-Fifa14GameplayProcess -LauncherPid $launcher.Id -RejectedPids @($fifaPid) -Seconds 180
            $fifaPid = [int]$fifaProcess.Id
            Write-Host "Re-acquired gameplay fifa14.exe as PID $fifaPid."
            Write-ProcessHandoffLog "re-acquired gameplay pid=$fifaPid"
            $minimumAttachAt = (Get-Date).AddSeconds(15)
            if ($minimumAttachAt -gt $attachAt) { $attachAt = $minimumAttachAt }
        }
        Start-Sleep -Seconds 1
    }

    # One final process check closes the race between the delay and Frida attach.
    if (-not (Get-Process -Id $fifaPid -ErrorAction SilentlyContinue)) {
        Write-ProcessHandoffLog "tracked gameplay pid=$fifaPid exited at final attach boundary; waiting for replacement"
        $fifaProcess = Wait-Fifa14GameplayProcess -LauncherPid $launcher.Id -RejectedPids @($fifaPid) -Seconds 180
        $fifaPid = [int]$fifaProcess.Id
        Write-Host "Re-acquired gameplay fifa14.exe as PID $fifaPid immediately before tracer attach."
    }

    $helperArgs = @(
        (Quote-Arg $helperPath),
        "--pid", "$fifaPid",
        "--ca-file", (Quote-Arg $caFile),
        "--identity-db", (Quote-Arg $identityDb),
        "--log", (Quote-Arg $helperLog),
        "--run-seconds", "1800"
    ) -join " "

    $helper = Start-Process -FilePath $python -ArgumentList $helperArgs `
        -RedirectStandardOutput $helperOut -RedirectStandardError $helperErr `
        -PassThru -WindowStyle Hidden
    Wait-LogPattern -Process $helper -LogPath $helperLog `
        -Pattern 'native-fut-nav-route-patch-trace-ready' -ErrorPath $helperErr -Seconds 30

    if (Select-String -LiteralPath $helperLog -Pattern '"hooks_enabled": false' -Quiet) {
        throw "The decrypted FIFA runtime signatures did not match this exact build. The native auth75 hooks were not armed. See $helperLog"
    }

    Write-Host ""
    Write-Host "TRACE ATTACHED. The low-overhead v2.41.1 BETA tracer is armed before you click FUT."
    Write-Host "The redirector already uses the OldProtoSSL certificate accepted by the legacy client; the runtime CA hook is fallback telemetry only."
    Write-Host ""
    Write-Host "LOCAL FUT BETA 2 IS READY: OFFLINE COMPETITIONS, STORE ART FIX, STARTER CLUB, ECONOMY LEDGER AND MATCH TRACE ARE ONLINE."
    Write-Host "Now select Play FUT Now ONCE."
    Write-Host "Existing BETA club progression is preserved. The one-time Store test balance remains available so pack and Transfer Market testing are not blocked by coins."
    Write-Host "BETA 2.25.1 TEST: verify an outfield player card shows its real position/outfield stat labels, then open Transfer Market and confirm Transfer List capacity is non-zero." -ForegroundColor Cyan
    Write-Host "Search the market for a few players (including 92 Ronaldo), Buy Now one card and assign it from New Items. List one tradeable card and confirm it appears on the Transfer List." -ForegroundColor Cyan
    Write-Host "Open 100k/Jumbo Rare packs to exercise the accessible special-card weights. The generator is hard-capped at two special player cards in one pack; two should remain uncommon." -ForegroundColor Cyan
    Write-Host "The BETA 2.23 tournament baseline must remain intact: record/contracts should still settle exactly once and forfeiting must stay inside FUT with no Origin Servers disconnect." -ForegroundColor Cyan
    Write-Host "22 supplied internal/unmapped rows are intentionally catalogue-only and are not emitted in packs until their native FIFA 14 meaning is proven." -ForegroundColor DarkGray
    Write-Host "When you have finished the player/market/pack test, CLOSE FIFA COMPLETELY, then press Enter here to package the BETA 2.25.1 report."
    [Console]::ReadLine() | Out-Null

    $stillRunning = Get-Process -Name fifa14 -ErrorAction SilentlyContinue
    if ($stillRunning) {
        Write-Host "FIFA is still running. Close FIFA now; BETA 2.25.1 will wait before stopping the backend and packaging the market/pack report." -ForegroundColor Yellow
        $exitDeadline = (Get-Date).AddMinutes(5)
        while ((Get-Date) -lt $exitDeadline -and (Get-Process -Name fifa14 -ErrorAction SilentlyContinue)) {
            Start-Sleep -Milliseconds 500
        }
        if (Get-Process -Name fifa14 -ErrorAction SilentlyContinue) {
            Write-Host "FIFA is still running after 5 minutes; packaging the available capture without force-closing the game." -ForegroundColor Yellow
        } else {
            Write-Host "FIFA has exited. Capturing 2 final seconds of backend/log flush before packaging." -ForegroundColor Green
            Start-Sleep -Seconds 2
        }
    } else {
        Write-Host "FIFA is closed. Capturing 2 final seconds of backend/log flush before packaging."
        Start-Sleep -Seconds 2
    }
    $captureSucceeded = $true
}
finally {
    if ($helper -and -not $helper.HasExited) {
        Stop-Process -Id $helper.Id -Force -ErrorAction SilentlyContinue
    }
    if ($routeWatcher -and -not $routeWatcher.HasExited) {
        Stop-Process -Id $routeWatcher.Id -Force -ErrorAction SilentlyContinue
    }
    if ($discordStatus -and -not $discordStatus.HasExited) {
        Stop-Process -Id $discordStatus.Id -Force -ErrorAction SilentlyContinue
    }
    if ($actualBackendPid -gt 0 -and (-not $probe -or $actualBackendPid -ne $probe.Id)) {
        Stop-Process -Id $actualBackendPid -Force -ErrorAction SilentlyContinue
    }
    if ($probe -and -not $probe.HasExited) {
        Stop-Process -Id $probe.Id -Force -ErrorAction SilentlyContinue
    }

    if (Test-Path -LiteralPath $backupPath) {
        Copy-Item -LiteralPath $backupPath -Destination $clIni -Force
        Write-Host "Restored the previous cl.ini from $backupPath"
    } elseif (Test-Path -LiteralPath $clIni) {
        $current = Get-Content -LiteralPath $clIni -Raw
        if ($current -match '(?im)^\s*FUT_ENABLE_MENU\s*=\s*1\s*$') {
            Remove-Item -LiteralPath $clIni -Force
            Write-Host "Removed the temporary menu-only cl.ini."
        }
    }
    Remove-Item -LiteralPath $markerPath -Force -ErrorAction SilentlyContinue

    if (Test-Path -LiteralPath $helperLog) {
        & $python $summaryScript --helper-log $helperLog --probe-log $probeLog --output $summaryPath
    }

    try {
        $events = Get-WinEvent -FilterHashtable @{ LogName = 'Application'; StartTime = $captureStart.AddMinutes(-1); Id = 1000,1001 } -ErrorAction SilentlyContinue |
            Where-Object { ([string]$_.Message) -match '(?i)fifa14\.exe|fifa14' } |
            Sort-Object TimeCreated
        if ($events) {
            $events | Format-List TimeCreated, Id, ProviderName, LevelDisplayName, Message | Out-String -Width 240 |
                Set-Content -LiteralPath $crashLog -Encoding UTF8
        } else {
            Set-Content -LiteralPath $crashLog -Value "No FIFA 14 Application Error/WER 1000/1001 event was found for this capture window." -Encoding UTF8
        }
    } catch {
        Set-Content -LiteralPath $crashLog -Value ("Crash-event query failed: " + $_.Exception.Message) -Encoding UTF8
    }

    $resultsZip = Join-Path $artifactsDir "fifa14-local-fut-beta-v2411-beta2259-results.zip"
    Remove-Item -LiteralPath $resultsZip -Force -ErrorAction SilentlyContinue
    $fixture = Join-Path $projectDir "server\icebreakerpacklist.v27.json"
    $verifiedPool = Join-Path $projectDir "server\fifa14-player-catalog.v237.json"
    $packCatalog = Join-Path $projectDir "server\pack-catalog.v237.json"
    $packWeights = Join-Path $projectDir "server\pack-weights.v237.json"
    $managerCatalog = Join-Path $projectDir "server\manager-catalog.v237.json"
    $consumableCatalog = Join-Path $projectDir "server\fifa14-consumable-catalog.v2412.json"
    $consumableSource = Join-Path $projectDir "reference\fifa14_consumables.user.json"
    $resultFiles = @($helperLog, $helperOut, $helperErr, $routeWatcherLog, $routeWatcherErr, $discordStatusLog, $discordStatusErr, $probeLog, $probeErr, $processLog, $crashLog, $locStateLog, $summaryPath, $identityDb, $fixture, $verifiedPool, $packCatalog, $packWeights, $managerCatalog, $consumableCatalog, $consumableSource, $packSelectExtract, $storeUiExtract, $competitionUiExtract, $matchAssetReport, $popupScan, $popupState, $clientDbScan, $legendDbReport) |
        Where-Object { Test-Path -LiteralPath $_ }
    if ($resultFiles.Count -gt 0) {
        Compress-Archive -LiteralPath $resultFiles -DestinationPath $resultsZip -Force
    }

    Write-Host ""
    if ($captureSucceeded) {
        Write-Host "CAPTURE COMPLETE. The BETA results include the persistent progression database, consumable catalogue/source, wallet/match state, pack catalogue/weights, native timeline, and Windows crash events when available."
        Write-Host "Share this single results archive when requesting analysis:"
    } else {
        Write-Host "CAPTURE DID NOT COMPLETE. Partial diagnostics were packaged here:"
    }
    Write-Host $resultsZip
    Write-Host ""
    Write-Host "Individual files remain at:"
    Write-Host $processLog
    Write-Host $helperLog
    Write-Host $probeLog
    Write-Host $summaryPath
}
