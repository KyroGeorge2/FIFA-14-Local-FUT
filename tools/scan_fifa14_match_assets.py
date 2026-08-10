#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from patch_fifa14_fut_legends_db import (
    candidate_blob,
    discover_descriptor_record,
    discover_fifa_db_records,
    load_named_payload,
    pair_descriptor,
    parse_descriptor,
    parse_fifa_db,
    read_row,
    scan_named_candidates,
)

KIT_TEAM_ID = 241
STADIUM_ID = 34
STADIUM_NAME = "Town Park"
SAMPLE_PLAYER_IDS = (199575, 170114, 200376, 217787, 181381)


def unique_tables(db) -> list[Any]:
    seen: set[int] = set()
    out: list[Any] = []
    for table in db.tables.values():
        marker = id(table)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(table)
    return out


def row_int(row: dict[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        value = row.get(key.lower())
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return int(default)


def safe_rows(db, table, limit: int | None = None) -> Iterable[dict[str, Any]]:
    count = int(table.valid_records_count)
    if limit is not None:
        count = min(count, int(limit))
    for index in range(count):
        try:
            row = read_row(db, table, index)
        except Exception:
            continue
        row["_index"] = index
        yield row


def descriptor_for_archive(root: Path, archive: str, names: tuple[str, ...]) -> tuple[bytes, dict[str, Any]] | None:
    named = pair_descriptor(root, archive, names)
    if named:
        return named
    return discover_descriptor_record(root / archive, (root / archive).with_suffix(".bh"))


def named_db_for_archive(root: Path, archive: str, basename: str) -> tuple[bytes, dict[str, Any]] | None:
    rows = scan_named_candidates(root / archive, (root / archive).with_suffix(".bh"), basename)
    if rows:
        row = rows[0]
        return candidate_blob(row), {
            "archive": archive,
            "entry": row.entry.name,
            "sourceKind": row.source_kind,
        }
    return None


def parse_db_candidates(root: Path, archives: tuple[str, ...], db_name: str, meta_names: tuple[str, ...], *, cards: bool = False):
    parsed: list[tuple[Any, dict[str, Any]]] = []
    shared_descriptor: tuple[bytes, dict[str, Any]] | None = None
    for archive in archives:
        if not (root / archive).is_file():
            continue
        descriptor_info = descriptor_for_archive(root, archive, meta_names)
        if descriptor_info and shared_descriptor is None:
            shared_descriptor = descriptor_info
        db_info = named_db_for_archive(root, archive, db_name)
        descriptor_to_use = descriptor_info or shared_descriptor
        if db_info and descriptor_to_use:
            try:
                parsed.append((
                    parse_fifa_db(db_info[0], parse_descriptor(descriptor_to_use[0])),
                    {"db": db_info[1], "descriptor": descriptor_to_use[1]},
                ))
                continue
            except Exception:
                pass
        if cards and descriptor_to_use:
            try:
                candidates = discover_fifa_db_records(
                    root / archive,
                    (root / archive).with_suffix(".bh"),
                    descriptor_to_use[0],
                    SAMPLE_PLAYER_IDS,
                )
            except Exception:
                candidates = []
            for candidate in candidates[:4]:
                try:
                    parsed.append((
                        parse_fifa_db(candidate_blob(candidate), parse_descriptor(descriptor_to_use[0])),
                        {
                            "db": {
                                "archive": archive,
                                "entry": candidate.entry.name,
                                "sourceKind": candidate.source_kind,
                            },
                            "descriptor": descriptor_to_use[1],
                        },
                    ))
                except Exception:
                    continue
    # Loose extracted DBs are useful for modded/repacked installs.
    loose_db = root / "data" / "db" / db_name
    loose_meta = None
    for meta in meta_names:
        p = root / "data" / "db" / meta
        if p.is_file():
            loose_meta = p
            break
    if loose_db.is_file() and loose_meta is not None:
        try:
            parsed.append((
                parse_fifa_db(loose_db.read_bytes(), parse_descriptor(loose_meta.read_bytes())),
                {"db": {"archive": None, "entry": str(loose_db), "sourceKind": "loose"},
                 "descriptor": {"archive": None, "entry": str(loose_meta), "sourceKind": "loose"}},
            ))
        except Exception:
            pass
    return parsed


def find_main_assets(parsed_dbs) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    home: dict[str, Any] | None = None
    away: dict[str, Any] | None = None
    stadium: dict[str, Any] | None = None
    team_stadium_link: dict[str, Any] | None = None

    for db, source in parsed_dbs:
        table_names = sorted({t.name for t in unique_tables(db)})
        diag = {"source": source, "tables": table_names}
        diagnostics.append(diag)
        teamkits = db.table("teamkits")
        if teamkits is not None:
            matches: list[dict[str, Any]] = []
            for row in safe_rows(db, teamkits):
                team = row_int(row, "teamtechid", "teamid")
                if team != KIT_TEAM_ID:
                    continue
                kit_type = row_int(row, "teamkittypetechid", "kittype", "type", default=-1)
                kit_id = row_int(row, "teamkitid", "kitid", "id", default=-1)
                compact = {
                    "teamid": team,
                    "teamkittypetechid": kit_type,
                    "teamkitid": kit_id,
                    "rowIndex": row.get("_index"),
                    "source": source,
                }
                # Retain all numeric fields to make the user capture useful if a
                # title update uses a slightly different kit-field contract.
                for key, value in row.items():
                    if key.startswith("_") or isinstance(value, str):
                        continue
                    if isinstance(value, (int, float)):
                        compact.setdefault(key, value)
                matches.append(compact)
                if kit_id > 0 and kit_type == 0 and home is None:
                    home = compact
                elif kit_id > 0 and kit_type == 1 and away is None:
                    away = compact
            if matches:
                diag["team241Kits"] = matches
        stadiums = db.table("stadiums")
        if stadiums is not None and stadium is None:
            for row in safe_rows(db, stadiums):
                sid = row_int(row, "stadiumid", "stadiumtechid", "id", default=-1)
                if sid == STADIUM_ID:
                    stadium = {k: v for k, v in row.items() if not k.startswith("_") and not isinstance(v, str)}
                    stadium.update({"stadiumid": STADIUM_ID, "rowIndex": row.get("_index"), "source": source})
                    break
        links = db.table("teamstadiumlinks")
        if links is not None and team_stadium_link is None:
            for row in safe_rows(db, links):
                team = row_int(row, "teamid", "teamtechid", default=-1)
                if team == KIT_TEAM_ID:
                    team_stadium_link = {k: v for k, v in row.items() if not k.startswith("_") and not isinstance(v, str)}
                    team_stadium_link.update({"rowIndex": row.get("_index"), "source": source})
                    break

    return {
        "home": home,
        "away": away,
        "stadium": stadium,
        "teamStadiumLink": team_stadium_link,
    }, diagnostics


def relevant_cards_rows(parsed_dbs, home_kit_id: int, away_kit_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matches: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    wanted_assets = {x for x in (home_kit_id, away_kit_id, STADIUM_ID) if x > 0}
    relevant_field_names = {
        "assetid", "resourceid", "teamid", "teamtechid", "teamkitid", "teamkittypetechid",
        "stadiumid", "category", "cardsubtypeid", "year", "itemtype", "itemtypeid",
    }
    for db, source in parsed_dbs:
        hit_tables: list[str] = []
        for table in unique_tables(db):
            names = set(table.by_name)
            if not (names & relevant_field_names):
                continue
            if not ("kit" in table.name.lower() or "stad" in table.name.lower() or {"assetid", "resourceid"}.issubset(names)):
                continue
            table_hits = 0
            for row in safe_rows(db, table):
                team = row_int(row, "teamid", "teamtechid", default=-1)
                asset = row_int(row, "assetid", "teamkitid", "stadiumid", default=-1)
                resource = row_int(row, "resourceid", default=-1)
                sid = row_int(row, "stadiumid", default=-1)
                if not (team == KIT_TEAM_ID or asset in wanted_assets or sid == STADIUM_ID or resource in wanted_assets):
                    continue
                compact = {"table": table.name, "rowIndex": row.get("_index"), "source": source}
                for key, value in row.items():
                    if key.startswith("_") or isinstance(value, str):
                        continue
                    if isinstance(value, (int, float)):
                        compact[key] = value
                matches.append(compact)
                table_hits += 1
                if table_hits >= 40:
                    break
            if table_hits:
                hit_tables.append(table.name)
        diagnostics.append({"source": source, "hitTables": sorted(set(hit_tables))})
    return matches, diagnostics



def full_cosmetic_catalog(parsed_dbs) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Extract all normal FIFA 14 FUT kits, stadiums and badges read-only.

    The fcc_* ``carddbid`` is the static FUT resource identity. We deliberately
    prefer normal ``year=0`` rows and, for kits, only the proven home/away
    categories 2/3. Unknown subtype guesses are never generated.
    """
    best: dict[str, dict[int, tuple[int, dict[str, Any]]]] = {
        "kits": {}, "stadiums": {}, "badges": {},
    }
    diagnostics: list[dict[str, Any]] = []
    table_specs = {
        "fcc_kitcards": "kits",
        "fcc_stadium": "stadiums",
        "fcc_badgecards": "badges",
    }
    for source_index, (db, source) in enumerate(parsed_dbs):
        counts = {key: 0 for key in best}
        for table_name, group in table_specs.items():
            table = db.table(table_name)
            if table is None:
                continue
            for row in safe_rows(db, table):
                asset_id = row_int(row, "assetid", default=0)
                resource_id = row_int(row, "carddbid", "resourceid", default=0)
                if asset_id <= 0 or resource_id <= 0:
                    continue
                year = row_int(row, "year", default=0)
                if year not in {0, 2014}:
                    continue
                category = row_int(row, "category", default=0)
                if group == "kits" and category not in {2, 3}:
                    continue
                compact: dict[str, Any] = {
                    "assetId": int(asset_id),
                    "resourceId": int(resource_id),
                    "definitionId": int(resource_id),
                    "carddbid": int(resource_id),
                    "category": int(category),
                    "year": int(year),
                }
                for field in (
                    "teamid", "cardassetid", "value", "weightrare", "stadiumid",
                    "badgeid", "badgedbid", "teamkitid", "teamkittypetechid",
                ):
                    if field in row:
                        try:
                            compact[field] = int(row[field])
                        except (TypeError, ValueError):
                            pass
                if group == "kits":
                    compact["itemType"] = "kit"
                    compact["teamkittypetechid"] = 0 if category == 2 else 1
                    team_id = row_int(row, "teamid", default=0)
                    compact["name"] = f"Team {team_id} {'Home' if category == 2 else 'Away'} Kit"
                elif group == "stadiums":
                    compact["itemType"] = "stadium"
                    stadium_id = row_int(row, "stadiumid", default=asset_id) or asset_id
                    compact["stadiumid"] = int(stadium_id)
                    compact["name"] = STADIUM_NAME if stadium_id == STADIUM_ID else f"Stadium {stadium_id}"
                else:
                    compact["itemType"] = "custom"
                    compact["badgeDBid"] = int(resource_id)
                    compact["name"] = f"Badge {asset_id}"
                compact["source"] = source
                compact["rowIndex"] = row.get("_index")

                # Prefer year=0, records with cardassetid, then earlier archive
                # candidates (patch.big normally appears first).
                score = 100 if year == 0 else 50
                if row_int(row, "cardassetid", default=0) > 0:
                    score += 10
                if row_int(row, "value", default=0) > 0:
                    score += 2
                score -= source_index
                previous = best[group].get(resource_id)
                if previous is None or score > previous[0]:
                    best[group][resource_id] = (score, compact)
                counts[group] += 1
        diagnostics.append({"source": source, "candidateCounts": counts})

    catalog = {
        group: [pair[1] for _resource, pair in sorted(rows.items())]
        for group, rows in best.items()
    }
    return catalog, diagnostics

def exact_fut_kit_card(rows: list[dict[str, Any]], *, team_id: int, category: int) -> dict[str, Any] | None:
    """Return the retail FUT kit-card definition, not the gameplay teamkit row.

    FIFA 14 keeps two different identities for a kit: `teamkits.teamkitid`
    selects the rendered uniform inside the main game database, while
    `fcc_kitcards.assetid` is the FUT ItemData/resource identity consumed by
    CardsDLL.  BETA 2.9 accidentally put the former on the FUT wire.
    Prefer the normal year=0 fcc_kitcards record for the requested FUT
    category (2=home, 3=away).
    """
    candidates: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        if str(row.get("table", "")).lower() != "fcc_kitcards":
            continue
        if row_int(row, "teamid", default=-1) != int(team_id):
            continue
        if row_int(row, "category", default=-1) != int(category):
            continue
        asset = row_int(row, "assetid", default=0)
        if asset <= 0:
            continue
        score = 0
        if row_int(row, "year", default=-1) == 0:
            score += 100
        if row_int(row, "carddbid", default=0) > 0:
            score += 20
        if row_int(row, "cardassetid", default=0) > 0:
            score += 10
        if row_int(row, "weightrare", default=0) > 0:
            score += 2
        candidates.append((score, row))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: (pair[0], -int(pair[1].get("rowIndex", 0))), reverse=True)
    return candidates[0][1]


def exact_fut_stadium_card(rows: list[dict[str, Any]], stadium_id: int) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("table", "")).lower() != "fcc_stadium":
            continue
        if row_int(row, "stadiumid", default=-1) == int(stadium_id) and row_int(row, "assetid", default=-1) == int(stadium_id):
            return row
    return None


def best_cards_row(rows: list[dict[str, Any]], *, asset_id: int, team_id: int = 0, kit_type: int | None = None, stadium_id: int | None = None) -> dict[str, Any] | None:
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        score = 0
        asset = row_int(row, "assetid", "teamkitid", default=-1)
        resource = row_int(row, "resourceid", default=-1)
        team = row_int(row, "teamid", "teamtechid", default=-1)
        category = row_int(row, "category", "teamkittypetechid", "cardsubtypeid", default=-99)
        sid = row_int(row, "stadiumid", default=-1)
        if asset == asset_id:
            score += 120
        if resource == asset_id:
            score += 30
        if team_id and team == team_id:
            score += 35
        if kit_type is not None and category == kit_type:
            score += 20
        if stadium_id is not None and sid == stadium_id:
            score += 120
        if resource > 0:
            score += 8
        if "kit" in str(row.get("table", "")).lower() and kit_type is not None:
            score += 8
        if "stad" in str(row.get("table", "")).lower() and stadium_id is not None:
            score += 8
        if score > 0:
            scored.append((score, row))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0][1]


def resolve_item(main_row: dict[str, Any] | None, cards_row: dict[str, Any] | None, *, item_type: str, item_state: str, kit_type: int | None = None) -> dict[str, Any]:
    if item_type == "kit":
        base_asset = row_int(main_row or {}, "teamkitid", default=0)
        team = KIT_TEAM_ID
    else:
        base_asset = STADIUM_ID
        team = 0
    asset = row_int(cards_row or {}, "assetid", "teamkitid", default=base_asset) or base_asset
    # FUT resourceId is the unique static-definition identity. The retail
    # fcc_* tables expose that as carddbid; assetid is the artwork/type ID and
    # is intentionally repeated across many kit definitions. BETA 2.10 used
    # assetid as resourceId, which could not uniquely identify team 241's kit.
    resource = row_int(cards_row or {}, "resourceid", "carddbid", default=asset) or asset
    # `category` in fcc_kitcards/fcc_stadium is a native DB classification
    # column and is NOT safe to copy into ItemData ``cardsubtypeid``. BETA 2.13
    # also tested guessed 5/7/8 subtype values; the retail PC client crashed as
    # soon as the kit collection was parsed. BETA 2.22 keeps native category
    # (plus teamkittypetechid/stadiumid) as its own wire classification while
    # never aliasing it to cardsubtypeid.
    wire_item_type = "kit" if item_type == "kit" else "stadium"
    resolved = {
        "itemType": wire_item_type,
        "itemState": item_state,
        "assetId": int(asset),
        "resourceId": int(resource),
        "definitionId": int(resource),
        "teamid": int(row_int(cards_row or {}, "teamid", "teamtechid", default=team) or team),
    }
    if kit_type is not None:
        resolved["category"] = int(row_int(cards_row or {}, "category", "teamkittypetechid", default=kit_type))
        resolved["teamkittypetechid"] = int(kit_type)
    else:
        resolved["stadiumid"] = STADIUM_ID
        resolved["name"] = STADIUM_NAME
    if cards_row:
        # Preserve the exact FUT definition metadata in diagnostics and on the
        # synthetic owned item. Unknown JSON members are ignored by retail
        # CardsDLL, while these make it possible to prove which native row won.
        for field in ("carddbid", "cardassetid", "category", "year", "value", "weightrare"):
            if field in cards_row:
                resolved[field] = int(cards_row[field])
        resolved["cardsSource"] = {"table": cards_row.get("table"), "rowIndex": cards_row.get("rowIndex"), "source": cards_row.get("source")}
    if main_row:
        if item_type == "kit":
            resolved["teamkitid"] = int(row_int(main_row, "teamkitid", default=0))
        resolved["mainSource"] = {"rowIndex": main_row.get("rowIndex"), "source": main_row.get("source")}
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only FIFA 14 native kit/stadium/badge catalogue scanner")
    parser.add_argument("--game-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.game_root).resolve()
    output = Path(args.output).resolve()

    main_dbs = parse_db_candidates(
        root,
        ("patch.big", "data0.big", "data1.big"),
        "fifa_ng_db.db",
        ("fifa_ng_db-meta.xml", "fifa_ng_db_meta.xml"),
        cards=True,
    )
    main_assets, main_diag = find_main_assets(main_dbs)
    home_id = row_int(main_assets.get("home") or {}, "teamkitid", default=0)
    away_id = row_int(main_assets.get("away") or {}, "teamkitid", default=0)

    cards_dbs = parse_db_candidates(
        root,
        ("patch.big", "cards0.big"),
        "cards_ng_db.db",
        ("cards_ng_db-meta.xml", "cards_ng_db_meta.xml", "fifa_ng_db-meta.xml"),
        cards=True,
    )
    cards_rows, cards_diag = relevant_cards_rows(cards_dbs, home_id, away_id)
    cosmetic_catalog, cosmetic_catalog_diag = full_cosmetic_catalog(cards_dbs)
    # The FUT DB categories are the authoritative ItemData identities. In the
    # user's exact retail DB team 241 resolves to home asset 14/category 2 and
    # away asset 15/category 3. Do not substitute teamkits IDs 1491/1492 here.
    home_cards = exact_fut_kit_card(cards_rows, team_id=KIT_TEAM_ID, category=2)
    away_cards = exact_fut_kit_card(cards_rows, team_id=KIT_TEAM_ID, category=3)
    stadium_cards = exact_fut_stadium_card(cards_rows, STADIUM_ID) or best_cards_row(cards_rows, asset_id=STADIUM_ID, stadium_id=STADIUM_ID)

    resolved = {
        "kitTeamId": KIT_TEAM_ID,
        "homeKit": resolve_item(main_assets.get("home"), home_cards, item_type="kit", item_state="activeHomeKit", kit_type=0),
        "awayKit": resolve_item(main_assets.get("away"), away_cards, item_type="kit", item_state="activeAwayKit", kit_type=1),
        "stadium": resolve_item(main_assets.get("stadium"), stadium_cards, item_type="stadium", item_state="activeStadium"),
    }
    valid_kits = (
        home_cards is not None and away_cards is not None
        and resolved["homeKit"]["assetId"] > 0 and resolved["awayKit"]["assetId"] > 0
        and int(resolved["homeKit"].get("category", -1)) == 2
        and int(resolved["awayKit"].get("category", -1)) == 3
    )
    doc = {
        "schema": "fifa14-local-fut-v2.41.1-beta2.22-match-assets",
        "gameRoot": str(root),
        "readOnly": True,
        "resolved": resolved,
        "validKits": bool(valid_kits),
        "mainDbAssets": main_assets,
        "mainDbDiagnostics": main_diag,
        "cardsCandidateRows": cards_rows[:250],
        "cardsDiagnostics": cards_diag,
        "catalog": cosmetic_catalog,
        "catalogCounts": {key: len(value) for key, value in cosmetic_catalog.items()},
        "catalogDiagnostics": cosmetic_catalog_diag,
        "note": "No FIFA archive is modified. FUT assetId comes from fcc_kitcards.assetid; resourceId/definitionId uses the unique fcc carddbid. fcc category is retained as its own classification field; BETA 2.22 never aliases it to ItemData cardsubtypeid. Kits use kit, stadiums use stadium, and retail fcc_badgecards are exposed through the existing custom/badge family on the wire.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "validKits": bool(valid_kits),
        "homeAssetId": resolved["homeKit"]["assetId"],
        "awayAssetId": resolved["awayKit"]["assetId"],
        "stadiumId": STADIUM_ID,
        "catalogCounts": {key: len(value) for key, value in cosmetic_catalog.items()},
        "mainDbCount": len(main_dbs),
        "cardsDbCount": len(cards_dbs),
    }))
    return 0 if valid_kits else 2


if __name__ == "__main__":
    raise SystemExit(main())
