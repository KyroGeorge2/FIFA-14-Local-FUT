#!/usr/bin/env python3
"""Apply/verify/restore the FIFA 14 futPackSelect deterministic dock-completion v18.

Runtime evidence from v17 established that the Icebreaker pack-list HTTP request
and all four entry parsers complete, but the shared ObjectPicker never promotes
its per-object callbacks into futPackSelect::_onDockReady. CaptainObj::SetData
itself calls mOnLoadedCB immediately, so this is not an image-download or header
wait.

V18 keeps the v17 no-cinematic relay and makes one narrowly scoped APT repair:

* ObjectPicker's late onLoadingComplete delegate is redirected to the harmless,
  idempotent _AnimateInComplete handler so it cannot run _onDockReady twice.
* Immediately after retail mcObjectPicker.createByObj returns, _LoadDock calls
  futPackSelect::_onDockReady exactly once.
* The replacement is exactly eight bytes, stack-balanced, contains no END
  opcode, preserves the function size, and parses as:
      pushbyte 0; pushregister this; call _onDockReady; pushzero; pop

Together with v17's four operand substitutions, this releases the network gate,
runs the shipped started -> stopped -> _AnimatePacksIn chain, and leaves Martin
Tyler audio, Skip Audio, captain input, RetrievePack and BuildSquad untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
V17_PATH = HERE / "patch_fifa14_fut_packselect_safe_relay_v17.py"
spec = importlib.util.spec_from_file_location("fifa14_packselect_v17", V17_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not load v17 recovery helper: {V17_PATH}")
v17 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v17)
legacy = v17.legacy

RECORD_INDEX = v17.RECORD_INDEX
RECORD_OFFSET = v17.RECORD_OFFSET
RECORD_PATH_HASH = v17.RECORD_PATH_HASH
SLOT_CAPACITY = v17.SLOT_CAPACITY
RETAIL_RECORD_SIZE = v17.RETAIL_RECORD_SIZE
PATCHED_RECORD_SIZE = RETAIL_RECORD_SIZE
RETAIL_STORED_SHA256 = v17.RETAIL_STORED_SHA256
RETAIL_DECODED_SHA256 = v17.RETAIL_DECODED_SHA256
RETAIL_APT_SHA256 = v17.RETAIL_APT_SHA256
RETAIL_CONST_SHA256 = v17.RETAIL_CONST_SHA256

V18_STORED_SHA256 = "e714d25956a5f2b0b2cd09d50118d25b91bf1507c4a492f8f348c3ca5646434b"
V18_DECODED_SHA256 = "5ac6bf1faa57cfee49cb8e4d6a5698403ced0f308a0f2d81c254ffa5f637a651"
V18_APT_SHA256 = "d5537a7c4a6b3546d6003687b53afee7a4dd75425320631ab765c825f522d507"
V18_CONST_SHA256 = RETAIL_CONST_SHA256

# Existing v17 four-byte operand repair.
PLAY_CINEMATIC_MANAGER_OPERAND_OFFSET = v17.PLAY_CINEMATIC_MANAGER_OPERAND_OFFSET
PLAY_CINEMATIC_MANAGER_ORIGINAL = v17.PLAY_CINEMATIC_MANAGER_ORIGINAL
PLAY_CINEMATIC_MANAGER_PATCHED = v17.PLAY_CINEMATIC_MANAGER_PATCHED
PLAY_CINEMATIC_METHOD_OPERAND_OFFSET = v17.PLAY_CINEMATIC_METHOD_OPERAND_OFFSET
PLAY_CINEMATIC_METHOD_ORIGINAL = v17.PLAY_CINEMATIC_METHOD_ORIGINAL
PLAY_CINEMATIC_METHOD_PATCHED = v17.PLAY_CINEMATIC_METHOD_PATCHED
DOCK_READY_START_CALLBACK_OPERAND_OFFSET = v17.DOCK_READY_START_CALLBACK_OPERAND_OFFSET
DOCK_READY_START_CALLBACK_ORIGINAL = v17.DOCK_READY_START_CALLBACK_ORIGINAL
DOCK_READY_START_CALLBACK_PATCHED = v17.DOCK_READY_START_CALLBACK_PATCHED
STARTED_CB_FINAL_METHOD_OPERAND_OFFSET = v17.STARTED_CB_FINAL_METHOD_OPERAND_OFFSET
STARTED_CB_FINAL_METHOD_ORIGINAL = v17.STARTED_CB_FINAL_METHOD_ORIGINAL
STARTED_CB_FINAL_METHOD_PATCHED = v17.STARTED_CB_FINAL_METHOD_PATCHED

# New deterministic completion repair.
ON_LOADING_COMPLETE_DELEGATE_OPERAND_OFFSET = 0x1B98
ON_LOADING_COMPLETE_DELEGATE_ORIGINAL = 0x87  # _onDockReady
ON_LOADING_COMPLETE_DELEGATE_PATCHED = 0x7B   # _AnimateInComplete
LOAD_DOCK_TAIL_OFFSET = 0x1BC6
LOAD_DOCK_TAIL_ORIGINAL = bytes.fromhex("74 5A B9 01 AF 5E B2 6A")
LOAD_DOCK_TAIL_PATCHED = bytes.fromhex("B5 00 B9 01 B2 87 59 17")

LOAD_DOCK_CALLBACK_CONTEXT = bytes.fromhex(
    "B9 02 A2 86 B9 01 AF 87 B9 01 B5 02 AE 0A AF 0B AF 0C A2 0D 52 4F"
)
LOAD_DOCK_TAIL_CONTEXT = bytes.fromhex(
    "B9 02 5A B9 01 AF 5E B2 89 74 5A B9 01 AF 5E B2 6A"
)

STATUS = "deterministic-objectpicker-dock-completion-v18"
STATE_FILE = "fut-packselect-force-dock-ready-v18-state.json"
RESTORE_STATE_FILE = "fut-packselect-force-dock-ready-v18-restore-state.json"
BACKUP_RECORD = "original/patch-futpackselect.record.bin"
BACKUP_BH = "original/patch.bh.bin"
BACKUP_METADATA = "original/metadata.json"

RECOGNIZED_PRIOR = set(v17.LEGACY_STATUSES) | {v17.STATUS}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def classify(decoded: bytes, stored: bytes) -> tuple[str, dict]:
    apt_entry, apt = legacy.locate_apt(decoded)
    const_entry, const = legacy.locate_const(decoded)
    identity = {
        "stored_sha256": sha256_bytes(stored),
        "decoded_sha256": sha256_bytes(decoded),
        "apt_sha256": sha256_bytes(apt),
        "const_sha256": sha256_bytes(const),
        "apt_entry": apt_entry,
        "const_entry": const_entry,
    }
    if (
        identity["stored_sha256"] == V18_STORED_SHA256
        and identity["decoded_sha256"] == V18_DECODED_SHA256
        and identity["apt_sha256"] == V18_APT_SHA256
        and identity["const_sha256"] == V18_CONST_SHA256
    ):
        return STATUS, identity
    return v17.classify(decoded, stored)


def inspect_install(game_root: Path) -> dict:
    big_path = game_root / "patch.big"
    bh_path = game_root / "patch.bh"
    if not big_path.is_file() or not bh_path.is_file():
        raise FileNotFoundError("patch.big and patch.bh are required in the FIFA game root")
    bh = bh_path.read_bytes()
    record = legacy.parse_bh_record(bh)
    stored = legacy.read_payload(big_path, record["size"])
    decoded, chunkzip = legacy.decode_chunkzip(stored)
    status, identity = classify(decoded, stored)
    return {
        "game_root": str(game_root),
        "status": status,
        "record": {
            "index": record["index"],
            "offset": record["offset"],
            "size": record["size"],
            "slot_capacity": record["slot_capacity"],
            "free_bytes": record["slot_capacity"] - record["size"],
            "path_hash": f"{record['path_hash']:016X}",
        },
        **identity,
        "chunkzip": chunkzip,
        "big_path": str(big_path),
        "bh_path": str(bh_path),
    }


def reverse_v18(decoded: bytes) -> bytes:
    apt_entry, apt = legacy.locate_apt(decoded)
    _, const = legacy.locate_const(decoded)
    if sha256_bytes(apt) != V18_APT_SHA256 or sha256_bytes(const) != V18_CONST_SHA256:
        raise ValueError("v18 APT/constant identity does not match")
    recovered = bytearray(apt)
    if recovered[ON_LOADING_COMPLETE_DELEGATE_OPERAND_OFFSET] != ON_LOADING_COMPLETE_DELEGATE_PATCHED:
        raise ValueError("v18 onLoadingComplete delegate operand does not match")
    if recovered[LOAD_DOCK_TAIL_OFFSET:LOAD_DOCK_TAIL_OFFSET + len(LOAD_DOCK_TAIL_PATCHED)] != LOAD_DOCK_TAIL_PATCHED:
        raise ValueError("v18 _LoadDock tail does not match")
    for offset, patched in (
        (PLAY_CINEMATIC_MANAGER_OPERAND_OFFSET, PLAY_CINEMATIC_MANAGER_PATCHED),
        (PLAY_CINEMATIC_METHOD_OPERAND_OFFSET, PLAY_CINEMATIC_METHOD_PATCHED),
        (DOCK_READY_START_CALLBACK_OPERAND_OFFSET, DOCK_READY_START_CALLBACK_PATCHED),
        (STARTED_CB_FINAL_METHOD_OPERAND_OFFSET, STARTED_CB_FINAL_METHOD_PATCHED),
    ):
        if recovered[offset] != patched:
            raise ValueError(f"v18 byte at 0x{offset:X} does not match")

    recovered[ON_LOADING_COMPLETE_DELEGATE_OPERAND_OFFSET] = ON_LOADING_COMPLETE_DELEGATE_ORIGINAL
    recovered[LOAD_DOCK_TAIL_OFFSET:LOAD_DOCK_TAIL_OFFSET + len(LOAD_DOCK_TAIL_ORIGINAL)] = LOAD_DOCK_TAIL_ORIGINAL
    recovered[PLAY_CINEMATIC_MANAGER_OPERAND_OFFSET] = PLAY_CINEMATIC_MANAGER_ORIGINAL
    recovered[PLAY_CINEMATIC_METHOD_OPERAND_OFFSET] = PLAY_CINEMATIC_METHOD_ORIGINAL
    recovered[DOCK_READY_START_CALLBACK_OPERAND_OFFSET] = DOCK_READY_START_CALLBACK_ORIGINAL
    recovered[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] = STARTED_CB_FINAL_METHOD_ORIGINAL
    recovered = bytes(recovered)
    if sha256_bytes(recovered) != RETAIL_APT_SHA256:
        raise AssertionError("v18 reversal did not recover the retail APT")
    output = bytearray(decoded)
    output[apt_entry["offset"]:apt_entry["offset"] + apt_entry["size"]] = recovered
    output = bytes(output)
    if sha256_bytes(output) != RETAIL_DECODED_SHA256:
        raise AssertionError("v18 reversal did not recover the retail decoded package")
    return output


def recover_retail_decoded(decoded: bytes, status: str) -> bytes:
    if status == STATUS:
        return reverse_v18(decoded)
    return v17.recover_retail_decoded(decoded, status)


def patch_retail_decoded(decoded: bytes) -> bytes:
    if sha256_bytes(decoded) != RETAIL_DECODED_SHA256:
        raise ValueError("refusing to patch an unverified retail futPackSelect package")
    apt_entry, apt = legacy.locate_apt(decoded)
    _, const = legacy.locate_const(decoded)
    if sha256_bytes(apt) != RETAIL_APT_SHA256 or sha256_bytes(const) != RETAIL_CONST_SHA256:
        raise ValueError("retail APT/constant identities do not match")
    if apt[0x1B91:0x1B91 + len(LOAD_DOCK_CALLBACK_CONTEXT)] != LOAD_DOCK_CALLBACK_CONTEXT:
        raise ValueError("retail _LoadDock onLoadingComplete delegate context does not match")
    if apt[0x1BBD:0x1BBD + len(LOAD_DOCK_TAIL_CONTEXT)] != LOAD_DOCK_TAIL_CONTEXT:
        raise ValueError("retail _LoadDock create/tail context does not match")
    if apt[LOAD_DOCK_TAIL_OFFSET:LOAD_DOCK_TAIL_OFFSET + 8] != LOAD_DOCK_TAIL_ORIGINAL:
        raise ValueError("retail _LoadDock tail bytes do not match")

    patched = bytearray(apt)
    patched[PLAY_CINEMATIC_MANAGER_OPERAND_OFFSET] = PLAY_CINEMATIC_MANAGER_PATCHED
    patched[PLAY_CINEMATIC_METHOD_OPERAND_OFFSET] = PLAY_CINEMATIC_METHOD_PATCHED
    patched[DOCK_READY_START_CALLBACK_OPERAND_OFFSET] = DOCK_READY_START_CALLBACK_PATCHED
    patched[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] = STARTED_CB_FINAL_METHOD_PATCHED
    patched[ON_LOADING_COMPLETE_DELEGATE_OPERAND_OFFSET] = ON_LOADING_COMPLETE_DELEGATE_PATCHED
    patched[LOAD_DOCK_TAIL_OFFSET:LOAD_DOCK_TAIL_OFFSET + len(LOAD_DOCK_TAIL_PATCHED)] = LOAD_DOCK_TAIL_PATCHED
    patched = bytes(patched)
    if sha256_bytes(patched) != V18_APT_SHA256:
        raise AssertionError("patched APT does not match the reviewed v18 identity")

    output = bytearray(decoded)
    output[apt_entry["offset"]:apt_entry["offset"] + apt_entry["size"]] = patched
    output = bytes(output)
    if sha256_bytes(output) != V18_DECODED_SHA256:
        raise AssertionError("patched BIG does not match the reviewed v18 identity")
    return output


def current_pair(game_root: Path, current: dict) -> tuple[bytes, bytes, dict]:
    bh = (game_root / "patch.bh").read_bytes()
    stored = legacy.read_payload(game_root / "patch.big", current["record"]["size"])
    decoded, layout = legacy.decode_chunkzip(stored)
    return stored, bh, {"decoded": decoded, "layout": layout}


def synthesize_retail_pair(game_root: Path, current: dict) -> tuple[bytes, bytes, str]:
    stored, bh, parts = current_pair(game_root, current)
    if current["status"] == "retail-original":
        return stored, bh, "live verified retail archive"
    if current["status"] not in RECOGNIZED_PRIOR | {STATUS}:
        raise ValueError("unknown futPackSelect identity; refusing to synthesize retail")
    retail_decoded = recover_retail_decoded(parts["decoded"], current["status"])
    retail_stored = legacy.encode_chunkzip_like(retail_decoded, parts["layout"])
    if len(retail_stored) != RETAIL_RECORD_SIZE or sha256_bytes(retail_stored) != RETAIL_STORED_SHA256:
        raise AssertionError("deterministic reversal did not recover the retail stored record")
    retail_bh = legacy.bh_with_size(bh, RETAIL_RECORD_SIZE)
    return retail_stored, retail_bh, f"deterministic reversal of {current['status']}"


def backup_paths(state_dir: Path) -> tuple[Path, Path, Path]:
    return state_dir / BACKUP_RECORD, state_dir / BACKUP_BH, state_dir / BACKUP_METADATA


def load_verified_backup(state_dir: Path) -> tuple[bytes, bytes] | None:
    record_path, bh_path, _ = backup_paths(state_dir)
    if not record_path.is_file() or not bh_path.is_file():
        return None
    stored = record_path.read_bytes()
    bh = bh_path.read_bytes()
    if len(stored) != RETAIL_RECORD_SIZE or sha256_bytes(stored) != RETAIL_STORED_SHA256:
        raise ValueError("v18 retail rollback record failed verification")
    decoded, _ = legacy.decode_chunkzip(stored)
    if sha256_bytes(decoded) != RETAIL_DECODED_SHA256:
        raise ValueError("v18 retail rollback decoded package failed verification")
    record = legacy.parse_bh_record(bh)
    if record["size"] != RETAIL_RECORD_SIZE:
        raise ValueError("v18 retail rollback BH size is incorrect")
    return stored, bh


def ensure_backup(game_root: Path, state_dir: Path, current: dict) -> dict:
    record_path, bh_path, metadata_path = backup_paths(state_dir)
    existing = load_verified_backup(state_dir)
    if existing is None:
        retail_stored, retail_bh, source = synthesize_retail_pair(game_root, current)
        atomic_write(record_path, retail_stored)
        atomic_write(bh_path, retail_bh)
        metadata = {
            "source": source,
            "record_backup": str(record_path),
            "bh_backup": str(bh_path),
            "record_index": RECORD_INDEX,
            "record_offset": RECORD_OFFSET,
            "record_size": RETAIL_RECORD_SIZE,
            "slot_capacity": SLOT_CAPACITY,
            "path_hash": f"{RECORD_PATH_HASH:016X}",
            "retail_stored_sha256": RETAIL_STORED_SHA256,
            "retail_decoded_sha256": RETAIL_DECODED_SHA256,
            "retail_apt_sha256": RETAIL_APT_SHA256,
        }
        atomic_write(metadata_path, (json.dumps(metadata, indent=2) + "\n").encode("utf-8"))
        return metadata
    if metadata_path.is_file():
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    return {"source": "verified existing v18 rollback backup"}


def install_pair(game_root: Path, stored: bytes, bh: bytes, expected_status: str) -> dict:
    legacy.write_slot(game_root / "patch.big", stored)
    atomic_write(game_root / "patch.bh", bh)
    installed = inspect_install(game_root)
    if installed["status"] != expected_status:
        raise AssertionError(f"post-write verification expected {expected_status}, got {installed['status']}")
    return installed


def apply_patch(game_root: Path, state_dir: Path) -> dict:
    current = inspect_install(game_root)
    if current["status"] == STATUS:
        return {"action": "apply", "changed": False, "reason": "v18 already installed", "install": current}
    if current["status"] != "retail-original" and current["status"] not in RECOGNIZED_PRIOR:
        raise ValueError(
            "unknown futPackSelect identity; refusing to overwrite it. "
            f"stored={current['stored_sha256']} apt={current['apt_sha256']}"
        )
    backup = ensure_backup(game_root, state_dir, current)
    retail_stored, retail_bh, repair_method = synthesize_retail_pair(game_root, current)
    retail_decoded, layout = legacy.decode_chunkzip(retail_stored)
    patched_decoded = patch_retail_decoded(retail_decoded)
    patched_stored = legacy.encode_chunkzip_like(patched_decoded, layout)
    if len(patched_stored) != PATCHED_RECORD_SIZE or sha256_bytes(patched_stored) != V18_STORED_SHA256:
        raise AssertionError("v18 stored record identity mismatch")
    patched_bh = legacy.bh_with_size(retail_bh, PATCHED_RECORD_SIZE)
    installed = install_pair(game_root, patched_stored, patched_bh, STATUS)
    result = {
        "action": "apply",
        "changed": True,
        "repaired_from": current["status"],
        "repair_method": repair_method,
        "controlled_changes": [
            "v17 four one-byte no-cinematic completion operands retained",
            "ObjectPicker onLoadingComplete delegate redirected from _onDockReady to idempotent _AnimateInComplete",
            "stack-balanced eight-byte _LoadDock tail invokes _onDockReady exactly once after createByObj returns",
            "no END opcode, function-size change, branch rewrite, native callback, input-memory or inventory write",
            "Martin Tyler PlayAudio, Skip Audio/InterruptAudio, captain input, RetrievePack and BuildSquad remain retail",
        ],
        "backup": backup,
        "install": installed,
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(state_dir / STATE_FILE, (json.dumps(result, indent=2) + "\n").encode("utf-8"))
    return result


def restore_patch(game_root: Path, state_dir: Path) -> dict:
    current = inspect_install(game_root)
    if current["status"] == "retail-original":
        result = {"action": "restore", "changed": False, "method": "already retail original", "install": current}
    else:
        if current["status"] != STATUS and current["status"] not in RECOGNIZED_PRIOR:
            raise ValueError("unknown futPackSelect identity; refusing to restore over it")
        backup = load_verified_backup(state_dir)
        if backup is not None:
            retail_stored, retail_bh = backup
            method = "verified v18 rollback backup"
        else:
            retail_stored, retail_bh, method = synthesize_retail_pair(game_root, current)
        installed = install_pair(game_root, retail_stored, retail_bh, "retail-original")
        result = {"action": "restore", "changed": True, "method": method, "install": installed}
    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(state_dir / RESTORE_STATE_FILE, (json.dumps(result, indent=2) + "\n").encode("utf-8"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply/restore/verify FIFA 14 futPackSelect deterministic dock completion v18")
    parser.add_argument("--game-root", required=True, type=Path)
    parser.add_argument("--state-dir", type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--apply", action="store_true")
    action.add_argument("--restore", action="store_true")
    action.add_argument("--verify", action="store_true")
    action.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    game_root = args.game_root.expanduser().resolve()
    state_dir = args.state_dir.expanduser().resolve() if args.state_dir else (HERE.parent / "artifacts" / "fut-packselect-force-dock-ready-v18")
    try:
        if args.apply:
            result = apply_patch(game_root, state_dir)
        elif args.restore:
            result = restore_patch(game_root, state_dir)
        else:
            install = inspect_install(game_root)
            result = {"action": "verify" if args.verify else "inspect", "install": install}
            if args.verify and install["status"] != STATUS:
                raise ValueError("deterministic dock completion v18 is not installed; status=" + install["status"])
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error), "type": type(error).__name__}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
