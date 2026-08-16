#!/usr/bin/env python3
"""Extract FIFA 14's cards_ng_db descriptor XML without modifying game files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from patch_fifa14_fut_legends_db import (
    discover_descriptor_record,
    pair_descriptor,
    parse_descriptor,
)


META_NAMES = ("cards_ng_db-meta.xml", "cards_ng_db_meta.xml", "fifa_ng_db-meta.xml")
ARCHIVES = ("patch.big", "cards0.big")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def find_descriptor(game_root: Path) -> tuple[bytes, dict[str, Any]]:
    for name in META_NAMES:
        path = game_root / "data" / "db" / name
        if path.is_file():
            return path.read_bytes(), {
                "archive": None,
                "entry": str(path),
                "sourceKind": "loose",
            }

    for archive_name in ARCHIVES:
        named = pair_descriptor(game_root, archive_name, META_NAMES)
        if named is not None:
            return named

    for archive_name in ARCHIVES:
        archive = game_root / archive_name
        if not archive.is_file():
            continue
        discovered = discover_descriptor_record(archive, archive.with_suffix(".bh"))
        if discovered is not None:
            return discovered

    raise RuntimeError("cards_ng_db descriptor XML was not found in the loose DB or supported FIFA archives")


def descriptor_manifest(xml: bytes, source: dict[str, Any]) -> dict[str, Any]:
    descriptor = parse_descriptor(xml)
    tables: dict[str, Any] = {}
    for table in descriptor.values():
        key = table.name.lower()
        if key in tables:
            continue
        tables[key] = {
            "name": table.name,
            "shortName": table.short_name,
            "fields": [
                {
                    "name": field.name,
                    "shortName": field.short_name,
                    "rangeLow": field.range_low,
                    "rangeHigh": field.range_high,
                }
                for field in table.fields.values()
            ],
        }
    return {
        "schema": "fifa14-cards-ng-db-descriptor-v1",
        "readOnly": True,
        "source": source,
        "sha256": sha256(xml),
        "tables": sorted(tables.values(), key=lambda table: table["name"].lower()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract FIFA 14 cards_ng_db descriptor XML read-only")
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Directory for cards_ng_db-meta.xml and manifest.json")
    args = parser.parse_args()

    game_root = args.game_root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    xml, source = find_descriptor(game_root)
    manifest = descriptor_manifest(xml, source)

    output.mkdir(parents=True, exist_ok=True)
    xml_path = output / "cards_ng_db-meta.xml"
    manifest_path = output / "cards_ng_db-meta.manifest.json"
    xml_path.write_bytes(xml)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "xml": str(xml_path),
        "manifest": str(manifest_path),
        "tableCount": len(manifest["tables"]),
        "source": source,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())