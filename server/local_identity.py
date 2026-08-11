from __future__ import annotations

import os
import json
import math
import random
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from fifa14_ids import definition_id_for, resource_id_for


DEFAULT_NUCLEUS_ID = 1_000_001
DEFAULT_PERSONA_ID = 1_000_001
DEFAULT_PERSONA_NAME = "LocalFUT"
DEFAULT_SID = "LOCAL-FIFA14-SID"
DEFAULT_PHISHING_TOKEN = "LOCAL-FIFA14-PHISHING"
DEFAULT_FUT_ACTIONS = ("INTRO_DONE",)
FUT_ACTION_PATTERN = re.compile(r"^[A-Z0-9_]{1,64}$")

PACK_CATALOG_PATH = Path(__file__).with_name("pack-catalog.v237.json")
PACK_WEIGHTS_PATH = Path(__file__).with_name("pack-weights.v237.json")
PLAYER_CATALOG_PATH = Path(__file__).with_name("fifa14-player-catalog.v237.json")
MANAGER_CATALOG_PATH = Path(__file__).with_name("manager-catalog.v237.json")
SPECIAL_CATALOG_PATH = Path(__file__).with_name("fifa14-special-catalog.v240.json")
LEGEND_CATALOG_PATH = Path(__file__).with_name("fifa14-legend-catalog.v24013.json")
CONSUMABLE_CATALOG_PATH = Path(__file__).with_name("fifa14-consumable-catalog.v2412.json")


def _load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError(f"expected JSON object in {path.name}")
    return document


PACK_CATALOG_DOCUMENT = _load_json(PACK_CATALOG_PATH)
PACK_WEIGHTS_DOCUMENT = _load_json(PACK_WEIGHTS_PATH)
PLAYER_CATALOG_DOCUMENT = _load_json(PLAYER_CATALOG_PATH)
MANAGER_CATALOG_DOCUMENT = _load_json(MANAGER_CATALOG_PATH)
SPECIAL_CATALOG_DOCUMENT = _load_json(SPECIAL_CATALOG_PATH)
LEGEND_CATALOG_DOCUMENT = _load_json(LEGEND_CATALOG_PATH)
CONSUMABLE_CATALOG_DOCUMENT = _load_json(CONSUMABLE_CATALOG_PATH)
PACK_DEFINITIONS = {int(entry["packType"]): entry for entry in PACK_CATALOG_DOCUMENT.get("packs", [])}
PLAYER_CATALOG = list(PLAYER_CATALOG_DOCUMENT.get("players", []))
PLAYER_BY_ASSET = {int(player["assetId"]): player for player in PLAYER_CATALOG}
SPECIAL_PLAYER_CATALOG = list(SPECIAL_CATALOG_DOCUMENT.get("players", []))
# World Cup cards belonged to the separate FIFA 14 World Cup mode and the three
# old fuzzy "legend" matches are superseded by the verified 42-card legend file.
NORMAL_SPECIAL_PLAYER_CATALOG = [
    player for player in SPECIAL_PLAYER_CATALOG
    if str(player.get("cardType", "")).lower() not in {"worldcup", "legend"}
]
LEGEND_PLAYER_CATALOG = list(LEGEND_CATALOG_DOCUMENT.get("players", []))
LEGEND_BY_ASSET = {int(player["assetId"]): player for player in LEGEND_PLAYER_CATALOG}
PLAYER_REFERENCE_BY_ASSET = {**PLAYER_BY_ASSET, **LEGEND_BY_ASSET}
CONSUMABLE_CATALOG = list(CONSUMABLE_CATALOG_DOCUMENT.get("items", []))
CONSUMABLE_BY_RESOURCE = {int(row["resourceId"]): row for row in CONSUMABLE_CATALOG}
# Backward-compatible aliases for older tools; the pool is now a real tiered catalogue.
VERIFIED_PLAYER_POOL_DOCUMENT = PLAYER_CATALOG_DOCUMENT
VERIFIED_PLAYER_POOL = PLAYER_CATALOG
VERIFIED_PLAYER_BY_ASSET = PLAYER_BY_ASSET
LOCAL_TEST_STARTING_COINS = int(PACK_WEIGHTS_DOCUMENT.get("localTestStartingCoins", 500000))

# BETA 2.25.0: regular FUT market contains every base PC player plus every
# normal-mode special variant that our PC catalogue can already render. World
# Cup cards remain in their separate mode and Legends remain disabled on PC.
MARKET_PLAYER_CATALOG = PLAYER_CATALOG + NORMAL_SPECIAL_PLAYER_CATALOG
MARKET_ITEM_ID_BASE = 181_000_000_000
MARKET_TRADE_ID_BASE = 1_900_000_000
USER_TRADE_ID_BASE = 2_000_000_000
TRANSFER_LIST_CAPACITY = 30
MARKET_MAX_COPIES = 8
MARKET_SYNTHETIC_RELIST_SECONDS = 15 * 60
MARKET_SELL_TAX_RATE = 0.05

def _market_listing_copies_for_card(player: dict[str, Any]) -> int:
    rating = int(player.get("rating", 0) or 0)
    special = bool(player.get("specialCard")) or int(player.get("rareFlag", player.get("rareflag", 0)) or 0) > 1
    if special:
        return 3 if rating >= 90 else 4
    if rating <= 64:
        return 3
    if rating <= 74:
        return 4
    if rating <= 82:
        return 5
    if rating <= 87:
        return 6
    return 7

MARKET_PLAYER_BY_RESOURCE = {
    int(player.get("resourceId", player.get("assetId", 0)) or 0): player
    for player in MARKET_PLAYER_CATALOG
}
MARKET_RESOURCE_INDEX = {
    int(player.get("resourceId", player.get("assetId", 0)) or 0): index
    for index, player in enumerate(MARKET_PLAYER_CATALOG)
}
MARKET_LIVE_LISTING_COUNT = sum(_market_listing_copies_for_card(player) for player in MARKET_PLAYER_CATALOG)

PLAYER_ITEM_TYPE = "player"
def player_card_subtype(rare_flag: int) -> int:
    """Retail player ``cardsubtypeid`` is a rare/common discriminator here.

    The previous local build incorrectly treated this as a GK/DEF/MID/ATT
    positional band.  The working FIFA 14 revival payload instead supplies
    position explicitly in ``preferredPosition`` and emits subtype 1 for a
    rare player, 0 otherwise.  Mixing the two contracts is what sent outfield
    cards down the goalkeeper face-stat layout.
    """
    try:
        return 1 if int(rare_flag) else 0
    except (TypeError, ValueError):
        return 0
PLAYER_STAT_COUNT = 5
PLAYER_ATTRIBUTE_COUNT = 6
MIN_RECOGNIZED_SQUAD_PLAYERS = 7
FULL_PLAYER_SCHEMA = "fifa14-v237-capture-fixed-full-itemdata"
FULL_CLUB_SEED_SCHEMA = "fifa14-v24018-duplicate-pairing"
FULL_CLUB_ITEM_BASE = 171_000_000_000
FULL_SPECIAL_ITEM_BASE = 172_000_000_000
FULL_LEGEND_ITEM_BASE = 173_000_000_000
PACK_ITEM_BASE = 180_000_000_000
PACK_FIDELITY_SCHEMA = "fifa14-v24012-pack-fidelity"
LEGACY_INTRO_ITEM_BASE = 170_000_000_000


class LocalIdentityStore:
    """Persistent localhost-only FIFA 14 FUT identity and onboarding state.

    V27 preserves the fresh-account Icebreaker route until a captain is
    selected.  At that exact retail milestone it creates one deterministic
    local club and an active squad from the validated Icebreaker fixture.  A
    later launch can therefore use FIFA's returning-user path instead of
    repeating the captain tutorial or charity match.
    """

    def __init__(self, database: Path | str, initial_mode: str = "new") -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize(initial_mode)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self, initial_mode: str) -> None:
        if initial_mode not in {"new", "existing"}:
            raise ValueError(f"unsupported initial account mode: {initial_mode}")
        with self._lock, closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS identity (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    nucleus_id INTEGER NOT NULL,
                    persona_id INTEGER NOT NULL,
                    persona_name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    online_access INTEGER NOT NULL,
                    trusted INTEGER NOT NULL,
                    phishing_question INTEGER NOT NULL,
                    phishing_token TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    sid TEXT PRIMARY KEY,
                    persona_id INTEGER NOT NULL,
                    client_payload TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clubs (
                    club_id INTEGER PRIMARY KEY,
                    persona_id INTEGER NOT NULL UNIQUE,
                    club_name TEXT NOT NULL,
                    club_abbr TEXT NOT NULL,
                    badge_id INTEGER NOT NULL,
                    team_id INTEGER NOT NULL,
                    established INTEGER NOT NULL,
                    division_online INTEGER NOT NULL,
                    coins INTEGER NOT NULL,
                    fifa_points INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fut_users (
                    persona_id INTEGER PRIMARY KEY,
                    created_at INTEGER NOT NULL,
                    active_squad_id INTEGER,
                    starter_pack_claimed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS squads (
                    squad_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    persona_id INTEGER NOT NULL,
                    squad_name TEXT NOT NULL,
                    formation TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    chemistry INTEGER NOT NULL DEFAULT 0,
                    star_rating INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS squad_players (
                    squad_id INTEGER NOT NULL,
                    slot_index INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    asset_id INTEGER NOT NULL,
                    resource_id INTEGER NOT NULL,
                    team_id INTEGER NOT NULL,
                    rating INTEGER NOT NULL,
                    rare_flag INTEGER NOT NULL,
                    play_style INTEGER NOT NULL,
                    preferred_position TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    kit_number INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (squad_id, slot_index)
                );
                CREATE TABLE IF NOT EXISTS items (
                    item_id INTEGER PRIMARY KEY,
                    persona_id INTEGER NOT NULL,
                    asset_id INTEGER NOT NULL,
                    item_type TEXT NOT NULL,
                    pile TEXT NOT NULL,
                    tradeable INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS consumable_effects (
                    persona_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    effect_type TEXT NOT NULL,
                    resource_id INTEGER NOT NULL,
                    base_payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (persona_id, item_id, effect_type)
                );
                CREATE TABLE IF NOT EXISTS packs (
                    pack_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    persona_id INTEGER NOT NULL,
                    pack_type INTEGER NOT NULL,
                    pack_name TEXT NOT NULL,
                    unopened INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fut_actions (
                    persona_id INTEGER NOT NULL,
                    action_name TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (persona_id, action_name)
                );
                CREATE TABLE IF NOT EXISTS catalog_items (
                    resource_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    position TEXT NOT NULL,
                    rarity TEXT NOT NULL,
                    nation TEXT NOT NULL,
                    base_price INTEGER NOT NULL,
                    stats_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS client_data (
                    persona_id INTEGER NOT NULL,
                    data_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (persona_id, data_key)
                );
                CREATE TABLE IF NOT EXISTS pack_contents (
                    pack_id INTEGER NOT NULL,
                    ordinal INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (pack_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS schema_meta (
                    meta_key TEXT PRIMARY KEY,
                    meta_value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manager_reference (
                    manager_key INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    quality TEXT NOT NULL,
                    rare INTEGER NOT NULL,
                    rating INTEGER NOT NULL,
                    contract_boost INTEGER NOT NULL,
                    resource_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS market_listings (
                    trade_id INTEGER PRIMARY KEY,
                    persona_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL UNIQUE,
                    starting_bid INTEGER NOT NULL,
                    buy_now_price INTEGER NOT NULL,
                    duration INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    trade_state TEXT NOT NULL DEFAULT 'active'
                );
                CREATE TABLE IF NOT EXISTS market_trends (
                    resource_id INTEGER PRIMARY KEY,
                    pressure REAL NOT NULL DEFAULT 0.0,
                    updated_at INTEGER NOT NULL,
                    last_price INTEGER NOT NULL DEFAULT 0,
                    total_buys INTEGER NOT NULL DEFAULT 0,
                    total_sales INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS market_synthetic_sales (
                    trade_id INTEGER PRIMARY KEY,
                    resource_id INTEGER NOT NULL,
                    sold_price INTEGER NOT NULL,
                    sold_at INTEGER NOT NULL
                );
                """
            )
            # BETA 2.8: existing progression databases predate the native squad
            # scalar/kit-number fields. SQLite CREATE TABLE IF NOT EXISTS does
            # not add columns, so migrate them in place without resetting the
            # user's club, players, packs, or economy.
            squad_columns = {row[1] for row in connection.execute("PRAGMA table_info(squads)").fetchall()}
            if "chemistry" not in squad_columns:
                connection.execute("ALTER TABLE squads ADD COLUMN chemistry INTEGER NOT NULL DEFAULT 0")
            if "star_rating" not in squad_columns:
                connection.execute("ALTER TABLE squads ADD COLUMN star_rating INTEGER NOT NULL DEFAULT 0")
            squad_player_columns = {row[1] for row in connection.execute("PRAGMA table_info(squad_players)").fetchall()}
            if "kit_number" not in squad_player_columns:
                connection.execute("ALTER TABLE squad_players ADD COLUMN kit_number INTEGER NOT NULL DEFAULT 0")
            market_listing_columns = {row[1] for row in connection.execute("PRAGMA table_info(market_listings)").fetchall()}
            if "item_payload" not in market_listing_columns:
                connection.execute("ALTER TABLE market_listings ADD COLUMN item_payload TEXT NOT NULL DEFAULT '{}'")
            if "sold_price" not in market_listing_columns:
                connection.execute("ALTER TABLE market_listings ADD COLUMN sold_price INTEGER NOT NULL DEFAULT 0")
            if "sold_at" not in market_listing_columns:
                connection.execute("ALTER TABLE market_listings ADD COLUMN sold_at INTEGER NOT NULL DEFAULT 0")
            if "auto_sell_after" not in market_listing_columns:
                connection.execute("ALTER TABLE market_listings ADD COLUMN auto_sell_after INTEGER NOT NULL DEFAULT 0")
            if "market_value_at_list" not in market_listing_columns:
                connection.execute("ALTER TABLE market_listings ADD COLUMN market_value_at_list INTEGER NOT NULL DEFAULT 0")
            now = int(time.time())

            # BETA 2.25.4: BETA 2.25.4 added item_payload/sale metadata to an
            # already-existing market_listings table. SQLite filled legacy rows
            # with '{}'. If a lazy bot then bought one of those listings, 2.25.4
            # deleted the backing item before tradePile rendered the closed sale,
            # leaving itemData={} and crashing the retail CardsDLL parser.
            #
            # Snapshot any still-backed legacy listing before market ticks run.
            # Old active listings are re-aged from this launch so upgrading does
            # not instantly auto-sell everything merely because created_at came
            # from a pre-bot build. Closed rows that have already lost both their
            # item snapshot and backing item are safe to clear: their sale coins
            # were credited by 2.25.4 and the malformed row is not renderable.
            legacy_market_rows = connection.execute(
                "SELECT trade_id,persona_id,item_id,trade_state,item_payload,market_value_at_list "
                "FROM market_listings WHERE item_payload IS NULL OR TRIM(item_payload) IN ('','{}')"
            ).fetchall()
            for legacy in legacy_market_rows:
                backing = connection.execute(
                    "SELECT payload FROM items WHERE persona_id=? AND item_id=?",
                    (int(legacy["persona_id"]), int(legacy["item_id"])),
                ).fetchone()
                backing_payload = str(backing["payload"] or "") if backing is not None else ""
                if backing_payload and backing_payload.strip() not in {"", "{}"}:
                    connection.execute(
                        "UPDATE market_listings SET item_payload=? WHERE trade_id=?",
                        (backing_payload, int(legacy["trade_id"])),
                    )
                    if str(legacy["trade_state"]) == "active" and int(legacy["market_value_at_list"] or 0) <= 0:
                        connection.execute(
                            "UPDATE market_listings SET created_at=?,auto_sell_after=0 WHERE trade_id=?",
                            (now, int(legacy["trade_id"])),
                        )
                elif str(legacy["trade_state"]) == "closed":
                    connection.execute(
                        "DELETE FROM market_listings WHERE trade_id=?",
                        (int(legacy["trade_id"]),),
                    )

            connection.execute(
                """
                INSERT OR IGNORE INTO identity (
                    singleton, nucleus_id, persona_id, persona_name, platform,
                    online_access, trusted, phishing_question, phishing_token,
                    created_at
                ) VALUES (1, ?, ?, ?, 'pc', 1, ?, 0, ?, ?)
                """,
                (
                    DEFAULT_NUCLEUS_ID,
                    DEFAULT_PERSONA_ID,
                    DEFAULT_PERSONA_NAME,
                    1 if initial_mode == "existing" else 0,
                    DEFAULT_PHISHING_TOKEN,
                    now,
                ),
            )
            for manager in MANAGER_CATALOG_DOCUMENT.get("managers", []):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO manager_reference (
                        name, quality, rare, rating, contract_boost, resource_id
                    ) VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        str(manager.get("name", "Local Manager")),
                        str(manager.get("quality", "gold")),
                        1 if manager.get("rare") else 0,
                        int(manager.get("rating", 75)),
                        int(manager.get("contractBoost", 0)),
                    ),
                )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta (meta_key, meta_value) VALUES ('backend_schema', 'fifa14-v237-v29-contract-real-card-identities')"
            )
            # Match V29's startup behavior as well as its response shape: an
            # imported profile is canonicalized before the first HTTP request.
            # The launcher also runs the explicit migration/reporting pass.
            self._repair_owned_items_locked(connection, int(self._identity(connection)["persona_id"]))
            self._repair_active_squad_locked(connection, int(self._identity(connection)["persona_id"]))

    def _identity(self, connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM identity WHERE singleton = 1").fetchone()
        if row is None:
            raise RuntimeError("local identity database is not initialized")
        return row

    def _ensure_fut_user_locked(self, connection: sqlite3.Connection) -> sqlite3.Row:
        identity = self._identity(connection)
        connection.execute(
            """
            INSERT OR IGNORE INTO fut_users (
                persona_id, created_at, active_squad_id, starter_pack_claimed
            ) VALUES (?, ?, NULL, 0)
            """,
            (identity["persona_id"], int(time.time())),
        )
        row = connection.execute(
            "SELECT * FROM fut_users WHERE persona_id = ?", (identity["persona_id"],)
        ).fetchone()
        if row is None:
            raise RuntimeError("failed to initialize local FUT user")
        return row

    @staticmethod
    def _club_document(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "year": "2014",
            "assetId": int(row["club_id"]),
            "teamId": int(row["team_id"]),
            "lastAccessTime": int(time.time()),
            "platform": "pc",
            "clubName": row["club_name"],
            "clubAbbr": row["club_abbr"],
            "established": int(row["established"]),
            "divisionOnline": int(row["division_online"]),
            "badgeId": int(row["badge_id"]),
            "skuAccessList": {"FFA14PC": int(time.time())},
        }

    def has_club(self) -> bool:
        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            return connection.execute(
                "SELECT 1 FROM clubs WHERE persona_id = ?", (identity["persona_id"],)
            ).fetchone() is not None

    def profile_kind(self) -> str:
        return "returning-local-club-pc" if self.has_club() else "first-use-no-club-pc"

    def account_info(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            club = connection.execute(
                "SELECT * FROM clubs WHERE persona_id = ?", (identity["persona_id"],)
            ).fetchone()
            clubs = [] if club is None else [self._club_document(club)]
            persona = {
                "personaId": int(identity["persona_id"]),
                "personaName": identity["persona_name"],
                "returningUser": 1 if club is not None else 0,
                "onlineAccess": bool(identity["online_access"]),
                "trial": False,
                "userState": None,
                "userClubList": clubs,
                "trialFree": False,
            }
            return {"userAccountInfo": {"personas": [persona]}}

    def start_session(self, client_payload: bytes = b"") -> str:
        try:
            client_document = json.loads(client_payload.decode("utf-8")) if client_payload else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            client_document = {"rawHex": client_payload.hex()}
        now = int(time.time())
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            connection.execute(
                """
                INSERT INTO sessions (sid, persona_id, client_payload, created_at, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(sid) DO UPDATE SET
                    persona_id = excluded.persona_id,
                    client_payload = excluded.client_payload,
                    last_seen = excluded.last_seen
                """,
                (
                    DEFAULT_SID,
                    identity["persona_id"],
                    json.dumps(client_document, separators=(",", ":"), sort_keys=True),
                    now,
                    now,
                ),
            )
        return DEFAULT_SID

    def _user_actions_locked(self, connection: sqlite3.Connection, persona_id: int) -> dict[str, bool]:
        rows = connection.execute(
            "SELECT action_name, completed FROM fut_actions WHERE persona_id = ?",
            (persona_id,),
        ).fetchall()
        actions = {name: False for name in DEFAULT_FUT_ACTIONS}
        for row in rows:
            name = str(row["action_name"]).upper()
            # The retail PC onboarding parser branches on key presence, not
            # merely the boolean value. Never expose this key until the charity
            # match protocol is implemented; V26 proved it re-enters the broken
            # match path on the following login.
            if name in {
                "CHARITY_MATCH_PLAYED",
                "ICEBREAKER_ENGLISH_CAPTAIN_SELECTED",
            }:
                continue
            actions[name] = bool(row["completed"])
        return actions

    def ensure_fut_user(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            fut_user = self._ensure_fut_user_locked(connection)
            club = connection.execute(
                "SELECT * FROM clubs WHERE persona_id = ?", (identity["persona_id"],)
            ).fetchone()
            club_document = None if club is None else self._club_document(club)
            return {
                "personaId": int(identity["persona_id"]),
                "personaName": identity["persona_name"],
                "userId": int(identity["persona_id"]),
                "created": int(fut_user["created_at"]),
                "returningUser": 1 if club is not None else 0,
                "clubName": "" if club is None else club["club_name"],
                "clubAbbr": "" if club is None else club["club_abbr"],
                "badgeId": 0 if club is None else int(club["badge_id"]),
                "teamId": 0 if club is None else int(club["team_id"]),
                "activeSquadId": fut_user["active_squad_id"],
                "userClubList": [] if club_document is None else [club_document],
                # Only completed, safe action keys are emitted. FIFA 14's
                # parser treats key presence as state, so false-valued tutorial
                # keys are actively harmful.
                **{
                    name: True
                    for name, completed in self._user_actions_locked(
                        connection, int(identity["persona_id"])
                    ).items()
                    if completed
                },
            }

    def user_actions(self) -> dict[str, bool]:
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            self._ensure_fut_user_locked(connection)
            return self._user_actions_locked(connection, int(identity["persona_id"]))

    def update_user_action(self, action_name: str, completed: bool = True) -> dict:
        normalized = action_name.strip().upper()
        if not FUT_ACTION_PATTERN.fullmatch(normalized):
            raise ValueError("invalid FUT user action name")
        # The retail PC client treats the *presence* of CHARITY_MATCH_PLAYED in
        # the action response as a branch input. V26 showed that exposing it on
        # the next login launches the broken charity match again. Acknowledge
        # that telemetry action, but keep it out of the persisted query until
        # FUT Central is stable.
        if normalized in {
            "CHARITY_MATCH_PLAYED",
            "ICEBREAKER_ENGLISH_CAPTAIN_SELECTED",
        }:
            # The selected action is used as the provisioning trigger by the
            # HTTP handler, but returning it on a later action query re-enters
            # the tutorial/match state machine. Persist only the aggregate
            # INTRO_DONE marker created by provision_icebreaker_completion().
            return {}
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            self._ensure_fut_user_locked(connection)
            connection.execute(
                """
                INSERT INTO fut_actions (persona_id, action_name, completed, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(persona_id, action_name) DO UPDATE SET
                    completed = excluded.completed,
                    updated_at = excluded.updated_at
                """,
                (int(identity["persona_id"]), normalized, 1 if completed else 0, int(time.time())),
            )
        return {}

    def phishing_question(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            if identity["trusted"]:
                return {"debug": "Already answered question.", "token": identity["phishing_token"]}
            return {"question": int(identity["phishing_question"]), "attempts": 5, "recoverAttempts": 20}

    def trusted_device(self) -> dict[str, Any]:
        """Return persisted V29-compatible trusted-console state."""
        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            trusted = bool(identity["trusted"])
            return {
                "trusted": trusted,
                "changed": False,
                "exists": trusted,
                "locked": False,
                "deviceId": "LOCAL-FIFA14-PC",
            }

    def validate_phishing_answer(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            connection.execute("UPDATE identity SET trusted = 1 WHERE singleton = 1")
            token = identity["phishing_token"]
        return {
            "debug": "Answer is correct.",
            "string": "OK",
            "code": "200",
            "reason": "Answer is correct.",
            "token": token,
        }

    def _create_club_locked(
        self,
        connection: sqlite3.Connection,
        *,
        club_name: str,
        club_abbr: str,
        badge_id: int,
        team_id: int,
        established: int | None = None,
    ) -> dict[str, Any]:
        identity = self._identity(connection)
        self._ensure_fut_user_locked(connection)
        club_name = club_name.strip()
        club_abbr = club_abbr.strip().upper()
        if not 1 <= len(club_name) <= 24:
            raise ValueError("club name must be between 1 and 24 characters")
        if not 1 <= len(club_abbr) <= 3:
            raise ValueError("club abbreviation must be between 1 and 3 characters")
        established = int(established if established is not None else time.time())
        connection.execute(
            """
            INSERT INTO clubs (
                club_id, persona_id, club_name, club_abbr, badge_id, team_id,
                established, division_online, coins, fifa_points
            ) VALUES (1, ?, ?, ?, ?, ?, ?, 10, ?, 0)
            ON CONFLICT(persona_id) DO UPDATE SET
                club_name = excluded.club_name,
                club_abbr = excluded.club_abbr,
                badge_id = excluded.badge_id,
                team_id = excluded.team_id
            """,
            (identity["persona_id"], club_name, club_abbr, int(badge_id), int(team_id), established, LOCAL_TEST_STARTING_COINS),
        )
        row = connection.execute(
            "SELECT * FROM clubs WHERE persona_id = ?", (identity["persona_id"],)
        ).fetchone()
        assert row is not None
        return self._club_document(row)

    def create_club(self, club_name: str, club_abbr: str, badge_id: int = 241, team_id: int = 241) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection, connection:
            return self._create_club_locked(
                connection,
                club_name=club_name,
                club_abbr=club_abbr,
                badge_id=badge_id,
                team_id=team_id,
            )

    @staticmethod
    def _validate_pack(pack: dict[str, Any]) -> None:
        arrays = ["squad", "teamId", "Rating", "Rare", "playStyle"] + [f"Attribute{i}" for i in range(1, 7)]
        for key in arrays:
            values = pack.get(key)
            if not isinstance(values, list) or len(values) != 23:
                raise ValueError(f"Icebreaker pack {key} must contain 23 values")
        if any(not isinstance(value, int) or value <= 0 for value in pack["squad"]):
            raise ValueError("Icebreaker squad contains a zero or invalid player resource")

    def provision_icebreaker_completion(self, pack: dict[str, Any]) -> dict[str, Any]:
        """Persist one deterministic local club and active squad.

        The selector's POST does not include its selected pack id, so V27 uses
        the validated Messi pack as the canonical persistent squad. The retail
        Icebreaker presentation remains untouched; this state only supports the
        post-selector FUT Central bootstrap and subsequent launches.
        """
        self._validate_pack(pack)
        positions = [
            "ST", "ST", "LM", "CM", "CM", "RM", "LB", "CB", "CB", "RB", "GK",
            "SUB", "SUB", "SUB", "SUB", "SUB", "SUB", "SUB", "RES", "RES", "RES", "RES", "RES",
        ]
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            fut_user = self._ensure_fut_user_locked(connection)
            existing_squad_id = fut_user["active_squad_id"]
            if existing_squad_id is not None and bool(fut_user["starter_pack_claimed"]):
                self._repair_owned_items_locked(connection, int(identity["persona_id"]))
                self._repair_active_squad_locked(connection, int(identity["persona_id"]))
                existing_count = int(connection.execute(
                    "SELECT COUNT(*) FROM squad_players WHERE squad_id = ? AND item_id > 0",
                    (int(existing_squad_id),),
                ).fetchone()[0])
                existing_club = connection.execute(
                    "SELECT * FROM clubs WHERE persona_id = ?", (identity["persona_id"],)
                ).fetchone()
                if existing_count > 0 and existing_club is not None:
                    return {
                        "club": self._club_document(existing_club),
                        "activeSquadId": int(existing_squad_id),
                        "players": existing_count,
                        "alreadyProvisioned": True,
                    }
            club = self._create_club_locked(
                connection,
                club_name="Local FUT",
                club_abbr="LFT",
                badge_id=241,
                team_id=241,
            )
            row = connection.execute(
                "SELECT squad_id FROM squads WHERE persona_id = ? AND active = 1 ORDER BY squad_id LIMIT 1",
                (identity["persona_id"],),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    "INSERT INTO squads (persona_id, squad_name, formation, active) VALUES (?, 'Local XI', ?, 1)",
                    (identity["persona_id"], str(pack.get("formation") or "f442")),
                )
                squad_id = int(cursor.lastrowid)
            else:
                squad_id = int(row["squad_id"])
                connection.execute(
                    "UPDATE squads SET squad_name = 'Local XI', formation = ?, active = 1 WHERE squad_id = ?",
                    (str(pack.get("formation") or "f442"), squad_id),
                )
            connection.execute("DELETE FROM squad_players WHERE squad_id = ?", (squad_id,))
            connection.execute("DELETE FROM items WHERE persona_id = ?", (identity["persona_id"],))
            for index, asset_id in enumerate(pack["squad"]):
                item_id = 170_000_000_000 + index + 1
                payload = self._v27_player_payload(item_id=item_id, pack=pack, index=index)
                attrs = [int(entry["value"]) for entry in payload["attributeList"]]
                connection.execute(
                    """
                    INSERT INTO squad_players (
                        squad_id, slot_index, item_id, asset_id, resource_id,
                        team_id, rating, rare_flag, play_style,
                        preferred_position, attributes_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        squad_id,
                        index,
                        item_id,
                        int(payload["assetId"]),
                        int(payload["resourceId"]),
                        int(payload["teamId"]),
                        int(payload["rating"]),
                        int(payload["rareflag"]),
                        int(payload["playStyle"]),
                        str(payload["preferredPosition"]),
                        json.dumps(attrs, separators=(",", ":")),
                    ),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO items (item_id, persona_id, asset_id, item_type, pile, tradeable, payload) VALUES (?, ?, ?, 'player', 'squad', 0, ?)",
                    (item_id, identity["persona_id"], int(payload["assetId"]), json.dumps(payload, separators=(",", ":"))),
                )
            connection.execute(
                "UPDATE fut_users SET active_squad_id = ?, starter_pack_claimed = 1 WHERE persona_id = ?",
                (squad_id, identity["persona_id"]),
            )
            # INTRO_DONE is safe to persist. Keep CHARITY_MATCH_PLAYED absent;
            # V26 proved its returned key selects the broken match branch.
            connection.execute(
                """
                INSERT INTO fut_actions (persona_id, action_name, completed, updated_at)
                VALUES (?, 'INTRO_DONE', 1, ?)
                ON CONFLICT(persona_id, action_name) DO UPDATE SET completed = 1, updated_at = excluded.updated_at
                """,
                (identity["persona_id"], int(time.time())),
            )
            return {"club": club, "activeSquadId": squad_id, "players": 23}

    @staticmethod
    def _full_club_item_id(asset_id: int) -> int:
        return FULL_CLUB_ITEM_BASE + int(asset_id)

    @staticmethod
    def _full_club_roster_assets() -> list[int]:
        """Choose a deterministic, balanced 23-man squad from the full catalogue.

        This is only the active squad representation. Every resolved base player
        is owned separately in My Club; the squad no longer comes from the
        Icebreaker/test-XI fixture.
        """
        preferred = [
            167495, 121939, 155862, 164240, 146530,
            9014, 10535, 41, 156616,
            20801, 158023,
            7826, 41236, 167397, 176580, 168542, 183898,
            139720, 152729, 173731, 189505, 121944, 188545,
        ]
        result: list[int] = []
        seen: set[int] = set()
        for asset in preferred:
            if asset in PLAYER_BY_ASSET and asset not in seen:
                result.append(asset)
                seen.add(asset)
        if len(result) < 23:
            for player in sorted(
                PLAYER_CATALOG,
                key=lambda row: (-int(row.get("rating", 0)), int(row.get("assetId", 0))),
            ):
                asset = int(player["assetId"])
                if asset not in seen:
                    result.append(asset)
                    seen.add(asset)
                if len(result) >= 23:
                    break
        return result[:23]

    def _remove_obsolete_bulk_grant_duplicates_locked(
        self, connection: sqlite3.Connection, persona_id: int
    ) -> int:
        """Remove stale bulk-grant copies while preserving real pack duplicates.

        V2.39 introduced deterministic catalogue-owned item IDs.  Some upgraded
        profiles can already contain a second, older generated copy of most of
        the same base players under a different ID range.  A repeated seed must
        not count both sets as My Club ownership.

        Safety rules:
        * the canonical V2.39 item for the same EA asset must already exist;
        * any item referenced by a pack purchase is preserved;
        * any item referenced by a squad is preserved;
        * unknown/non-catalogue players are preserved.

        This deliberately keeps genuine pack-pulled duplicates while collapsing
        obsolete bulk grants to one canonical base-card item per player.
        """
        pack_item_ids: set[int] = set()
        rows = connection.execute(
            "SELECT pc.payload FROM pack_contents pc "
            "JOIN packs p ON p.pack_id = pc.pack_id WHERE p.persona_id = ?",
            (int(persona_id),),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                raw_id = payload.get("id", payload.get("itemId", 0))
                try:
                    item_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if item_id > 0:
                    pack_item_ids.add(item_id)

        squad_item_ids = {
            int(row[0])
            for row in connection.execute(
                "SELECT sp.item_id FROM squad_players sp "
                "JOIN squads s ON s.squad_id = sp.squad_id "
                "WHERE s.persona_id = ? AND sp.item_id > 0",
                (int(persona_id),),
            ).fetchall()
        }

        canonical_ids = {
            self._full_club_item_id(int(player["assetId"]))
            for player in PLAYER_CATALOG
        }
        existing_canonical = {
            int(row[0])
            for row in connection.execute(
                "SELECT item_id FROM items WHERE persona_id = ? AND item_id >= ? AND item_id < ?",
                (int(persona_id), FULL_CLUB_ITEM_BASE, FULL_CLUB_ITEM_BASE + 10_000_000),
            ).fetchall()
            if int(row[0]) in canonical_ids
        }

        removed = 0
        candidate_rows = connection.execute(
            "SELECT item_id, asset_id FROM items "
            "WHERE persona_id = ? AND item_type = ? ORDER BY item_id",
            (int(persona_id), PLAYER_ITEM_TYPE),
        ).fetchall()
        for row in candidate_rows:
            item_id = int(row["item_id"])
            asset_id = int(row["asset_id"])
            if asset_id not in PLAYER_BY_ASSET:
                continue
            # Never collapse intentional special-card variants into the base card.
            payload_row = connection.execute(
                "SELECT payload FROM items WHERE persona_id = ? AND item_id = ?",
                (int(persona_id), item_id),
            ).fetchone()
            try:
                candidate_payload = json.loads(payload_row[0] or "{}") if payload_row is not None else {}
            except (TypeError, json.JSONDecodeError):
                candidate_payload = {}
            if isinstance(candidate_payload, dict) and (
                bool(candidate_payload.get("specialCard"))
                or self._bounded_int(candidate_payload.get("rareflag", candidate_payload.get("rareFlag", 0)), 0) > 1
                or self._bounded_int(candidate_payload.get("resourceId", asset_id), asset_id) != asset_id
            ):
                continue
            canonical_id = self._full_club_item_id(asset_id)
            if item_id == canonical_id or canonical_id not in existing_canonical:
                continue
            if item_id in pack_item_ids or item_id in squad_item_ids:
                continue
            connection.execute(
                "DELETE FROM items WHERE persona_id = ? AND item_id = ?",
                (int(persona_id), item_id),
            )
            removed += 1
        return removed

    def _remove_historical_owned_player_duplicates_locked(
        self, connection: sqlite3.Connection, persona_id: int
    ) -> int:
        """Collapse old pack pulls that duplicate an already-owned player resource.

        v2.40.13 continues to reject *new* duplicate moves, but databases upgraded
        from the earlier pack experiments can already contain 180... pack-pull
        rows in My Club.  FIFA FUT ownership uniqueness is by the card's
        resourceId, not merely assetId, so special versions remain distinct.

        Prefer deterministic catalogue-owned items (171/172/173 ranges) and
        only remove extra owned copies of the exact same player resource.  If
        an old duplicate was referenced by a squad slot, rewire that slot to
        the canonical kept item before deleting it.
        """
        rows = connection.execute(
            "SELECT item_id, asset_id, payload FROM items "
            "WHERE persona_id = ? AND item_type = ? ORDER BY item_id",
            (int(persona_id), PLAYER_ITEM_TYPE),
        ).fetchall()
        by_resource: dict[int, list[tuple[int, int, dict[str, Any]]]] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            resource_id = self._bounded_int(
                payload.get("resourceId", payload.get("assetId", row["asset_id"])),
                int(row["asset_id"]), minimum=1,
            )
            by_resource.setdefault(resource_id, []).append(
                (int(row["item_id"]), int(row["asset_id"]), payload)
            )

        removed = 0
        for resource_id, copies in by_resource.items():
            if len(copies) <= 1:
                continue
            # Catalogue-owned deterministic rows sort before 180... pack pulls.
            canonical = min(copies, key=lambda row: (1 if row[0] >= PACK_ITEM_BASE else 0, row[0]))
            keep_id, keep_asset, keep_payload = canonical
            for item_id, asset_id, payload in copies:
                if item_id == keep_id:
                    continue
                # Rewire any squad slot to the kept canonical card.  The slot
                # payload is refreshed from the canonical ItemData immediately.
                squad_rows = connection.execute(
                    "SELECT squad_id, slot_index FROM squad_players WHERE item_id = ?",
                    (item_id,),
                ).fetchall()
                for squad_row in squad_rows:
                    connection.execute(
                        "UPDATE squad_players SET item_id=?, asset_id=?, resource_id=?, team_id=?, rating=?, rare_flag=?, "
                        "play_style=?, preferred_position=?, attributes_json=? WHERE squad_id=? AND slot_index=?",
                        (
                            keep_id, keep_asset, resource_id,
                            self._bounded_int(keep_payload.get("teamId", keep_payload.get("teamid", 0)), 0),
                            self._bounded_int(keep_payload.get("rating", 0), 0),
                            self._bounded_int(keep_payload.get("rareFlag", keep_payload.get("rareflag", 0)), 0),
                            self._bounded_int(keep_payload.get("playStyle", 0), 0),
                            str(keep_payload.get("preferredPosition") or "CM"),
                            json.dumps(self._array_values(keep_payload.get("attributeArray", keep_payload.get("attributeList", [])), PLAYER_ATTRIBUTE_COUNT)),
                            int(squad_row["squad_id"]), int(squad_row["slot_index"]),
                        ),
                    )
                connection.execute(
                    "DELETE FROM items WHERE persona_id = ? AND item_id = ?",
                    (int(persona_id), item_id),
                )
                removed += 1
        return removed

    def provision_full_catalog_club(self, pack: dict[str, Any] | None = None) -> dict[str, Any]:
        """Own every resolved FIFA 14 base player and bypass the test-XI intro state.

        V2.39 makes the resolved base-player catalogue the user's persistent My
        Club collection. The deterministic catalogue item IDs are disjoint from
        pack-pull IDs, so packs can still create duplicates later. Existing
        custom club naming and non-intro squad edits are preserved.
        """
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            fut_user = self._ensure_fut_user_locked(connection)
            existing_club = connection.execute(
                "SELECT * FROM clubs WHERE persona_id = ?", (persona_id,)
            ).fetchone()
            if existing_club is None:
                club_document = self._create_club_locked(
                    connection,
                    club_name="Local FUT",
                    club_abbr="LFT",
                    badge_id=241,
                    team_id=241,
                )
            else:
                club_document = self._club_document(existing_club)

            existing_catalog_assets = {
                int(row[0])
                for row in connection.execute(
                    "SELECT asset_id FROM items WHERE persona_id = ? AND item_type = ? AND item_id >= ? AND item_id < ?",
                    (persona_id, PLAYER_ITEM_TYPE, FULL_CLUB_ITEM_BASE, FULL_CLUB_ITEM_BASE + 10_000_000),
                ).fetchall()
            }
            seeded = 0
            repaired = 0
            for player in PLAYER_CATALOG:
                asset_id = int(player["assetId"])
                item_id = self._full_club_item_id(asset_id)
                current = connection.execute(
                    "SELECT * FROM items WHERE persona_id = ? AND item_id = ?",
                    (persona_id, item_id),
                ).fetchone()
                existing_payload: dict[str, Any] = {}
                if current is not None:
                    try:
                        decoded = json.loads(current["payload"] or "{}")
                        if isinstance(decoded, dict):
                            existing_payload = decoded
                    except (TypeError, json.JSONDecodeError):
                        pass
                # FUT ItemData pile 7 is My Club.  Squad membership is stored
                # separately in squad_players; it is not a different ItemData pile.
                target_pile = 7
                payload = self._canonical_player_payload(
                    item_id=item_id,
                    asset_id=asset_id,
                    existing=existing_payload,
                    pile=target_pile,
                )
                encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                if current is None:
                    connection.execute(
                        "INSERT INTO items (item_id, persona_id, asset_id, item_type, pile, tradeable, payload) "
                        "VALUES (?, ?, ?, ?, 'club', 0, ?)",
                        (item_id, persona_id, asset_id, PLAYER_ITEM_TYPE, encoded),
                    )
                    seeded += 1
                elif (
                    int(current["asset_id"]) != asset_id
                    or str(current["item_type"]).lower() != PLAYER_ITEM_TYPE
                    or str(current["pile"]).lower() not in {"club", "squad"}
                    or str(current["payload"]) != encoded
                ):
                    target_pile_name = "squad" if current is not None and str(current["pile"]).lower() == "squad" else "club"
                    connection.execute(
                        "UPDATE items SET asset_id = ?, item_type = ?, pile = ?, tradeable = 0, payload = ? "
                        "WHERE persona_id = ? AND item_id = ?",
                        (asset_id, PLAYER_ITEM_TYPE, target_pile_name, encoded, persona_id, item_id),
                    )
                    repaired += 1

            # V2.40: own every conservatively resolved historical special variant
            # (IF/TOTY/TOTS/MOTM/green/World Cup/etc.) as a separate persistent
            # card.  These IDs are disjoint from both base grants and pack pulls.
            special_seeded = 0
            special_repaired = 0
            for special_index, player in enumerate(SPECIAL_PLAYER_CATALOG, start=1):
                if str(player.get("cardType", "")).lower() == "legend":
                    continue
                asset_id = int(player["assetId"])
                item_id = FULL_SPECIAL_ITEM_BASE + special_index
                current = connection.execute(
                    "SELECT * FROM items WHERE persona_id = ? AND item_id = ?",
                    (persona_id, item_id),
                ).fetchone()
                existing_payload = dict(player)
                if current is not None:
                    try:
                        prior = json.loads(current["payload"] or "{}")
                        if isinstance(prior, dict):
                            # Preserve gameplay counters while authoritative card
                            # identity/rating/rarity comes from the special catalog.
                            prior.update(player)
                            existing_payload = prior
                    except (TypeError, json.JSONDecodeError):
                        pass
                payload = self._canonical_player_payload(
                    item_id=item_id, asset_id=asset_id, existing=existing_payload, pile=7
                )
                encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                if current is None:
                    connection.execute(
                        "INSERT INTO items (item_id, persona_id, asset_id, item_type, pile, tradeable, payload) "
                        "VALUES (?, ?, ?, ?, 'club', 0, ?)",
                        (item_id, persona_id, asset_id, PLAYER_ITEM_TYPE, encoded),
                    )
                    special_seeded += 1
                elif str(current["payload"]) != encoded or str(current["pile"]).lower() not in {"club", "squad"}:
                    connection.execute(
                        "UPDATE items SET asset_id=?, item_type=?, pile='club', tradeable=0, payload=? "
                        "WHERE persona_id=? AND item_id=?",
                        (asset_id, PLAYER_ITEM_TYPE, encoded, persona_id, item_id),
                    )
                    special_repaired += 1

            # v2.40.13: remove the three obsolete fuzzy legend matches from the old
            # special catalogue, then seed the complete 42-card FIFA 14 Legends set.
            connection.execute(
                "DELETE FROM items WHERE persona_id = ? AND item_id >= ? AND item_id < ? "
                "AND lower(COALESCE(json_extract(payload, '$.cardType'), '')) = 'legend'",
                (persona_id, FULL_SPECIAL_ITEM_BASE, FULL_SPECIAL_ITEM_BASE + len(SPECIAL_PLAYER_CATALOG) + 10),
            )
            legend_seeded = 0
            legend_repaired = 0
            for legend_index, player in enumerate(LEGEND_PLAYER_CATALOG, start=1):
                asset_id = int(player["assetId"])
                item_id = FULL_LEGEND_ITEM_BASE + legend_index
                current = connection.execute(
                    "SELECT * FROM items WHERE persona_id = ? AND item_id = ?", (persona_id, item_id)
                ).fetchone()
                existing_payload = dict(player)
                if current is not None:
                    try:
                        prior = json.loads(current["payload"] or "{}")
                        if isinstance(prior, dict):
                            prior.update(player)
                            existing_payload = prior
                    except (TypeError, json.JSONDecodeError):
                        pass
                payload = self._canonical_player_payload(
                    item_id=item_id, asset_id=asset_id, existing=existing_payload, pile=7
                )
                encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                if current is None:
                    connection.execute(
                        "INSERT INTO items (item_id, persona_id, asset_id, item_type, pile, tradeable, payload) "
                        "VALUES (?, ?, ?, ?, 'club', 0, ?)",
                        (item_id, persona_id, asset_id, PLAYER_ITEM_TYPE, encoded),
                    )
                    legend_seeded += 1
                elif str(current["payload"]) != encoded or str(current["pile"]).lower() not in {"club", "squad"}:
                    connection.execute(
                        "UPDATE items SET asset_id=?, item_type=?, pile='club', tradeable=0, payload=? "
                        "WHERE persona_id=? AND item_id=?",
                        (asset_id, PLAYER_ITEM_TYPE, encoded, persona_id, item_id),
                    )
                    legend_repaired += 1

            # Collapse stale pre-v2.39 bulk grants, then clean historical pack
            # pulls that were admitted before duplicate rejection existed.  Resource
            # identity keeps every legitimate special version distinct.
            obsolete_bulk_duplicates_removed = self._remove_obsolete_bulk_grant_duplicates_locked(
                connection, persona_id
            )
            historical_owned_duplicates_removed = self._remove_historical_owned_player_duplicates_locked(
                connection, persona_id
            )

            # Remove only the old deterministic Icebreaker-owned cards. Pack
            # pulls use the 180... range and are deliberately preserved.
            legacy_min = LEGACY_INTRO_ITEM_BASE + 1
            legacy_max = LEGACY_INTRO_ITEM_BASE + 23
            legacy_removed = int(connection.execute(
                "SELECT COUNT(*) FROM items WHERE persona_id = ? AND item_id BETWEEN ? AND ?",
                (persona_id, legacy_min, legacy_max),
            ).fetchone()[0])
            connection.execute(
                "DELETE FROM items WHERE persona_id = ? AND item_id BETWEEN ? AND ?",
                (persona_id, legacy_min, legacy_max),
            )

            # Reuse a genuinely user-edited squad; replace the old Local XI or
            # missing squad with a normal persistent squad backed by the same
            # My Club item IDs.
            squad_row = None
            active_id = fut_user["active_squad_id"]
            if active_id is not None:
                squad_row = connection.execute(
                    "SELECT * FROM squads WHERE persona_id = ? AND squad_id = ?",
                    (persona_id, int(active_id)),
                ).fetchone()
            if squad_row is None:
                squad_row = connection.execute(
                    "SELECT * FROM squads WHERE persona_id = ? AND active = 1 ORDER BY squad_id LIMIT 1",
                    (persona_id,),
                ).fetchone()
            should_rebuild = squad_row is None
            if squad_row is not None:
                old_intro_refs = int(connection.execute(
                    "SELECT COUNT(*) FROM squad_players WHERE squad_id = ? AND item_id BETWEEN ? AND ?",
                    (int(squad_row["squad_id"]), legacy_min, legacy_max),
                ).fetchone()[0])
                should_rebuild = str(squad_row["squad_name"]).strip().lower() in {"", "local xi", "test xi"} or old_intro_refs > 0

            if squad_row is None:
                cursor = connection.execute(
                    "INSERT INTO squads (persona_id, squad_name, formation, active) VALUES (?, 'Ultimate XI', 'f442', 1)",
                    (persona_id,),
                )
                squad_id = int(cursor.lastrowid)
            else:
                squad_id = int(squad_row["squad_id"])
                connection.execute("UPDATE squads SET active = CASE WHEN squad_id = ? THEN 1 ELSE 0 END WHERE persona_id = ?", (squad_id, persona_id))

            if should_rebuild:
                connection.execute(
                    "UPDATE squads SET squad_name = 'Ultimate XI', formation = 'f442', active = 1 WHERE squad_id = ?",
                    (squad_id,),
                )
                connection.execute("DELETE FROM squad_players WHERE squad_id = ?", (squad_id,))
                for slot_index, asset_id in enumerate(self._full_club_roster_assets()):
                    item_id = self._full_club_item_id(asset_id)
                    item = connection.execute(
                        "SELECT * FROM items WHERE persona_id = ? AND item_id = ?",
                        (persona_id, item_id),
                    ).fetchone()
                    if item is None:
                        continue
                    self._write_squad_slot_locked(connection, squad_id, slot_index, item)
                    try:
                        existing = json.loads(item["payload"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        existing = {}
                    squad_payload = self._canonical_player_payload(
                        item_id=item_id,
                        asset_id=asset_id,
                        existing=existing if isinstance(existing, dict) else {},
                        pile=7,
                        slot_index=slot_index,
                    )
                    connection.execute(
                        "UPDATE items SET pile = 'squad', payload = ? WHERE persona_id = ? AND item_id = ?",
                        (json.dumps(squad_payload, separators=(",", ":"), ensure_ascii=False), persona_id, item_id),
                    )

            connection.execute(
                "UPDATE fut_users SET active_squad_id = ?, starter_pack_claimed = 1 WHERE persona_id = ?",
                (squad_id, persona_id),
            )
            now = int(time.time())
            for action_name in ("INTRO_DONE", "ICEBREAKER_ENGLISH_CAPTAIN_SELECTED"):
                connection.execute(
                    "INSERT INTO fut_actions (persona_id, action_name, completed, updated_at) VALUES (?, ?, 1, ?) "
                    "ON CONFLICT(persona_id, action_name) DO UPDATE SET completed = 1, updated_at = excluded.updated_at",
                    (persona_id, action_name, now),
                )
            # Do not set CHARITY_MATCH_PLAYED: earlier runtime tracing proved
            # that key selects the obsolete match branch in this build.
            connection.execute(
                "INSERT INTO schema_meta (meta_key, meta_value) VALUES ('full_club_seed', ?) "
                "ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value",
                (f"{FULL_CLUB_SEED_SCHEMA}:{len(PLAYER_CATALOG)}:{len(SPECIAL_PLAYER_CATALOG)}:{len(LEGEND_PLAYER_CATALOG)}",),
            )
            total_owned = int(connection.execute(
                "SELECT COUNT(*) FROM items WHERE persona_id = ? AND item_type = ?",
                (persona_id, PLAYER_ITEM_TYPE),
            ).fetchone()[0])
            rare_owned = int(connection.execute(
                "SELECT COUNT(*) FROM items WHERE persona_id = ? AND item_type = ? AND json_extract(payload, '$.rareflag') > 0",
                (persona_id, PLAYER_ITEM_TYPE),
            ).fetchone()[0])
            return {
                "club": club_document,
                "activeSquadId": squad_id,
                "catalogPlayers": len(PLAYER_CATALOG),
                "specialCatalogPlayers": len([p for p in SPECIAL_PLAYER_CATALOG if str(p.get("cardType", "")).lower() != "legend"]),
                "legendCatalogPlayers": len(LEGEND_PLAYER_CATALOG),
                "playersSeeded": seeded,
                "specialPlayersSeeded": special_seeded,
                "specialPlayersRepaired": special_repaired,
                "legendPlayersSeeded": legend_seeded,
                "legendPlayersRepaired": legend_repaired,
                "playersRepaired": repaired,
                "legacyIntroItemsRemoved": legacy_removed,
                "obsoleteBulkDuplicatesRemoved": obsolete_bulk_duplicates_removed,
                "historicalOwnedDuplicatesRemoved": historical_owned_duplicates_removed,
                "ownedPlayers": total_owned,
                "rarePlayers": rare_owned,
                "squadRebuilt": bool(should_rebuild),
            }


    def provision_pack_play_club(
        self,
        pack: dict[str, Any] | None = None,
        *,
        legend_client_ready: bool = False,
        legend_asset_ids: list[int] | set[int] | tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        """Migrate My Club to a compact, pack-play-safe ownership model.

        Base/special catalogues are reference data, not automatically-owned cards.
        V2.40.20 keeps Legends out of packs but can grant the 42 cards directly
        to My Club after the launcher has independently verified that all 42
        player IDs exist in the patched PC cards_ng_db database. If that client
        database gate is not satisfied, the previous compact/no-Legend behavior
        is preserved and any stale experimental Legend rows are removed.

        On the one-time schema migration we close stale unresolved pack piles from
        older experimental builds.  Future purchases are guarded so a second pack
        cannot be opened while New Items from the first pack remain unresolved.
        """
        if not self.has_club():
            self.provision_full_catalog_club(pack)

        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            previous_schema_row = connection.execute(
                "SELECT meta_value FROM schema_meta WHERE meta_key='club_ownership_schema'"
            ).fetchone()
            previous_schema = "" if previous_schema_row is None else str(previous_schema_row[0] or "")
            first_v24018_migration = previous_schema != FULL_CLUB_SEED_SCHEMA

            squad_rows = connection.execute(
                "SELECT sp.squad_id,sp.slot_index,sp.item_id,sp.asset_id FROM squad_players sp "
                "JOIN squads s ON s.squad_id=sp.squad_id "
                "WHERE s.persona_id=? AND sp.item_id>0",
                (persona_id,),
            ).fetchall()

            # Old full-club builds could put deterministic special rows into the
            # squad. Move those slots to the matching base card before removing
            # the bulk special seed.
            squad_specials_demoted = 0
            for squad_row in squad_rows:
                old_item_id = int(squad_row["item_id"])
                asset_id = int(squad_row["asset_id"])
                if not (FULL_SPECIAL_ITEM_BASE <= old_item_id < FULL_LEGEND_ITEM_BASE):
                    continue
                if asset_id not in PLAYER_BY_ASSET:
                    continue
                base_item_id = self._full_club_item_id(asset_id)
                base_item = connection.execute(
                    "SELECT * FROM items WHERE persona_id=? AND item_id=?",
                    (persona_id, base_item_id),
                ).fetchone()
                if base_item is None:
                    base_payload = self._canonical_player_payload(
                        item_id=base_item_id, asset_id=asset_id,
                        existing={"untradeable": True, "contract": 99, "fitness": 99, "morale": 99},
                        pile=7,
                    )
                    connection.execute(
                        "INSERT INTO items(item_id,persona_id,asset_id,item_type,pile,tradeable,payload) "
                        "VALUES(?,?,?,?,'club',0,?)",
                        (base_item_id, persona_id, asset_id, PLAYER_ITEM_TYPE,
                         json.dumps(base_payload, separators=(",", ":"), ensure_ascii=False)),
                    )
                    base_item = connection.execute(
                        "SELECT * FROM items WHERE persona_id=? AND item_id=?",
                        (persona_id, base_item_id),
                    ).fetchone()
                self._write_squad_slot_locked(
                    connection, int(squad_row["squad_id"]), int(squad_row["slot_index"]), base_item
                )
                squad_specials_demoted += 1

            verified_legend_ids = {int(value) for value in (legend_asset_ids or [])}
            catalog_legend_ids = {int(player["assetId"]) for player in LEGEND_PLAYER_CATALOG}
            legend_client_ready = bool(legend_client_ready and catalog_legend_ids.issubset(verified_legend_ids))

            # Before v2.40.20, direct Legend rows were deliberately removed because
            # FIFA 14 PC had no usable player definition for the Xbox-only IDs.
            # Keep that safe behavior unless the launcher has verified all 42 IDs
            # in the client cards DB. Once verified, preserve any Legend squad slot
            # and repair the deterministic My Club rows in-place below.
            broken_legend_squad_slots_cleared = 0
            if not legend_client_ready:
                for squad_row in squad_rows:
                    old_item_id = int(squad_row["item_id"])
                    if not (FULL_LEGEND_ITEM_BASE <= old_item_id < FULL_LEGEND_ITEM_BASE + 1000):
                        continue
                    connection.execute(
                        "DELETE FROM squad_players WHERE squad_id=? AND slot_index=?",
                        (int(squad_row["squad_id"]), int(squad_row["slot_index"])),
                    )
                    broken_legend_squad_slots_cleared += 1

            squad_item_ids = {
                int(row[0])
                for row in connection.execute(
                    "SELECT sp.item_id FROM squad_players sp "
                    "JOIN squads s ON s.squad_id=sp.squad_id "
                    "WHERE s.persona_id=? AND sp.item_id>0",
                    (persona_id,),
                ).fetchall()
            }

            legend_rows_removed = 0
            legend_seeded = 0
            legend_repaired = 0
            if not legend_client_ready:
                # No verified client DB = no server-only Legend rows. This keeps
                # the stable v2.40.19 behavior and avoids DB ERROR cards.
                legend_rows_removed = int(connection.execute(
                    "SELECT COUNT(*) FROM items WHERE persona_id=? AND item_id>=? AND item_id<? AND item_type=?",
                    (persona_id, FULL_LEGEND_ITEM_BASE, FULL_LEGEND_ITEM_BASE + 1000, PLAYER_ITEM_TYPE),
                ).fetchone()[0])
                connection.execute(
                    "DELETE FROM items WHERE persona_id=? AND item_id>=? AND item_id<? AND item_type=?",
                    (persona_id, FULL_LEGEND_ITEM_BASE, FULL_LEGEND_ITEM_BASE + 1000, PLAYER_ITEM_TYPE),
                )
            else:
                # The client database has all 42 Legend player IDs. Grant exactly
                # one deterministic, untradeable copy of each Legend directly to
                # My Club for validation. Legends remain disabled in pack draws.
                for legend_index, player in enumerate(LEGEND_PLAYER_CATALOG, start=1):
                    asset_id = int(player["assetId"])
                    item_id = FULL_LEGEND_ITEM_BASE + legend_index
                    current = connection.execute(
                        "SELECT * FROM items WHERE persona_id=? AND item_id=?",
                        (persona_id, item_id),
                    ).fetchone()
                    existing_payload = dict(player)
                    existing_payload.update({
                        "untradeable": True,
                        "contract": 99,
                        "fitness": 99,
                        "morale": 99,
                        "pile": 7,
                    })
                    if current is not None:
                        try:
                            prior = json.loads(current["payload"] or "{}")
                            if isinstance(prior, dict):
                                prior.update(existing_payload)
                                existing_payload = prior
                        except (TypeError, json.JSONDecodeError):
                            pass
                    payload = self._canonical_player_payload(
                        item_id=item_id,
                        asset_id=asset_id,
                        existing=existing_payload,
                        pile=7,
                    )
                    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                    if current is None:
                        connection.execute(
                            "INSERT INTO items(item_id,persona_id,asset_id,item_type,pile,tradeable,payload) "
                            "VALUES(?,?,?,?,'club',0,?)",
                            (item_id, persona_id, asset_id, PLAYER_ITEM_TYPE, encoded),
                        )
                        legend_seeded += 1
                    elif (
                        int(current["asset_id"]) != asset_id
                        or str(current["item_type"]) != PLAYER_ITEM_TYPE
                        or str(current["pile"]).lower() not in {"club", "squad"}
                        or int(current["tradeable"]) != 0
                        or str(current["payload"]) != encoded
                    ):
                        connection.execute(
                            "UPDATE items SET asset_id=?,item_type=?,pile='club',tradeable=0,payload=? "
                            "WHERE persona_id=? AND item_id=?",
                            (asset_id, PLAYER_ITEM_TYPE, encoded, persona_id, item_id),
                        )
                        legend_repaired += 1

            seeded_specials_removed = int(connection.execute(
                "SELECT COUNT(*) FROM items WHERE persona_id=? AND item_id>=? AND item_id<? AND item_type=?",
                (persona_id, FULL_SPECIAL_ITEM_BASE, FULL_LEGEND_ITEM_BASE, PLAYER_ITEM_TYPE),
            ).fetchone()[0])
            connection.execute(
                "DELETE FROM items WHERE persona_id=? AND item_id>=? AND item_id<? AND item_type=?",
                (persona_id, FULL_SPECIAL_ITEM_BASE, FULL_LEGEND_ITEM_BASE, PLAYER_ITEM_TYPE),
            )

            seeded_base_removed = 0
            base_rows = connection.execute(
                "SELECT item_id FROM items WHERE persona_id=? AND item_id>=? AND item_id<? AND item_type=?",
                (persona_id, FULL_CLUB_ITEM_BASE, FULL_SPECIAL_ITEM_BASE, PLAYER_ITEM_TYPE),
            ).fetchall()
            for row in base_rows:
                item_id = int(row["item_id"])
                if item_id in squad_item_ids:
                    continue
                connection.execute("DELETE FROM items WHERE persona_id=? AND item_id=?", (persona_id, item_id))
                seeded_base_removed += 1

            historical_duplicates_removed = self._remove_historical_owned_player_duplicates_locked(
                connection, persona_id
            )

            # v2.40.16 can leave one frozen New Items pack behind; older builds could accumulate
            # every unresolved pack. On the one-time migration close only those
            # stale unassigned pack rows. Accepted club items are stored separately
            # in items and are never deleted here.
            stale_pack_items_cleared = 0
            stale_packs_closed = 0
            if first_v24018_migration:
                stale_pack_rows = connection.execute(
                    "SELECT pack_id FROM packs WHERE persona_id=? AND unopened=1",
                    (persona_id,),
                ).fetchall()
                stale_packs_closed = len(stale_pack_rows)
                for stale in stale_pack_rows:
                    stale_pack_id = int(stale["pack_id"])
                    stale_pack_items_cleared += int(connection.execute(
                        "SELECT COUNT(*) FROM pack_contents WHERE pack_id=?", (stale_pack_id,)
                    ).fetchone()[0])
                    connection.execute("DELETE FROM pack_contents WHERE pack_id=?", (stale_pack_id,))
                    connection.execute("UPDATE packs SET unopened=0 WHERE pack_id=?", (stale_pack_id,))

            connection.execute(
                "INSERT INTO schema_meta(meta_key,meta_value) VALUES('club_ownership_schema',?) "
                "ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value",
                (FULL_CLUB_SEED_SCHEMA,),
            )
            total_owned = int(connection.execute(
                "SELECT COUNT(*) FROM items WHERE persona_id=? AND item_type=?",
                (persona_id, PLAYER_ITEM_TYPE),
            ).fetchone()[0])
            distinct_resources = int(connection.execute(
                "SELECT COUNT(DISTINCT CAST(json_extract(payload,'$.resourceId') AS INTEGER)) "
                "FROM items WHERE persona_id=? AND item_type=?",
                (persona_id, PLAYER_ITEM_TYPE),
            ).fetchone()[0])
            return {
                "schema": FULL_CLUB_SEED_SCHEMA,
                "baseCatalogPlayers": len(PLAYER_CATALOG),
                "specialPackPoolPlayers": len(NORMAL_SPECIAL_PLAYER_CATALOG),
                "legendCatalogPlayers": len(LEGEND_PLAYER_CATALOG),
                "legendClientDbReady": legend_client_ready,
                "legendClubTestPlayers": len(LEGEND_PLAYER_CATALOG) if legend_client_ready else 0,
                "seededBaseRemoved": seeded_base_removed,
                "seededSpecialsRemoved": seeded_specials_removed,
                "squadSeededSpecialsDemotedToBase": squad_specials_demoted,
                "brokenLegendSquadSlotsCleared": broken_legend_squad_slots_cleared,
                "historicalOwnedDuplicatesRemoved": historical_duplicates_removed,
                "legendPlayersRemoved": legend_rows_removed,
                "legendPlayersSeeded": legend_seeded,
                "legendPlayersRepaired": legend_repaired,
                "legendPlayersOwned": int(connection.execute(
                    "SELECT COUNT(*) FROM items WHERE persona_id=? AND item_id>=? AND item_id<? AND item_type=?",
                    (persona_id, FULL_LEGEND_ITEM_BASE, FULL_LEGEND_ITEM_BASE + 1000, PLAYER_ITEM_TYPE),
                ).fetchone()[0]),
                "stalePackItemsCleared": stale_pack_items_cleared,
                "stalePacksClosed": stale_packs_closed,
                "ownedPlayers": total_owned,
                "distinctOwnedResources": distinct_resources,
            }

    @staticmethod
    def _canonical_player(asset_id: int, *, pack: dict[str, Any] | None = None, index: int | None = None) -> dict[str, Any]:
        """Return FIFA 14 base-card metadata using the real FUT identity model.

        assetId identifies the footballer. For FIFA 14 base owned ItemData the
        client-facing resourceId is the same asset ID, as observed in the V2.35
        capture and Loopizzle's V29-style payload. The 0x60000000 calculator
        value is retained separately for versioned-card diagnostics. FUTBIN
        page-record numbers are never used as EA identity values.
        """
        player = PLAYER_BY_ASSET.get(int(asset_id))
        if player is not None:
            return player
        if pack is None or index is None:
            raise KeyError(f"unverified FIFA 14 player asset id {asset_id}")
        asset = int(asset_id)
        return {
            "assetId": asset,
            # Captured FIFA 14 base owned-card ItemData uses the base asset ID
            # directly. Keep fut-calculator math for versioned/special cards.
            "resourceId": asset,
            "definitionId": asset,
            "version": 1,
            "quality": "gold" if int(pack["Rating"][index]) >= 75 else "silver" if int(pack["Rating"][index]) >= 65 else "bronze",
            "name": f"FIFA 14 player {asset}",
            "rating": int(pack["Rating"][index]),
            "position": LocalIdentityStore._v27_positions()[index],
            "teamId": int(pack["teamId"][index]),
            "rareFlag": int(pack["Rare"][index]),
            "playStyle": int(pack["playStyle"][index]),
            "attributes": [int(pack[f"Attribute{n}"][index]) for n in range(1, 7)],
        }

    @staticmethod
    def _v27_positions() -> list[str]:
        return [
            "ST", "ST", "LM", "CM", "CM", "RM", "LB", "CB", "CB", "RB", "GK",
            "SUB", "SUB", "SUB", "SUB", "SUB", "SUB", "SUB", "RES", "RES", "RES", "RES", "RES",
        ]

    @staticmethod
    def _slot_position(index: int) -> str:
        positions = LocalIdentityStore._v27_positions()
        return positions[index] if 0 <= index < len(positions) else "RES"

    @staticmethod
    def _bounded_int(value: Any, default: int = 0, minimum: int = 0, maximum: int = 2_147_483_647) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    @staticmethod
    def _array_values(value: Any, size: int) -> list[int]:
        result = [0] * size
        if not isinstance(value, list):
            return result
        for fallback_index, entry in enumerate(value[:size]):
            if isinstance(entry, dict):
                try:
                    index = int(entry.get("index", fallback_index))
                    number = int(entry.get("value", 0))
                except (TypeError, ValueError):
                    continue
            else:
                index = fallback_index
                try:
                    number = int(entry)
                except (TypeError, ValueError):
                    continue
            if 0 <= index < size:
                result[index] = max(0, min(99, number))
        return result

    def _canonical_player_payload(
        self,
        *,
        item_id: int,
        asset_id: int,
        existing: dict[str, Any] | None = None,
        pile: int | None = None,
        slot_index: int | None = None,
        pack: dict[str, Any] | None = None,
        index: int | None = None,
    ) -> dict[str, Any]:
        """Build the fuller FIFA 14 player ItemData contract discovered in V29.

        V2.34 corrected asset/resource identities but intentionally retained a
        minimal V27 payload.  Loopizzle's V29 capture shows that FIFA 14 uses
        player-only discriminator fields (especially itemType/cardsubtypeid and
        stats/lifetime arrays) before it treats an owned item as a real player.

        V2.37 follows the actual client-facing behaviour captured from V2.35:
        base owned cards are serialized by CardsDLL with resourceId == assetId.
        The FIFA-14 fut-calculator 0x60000000 resource identity is retained in
        the catalogue as calculatorResourceId for diagnostics and for future
        versioned/special-card work, but is not forced into base ItemData.
        """
        source = dict(existing or {})
        asset_id = self._bounded_int(asset_id, 0, minimum=1)
        base_player = PLAYER_BY_ASSET.get(asset_id)
        if base_player is None and asset_id in LEGEND_BY_ASSET:
            base_player = LEGEND_BY_ASSET[asset_id]
            # A persisted legend payload may carry gameplay counters; merge those
            # below while keeping the verified legend identity authoritative.
            if not source:
                source = dict(base_player)
        if base_player is None:
            base_player = self._canonical_player(asset_id, pack=pack, index=index)
        is_special = bool(source.get("specialCard")) or asset_id in LEGEND_BY_ASSET or self._bounded_int(
            source.get("rareflag", source.get("rareFlag", 0)), 0, minimum=0
        ) > 1
        player = dict(base_player)
        if is_special:
            # Preserve the verified base identity/team/league/nation while using
            # the historical variant's rating, rarity, position and attributes.
            for key in (
                "rating", "position", "teamId", "leagueId", "nation", "nationName",
                "rareFlag", "playStyle", "attributes", "version", "resourceId",
                "cardType", "specialCard",
            ):
                if key in source:
                    player[key] = source[key]
            # Persisted ItemData stores the six face attributes as attributeArray/
            # attributeList rather than the catalogue-only `attributes` key.
            # Preserve those special/Legend values during later repair passes.
            if "attributes" not in source and isinstance(source.get("attributeArray"), list):
                player["attributes"] = source["attributeArray"]
            if "position" not in source and source.get("preferredPosition"):
                player["position"] = source["preferredPosition"]
        version = self._bounded_int(player.get("version", 1), 1, minimum=1, maximum=99)
        # Captured base ItemData uses definition-style IDs (assetId for v1).
        # Special variants therefore use the same definition/revision bit layout
        # rather than the calculator's 0x60000000-prefixed full resource ID.
        expected_resource = asset_id if version == 1 else definition_id_for(asset_id, version)
        resource_id = self._bounded_int(player.get("resourceId", expected_resource), expected_resource, minimum=1)
        if resource_id != expected_resource:
            resource_id = expected_resource

        rating = self._bounded_int(player.get("rating", source.get("rating", 50)), 50, minimum=1, maximum=99)
        source_attributes = source.get("attributeArray", source.get("attributeList"))
        attributes = self._array_values(
            source_attributes if isinstance(source_attributes, list) else player.get("attributes", []),
            PLAYER_ATTRIBUTE_COUNT,
        )
        if not any(attributes):
            attributes = [rating] * PLAYER_ATTRIBUTE_COUNT
        stats = self._array_values(source.get("statsList", source.get("statsArray", [])), PLAYER_STAT_COUNT)
        lifetime_stats = self._array_values(
            source.get("lifetimeStats", source.get("lifetimeStatsArray", [])), PLAYER_STAT_COUNT
        )

        preferred_position = str(source.get("preferredPosition") or player.get("position") or "CM").upper()
        if preferred_position in {"SUB", "RES", "", "UNKNOWN"}:
            slot_position = self._slot_position(slot_index) if slot_index is not None else "CM"
            preferred_position = "CM" if slot_position in {"SUB", "RES"} else slot_position
        is_goalkeeper = preferred_position == "GK"
        team_id = self._bounded_int(player.get("teamId", source.get("teamId", source.get("teamid", 0))), 0)
        rare_flag = self._bounded_int(
            player.get("rareFlag", source.get("rareFlag", source.get("rareflag", 0))), 0, minimum=0, maximum=255
        )
        current_pile = (
            self._bounded_int(source.get("pile", 7), 7, minimum=0, maximum=99)
            if pile is None
            else self._bounded_int(pile, 7, minimum=0, maximum=99)
        )
        # BETA 2.25.8: market/pack cards are tradeable and must keep a retail-like
        # quick-sell value. Older market wins were canonicalized without a
        # discardValue and therefore rendered as 0 coins. Repair those persisted
        # cards lazily while leaving deliberately-untradeable starter cards at 0.
        source_untradeable = bool(source.get("untradeable", True))
        source_tradeable = bool(source.get("tradeable", not source_untradeable))
        discard_value = self._bounded_int(source.get("discardValue", 0), 0)
        if discard_value <= 0 and source_tradeable and not source_untradeable:
            discard_value = self._player_discard_value(player)

        # BETA 2.25.0: keep the native-critical ItemData members first.  FIFA
        # 14's player parser is sensitive to this stream, and the older local
        # payload placed preferredPosition behind local aliases.  Supplying the
        # catalogue position early prevents outfield cards being rendered with
        # the goalkeeper DIV/HAN/KIC/REF/SPD/POS template.
        payload: dict[str, Any] = {
            "id": int(item_id),
            "assetId": asset_id,
            "resourceId": resource_id,
            "rating": rating,
            "preferredPosition": preferred_position,
            "teamid": team_id,
            "leagueId": self._bounded_int(player.get("leagueId", source.get("leagueId", 0)), 0),
            "nation": self._bounded_int(player.get("nation", source.get("nation", 0)), 0),
            "itemType": PLAYER_ITEM_TYPE,
            "itemState": str(source.get("itemState") or "free"),
            "formation": str(source.get("formation") or "f442"),
            "contract": self._bounded_int(source.get("contract", 99), 99, minimum=0, maximum=99),
            "fitness": self._bounded_int(source.get("fitness", 99), 99, minimum=0, maximum=99),
            "injuryGames": self._bounded_int(source.get("injuryGames", 0), 0),
            "injuryType": str(source.get("injuryType") or "none"),
            "suspension": self._bounded_int(source.get("suspension", 0), 0),
            "training": self._bounded_int(source.get("training", 0), 0),
            "playStyle": self._bounded_int(source.get("playStyle", player.get("playStyle", 0)), 0),
            "discardValue": discard_value,
            "lastSalePrice": self._bounded_int(source.get("lastSalePrice", 0), 0),
            "timestamp": self._bounded_int(source.get("timestamp", int(time.time())), int(time.time()), minimum=1),
            "untradeable": bool(source.get("untradeable", True)),
            "rareflag": rare_flag,
            "cardsubtypeid": player_card_subtype(rare_flag),
            "assists": self._bounded_int(source.get("assists", 0), 0),
            "lifetimeAssists": self._bounded_int(source.get("lifetimeAssists", 0), 0),
            "attributeList": [{"index": n, "value": value} for n, value in enumerate(attributes)],
            "statsList": [{"index": n, "value": value} for n, value in enumerate(stats)],
            "lifetimeStats": [{"index": n, "value": value} for n, value in enumerate(lifetime_stats)],

            # Compatibility/backend aliases come only after the native-critical
            # stream above, so an older parser can stop without losing position.
            "itemId": int(item_id),
            "teamId": team_id,
            "name": str(player.get("name", source.get("name", ""))),
            "commonName": str(player.get("commonName", player.get("name", source.get("commonName", source.get("name", ""))))),
            "owners": self._bounded_int(source.get("owners", 1), 1, minimum=1),
            "morale": self._bounded_int(source.get("morale", 99), 99, minimum=0, maximum=99),
            "playerId": asset_id,
            "rareFlag": rare_flag,
            "loyaltyBonus": self._bounded_int(source.get("loyaltyBonus", 1), 1, minimum=0, maximum=1),
            "pile": current_pile,
            "resourceGameYear": 2014,
            "attributeArray": attributes,
            "statsArray": stats,
            "lifetimeStatsArray": lifetime_stats,
        }
        # FIFA 14 PC uses resourceId for the FUT card revision, but its static
        # player lookup still needs the *base* definition.  Sending the versioned
        # revision as definitionId made 91 IF Ibrahimovic resolve as a GK; omitting
        # definitionId entirely still left the PC client without a stable static
        # position source.  The retail-compatible split is therefore:
        #   base card:    resourceId=assetId, definitionId=assetId
        #   special card: resourceId=versionedId, definitionId=assetId
        # This preserves IF/TOTY/etc. artwork/rarity while resolving name/position
        # against the base footballer definition in cards_ng_db.
        payload["definitionId"] = asset_id

        # Keep two backend-only markers in the persisted JSON. CardsDLL ignores
        # unknown keys, while they let migration/repair distinguish an intended
        # special variant from an obsolete duplicate base grant.
        if is_special:
            payload["specialCard"] = True
            payload["cardType"] = str(player.get("cardType", source.get("cardType", "special")))
            payload["version"] = version
        return payload

    def _v27_player_payload(self, *, item_id: int, pack: dict[str, Any], index: int) -> dict[str, Any]:
        asset_id = int(pack["squad"][index])
        initial = {
            "formation": str(pack.get("formation") or "f442"),
            "pile": 7,
            "untradeable": True,
            "contract": 99,
            "fitness": 99,
            "morale": 99,
        }
        return self._canonical_player_payload(
            item_id=item_id,
            asset_id=asset_id,
            existing=initial,
            pile=7,
            slot_index=index,
            pack=pack,
            index=index,
        )

    def _repair_owned_items_locked(self, connection: sqlite3.Connection, persona_id: int | None = None) -> int:
        repaired = 0
        params: tuple[Any, ...] = ()
        sql = "SELECT * FROM items WHERE item_type = ?"
        params = (PLAYER_ITEM_TYPE,)
        if persona_id is not None:
            sql += " AND persona_id = ?"
            params = (PLAYER_ITEM_TYPE, int(persona_id))
        sql += " ORDER BY item_id"
        for row in connection.execute(sql, params).fetchall():
            try:
                existing = json.loads(row["payload"] or "{}")
            except (TypeError, json.JSONDecodeError):
                existing = {}
            if int(row["asset_id"]) not in PLAYER_REFERENCE_BY_ASSET:
                # Unknown imported cards are preserved until their FIFA 14
                # metadata is verified; don't fabricate a new identity.
                continue
            # Transfer-list items are the one owned-player exception to My Club
            # pile 7. Preserve their retail pile 5 identity across relaunches.
            row_pile = str(row["pile"]).lower()
            pile = 5 if row_pile == "trade" else 6 if row_pile == "pending" else 7
            payload = self._canonical_player_payload(
                item_id=int(row["item_id"]),
                asset_id=int(row["asset_id"]),
                existing=existing if isinstance(existing, dict) else {},
                pile=pile,
            )
            encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            if encoded != str(row["payload"]) or str(row["item_type"]).lower() != PLAYER_ITEM_TYPE:
                connection.execute(
                    "UPDATE items SET item_type = ?, payload = ? WHERE item_id = ? AND persona_id = ?",
                    (PLAYER_ITEM_TYPE, encoded, int(row["item_id"]), int(row["persona_id"])),
                )
                repaired += 1
        return repaired

    def _write_squad_slot_locked(
        self,
        connection: sqlite3.Connection,
        squad_id: int,
        slot_index: int,
        item: sqlite3.Row | None,
        kit_number: int = 0,
    ) -> None:
        if item is None:
            connection.execute(
                """
                INSERT OR REPLACE INTO squad_players (
                    squad_id, slot_index, item_id, asset_id, resource_id,
                    team_id, rating, rare_flag, play_style,
                    preferred_position, attributes_json, kit_number
                ) VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0, ?, '[]', ?)
                """,
                (squad_id, slot_index, self._slot_position(slot_index), int(kit_number)),
            )
            return
        try:
            existing = json.loads(item["payload"] or "{}")
        except (TypeError, json.JSONDecodeError):
            existing = {}
        payload = self._canonical_player_payload(
            item_id=int(item["item_id"]),
            asset_id=int(item["asset_id"]),
            existing=existing if isinstance(existing, dict) else {},
            pile=7,
            slot_index=slot_index,
        )
        attrs = payload["attributeArray"]
        connection.execute(
            """
            INSERT OR REPLACE INTO squad_players (
                squad_id, slot_index, item_id, asset_id, resource_id,
                team_id, rating, rare_flag, play_style,
                preferred_position, attributes_json, kit_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                squad_id, slot_index, int(item["item_id"]), int(payload["assetId"]),
                int(payload["resourceId"]), int(payload["teamid"]), int(payload["rating"]),
                int(payload["rareflag"]), int(payload["playStyle"]), self._slot_position(slot_index),
                json.dumps(attrs, separators=(",", ":")), int(kit_number),
            ),
        )
        connection.execute(
            "UPDATE items SET pile = 'squad', item_type = ?, payload = ? WHERE item_id = ? AND persona_id = ?",
            (
                PLAYER_ITEM_TYPE,
                json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                int(item["item_id"]), int(item["persona_id"]),
            ),
        )

    def _repair_active_squad_locked(self, connection: sqlite3.Connection, persona_id: int) -> bool:
        fut_user = connection.execute(
            "SELECT active_squad_id FROM fut_users WHERE persona_id = ? AND active_squad_id IS NOT NULL",
            (int(persona_id),),
        ).fetchone()
        if fut_user is None:
            return False
        squad_id = int(fut_user["active_squad_id"])
        nonzero = int(connection.execute(
            "SELECT COUNT(*) FROM squad_players WHERE squad_id = ? AND item_id > 0", (squad_id,)
        ).fetchone()[0])
        owned = connection.execute(
            "SELECT * FROM items WHERE persona_id = ? AND item_type = ? AND pile NOT IN ('trade','pending') "
            "ORDER BY CASE WHEN pile = 'squad' THEN 0 ELSE 1 END, item_id",
            (int(persona_id), PLAYER_ITEM_TYPE),
        ).fetchall()
        if len(owned) < 11 or nonzero >= 11:
            return False
        connection.execute("DELETE FROM squad_players WHERE squad_id = ?", (squad_id,))
        used: set[int] = set()
        for slot_index in range(23):
            item = owned[slot_index] if slot_index < len(owned) else None
            self._write_squad_slot_locked(connection, squad_id, slot_index, item)
            if item is not None:
                used.add(int(item["item_id"]))
        for item in owned:
            item_id = int(item["item_id"])
            if item_id in used:
                continue
            try:
                existing = json.loads(item["payload"] or "{}")
            except (TypeError, json.JSONDecodeError):
                existing = {}
            payload = self._canonical_player_payload(
                item_id=item_id, asset_id=int(item["asset_id"]),
                existing=existing if isinstance(existing, dict) else {}, pile=7,
            )
            connection.execute(
                "UPDATE items SET pile = 'club', payload = ? WHERE item_id = ? AND persona_id = ?",
                (json.dumps(payload, separators=(",", ":"), ensure_ascii=False), item_id, int(persona_id)),
            )
        return True

    def _resolve_item_locked(self, connection: sqlite3.Connection, persona_id: int, raw_id: int) -> sqlite3.Row | None:
        if raw_id <= 0:
            return None
        row = connection.execute(
            "SELECT * FROM items WHERE persona_id = ? AND item_id = ?", (int(persona_id), int(raw_id))
        ).fetchone()
        if row is not None:
            return row
        return connection.execute(
            "SELECT * FROM items WHERE persona_id = ? AND asset_id = ? ORDER BY item_id LIMIT 1",
            (int(persona_id), int(raw_id)),
        ).fetchone()

    def repair_known_good_roster(self, pack: dict[str, Any]) -> dict[str, Any]:
        """Upgrade the existing squad to the full V29 player contract in-place.

        V2.34/V2.35 carried older identity/payload assumptions. This migration
        preserves the user's squad/order and collected
        club cards, canonicalizes owned player payloads, and only reconstructs
        the active squad if an older client write has already zeroed it.
        """
        self._validate_pack(pack)
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            club = connection.execute(
                "SELECT 1 FROM clubs WHERE persona_id = ?", (persona_id,)
            ).fetchone()
            if club is None:
                return {"changed": False, "reason": "no-club"}
            fut_user = self._ensure_fut_user_locked(connection)
            active_id = fut_user["active_squad_id"]
            if active_id is None:
                row = connection.execute(
                    "SELECT squad_id FROM squads WHERE persona_id = ? AND active = 1 ORDER BY squad_id LIMIT 1",
                    (persona_id,),
                ).fetchone()
                if row is None:
                    cursor = connection.execute(
                        "INSERT INTO squads (persona_id, squad_name, formation, active) VALUES (?, 'Local XI', ?, 1)",
                        (persona_id, str(pack.get("formation") or "f442")),
                    )
                    active_id = int(cursor.lastrowid)
                else:
                    active_id = int(row["squad_id"])
                connection.execute(
                    "UPDATE fut_users SET active_squad_id = ?, starter_pack_claimed = 1 WHERE persona_id = ?",
                    (int(active_id), persona_id),
                )

            # If an imported profile somehow has no owned starter items, seed
            # only the missing deterministic starter set. Normal upgrades never
            # delete or replace already-owned pack cards.
            owned_count = int(connection.execute(
                "SELECT COUNT(*) FROM items WHERE persona_id = ? AND item_type = ?", (persona_id, PLAYER_ITEM_TYPE)
            ).fetchone()[0])
            seeded = 0
            if owned_count == 0:
                for index, asset_id in enumerate(pack["squad"]):
                    item_id = 170_000_000_000 + index + 1
                    payload = self._v27_player_payload(item_id=item_id, pack=pack, index=index)
                    connection.execute(
                        "INSERT OR REPLACE INTO items (item_id, persona_id, asset_id, item_type, pile, tradeable, payload) "
                        "VALUES (?, ?, ?, ?, 'squad', 0, ?)",
                        (item_id, persona_id, int(asset_id), PLAYER_ITEM_TYPE,
                         json.dumps(payload, separators=(",", ":"), ensure_ascii=False)),
                    )
                    seeded += 1

            repaired_items = self._repair_owned_items_locked(connection, persona_id)
            repaired_squad = self._repair_active_squad_locked(connection, persona_id)

            # Re-canonicalize every existing nonzero squad slot so the table's
            # resource/team/rating mirrors the newly repaired ItemData.
            squad_id = int(connection.execute(
                "SELECT active_squad_id FROM fut_users WHERE persona_id = ?", (persona_id,)
            ).fetchone()[0])
            rows = connection.execute(
                "SELECT slot_index, item_id FROM squad_players WHERE squad_id = ? ORDER BY slot_index", (squad_id,)
            ).fetchall()
            for slot in rows:
                if int(slot["item_id"]) <= 0:
                    continue
                item = connection.execute(
                    "SELECT * FROM items WHERE persona_id = ? AND item_id = ?", (persona_id, int(slot["item_id"]))
                ).fetchone()
                if item is not None:
                    self._write_squad_slot_locked(connection, squad_id, int(slot["slot_index"]), item)

            connection.execute(
                "INSERT OR REPLACE INTO schema_meta (meta_key, meta_value) VALUES ('card_schema', ?)",
                (FULL_PLAYER_SCHEMA,),
            )
            changed = bool(seeded or repaired_items or repaired_squad)
            return {
                "changed": changed,
                "reason": "v29-full-itemdata-migrated" if changed else "already-v29-full-itemdata",
                "playersSeeded": seeded,
                "itemsRepaired": repaired_items,
                "squadRepaired": repaired_squad,
            }

    def repair_catalogued_owned_players(self) -> dict[str, Any]:
        """Canonicalize collected FIFA 14 players to the full V29 ItemData contract."""
        changed = 0
        inspected = 0
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            rows = connection.execute(
                "SELECT * FROM items WHERE persona_id = ? AND item_type = ? AND pile NOT IN ('squad','trade','pending') ORDER BY item_id",
                (persona_id, PLAYER_ITEM_TYPE),
            ).fetchall()
            for row in rows:
                inspected += 1
                if int(row["asset_id"]) not in PLAYER_REFERENCE_BY_ASSET:
                    continue
                try:
                    existing = json.loads(row["payload"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    existing = {}
                rebuilt = self._canonical_player_payload(
                    item_id=int(row["item_id"]), asset_id=int(row["asset_id"]),
                    existing=existing if isinstance(existing, dict) else {}, pile=7,
                )
                encoded = json.dumps(rebuilt, separators=(",", ":"), ensure_ascii=False)
                if encoded == str(row["payload"]):
                    continue
                connection.execute(
                    "UPDATE items SET item_type = ?, pile = 'club', payload = ? WHERE item_id = ? AND persona_id = ?",
                    (PLAYER_ITEM_TYPE, encoded, int(row["item_id"]), persona_id),
                )
                changed += 1
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta (meta_key, meta_value) VALUES ('owned_player_identity_schema', ?)",
                (FULL_PLAYER_SCHEMA,),
            )
        return {"changed": changed > 0, "itemsChanged": changed, "itemsInspected": inspected}

    def repair_unopened_pack_contents(self) -> dict[str, Any]:
        """Rebuild stale unopened packs once against the v2.40.12 fidelity schema."""
        rebuilt_packs = 0
        rebuilt_cards = 0
        inspected = 0
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            rows = connection.execute(
                "SELECT pack_id, pack_type FROM packs WHERE persona_id = ? AND unopened = 1 ORDER BY pack_id",
                (identity["persona_id"],),
            ).fetchall()
            for row in rows:
                inspected += 1
                pack_id = int(row["pack_id"])
                definition = PACK_DEFINITIONS.get(int(row["pack_type"]))
                if definition is None:
                    continue
                expected_count = int(definition.get("totalCards", 12))
                item_rows = connection.execute(
                    "SELECT payload FROM pack_contents WHERE pack_id = ? ORDER BY ordinal", (pack_id,)
                ).fetchall()
                valid = len(item_rows) == expected_count
                if valid:
                    for item_row in item_rows:
                        try:
                            payload = json.loads(item_row["payload"])
                        except (TypeError, json.JSONDecodeError):
                            valid = False
                            break
                        if not isinstance(payload, dict) or payload.get("localPackSchema") != PACK_FIDELITY_SCHEMA:
                            valid = False
                            break
                        if int(payload.get("resourceGameYear", 0)) != 2014:
                            valid = False
                            break
                        if bool(payload.get("untradeable", True)) or not bool(payload.get("tradeable", False)):
                            valid = False
                            break
                if valid:
                    continue
                connection.execute("DELETE FROM pack_contents WHERE pack_id = ?", (pack_id,))
                items = self._generate_pack_contents_locked(connection, pack_id=pack_id, definition=definition)
                rebuilt_packs += 1
                rebuilt_cards += len(items)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta (meta_key, meta_value) VALUES (?, ?)",
                ("unopened_pack_identity_schema", PACK_FIDELITY_SCHEMA),
            )
        return {
            "changed": rebuilt_packs > 0,
            "packsRebuilt": rebuilt_packs,
            "cardsRebuilt": rebuilt_cards,
            "packsInspected": inspected,
        }

    @staticmethod
    def _quality_for_rating(rating: int) -> str:
        value = int(rating)
        return "bronze" if value <= 64 else "silver" if value <= 74 else "gold"

    @staticmethod
    def _player_discard_value(player: dict[str, Any]) -> int:
        """Approximate the original FUT 14 discard table without signed overflow."""
        rating = max(1, min(99, int(player.get("rating", 1))))
        quality = str(player.get("quality", LocalIdentityStore._quality_for_rating(rating))).lower()
        rare = int(player.get("rareFlag", player.get("rareflag", 0)))
        special = bool(player.get("specialCard")) or rare > 1
        if special:
            factor = 122.0 if quality == "gold" else 70.0 if quality == "silver" else 20.0
        elif rare > 0:
            factor = 8.0 if quality == "gold" else 3.5 if quality == "silver" else 0.75
        else:
            factor = 4.0 if quality == "gold" else 1.5 if quality == "silver" else 0.30
        return max(0, min(2_147_483_647, int(round(rating * factor))))

    @staticmethod
    def _weighted_player(
        rng: random.Random, *, quality: str, rare_slot: bool, promo: bool,
        excluded_assets: set[int] | None = None, max_rating: int | None = None
    ) -> dict[str, Any]:
        """Draw a base player using deliberately conservative FIFA-14-like tiers.

        EA never published exact per-rating odds for FIFA 14, so the local server
        uses tunable bands instead of pretending the old test-harness weights are
        historical probabilities.
        """
        quality = str(quality).lower()
        excluded_assets = excluded_assets or set()
        quality_pool = [
            player for player in PLAYER_CATALOG
            if str(player.get("quality", LocalIdentityStore._quality_for_rating(int(player["rating"])))) == quality
            and (max_rating is None or int(player.get("rating", 0)) <= int(max_rating))
        ]
        if not quality_pool:
            raise RuntimeError(f"no FIFA 14 {quality} players in live catalogue")
        candidates = [player for player in quality_pool if int(player.get("assetId", -1)) not in excluded_assets]
        if not candidates:
            candidates = quality_pool
        if rare_slot:
            rarity_candidates = [player for player in candidates if int(player.get("rareFlag", 0)) > 0]
        else:
            rarity_candidates = [player for player in candidates if int(player.get("rareFlag", 0)) == 0]
        if rarity_candidates:
            candidates = rarity_candidates
        if quality != "gold":
            return rng.choice(candidates)

        rare_multipliers = PACK_WEIGHTS_DOCUMENT.get("rareSlotMultiplier", {})
        promo_multipliers = PACK_WEIGHTS_DOCUMENT.get("promoSlotMultiplier", {})

        def multiplier_for(band: dict[str, Any], table: dict[str, Any]) -> float:
            lo, hi = int(band["min"]), int(band["max"])
            key = "90+" if lo >= 90 else f"{lo}-{hi}"
            return float(table.get(key, 1.0))

        weighted_bands: list[tuple[list[dict[str, Any]], float]] = []
        for band in PACK_WEIGHTS_DOCUMENT.get("ratingBands", []):
            lo, hi = int(band["min"]), int(band["max"])
            members = [player for player in candidates if lo <= int(player["rating"]) <= hi]
            if not members:
                continue
            weight = max(float(band.get("weight", 0.0)), 0.0)
            if rare_slot:
                weight *= multiplier_for(band, rare_multipliers)
            if promo:
                weight *= multiplier_for(band, promo_multipliers)
            if weight > 0:
                weighted_bands.append((members, weight))
        if not weighted_bands:
            return rng.choice(candidates)
        total = sum(weight for _, weight in weighted_bands)
        pick = rng.random() * total
        chosen_members = weighted_bands[-1][0]
        for members, weight in weighted_bands:
            pick -= weight
            if pick <= 0:
                chosen_members = members
                break
        return rng.choice(chosen_members)

    @staticmethod
    def _weighted_special_player(
        rng: random.Random, *, quality: str, excluded_resources: set[int] | None = None,
        max_rating: int | None = None
    ) -> dict[str, Any] | None:
        """Draw a normal-mode special by *card family* first, then by rating.

        BETA 2.25.0 weighted every individual special row directly. Because the
        catalogue contains hundreds of IFs but only 10 TOTYs / 33 MOTMs, that
        made the visible pack pool look like an IF-only pool even though the
        blue, orange, green and TOTY rows were technically eligible. 2.25.8
        gives each family an explicit share and only then chooses a card inside
        that family. This preserves the hard two-special-per-pack cap.
        """
        excluded_resources = excluded_resources or set()
        quality_key = str(quality).lower()
        candidates = [
            player for player in NORMAL_SPECIAL_PLAYER_CATALOG
            if str(player.get("quality", LocalIdentityStore._quality_for_rating(int(player.get("rating", 0))))) == quality_key
            and int(player.get("resourceId", 0)) not in excluded_resources
            and (max_rating is None or int(player.get("rating", 0)) <= int(max_rating))
        ]
        if not candidates:
            return None

        configured = PACK_WEIGHTS_DOCUMENT.get("specialTypeWeights", {})
        family_weights = configured.get(quality_key, {}) if isinstance(configured, dict) else {}
        if not isinstance(family_weights, dict) or not family_weights:
            family_weights = {
                "goldif": 42.0, "goldblue": 28.0, "motm": 14.0,
                "toty": 8.0, "green": 6.0, "special": 2.0,
            } if quality_key == "gold" else (
                {"silverif": 70.0, "silverblue": 25.0, "motm": 4.0, "green": 1.0}
                if quality_key == "silver" else
                {"bronzeif": 75.0, "bronzeblue": 24.0, "motm": 1.0}
            )

        by_family: dict[str, list[dict[str, Any]]] = {}
        for player in candidates:
            family = str(player.get("cardType", "special")).lower()
            by_family.setdefault(family, []).append(player)
        available = [(family, float(weight)) for family, weight in family_weights.items()
                     if float(weight) > 0 and by_family.get(str(family).lower())]
        if not available:
            available = [(family, 1.0) for family in sorted(by_family)]
        family_names = [str(family).lower() for family, _ in available]
        chosen_family = rng.choices(family_names, weights=[weight for _, weight in available], k=1)[0]
        family_candidates = by_family[chosen_family]

        def rating_weight(player: dict[str, Any]) -> float:
            rating = int(player.get("rating", 0) or 0)
            return (
                1.00 if rating <= 79 else
                0.80 if rating <= 83 else
                0.60 if rating <= 86 else
                0.38 if rating <= 89 else
                0.18 if rating <= 92 else
                0.09
            )
        return rng.choices(family_candidates, weights=[rating_weight(p) for p in family_candidates], k=1)[0]

    @staticmethod
    def _weighted_legend(
        rng: random.Random, *, excluded_resources: set[int] | None = None, max_rating: int | None = None
    ) -> dict[str, Any] | None:
        excluded_resources = excluded_resources or set()
        candidates = [
            p for p in LEGEND_PLAYER_CATALOG
            if int(p.get("resourceId", 0)) not in excluded_resources
            and (max_rating is None or int(p.get("rating", 0)) <= int(max_rating))
        ]
        return rng.choice(candidates) if candidates else None

    def _local_pack_player_payload(
        self, *, item_id: int, player: dict[str, Any], quality: str, rare: bool
    ) -> dict[str, Any]:
        requested_quality = str(quality).lower()
        actual_quality = str(player.get("quality", self._quality_for_rating(int(player["rating"]))))
        if requested_quality != actual_quality:
            raise RuntimeError(
                f"pack tier mismatch: requested {requested_quality}, got {actual_quality} assetId={player.get('assetId')}"
            )
        asset_id = int(player["assetId"])
        version = int(player.get("version", 1))
        expected_resource = asset_id if version == 1 else definition_id_for(asset_id, version)
        initial = dict(player)
        initial.update({
            "untradeable": False,
            "tradeable": True,
            "contract": 7,
            "fitness": 99,
            "morale": 99,
            "formation": "f442",
            "pile": 6,
            "discardValue": self._player_discard_value(player),
            "localPackSchema": PACK_FIDELITY_SCHEMA,
        })
        payload = self._canonical_player_payload(
            item_id=int(item_id), asset_id=asset_id, existing=initial, pile=6
        )
        # Preserve backend markers/explicit tradeability after canonicalization.
        payload["untradeable"] = False
        payload["tradeable"] = True
        payload["discardValue"] = self._player_discard_value(player)
        payload["localPackSchema"] = PACK_FIDELITY_SCHEMA
        if bool(player.get("specialCard")) or int(player.get("rareFlag", 0)) > 1:
            payload["specialCard"] = True
            payload["cardType"] = str(player.get("cardType", "special"))
            payload["version"] = version
        if int(payload.get("resourceId", expected_resource)) != expected_resource:
            payload["resourceId"] = expected_resource
        return payload

    @staticmethod
    def _consumable_category_weights(quality: str) -> dict[str, float]:
        if str(quality).lower() == "gold":
            return {
                "Contract": 27.0, "Fitness": 18.0, "Healing": 17.0,
                "Training": 8.0, "GK Training": 8.0,
                "Positioning": 10.0, "Chemistry Style": 9.0, "Manager League": 3.0,
            }
        return {
            "Contract": 30.0, "Fitness": 22.0, "Healing": 22.0,
            "Training": 13.0, "GK Training": 13.0,
        }

    @classmethod
    def _weighted_consumable(cls, rng: random.Random, *, quality: str, rare_slot: bool) -> dict[str, Any]:
        quality = str(quality).lower()
        pool = [
            row for row in CONSUMABLE_CATALOG
            if str(row.get("quality", "")).lower() == quality and bool(row.get("packEligible", True))
        ]
        if not pool:
            raise RuntimeError(f"no {quality} consumables in local catalogue")
        rarity_pool = [row for row in pool if (int(row.get("rareFlag", 0)) > 0) == bool(rare_slot)]
        if rarity_pool:
            pool = rarity_pool
        categories = cls._consumable_category_weights(quality)
        available = [name for name in categories if any(str(row.get("category")) == name for row in pool)]
        chosen_category = rng.choices(available, weights=[categories[name] for name in available], k=1)[0]
        candidates = [row for row in pool if str(row.get("category")) == chosen_category]
        return rng.choice(candidates)

    def _local_consumable_payload(self, *, item_id: int, consumable: dict[str, Any]) -> dict[str, Any]:
        # v2.40.17: keep pack consumables on the narrow retail wire contract.
        # The v2.40.16 second-pack freeze was captured inside the CardsDLL
        # purchased-items parser. The frozen pack was the first successful run
        # containing a Positioning row whose local-only ``kind`` value included
        # a Unicode arrow (for example ``RW\u2192RF``). Those descriptive catalogue
        # fields are useful to the generator but are not required by the retail
        # item parser, so do not emit them over the wire.
        wire_keys = (
            "resourceId", "cardassetid", "cardsubtypeid", "rating",
            "rareFlag", "rareflag", "bronze", "silver", "gold", "amount",
            "itemType", "resourceGameYear", "discardValue",
        )
        payload = {key: consumable[key] for key in wire_keys if key in consumable}
        payload.update({
            "id": int(item_id), "itemId": int(item_id), "timestamp": int(time.time()),
            "lastSalePrice": 0, "owners": 1, "untradeable": False, "tradeable": True,
            "itemState": "free", "pile": 6, "resourceGameYear": 2014,
        })
        # Client payloads historically use both spellings in different parsers.
        payload["rareflag"] = int(payload.get("rareFlag", payload.get("rareflag", 0)))
        payload["rareFlag"] = payload["rareflag"]
        payload["discardValue"] = max(0, int(payload.get("discardValue", 0)))
        return payload

    @staticmethod
    def _consumable_filter_category(definition: dict[str, Any]) -> str:
        category = str(definition.get("category", "")).strip()
        mapping = {
            "contract": "contract", "contracts": "contract",
            "fitness": "fitness", "healing": "healing",
            "training": "training", "playertraining": "training",
            "gktraining": "gktraining", "goalkeepertraining": "gktraining",
            "position": "position", "positioning": "position",
            "playstyle": "playstyle", "chemistry": "playstyle", "chemistrystyle": "playstyle",
            "managerleague": "managerleague", "managerleaguemodifier": "managerleague", "league": "managerleague",
        }
        return mapping.get(category.replace("_", "").replace("-", "").replace(" ", "").casefold(), "")

    @staticmethod
    def _consumable_catalog_filter_key(definition: dict[str, Any]) -> str:
        category = str(definition.get("category", "")).casefold()
        if category == "contract": return "contract"
        if category == "fitness": return "fitness"
        if category == "healing": return "healing"
        if category == "training": return "training"
        if category == "gk training": return "gktraining"
        if category == "positioning": return "position"
        if category == "chemistry style": return "playstyle"
        if category == "manager league": return "managerleague"
        return ""

    @staticmethod
    def _injury_matches_kind(injury_type: str, kind: str) -> bool:
        if str(kind).casefold() == "all":
            return True
        injury = re.sub(r"[^a-z]", "", str(injury_type or "").casefold())
        wanted = re.sub(r"[^a-z]", "", str(kind or "").casefold())
        aliases = {
            "upperbody": {"upperbody", "back", "torso", "shoulder", "chest"},
            "head": {"head"}, "arm": {"arm", "elbow", "hand", "wrist"},
            "knee": {"knee"}, "leg": {"leg", "thigh", "calf", "hamstring"},
            "foot": {"foot", "ankle", "toe"},
        }
        return any(token in injury for token in aliases.get(wanted, {wanted}) if token)

    def _owned_consumable_row_locked(
        self, connection: sqlite3.Connection, persona_id: int, resource_id: int
    ) -> sqlite3.Row | None:
        rows = connection.execute(
            "SELECT * FROM items WHERE persona_id=? AND item_type IN ('development','training') ORDER BY item_id",
            (int(persona_id),),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
                owned_resource = int(payload.get("resourceId", 0)) if isinstance(payload, dict) else 0
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if owned_resource == int(resource_id):
                return row
        return None

    def _save_player_payload_locked(
        self, connection: sqlite3.Connection, persona_id: int, row: sqlite3.Row, payload: dict[str, Any]
    ) -> dict[str, Any]:
        canonical = self._canonical_player_payload(
            item_id=int(row["item_id"]), asset_id=int(row["asset_id"]), existing=payload, pile=7,
        )
        connection.execute(
            "UPDATE items SET payload=? WHERE persona_id=? AND item_id=?",
            (json.dumps(canonical, separators=(",", ":"), ensure_ascii=False),
             int(persona_id), int(row["item_id"])),
        )
        for squad_row in connection.execute(
            "SELECT squad_id,slot_index FROM squad_players WHERE item_id=?", (int(row["item_id"]),)
        ).fetchall():
            connection.execute(
                "UPDATE squad_players SET resource_id=?,team_id=?,rating=?,rare_flag=?,play_style=?,preferred_position=?,attributes_json=? "
                "WHERE squad_id=? AND slot_index=?",
                (int(canonical.get("resourceId", row["asset_id"])), int(canonical.get("teamid", 0)),
                 int(canonical.get("rating", 0)), int(canonical.get("rareflag", 0)), int(canonical.get("playStyle", 0)),
                 str(canonical.get("preferredPosition", "CM")),
                 json.dumps(canonical.get("attributeArray", []), separators=(",", ":")),
                 int(squad_row["squad_id"]), int(squad_row["slot_index"])),
            )
        return canonical

    def _active_squad_player_ids_locked(self, connection: sqlite3.Connection, persona_id: int) -> list[int]:
        user = connection.execute(
            "SELECT active_squad_id FROM fut_users WHERE persona_id=?", (int(persona_id),)
        ).fetchone()
        if user is None or user["active_squad_id"] is None:
            return []
        return [
            int(row["item_id"]) for row in connection.execute(
                "SELECT item_id FROM squad_players WHERE squad_id=? AND item_id>0 ORDER BY slot_index",
                (int(user["active_squad_id"]),),
            ).fetchall()
        ]

    def apply_consumable(self, resource_id: int, target_item_ids: list[int]) -> dict[str, Any]:
        """Apply one owned consumable using FUT's /item/resource/<resourceId> contract.

        The request identifies the consumable definition, not a unique consumable item instance.
        We consume one owned matching row only after the target validation and state mutation succeed.
        """
        resource_id = int(resource_id)
        definition = CONSUMABLE_BY_RESOURCE.get(resource_id)
        if definition is None:
            raise ValueError(f"unknown consumable resourceId {resource_id}")
        if not bool(definition.get("applicationSupported", True)):
            raise ValueError(f"consumable resourceId {resource_id} has no proven FIFA 14 application mapping")
        targets: list[int] = []
        for value in target_item_ids or []:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0 and number not in targets:
                targets.append(number)
        if not targets:
            raise ValueError("consumable application requires a target item id")

        with self._lock, closing(self._connect()) as connection, connection:
            persona_id = int(self._identity(connection)["persona_id"])
            consumable_row = self._owned_consumable_row_locked(connection, persona_id, resource_id)
            if consumable_row is None:
                raise ValueError(f"consumable resourceId {resource_id} is not owned")

            category = str(definition.get("category", ""))
            kind = str(definition.get("kind", ""))
            amount = max(0, int(definition.get("amount", 0) or 0))
            changed: list[dict[str, Any]] = []
            effect = ""

            def player_row(item_id: int) -> sqlite3.Row:
                row = connection.execute(
                    "SELECT * FROM items WHERE persona_id=? AND item_id=?", (persona_id, int(item_id))
                ).fetchone()
                if row is None or str(row["item_type"]).lower() != PLAYER_ITEM_TYPE:
                    raise ValueError("consumable target is not an owned player")
                return row

            if category == "Fitness" and kind.casefold() == "squad":
                squad_ids = self._active_squad_player_ids_locked(connection, persona_id)
                if not squad_ids:
                    raise ValueError("no active squad available for squad fitness")
                for item_id in squad_ids:
                    row = player_row(item_id)
                    payload = json.loads(row["payload"] or "{}")
                    payload["fitness"] = min(99, max(0, int(payload.get("fitness", 0))) + amount)
                    changed.append(self._save_player_payload_locked(connection, persona_id, row, payload))
                effect = f"squad fitness +{amount}"
            elif category in {"Contract", "Manager League"} and str(definition.get("class", "")).casefold() == "manager":
                row = connection.execute(
                    "SELECT * FROM items WHERE persona_id=? AND item_id=?", (persona_id, int(targets[0]))
                ).fetchone()
                if row is None or str(row["item_type"]).lower() not in {"manager", "staff"}:
                    raise ValueError("manager consumable target is not an owned manager")
                payload = json.loads(row["payload"] or "{}")
                if category == "Contract":
                    target_quality = self._quality_for_rating(int(payload.get("rating", 75) or 75))
                    gain = max(0, int(definition.get(target_quality, amount) or 0))
                    payload["contract"] = min(99, max(0, int(payload.get("contract", 0))) + gain)
                    effect = f"manager contract +{gain}"
                else:
                    payload["leagueId"] = amount
                    effect = f"manager league {amount}"
                connection.execute(
                    "UPDATE items SET payload=? WHERE persona_id=? AND item_id=?",
                    (json.dumps(payload, separators=(",", ":"), ensure_ascii=False), persona_id, int(row["item_id"])),
                )
                changed.append(payload)
            else:
                row = player_row(targets[0])
                try:
                    payload = json.loads(row["payload"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                position = str(payload.get("preferredPosition", "CM")).upper()

                if category == "Contract":
                    target_quality = self._quality_for_rating(int(payload.get("rating", 50) or 50))
                    gain = max(0, int(definition.get(target_quality, amount) or 0))
                    payload["contract"] = min(99, max(0, int(payload.get("contract", 0))) + gain)
                    effect = f"player contract +{gain}"
                elif category == "Fitness":
                    payload["fitness"] = min(99, max(0, int(payload.get("fitness", 0))) + amount)
                    effect = f"player fitness +{amount}"
                elif category == "Healing":
                    games = max(0, int(payload.get("injuryGames", 0) or 0))
                    injury = str(payload.get("injuryType", "none"))
                    if games <= 0:
                        raise ValueError("player is not injured")
                    if not self._injury_matches_kind(injury, kind):
                        raise ValueError(f"{kind} healing card does not match injury type {injury}")
                    remaining = max(0, games - amount)
                    payload["injuryGames"] = remaining
                    if remaining == 0:
                        payload["injuryType"] = "none"
                    effect = f"healing -{amount} match(es)"
                elif category == "Positioning":
                    match = re.fullmatch(r"([A-Z]+)[→>-]+([A-Z]+)", kind.upper())
                    if not match:
                        raise ValueError("position card has no valid transition")
                    old_pos, new_pos = match.group(1), match.group(2)
                    if position != old_pos:
                        raise ValueError(f"position card requires {old_pos}, target is {position}")
                    payload["preferredPosition"] = new_pos
                    effect = f"position {old_pos}->{new_pos}"
                elif category == "Chemistry Style":
                    if position == "GK":
                        raise ValueError("outfield chemistry style cannot be applied to a goalkeeper")
                    payload["playStyle"] = amount
                    effect = f"chemistry style {kind}"
                elif category in {"Training", "GK Training"}:
                    is_gk = position == "GK"
                    if category == "GK Training" and not is_gk:
                        raise ValueError("goalkeeper training requires a goalkeeper")
                    if category == "Training" and is_gk:
                        raise ValueError("outfield training cannot be applied to a goalkeeper")
                    previous = connection.execute(
                        "SELECT base_payload_json FROM consumable_effects WHERE persona_id=? AND item_id=? AND effect_type='training'",
                        (persona_id, int(row["item_id"])),
                    ).fetchone()
                    if previous is not None:
                        try:
                            base_payload = json.loads(previous["base_payload_json"] or "{}")
                        except (TypeError, json.JSONDecodeError):
                            base_payload = {}
                        if isinstance(base_payload, dict):
                            payload["attributeArray"] = self._array_values(
                                base_payload.get("attributeArray", base_payload.get("attributeList", [])), PLAYER_ATTRIBUTE_COUNT
                            )
                            payload["attributeList"] = [
                                {"index": i, "value": value} for i, value in enumerate(payload["attributeArray"])
                            ]
                    base_payload = self._canonical_player_payload(
                        item_id=int(row["item_id"]), asset_id=int(row["asset_id"]), existing=payload, pile=7,
                    )
                    attrs = self._array_values(base_payload.get("attributeArray", []), PLAYER_ATTRIBUTE_COUNT)
                    if category == "GK Training":
                        index_map = {"DIV": 0, "HAN": 1, "KIC": 2, "REF": 3, "SPD": 4, "POS": 5}
                    else:
                        # FIFA 14 face-stat order is PAC, SHO, PAS, DRI, DEF, HEA/PHY.
                        index_map = {"PAC": 0, "SHO": 1, "PAS": 2, "DRI": 3, "DEF": 4, "PHY": 5, "HEA": 5}
                    if kind.upper() == "ALL":
                        indexes = range(PLAYER_ATTRIBUTE_COUNT)
                    elif kind.upper() in index_map:
                        indexes = (index_map[kind.upper()],)
                    else:
                        raise ValueError(f"unsupported training subtype {kind}")
                    for idx in indexes:
                        attrs[idx] = min(99, max(0, int(attrs[idx])) + amount)
                    connection.execute(
                        "INSERT OR REPLACE INTO consumable_effects "
                        "(persona_id,item_id,effect_type,resource_id,base_payload_json,created_at) VALUES (?,?,?,?,?,?)",
                        (persona_id, int(row["item_id"]), "training", resource_id,
                         json.dumps(base_payload, separators=(",", ":"), ensure_ascii=False), int(time.time())),
                    )
                    payload = dict(base_payload)
                    payload["attributeArray"] = attrs
                    payload["attributeList"] = [{"index": i, "value": value} for i, value in enumerate(attrs)]
                    payload["training"] = max(1, int(definition.get("cardsubtypeid", 1) or 1))
                    effect = f"{category.lower()} {kind} +{amount} for next match"
                else:
                    raise ValueError(f"unsupported consumable category {category}")
                changed.append(self._save_player_payload_locked(connection, persona_id, row, payload))

            # Consume exactly one matching card only after every validation/write above succeeded.
            connection.execute(
                "DELETE FROM items WHERE persona_id=? AND item_id=?", (persona_id, int(consumable_row["item_id"]))
            )
            return {
                "success": True,
                "resourceId": resource_id,
                "consumedItemId": int(consumable_row["item_id"]),
                "effect": effect,
                "itemData": changed,
            }

    def _generate_pack_contents_locked(
        self, connection: sqlite3.Connection, *, pack_id: int, definition: dict[str, Any]
    ) -> list[dict[str, Any]]:
        count = int(definition.get("totalCards", 12))
        rare_count = min(count, int(definition.get("rareCards", 0)))
        quality = str(definition.get("minQuality", "gold")).lower()
        promo = not bool(definition.get("regular", True))
        player_slots = max(0, min(count, int(definition.get("playerSlots", count))))
        seed = f"{PACK_WEIGHTS_DOCUMENT.get('seed','FIFA14')}:{pack_id}:{definition.get('packType')}"
        rng = random.Random(seed)

        # Rares apply to the entire pack, not just the player positions.
        rare_indices = set(rng.sample(range(count), k=rare_count)) if rare_count else set()
        slot_kinds = ["player"] * player_slots + ["consumable"] * (count - player_slots)
        rng.shuffle(slot_kinds)

        used_assets: set[int] = set()
        used_resources: set[int] = set()
        items: list[dict[str, Any]] = []

        # v2.40.14: specials are chosen as an explicit per-pack jackpot instead
        # of a second tiny roll that only happened when a player randomly landed
        # in one of the pack's rare item positions.  The old implementation made
        # a Premium Gold special effectively ~0.56% per pack even though the
        # configured gold value looked like 0.75% per player.
        pack_type = int(definition.get("packType", 0))
        player_indices = [index for index, kind in enumerate(slot_kinds) if kind == "player"]
        rare_player_indices = [index for index in player_indices if index in rare_indices]

        def ensure_rare_player_slot() -> int | None:
            if not player_indices or rare_count <= 0:
                return None
            if rare_player_indices:
                return rng.choice(rare_player_indices)
            # Preserve the advertised total rare count: move one rare marker
            # from a consumable slot onto the selected player slot.
            player_index = rng.choice(player_indices)
            donor_indices = [index for index in rare_indices if index not in player_indices]
            if donor_indices:
                rare_indices.remove(rng.choice(donor_indices))
                rare_indices.add(player_index)
                rare_player_indices.append(player_index)
                return player_index
            return None

        special_targets: set[int] = set()
        legend_target: int | None = None
        special_table = PACK_WEIGHTS_DOCUMENT.get("specialChancePerPack", {})
        second_table = PACK_WEIGHTS_DOCUMENT.get("secondSpecialChanceGivenFirst", {})
        special_chance = max(0.0, min(1.0, float(special_table.get(str(pack_type), 0.0))))
        second_chance = max(0.0, min(1.0, float(second_table.get(str(pack_type), 0.0))))
        max_specials = max(0, min(2, int(PACK_WEIGHTS_DOCUMENT.get("maxSpecialsPerPack", 2))))
        legend_table = PACK_WEIGHTS_DOCUMENT.get("legendChancePerPack", {})
        # Keep the configured table loaded for diagnostics, but force the live
        # PC chance to zero until Legend client identity/art rendering is proven.
        legend_chance = 0.0 if legend_table is not None else 0.0
        if legend_chance > 0.0 and rng.random() < legend_chance:
            legend_target = ensure_rare_player_slot()
        elif special_chance > 0.0 and rng.random() < special_chance and max_specials > 0:
            first = ensure_rare_player_slot()
            if first is not None:
                special_targets.add(first)
                # A second special is conditional on already hitting one and is
                # intentionally much rarer. Never allow more than two.
                if max_specials >= 2 and second_chance > 0.0 and rng.random() < second_chance:
                    available = [idx for idx in player_indices if idx not in special_targets]
                    if available:
                        second = rng.choice(available)
                        if second not in rare_indices:
                            donors = [idx for idx in rare_indices if idx not in player_indices]
                            if donors:
                                rare_indices.remove(rng.choice(donors)); rare_indices.add(second)
                            elif rare_count > len([idx for idx in rare_indices if idx in player_indices]):
                                rare_indices.add(second)
                        # Specials are rare cards; only admit the second if its
                        # player slot can carry a rare marker without increasing
                        # the pack's advertised total rare count.
                        if second in rare_indices:
                            special_targets.add(second)

        max_elites = max(1, int(PACK_WEIGHTS_DOCUMENT.get("regularPackMax90Plus", 2))) if not promo else 2
        elite_count = 0
        jackpot_targets = set(special_targets)
        if legend_target is not None:
            jackpot_targets.add(legend_target)

        for source_ordinal, kind in enumerate(slot_kinds):
            rare = source_ordinal in rare_indices
            item_id = 180_000_000_000 + int(pack_id) * 100 + source_ordinal + 1
            if kind == "player":
                player: dict[str, Any] | None = None
                jackpot_pending = any(source_ordinal < int(target) for target in jackpot_targets)
                # Reserve one elite slot for a pending special/Legend jackpot.
                base_elite_limit = max_elites - 1 if jackpot_pending else max_elites
                base_max_rating = 89 if elite_count >= max(0, base_elite_limit) else None

                if legend_target == source_ordinal:
                    player = self._weighted_legend(
                        rng, excluded_resources=used_resources,
                        max_rating=89 if elite_count >= max_elites else None,
                    )
                elif source_ordinal in special_targets:
                    player = self._weighted_special_player(
                        rng, quality=quality, excluded_resources=used_resources,
                        max_rating=89 if elite_count >= max_elites else None,
                    )
                if player is None:
                    player = self._weighted_player(
                        rng, quality=quality, rare_slot=rare, promo=promo,
                        excluded_assets=used_assets, max_rating=base_max_rating,
                    )
                resource = int(player.get("resourceId", player.get("assetId", 0)))
                # Avoid the same exact card twice in one pack.
                attempts = 0
                while resource in used_resources and attempts < 20:
                    attempts += 1
                    fallback_max = 89 if elite_count >= max_elites else None
                    player = self._weighted_player(
                        rng, quality=quality, rare_slot=rare, promo=promo,
                        excluded_assets=used_assets, max_rating=fallback_max,
                    )
                    resource = int(player.get("resourceId", player.get("assetId", 0)))
                used_assets.add(int(player.get("assetId", 0)))
                used_resources.add(resource)
                if int(player.get("rating", 0)) >= 90:
                    elite_count += 1
                payload = self._local_pack_player_payload(item_id=item_id, player=player, quality=quality, rare=rare)
            else:
                consumable = self._weighted_consumable(rng, quality=quality, rare_slot=rare)
                payload = self._local_consumable_payload(item_id=item_id, consumable=consumable)
            items.append(payload)

        # FIFA's pack reveal consumes the leading player identity as the hero.
        # Put the highest-rated player first, then the remaining players, then
        # consumables. Ties favor special/rare cards.
        def reveal_key(payload: dict[str, Any]) -> tuple[int, int, int, int]:
            is_player = str(payload.get("itemType", "")).lower() == PLAYER_ITEM_TYPE
            return (
                0 if is_player else 1,
                -int(payload.get("rating", 0)) if is_player else 0,
                -int(payload.get("rareflag", payload.get("rareFlag", 0))) if is_player else 0,
                int(payload.get("id", 0)),
            )
        items.sort(key=reveal_key)
        for ordinal, payload in enumerate(items):
            connection.execute(
                "INSERT OR REPLACE INTO pack_contents (pack_id, ordinal, payload) VALUES (?, ?, ?)",
                (int(pack_id), ordinal, json.dumps(payload, separators=(",", ":"), ensure_ascii=False)),
            )
        return items

    def store_pack_types(self) -> dict[str, Any]:
        """Return the native FIFA 14 FutStoreGetPackTypesServerResponse shape.

        Static validation against the retail CardsDLLzf.dll parser shows that
        this response consumes exactly two top-level fields: ``purchase`` (an
        array of offers) and ``timestamp``.  Older local builds exposed
        ``packList``/``packTypes`` aliases only, so the retail parser skipped the
        actual offers and the Store could render with no purchasable content.

        The nested offer contract is kept deliberately conservative: only keys
        whose native parser/type is known are emitted.  In particular,
        ``displayGroup`` is an object and ``currencies`` contains native
        ``name``/``funds``/``finalFunds`` objects.  ``extPrice`` is omitted
        because it is a nested external-money structure and is unnecessary for
        local coin/FIFA-point purchases.
        """
        native_offers: list[dict[str, Any]] = []
        compatibility_entries: list[dict[str, Any]] = []
        now = int(time.time())
        for priority, definition in enumerate(PACK_CATALOG_DOCUMENT.get("packs", []), start=1):
            coins = int(definition.get("priceCoins", 0))
            points = int(definition.get("pricePoints", 0))
            pack_type = int(definition["packType"])
            pack_id = int(definition.get("packId", pack_type))
            total = int(definition.get("totalCards", 12))
            rare = int(definition.get("rareCards", 0))
            category = str(definition.get("category", "GOLD")).upper()
            min_quality = str(definition.get("minQuality", category)).lower()
            tier = "bronze" if min_quality == "bronze" else "silver" if min_quality == "silver" else "gold"
            # BETA 2.2 PC evidence resolved the Store regression precisely:
            # ASSET_ID 4 is the old Season Ticket promotion, while the attempted
            # 6..11 range produces the retail bright-green NOT FOUND fallback.
            # Never make that survey the default again.  The normal launcher
            # therefore uses the proven 1/2/3 bronze/silver/gold assets for a
            # fully usable Store while the read-only archive scanner collects
            # the APT override-path evidence needed for authentic SKU artwork.
            tier_asset = {"bronze": 1, "silver": 2, "gold": 3}[tier]
            survey_asset_by_pack_type = {
                # Diagnostic-only reproduction of the BETA 2.2 survey.  Keep it
                # available behind an explicit environment variable so the same
                # exact experiment can be reproduced, but fail closed to tier art
                # for every unknown mode.
                1: 1,
                2: 1,
                3: 2,
                4: 2,
                5: 3,
                6: 3,
                101: 4,
                102: 6,
                103: 7,
                104: 8,
                105: 9,
                106: 10,
                107: 11,
            }
            art_mode = os.environ.get("FIFA14_STORE_ART_MODE", "tier").strip().lower()
            if art_mode == "survey":
                art_asset = survey_asset_by_pack_type.get(pack_type, tier_asset)
            elif art_mode == "season-ticket" and category == "PROMO":
                art_asset = 4
            else:
                art_asset = tier_asset
            group_priority = tier_asset
            name = str(definition.get("name", f"Local Pack {pack_type}"))
            name_token = f"LOCAL_PACK_NAME_{pack_type}"
            description_token = f"FUT_STORE_PACK_{pack_id}_DESC"

            # Exact keys consumed by FutStoreGetPackTypesServerResponse's offer
            # parser.  The v2.40.9 retail Store APT capture proves that this
            # field is exposed as mPurchaseItem.POST_PURCHASE_ACTION and that
            # the normal pack branch recognises CREATEPACK (alongside the
            # non-pack REFRESHSTORE/REFRESHBALANCE actions).  BUY_PACK was a
            # local invention and prevented the frontend from reaching the
            # native PurchasePack wrapper at all.
            native_offer = {
                "actionType": "CREATEPACK",
                "assetId": art_asset,
                "bonus": 0,
                # FIFA 14's Store-offer parser compares these names against
                # lowercase legacy literals.  This is intentionally separate
                # from /user/credits, whose working top-bar response stays uppercase.
                "currencies": [
                    # FIFA 14 CardsDLL compares these offer-currency names to
                    # the lowercase literals "coins" and "points".
                    {"name": "coins", "funds": coins, "finalFunds": coins},
                    {"name": "points", "funds": points, "finalFunds": points},
                ],
                # Retail Store treats both name and description as localization keys.
                # Supplying literal prose here resolves to the frontend's "*" missing-text marker.
                "name": name_token,
                # FIFA treats this member as a localization key, not literal
                # prose. The corresponding locstring is served by the existing
                # storepackdescriptions endpoint.
                "description": description_token,
                "nameToken": name_token,
                "descriptionToken": description_token,
                "displayGroup": {"priority": group_priority, "value": tier},
                "displayGroupAssetId": tier_asset,
                "displayGroupUseDefaultImage": True,
                # Keep the offer inside an explicit valid retail time window.
                "end": min(2_147_483_647, now + (86400 * 3650)),
                "firstPartyStoreId": "0",
                "id": pack_type,
                "isPremium": "PREMIUM" in name.upper(),
                "isSeasonTicketDiscount": False,
                "points": points,
                "priority": priority,
                # These are part of the retail purchase-offer identity.  The
                # numeric pack SKU remains in packId/packType while the kind of
                # purchase is the enum literal CARDPACK.
                "packId": pack_id,
                "packType": pack_type,
                "purchasePackType": "CARDPACK",
                "purchaseCount": 0,
                # A high finite limit avoids ambiguous zero-limit handling in
                # the native Store availability path while remaining effectively
                # unlimited for this local/offline server.
                "purchaseLimit": 999999,
                "quantity": total,
                "saleType": "NONE",
                "sortPriority": priority,
                "start": max(1, now - 86400),
                "state": "active",
                "unopened": False,
                "useDefaultImage": True,
                "visible": True,
            }
            # Retail deal enums are uppercase. Regular packs do not need
            # this optional field; promotional packs advertise PROMO.
            if category == "PROMO":
                # FIFA 14's parser compares the legacy lowercase enum.
                native_offer["dealType"] = "promo"
            native_offers.append(native_offer)

            # Retain non-native aliases for diagnostics/backward compatibility;
            # CardsDLL ignores unknown fields at this level.
            compatibility_entries.append(
                {
                    **native_offer,
                    "packId": pack_id,
                    "packType": pack_type,
                    "purchasePackType": "CARDPACK",
                    "name": name_token,
                    "nameText": name,
                    "descriptionText": str(definition.get("description", "")),
                    "category": category,
                    "price": coins,
                    "priceCoins": coins,
                    "pricePoints": points,
                    "rarePlayers": rare,
                    "playersBronze": int(definition.get("playerSlots", 0)) if category == "BRONZE" else 0,
                    "playersSilver": int(definition.get("playerSlots", 0)) if category == "SILVER" else 0,
                    "playersGold": int(definition.get("playerSlots", 0)) if category in {"GOLD", "PROMO"} else 0,
                }
            )

        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            unopened = int(connection.execute(
                "SELECT COUNT(*) FROM packs WHERE persona_id = ? AND unopened = 1",
                (identity["persona_id"],),
            ).fetchone()[0])
            club = connection.execute(
                "SELECT coins, fifa_points FROM clubs WHERE persona_id = ?",
                (identity["persona_id"],),
            ).fetchone()
            credits = 0 if club is None else int(club["coins"])
            fifa_points = 0 if club is None else int(club["fifa_points"])

        return {
            # Native FutStoreGetPackTypesServerResponse contract.
            "purchase": native_offers,
            "timestamp": now,
            # Compatibility/diagnostic aliases ignored by the native parser.
            "packList": compatibility_entries,
            "packTypes": compatibility_entries,
            "total": len(native_offers),
            "unopenedPacks": unopened,
            "credits": credits,
            "fifaPoints": fifa_points,
        }

    @staticmethod
    def _duplicate_item_id_list_locked(
        connection: sqlite3.Connection, persona_id: int, items: list[dict[str, Any]]
    ) -> list[dict[str, int]]:
        # Retail FUT does not return a bare list of new duplicate item IDs. Each
        # entry pairs the newly-opened item with the already-owned My Club item:
        #   {"itemId": <new>, "duplicateItemId": <owned>}
        # The PC NewItemsHelper uses duplicateItemId to bind/mark the card. A bare
        # itemId (v2.40.12-v2.40.17) was detected server-side but rendered as a
        # normal item, causing Send All to Club to attempt the duplicate and hit
        # error 472 afterwards.
        owned_by_resource: dict[int, int] = {}
        for row in connection.execute(
            "SELECT item_id, payload FROM items WHERE persona_id = ? AND item_type = ? "
            "AND pile NOT IN ('trade','pending') ORDER BY item_id",
            (int(persona_id), PLAYER_ITEM_TYPE),
        ).fetchall():
            try:
                # Normal store connections use sqlite3.Row, but keep this helper
                # tolerant of plain tuple rows so verifier/maintenance callers
                # cannot silently lose duplicate pairing.
                if isinstance(row, sqlite3.Row):
                    owned_item_id = int(row["item_id"])
                    raw_payload = row["payload"]
                else:
                    owned_item_id = int(row[0])
                    raw_payload = row[1]
                payload = json.loads(raw_payload or "{}")
                if not isinstance(payload, dict):
                    continue
                resource_id = int(payload.get("resourceId", payload.get("assetId", 0)))
                if resource_id > 0 and resource_id not in owned_by_resource:
                    owned_by_resource[resource_id] = owned_item_id
            except (TypeError, ValueError, json.JSONDecodeError, IndexError, KeyError):
                continue
        duplicates: list[dict[str, int]] = []
        for payload in items:
            if str(payload.get("itemType", "")).lower() != PLAYER_ITEM_TYPE:
                continue
            try:
                resource_id = int(payload.get("resourceId", payload.get("assetId", 0)))
                item_id = int(payload.get("id", payload.get("itemId")))
            except (TypeError, ValueError):
                continue
            duplicate_item_id = owned_by_resource.get(resource_id)
            if duplicate_item_id is not None and int(duplicate_item_id) != item_id:
                duplicates.append({"itemId": item_id, "duplicateItemId": int(duplicate_item_id)})
        return duplicates

    @staticmethod
    def _pack_purchase_response_document(
        *,
        definition: dict[str, Any],
        pack_type: int,
        transaction_id: int,
        items: list[dict[str, Any]],
        credits: int,
        fifa_points: int,
        unopened_packs: int,
        duplicate_item_ids: list[dict[str, int]] | None = None,
    ) -> dict[str, Any]:
        """Build the retail FIFA 14 purchase + create-pack response contracts."""
        catalog_pack_id = int(definition.get("packId", pack_type))
        duplicate_item_ids = list(duplicate_item_ids or [])
        create_pack_response = {
            # Exact FutCreatePackServerResponse fields recovered from the
            # retail FIFA 14 CardsDLL parser.
            "duplicateItemIdList": duplicate_item_ids,
            "itemList": items,
            "numberItems": len(items),
            "purchasedPackId": catalog_pack_id,
            # Compatibility aliases used by diagnostics only.
            "itemData": items,
            "packId": catalog_pack_id,
            "packType": int(pack_type),
        }
        return {
            # Exact FutPurchaseItemsServerResponse fields.
            "packId": catalog_pack_id,
            "firstPartyStoreId": 0,
            "purchasePackType": "CARDPACK",
            "state": "PURCHASECOMPLETE",
            "transactionId": int(transaction_id),
            "useAuth": 0,
            "useCount": 1,
            "useTime": 0,
            # Compatibility/diagnostic fields.
            "purchasedPackId": catalog_pack_id,
            "purchasePackTypeId": int(pack_type),
            "packType": int(pack_type),
            "credits": int(credits),
            "fifaPoints": int(fifa_points),
            "itemData": items,
            "duplicateItemIdList": duplicate_item_ids,
            "createPackResponse": create_pack_response,
            "unopenedPacks": int(unopened_packs),
        }

    def purchase_pack(self, pack_type: int, *, currency: str = "COINS") -> dict[str, Any]:
        definition = PACK_DEFINITIONS.get(int(pack_type))
        if definition is None:
            raise ValueError(f"unknown local FIFA 14 pack type {pack_type}")
        currency = str(currency or "COINS").upper()
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            club = connection.execute(
                "SELECT coins, fifa_points FROM clubs WHERE persona_id = ?", (identity["persona_id"],)
            ).fetchone()
            if club is None:
                raise ValueError("a FUT club is required before packs can be purchased")
            # Pack reveal is single-pile in the retail PC frontend. Do not allow
            # a second purchase while any New Items remain unresolved; older
            # builds could return 24+ flattened items and hang the animation.
            pending_items = int(connection.execute(
                "SELECT COUNT(*) FROM pack_contents pc JOIN packs p ON p.pack_id=pc.pack_id "
                "WHERE p.persona_id=? AND p.unopened=1",
                (identity["persona_id"],),
            ).fetchone()[0])
            if pending_items > 0:
                raise ValueError(f"resolve the {pending_items} current New Items before opening another pack")
            if currency in {"FIFA_POINTS", "POINTS", "FIFA POINTS"}:
                price = int(definition.get("pricePoints", 0))
                if price <= 0 or int(club["fifa_points"]) < price:
                    raise ValueError("not enough local FIFA Points")
                connection.execute(
                    "UPDATE clubs SET fifa_points = fifa_points - ? WHERE persona_id = ?",
                    (price, identity["persona_id"]),
                )
            else:
                price = int(definition.get("priceCoins", 0))
                if int(club["coins"]) < price:
                    raise ValueError("not enough local FUT coins")
                connection.execute(
                    "UPDATE clubs SET coins = coins - ? WHERE persona_id = ?",
                    (price, identity["persona_id"]),
                )
            cursor = connection.execute(
                "INSERT INTO packs (persona_id, pack_type, pack_name, unopened, created_at) VALUES (?, ?, ?, 1, ?)",
                (identity["persona_id"], int(pack_type), str(definition["name"]), int(time.time())),
            )
            pack_id = int(cursor.lastrowid)
            items = self._generate_pack_contents_locked(connection, pack_id=pack_id, definition=definition)
            balances = connection.execute(
                "SELECT coins, fifa_points FROM clubs WHERE persona_id = ?", (identity["persona_id"],)
            ).fetchone()
            assert balances is not None
            unopened_packs = int(connection.execute(
                "SELECT COUNT(*) FROM packs WHERE persona_id = ? AND unopened = 1",
                (identity["persona_id"],),
            ).fetchone()[0])
            duplicates = self._duplicate_item_id_list_locked(connection, int(identity["persona_id"]), items)
            return self._pack_purchase_response_document(
                definition=definition,
                pack_type=int(pack_type),
                transaction_id=pack_id,
                items=items,
                credits=int(balances["coins"]),
                fifa_points=int(balances["fifa_points"]),
                unopened_packs=unopened_packs,
                duplicate_item_ids=duplicates,
            )

    def purchase_transaction(self, transaction_id: int) -> dict[str, Any] | None:
        """Return an already-created local pack purchase without charging twice."""
        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            pack = connection.execute(
                "SELECT pack_id, pack_type FROM packs WHERE persona_id = ? AND pack_id = ?",
                (identity["persona_id"], int(transaction_id)),
            ).fetchone()
            if pack is None:
                return None
            pack_type = int(pack["pack_type"])
            definition = PACK_DEFINITIONS.get(pack_type)
            if definition is None:
                return None
            item_rows = connection.execute(
                "SELECT payload FROM pack_contents WHERE pack_id = ? ORDER BY ordinal",
                (int(transaction_id),),
            ).fetchall()
            items = [json.loads(row["payload"]) for row in item_rows]
            balances = connection.execute(
                "SELECT coins, fifa_points FROM clubs WHERE persona_id = ?",
                (identity["persona_id"],),
            ).fetchone()
            if balances is None:
                return None
            unopened_packs = int(connection.execute(
                "SELECT COUNT(*) FROM packs WHERE persona_id = ? AND unopened = 1",
                (identity["persona_id"],),
            ).fetchone()[0])
            duplicates = self._duplicate_item_id_list_locked(connection, int(identity["persona_id"]), items)
            return self._pack_purchase_response_document(
                definition=definition,
                pack_type=pack_type,
                transaction_id=int(transaction_id),
                items=items,
                credits=int(balances["coins"]),
                fifa_points=int(balances["fifa_points"]),
                unopened_packs=unopened_packs,
                duplicate_item_ids=duplicates,
            )

    def _finish_pack_if_resolved_locked(self, connection: sqlite3.Connection, persona_id: int, pack_id: int) -> None:
        remaining = connection.execute(
            "SELECT payload FROM pack_contents WHERE pack_id = ? ORDER BY ordinal", (int(pack_id),)
        ).fetchall()
        if not remaining:
            connection.execute("UPDATE packs SET unopened = 0 WHERE pack_id = ?", (int(pack_id),))
            return
        for row in remaining:
            try:
                item_id = int(json.loads(row["payload"]).get("id"))
            except (TypeError, ValueError, json.JSONDecodeError):
                return
            owned = connection.execute(
                "SELECT 1 FROM items WHERE persona_id = ? AND item_id = ?", (int(persona_id), item_id)
            ).fetchone()
            if owned is None:
                return
        connection.execute("UPDATE packs SET unopened = 0 WHERE pack_id = ?", (int(pack_id),))

    def move_items(self, updates: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply retail pile moves, including the transfer pile (5)."""
        if not isinstance(updates, list):
            raise ValueError("itemData must be a list")
        results: list[dict[str, Any]] = []
        touched_packs: set[int] = set()
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            for update in updates:
                if not isinstance(update, dict):
                    continue
                raw_id = update.get("id", update.get("itemId"))
                try:
                    item_id = int(raw_id)
                except (TypeError, ValueError):
                    results.append({"id": raw_id, "success": False, "reason": "invalid item id"})
                    continue
                raw_pile = update.get("pile", 7)
                if isinstance(raw_pile, str):
                    pile_name = raw_pile.strip().casefold()
                    named_piles = {
                        "trade": 5, "transfer": 5, "transferpile": 5, "tradepile": 5,
                        "purchased": 6, "new": 6, "club": 7, "owned": 7,
                    }
                    if pile_name in named_piles:
                        requested_pile = named_piles[pile_name]
                    else:
                        requested_pile = self._bounded_int(raw_pile, 7, minimum=0, maximum=99)
                else:
                    requested_pile = self._bounded_int(raw_pile, 7, minimum=0, maximum=99)
                to_transfer = requested_pile == 5
                target_client_pile = 5 if to_transfer else 7
                target_db_pile = "trade" if to_transfer else "club"

                packed = connection.execute(
                    "SELECT pc.pack_id, pc.payload FROM pack_contents pc JOIN packs p ON p.pack_id=pc.pack_id "
                    "WHERE p.persona_id=? AND json_extract(pc.payload,'$.id')=? ORDER BY pc.pack_id DESC LIMIT 1",
                    (persona_id, item_id),
                ).fetchone()
                existing = connection.execute(
                    "SELECT payload,asset_id,item_type FROM items WHERE persona_id=? AND item_id=?",
                    (persona_id, item_id),
                ).fetchone()
                if packed is None and existing is None:
                    results.append({"id": item_id, "itemId": item_id, "success": False, "reason": "item not found"})
                    continue
                payload = json.loads(packed["payload"] if packed is not None else existing["payload"])
                if packed is not None:
                    touched_packs.add(int(packed["pack_id"]))
                item_type = str(payload.get("itemType", existing["item_type"] if existing else "unknown")).lower()
                asset_id = self._bounded_int(payload.get("assetId", existing["asset_id"] if existing else 0), 0, minimum=0)

                if item_type == PLAYER_ITEM_TYPE:
                    resource_id = self._bounded_int(payload.get("resourceId", asset_id), asset_id, minimum=1)
                    # My Club cannot hold the same card resource twice. The
                    # transfer pile can: this is how a duplicate pack pull is
                    # moved out of New Items instead of being dead-ended.
                    if not to_transfer:
                        duplicate = None
                        for owned in connection.execute(
                            "SELECT item_id,payload FROM items WHERE persona_id=? AND item_type=? AND item_id<>? AND pile NOT IN ('trade','pending')",
                            (persona_id, PLAYER_ITEM_TYPE, item_id),
                        ).fetchall():
                            try:
                                owned_payload = json.loads(owned["payload"] or "{}")
                                owned_resource = int(owned_payload.get("resourceId", owned_payload.get("assetId", 0)))
                            except (TypeError, ValueError, json.JSONDecodeError):
                                continue
                            if owned_resource == resource_id:
                                duplicate = int(owned["item_id"]); break
                        if duplicate is not None:
                            results.append({"id": item_id, "itemId": item_id, "success": False,
                                            "reason": "Duplicate Item Type", "errorCode": 472,
                                            "duplicateItemId": duplicate})
                            continue
                    if asset_id in PLAYER_REFERENCE_BY_ASSET:
                        payload = self._canonical_player_payload(
                            item_id=item_id, asset_id=asset_id, existing=payload, pile=target_client_pile
                        )
                else:
                    payload["pile"] = target_client_pile

                payload["untradeable"] = False
                payload["tradeable"] = True
                payload["itemState"] = "forSale" if to_transfer else "free"
                connection.execute(
                    "INSERT OR REPLACE INTO items (item_id,persona_id,asset_id,item_type,pile,tradeable,payload) "
                    "VALUES (?,?,?,?,?,1,?)",
                    (item_id, persona_id, asset_id, item_type, target_db_pile,
                     json.dumps(payload, separators=(",", ":"), ensure_ascii=False)),
                )
                if packed is not None:
                    connection.execute(
                        "DELETE FROM pack_contents WHERE pack_id=? AND json_extract(payload,'$.id')=?",
                        (int(packed["pack_id"]), item_id),
                    )
                if not to_transfer:
                    connection.execute("DELETE FROM market_listings WHERE persona_id=? AND item_id=?", (persona_id,item_id))
                results.append({"id": item_id, "itemId": item_id, "success": True, "reason": "", "errorCode": 0, "pile": target_client_pile})
            for pack_id in touched_packs:
                self._finish_pack_if_resolved_locked(connection, persona_id, pack_id)
        return {"itemData": results}

    def quick_sell(self, item_ids: list[int]) -> dict[str, Any]:
        """Quick-sell owned or just-opened items and return retail-safe credits."""
        sold: list[dict[str, Any]] = []
        touched_packs: set[int] = set()
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            club = connection.execute(
                "SELECT coins FROM clubs WHERE persona_id = ?", (persona_id,)
            ).fetchone()
            credits = 0 if club is None else max(0, min(2_147_483_647, int(club["coins"])))
            total_gain = 0
            for raw_id in item_ids:
                try:
                    item_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                packed = connection.execute(
                    """
                    SELECT pc.pack_id, pc.ordinal, pc.payload FROM pack_contents pc
                    JOIN packs p ON p.pack_id = pc.pack_id
                    WHERE p.persona_id = ? AND json_extract(pc.payload, '$.id') = ?
                    ORDER BY pc.pack_id DESC LIMIT 1
                    """, (persona_id, item_id)
                ).fetchone()
                owned = connection.execute(
                    "SELECT payload FROM items WHERE persona_id = ? AND item_id = ?", (persona_id, item_id)
                ).fetchone()
                payload = None
                if packed is not None:
                    payload = json.loads(packed["payload"])
                    touched_packs.add(int(packed["pack_id"]))
                elif owned is not None:
                    payload = json.loads(owned["payload"])
                if not isinstance(payload, dict):
                    continue
                gain = 0 if bool(payload.get("untradeable", False)) else max(0, int(payload.get("discardValue", 0)))
                total_gain += gain
                if packed is not None:
                    connection.execute(
                        "DELETE FROM pack_contents WHERE pack_id = ? AND ordinal = ?",
                        (int(packed["pack_id"]), int(packed["ordinal"])),
                    )
                connection.execute("DELETE FROM items WHERE persona_id = ? AND item_id = ?", (persona_id, item_id))
                sold.append({"id": item_id, "itemId": item_id, "discardValue": gain})
            credits = max(0, min(2_147_483_647, credits + total_gain))
            connection.execute("UPDATE clubs SET coins = ? WHERE persona_id = ?", (credits, persona_id))
            for pack_id in touched_packs:
                self._finish_pack_if_resolved_locked(connection, persona_id, pack_id)
        return {"items": sold, "itemData": sold, "totalCredits": credits, "credits": credits}

    def purchased_items(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            rows = connection.execute(
                "SELECT pack_id, pack_type, pack_name, created_at FROM packs WHERE persona_id = ? AND unopened = 1 ORDER BY pack_id",
                (persona_id,),
            ).fetchall()
            unopened = []
            for row in rows:
                item_rows = connection.execute(
                    "SELECT payload FROM pack_contents WHERE pack_id = ? ORDER BY ordinal", (row["pack_id"],)
                ).fetchall()
                item_data = [json.loads(item["payload"]) for item in item_rows]
                unopened.append({
                    "packId": int(row["pack_id"]), "packType": int(row["pack_type"]),
                    "packName": row["pack_name"], "itemData": item_data,
                })
            flat_items = [item for pack in unopened for item in pack["itemData"]]
            # Transfer-market wins live in a persistent pending pile until the
            # retail assignment screen sends the normal /item move or discard.
            pending_rows = connection.execute(
                "SELECT payload FROM items WHERE persona_id=? AND pile='pending' ORDER BY item_id", (persona_id,)
            ).fetchall()
            flat_items.extend(json.loads(row["payload"] or "{}") for row in pending_rows)
            duplicates = self._duplicate_item_id_list_locked(connection, persona_id, flat_items)
            return {
                "duplicateItemIdList": duplicates,
                "itemData": flat_items,
                "unopenedPacks": unopened,
                "packList": unopened,
            }

    def manager_reference_catalog(self) -> dict[str, Any]:
        return MANAGER_CATALOG_DOCUMENT

    def ensure_local_test_balance(self) -> dict[str, int]:
        """Seed the localhost test balance once, then preserve deductions.

        v2.40.10 and earlier topped the club back up to LOCAL_TEST_STARTING_COINS
        on every launcher run. That hid pack charges after restarting the game.
        v2.40.11 records a schema_meta marker the first time this seed is applied
        (or observed on an already-funded imported club) and never replenishes it
        again automatically.
        """
        marker_key = "local_test_balance_seeded_v24011"
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            row = connection.execute(
                "SELECT coins, fifa_points FROM clubs WHERE persona_id = ?",
                (identity["persona_id"],),
            ).fetchone()
            if row is None:
                return {"credits": 0, "fifaPoints": 0}
            marker = connection.execute(
                "SELECT meta_value FROM schema_meta WHERE meta_key = ?",
                (marker_key,),
            ).fetchone()
            # A v2.40.11 DELETE /item routing bug could make the client persist
            # a nonsensical negative balance after quick-sell. Negative FUT coins
            # are never legitimate, so repair only that corruption on upgrade.
            if int(row["coins"]) < 0:
                connection.execute(
                    "UPDATE clubs SET coins = ? WHERE persona_id = ?",
                    (LOCAL_TEST_STARTING_COINS, identity["persona_id"]),
                )
                row = connection.execute(
                    "SELECT coins, fifa_points FROM clubs WHERE persona_id = ?",
                    (identity["persona_id"],),
                ).fetchone()
            if marker is None:
                if int(row["coins"]) < LOCAL_TEST_STARTING_COINS:
                    connection.execute(
                        "UPDATE clubs SET coins = ? WHERE persona_id = ?",
                        (LOCAL_TEST_STARTING_COINS, identity["persona_id"]),
                    )
                    row = connection.execute(
                        "SELECT coins, fifa_points FROM clubs WHERE persona_id = ?",
                        (identity["persona_id"],),
                    ).fetchone()
                connection.execute(
                    "INSERT OR REPLACE INTO schema_meta (meta_key, meta_value) VALUES (?, ?)",
                    (marker_key, "1"),
                )
            assert row is not None
            return {"credits": int(row["coins"]), "fifaPoints": int(row["fifa_points"])}

    def credits(self) -> dict[str, Any]:
        """SAFE-ENTRY v2.40.1: exact v2.39 boot-time credits shape."""
        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            club = connection.execute(
                "SELECT coins FROM clubs WHERE persona_id = ?",
                (identity["persona_id"],),
            ).fetchone()
            return {"credits": 0 if club is None else int(club["coins"])}

    def currencies(self) -> dict[str, Any]:
        """Richer balance document retained for post-entry use/tests."""
        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            club = connection.execute(
                "SELECT coins, fifa_points FROM clubs WHERE persona_id = ?",
                (identity["persona_id"],),
            ).fetchone()
            coins = 0 if club is None else int(club["coins"])
            points = 0 if club is None else int(club["fifa_points"])
            return {
                "credits": coins,
                "fifaPoints": points,
                "bidTokens": {"count": 0, "updateTime": 0},
                "currencies": [
                    # The exact FIFA 14 PC FutUserCreditsServerResponse parser
                    # compares currency names against the legacy literals
                    # "coins" and "points" before copying funds into its
                    # native coin/points fields.  Keep the top-level credits
                    # scalar unchanged for the already-working HUD balance.
                    {"name": "coins", "funds": coins, "finalFunds": coins},
                    {"name": "points", "funds": points, "finalFunds": points},
                ],
                "unopenedPacks": {"preOrderPacks": 0, "recoveredPacks": 0},
            }

    def update_club_profile(self, document: dict[str, Any]) -> dict[str, Any]:
        """Persist club naming/profile writes observed from the retail client."""
        if not isinstance(document, dict):
            raise ValueError("club profile body must be a JSON object")
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            current = connection.execute(
                "SELECT * FROM clubs WHERE persona_id = ?", (identity["persona_id"],)
            ).fetchone()
            name = str(document.get("clubName") or (current["club_name"] if current else "Local FUT")).strip()
            abbr = str(document.get("clubAbbr") or (current["club_abbr"] if current else "LFT")).strip()
            badge = int(document.get("badgeId") or (current["badge_id"] if current else 241))
            team = int(document.get("teamId") or (current["team_id"] if current else 241))
            return self._create_club_locked(
                connection, club_name=name, club_abbr=abbr, badge_id=badge, team_id=team,
                established=(int(current["established"]) if current else None),
            )

    def save_client_data(self, data_key: str, document: dict[str, Any]) -> dict[str, Any]:
        normalized = data_key.strip()
        if not normalized or len(normalized) > 64:
            raise ValueError("invalid clientdata key")
        if not isinstance(document, dict):
            raise ValueError("clientdata body must be a JSON object")
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            connection.execute(
                """
                INSERT INTO client_data (persona_id, data_key, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(persona_id, data_key) DO UPDATE SET
                    payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (int(identity["persona_id"]), normalized,
                 json.dumps(document, separators=(",", ":"), sort_keys=True), int(time.time())),
            )
        return document

    def client_data(self, data_key: str) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            row = connection.execute(
                "SELECT payload FROM client_data WHERE persona_id = ? AND data_key = ?",
                (int(identity["persona_id"]), data_key),
            ).fetchone()
            if row is None:
                return {}
            try:
                value = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                return {}
            return value if isinstance(value, dict) else {}

    def hub_data(self) -> dict[str, Any]:
        """Return the native FUT hub summary including the *whole* local market.

        The 2.25.1 capture proved the Transfers tile reads ``auctionCount``
        from this endpoint.  Counting only the user's own listings is why it
        showed 0 live transfers, then 1/2 when the user listed cards.
        """
        now = int(time.time())
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            self._market_tick_locked(connection, persona_id, now)
            club_players = int(connection.execute(
                "SELECT COUNT(*) FROM items WHERE persona_id = ? AND item_type = ?",
                (persona_id, PLAYER_ITEM_TYPE),
            ).fetchone()[0])
            recent_cutoff = now - MARKET_SYNTHETIC_RELIST_SECONDS
            recent_sold = int(connection.execute(
                "SELECT COUNT(*) FROM market_synthetic_sales WHERE sold_at>=?",
                (recent_cutoff,),
            ).fetchone()[0])
            connection.execute("DELETE FROM market_synthetic_sales WHERE sold_at<?", (recent_cutoff,))
            user_live = int(connection.execute(
                "SELECT COUNT(*) FROM market_listings WHERE persona_id=? AND trade_state='active'",
                (persona_id,),
            ).fetchone()[0])
            user_sold = int(connection.execute(
                "SELECT COUNT(*) FROM market_listings WHERE persona_id=? AND trade_state='closed'",
                (persona_id,),
            ).fetchone()[0])
            user_total = user_live + user_sold
            auction_count = max(0, MARKET_LIVE_LISTING_COUNT - recent_sold) + user_live
            return {
                "auctionCount": int(auction_count),
                "clubPlayers": club_players,
                # The PC transfer-hub tile has several binders across retail UI
                # revisions. Unknown top-level JSON siblings are skipped by the
                # Cards parser, so expose the same owner counts under the small
                # scalar aliases those binders commonly expect. /tradePile remains
                # the source of truth for the actual cards.
                "tradePileCount": user_total,
                "tradePileItems": user_total,
                "transferListCount": user_total,
                "selling": user_live,
                "sold": user_sold,
            }

    def consumable_stats(self) -> dict[str, Any]:
        """Return the retail consumable-count members used by squad apply menus.

        ``/club/stats/consumables`` is not a StickerBook player-stat response.
        FIFA 14 reads named scalar members such as ``consumablesContractPlayer``
        and ``consumablesTrainingGk``.  Returning player counters here makes the
        squad screen conclude that zero usable consumables exist even though
        the item rows are present in My Club.
        """
        known_members = {
            "consumablesContractPlayer", "consumablesContractManager",
            "consumablesFitnessPlayer", "consumablesFitnessTeam",
            "consumablesHealing", "consumablesTrainingPlayer",
            "consumablesTrainingGk", "consumablesTrainingPlayerPlayStyle",
            "consumablesTrainingGkPlayStyle", "consumablesPosition",
            "consumablesTrainingManager",
            "consumablesTrainingManagerLeagueModifier",
            "consumablesFormationManager",
        }
        counts = {name: 0 for name in known_members}
        family_counts = {
            "contract": 0, "fitness": 0, "healing": 0,
            "training": 0, "position": 0, "playstyle": 0,
            "managerleague": 0,
        }
        total = 0
        with self._lock, closing(self._connect()) as connection, connection:
            persona_id = int(self._identity(connection)["persona_id"])
            rows = connection.execute(
                "SELECT payload FROM items WHERE persona_id=? "
                "AND item_type IN ('development','training') "
                "AND pile NOT IN ('trade','pending')",
                (persona_id,),
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload"] or "{}")
                    resource_id = int(payload.get("resourceId", 0)) if isinstance(payload, dict) else 0
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                definition = CONSUMABLE_BY_RESOURCE.get(resource_id)
                if not definition:
                    continue
                total += 1
                filter_key = self._consumable_catalog_filter_key(definition)
                if filter_key in family_counts:
                    family_counts[filter_key] += 1
                # Both outfield and GK training are part of the aggregate
                # consumablesTraining family.
                if filter_key == "gktraining":
                    family_counts["training"] += 1
                member = str(definition.get("sourceMember") or "").strip()
                if not member:
                    category = str(definition.get("category") or "").casefold()
                    if category == "chemistry style":
                        member = "consumablesTrainingPlayerPlayStyle"
                    elif category == "manager league":
                        member = "consumablesTrainingManagerLeagueModifier"
                if member:
                    counts[member] = counts.get(member, 0) + 1

        # Defensive family fallbacks for retail apply dialogs.  These do not
        # create cards; they only prevent a valid family from being advertised
        # as empty when a subtype/member mapping is incomplete.
        counts["consumablesTrainingManager"] = max(
            counts.get("consumablesTrainingManager", 0), family_counts["training"]
        )
        counts["consumablesTrainingManagerLeagueModifier"] = max(
            counts.get("consumablesTrainingManagerLeagueModifier", 0), family_counts["managerleague"]
        )
        counts["consumablesFormationManager"] = max(
            counts.get("consumablesFormationManager", 0), family_counts["position"]
        )
        counts.update({
            "consumablesContract": family_counts["contract"],
            "consumablesFitness": family_counts["fitness"],
            "consumablesTraining": family_counts["training"],
            "consumables": total,
        })
        # The squad popup is backed by FutStickerBookStats2ServerResponse on this
        # PC build. Preserve the named scalar aliases, but also expose the same
        # counters as context-6 stat/entries rows so the retail collection binder
        # can populate its consumable-type buttons.
        entries = [
            {"contextId": 6, "contextValue": 0, "type": key, "typeValue": int(value)}
            for key, value in sorted(counts.items())
        ]
        return {**counts, "stat": entries, "entries": entries}

    def club_stats(
        self, *, context_id: int = 0, context_value: int = 2014,
        nation: int | None = None, league: int | None = None,
    ) -> dict[str, Any]:
        """Return native StickerBook/My Club counters for the requested context."""
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            self._repair_owned_items_locked(connection, persona_id)
            rows = connection.execute(
                "SELECT payload FROM items WHERE persona_id = ? AND item_type = ?",
                (persona_id, PLAYER_ITEM_TYPE),
            ).fetchall()
            docs: list[dict[str, Any]] = []
            for row in rows:
                try:
                    payload = json.loads(row["payload"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if nation is not None and int(payload.get("nation", -1)) != int(nation):
                    continue
                if league is not None and int(payload.get("leagueId", -1)) != int(league):
                    continue
                docs.append(payload)

            ratings = [self._bounded_int(d.get("rating"), 0, minimum=0, maximum=99) for d in docs]
            rare_players = sum(
                1 for d in docs
                if self._bounded_int(d.get("rareflag", d.get("rareFlag", 0)), 0, minimum=0) > 0
            )
            gold = sum(1 for rating in ratings if rating >= 75)
            silver = sum(1 for rating in ratings if 65 <= rating < 75)
            bronze = sum(1 for rating in ratings if 0 < rating < 65)
            item_type_counts = {
                str(row["item_type"]).lower(): int(row["quantity"])
                for row in connection.execute(
                    "SELECT item_type, COUNT(*) AS quantity FROM items WHERE persona_id=? GROUP BY item_type",
                    (persona_id,),
                ).fetchall()
            }
            values = {
                "players": len(ratings), "playersBronze": bronze, "playersSilver": silver,
                "playersGold": gold, "rarePlayers": rare_players, "staff": 0,
                "stadia": item_type_counts.get("stadium", 0),
                "balls": item_type_counts.get("ball", 0),
                "kits": item_type_counts.get("kit", 0),
                "badges": item_type_counts.get("custom", 0),
                "trophies": item_type_counts.get("trophy", 0),
            }
            native_stats = [
                {
                    "contextId": int(context_id),
                    "contextValue": int(context_value),
                    "type": stat_type,
                    "typeValue": int(value),
                }
                for stat_type, value in values.items()
            ]
            return {
                "stat": native_stats, "entries": native_stats,
                "playerCount": len(ratings), "totalPlayers": len(ratings),
                "players": len(ratings), "rarePlayers": rare_players,
                "playersBronze": bronze, "playersSilver": silver, "playersGold": gold,
                "staff": values["staff"], "stadia": values["stadia"], "balls": values["balls"],
                "kits": values["kits"], "badges": values["badges"], "trophies": values["trophies"],
            }

    def club_items(
        self, filters: dict[str, Any] | None = None, *, include_consumables_default: bool = False
    ) -> dict[str, Any]:
        """Return paginated My Club ItemData.

        ``/clubUser`` is also the PC client's face-card cache bootstrap. When it
        arrives without an explicit type, BETA 2.25.8 appends every owned
        consumable to the normal first player page so Apply Consumable can bind
        the real cards without loading hundreds of extra players.
        """
        query = filters or {}

        def first(name: str, default: str = "") -> str:
            value = query.get(name, default)
            if isinstance(value, list):
                value = value[0] if value else default
            return str(value)

        start = self._bounded_int(first("start", "0"), 0, minimum=0)
        count = self._bounded_int(first("count", "50"), 50, minimum=1, maximum=200)
        explicit_type = "type" in query and bool(first("type", "").strip())
        requested_type = first("type", "player").strip().lower()
        level = first("level", first("lev", "any")).strip()
        level_key = level.casefold()
        level_key = {"1":"bronze", "2":"silver", "3":"gold", "4":"sp", "10":"any"}.get(level_key, level_key)
        position = first("position", first("pos", "")).strip().upper()
        team = self._bounded_int(first("team", first("club", "-1")), -1, minimum=-1)
        nation = self._bounded_int(first("nation", first("nat", "-1")), -1, minimum=-1)
        league = self._bounded_int(first("league", first("leag", "-1")), -1, minimum=-1)
        requested_category = self._consumable_filter_category({"category": first("cat", first("category", ""))})

        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            self._repair_owned_items_locked(connection, persona_id)
            documents: list[dict[str, Any]] = []
            preload_consumables: list[dict[str, Any]] = []
            player_request = requested_type in {"", "1", "player"}
            consumable_request = requested_type in {"consumable", "consumables"}
            nonplayer_type = requested_type if requested_type in {
                "development", "training", "kit", "stadium", "custom", "ball", "trophy"
            } else None

            if player_request:
                rows = connection.execute(
                    "SELECT * FROM items WHERE persona_id = ? AND item_type = ? AND pile NOT IN ('trade','pending')",
                    (persona_id, PLAYER_ITEM_TYPE),
                ).fetchall()
                for row in rows:
                    if int(row["asset_id"]) not in PLAYER_REFERENCE_BY_ASSET:
                        continue
                    try:
                        existing = json.loads(row["payload"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        existing = {}
                    payload = self._canonical_player_payload(
                        item_id=int(row["item_id"]), asset_id=int(row["asset_id"]),
                        existing=existing if isinstance(existing, dict) else {}, pile=7,
                    )
                    rating = int(payload.get("rating", 0))
                    rareflag = int(payload.get("rareflag", payload.get("rareFlag", 0)))
                    is_special = bool(payload.get("specialCard")) or rareflag > 1
                    if level_key not in {"", "any", "-1"}:
                        if level_key == "bronze" and not (rating <= 64 and not is_special): continue
                        if level_key == "silver" and not (65 <= rating <= 74 and not is_special): continue
                        if level_key == "gold" and not (rating >= 75 and not is_special): continue
                        if level_key in {"sp", "special"} and not is_special: continue
                        if level_key not in {"bronze", "silver", "gold", "sp", "special"}: continue
                    if position and position not in {"ANY", "-1"} and payload.get("preferredPosition") != position:
                        continue
                    if team >= 0 and int(payload.get("teamid", -1)) != team: continue
                    if league >= 0 and int(payload.get("leagueId", -1)) != league: continue
                    if nation >= 0 and int(payload.get("nation", -1)) != nation: continue
                    documents.append(payload)

                if include_consumables_default and not explicit_type:
                    preload_rows = connection.execute(
                        "SELECT payload FROM items WHERE persona_id=? "
                        "AND item_type IN ('development','training') "
                        "AND pile NOT IN ('trade','pending') ORDER BY item_id",
                        (persona_id,),
                    ).fetchall()
                    for preload_row in preload_rows:
                        try:
                            consumable = json.loads(preload_row["payload"] or "{}")
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if not isinstance(consumable, dict):
                            continue
                        consumable["pile"] = 7
                        resource_id = self._bounded_int(consumable.get("resourceId", 0), 0, minimum=0)
                        if resource_id not in CONSUMABLE_BY_RESOURCE:
                            continue
                        preload_consumables.append(consumable)
            elif consumable_request or nonplayer_type is not None:
                if consumable_request:
                    rows = connection.execute(
                        "SELECT payload FROM items WHERE persona_id=? "
                        "AND item_type IN ('development','training') "
                        "AND pile NOT IN ('trade','pending')",
                        (persona_id,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT payload FROM items WHERE persona_id = ? AND item_type = ? "
                        "AND pile NOT IN ('trade','pending')",
                        (persona_id, nonplayer_type),
                    ).fetchall()
                for row in rows:
                    try:
                        payload = json.loads(row["payload"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    payload["pile"] = 7
                    quality = self._quality_for_rating(int(payload.get("rating", 0) or 0))
                    if level_key not in {"", "any", "-1"} and level_key != quality:
                        continue
                    resource_id = self._bounded_int(payload.get("resourceId", 0), 0, minimum=0)
                    definition = CONSUMABLE_BY_RESOURCE.get(resource_id, {})
                    if requested_category and self._consumable_catalog_filter_key(definition) != requested_category:
                        continue
                    documents.append(payload)

            documents.sort(key=lambda d: (
                -int(d.get("rating", 0)),
                -int(d.get("rareflag", d.get("rareFlag", 0))),
                int(d.get("assetId", d.get("resourceId", 0))),
                int(d.get("id", d.get("itemId", 0))),
            ))
            total = len(documents) + len(preload_consumables)
            page = documents[start:start + count]
            if preload_consumables and start == 0:
                preload_consumables.sort(key=lambda d: (
                    str(d.get("itemType", "")), int(d.get("resourceId", 0)), int(d.get("id", d.get("itemId", 0)))
                ))
                page = page + preload_consumables
            return {"itemData": page, "total": total, "count": len(page), "start": start}

    def empty_club_items(self) -> dict[str, Any]:
        return self.club_items()

    @staticmethod
    def _market_first(query: dict[str, Any], *names: str, default: str = "") -> str:
        for name in names:
            if name not in query:
                continue
            value = query.get(name)
            if isinstance(value, list):
                value = value[0] if value else default
            if value is not None:
                return str(value)
        return default

    @staticmethod
    def _market_int(query: dict[str, Any], *names: str, default: int | None = None) -> int | None:
        raw = LocalIdentityStore._market_first(query, *names, default="")
        if raw == "":
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _market_round_price(value: float) -> int:
        price = max(150, int(round(value)))
        step = 50 if price < 1000 else 100 if price < 10000 else 250 if price < 50000 else 500 if price < 100000 else 1000
        return max(150, int(round(price / step) * step))

    @classmethod
    def _market_price_for(cls, player: dict[str, Any]) -> int:
        """Long-run reference value: deliberately old-era, not modern FUT."""
        rating = max(1, min(99, int(player.get("rating", 1))))
        anchors = {
            64: 400, 65: 500, 70: 900, 74: 1500, 75: 1600, 76: 2200,
            77: 3000, 78: 4500, 79: 6500, 80: 9000, 81: 13000, 82: 20000,
            83: 30000, 84: 45000, 85: 70000, 86: 110000, 87: 175000,
            88: 275000, 89: 425000, 90: 650000, 91: 850000, 92: 1000000,
            93: 1250000, 94: 1500000, 95: 1800000, 96: 2200000,
            97: 2700000, 98: 3300000, 99: 4000000,
        }
        if rating <= 40:
            base = 150
        elif rating < 64:
            base = 150 + (rating - 40) * 10
        else:
            keys = sorted(anchors)
            lo = max(k for k in keys if k <= rating)
            base = anchors[lo]

        rare = int(player.get("rareFlag", player.get("rareflag", 0)) or 0)
        card_type = str(player.get("cardType", "")).lower()
        multiplier = 1.0
        if rare == 0:
            multiplier *= 0.82
        if rare > 1 or bool(player.get("specialCard")):
            multiplier *= {
                "goldif": 1.35, "silverif": 1.35, "bronzeif": 1.35,
                "motm": 1.60, "green": 1.55,
                "goldblue": 1.80, "silverblue": 1.70, "bronzeblue": 1.70,
                "toty": 2.25, "special": 1.50,
            }.get(card_type, 1.45)

        asset_id = int(player.get("assetId", 0) or 0)
        version = int(player.get("version", 1) or 1)
        # User calibration target: NIF 92 Ronaldo's long-run reference is 1.2m.
        if asset_id == 20801 and version == 1:
            return 1_200_000
        if asset_id == 158023 and version == 1:
            return 1_550_000

        resource = int(player.get("resourceId", asset_id) or asset_id)
        jitter = 0.94 + ((resource * 1103515245 + 12345) & 0xFFFF) / 65535.0 * 0.12
        return cls._market_round_price(base * multiplier * jitter)

    @staticmethod
    def _market_trend_rows_locked(connection: sqlite3.Connection) -> dict[int, tuple[float, int]]:
        return {
            int(row["resource_id"]): (float(row["pressure"]), int(row["updated_at"]))
            for row in connection.execute("SELECT resource_id,pressure,updated_at FROM market_trends").fetchall()
        }

    @classmethod
    def _market_current_value_for(cls, player: dict[str, Any], trend_rows: dict[int, tuple[float, int]] | None = None,
                                  now: int | None = None) -> int:
        """Current market value = old-era reference + time trend + local demand/supply."""
        now = int(time.time()) if now is None else int(now)
        resource = int(player.get("resourceId", player.get("assetId", 0)) or 0)
        pressure = 0.0
        if trend_rows and resource in trend_rows:
            stored, updated = trend_rows[resource]
            hours = max(0.0, (now - int(updated)) / 3600.0)
            pressure = float(stored) * (0.965 ** hours)
        # Thirty-minute snapshots keep prices stable while the user is browsing
        # or bidding, while still producing a market that moves over a session.
        epoch = now // 1800
        seed = (resource % 997) / 997.0 * math.tau
        global_wave = 0.020 * math.sin(epoch / 3.0)
        card_wave = 0.025 * math.sin(epoch / 2.0 + seed)
        multiplier = max(0.72, min(1.35, 1.0 + global_wave + card_wave + pressure))
        return cls._market_round_price(cls._market_price_for(player) * multiplier)

    @classmethod
    def _market_listing_price_for(cls, player: dict[str, Any], copy_index: int,
                                  trend_rows: dict[int, tuple[float, int]] | None = None,
                                  now: int | None = None) -> int:
        count = _market_listing_copies_for_card(player)
        spreads = {
            3: (-0.040, 0.000, 0.045),
            4: (-0.050, -0.015, 0.025, 0.065),
            5: (-0.060, -0.030, 0.000, 0.032, 0.070),
            6: (-0.065, -0.040, -0.015, 0.015, 0.045, 0.080),
            7: (-0.070, -0.045, -0.020, 0.000, 0.025, 0.055, 0.090),
        }
        copy_index = max(0, min(count - 1, int(copy_index)))
        value = cls._market_current_value_for(player, trend_rows, now)
        return cls._market_round_price(value * (1.0 + spreads[count][copy_index]))

    @staticmethod
    def _market_listing_duration(player: dict[str, Any], copy_index: int) -> int:
        resource = int(player.get("resourceId", player.get("assetId", 0)) or 0)
        durations = (3600, 10800, 21600, 43200, 86400)
        return durations[(resource + int(copy_index) * 3) % len(durations)]

    @staticmethod
    def _market_trade_id(player: dict[str, Any], copy_index: int = 0) -> int:
        resource = int(player.get("resourceId", player.get("assetId", 0)) or 0)
        index = MARKET_RESOURCE_INDEX[resource]
        return MARKET_TRADE_ID_BASE + index * MARKET_MAX_COPIES + int(copy_index)

    @staticmethod
    def _market_from_trade_id(trade_id: int) -> tuple[dict[str, Any], int] | None:
        offset = int(trade_id) - MARKET_TRADE_ID_BASE
        if offset < 0:
            return None
        index, copy_index = divmod(offset, MARKET_MAX_COPIES)
        if index < 0 or index >= len(MARKET_PLAYER_CATALOG):
            return None
        player = MARKET_PLAYER_CATALOG[index]
        if copy_index >= _market_listing_copies_for_card(player):
            return None
        return player, copy_index

    def _market_item_payload(self, player: dict[str, Any], copy_index: int = 0) -> dict[str, Any]:
        asset_id = int(player["assetId"])
        resource = int(player.get("resourceId", asset_id) or asset_id)
        index = MARKET_RESOURCE_INDEX[resource]
        initial = dict(player)
        initial.update({
            "untradeable": False, "tradeable": True, "contract": 7, "fitness": 99,
            "morale": 99, "formation": "f442", "pile": 0, "itemState": "free",
            "discardValue": self._player_discard_value(player),
        })
        payload = self._canonical_player_payload(
            item_id=MARKET_ITEM_ID_BASE + index * MARKET_MAX_COPIES + int(copy_index),
            asset_id=asset_id,
            existing=initial,
            pile=0,
        )
        payload["untradeable"] = False
        payload["tradeable"] = True
        payload["discardValue"] = self._player_discard_value(player)
        payload["itemState"] = "free"
        return payload

    def _market_auction(self, player: dict[str, Any], *, owner: bool = False,
                        trade_id: int | None = None, starting_bid: int | None = None,
                        buy_now: int | None = None, duration: int | None = None,
                        item_payload: dict[str, Any] | None = None, copy_index: int = 0,
                        trend_rows: dict[int, tuple[float, int]] | None = None,
                        now: int | None = None, trade_state: str = "active",
                        sold_price: int = 0) -> dict[str, Any]:
        now = int(time.time()) if now is None else int(now)
        if buy_now is None:
            price = self._market_listing_price_for(player, copy_index, trend_rows, now)
        else:
            price = int(buy_now)
        start = int(starting_bid if starting_bid is not None else self._market_round_price(price * 0.82))
        duration = int(duration if duration is not None else self._market_listing_duration(player, copy_index))
        item = item_payload if item_payload is not None else self._market_item_payload(player, copy_index)
        seller_names = ("FUT", "LegacyFC", "UltimateXI", "TradeKing", "OldSchoolUT", "MarketFC", "FootyClub", "RareGoldFC")
        resource = int(player.get("resourceId", player.get("assetId", 0)) or 0)
        seller = "Local FUT" if owner else seller_names[(resource + int(copy_index)) % len(seller_names)]
        closed = trade_state == "closed"
        return {
            "tradeId": int(trade_id if trade_id is not None else self._market_trade_id(player, copy_index)),
            "tradeState": str(trade_state),
            "expires": max(60, duration),
            "EXPIRE_TIME": max(60, duration),
            "expireTime": max(60, duration),
            "startTime": 0,
            "endtime": 2147483647,
            "buyNowPrice": price,
            "startingBid": max(150, start),
            "currentBid": int(sold_price if closed else 0),
            # A local-bot Buy Now is a completed sale, not a pending bid/offer.
            # Reporting offers=1 makes FIFA 14 surface the retail "offer received"
            # action and route the user into /trade/<id>/offer.  Keep the sold
            # price in currentBid, but expose no outstanding offers.
            "offers": 0,
            "watched": False,
            "bidState": "none",
            "tradeOwner": bool(owner),
            "sellerName": seller,
            "sellerEstablished": 2013,
            "sellerId": DEFAULT_PERSONA_ID if owner else 1 + ((resource + int(copy_index)) % 999999),
            "confidenceValue": 100,
            "itemData": item,
        }

    @staticmethod
    def _market_adjust_trend_locked(connection: sqlite3.Connection, resource: int, delta: float,
                                    now: int, *, buy: bool = False, sale: bool = False) -> None:
        row = connection.execute(
            "SELECT pressure,updated_at,total_buys,total_sales FROM market_trends WHERE resource_id=?",
            (int(resource),),
        ).fetchone()
        if row is None:
            pressure = 0.0
            total_buys = 0
            total_sales = 0
        else:
            hours = max(0.0, (now - int(row["updated_at"])) / 3600.0)
            pressure = float(row["pressure"]) * (0.965 ** hours)
            total_buys = int(row["total_buys"])
            total_sales = int(row["total_sales"])
        pressure = max(-0.18, min(0.18, pressure + float(delta)))
        connection.execute(
            "INSERT OR REPLACE INTO market_trends(resource_id,pressure,updated_at,last_price,total_buys,total_sales) VALUES (?,?,?,?,?,?)",
            (int(resource), pressure, int(now), 0, total_buys + (1 if buy else 0), total_sales + (1 if sale else 0)),
        )

    def _market_tick_locked(self, connection: sqlite3.Connection, persona_id: int, now: int | None = None) -> int:
        """Lazy local buyers: sell competitively priced user listings as FUT is polled."""
        now = int(time.time()) if now is None else int(now)
        trend_rows = self._market_trend_rows_locked(connection)
        sold_count = 0
        rows = connection.execute(
            "SELECT * FROM market_listings WHERE persona_id=? AND trade_state='active' ORDER BY created_at,trade_id",
            (int(persona_id),),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["item_payload"] or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            if not payload:
                item = connection.execute(
                    "SELECT payload FROM items WHERE persona_id=? AND item_id=?",
                    (int(persona_id), int(row["item_id"])),
                ).fetchone()
                if item is not None:
                    try:
                        payload = json.loads(item["payload"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        payload = {}
            resource = int(payload.get("resourceId", payload.get("assetId", 0)) or 0)
            player = MARKET_PLAYER_BY_RESOURCE.get(resource) or payload
            current_value = self._market_current_value_for(player, trend_rows, now)
            cheapest = self._market_listing_price_for(player, 0, trend_rows, now)
            ask = int(row["buy_now_price"])
            if ask > self._market_round_price(current_value * 1.10):
                continue
            # Cheapest listings move first. Anything at/under market value sells
            # quickly; +10% is still liquid but takes longer.
            if ask <= cheapest:
                base_delay = 18
                span = 28
            elif ask <= current_value:
                base_delay = 40
                span = 55
            else:
                base_delay = 75
                span = 100
            deterministic = (int(row["trade_id"]) * 1103515245 + resource * 12345) & 0x7FFFFFFF
            delay = base_delay + deterministic % span
            auto_after = int(row["auto_sell_after"] or 0)
            eligible_at = int(row["created_at"]) + (auto_after if auto_after > 0 else delay)
            if now < eligible_at:
                continue
            sold_price = ask
            net = max(0, int(round(sold_price * (1.0 - MARKET_SELL_TAX_RATE))))
            sold_payload = dict(payload)
            sold_payload["pile"] = 5
            sold_payload["itemState"] = "sold"
            sold_payload["lastSalePrice"] = sold_price
            sold_payload["untradeable"] = False
            sold_payload["tradeable"] = True
            sold_payload_json = json.dumps(sold_payload, separators=(",", ":"), ensure_ascii=False)
            connection.execute("UPDATE clubs SET coins=coins+? WHERE persona_id=?", (net, int(persona_id)))
            connection.execute(
                "UPDATE market_listings SET trade_state='closed',sold_price=?,sold_at=?,item_payload=? "
                "WHERE persona_id=? AND trade_id=?",
                (sold_price, now, sold_payload_json, int(persona_id), int(row["trade_id"])),
            )
            # Keep the sold ItemData snapshot in the transfer pile until the user
            # clears the Sold tile. FIFA 14's trade-pile parser dereferences the
            # item even for a closed auction; deleting it here caused the 2.25.4
            # access violation for migrated listings.
            connection.execute(
                "UPDATE items SET pile='trade',payload=? WHERE persona_id=? AND item_id=?",
                (sold_payload_json, int(persona_id), int(row["item_id"])),
            )
            self._market_adjust_trend_locked(connection, resource, -0.006, now, sale=True)
            trend_rows[resource] = (max(-0.18, trend_rows.get(resource, (0.0, now))[0] - 0.006), now)
            sold_count += 1
        return sold_count

    def market_search(self, query: dict[str, Any] | None = None) -> dict[str, Any]:
        query = query or {}
        requested_type = self._market_first(query, "type", default="player").lower()
        if requested_type not in {"", "player", "1"}:
            return self.empty_auctions()
        definition = self._market_int(query, "definitionId", "maskedDefId")
        level = self._market_first(query, "lev", "level", default="any").lower()
        position = self._market_first(query, "pos", "position", default="").upper()
        nation = self._market_int(query, "nat", "nation", default=-1)
        league = self._market_int(query, "leag", "league", default=-1)
        team = self._market_int(query, "team", "club", default=-1)
        rare_filter = self._market_first(query, "rare", "rarity", default="").lower()
        min_buy = self._market_int(query, "micr", "minBuyNow", default=0) or 0
        max_buy = self._market_int(query, "macr", "maxBuyNow", default=0) or 0
        min_bid = self._market_int(query, "minb", "minBid", default=0) or 0
        max_bid = self._market_int(query, "maxb", "maxBid", default=0) or 0
        start = max(0, self._market_int(query, "start", "offset", "skip", default=0) or 0)
        count = max(1, min(100, self._market_int(query, "num", "count", default=20) or 20))
        now = int(time.time())

        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            self._market_tick_locked(connection, persona_id, now)
            club = connection.execute("SELECT coins FROM clubs WHERE persona_id=?", (persona_id,)).fetchone()
            coins = int(club["coins"]) if club is not None else 0
            trend_rows = self._market_trend_rows_locked(connection)
            recent_cutoff = now - MARKET_SYNTHETIC_RELIST_SECONDS
            sold_ids = {
                int(row["trade_id"])
                for row in connection.execute("SELECT trade_id FROM market_synthetic_sales WHERE sold_at>=?", (recent_cutoff,)).fetchall()
            }

        refs: list[tuple[dict[str, Any], int, int, int]] = []
        for player in MARKET_PLAYER_CATALOG:
            asset_id = int(player.get("assetId", 0) or 0)
            resource = int(player.get("resourceId", asset_id) or asset_id)
            if definition not in (None, 0, -1) and int(definition) not in {asset_id, resource}:
                continue
            quality = str(player.get("quality", self._quality_for_rating(int(player.get("rating", 0))))).lower()
            rare = int(player.get("rareFlag", player.get("rareflag", 0)) or 0)
            special = bool(player.get("specialCard")) or rare > 1
            if level not in {"", "any", "-1", "10"}:
                normalized = {"1":"bronze", "2":"silver", "3":"gold", "4":"special", "sp":"special"}.get(level, level)
                if normalized in {"bronze","silver","gold"} and quality != normalized:
                    continue
                if normalized == "special" and not special:
                    continue
            if rare_filter not in {"", "any", "-1"}:
                if rare_filter in {"1","true","rare"} and rare <= 0:
                    continue
                if rare_filter in {"0","false","common"} and rare != 0:
                    continue
                if rare_filter in {"sp","special"} and not special:
                    continue
            if position not in {"", "ANY", "-1"} and str(player.get("position", "")).upper() != position:
                continue
            if nation not in (None, -1, 0) and int(player.get("nation", -999)) != int(nation):
                continue
            if league not in (None, -1, 0) and int(player.get("leagueId", -999)) != int(league):
                continue
            if team not in (None, -1, 0) and int(player.get("teamId", -999)) != int(team):
                continue
            for copy_index in range(_market_listing_copies_for_card(player)):
                trade_id = self._market_trade_id(player, copy_index)
                if trade_id in sold_ids:
                    continue
                price = self._market_listing_price_for(player, copy_index, trend_rows, now)
                starting = self._market_round_price(price * 0.82)
                if min_buy and price < min_buy:
                    continue
                if max_buy and price > max_buy:
                    continue
                if min_bid and starting < min_bid:
                    continue
                if max_bid and starting > max_bid:
                    continue
                refs.append((player, copy_index, price, starting))

        refs.sort(key=lambda ref: (-int(ref[0].get("rating", 0)), ref[2], str(ref[0].get("name", "")), ref[1]))
        total = len(refs)
        page = refs[start:start + count]
        auctions = [
            self._market_auction(player, copy_index=copy_index, buy_now=price, starting_bid=starting,
                                 trend_rows=trend_rows, now=now)
            for player, copy_index, price, starting in page
        ]
        return {"auctionInfo": auctions, "duplicateItemIdList": [], "total": total,
                "credits": coins, "totalCredits": coins, "coins": coins}

    def market_status(self, trade_ids: list[int]) -> dict[str, Any]:
        wanted = {int(x) for x in trade_ids}
        auctions: list[dict[str, Any]] = []
        now = int(time.time())
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            self._market_tick_locked(connection, persona_id, now)
            club = connection.execute("SELECT coins FROM clubs WHERE persona_id=?", (persona_id,)).fetchone()
            coins = int(club["coins"]) if club is not None else 0
            trend_rows = self._market_trend_rows_locked(connection)
            recent_cutoff = now - MARKET_SYNTHETIC_RELIST_SECONDS
            recent_sold = {
                int(row["trade_id"])
                for row in connection.execute("SELECT trade_id FROM market_synthetic_sales WHERE sold_at>=?", (recent_cutoff,)).fetchall()
            }
            for trade_id in wanted:
                decoded = self._market_from_trade_id(trade_id)
                if decoded is not None and trade_id not in recent_sold:
                    player, copy_index = decoded
                    auctions.append(self._market_auction(player, copy_index=copy_index, trend_rows=trend_rows, now=now))
            if wanted:
                rows = connection.execute(
                    "SELECT * FROM market_listings WHERE persona_id=? AND trade_id IN (%s)" % ",".join("?" for _ in wanted),
                    (persona_id, *sorted(wanted)),
                ).fetchall()
                for row in rows:
                    try:
                        payload = json.loads(row["item_payload"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        payload = {}
                    if not payload:
                        item = connection.execute("SELECT payload FROM items WHERE persona_id=? AND item_id=?", (persona_id,int(row["item_id"]))).fetchone()
                        if item is not None:
                            payload = json.loads(item["payload"] or "{}")
                    player = MARKET_PLAYER_BY_RESOURCE.get(int(payload.get("resourceId", payload.get("assetId", 0)) or 0)) or payload
                    auctions.append(self._market_auction(
                        player, owner=True, trade_id=int(row["trade_id"]), starting_bid=int(row["starting_bid"]),
                        buy_now=int(row["buy_now_price"]), duration=int(row["duration"]), item_payload=payload,
                        trade_state=str(row["trade_state"]), sold_price=int(row["sold_price"] or 0), now=now,
                    ))
        return {"auctionInfo": auctions, "duplicateItemIdList": [], "total": len(auctions),
                "credits": coins, "totalCredits": coins, "coins": coins}

    def market_bid(self, trade_id: int, amount: int) -> dict[str, Any]:
        trade_id = int(trade_id)
        amount = max(0, int(amount))
        decoded = self._market_from_trade_id(trade_id)
        if decoded is None:
            return {"reason": "INVALID_REQUEST", "tradeId": trade_id}
        player, copy_index = decoded
        now = int(time.time())
        resource = int(player.get("resourceId", player.get("assetId", 0)) or 0)
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            recent = connection.execute(
                "SELECT 1 FROM market_synthetic_sales WHERE trade_id=? AND sold_at>=?",
                (trade_id, now - MARKET_SYNTHETIC_RELIST_SECONDS),
            ).fetchone()
            if recent is not None:
                return {"reason": "AUCTION_EXPIRED", "tradeId": trade_id}
            trend_rows = self._market_trend_rows_locked(connection)
            buy_now = self._market_listing_price_for(player, copy_index, trend_rows, now)
            club = connection.execute("SELECT coins FROM clubs WHERE persona_id=?", (persona_id,)).fetchone()
            coins = int(club["coins"]) if club is not None else 0
            if amount <= 0:
                amount = buy_now
            if amount > coins:
                return {"reason": "INSUFFICIENT_COINS", "tradeId": trade_id, "credits": coins, "totalCredits": coins, "coins": coins}
            won = amount >= buy_now
            if won:
                # Keep the existing duplicate guard until the post-market duplicate
                # assignment path is separately exercised on this PC build.
                for row in connection.execute("SELECT item_id,payload FROM items WHERE persona_id=? AND item_type=? AND pile NOT IN ('trade','pending')", (persona_id,PLAYER_ITEM_TYPE)).fetchall():
                    try:
                        owned = json.loads(row["payload"] or "{}")
                        if int(owned.get("resourceId", owned.get("assetId", 0)) or 0) == resource:
                            return {"reason":"Duplicate Item Type", "errorCode":472, "duplicateItemId":int(row["item_id"]),
                                    "tradeId":trade_id, "credits":coins, "totalCredits":coins, "coins":coins}
                    except (ValueError,TypeError,json.JSONDecodeError):
                        continue
                connection.execute("UPDATE clubs SET coins=coins-? WHERE persona_id=?", (buy_now,persona_id))
                next_id = int(connection.execute("SELECT COALESCE(MAX(item_id), ?) + 1 FROM items WHERE persona_id=?", (PACK_ITEM_BASE,persona_id)).fetchone()[0])
                initial = dict(player)
                initial.update({"untradeable":False,"tradeable":True,"contract":7,"fitness":99,"morale":99,"itemState":"new","pile":6,"lastSalePrice":buy_now,"discardValue":self._player_discard_value(player)})
                payload = self._canonical_player_payload(item_id=next_id, asset_id=int(player["assetId"]), existing=initial, pile=6)
                payload["untradeable"] = False
                payload["tradeable"] = True
                payload["lastSalePrice"] = buy_now
                payload["discardValue"] = self._player_discard_value(player)
                payload["itemState"] = "new"
                connection.execute("INSERT INTO items(item_id,persona_id,asset_id,item_type,pile,tradeable,payload) VALUES (?,?,?,?, 'pending',1,?)",
                    (next_id,persona_id,int(player["assetId"]),PLAYER_ITEM_TYPE,json.dumps(payload,separators=(",",":"),ensure_ascii=False)))
                connection.execute(
                    "INSERT OR REPLACE INTO market_synthetic_sales(trade_id,resource_id,sold_price,sold_at) VALUES (?,?,?,?)",
                    (trade_id, resource, buy_now, now),
                )
                self._market_adjust_trend_locked(connection, resource, +0.010, now, buy=True)
                coins -= buy_now
            listing = self._market_auction(player, copy_index=copy_index, buy_now=buy_now, trend_rows=trend_rows, now=now)
            listing.update({"currentBid": amount, "offers":0 if won else 1, "bidState":"highest", "tradeState":"closed" if won else "active",
                            "credits":coins, "totalCredits":coins, "coins":coins})
            if won:
                listing["itemData"] = payload
            return listing

    def list_for_sale(self, document: dict[str, Any]) -> dict[str, Any]:
        item_data = document.get("itemData") if isinstance(document, dict) else None
        if isinstance(item_data, list):
            item_data = item_data[0] if item_data else None
        raw_id = item_data.get("id", item_data.get("itemId")) if isinstance(item_data, dict) else document.get("itemId", document.get("id"))
        try:
            item_id = int(raw_id)
        except (TypeError,ValueError):
            raise ValueError("auction listing missing item id")
        try:
            starting = max(150, int(document.get("startingBid") or 150))
        except (TypeError,ValueError):
            starting = 150
        try:
            buy_now = max(starting, int(document.get("buyNowPrice") or max(200, starting*2)))
        except (TypeError,ValueError):
            buy_now = max(200,starting*2)
        try:
            duration = max(60, int(document.get("duration") or 3600))
        except (TypeError,ValueError):
            duration = 3600
        now = int(time.time())
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            self._market_tick_locked(connection, persona_id, now)
            count = int(connection.execute("SELECT COUNT(*) FROM market_listings WHERE persona_id=? AND trade_state='active'",(persona_id,)).fetchone()[0])
            if count >= TRANSFER_LIST_CAPACITY:
                raise ValueError("transfer list full")
            row = connection.execute("SELECT * FROM items WHERE persona_id=? AND item_id=?",(persona_id,item_id)).fetchone()
            if row is None:
                raise ValueError("item not found")
            if not bool(row["tradeable"]):
                raise ValueError("item is untradeable")
            payload = json.loads(row["payload"] or "{}")
            payload["pile"] = 5
            payload["itemState"] = "forSale"
            payload["untradeable"] = False
            payload["tradeable"] = True
            resource = int(payload.get("resourceId", payload.get("assetId", 0)) or 0)
            player = MARKET_PLAYER_BY_RESOURCE.get(resource) or payload
            trend_rows = self._market_trend_rows_locked(connection)
            market_value = self._market_current_value_for(player, trend_rows, now)
            cheapest = self._market_listing_price_for(player, 0, trend_rows, now)
            if buy_now <= cheapest:
                base_delay, span = 18, 28
            elif buy_now <= market_value:
                base_delay, span = 40, 55
            elif buy_now <= self._market_round_price(market_value * 1.10):
                base_delay, span = 75, 100
            else:
                base_delay, span = 0, 0
            existing = connection.execute("SELECT trade_id FROM market_listings WHERE persona_id=? AND item_id=?",(persona_id,item_id)).fetchone()
            if existing is not None:
                trade_id = int(existing["trade_id"])
            else:
                current = connection.execute("SELECT COALESCE(MAX(trade_id),?) FROM market_listings",(USER_TRADE_ID_BASE,)).fetchone()[0]
                trade_id = max(USER_TRADE_ID_BASE,int(current))+1
            auto_sell_after = 0
            if base_delay:
                deterministic = (trade_id * 1103515245 + resource * 12345) & 0x7FFFFFFF
                auto_sell_after = base_delay + deterministic % span
            connection.execute("UPDATE items SET pile='trade',tradeable=1,payload=? WHERE persona_id=? AND item_id=?",
                               (json.dumps(payload,separators=(",",":"),ensure_ascii=False),persona_id,item_id))
            connection.execute(
                "INSERT OR REPLACE INTO market_listings(trade_id,persona_id,item_id,starting_bid,buy_now_price,duration,created_at,trade_state,item_payload,sold_price,sold_at,auto_sell_after,market_value_at_list) VALUES (?,?,?,?,?,?,?,'active',?,0,0,?,?)",
                (trade_id,persona_id,item_id,starting,buy_now,duration,now,json.dumps(payload,separators=(",",":"),ensure_ascii=False),auto_sell_after,market_value),
            )
            auction = self._market_auction(payload,owner=True,trade_id=trade_id,starting_bid=starting,buy_now=buy_now,duration=duration,item_payload=payload,now=now)
            auction["marketValue"] = market_value
            auction["cheapestMarketPrice"] = cheapest
            return auction

    def trade_pile(self) -> dict[str, Any]:
        now = int(time.time())
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            self._market_tick_locked(connection, persona_id, now)
            club = connection.execute("SELECT coins FROM clubs WHERE persona_id=?",(persona_id,)).fetchone()
            coins = int(club["coins"]) if club else 0
            rows = connection.execute(
                "SELECT * FROM market_listings WHERE persona_id=? ORDER BY CASE trade_state WHEN 'active' THEN 0 WHEN 'closed' THEN 1 ELSE 2 END,trade_id",
                (persona_id,),
            ).fetchall()
            auctions = []
            for row in rows:
                try:
                    payload = json.loads(row["item_payload"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                if not payload:
                    item = connection.execute(
                        "SELECT payload FROM items WHERE persona_id=? AND item_id=?",
                        (persona_id, int(row["item_id"])),
                    ).fetchone()
                    if item is not None:
                        try:
                            payload = json.loads(item["payload"] or "{}")
                        except (TypeError, json.JSONDecodeError):
                            payload = {}
                    if isinstance(payload, dict) and payload:
                        connection.execute(
                            "UPDATE market_listings SET item_payload=? WHERE persona_id=? AND trade_id=?",
                            (json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                             persona_id, int(row["trade_id"])),
                        )
                # Never hand the retail CardsDLL a closed auction with itemData={}.
                # BETA 2.25.4 did exactly that after auto-selling legacy listings,
                # which is the access violation fixed by this build.
                if not isinstance(payload, dict) or not payload or int(payload.get("id", payload.get("itemId", 0)) or 0) <= 0:
                    if str(row["trade_state"]) == "closed":
                        connection.execute(
                            "DELETE FROM market_listings WHERE persona_id=? AND trade_id=?",
                            (persona_id, int(row["trade_id"])),
                        )
                    continue
                player = MARKET_PLAYER_BY_RESOURCE.get(int(payload.get("resourceId",payload.get("assetId",0)) or 0)) or payload
                auctions.append(self._market_auction(
                    player, owner=True, trade_id=int(row["trade_id"]), starting_bid=int(row["starting_bid"]),
                    buy_now=int(row["buy_now_price"]), duration=int(row["duration"]), item_payload=payload,
                    trade_state=str(row["trade_state"]), sold_price=int(row["sold_price"] or 0), now=now,
                ))
            # A card can be on the Transfer List without being listed for sale yet.
            # move_items() persists that state as items.pile='trade', while an
            # auction row is created only after the user chooses List on Market.
            # Older builds rendered only market_listings here, so moving a card
            # to pile 5 made it disappear from both My Club and the Transfer List.
            listed_item_ids = {int(row["item_id"]) for row in rows}
            unlisted_rows = connection.execute(
                "SELECT item_id,payload FROM items WHERE persona_id=? AND pile='trade' ORDER BY item_id",
                (persona_id,),
            ).fetchall()
            unlisted = 0
            for item_row in unlisted_rows:
                item_id = int(item_row["item_id"])
                if item_id in listed_item_ids:
                    continue
                try:
                    item_payload = json.loads(item_row["payload"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(item_payload, dict) or not item_payload:
                    continue
                item_payload["pile"] = 5
                item_payload["itemState"] = "free"
                item_payload["untradeable"] = False
                item_payload["tradeable"] = True
                resource = int(item_payload.get("resourceId", item_payload.get("assetId", 0)) or 0)
                player = MARKET_PLAYER_BY_RESOURCE.get(resource) or item_payload
                auction = self._market_auction(
                    player, owner=True, trade_id=0, starting_bid=150, buy_now=200,
                    duration=60, item_payload=item_payload, now=now, trade_state="inactive",
                )
                # tradeId=0 is the native no-auction sentinel: the card lives in
                # the transfer pile but has not been submitted to auctionhouse.
                auction.update({
                    "tradeId": 0, "tradeState": "inactive", "expires": 0,
                    "EXPIRE_TIME": 0, "expireTime": 0, "buyNowPrice": 0,
                    "startingBid": 0, "currentBid": 0, "offers": 0,
                })
                auctions.append(auction)
                unlisted += 1

            active = sum(1 for row in rows if str(row["trade_state"]) == "active")
            sold = sum(1 for row in rows if str(row["trade_state"]) == "closed")
            total = len(auctions)
            return {"auctionInfo":auctions,"duplicateItemIdList":[],"total":total,
                    "selling":active,"sold":sold,"available":unlisted,"unlisted":unlisted,
                    # Harmless scalar aliases for the transfer-hub/list summary
                    # binders. The canonical page total remains `total`.
                    "tradePileCount":total,"tradePileItems":total,"transferListCount":total,
                    "activeCount":active,"soldCount":sold,
                    "credits":coins,"totalCredits":coins,"coins":coins}

    def withdraw_listing(self, trade_id: int) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            row = connection.execute("SELECT * FROM market_listings WHERE persona_id=? AND trade_id=?",(persona_id,int(trade_id))).fetchone()
            if row is not None:
                state = str(row["trade_state"])
                if state == "active":
                    item = connection.execute("SELECT payload,asset_id FROM items WHERE persona_id=? AND item_id=?",(persona_id,int(row["item_id"]))).fetchone()
                    if item is not None:
                        payload = json.loads(item["payload"] or "{}")
                        payload["pile"] = 7
                        payload["itemState"] = "free"
                        connection.execute("UPDATE items SET pile='club',payload=? WHERE persona_id=? AND item_id=?",
                                           (json.dumps(payload,separators=(",",":"),ensure_ascii=False),persona_id,int(row["item_id"])))
                elif state == "closed":
                    # Sold ItemData is retained only so the retail transfer-list
                    # screen can render it safely. Clearing Sold finalizes removal.
                    connection.execute(
                        "DELETE FROM items WHERE persona_id=? AND item_id=?",
                        (persona_id, int(row["item_id"])),
                    )
                # Closed = clear sold; active = withdraw. A sold card is never
                # resurrected back into the club.
                connection.execute("DELETE FROM market_listings WHERE persona_id=? AND trade_id=?",(persona_id,int(trade_id)))
        return {"id":int(trade_id),"tradeId":int(trade_id)}

    def empty_auctions(self) -> dict[str, Any]:
        return {"auctionInfo": [], "duplicateItemIdList": [], "total": 0}

    def empty_purchased_items(self) -> dict[str, Any]:
        return self.purchased_items()

    def save_squad(self, document: dict[str, Any], requested_id: int | None = None) -> dict[str, Any]:
        """Persist retail PUT/POST /squad/{id} without allowing parser-corruption writes.

        FIFA 14 can briefly write a nearly-empty squad while its frontend is still
        resolving ItemData.  The BETA 2.20 capture proved that the first write after
        loading the squad contained only the goalkeeper even though 22/23 slots were
        valid in the persistent DB.  Treat that specific sparse write as a refresh
        acknowledgement: keep the existing players *and* squad metadata verbatim.
        """
        if not isinstance(document, dict):
            raise ValueError("squad body must be a JSON object")
        players = document.get("players")
        # The retail tournament handoff sends a partial captain/kicktakers PUT
        # immediately before MatchReady. It intentionally contains no players array.
        if players is None:
            return self.squad_list()
        if not isinstance(players, list):
            raise ValueError("squad players must be an array")
        formation = str(document.get("formation") or "f442")
        squad_name = str(document.get("squadName") or "Local XI").strip()[:32] or "Local XI"
        chemistry = self._bounded_int(document.get("chemistry", 0), 0, minimum=0, maximum=100)
        star_rating = self._bounded_int(
            document.get("starRating", document.get("rating", 0)), 0, minimum=0, maximum=100
        )

        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            fut_user = self._ensure_fut_user_locked(connection)
            persona_id = int(identity["persona_id"])
            self._repair_owned_items_locked(connection, persona_id)
            active_id = fut_user["active_squad_id"]
            squad_id = int(active_id) if requested_id in (None, 0) and active_id is not None else int(requested_id or 0)
            row = None
            if squad_id > 0:
                row = connection.execute(
                    "SELECT squad_id,squad_name,formation,active,chemistry,star_rating FROM squads "
                    "WHERE persona_id = ? AND squad_id = ?", (persona_id, squad_id)
                ).fetchone()
            if row is None:
                cursor = connection.execute(
                    "INSERT INTO squads (persona_id, squad_name, formation, active, chemistry, star_rating) VALUES (?, ?, ?, 1, ?, ?)",
                    (persona_id, squad_name, formation, chemistry, star_rating),
                )
                squad_id = int(cursor.lastrowid)
                row = connection.execute(
                    "SELECT squad_id,squad_name,formation,active,chemistry,star_rating FROM squads WHERE squad_id=?",
                    (squad_id,),
                ).fetchone()

            existing_rows = {
                int(existing["slot_index"]): existing
                for existing in connection.execute(
                    "SELECT * FROM squad_players WHERE squad_id = ?", (squad_id,)
                ).fetchall()
            }
            incoming: dict[int, sqlite3.Row | None] = {}
            incoming_kit_numbers: dict[int, int] = {}
            for entry in players:
                if not isinstance(entry, dict):
                    continue
                try:
                    index = int(entry.get("index", -1))
                except (TypeError, ValueError):
                    continue
                if not 0 <= index < 23:
                    continue
                item_data = entry.get("itemData")
                raw_id = 0
                if isinstance(item_data, dict):
                    try:
                        raw_id = int(item_data.get("id") or item_data.get("itemId") or item_data.get("assetId") or 0)
                    except (TypeError, ValueError):
                        raw_id = 0
                incoming[index] = self._resolve_item_locked(connection, persona_id, raw_id) if raw_id > 0 else None
                incoming_kit_numbers[index] = self._bounded_int(entry.get("kitNumber", 0), 0, minimum=0, maximum=99)

            existing_nonzero = sum(1 for value in existing_rows.values() if int(value["item_id"]) > 0)
            incoming_recognized = sum(1 for value in incoming.values() if value is not None)
            sparse_legacy_write = existing_nonzero >= 11 and incoming_recognized < MIN_RECOGNIZED_SQUAD_PLAYERS

            if sparse_legacy_write:
                # Do not let the transient GK-only/mostly-zero parser state destroy
                # either the 23 slots or the known-good chemistry/rating/name.  Only
                # reaffirm which existing squad is active and acknowledge the PUT.
                connection.execute("UPDATE squads SET active = 0 WHERE persona_id = ?", (persona_id,))
                connection.execute("UPDATE squads SET active = 1 WHERE squad_id = ?", (squad_id,))
                connection.execute(
                    "UPDATE fut_users SET active_squad_id = ? WHERE persona_id = ?", (squad_id, persona_id)
                )
                connection.commit()
                return self.squad_list()

            connection.execute("UPDATE squads SET active = 0 WHERE persona_id = ?", (persona_id,))
            connection.execute(
                "UPDATE squads SET squad_name = ?, formation = ?, active = 1, chemistry = ?, star_rating = ? WHERE squad_id = ?",
                (squad_name, formation, chemistry, star_rating, squad_id),
            )
            connection.execute("DELETE FROM squad_players WHERE squad_id = ?", (squad_id,))
            used_items: set[int] = set()
            for index in range(23):
                item = incoming.get(index)
                kit_number = incoming_kit_numbers.get(index, 0)
                self._write_squad_slot_locked(connection, squad_id, index, item, kit_number=kit_number)
                if item is not None:
                    used_items.add(int(item["item_id"]))

            connection.execute(
                "UPDATE fut_users SET active_squad_id = ? WHERE persona_id = ?", (squad_id, persona_id)
            )
            for item_row in connection.execute(
                "SELECT * FROM items WHERE persona_id = ? AND item_type = ? AND pile NOT IN ('trade','pending')", (persona_id, PLAYER_ITEM_TYPE)
            ).fetchall():
                item_id = int(item_row["item_id"])
                if int(item_row["asset_id"]) not in PLAYER_REFERENCE_BY_ASSET:
                    continue
                try:
                    existing_payload = json.loads(item_row["payload"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    existing_payload = {}
                in_squad = item_id in used_items
                payload = self._canonical_player_payload(
                    item_id=item_id, asset_id=int(item_row["asset_id"]),
                    existing=existing_payload if isinstance(existing_payload, dict) else {},
                    pile=7,
                )
                connection.execute(
                    "UPDATE items SET item_type = ?, pile = ?, payload = ? WHERE item_id = ? AND persona_id = ?",
                    (
                        PLAYER_ITEM_TYPE, "squad" if in_squad else "club",
                        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                        item_id, persona_id,
                    ),
                )
        return self.squad_list()

    def squad_list(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection, connection:
            identity = self._identity(connection)
            persona_id = int(identity["persona_id"])
            self._repair_owned_items_locked(connection, persona_id)
            self._repair_active_squad_locked(connection, persona_id)
            squads = []
            for row in connection.execute(
                "SELECT squad_id, squad_name, formation, active, chemistry, star_rating FROM squads WHERE persona_id = ? ORDER BY squad_id",
                (persona_id,),
            ).fetchall():
                rows = {
                    int(player["slot_index"]): player
                    for player in connection.execute(
                        "SELECT * FROM squad_players WHERE squad_id = ? ORDER BY slot_index", (row["squad_id"],)
                    ).fetchall()
                }
                players = []
                for slot_index in range(23):
                    player = rows.get(slot_index)
                    item_document: dict[str, Any] = {"id": 0}
                    if player is not None and int(player["item_id"]) > 0:
                        item = connection.execute(
                            "SELECT * FROM items WHERE persona_id = ? AND item_id = ?", (persona_id, int(player["item_id"]))
                        ).fetchone()
                        if item is not None and int(item["asset_id"]) in PLAYER_REFERENCE_BY_ASSET:
                            try:
                                existing = json.loads(item["payload"] or "{}")
                            except (TypeError, json.JSONDecodeError):
                                existing = {}
                            item_document = self._canonical_player_payload(
                                item_id=int(item["item_id"]), asset_id=int(item["asset_id"]),
                                existing=existing if isinstance(existing, dict) else {}, pile=7, slot_index=slot_index,
                            )
                    players.append({
                        "index": slot_index,
                        "itemData": item_document,
                        "kitNumber": 0 if player is None else int(player["kit_number"] or 0),
                    })
                captain = next((int(p["itemData"].get("id", 0)) for p in players[:11] if int(p["itemData"].get("id", 0)) > 0), 0)
                rating = int(row["star_rating"] or 0)
                squads.append({
                    "id": int(row["squad_id"]), "squadId": int(row["squad_id"]),
                    "personaId": persona_id,
                    "squadName": row["squad_name"], "formation": row["formation"],
                    "active": bool(row["active"]),
                    "changed": False,
                    "captain": captain,
                    "chemistry": int(row["chemistry"] or 0),
                    "starRating": rating,
                    # Retail SquadDetails carries both rating and starRating plus
                    # explicit validity/new-squad/taker/tactics members.  Supplying
                    # them prevents an old frontend from manufacturing a partial
                    # default squad before it has parsed all 23 ItemData records.
                    "rating": rating,
                    "valid": True,
                    "newsquad": 0,
                    "kicktakers": [],
                    "tactics": [],
                    "dreamSquad": False,
                    "custom": "",
                    "manager": [],
                    "actives": [],
                    "players": players,
                })
            return {"squadList": squads, "squad": squads}

    @staticmethod
    def _compact_squad_record(squad: dict[str, Any]) -> dict[str, Any]:
        """Retail /squad/list record: metadata only; player ItemData is fetched separately."""
        return {
            "id": int(squad.get("id", squad.get("squadId", 0)) or 0),
            "personaId": int(squad.get("personaId", 0) or 0),
            "squadName": str(squad.get("squadName") or "Local XI"),
            "formation": str(squad.get("formation") or "f442"),
            "active": bool(squad.get("active", False)),
            "changed": bool(squad.get("changed", False)),
            "chemistry": int(squad.get("chemistry", 0) or 0),
            "starRating": int(squad.get("starRating", squad.get("rating", 0)) or 0),
            "rating": int(squad.get("rating", squad.get("starRating", 0)) or 0),
            "valid": bool(squad.get("valid", True)),
            "newsquad": int(squad.get("newsquad", 0) or 0),
        }

    def squad_list_compact(self) -> dict[str, Any]:
        """Return the retail SquadListResponse contract used by GET /squad/list."""
        full = self.squad_list()
        squads = full.get("squadList", full.get("squad", [])) if isinstance(full, dict) else []
        compact = [self._compact_squad_record(row) for row in squads if isinstance(row, dict)]
        active = next((row for row in compact if row.get("active")), compact[0] if compact else None)
        return {
            "activeSquadId": int(active.get("id", 0) if active else 0),
            "squad": compact,
        }

    def squad_detail(self, requested_id: int | None = None) -> dict[str, Any]:
        """Return one full SquadDetailsResponse, including all 23 slots."""
        listing = self.squad_list()
        squads = listing.get("squadList", listing.get("squad", [])) if isinstance(listing, dict) else []
        if requested_id not in (None, 0):
            for squad in squads:
                if isinstance(squad, dict) and int(squad.get("id", squad.get("squadId", 0)) or 0) == int(requested_id):
                    return dict(squad)
        for squad in squads:
            if isinstance(squad, dict) and squad.get("active"):
                return dict(squad)
        return dict(squads[0]) if squads else {}

    def active_squad_document(self) -> dict[str, Any]:
        """Return the active native squad record used by CreateMatch."""
        return self.squad_detail(None)

    def store_pack_quantities(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            rows = connection.execute(
                "SELECT pack_type, COUNT(*) AS quantity FROM packs WHERE persona_id = ? AND unopened = 1 GROUP BY pack_type ORDER BY pack_type",
                (identity["persona_id"],),
            ).fetchall()
            return {"packList": [{"packType": int(row["pack_type"]), "quantity": int(row["quantity"])} for row in rows]}

    def snapshot(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            identity = self._identity(connection)
            club = connection.execute("SELECT * FROM clubs WHERE persona_id = ?", (identity["persona_id"],)).fetchone()
            fut_user = connection.execute("SELECT * FROM fut_users WHERE persona_id = ?", (identity["persona_id"],)).fetchone()
            session_count = int(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
            player_count = int(connection.execute("SELECT COUNT(*) FROM squad_players").fetchone()[0])
            actions = self._user_actions_locked(connection, int(identity["persona_id"]))
            return {
                "nucleusId": int(identity["nucleus_id"]),
                "personaId": int(identity["persona_id"]),
                "personaName": identity["persona_name"],
                "platform": identity["platform"],
                "trusted": bool(identity["trusted"]),
                "sessionCount": session_count,
                "onboardingStage": "club-created" if club is not None else "fut-user-created" if fut_user is not None else "security-verified" if identity["trusted"] else "new",
                "club": None if club is None else self._club_document(club),
                "activeSquadId": None if fut_user is None else fut_user["active_squad_id"],
                "squadPlayerCount": player_count,
                "userActions": actions,
                "packCatalogCount": len(PACK_DEFINITIONS),
                "unopenedPackCount": int(connection.execute("SELECT COUNT(*) FROM packs WHERE persona_id = ? AND unopened = 1", (identity["persona_id"],)).fetchone()[0]),
                "verifiedLivePlayerPool": len(PLAYER_CATALOG),
                "playerCatalogCount": len(PLAYER_CATALOG),
                "ownedPlayerCount": int(connection.execute(
                    "SELECT COUNT(*) FROM items WHERE persona_id = ? AND item_type = ?",
                    (identity["persona_id"], PLAYER_ITEM_TYPE),
                ).fetchone()[0]),
                "managerReferenceCount": len(MANAGER_CATALOG_DOCUMENT.get("managers", [])),
                "managerLiveEmission": bool(MANAGER_CATALOG_DOCUMENT.get("liveEmissionEnabled", False)),
            }
