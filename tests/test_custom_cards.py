import tempfile
import unittest
from pathlib import Path

from custom_cards import CustomCardCatalog
from fifa14_ids import definition_id_for, resource_id_for
from local_identity import LocalIdentityStore


class CustomCardCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = {
            1001: {
                "assetId": 1001,
                "name": "Test Player",
                "commonName": "Test",
                "position": "CM",
                "nation": 1,
                "nationName": "England",
                "rating": 80,
                "teamId": 10,
                "leagueId": 2,
                "rareFlag": 1,
                "playStyle": 1,
                "attributes": [80] * 6,
                "gameplayAttributes": {
                    "acceleration": 80,
                    "sprintspeed": 80,
                    "positioning": 80,
                    "finishing": 80,
                    "shotpower": 80,
                    "longshots": 80,
                    "volleys": 80,
                    "penalties": 80,
                    "vision": 80,
                    "crossing": 80,
                    "freekickaccuracy": 80,
                    "shortpassing": 80,
                    "longpassing": 80,
                    "curve": 80,
                    "agility": 80,
                    "balance": 80,
                    "reactions": 80,
                    "ballcontrol": 80,
                    "dribbling": 80,
                    "interceptions": 80,
                    "headingaccuracy": 80,
                    "marking": 80,
                    "standingtackle": 80,
                    "slidingtackle": 80,
                    "jumping": 80,
                    "stamina": 80,
                    "strength": 80,
                    "aggression": 80,
                    "gkdiving": 80,
                    "gkhandling": 80,
                    "gkkicking": 80,
                    "gkreflexes": 80,
                    "gkpositioning": 80,
                },
                "traits": {
                    "weakfootabilitytypecode": 3,
                    "skillmoves": 3,
                    "preferredfoot": 1,
                    "attackingworkrate": 1,
                    "defensiveworkrate": 1,
                },
            }
        }

    def test_special_card_uses_versioned_resource_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "custom-cards.json"
            catalog = CustomCardCatalog(path, self.base)
            card = catalog.save(
                {
                    "assetId": 1001,
                    "rating": 85,
                    "teamId": 10,
                    "leagueId": 2,
                    "rareFlag": 2,
                    "attributes": [85] * 6,
                    "gameplayAttributes": self.base[1001]["gameplayAttributes"],
                    "traits": self.base[1001]["traits"],
                    "version": 50,
                }
            )

            self.assertEqual(card["definitionId"], 1001)
            self.assertEqual(card["resourceId"], resource_id_for(1001, 50))
            self.assertNotEqual(card["resourceId"], definition_id_for(1001, 50))

    def test_custom_payload_keeps_gameplay_stats_and_traits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "local-fut.db"
            store = LocalIdentityStore(database, initial_mode="new")
            asset_id = 158023
            source = {
                "assetId": asset_id,
                "rating": 85,
                "teamId": 10,
                "leagueId": 2,
                "rareFlag": 2,
                "attributes": [85] * 6,
                "gameplayAttributes": self.base.get(asset_id, self.base[1001])["gameplayAttributes"],
                "traits": self.base.get(asset_id, self.base[1001])["traits"],
                "untradeable": True,
                "pile": 7,
            }

            payload = store._canonical_player_payload(item_id=9001, asset_id=asset_id, existing=source, pile=7)

            self.assertEqual(payload["gameplayAttributes"]["acceleration"], 80)
            self.assertEqual(payload["traits"]["preferredfoot"], 1)
            self.assertIn("positioning", payload["gameplayAttributes"])
            self.assertIn("weakfootabilitytypecode", payload["traits"])
            self.assertEqual(payload["acceleration"], 80)
            self.assertEqual(payload["positioning"], 80)
            self.assertEqual(payload["preferredfoot"], 1)
            self.assertEqual(payload["skillmoves"], 3)
            native_keys = list(payload)
            self.assertLess(native_keys.index("acceleration"), native_keys.index("itemId"))
            self.assertLess(native_keys.index("defensiveworkrate"), native_keys.index("itemId"))


if __name__ == "__main__":
    unittest.main()
