#!/usr/bin/env python3
"""Restore and verify FIFA 14's authenticated first-use route to Icebreaker.

This tool changes one reviewed JSON target in
``data/ui/nav/fut/futLogInFlow.nav`` so the natural ``futLogIn1 / advance``
transition enters the retail ``iceBreaker`` state. It recognizes exact retail,
Icebreaker, and Create Club route identities and refuses unknown records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import zlib

BH_MAGIC = b"ViV4"
CHUNKZIP_MAGIC = b"chunkzip"

RECORD_INDEX = 16_469  # reviewed retail index (diagnostic/default only)
RECORD_OFFSET = 299_329_536  # reviewed retail offset (diagnostic/default only)
RECORD_PATH_HASH = 0x4B5CD1DA3749E8E0
SLOT_CAPACITY = 640  # reviewed retail slot capacity
NEXT_RECORD_OFFSET = RECORD_OFFSET + SLOT_CAPACITY

IDENTITIES = {
    "retail-original": {
        "size": 601,
        "stored": "233b178542769cec15c32f73348d5c98f96df6e4cb9f72512231ce54f2f6fa7b",
        "decoded": "a39c8e57e826cd22c6c17d16f20e6cf91ad53e57dd85e1b9ec8f3c8e25a791e3",
        "target": b'"targets":["futLogIn2"]',
    },
    "advance-to-icebreaker": {
        "size": 598,
        "stored": "95e3f1d31e695e8aded5b3df4c3a8109db27f227be1a050f0c232be9935d0a97",
        "decoded": "4d71d20cb7292886a3835fafa261e83bccaed11b11888cfe3b2a2e03ff8a26f1",
        "target": b'"targets":["iceBreaker"]',
    },
    "advance-to-create-club": {
        "size": 596,
        "stored": "9c2c1bcb440ce153c65c44a7ca982e2cb43e26028a51865bd2cc15b4541c959d",
        "decoded": "24e7847e0278556e696c386bc1f5812be95c40c17a1fd8fd68b14201bf987885",
        "target": b'"targets":["createClub"]',
    },
}

STATE_MARKER = b'"name":"futLogIn1"'
NEXT_STATE_MARKER = b'"name":"futLogIn2"'
TRANSITIONS_MARKER = b'"transitions"'
ADVANCE_MARKER = b'"event":"advance"'
STATE_FILE = "fut-icebreaker-route-v15-state.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def align(value: int, boundary: int = 16) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def parse_bh_records(bh: bytes) -> list[dict]:
    """Parse every ViV4 BH row without assuming the retail record number.

    Alternate FIFA 14 title-update/data layouts can move a resource to a
    different table index and physical offset while retaining the same path
    hash.  We therefore resolve futLogInFlow by its stable path hash and then
    verify the decoded payload before any write.
    """
    if len(bh) < 16 or bh[:4] != BH_MAGIC:
        raise ValueError("data1.bh is not a ViV4 index")
    count = struct.unpack_from(">I", bh, 8)[0]
    table_end = 16 + count * 20
    if table_end > len(bh):
        raise ValueError(f"data1.bh record table is truncated: {count} records need {table_end} bytes")
    rows = []
    for index in range(count):
        table_offset = 16 + index * 20
        offset, size, reserved, hash_hi, hash_lo = struct.unpack_from(">IIIII", bh, table_offset)
        rows.append({
            "record_index": index,
            "table_offset": table_offset,
            "offset": offset,
            "size": size,
            "reserved": reserved,
            "path_hash": (hash_hi << 32) | hash_lo,
        })
    return rows


def _physical_slot_capacity(rows: list[dict], record: dict, big_size: int) -> int:
    offset = record["offset"]
    later = [row["offset"] for row in rows if row["offset"] > offset]
    next_offset = min(later) if later else big_size
    capacity = next_offset - offset
    if capacity < record["size"]:
        raise ValueError(
            f"futLogInFlow physical slot is invalid: size={record['size']} capacity={capacity}"
        )
    return capacity


def resolve_bh_record(bh: bytes, big_path: Path) -> dict:
    rows = parse_bh_records(bh)
    candidates = [row.copy() for row in rows if row["path_hash"] == RECORD_PATH_HASH]
    if not candidates:
        raise ValueError(
            f"data1.bh has {len(rows)} records but no futLogInFlow path-hash record "
            f"({RECORD_PATH_HASH:016X})"
        )

    big_size = big_path.stat().st_size
    usable = []
    rejected = []
    with big_path.open("rb") as handle:
        for candidate in candidates:
            try:
                if candidate["size"] <= 0:
                    raise ValueError(f"invalid record size {candidate['size']}")
                candidate["slot_capacity"] = _physical_slot_capacity(rows, candidate, big_size)
                handle.seek(candidate["offset"])
                payload = handle.read(candidate["size"])
                if len(payload) != candidate["size"]:
                    raise ValueError("short read")
                decoded = decode_chunkzip(payload)
                # A stable path hash alone is not enough to authorize a write.
                # Require the expected FUT login state/advance structure too.
                find_advance_target(decoded)
                candidate["payload"] = payload
                candidate["decoded"] = decoded
                usable.append(candidate)
            except Exception as exc:
                rejected.append(f"index {candidate['record_index']} offset {candidate['offset']}: {exc}")

    if len(usable) != 1:
        detail = "; ".join(rejected[:4])
        if len(usable) == 0:
            raise ValueError(
                "futLogInFlow path-hash record was found but no uniquely compatible NAV payload could be verified"
                + (f": {detail}" if detail else "")
            )
        raise ValueError(
            f"multiple ({len(usable)}) compatible futLogInFlow records were found; refusing an ambiguous write"
        )
    return usable[0]


def decode_chunkzip(payload: bytes) -> bytes:
    if len(payload) < 48 or payload[:8] != CHUNKZIP_MAGIC:
        raise ValueError("futLogInFlow record is not chunkzip")
    version, output_size, chunk_size, count, alignment, flag_a, flag_b, flag_c = struct.unpack_from(
        ">IIIIIIII", payload, 8
    )
    if version != 2 or alignment != 16 or flag_a or flag_b or flag_c:
        raise ValueError("unsupported chunkzip header")
    pos = 40
    output = bytearray()
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
        pos = align(end + 8) - 8
    if len(output) != output_size:
        raise ValueError(f"decoded size {len(output)} != chunkzip header {output_size}")
    return bytes(output)


def encode_single_chunkzip(decoded: bytes) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    compressed = compressor.compress(decoded) + compressor.flush()
    return CHUNKZIP_MAGIC + struct.pack(
        ">IIIIIIIIII", 2, len(decoded), 262_144, 1, 16, 0, 0, 0, len(compressed), 1
    ) + compressed


def classify(payload: bytes, decoded: bytes, bh_size: int) -> str:
    stored_hash = sha256_bytes(payload)
    decoded_hash = sha256_bytes(decoded)
    for name, identity in IDENTITIES.items():
        if (
            bh_size == identity["size"]
            and stored_hash == identity["stored"]
            and decoded_hash == identity["decoded"]
        ):
            return name
    return "unknown"


def find_advance_target(decoded: bytes) -> tuple[int, int, bytes]:
    state_start = decoded.find(STATE_MARKER)
    if state_start < 0 or decoded.find(STATE_MARKER, state_start + 1) >= 0:
        raise ValueError("could not uniquely identify futLogIn1")
    transitions_start = decoded.find(TRANSITIONS_MARKER, state_start)
    next_state = decoded.find(NEXT_STATE_MARKER, transitions_start)
    if transitions_start < 0 or next_state < 0:
        raise ValueError("could not bound futLogIn1 transitions")
    advance_at = decoded.find(ADVANCE_MARKER, transitions_start, next_state)
    if advance_at < 0 or decoded.find(ADVANCE_MARKER, advance_at + 1, next_state) >= 0:
        raise ValueError("could not uniquely identify futLogIn1/advance")
    next_event = decoded.find(b'"event":', advance_at + len(ADVANCE_MARKER), next_state)
    transition_end = next_event if next_event >= 0 else next_state
    candidates = []
    for identity in IDENTITIES.values():
        marker = identity["target"]
        pos = decoded.find(marker, advance_at, transition_end)
        if pos >= 0:
            candidates.append((pos, marker))
    if len(candidates) != 1:
        raise ValueError("futLogIn1/advance target is not a recognized reviewed value")
    pos, marker = candidates[0]
    return pos, next_state, marker


def transform(decoded: bytes, target_status: str) -> bytes:
    if target_status not in IDENTITIES:
        raise ValueError(f"unknown target status {target_status}")
    pos, _next_state, current_marker = find_advance_target(decoded)
    target_marker = IDENTITIES[target_status]["target"]
    result = decoded[:pos] + target_marker + decoded[pos + len(current_marker):]
    expected = IDENTITIES[target_status]
    if sha256_bytes(result) != expected["decoded"]:
        raise AssertionError("transformed decoded NAV does not match the reviewed target identity")
    check_pos, _, check_marker = find_advance_target(result)
    if check_pos != pos or check_marker != target_marker:
        raise AssertionError("post-transform transition verification failed")
    return result


def read_install(game_root: Path) -> tuple[dict, bytes, bytes, bytes]:
    big_path = game_root / "data1.big"
    bh_path = game_root / "data1.bh"
    if not big_path.is_file() or not bh_path.is_file():
        raise FileNotFoundError(f"data1.big/data1.bh not found under {game_root}")
    bh = bh_path.read_bytes()
    record = resolve_bh_record(bh, big_path)
    payload = record.pop("payload")
    decoded = record.pop("decoded")
    status = classify(payload, decoded, record["size"])
    info = {
        "status": status,
        "record_index": record["record_index"],
        "record_offset": record["offset"],
        "record_size": record["size"],
        "slot_capacity": record["slot_capacity"],
        "stored_sha256": sha256_bytes(payload),
        "decoded_sha256": sha256_bytes(decoded),
        "path_hash": f"{record['path_hash']:016X}",
        "layout": "reviewed-retail" if (
            record["record_index"] == RECORD_INDEX and record["offset"] == RECORD_OFFSET
        ) else "dynamically-resolved",
    }
    return info, payload, decoded, bh


def update_bh_size(bh: bytes, table_offset: int, size: int) -> bytes:
    result = bytearray(bh)
    if table_offset < 16 or table_offset + 8 > len(result):
        raise ValueError("resolved futLogInFlow BH table offset is out of range")
    struct.pack_into(">I", result, table_offset + 4, size)
    return bytes(result)


def write_install(game_root: Path, payload: bytes, bh: bytes, current_info: dict, expected_status: str) -> dict:
    expected = IDENTITIES[expected_status]
    if len(payload) != expected["size"] or sha256_bytes(payload) != expected["stored"]:
        raise AssertionError("refusing to write a payload that does not match the reviewed target identity")
    slot_capacity = int(current_info["slot_capacity"])
    record_offset = int(current_info["record_offset"])
    record_index = int(current_info["record_index"])
    table_offset = 16 + record_index * 20
    if len(payload) > slot_capacity:
        raise AssertionError(
            f"target payload ({len(payload)} bytes) exceeds resolved slot capacity ({slot_capacity} bytes)"
        )
    big_path = game_root / "data1.big"
    bh_path = game_root / "data1.bh"
    with big_path.open("r+b") as handle:
        handle.seek(record_offset)
        handle.write(payload)
        handle.write(b"\0" * (slot_capacity - len(payload)))
        handle.flush()
        os.fsync(handle.fileno())
    atomic_write(bh_path, update_bh_size(bh, table_offset, len(payload)))
    installed, _, _, _ = read_install(game_root)
    if installed["status"] != expected_status:
        raise AssertionError(f"post-write verification failed: {installed['status']}")
    if installed["record_index"] != record_index or installed["record_offset"] != record_offset:
        raise AssertionError("post-write resolver selected a different NAV record")
    return installed


def save_pre_v10_backup(state_dir: Path, payload: bytes, bh: bytes, info: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    record_path = state_dir / "pre-v10-futloginflow.record.bin"
    bh_path = state_dir / "pre-v10-data1.bh.bin"
    metadata_path = state_dir / "pre-v10-metadata.json"
    present = [p.exists() for p in (record_path, bh_path, metadata_path)]
    if any(present) and not all(present):
        raise ValueError(f"incomplete pre-v10 backup under {state_dir}")
    if all(present):
        return
    atomic_write(record_path, payload)
    atomic_write(bh_path, bh)
    atomic_write(metadata_path, (json.dumps(info, indent=2) + "\n").encode("utf-8"))


def set_status(game_root: Path, state_dir: Path, target_status: str) -> dict:
    current, payload, decoded, bh = read_install(game_root)
    if current["status"] == "unknown":
        raise ValueError(
            "installed futLogInFlow.nav is not one of the three reviewed identities; refusing to modify it"
        )
    if current["status"] == target_status:
        return {"action": "set-status", "changed": False, "reason": "already installed", "install": current}
    save_pre_v10_backup(state_dir, payload, bh, current)
    target_decoded = transform(decoded, target_status)
    target_payload = encode_single_chunkzip(target_decoded)
    expected = IDENTITIES[target_status]
    if len(target_payload) != expected["size"] or sha256_bytes(target_payload) != expected["stored"]:
        raise AssertionError("encoded target record identity mismatch")
    installed = write_install(game_root, target_payload, bh, current, target_status)
    result = {
        "action": "set-status",
        "changed": True,
        "previous_status": current["status"],
        "target_status": target_status,
        "route_change": "futLogIn1 / advance -> iceBreaker" if target_status == "advance-to-icebreaker" else target_status,
        "install": installed,
    }
    atomic_write(state_dir / STATE_FILE, (json.dumps(result, indent=2) + "\n").encode("utf-8"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore FIFA 14's verified FUT first-use NAV route to Icebreaker")
    parser.add_argument("--game-root", required=True, type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--allow-unknown", action="store_true", help="skip incompatible/unknown NAV layouts without failing startup")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true", help="route futLogIn1 advance to iceBreaker")
    group.add_argument("--restore-retail", action="store_true")
    group.add_argument("--restore-icebreaker", action="store_true")
    group.add_argument("--verify", action="store_true")
    group.add_argument("--inspect", action="store_true")
    args = parser.parse_args()

    game_root = args.game_root.expanduser().resolve()
    state_dir = args.state_dir.expanduser().resolve() if args.state_dir else (
        Path(__file__).resolve().parents[1] / "artifacts" / "fut-icebreaker-route-v15"
    )
    try:
        if args.apply:
            result = set_status(game_root, state_dir, "advance-to-icebreaker")
        elif args.restore_retail:
            result = set_status(game_root, state_dir, "retail-original")
        elif args.restore_icebreaker:
            result = set_status(game_root, state_dir, "advance-to-icebreaker")
        else:
            install, _, _, _ = read_install(game_root)
            if args.verify and install["status"] != "advance-to-icebreaker":
                raise ValueError("Icebreaker NAV route is not installed; status=" + install["status"])
            result = {"action": "verify" if args.verify else "inspect", "install": install}
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        if args.allow_unknown:
            print(json.dumps({
                "action": "compatibility-skip",
                "changed": False,
                "warning": str(error),
                "type": type(error).__name__,
                "safety": "data1.big/data1.bh left untouched",
            }, indent=2))
            return 0
        print(json.dumps({"error": str(error), "type": type(error).__name__}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
