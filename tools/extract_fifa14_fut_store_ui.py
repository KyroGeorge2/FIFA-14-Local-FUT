#!/usr/bin/env python3
"""Read-only FIFA 14 patch archive scanner for the normal FUT Store frontend.

This tool never edits patch.big/patch.bh.  It walks the indexed patch records,
decodes FIFA 14 chunkzip payloads, recursively inspects BIG4/BIGF packages, and
exports only records/inner blobs containing Store frontend strings that can tell
us what the UI expects after ValidateCoinPurchase succeeds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import tempfile
import zipfile
import zlib

BH_MAGIC = b"ViV4"
CHUNKZIP_MAGIC = b"chunkzip"
BIG_MAGICS = {b"BIG4", b"BIGF"}
MAX_RECURSION = 4
MAX_MATCHES = 80
MAX_SAVED_PAYLOAD = 32 * 1024 * 1024

# These are deliberately specific to the FIFA 14 Store handoff.  The v2.40.9
# retail capture proved POST_PURCHASE_ACTION is compared with CREATEPACK,
# REFRESHSTORE and REFRESHBALANCE.  BUY_PACK remains a negative-control search
# target because older local builds emitted it but retail did not.
TARGETS = (
    b"POST_PURCHASE_ACTION",
    b"PurchasePack",
    b"PURCHASEPACK",
    b"ValidateCoinPurchase",
    b"ValidatePointsPurchase",
    b"IsPackAvailable",
    b"COINS_PURCHASE_ENABLED",
    b"POINTS_PURCHASE_ENABLED",
    b"FUTStoreReady",
    b"GOTO_STORE_MYPACK",
    b"GOTO_STORE_GOLDPACK",
    b"GOTO_STORE_SPECIALPACK",
    b"CREATEPACK",
    b"REFRESHSTORE",
    b"REFRESHBALANCE",
    b"BUY_PACK",
    # BETA 2.3: recover the retail art-routing contract as well as purchase
    # routing. BETA 2.2 proved that guessing sequential ASSET_ID values is wrong
    # (4 = Season Ticket; 6..11 = NOT FOUND on this PC build), so these exact
    # StoreFrontWC symbols are now captured with byte offsets and local context.
    b"GetPackForegroundAssetPath",
    b"ASSET_ID",
    b"ASSET_PATH",
    b"packs_backgrounds",
    b"BANNER_PATH",
    b"packs_banners",
    b"SPECIAL_PACK_ID",
    b"FOREGROUND_ASSET_PATH",
    b"OVERRIDE_ASSET_ID",
    b"OVERRIDE_ASSET_PATH",
    b"SEASONTICKET",
    b"SHOW_SEASONTICKETS",
    b"IS_SEASON_TICKET",
    b"IS_PREMIUM",
    b"SHOW_DEAL",
    b"IS_DEAL",
    b"SHOW_PROMO",
    b"IS_PROMO",
)
SECONDARY_TARGETS = (
    b"STORE_ID",
    b"SERVER_ID",
    b"SALE_TYPE",
    b"PACKS_REMAINING",
    b"getArtAssetPath",
    b"getPackFrontPath",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def align(value: int, boundary: int = 16) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def printable_strings(data: bytes, minimum: int = 4) -> list[str]:
    rx = re.compile(rb"[\x20-\x7e]{%d,}" % minimum)
    result: list[str] = []
    seen: set[str] = set()
    for match in rx.finditer(data):
        value = match.group().decode("ascii", errors="replace")
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "unnamed"


def target_hits(data: bytes) -> list[str]:
    return [value.decode("ascii") for value in TARGETS if value in data]


def secondary_hits(data: bytes) -> list[str]:
    return [value.decode("ascii") for value in SECONDARY_TARGETS if value in data]


def contexts(data: bytes, needles: tuple[bytes, ...], radius: int = 96) -> list[dict]:
    out: list[dict] = []
    for needle in needles:
        start = 0
        count = 0
        while count < 12:
            pos = data.find(needle, start)
            if pos < 0:
                break
            lo = max(0, pos - radius)
            hi = min(len(data), pos + len(needle) + radius)
            raw = data[lo:hi]
            printable = "".join(chr(b) if 32 <= b <= 126 else "." for b in raw)
            out.append({"target": needle.decode("ascii"), "offset": pos, "context": printable})
            start = pos + max(1, len(needle))
            count += 1
    return out


def decode_chunkzip(payload: bytes) -> tuple[bytes, dict]:
    if len(payload) < 40 or payload[:8] != CHUNKZIP_MAGIC:
        raise ValueError("not chunkzip")
    version, output_size, chunk_size, count, alignment, a, b, c = struct.unpack_from(">IIIIIIII", payload, 8)
    if version != 2 or alignment not in (8, 16) or a or b or c:
        raise ValueError(
            f"unsupported chunkzip header version={version} alignment={alignment} tail={a}/{b}/{c}"
        )
    pos = 40
    output = bytearray()
    chunks = []
    for index in range(count):
        if pos + 8 > len(payload):
            raise ValueError(f"truncated chunk descriptor {index}")
        stored_size, compression_type = struct.unpack_from(">II", payload, pos)
        start = pos + 8
        end = start + stored_size
        if end > len(payload):
            raise ValueError(f"truncated chunk {index}")
        raw = payload[start:end]
        if compression_type == 0:
            decoded = raw
        elif compression_type == 1:
            decoded = zlib.decompress(raw, -zlib.MAX_WBITS)
        else:
            raise ValueError(f"unsupported compression type {compression_type}")
        output.extend(decoded)
        chunks.append({
            "index": index,
            "stored_size": stored_size,
            "decoded_size": len(decoded),
            "compression_type": compression_type,
        })
        pos = align(end + 8, alignment) - 8
    if len(output) != output_size:
        raise ValueError(f"decoded size {len(output)} != declared {output_size}")
    return bytes(output), {
        "version": version,
        "output_size": output_size,
        "chunk_size": chunk_size,
        "chunk_count": count,
        "alignment": alignment,
        "chunks": chunks,
    }


def parse_big(data: bytes) -> tuple[list[dict], dict]:
    if len(data) < 16 or data[:4] not in BIG_MAGICS:
        raise ValueError("not BIG4/BIGF")
    magic = data[:4].decode("ascii", errors="replace")
    declared_be = struct.unpack_from(">I", data, 4)[0]
    declared_le = struct.unpack_from("<I", data, 4)[0]
    count = struct.unpack_from(">I", data, 8)[0]
    header_size = struct.unpack_from(">I", data, 12)[0]
    if not 0 <= count <= 1_000_000:
        raise ValueError(f"unreasonable BIG count {count}")
    if not 16 <= header_size <= len(data):
        raise ValueError(f"invalid BIG header size {header_size} for {len(data)} bytes")
    pos = 16
    entries = []
    for index in range(count):
        if pos + 8 > header_size:
            raise ValueError("truncated BIG entry table")
        offset, size = struct.unpack_from(">II", data, pos)
        pos += 8
        end = data.find(b"\0", pos, header_size)
        if end < 0:
            raise ValueError("unterminated BIG entry name")
        name = data[pos:end].decode("utf-8", errors="replace").replace("\\", "/")
        pos = end + 1
        if offset + size > len(data):
            raise ValueError(f"BIG entry outside package: {name}")
        entries.append({"index": index, "name": name, "offset": offset, "size": size})
    return entries, {
        "magic": magic,
        "actual_size": len(data),
        "declared_be": declared_be,
        "declared_le": declared_le,
        "header_size": header_size,
        "entry_count": count,
    }


def bh_records(bh: bytes) -> list[dict]:
    if len(bh) < 16 or bh[:4] != BH_MAGIC:
        raise ValueError("patch.bh is not a ViV4 index")
    count = struct.unpack_from(">I", bh, 8)[0]
    table_end = 16 + count * 20
    if table_end > len(bh):
        raise ValueError(f"patch.bh record table truncated: count={count}")
    records = []
    for index in range(count):
        pos = 16 + index * 20
        offset, size, reserved, hi, lo = struct.unpack_from(">IIIII", bh, pos)
        records.append({
            "index": index,
            "offset": offset,
            "size": size,
            "reserved": reserved,
            "path_hash": f"{((hi << 32) | lo):016X}",
        })
    return records


def write_zip(root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.unlink(missing_ok=True)
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    temp.replace(output)


class Collector:
    def __init__(self, root: Path):
        self.root = root
        self.matches: list[dict] = []
        self.errors: list[dict] = []
        self.seen_blob_hashes: set[str] = set()

    def save_match(self, *, record: dict, label: str, data: bytes, depth: int, metadata: dict | None = None) -> None:
        digest = sha256(data)
        key = f"{record['index']}:{label}:{digest}"
        if key in self.seen_blob_hashes or len(self.matches) >= MAX_MATCHES:
            return
        self.seen_blob_hashes.add(key)
        hits = target_hits(data)
        if not hits:
            return
        entry = {
            "record": record,
            "label": label,
            "depth": depth,
            "size": len(data),
            "sha256": digest,
            "targets": hits,
            "secondary_targets": secondary_hits(data),
            "contexts": contexts(data, TARGETS),
        }
        if metadata:
            entry["metadata"] = metadata
        index = len(self.matches)
        folder = self.root / "matches" / f"{index:03d}_record_{record['index']:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        if len(data) <= MAX_SAVED_PAYLOAD:
            blob_name = safe_name(label) + ".bin"
            (folder / blob_name).write_bytes(data)
            entry["saved_blob"] = f"matches/{folder.name}/{blob_name}"
        strings = printable_strings(data)
        relevant = [s for s in strings if any(t.decode("ascii").lower() in s.lower() for t in TARGETS + SECONDARY_TARGETS)]
        if relevant:
            strings_name = safe_name(label) + ".relevant-strings.txt"
            (folder / strings_name).write_text("\n".join(relevant) + "\n", encoding="utf-8")
            entry["relevant_strings_file"] = f"matches/{folder.name}/{strings_name}"
        self.matches.append(entry)

    def inspect(self, *, record: dict, label: str, data: bytes, depth: int = 0) -> None:
        if depth > MAX_RECURSION or len(self.matches) >= MAX_MATCHES:
            return
        self.save_match(record=record, label=label, data=data, depth=depth)

        decoded = data
        decoded_meta = None
        if data.startswith(CHUNKZIP_MAGIC):
            try:
                decoded, decoded_meta = decode_chunkzip(data)
            except Exception as exc:
                self.errors.append({"record": record["index"], "label": label, "stage": "chunkzip", "error": str(exc)})
                return
            self.save_match(
                record=record,
                label=label + ".decoded",
                data=decoded,
                depth=depth,
                metadata={"chunkzip": decoded_meta},
            )

        if decoded[:4] not in BIG_MAGICS:
            return
        try:
            entries, big_meta = parse_big(decoded)
        except Exception as exc:
            self.errors.append({"record": record["index"], "label": label, "stage": "big", "error": str(exc)})
            return
        for item in entries:
            blob = decoded[item["offset"]: item["offset"] + item["size"]]
            child_label = f"{label}/entry_{item['index']:04d}_{item['name']}"
            # Save direct hits with useful BIG metadata, then recurse for nested BIG/chunkzip.
            if target_hits(blob):
                self.save_match(
                    record=record,
                    label=child_label,
                    data=blob,
                    depth=depth + 1,
                    metadata={"parent_big": big_meta, "big_entry": item},
                )
            if blob.startswith(CHUNKZIP_MAGIC) or blob[:4] in BIG_MAGICS:
                self.inspect(record=record, label=child_label, data=blob, depth=depth + 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only FIFA 14 FUT Store frontend archive scanner")
    parser.add_argument("--game-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    game_root = args.game_root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    patch_big = game_root / "patch.big"
    patch_bh = game_root / "patch.bh"

    result = {
        "schema": "fifa14-beta28-store-art-routing-static-trace",
        "mode": "read-only",
        "game_root": str(game_root),
        "archive": str(patch_big),
        "index": str(patch_bh),
        "targets": [v.decode("ascii") for v in TARGETS],
        "matches": [],
        "errors": [],
    }

    try:
        if not patch_big.is_file() or not patch_bh.is_file():
            raise FileNotFoundError(f"patch.big/patch.bh not found under {game_root}")
        records = bh_records(patch_bh.read_bytes())
        result["record_count"] = len(records)
        result["patch_big_size"] = patch_big.stat().st_size

        with tempfile.TemporaryDirectory(prefix="fifa14-store-ui-") as tmp:
            root = Path(tmp)
            collector = Collector(root)
            with patch_big.open("rb") as handle:
                for rec in records:
                    if rec["size"] <= 0:
                        continue
                    if rec["offset"] + rec["size"] > result["patch_big_size"]:
                        collector.errors.append({"record": rec["index"], "stage": "read", "error": "record exceeds patch.big"})
                        continue
                    handle.seek(rec["offset"])
                    payload = handle.read(rec["size"])
                    if len(payload) != rec["size"]:
                        collector.errors.append({"record": rec["index"], "stage": "read", "error": "short read"})
                        continue
                    # Cheap prefilter: compressed records need decoding; raw records only need deeper
                    # parsing if they already contain a target or are a BIG container.
                    if payload.startswith(CHUNKZIP_MAGIC) or payload[:4] in BIG_MAGICS or target_hits(payload):
                        collector.inspect(record=rec, label="record", data=payload)
                    if len(collector.matches) >= MAX_MATCHES:
                        break

            result["matches"] = collector.matches
            result["errors"] = collector.errors[:200]
            result["match_count"] = len(collector.matches)
            result["records_with_matches"] = sorted({m["record"]["index"] for m in collector.matches})
            (root / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            lines = [
                "FIFA 14 FUT Store frontend static trace",
                "read-only; no game archive was modified",
                f"patch records scanned: {len(records)}",
                f"matches: {len(collector.matches)}",
                "",
            ]
            for match in collector.matches:
                lines.append(
                    f"record={match['record']['index']} label={match['label']} size={match['size']} targets={','.join(match['targets'])}"
                )
                for ctx in match.get("contexts", [])[:12]:
                    lines.append(f"  {ctx['target']} @ +0x{ctx['offset']:X}: {ctx['context']}")
            if not collector.matches:
                lines.append("No strong Store frontend strings were found in patch.big. The live wrapper trace is still useful.")
            (root / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
            write_zip(root, output)

        print(json.dumps({
            "output": str(output),
            "recordCount": len(records),
            "matchCount": result["match_count"],
            "recordsWithMatches": result["records_with_matches"],
        }))
        return 0
    except Exception as exc:
        # Always create a diagnostic artifact even on failure so the main launcher
        # can continue into FUT and the results zip explains what happened.
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fifa14-store-ui-error-") as tmp:
            root = Path(tmp)
            result["fatal_error"] = str(exc)
            (root / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            (root / "summary.txt").write_text(f"Store UI static scan could not run: {exc}\n", encoding="utf-8")
            write_zip(root, output)
        print(json.dumps({"output": str(output), "error": str(exc)}))
        return 0  # Diagnostic-only: never block the working FUT launcher.


if __name__ == "__main__":
    raise SystemExit(main())
