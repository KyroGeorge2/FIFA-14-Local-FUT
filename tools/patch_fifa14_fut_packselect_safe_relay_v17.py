#!/usr/bin/env python3
"""Apply/verify/restore the FIFA 14 futPackSelect safe no-cinematic relay v17.

V16 crashed while the APT loader was relocating ActionScript because it placed
an END opcode inside the existing _AnimateIn function body. FIFA stopped walking
that body and later dereferenced the still-relative function parameter pointer
0x3B94. V17 never inserts or removes an opcode and never changes a function
length. It changes four one-byte constant operands only:

* CinematicManager -> AspectRatioManager
* PlayCinematic -> AdjustCameraPerspective
* _onDockReady final call: _AnimatePacksIn -> _IntroMovieStartedCB
* _IntroMovieStartedCB final call: _ResetLights -> _IntroMovieStoppedCB

The first two substitutions consume the original five cinematic arguments via
a known retail method without creating the VP6/loading surface. Once all four
captain objects report ready, the latter two substitutions run FIFA's shipped
started -> stopped -> _AnimatePacksIn chain. Martin Tyler commentary, Skip Audio,
input, RetrievePack and BuildSquad remain retail.

The patcher recognizes and deterministically reverses v16 and all reviewed
v4-v13 experiments before applying v17. It changes only the futPackSelect APT
record in patch.big/patch.bh and is fully reversible.
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
LEGACY_PATH = HERE / "patch_fifa14_fut_packselect_no_cinematic_v16.py"
spec = importlib.util.spec_from_file_location("fifa14_packselect_legacy", LEGACY_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not load legacy recovery helper: {LEGACY_PATH}")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)

RECORD_INDEX = legacy.RECORD_INDEX
RECORD_OFFSET = legacy.RECORD_OFFSET
RECORD_PATH_HASH = legacy.RECORD_PATH_HASH
SLOT_CAPACITY = legacy.SLOT_CAPACITY
RETAIL_RECORD_SIZE = legacy.RETAIL_RECORD_SIZE
PATCHED_RECORD_SIZE = RETAIL_RECORD_SIZE
RETAIL_STORED_SHA256 = legacy.RETAIL_STORED_SHA256
RETAIL_DECODED_SHA256 = legacy.RETAIL_DECODED_SHA256
RETAIL_APT_SHA256 = legacy.RETAIL_APT_SHA256
RETAIL_CONST_SHA256 = legacy.RETAIL_CONST_SHA256

V17_STORED_SHA256 = "bcf6106f1f0b0da98213ef2d0bbc365fb3a2ae64489a9b231b7d8cff2ff524da"
V17_DECODED_SHA256 = "f4358ee3ceace958a3c5832733328ebe422f680d51696f6ff337122b9d87b211"
V17_APT_SHA256 = "b4579815f1ab439d93c7ab51f08172d1cd92559625dba9db836b1e4563dba090"
V17_CONST_SHA256 = RETAIL_CONST_SHA256

PLAY_CINEMATIC_MANAGER_OPERAND_OFFSET = 0x1AF6
PLAY_CINEMATIC_MANAGER_ORIGINAL = 0x79  # CinematicManager
PLAY_CINEMATIC_MANAGER_PATCHED = 0x74   # AspectRatioManager
PLAY_CINEMATIC_METHOD_OPERAND_OFFSET = 0x1AF8
PLAY_CINEMATIC_METHOD_ORIGINAL = 0x7A   # PlayCinematic
PLAY_CINEMATIC_METHOD_PATCHED = 0x75    # AdjustCameraPerspective
DOCK_READY_START_CALLBACK_OPERAND_OFFSET = 0x1CF0
DOCK_READY_START_CALLBACK_ORIGINAL = 0x98  # _AnimatePacksIn
DOCK_READY_START_CALLBACK_PATCHED = 0x77   # _IntroMovieStartedCB
STARTED_CB_FINAL_METHOD_OPERAND_OFFSET = 0x256C
STARTED_CB_FINAL_METHOD_ORIGINAL = 0xE7     # _ResetLights
STARTED_CB_FINAL_METHOD_PATCHED = 0x78      # _IntroMovieStoppedCB

PLAY_CINEMATIC_CONTEXT = bytes.fromhex(
    "74 75 B9 02 B5 05 AE 20 AF 21 AF 79 B2 7A"
)
DOCK_READY_CONTEXT = bytes.fromhex("59 B9 01 B2 98 4F B9 02 A2 98")
STARTED_CB_CONTEXT = bytes.fromhex("59 B9 01 B2 E7 4F B9 02 A2 78")

STATUS = "safe-no-cinematic-dock-relay-v17"
STATE_FILE = "fut-packselect-safe-relay-v17-state.json"
RESTORE_STATE_FILE = "fut-packselect-safe-relay-v17-restore-state.json"
BACKUP_RECORD = "original/patch-futpackselect.record.bin"
BACKUP_BH = "original/patch.bh.bin"
BACKUP_METADATA = "original/metadata.json"

LEGACY_STATUSES = {
    "unsafe-v4-crash-patch",
    "retired-movie-stop-relay-v4.1",
    "loading-symbol-repair-v5",
    "cinematic-gate-relay-v6",
    "completion-input-repair-v7",
    "v6-baseline-loading-reset-v11",
    "v6-baseline-direct-loading-hide-v12",
    "v6-baseline-audio-stage-gate-release-v13",
    "no-cinematic-dock-relay-v16",
}


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
        identity["stored_sha256"] == V17_STORED_SHA256
        and identity["decoded_sha256"] == V17_DECODED_SHA256
        and identity["apt_sha256"] == V17_APT_SHA256
        and identity["const_sha256"] == V17_CONST_SHA256
    ):
        return STATUS, identity
    legacy_status, legacy_identity = legacy.classify(decoded, stored)
    return legacy_status, legacy_identity


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


def reverse_v17(decoded: bytes) -> bytes:
    apt_entry, apt = legacy.locate_apt(decoded)
    _, const = legacy.locate_const(decoded)
    if sha256_bytes(apt) != V17_APT_SHA256 or sha256_bytes(const) != V17_CONST_SHA256:
        raise ValueError("v17 APT/constant identity does not match")
    recovered = bytearray(apt)
    expected = (
        (PLAY_CINEMATIC_MANAGER_OPERAND_OFFSET, PLAY_CINEMATIC_MANAGER_PATCHED),
        (PLAY_CINEMATIC_METHOD_OPERAND_OFFSET, PLAY_CINEMATIC_METHOD_PATCHED),
        (DOCK_READY_START_CALLBACK_OPERAND_OFFSET, DOCK_READY_START_CALLBACK_PATCHED),
        (STARTED_CB_FINAL_METHOD_OPERAND_OFFSET, STARTED_CB_FINAL_METHOD_PATCHED),
    )
    for offset, value in expected:
        if recovered[offset] != value:
            raise ValueError(f"v17 byte at 0x{offset:X} does not match the reviewed patch")
    recovered[PLAY_CINEMATIC_MANAGER_OPERAND_OFFSET] = PLAY_CINEMATIC_MANAGER_ORIGINAL
    recovered[PLAY_CINEMATIC_METHOD_OPERAND_OFFSET] = PLAY_CINEMATIC_METHOD_ORIGINAL
    recovered[DOCK_READY_START_CALLBACK_OPERAND_OFFSET] = DOCK_READY_START_CALLBACK_ORIGINAL
    recovered[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] = STARTED_CB_FINAL_METHOD_ORIGINAL
    recovered = bytes(recovered)
    if sha256_bytes(recovered) != RETAIL_APT_SHA256:
        raise AssertionError("v17 reversal did not recover the retail APT")
    output = bytearray(decoded)
    output[apt_entry["offset"]:apt_entry["offset"] + apt_entry["size"]] = recovered
    output = bytes(output)
    if sha256_bytes(output) != RETAIL_DECODED_SHA256:
        raise AssertionError("v17 reversal did not recover the retail decoded package")
    return output


def recover_retail_decoded(decoded: bytes, status: str) -> bytes:
    if status == STATUS:
        return reverse_v17(decoded)
    return legacy.recover_retail_decoded(decoded, status)


def patch_retail_decoded(decoded: bytes) -> bytes:
    if sha256_bytes(decoded) != RETAIL_DECODED_SHA256:
        raise ValueError("refusing to patch an unverified retail futPackSelect package")
    apt_entry, apt = legacy.locate_apt(decoded)
    _, const = legacy.locate_const(decoded)
    if sha256_bytes(apt) != RETAIL_APT_SHA256 or sha256_bytes(const) != RETAIL_CONST_SHA256:
        raise ValueError("retail APT/constant identities do not match")
    if apt[0x1AEB:0x1AEB + len(PLAY_CINEMATIC_CONTEXT)] != PLAY_CINEMATIC_CONTEXT:
        raise ValueError("retail PlayCinematic call context does not match")
    if apt[0x1CEC:0x1CEC + len(DOCK_READY_CONTEXT)] != DOCK_READY_CONTEXT:
        raise ValueError("retail _onDockReady final-call context does not match")
    if apt[0x2568:0x2568 + len(STARTED_CB_CONTEXT)] != STARTED_CB_CONTEXT:
        raise ValueError("retail _IntroMovieStartedCB context does not match")

    patched = bytearray(apt)
    patched[PLAY_CINEMATIC_MANAGER_OPERAND_OFFSET] = PLAY_CINEMATIC_MANAGER_PATCHED
    patched[PLAY_CINEMATIC_METHOD_OPERAND_OFFSET] = PLAY_CINEMATIC_METHOD_PATCHED
    patched[DOCK_READY_START_CALLBACK_OPERAND_OFFSET] = DOCK_READY_START_CALLBACK_PATCHED
    patched[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] = STARTED_CB_FINAL_METHOD_PATCHED
    patched = bytes(patched)
    if sha256_bytes(patched) != V17_APT_SHA256:
        raise AssertionError("patched APT does not match the reviewed v17 identity")

    output = bytearray(decoded)
    output[apt_entry["offset"]:apt_entry["offset"] + apt_entry["size"]] = patched
    output = bytes(output)
    if sha256_bytes(output) != V17_DECODED_SHA256:
        raise AssertionError("patched BIG does not match the reviewed v17 identity")
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
    if current["status"] not in LEGACY_STATUSES | {STATUS}:
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
        raise ValueError("v17 retail rollback record failed verification")
    decoded, _ = legacy.decode_chunkzip(stored)
    if sha256_bytes(decoded) != RETAIL_DECODED_SHA256:
        raise ValueError("v17 retail rollback decoded package failed verification")
    record = legacy.parse_bh_record(bh)
    if record["size"] != RETAIL_RECORD_SIZE:
        raise ValueError("v17 retail rollback BH size is incorrect")
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
    return {"source": "verified existing v17 rollback backup"}


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
        return {"action": "apply", "changed": False, "reason": "v17 already installed", "install": current}
    if current["status"] != "retail-original" and current["status"] not in LEGACY_STATUSES:
        raise ValueError(
            "unknown futPackSelect identity; refusing to overwrite it. "
            f"stored={current['stored_sha256']} apt={current['apt_sha256']}"
        )
    backup = ensure_backup(game_root, state_dir, current)
    retail_stored, retail_bh, repair_method = synthesize_retail_pair(game_root, current)
    retail_decoded, layout = legacy.decode_chunkzip(retail_stored)
    patched_decoded = patch_retail_decoded(retail_decoded)
    patched_stored = legacy.encode_chunkzip_like(patched_decoded, layout)
    if len(patched_stored) != PATCHED_RECORD_SIZE or sha256_bytes(patched_stored) != V17_STORED_SHA256:
        raise AssertionError("v17 stored record identity mismatch")
    patched_bh = legacy.bh_with_size(retail_bh, PATCHED_RECORD_SIZE)
    installed = install_pair(game_root, patched_stored, patched_bh, STATUS)
    result = {
        "action": "apply",
        "changed": True,
        "repaired_from": current["status"],
        "repair_method": repair_method,
        "controlled_changes": [
            "four one-byte ActionScript constant operands; no END opcode and no function-size change",
            "CinematicManager.PlayCinematic is redirected to the already-used AspectRatioManager.AdjustCameraPerspective method",
            "the natural all-four-captains-ready edge runs retail _IntroMovieStartedCB -> _IntroMovieStoppedCB -> _AnimatePacksIn",
            "Martin Tyler PlayAudio, Skip Audio/InterruptAudio, input, RetrievePack and BuildSquad remain retail",
            "no native callback, input memory, captain, club, squad, inventory, coins, or items are written",
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
        if current["status"] != STATUS and current["status"] not in LEGACY_STATUSES:
            raise ValueError("unknown futPackSelect identity; refusing to restore over it")
        backup = load_verified_backup(state_dir)
        if backup is not None:
            retail_stored, retail_bh = backup
            method = "verified v17 rollback backup"
        else:
            retail_stored, retail_bh, method = synthesize_retail_pair(game_root, current)
        installed = install_pair(game_root, retail_stored, retail_bh, "retail-original")
        result = {"action": "restore", "changed": True, "method": method, "install": installed}
    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(state_dir / RESTORE_STATE_FILE, (json.dumps(result, indent=2) + "\n").encode("utf-8"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply/restore/verify FIFA 14 futPackSelect safe relay v17")
    parser.add_argument("--game-root", required=True, type=Path)
    parser.add_argument("--state-dir", type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--apply", action="store_true")
    action.add_argument("--restore", action="store_true")
    action.add_argument("--verify", action="store_true")
    action.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    game_root = args.game_root.expanduser().resolve()
    state_dir = args.state_dir.expanduser().resolve() if args.state_dir else (HERE.parent / "artifacts" / "fut-packselect-safe-relay-v17")
    try:
        if args.apply:
            result = apply_patch(game_root, state_dir)
        elif args.restore:
            result = restore_patch(game_root, state_dir)
        else:
            install = inspect_install(game_root)
            result = {"action": "verify" if args.verify else "inspect", "install": install}
            if args.verify and install["status"] != STATUS:
                raise ValueError("safe no-cinematic dock relay v17 is not installed; status=" + install["status"])
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error), "type": type(error).__name__}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
