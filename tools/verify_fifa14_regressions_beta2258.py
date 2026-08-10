#!/usr/bin/env python3
from __future__ import annotations

import collections
import json
import random
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import local_identity as li  # noqa: E402
from beta_identity import BetaIdentityStore  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def grant_consumable(store: BetaIdentityStore, db: Path, resource_id: int, serial: int) -> int:
    definition = li.CONSUMABLE_BY_RESOURCE[int(resource_id)]
    item_id = 198_580_000_000 + int(serial)
    payload = store._local_consumable_payload(item_id=item_id, consumable=definition)
    with closing(sqlite3.connect(db)) as con, con:
        persona_id = int(con.execute("SELECT persona_id FROM identity LIMIT 1").fetchone()[0])
        con.execute(
            "INSERT INTO items(item_id,persona_id,asset_id,item_type,pile,tradeable,payload) VALUES(?,?,?,?,?,?,?)",
            (item_id, persona_id, int(definition.get("cardassetid", 0)), str(payload.get("itemType", "development")),
             "club", 1, json.dumps(payload, separators=(",", ":"))),
        )
    return item_id


def insert_player(store: BetaIdentityStore, db: Path, *, player: dict, item_id: int, pile: str) -> dict:
    initial = dict(player)
    initial.update({"untradeable": False, "tradeable": True, "contract": 7, "fitness": 99, "morale": 99,
                    "pile": 6 if pile == "pending" else 7, "itemState": "new" if pile == "pending" else "free",
                    "discardValue": store._player_discard_value(player)})
    payload = store._canonical_player_payload(item_id=item_id, asset_id=int(player["assetId"]), existing=initial,
                                              pile=6 if pile == "pending" else 7)
    payload["untradeable"] = False
    payload["tradeable"] = True
    payload["discardValue"] = store._player_discard_value(player)
    if bool(player.get("specialCard")) or int(player.get("rareFlag", 0) or 0) > 1:
        payload["specialCard"] = True
        payload["cardType"] = str(player.get("cardType", "special"))
        payload["version"] = int(player.get("version", 1) or 1)
    with closing(sqlite3.connect(db)) as con, con:
        persona_id = int(con.execute("SELECT persona_id FROM identity LIMIT 1").fetchone()[0])
        con.execute("INSERT INTO items(item_id,persona_id,asset_id,item_type,pile,tradeable,payload) VALUES(?,?,?,?,?,?,?)",
                    (item_id, persona_id, int(player["assetId"]), li.PLAYER_ITEM_TYPE, pile, 1,
                     json.dumps(payload, separators=(",", ":"))))
    return payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fifa14-beta2258-") as td:
        db = Path(td) / "regressions.sqlite3"
        store = BetaIdentityStore(str(db), "existing")

        # The real PC client requests the context-6 StickerBook response and relies
        # on /clubUser as a face-card cache. Exercise both paths, not just scalars.
        granted = [5001003, 5002001, 5003022]
        for i, rid in enumerate(granted, 1):
            grant_consumable(store, db, rid, i)
        stats = store.consumable_stats()
        require(int(stats.get("consumables", 0)) >= 3, f"aggregate consumable count missing: {stats}")
        require(len(stats.get("stat", [])) >= 3 and len(stats.get("entries", [])) >= 3,
                f"context-6 StickerBook rows missing: {stats}")
        require(all(int(row.get("contextId", -1)) == 6 for row in stats["stat"]),
                f"wrong consumable context rows: {stats['stat'][:3]}")
        bootstrap = store.club_items({}, include_consumables_default=True)
        bootstrap_resources = {int(row.get("resourceId", 0)) for row in bootstrap.get("itemData", [])
                               if str(row.get("itemType", "")) in {"development", "training"}}
        require(set(granted) <= bootstrap_resources,
                f"/clubUser cache bootstrap lost consumables: {sorted(bootstrap_resources)[:20]}")

        # Purchased market card must never mark itself duplicate. A base version
        # and a special version of the same footballer are also distinct FUT cards.
        suarez_base = li.PLAYER_BY_ASSET[176580]
        suarez_special = next(p for p in li.NORMAL_SPECIAL_PLAYER_CATALOG
                              if int(p.get("assetId", 0)) == 176580 and int(p.get("resourceId", 0)) != 176580)
        base_id = 198_580_010_001
        pending_id = 198_580_010_002
        insert_player(store, db, player=suarez_base, item_id=base_id, pile="club")
        special_payload = insert_player(store, db, player=suarez_special, item_id=pending_id, pile="pending")
        purchased = store.purchased_items()
        pending_row = next(row for row in purchased["itemData"] if int(row.get("id", 0)) == pending_id)
        require(int(pending_row.get("discardValue", 0)) > 0,
                f"market/pending special has zero quick sell: {pending_row}")
        require(not any(int(row.get("itemId", -1)) == pending_id for row in purchased.get("duplicateItemIdList", [])),
                f"pending market item duplicated itself: {purchased.get('duplicateItemIdList')}")
        # Add an exact special copy to My Club: now and only now it should be a duplicate.
        exact_owned_id = 198_580_010_003
        insert_player(store, db, player=suarez_special, item_id=exact_owned_id, pile="club")
        purchased2 = store.purchased_items()
        pair = next((row for row in purchased2.get("duplicateItemIdList", [])
                     if int(row.get("itemId", -1)) == pending_id), None)
        require(pair is not None and int(pair.get("duplicateItemId", 0)) == exact_owned_id,
                f"exact special duplicate pairing failed: {purchased2.get('duplicateItemIdList')}")

        market = store.market_search({"start": ["0"], "num": ["12"], "pos": ["ST"]})
        require(market.get("auctionInfo"), "market search empty")
        require(all(int(a.get("itemData", {}).get("discardValue", 0)) > 0 for a in market["auctionInfo"]),
                "one or more transfer-market cards still have zero quick sell")

        # Verify family-first selection makes every requested normal-mode special
        # family reachable and that actual 100k packs remain capped at two specials.
        rng = random.Random(2258)
        family_draws = collections.Counter()
        for _ in range(10_000):
            player = store._weighted_special_player(rng, quality="gold")
            require(player is not None, "gold special draw unexpectedly empty")
            family_draws[str(player.get("cardType", "special"))] += 1
        for family in ("goldif", "goldblue", "motm", "toty", "green", "special"):
            require(family_draws[family] > 0, f"special family never selected: {family}; {family_draws}")

        pack_families = collections.Counter()
        max_specials = 0
        definition = li.PACK_DEFINITIONS[105]
        with closing(sqlite3.connect(db)) as con, con:
            con.row_factory = sqlite3.Row
            for pack_id in range(925_800, 926_100):
                items = store._generate_pack_contents_locked(con, pack_id=pack_id, definition=definition)
                specials = [row for row in items if bool(row.get("specialCard")) or int(row.get("rareflag", 0) or 0) > 1]
                max_specials = max(max_specials, len(specials))
                for row in specials:
                    pack_families[str(row.get("cardType", "special"))] += 1
        require(max_specials <= 2, f"100k pack exceeded two specials: {max_specials}")
        for family in ("goldif", "goldblue", "motm", "toty"):
            require(pack_families[family] > 0, f"100k simulation never emitted {family}: {pack_families}")

    probe_source = (ROOT / "server" / "probe.py").read_text(encoding="utf-8")
    require('include_consumables_default=(path_without_query == "/ut/game/fifa14/clubUser")' in probe_source,
            "/clubUser consumable cache preload route missing")
    require("fut-consumable-club-stats-beta2258" in probe_source,
            "BETA 2.25.8 consumable stats route marker missing")

    print(json.dumps({
        "ok": True,
        "build": "2.41.1-beta2.25.9",
        "clubUserConsumablePreload": True,
        "consumableStickerBookContext": 6,
        "marketFalseDuplicateFixed": True,
        "marketQuickSellNonZero": True,
        "goldSpecialFamilyDraws": dict(family_draws),
        "hundredKPackSpecialFamilies": dict(pack_families),
        "maxSpecialsObserved": max_specials,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
