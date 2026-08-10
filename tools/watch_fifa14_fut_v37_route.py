#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


def has_club(database: Path) -> bool:
    if not database.is_file():
        return False
    try:
        with sqlite3.connect(database, timeout=2) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "clubs" not in tables:
                return False
            return connection.execute("SELECT 1 FROM clubs LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


def emit(**document: object) -> None:
    print(json.dumps({"time": time.time(), **document}, sort_keys=True), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore FIFA 14 retail returning-user route after v2.37 persists a club"
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--patcher", type=Path, required=True)
    parser.add_argument("--poll-ms", type=int, default=500)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()

    database = args.database.expanduser().resolve()
    game_root = args.game_root.expanduser().resolve()
    state_dir = args.state_dir.expanduser().resolve()
    patcher = args.patcher.expanduser().resolve()
    emit(kind="v237-route-watch-started", database=str(database), gameRoot=str(game_root))
    deadline = time.monotonic() + max(1, args.timeout_seconds)
    while time.monotonic() < deadline:
        if has_club(database):
            command = [
                sys.executable,
                str(patcher),
                "--game-root",
                str(game_root),
                "--state-dir",
                str(state_dir),
                "--restore-retail",
            ]
            completed = subprocess.run(command, text=True, capture_output=True)
            emit(
                kind="v237-route-restored-after-club-save",
                returncode=completed.returncode,
                stdout=completed.stdout.strip(),
                stderr=completed.stderr.strip(),
            )
            return completed.returncode
        time.sleep(max(50, args.poll_ms) / 1000.0)
    emit(kind="v237-route-watch-timeout")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
