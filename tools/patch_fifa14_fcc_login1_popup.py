#!/usr/bin/env python3
"""FIFA 14 fcc_login1 stale Loading-popup bypass.

The existing first-use NAV redirect sends fcc_login1's natural `advance` event
into the retail iceBreaker state.  On the tested FIFA 14 PC build the login
screen's BeginLogin bytecode creates a zero-button standard popup containing
"Loading" before the redirected transition.  The normal returning-user route
later destroys that popup, but the redirected first-use route bypasses the
cleanup and leaves the popup above futPackSelect.

This patch changes ONE verified APT opcode byte in the retail fcc_login1 asset:

    APT + 0xCA: 0x49 (EQUALS2) -> 0x11 (OR)

The surrounding function layout and APT byte length are not changed. The
patcher resolves the reviewed cards0 record directly, verifies the exact archive
identity/APT layout and BeginLogin instruction context, and treats loading-icon
strings as diagnostics only. If 0x11 is already installed it performs no write.
If 0x49 is present it changes only that decoded byte and backs up the exact
pre-write record. Any other opcode/context fails closed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import zlib

BH_MAGIC = b"ViV4"
CHUNKZIP_MAGIC = b"chunkzip"
BIG_MAGICS = {b"BIG4", b"BIGF"}
APT_PATCH_OFFSET = 0xCA
RETAIL_OPCODE = 0x49  # EQUALS2
PATCHED_OPCODE = 0x11  # OR
STATE_NAME = "fcc-login1-popup-bypass-state.json"
SCAN_NAME = "fcc-login1-popup-bypass-scan.json"
MARKERS = (b"fcc_login1", b"beginlogin", b"loading")
ARCHIVE_PRIORITY = (("cards0.big", "cards0.bh"), ("patch.big", "patch.bh"), ("data1.big", "data1.bh"), ("data0.big", "data0.bh"))
SHOW = b"ShowLoadingIcon"
HIDE = b"HideLoadingIcon"
KNOWN_CARDS0_RECORD_INDEX = 3891
KNOWN_CARDS0_RECORD_OFFSET = 58_286_528
KNOWN_CARDS0_PATH_HASH = "29333257A32EB487"
KNOWN_CARDS0_RECORD_COUNT = 3957
KNOWN_CARDS0_NEXT_RECORD_OFFSET = 58_288_256
KNOWN_APT_ENTRY_NAME = "0"
KNOWN_APT_ENTRY_OFFSET = 0x40
KNOWN_APT_ENTRY_SIZE = 0x5B5
EXPECTED_CONTEXT_PREFIX = bytes.fromhex("b9 01 af 07 af 08 5a b9 01 af 04 af 09 a2 0a 52 73")
EXPECTED_CONTEXT_SUFFIX = bytes.fromhex("12 12 9d 00 00 30 00 00 00")
LOADING_LITERAL = b"Loading"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def align(value: int, boundary: int = 16) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


@dataclass(frozen=True)
class BhRecord:
    index: int
    offset: int
    size: int
    reserved: int
    path_hash: int
    table_offset: int


def parse_bh(data: bytes) -> list[BhRecord]:
    if len(data) < 16 or data[:4] != BH_MAGIC:
        raise ValueError("archive BH is not a ViV4 index")
    count = struct.unpack_from(">I", data, 8)[0]
    expected = 16 + count * 20
    if expected > len(data):
        raise ValueError(f"BH is truncated: needs {expected} bytes, has {len(data)}")
    out: list[BhRecord] = []
    pos = 16
    for index in range(count):
        table_offset = pos
        offset, size, reserved, hi, lo = struct.unpack_from(">IIIII", data, pos)
        pos += 20
        out.append(BhRecord(index, offset, size, reserved, (hi << 32) | lo, table_offset))
    return out


def decode_chunkzip(payload: bytes) -> tuple[bytes, dict]:
    if len(payload) < 40 or payload[:8] != CHUNKZIP_MAGIC:
        raise ValueError("payload is not chunkzip")
    version, output_size, chunk_size, count, alignment, a, b, c = struct.unpack_from(">IIIIIIII", payload, 8)
    if version != 2 or alignment != 16 or a or b or c:
        raise ValueError(f"unsupported chunkzip header: version={version}, alignment={alignment}, flags={(a,b,c)}")
    if count <= 0 or count > 4096 or output_size <= 0 or output_size > 64 * 1024 * 1024:
        raise ValueError("unreasonable chunkzip dimensions")
    pos = 40
    output = bytearray()
    chunks: list[dict] = []
    for index in range(count):
        if pos + 8 > len(payload):
            raise ValueError(f"truncated chunk descriptor {index}")
        stored_size, compression_type = struct.unpack_from(">II", payload, pos)
        start = pos + 8
        end = start + stored_size
        if end > len(payload):
            raise ValueError(f"truncated chunk {index}")
        stored = payload[start:end]
        if compression_type == 0:
            decoded = stored
        elif compression_type == 1:
            decoded = zlib.decompress(stored, -zlib.MAX_WBITS)
        else:
            raise ValueError(f"unsupported compression type {compression_type}")
        output.extend(decoded)
        chunks.append({"index": index, "stored_size": stored_size, "decoded_size": len(decoded), "compression_type": compression_type})
        pos = align(end + 8) - 8
    if len(output) != output_size:
        raise ValueError(f"decoded size {len(output)} != header {output_size}")
    return bytes(output), {
        "version": version, "output_size": output_size, "chunk_size": chunk_size,
        "chunk_count": count, "chunks": chunks,
    }


def encode_chunkzip(decoded: bytes, original_info: dict) -> bytes:
    chunk_size = int(original_info.get("chunk_size") or 262_144)
    if chunk_size <= 0 or chunk_size > 8 * 1024 * 1024:
        chunk_size = 262_144
    parts = [decoded[i:i + chunk_size] for i in range(0, len(decoded), chunk_size)]
    result = bytearray(CHUNKZIP_MAGIC + struct.pack(">IIIIIIII", 2, len(decoded), chunk_size, len(parts), 16, 0, 0, 0))
    for idx, part in enumerate(parts):
        comp = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
        stored = comp.compress(part) + comp.flush()
        result.extend(struct.pack(">II", len(stored), 1))
        result.extend(stored)
        if idx != len(parts) - 1:
            next_pos = align(len(result) + 8) - 8
            if next_pos > len(result):
                result.extend(b"\0" * (next_pos - len(result)))
    return bytes(result)


def parse_big_entries(data: bytes) -> list[dict]:
    if len(data) < 16 or data[:4] not in BIG_MAGICS:
        raise ValueError("decoded record is not BIG4/BIGF")
    count = struct.unpack_from(">I", data, 8)[0]
    header_size = struct.unpack_from(">I", data, 12)[0]
    if count < 0 or count > 100_000 or not 16 <= header_size <= len(data):
        raise ValueError("invalid BIG header")
    pos = 16
    entries: list[dict] = []
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
            raise ValueError(f"BIG entry {name!r} outside decoded package")
        entries.append({"index": index, "name": name, "offset": offset, "size": size})
    return entries


def apt_entries(decoded: bytes) -> list[dict]:
    out = []
    for entry in parse_big_entries(decoded):
        blob = decoded[entry["offset"]:entry["offset"] + entry["size"]]
        if blob.startswith(b"Apt Data:"):
            out.append({**entry, "blob": blob})
    return out


def marker_hits(blob: bytes) -> list[str]:
    low = blob.lower()
    return [m.decode("ascii") for m in MARKERS if m in low]


def physical_capacity(records: list[BhRecord], record: BhRecord, big_size: int) -> int:
    higher = [r.offset for r in records if r.offset > record.offset]
    next_offset = min(higher) if higher else big_size
    capacity = next_offset - record.offset
    if capacity < record.size:
        raise ValueError(f"record {record.index} physical capacity {capacity} < stored size {record.size}")
    return capacity


def read_record(handle, record: BhRecord) -> bytes:
    handle.seek(record.offset)
    data = handle.read(record.size)
    if len(data) != record.size:
        raise ValueError(f"short read of record {record.index}: {len(data)} != {record.size}")
    return data


def inspect_candidate(decoded: bytes, record: BhRecord, archive: str, stored_hash: str, chunk_info: dict) -> list[dict]:
    results = []
    try:
        entries = apt_entries(decoded)
    except Exception:
        return results
    for entry in entries:
        blob = entry["blob"]
        hits = marker_hits(blob)
        if not hits:
            continue
        byte = blob[APT_PATCH_OFFSET] if len(blob) > APT_PATCH_OFFSET else None
        results.append({
            "archive": archive,
            "record_index": record.index,
            "record_offset": record.offset,
            "record_size": record.size,
            "path_hash": f"{record.path_hash:016X}",
            "stored_sha256": stored_hash,
            "decoded_sha256": sha256_bytes(decoded),
            "apt_entry_index": entry["index"],
            "apt_entry_name": entry["name"],
            "apt_entry_offset": entry["offset"],
            "apt_entry_size": entry["size"],
            "markers": hits,
            "apt_0xCA": None if byte is None else f"0x{byte:02X}",
            "chunkzip": chunk_info,
        })
    return results


def scan_archive(big_path: Path, bh_path: Path, index_range: tuple[int, int] | None = None) -> tuple[list[dict], list[dict]]:
    bh = bh_path.read_bytes()
    records = parse_bh(bh)
    big_size = big_path.stat().st_size
    candidates: list[dict] = []
    near: list[dict] = []
    with big_path.open("rb") as handle:
        for record in records:
            if index_range is not None and not (index_range[0] <= record.index <= index_range[1]):
                continue
            if record.size < 128 or record.size > 4_000_000 or record.offset < 0 or record.offset + record.size > big_size:
                continue
            handle.seek(record.offset)
            header = handle.read(40)
            if len(header) < 40 or header[:8] != CHUNKZIP_MAGIC:
                continue
            try:
                version, output_size, chunk_size, count, alignment, a, b, c = struct.unpack_from(">IIIIIIII", header, 8)
            except struct.error:
                continue
            if version != 2 or alignment != 16 or a or b or c or output_size < 128 or output_size > 8_000_000:
                continue
            try:
                stored = read_record(handle, record)
                decoded, chunk_info = decode_chunkzip(stored)
            except Exception:
                continue
            if decoded[:4] not in BIG_MAGICS:
                continue
            low = decoded.lower()
            # The full package has to at least identify the fcc_login1 screen.
            if b"fcc_login1" not in low:
                continue
            found = inspect_candidate(decoded, record, big_path.name, sha256_bytes(stored), chunk_info)
            for item in found:
                if set(item["markers"]) == {m.decode("ascii") for m in MARKERS}:
                    candidates.append(item)
                else:
                    near.append(item)
    return candidates, near


def discover(game_root: Path, scan_path: Path | None = None) -> dict:
    report = {"schema": 1, "game_root": str(game_root), "markers": [m.decode("ascii") for m in MARKERS], "archives_scanned": [], "near_candidates": [], "candidates": []}
    for big_name, bh_name in ARCHIVE_PRIORITY:
        big_path, bh_path = game_root / big_name, game_root / bh_name
        if not big_path.is_file() or not bh_path.is_file():
            continue
        # Most shipped external FUT APT packages in this exact patch archive
        # sit near the verified futPackSelect/helperFunctions records.  Try that
        # bounded UI window first, then fall back to a full archive scan only if
        # needed.  Lower-priority base archives are scanned only when patch.big
        # contains no candidate.
        if big_name == "cards0.big":
            # The reviewed FIFA 14 PC fcc_login1 package is record 3891 in cards0.
            # Probe that exact record first so we do not accidentally select a duplicate/base asset.
            candidates, near = scan_archive(big_path, bh_path, (KNOWN_CARDS0_RECORD_INDEX, KNOWN_CARDS0_RECORD_INDEX))
            report["archives_scanned"].append({"big": big_name, "bh": bh_name, "scope": "reviewed-record-3891", "candidate_count": len(candidates), "near_count": len(near)})
            report["near_candidates"].extend(near[:20])
            if not candidates:
                candidates, near = scan_archive(big_path, bh_path)
                report["archives_scanned"].append({"big": big_name, "bh": bh_name, "scope": "full-fallback", "candidate_count": len(candidates), "near_count": len(near)})
                report["near_candidates"].extend(near[:20])
        elif big_name == "patch.big":
            candidates, near = scan_archive(big_path, bh_path, (1500, 2700))
            report["archives_scanned"].append({"big": big_name, "bh": bh_name, "scope": "ui-fast-window-1500-2700", "candidate_count": len(candidates), "near_count": len(near)})
            report["near_candidates"].extend(near[:20])
            if not candidates:
                candidates, near = scan_archive(big_path, bh_path)
                report["archives_scanned"].append({"big": big_name, "bh": bh_name, "scope": "full-fallback", "candidate_count": len(candidates), "near_count": len(near)})
                report["near_candidates"].extend(near[:20])
        else:
            candidates, near = scan_archive(big_path, bh_path)
            report["archives_scanned"].append({"big": big_name, "bh": bh_name, "scope": "full", "candidate_count": len(candidates), "near_count": len(near)})
            report["near_candidates"].extend(near[:20])
        if candidates:
            report["candidates"].extend(candidates)
            # patch archive precedence: once an effective archive has a unique
            # candidate, do not patch lower-priority duplicate/base assets.
            break
    if scan_path is not None:
        scan_path.parent.mkdir(parents=True, exist_ok=True)
        scan_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def resolve_candidate(game_root: Path, state_dir: Path) -> tuple[dict, list[BhRecord], bytes, bytes, dict]:
    """Resolve the reviewed fcc_login1 package by exact cards0 archive identity.

    v2.25.1 attempted semantic marker discovery inside the APT blob.  The real
    retail package does not expose all of those text markers in the way that
    heuristic assumed, so a valid reviewed install could produce zero
    candidates.  The reviewed V24 patcher already established a stronger
    identity: cards0 record 3891, fixed offset/path hash/next-slot boundary,
    APT entry name ``0`` at 0x40 with size 0x5B5, and an exact instruction
    context around APT+0xCA.  Use those invariants directly and fail closed if
    any of them differ.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    scan_path = state_dir / SCAN_NAME
    report: dict = {
        "schema": 2,
        "resolver": "exact-reviewed-cards0-record",
        "game_root": str(game_root),
        "reviewed_record": {
            "archive": "cards0.big",
            "index": KNOWN_CARDS0_RECORD_INDEX,
            "offset": KNOWN_CARDS0_RECORD_OFFSET,
            "path_hash": KNOWN_CARDS0_PATH_HASH,
            "next_record_offset": KNOWN_CARDS0_NEXT_RECORD_OFFSET,
            "apt_entry_name": KNOWN_APT_ENTRY_NAME,
            "apt_entry_offset": KNOWN_APT_ENTRY_OFFSET,
            "apt_entry_size": KNOWN_APT_ENTRY_SIZE,
        },
        "status": "error",
    }
    try:
        big_path = game_root / "cards0.big"
        bh_path = game_root / "cards0.bh"
        if not big_path.is_file() or not bh_path.is_file():
            raise FileNotFoundError(f"cards0.big/cards0.bh were not found under {game_root}")

        bh = bh_path.read_bytes()
        records = parse_bh(bh)
        if len(records) != KNOWN_CARDS0_RECORD_COUNT:
            raise ValueError(f"cards0.bh has {len(records)} records; expected {KNOWN_CARDS0_RECORD_COUNT}")
        if KNOWN_CARDS0_RECORD_INDEX >= len(records):
            raise ValueError("reviewed fcc_login1 record index is outside cards0.bh")
        record = records[KNOWN_CARDS0_RECORD_INDEX]
        if record.offset != KNOWN_CARDS0_RECORD_OFFSET:
            raise ValueError(f"fcc_login1 record offset changed: {record.offset} != {KNOWN_CARDS0_RECORD_OFFSET}")
        if record.reserved != 0:
            raise ValueError(f"fcc_login1 reserved field changed: {record.reserved}")
        path_hash = f"{record.path_hash:016X}"
        if path_hash != KNOWN_CARDS0_PATH_HASH:
            raise ValueError(f"fcc_login1 path hash changed: {path_hash} != {KNOWN_CARDS0_PATH_HASH}")
        next_record = records[KNOWN_CARDS0_RECORD_INDEX + 1]
        if next_record.offset != KNOWN_CARDS0_NEXT_RECORD_OFFSET:
            raise ValueError(
                f"fcc_login1 next-record boundary changed: {next_record.offset} != {KNOWN_CARDS0_NEXT_RECORD_OFFSET}"
            )
        capacity = next_record.offset - record.offset
        if capacity <= 0 or record.size <= 0 or record.size > capacity:
            raise ValueError(f"invalid reviewed fcc_login1 slot: size={record.size}, capacity={capacity}")

        with big_path.open("rb") as handle:
            stored = read_record(handle, record)
        decoded, chunk_info = decode_chunkzip(stored)
        entries = parse_big_entries(decoded)
        matching = [e for e in entries if e["name"] == KNOWN_APT_ENTRY_NAME]
        if len(matching) != 1:
            raise ValueError(f"expected one fcc_login1 APT entry named {KNOWN_APT_ENTRY_NAME!r}; found {len(matching)}")
        apt = matching[0]
        if int(apt["offset"]) != KNOWN_APT_ENTRY_OFFSET or int(apt["size"]) != KNOWN_APT_ENTRY_SIZE:
            raise ValueError(f"fcc_login1 APT layout changed: offset={apt['offset']}, size={apt['size']}")
        blob = decoded[apt["offset"]:apt["offset"] + apt["size"]]
        if not blob.startswith(b"Apt Data"):
            raise ValueError("fcc_login1 APT entry has no Apt Data magic")
        if len(blob) <= APT_PATCH_OFFSET:
            raise ValueError("fcc_login1 APT is shorter than offset 0xCA")
        opcode = blob[APT_PATCH_OFFSET]
        if opcode not in (RETAIL_OPCODE, PATCHED_OPCODE):
            raise ValueError(f"fcc_login1 APT+0xCA is 0x{opcode:02X}, expected retail 0x49 or patched 0x11")
        context_start = APT_PATCH_OFFSET - len(EXPECTED_CONTEXT_PREFIX)
        context_end = APT_PATCH_OFFSET + 1 + len(EXPECTED_CONTEXT_SUFFIX)
        actual_context = blob[context_start:context_end]
        expected_context = EXPECTED_CONTEXT_PREFIX + bytes([opcode]) + EXPECTED_CONTEXT_SUFFIX
        if actual_context != expected_context:
            raise ValueError("fcc_login1 popup branch no longer matches the reviewed BeginLogin instruction sequence")
        if decoded.count(LOADING_LITERAL) < 1:
            raise ValueError("fcc_login1 package no longer contains the literal Loading popup message")

        c = {
            "archive": "cards0.big",
            "record_index": record.index,
            "record_offset": record.offset,
            "record_size": record.size,
            "path_hash": path_hash,
            "stored_sha256": sha256_bytes(stored),
            "decoded_sha256": sha256_bytes(decoded),
            "apt_entry_index": apt["index"],
            "apt_entry_name": apt["name"],
            "apt_entry_offset": apt["offset"],
            "apt_entry_size": apt["size"],
            "apt_0xCA": f"0x{opcode:02X}",
            "capacity": capacity,
            "chunkzip": chunk_info,
        }
        report.update({
            "status": "verified",
            "record": {k: c[k] for k in ("record_index", "record_offset", "record_size", "capacity", "path_hash")},
            "apt": {
                "entry_index": apt["index"],
                "entry_name": apt["name"],
                "entry_offset": apt["offset"],
                "entry_size": apt["size"],
                "opcode_0xCA": f"0x{opcode:02X}",
                "apt_sha256": sha256_bytes(blob),
                "show_loading_icon_count": blob.count(SHOW),
                "hide_loading_icon_count": blob.count(HIDE),
                "loading_literal_count_package": decoded.count(LOADING_LITERAL),
                "instruction_context_verified": True,
            },
            "stored_sha256": c["stored_sha256"],
            "decoded_sha256": c["decoded_sha256"],
        })
        scan_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return c, records, bh, decoded, chunk_info
    except Exception as exc:
        report["error"] = str(exc)
        report["type"] = type(exc).__name__
        scan_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        raise

def _apt_blob(decoded: bytes, candidate: dict) -> bytes:
    start = int(candidate["apt_entry_offset"])
    size = int(candidate["apt_entry_size"])
    return decoded[start:start + size]


def _opcode_absolute(candidate: dict) -> int:
    return int(candidate["apt_entry_offset"]) + APT_PATCH_OFFSET


def classify(decoded: bytes, candidate: dict) -> dict:
    """Classify only the invariants actually proven by the reviewed record.

    v2.25.2 incorrectly tried to reconstruct a canonical retail package using
    an APT hash and ShowLoadingIcon/HideLoadingIcon placement assumptions.  The
    user's exact scan proved the record identity and BeginLogin instruction
    context while also proving those strings are not resident in this APT.
    They are therefore telemetry only and are never patch preconditions here.
    """
    apt = _apt_blob(decoded, candidate)
    opcode = apt[APT_PATCH_OFFSET]
    if opcode not in (RETAIL_OPCODE, PATCHED_OPCODE):
        raise ValueError(f"fcc_login1 APT+0xCA is 0x{opcode:02X}, expected 0x49 or 0x11")
    return {
        "opcode": opcode,
        "opcode_hex": f"0x{opcode:02X}",
        "status": "verified-one-byte-opcode-present" if opcode == PATCHED_OPCODE else "verified-unpatched-opcode",
        "apt_sha256": sha256_bytes(apt),
        # Diagnostic only. Do not require either string to live in APT entry 0.
        "show_loading_icon_count_apt": apt.count(SHOW),
        "hide_loading_icon_count_apt": apt.count(HIDE),
        "show_loading_icon_count_package": decoded.count(SHOW),
        "hide_loading_icon_count_package": decoded.count(HIDE),
        "loading_literal_count_package": decoded.count(LOADING_LITERAL),
    }


def state_file(state_dir: Path) -> Path:
    return state_dir / STATE_NAME


def update_bh_size(bh: bytes, record: BhRecord, new_size: int) -> bytes:
    out = bytearray(bh)
    struct.pack_into(">I", out, record.table_offset + 4, new_size)
    return bytes(out)


def write_slot(big_path: Path, offset: int, capacity: int, payload: bytes, clear_to: int | None = None) -> None:
    if len(payload) > capacity:
        raise ValueError(f"patched payload {len(payload)} exceeds physical slot capacity {capacity}")
    clear_to = len(payload) if clear_to is None else max(len(payload), min(clear_to, capacity))
    with big_path.open("r+b") as handle:
        handle.seek(offset)
        handle.write(payload)
        if len(payload) < clear_to:
            handle.write(b"\0" * (clear_to - len(payload)))
        handle.flush(); os.fsync(handle.fileno())


def atomic_write(path: Path, data: bytes) -> None:
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception:
        try: os.unlink(temp)
        except OSError: pass
        raise


def _write_prepatch_backup(state_dir: Path, candidate: dict, bh: bytes, stored: bytes, decoded: bytes) -> dict:
    """Back up exactly what v2.25.4 found, with no synthetic 'retail' claim."""
    original = state_dir / "original"
    original.mkdir(parents=True, exist_ok=True)
    record_path = original / "fcc_login1.pre-v2253.record.bin"
    bh_path = original / "cards0.pre-v2253.bh.bin"
    meta_path = original / "pre-v2253-metadata.json"
    record_path.write_bytes(stored)
    bh_path.write_bytes(bh)
    meta = {
        "schema": 3,
        "archive": candidate["archive"],
        "record_index": candidate["record_index"],
        "record_offset": candidate["record_offset"],
        "record_size": len(stored),
        "capacity": candidate["capacity"],
        "path_hash": candidate["path_hash"],
        "stored_sha256": sha256_bytes(stored),
        "decoded_sha256": sha256_bytes(decoded),
        "bh_sha256": sha256_bytes(bh),
        "apt_entry_offset": candidate["apt_entry_offset"],
        "apt_entry_size": candidate["apt_entry_size"],
        "apt_patch_offset": APT_PATCH_OFFSET,
        "note": "Exact pre-v2.25.4 bytes; no retail/canonicalization assumption.",
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return {"record": str(record_path), "bh": str(bh_path), "metadata": str(meta_path)}


def inspect(game_root: Path, state_dir: Path) -> dict:
    c, _, _, decoded, _ = resolve_candidate(game_root, state_dir)
    semantic = classify(decoded, c)
    return {
        "schema": 3,
        "revision": "v2.25.4",
        "status": semantic["status"],
        "game_root": str(game_root),
        "archive": c["archive"],
        "record": {k: c[k] for k in ("record_index", "record_offset", "record_size", "capacity", "path_hash")},
        "apt": {
            "entry_index": c["apt_entry_index"],
            "entry_offset": c["apt_entry_offset"],
            "entry_size": c["apt_entry_size"],
            "patch_offset": "0xCA",
            **semantic,
            "instruction_context_verified": True,
        },
        "decoded_sha256": sha256_bytes(decoded),
        "scan_report": str(state_dir / SCAN_NAME),
        "policy": "exact record + exact instruction context; no APT hash or loading-icon string-count gate",
    }


def apply(game_root: Path, state_dir: Path) -> dict:
    state_dir.mkdir(parents=True, exist_ok=True)
    c, records, bh, decoded, chunk_info = resolve_candidate(game_root, state_dir)
    semantic = classify(decoded, c)
    archive = c["archive"]
    big_path = game_root / archive
    bh_path = big_path.with_suffix(".bh")
    record = records[int(c["record_index"])]

    # The uploaded v2.25.4 scan already showed 0x11 on the user's install. In
    # that state there is nothing to patch. Most importantly, do not rewrite or
    # 'normalize' any other fcc_login1 bytes just to satisfy an assumed hash.
    if semantic["opcode"] == PATCHED_OPCODE:
        result = inspect(game_root, state_dir)
        result.update({
            "action": "apply",
            "changed": False,
            "reason": "verified APT+0xCA is already OR(0x11); accepted in place without rewriting any package bytes",
        })
        return result

    with big_path.open("rb") as handle:
        pre_stored = read_record(handle, record)
    backups = _write_prepatch_backup(state_dir, c, bh, pre_stored, decoded)

    desired = bytearray(decoded)
    opcode_abs = _opcode_absolute(c)
    if desired[opcode_abs] != RETAIL_OPCODE:
        raise ValueError(f"refusing write: expected current opcode 0x49, got 0x{desired[opcode_abs]:02X}")
    desired[opcode_abs] = PATCHED_OPCODE
    desired = bytes(desired)
    diffs = [i for i, (a, b) in enumerate(zip(decoded, desired)) if a != b]
    if diffs != [opcode_abs]:
        raise AssertionError(f"decoded patch is not exactly one byte: {diffs[:16]}")

    desired_stored = encode_chunkzip(desired, chunk_info)
    if len(desired_stored) > int(c["capacity"]):
        raise ValueError(f"patched fcc_login1 record {len(desired_stored)} exceeds slot capacity {c['capacity']}")
    desired_bh = update_bh_size(bh, record, len(desired_stored))

    write_slot(big_path, record.offset, int(c["capacity"]), desired_stored, clear_to=max(len(pre_stored), len(desired_stored)))
    atomic_write(bh_path, desired_bh)
    try:
        verified = inspect(game_root, state_dir)
        if verified["apt"]["opcode"] != PATCHED_OPCODE or not verified["apt"]["instruction_context_verified"]:
            raise ValueError("post-write opcode/context verification failed")
    except Exception:
        write_slot(big_path, record.offset, int(c["capacity"]), pre_stored, clear_to=max(len(pre_stored), len(desired_stored)))
        atomic_write(bh_path, bh)
        raise

    state = {
        "schema": 3,
        "revision": "v2.25.4",
        "archive": archive,
        "record_index": record.index,
        "record_offset": record.offset,
        "preexisting_size": len(pre_stored),
        "patched_size": len(desired_stored),
        "capacity": int(c["capacity"]),
        "path_hash": c["path_hash"],
        "apt_entry_offset": c["apt_entry_offset"],
        "apt_patch_offset": APT_PATCH_OFFSET,
        "before": "0x49",
        "after": "0x11",
        "decoded_changed_offsets": [opcode_abs],
        "preexisting_stored_sha256": sha256_bytes(pre_stored),
        "preexisting_decoded_sha256": sha256_bytes(decoded),
        "patched_stored_sha256": sha256_bytes(desired_stored),
        "patched_decoded_sha256": sha256_bytes(desired),
        "backups": backups,
    }
    state_file(state_dir).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    verified.update({
        "action": "apply",
        "changed": True,
        "reason": "changed only the verified decoded APT+0xCA opcode 0x49 -> 0x11; no other decoded package bytes modified",
        "state_file": str(state_file(state_dir)),
        "backups": backups,
    })
    return verified


def restore(game_root: Path, state_dir: Path) -> dict:
    sf = state_file(state_dir)
    if not sf.is_file():
        current = inspect(game_root, state_dir)
        if current["apt"]["opcode"] == RETAIL_OPCODE:
            return {"action": "restore", "changed": False, "reason": "opcode already 0x49 and no v2.25.4 state exists"}
        raise FileNotFoundError("No v2.25.4 pre-patch backup exists; refusing to invent a rollback for a pre-existing 0x11 install")
    state = json.loads(sf.read_text(encoding="utf-8"))
    backups = state.get("backups") or {}
    record_path = Path(backups.get("record", ""))
    bh_backup = Path(backups.get("bh", ""))
    if not record_path.is_file() or not bh_backup.is_file():
        raise FileNotFoundError("v2.25.4 exact pre-patch backup is incomplete")
    stored = record_path.read_bytes()
    bh = bh_backup.read_bytes()
    if sha256_bytes(stored) != state.get("preexisting_stored_sha256"):
        raise ValueError("v2.25.4 pre-patch record hash mismatch")
    records = parse_bh(bh)
    record = records[int(state["record_index"])]
    big_path = game_root / state["archive"]
    bh_path = big_path.with_suffix(".bh")
    write_slot(big_path, record.offset, int(state["capacity"]), stored, clear_to=max(len(stored), int(state.get("patched_size", 0))))
    atomic_write(bh_path, bh)
    sf.unlink()
    verified = inspect(game_root, state_dir)
    return {"action": "restore", "changed": True, "restored_status": verified["status"], "apt": verified["apt"]}

def main() -> int:
    p = argparse.ArgumentParser(description="Patch FIFA 14 fcc_login1 to skip only the stale standard Loading popup")
    p.add_argument("--game-root", required=True, type=Path)
    p.add_argument("--state-dir", required=True, type=Path)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true")
    group.add_argument("--verify", action="store_true")
    group.add_argument("--restore", action="store_true")
    args = p.parse_args()
    try:
        root = args.game_root.expanduser().resolve()
        state = args.state_dir.expanduser().resolve()
        if args.apply: result = apply(root, state)
        elif args.restore: result = restore(root, state)
        else: result = inspect(root, state)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
