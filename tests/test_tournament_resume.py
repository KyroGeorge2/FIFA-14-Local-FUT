"""Offline tournament resume invariants.

Regression coverage for issue #25 (and the second half of issue #9): after
winning a round and leaving FUT, reopening the cup crashed the client.

The server advances the knockout round itself and deliberately does not invent
a ``tournamentData`` bracket blob, so a server-advanced round has an empty one.
It was still advertised as resumable, which handed the retail client an empty
buffer to parse as a bracket.

These tests use a temporary database and need no FIFA 14 installation.
"""
from __future__ import annotations

import base64
import sys
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from beta_identity import BetaIdentityStore, OFFLINE_TOURNAMENTS  # noqa: E402

TOURNAMENT_ID = int(OFFLINE_TOURNAMENTS[0]["tournamentId"])


@pytest.fixture
def store(tmp_path: Path) -> BetaIdentityStore:
    store = BetaIdentityStore(str(tmp_path / "beta.db"))
    store.ensure_beta_starter_club()
    return store


def _win_a_round(store: BetaIdentityStore, tournament_id: int = TOURNAMENT_ID) -> dict:
    with closing(store._connect()) as connection, connection:
        persona_id = int(store._identity(connection)["persona_id"])
        return store._settle_tournament_result_locked(
            connection, persona_id, tournament_id, "WIN", match_id="test-match"
        )


def _stored_row(store: BetaIdentityStore, tournament_id: int = TOURNAMENT_ID):
    with closing(store._connect()) as connection:
        persona_id = int(store._identity(connection)["persona_id"])
        return connection.execute(
            "SELECT round_value, tournament_data, progress_data FROM beta_tournament_progress "
            "WHERE persona_id=? AND tournament_id=?",
            (persona_id, tournament_id),
        ).fetchone()


def test_server_advanced_round_is_not_advertised_without_a_bracket(
    store: BetaIdentityStore,
) -> None:
    """Issue #25: winning a round must not advertise an unopenable saved cup."""
    outcome = _win_a_round(store)
    assert outcome["advanced"] is True
    assert outcome["round"] == 2

    row = _stored_row(store)
    assert int(row["round_value"]) == 2
    assert str(row["tournament_data"] or "") == "", (
        "precondition: the server advances the round without storing a bracket"
    )

    listed = store.offline_tournament_user_list()["tournamentId"]
    assert TOURNAMENT_ID not in listed, (
        "a cup with no stored bracket must not be offered as resumable"
    )

    resumed = store.offline_tournament_user(TOURNAMENT_ID)
    assert not str(resumed.get("tournamentData", "")), (
        "the client must never be handed an empty bracket to parse"
    )
    assert "round" not in resumed


def test_server_still_tracks_the_round_after_a_win(store: BetaIdentityStore) -> None:
    """Not advertising a resume must not throw away the server-side progress."""
    _win_a_round(store)
    with closing(store._connect()) as connection:
        persona_id = int(store._identity(connection)["persona_id"])
        assert store._tournament_round_locked(connection, persona_id, TOURNAMENT_ID) == 2


def test_client_written_bracket_is_still_resumable(store: BetaIdentityStore) -> None:
    """Guard against over-correcting: a real saved bracket must still resume."""
    store.update_offline_tournament_user(
        TOURNAMENT_ID,
        {
            "round": 2,
            "dataVersion": 3,
            "tournamentData": base64.b64encode(b"real-bracket-bytes").decode("ascii"),
            "progressDataVersion": 2,
            "progressData": base64.b64encode(b"\x01\x02\x03\x04").decode("ascii"),
        },
    )

    assert TOURNAMENT_ID in store.offline_tournament_user_list()["tournamentId"]

    resumed = store.offline_tournament_user(TOURNAMENT_ID)
    assert int(resumed["round"]) == 2
    assert base64.b64decode(resumed["tournamentData"]) == b"real-bracket-bytes"


def test_first_round_pre_match_blob_is_still_rejected(store: BetaIdentityStore) -> None:
    """Existing BETA 2.18 behaviour: zeroed first-round progress is not a save."""
    store.update_offline_tournament_user(
        TOURNAMENT_ID,
        {
            "round": 1,
            "dataVersion": 1,
            "tournamentData": base64.b64encode(b"pre-match-bracket").decode("ascii"),
            "progressDataVersion": 1,
            "progressData": base64.b64encode(b"\x00\x00\x00\x00").decode("ascii"),
        },
    )
    assert TOURNAMENT_ID not in store.offline_tournament_user_list()["tournamentId"]


@pytest.mark.parametrize(
    "round_value, tournament_data, progress_data, expected",
    [
        (2, "", "", False),                                    # server-advanced round
        (5, "", "", False),                                    # later server-advanced round
        (1, "", "", False),                                    # untouched cup
        (2, "YnJhY2tldA==", "", True),                         # real saved later round
        (1, "YnJhY2tldA==", "AAAAAA==", False),                # zeroed first-round progress
        (1, "YnJhY2tldA==", "AQIDBA==", True),                 # live first-round progress
    ],
)
def test_resumable_predicate(
    round_value: int, tournament_data: str, progress_data: str, expected: bool
) -> None:
    assert (
        BetaIdentityStore._tournament_progress_is_resumable(
            round_value, tournament_data, progress_data
        )
        is expected
    )
