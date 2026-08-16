"""Portable custom-card definitions for variants of FIFA 14 base players."""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fifa14_ids import resource_id_for
from player_data import GAMEPLAY_FIELDS, TRAIT_RANGES


SCHEMA = "fifa14-local-custom-cards-v1"
MIN_CUSTOM_VERSION = 50
MAX_CUSTOM_VERSION = 99
ATTRIBUTE_COUNT = 6


def _quality_for_rating(rating: int) -> str:
    return "bronze" if rating <= 64 else "silver" if rating <= 74 else "gold"


class CustomCardCatalog:
    """Read/write local custom cards while preserving a real player identity."""

    def __init__(self, path: Path | str, base_players: dict[int, dict[str, Any]]) -> None:
        self.path = Path(path)
        self.base_players = {int(asset_id): dict(player) for asset_id, player in base_players.items()}

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema": SCHEMA, "cards": []}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"could not read custom card catalog: {error}") from error
        if not isinstance(document, dict) or document.get("schema") != SCHEMA:
            raise ValueError("unsupported custom card catalog schema")
        cards = document.get("cards")
        if not isinstance(cards, list):
            raise ValueError("custom card catalog cards must be an array")
        return document

    def _write(self, document: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        handle, temporary = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def _card_from_input(
        self,
        source: dict[str, Any],
        *,
        current_cards: list[dict[str, Any]],
        card_id: str | None = None,
        created_at: int | None = None,
    ) -> dict[str, Any]:
        try:
            asset_id = int(source.get("assetId", source.get("baseAssetId")))
        except (TypeError, ValueError) as error:
            raise ValueError("base player assetId is required") from error
        base = self.base_players.get(asset_id)
        if base is None:
            raise ValueError(f"assetId {asset_id} is not a base-game player")

        def bounded(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(source.get(name, default))
            except (TypeError, ValueError) as error:
                raise ValueError(f"{name} must be an integer") from error
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
            return value

        rating = bounded("rating", int(base.get("rating", 1)), 1, 99)
        team_id = bounded("teamId", int(base.get("teamId", 0)), 1, 2_147_483_647)
        league_id = bounded("leagueId", int(base.get("leagueId", 0)), 1, 2_147_483_647)
        rare_flag = bounded("rareFlag", int(base.get("rareFlag", 0)), 0, 255)
        raw_attributes = source.get("attributes", base.get("attributes", []))
        if not isinstance(raw_attributes, list) or len(raw_attributes) != ATTRIBUTE_COUNT:
            raise ValueError("attributes must contain exactly six values")
        attributes: list[int] = []
        for index, value in enumerate(raw_attributes):
            try:
                number = int(value)
            except (TypeError, ValueError) as error:
                raise ValueError(f"attributes[{index}] must be an integer") from error
            if not 0 <= number <= 99:
                raise ValueError(f"attributes[{index}] must be between 0 and 99")
            attributes.append(number)

        raw_gameplay = source.get("gameplayAttributes", base.get("gameplayAttributes"))
        if not isinstance(raw_gameplay, dict):
            raise ValueError("full player data is unavailable for this base player")
        gameplay_attributes: dict[str, int] = {}
        for field in GAMEPLAY_FIELDS:
            try:
                value = int(raw_gameplay[field])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"gameplayAttributes.{field} must be an integer") from error
            if not 1 <= value <= 99:
                raise ValueError(f"gameplayAttributes.{field} must be between 1 and 99")
            gameplay_attributes[field] = value
        raw_traits = source.get("traits", base.get("traits"))
        if not isinstance(raw_traits, dict):
            raise ValueError("full player traits are unavailable for this base player")
        traits: dict[str, int] = {}
        for field, (minimum, maximum) in TRAIT_RANGES.items():
            try:
                value = int(raw_traits[field])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"traits.{field} must be an integer") from error
            if not minimum <= value <= maximum:
                raise ValueError(f"traits.{field} must be between {minimum} and {maximum}")
            traits[field] = value

        requested_version = source.get("version")
        if requested_version is None:
            occupied = {
                int(card.get("version", 0))
                for card in current_cards
                if int(card.get("assetId", 0)) == asset_id and card.get("cardId") != card_id
            }
            version = next((value for value in range(MIN_CUSTOM_VERSION, MAX_CUSTOM_VERSION + 1) if value not in occupied), None)
            if version is None:
                raise ValueError(f"assetId {asset_id} has no remaining custom card versions")
        else:
            version = bounded("version", MIN_CUSTOM_VERSION, MIN_CUSTOM_VERSION, MAX_CUSTOM_VERSION)
            if any(
                int(card.get("assetId", 0)) == asset_id
                and int(card.get("version", 0)) == version
                and card.get("cardId") != card_id
                for card in current_cards
            ):
                raise ValueError(f"assetId {asset_id} version {version} is already in use")

        now = int(time.time())
        return {
            "cardId": card_id or str(uuid.uuid4()),
            "assetId": asset_id,
            "resourceId": resource_id_for(asset_id, version),
            "definitionId": asset_id,
            "version": version,
            "name": str(base.get("name", "")),
            "commonName": str(base.get("commonName", base.get("name", ""))),
            "position": str(base.get("position", "CM")).upper(),
            "nation": int(base.get("nation", 0)),
            "nationName": str(base.get("nationName", "")),
            "rating": rating,
            "teamId": team_id,
            "leagueId": league_id,
            "rareFlag": rare_flag,
            "playStyle": int(base.get("playStyle", 0)),
            "attributes": attributes,
            "gameplayAttributes": gameplay_attributes,
            "traits": traits,
            "quality": _quality_for_rating(rating),
            "cardType": "custom",
            "specialCard": True,
            "createdAt": int(created_at or now),
            "updatedAt": now,
        }

    def list_cards(self) -> list[dict[str, Any]]:
        document = self._load()
        cards = [dict(card) for card in document["cards"] if isinstance(card, dict)]
        return sorted(cards, key=lambda card: (str(card.get("name", "")).casefold(), str(card.get("cardId", ""))))

    def get(self, card_id: str) -> dict[str, Any] | None:
        return next((card for card in self.list_cards() if card.get("cardId") == card_id), None)

    def save(self, source: dict[str, Any], card_id: str | None = None) -> dict[str, Any]:
        document = self._load()
        cards = [card for card in document["cards"] if isinstance(card, dict)]
        existing = next((card for card in cards if card.get("cardId") == card_id), None)
        if card_id is not None and existing is None:
            raise ValueError("custom card does not exist")
        card = self._card_from_input(
            source,
            current_cards=cards,
            card_id=card_id,
            created_at=None if existing is None else int(existing.get("createdAt", 0) or 0),
        )
        document["cards"] = [row for row in cards if row.get("cardId") != card_id] + [card]
        self._write(document)
        return card

    def delete(self, card_id: str) -> bool:
        document = self._load()
        cards = [card for card in document["cards"] if isinstance(card, dict)]
        retained = [card for card in cards if card.get("cardId") != card_id]
        if len(retained) == len(cards):
            return False
        document["cards"] = retained
        self._write(document)
        return True

    def export_document(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "cards": self.list_cards()}

    def import_document(self, document: dict[str, Any], *, replace: bool = False) -> list[dict[str, Any]]:
        if not isinstance(document, dict) or document.get("schema") != SCHEMA:
            raise ValueError("unsupported custom card import schema")
        incoming = document.get("cards")
        if not isinstance(incoming, list):
            raise ValueError("custom card import cards must be an array")
        current = [] if replace else self.list_cards()
        for raw_card in incoming:
            if not isinstance(raw_card, dict):
                raise ValueError("custom card import contains a non-object card")
            card_id = str(raw_card.get("cardId") or uuid.uuid4())
            existing = next((card for card in current if card.get("cardId") == card_id), None)
            card = self._card_from_input(
                raw_card,
                current_cards=current,
                card_id=card_id if existing is not None else None,
                created_at=None if existing is None else int(existing.get("createdAt", 0) or 0),
            )
            card["cardId"] = card_id
            current = [row for row in current if row.get("cardId") != card_id] + [card]
        self._write({"schema": SCHEMA, "cards": current})
        return self.list_cards()