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
$statePrep = Join-Path $PSScriptRoot "prepare_fifa14_fut_v37_state.py"
$dynamicRoute = Join-Path $PSScriptRoot "patch_fifa14_fut_dynamic_route.py"
$storeUiScanner = Join-Path $PSScriptRoot "extract_fifa14_fut_store_ui.py"
$competitionUiScanner = Join-Path $PSScriptRoot "extract_fifa14_fut_competition_ui.py"
$clientDbScanner = Join-Path $PSScriptRoot "scan_fifa14_client_db.py"
$legendDbPatcher = Join-Path $PSScriptRoot "patch_fifa14_fut_legends_db.py"
$storeUiExtract = Join-Path (Join-Path (Get-ProjectRoot) "artifacts") "fut-store-ui-static-extract.zip"
$competitionUiExtract = Join-Path (Join-Path (Get-ProjectRoot) "artifacts") "fut-competition-ui-static-extract.zip"
$retailRestore = Join-Path $PSScriptRoot "restore_fifa14_fut_packselect_retail_v19.ps1"
$retailVerify = Join-Path $PSScriptRoot "verify_fifa14_fut_packselect_retail_v19.ps1"
$vp6Apply = Join-Path $PSScriptRoot "apply_fifa14_fut_intro_vp6_v19.ps1"
$vp6Verify = Join-Path $PSScriptRoot "verify_fifa14_fut_intro_vp6_v19.ps1"
$python = Resolve-ProjectPython
$projectDir = Get-ProjectRoot
$persistentDir = Join-Path $env:LOCALAPPDATA "FIFA14LocalFUT"
$identityDb = Join-Path $persistentDir "local-fut-v237.sqlite3"
$fixturePath = Join-Path $projectDir "server\icebreakerpacklist.v27.json"
$legendCatalogPath = Join-Path $projectDir "server\fifa14-legend-catalog.v24013.json"
$baseCatalogPath = Join-Path $projectDir "server\fifa14-player-catalog.v237.json"
$legendDbReport = Join-Path $projectDir "artifacts\fifa14-legend-db-patch-v24022.json"
$routeStateDir = Join-Path $projectDir "artifacts\fut-dynamic-route-v237"

foreach ($required in @($branchApply, $popupApply, $popupVerify, $traceScript, $installVerify, $statePrep, $dynamicRoute, $storeUiScanner, $competitionUiScanner, $clientDbScanner, $legendDbPatcher, $retailRestore, $retailVerify, $vp6Apply, $vp6Verify, $fixturePath, $legendCatalogPath, $baseCatalogPath)) {
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

Write-Host "Recovering the retail FIFA client DB before launch..." -ForegroundColor Yellow
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
    throw "Legend recovery report could not be parsed; refusing to launch against an uncertain client DB state."
}
Write-Warning "Legends are temporarily disabled in v2.40.22.1. The stable FUT/pack runtime and corrected player subtype mapping remain enabled."

$clientDbScan = Join-Path (Join-Path $projectDir "artifacts") "fifa14-client-db-scan-v24022.json"
Write-Host "Scanning FIFA 14 client database/archive layout for Legend injection (read-only diagnostic)..."
& $python $clientDbScanner --game-root $GameRoot --output $clientDbScan
if ($LASTEXITCODE -ne 0) { Write-Warning "Client DB scan failed; FUT launch will continue." }

& $python $installVerify
if ($LASTEXITCODE -ne 0) { throw "FIFA 14 FUT v2.40.22.1 runtime/install verification failed." }

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

New-Item -ItemType Directory -Path $persistentDir -Force | Out-Null
$stateArgs = @($statePrep, "--database", $identityDb, "--fixture", $fixturePath, "--legend-client-report", $legendDbReport)
if ($ResetIdentity) { $stateArgs += "--reset" }
if ($SkipToHub) { $stateArgs += "--force-provision" }
$stateJson = & $python @stateArgs
if ($LASTEXITCODE -ne 0) { throw "Preparing persistent captain-to-hub state failed." }
$state = ($stateJson -join "`n") | ConvertFrom-Json
Write-Host ("Persistent FUT profile: " + $state.profileKind)
Write-Host ("Startup route: " + $state.route)
if ($state.importedFromPreviousBuild) {
    Write-Host ("Imported previous FUT identity database: " + $state.importedFromPreviousBuild)
}
if ($state.fullClubSeed) {
    # v2.40.17 compact-club migration no longer exposes the legacy full-seed
    # fields (rarePlayers/playersSeeded/legacyIntroItemsRemoved/squadRebuilt).
    # Only print fields guaranteed by provision_pack_play_club so StrictMode
    # cannot abort startup on a harmless status line.
    Write-Host ("Compact My Club: " + $state.fullClubSeed.ownedPlayers +
        " owned players; distinct resources=" + $state.fullClubSeed.distinctOwnedResources +
        "; base seed removed=" + $state.fullClubSeed.seededBaseRemoved +
        "; special seed removed=" + $state.fullClubSeed.seededSpecialsRemoved +
        "; historical duplicates removed=" + $state.fullClubSeed.historicalOwnedDuplicatesRemoved +
        "; Legend client DB ready=" + $state.fullClubSeed.legendClientDbReady +
        "; Legends owned=" + $state.fullClubSeed.legendPlayersOwned + "/" + $state.fullClubSeed.legendCatalogPlayers +
        "; Legends seeded=" + $state.fullClubSeed.legendPlayersSeeded +
        "; broken Legends removed=" + $state.fullClubSeed.legendPlayersRemoved +
        "; stale pack piles closed=" + $state.fullClubSeed.stalePacksClosed +
        "; stale New Items cleared=" + $state.fullClubSeed.stalePackItemsCleared +
        "; squad specials demoted=" + $state.fullClubSeed.squadSeededSpecialsDemotedToBase) -ForegroundColor Green
}
if ($state.cardRepair) {
    Write-Host ("V29 full-card repair: " + $state.cardRepair.reason +
        " (seeded=" + $state.cardRepair.playersSeeded +
        ", items repaired=" + $state.cardRepair.itemsRepaired +
        ", squad repaired=" + $state.cardRepair.squadRepaired + ")")
}
if ($state.ownedCardRepair) {
    Write-Host ("Owned player identity repair: " + $state.ownedCardRepair.itemsChanged + " changed / " + $state.ownedCardRepair.itemsInspected + " inspected")
}
if ($state.unopenedPackRepair) {
    Write-Host ("Unopened pack migration: " + $state.unopenedPackRepair.packsRebuilt + " packs rebuilt / " + $state.unopenedPackRepair.packsInspected + " inspected")
}
if ($state.testBalance) {
    Write-Host ("Local FUT test balance: " + $state.testBalance.credits + " coins")
}
if ($state.snapshot.club) {
    Write-Host ("Preserved club: " + $state.snapshot.club.clubName + " (" + $state.snapshot.club.clubAbbr + ")")
}

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
        & $python $dynamicRoute --game-root $GameRoot --state-dir $routeStateDir --restore-retail --allow-unknown
        if ($LASTEXITCODE -ne 0) { throw "Restoring the retail returning-user NAV route failed." }
    } else {
        & $python $dynamicRoute --game-root $GameRoot --state-dir $routeStateDir --restore-icebreaker --allow-unknown
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
Write-Host "Starting FIFA 14 FUT hub + store v2.40.22.1 recovery + special-static subtype fix (stable pack/runtime baseline preserved)."
Write-Host "Branch mode: $Mode"
Write-Host "Expected transition: returning-user hub -> compact My Club -> Legends disabled pending safer client integration -> stable V2 Store/pack catalogue"
Write-Host "During the delay, pass language selection and stop at the normal main menu."
Write-Host "Do not click Play FUT Now until the final READY line appears."
& $traceScript -GameExe $GameExe -SecurityStartupDelaySeconds $SecurityStartupDelaySeconds -IdentityDb $identityDb
