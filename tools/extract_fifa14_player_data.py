#!/usr/bin/env python3
"""Export FIFA 14 base player data from installed archives without writing them."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scan_fifa14_match_assets import parse_db_candidates, row_int, safe_rows
from patch_fifa14_fut_legends_db import playername_map


ARCHIVES = ("patch.big", "cards0.big")
META_NAMES = ("cards_ng_db-meta.xml", "cards_ng_db_meta.xml", "fifa_ng_db-meta.xml")
GAMEPLAY_FIELDS = (
    "acceleration", "sprintspeed", "positioning", "finishing", "shotpower", "longshots", "volleys", "penalties",
    "vision", "crossing", "freekickaccuracy", "shortpassing", "longpassing", "curve",
    "agility", "balance", "reactions", "ballcontrol", "dribbling",
    "interceptions", "headingaccuracy", "marking", "standingtackle", "slidingtackle",
    "jumping", "stamina", "strength", "aggression",
    "gkdiving", "gkhandling", "gkkicking", "gkreflexes", "gkpositioning",
)
TRAIT_FIELDS = (
    "weakfootabilitytypecode", "skillmoves", "preferredfoot", "attackingworkrate", "defensiveworkrate",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def first_name(row: dict[str, Any], names: dict[int, str], key: str) -> str:
    value = row_int(row, key, default=0)
    return str(names.get(value, "")) if value > 0 else ""


def build_export(parsed_dbs: list[tuple[Any, dict[str, Any]]], game_root: Path) -> dict[str, Any]:
    resolved: dict[int, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    for source_rank, (db, source) in enumerate(parsed_dbs):
        players_table = db.table("players")
        links_table = db.table("teamplayerlinks")
        league_links_table = db.table("leagueteamlinks")
        if players_table is None:
            diagnostics.append({"source": source, "error": "players table missing"})
            continue
        names_by_id, _names_by_text = playername_map(db)
        player_teams = {
            row_int(row, "playerid"): row_int(row, "teamid")
            for row in safe_rows(db, links_table)
            if row_int(row, "playerid") > 0 and row_int(row, "teamid") > 0
        } if links_table is not None else {}
        team_leagues = {
            row_int(row, "teamid"): row_int(row, "leagueid")
            for row in safe_rows(db, league_links_table)
            if row_int(row, "teamid") > 0 and row_int(row, "leagueid") > 0
        } if league_links_table is not None else {}
        exported = 0
        for row in safe_rows(db, players_table):
            asset_id = row_int(row, "playerid")
            if asset_id <= 0:
                continue
            team_id = player_teams.get(asset_id, row_int(row, "teamid"))
            league_id = team_leagues.get(team_id, row_int(row, "leagueid"))
            card = {
                "assetId": asset_id,
                "name": first_name(row, names_by_id, "commonnameid") or first_name(row, names_by_id, "playerjerseynameid"),
                "firstName": first_name(row, names_by_id, "firstnameid"),
                "lastName": first_name(row, names_by_id, "lastnameid"),
                "rating": row_int(row, "overallrating"),
                "teamId": team_id,
                "leagueId": league_id,
                "nation": row_int(row, "nationality"),
                "gameplayAttributes": {field: row_int(row, field) for field in GAMEPLAY_FIELDS},
                "traits": {field: row_int(row, field) for field in TRAIT_FIELDS},
            }
            card["name"] = card["name"] or " ".join(value for value in (card["firstName"], card["lastName"]) if value).strip()
            score = (1 if card["name"] else 0, 1 if team_id > 0 else 0, 1 if league_id > 0 else 0, -source_rank)
            previous = resolved.get(asset_id)
            if previous is None or tuple(previous["_score"]) < score:
                card["_score"] = list(score)
                resolved[asset_id] = card
            exported += 1
        diagnostics.append({
            "source": source,
            "playersRead": exported,
            "playerTeamLinks": len(player_teams),
            "teamLeagueLinks": len(team_leagues),
        })
    players = []
    for player in resolved.values():
        player.pop("_score", None)
        players.append(player)
    players.sort(key=lambda player: int(player["assetId"]))
    archive_hashes = {
        archive: sha256(game_root / archive)
        for archive in ARCHIVES if (game_root / archive).is_file()
    }
    return {
        "schema": "fifa14-player-data-v1",
        "readOnly": True,
        "gameRoot": str(game_root),
        "archiveSha256": archive_hashes,
        "gameplayFields": list(GAMEPLAY_FIELDS),
        "traitFields": list(TRAIT_FIELDS),
        "players": players,
        "diagnostics": diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export full FIFA 14 player data read-only")
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    game_root = args.game_root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    parsed = parse_db_candidates(game_root, ARCHIVES, "cards_ng_db.db", META_NAMES, cards=True)
    document = build_export(parsed, game_root)
    if not document["players"]:
        raise RuntimeError("could not extract any player rows from cards_ng_db")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "players": len(document["players"]), "sources": len(parsed)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())