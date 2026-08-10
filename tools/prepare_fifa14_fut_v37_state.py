#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SERVER = PROJECT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from local_identity import LocalIdentityStore, LEGEND_PLAYER_CATALOG  # noqa: E402


def read_legacy_signal(path: Path) -> dict:
    result = {
        "exists": path.is_file(),
        "trusted": False,
        "captain_selected": False,
        "charity_seen": False,
        "club_exists": False,
    }
    if not path.is_file():
        return result
    try:
        connection = sqlite3.connect(path)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "identity" in tables:
            row = connection.execute(
                "SELECT trusted FROM identity WHERE singleton=1"
            ).fetchone()
            result["trusted"] = bool(row and row[0])
        if "clubs" in tables:
            result["club_exists"] = bool(
                connection.execute("SELECT 1 FROM clubs LIMIT 1").fetchone()
            )
        if "fut_actions" in tables:
            actions = {
                str(row[0]).upper(): bool(row[1])
                for row in connection.execute(
                    "SELECT action_name, completed FROM fut_actions"
                ).fetchall()
            }
            result["captain_selected"] = actions.get(
                "ICEBREAKER_ENGLISH_CAPTAIN_SELECTED", False
            )
            result["charity_seen"] = actions.get("CHARITY_MATCH_PLAYED", False)
        connection.close()
    except sqlite3.Error as error:
        result["error"] = str(error)
    return result


def set_trusted(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE identity SET trusted=1 WHERE singleton=1")
        connection.commit()
    finally:
        connection.close()


def compatible_hub_database(path: Path) -> bool:
    if not path.is_file():
        return False
    connection = None
    try:
        connection = sqlite3.connect(path)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required_tables = {"identity", "clubs", "fut_users", "squads", "squad_players", "items"}
        if not required_tables.issubset(tables):
            return False
        if connection.execute("SELECT 1 FROM clubs LIMIT 1").fetchone() is None:
            return False
        required_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(squad_players)").fetchall()
        }
        return {
            "squad_id", "slot_index", "item_id", "asset_id", "resource_id",
            "team_id", "rating", "rare_flag", "play_style",
            "preferred_position", "attributes_json",
        }.issubset(required_columns)
    except sqlite3.Error:
        return False
    finally:
        if connection is not None:
            connection.close()


def club_name(path: Path) -> str:
    try:
        connection = sqlite3.connect(path)
        row = connection.execute("SELECT club_name FROM clubs LIMIT 1").fetchone()
        connection.close()
        return "" if row is None else str(row[0])
    except sqlite3.Error:
        return ""


def find_previous_hub_database(destination: Path) -> Path | None:
    # First prefer a compatible persistent DB from an earlier hub build in the
    # same %LOCALAPPDATA%\FIFA14LocalFUT directory. This is the normal v2.34 ->
    # v2.37 upgrade path and avoids making the user wipe/recreate their club.
    candidates: list[Path] = []
    try:
        for candidate in destination.parent.glob("local-fut-v*.sqlite3"):
            resolved = candidate.resolve()
            if resolved != destination.resolve() and compatible_hub_database(resolved):
                candidates.append(resolved)
    except OSError:
        pass

    project = Path(__file__).resolve().parents[1]
    search_root = project.parent
    for ancestor in project.parents:
        if ancestor.name.lower() == "downloads":
            search_root = ancestor
            break
    patterns = (
        "fifa14-fut-*/**/artifacts/local-fut-pc-*.sqlite3",
        "fifa14-fut-*/**/artifacts/*.sqlite3",
    )
    for pattern in patterns:
        try:
            for candidate in search_root.glob(pattern):
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if resolved == destination.resolve():
                    continue
                if compatible_hub_database(resolved):
                    candidates.append(resolved)
        except OSError:
            continue
    if not candidates:
        return None
    # Prefer a user-renamed club over the generic Local FUT seed, then newest.
    return max(
        set(candidates),
        key=lambda item: (
            1 if club_name(item).strip().lower() not in {"", "local fut"} else 0,
            item.stat().st_mtime,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare persistent FIFA 14 V2.37 hub/real-card/store state"
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--legacy-database", type=Path)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--force-provision", action="store_true")
    parser.add_argument("--legend-client-report", type=Path)
    args = parser.parse_args()

    database = args.database.expanduser().resolve()
    fixture_path = args.fixture.expanduser().resolve()
    legacy = (
        args.legacy_database.expanduser().resolve()
        if args.legacy_database
        else database.with_name("local-fut-pc-captain-v26.sqlite3")
    )

    if args.reset and database.exists():
        database.unlink()

    document = json.loads(fixture_path.read_text(encoding="utf-8"))
    packs = document.get("packList")
    if not isinstance(packs, list) or len(packs) != 4:
        raise ValueError("V27 fixture must contain four packs")

    imported_from = None
    if not args.reset and not database.exists():
        previous = find_previous_hub_database(database)
        if previous is not None:
            database.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(previous, database)
            imported_from = str(previous)

    legacy_signal = read_legacy_signal(legacy)
    store = LocalIdentityStore(database, "new")
    if legacy_signal.get("trusted"):
        set_trusted(database)

    # V2.40.20 only grants Legends when the client-DB patcher has independently
    # verified all 42 player IDs in FIFA's cards_ng_db. Otherwise the stable
    # compact/no-Legend ownership behavior is preserved.
    legend_client_state = {
        "report": str(args.legend_client_report.expanduser().resolve()) if args.legend_client_report else None,
        "verifiedAllLegends": False,
        "verifiedLegendIds": [],
        "error": None,
    }
    if args.legend_client_report:
        report_path = args.legend_client_report.expanduser().resolve()
        try:
            document = json.loads(report_path.read_text(encoding="utf-8"))
            requested = {int(player["assetId"]) for player in LEGEND_PLAYER_CATALOG}
            verified = {int(value) for value in document.get("verifiedLegendIds", [])}
            legend_client_state["verifiedLegendIds"] = sorted(verified)
            legend_client_state["verifiedAllLegends"] = bool(
                document.get("verifiedAllLegends") and requested.issubset(verified)
            )
            if not legend_client_state["verifiedAllLegends"]:
                legend_client_state["error"] = document.get("error") or "all 42 Legend IDs were not verified in the client DB"
        except Exception as error:
            legend_client_state["error"] = str(error)

    full_club_seed = store.provision_pack_play_club(
        packs[1],
        legend_client_ready=bool(legend_client_state["verifiedAllLegends"]),
        legend_asset_ids=legend_client_state["verifiedLegendIds"],
    )
    migrated = bool(
        full_club_seed.get("seededBaseRemoved")
        or full_club_seed.get("seededSpecialsRemoved")
        or full_club_seed.get("squadSeededSpecialsDemotedToBase")
        or full_club_seed.get("legendPlayersRemoved")
        or full_club_seed.get("stalePacksClosed")
        or full_club_seed.get("historicalOwnedDuplicatesRemoved")
    )
    migration_reason = "v24022-conditional-legend-client-db"

    card_repair = None
    owned_card_repair = None
    unopened_pack_repair = None
    balance = None
    if store.has_club():
        # V29 demonstrated that the retail client needs the full player
        # discriminator contract, not the V27-minimal object used by v2.34.
        # Upgrade those payloads in-place while preserving club/session state,
        # squad edits and separately owned pack cards.
        card_repair = store.repair_known_good_roster(packs[1])
        owned_card_repair = store.repair_catalogued_owned_players()
        unopened_pack_repair = store.repair_unopened_pack_contents()
        balance = store.ensure_local_test_balance()

    snapshot = store.snapshot()
    result = {
        "database": str(database),
        "importedFromPreviousBuild": imported_from,
        "legacyDatabase": str(legacy),
        "legacySignal": legacy_signal,
        "reset": bool(args.reset),
        "migrated": migrated,
        "migrationReason": migration_reason,
        "fullClubSeed": full_club_seed,
        "legendClientDb": legend_client_state,
        "cardRepair": card_repair,
        "ownedCardRepair": owned_card_repair,
        "unopenedPackRepair": unopened_pack_repair,
        "testBalance": balance,
        "hasClub": store.has_club(),
        "profileKind": store.profile_kind(),
        "route": "retail-returning-user" if store.has_club() else "icebreaker-first-use",
        "snapshot": snapshot,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
