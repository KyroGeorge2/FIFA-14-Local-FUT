param(
    [ValidateSet("branch-saveload", "branch-offline")]
    [string]$Mode = "branch-offline",

    [string]$GameRoot,
    [string]$GameExe,

    [ValidateRange(5, 180)]
    [int]$SecurityStartupDelaySeconds = 15,

    [switch]$DualArchive,
    [switch]$SkipBranchApply,
    [switch]$SkipNavApply,
    [switch]$SkipPopupApply,
    [switch]$SkipStaticExtract,
    [switch]$ResetIdentity,
    [switch]$SkipToHub
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")
$config = Resolve-Fifa14Paths -GameRoot $GameRoot -GameExe $GameExe
$GameRoot = $config.GameRoot
$GameExe = $config.GameExe

function Stop-Fifa14ForOnDiskPatch {
    $running = @(Get-Process -Name "fifa14" -ErrorAction SilentlyContinue)
    if ($running.Count -eq 0) { return }

    Write-Host ("Closing stale FIFA 14 process(es) before on-disk patching: " + (($running | ForEach-Object { $_.Id }) -join ", ")) -ForegroundColor Yellow
    $running | Stop-Process -Force -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 250
        $running = @(Get-Process -Name "fifa14" -ErrorAction SilentlyContinue)
    } while ($running.Count -gt 0 -and (Get-Date) -lt $deadline)

    if ($running.Count -gt 0) {
        throw "Could not close fifa14.exe before on-disk FUT patching."
    }
}

# Direct invocation of this script gets the same stale-process protection as
# RUN_FIFA14_HUB_STORE.cmd. FIFA is launched later by the trace helper.
Stop-Fifa14ForOnDiskPatch

# Compatibility policy: do not hard-block the launcher on executable/DLL hashes.
# The local FUT runtime still requires the game binary and FUT DLLs to exist,
# while archive patchers independently validate the specific BIG/BH records
# they modify before writing. Unknown game builds are therefore allowed to
# continue, but are not guaranteed to be compatible with native signatures.
$cardsDll = Join-Path $GameRoot "CardsDLLzf.dll"
$powCandidates = @(
    (Join-Path $GameRoot "dlc\dlc_powdll\dlc\powdll\powdllzf.dll"),
    (Join-Path $GameRoot "powdllzf.dll")
)
$powDll = $powCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
foreach ($binary in @($GameExe, $cardsDll)) {
    if (-not (Test-Path -LiteralPath $binary)) { throw "Required FIFA 14 binary not found: $binary" }
}
if ([string]::IsNullOrWhiteSpace($powDll)) {
    throw ("Required powdllzf.dll not found. Checked: " + ($powCandidates -join "; "))
}
Write-Host ("Resolved powdllzf.dll: " + $powDll)
Write-Host "Executable/DLL hash enforcement is disabled; continuing with the detected FIFA 14 installation." -ForegroundColor Yellow
Write-Host "Archive patchers will still validate supported BIG/BH record layouts before writing." -ForegroundColor DarkGray

$branchApply = Join-Path $PSScriptRoot "apply_fifa14_fut_branch_bypass.ps1"
$popupApply = Join-Path $PSScriptRoot "apply_fifa14_fcc_login1_popup.ps1"
$popupVerify = Join-Path $PSScriptRoot "verify_fifa14_fcc_login1_popup.ps1"
$traceScript = Join-Path $PSScriptRoot "trace_fifa14_fut_hub_store.ps1"
$installVerify = Join-Path $PSScriptRoot "verify_fifa14_v237_install.py"
$betaVerify = Join-Path $PSScriptRoot "verify_fifa14_beta2.py"
$postMatchVerify = Join-Path $PSScriptRoot "verify_fifa14_postmatch_beta2259.py"
$consumableVerify = Join-Path $PSScriptRoot "verify_fifa14_consumables_beta224.py"
$packPerfVerify = Join-Path $PSScriptRoot "verify_fifa14_pack_ui_performance_beta2250.py"
$marketVerify = Join-Path $PSScriptRoot "verify_fifa14_market_beta2250.py"
$regressionVerify = Join-Path $PSScriptRoot "verify_fifa14_regressions_beta2258.py"
$statePrep = Join-Path $PSScriptRoot "prepare_fifa14_beta_state.py"
$dynamicRoute = Join-Path $PSScriptRoot "patch_fifa14_fut_dynamic_route.py"
$storeUiScanner = Join-Path $PSScriptRoot "extract_fifa14_fut_store_ui.py"
$competitionUiScanner = Join-Path $PSScriptRoot "extract_fifa14_fut_competition_ui.py"
$matchAssetScanner = Join-Path $PSScriptRoot "scan_fifa14_match_assets.py"
$legendDbPatcher = Join-Path $PSScriptRoot "patch_fifa14_fut_legends_db.py"
$storeUiExtract = Join-Path (Join-Path (Get-ProjectRoot) "artifacts") "fut-store-ui-static-extract.zip"
$competitionUiExtract = Join-Path (Join-Path (Get-ProjectRoot) "artifacts") "fut-competition-ui-static-extract.zip"
$matchAssetReport = Join-Path (Join-Path (Get-ProjectRoot) "artifacts") "fifa14-match-assets-v2411-beta222.json"
$retailRestore = Join-Path $PSScriptRoot "restore_fifa14_fut_packselect_retail_v19.ps1"
$retailVerify = Join-Path $PSScriptRoot "verify_fifa14_fut_packselect_retail_v19.ps1"
$vp6Apply = Join-Path $PSScriptRoot "apply_fifa14_fut_intro_vp6_v19.ps1"
$vp6Verify = Join-Path $PSScriptRoot "verify_fifa14_fut_intro_vp6_v19.ps1"
$python = Resolve-ProjectPython
$projectDir = Get-ProjectRoot
$persistentDir = Join-Path $env:LOCALAPPDATA "FIFA14LocalFUTBeta"
$identityDb = Join-Path $persistentDir "local-fut-beta-v2410.sqlite3"
$fixturePath = Join-Path $projectDir "server\icebreakerpacklist.v27.json"
$legendCatalogPath = Join-Path $projectDir "server\fifa14-legend-catalog.v24013.json"
$baseCatalogPath = Join-Path $projectDir "server\fifa14-player-catalog.v237.json"
$legendDbReport = Join-Path $projectDir "artifacts\fifa14-legend-db-patch-v24022.json"
$routeStateDir = Join-Path $projectDir "artifacts\fut-dynamic-route-v237"

foreach ($required in @($branchApply, $popupApply, $popupVerify, $traceScript, $installVerify, $statePrep, $betaVerify, $postMatchVerify, $consumableVerify, $packPerfVerify, $marketVerify, $regressionVerify, $dynamicRoute, $storeUiScanner, $competitionUiScanner, $matchAssetScanner, $legendDbPatcher, $retailRestore, $retailVerify, $vp6Apply, $vp6Verify, $fixturePath, $legendCatalogPath, $baseCatalogPath)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required route-patch tool not found: $required" }
}

Write-Host "Checking bundled FIFA 14 base-player catalogue..."
$catalogPath = Join-Path $projectDir "server\fifa14-player-catalog.v237.json"
$catalogDocument = Get-Content -LiteralPath $catalogPath -Raw | ConvertFrom-Json
$catalogCount = @($catalogDocument.players).Count
if ($catalogCount -lt 10000) {
    throw "Bundled FIFA 14 base-player catalogue is incomplete: $catalogCount players. Re-extract the v2.40.22.1 package."
}
Write-Host ("Bundled full FIFA 14 base-player catalogue is ready: " + $catalogCount + " players. No network sync is required.") -ForegroundColor Green

Write-Host "Running one-time client DB recovery safeguard before launch..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path (Split-Path -Parent $legendDbReport) -Force | Out-Null
# v2.40.22 could produce a cards_ng_db that our parser verified but the retail
# game rejected during startup. Restore the byte-for-byte pre-Legend archive
# backups before touching any other runtime patch. The restore action is safe to
# repeat; when no backup exists it simply reports an empty restored list.
$legendRestoreJson = & $python $legendDbPatcher --game-root $GameRoot --output-report $legendDbReport --restore
if ($LASTEXITCODE -ne 0) {
    throw "Could not restore the pre-v2.40.22 FIFA archive backup. Run RESTORE_FIFA14_LEGEND_DB.cmd and retry."
}
$legendClientReady = $false
try {
    $legendDbState = Get-Content -LiteralPath $legendDbReport -Raw | ConvertFrom-Json
    $restoredCount = @($legendDbState.restored).Count
    if ($restoredCount -gt 0) {
        Write-Host ("Recovered " + $restoredCount + " FIFA archive file(s) from the pre-Legend backup. Unsafe Legend DB expansion is disabled in this build.") -ForegroundColor Green
    } else {
        Write-Host "No Legend archive backup needed restoring. Unsafe Legend DB expansion remains disabled." -ForegroundColor Green
    }
} catch {
    throw "Client DB recovery report could not be parsed; refusing to launch against an uncertain client DB state."
}
Write-Host "Legacy Legend experiments are disabled for the BETA branch. Stable packs/specials remain enabled." -ForegroundColor Yellow

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $installVerifyOutput = & $python $installVerify 2>&1
    $installVerifyExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($installVerifyExitCode -ne 0) {
    Write-Host "----- BETA 2.25.9 install verifier output -----" -ForegroundColor Yellow
    $installVerifyOutput | ForEach-Object { Write-Host $_ }
    Write-Host "-----------------------------------------------" -ForegroundColor Yellow
    throw "FIFA 14 Local FUT v2.41.1 BETA 2.25.9 runtime/install verification failed (exit $installVerifyExitCode)."
}
Write-Host "Stable FUT runtime verifier passed." -ForegroundColor Green
# Native stderr becomes a PowerShell ErrorRecord on Windows PowerShell. Under
# ErrorActionPreference=Stop that can terminate this assignment before we get a
# chance to print Python's actual traceback. Temporarily capture it normally.
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $betaVerifyOutput = & $python $betaVerify 2>&1
    $betaVerifyExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($betaVerifyExitCode -ne 0) {
    Write-Host "----- BETA progression verifier output -----" -ForegroundColor Yellow
    $betaVerifyOutput | ForEach-Object { Write-Host $_ }
    Write-Host "-------------------------------------------" -ForegroundColor Yellow
    throw "FIFA 14 Local FUT v2.41.1 BETA 2.25.9 progression verification failed (exit $betaVerifyExitCode)."
}
Write-Host "BETA 2.25.9 verification passed: native W/D/L bridge + badge activation + persistent squad guard + rendered-match/tournament baseline." -ForegroundColor Green

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $postMatchVerifyOutput = & $python $postMatchVerify 2>&1
    $postMatchVerifyExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($postMatchVerifyExitCode -ne 0) {
    Write-Host "----- BETA 2.25.9 full-match verifier output -----" -ForegroundColor Yellow
    $postMatchVerifyOutput | ForEach-Object { Write-Host $_ }
    Write-Host "-----------------------------------------------" -ForegroundColor Yellow
    throw "FIFA 14 Local FUT v2.41.1 BETA 2.25.9 full-match reward/return verification failed (exit $postMatchVerifyExitCode)."
}
Write-Host "BETA 2.25.9 full-match verifier passed: GameReporting terminal notification + native reward scalars + persistent tournament round advancement." -ForegroundColor Green

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $consumableVerifyOutput = & $python $consumableVerify 2>&1
    $consumableVerifyExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($consumableVerifyExitCode -ne 0) {
    Write-Host "----- BETA 2.25.9 consumables verifier output -----" -ForegroundColor Yellow
    $consumableVerifyOutput | ForEach-Object { Write-Host $_ }
    Write-Host "------------------------------------------------" -ForegroundColor Yellow
    throw "FIFA 14 Local FUT v2.41.1 BETA 2.25.9 consumables verification failed (exit $consumableVerifyExitCode)."
}
Write-Host "BETA 2.25.9 consumables verification passed: 151-definition union catalogue + functional application + Bronze/Silver/Gold pack pools." -ForegroundColor Green

# Windows PowerShell 5.1 turns native stderr redirected through 2>&1 into
# NativeCommandError records. With ErrorActionPreference=Stop that can abort
# the launcher before LASTEXITCODE is checked, even when Python is otherwise
# usable. Capture both verifier processes under Continue and gate only on the
# real process exit code.
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $packPerfVerifyOutput = & $python $packPerfVerify 2>&1
    $packPerfVerifyExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($packPerfVerifyExitCode -ne 0) {
    Write-Host "----- BETA 2.25.9 pack UI verifier output -----" -ForegroundColor Yellow
    $packPerfVerifyOutput | ForEach-Object { Write-Host $_ }
    Write-Host "------------------------------------------------" -ForegroundColor Yellow
    throw "FIFA 14 Local FUT v2.41.1 BETA 2.25.9 pack UI performance verification failed (exit $packPerfVerifyExitCode)."
}
Write-Host "BETA 2.25.9 pack UI performance verifier passed: hot reverse-engineering traces disabled; functional Store/tournament hooks preserved." -ForegroundColor Green

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $marketVerifyOutput = & $python $marketVerify 2>&1
    $marketVerifyExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($marketVerifyExitCode -ne 0) {
    Write-Host "----- BETA 2.25.9 transfer-market verifier output -----" -ForegroundColor Yellow
    $marketVerifyOutput | ForEach-Object { Write-Host $_ }
    Write-Host "-------------------------------------------------------" -ForegroundColor Yellow
    throw "FIFA 14 Local FUT v2.41.1 BETA 2.25.9 transfer-market/pack-weight verification failed (exit $marketVerifyExitCode)."
}
Write-Host "BETA 2.25.9 market verifier passed: full live market count + multi-listing supply + dynamic prices + local-bot auto-buy + max-two special pack contract." -ForegroundColor Green

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $regressionVerifyOutput = & $python $regressionVerify 2>&1
    $regressionVerifyExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if ($regressionVerifyExitCode -ne 0) {
    Write-Host "----- BETA 2.25.9 re-entry/consumables/transfer-list verifier output -----" -ForegroundColor Yellow
    $regressionVerifyOutput | ForEach-Object { Write-Host $_ }
    Write-Host "-----------------------------------------------------------------------" -ForegroundColor Yellow
    throw "FIFA 14 Local FUT v2.41.1 BETA 2.25.9 regression verification failed (exit $regressionVerifyExitCode)."
}
Write-Host "BETA 2.25.9 regression verifier passed: second-entry CardsDLL rearm + consumable inventory/stats + transfer pile 5 + Clear Sold route." -ForegroundColor Green

Write-Host "Scanning the retail patch archive for the normal FUT Store frontend (read-only diagnostic)..."
New-Item -ItemType Directory -Path (Split-Path -Parent $storeUiExtract) -Force | Out-Null
$storeUiJson = & $python $storeUiScanner --game-root $GameRoot --output $storeUiExtract
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Store frontend static scanner returned a non-zero status. FUT launch will continue."
} elseif ($storeUiJson) {
    try {
        $storeUiState = ($storeUiJson -join "`n") | ConvertFrom-Json
        if ($storeUiState.error) {
            Write-Warning ("Store frontend static scan could not inspect patch.big: " + $storeUiState.error)
        } else {
            Write-Host ("Store frontend static trace: " + $storeUiState.matchCount + " matching blobs from " + $storeUiState.recordCount + " patch records.")
        }
    } catch {
        Write-Warning "Store frontend scanner output could not be parsed; the raw artifact will still be included."
    }
}

Write-Host "Scanning the retail patch archive for Offline Seasons/Tournaments frontend contracts (read-only diagnostic)..."
New-Item -ItemType Directory -Path (Split-Path -Parent $competitionUiExtract) -Force | Out-Null
$competitionUiJson = & $python $competitionUiScanner --game-root $GameRoot --output $competitionUiExtract
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Competition frontend static scanner returned a non-zero status. FUT launch will continue."
} elseif ($competitionUiJson) {
    try {
        $competitionUiState = ($competitionUiJson -join "`n") | ConvertFrom-Json
        if ($competitionUiState.error) {
            Write-Warning ("Competition frontend static scan could not inspect patch.big: " + $competitionUiState.error)
        } else {
            Write-Host ("Competition frontend static trace: " + $competitionUiState.matchCount + " matching blobs from " + $competitionUiState.recordCount + " patch records.")
        }
    } catch {
        Write-Warning "Competition frontend scanner output could not be parsed; the raw artifact will still be included."
    }
}

Write-Host "Resolving native FIFA 14 kits, stadiums and badges from the installed retail databases (read-only)..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path (Split-Path -Parent $matchAssetReport) -Force | Out-Null
$matchAssetJson = & $python $matchAssetScanner --game-root $GameRoot --output $matchAssetReport
$matchAssetExitCode = $LASTEXITCODE
if (-not (Test-Path -LiteralPath $matchAssetReport)) {
    throw "Native match-asset scan did not produce $matchAssetReport. Refusing to launch with fabricated cosmetic IDs."
}
try {
    $matchAssetState = Get-Content -LiteralPath $matchAssetReport -Raw | ConvertFrom-Json
} catch {
    throw "Native match-asset report could not be parsed. Refusing to launch with uncertain cosmetic IDs."
}
if ($matchAssetExitCode -ne 0 -or -not $matchAssetState.validKits) {
    throw ("Could not resolve both native home/away kit assets from this FIFA 14 installation. Diagnostic report: " + $matchAssetReport)
}
$nativeHomeKit = $matchAssetState.resolved.homeKit
$nativeAwayKit = $matchAssetState.resolved.awayKit
$nativeStadium = $matchAssetState.resolved.stadium
Write-Host ("Native match assets resolved: home FUT asset={0}/resource={1}/category={2}/teamkitType={3}; away FUT asset={4}/resource={5}/category={6}/teamkitType={7}; stadium={8} ({9})." -f `
    $nativeHomeKit.assetId, $nativeHomeKit.resourceId, $nativeHomeKit.category, $nativeHomeKit.teamkittypetechid, `
    $nativeAwayKit.assetId, $nativeAwayKit.resourceId, $nativeAwayKit.category, $nativeAwayKit.teamkittypetechid, `
    $nativeStadium.stadiumid, $nativeStadium.name) -ForegroundColor Green
if ($matchAssetState.catalogCounts) {
    Write-Host ("Full My Club cosmetic catalogue: {0} kits, {1} stadiums, {2} badges." -f `
        $matchAssetState.catalogCounts.kits, $matchAssetState.catalogCounts.stadiums, $matchAssetState.catalogCounts.badges) -ForegroundColor Green
}

New-Item -ItemType Directory -Path $persistentDir -Force | Out-Null
# BETA 2.22: keep a last-known-good copy before any schema/state preparation.
# The active DB remains shared across extracted builds; this backup is only a
# safety net against an accidental future migration regression.
if (Test-Path -LiteralPath $identityDb -PathType Leaf) {
    $backupDir = Join-Path $persistentDir "backups"
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $identityBackup = Join-Path $backupDir "local-fut-beta-v2410-pre-beta223.sqlite3"
    Copy-Item -LiteralPath $identityDb -Destination $identityBackup -Force
    Write-Host ("Persistent club backup: " + $identityBackup) -ForegroundColor DarkGray
}
$stateArgs = @($statePrep, "--database", $identityDb)
if ($ResetIdentity) { $stateArgs += "--reset" }
$stateJson = & $python @stateArgs
if ($LASTEXITCODE -ne 0) { throw "Preparing persistent captain-to-hub state failed." }
$state = ($stateJson -join "`n") | ConvertFrom-Json
Write-Host ("Persistent FUT BETA profile: " + $state.profileKind)
Write-Host ("Startup route: " + $state.route)
Write-Host ("BETA starter club: " + $state.beta.ownedItems + " owned items; squad=" + $state.beta.squadPlayers + "/23; coins=" + $state.beta.coins + "; FIFA Points=" + $state.beta.fifaPoints + "; stadium=" + $state.beta.stadium) -ForegroundColor Green
Write-Host ("Fresh-profile economy: no developer coin grant; current balance=" + $state.beta.coins) -ForegroundColor Cyan
Write-Host ("Discord account link: " + $state.beta.authState + " (OAuth activation is not enabled in BETA 2.25.9)")
Write-Host ("Persistent BETA database: " + $identityDb)


if (-not $SkipStaticExtract) {
    # The restore helper deliberately refuses to write while fifa14.exe owns the
    # archive. Re-check here because dependency/diagnostic preparation can take
    # long enough for an old game process to be relaunched manually.
    Stop-Fifa14ForOnDiskPatch
    & $retailRestore -GameRoot $GameRoot -AllowUnknown
    & $retailVerify -GameRoot $GameRoot -AllowUnknown
    Write-Host "futPackSelect recovery/compatibility check completed."
}
if (-not $SkipBranchApply) {
    & $branchApply -Mode $Mode -GameRoot $GameRoot -DualArchive:$DualArchive -AllowUnknown
}

# V27 parity: fresh users take Icebreaker; persisted clubs use FIFA's retail returning-user route.
if (-not $SkipNavApply) {
    if ($state.hasClub) {
        & $python $dynamicRoute --game-root $GameRoot --state-dir $routeStateDir --restore-retail
        if ($LASTEXITCODE -ne 0) { throw "Restoring the retail returning-user NAV route failed." }
    } else {
        & $python $dynamicRoute --game-root $GameRoot --state-dir $routeStateDir --restore-icebreaker
        if ($LASTEXITCODE -ne 0) { throw "Installing the authenticated Icebreaker NAV route failed." }
    }
}

if (-not $SkipPopupApply) {
    & $popupApply -GameRoot $GameRoot
}
& $popupVerify -GameRoot $GameRoot

& $vp6Apply -GameRoot $GameRoot
& $vp6Verify -GameRoot $GameRoot

Write-Host ""
Write-Host "Starting FIFA 14 Local FUT v2.41.1 BETA 2.25.9 (post-match reporting/tournament hotfix; Transfer Market Economy BETA 2 preserved)."
Write-Host "Branch mode: $Mode"
Write-Host "Expected transition: returning-user hub -> persistent club -> Bronze/Silver/Gold packs -> Send to Club -> Apply Consumable -> match settlement without tournament regression"
Write-Host "During the delay, pass language selection and stop at the normal main menu."
Write-Host "Do not click Play FUT Now until the final READY line appears."
Write-Host "Shared-build economy: fresh clubs start at 0 coins; match rewards are server-ledgered and idempotent." -ForegroundColor Cyan
Write-Host "Metrics endpoint after READY: http://127.0.0.1:8099/local/beta/metrics" -ForegroundColor Cyan
& $traceScript -GameExe $GameExe -SecurityStartupDelaySeconds $SecurityStartupDelaySeconds -IdentityDb $identityDb
