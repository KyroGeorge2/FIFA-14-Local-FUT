"""Pack generation invariants.

Regression coverage for issue #14: a pack handed out two cards for the same
footballer, because a special card and its base card share an ``assetId`` but
carry different ``resourceId`` values.

These tests run against the bundled catalogues and a temporary SQLite file, so
they need no FIFA 14 installation.
"""
from __future__ import annotations

import random
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from local_identity import (  # noqa: E402
    LocalIdentityStore,
    NORMAL_SPECIAL_PLAYER_CATALOG,
    PACK_DEFINITIONS,
    PLAYER_BY_ASSET,
    PLAYER_ITEM_TYPE,
)

# Highest special-card chance in pack-weights.v237.json, so the special draw is
# actually exercised: 104 "Rare Players Pack", 105 "Jumbo Rare Players Pack".
SPECIAL_HEAVY_PACK_TYPES = (104, 105)


@pytest.fixture
def store(tmp_path: Path) -> LocalIdentityStore:
    return LocalIdentityStore(tmp_path / "identity.db", initial_mode="new")


def _players(items: list[dict]) -> list[dict]:
    return [i for i in items if str(i.get("itemType", "")).lower() == PLAYER_ITEM_TYPE]


def test_specials_share_asset_ids_with_base_cards() -> None:
    """The precondition that makes issue #14 possible must still hold.

    If this ever fails the catalogues changed shape and the dedupe rules below
    need re-checking rather than silently passing.
    """
    colliding = [
        special for special in NORMAL_SPECIAL_PLAYER_CATALOG
        if int(special.get("assetId", -1)) in PLAYER_BY_ASSET
    ]
    assert colliding, "expected special cards to reuse base-card assetIds"


def test_special_draw_excludes_players_already_in_the_pack() -> None:
    """A special must not reuse a footballer already placed in the pack."""
    # Robbie Keane: the duplicate reported in issue #14.
    base = PLAYER_BY_ASSET[330]
    used_resources = {int(base["resourceId"])}
    used_assets = {int(base["assetId"])}

    rng = random.Random(0)
    for _ in range(200):
        special = LocalIdentityStore._weighted_special_player(
            rng,
            quality="gold",
            excluded_resources=used_resources,
            excluded_assets=used_assets,
        )
        if special is None:
            continue
        assert int(special["assetId"]) not in used_assets, (
            f"special {special['resourceId']} duplicates footballer "
            f"{special['assetId']}, which is already in the pack"
        )


def test_legend_draw_excludes_players_already_in_the_pack() -> None:
    rng = random.Random(0)
    first = LocalIdentityStore._weighted_legend(rng)
    if first is None:
        pytest.skip("no legends in the bundled catalogue")

    used_assets = {int(first["assetId"])}
    for _ in range(100):
        legend = LocalIdentityStore._weighted_legend(rng, excluded_assets=used_assets)
        if legend is None:
            continue
        assert int(legend["assetId"]) not in used_assets


def test_pack_with_a_forced_collision_never_repeats_a_footballer(
    store: LocalIdentityStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the real generator with a collision guaranteed to be available.

    Randomly generated packs hit this only rarely -- the same footballer has to
    be drawn as a base card *and* as a special in one pack -- so a sampling test
    passes happily against the bug. Here the special pool is narrowed to Robbie
    Keane's variants and the base draw is pinned to Keane, which makes the
    duplicate certain unless the special draw honours ``excluded_assets``.
    """
    import local_identity

    keane = PLAYER_BY_ASSET[330]
    keane_specials = [
        s for s in NORMAL_SPECIAL_PLAYER_CATALOG if int(s.get("assetId", -1)) == 330
    ]
    assert keane_specials, "expected Robbie Keane special variants in the catalogue"

    # Distinct stand-ins, so any repeat the assertion sees is a real duplicate
    # produced by the generator rather than an artefact of this stub.
    substitutes = [
        p for p in PLAYER_BY_ASSET.values() if int(p["assetId"]) != 330
    ][:400]
    assert len(substitutes) > 60, "need enough distinct stand-in players"

    monkeypatch.setattr(local_identity, "NORMAL_SPECIAL_PLAYER_CATALOG", keane_specials)

    def fake_base_draw(rng, *, quality, rare_slot, promo,
                       excluded_assets=None, max_rating=None):
        """Hand out Keane first, then a fresh distinct player on every call."""
        excluded_assets = excluded_assets or set()
        if 330 not in excluded_assets:
            return keane
        for candidate in substitutes:
            if int(candidate["assetId"]) not in excluded_assets:
                return candidate
        raise AssertionError("stand-in pool exhausted")

    monkeypatch.setattr(
        local_identity.LocalIdentityStore, "_weighted_player", staticmethod(fake_base_draw)
    )

    pack_type = SPECIAL_HEAVY_PACK_TYPES[0]
    definition = dict(PACK_DEFINITIONS[pack_type])
    # Force the special jackpot so the collision path is always taken.
    monkeypatch.setitem(
        local_identity.PACK_WEIGHTS_DOCUMENT,
        "specialChancePerPack",
        {str(pack_type): 1.0},
    )

    connection = store._connect()
    try:
        for pack_id in range(1, 26):
            items = store._generate_pack_contents_locked(
                connection, pack_id=pack_id, definition=definition
            )
            assets = [int(p.get("assetId", 0)) for p in _players(items)]
            repeated = [a for a, c in Counter(assets).items() if c > 1]
            assert not repeated, (
                f"pack {pack_id} handed out {repeated} twice: a special card must "
                f"not reuse a footballer already placed in the pack"
            )
    finally:
        connection.close()


@pytest.mark.parametrize("pack_type", SPECIAL_HEAVY_PACK_TYPES)
def test_generated_pack_never_repeats_a_footballer(
    store: LocalIdentityStore, pack_type: int
) -> None:
    definition = PACK_DEFINITIONS[pack_type]
    connection = store._connect()
    try:
        for pack_id in range(1, 41):
            items = store._generate_pack_contents_locked(
                connection, pack_id=pack_id * 1000 + pack_type, definition=definition
            )
            players = _players(items)
            assert players, f"pack type {pack_type} produced no player cards"

            repeated = [
                asset for asset, count
                in Counter(int(p.get("assetId", 0)) for p in players).items()
                if count > 1
            ]
            assert not repeated, (
                f"pack type {pack_type} pack {pack_id} repeated footballer(s) {repeated}"
            )
    finally:
        connection.close()


@pytest.mark.parametrize("pack_type", SPECIAL_HEAVY_PACK_TYPES)
def test_generated_pack_never_repeats_an_exact_card(
    store: LocalIdentityStore, pack_type: int
) -> None:
    definition = PACK_DEFINITIONS[pack_type]
    connection = store._connect()
    try:
        for pack_id in range(1, 41):
            items = store._generate_pack_contents_locked(
                connection, pack_id=pack_id * 1000 + pack_type, definition=definition
            )
            resources = [int(p.get("resourceId", 0)) for p in _players(items)]
            assert len(resources) == len(set(resources))
    finally:
        connection.close()
