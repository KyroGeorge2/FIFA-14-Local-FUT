#!/usr/bin/env python3
from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from fifa14_ids import decode_resource_id, definition_id_for, resource_id_for  # noqa: E402



def require(path: Path, markers=()):
    if not path.is_file():
        raise RuntimeError(f"missing required file: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise RuntimeError(f"{path.name} missing markers: {missing}")


def main() -> int:
    probe = ROOT / "server" / "probe.py"
    identity = ROOT / "server" / "local_identity.py"
    ids = ROOT / "server" / "fifa14_ids.py"
    fixture = ROOT / "server" / "icebreakerpacklist.v27.json"
    player_catalog = ROOT / "server" / "fifa14-player-catalog.v237.json"
    special_catalog = ROOT / "server" / "fifa14-special-catalog.v240.json"
    legend_catalog = ROOT / "server" / "fifa14-legend-catalog.v24013.json"
    consumable_catalog = ROOT / "server" / "fifa14-consumable-catalog.v2412.json"
    pack_catalog = ROOT / "server" / "pack-catalog.v237.json"
    pack_weights = ROOT / "server" / "pack-weights.v237.json"
    managers = ROOT / "server" / "manager-catalog.v237.json"
    prep = ROOT / "tools" / "prepare_fifa14_fut_v37_state.py"
    recovery = ROOT / "tools" / "recover_fifa14_fut_frontend.ps1"
    pack_v18 = ROOT / "tools" / "patch_fifa14_fut_packselect_force_dock_ready_v18.py"
    pack_v17 = ROOT / "tools" / "patch_fifa14_fut_packselect_safe_relay_v17.py"
    pack_v16 = ROOT / "tools" / "patch_fifa14_fut_packselect_no_cinematic_v16.py"
    setup = ROOT / "tools" / "setup.ps1"
    run_script = ROOT / "tools" / "run_fifa14_fut_hub_store.ps1"
    beta_run_script = ROOT / "tools" / "run_fifa14_local_beta.ps1"
    route_watcher = ROOT / "tools" / "watch_fifa14_fut_v37_route.py"
    trace_script = ROOT / "tools" / "trace_fifa14_fut_hub_store.ps1"
    client_db_scanner = ROOT / "tools" / "scan_fifa14_client_db.py"
    legend_db_patcher = ROOT / "tools" / "patch_fifa14_fut_legends_db.py"
    competition_ui_scanner = ROOT / "tools" / "extract_fifa14_fut_competition_ui.py"
    match_asset_scanner = ROOT / "tools" / "scan_fifa14_match_assets.py"
    frida_trace = ROOT / "tools" / "frida_pc_fut_nav_route_patch_trace.py"
    beta_identity = ROOT / "server" / "beta_identity.py"
    consumable_verify = ROOT / "tools" / "verify_fifa14_consumables_beta224.py"
    market_verify = ROOT / "tools" / "verify_fifa14_market_beta2250.py"

    require(probe, (
        "icebreakerpacklist.v27.json", "ICEBREAKER_ENGLISH_CAPTAIN_SELECTED",
        "provision_pack_play_club", "local-store-pack-purchase",
        "store-pack-types-v237", "packs/loc/storepackdescriptions.", "_local_ui_locstrings_payload", "_is_fut_localization_path",
        '"/ut/game/fifa14/managerquest"', "save_squad", "trusted_device",
        "save_client_data", "update_club_profile", "club_stats",
        'path_without_query == "/ut/game/fifa14/hub"', "fut-hub-data-native",
        "_leaderboards_locstrings_payload", "fut-club-query-response-v237",
        "fut-static-image-miss", "_fut_player_metadata_payload",
        "fut-trophy-archive-empty-big-success", "_empty_bigf_archive", "FIFA14_TROPHY_ARCHIVE_MODE",
        'path_without_query == "/ut/game/fifa14/tournament/teams"', "fut-offline-tournament-teams-beta28-native",
        "FIFA14LocalFUT/2.41.1-beta2.25.9", "/__fifa14_local_fut_health",
        "ExclusiveThreadingHTTPServer", "SO_EXCLUSIVEADDRUSE",
        '"buildVersion": "2.41.1-beta2.25.9"', "v237-health-response",
        "local-consumable-apply-beta224", "fut-local-consumable-apply-beta224",
        "item/resource", "apply_consumable",
    ))
    require(ids, ("FIFA14_RESOURCE_BASE = 0x60000000", "resource_id_for", "decode_resource_id"))
    require(identity, (
        "fifa14-v237-capture-fixed-full-itemdata", "repair_known_good_roster",
        "PLAYER_ITEM_TYPE", "player_card_subtype", "MARKET_PLAYER_CATALOG", "TRANSFER_LIST_CAPACITY",
        "MIN_RECOGNIZED_SQUAD_PLAYERS", "cardsubtypeid", "resourceGameYear",
        "statsList", "lifetimeStats", "PLAYER_CATALOG", "resource_id_for", "nation", "leagueId",
        "purchase_pack", "move_items", "save_squad", "trusted_device",
        "save_client_data", "update_club_profile", "club_stats",
        "provision_pack_play_club", "repair_catalogued_owned_players", "repair_unopened_pack_contents",
        "manager_reference_catalog", "quality_pool", "pack tier mismatch",
        "PACK_FIDELITY_SCHEMA", "quick_sell", "Duplicate Item Type",
        "LEGEND_PLAYER_CATALOG", "NORMAL_SPECIAL_PLAYER_CATALOG", "CONSUMABLE_CATALOG", "CONSUMABLE_BY_RESOURCE",
        "apply_consumable", "consumable_effects", "packEligible", "GK Training",
        "specialChancePerPack", "secondSpecialChanceGivenFirst", "maxSpecialsPerPack", "legendChancePerPack", "LOCAL_PACK_NAME_", "FUT_STORE_PACK_",
        "market_search", "market_bid", "list_for_sale", "trade_pile", "withdraw_listing",
        "pile='trade'", 'trade_state="inactive"', "unlisted",
    ))
    require(prep, (
        "find_previous_hub_database", 'destination.parent.glob("local-fut-v*.sqlite3")',
        "repair_known_good_roster", "repair_catalogued_owned_players",
        "repair_unopened_pack_contents", "ensure_local_test_balance",
        "provision_pack_play_club", "importedFromPreviousBuild", "--legend-client-report", "verifiedAllLegends",
    ))
    require(recovery, ("AllowMissingBackup", "patch_fifa14_fut_spinner.py", "--restore"))
    require(pack_v18, ("patch_fifa14_fut_packselect_safe_relay_v17.py", "--restore", "retail-original"))
    require(pack_v17, ("patch_fifa14_fut_packselect_no_cinematic_v16.py", "RETAIL_APT_SHA256", "recover_retail_decoded"))
    require(pack_v16, ("RETAIL_APT_SHA256", "RETAIL_DECODED_SHA256", "retail-original"))
    pack_restore_wrapper = ROOT / "tools" / "restore_fifa14_fut_packselect_retail_v19.ps1"
    pack_verify_wrapper = ROOT / "tools" / "verify_fifa14_fut_packselect_retail_v19.ps1"
    require(pack_restore_wrapper, ("AllowUnknown", "inspection is unavailable", "continuing startup in unverified compatibility mode", "No recovery bytes were written"))
    require(pack_verify_wrapper, ("AllowUnknown", "Skipping exact-retail futPackSelect verification in unverified compatibility mode", "No unknown archive bytes were overwritten"))
    require(setup, ("Resolve-Fifa14Paths", "Save-Fifa14Config", "RUN_FIFA14_LOCAL_BETA.cmd"))
    setup_text = setup.read_text(encoding="utf-8", errors="replace")
    if "-PromptIfMissing" not in setup_text or "config.local.psd1" not in (ROOT / "tools" / "common.ps1").read_text(encoding="utf-8", errors="replace"):
        raise RuntimeError("configurable FIFA 14 path setup is incomplete")
    launcher_text = (ROOT / "tools" / "launch_fifa14_hub_store.ps1").read_text(encoding="utf-8", errors="replace")
    if "Resolve-Fifa14Paths -PromptIfMissing -PersistDetected" not in launcher_text:
        raise RuntimeError("normal launcher does not auto-detect/persist the FIFA 14 Game directory")
    if '-GameRoot $GameRoot -GameExe $GameExe' not in launcher_text:
        raise RuntimeError("normal launcher does not pass the resolved FIFA path into the beta runner")
    if 'Get-Process -Name "fifa14"' not in launcher_text or 'Stop-Process -Force' not in launcher_text:
        raise RuntimeError("normal launcher does not close stale fifa14.exe before archive/database patching")
    require(run_script, ("dlc\\dlc_powdll\\dlc\\powdll\\powdllzf.dll", 'Join-Path $GameRoot "powdllzf.dll"', "Resolved powdllzf.dll", "hash enforcement is disabled",
                         "Compact My Club:", "distinctOwnedResources", "legendPlayersRemoved", "stalePacksClosed", "stalePackItemsCleared",
                         "fifa14-legend-db-patch-v24022.json", "fifa14-client-db-scan-v24022.json",
                         "extract_fifa14_fut_competition_ui.py", "fut-competition-ui-static-extract.zip",
                         "SecurityStartupDelaySeconds = 15",
                         "Stop-Fifa14ForOnDiskPatch", 'Get-Process -Name "fifa14"', "Stop-Process -Force"))
    require(beta_run_script, (
        "scan_fifa14_match_assets.py", "fifa14-match-assets-v2411-beta222.json",
        "Native match assets resolved", "Refusing to launch with fabricated cosmetic IDs",
        "Full My Club cosmetic catalogue", "SecurityStartupDelaySeconds = 15",
        "local-fut-beta-v2410-pre-beta223.sqlite3", "Persistent club backup",
        "verify_fifa14_consumables_beta224.py", "151-definition union catalogue",
        "verify_fifa14_market_beta2250.py", "market verifier passed",
    ))
    require(consumable_verify, (
        "catalogDefinitions", "packEligibleDefinitions", "gkTrainingDefinitions",
        "player training expiry", "GK training", "pack pools",
    ))
    require(market_verify, (
        "basePlayersOnMarket", "normalRonaldoReference", "syntheticLiveTransfers",
        "preferredPosition", "market win missing from New Items", "userAutoBuyBand",
        "marketSellTaxPct", "maxSpecialsPerPack",
    ))
    require(match_asset_scanner, (
        "exact_fut_kit_card", "full_cosmetic_catalog", "fcc_kitcards", "fcc_stadium", "fcc_badgecards",
        "category=2", "category=3", "catalogCounts",
        "resourceId/definitionId uses the unique fcc carddbid",
    ))
    run_text = run_script.read_text(encoding="utf-8", errors="replace")
    stale_full_seed_fields = ("$state.fullClubSeed.rarePlayers", "$state.fullClubSeed.playersSeeded",
                              "$state.fullClubSeed.legacyIntroItemsRemoved", "$state.fullClubSeed.squadRebuilt")
    stale = [field for field in stale_full_seed_fields if field in run_text]
    if stale:
        raise RuntimeError(f"launcher still references removed v2.40.14 fullClubSeed fields: {stale}")
    require(competition_ui_scanner, (
        "fifa14-beta28-competition-static-trace", "futOfflineSeasonEntry",
        "THRESHOLD_CHAMPION", "TROPHY_PACK_ID",
        "EVENT_CARDS_TOURNAMENT_LIST_SUCCESS", "GetOfflineTournamentInfo",
    ))
    require(frida_trace, (
        "noteCompetitionHttpRequest", "season-list", "season-user",
        "tournament-list", "tournament-teams", "tournament-user-list",
        "competition-v3-match-postmatch-v1", "durationMs || 15000",
        "fifa-postmatch-return-guard-armed-beta223", "fifa-postmatch-fcc-logout-redirect-beta223",
        "postMatchStayInFutView", "ut/game/fifa14/match/end",
        "GET_STADIUM_ID_WRAPPER_RVA", "STADIUM_PROVIDER_GLOBAL_RVA", "LOCAL_OFFLINE_STADIUM_ID",
        "cards-stadium-provider-get-beta222", "cards-stadium-provider-poll-beta222",
        "cards-match-bridge-enter-beta222", "cards-get-stadium-script-result-beta222", "mscdecl",
        "cards-activate-card-accessor-enter-beta222", "cards-activate-card-status-enter-beta222",
        "VIEW_CARDS_WRAPPER_RVA", "VIEW_CARDS_PATH_BUILDER_RVA", "ACTIVATE_CARD_PATH_BUILDER_RVA",
        "cards-view-cards-wrapper-enter-beta222", "cards-view-cards-path-enter-beta222",
        "cards-view-cards-empty-list-guard-ready-beta222", "focused-home-kit-fallback",
        "fifa14-native-exception-beta222", "ActivateCard", "ViewCards", "operation_index:45",
        "USER_RECORD_COPY_RVA", "cards-native-record-builder-enter-beta222",
        "cards-native-record-source-override-beta222", "cards-native-record-copy-override-beta222",
        "record-copy-object-override", "cards-native-record-override-beta222", "setrecord", "--identity-db",
    ))
    require(beta_identity, (
        "fifa14-local-fut-v2.41.1-beta2.24", "pointsToWinTitle",
        "pointsToAvoidRelegation", "FIFA14_TOURNAMENT_MODE",
        "OFFLINE_COMPETITION_TEAM_IDS", "offline_tournament_teams", "rewardMult",
        "MATCH_ASSET_REPORT", "beta222_cosmetic_catalog_signature", "activeHomeKit", "activeAwayKit", "activeStadium", "activeBadge",
        "beta222_persistent_club_guard", "include_badge", "badgeId", "badgeDBid",
        "_tournament_progress_is_resumable", "AAAAAA==", "endReason", "secondsPlayed", "matchDifficulty", "match_ready", "view_items",
    ))
    require(route_watcher, ("v237-route-watch-started", "v237-route-restored-after-club-save", "--restore-retail"))
    require(trace_script, (
        "watch_fifa14_fut_v37_route.py", "routeWatcher", "club-save/returning-route watcher",
        "Assert-FifaLocalPortsFree", "Stop-StaleHealthBackend", "Assert-BetaBackendOwnership",
        "Backend ownership verified: v2.41.1-beta2.25.9 launch token matched", "fifa14-local-fut-beta-v2411-beta2259-results.zip",
        "PORT CONFLICT", "stale-helper-stopped", "instanceToken", "launch-instance-token",
        "fifa14-client-db-scan-v24022.json", "fifa14-legend-db-patch-v24022.json",
        "fut-competition-ui-static-extract.zip", "fifa14-match-assets-v2411-beta222.json", "SecurityStartupDelaySeconds = 15",
    ))
    common_script = ROOT / "tools" / "common.ps1"
    require(common_script, ("Get-LocalConfig", "Save-Fifa14Config", "FIFA14_GAME_ROOT", "Get-Fifa14AutoDetectCandidates", "PromptIfMissing", "PersistDetected"))
    require(ROOT / "config.local.psd1.example", ("GameRoot", "GameExe"))
    if "config.local.psd1" not in (ROOT / ".gitignore").read_text(encoding="utf-8", errors="replace"):
        raise RuntimeError("config.local.psd1 must be ignored by Git")
    patcher_test = subprocess.run([sys.executable, str(legend_db_patcher), "--self-test"],
                                  capture_output=True, text=True, check=False)
    if patcher_test.returncode != 0:
        raise RuntimeError("Legend DB patcher self-test failed: " + (patcher_test.stderr or patcher_test.stdout).strip())
    patcher_state = json.loads(patcher_test.stdout)
    if not patcher_state.get("ok") or not patcher_state.get("hashedDirectRecords"):
        raise RuntimeError(f"Legend DB patcher hashed-record self-test did not pass: {patcher_state}")

    fixture_doc = json.loads(fixture.read_text(encoding="utf-8"))
    packs = fixture_doc.get("packList")
    if not isinstance(packs, list) or len(packs) != 4:
        raise RuntimeError("Icebreaker fixture must have four packs")
    canonical_assets = [int(value) for value in packs[1]["squad"]]
    if len(canonical_assets) != 23 or any(value <= 0 for value in canonical_assets):
        raise RuntimeError("canonical V27 fixture must contain 23 non-zero asset IDs")

    doc = json.loads(player_catalog.read_text(encoding="utf-8"))
    players = doc.get("players")
    if not isinstance(players, list) or len(players) < 10000:
        raise RuntimeError("FIFA 14 v2.40 full-base player catalogue must contain at least 10,000 resolved base players")
    by_asset = {int(player["assetId"]): player for player in players}
    if not set(canonical_assets).issubset(by_asset):
        raise RuntimeError("player catalogue does not contain all 23 proven V27 squad asset IDs")
    if len(by_asset) != len(players):
        raise RuntimeError("duplicate assetId in FIFA 14 player catalogue")
    goalkeepers = [player for player in players if str(player.get("position", "")).upper() == "GK"]
    if len(goalkeepers) < 50:
        raise RuntimeError(f"goalkeeper catalogue regression: only {len(goalkeepers)} GK rows")

    tier_counts = {"bronze": 0, "silver": 0, "gold": 0}
    for player in players:
        asset = int(player["assetId"]); version = int(player.get("version", 1))
        resource = int(player["resourceId"]); definition = int(player["definitionId"]); rating = int(player["rating"])
        calculator_resource = int(player.get("calculatorResourceId", 0))
        expected_item_resource = asset if version == 1 else resource_id_for(asset, version)
        if resource != expected_item_resource:
            raise RuntimeError(f"base ItemData resourceId mismatch for {player.get('name')}: {resource}")
        if calculator_resource != resource_id_for(asset, version):
            raise RuntimeError(f"calculatorResourceId mismatch for {player.get('name')}: {calculator_resource}")
        if definition != definition_id_for(asset, version):
            raise RuntimeError(f"definitionId mismatch for {player.get('name')}: {definition}")
        decoded = decode_resource_id(calculator_resource)
        if (decoded.asset_id, decoded.version) != (asset, version):
            raise RuntimeError(f"calculatorResourceId does not round-trip for {player.get('name')}")
        if int(player.get("nation", 0)) <= 0 or int(player.get("leagueId", 0)) <= 0:
            raise RuntimeError(f"numeric nation/league missing for {player.get('name')}")
        if len(player.get("attributes", [])) != 6:
            raise RuntimeError(f"invalid six-attribute catalogue entry for {player.get('name')}")
        expected_quality = "bronze" if rating <= 64 else "silver" if rating <= 74 else "gold"
        if str(player.get("quality")) != expected_quality:
            raise RuntimeError(f"quality mismatch for {player.get('name')}")
        tier_counts[expected_quality] += 1
    if tier_counts["bronze"] < 3 or tier_counts["silver"] < 4 or tier_counts["gold"] < 24:
        raise RuntimeError(f"catalogue tier seeds are incomplete: {tier_counts}")

    expected = {
        158023: ("Lionel Messi", 94, "CF", 241, 53),
        190871: ("Neymar", 84, "LW", 241, 53),
        20801: ("Cristiano Ronaldo", 92, "LW", 243, 53),
        41236: ("Zlatan Ibrahimović", 89, "ST", 73, 16),
        189505: ("Pedro", 85, "LW", 241, 53),
        176580: ("Luis Suárez", 86, "CF", 9, 13),
        207494: ("Jesse Lingard", 63, "CAM", 11, 13),
        199564: ("Sergi Roberto", 74, "CM", 241, 53),
    }
    for asset, wanted in expected.items():
        player = by_asset.get(asset)
        if player is None:
            raise RuntimeError(f"verified FIFA 14 player {asset} missing")
        observed = (str(player["name"]), int(player["rating"]), str(player["position"]), int(player["teamId"]), int(player["leagueId"]))
        if observed != wanted:
            raise RuntimeError(f"verified player {asset} mismatch: {observed} != {wanted}")


    special_doc = json.loads(special_catalog.read_text(encoding="utf-8"))
    specials = special_doc.get("players")
    if not isinstance(specials, list) or len(specials) < 1800:
        raise RuntimeError("FIFA 14 v2.40 special catalogue must contain at least 1,800 conservatively matched variants")
    rare_types = set()
    for player in specials:
        asset = int(player["assetId"]); version = int(player["version"]); resource = int(player["resourceId"])
        if asset not in by_asset:
            raise RuntimeError(f"special card base asset missing: {asset}")
        if version <= 1 or resource != definition_id_for(asset, version):
            raise RuntimeError(f"special definition/revision identity mismatch for {player.get('name')}")
        if int(player.get("rareFlag", 0)) <= 1:
            raise RuntimeError(f"special card has base rareFlag for {player.get('name')}")
        rare_types.add(int(player["rareFlag"]))
    if not {3, 5, 7, 8, 11, 13}.issubset(rare_types):
        raise RuntimeError(f"special rarity coverage incomplete: {sorted(rare_types)}")

    legend_doc = json.loads(legend_catalog.read_text(encoding="utf-8"))
    legends = legend_doc.get("players")
    if not isinstance(legends, list) or len(legends) != 42:
        raise RuntimeError("FIFA 14 legend catalogue must contain exactly 42 cards")
    legend_names = {str(player.get("name")) for player in legends}
    for required in {"Pelé", "Paolo Maldini", "Ruud Gullit", "Romário", "Luís Figo", "Pavel Nedvěd"}:
        if required not in legend_names:
            raise RuntimeError(f"required FIFA 14 Legend missing: {required}")
    for player in legends:
        asset = int(player["assetId"]); version = int(player.get("version", 0)); resource = int(player["resourceId"])
        if version != 1 or resource != asset or int(player.get("definitionId", 0)) != asset:
            raise RuntimeError(f"Legend native-PID identity mismatch for {player.get('name')}")
        if str(player.get("cardType", "")).lower() != "legend" or int(player.get("rareFlag", 0)) != 12:
            raise RuntimeError(f"Legend rarity/type mismatch for {player.get('name')}")

    consumable_doc = json.loads(consumable_catalog.read_text(encoding="utf-8"))
    consumables = consumable_doc.get("items")
    if not isinstance(consumables, list) or len(consumables) != 151:
        raise RuntimeError(f"BETA 2.24 consumable union catalogue must contain exactly 151 definitions, got {0 if not isinstance(consumables, list) else len(consumables)}")
    consumable_categories = {str(row.get("category")) for row in consumables}
    required_categories = {"Contract", "Fitness", "Healing", "Training", "GK Training", "Positioning", "Chemistry Style", "Manager League", "Internal GK PlayStyle", "Internal Position"}
    if not required_categories.issubset(consumable_categories):
        raise RuntimeError(f"consumable category coverage incomplete: {sorted(consumable_categories)}")
    pack_eligible = [row for row in consumables if bool(row.get("packEligible", True))]
    if len(pack_eligible) != 128:
        raise RuntimeError(f"BETA 2.24 must expose exactly 128 proven retail pack consumables, got {len(pack_eligible)}")
    gk_training = [row for row in consumables if str(row.get("category")) == "GK Training"]
    if len(gk_training) != 21 or not all(bool(row.get("packEligible")) and bool(row.get("applicationSupported")) for row in gk_training):
        raise RuntimeError("BETA 2.24 GK Training catalogue must contain 21 pack/apply-enabled definitions")
    held_back = [row for row in consumables if str(row.get("category")) in {"Internal GK PlayStyle", "Internal Position"}]
    if len(held_back) != 22 or any(bool(row.get("packEligible")) or bool(row.get("applicationSupported")) for row in held_back):
        raise RuntimeError("the 22 unmapped internal consumables must remain catalogued but excluded from packs/application")
    contract_99 = next((row for row in consumables if int(row.get("resourceId", 0)) == 5001013), None)
    if not contract_99 or bool(contract_99.get("packEligible")) or not bool(contract_99.get("applicationSupported")):
        raise RuntimeError("99-match player contract must remain applyable but excluded from normal pack pools")

    # Exact original calculator example: this proves versioned cards are distinct.
    if resource_id_for(20801, 5) != 1711296833 or definition_id_for(20801, 5) != 100684097:
        raise RuntimeError("FIFA 14 fut-calculator compatibility check failed")

    catalog = json.loads(pack_catalog.read_text(encoding="utf-8"))
    definitions = catalog.get("packs")
    if not isinstance(definitions, list) or len(definitions) < 10:
        raise RuntimeError("pack catalogue is incomplete")
    required_names = {
        "Bronze Pack", "Premium Bronze Pack", "Silver Pack", "Premium Silver Pack",
        "Gold Pack", "Premium Gold Pack", "Jumbo Premium Gold Pack",
        "Premium Gold Players Pack", "Rare Players Pack", "Jumbo Rare Players Pack",
    }
    if not required_names.issubset({str(p["name"]) for p in definitions}):
        raise RuntimeError("pack catalogue is missing required FIFA 14 store packs")
    if any("downgrade" in str(p.get("safeLivePool", "")).lower() for p in definitions):
        raise RuntimeError("synthetic bronze/silver downgrade pool is still configured")

    weights = json.loads(pack_weights.read_text(encoding="utf-8"))
    if not isinstance(weights.get("ratingBands"), list) or len(weights["ratingBands"]) < 4:
        raise RuntimeError("local pack-weight table is missing gold rating bands")
    if int(weights.get("localTestStartingCoins", 0)) <= 0:
        raise RuntimeError("local test starting balance must be positive")
    special_per_pack = weights.get("specialChancePerPack")
    legend_per_pack = weights.get("legendChancePerPack")
    if not isinstance(special_per_pack, dict) or float(special_per_pack.get("6", 0.0)) <= 0.0:
        raise RuntimeError("Premium Gold per-pack special probability is not configured")
    if not isinstance(legend_per_pack, dict) or "6" not in legend_per_pack:
        raise RuntimeError("Premium Gold per-pack Legend probability is not configured")
    premium = next((row for row in definitions if int(row.get("packType", 0)) == 6), None)
    if premium is None or not str(premium.get("description", "")).strip():
        raise RuntimeError("Premium Gold pack description metadata is missing")

    # v2.40.9's retail Store APT trace proved actionType is surfaced as
    # POST_PURCHASE_ACTION and the pack-purchase branch recognises CREATEPACK.
    # Verify the shipped source contains that contract and cannot regress to
    # the old invented BUY_PACK value.
    identity_source = identity.read_text(encoding="utf-8", errors="replace")
    if '"actionType": "CREATEPACK"' not in identity_source:
        raise RuntimeError("Store offers do not advertise retail POST_PURCHASE_ACTION=CREATEPACK")
    if '"actionType": "BUY_PACK"' in identity_source:
        raise RuntimeError("stale invented Store actionType=BUY_PACK is still active")

    # v2.40.11 regression: the real FIFA 14 PC client purchases packs by
    # POSTing directly to /purchased/items. That method must reach
    # purchase_pack(); a read-only handler recreates the exact v2.40.10
    # symptom (New Items=0 and unchanged coins).
    probe_source = probe.read_text(encoding="utf-8", errors="replace")
    if 'LOCAL_PACK_NAME_' not in probe_source or 'LOCAL_PACK_DESC_' not in probe_source or '_locstrings_payload(strings, target="storepackdescriptions")' not in probe_source:
        raise RuntimeError("Store pack localization metadata route is incomplete")
    if '<trans-unit id=' in probe_source:
        raise RuntimeError("stale Store trans-unit localization format is still active")
    required_purchase_route_markers = (
        'if effective_method in {"POST", "PUT"}:',
        'response = identity_store.purchase_pack(raw_pack_type, currency=currency)',
        'response_name = "local-purchased-items-pack-purchase"',
        'response = identity_store.purchased_items()',
        'response = identity_store.quick_sell(item_ids)',
        'response_name = "local-item-quicksell"',
        'path_without_query == "/ut/delete/game/fifa14/item" and effective_method == "POST"',
        'local-view-cards-beta222', 'fut-view-cards-response-beta222', 'idList',
        'document.get("itemId", document.get("itemIds", document.get("itemData", [])))',
    )
    missing_route_markers = [m for m in required_purchase_route_markers if m not in probe_source]
    if missing_route_markers:
        raise RuntimeError(f"/purchased/items purchase-commit route is incomplete: {missing_route_markers}")

    # v2.40.19 executable regression: My Club stays compact, the broken
    # server-only Legend test rows are absent, specials remain pack-eligible,
    # and only one unresolved New Items pile may exist at a time.
    import tempfile
    from local_identity import LocalIdentityStore  # noqa: E402
    import sqlite3
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Path(tmpdir) / "fut-v24019.sqlite3"
        test_store = LocalIdentityStore(test_db, "existing")
        seed_result = test_store.provision_pack_play_club()
        if int(seed_result.get("legendPlayersOwned", -1)) != 0:
            raise RuntimeError(f"broken Legend test rows must not be owned: {seed_result}")
        if int(seed_result.get("ownedPlayers", 0)) != 23 or int(seed_result.get("distinctOwnedResources", 0)) != 23:
            raise RuntimeError(f"compact fresh club should contain only the 23 squad cards: {seed_result}")
        if int(seed_result.get("specialPackPoolPlayers", 0)) < 1000:
            raise RuntimeError(f"special pack pool disappeared during club compaction: {seed_result}")

        # v2.40.20 gate: direct Legend ownership is enabled only when a client
        # database report has verified every one of the 42 player IDs. Test the
        # server-side half independently without touching a real FIFA archive.
        ready_db = Path(tmpdir) / "fut-v24020-legends-ready.sqlite3"
        ready_store = LocalIdentityStore(ready_db, "existing")
        legend_ids = {int(row["assetId"]) for row in legends}
        ready_seed = ready_store.provision_pack_play_club(
            legend_client_ready=True, legend_asset_ids=legend_ids
        )
        if ready_seed.get("legendClientDbReady") is not True or int(ready_seed.get("legendPlayersOwned", 0)) != 42:
            raise RuntimeError(f"verified Legend client DB did not grant all 42 direct My Club test cards: {ready_seed}")
        with closing(sqlite3.connect(ready_db)) as ready_conn:
            legend_rows = ready_conn.execute(
                "SELECT asset_id,payload FROM items WHERE item_id>=? AND item_id<? ORDER BY item_id",
                (173000000000, 173000001000),
            ).fetchall()
        if len(legend_rows) != 42:
            raise RuntimeError(f"Legend client-ready club has {len(legend_rows)} rows instead of 42")
        pele_row = next((row for row in legend_rows if int(row[0]) == 190043), None)
        if pele_row is None:
            raise RuntimeError("Pelé was not seeded into the client-ready My Club")
        pele_payload = json.loads(pele_row[1])
        if str(pele_payload.get("name")) != "Pelé" or str(pele_payload.get("preferredPosition")) != "CF" or int(pele_payload.get("rating", 0)) != 95:
            raise RuntimeError(f"Pelé direct-club payload is malformed: {pele_payload}")
        if int(pele_payload.get("rareFlag", 0)) != 12 or int(pele_payload.get("resourceId", 0)) != 190043:
            raise RuntimeError(f"Pelé Legend identity is malformed: {pele_payload}")

        import local_identity as local_identity_module
        if any(float(value) != 0.0 for value in local_identity_module.PACK_WEIGHTS_DOCUMENT.get("legendChancePerPack", {}).values()):
            raise RuntimeError("Legend pack odds must stay zero until PC client DB support exists")

        before = test_store.ensure_local_test_balance()["credits"]
        purchase = test_store.purchase_pack(6, currency="COINS")
        pack_items = purchase.get("itemData", [])
        if int(purchase.get("credits", -1)) != before - 7500:
            raise RuntimeError("Premium Gold purchase did not deduct exactly 7,500 coins")
        if len(pack_items) != 12:
            raise RuntimeError(f"Premium Gold pack must contain 12 items, got {len(pack_items)}")
        pack_players = [row for row in pack_items if str(row.get("itemType", "")).lower() == "player"]
        if len(pack_players) != 3:
            raise RuntimeError(f"Premium Gold local fidelity pack must contain 3 players, got {len(pack_players)}")
        if any(str(row.get("cardType", "")).lower() == "legend" for row in pack_players):
            raise RuntimeError("Legend leaked into a pack while Legends are disabled")
        if sum(int(row.get("rareFlag", row.get("rareflag", 0))) > 0 for row in pack_items) != 3:
            raise RuntimeError("Premium Gold pack does not contain exactly 3 rare items")
        if any(bool(row.get("untradeable", True)) or not bool(row.get("tradeable", False)) for row in pack_items):
            raise RuntimeError("purchased pack emitted an untradeable item")
        if any(int(row.get("discardValue", 0)) <= 0 for row in pack_items):
            raise RuntimeError("purchased pack emitted a zero/negative discard value")
        if pack_items[0].get("itemType") != "player" or int(pack_items[0].get("rating", 0)) != max(int(row.get("rating", 0)) for row in pack_players):
            raise RuntimeError("pack hero/reveal item is not the highest-rated player")

        # v2.40.18 regression: generator-only description fields must never
        # leak into consumable wire payloads. In particular, FIFA 14 PC froze
        # inside the purchased-items parser on the first captured Positioning
        # card carrying a Unicode arrow such as RW\u2192RF.
        utility_items = [row for row in pack_items if str(row.get("itemType", "")).lower() != "player"]
        forbidden_wire_keys = {"category", "kind", "quality", "class", "dataSource", "localPackSchema"}
        leaked = [(row.get("resourceId"), sorted(forbidden_wire_keys.intersection(row))) for row in utility_items if forbidden_wire_keys.intersection(row)]
        if leaked:
            raise RuntimeError(f"local consumable description fields leaked onto the FUT wire: {leaked}")
        wire_json = json.dumps(test_store.purchased_items(), separators=(",", ":"))
        if "\\u2192" in wire_json or "→" in wire_json:
            raise RuntimeError("Unicode position-modifier arrow leaked into purchased-items JSON")

        # Exercise the exact previously frozen Positioning resource directly.
        positioning = next(row for row in local_identity_module.CONSUMABLE_CATALOG if int(row.get("resourceId", 0)) == 5003068)
        positioning_wire = test_store._local_consumable_payload(item_id=180999999968, consumable=positioning)
        if forbidden_wire_keys.intersection(positioning_wire):
            raise RuntimeError(f"Positioning card still contains parser-risk description fields: {positioning_wire}")
        if "→" in json.dumps(positioning_wire, ensure_ascii=False):
            raise RuntimeError("Positioning card still contains a Unicode arrow on the wire")

        # BETA 2.25 player ItemData regression. The working revival contract
        # supplies position explicitly in preferredPosition while cardsubtypeid
        # is the rare/common discriminator (1/0), not a positional band.
        # Keep the special revision in resourceId and the base identity in
        # definitionId, while emitting the proven rare-player subtype and ST position for Zlatan.
        ibra_91 = next((row for row in specials if int(row.get("assetId", 0)) == 41236 and int(row.get("rating", 0)) == 91 and int(row.get("resourceId", 0)) == 67150100), None)
        if ibra_91 is None:
            raise RuntimeError("captured 91 IF Ibrahimovic special is missing from the catalogue")
        ibra_wire = test_store._local_pack_player_payload(
            item_id=180999999919, player=ibra_91, quality="gold", rare=True
        )
        if str(ibra_wire.get("preferredPosition", "")) != "ST" or int(ibra_wire.get("cardsubtypeid", -1)) != 1:
            raise RuntimeError(f"91 IF Ibrahimovic is not emitted as rare subtype 1 with ST position: {ibra_wire}")
        if int(ibra_wire.get("resourceId", 0)) != 67150100:
            raise RuntimeError(f"91 IF Ibrahimovic lost its special resourceId: {ibra_wire}")
        if int(ibra_wire.get("definitionId", 0)) != 41236:
            raise RuntimeError(f"91 IF Ibrahimovic does not expose base static definitionId=41236: {ibra_wire}")
        base_ibra_wire = test_store._canonical_player_payload(
            item_id=180999999918, asset_id=41236, existing={"untradeable": False}, pile=6
        )
        if int(base_ibra_wire.get("definitionId", 0)) != 41236 or str(base_ibra_wire.get("preferredPosition")) != "ST":
            raise RuntimeError(f"base Ibrahimovic identity regressed while fixing specials: {base_ibra_wire}")
        subtype_expectations = {0: 0, 1: 1, 2: 1, 3: 1}
        for rare_flag, expected in subtype_expectations.items():
            actual = int(local_identity_module.player_card_subtype(rare_flag))
            if actual != expected:
                raise RuntimeError(f"player rarity cardsubtypeid mapping regressed: {rare_flag} -> {actual}, expected {expected}")

        # A second purchase with unresolved New Items must be rejected BEFORE
        # any coins are charged, preventing 24/36-item reveal piles.
        blocked_balance = int(purchase["credits"])
        try:
            test_store.purchase_pack(6, currency="COINS")
        except ValueError as error:
            if "current New Items" not in str(error):
                raise RuntimeError(f"wrong overlapping-pack rejection: {error}")
        else:
            raise RuntimeError("second pack purchase was allowed while New Items remained unresolved")
        if int(test_store.credits().get("credits", -1)) != blocked_balance:
            raise RuntimeError("blocked overlapping pack purchase changed the coin balance")

        duplicate_ids = {int(row.get("itemId", row.get("id", 0))) for row in purchase.get("duplicateItemIdList", [])}
        accepted_player = next((row for row in pack_players if int(row["id"]) not in duplicate_ids), None)
        if accepted_player is None:
            raise RuntimeError(f"compact-club regression pack unexpectedly made all three players duplicates: {purchase.get('duplicateItemIdList')}")
        moved_player = test_store.move_items([{"id": int(accepted_player["id"]), "pile": 7}])["itemData"][0]
        if int(moved_player.get("id", 0)) != int(accepted_player["id"]) or moved_player.get("success") is not True:
            raise RuntimeError(f"non-duplicate player Send to Club failed: {moved_player}")
        pending_after_player_move = {int(row["id"]) for row in test_store.purchased_items().get("itemData", [])}
        if int(accepted_player["id"]) in pending_after_player_move:
            raise RuntimeError("accepted player remained in New Items after Send to Club")

        movable_utility = next((row for row in pack_items if str(row.get("itemType", "")).lower() != "player"), None)
        if movable_utility is None:
            raise RuntimeError("Premium Gold regression pack has no utility item")
        moved_utility = test_store.move_items([{"id": int(movable_utility["id"]), "pile": 7}])["itemData"][0]
        if int(moved_utility.get("id", 0)) != int(movable_utility["id"]) or moved_utility.get("success") is not True:
            raise RuntimeError(f"utility Send to Club failed: {moved_utility}")

        # Create one exact-resource second owned row and prove duplicate rejection.
        with closing(sqlite3.connect(test_db)) as conn, conn:
            base = conn.execute("SELECT persona_id,asset_id,payload FROM items WHERE item_id=?", (int(accepted_player["id"]),)).fetchone()
            if base is None:
                raise RuntimeError("accepted player was not persisted in My Club")
            clone_id = 180999000001
            payload = json.loads(base[2]); payload["id"] = clone_id; payload["itemId"] = clone_id; payload["pile"] = 6
            conn.execute(
                "INSERT INTO items(item_id,persona_id,asset_id,item_type,pile,tradeable,payload) VALUES(?,?,?,?,?,?,?)",
                (clone_id, int(base[0]), int(base[1]), "player", "unassigned", 1, json.dumps(payload)),
            )
            paired = test_store._duplicate_item_id_list_locked(conn, int(base[0]), [payload])
            expected_pair = [{"itemId": clone_id, "duplicateItemId": int(accepted_player["id"])}]
            if paired != expected_pair:
                raise RuntimeError(f"retail duplicateItemIdList pairing regression failed: {paired} expected {expected_pair}")
        duplicate_move = test_store.move_items([{"id": clone_id, "pile": 7}])["itemData"][0]
        if duplicate_move.get("success") is not False or duplicate_move.get("errorCode") != 472:
            raise RuntimeError(f"duplicate player rejection regression failed: {duplicate_move}")
        if int(duplicate_move.get("duplicateItemId", 0)) != int(accepted_player["id"]):
            raise RuntimeError(f"duplicate player rejection did not identify owned counterpart: {duplicate_move}")
        with closing(sqlite3.connect(test_db)) as conn, conn:
            conn.execute("DELETE FROM items WHERE item_id=?", (clone_id,)); conn.commit()

        # Resolve every remaining item from pack one so pack two can be opened.
        remaining_ids = [int(row["id"]) for row in test_store.purchased_items().get("itemData", [])]
        if remaining_ids:
            test_store.quick_sell(remaining_ids)
        if test_store.purchased_items().get("itemData"):
            raise RuntimeError("resolved first pack still appears in New Items")

        # Deterministically prove ordinary historical specials remain reachable.
        old_special = local_identity_module.PACK_WEIGHTS_DOCUMENT["specialChancePerPack"].get("6", 0.0)
        try:
            local_identity_module.PACK_WEIGHTS_DOCUMENT["specialChancePerPack"]["6"] = 1.0
            forced = test_store.purchase_pack(6, currency="COINS")
        finally:
            local_identity_module.PACK_WEIGHTS_DOCUMENT["specialChancePerPack"]["6"] = old_special
        forced_players = [row for row in forced.get("itemData", []) if str(row.get("itemType", "")).lower() == "player"]
        forced_specials = [row for row in forced_players if bool(row.get("specialCard")) and str(row.get("cardType", "")).lower() not in {"legend", "worldcup"}]
        if len(forced_specials) != 1:
            raise RuntimeError(f"forced Premium Gold special jackpot did not emit exactly one normal special: {forced_specials}")
        eligible_special_resources = {int(row["resourceId"]) for row in specials if str(row.get("cardType", "")).lower() not in {"worldcup", "legend"}}
        if int(forced_specials[0].get("resourceId", 0)) not in eligible_special_resources:
            raise RuntimeError(f"forced special is not from the historical normal-FUT special catalogue: {forced_specials[0]}")

        with closing(sqlite3.connect(test_db)) as conn, conn:
            seeded_special_count = conn.execute("SELECT COUNT(*) FROM items WHERE item_id>=? AND item_id<?", (172000000000, 173000000000)).fetchone()[0]
            if int(seeded_special_count) != 0:
                raise RuntimeError(f"historical specials are still pre-owned after compact migration: {seeded_special_count}")
            legend_owned = conn.execute("SELECT COUNT(*) FROM items WHERE item_id>=? AND item_id<?", (173000000000, 173000001000)).fetchone()[0]
            if int(legend_owned) != 0:
                raise RuntimeError(f"broken direct Legend rows remain owned: {legend_owned}")
            rows = conn.execute("SELECT payload FROM items WHERE item_type='player'").fetchall()
            resources = [int(json.loads(row[0]).get("resourceId", 0)) for row in rows]
            if len(resources) != len(set(resources)):
                raise RuntimeError("duplicate player resourceIds remain after compact migration")

        # Simulate a v2.40.15 profile with one broken Legend and two stale packs,
        # then prove the v2.40.18 migration removes/closes them exactly once.
        migration_db = Path(tmpdir) / "fut-v24015-migration.sqlite3"
        migration_store = LocalIdentityStore(migration_db, "existing")
        migration_store.provision_pack_play_club()
        migration_store.ensure_local_test_balance()
        old_purchase = migration_store.purchase_pack(6, currency="COINS")
        with closing(sqlite3.connect(migration_db)) as conn, conn:
            conn.execute("UPDATE schema_meta SET meta_value='fifa14-v24015-compact-pack-club-legends-test' WHERE meta_key='club_ownership_schema'")
            sample = dict(local_identity_module.LEGEND_PLAYER_CATALOG[0])
            sample.update({"id":173000000001,"itemId":173000000001,"itemType":"player","pile":7,"resourceId":int(sample["assetId"])})
            conn.execute(
                "INSERT OR REPLACE INTO items(item_id,persona_id,asset_id,item_type,pile,tradeable,payload) VALUES(?,?,?,?,?,?,?)",
                (173000000001,1000001,int(sample["assetId"]),"player","club",0,json.dumps(sample)),
            )
            # Recreate the exact old failure class: two unresolved 12-item packs
            # flatten to 24 New Items. The migration must close both piles.
            second = conn.execute(
                "INSERT INTO packs(persona_id,pack_type,pack_name,unopened,created_at) VALUES(1000001,6,'Premium Gold Pack',1,1)"
            )
            second_pack_id = int(second.lastrowid)
            for ordinal, item in enumerate(old_purchase.get("itemData", [])):
                clone = dict(item); clone["id"] = int(clone["id"]) + 10000; clone["itemId"] = int(clone["id"])
                conn.execute(
                    "INSERT INTO pack_contents(pack_id,ordinal,payload) VALUES(?,?,?)",
                    (second_pack_id, ordinal, json.dumps(clone, separators=(",", ":"))),
                )
        migrated = migration_store.provision_pack_play_club()
        if int(migrated.get("legendPlayersRemoved", 0)) < 1:
            raise RuntimeError(f"v2.40.18 migration failed to remove broken Legend row: {migrated}")
        expected_stale = 2 * len(old_purchase.get("itemData", []))
        if int(migrated.get("stalePacksClosed", 0)) < 2 or int(migrated.get("stalePackItemsCleared", 0)) != expected_stale:
            raise RuntimeError(f"v2.40.18 migration failed to clear the 24-item stale New Items case: {migrated}")
        if migration_store.purchased_items().get("itemData"):
            raise RuntimeError("stale New Items survived the v2.40.18 migration")

    from probe import HttpProbe  # noqa: E402
    store_loc = HttpProbe._store_pack_descriptions_payload().decode("utf-8", errors="replace")
    if '<locstring id="LOCAL_PACK_NAME_6">Premium Gold Pack</locstring>' not in store_loc:
        raise RuntimeError("Premium Gold name localization token did not serialize")
    if 'LOCAL_PACK_DESC_6' not in store_loc or '3 players' not in store_loc:
        raise RuntimeError("Premium Gold description localization token did not serialize")

    manager_doc = json.loads(managers.read_text(encoding="utf-8"))
    if not isinstance(manager_doc.get("managers"), list) or len(manager_doc["managers"]) < 5:
        raise RuntimeError("manager reference catalogue is incomplete")
    if manager_doc.get("liveEmissionEnabled") is not False:
        raise RuntimeError("manager live emission must remain disabled until retail resource IDs are verified")

    for launcher in (run_script, beta_run_script):
        launcher_text = launcher.read_text(encoding="utf-8", errors="replace")
        if "Unsupported fifa14.exe SHA-256" in launcher_text or "expectedExeSha256" in launcher_text:
            raise RuntimeError(f"{launcher.name} still contains the old executable hash blocker")
        if "hash enforcement is disabled" not in launcher_text:
            raise RuntimeError(f"{launcher.name} does not declare the non-blocking compatibility policy")

    print("Executable/DLL hash enforcement: disabled (BIG/BH record safety checks remain enabled).")
    print("FIFA 14 LOCAL FUT v2.41.1 BETA 2.25.9 INSTALL VERIFIED (Transfer Market Economy Beta 2 + accessible pack weights).")
    print(f"Real FIFA 14 base-card catalogue: {len(players)} cards {tier_counts}; special variants: {len(specials)}; Legends reference catalogue: {len(legends)}; progression BETA + retail-safe Seasons bootstrap + JSON item-metadata guard + compact club/single New Items pile/parser-safe consumables/base-definition special wire contract/mixed packs/special draw/Send to Club/duplicates/quicksell/store metadata validated.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
