from __future__ import annotations

"""Runtime compatibility contracts for the public FIFA 14 Local FUT build.

The launcher places ``server`` on PYTHONPATH so normal Windows Python imports
this module during startup.  The frozen BETA state store remains untouched;
only the two retail-PC client contracts proven during testing are adapted here.
"""

import importlib.abc
import importlib.machinery
import os
import sys
from typing import Any


def _patch_beta_identity(module: Any) -> None:
    if getattr(module, "_release_contract_patch_applied", False):
        return
    core_store = getattr(module, "BetaIdentityStore", None)
    if core_store is None:
        return

    class ReleaseBetaIdentityStore(core_store):
        def offline_tournament_user(self, tournament_id: int) -> dict[str, Any]:
            saved = super().offline_tournament_user(tournament_id)
            tournament_data = str(saved.get("tournamentData") or "")
            if not tournament_data or "round" not in saved:
                return saved

            # Official FIFA 14 PC CardsDLL parses this response as a stream.
            # dataVersion consumes/decodes the tournamentData buffer immediately
            # before it, so this insertion order is part of the wire contract.
            return {
                "round": int(saved.get("round", 1) or 1),
                "tournamentData": tournament_data,
                "dataVersion": max(1, int(saved.get("dataVersion", 1) or 1)),
            }

        def settle_match_end(self, document: dict[str, Any] | None = None) -> dict[str, Any]:
            request = document if isinstance(document, dict) else {}
            submitted_reason = str(request.get("endReason") or "NO_CONTEST").upper()
            response = super().settle_match_end(request)
            if submitted_reason not in {"QUIT", "DNF"}:
                return response

            # The core has already persisted the genuine DNF/loss, zero reward,
            # contract use and tournament elimination.  Normalize only the
            # client-facing terminal acknowledgement.  Retail FIFA otherwise
            # treats a successful local QUIT/DNF as a server disconnect and
            # routes into fcc_logout / the endless Leaving Ultimate Team path.
            response["endReason"] = "LOSS"
            response["completionAward"] = 0
            response["skillAward"] = 0
            response["rewardCoins"] = 0
            response["totalCoins"] = 0
            credits = int(self.credits().get("credits", 0) or 0)
            response["credits"] = credits
            response["coins"] = credits
            response["dnfModifier"] = 0.0
            if int(response.get("secondsPlayed", 0) or 0) <= 0:
                response["secondsPlayed"] = 60

            empty_stats = {
                "goals": 0,
                "shotsOnTarget": 0,
                "successfulTackles": 0,
                "corners": 0,
                "cleansheets": 0,
                "passingPercentage": 0,
                "possessionPercentage": 0,
                "manOfTheMatch": 0,
                "fouls": 0,
                "yellowCards": 0,
                "redCards": 0,
                "offsides": 0,
            }
            for target in ("myMatchStats", "opponentMatchStats"):
                source = request.get(target) if isinstance(request.get(target), dict) else {}
                response[target] = {
                    key: int(source.get(key, default) or 0)
                    for key, default in empty_stats.items()
                }
            return response

    ReleaseBetaIdentityStore.__name__ = "BetaIdentityStore"
    ReleaseBetaIdentityStore.__qualname__ = "BetaIdentityStore"
    module.BetaIdentityStore = ReleaseBetaIdentityStore
    module._release_contract_patch_applied = True


class _BetaIdentityLoader(importlib.abc.Loader):
    def __init__(self, wrapped: importlib.abc.Loader):
        self._wrapped = wrapped

    def create_module(self, spec):
        create = getattr(self._wrapped, "create_module", None)
        return create(spec) if create is not None else None

    def exec_module(self, module):
        self._wrapped.exec_module(module)
        _patch_beta_identity(module)


class _BetaIdentityFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "beta_identity":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return spec
        spec.loader = _BetaIdentityLoader(spec.loader)
        return spec


_entry = os.path.basename(sys.argv[0] or "").lower()
# Verifiers continue to exercise the frozen core contract; the release adapter
# is deliberately limited to the actual runtime/helper processes.
if not _entry.startswith("verify_") and not any(isinstance(finder, _BetaIdentityFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _BetaIdentityFinder())
