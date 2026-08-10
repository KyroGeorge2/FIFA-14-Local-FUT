from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from beta_identity import BetaIdentityStore
from local_identity import CONSUMABLE_BY_RESOURCE, CONSUMABLE_CATALOG


def fail(message: str) -> None:
    raise AssertionError(message)


def owned_payload(db: Path, item_id: int) -> dict:
    with closing(sqlite3.connect(db)) as con:
        row = con.execute("SELECT payload FROM items WHERE item_id=?", (int(item_id),)).fetchone()
    if row is None:
        fail(f"owned item {item_id} missing")
    return json.loads(row[0])


def mutate_payload(db: Path, item_id: int, **changes) -> dict:
    payload = owned_payload(db, item_id)
    payload.update(changes)
    with closing(sqlite3.connect(db)) as con, con:
        con.execute("UPDATE items SET payload=? WHERE item_id=?", (json.dumps(payload), int(item_id)))
    return payload


def grant(store: BetaIdentityStore, db: Path, resource_id: int, serial: int) -> int:
    definition = CONSUMABLE_BY_RESOURCE[int(resource_id)]
    item_id = 199_000_000_000 + int(serial)
    payload = store._local_consumable_payload(item_id=item_id, consumable=definition)
    with closing(sqlite3.connect(db)) as con, con:
        persona_id = int(con.execute("SELECT persona_id FROM identity LIMIT 1").fetchone()[0])
        con.execute(
            "INSERT INTO items(item_id,persona_id,asset_id,item_type,pile,tradeable,payload) VALUES(?,?,?,?,?,?,?)",
            (item_id, persona_id, int(definition.get("cardassetid", 0)), str(payload.get("itemType", "development")),
             "club", 1, json.dumps(payload, separators=(",", ":"))),
        )
    return item_id


def main() -> int:
    if len(CONSUMABLE_CATALOG) != 151:
        fail(f"expected 151 union consumable definitions, got {len(CONSUMABLE_CATALOG)}")
    eligible = [row for row in CONSUMABLE_CATALOG if bool(row.get("packEligible", True))]
    if len(eligible) != 128:
        fail(f"expected 128 retail-pack-eligible definitions, got {len(eligible)}")
    if len([row for row in eligible if row.get("category") == "GK Training"]) != 21:
        fail("21 goalkeeper training definitions were not added to the pack pool")
    if any(5003079 <= int(row["resourceId"]) <= 5003094 and row.get("packEligible") for row in CONSUMABLE_CATALOG):
        fail("unmapped asset-35 internal playStyle rows leaked into packs")
    if any(5004007 <= int(row["resourceId"]) <= 5004012 and row.get("packEligible") for row in CONSUMABLE_CATALOG):
        fail("unmapped asset-43 internal position rows leaked into packs")

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "consumables.sqlite3"
        store = BetaIdentityStore(str(db), "existing")
        squad = store.squad_list()["squadList"][0]["players"]
        players = [row["itemData"] for row in squad if int(row.get("itemData", {}).get("id", 0)) > 0]
        gk = next(row for row in players if row.get("preferredPosition") == "GK")
        outfield = next(row for row in players if row.get("preferredPosition") != "GK")
        outfield_id = int(outfield["id"])
        gk_id = int(gk["id"])

        serial = 0
        def use(resource_id: int, target: int) -> dict:
            nonlocal serial
            serial += 1
            owned = grant(store, db, resource_id, serial)
            before = owned_payload(db, target)
            result = store.apply_consumable(resource_id, [target])
            with closing(sqlite3.connect(db)) as con:
                still_owned = con.execute("SELECT 1 FROM items WHERE item_id=?", (owned,)).fetchone()
            if still_owned is not None:
                fail(f"consumable {resource_id} was not consumed")
            return {"before": before, "after": owned_payload(db, target), "result": result}

        # Contract uses the target player's quality-specific yield, not the consumable card's own tier.
        mutate_payload(db, outfield_id, contract=5)
        state = use(5001003, outfield_id)  # gold common player contract -> +15 on a bronze player
        if int(state["after"].get("contract", -1)) != 20:
            fail(f"player contract apply failed: {state}")

        # Player fitness.
        mutate_payload(db, outfield_id, fitness=40)
        state = use(5002001, outfield_id)
        if int(state["after"].get("fitness", -1)) != 60:
            fail(f"player fitness apply failed: {state}")

        # Healing respects injury type and number of matches.
        mutate_payload(db, outfield_id, injuryType="head", injuryGames=3)
        state = use(5002007, outfield_id)
        if int(state["after"].get("injuryGames", -1)) != 2:
            fail(f"healing apply failed: {state}")

        # Position modifier persists through canonicalization.
        mutate_payload(db, outfield_id, preferredPosition="ST")
        state = use(5003078, outfield_id)  # ST -> CF
        if state["after"].get("preferredPosition") != "CF" or int(state["after"].get("cardsubtypeid", -1)) != 1:
            fail(f"position modifier apply failed: {state}")

        # Chemistry style is permanent until another style is applied.
        state = use(5003096, outfield_id)  # SNIPER -> playStyle 1
        if int(state["after"].get("playStyle", -1)) != 1:
            fail(f"chemistry style apply failed: {state}")

        # Outfield training is temporary and boosts the selected face stat for one match.
        base_attrs = list(owned_payload(db, outfield_id)["attributeArray"])
        state = use(5003022, outfield_id)  # PAC +5
        trained = state["after"]
        if int(trained["attributeArray"][0]) != min(99, int(base_attrs[0]) + 5) or int(trained.get("training", 0)) == 0:
            fail(f"outfield training apply failed: {state}")
        with closing(sqlite3.connect(db)) as con, con:
            con.row_factory = sqlite3.Row
            persona_id = int(con.execute("SELECT persona_id FROM identity LIMIT 1").fetchone()[0])
            row = con.execute("SELECT asset_id FROM items WHERE item_id=?", (outfield_id,)).fetchone()
            if row is None:
                fail("trained player disappeared")
            store._apply_match_end_items_locked(
                con, persona_id, [{"id": outfield_id, "fitness": 90}],
                starting_xi_ids=[outfield_id], decrement_contracts=True,
            )
        expired = owned_payload(db, outfield_id)
        if list(expired.get("attributeArray", [])) != base_attrs or int(expired.get("training", -1)) != 0:
            fail(f"one-match training did not expire cleanly: before={base_attrs} after={expired}")
        with closing(sqlite3.connect(db)) as con:
            effect = con.execute(
                "SELECT 1 FROM consumable_effects WHERE item_id=? AND effect_type='training'", (outfield_id,)
            ).fetchone()
        if effect is not None:
            fail("expired training effect row remained in database")

        # Goalkeeper training uses the 21-card asset-3 family from the supplied dataset.
        gk_base = list(owned_payload(db, gk_id)["attributeArray"])
        state = use(5003001, gk_id)  # DIV +5
        if int(state["after"]["attributeArray"][0]) != min(99, int(gk_base[0]) + 5):
            fail(f"goalkeeper training apply failed: {state}")

        # Squad fitness updates all 23 active-squad cards once.
        squad_ids = [int(row["id"]) for row in players]
        for player_id in squad_ids:
            mutate_payload(db, player_id, fitness=40)
        serial += 1
        owned = grant(store, db, 5002004, serial)  # bronze squad fitness +10
        result = store.apply_consumable(5002004, [outfield_id])
        if any(int(owned_payload(db, player_id).get("fitness", -1)) != 50 for player_id in squad_ids):
            fail(f"squad fitness did not update every active squad card: {result}")
        with closing(sqlite3.connect(db)) as con:
            if con.execute("SELECT 1 FROM items WHERE item_id=?", (owned,)).fetchone() is not None:
                fail("squad fitness card was not consumed")

        # Pack picker must be able to emit GK training in every tier and must never emit excluded rows.
        import random
        seen_gk = set()
        for quality in ("bronze", "silver", "gold"):
            rng = random.Random(f"beta224-{quality}")
            for i in range(500):
                row = store._weighted_consumable(rng, quality=quality, rare_slot=bool(i % 4 == 0))
                if not bool(row.get("packEligible", True)):
                    fail(f"excluded consumable emitted into {quality} pool: {row}")
                if row.get("category") == "GK Training":
                    seen_gk.add(quality)
            if quality not in seen_gk:
                fail(f"GK training was unreachable in {quality} packs")

        print(json.dumps({
            "status": "ok",
            "catalogDefinitions": len(CONSUMABLE_CATALOG),
            "packEligibleDefinitions": len(eligible),
            "gkTrainingDefinitions": 21,
            "tested": ["contract", "player fitness", "squad fitness", "healing", "position", "chemistry style", "player training expiry", "GK training", "pack pools"],
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
