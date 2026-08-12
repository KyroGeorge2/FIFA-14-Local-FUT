from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

BH_MAGIC = b"ViV4"
CHUNKZIP_MAGIC = b"chunkzip"
BIG_MAGICS = {b"BIG4", b"BIGF"}
STATE_NAME = "fut-spinner-patch-state.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def align(value: int, boundary: int = 16) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


@dataclass(frozen=True)
class ArchiveSpec:
    name: str
    big_name: str
    bh_name: str
    expected_index: int
    expected_offset: int
    expected_size: int
    expected_hash: int
    expected_stored_sha256: str
    expected_decoded_sha256: str


ARCHIVES: dict[str, ArchiveSpec] = {
    "patch": ArchiveSpec(
        name="patch",
        big_name="patch.big",
        bh_name="patch.bh",
        expected_index=2146,
        expected_offset=111_471_040,
        expected_size=20_481,
        expected_hash=0x56CC043AC27ECC11,
        expected_stored_sha256="91e6b9ab3d6a603fff2a5ed73b81c4f472933663b153748e9647cb8d48ca46b6",
        expected_decoded_sha256="35b0c792c5e49a5b906a6b37c2fd281d2b6b13719266a19c27a328b9056ab571",
    ),
    # The base archive contains a byte-identical helperFunctions package. Its
    # BH path hash is zero in this build, so identity is anchored by exact
    # record index, offset, size and payload hashes instead.
    "data0": ArchiveSpec(
        name="data0",
        big_name="data0.big",
        bh_name="data0.bh",
        expected_index=110,
        expected_offset=3_440_960,
        expected_size=20_481,
        expected_hash=0,
        expected_stored_sha256="91e6b9ab3d6a603fff2a5ed73b81c4f472933663b153748e9647cb8d48ca46b6",
        expected_decoded_sha256="35b0c792c5e49a5b906a6b37c2fd281d2b6b13719266a19c27a328b9056ab571",
    ),
}


@dataclass(frozen=True)
class BhRecord:
    index: int
    offset: int
    size: int
    reserved: int
    path_hash: int
    table_offset: int


@dataclass(frozen=True)
class BigEntry:
    index: int
    name: str
    offset: int
    size: int


def parse_bh(data: bytes) -> list[BhRecord]:
    if len(data) < 16 or data[:4] != BH_MAGIC:
        raise ValueError("not a ViV4 BH index")
    count = struct.unpack_from(">I", data, 8)[0]
    expected = 16 + count * 20
    if expected > len(data):
        raise ValueError(f"BH index is truncated: needs {expected} bytes, has {len(data)}")
    records: list[BhRecord] = []
    pos = 16
    for index in range(count):
        table_offset = pos
        offset, size, reserved, hash_hi, hash_lo = struct.unpack_from(">IIIII", data, pos)
        pos += 20
        records.append(
            BhRecord(
                index=index,
                offset=offset,
                size=size,
                reserved=reserved,
                path_hash=(hash_hi << 32) | hash_lo,
                table_offset=table_offset,
            )
        )
    return records


def get_record(data: bytes, spec: ArchiveSpec) -> BhRecord:
    """Return the exact reviewed retail record from a BH index.

    This remains the strict resolver used for legacy/backup validation paths.
    Public compatibility mode uses ``resolve_compatible_record`` below so a
    repacked or older title-update BH can still be handled when the actual
    helperFunctions payload is byte-for-byte/decoded-identical.
    """
    records = parse_bh(data)
    if spec.expected_index >= len(records):
        raise ValueError(f"{spec.name}.bh does not contain record {spec.expected_index}")
    record = records[spec.expected_index]
    actual = (record.offset, record.size, record.path_hash)
    expected = (spec.expected_offset, spec.expected_size, spec.expected_hash)
    if actual != expected:
        raise ValueError(
            f"{spec.name} helperFunctions record changed: actual={actual}, expected={expected}"
        )
    return record


def detect_modes_from_apt(apt: bytes) -> list[str]:
    return [
        candidate
        for candidate, changes in PATCHES.items()
        if all(
            apt[offset : offset + len(after)] == after
            for offset, _before, after, _description in changes
        )
    ]


def inspect_helper_payload(payload: bytes, spec: ArchiveSpec) -> dict:
    decoded, chunk_info = decode_chunkzip(payload)
    apt_entry = get_apt_entry(decoded)
    apt = decoded[apt_entry.offset : apt_entry.offset + apt_entry.size]
    decoded_hash = sha256_bytes(decoded)
    stored_hash = sha256_bytes(payload)
    modes = detect_modes_from_apt(apt)
    return {
        "payload": payload,
        "decoded": decoded,
        "stored_sha256": stored_hash,
        "decoded_sha256": decoded_hash,
        "apt_sha256": sha256_bytes(apt),
        "chunkzip": chunk_info,
        "detected_modes": modes,
        "is_reviewed_retail_decoded": decoded_hash == spec.expected_decoded_sha256,
        "is_reviewed_retail_stored": stored_hash == spec.expected_stored_sha256,
    }


def resolve_compatible_record(game_root: Path, spec: ArchiveSpec) -> tuple[BhRecord, dict, str]:
    """Resolve helperFunctions by content when archive record numbering differs.

    Some alternate/older FIFA 14 installs have a much smaller patch.bh, so the
    retail title-update record index does not exist.  We do *not* guess an
    offset.  Instead we first try the exact reviewed record, then scan BH
    records for a unique payload whose decoded helperFunctions package matches
    the reviewed retail package or one of our known branch-only patched states.
    This keeps compatibility permissive without blind archive writes.
    """
    big_path = game_root / spec.big_name
    bh_path = game_root / spec.bh_name
    if not big_path.is_file() or not bh_path.is_file():
        raise FileNotFoundError(f"{spec.big_name}/{spec.bh_name} not found under {game_root}")

    bh = bh_path.read_bytes()
    records = parse_bh(bh)
    big_size = big_path.stat().st_size

    # Fast path: reviewed record index/identity.  The compressed size may have
    # changed after our own patch, so path hash + readable bounds are enough to
    # inspect it before classifying the payload.
    if spec.expected_index < len(records):
        record = records[spec.expected_index]
        if (
            record.path_hash == spec.expected_hash
            and record.size > 0
            and record.offset >= 0
            and record.offset + record.size <= big_size
        ):
            with big_path.open("rb") as handle:
                handle.seek(record.offset)
                payload = handle.read(record.size)
            try:
                info = inspect_helper_payload(payload, spec)
                if info["is_reviewed_retail_decoded"] or info["detected_modes"]:
                    return record, info, "reviewed-record"
            except Exception:
                pass

    candidates: list[tuple[BhRecord, dict]] = []
    with big_path.open("rb") as handle:
        for record in records:
            # helperFunctions is small; broad bounds avoid reading huge assets
            # while still tolerating different compression ratios.
            if record.size < 128 or record.size > 512_000:
                continue
            if record.offset < 0 or record.offset + record.size > big_size:
                continue
            handle.seek(record.offset)
            payload = handle.read(record.size)
            if len(payload) != record.size or not payload.startswith(CHUNKZIP_MAGIC):
                continue
            try:
                info = inspect_helper_payload(payload, spec)
            except Exception:
                continue
            if info["is_reviewed_retail_decoded"] or info["detected_modes"]:
                candidates.append((record, info))

    if not candidates:
        raise ValueError(
            f"{spec.name}.bh has {len(records)} records and no compatible helperFunctions payload was found"
        )
    if len(candidates) > 1:
        exact_stored = [item for item in candidates if item[1]["is_reviewed_retail_stored"]]
        if len(exact_stored) == 1:
            candidates = exact_stored
        else:
            indexes = ",".join(str(item[0].index) for item in candidates[:12])
            raise ValueError(
                f"{spec.name}.bh contains multiple compatible helperFunctions candidates ({indexes}); refusing an ambiguous write"
            )
    record, info = candidates[0]
    return record, info, "content-scan"


def decode_chunkzip(payload: bytes) -> tuple[bytes, dict]:
    if len(payload) < 40 or payload[:8] != CHUNKZIP_MAGIC:
        raise ValueError("payload is not chunkzip")
    version, output_size, chunk_size, count, alignment, a, b, c = struct.unpack_from(
        ">IIIIIIII", payload, 8
    )
    if version != 2 or alignment != 16 or a or b or c:
        raise ValueError(
            f"unsupported chunkzip header: version={version}, alignment={alignment}, flags={(a,b,c)}"
        )
    pos = 40
    output = bytearray()
    chunks: list[dict] = []
    for index in range(count):
        if pos + 8 > len(payload):
            raise ValueError(f"truncated chunk descriptor {index}")
        stored_size, compression_type = struct.unpack_from(">II", payload, pos)
        data_start = pos + 8
        data_end = data_start + stored_size
        if data_end > len(payload):
            raise ValueError(f"truncated chunk data {index}")
        stored = payload[data_start:data_end]
        if compression_type == 0:
            decoded = stored
        elif compression_type == 1:
            decoded = zlib.decompress(stored, -zlib.MAX_WBITS)
        else:
            raise ValueError(f"unsupported chunk compression type {compression_type}")
        output.extend(decoded)
        chunks.append(
            {
                "index": index,
                "stored_size": stored_size,
                "decoded_size": len(decoded),
                "compression_type": compression_type,
            }
        )
        pos = align(data_end + 8) - 8
    if len(output) != output_size:
        raise ValueError(f"chunkzip output mismatch: {len(output)} != {output_size}")
    return bytes(output), {
        "version": version,
        "output_size": output_size,
        "chunk_size": chunk_size,
        "chunk_count": count,
        "chunks": chunks,
    }


def encode_single_chunkzip(decoded: bytes) -> bytes:
    """Encode a one-chunk ChunkZip payload using the smallest safe zlib variant.

    FIFA's retail archives were not produced by Python's exact zlib settings. A
    handful of byte-only APT branch changes can therefore recompress slightly
    larger than the retail physical slot even though the decoded package size
    is unchanged. Try several standards-compliant raw-DEFLATE settings first so
    installations with a tight slot do not need relocation unnecessarily.
    """
    candidates: list[bytes] = []
    settings = (
        (9, 9, zlib.Z_DEFAULT_STRATEGY),
        (9, 8, zlib.Z_DEFAULT_STRATEGY),
        (8, 9, zlib.Z_DEFAULT_STRATEGY),
        (8, 8, zlib.Z_DEFAULT_STRATEGY),
        (9, 9, zlib.Z_FILTERED),
        (9, 8, zlib.Z_FILTERED),
    )
    for level, mem_level, strategy in settings:
        compressor = zlib.compressobj(
            level, zlib.DEFLATED, -zlib.MAX_WBITS, mem_level, strategy
        )
        candidates.append(compressor.compress(decoded) + compressor.flush())
    compressed = min(candidates, key=len)
    header = CHUNKZIP_MAGIC + struct.pack(
        ">IIIIIIII", 2, len(decoded), 262_144, 1, 16, 0, 0, 0
    )
    return header + struct.pack(">II", len(compressed), 1) + compressed


def parse_big(data: bytes) -> list[BigEntry]:
    if len(data) < 16 or data[:4] not in BIG_MAGICS:
        raise ValueError("decoded helperFunctions package is not BIG4/BIGF")
    count = struct.unpack_from(">I", data, 8)[0]
    header_size = struct.unpack_from(">I", data, 12)[0]
    if not 16 <= header_size <= len(data):
        raise ValueError(f"invalid BIG header size {header_size}")
    pos = 16
    entries: list[BigEntry] = []
    for index in range(count):
        if pos + 8 > header_size:
            raise ValueError("truncated BIG entry table")
        offset, size = struct.unpack_from(">II", data, pos)
        pos += 8
        end = data.find(b"\0", pos, header_size)
        if end < 0:
            raise ValueError("unterminated BIG entry name")
        name = data[pos:end].decode("ascii", errors="strict")
        pos = end + 1
        if offset + size > len(data):
            raise ValueError(f"BIG entry {name!r} lies outside package")
        entries.append(BigEntry(index=index, name=name, offset=offset, size=size))
    return entries


def get_apt_entry(decoded: bytes) -> BigEntry:
    candidates = [
        entry
        for entry in parse_big(decoded)
        if decoded[entry.offset : entry.offset + 9] == b"Apt Data:"
    ]
    if len(candidates) != 1:
        raise ValueError(f"expected one APT data entry, found {len(candidates)}")
    entry = candidates[0]
    if entry.name != "0" or entry.offset != 64 or entry.size != 28_172:
        raise ValueError(
            f"unexpected helperFunctions APT layout: name={entry.name!r}, "
            f"offset={entry.offset}, size={entry.size}"
        )
    return entry


PATCHES: dict[str, tuple[tuple[int, bytes, bytes, str], ...]] = {
    # Branch-only modes deliberately preserve the original instruction-stream
    # length and alignment. They introduce no End opcode and do not replace a
    # multi-instruction body with a shorter body.
    "branch-saveload": (
        (
            0x2C86,
            bytes.fromhex("9d 00 28 00 00 00"),
            bytes.fromhex("99 00 64 00 00 00"),
            "force checkForFUTRosters to the existing retail futSquadLoadSuccess block at 0x2CF0",
        ),
    ),
    "branch-offline": (
        (
            0x2C86,
            bytes.fromhex("9d 00 28 00 00 00"),
            bytes.fromhex("99 00 64 00 00 00"),
            "force checkForFUTRosters to the existing retail futSquadLoadSuccess block at 0x2CF0",
        ),
        (
            0x2D92,
            bytes.fromhex("9d 00 0c 00 00 00"),
            bytes.fromhex("9d 00 00 00 00 00"),
            "make the existing LiveDB conditional branch land on its next instruction, preserving the retail direct continuation",
        ),
        (
            0x2FEA,
            bytes.fromhex("9d 00 b0 00 00 00"),
            bytes.fromhex("99 00 b0 00 00 00"),
            "force proceedEnterFUT to the existing retail enterFutCallback block at 0x30A0",
        ),
    ),
}

EXPECTED_BRANCH_TARGETS: dict[str, tuple[tuple[int, int, str], ...]] = {
    "branch-saveload": (
        (0x2C86, 0x2CF0, "futSquadLoadSuccess call block"),
    ),
    "branch-offline": (
        (0x2C86, 0x2CF0, "futSquadLoadSuccess call block"),
        (0x2D92, 0x2D98, "futPostRosterXMLDownload direct path"),
        (0x2FEA, 0x30A0, "enterFutCallback call block"),
    ),
}



def apply_apt_patches(decoded: bytes, mode: str) -> tuple[bytes, list[dict]]:
    apt_entry = get_apt_entry(decoded)
    apt = bytearray(decoded[apt_entry.offset : apt_entry.offset + apt_entry.size])
    changes: list[dict] = []
    for apt_offset, expected, replacement, description in PATCHES[mode]:
        actual = bytes(apt[apt_offset : apt_offset + len(expected)])
        if actual != expected:
            raise ValueError(
                f"APT verification failed at 0x{apt_offset:X}: "
                f"expected {expected.hex(' ')}, got {actual.hex(' ')}"
            )
        apt[apt_offset : apt_offset + len(replacement)] = replacement
        changes.append(
            {
                "apt_offset": f"0x{apt_offset:X}",
                "before": expected.hex(" "),
                "after": replacement.hex(" "),
                "description": description,
            }
        )
    # All hotfix patches must preserve the APT byte length and must not add or
    # remove End opcodes. The previous crashing build violated this rule by
    # inserting 0x00 inside function bodies.
    original_apt = decoded[apt_entry.offset : apt_entry.offset + apt_entry.size]
    if len(apt) != len(original_apt):
        raise AssertionError("APT length changed")
    # Every replacement is constrained to one existing six-byte aligned
    # branch instruction. Zero bytes inside the padding/displacement are data,
    # not new End opcodes.
    for branch_offset, _expected_target, _label in EXPECTED_BRANCH_TARGETS[mode]:
        if branch_offset not in [change[0] for change in PATCHES[mode]]:
            raise AssertionError(f"untracked branch patch at 0x{branch_offset:X}")

    # Verify the exact aligned branch destinations after patching. All three
    # instructions are six-byte aligned branch encodings: opcode, one padding
    # byte, then a signed little-endian 32-bit displacement relative to the
    # end of the instruction.
    for branch_offset, expected_target, label in EXPECTED_BRANCH_TARGETS[mode]:
        opcode = apt[branch_offset]
        if opcode not in (0x99, 0x9D):
            raise AssertionError(f"unexpected branch opcode 0x{opcode:02X} at 0x{branch_offset:X}")
        displacement = struct.unpack_from("<i", apt, branch_offset + 2)[0]
        target = branch_offset + 6 + displacement
        if target != expected_target:
            raise AssertionError(
                f"branch target mismatch for {label}: 0x{target:X} != 0x{expected_target:X}"
            )

    result = bytearray(decoded)
    result[apt_entry.offset : apt_entry.offset + apt_entry.size] = apt
    return bytes(result), changes


def verify_original_payload(payload: bytes, spec: ArchiveSpec) -> tuple[bytes, dict]:
    """Verify the reviewed *decoded* retail helperFunctions package.

    Alternate distributions sometimes recompress an otherwise identical
    chunkzip payload, so the stored SHA can legitimately differ while the
    decoded package is still exactly the reviewed FIFA 14 content.  The decoded
    SHA remains a hard safety boundary before any branch bytes are changed.
    """
    stored_hash = sha256_bytes(payload)
    decoded, chunk_info = decode_chunkzip(payload)
    decoded_hash = sha256_bytes(decoded)
    if decoded_hash != spec.expected_decoded_sha256:
        raise ValueError(
            f"{spec.name} helperFunctions decoded package mismatch: "
            f"{decoded_hash} != {spec.expected_decoded_sha256}"
        )
    apt_entry = get_apt_entry(decoded)
    return decoded, {
        "stored_sha256": stored_hash,
        "stored_sha256_matches_reviewed": stored_hash == spec.expected_stored_sha256,
        "decoded_sha256": decoded_hash,
        "apt_sha256": sha256_bytes(decoded[apt_entry.offset : apt_entry.offset + apt_entry.size]),
        "chunkzip": chunk_info,
    }


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
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


def write_range(path: Path, offset: int, data: bytes, clear_to: int) -> None:
    with path.open("r+b") as handle:
        handle.seek(offset)
        handle.write(data)
        if clear_to > len(data):
            handle.write(b"\0" * (clear_to - len(data)))
        handle.flush()
        os.fsync(handle.fileno())


def append_aligned_payload(path: Path, data: bytes, boundary: int = 16) -> int:
    """Append payload to a BIG file and return its aligned absolute offset.

    The BH index is the authoritative path-hash -> offset/size table. Relocating
    a record to unused appended space avoids overwriting the following physical
    slot when recompression grows beyond the retail allocation. The original BH
    and record are still retained in the targeted backup for exact restoration.
    """
    with path.open("r+b") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        offset = align(end, boundary)
        if offset > 0xFFFFFFFF or offset + len(data) > 0x100000000:
            raise ValueError(f"{path.name} relocation would exceed the 32-bit BH offset range")
        if offset > end:
            handle.write(b"\0" * (offset - end))
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return offset


def backup_paths(state_dir: Path, spec: ArchiveSpec) -> tuple[Path, Path, Path]:
    original_dir = state_dir / "original"
    return (
        original_dir / f"{spec.name}.record.bin",
        original_dir / f"{spec.name}.bh.bin",
        original_dir / f"{spec.name}.metadata.json",
    )


def ensure_targeted_backup(game_root: Path, state_dir: Path, spec: ArchiveSpec) -> tuple[bytes, bytes, BhRecord]:
    record_backup, bh_backup, metadata_backup = backup_paths(state_dir, spec)
    present = [path.exists() for path in (record_backup, bh_backup, metadata_backup)]
    if any(present) and not all(present):
        raise ValueError(f"incomplete {spec.name} backup set under {record_backup.parent}")
    if all(present):
        original_bh = bh_backup.read_bytes()
        records = parse_bh(original_bh)
        metadata = json.loads(metadata_backup.read_text(encoding="utf-8"))
        index = int(metadata.get("record", {}).get("index", -1))
        if index < 0 or index >= len(records):
            raise ValueError(f"{spec.name} backup metadata record index {index} is invalid")
        record = records[index]
        original_payload = record_backup.read_bytes()
        verify_original_payload(original_payload, spec)
        return original_payload, original_bh, record

    big_path = game_root / spec.big_name
    bh_path = game_root / spec.bh_name
    if not big_path.is_file() or not bh_path.is_file():
        raise FileNotFoundError(f"{spec.big_name}/{spec.bh_name} not found under {game_root}")
    original_bh = bh_path.read_bytes()
    record, resolved, resolution = resolve_compatible_record(game_root, spec)
    if not resolved["is_reviewed_retail_decoded"]:
        raise ValueError(
            f"{spec.name} helperFunctions is already modified ({resolved['detected_modes']}) but no original backup exists"
        )
    original_payload = resolved["payload"]
    identity = verify_original_payload(original_payload, spec)[1]
    record_backup.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(record_backup, original_payload)
    atomic_write(bh_backup, original_bh)
    metadata = {
        "archive": spec.name,
        "big_path": str(big_path),
        "bh_path": str(bh_path),
        "resolution": resolution,
        "record": {
            "index": record.index,
            "offset": record.offset,
            "size": record.size,
            "path_hash": f"{record.path_hash:016X}",
        },
        "identity": identity,
    }
    metadata_backup.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return original_payload, original_bh, record


def parse_archive_selection(value: str) -> list[ArchiveSpec]:
    names = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not names:
        raise ValueError("no archives selected")
    unknown = [name for name in names if name not in ARCHIVES]
    if unknown:
        raise ValueError(f"unknown archives: {', '.join(unknown)}")
    result: list[ArchiveSpec] = []
    for name in names:
        spec = ARCHIVES[name]
        if spec not in result:
            result.append(spec)
    return result


def inspect_installed_archive(game_root: Path, spec: ArchiveSpec) -> dict:
    """Inspect helperFunctions using exact identity first, then safe content scan."""
    record, info, resolution = resolve_compatible_record(game_root, spec)
    retail_original = (
        record.index == spec.expected_index
        and record.offset == spec.expected_offset
        and record.size == spec.expected_size
        and record.path_hash == spec.expected_hash
        and info["is_reviewed_retail_stored"]
        and info["is_reviewed_retail_decoded"]
    )
    return {
        "record": record,
        "payload": info["payload"],
        "decoded": info["decoded"],
        "retail_original": retail_original,
        "detected_modes": info["detected_modes"],
        "payload_sha256": info["stored_sha256"],
        "decoded_sha256": info["decoded_sha256"],
        "apt_sha256": info["apt_sha256"],
        "chunkzip": info["chunkzip"],
        "resolution": resolution,
    }


def patch_one_archive(game_root: Path, state_dir: Path, spec: ArchiveSpec, mode: str) -> dict:
    installed = inspect_installed_archive(game_root, spec)
    if mode in installed["detected_modes"]:
        record = installed["record"]
        return {
            "archive": spec.name,
            "big": str(game_root / spec.big_name),
            "bh": str(game_root / spec.bh_name),
            "changed": False,
            "reason": "requested branch-only mode is already installed",
            "record": {
                "index": record.index,
                "path_hash": f"{record.path_hash:016X}",
                "offset": record.offset,
                "size": record.size,
            },
            "retail_original": installed["retail_original"],
            "detected_modes": installed["detected_modes"],
            "payload_sha256": installed["payload_sha256"],
            "decoded_sha256": installed["decoded_sha256"],
            "apt_sha256": installed["apt_sha256"],
            "chunkzip": installed["chunkzip"],
        }

    original_payload, original_bh, record = ensure_targeted_backup(game_root, state_dir, spec)
    decoded, identity = verify_original_payload(original_payload, spec)
    patched_decoded, changes = apply_apt_patches(decoded, mode)
    patched_payload = encode_single_chunkzip(patched_decoded)
    decoded_check, _ = decode_chunkzip(patched_payload)
    if decoded_check != patched_decoded:
        raise AssertionError("re-encoded package failed round-trip validation")

    records = parse_bh(original_bh)
    next_offsets = [row.offset for row in records if row.offset > record.offset]
    big_path = game_root / spec.big_name
    bh_path = game_root / spec.bh_name
    next_offset = min(next_offsets) if next_offsets else big_path.stat().st_size
    capacity = next_offset - record.offset

    patched_bh = bytearray(original_bh)
    relocated = len(patched_payload) > capacity
    if relocated:
        # Do not overwrite the following archive record. Append the patched
        # helperFunctions payload to aligned free space and point only this BH
        # record at it. This is reversible because original_bh is backed up.
        write_offset = append_aligned_payload(big_path, patched_payload)
        struct.pack_into(">I", patched_bh, record.table_offset, write_offset)
    else:
        write_offset = record.offset
        write_range(big_path, write_offset, patched_payload, record.size)
    struct.pack_into(">I", patched_bh, record.table_offset + 4, len(patched_payload))
    written_record = parse_bh(bytes(patched_bh))[record.index]
    if written_record.offset != write_offset or written_record.size != len(patched_payload):
        raise AssertionError("BH offset/size update failed")

    # Write/append the package first, then atomically replace the small BH index.
    # The targeted backup can restore the exact retail mapping after interruption.
    atomic_write(bh_path, bytes(patched_bh))

    # Read back from disk and validate the exact patched archive pair.
    current_bh = bh_path.read_bytes()
    current_record = parse_bh(current_bh)[record.index]
    with big_path.open("rb") as handle:
        handle.seek(current_record.offset)
        current_payload = handle.read(current_record.size)
    current_decoded, _ = decode_chunkzip(current_payload)
    if current_decoded != patched_decoded:
        raise AssertionError(f"{spec.name} on-disk validation failed")

    return {
        "archive": spec.name,
        "big": str(big_path),
        "changed": True,
        "bh": str(bh_path),
        "record": {
            "index": record.index,
            "path_hash": f"{record.path_hash:016X}",
            "original_offset": record.offset,
            "patched_offset": write_offset,
            "original_size": record.size,
            "patched_size": len(patched_payload),
            "slot_capacity": capacity,
            "relocated": relocated,
        },
        "identity": identity,
        "patched_payload_sha256": sha256_bytes(patched_payload),
        "patched_decoded_sha256": sha256_bytes(patched_decoded),
        "changes": changes,
    }


def patch_archives(game_root: Path, state_dir: Path, specs: list[ArchiveSpec], mode: str) -> dict:
    results = [patch_one_archive(game_root, state_dir, spec, mode) for spec in specs]
    state = {
        "schema": 3,
        "mode": mode,
        "game_root": str(game_root),
        "archives": results,
        "backups": str(state_dir / "original"),
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / STATE_NAME
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    state["state_file"] = str(state_path)
    return state


def restore_one_archive(game_root: Path, state_dir: Path, spec: ArchiveSpec) -> dict:
    record_backup, bh_backup, metadata_backup = backup_paths(state_dir, spec)
    if not record_backup.is_file() or not bh_backup.is_file() or not metadata_backup.is_file():
        raise FileNotFoundError(f"no targeted backup exists for {spec.name}")
    original_payload = record_backup.read_bytes()
    original_bh = bh_backup.read_bytes()
    records = parse_bh(original_bh)
    metadata = json.loads(metadata_backup.read_text(encoding="utf-8"))
    index = int(metadata.get("record", {}).get("index", -1))
    if index < 0 or index >= len(records):
        raise ValueError(f"{spec.name} backup metadata record index {index} is invalid")
    record = records[index]
    verify_original_payload(original_payload, spec)
    big_path = game_root / spec.big_name
    bh_path = game_root / spec.bh_name
    write_range(big_path, record.offset, original_payload, record.size)
    atomic_write(bh_path, original_bh)
    return {
        "archive": spec.name,
        "restored": True,
        "record_index": record.index,
        "record_size": record.size,
        "payload_sha256": sha256_bytes(original_payload),
        "backups_preserved": True,
    }


def restore_archives(game_root: Path, state_dir: Path, specs: list[ArchiveSpec]) -> dict:
    results = [restore_one_archive(game_root, state_dir, spec) for spec in specs]
    result = {"game_root": str(game_root), "archives": results}
    state_path = state_dir / "fut-spinner-restore-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    result["state_file"] = str(state_path)
    return result


def verify_one_archive(game_root: Path, spec: ArchiveSpec) -> dict:
    record, info, resolution = resolve_compatible_record(game_root, spec)
    original = (
        record.index == spec.expected_index
        and record.offset == spec.expected_offset
        and record.size == spec.expected_size
        and record.path_hash == spec.expected_hash
        and info["is_reviewed_retail_stored"]
        and info["is_reviewed_retail_decoded"]
    )
    return {
        "archive": spec.name,
        "record": {
            "index": record.index,
            "path_hash": f"{record.path_hash:016X}",
            "offset": record.offset,
            "size": record.size,
        },
        "resolution": resolution,
        "payload_sha256": info["stored_sha256"],
        "decoded_sha256": info["decoded_sha256"],
        "apt_sha256": info["apt_sha256"],
        "retail_original": original,
        "detected_modes": info["detected_modes"],
        "chunkzip": info["chunkzip"],
    }


def verify_archives(game_root: Path, specs: list[ArchiveSpec]) -> dict:
    return {
        "game_root": str(game_root),
        "archives": [verify_one_archive(game_root, spec) for spec in specs],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply a branch-only FIFA 14 helperFunctions APT hotfix for the FUT frontend spinner"
    )
    parser.add_argument("--game-root", required=True, type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument(
        "--archives",
        default="patch",
        help="comma-separated archive set: patch or patch,data0",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--apply", choices=tuple(PATCHES))
    action.add_argument("--restore", action="store_true")
    action.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    try:
        game_root = args.game_root.expanduser().resolve()
        state_dir = (
            args.state_dir.expanduser().resolve()
            if args.state_dir
            else game_root / "fut-spinner-patch"
        )
        specs = parse_archive_selection(args.archives)
        if args.apply:
            result = patch_archives(game_root, state_dir, specs, args.apply)
        elif args.restore:
            result = restore_archives(game_root, state_dir, specs)
        else:
            result = verify_archives(game_root, specs)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
