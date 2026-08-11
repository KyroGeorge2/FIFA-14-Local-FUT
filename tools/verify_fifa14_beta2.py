from __future__ import annotations

import json
import os
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
from beta_identity import BetaIdentityStore, OFFLINE_COMPETITION_TEAM_IDS
from local_identity import PLAYER_CATALOG
from probe import HttpProbe


def fail(message: str) -> None:
    raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "beta.sqlite3"
        report = Path(td) / "fifa14-match-assets-v2411-beta222.json"
        report.write_text(json.dumps({
            "catalog": {
                "kits": [
                    {"assetId": 16, "resourceId": 6500000, "definitionId": 6500000, "carddbid": 6500000,
                     "teamid": 1, "category": 2, "teamkittypetechid": 0, "value": 80, "weightrare": 10, "year": 0,
                     "itemType": "kit", "name": "Team 1 Home Kit"}
                ],
                "stadiums": [
                    {"assetId": 35, "resourceId": 6200017, "definitionId": 6200017, "carddbid": 6200017,
                     "stadiumid": 35, "category": 4, "value": 66, "weightrare": 0, "year": 0,
                     "itemType": "stadium", "name": "Stadium 35"}
                ],
                "badges": [
                    {"assetId": 1, "resourceId": 6100000, "definitionId": 6100000, "carddbid": 6100000,
                     "badgeDBid": 6100000, "teamid": 1, "category": 1, "value": 75, "weightrare": 10, "year": 0,
                     "itemType": "custom", "name": "Badge 1"}
                ],
            }
        }), encoding="utf-8")
        beta_identity_module.MATCH_ASSET_REPORT = report
        store = BetaIdentityStore(str(db), "existing")
        compact_squads = store.squad_list_compact()
        if set(compact_squads) != {"activeSquadId", "squad"}:
            fail(f"/squad/list must use retail activeSquadId+squad envelope: {compact_squads}")
        if compact_squads.get("squad") and any("players" in row or "actives" in row for row in compact_squads.get("squad", []) if isinstance(row, dict)):
            fail(f"/squad/list must stay metadata-only; details are fetched separately: {compact_squads}")
        active_detail = store.active_squad_document()
        if active_detail and len(active_detail.get("players", [])) != 23:
            fail(f"/squad/active must return one full 23-slot SquadDetailsResponse: {active_detail}")
        profile = store.beta_profile_summary()
        if profile["coins"] != 0 or profile["fifaPoints"] != 0:
            fail(f"fresh BETA wallet must be 0/0, got {profile}")
        if profile["ownedItems"] != 29 or profile["squadPlayers"] != 23:
            fail(f"starter club must contain 23 players plus 3 proven and 3 scanned cosmetics, got {profile}")
        squad = store.squad_list()["squadList"][0]["players"]
        items = [row["itemData"] for row in squad if row.get("itemData", {}).get("id")]
        if len(items) != 23:
            fail("starter squad serialization lost players")
        if any(int(row.get("rating", 99)) >= 65 for row in items):
            fail("starter squad contains a non-bronze player")
        if sum(1 for row in items if row.get("preferredPosition") == "GK") != 1:
            fail("starter XI must contain exactly one bronze GK in this source catalogue")
        if any(not bool(row.get("untradeable", False)) for row in items):
            fail("starter cards must be untradeable")
        squad_doc = store.squad_list()["squadList"][0]
        if int(squad_doc.get("chemistry", -1)) != 54 or int(squad_doc.get("starRating", -1)) != 61:
            fail(f"starter squad native scalar state missing: {squad_doc}")
        actives = squad_doc.get("actives")
        if actives != []:
            fail(f"fresh BETA 2.22 must not fake active cosmetics before the retail client selects them: {actives}")

        # Exercise the real HTTP parse_qs shape as well as scalar helper calls.
        # BETA 2.14 verified only scalar values, which hid the live type=["kit"]
        # projection bypass.
        kit_inventory = store.club_items({
            "year": ["2014"], "type": ["kit"], "level": ["any"], "count": ["11"]
        }).get("itemData", [])
        stadium_inventory = store.club_items({
            "year": ["2014"], "type": ["stadium"], "level": ["any"], "count": ["11"]
        }).get("itemData", [])
        badge_inventory = store.club_items({
            "year": ["2014"], "type": ["badge"], "level": ["any"], "count": ["11"]
        }).get("itemData", [])
        if len(kit_inventory) != 3 or len(stadium_inventory) != 2 or len(badge_inventory) != 1:
            fail(f"full cosmetic My Club inventory contract is not populated: kits={len(kit_inventory)} stadiums={len(stadium_inventory)} badges={len(badge_inventory)}")
        if any(str(row.get("itemState")) != "free" for row in kit_inventory + stadium_inventory + badge_inventory):
            fail(f"fresh selectable cosmetics must start free: {kit_inventory + stadium_inventory + badge_inventory}")
        if store.club_items({"type": "2"}).get("total") != 3 or store.club_items({"type": "4"}).get("total") != 2:
            fail("numeric kit/stadium query aliases do not expose the selectable club items")
        if store.club_items({"type": "badges"}).get("total") != 1 or badge_inventory[0].get("itemType") != "custom":
            fail(f"badge/custom My Club alias or wire family is wrong: {badge_inventory}")

        home = next(row for row in kit_inventory if int(row.get("assetId", -1)) == 14)
        away = next(row for row in kit_inventory if int(row.get("assetId", -1)) == 15)
        stadium_card = next(row for row in stadium_inventory if int(row.get("assetId", -1)) == 34)

        # BETA 2.13's guessed 5/7/8 cardsubtypeid + clubInfo experiment crashed
        # the retail PC client at kit-search response parse time. BETA 2.22 uses
        # a deliberately conservative, canonical wire projection instead.
        if home.get("itemType") != "kit" or away.get("itemType") != "kit":
            fail(f"kit ItemData must serialize in the kit family: {kit_inventory}")
        if stadium_card.get("itemType") != "stadium":
            fail(f"stadium ItemData must serialize in the stadium family: {stadium_card}")
        if int(home.get("category", -1)) != 2 or int(home.get("teamkittypetechid", -1)) != 0:
            fail(f"home-kit retail classification lost: {home}")
        if int(away.get("category", -1)) != 3 or int(away.get("teamkittypetechid", -1)) != 1:
            fail(f"away-kit retail classification lost: {away}")
        if int(stadium_card.get("category", -1)) != 4 or int(stadium_card.get("stadiumid", -1)) != 34:
            fail(f"stadium retail classification lost: {stadium_card}")
        # category/teamkittypetechid/stadiumid are retail classification members
        # present in the BETA 2.14 response that rendered correctly.  FCC database
        # bookkeeping must not leak onto the wire, and the old 2/3/4 subtype alias
        # remains forbidden.
        risky_wire_keys = {
            "cardsubtypeid", "carddbid", "cardassetid", "weightrare",
            "teamkitid", "year", "value", "cardsSource",
        }
        for cosmetic in kit_inventory + stadium_inventory + badge_inventory:
            leaked = risky_wire_keys.intersection(cosmetic)
            if leaked:
                fail(f"database-only cosmetic keys leaked onto My Club wire: {sorted(leaked)} / {cosmetic}")
            keys = list(cosmetic.keys())
            if keys[:4] != ["id", "itemId", "timestamp", "itemType"]:
                fail(f"cosmetic parser-safe prefix/order regressed: {keys}")
            if keys.index("itemType") > keys.index("assetId") or keys.index("itemType") > keys.index("resourceId"):
                fail(f"itemType must precede cosmetic identity fields: {keys}")
        if (int(home.get("rating", -1)), int(away.get("rating", -1)), int(stadium_card.get("rating", -1))) != (89, 89, 64):
            fail(f"retail fcc value/rating projection is wrong: {kit_inventory + stadium_inventory}")
        required_common = {
            "id", "timestamp", "rating", "assetId", "resourceId", "itemState",
            "rareflag", "formation", "leagueId", "injuryType", "injuryGames",
            "lastSalePrice", "fitness", "training", "suspension", "contract",
            "discardValue", "itemType", "owners",
        }
        for cosmetic in kit_inventory + stadium_inventory:
            if not required_common.issubset(cosmetic):
                fail(f"cosmetic ItemData common envelope is incomplete: {cosmetic}")

        badge_card = badge_inventory[0]
        viewed = store.view_items([int(home["id"]), int(away["id"]), int(stadium_card["id"]), int(badge_card["id"])])
        viewed_rows = viewed.get("itemData", [])
        if [int(row.get("id", 0)) for row in viewed_rows] != [int(home["id"]), int(away["id"]), int(stadium_card["id"]), int(badge_card["id"])]:
            fail(f"ViewCards exact-id lookup lost requested order/identity: {viewed}")
        for viewed_item in viewed_rows:
            if str(viewed_item.get("itemType", "")).lower() in {"kit", "stadium", "custom"}:
                keys = list(viewed_item.keys())
                if keys[:4] != ["id", "itemId", "timestamp", "itemType"] or "cardsubtypeid" in viewed_item:
                    fail(f"ViewCards cosmetic wire projection regressed: {viewed_item}")

        # Use the *actual retail FIFA 14 My Club activation documents captured
        # from BETA 2.16.  Kits use generic itemState=active plus slot 101/102;
        # stadium activation has no slot number.  Older local-only synthetic
        # state names hid the persistence bug this test is meant to prevent.
        activation_updates = [
            {"id": int(home["id"]), "itemState": "active", "activateSlotNumber": "101"},
            {"id": int(away["id"]), "itemState": "active", "activateSlotNumber": "102"},
            {"id": int(stadium_card["id"]), "itemState": "active"},
            {"id": int(badge_card["id"]), "itemState": "active"},
        ]
        activation = store.move_items(activation_updates)
        if len(activation.get("itemData", [])) != 4 or not all(row.get("success") for row in activation["itemData"]):
            fail(f"retail cosmetic activation failed: {activation}")
        badge_ack = next((row for row in activation["itemData"] if row.get("itemState") == "activeBadge"), None)
        if badge_ack is None:
            fail(f"badge activation acknowledgement missing: {activation}")
        if (int(badge_ack.get("assetId", -1)) != 1 or int(badge_ack.get("badge", -1)) != 1 or
                int(badge_ack.get("badgeId", -1)) != 1 or int(badge_ack.get("teamId", -1)) != 1 or
                int(badge_ack.get("definitionId", -1)) != 6100000):
            fail(f"badge activation acknowledgement lacks retail badge identity: {badge_ack}")
        active_badge_wire = store.view_items([int(badge_card["id"])])["itemData"][0]
        if (int(active_badge_wire.get("badgeId", -1)) != 1 or
                int(active_badge_wire.get("badgeResourceId", -1)) != 6100000 or
                int(active_badge_wire.get("badgeDefinitionId", -1)) != 6100000):
            fail(f"active badge wire aliases are incomplete: {active_badge_wire}")

        with closing(sqlite3.connect(db)) as badge_con:
            badge_con.row_factory = sqlite3.Row
            club_badge = badge_con.execute("SELECT badge_id FROM clubs LIMIT 1").fetchone()
            badge_setting = badge_con.execute("SELECT badge_resource_id FROM beta_club_settings LIMIT 1").fetchone()
            if club_badge is None or int(club_badge["badge_id"]) != 1 or badge_setting is None or int(badge_setting["badge_resource_id"]) != 6100000:
                fail(f"active badge did not persist to club/settings: club={club_badge} settings={badge_setting}")

        squad_doc = store.squad_list()["squadList"][0]
        actives = squad_doc.get("actives")
        # BETA 2.22 deliberately keeps activeBadge out of squad.actives: the
        # BETA 2.20 client wrote back a GK-only squad after parsing that shape.
        # Badge state remains persistent in club/settings and is bridged directly
        # to the native BADGE_ID UI writers instead.
        if not isinstance(actives, list) or len(actives) != 3:
            fail(f"UI-facing squad must keep only the proven home/away/stadium actives: {actives}")
        active_states = {str(row.get("itemState")) for row in actives}
        if active_states != {"activeHomeKit", "activeAwayKit", "activeStadium"}:
            fail(f"active kit/stadium states are wrong after manual selection: {actives}")
        active_by_state = {str(row.get("itemState")): row for row in actives}
        if int(active_by_state["activeHomeKit"].get("assetId", -1)) != 14 or active_by_state["activeHomeKit"].get("itemType") != "kit":
            fail(f"active home kit lost its safe wire identity: {active_by_state['activeHomeKit']}")
        if int(active_by_state["activeAwayKit"].get("assetId", -1)) != 15 or active_by_state["activeAwayKit"].get("itemType") != "kit":
            fail(f"active away kit lost its safe wire identity: {active_by_state['activeAwayKit']}")
        if int(active_by_state["activeHomeKit"].get("resourceId", -1)) != 6300000:
            fail(f"home kit must keep unique fcc carddbid 6300000 as resource identity: {active_by_state['activeHomeKit']}")
        if int(active_by_state["activeAwayKit"].get("resourceId", -1)) != 6400000:
            fail(f"away kit must keep unique fcc carddbid 6400000 as resource identity: {active_by_state['activeAwayKit']}")
        if int(active_by_state["activeStadium"].get("assetId", 0)) != 34 or active_by_state["activeStadium"].get("itemType") != "stadium":
            fail(f"native offline stadium fallback must remain Town Park ID 34: {active_by_state['activeStadium']}")
        if int(active_by_state["activeStadium"].get("resourceId", -1)) != 6200016:
            fail(f"Town Park must keep unique fcc carddbid 6200016 as resource identity: {active_by_state['activeStadium']}")
        for cosmetic in actives:
            leaked = risky_wire_keys.intersection(cosmetic)
            if leaked:
                fail(f"native/debug-only keys leaked onto squad active cosmetic wire: {sorted(leaked)} / {cosmetic}")


        seasons = store.offline_seasons_list()
        season_rows = seasons.get("seasons", [])
        if len(season_rows) != 10:
            fail(f"offline seasons must expose ten native division records: {seasons}")
        first = season_rows[0]
        required_season = {
            "id", "type", "divisionId", "numMatches", "matchLengthMin", "matches",
            "prizeSet", "elgOperation", "elgReq", "trophyResourceId", "trophyUseCount",
            "visStartDays", "visEndDays", "startDateTime", "endDateTime",
            "untilStartSeconds", "untilEndSeconds",
        }
        if not required_season.issubset(first):
            fail(f"Division 10 native season record is incomplete: {first}")
        guessed_season_keys = {
            "seasonId", "division", "name", "matchesPlayed", "matchesToPlay",
            "pointsToWinTitle", "pointsToPromote", "pointsToAvoidRelegation",
            "points", "won", "draw", "lost", "coinsPerWin", "trophiesWon",
        }
        if guessed_season_keys.intersection(first):
            fail(f"legacy guessed season keys leaked back onto the wire: {first}")
        if int(first["id"]) != 1 or first["type"] != "OFFLINE" or int(first["divisionId"]) != 10:
            fail(f"first native season record must map ID 1 to Division 10 OFFLINE: {first}")
        if int(first.get("trophyResourceId", 0)) != -1:
            fail(f"BETA 2.22 season no-trophy sentinel must be -1: {first}")
        matches = first.get("matches")
        if int(first["numMatches"]) != 10 or int(first["matchLengthMin"]) != 6 or not isinstance(matches, list) or len(matches) != 10:
            fail(f"Division 10 native match contract is wrong: {first}")
        required_match = {"teamId", "difficulty", "rewardMult", "roundId", "coins"}
        if any(not required_match.issubset(match) for match in matches):
            fail(f"Division 10 native match record is incomplete: {matches}")
        if [int(match["roundId"]) for match in matches] != list(range(10)):
            fail(f"season match roundId values must be internal 0..9: {matches}")
        valid_team_ids = {int(player.get("teamId", 0)) for player in PLAYER_CATALOG}
        if any(int(match["teamId"]) not in valid_team_ids for match in matches):
            fail(f"season schedule contains a team absent from the retail player catalogue: {matches}")
        prizes = first.get("prizeSet")
        if not isinstance(prizes, list) or [p.get("prizeLevel") for p in prizes] != [
            "RELEGATION", "MAINTENANCE", "PROMOTION", "CHAMPIONSHIP"
        ]:
            fail(f"Division 10 prizeSet enum/order is wrong: {prizes}")
        thresholds = {p["prizeLevel"]: int(p.get("thresholdPoint", -1)) for p in prizes}
        if thresholds != {"RELEGATION": 0, "MAINTENANCE": 0, "PROMOTION": 9, "CHAMPIONSHIP": 12}:
            fail(f"Division 10 native thresholds are wrong: {thresholds}")
        for prize in prizes:
            mappings = prize.get("awardMappings")
            if not isinstance(mappings, list) or not mappings or not isinstance(mappings[0].get("awards"), list):
                fail(f"season awardMappings must be array -> awards array: {prize}")
        season_user = store.offline_season_user()
        if season_user != {"seasonId": 1, "divisionId": 10, "round": 1}:
            fail(f"fresh parser-native offline season progress must use one-based wire round 1: {season_user}")

        # BETA 2.22 keeps the proven native PC tournament schema but exposes
        # four independent knockout cups. `rounds` remains an ARRAY; names are
        # intentionally NOT sent in this JSON because the retail list parser
        # does not consume a name member. The exact-build frontend hook supplies
        # the local display names instead.
        os.environ.pop("FIFA14_TOURNAMENT_MODE", None)
        tournaments = store.offline_tournaments_list()
        tournament_rows = tournaments.get("tournament", [])
        if len(tournament_rows) != 4:
            fail(f"BETA 2.22 default tournament catalogue must contain four cups: {tournaments}")
        required_cup = {
            "id", "type", "treeType", "aigroup", "eligibilityOperation", "elgReq",
            "numTeams", "numRounds", "matchlength", "rounds", "awardSet", "lock",
            "unlockreq", "triesMax", "triesPeriod", "triesRemaining", "nextReset",
            "starttime", "endtime", "timeUntilStart", "timeUntilEnd", "visStart",
            "visEnd", "trophyResourceId", "trophyUserCount",
        }
        guessed_cup_keys = {"tournamentId", "name", "level", "prize", "repeatPrize", "currentRound", "entryFee", "active", "won"}
        expected_prizes = {1: 500, 2: 750, 3: 1000, 4: 1500}
        if [int(row.get("id", 0)) for row in tournament_rows] != [1, 2, 3, 4]:
            fail(f"tournament IDs/order regressed: {tournament_rows}")
        for cup in tournament_rows:
            if not required_cup.issubset(cup):
                fail(f"native tournament record is incomplete: {cup}")
            if guessed_cup_keys.intersection(cup):
                fail(f"legacy guessed tournament keys leaked back onto the wire: {cup}")
            if cup["type"] != "offline" or cup["treeType"] != "knockout" or cup["lock"] != "UNLOCKED":
                fail(f"tournament enum mapping is wrong: {cup}")
            rounds = cup.get("rounds")
            if not isinstance(rounds, list) or len(rounds) != 4:
                fail(f"tournament rounds MUST be a four-record array: {rounds}")
            if any(not {"id", "difficulty", "rewardMultiplier", "coins"}.issubset(r) for r in rounds):
                fail(f"tournament native round schema is incomplete: {rounds}")
            expected_awards = [{"awardType": 1, "value": expected_prizes[int(cup["id"])], "halid": 0}]
            if cup.get("awardSet", {}).get("awards") != expected_awards:
                fail(f"tournament awardSet is not parser-native: {cup}")
        if store.offline_tournament_user_list().get("tournamentId") != []:
            fail("tournament user-list must remain empty until a cup is actually entered")
        teams = store.offline_tournament_teams(15)
        if list(teams) != ["teamId"] or len(teams.get("teamId", [])) != 15:
            fail(f"tournament/teams must return only a 15-element teamId array: {teams}")
        if teams["teamId"] != list(OFFLINE_COMPETITION_TEAM_IDS):
            fail(f"tournament team pool is not deterministic: {teams}")
        if any(int(team_id) not in valid_team_ids for team_id in teams["teamId"]):
            fail(f"tournament team pool contains an unknown retail club: {teams}")
        tournament_progress = store.update_offline_tournament_user(1, {
            "round": 1, "dataVersion": 1, "tournamentData": "fixture-data",
            "progressDataVersion": 1, "progressData": "AAAAAA==",
        })
        if tournament_progress.get("tournamentId") != 1 or tournament_progress.get("tournamentData") != "fixture-data":
            fail(f"tournament/user/1 progress was not persisted/echoed: {tournament_progress}")
        if store.offline_tournament_user(1) != {"tournamentId": 1}:
            fail(f"first-round zero progress must be treated as a fresh tournament, got {store.offline_tournament_user(1)}")
        if store.offline_tournament_user_list().get("tournamentId") != []:
            fail(f"first-round zero progress must NOT advertise Underway: {store.offline_tournament_user_list()}")

        # Progress in another cup must survive a DNF in tournament 1.
        store.update_offline_tournament_user(2, {
            "round": 2, "dataVersion": 1, "tournamentData": "cup-two-data",
            "progressDataVersion": 1, "progressData": "AQAAAA==",
        })

        os.environ["FIFA14_TOURNAMENT_MODE"] = "empty"
        if store.offline_tournaments_list().get("tournament") != []:
            fail("explicit empty tournament fallback no longer works")
        if store.offline_tournament_user_list().get("tournamentId") != []:
            fail("empty tournament fallback must hide resumable user state")
        os.environ.pop("FIFA14_TOURNAMENT_MODE", None)

        # BETA 2.4 still returned 404 for the trophy bundle immediately before
        # season/user and the native unavailable popup. BETA 2.22's controlled
        # compatibility response is a minimal structurally valid empty BIGF.
        trophy_path = "/fut/items/images/trophies/pc/item.big"
        if not HttpProbe._is_fut_static_archive_path(trophy_path):
            fail("trophy item.big is not classified as a FUT static archive")
        if not HttpProbe._is_fut_trophy_archive_path(trophy_path):
            fail("trophy item.big is not classified as the Offline Seasons trophy bundle")
        if not HttpProbe._is_fut_trophy_archive_path("/fut/items/images/trophies/pc/.big"):
            fail("degenerate tournament trophy .big path must receive the safe empty BIGF")
        if HttpProbe._is_fut_static_archive_path("/ut/game/fifa14/tournament/list"):
            fail("dynamic FUT route was incorrectly classified as static archive")
        empty_big = HttpProbe._empty_bigf_archive()
        if len(empty_big) != 16 or empty_big[:4] != b"BIGF":
            fail(f"empty trophy BIGF has wrong magic/size: {empty_big!r}")
        if int.from_bytes(empty_big[4:8], "big") != 16:
            fail("empty trophy BIGF declared size is not 16")
        if int.from_bytes(empty_big[8:12], "big") != 0:
            fail("empty trophy BIGF must contain zero entries")
        if int.from_bytes(empty_big[12:16], "big") != 16:
            fail("empty trophy BIGF header size is not 16")

        if os.environ.get("FIFA14_SEASON_ITEM0_MODE") is not None:
            os.environ.pop("FIFA14_SEASON_ITEM0_MODE", None)
        # The route handler's default is deliberately a compatibility probe: item
        # ID 0 (the PC client's no-reward sentinel) gets 200 + {}. Non-zero item
        # IDs remain strict misses. Live BETA 2.22 logging differentiates both.

        offers = store.store_pack_types().get("purchase", [])
        if not offers:
            fail("store pack catalogue is empty")
        expected_art = {
            1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3,
            101: 3, 102: 3, 103: 3, 104: 3, 105: 3, 106: 2, 107: 3,
        }
        expected_group = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3,
                          101: 3, 102: 3, 103: 3, 104: 3, 105: 3, 106: 2, 107: 3}
        for offer in offers:
            pack_type = int(offer.get("packType", 0))
            asset = int(offer.get("assetId", 0))
            group_asset = int(offer.get("displayGroupAssetId", 0))
            if asset != expected_art.get(pack_type):
                fail(f"Store safe tier asset mismatch for pack {pack_type}: {offer}")
            if group_asset != expected_group.get(pack_type):
                fail(f"Store display grouping changed for pack {pack_type}: {offer}")
            if not str(offer.get("description", "")).startswith("FUT_STORE_PACK_"):
                fail(f"Store description must be a retail loc token, got {offer.get('description')}")
            if not str(offer.get("name", "")).startswith("LOCAL_PACK_NAME_"):
                fail(f"Store name must be a local loc token, got {offer.get('name')}")

        partial_update = store.save_squad({"id": 1, "captain": int(items[0]["id"]), "kicktakers": [int(items[0]["id"])]})
        if not partial_update.get("squadList") or len(partial_update["squadList"][0].get("players", [])) != 23:
            fail(f"captain/kicktakers-only squad PUT must preserve the 23-player squad: {partial_update}")

        # BETA 2.22 migration guard: an existing user club/squad must survive even
        # if an older build lost the beta_starter_provisioned metadata marker.
        # BETA 2.19's old fallback deleted squads/items whenever that marker was
        # absent, which made extraction-to-extraction persistence fragile.
        with closing(sqlite3.connect(db)) as persist_con, persist_con:
            before_slots = [tuple(row) for row in persist_con.execute(
                "SELECT slot_index,item_id,asset_id,resource_id FROM squad_players ORDER BY squad_id,slot_index"
            ).fetchall()]
            before_items = int(persist_con.execute("SELECT COUNT(*) FROM items").fetchone()[0])
            persist_con.execute("DELETE FROM schema_meta WHERE meta_key='beta_starter_provisioned'")
        store = BetaIdentityStore(str(db), "existing")
        with closing(sqlite3.connect(db)) as persist_con:
            after_slots = [tuple(row) for row in persist_con.execute(
                "SELECT slot_index,item_id,asset_id,resource_id FROM squad_players ORDER BY squad_id,slot_index"
            ).fetchall()]
            after_items = int(persist_con.execute("SELECT COUNT(*) FROM items").fetchone()[0])
            marker = persist_con.execute(
                "SELECT meta_value FROM schema_meta WHERE meta_key='beta_starter_provisioned'"
            ).fetchone()
            guard = persist_con.execute(
                "SELECT meta_value FROM schema_meta WHERE meta_key='beta222_persistent_club_guard'"
            ).fetchone()
        if after_slots != before_slots or after_items != before_items:
            fail(f"existing club migration reset persistent squad/items: before_slots={before_slots} after_slots={after_slots} before_items={before_items} after_items={after_items}")
        if marker != ("1",) or guard != ("1",):
            fail(f"persistent club migration guard markers missing: starter={marker} guard={guard}")

        # The exact BETA 2.20 failure was a first PUT with only the goalkeeper
        # resolved and bogus 4 chemistry / 6 rating.  It must be a no-op.
        before_sparse = store.squad_list()["squadList"][0]
        before_sparse_ids = [int((row.get("itemData") or {}).get("id", 0) or 0) for row in before_sparse.get("players", [])]
        sparse_players = []
        for index in range(23):
            sparse_players.append({
                "index": index,
                "itemData": {"id": before_sparse_ids[0] if index == 0 else 0},
                "kitNumber": 1 if index == 0 else 0,
            })
        store.save_squad({
            "id": int(before_sparse.get("id", 1)),
            "squadName": "Starter XI", "formation": "f442",
            "chemistry": 4, "starRating": 6, "rating": 6,
            "players": sparse_players,
        }, requested_id=int(before_sparse.get("id", 1)))
        after_sparse = store.squad_list()["squadList"][0]
        after_sparse_ids = [int((row.get("itemData") or {}).get("id", 0) or 0) for row in after_sparse.get("players", [])]
        if after_sparse_ids != before_sparse_ids or int(after_sparse.get("chemistry", -1)) != int(before_sparse.get("chemistry", -2)) or int(after_sparse.get("starRating", -1)) != int(before_sparse.get("starRating", -2)):
            fail(f"GK-only sparse squad write corrupted persistent squad state: before={before_sparse} after={after_sparse}")
        required_squad_fields = {"rating", "valid", "newsquad", "kicktakers", "tactics", "dreamSquad", "custom"}
        if not required_squad_fields.issubset(after_sparse):
            fail(f"outgoing SquadDetails compatibility fields are incomplete: {after_sparse.keys()}")

        # A real squad edit must survive closing/reopening the identity store, not
        # merely remain visible in one in-memory instance. Swap two populated
        # slots, save the complete retail-shaped squad and reopen from disk.
        persisted_doc = store.squad_list()["squadList"][0]
        persisted_players = json.loads(json.dumps(persisted_doc.get("players", [])))
        populated_indices = [
            i for i, entry in enumerate(persisted_players)
            if isinstance(entry, dict) and int((entry.get("itemData") or {}).get("id", 0) or 0) > 0
        ]
        if len(populated_indices) < 2:
            fail(f"persistence test requires two populated squad slots: {persisted_players}")
        first_i, second_i = populated_indices[:2]
        first_item = persisted_players[first_i].get("itemData")
        second_item = persisted_players[second_i].get("itemData")
        persisted_players[first_i]["itemData"] = second_item
        persisted_players[second_i]["itemData"] = first_item
        save_payload = {
            "id": int(persisted_doc.get("id", 1)),
            "squadName": persisted_doc.get("squadName", "Starter XI"),
            "formation": persisted_doc.get("formation", "f442"),
            "chemistry": int(persisted_doc.get("chemistry", 0)),
            "starRating": int(persisted_doc.get("starRating", persisted_doc.get("rating", 0))),
            "players": persisted_players,
        }
        store.save_squad(save_payload, requested_id=int(persisted_doc.get("id", 1)))
        store = BetaIdentityStore(str(db), "existing")
        reopened_players = store.squad_list()["squadList"][0].get("players", [])
        reopened_first = int((reopened_players[first_i].get("itemData") or {}).get("id", 0) or 0)
        reopened_second = int((reopened_players[second_i].get("itemData") or {}).get("id", 0) or 0)
        if reopened_first != int((second_item or {}).get("id", 0) or 0) or reopened_second != int((first_item or {}).get("id", 0) or 0):
            fail(f"saved squad edit did not survive store reopen: first={reopened_first} second={reopened_second}")

        empty_reset = store.reset_match({})
        if empty_reset.get("status") != "reset" or store.metrics()["activeMatches"] != 0:
            fail("bootstrap match/reset must not create a phantom active match")
        create_response = store.create_match({"squadId": 0, "type": "OFFLINE", "tournamentId": 1})
        if set(create_response) != {"squad", "startDateTime"}:
            fail(f"CreateMatch must emit only the two parser-native top-level fields: {create_response.keys()}")
        create_squad = create_response.get("squad", {})
        if int(create_squad.get("chemistry", -1)) != 54 or int(create_squad.get("starRating", -1)) != 61:
            fail(f"CreateMatch lost squad scalar state: {create_squad}")
        if len(create_squad.get("players", [])) != 23 or len(create_squad.get("actives", [])) != 3:
            fail(f"CreateMatch did not embed full local squad/cosmetics: {create_squad}")
        match_players = [
            int((row.get("itemData") or {}).get("id", 0) or 0)
            for row in create_squad.get("players", [])[:18]
            if int((row.get("itemData") or {}).get("id", 0) or 0) > 0
        ]
        starters = match_players[:11]
        before_contract_rows = store.view_items(match_players).get("itemData", [])
        before_contracts = {int(row.get("id", 0)): int(row.get("contract", 0) or 0) for row in before_contract_rows}
        ready_response = store.match_ready({"items": [{"id": item_id} for item_id in starters]})
        if set(ready_response) != {"squad", "startDateTime"} or len(ready_response.get("squad", {}).get("players", [])) != 23:
            fail(f"items-only match-ready handoff must return the parser-native squad/startDateTime shape: {ready_response}")
        empty_match = store.settle_match({})
        if empty_match.get("settled") or int(empty_match.get("rewardCoins", -1)) != 0 or int(empty_match.get("credits", -1)) != 0:
            fail(f"empty /match acknowledgement incorrectly settled/paid: {empty_match}")
        dnf_reward = store._reward_breakdown({"minutesPlayed": 23, "goalsFor": 1, "goalsAgainst": 0, "dnf": 1}, 1.25)
        if int(dnf_reward.get("totalCoins", -1)) != 0:
            fail(f"DNF match must not receive normal BETA completion reward: {dnf_reward}")

        destroy = store.settle_match_end({
            "endReason": "QUIT",
            "matchData": "verify-destroy-match-data",
            "items": [
                {"id": item_id, "fitness": 97 - (index % 2)}
                for index, item_id in enumerate(match_players)
            ],
        })
        expected_destroy_keys = {"endReason", "secondsPlayed", "matchDifficulty", "items", "matchData"}
        if set(destroy) != expected_destroy_keys:
            fail(f"QUIT DestroyMatch must mirror the retail DNF branch exactly: {destroy}")
        if destroy.get("endReason") != "QUIT" or destroy.get("matchData") != "verify-destroy-match-data":
            fail(f"DestroyMatch must echo QUIT + native matchData: {destroy}")
        returned_items = destroy.get("items")
        if not isinstance(returned_items, list) or len(returned_items) != len(match_players) or "myMatchStats" in destroy or "opponentMatchStats" in destroy:
            fail(f"QUIT must return match-stat items and omit both matchStats objects: {destroy}")
        allowed_match_stat_keys = {"id", "shots", "goals", "yellowCards", "redCards", "suspension", "injuryType", "injuryGames", "fitness", "assists"}
        for row in returned_items:
            if not isinstance(row, dict) or not set(row).issubset(allowed_match_stat_keys):
                fail(f"DestroyMatch items leaked ItemData fields into the match-stat parser: {row}")
        returned_by_id = {int(row.get("id", 0)): row for row in returned_items if isinstance(row, dict)}
        if any("contract" in row or "assetId" in row or "itemType" in row for row in returned_items if isinstance(row, dict)):
            fail(f"DestroyMatch match-stat items must never contain card ItemData: {returned_items}")
        after_contract_rows = store.view_items(match_players).get("itemData", [])
        after_contracts = {int(row.get("id", 0)): int(row.get("contract", 0) or 0) for row in after_contract_rows}
        for item_id in starters:
            expected = max(0, int(before_contracts.get(item_id, 0)) - 1)
            if int(after_contracts.get(item_id, -1)) != expected:
                fail(f"starter contract was not decremented exactly once in persistent ItemData: id={item_id} before={before_contracts.get(item_id)} after={after_contracts.get(item_id)}")
        for item_id in match_players[11:]:
            if int(after_contracts.get(item_id, -1)) != int(before_contracts.get(item_id, 0)):
                fail(f"bench/reserve contract changed despite not starting: id={item_id} before={before_contracts.get(item_id)} after={after_contracts.get(item_id)}")
        record_after_dnf = store.match_record()
        if (int(record_after_dnf.get("wins", -1)), int(record_after_dnf.get("draws", -1)), int(record_after_dnf.get("losses", -1))) != (0, 0, 1):
            fail(f"server-side local FUT record did not become 0-0-1 after QUIT: {record_after_dnf}")
        resumable_after_dnf = store.offline_tournament_user_list().get("tournamentId")
        if resumable_after_dnf != [2]:
            fail(f"DNF in cup 1 must clear only cup 1 and preserve cup 2 progress: {resumable_after_dnf}")
        cup1_after_dnf = store.offline_tournament_user(1)
        if cup1_after_dnf != {"tournamentId": 1}:
            fail(f"DNF cup 1 state was not reset to a fresh/non-resumable cup: {cup1_after_dnf}")
        cup2_after_dnf = store.offline_tournament_user(2)
        if (int(cup2_after_dnf.get("round", 0)) != 2 or
                cup2_after_dnf.get("tournamentData") != "cup-two-data" or
                cup2_after_dnf.get("progressData") != "AQAAAA=="):
            fail(f"DNF in cup 1 incorrectly destroyed cup 2 progress: {cup2_after_dnf}")

        store.reset_match({"matchId": "verify-match", "mode": "single-player", "difficulty": "Professional"})
        if store.metrics()["activeMatches"] != 1:
            fail("explicit match reset/bootstrap did not create active match")
        result = store.settle_match({
            "matchId": "verify-match",
            "completed": 1,
            "minutesPlayed": 90,
            "goalsFor": 3,
            "goalsAgainst": 1,
            "shotsOnTarget": 8,
            "successfulTackles": 12,
            "corners": 5,
            "passAccuracy": 78,
            "possession": 55,
            "manOfTheMatch": 1,
            "fouls": 4,
            "yellowCards": 1,
            "offsides": 2,
            "multiplier": 1.0,
        })
        if result["rewardCoins"] != 634 or result["credits"] != 634:
            fail(f"FUT14 reward regression: expected 634, got {result}")
        replay = store.settle_match({"matchId": "verify-match", "completed": 1, "minutesPlayed": 90})
        if not replay.get("idempotent") or replay["credits"] != 634:
            fail(f"match reward paid more than once: {replay}")

        # A bronze pack can now be bought from earned match coins. It must debit
        # the wallet and produce a ledger entry, while the original dev profile
        # remains unrelated because this is a dedicated BETA DB.
        pack = store.purchase_pack(1, currency="COINS")
        if int(pack.get("credits", -1)) != 234:
            fail(f"400-coin Bronze Pack should leave 234 from 634, got {pack.get('credits')}")
        ledger = store.wallet_ledger(20)["transactions"]
        reasons = {row["reason"] for row in ledger}
        if not {"MATCH_REWARD", "PACK_PURCHASE"}.issubset(reasons):
            fail(f"missing wallet ledger reasons: {reasons}")
        metrics = store.metrics()
        if metrics["packsOpenedToday"] != 1 or metrics["matchesCompletedToday"] != 1 or metrics["matchesAbandonedToday"] != 1:
            fail(f"BETA counters invalid: {metrics}")
        if metrics["coinsInCirculation"] != 234:
            fail(f"economy circulation should equal 234 in one-account verifier, got {metrics['coinsInCirculation']}")

        # sqlite3.Connection.__exit__ commits/rolls back but does NOT close the
        # underlying handle. Windows therefore keeps the TemporaryDirectory DB
        # locked unless we explicitly close it before cleanup.
        with closing(sqlite3.connect(db)) as con, con:
            beta_schema = con.execute("SELECT meta_value FROM schema_meta WHERE meta_key='beta_schema'").fetchone()
            if not beta_schema or beta_schema[0] != "fifa14-local-fut-v2.41.1-beta2.24":
                fail("BETA schema marker missing")

        print(json.dumps({
            "status": "ok",
            "starterPlayers": 23,
            "provenStarterCosmetics": 3,
            "syntheticScannedCosmetics": 3,
            "startingCoins": 0,
            "startingFifaPoints": 0,
            "offlineSeasons": len(season_rows),
            "offlineTournamentsDefault": len(tournament_rows),
            "offlineTournamentRounds": len(rounds),
            "storeSafeTierAssets": {str(int(row["packType"])): int(row["assetId"]) for row in offers},
            "verificationMatchReward": 634,
            "postPackCoins": 234,
            "walletLedgerEntries": len(ledger),
            "metrics": metrics,
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
