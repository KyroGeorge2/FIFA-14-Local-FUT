from __future__ import annotations

import base64
import json
import math
import os
import random
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from local_identity import (
    LocalIdentityStore,
    PLAYER_CATALOG,
    PLAYER_BY_ASSET,
    PLAYER_ITEM_TYPE,
)

BETA_SCHEMA = "fifa14-local-fut-v2.41.1-beta2.24"
STARTER_ITEM_BASE = 190_000_000_000
DEFAULT_STARTER_CLUB_NAME = "Local FUT"
DEFAULT_STARTER_CLUB_ABBR = "LFT"
DEFAULT_STARTER_BADGE_ID = 241
# Keep the venue as backend state until the retail client tells us its exact
# stadium-item contract. The match bootstrap can safely fall back to a known
# retail venue name without fabricating an unknown item resource.
DEFAULT_STARTER_STADIUM_NAME = "Town Park"
# BETA 2.22 keeps match-facing kit/resource IDs resolved from the user's installed
# FIFA databases before the backend starts. Previous betas fabricated 90M
# resource IDs, which the retail client could parse as ItemData but could not
# resolve to real textures.  The synthetic *owned item IDs* remain local-only;
# their asset/resource/team fields now come from the retail DB scan.
STARTER_COSMETIC_ITEM_BASE = 191_000_000_000
DEFAULT_STARTER_STADIUM_ID = 34
MATCH_ASSET_REPORT = Path(__file__).resolve().parent.parent / "artifacts" / "fifa14-match-assets-v2411-beta222.json"


def _resolved_match_assets() -> dict[str, Any]:
    # Exact fallback values mirror the retail fcc_kitcards/fcc_stadium rows
    # recovered from this FIFA 14 database. The launcher normally refuses to
    # start if the read-only scanner cannot rediscover these rows.
    fallback = {
        "kitTeamId": 241,
        # fcc category/card metadata is retained as scan evidence. The native
        # category/team-kit/stadium classification is deliberately kept on the
        # BETA 2.22 cosmetic wire, but it is never aliased to cardsubtypeid.
        "homeKit": {"assetId": 14, "resourceId": 6300000, "definitionId": 6300000, "teamid": 241,
                    "category": 2, "teamkittypetechid": 0, "carddbid": 6300000, "cardassetid": 35, "year": 0,
                    "value": 89, "weightrare": 10},
        "awayKit": {"assetId": 15, "resourceId": 6400000, "definitionId": 6400000, "teamid": 241,
                    "category": 3, "teamkittypetechid": 1, "carddbid": 6400000, "cardassetid": 35, "year": 0,
                    "value": 89, "weightrare": 10},
        "stadium": {"assetId": DEFAULT_STARTER_STADIUM_ID, "resourceId": 6200016, "definitionId": 6200016,
                    "category": 4, "teamid": 0, "stadiumid": DEFAULT_STARTER_STADIUM_ID,
                    "carddbid": 6200016, "cardassetid": 36, "name": DEFAULT_STARTER_STADIUM_NAME,
                    "value": 64, "weightrare": 0},
    }
    try:
        document = json.loads(MATCH_ASSET_REPORT.read_text(encoding="utf-8"))
        resolved = document.get("resolved") if isinstance(document, dict) else None
        if not isinstance(resolved, dict):
            return fallback
        result = dict(fallback)
        result.update(resolved)
        for key in ("homeKit", "awayKit", "stadium"):
            if not isinstance(result.get(key), dict):
                result[key] = dict(fallback[key])
            else:
                merged = dict(fallback[key]); merged.update(result[key]); result[key] = merged
        return result
    except Exception:
        return fallback


def _resolved_cosmetic_catalog() -> dict[str, list[dict[str, Any]]]:
    """Return the retail cosmetic catalogue emitted by the launch-time DB scan.

    The scan is read-only and runs against the user's installed cards_ng_db.db.
    A missing catalogue is non-fatal: the three proven starter cosmetics remain
    available through ``_resolved_match_assets``.
    """
    empty = {"kits": [], "stadiums": [], "badges": []}
    try:
        document = json.loads(MATCH_ASSET_REPORT.read_text(encoding="utf-8"))
    except Exception:
        return empty
    catalog = document.get("catalog") if isinstance(document, dict) else None
    if not isinstance(catalog, dict):
        return empty
    result: dict[str, list[dict[str, Any]]] = {}
    for key in ("kits", "stadiums", "badges"):
        rows = catalog.get(key)
        result[key] = [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    return result

# BETA 2.6 no longer guesses the competition JSON contract. The exact field
# names/types below were recovered from this user's retail CardsDLLzf.dll
# (SHA-256 642B11EF...F060E6) and the BETA 2.6 live parser trace.
#
# The season-list parser consumes 1-based `id`, the literal type enum OFFLINE,
# a divisionId, numMatches/matchLengthMin, and an array-shaped prizeSet. The
# old beta payload used names such as seasonId/division/pointsToPromote which
# that parser simply did not handle, leaving native record fields at defaults.
OFFLINE_SEASONS = [
    {
        "seasonId": 1,       # season/user is 1-based; native client stores id-1
        "division": 10,
        "name": "World Tour",
        "matchesToPlay": 10,
        "pointsToWinTitle": 12,
        "pointsToPromote": 9,
        "pointsToAvoidRelegation": 0,
        "titleRewardCoins": 1900,
        "promotionRewardCoins": 1500,
        "holdingRewardCoins": 300,
        "difficulty": "1 star",
    },
]

# division, display-name (server-side only), matches, promotion threshold,
# championship coin award. Only the parser-native wire members are emitted.
OFFLINE_SEASON_DIVISIONS = [
    (10, "World Tour", 10, 9, 1900),
    (9, "World Tour", 10, 11, 2100),
    (8, "World Tour", 10, 13, 2900),
    (7, "World Tour", 10, 14, 3300),
    (6, "World Tour", 10, 16, 3800),
    (5, "World Tour", 10, 16, 4400),
    (4, "World Tour", 10, 18, 5000),
    (3, "World Tour", 10, 19, 6000),
    (2, "World Tour", 10, 21, 7500),
    (1, "World Tour", 10, 23, 10000),
]

# Tournament BETA 2.2 froze after a guessed record containing `rounds: 4`.
# The retail parser proves `rounds` is an ARRAY. BETA 2.6 uses only keys the
# exact PC tournament parser handles. A safe-empty fallback remains available
# with FIFA14_TOURNAMENT_MODE=empty.
OFFLINE_TOURNAMENTS = [
    {
        "tournamentId": 1, "name": "Starter Cup", "prize": 500, "repeatPrize": 300, "trophyResourceId": 7100001,
        "rounds": [(1, 150), (1, 200), (2, 300), (2, 500)],
    },
    {
        "tournamentId": 2, "name": "Bronze Cup", "prize": 750, "repeatPrize": 450, "trophyResourceId": 7100002,
        "rounds": [(1, 175), (2, 250), (2, 375), (3, 650)],
    },
    {
        "tournamentId": 3, "name": "Silver Cup", "prize": 1000, "repeatPrize": 600, "trophyResourceId": 7100003,
        "rounds": [(2, 225), (2, 325), (3, 500), (3, 850)],
    },
    {
        "tournamentId": 4, "name": "Gold Cup", "prize": 1500, "repeatPrize": 900, "trophyResourceId": 7100004,
        "rounds": [(3, 300), (3, 450), (4, 700), (4, 1200)],
    },
]

# Verified retail FIFA 14 club IDs from the bundled 10,274-card catalogue.
# The tournament client asks for count=15 after selecting a 16-team cup; the
# user's FUT club occupies the remaining bracket slot.  Seasons reuse the first
# ten clubs for their ten scheduled AI fixtures.
OFFLINE_COMPETITION_TEAM_IDS = (
    241, 243, 21, 69, 10, 11, 9, 22, 5, 73,
    1, 13, 18, 2, 7,
)


def _season_matches(division: int, match_count: int) -> list[dict[str, Any]]:
    # The exact retail match-record parser consumes teamId, difficulty,
    # rewardMult, roundId and coins.  season/user's wire round is 1-based and
    # decremented internally, while match roundId is stored directly; therefore
    # scheduled roundId values are 0..N-1.
    base_difficulty = max(1, min(5, 1 + (10 - int(division)) // 2))
    rows: list[dict[str, Any]] = []
    for index in range(max(0, int(match_count))):
        rows.append({
            "teamId": int(OFFLINE_COMPETITION_TEAM_IDS[index % len(OFFLINE_COMPETITION_TEAM_IDS)]),
            "difficulty": int(min(5, base_difficulty + (1 if index >= 7 else 0))),
            "rewardMult": 1,
            "roundId": int(index),
            "coins": int(250 + (10 - int(division)) * 25 + index * 10),
        })
    return rows


def _coin_award(value: int) -> dict[str, Any]:
    return {"type": "coin", "value": int(value), "assetId": 0, "count": 1, "halId": 0, "teamId": 0}


def _season_prize(prize_level: str, threshold: int, coin_value: int = 0) -> dict[str, Any]:
    awards = [] if int(coin_value) <= 0 else [_coin_award(int(coin_value))]
    return {
        "prizeLevel": str(prize_level),
        "thresholdPoint": int(threshold),
        "awardMappings": [{"awards": awards}],
    }


def _native_season_record(index: int, division: int, matches: int, promote: int, championship_coins: int) -> dict[str, Any]:
    # Division 10 thresholds are known from the retail frontend (12/9/0).
    # Higher-division championship thresholds retain the prior conservative
    # promote+3 progression, but crucially they now travel through the exact
    # native prizeSet schema rather than unrecognised top-level members.
    title_threshold = 12 if int(division) == 10 else min(30, int(promote) + 3)
    maintenance_threshold = 0
    holding_coins = 300 if int(division) == 10 else max(300, int(championship_coins) // 5)
    promotion_coins = 1500 if int(division) == 10 else max(500, int(championship_coins) - 400)
    return {
        "id": int(index),
        "type": "OFFLINE",
        "divisionId": int(division),
        "numMatches": int(matches),
        "matchLengthMin": 6,
        "matches": _season_matches(int(division), int(matches)),
        "prizeSet": [
            _season_prize("RELEGATION", 0, 0),
            _season_prize("MAINTENANCE", maintenance_threshold, holding_coins),
            _season_prize("PROMOTION", int(promote), promotion_coins),
            _season_prize("CHAMPIONSHIP", title_threshold, int(championship_coins)),
        ],
        "elgOperation": "AND",
        "elgReq": [],
        # -1 is the native "no trophy resource" sentinel experiment. BETA 2.7
        # used 0, which made the client perform ten meaningless item-0 lookups.
        "trophyResourceId": -1,
        "trophyUseCount": 0,
        "visStartDays": 3650,
        "visEndDays": 3650,
        "startDateTime": 0,
        "endDateTime": 2147483647,
        "untilStartSeconds": 0,
        "untilEndSeconds": 315360000,
    }


def _native_tournament_round(round_id: int, difficulty: int, coins: int) -> dict[str, Any]:
    return {
        "id": int(round_id),
        "difficulty": int(difficulty),
        "rewardMultiplier": 1,
        "coins": int(coins),
    }


def _native_tournament_record(definition: dict[str, Any]) -> dict[str, Any]:
    tournament_id = max(1, int(definition.get("tournamentId", 1) or 1))
    round_defs = definition.get("rounds")
    if not isinstance(round_defs, list) or not round_defs:
        round_defs = [(1, 150), (1, 200), (2, 300), (2, 500)]
    rounds: list[dict[str, Any]] = []
    for round_index, row in enumerate(round_defs, start=1):
        try:
            difficulty, coins = row
        except (TypeError, ValueError):
            difficulty, coins = 1, 0
        rounds.append(_native_tournament_round(round_index, int(difficulty), int(coins)))
    return {
        "id": tournament_id,
        "type": "offline",
        "treeType": "knockout",
        "aigroup": 0,
        "eligibilityOperation": "AND",
        "elgReq": [],
        "numTeams": 16,
        "numRounds": len(rounds),
        "matchlength": 6,
        "rounds": rounds,
        "awardSet": {"awards": [{"awardType": 1, "value": int(definition.get("prize", 0) or 0), "halid": 0}]},
        "lock": "UNLOCKED",
        "unlockreq": 0,
        "triesMax": 0,
        "triesPeriod": 0,
        "triesRemaining": 0,
        "nextReset": 0,
        "starttime": 0,
        "endtime": 2147483647,
        "timeUntilStart": 0,
        "timeUntilEnd": 315360000,
        "visStart": 3650,
        "visEnd": 3650,
        # BETA 2.22 gives each local cup its own static trophy resource instead
        # of the old 0 sentinel. The static resource carries NAME/IMAGEFILE_*
        # members used by the retail GetOfflineTournamentInfo/fccTournamentTrophies
        # path; the associated .big remains a safe empty archive until the exact
        # trophy image payload format is measured.
        "trophyResourceId": int(definition.get("trophyResourceId", 0) or 0),
        "trophyUserCount": 0,
    }


def _native_starter_tournament() -> dict[str, Any]:
    # Compatibility helper retained for older tests/tools.
    return _native_tournament_record(OFFLINE_TOURNAMENTS[0])


def _utc_day(ts: int | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts if ts is not None else time.time())))


class BetaIdentityStore(LocalIdentityStore):
    """v2.41 local-play account/economy/match state layered on the stable FUT store.

    The BETA database is persistent across extracted builds. A truly fresh database
    receives one untradeable bronze starter squad and starts at 0 coins / 0 FIFA
    Points; once a club exists, later builds must preserve that club, its squad,
    cosmetics and economy. Match and wallet writes are transactional/idempotent.
    """

    def __init__(self, database: str, initial_mode: str = "existing") -> None:
        self.started_at = int(time.time())
        super().__init__(database, initial_mode)
        self._initialize_beta_schema()
        self.ensure_beta_starter_club()

    def _initialize_beta_schema(self) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS beta_accounts (
                    persona_id INTEGER PRIMARY KEY,
                    account_uuid TEXT NOT NULL UNIQUE,
                    discord_user_id TEXT UNIQUE,
                    discord_username TEXT,
                    auth_state TEXT NOT NULL DEFAULT 'local-unlinked',
                    created_at INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    dnf_modifier REAL NOT NULL DEFAULT 1.25
                );
                CREATE TABLE IF NOT EXISTS wallet_transactions (
                    transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    persona_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    balance_before INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    reference_type TEXT,
                    reference_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(persona_id, currency, reason, reference_type, reference_id)
                );
                CREATE TABLE IF NOT EXISTS beta_match_sessions (
                    match_id TEXT PRIMARY KEY,
                    persona_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    difficulty TEXT,
                    stadium_name TEXT,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    completed_at INTEGER,
                    result TEXT,
                    home_goals INTEGER,
                    away_goals INTEGER,
                    minutes_played INTEGER,
                    reward_coins INTEGER NOT NULL DEFAULT 0,
                    reward_breakdown_json TEXT NOT NULL DEFAULT '{}',
                    raw_result_json TEXT NOT NULL DEFAULT '{}',
                    easfc_signal INTEGER,
                    settled INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS beta_counters (
                    counter_key TEXT PRIMARY KEY,
                    counter_value INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS beta_daily_counters (
                    day TEXT NOT NULL,
                    counter_key TEXT NOT NULL,
                    counter_value INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, counter_key)
                );
                CREATE TABLE IF NOT EXISTS beta_club_settings (
                    persona_id INTEGER PRIMARY KEY,
                    stadium_name TEXT NOT NULL,
                    home_kit_resource_id INTEGER,
                    away_kit_resource_id INTEGER,
                    badge_resource_id INTEGER,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS beta_offline_seasons (
                    persona_id INTEGER NOT NULL,
                    season_id INTEGER NOT NULL,
                    division INTEGER NOT NULL,
                    matches_played INTEGER NOT NULL DEFAULT 0,
                    points INTEGER NOT NULL DEFAULT 0,
                    won INTEGER NOT NULL DEFAULT 0,
                    draw INTEGER NOT NULL DEFAULT 0,
                    lost INTEGER NOT NULL DEFAULT 0,
                    trophies_won INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(persona_id, season_id)
                );
                CREATE TABLE IF NOT EXISTS beta_offline_tournaments (
                    persona_id INTEGER NOT NULL,
                    tournament_id INTEGER NOT NULL,
                    current_round INTEGER NOT NULL DEFAULT 0,
                    won INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(persona_id, tournament_id)
                );
                CREATE TABLE IF NOT EXISTS beta_tournament_progress (
                    persona_id INTEGER NOT NULL,
                    tournament_id INTEGER NOT NULL,
                    round_value INTEGER NOT NULL DEFAULT 1,
                    data_version INTEGER NOT NULL DEFAULT 1,
                    tournament_data TEXT NOT NULL DEFAULT '',
                    progress_data_version INTEGER NOT NULL DEFAULT 1,
                    progress_data TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(persona_id, tournament_id)
                );
                """
            )
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            now = int(time.time())
            connection.execute(
                """
                INSERT OR IGNORE INTO beta_accounts (
                    persona_id, account_uuid, discord_user_id, discord_username,
                    auth_state, created_at, last_seen, dnf_modifier
                ) VALUES (?, ?, NULL, NULL, 'local-unlinked', ?, ?, 1.25)
                """,
                (persona_id, str(uuid.uuid4()), now, now),
            )
            connection.execute(
                "UPDATE beta_accounts SET last_seen=? WHERE persona_id=?",
                (now, persona_id),
            )
            for season in OFFLINE_SEASONS:
                connection.execute(
                    """INSERT OR IGNORE INTO beta_offline_seasons
                    (persona_id,season_id,division,matches_played,points,won,draw,lost,trophies_won,active,updated_at)
                    VALUES (?,?,?,0,0,0,0,0,0,1,?)""",
                    (persona_id, int(season["seasonId"]), int(season["division"]), now),
                )
            for tournament in OFFLINE_TOURNAMENTS:
                connection.execute(
                    """INSERT OR IGNORE INTO beta_offline_tournaments
                    (persona_id,tournament_id,current_round,won,active,updated_at)
                    VALUES (?,?,0,0,1,?)""",
                    (persona_id, int(tournament["tournamentId"]), now),
                )
            # BETA 2.18 persisted the first-round pre-match tournamentData blob
            # as an "Underway" save even though progressData was only zero bytes.
            # Clear those stale markers once so reopening the cup starts fresh.
            stale_rows = connection.execute(
                "SELECT tournament_id,round_value,tournament_data,progress_data FROM beta_tournament_progress WHERE persona_id=?",
                (persona_id,),
            ).fetchall()
            for stale in stale_rows:
                if self._tournament_progress_is_resumable(
                    int(stale["round_value"]), str(stale["tournament_data"] or ""), str(stale["progress_data"] or "")
                ):
                    continue
                connection.execute(
                    "UPDATE beta_tournament_progress SET round_value=1,tournament_data='',progress_data='',updated_at=? "
                    "WHERE persona_id=? AND tournament_id=?",
                    (now, persona_id, int(stale["tournament_id"])),
                )
                connection.execute(
                    "UPDATE beta_offline_tournaments SET current_round=0,updated_at=? WHERE persona_id=? AND tournament_id=?",
                    (now, persona_id, int(stale["tournament_id"])),
                )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta (meta_key, meta_value) VALUES ('beta_schema', ?)",
                (BETA_SCHEMA,),
            )

    @staticmethod
    def _starter_requirements() -> list[str]:
        # Retail 4-4-2 ordering used throughout the existing squad contract.
        return [
            "ST", "ST", "LM", "CM", "CM", "RM", "LB", "CB", "CB", "RB", "GK",
            "CB", "CB", "LB", "RB", "CM", "LM", "RM", "ST", "CB", "CM", "RW", "ST",
        ]

    @staticmethod
    def _bronze_candidates(position: str) -> list[dict[str, Any]]:
        requested = position.upper()
        exact = [
            p for p in PLAYER_CATALOG
            if str(p.get("quality", "")).lower() == "bronze"
            and str(p.get("position", "")).upper() == requested
        ]
        if exact:
            return exact
        band: set[str]
        if requested == "GK":
            band = {"GK"}
        elif requested in {"RB", "RWB", "CB", "LB", "LWB"}:
            band = {"RB", "RWB", "CB", "LB", "LWB"}
        elif requested in {"CDM", "CM", "CAM", "RM", "LM"}:
            band = {"CDM", "CM", "CAM", "RM", "LM"}
        else:
            band = {"RW", "LW", "CF", "ST"}
        return [
            p for p in PLAYER_CATALOG
            if str(p.get("quality", "")).lower() == "bronze"
            and str(p.get("position", "")).upper() in band
        ]

    def _starter_players(self, persona_id: int) -> list[dict[str, Any]]:
        rng = random.Random((int(persona_id) << 16) ^ 0xF14BE7A1)
        chosen: list[dict[str, Any]] = []
        used: set[int] = set()
        for position in self._starter_requirements():
            candidates = [p for p in self._bronze_candidates(position) if int(p["assetId"]) not in used]
            if not candidates:
                raise RuntimeError(f"no unused bronze starter candidate for {position}")
            # Weight toward ordinary low/mid bronze cards rather than the very top
            # of the tier, while remaining deterministic per account.
            candidates.sort(key=lambda p: (int(p.get("rating", 0)), int(p["assetId"])))
            lo = max(0, len(candidates) // 5)
            hi = max(lo + 1, min(len(candidates), (len(candidates) * 4) // 5))
            pick = candidates[rng.randrange(lo, hi)]
            used.add(int(pick["assetId"]))
            chosen.append(pick)
        return chosen

    @staticmethod
    def _cosmetic_payload(
        *, item_id: int, asset_id: int, resource_id: int, wire_item_type: str,
        item_state: str, team_id: int = 0, rating: int = 0, rareflag: int = 0,
        category: int | None = None, teamkittypetechid: int | None = None,
        stadium_id: int | None = None, badge_dbid: int | None = None, name: str = "",
    ) -> dict[str, Any]:
        """Build a parser-ordered FUT14 ItemData envelope for club cosmetics.

        BETA 2.22 keeps the BETA 2.17-proven kit/stadium wire untouched and adds
        badges through the retail ``custom`` family used by My Club statistics.
        Database-only FCC fields stay off the wire and ``itemType`` deliberately
        precedes the static asset/resource identity.
        """
        payload: dict[str, Any] = {
            "id": int(item_id),
            "itemId": int(item_id),
            "timestamp": int(time.time()),
            "itemType": str(wire_item_type),
        }
        if category is not None:
            payload["category"] = int(category)
        if teamkittypetechid is not None:
            payload["teamkittypetechid"] = int(teamkittypetechid)
        if stadium_id is not None:
            payload["stadiumid"] = int(stadium_id)
            payload["StadiumId"] = int(stadium_id)
        if badge_dbid is not None:
            payload["badgeDBid"] = int(badge_dbid)
            payload["badge"] = int(asset_id)
            payload["badgeId"] = int(asset_id)
            payload["badgeResourceId"] = int(resource_id)
            payload["badgeDefinitionId"] = int(resource_id)
        payload.update({
            "rating": int(rating),
            "assetId": int(asset_id),
            "resourceId": int(resource_id),
            "definitionId": int(resource_id),
            "itemState": str(item_state),
            "rareflag": int(rareflag),
            "formation": "",
            "leagueId": 0,
            "injuryType": "none",
            "injuryGames": 0,
            "lastSalePrice": 0,
            "fitness": 0,
            "training": 0,
            "suspension": 0,
            "contract": 0,
            "discardValue": 0,
            "owners": 1,
            "teamid": int(team_id),
            "teamId": int(team_id),
            "untradeable": True,
            "duplicate": False,
            "pile": 7,
            "resourceGameYear": 2014,
        })
        if name:
            payload["name"] = str(name)
            payload["description"] = str(name)
        return payload

    @classmethod
    def _cosmetic_wire_payload(cls, source: dict[str, Any]) -> dict[str, Any]:
        """Project persisted FCC metadata to a deterministic retail wire object."""
        raw_type = str(source.get("itemType") or "").strip()
        if raw_type == "clubInfo":
            raw_type = "kit"
        if raw_type in {"badge", "badges"}:
            raw_type = "custom"
        if raw_type not in {"kit", "stadium", "custom"}:
            if any(k in source for k in ("badgeDBid", "badge")):
                raw_type = "custom"
            elif any(k in source for k in ("stadiumid", "StadiumId")):
                raw_type = "stadium"
            else:
                raw_type = "kit"

        is_stadium = raw_type == "stadium"
        is_badge = raw_type == "custom"
        category = int(source.get("category", 4 if is_stadium else 0) or 0)
        teamkit_type: int | None = None
        stadium_id: int | None = None
        badge_dbid: int | None = None
        if is_stadium:
            stadium_id = int(source.get("stadiumid", source.get("StadiumId", source.get("assetId", 0))) or 0)
        elif is_badge:
            badge_dbid = int(source.get("badgeDBid", source.get("carddbid", source.get("resourceId", 0))) or 0)
        else:
            teamkit_type = int(source.get("teamkittypetechid", 0 if category == 2 else 1) or 0)

        return cls._cosmetic_payload(
            item_id=int(source.get("id", source.get("itemId", 0)) or 0),
            asset_id=int(source.get("assetId", 0) or 0),
            resource_id=int(source.get("resourceId", source.get("definitionId", source.get("assetId", 0))) or 0),
            wire_item_type=raw_type,
            item_state=str(source.get("itemState") or "free"),
            team_id=int(source.get("teamid", source.get("teamId", 0)) or 0),
            rating=int(source.get("rating", source.get("value", 0)) or 0),
            rareflag=int(source.get("rareflag", source.get("rareFlag", 0)) or 0),
            category=category,
            teamkittypetechid=teamkit_type,
            stadium_id=stadium_id,
            badge_dbid=badge_dbid,
            name=str(source.get("name") or source.get("description") or ""),
        )

    @staticmethod
    def _cosmetic_state_for_kind(kind: str) -> str:
        return {
            "homeKit": "activeHomeKit",
            "awayKit": "activeAwayKit",
            "stadium": "activeStadium",
            "badge": "activeBadge",
        }[kind]

    def _beta_cosmetic_definitions(self, persona_id: int) -> list[tuple[str, int, dict[str, Any]]]:
        """Return proven starters plus every retail kit/stadium/badge scanned locally."""
        native = _resolved_match_assets()
        starters = [
            ("homeKit", STARTER_COSMETIC_ITEM_BASE + int(persona_id) * 10 + 1, dict(native.get("homeKit") or {})),
            ("awayKit", STARTER_COSMETIC_ITEM_BASE + int(persona_id) * 10 + 2, dict(native.get("awayKit") or {})),
            ("stadium", STARTER_COSMETIC_ITEM_BASE + int(persona_id) * 10 + 3, dict(native.get("stadium") or {})),
        ]
        definitions = list(starters)
        seen_resources = {
            int(row.get("resourceId", row.get("definitionId", 0)) or 0)
            for _kind, _item_id, row in starters
        }
        catalog = _resolved_cosmetic_catalog()
        ranges = {
            "kits": ("kit", 192_000_000_000),
            "stadiums": ("stadium", 193_000_000_000),
            "badges": ("badge", 194_000_000_000),
        }
        for group, (kind, item_base) in ranges.items():
            for native_row in catalog.get(group, []):
                resource_id = int(native_row.get("resourceId", native_row.get("definitionId", 0)) or 0)
                asset_id = int(native_row.get("assetId", 0) or 0)
                if resource_id <= 0 or asset_id <= 0 or resource_id in seen_resources:
                    continue
                seen_resources.add(resource_id)
                definitions.append((kind, item_base + resource_id, dict(native_row)))
        return definitions

    def _ensure_beta_cosmetics_locked(self, connection: sqlite3.Connection, persona_id: int) -> list[dict[str, Any]]:
        """Seed the complete retail cosmetic catalogue without disturbing active choices."""
        now = int(time.time())
        definitions = self._beta_cosmetic_definitions(persona_id)
        signature_parts = []
        for kind, item_id, row in definitions:
            signature_parts.append((kind, int(item_id), int(row.get("resourceId", row.get("definitionId", 0)) or 0)))
        catalog_signature = json.dumps(signature_parts, separators=(",", ":"))
        marker = connection.execute(
            "SELECT meta_value FROM schema_meta WHERE meta_key='beta222_cosmetic_catalog_signature'"
        ).fetchone()
        if marker is not None and str(marker[0]) == catalog_signature:
            rows = connection.execute(
                "SELECT payload FROM items WHERE persona_id=? AND item_type IN ('kit','stadium','custom') ORDER BY item_id",
                (int(persona_id),),
            ).fetchall()
            result = []
            for row in rows:
                try:
                    payload = json.loads(row["payload"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    result.append(payload)
            return result

        payloads: list[dict[str, Any]] = []
        for kind, item_id, native_row in definitions:
            is_stadium = kind == "stadium"
            is_badge = kind == "badge"
            db_item_type = "stadium" if is_stadium else "custom" if is_badge else "kit"
            wire_item_type = db_item_type
            asset_id = int(native_row.get("assetId", DEFAULT_STARTER_STADIUM_ID if is_stadium else 0) or 0)
            resource_id = int(native_row.get("resourceId", native_row.get("definitionId", asset_id)) or asset_id)
            if asset_id <= 0 or resource_id <= 0:
                continue
            team_id = int(native_row.get("teamid", native_row.get("teamId", 0)) or 0)
            rating = int(native_row.get("value", native_row.get("rating", 0)) or 0)
            weight_rare = int(native_row.get("weightrare", native_row.get("rareflag", 0)) or 0)
            rareflag = int(native_row.get("rareflag", 1 if weight_rare > 0 else 0) or 0)

            existing_row = connection.execute(
                "SELECT payload FROM items WHERE persona_id=? AND item_id=?",
                (int(persona_id), int(item_id)),
            ).fetchone()
            existing_payload: dict[str, Any] = {}
            if existing_row is not None:
                try:
                    decoded = json.loads(existing_row["payload"] or "{}")
                    if isinstance(decoded, dict):
                        existing_payload = decoded
                except (TypeError, json.JSONDecodeError):
                    pass

            existing_state = str(existing_payload.get("itemState", "free"))
            allowed_states = {"free"}
            if is_stadium:
                allowed_states.add("activeStadium")
            elif is_badge:
                allowed_states.add("activeBadge")
            else:
                allowed_states.update({"activeHomeKit", "activeAwayKit"})
            item_state = existing_state if existing_state in allowed_states else "free"

            category_default = 4 if is_stadium else 0
            native_category = int(native_row.get("category", category_default) or 0)
            native_teamkit_type = None
            native_stadium_id = None
            badge_dbid = None
            if is_stadium:
                native_stadium_id = int(native_row.get("stadiumid", asset_id) or asset_id)
            elif is_badge:
                badge_dbid = int(native_row.get("badgeDBid", native_row.get("carddbid", resource_id)) or resource_id)
            else:
                native_teamkit_type = int(native_row.get("teamkittypetechid", 0 if native_category == 2 else 1) or 0)

            default_name = (
                DEFAULT_STARTER_STADIUM_NAME if is_stadium and native_stadium_id == DEFAULT_STARTER_STADIUM_ID
                else f"Stadium {native_stadium_id}" if is_stadium
                else f"Badge {asset_id}" if is_badge
                else f"Team {team_id} {'Home' if native_category == 2 else 'Away'} Kit"
            )
            native_name = str(native_row.get("name") or default_name)
            payload = self._cosmetic_payload(
                item_id=int(item_id), asset_id=asset_id, resource_id=resource_id,
                wire_item_type=wire_item_type, item_state=item_state,
                team_id=team_id, rating=rating, rareflag=rareflag,
                category=native_category, teamkittypetechid=native_teamkit_type,
                stadium_id=native_stadium_id, badge_dbid=badge_dbid, name=native_name,
            )
            payload["quality"] = "gold" if rating >= 75 else "silver" if rating >= 65 else "bronze"
            for field in ("carddbid", "cardassetid", "year", "value", "weightrare", "teamkitid", "stadiumid", "badgeDBid"):
                if field in native_row:
                    try:
                        payload[field] = int(native_row[field])
                    except (TypeError, ValueError):
                        pass

            connection.execute(
                """INSERT INTO items (item_id,persona_id,asset_id,item_type,pile,tradeable,payload)
                   VALUES (?,?,?,?,'club',0,?)
                   ON CONFLICT(item_id) DO UPDATE SET persona_id=excluded.persona_id,
                     asset_id=excluded.asset_id,item_type=excluded.item_type,pile='club',
                     tradeable=0,payload=excluded.payload""",
                (
                    int(item_id), int(persona_id), int(asset_id), db_item_type,
                    json.dumps(payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True),
                ),
            )
            payloads.append(payload)

        connection.execute(
            "INSERT OR REPLACE INTO schema_meta (meta_key,meta_value) VALUES ('beta222_cosmetic_catalog_signature',?)",
            (catalog_signature,),
        )
        return payloads

    def active_cosmetic_items(self, *, include_badge: bool = False) -> list[dict[str, Any]]:
        """Return cosmetics explicitly activated through the My Club flow.

        The normal squad/My Club refresh needs activeBadge so the crest changes
        immediately after the retail activation PUT. Match creation deliberately
        keeps the proven BETA 2.17 three-item kit/stadium list.
        """
        with self._lock, closing(self._connect()) as connection, connection:
            persona_id = int(self._identity(connection)["persona_id"])
            self._ensure_beta_cosmetics_locked(connection, persona_id)
            found: dict[str, dict[str, Any]] = {}
            rows = connection.execute(
                "SELECT payload FROM items WHERE persona_id=? AND item_type IN ('kit','stadium','custom') ORDER BY item_id",
                (persona_id,),
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                state = str(payload.get("itemState") or "")
                if state in {"activeHomeKit", "activeAwayKit", "activeStadium", "activeBadge"}:
                    found[state] = payload
            states = ["activeHomeKit", "activeAwayKit", "activeStadium"]
            if include_badge:
                states.append("activeBadge")
            return [
                self._cosmetic_wire_payload(found[state])
                for state in states
                if state in found
            ]

    def static_cosmetic_resource(self, resource_id: int) -> dict[str, Any] | None:
        """Resolve owned cosmetics plus local tournament trophy metadata."""
        resource_id = int(resource_id)
        for definition in OFFLINE_TOURNAMENTS:
            if int(definition.get("trophyResourceId", 0) or 0) == resource_id:
                tournament_id = int(definition.get("tournamentId", 0) or 0)
                image_key = f"local_tournament_{tournament_id}"
                # These names are taken from the exact StaticActiveTournamentNameInfo
                # serializer key table (NAME/ID/IMAGEFILE_SMALL/LARGE).
                return {
                    "ID": tournament_id,
                    # StaticActiveTournamentNameInfo resolves NAME through FUT localization.
                    # A literal name becomes the retail "*" missing-text marker.
                    "NAME": f"LOCAL_TOURNAMENT_NAME_{tournament_id}",
                    "NAME_TEXT": str(definition.get("name") or f"Local Cup {tournament_id}"),
                    "IMAGEFILE": image_key,
                    "IMAGEFILE_SMALL": image_key,
                    "IMAGEFILE_LARGE": image_key,
                    "CARD_ID": resource_id,
                    "CARD_TYPE": 14,
                    "resourceId": resource_id,
                    "id": resource_id,
                }
        with self._lock, closing(self._connect()) as connection, connection:
            persona_id = int(self._identity(connection)["persona_id"])
            self._ensure_beta_cosmetics_locked(connection, persona_id)
            rows = connection.execute(
                "SELECT payload FROM items WHERE persona_id=? AND item_type IN ('kit','stadium','custom')",
                (persona_id,),
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                try:
                    candidate = int(payload.get("resourceId", payload.get("definitionId", payload.get("assetId", 0))))
                except (TypeError, ValueError):
                    continue
                if candidate == resource_id:
                    return dict(payload)
        return None

    def _activate_beta_cosmetic_locked(
        self, connection: sqlite3.Connection, persona_id: int, item_id: int, requested_state: str
    ) -> dict[str, Any]:
        normalized = str(requested_state or "").strip()
        state_aliases = {
            "activehomekit": "activeHomeKit",
            "activeawaykit": "activeAwayKit",
            "activestadium": "activeStadium",
            "activebadge": "activeBadge",
        }
        state = state_aliases.get(normalized.replace("_", "").replace("-", "").lower())
        if state is None:
            raise ValueError(f"unsupported active club item state: {requested_state!r}")

        row = connection.execute(
            "SELECT item_type,payload FROM items WHERE persona_id=? AND item_id=?",
            (int(persona_id), int(item_id)),
        ).fetchone()
        if row is None:
            raise ValueError(f"club item {item_id} not found")
        try:
            selected = json.loads(row["payload"] or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"club item {item_id} has invalid payload") from exc
        if not isinstance(selected, dict):
            raise ValueError(f"club item {item_id} has invalid payload")

        item_type = str(row["item_type"] or selected.get("itemType") or "").lower()
        if state == "activeStadium" and item_type != "stadium":
            raise ValueError("only a stadium item can become activeStadium")
        if state in {"activeHomeKit", "activeAwayKit"} and item_type != "kit":
            raise ValueError("only a kit item can become an active kit")
        if state == "activeBadge" and item_type != "custom":
            raise ValueError("only a badge/custom item can become activeBadge")

        for other in connection.execute(
            "SELECT item_id,payload FROM items WHERE persona_id=? AND item_type IN ('kit','stadium','custom')",
            (int(persona_id),),
        ).fetchall():
            try:
                payload = json.loads(other["payload"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or str(payload.get("itemState") or "") != state:
                continue
            if int(other["item_id"]) == int(item_id):
                continue
            payload["itemState"] = "free"
            connection.execute(
                "UPDATE items SET payload=? WHERE persona_id=? AND item_id=?",
                (json.dumps(payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True), int(persona_id), int(other["item_id"])),
            )

        selected["itemState"] = state
        selected["pile"] = 7
        selected["untradeable"] = True
        selected["tradeable"] = False
        connection.execute(
            "UPDATE items SET pile='club',tradeable=0,payload=? WHERE persona_id=? AND item_id=?",
            (json.dumps(selected, separators=(",", ":"), ensure_ascii=False, sort_keys=True), int(persona_id), int(item_id)),
        )

        resource_id = int(selected.get("resourceId", selected.get("definitionId", selected.get("assetId", 0))) or 0)
        now = int(time.time())
        if state == "activeHomeKit":
            connection.execute(
                "UPDATE beta_club_settings SET home_kit_resource_id=?,updated_at=? WHERE persona_id=?",
                (resource_id, now, int(persona_id)),
            )
        elif state == "activeAwayKit":
            connection.execute(
                "UPDATE beta_club_settings SET away_kit_resource_id=?,updated_at=? WHERE persona_id=?",
                (resource_id, now, int(persona_id)),
            )
        elif state == "activeStadium":
            stadium_name = str(selected.get("name") or DEFAULT_STARTER_STADIUM_NAME)[:96]
            connection.execute(
                "UPDATE beta_club_settings SET stadium_name=?,updated_at=? WHERE persona_id=?",
                (stadium_name, now, int(persona_id)),
            )
        else:
            badge_asset_id = int(selected.get("assetId", 0) or 0)
            connection.execute(
                "UPDATE beta_club_settings SET badge_resource_id=?,updated_at=? WHERE persona_id=?",
                (resource_id, now, int(persona_id)),
            )
            if badge_asset_id > 0:
                connection.execute(
                    "UPDATE clubs SET badge_id=? WHERE persona_id=?",
                    (badge_asset_id, int(persona_id)),
                )

        result = {
            "id": int(item_id), "itemId": int(item_id), "success": True,
            "itemState": state, "resourceId": resource_id,
        }
        # The retail badge activation callback needs enough identity to refresh
        # the club crest immediately.  Kits/stadiums already work with the
        # compact acknowledgement, so keep their proven response untouched.
        if state == "activeBadge":
            badge_asset_id = int(selected.get("assetId", 0) or 0)
            badge_dbid = int(selected.get("badgeDBid", selected.get("carddbid", 0)) or 0)
            result.update({
                "assetId": badge_asset_id,
                "definitionId": resource_id,
                "badge": badge_asset_id,
                "badgeId": badge_asset_id,
                "badgeDBid": badge_dbid,
                "badgeResourceId": resource_id,
                "badgeDefinitionId": resource_id,
                "teamid": badge_asset_id,
                "teamId": badge_asset_id,
            })
        return result

    def move_items(self, updates: list[dict[str, Any]]) -> dict[str, Any]:
        """Support retail club-item activation as well as ordinary pile moves.

        FIFA 14 does *not* send the synthetic state names used by the older
        local tests.  The retail My Club UI sends ``itemState=active`` and
        identifies kit slots with ``activateSlotNumber`` 101 (home) / 102
        (away).  Stadium activation sends only ``itemState=active``.  Resolve
        those wire requests against the owned item's real type before routing
        them through the persisted cosmetic activation path.
        """
        if not isinstance(updates, list):
            raise ValueError("itemData must be a list")

        normalized_updates: list[dict[str, Any]] = []
        with self._lock, closing(self._connect()) as connection, connection:
            persona_id = int(self._identity(connection)["persona_id"])
            self._ensure_beta_cosmetics_locked(connection, persona_id)
            for raw_update in updates:
                if not isinstance(raw_update, dict):
                    continue
                update = dict(raw_update)
                state = update.get("itemState", update.get("state"))
                normalized = str(state or "").replace("_", "").replace("-", "").lower()
                if normalized == "active":
                    raw_id = update.get("id", update.get("itemId"))
                    try:
                        item_id = int(raw_id)
                    except (TypeError, ValueError):
                        item_id = 0
                    row = connection.execute(
                        "SELECT item_type FROM items WHERE persona_id=? AND item_id=?",
                        (persona_id, item_id),
                    ).fetchone() if item_id else None
                    item_type = str(row["item_type"] if row is not None else "").lower()
                    slot = str(update.get("activateSlotNumber", "")).strip()
                    if item_type == "kit" and slot == "101":
                        update["itemState"] = "activeHomeKit"
                    elif item_type == "kit" and slot == "102":
                        update["itemState"] = "activeAwayKit"
                    elif item_type == "stadium":
                        update["itemState"] = "activeStadium"
                    elif item_type == "custom":
                        update["itemState"] = "activeBadge"
                normalized_updates.append(update)

        activation: list[dict[str, Any]] = []
        ordinary: list[dict[str, Any]] = []
        for update in normalized_updates:
            state = update.get("itemState", update.get("state"))
            normalized = str(state or "").replace("_", "").replace("-", "").lower()
            if normalized in {"activehomekit", "activeawaykit", "activestadium", "activebadge"}:
                activation.append(update)
            else:
                ordinary.append(update)

        combined: list[dict[str, Any]] = []
        if ordinary:
            combined.extend(super().move_items(ordinary).get("itemData", []))

        if activation:
            with self._lock, closing(self._connect()) as connection, connection:
                persona_id = int(self._identity(connection)["persona_id"])
                self._ensure_beta_cosmetics_locked(connection, persona_id)
                for update in activation:
                    raw_id = update.get("id", update.get("itemId"))
                    try:
                        item_id = int(raw_id)
                        combined.append(
                            self._activate_beta_cosmetic_locked(
                                connection, persona_id, item_id,
                                str(update.get("itemState", update.get("state")) or ""),
                            )
                        )
                    except (TypeError, ValueError) as error:
                        combined.append({
                            "id": raw_id, "itemId": raw_id, "success": False,
                            "reason": str(error),
                        })
        return {"itemData": combined}

    def view_items(self, item_ids: list[int]) -> dict[str, Any]:
        """Return exact owned ItemData rows requested by the retail ViewCards operation."""
        requested: list[int] = []
        seen: set[int] = set()
        for raw in item_ids:
            try:
                item_id = int(raw)
            except (TypeError, ValueError):
                continue
            if item_id <= 0 or item_id in seen:
                continue
            seen.add(item_id)
            requested.append(item_id)
        if not requested:
            return {"itemData": [], "total": 0, "count": 0}

        with self._lock, closing(self._connect()) as connection, connection:
            persona_id = int(self._identity(connection)["persona_id"])
            self._ensure_beta_cosmetics_locked(connection, persona_id)
            placeholders = ",".join("?" for _ in requested)
            rows = connection.execute(
                f"SELECT item_id,asset_id,item_type,payload FROM items "
                f"WHERE persona_id=? AND item_id IN ({placeholders})",
                (persona_id, *requested),
            ).fetchall()
            by_id: dict[int, dict[str, Any]] = {}
            for row in rows:
                try:
                    payload = json.loads(row["payload"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                item_id = int(row["item_id"])
                item_type = str(row["item_type"] or payload.get("itemType") or "").lower()
                if item_type == "player":
                    payload = self._canonical_player_payload(
                        item_id=item_id,
                        asset_id=int(row["asset_id"]),
                        existing=payload,
                        pile=7,
                    )
                elif item_type in {"kit", "stadium", "custom"}:
                    payload = self._cosmetic_wire_payload(payload)
                else:
                    payload = dict(payload)
                    payload["pile"] = 7
                by_id[item_id] = payload
        documents = [by_id[item_id] for item_id in requested if item_id in by_id]
        return {"itemData": documents, "total": len(documents), "count": len(documents)}

    def club_items(
        self, filters: dict[str, Any] | None = None, *, include_consumables_default: bool = False
    ) -> dict[str, Any]:
        """Expose the complete retail cosmetic catalogue under My Club aliases."""
        with self._lock, closing(self._connect()) as connection, connection:
            persona_id = int(self._identity(connection)["persona_id"])
            self._ensure_beta_cosmetics_locked(connection, persona_id)

        query = dict(filters or {})
        raw_type = query.get("type", "")
        if isinstance(raw_type, list):
            raw_type_value = str(raw_type[0] if raw_type else "")
        else:
            raw_type_value = str(raw_type)
        lowered = raw_type_value.strip().lower()
        aliases = {
            "kit": "kit", "kits": "kit", "homekit": "kit", "awaykit": "kit",
            "stadium": "stadium", "stadia": "stadium", "stadiums": "stadium",
            "badge": "custom", "badges": "custom", "custom": "custom",
            "2": "kit", "3": "kit", "4": "stadium",
        }
        normalized_type = aliases.get(lowered, lowered)
        if normalized_type in {"kit", "stadium", "custom"}:
            query["type"] = normalized_type
        response = super().club_items(
            query, include_consumables_default=include_consumables_default
        )
        if normalized_type in {"kit", "stadium", "custom"} and isinstance(response, dict):
            rows = response.get("itemData")
            if isinstance(rows, list):
                response["itemData"] = [
                    self._cosmetic_wire_payload(row)
                    for row in rows
                    if isinstance(row, dict)
                ]
                response["count"] = len(response["itemData"])
        return response

    def ensure_beta_starter_club(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            fut_user = self._ensure_fut_user_locked(connection)
            existing_club = connection.execute(
                "SELECT * FROM clubs WHERE persona_id=?", (persona_id,)
            ).fetchone()
            existing_beta = connection.execute(
                "SELECT meta_value FROM schema_meta WHERE meta_key='beta_starter_provisioned'"
            ).fetchone()
            if existing_club is not None:
                # BETA 2.22: an existing club is authoritative persistent user data.
                # Older builds used beta_starter_provisioned as a destructive gate;
                # if that marker was missing/corrupt during a build migration, a valid
                # squad could be deleted and silently recreated.  Repair metadata and
                # additive cosmetic state only -- never reset an existing club/squad.
                self._ensure_beta_cosmetics_locked(connection, persona_id)
                active_squad = connection.execute(
                    "SELECT squad_id FROM squads WHERE persona_id=? AND active=1 ORDER BY squad_id LIMIT 1",
                    (persona_id,),
                ).fetchone()
                if active_squad is None:
                    fallback_squad = connection.execute(
                        "SELECT squad_id FROM squads WHERE persona_id=? ORDER BY squad_id LIMIT 1",
                        (persona_id,),
                    ).fetchone()
                    if fallback_squad is not None:
                        connection.execute("UPDATE squads SET active=0 WHERE persona_id=?", (persona_id,))
                        connection.execute(
                            "UPDATE squads SET active=1 WHERE persona_id=? AND squad_id=?",
                            (persona_id, int(fallback_squad["squad_id"])),
                        )
                        connection.execute(
                            "UPDATE fut_users SET active_squad_id=? WHERE persona_id=?",
                            (int(fallback_squad["squad_id"]), persona_id),
                        )
                connection.execute(
                    "UPDATE squads SET chemistry=CASE WHEN chemistry<=0 THEN 54 ELSE chemistry END, "
                    "star_rating=CASE WHEN star_rating<=0 THEN 61 ELSE star_rating END "
                    "WHERE persona_id=? AND active=1",
                    (persona_id,),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO schema_meta (meta_key, meta_value) VALUES ('beta_starter_provisioned','1')"
                )
                connection.execute(
                    "INSERT OR REPLACE INTO schema_meta (meta_key, meta_value) VALUES ('beta222_persistent_club_guard','1')"
                )
                return self.beta_profile_summary(connection=connection)

            # Truly fresh BETA profile only: there is no existing club to preserve.
            connection.execute("DELETE FROM pack_contents")
            connection.execute("DELETE FROM packs WHERE persona_id=?", (persona_id,))
            connection.execute(
                "DELETE FROM squad_players WHERE squad_id IN (SELECT squad_id FROM squads WHERE persona_id=?)",
                (persona_id,),
            )
            connection.execute("DELETE FROM squads WHERE persona_id=?", (persona_id,))
            connection.execute("DELETE FROM items WHERE persona_id=?", (persona_id,))
            connection.execute("DELETE FROM clubs WHERE persona_id=?", (persona_id,))

            self._create_club_locked(
                connection,
                club_name=DEFAULT_STARTER_CLUB_NAME,
                club_abbr=DEFAULT_STARTER_CLUB_ABBR,
                badge_id=DEFAULT_STARTER_BADGE_ID,
                team_id=DEFAULT_STARTER_BADGE_ID,
            )
            # BETA progression starts from zero, regardless of the historical
            # developer/test balance baked into the parent helper.
            connection.execute(
                "UPDATE clubs SET coins=0, fifa_points=0, division_online=10 WHERE persona_id=?",
                (persona_id,),
            )
            cursor = connection.execute(
                "INSERT INTO squads (persona_id, squad_name, formation, active, chemistry, star_rating) VALUES (?, 'Starter XI', 'f442', 1, 54, 61)",
                (persona_id,),
            )
            squad_id = int(cursor.lastrowid)
            for slot_index, player in enumerate(self._starter_players(persona_id)):
                item_id = STARTER_ITEM_BASE + int(persona_id) * 100 + slot_index + 1
                asset_id = int(player["assetId"])
                starter_source = dict(player)
                starter_source.update({
                    "untradeable": True,
                    "contract": 7,
                    "fitness": 99,
                    "discardValue": 0,
                    "owners": 1,
                    "itemState": "free",
                })
                payload = self._canonical_player_payload(
                    item_id=item_id,
                    asset_id=asset_id,
                    existing=starter_source,
                    pile=7,
                    slot_index=slot_index,
                )
                connection.execute(
                    """
                    INSERT INTO items (item_id, persona_id, asset_id, item_type, pile, tradeable, payload)
                    VALUES (?, ?, ?, ?, 'squad', 0, ?)
                    """,
                    (
                        item_id, persona_id, asset_id, PLAYER_ITEM_TYPE,
                        json.dumps(payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True),
                    ),
                )
                item = connection.execute(
                    "SELECT * FROM items WHERE item_id=? AND persona_id=?", (item_id, persona_id)
                ).fetchone()
                self._write_squad_slot_locked(connection, squad_id, slot_index, item)
            connection.execute(
                "UPDATE fut_users SET active_squad_id=?, starter_pack_claimed=1 WHERE persona_id=?",
                (squad_id, persona_id),
            )
            now = int(time.time())
            native_match_assets = _resolved_match_assets()
            home_kit = dict(native_match_assets.get("homeKit") or {})
            away_kit = dict(native_match_assets.get("awayKit") or {})
            connection.execute(
                """
                INSERT OR REPLACE INTO beta_club_settings (
                    persona_id, stadium_name, home_kit_resource_id,
                    away_kit_resource_id, badge_resource_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (persona_id, DEFAULT_STARTER_STADIUM_NAME,
                 int(home_kit.get("resourceId", home_kit.get("assetId", 241)) or 241),
                 int(away_kit.get("resourceId", away_kit.get("assetId", 241)) or 241),
                 DEFAULT_STARTER_BADGE_ID, now),
            )
            self._ensure_beta_cosmetics_locked(connection, persona_id)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta (meta_key, meta_value) VALUES ('beta_starter_provisioned','1')"
            )
            self._counter_add_locked(connection, "registered_clubs", 1)
            self._daily_counter_add_locked(connection, "new_accounts", 1)
            return self.beta_profile_summary(connection=connection)

    def ensure_consumables_beta_test_balance(self, target_coins: int = 100_000_000) -> dict[str, Any]:
        """Grant an established BETA club a one-time consumables test float.

        BETA 2.24 intentionally retained the progression branch's zero-coin start,
        which made the new pack/consumable flow impossible to test for an existing
        club with no match earnings.  2.24.2 tops the current club up to 100,000,000
        coins once, records the grant in the wallet ledger, and then leaves the
        balance entirely under normal pack/match economy control.  The fixed
        reference id makes this idempotent across launches and extracted builds.
        """
        target = max(0, int(target_coins))
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            club = connection.execute(
                "SELECT coins FROM clubs WHERE persona_id=?", (persona_id,)
            ).fetchone()
            if club is None:
                return {"granted": 0, "balanceBefore": 0, "balanceAfter": 0, "idempotent": True}
            before = int(club["coins"])
            existing = connection.execute(
                """
                SELECT transaction_id,amount,balance_before,balance_after
                FROM wallet_transactions
                WHERE persona_id=? AND currency='COINS' AND reason='BETA_CONSUMABLES_TEST_GRANT'
                  AND reference_type='BUILD' AND reference_id='2.41.1-beta2.24.2'
                """,
                (persona_id,),
            ).fetchone()
            if existing is not None:
                current = int(connection.execute(
                    "SELECT coins FROM clubs WHERE persona_id=?", (persona_id,)
                ).fetchone()["coins"])
                return {
                    "transactionId": int(existing["transaction_id"]),
                    "granted": 0,
                    "balanceBefore": current,
                    "balanceAfter": current,
                    "idempotent": True,
                }
            grant = max(0, target - before)
            if grant <= 0:
                return {"granted": 0, "balanceBefore": before, "balanceAfter": before, "idempotent": True}
            tx = self._wallet_write_locked(
                connection,
                amount=grant,
                reason="BETA_CONSUMABLES_TEST_GRANT",
                reference_type="BUILD",
                reference_id="2.41.1-beta2.24.2",
                metadata={"purpose": "consumables-pack-testing", "targetCoins": target},
            )
            return {
                "transactionId": int(tx["transactionId"]),
                "granted": grant,
                "balanceBefore": before,
                "balanceAfter": int(tx["balanceAfter"]),
                "idempotent": False,
            }

    def beta_profile_summary(self, *, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        owns_connection = connection is None
        ctx = closing(self._connect()) if owns_connection else None
        conn = ctx.__enter__() if ctx is not None else connection
        try:
            assert conn is not None
            identity = self._identity(conn)
            persona_id = int(identity["persona_id"])
            club = conn.execute("SELECT coins,fifa_points FROM clubs WHERE persona_id=?", (persona_id,)).fetchone()
            account = conn.execute("SELECT * FROM beta_accounts WHERE persona_id=?", (persona_id,)).fetchone()
            settings = conn.execute("SELECT * FROM beta_club_settings WHERE persona_id=?", (persona_id,)).fetchone()
            owned = int(conn.execute("SELECT COUNT(*) FROM items WHERE persona_id=?", (persona_id,)).fetchone()[0])
            squad = int(conn.execute(
                "SELECT COUNT(*) FROM squad_players sp JOIN squads s ON s.squad_id=sp.squad_id "
                "WHERE s.persona_id=? AND sp.item_id>0", (persona_id,)
            ).fetchone()[0])
            return {
                "betaSchema": BETA_SCHEMA,
                "personaId": persona_id,
                "accountUuid": None if account is None else account["account_uuid"],
                "discordLinked": bool(account and account["discord_user_id"]),
                "authState": "local-unlinked" if account is None else account["auth_state"],
                "coins": 0 if club is None else int(club["coins"]),
                "fifaPoints": 0 if club is None else int(club["fifa_points"]),
                "ownedItems": owned,
                "squadPlayers": squad,
                "stadium": DEFAULT_STARTER_STADIUM_NAME if settings is None else settings["stadium_name"],
            }
        finally:
            if ctx is not None:
                ctx.__exit__(None, None, None)

    def _counter_add_locked(self, connection: sqlite3.Connection, key: str, amount: int = 1) -> None:
        connection.execute(
            """
            INSERT INTO beta_counters(counter_key,counter_value) VALUES (?,?)
            ON CONFLICT(counter_key) DO UPDATE SET counter_value=counter_value+excluded.counter_value
            """, (str(key), int(amount))
        )

    def _daily_counter_add_locked(self, connection: sqlite3.Connection, key: str, amount: int = 1) -> None:
        connection.execute(
            """
            INSERT INTO beta_daily_counters(day,counter_key,counter_value) VALUES (?,?,?)
            ON CONFLICT(day,counter_key) DO UPDATE SET counter_value=counter_value+excluded.counter_value
            """, (_utc_day(), str(key), int(amount))
        )

    def _wallet_write_locked(
        self,
        connection: sqlite3.Connection,
        *,
        amount: int,
        reason: str,
        reference_type: str,
        reference_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        identity = self._identity(connection)
        persona_id = int(identity["persona_id"])
        club = connection.execute("SELECT coins FROM clubs WHERE persona_id=?", (persona_id,)).fetchone()
        if club is None:
            raise ValueError("FUT club does not exist")
        before = int(club["coins"])
        after = before + int(amount)
        if after < 0:
            raise ValueError("not enough FUT coins")
        existing = connection.execute(
            """
            SELECT transaction_id,balance_before,balance_after,amount FROM wallet_transactions
            WHERE persona_id=? AND currency='COINS' AND reason=? AND reference_type=? AND reference_id=?
            """, (persona_id, reason, reference_type, str(reference_id))
        ).fetchone()
        if existing is not None:
            return {
                "transactionId": int(existing["transaction_id"]),
                "amount": int(existing["amount"]),
                "balanceBefore": int(existing["balance_before"]),
                "balanceAfter": int(existing["balance_after"]),
                "idempotent": True,
            }
        connection.execute("UPDATE clubs SET coins=? WHERE persona_id=?", (after, persona_id))
        cursor = connection.execute(
            """
            INSERT INTO wallet_transactions (
                persona_id,created_at,currency,amount,balance_before,balance_after,
                reason,reference_type,reference_id,metadata_json
            ) VALUES (?,?, 'COINS', ?,?,?,?,?,?,?)
            """,
            (
                persona_id, int(time.time()), int(amount), before, after, reason,
                reference_type, str(reference_id),
                json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True),
            ),
        )
        return {
            "transactionId": int(cursor.lastrowid), "amount": int(amount),
            "balanceBefore": before, "balanceAfter": after, "idempotent": False,
        }

    def purchase_pack(self, pack_type: int, *, currency: str = "COINS") -> dict[str, Any]:
        before = self.credits()["credits"]
        response = super().purchase_pack(pack_type, currency=currency)
        after = int(response.get("credits", before))
        if str(currency or "COINS").upper() not in {"FIFA_POINTS", "POINTS", "FIFA POINTS"} and after != before:
            with self._lock, closing(self._connect()) as connection, connection:
                identity = self._identity(connection)
                persona_id = int(identity["persona_id"])
                transaction_id = str(response.get("transactionId") or response.get("packId") or int(time.time()))
                # Parent already changed the balance; ledger mirrors that committed state.
                connection.execute(
                    """
                    INSERT OR IGNORE INTO wallet_transactions (
                        persona_id,created_at,currency,amount,balance_before,balance_after,
                        reason,reference_type,reference_id,metadata_json
                    ) VALUES (?,?, 'COINS', ?,?,?,?,?,?,?)
                    """,
                    (persona_id, int(time.time()), after-before, before, after, "PACK_PURCHASE", "pack", transaction_id,
                     json.dumps({"packType": int(pack_type)}, separators=(",", ":"))),
                )
                self._counter_add_locked(connection, "packs_opened", 1)
                self._daily_counter_add_locked(connection, "packs_opened", 1)
                items = response.get("itemData", []) if isinstance(response, dict) else []
                player_rows = [row for row in items if isinstance(row, dict) and row.get("itemType") == "player"]
                self._counter_add_locked(connection, "players_packed", len(player_rows))
                self._daily_counter_add_locked(connection, "players_packed", len(player_rows))
                if player_rows:
                    highest = max(int(row.get("rating", 0) or 0) for row in player_rows)
                    current = connection.execute(
                        "SELECT counter_value FROM beta_daily_counters WHERE day=? AND counter_key='highest_rated_packed'",
                        (_utc_day(),),
                    ).fetchone()
                    if current is None or highest > int(current[0]):
                        connection.execute(
                            "INSERT OR REPLACE INTO beta_daily_counters(day,counter_key,counter_value) VALUES (?, 'highest_rated_packed', ?)",
                            (_utc_day(), highest),
                        )
        return response

    def quick_sell(self, item_ids: list[int]) -> dict[str, Any]:
        before = int(self.credits()["credits"])
        response = super().quick_sell(item_ids)
        after = int(response.get("credits", before))
        if after != before:
            ref = ",".join(str(int(x)) for x in sorted(set(item_ids)))[:512]
            with self._lock, closing(self._connect()) as connection, connection:
                identity = self._identity(connection)
                persona_id = int(identity["persona_id"])
                connection.execute(
                    """
                    INSERT OR IGNORE INTO wallet_transactions (
                        persona_id,created_at,currency,amount,balance_before,balance_after,
                        reason,reference_type,reference_id,metadata_json
                    ) VALUES (?,?, 'COINS', ?,?,?,?,?,?,?)
                    """,
                    (persona_id, int(time.time()), after-before, before, after, "ITEM_DISCARD", "items", ref,
                     json.dumps({"itemIds": list(map(int, item_ids))}, separators=(",", ":"))),
                )
        return response

    def squad_list(self) -> dict[str, Any]:
        response = super().squad_list()
        # Keep the squad parser on the BETA 2.17-proven actives contract: home
        # kit, away kit and stadium only.  Badge identity is synchronized through
        # the dedicated native BADGE_ID UI bridge in BETA 2.22 instead of being
        # inserted ahead of the players array.
        cosmetics = self.active_cosmetic_items(include_badge=False)
        for squad in response.get("squadList", []):
            if isinstance(squad, dict):
                squad["teamChemistry"] = int(squad.get("chemistry", 0))
                squad["teamRating"] = int(squad.get("starRating", 0))
                if squad.get("active"):
                    squad["actives"] = [dict(item) for item in cosmetics]
        response["squad"] = response.get("squadList", [])
        return response

    def squad_list_compact(self) -> dict[str, Any]:
        response = super().squad_list_compact()
        # The list endpoint is intentionally metadata-only. FIFA requests the
        # selected squad through /squad/active immediately afterwards.
        return response

    def squad_detail(self, requested_id: int | None = None) -> dict[str, Any]:
        squad = super().squad_detail(requested_id)
        if not squad:
            return {}
        # My Club/squad refresh needs all four active club cosmetics. CreateMatch
        # below trims this to the already-proven kit/stadium trio.
        squad["actives"] = self.active_cosmetic_items(include_badge=True)
        squad["teamChemistry"] = int(squad.get("chemistry", 0))
        squad["teamRating"] = int(squad.get("starRating", 0))
        return squad

    def active_squad_document(self) -> dict[str, Any]:
        return self.squad_detail(None)

    def create_match(self, document: dict[str, Any] | None = None) -> dict[str, Any]:
        """Native FutCreateMatchServerResponse: only squad + startDateTime are parsed."""
        document = document if isinstance(document, dict) else {}
        with self._lock, closing(self._connect()) as connection, connection:
            persona_id = int(self._identity(connection)["persona_id"])
            settings = connection.execute(
                "SELECT stadium_name FROM beta_club_settings WHERE persona_id=?", (persona_id,)
            ).fetchone()
            now = int(time.time())
            match_id = str(uuid.uuid4())
            mode = str(document.get("type") or document.get("mode") or "OFFLINE")[:64]
            connection.execute(
                "UPDATE beta_match_sessions SET status='abandoned',completed_at=? "
                "WHERE persona_id=? AND status='active' AND settled=0",
                (now, persona_id),
            )
            connection.execute(
                """INSERT INTO beta_match_sessions (
                    match_id,persona_id,mode,difficulty,stadium_name,status,created_at,started_at,
                    reward_breakdown_json,raw_result_json,settled
                ) VALUES (?,?,?,'unknown',?,'active',?,?, '{}',?,0)""",
                (match_id, persona_id, mode, settings[0] if settings else DEFAULT_STARTER_STADIUM_NAME,
                 now, now, json.dumps(document, separators=(",", ":"), sort_keys=True)),
            )
            self._counter_add_locked(connection, "matches_started", 1)
        squad = self.active_squad_document()
        if isinstance(squad, dict):
            squad["actives"] = self.active_cosmetic_items(include_badge=False)
        return {"squad": squad, "startDateTime": int(time.time())}

    def match_ready(self, document: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist the exact XI FIFA says entered gameplay, then acknowledge MatchReady.

        The retail MatchReady PUT contains the eleven starting item IDs.  BETA 2.20
        discarded those IDs, while /match/end later reported fitness but no contract
        member.  Save the starting XI so the post-match transaction can consume one
        contract from exactly the players that actually started.
        """
        document = document if isinstance(document, dict) else {}
        starter_ids: list[int] = []
        for row in document.get("items", []) if isinstance(document.get("items"), list) else []:
            if not isinstance(row, dict):
                continue
            try:
                item_id = int(row.get("id", row.get("itemId", 0)) or 0)
            except (TypeError, ValueError):
                item_id = 0
            if item_id > 0 and item_id not in starter_ids:
                starter_ids.append(item_id)
        if starter_ids:
            with self._lock, closing(self._connect()) as connection, connection:
                persona_id = int(self._identity(connection)["persona_id"])
                row = connection.execute(
                    "SELECT match_id,raw_result_json FROM beta_match_sessions "
                    "WHERE persona_id=? AND status='active' AND settled=0 "
                    "ORDER BY created_at DESC LIMIT 1",
                    (persona_id,),
                ).fetchone()
                if row is not None:
                    try:
                        saved = json.loads(row["raw_result_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        saved = {}
                    if not isinstance(saved, dict):
                        saved = {}
                    saved["_matchReadyItemIds"] = starter_ids[:11]
                    saved["_matchReadyAt"] = int(time.time())
                    connection.execute(
                        "UPDATE beta_match_sessions SET raw_result_json=? WHERE match_id=? AND persona_id=?",
                        (json.dumps(saved, separators=(",", ":"), sort_keys=True), str(row["match_id"]), persona_id),
                    )
        return {"squad": self.active_squad_document(), "startDateTime": int(time.time())}

    def reset_match(self, document: dict[str, Any] | None = None) -> dict[str, Any]:
        document = document if isinstance(document, dict) else {}
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            # FIFA calls /match/reset during normal FUT bootstrap as well as
            # around match flows. Empty reset requests therefore clear stale
            # state but do NOT manufacture an active match.
            connection.execute(
                "UPDATE beta_match_sessions SET status='abandoned', completed_at=? "
                "WHERE persona_id=? AND status='active' AND settled=0",
                (int(time.time()), persona_id),
            )
            settings = connection.execute(
                "SELECT stadium_name FROM beta_club_settings WHERE persona_id=?", (persona_id,)
            ).fetchone()
            stadium = str(document.get("stadium") or (settings[0] if settings else DEFAULT_STARTER_STADIUM_NAME))[:96]
            if not document:
                return {"status": "reset", "stadium": stadium}
            match_id = str(document.get("matchId") or document.get("id") or uuid.uuid4())
            mode = str(document.get("mode") or document.get("gameMode") or "single-player-local")[:64]
            difficulty = str(document.get("difficulty") or "unknown")[:32]
            now = int(time.time())
            connection.execute(
                """
                INSERT OR REPLACE INTO beta_match_sessions (
                    match_id,persona_id,mode,difficulty,stadium_name,status,
                    created_at,started_at,reward_breakdown_json,raw_result_json,settled
                ) VALUES (?,?,?,?,?,'active',?,?, '{}','{}',0)
                """,
                (match_id, persona_id, mode, difficulty, stadium, now, now),
            )
            self._counter_add_locked(connection, "matches_started", 1)
            return {"matchId": match_id, "status": "active", "stadium": stadium, "mode": mode}

    @staticmethod
    def _walk_values(document: Any, result: dict[str, Any]) -> None:
        if isinstance(document, dict):
            for key, value in document.items():
                lowered = str(key).lower().replace("_", "").replace("-", "")
                result.setdefault(lowered, value)
                BetaIdentityStore._walk_values(value, result)
        elif isinstance(document, list):
            for value in document:
                BetaIdentityStore._walk_values(value, result)

    @staticmethod
    def _ival(flat: dict[str, Any], names: tuple[str, ...], default: int = 0) -> int:
        for name in names:
            key = name.lower().replace("_", "").replace("-", "")
            if key in flat:
                try:
                    return int(float(flat[key]))
                except (TypeError, ValueError):
                    continue
        return int(default)

    @staticmethod
    def _fval(flat: dict[str, Any], names: tuple[str, ...], default: float = 0.0) -> float:
        for name in names:
            key = name.lower().replace("_", "").replace("-", "")
            if key in flat:
                try:
                    return float(flat[key])
                except (TypeError, ValueError):
                    continue
        return float(default)

    def _reward_breakdown(self, document: dict[str, Any], dnf_modifier: float) -> dict[str, Any]:
        flat: dict[str, Any] = {}
        self._walk_values(document, flat)
        minutes = max(0, min(90, self._ival(flat, ("minutesPlayed", "minutes", "matchMinutes"), 90)))
        # Prefer the explicit top-level goalsFor alias produced by DestroyMatch.
        # The raw document also contains per-player ``goals`` members, and the
        # flattening pass intentionally keeps them for generic callers. Picking
        # generic ``goals`` first therefore made an 8-0 win look like the final
        # individual player's 1 goal in the coin calculation.
        goals = max(0, self._ival(flat, ("goalsFor", "goalsScored", "homeGoals", "goals"), 0))
        goals_against = max(0, self._ival(flat, ("goalsAgainst", "goalsConceded", "awayGoals"), 0))
        shots = max(0, self._ival(flat, ("shotsOnTarget", "shotsontarget"), 0))
        tackles = max(0, self._ival(flat, ("successfulTackles", "tacklesWon", "tackles"), 0))
        corners = max(0, self._ival(flat, ("corners", "cornerKicks"), 0))
        pass_accuracy = max(0, min(100, self._ival(flat, ("passAccuracy", "passingAccuracy"), 0)))
        possession = max(0, min(100, self._ival(flat, ("possession", "possessionPercent"), 0)))
        fouls = max(0, self._ival(flat, ("fouls",), 0))
        cards = max(0, self._ival(flat, ("cards", "yellowCards"), 0)) + max(0, self._ival(flat, ("redCards",), 0))
        offsides = max(0, self._ival(flat, ("offsides", "offside"), 0))
        motm = 1 if self._ival(flat, ("manOfTheMatch", "motm"), 0) else 0
        clean_sheet = 1 if goals_against == 0 and minutes >= 45 else 0
        completed = bool(self._ival(flat, ("completed", "matchCompleted", "finished"), 1)) and minutes >= 1
        dnf = bool(self._ival(flat, ("dnf", "didNotFinish", "quit", "abandoned"), 0)) or not completed
        multiplier = self._fval(flat, ("multiplier", "coinMultiplier", "matchMultiplier"), 1.0)
        multiplier = max(0.0, min(5.0, multiplier))

        # Never award a normal completion payment to an abandoned/DNF match.
        # We will tune historical partial-DNF behavior only after the PC client
        # exposes the exact end-of-match payload in a live capture.
        completion = int(round(325 * minutes / 90.0)) if completed and not dnf else 0
        bonus = {
            "goals": min(goals * 40, 200),
            "shotsOnTarget": min(shots * 5, 75),
            "successfulTackles": min(tackles, 20),
            "corners": min(corners * 5, 50),
            "cleanSheet": 75 if clean_sheet else 0,
            "passAccuracy": min(pass_accuracy, 80),
            "possession": min(possession, 80),
            "manOfTheMatch": 15 if motm else 0,
        }
        penalty = {
            "goalsAgainst": -min(goals_against * 20, 80),
            "fouls": -min(fouls, 20),
            "cards": -min(cards * 10, 80),
            "offsides": -min(offsides, 15),
        }
        skill_raw = sum(bonus.values()) + sum(penalty.values())
        applied_dnf = min(1.0, max(0.0, float(dnf_modifier)))
        skill = int(round(skill_raw * applied_dnf * multiplier)) if not dnf else 0
        total = max(0, completion + skill)
        return {
            "minutesPlayed": minutes,
            "completed": bool(completed and not dnf),
            "dnf": dnf,
            "completionAward": completion,
            "bonuses": bonus,
            "penalties": penalty,
            "skillAwardRaw": skill_raw,
            "dnfModifier": round(float(dnf_modifier), 4),
            "dnfModifierApplied": round(applied_dnf, 4),
            "multiplier": multiplier,
            "skillAward": skill,
            "totalCoins": total,
            "goalsFor": goals,
            "goalsAgainst": goals_against,
        }

    def settle_match(self, document: dict[str, Any] | None = None) -> dict[str, Any]:
        document = document if isinstance(document, dict) else {}
        # FIFA has historically touched /match during bootstrap as well as after
        # gameplay. An empty/generic acknowledgement must never mint coins. Only
        # settle when the request contains at least one concrete result/stat key.
        flat_probe: dict[str, Any] = {}
        self._walk_values(document, flat_probe)
        settlement_markers = {
            "minutesplayed", "minutes", "matchminutes", "goals", "goalsscored",
            "goalsfor", "homegoals", "goalsagainst", "goalsconceded", "awaygoals",
            "result", "completed", "matchcompleted", "finished", "dnf",
            "didnotfinish", "quit", "abandoned", "shotsontarget", "passaccuracy",
            "possession", "successfulTackles".lower(),
        }
        if not any(key in flat_probe for key in settlement_markers):
            return {
                "status": "awaiting-result",
                "settled": False,
                "rewardCoins": 0,
                "credits": int(self.credits()["credits"]),
            }
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            account = connection.execute(
                "SELECT dnf_modifier FROM beta_accounts WHERE persona_id=?", (persona_id,)
            ).fetchone()
            # Keep the classic DNF multiplier meaningful, but never let repeated
            # local-beta forfeits/test quits collapse it to zero. FIFA's match
            # summary still expects a non-zero skill multiplier after a completed
            # match. Existing profiles created by earlier betas are repaired on
            # the fly rather than requiring a club reset.
            dnf_modifier = 1.25 if account is None else max(0.25, float(account["dnf_modifier"]))
            requested_id = str(document.get("matchId") or document.get("id") or "")
            row = None
            if requested_id:
                row = connection.execute(
                    "SELECT * FROM beta_match_sessions WHERE persona_id=? AND match_id=?",
                    (persona_id, requested_id),
                ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM beta_match_sessions WHERE persona_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
                    (persona_id,),
                ).fetchone()
            if row is None:
                # ProtoHttp may retry DestroyMatch after a slow frontend transition.
                # FIFA's request carries a stable matchData token even when it omits
                # matchId, so match it against recent settled rows before creating a
                # new fallback session. This keeps W-D-L and contracts idempotent.
                match_data = str(document.get("matchData") or "")
                if match_data:
                    for candidate in connection.execute(
                        "SELECT * FROM beta_match_sessions WHERE persona_id=? AND settled=1 "
                        "ORDER BY completed_at DESC LIMIT 16",
                        (persona_id,),
                    ).fetchall():
                        try:
                            previous = json.loads(candidate["raw_result_json"] or "{}")
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if isinstance(previous, dict) and str(previous.get("matchData") or "") == match_data:
                            row = candidate
                            break
            if row is None:
                match_id = requested_id or str(uuid.uuid4())
                now = int(time.time())
                connection.execute(
                    """
                    INSERT INTO beta_match_sessions (
                        match_id,persona_id,mode,difficulty,stadium_name,status,created_at,started_at,
                        reward_breakdown_json,raw_result_json,settled
                    ) VALUES (?,?,'single-player-local','unknown',?,'active',?,?, '{}','{}',0)
                    """,
                    (match_id, persona_id, DEFAULT_STARTER_STADIUM_NAME, now, now),
                )
                row = connection.execute("SELECT * FROM beta_match_sessions WHERE match_id=?", (match_id,)).fetchone()
            assert row is not None
            match_id = str(row["match_id"])
            if int(row["settled"]):
                breakdown = json.loads(row["reward_breakdown_json"] or "{}")
                return {
                    "matchId": match_id, "status": row["status"], "settled": True,
                    "rewardCoins": int(row["reward_coins"]), "reward": breakdown,
                    "credits": int(self.credits()["credits"]), "idempotent": True,
                }

            breakdown = self._reward_breakdown(document, dnf_modifier)
            completed = bool(breakdown["completed"])
            dnf = bool(breakdown["dnf"])
            if dnf:
                new_dnf = max(0.0, dnf_modifier - 0.25)
            elif completed:
                new_dnf = min(1.25, dnf_modifier + 0.02)
            else:
                new_dnf = dnf_modifier
            result = str(document.get("result") or "").upper()
            if dnf:
                result = "DNF"
            elif not result:
                gf, ga = int(breakdown["goalsFor"]), int(breakdown["goalsAgainst"])
                result = "WIN" if gf > ga else "LOSS" if gf < ga else "DRAW"
            reward = int(breakdown["totalCoins"])
            status = "dnf" if dnf else "complete"
            if reward > 0:
                wallet = self._wallet_write_locked(
                    connection, amount=reward, reason="MATCH_REWARD",
                    reference_type="match", reference_id=match_id,
                    metadata={"result": result, "reward": breakdown},
                )
            else:
                current_credits = int(connection.execute(
                    "SELECT coins FROM clubs WHERE persona_id=?", (persona_id,)
                ).fetchone()[0])
                wallet = {"transactionId": 0, "balanceAfter": current_credits}
            now = int(time.time())
            connection.execute(
                """
                UPDATE beta_match_sessions SET status=?, completed_at=?, result=?,
                    home_goals=?, away_goals=?, minutes_played=?, reward_coins=?,
                    reward_breakdown_json=?, raw_result_json=?, settled=1
                WHERE match_id=? AND persona_id=?
                """,
                (
                    status, now, result, int(breakdown["goalsFor"]), int(breakdown["goalsAgainst"]),
                    int(breakdown["minutesPlayed"]), reward,
                    json.dumps(breakdown, separators=(",", ":"), sort_keys=True),
                    json.dumps(document, separators=(",", ":"), sort_keys=True),
                    match_id, persona_id,
                ),
            )
            connection.execute(
                "UPDATE beta_accounts SET dnf_modifier=?, last_seen=? WHERE persona_id=?",
                (new_dnf, now, persona_id),
            )
            if dnf:
                self._counter_add_locked(connection, "matches_dnf", 1)
                self._daily_counter_add_locked(connection, "matches_dnf", 1)
            else:
                self._counter_add_locked(connection, "matches_completed", 1)
                self._daily_counter_add_locked(connection, "matches_completed", 1)
            if reward > 0:
                self._counter_add_locked(connection, "match_coins_awarded", reward)
                self._daily_counter_add_locked(connection, "match_coins_awarded", reward)
            return {
                "matchId": match_id,
                "status": status,
                "settled": True,
                "result": result,
                "rewardCoins": reward,
                "reward": breakdown,
                "credits": int(wallet["balanceAfter"]),
                "walletTransactionId": int(wallet["transactionId"]),
                "dnfModifier": round(new_dnf, 4),
                "idempotent": False,
            }


    def match_record(self) -> dict[str, int]:
        """Return the local FUT W-D-L record from idempotently settled matches."""
        with self._lock, closing(self._connect()) as connection:
            persona_id = int(self._identity(connection)["persona_id"])
            rows = connection.execute(
                "SELECT result,status FROM beta_match_sessions WHERE persona_id=? AND settled=1",
                (persona_id,),
            ).fetchall()
        wins = draws = losses = 0
        for row in rows:
            result = str(row["result"] or "").upper()
            status = str(row["status"] or "").lower()
            if result == "WIN":
                wins += 1
            elif result == "DRAW":
                draws += 1
            elif result in {"LOSS", "DNF"} or status == "dnf":
                losses += 1
        return {"wins": wins, "draws": draws, "losses": losses}

    def ensure_fut_user(self) -> dict[str, Any]:
        """Publish the persisted local W-D-L alongside the normal user contract."""
        response = super().ensure_fut_user()
        record = self.match_record()
        # The retail binary contains the exact num_wins/num_draws/num_losses
        # tokens as well as the front-end WINS/DRAWS/LOSSES labels. Supplying the
        # snake_case counters is harmless to parsers that ignore unknown fields
        # and gives the original HUD a native value source after local settlement.
        balances = self.currencies()
        response.update({
            "num_wins": int(record["wins"]),
            "num_draws": int(record["draws"]),
            "num_losses": int(record["losses"]),
            "wins": int(record["wins"]),
            "draws": int(record["draws"]),
            "losses": int(record["losses"]),
            # Completed-match settlement changes the wallet before FIFA refreshes
            # /user. Publish the same native balance scalars here so the HUD and
            # post-match controller cannot keep the pre-match coin cache.
            "credits": int(balances.get("credits", 0) or 0),
            "coins": int(balances.get("credits", 0) or 0),
            "fifaPoints": int(balances.get("fifaPoints", 0) or 0),
        })
        return response

    def _apply_match_end_items_locked(
        self, connection: sqlite3.Connection, persona_id: int, items: Any,
        *, starting_xi_ids: list[int] | None = None, decrement_contracts: bool = False,
    ) -> list[dict[str, Any]]:
        """Apply FIFA 14's post-match player-card transaction and return ItemData.

        The BETA 2.20 trace showed /match/end reports fitness for the match group but
        no contract member. MatchReady supplies the XI separately. On the first
        settlement only, consume one contract from those starters and return complete
        canonical ItemData so CardsDLL can reconcile its local card state.
        """
        updated: list[dict[str, Any]] = []
        if not isinstance(items, list):
            return updated
        starters = {int(x) for x in (starting_xi_ids or []) if int(x) > 0}
        for submitted in items:
            if not isinstance(submitted, dict):
                continue
            raw_id = submitted.get("id", submitted.get("itemId"))
            try:
                item_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            row = connection.execute(
                "SELECT payload,item_type,asset_id FROM items WHERE persona_id=? AND item_id=?",
                (int(persona_id), item_id),
            ).fetchone()
            if row is None or str(row["item_type"]).lower() != PLAYER_ITEM_TYPE:
                continue
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if "fitness" in submitted:
                try:
                    payload["fitness"] = max(0, min(99, int(submitted["fitness"])))
                except (TypeError, ValueError):
                    pass

            contract_before = max(0, int(payload.get("contract", payload.get("contracts", 0)) or 0))
            contract_after = contract_before
            if decrement_contracts and item_id in starters:
                contract_after = max(0, contract_before - 1)
                payload["contract"] = contract_after
                if "contracts" in payload:
                    payload["contracts"] = contract_after

            match_goals = 0
            match_assists = 0
            try:
                match_goals = max(0, int(submitted.get("goals", 0) or 0))
            except (TypeError, ValueError):
                pass
            try:
                match_assists = max(0, int(submitted.get("assists", 0) or 0))
            except (TypeError, ValueError):
                pass
            if match_goals:
                payload["lifetimeGoals"] = max(0, int(payload.get("lifetimeGoals", 0) or 0)) + match_goals
            if match_assists:
                payload["lifetimeAssists"] = max(0, int(payload.get("lifetimeAssists", 0) or 0)) + match_assists
                payload["assists"] = max(0, int(payload.get("assists", 0) or 0)) + match_assists

            # FIFA 14 player/GK training is a one-match effect.  Only expire it
            # during the first settlement and only for cards the game actually
            # submitted in /match/end; unused bench/reserve cards keep the boost.
            if decrement_contracts:
                training_effect = connection.execute(
                    "SELECT base_payload_json FROM consumable_effects "
                    "WHERE persona_id=? AND item_id=? AND effect_type='training'",
                    (int(persona_id), item_id),
                ).fetchone()
                if training_effect is not None:
                    try:
                        base_payload = json.loads(training_effect["base_payload_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        base_payload = {}
                    if isinstance(base_payload, dict):
                        base_attrs = self._array_values(
                            base_payload.get("attributeArray", base_payload.get("attributeList", [])), 6
                        )
                        payload["attributeArray"] = base_attrs
                        payload["attributeList"] = [
                            {"index": index, "value": value} for index, value in enumerate(base_attrs)
                        ]
                    payload["training"] = 0
                    connection.execute(
                        "DELETE FROM consumable_effects WHERE persona_id=? AND item_id=? AND effect_type='training'",
                        (int(persona_id), item_id),
                    )

            canonical = self._canonical_player_payload(
                item_id=item_id,
                asset_id=int(row["asset_id"]),
                existing=payload,
                pile=7,
            )
            canonical["contract"] = contract_after
            if "contracts" in canonical:
                canonical["contracts"] = contract_after
            connection.execute(
                "UPDATE items SET payload=? WHERE persona_id=? AND item_id=?",
                (json.dumps(canonical, separators=(",", ":"), ensure_ascii=False, sort_keys=True), int(persona_id), item_id),
            )
            updated.append(canonical)
        return updated

    def _active_match_context(self) -> tuple[int | None, list[int]]:
        """Return tournament identity and the MatchReady starting XI for the active match."""
        with self._lock, closing(self._connect()) as connection:
            persona_id = int(self._identity(connection)["persona_id"])
            row = connection.execute(
                "SELECT raw_result_json FROM beta_match_sessions "
                "WHERE persona_id=? AND status='active' AND settled=0 "
                "ORDER BY created_at DESC LIMIT 1",
                (persona_id,),
            ).fetchone()
        if row is None:
            return None, []
        try:
            payload = json.loads(row["raw_result_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return None, []
        if not isinstance(payload, dict):
            return None, []
        try:
            tournament_id = int(payload.get("tournamentId", 0) or 0)
        except (TypeError, ValueError):
            tournament_id = 0
        valid_ids = {int(row["tournamentId"]) for row in OFFLINE_TOURNAMENTS}
        if tournament_id not in valid_ids:
            tournament_id = 0
        starters: list[int] = []
        raw_starters = payload.get("_matchReadyItemIds", [])
        if isinstance(raw_starters, list):
            for raw_id in raw_starters:
                try:
                    item_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if item_id > 0 and item_id not in starters:
                    starters.append(item_id)
        return (tournament_id if tournament_id else None), starters[:11]


    @staticmethod
    def _offline_tournament_definition(tournament_id: int) -> dict[str, Any] | None:
        wanted = int(tournament_id)
        for definition in OFFLINE_TOURNAMENTS:
            if int(definition.get("tournamentId", 0) or 0) == wanted:
                return definition
        return None

    def _tournament_round_locked(
        self, connection: sqlite3.Connection, persona_id: int, tournament_id: int
    ) -> int:
        row = connection.execute(
            "SELECT round_value FROM beta_tournament_progress WHERE persona_id=? AND tournament_id=?",
            (int(persona_id), int(tournament_id)),
        ).fetchone()
        if row is None:
            return 1
        return max(1, int(row["round_value"] or 1))

    def _settle_tournament_result_locked(
        self,
        connection: sqlite3.Connection,
        persona_id: int,
        tournament_id: int,
        end_reason: str,
        *,
        match_id: str,
    ) -> dict[str, Any]:
        """Persist knockout progression independently of FIFA's opaque bracket blob.

        The retail client writes a first-round tournamentData blob whose progressData
        is only four zero bytes. We intentionally do not persist that unsafe blob,
        so the server must advance the canonical round counter when a settled FUT
        match ends. round_value is the parser-native 1-based wire round.
        """
        definition = self._offline_tournament_definition(tournament_id)
        if definition is None:
            return {"tournamentId": int(tournament_id), "round": 1, "advanced": False}
        round_defs = definition.get("rounds") if isinstance(definition.get("rounds"), list) else []
        round_count = max(1, len(round_defs))
        current_round = min(round_count, self._tournament_round_locked(connection, persona_id, tournament_id))
        now = int(time.time())
        result = str(end_reason or "").upper()
        prize = 0

        if result == "WIN":
            if current_round < round_count:
                next_round = current_round + 1
                connection.execute(
                    "UPDATE beta_offline_tournaments SET current_round=?,won=0,active=1,updated_at=? "
                    "WHERE persona_id=? AND tournament_id=?",
                    (next_round - 1, now, int(persona_id), int(tournament_id)),
                )
                connection.execute(
                    """INSERT INTO beta_tournament_progress (
                        persona_id,tournament_id,round_value,data_version,tournament_data,
                        progress_data_version,progress_data,updated_at
                    ) VALUES (?,?,?,1,'',1,'',?)
                    ON CONFLICT(persona_id,tournament_id) DO UPDATE SET
                        round_value=excluded.round_value,data_version=1,tournament_data='',
                        progress_data_version=1,progress_data='',updated_at=excluded.updated_at""",
                    (int(persona_id), int(tournament_id), next_round, now),
                )
                return {
                    "tournamentId": int(tournament_id), "round": next_round,
                    "previousRound": current_round, "advanced": True, "won": False,
                }

            # Final-round WIN: mark the cup won, make the next visit replayable,
            # and award the advertised tournament prize exactly once.
            prior = connection.execute(
                "SELECT won FROM beta_offline_tournaments WHERE persona_id=? AND tournament_id=?",
                (int(persona_id), int(tournament_id)),
            ).fetchone()
            repeat = bool(prior is not None and int(prior["won"] or 0))
            prize = max(0, int(definition.get("repeatPrize" if repeat else "prize", 0) or 0))
            connection.execute(
                "UPDATE beta_offline_tournaments SET current_round=0,won=1,active=1,updated_at=? "
                "WHERE persona_id=? AND tournament_id=?",
                (now, int(persona_id), int(tournament_id)),
            )
            connection.execute(
                """INSERT INTO beta_tournament_progress (
                    persona_id,tournament_id,round_value,data_version,tournament_data,
                    progress_data_version,progress_data,updated_at
                ) VALUES (?,?,1,1,'',1,'',?)
                ON CONFLICT(persona_id,tournament_id) DO UPDATE SET
                    round_value=1,data_version=1,tournament_data='',progress_data_version=1,
                    progress_data='',updated_at=excluded.updated_at""",
                (int(persona_id), int(tournament_id), now),
            )
            if prize > 0:
                self._wallet_write_locked(
                    connection,
                    amount=prize,
                    reason="TOURNAMENT_PRIZE",
                    reference_type="tournament_match",
                    reference_id=str(match_id),
                    metadata={"tournamentId": int(tournament_id), "round": current_round, "won": True},
                )
            return {
                "tournamentId": int(tournament_id), "round": 1,
                "previousRound": current_round, "advanced": True, "won": True,
                "tournamentPrize": prize,
            }

        if result in {"LOSS", "DNF", "QUIT"}:
            connection.execute(
                "UPDATE beta_offline_tournaments SET current_round=0,won=0,active=1,updated_at=? "
                "WHERE persona_id=? AND tournament_id=?",
                (now, int(persona_id), int(tournament_id)),
            )
            connection.execute(
                """INSERT INTO beta_tournament_progress (
                    persona_id,tournament_id,round_value,data_version,tournament_data,
                    progress_data_version,progress_data,updated_at
                ) VALUES (?,?,1,1,'',1,'',?)
                ON CONFLICT(persona_id,tournament_id) DO UPDATE SET
                    round_value=1,data_version=1,tournament_data='',progress_data_version=1,
                    progress_data='',updated_at=excluded.updated_at""",
                (int(persona_id), int(tournament_id), now),
            )
            return {
                "tournamentId": int(tournament_id), "round": 1,
                "previousRound": current_round, "advanced": False, "won": False,
                "eliminated": True,
            }

        # Draw/no-contest: replay the same knockout round.
        return {
            "tournamentId": int(tournament_id), "round": current_round,
            "previousRound": current_round, "advanced": False, "won": False,
        }

    def _active_match_tournament_id(self) -> int | None:
        return self._active_match_context()[0]

    def settle_match_end(self, document: dict[str, Any] | None = None) -> dict[str, Any]:
        """Settle the match and mirror the retail post-match card transaction.

        QUIT/DNF omits the two match-stat objects, but the returned ``items`` array
        still needs the card updates. BETA 2.22 also consumes exactly one contract
        for each MatchReady starter on the first settlement only.
        """
        document = document if isinstance(document, dict) else {}
        end_reason = str(document.get("endReason") or "NO_CONTEST").strip().upper()
        if end_reason == "FORFEIT":
            end_reason = "QUIT"
        if end_reason not in {"WIN", "DRAW", "LOSS", "DNF", "QUIT", "NO_CONTEST"}:
            end_reason = "NO_CONTEST"
        normalized = dict(document)
        if end_reason in {"QUIT", "DNF"}:
            normalized.update({"quit": 1, "dnf": 1, "completed": 0, "result": "DNF"})
        elif end_reason in {"WIN", "DRAW", "LOSS"}:
            # The PC client does not include minutesPlayed/secondsPlayed in the
            # observed completed-match DestroyMatch payload. Treat a genuine
            # terminal result as a completed 90-minute FUT match so both the
            # economy and the retail match-award screen receive sane duration.
            normalized.setdefault("completed", 1)
            normalized.setdefault("minutesPlayed", 90)
            normalized.setdefault("result", end_reason)

        my_match_stats = document.get("myMatchStats") if isinstance(document.get("myMatchStats"), dict) else {}
        opponent_match_stats = document.get("opponentMatchStats") if isinstance(document.get("opponentMatchStats"), dict) else {}
        if my_match_stats:
            # Normalize the exact FIFA 14 wire names into the reward calculator's
            # aliases without mutating the original request/response stat block.
            normalized.setdefault("goalsFor", my_match_stats.get("goals", 0))
            normalized.setdefault("shotsOnTarget", my_match_stats.get("shotsOnTarget", 0))
            normalized.setdefault("successfulTackles", my_match_stats.get("successfulTackles", 0))
            normalized.setdefault("corners", my_match_stats.get("corners", 0))
            normalized.setdefault("passAccuracy", my_match_stats.get("passingPercentage", 0))
            normalized.setdefault("possession", my_match_stats.get("possessionPercentage", 0))
            normalized.setdefault("manOfTheMatch", my_match_stats.get("manOfTheMatch", 0))
            normalized.setdefault("fouls", my_match_stats.get("fouls", 0))
            normalized.setdefault("yellowCards", my_match_stats.get("yellowCards", 0))
            normalized.setdefault("redCards", my_match_stats.get("redCards", 0))
            normalized.setdefault("offsides", my_match_stats.get("offsides", 0))
        if opponent_match_stats:
            normalized.setdefault("goalsAgainst", opponent_match_stats.get("goals", 0))

        submitted_items = document.get("items")
        if isinstance(submitted_items, list):
            normalized.setdefault(
                "goalsFor",
                sum(
                    max(0, int(row.get("goals", 0) or 0))
                    for row in submitted_items if isinstance(row, dict)
                    and str(row.get("goals", 0)).lstrip("-").isdigit()
                ),
            )

        tournament_id, starting_xi_ids = self._active_match_context()
        settlement = self.settle_match(normalized)
        first_settlement = not bool(settlement.get("idempotent", False))
        with self._lock, closing(self._connect()) as connection, connection:
            persona_id = int(self._identity(connection)["persona_id"])
            # Retries receive current canonical ItemData too, but only the first
            # settlement consumes a contract and advances/reset competition state.
            updated_items = self._apply_match_end_items_locked(
                connection, persona_id, submitted_items,
                starting_xi_ids=starting_xi_ids,
                decrement_contracts=first_settlement,
            )
            tournament_state: dict[str, Any] = {}
            if first_settlement and tournament_id is not None and end_reason in {"WIN", "DRAW", "LOSS", "QUIT", "DNF"}:
                tournament_state = self._settle_tournament_result_locked(
                    connection,
                    persona_id,
                    int(tournament_id),
                    end_reason,
                    match_id=str(settlement.get("matchId") or ""),
                )

        # FutDestroyMatchServerResponse.items is NOT ItemData. The retail
        # serializer expects per-player match-stat rows. BETA 2.22 returned full
        # card objects here, which parsed far enough to save the backend result
        # but left the frontend waiting until FUT was unloaded. Preserve the
        # server-side contract mutation above, but echo only parser-native stats.
        allowed_item_keys = (
            "id", "shots", "goals", "yellowCards", "redCards", "suspension",
            "injuryType", "injuryGames", "fitness", "assists",
        )
        match_stat_items: list[dict[str, Any]] = []
        if isinstance(submitted_items, list):
            for raw in submitted_items:
                if not isinstance(raw, dict):
                    continue
                try:
                    item_id = int(raw.get("id", raw.get("itemId", 0)) or 0)
                except (TypeError, ValueError):
                    item_id = 0
                if item_id <= 0:
                    continue
                row: dict[str, Any] = {"id": item_id}
                for key in allowed_item_keys[1:]:
                    if key not in raw:
                        continue
                    if key == "injuryType":
                        row[key] = str(raw.get(key) or "none")
                    else:
                        try:
                            row[key] = int(raw.get(key, 0) or 0)
                        except (TypeError, ValueError):
                            row[key] = 0
                match_stat_items.append(row)

        seconds_played = max(0, int(document.get("secondsPlayed", 0) or 0))
        if seconds_played <= 0 and end_reason in {"WIN", "DRAW", "LOSS"}:
            seconds_played = max(60, int(settlement.get("reward", {}).get("minutesPlayed", 90) or 90) * 60)

        match_difficulty = max(0, int(document.get("matchDifficulty", 0) or 0))
        if match_difficulty <= 0 and tournament_id is not None:
            definition = self._offline_tournament_definition(int(tournament_id))
            round_defs = definition.get("rounds", []) if isinstance(definition, dict) else []
            # tournament_state is the post-settlement state, so previousRound is
            # the round just played. If it is absent, fall back to round 1.
            played_round = max(1, int(tournament_state.get("previousRound", 1) or 1))
            if isinstance(round_defs, list) and round_defs:
                try:
                    difficulty, _round_coins = round_defs[min(len(round_defs), played_round) - 1]
                    match_difficulty = max(0, int(difficulty))
                except (TypeError, ValueError):
                    pass

        reward_breakdown = settlement.get("reward") if isinstance(settlement.get("reward"), dict) else {}
        completion_award = max(0, int(reward_breakdown.get("completionAward", 0) or 0))
        skill_award = max(0, int(reward_breakdown.get("skillAward", 0) or 0))
        match_reward = max(0, int(settlement.get("rewardCoins", reward_breakdown.get("totalCoins", 0)) or 0))
        tournament_prize = max(0, int(tournament_state.get("tournamentPrize", 0) or 0))
        current_credits = int(self.credits().get("credits", settlement.get("credits", 0)) or 0)
        response: dict[str, Any] = {
            "endReason": end_reason,
            "secondsPlayed": seconds_played,
            "matchDifficulty": match_difficulty,
            "items": match_stat_items,
            "matchData": str(document.get("matchData") or ""),
        }
        if end_reason in {"WIN", "DRAW", "LOSS"}:
            # The match award view was receiving correct stats but difficulty=0
            # and no settlement scalars, so it rendered all zeroes while the DB
            # wallet had already been credited. Publish the committed settlement
            # values alongside the native DestroyMatch members.
            response.update({
                "completionAward": completion_award,
                "skillAward": skill_award,
                "rewardCoins": match_reward,
                "totalCoins": match_reward + tournament_prize,
                "credits": current_credits,
                "coins": current_credits,
                "dnfModifier": float(settlement.get("dnfModifier", reward_breakdown.get("dnfModifierApplied", 1.0)) or 0.0),
            })
            if tournament_prize > 0:
                response["tournamentPrize"] = tournament_prize
            if tournament_state:
                response["tournamentId"] = int(tournament_state.get("tournamentId", tournament_id or 0) or 0)
                response["tournamentRound"] = int(tournament_state.get("round", 1) or 1)
        if end_reason not in {"QUIT", "DNF"}:
            # BETA 2.25.5 zeroed these two objects. That made the retail result
            # screen render 0 Completion Award / 0 Skill Awards even though the
            # backend had already credited the wallet, and the inconsistent
            # 0-second WIN path later fell through the stale logout route.
            # Echo the parser-native stats the game itself submitted instead.
            empty_stats = {
                "goals": 0, "shotsOnTarget": 0, "successfulTackles": 0,
                "corners": 0, "cleansheets": 0, "passingPercentage": 0,
                "possessionPercentage": 0, "manOfTheMatch": 0, "fouls": 0,
                "yellowCards": 0, "redCards": 0, "offsides": 0,
            }
            allowed_stats = tuple(empty_stats.keys())
            response["myMatchStats"] = {
                key: int(my_match_stats.get(key, empty_stats[key]) or 0)
                for key in allowed_stats
            }
            response["opponentMatchStats"] = {
                key: int(opponent_match_stats.get(key, empty_stats[key]) or 0)
                for key in allowed_stats
            }
        return response

    def offline_seasons_list(self) -> dict[str, Any]:
        """Return the exact CardsDLL offline-season list schema recovered in BETA 2.6."""
        seasons = [
            _native_season_record(index, division, matches, promote, coins)
            for index, (division, _name, matches, promote, coins) in enumerate(OFFLINE_SEASON_DIVISIONS, start=1)
        ]
        return {"seasons": seasons}

    def offline_season_user(self) -> dict[str, Any]:
        """Minimal parser-native current-season state.

        The retail season/user parser handles seasonId, divisionId and round.
        `seasonId` is decremented by the client, so ID 1 selects the first
        season-list record (Division 10). Unknown guessed progression members
        are intentionally omitted.
        """
        with self._lock, closing(self._connect()) as connection:
            persona_id = int(self._identity(connection)["persona_id"])
            row = connection.execute(
                "SELECT * FROM beta_offline_seasons WHERE persona_id=? AND division=10 AND active=1 ORDER BY season_id LIMIT 1",
                (persona_id,),
            ).fetchone()
            # CardsDLL decrements the wire `round` value before storing it.
            # Sending 0 therefore becomes 0xFFFF (its invalid/default sentinel).
            # Wire round 1 represents the first scheduled match (internal 0).
            round_index = 0 if row is None else max(0, int(row["matches_played"]))
        return {"seasonId": 1, "divisionId": 10, "round": round_index + 1}

    def tournament_wire_mode(self) -> str:
        raw = os.environ.get("FIFA14_TOURNAMENT_MODE", "native").strip().lower()
        if raw in {"", "native", "single", "full", "starter"}:
            return "native"
        if raw in {"safe", "empty", "off", "disabled"}:
            return "empty"
        return "native"

    def _starter_tournament_wire_entry(self) -> dict[str, Any]:
        return _native_starter_tournament()

    def offline_tournaments_list(self) -> dict[str, Any]:
        if self.tournament_wire_mode() == "empty":
            return {"tournament": []}
        # The retail list parser accepts an array of independent tournament
        # records. Keep the wire schema identical to the now-proven Starter Cup
        # and vary only IDs, round difficulty/coin awards and the final prize.
        # Names are supplied by the exact-build frontend fallback hook because
        # the list parser does not consume a `name` member.
        return {"tournament": [_native_tournament_record(row) for row in OFFLINE_TOURNAMENTS]}

    def offline_tournament_teams(self, count: int = 15) -> dict[str, Any]:
        """Return the exact FutGetTournamentTeams response shape.

        The retail response parser special-cases only `teamId` and requires it
        to be an array.  Returning the tournament catalogue here caused the
        BETA 2.6 deep-selection crash immediately after this request.
        """
        safe_count = max(0, min(int(count), len(OFFLINE_COMPETITION_TEAM_IDS)))
        return {"teamId": [int(team_id) for team_id in OFFLINE_COMPETITION_TEAM_IDS[:safe_count]]}

    @staticmethod
    def _tournament_progress_is_resumable(round_value: int, tournament_data: str, progress_data: str) -> bool:
        """Reject the first-round pre-match blob that made BETA 2.18 say Underway.

        The retail client writes tournamentData before the first match but its
        progressData is four zero bytes (``AAAAAA==``). That state is not a
        playable saved bracket and crashes when reopened. Only later rounds or
        non-zero opaque progress bytes are advertised as resumable.

        A saved round is also worthless without the bracket itself. When the
        server advances the round after a won match it deliberately does not
        invent a tournamentData blob, so an empty blob means there is nothing
        for the client to reopen: advertising it anyway makes the client parse
        an empty buffer as a bracket, which is the crash reported when
        continuing a tournament after leaving FUT.
        """
        if not str(tournament_data or "").strip():
            return False
        if int(round_value) > 1:
            return True
        raw = str(progress_data or "").strip()
        if not raw:
            return False
        try:
            decoded = base64.b64decode(raw, validate=False)
        except Exception:
            decoded = b""
        return bool(decoded and any(byte != 0 for byte in decoded))

    def offline_tournament_user_list(self) -> dict[str, Any]:
        if self.tournament_wire_mode() == "empty":
            return {"tournamentId": []}
        with self._lock, closing(self._connect()) as connection:
            persona_id = int(self._identity(connection)["persona_id"])
            rows = connection.execute(
                """SELECT tournament_id,round_value,tournament_data,progress_data
                   FROM beta_tournament_progress WHERE persona_id=? ORDER BY tournament_id""",
                (persona_id,),
            ).fetchall()
        valid_ids = {int(row["tournamentId"]) for row in OFFLINE_TOURNAMENTS}
        ids = []
        for row in rows:
            tournament_id = int(row["tournament_id"])
            if tournament_id not in valid_ids:
                continue
            if self._tournament_progress_is_resumable(
                int(row["round_value"]), str(row["tournament_data"] or ""), str(row["progress_data"] or "")
            ):
                ids.append(tournament_id)
        return {"tournamentId": ids}

    def update_offline_tournament_user(self, tournament_id: int, document: dict[str, Any] | None = None) -> dict[str, Any]:
        document = document if isinstance(document, dict) else {}
        tournament_id = max(1, int(tournament_id))
        payload = {
            "tournamentId": tournament_id,
            "round": max(1, int(document.get("round", 1) or 1)),
            "dataVersion": max(1, int(document.get("dataVersion", 1) or 1)),
            "tournamentData": str(document.get("tournamentData") or ""),
            "progressDataVersion": max(1, int(document.get("progressDataVersion", 1) or 1)),
            "progressData": str(document.get("progressData") or ""),
        }
        resumable = self._tournament_progress_is_resumable(
            payload["round"], payload["tournamentData"], payload["progressData"]
        )
        with self._lock, closing(self._connect()) as connection, connection:
            persona_id = int(self._identity(connection)["persona_id"])
            stored_round = payload["round"] if resumable else 1
            stored_tournament_data = payload["tournamentData"] if resumable else ""
            stored_progress_data = payload["progressData"] if resumable else ""
            connection.execute(
                """INSERT INTO beta_tournament_progress (
                    persona_id,tournament_id,round_value,data_version,tournament_data,
                    progress_data_version,progress_data,updated_at
                ) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(persona_id,tournament_id) DO UPDATE SET
                    round_value=excluded.round_value,data_version=excluded.data_version,
                    tournament_data=excluded.tournament_data,
                    progress_data_version=excluded.progress_data_version,
                    progress_data=excluded.progress_data,updated_at=excluded.updated_at""",
                (persona_id,tournament_id,stored_round,payload["dataVersion"],stored_tournament_data,
                 payload["progressDataVersion"],stored_progress_data,int(time.time())),
            )
            connection.execute(
                "UPDATE beta_offline_tournaments SET current_round=?,updated_at=? WHERE persona_id=? AND tournament_id=?",
                (payload["round"] if resumable else 0, int(time.time()), persona_id, tournament_id),
            )
        # Echo exactly what the client wrote; only the persisted resume marker is
        # sanitized. This keeps first-entry parsing identical to the proven flow.
        return payload

    def offline_tournament_user(self, tournament_id: int) -> dict[str, Any]:
        tournament_id = max(1, int(tournament_id))
        with self._lock, closing(self._connect()) as connection:
            persona_id = int(self._identity(connection)["persona_id"])
            row = connection.execute(
                "SELECT * FROM beta_tournament_progress WHERE persona_id=? AND tournament_id=?",
                (persona_id, tournament_id),
            ).fetchone()
            if row is None or not self._tournament_progress_is_resumable(
                int(row["round_value"]), str(row["tournament_data"] or ""), str(row["progress_data"] or "")
            ):
                return {"tournamentId": tournament_id}
            return {
                "tournamentId": tournament_id, "round": int(row["round_value"]),
                "dataVersion": int(row["data_version"]), "tournamentData": row["tournament_data"],
                "progressDataVersion": int(row["progress_data_version"]), "progressData": row["progress_data"],
            }

    def record_easfc_signal(self, command: int) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            row = connection.execute(
                "SELECT match_id FROM beta_match_sessions WHERE persona_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
                (persona_id,),
            ).fetchone()
            if row is None and int(command) == 2:
                settings = connection.execute(
                    "SELECT stadium_name FROM beta_club_settings WHERE persona_id=?", (persona_id,)
                ).fetchone()
                match_id = str(uuid.uuid4())
                now = int(time.time())
                connection.execute(
                    """INSERT INTO beta_match_sessions (
                        match_id,persona_id,mode,difficulty,stadium_name,status,created_at,started_at,
                        reward_breakdown_json,raw_result_json,easfc_signal,settled
                    ) VALUES (?,?,'easfc-local','unknown',?,'active',?,?, '{}','{}',2,0)""",
                    (match_id, persona_id, settings[0] if settings else DEFAULT_STARTER_STADIUM_NAME, now, now),
                )
                self._counter_add_locked(connection, "matches_started", 1)
                return {"recorded": True, "command": 2, "matchId": match_id, "created": True}
            if row is None:
                return {"recorded": False, "command": int(command)}
            connection.execute(
                "UPDATE beta_match_sessions SET easfc_signal=? WHERE match_id=?",
                (int(command), str(row["match_id"])),
            )
            return {"recorded": True, "command": int(command), "matchId": str(row["match_id"]), "created": False}

    def metrics(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            now = int(time.time())
            day = _utc_day(now)
            counters = {row["counter_key"]: int(row["counter_value"]) for row in connection.execute("SELECT * FROM beta_counters")}
            daily = {row["counter_key"]: int(row["counter_value"]) for row in connection.execute(
                "SELECT counter_key,counter_value FROM beta_daily_counters WHERE day=?", (day,)
            )}
            players_online = int(connection.execute(
                "SELECT COUNT(DISTINCT persona_id) FROM sessions WHERE last_seen>=?", (now - 300,)
            ).fetchone()[0])
            active_matches = int(connection.execute(
                "SELECT COUNT(*) FROM beta_match_sessions WHERE status='active'", ()
            ).fetchone()[0])
            coins_in_circulation = int(connection.execute("SELECT COALESCE(SUM(coins),0) FROM clubs").fetchone()[0])
            registered = int(connection.execute("SELECT COUNT(*) FROM beta_accounts").fetchone()[0])
            last_tx = connection.execute("SELECT MAX(created_at) FROM wallet_transactions").fetchone()[0]
            return {
                "version": "2.41.1-beta2.25.9",
                "service": "FIFA 14 Local FUT BETA",
                "uptimeSeconds": max(0, now - self.started_at),
                "playersOnline": players_online,
                "activeMatches": active_matches,
                "coinsInCirculation": coins_in_circulation,
                "playersPacked": counters.get("players_packed", 0),
                "registeredClubs": registered,
                "clubsActiveToday": players_online,
                "newAccountsToday": daily.get("new_accounts", 0),
                "packsOpenedToday": daily.get("packs_opened", 0),
                "highestRatedPackedToday": daily.get("highest_rated_packed", 0),
                "matchesCompletedToday": daily.get("matches_completed", 0),
                "matchesAbandonedToday": daily.get("matches_dnf", 0),
                "matchCoinsAwardedToday": daily.get("match_coins_awarded", 0),
                "lastEconomyWrite": last_tx,
                "serviceHealth": {
                    "FUT": "Operational",
                    "GameplaySettlement": "Beta",
                    "TransferMarket": "BETA 2.25 enabled",
                    "DiscordAuth": "Schema ready / OAuth not configured",
                },
            }

    def wallet_ledger(self, limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(250, int(limit)))
        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            rows = connection.execute(
                "SELECT * FROM wallet_transactions WHERE persona_id=? ORDER BY transaction_id DESC LIMIT ?",
                (int(identity["persona_id"]), limit),
            ).fetchall()
            return {"transactions": [dict(row) for row in rows]}

    def snapshot(self) -> dict[str, Any]:
        base = super().snapshot()
        base["beta"] = self.beta_profile_summary()
        base["metrics"] = self.metrics()
        return base
