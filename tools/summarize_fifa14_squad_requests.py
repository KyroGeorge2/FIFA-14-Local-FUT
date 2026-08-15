#!/usr/bin/env python3
"""Summarize every /squad request FIFA 14 made, plus the persisted squad state.

The local server can only create a second squad if the client actually addresses
one -- by path (``PUT /squad/{id}``) or by an ``id``/``squadId`` in the body.
This reads the probe capture written by the launcher and reports exactly which
squad ids the client asked for, so an in-game "create squad" that does nothing
can be told apart from a server that refused the write.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

DEFAULT_LOG = Path(__file__).resolve().parents[1] / "artifacts" / "redirect-probe.log"
DEFAULT_DATABASE = Path(os.environ.get("LOCALAPPDATA", "")) / "FIFA14LocalFUTBeta" / "local-fut-beta-v2410.sqlite3"


def load_json_lines(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.is_file():
        return events
    for number, raw_line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            item.setdefault("_line", number)
            events.append(item)
    return events


def decode_body(event: dict[str, Any]) -> dict[str, Any] | None:
    raw = event.get("body_hex")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        document = json.loads(bytes.fromhex(raw).decode("utf-8", errors="replace"))
    except (ValueError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def body_squad_id(document: dict[str, Any]) -> int:
    for key in ("squadId", "id"):
        try:
            value = int(document.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def path_squad_id(path: str) -> int:
    tail = path.partition("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def summarize_request(event: dict[str, Any]) -> dict[str, Any]:
    path = str(event.get("path", ""))
    document = decode_body(event)
    record: dict[str, Any] = {
        "line": event.get("_line"),
        "time": event.get("time"),
        "method": str(event.get("method", "")),
        "path": path,
        "pathSquadId": path_squad_id(path),
    }
    headers = event.get("headers")
    if isinstance(headers, dict):
        override = headers.get("X-HTTP-Method-Override") or headers.get("x-http-method-override")
        if override:
            record["methodOverride"] = str(override).upper()
    if document is not None:
        players = document.get("players")
        record["bodySquadId"] = body_squad_id(document)
        record["bodyKeys"] = sorted(document.keys())
        if isinstance(document.get("squadName"), str):
            record["squadName"] = document["squadName"]
        if "active" in document:
            record["active"] = document["active"]
        if isinstance(players, list):
            record["playerSlots"] = len(players)
            record["filledSlots"] = sum(
                1 for row in players
                if isinstance(row, dict) and isinstance(row.get("itemData"), dict)
                and int((row["itemData"].get("id") or row["itemData"].get("itemId") or 0) or 0) > 0
            )
    return record


def database_state(database: Path) -> dict[str, Any]:
    if not database.is_file():
        return {"database": str(database), "found": False}
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        squads = [
            {
                "squadId": int(row["squad_id"]),
                "personaId": int(row["persona_id"]),
                "squadName": row["squad_name"],
                "formation": row["formation"],
                "active": bool(row["active"]),
                "filledSlots": int(connection.execute(
                    "SELECT COUNT(*) FROM squad_players WHERE squad_id = ? AND item_id > 0", (int(row["squad_id"]),)
                ).fetchone()[0]),
            }
            for row in connection.execute("SELECT * FROM squads ORDER BY squad_id").fetchall()
        ]
        active_ids = [
            int(row["active_squad_id"]) for row in connection.execute(
                "SELECT active_squad_id FROM fut_users WHERE active_squad_id IS NOT NULL"
            ).fetchall()
        ]
    return {"database": str(database), "found": True, "squads": squads, "activeSquadIds": active_ids}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="probe capture (artifacts/redirect-probe.log)")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="persistent BETA identity database")
    parser.add_argument("--limit", type=int, default=40, help="most recent squad requests to print")
    arguments = parser.parse_args()

    events = load_json_lines(arguments.log)
    requests = [
        summarize_request(event)
        for event in events
        if event.get("kind") == "http-probe" and "/squad" in str(event.get("path", "")).lower()
    ]
    responses = [
        {
            "line": event.get("_line"), "method": event.get("effective_method", event.get("method")),
            "path": event.get("path"), "responseName": event.get("response_name"), "status": event.get("status"),
        }
        for event in events
        if event.get("kind") == "fut-http-response" and "/squad" in str(event.get("path", "")).lower()
    ]
    states = [
        {
            "line": event.get("_line"), "method": event.get("method"), "path": event.get("path"),
            "incomingFilled": event.get("incoming_filled"), "outgoingFilled": event.get("outgoing_filled"),
        }
        for event in events if event.get("kind") == "fut-squad-state-beta222"
    ]

    # A delete the client sends somewhere other than /squad would be invisible in
    # the filtered view above, so keep every non-GET request in the capture.
    other_writes = [
        summarize_request(event)
        for event in events
        if event.get("kind") == "http-probe"
        and str(event.get("method", "")).upper() not in {"GET", "HEAD"}
        and "/squad" not in str(event.get("path", "")).lower()
    ]
    writes = [row for row in requests if str(row.get("method")) in {"PUT", "POST"} or row.get("methodOverride") in {"PUT", "POST"}]
    addressed = sorted({row["pathSquadId"] for row in requests if row["pathSquadId"]} |
                       {int(row.get("bodySquadId", 0)) for row in requests if row.get("bodySquadId")})
    written = sorted({row["pathSquadId"] for row in writes if row["pathSquadId"]} |
                     {int(row.get("bodySquadId", 0)) for row in writes if row.get("bodySquadId")})
    # FIFA 14 has no DELETE verb: the squad hub deletes through
    # GET /ut/delete/game/fifa14/squad/{id}, the same tunnel as /ut/delete/auth.
    deletes = [
        row for row in requests
        if str(row.get("method", "")).upper() == "DELETE" or str(row.get("methodOverride", "")).upper() == "DELETE"
        or str(row.get("path", "")).lower().startswith("/ut/delete/game/fifa14/squad/")
    ]

    findings: list[str] = []
    if not events:
        findings.append(f"No probe capture at {arguments.log}. Launch through RUN_FIFA14_LOCAL_BETA.cmd first.")
    elif not requests:
        findings.append("The client made no /squad request at all in this capture.")
    else:
        if not writes:
            findings.append("The client only read or deleted squads in this capture; it sent no PUT/POST.")
        elif len(written) <= 1:
            findings.append(
                "Every squad write addressed the same id "
                f"{written or '(none in path or body)'}: the in-game action is not asking the server for a new squad. "
                "Squads are created by POST /squad carrying id 0."
            )
        if writes and not any(row.get("pathSquadId") or row.get("bodySquadId") for row in writes):
            findings.append(
                "Squad writes carry no id in the path or the body, so they always fall back to the active squad."
            )
        if deletes:
            unhandled = [
                response for response in responses
                if str(response.get("path", "")).lower().startswith("/ut/delete/")
                and str(response.get("responseName", "")).startswith("unmapped")
            ]
            findings.append(
                f"Squad deletes seen for ids {sorted({row['pathSquadId'] for row in deletes if row['pathSquadId']})}."
                + (" Some were answered by the generic unmapped-route ack and did nothing." if unhandled else "")
            )
        else:
            findings.append(
                "No squad delete was seen. If a squad was deleted in-game during this capture, "
                "the client sent no delete request the server could act on -- check nonSquadWrites for "
                "the request it sent instead."
            )
    if not findings:
        findings.append(f"The client addressed multiple squad ids: {written}.")

    print(json.dumps({
        "log": str(arguments.log),
        "squadRequests": len(requests),
        "squadWrites": len(writes),
        "squadIdsAddressed": addressed,
        "squadIdsWritten": written,
        "findings": findings,
        "recentRequests": requests[-max(arguments.limit, 0):],
        "recentResponses": responses[-max(arguments.limit, 0):],
        "recentSquadState": states[-max(arguments.limit, 0):],
        "nonSquadWrites": other_writes[-max(arguments.limit, 0):],
        "persistedState": database_state(arguments.database),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
