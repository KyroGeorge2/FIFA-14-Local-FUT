from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from beta_identity import BetaIdentityStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare FIFA 14 Local FUT v2.41 BETA progression profile")
    parser.add_argument("--database", required=True)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--test-coins", type=int, default=0, help="Optional one-time developer coin float; normal friend/shared builds leave this at 0")
    args = parser.parse_args()
    db = Path(args.database)
    if args.reset and db.exists():
        db.unlink()
    store = BetaIdentityStore(str(db), "existing")
    test_grant = store.ensure_consumables_beta_test_balance(int(args.test_coins)) if int(args.test_coins) > 0 else {"granted": 0, "balanceBefore": int(store.beta_profile_summary().get("coins", 0)), "balanceAfter": int(store.beta_profile_summary().get("coins", 0)), "idempotent": True}
    summary = store.beta_profile_summary()
    payload = {
        "profileKind": store.profile_kind(),
        "route": "retail-returning-user",
        "hasClub": store.has_club(),
        "snapshot": store.snapshot(),
        "beta": summary,
        "consumablesTestGrant": test_grant,
    }
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
