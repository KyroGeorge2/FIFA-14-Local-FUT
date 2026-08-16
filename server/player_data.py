"""Validated access to the read-only full player-data cache emitted at launch."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA = "fifa14-player-data-v1"
GAMEPLAY_FIELDS = (
    "acceleration", "sprintspeed", "positioning", "finishing", "shotpower", "longshots", "volleys", "penalties",
    "vision", "crossing", "freekickaccuracy", "shortpassing", "longpassing", "curve",
    "agility", "balance", "reactions", "ballcontrol", "dribbling",
    "interceptions", "headingaccuracy", "marking", "standingtackle", "slidingtackle",
    "jumping", "stamina", "strength", "aggression",
    "gkdiving", "gkhandling", "gkkicking", "gkreflexes", "gkpositioning",
)
TRAIT_RANGES = {
    "weakfootabilitytypecode": (1, 5), "skillmoves": (0, 4), "preferredfoot": (1, 2),
    "attackingworkrate": (0, 2), "defensiveworkrate": (0, 2),
}


def load_player_data(path: Path) -> tuple[dict[int, dict[str, Any]], str | None]:
    if not path.is_file():
        return {}, "player-data cache is missing; launch FIFA Local FUT to generate it"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {}, f"could not read player-data cache: {error}"
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        return {}, "player-data cache has an unsupported schema"
    rows = document.get("players")
    if not isinstance(rows, list):
        return {}, "player-data cache has no player list"
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            asset_id = int(row["assetId"])
            stats = row["gameplayAttributes"]
            traits = row["traits"]
            if asset_id <= 0 or not isinstance(stats, dict) or not isinstance(traits, dict):
                continue
            if any(not 1 <= int(stats[field]) <= 99 for field in GAMEPLAY_FIELDS):
                continue
            if any(not minimum <= int(traits[field]) <= maximum for field, (minimum, maximum) in TRAIT_RANGES.items()):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        result[asset_id] = dict(row)
    if not result:
        return {}, "player-data cache contains no valid player rows"
    return result, None