"""Verify that a persona can own, address and delete more than one FUT squad.

The persistent schema was always keyed by ``squad_id``; what pinned the local
server to a single squad was the write path always resolving to the active
squad, always force-activating whatever it wrote, and demoting every player not
in that one squad back to My Club.  These checks pin the fixed behaviour.
"""
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

import beta_identity as beta_identity_module
from beta_identity import BetaIdentityStore


def fail(message: str) -> None:
    raise AssertionError(message)


def squad_by_id(store: BetaIdentityStore, squad_id: int) -> dict:
    for row in store.squad_list().get("squadList", []):
        if int(row.get("id", 0)) == int(squad_id):
            return row
    fail(f"squad {squad_id} is missing from /squad list")
    return {}


def item_ids(squad: dict) -> list[int]:
    return [int((row.get("itemData") or {}).get("id", 0) or 0) for row in squad.get("players", [])]


def piles(db: Path, ids: list[int]) -> dict[int, str]:
    with closing(sqlite3.connect(db)) as connection:
        return {
            int(row[0]): str(row[1]).lower()
            for row in connection.execute(
                "SELECT item_id, pile FROM items WHERE item_id IN (%s)" % ",".join("?" * len(ids)),
                [int(value) for value in ids],
            ).fetchall()
        }


def owned_item_count(db: Path) -> int:
    with closing(sqlite3.connect(db)) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0])


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "beta.sqlite3"
        report = Path(td) / "fifa14-match-assets-v2411-beta222.json"
        report.write_text(json.dumps({"catalog": {"kits": [], "stadiums": [], "badges": []}}), encoding="utf-8")
        beta_identity_module.MATCH_ASSET_REPORT = report
        store = BetaIdentityStore(str(db), "existing")

        first = store.squad_list()["squadList"][0]
        first_id = int(first["id"])
        first_ids = item_ids(first)
        starters = [value for value in first_ids if value > 0][:11]
        if len(starters) < 11:
            fail(f"starter squad must be populated before this check: {first_ids}")

        # 1. A write addressed to an unknown id creates a second squad under that
        #    id instead of overwriting the first.
        second_id = first_id + 1
        second_players = [
            {"index": index, "itemData": {"id": starters[index] if index < 11 else 0}, "kitNumber": 0}
            for index in range(23)
        ]
        store.save_squad(
            {"id": second_id, "squadName": "Second XI", "formation": "f433",
             "chemistry": 42, "starRating": 55, "players": second_players},
            requested_id=second_id,
        )
        squads = store.squad_list()["squadList"]
        if len(squads) != 2:
            fail(f"saving an unknown squad id must create a second squad, got {[s['id'] for s in squads]}")
        second = squad_by_id(store, second_id)
        if str(second.get("squadName")) != "Second XI" or str(second.get("formation")) != "f433":
            fail(f"second squad metadata was not persisted: {second}")
        if len(second.get("players", [])) != 23:
            fail(f"second squad must serialize all 23 slots: {len(second.get('players', []))}")
        if item_ids(squad_by_id(store, first_id)) != first_ids:
            fail("creating a second squad mutated the first squad's slots")

        # 1b. The shapes the FIFA 14 squad hub actually sends, from the BETA
        #     capture: POST /squad with no path id and id 0 creates a squad -- an
        #     empty one, or a copy carrying the source squad's 23 slots.  Both
        #     used to resolve to the active squad and overwrite the only squad.
        created_empty = store.create_squad({
            "id": 0, "squadName": "test", "formation": "f442",
            "chemistry": 0, "rating": 0, "starRating": 0, "manager": [], "players": [],
        })
        empty_id = int(created_empty.get("id", 0))
        if empty_id in (0, first_id, second_id):
            fail(f"POST /squad with id 0 must create a new squad, got id {empty_id}")
        if str(created_empty.get("squadName")) != "test":
            fail(f"created squad kept the wrong name: {created_empty}")
        if any(value > 0 for value in item_ids(created_empty)):
            fail("an empty create must not inherit another squad's players")
        if int(store.squad_list_compact()["activeSquadId"]) == empty_id:
            fail("creating an empty squad must not become the match squad")
        if item_ids(squad_by_id(store, first_id)) != first_ids:
            fail("creating a squad mutated the first squad's slots")

        created_copy = store.create_squad({
            "id": 0, "squadName": "COPY  Second XI", "formation": "f433d",
            "chemistry": 42, "rating": 55, "starRating": 55, "manager": [],
            "players": second_players,
        })
        copy_id = int(created_copy.get("id", 0))
        if copy_id in (0, first_id, second_id, empty_id):
            fail(f"copying a squad must create a distinct squad, got id {copy_id}")
        if item_ids(created_copy) != item_ids(squad_by_id(store, second_id)):
            fail("a copy create must carry the copied slots")
        if len(store.squad_list()["squadList"]) != 4:
            fail("expected four squads after two creates")

        # 1c. A players-less PUT is the squad hub's rename.  Dropping it made
        #     renames revert on the next list refresh.
        store.save_squad({"id": empty_id, "squadName": "local"}, requested_id=empty_id)
        if str(squad_by_id(store, empty_id).get("squadName")) != "local":
            fail("a players-less PUT carrying squadName must persist the rename")
        if int(store.squad_list_compact()["activeSquadId"]) == empty_id:
            fail("a rename must not change the active squad")
        # The tournament handoff uses the same shape for captain/kicktakers only.
        before_handoff = squad_by_id(store, first_id)
        store.save_squad({"id": first_id, "captain": starters[0], "kicktakers": [starters[0]]})
        if squad_by_id(store, first_id) != before_handoff:
            fail("a captain/kicktakers-only PUT must not alter squad state")

        # 1d. Leaving the squad editor sends every slot and the formation but no
        #     squadName.  Absent metadata must keep its stored value: defaulting
        #     it reverted every renamed squad to "Local XI" on exit.
        store.save_squad(
            {"id": copy_id, "formation": "f4312", "players": second_players,
             "chemistry": 44, "starRating": 57},
            requested_id=copy_id,
        )
        edited = squad_by_id(store, copy_id)
        if str(edited.get("squadName")) != "COPY  Second XI":
            fail(f"a nameless editor write reverted the squad name: {edited.get('squadName')}")
        if str(edited.get("formation")) != "f4312":
            fail(f"a nameless editor write must still persist the formation: {edited}")
        store.save_squad({"id": copy_id, "players": second_players}, requested_id=copy_id)
        kept = squad_by_id(store, copy_id)
        if str(kept.get("squadName")) != "COPY  Second XI" or str(kept.get("formation")) != "f4312":
            fail(f"a slots-only write must keep name and formation: {kept}")
        if int(kept.get("chemistry", -1)) != 44 or int(kept.get("starRating", -1)) != 57:
            fail(f"a slots-only write must keep chemistry and rating: {kept}")

        store.delete_squad(empty_id)
        store.delete_squad(copy_id)
        if len(store.squad_list()["squadList"]) != 2:
            fail("failed to return to the two-squad fixture")

        # 2. Cards fielded by the first squad keep pile 'squad' after a write to
        #    the second one.  This is the regression that made multi-squad state
        #    collapse: the save loop demoted every card outside the saved squad.
        first_only = [value for value in first_ids if value > 0 and value not in starters]
        if not first_only:
            fail("expected the first squad to field cards the second one does not")
        stale = {item: pile for item, pile in piles(db, first_only).items() if pile != "squad"}
        if stale:
            fail(f"cards in another squad were demoted to My Club: {stale}")

        # 3. Save-with-active is unchanged (the saved squad is selected), and an
        #    explicit active:false leaves the user's selection alone.
        store.set_active_squad(first_id)
        store.save_squad(
            {"id": second_id, "squadName": "Second XI", "formation": "f433",
             "chemistry": 42, "starRating": 55, "players": second_players},
            requested_id=second_id,
        )
        if int(store.squad_list_compact()["activeSquadId"]) != second_id:
            fail("a save without an explicit active flag must keep selecting the saved squad")
        store.set_active_squad(first_id)
        compact = store.squad_list_compact()
        if int(compact["activeSquadId"]) != first_id:
            fail(f"set_active_squad did not switch the selection: {compact}")
        if sum(1 for row in compact["squad"] if row.get("active")) != 1:
            fail(f"exactly one squad may be active: {compact}")
        store.save_squad(
            {"id": second_id, "squadName": "Second XI", "formation": "f433", "active": False,
             "chemistry": 43, "starRating": 56, "players": second_players},
            requested_id=second_id,
        )
        if int(store.squad_list_compact()["activeSquadId"]) != first_id:
            fail("saving an unselected squad must not steal the active selection")
        if int(squad_by_id(store, second_id).get("chemistry", 0)) != 43:
            fail("an unselected save must still persist that squad's own state")

        # 4. Details and persistence are addressable per squad.
        if int(store.squad_detail(second_id).get("id", 0)) != second_id:
            fail("squad_detail must return the requested squad, not the active one")
        store = BetaIdentityStore(str(db), "existing")
        reopened = store.squad_list()["squadList"]
        if len(reopened) != 2 or int(store.squad_list_compact()["activeSquadId"]) != first_id:
            fail(f"multi-squad state did not survive a store reopen: {[s['id'] for s in reopened]}")

        # 5. Deleting a squad frees its cards without destroying them, and the
        #    final remaining squad cannot be deleted.
        owned_before = owned_item_count(db)
        store.delete_squad(second_id)
        squads = store.squad_list()["squadList"]
        if len(squads) != 1 or int(squads[0]["id"]) != first_id:
            fail(f"delete_squad removed the wrong squad: {[s['id'] for s in squads]}")
        owned_after = owned_item_count(db)
        if owned_after != owned_before:
            fail(f"delete_squad destroyed owned cards: {owned_before} -> {owned_after}")
        if item_ids(squad_by_id(store, first_id)) != first_ids:
            fail("delete_squad disturbed the surviving squad")
        store.delete_squad(first_id)
        if len(store.squad_list()["squadList"]) != 1:
            fail("the last remaining squad must not be deletable")
        if int(store.squad_list_compact()["activeSquadId"]) != first_id:
            fail("the persona must always keep an active squad")

        # 6. Cards dropped from every squad go back to My Club.
        benched = [value for value in first_ids if value > 0][11:]
        if not benched:
            fail("expected reserves to bench for the My Club check")
        store.save_squad(
            {"id": first_id, "squadName": str(squads[0].get("squadName")),
             "formation": str(squads[0].get("formation")),
             "chemistry": int(squads[0].get("chemistry", 0)),
             "starRating": int(squads[0].get("starRating", 0)),
             "players": [
                 {"index": index, "itemData": {"id": starters[index] if index < 11 else 0}, "kitNumber": 0}
                 for index in range(23)
             ]},
            requested_id=first_id,
        )
        wrong = {item: pile for item, pile in piles(db, benched).items() if pile != "club"}
        if wrong:
            fail(f"cards removed from every squad must return to My Club: {wrong}")

    print("FIFA 14 multi-squad verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
