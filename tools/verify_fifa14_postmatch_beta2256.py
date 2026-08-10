#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT))

from beta_identity import BetaIdentityStore  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"BETA 2.25.8 post-match verifier failed: {message}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fifa14-beta2256-") as td:
        db = Path(td) / "identity.sqlite3"
        store = BetaIdentityStore(str(db), "new")
        store.reset_match({})

        # Reproduce profiles damaged by repeated QA forfeits in older beta builds.
        with closing(sqlite3.connect(db)) as con, con:
            con.execute("UPDATE beta_accounts SET dnf_modifier=0")

        before = int(store.credits()["credits"])
        document = {
            "endReason": "WIN",
            "myRating": 10,
            "opponentRating": 10,
            "items": [{"id": 180000000001, "goals": 8, "fitness": 96}],
            "myMatchStats": {
                "goals": 8,
                "shotsOnTarget": 18,
                "successfulTackles": 25,
                "corners": 3,
                "cleansheets": 1,
                "passingPercentage": 83,
                "possessionPercentage": 55,
                "manOfTheMatch": 1,
                "fouls": 1,
                "yellowCards": 1,
                "redCards": 0,
                "offsides": 0,
            },
            "opponentMatchStats": {
                "goals": 0,
                "shotsOnTarget": 0,
                "successfulTackles": 8,
                "corners": 0,
                "cleansheets": 0,
                "passingPercentage": 82,
                "possessionPercentage": 45,
                "manOfTheMatch": 0,
                "fouls": 1,
                "yellowCards": 0,
                "redCards": 0,
                "offsides": 2,
            },
            "matchData": "beta2256-completed-match-regression",
        }
        response = store.settle_match_end(document)
        require(response.get("endReason") == "WIN", f"wrong end reason: {response}")
        require(int(response.get("secondsPlayed", 0)) == 5400, f"completed match stayed zero seconds: {response}")
        require(response.get("myMatchStats") == document["myMatchStats"], "client stats were zeroed/rewritten")
        require(response.get("opponentMatchStats") == document["opponentMatchStats"], "opponent stats were zeroed/rewritten")

        after = int(store.credits()["credits"])
        with closing(sqlite3.connect(db)) as con:
            row = con.execute(
                "SELECT reward_coins,reward_breakdown_json FROM beta_match_sessions "
                "WHERE settled=1 ORDER BY completed_at DESC LIMIT 1"
            ).fetchone()
            dnf = float(con.execute("SELECT dnf_modifier FROM beta_accounts LIMIT 1").fetchone()[0])
        require(row is not None, "completed match was not persisted")
        breakdown = json.loads(row[1])
        require(int(breakdown.get("completionAward", 0)) == 325, f"completion award wrong: {breakdown}")
        require(int(breakdown.get("skillAward", 0)) > 0, f"skill award remained zero: {breakdown}")
        require(int(breakdown.get("goalsFor", -1)) == 8, f"per-player goals overrode 8-0 team score: {breakdown}")
        require(int(row[0]) == int(breakdown["totalCoins"]) == after - before, "wallet and match reward diverged")
        require(dnf >= 0.25, f"legacy zero DNF multiplier was not repaired: {dnf}")

    trace = (ROOT / "tools" / "frida_pc_fut_nav_route_patch_trace.py").read_text(encoding="utf-8")
    require("ACTIVE_MATCH_RETURN_GUARD_MS = 45 * 60 * 1000" in trace, "full-match return guard window missing")
    require(":active-match', ACTIVE_MATCH_RETURN_GUARD_MS" in trace, "match-create pre-arm missing")
    require("postMatchStayInFutView" in trace and "fcc_logout" in trace, "one-shot GameHub return repair missing")

    print(json.dumps({
        "status": "ok",
        "build": "2.41.1-beta2.25.9",
        "completedSeconds": 5400,
        "completionAward": 325,
        "skillAwardNonZero": True,
        "legacyDnfFloor": 0.25,
        "fullMatchReturnGuardMinutes": 45,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
