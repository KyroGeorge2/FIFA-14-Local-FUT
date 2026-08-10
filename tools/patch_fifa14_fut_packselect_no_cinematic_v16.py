#!/usr/bin/env python3
"""Apply/verify/restore the FIFA 14 futPackSelect no-cinematic dock relay v16.

v15 proved the visible captain-screen blocker is not NAV ``MENU_LOADING``:
the only traced MENU_LOADING popup was created and removed before FUT login.
The remaining in-front Loading surface is therefore owned by the captain
cinematic path itself. Retail ``_AnimateIn`` launches a VP6 movie through
``CinematicManager.PlayCinematic``; the revived client has captain data and art
but cannot complete that legacy movie surface reliably.

v16 makes three reviewed, deterministic ActionScript changes while preserving
the shipped selection and squad-building flow:

* End ``_AnimateIn`` immediately after the retail
  ``AspectRatioManager.AdjustCameraPerspective`` call, so the broken VP6
  cinematic/loading surface is never created.
* Retarget ``_onDockReady``'s final zero-argument call from
  ``_AnimatePacksIn`` to the existing ``_IntroMovieStartedCB``.
* Retarget the started callback's final zero-argument call from
  ``_ResetLights`` to the existing ``_IntroMovieStoppedCB``.

The natural dock-ready edge now performs the retail started -> stopped ->
``_AnimatePacksIn`` sequence after all four captain objects are constructed.
That preserves Martin Tyler's ``FUT_IB_Captain_Select`` commentary, the retail
``Skip Audio``/``InterruptAudio`` callback, captain input, ``RetrievePack`` and
``BuildSquad``. No native callback is forced, no input memory is written, and
no club, squad, inventory, coin or item state is fabricated.

Recognized v4-v13 installations are deterministically reversed to exact retail
before v16 is installed. The patch is fully reversible and changes only the
futPackSelect APT record in patch.big/patch.bh.
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
BIG_MAGICS = {b"BIG4", b"BIGF"}

RECORD_INDEX = 2_107
RECORD_OFFSET = 106_170_752
RETAIL_RECORD_SIZE = 1_017_076
PATCHED_RECORD_SIZE = 1_017_060
LEGACY_V11_RECORD_SIZE = 1_017_079
LEGACY_V5_RECORD_SIZE = 1_017_077
RECORD_PATH_HASH = 0x49FB41B20E940BFC
SLOT_CAPACITY = 1_017_088
NEXT_RECORD_OFFSET = RECORD_OFFSET + SLOT_CAPACITY

RETAIL_STORED_SHA256 = "2b7e0dee724e070e40577fb38c2f49ab364f18b1a1d20070c86c7d878983a4b0"
RETAIL_DECODED_SHA256 = "dd658da4ad82c14b8849c1af1699fe077673479b672dad3e488a6e86df469d87"
RETAIL_APT_SHA256 = "2dc0096194db202875642930a73cb8098c83376853583f3b115547fdc0d3150d"
RETAIL_CONST_SHA256 = "2bbd90ba4834b06a74b8750ce02b6b789b2d497c86809f270abacca6822f53d4"

# Retired control-flow artifacts are recognized only for safe reversal.
UNSAFE_V4_STORED_SHA256 = "4861f54b3caccdfeaebe0a6b1daecd856cf7f7709a7b896d78f2f7657b9563bb"
UNSAFE_V4_DECODED_SHA256 = "fbda64e4bc25ed409e40953417827228e9c8762e5ff673c7c7642d03c03080c0"
UNSAFE_V4_APT_SHA256 = "0aae96c46472c8f9b945c3c4f5ebe5079fefa63b1f9704807dfd55148aefe71a"
V41_STORED_SHA256 = "3160679f4de9fa48e3130f9dff4e39125367c14607f056709ba828115f69bce8"
V41_DECODED_SHA256 = "6cf19940cb8d99b1f8271e9c81ec0592fe024091a2b1c09f5022ca7e3eb9e281"
V41_APT_SHA256 = "d29bee5d329ad6666da38810994a48a096efa25fef4880a7e8ad78f3eaa61493"

V5_STORED_SHA256 = "afc1040945a985dde7a5fbb84582c13f07d5f32753e4bd5c56b14be338fc0ca5"
V5_DECODED_SHA256 = "e2025fbeaae3b49304545c3e4f4a57529d9ac9083350690379bf88160ccceda8"
V5_APT_SHA256 = RETAIL_APT_SHA256
V5_CONST_SHA256 = "7a5b72a56c73b7304d791e435627314a498e08a16b974d31119f3b3c9e3accef"

V6_STORED_SHA256 = "3d51fa675a21444dc9346f18c8085a7a4f4ec7a363787f21c5a1d12afb26cf31"
V6_DECODED_SHA256 = "2ac37a8f6e47ca2b7aac1d0690f10bc730201da1500a859c35949674c11978d4"
V6_APT_SHA256 = "5e990893c1b7da3d9fc3b1405295d005b9c1428d3944cdb4b3f06c27bc89c641"
V6_CONST_SHA256 = RETAIL_CONST_SHA256

V7_STORED_SHA256 = "729d473a61a41378009a7ef58817269f854568b1d282f5413c0a12802216a9d9"
V11_STORED_SHA256 = "d46d319d10f14cf68d5215e43eeb94bd94b6f0a005da5d9bdc8717b3b150e966"
V12_STORED_SHA256 = "ce23e34dbd4e936f0add0c4abbccedb75a40b3e0e5d807f66e8a855295f0fe40"
V13_STORED_SHA256 = "c946dde103cd2be77ec0866f5a53a6247d600baa79fc5e5306a404c2cbc6669c"
V7_DECODED_SHA256 = "822b3587d88005dc09ecc97e8feebc9dbd86cf106555c7080bfce7a4683cbfd2"
V11_DECODED_SHA256 = "fd50fda11f32dae04218646862d6088cb170d08a7494362b217115ea3f96ebc1"
V12_DECODED_SHA256 = "c948b44ea47567272811febaf5fa4d52584897bc7d5c94bd36ffd09f64ad0b28"
V13_DECODED_SHA256 = "d8efe6a5f91e60a9657e936183ff63e2694a5f08ec5f0c379869d5205342c660"
V7_APT_SHA256 = "2c53d8b0e4f2c085ac5c765d55ca9fc43855a42441fc7931e0d528953ef4487c"
V7_CONST_SHA256 = "261e8b68ee5fa0c470b618a1cccb4982c6ce9f93e01ff241a6b81636747bdfa8"
V11_APT_SHA256 = V6_APT_SHA256
V11_CONST_SHA256 = V7_CONST_SHA256
V12_APT_SHA256 = "9d68cd43a00341699f1e98a7dbb1a64b1adc89609945c06d5412432d3fb9eabf"
V12_CONST_SHA256 = "ed7ada931fcbf8a65d0e849dbc0ced05dee8825cc76e6a3b1b685ad70142f4fc"
V13_APT_SHA256 = "75600495f8e14c79c56a5455f181c9b938f70913c3ed5acc189396eaf1d198dc"
V13_CONST_SHA256 = "82fa0ce5699018332d50cb2158d46c3e7f60003d3c86b4a8cfa7bab65478b299"

V16_STORED_SHA256 = "466d3ba0a93bacb76dc39ae647142bb7975edff3f825bf73dc33fb61950b4e33"
V16_DECODED_SHA256 = "122f79efe86e4cb089fbdfb0717b390da8328d5e6582bab74c5825eb81c56edc"
V16_APT_SHA256 = "af07bf3e2982e55f347320662d48cf7bd9bc35e3851fbd671feee2931df31915"
V16_CONST_SHA256 = RETAIL_CONST_SHA256

APT_ENTRY_INDEX = 0
APT_ENTRY_NAME = "0"
APT_ENTRY_OFFSET = 320
APT_ENTRY_SIZE = 16_085
CONST_ENTRY_INDEX = 26
CONST_ENTRY_NAME = "1"
CONST_ENTRY_OFFSET = 2_894_080
CONST_ENTRY_SIZE = 11_638
CONST_SYMBOL_OFFSET = 0x205D
SINGULAR_SYMBOL = b"ResetNetworkOperation\0\0\0ShowHideBackground"
PLURAL_SYMBOL = b"ResetNetworkOperations\0\0ShowHideBackground"

UNSAFE_LOAD_DOCK_PATCH_OFFSET = 0x1BC6
UNSAFE_LOAD_DOCK_ORIGINAL = bytes.fromhex("74 5A B9 01 AF 5E B2 6A")
UNSAFE_LOAD_DOCK_PATCHED = bytes.fromhex("59 B9 01 B2 87 00 00 00")
UNSAFE_DOCK_READY_METHOD_OFFSET = 0x1CF0
UNSAFE_DOCK_READY_ORIGINAL = 0x98
UNSAFE_DOCK_READY_PATCHED = 0xB4
MOVIE_STOP_METHOD_CONSTANT_OFFSET = 0x25EA
MOVIE_STOP_ORIGINAL = 0x98
MOVIE_STOP_PATCHED = 0x87

PLAY_CINEMATIC_STOP_DELEGATE_OPERAND_OFFSET = 0x1ADD
PLAY_CINEMATIC_STOP_DELEGATE_ORIGINAL = 0x78  # _IntroMovieStoppedCB
PLAY_CINEMATIC_STOP_DELEGATE_PATCHED = 0xE7   # _ResetLights
STARTED_CB_FINAL_METHOD_OPERAND_OFFSET = 0x256C
STARTED_CB_FINAL_METHOD_ORIGINAL = 0xE7        # _ResetLights
STARTED_CB_FINAL_METHOD_PATCHED = 0x78         # _IntroMovieStoppedCB
PLAY_CINEMATIC_CONTEXT = bytes.fromhex("AF 77 B9 01 B5 02 AE 0A AF 0B AF 0C A2 0D 52 B9 01 AF 78 B9 01")
STARTED_CB_CONTEXT = bytes.fromhex("59 B9 01 B2 E7 4F B9 02 A2 78")

CONST_ENTRY_144_POINTER_OFFSET = 0x4A4
CONST_ENTRY_144_ORIGINAL_POINTER = 0x175C
CONST_ENTRY_144_RESET_POINTER = 0x2048
DOCK_READY_FINAL_METHOD_OFFSET = 0x1CF0
DOCK_READY_FINAL_ORIGINAL = 0x98  # _AnimatePacksIn
DOCK_READY_FINAL_PATCHED = 0xB4   # AllowIntroUserInput
ALLOW_INTERACTION_BODY_OFFSET = 0x2AB4
ALLOW_INTERACTION_BODY_ORIGINAL = bytes.fromhex("B9 01 A2 F2 34 4F")
ALLOW_INTERACTION_BODY_PATCHED = bytes.fromhex("B9 01 A2 28 74 4F")
DOCK_READY_CONTEXT = bytes.fromhex("59 B9 01 B2 98 4F B9 02 A2 98")

# v12 legacy direct-hide identity, retained only so it can be reversed safely.
V12_INTRO_STARTED_HELPER_RECEIVER_OPERAND_OFFSET = 0x2526
V12_INTRO_STARTED_HELPER_RECEIVER_ORIGINAL = 0x39
V12_INTRO_STARTED_HELPER_RECEIVER_PATCHED = 0xC7
V12_LOCAL_EVENT_HANDLER_STRING_OFFSET = 0x1B20
V12_LOCAL_EVENT_HANDLER_ORIGINAL = b"futPackSelect::_LocalEventHandler()\0"
V12_LOCAL_EVENT_HANDLER_PATCHED = b"gIndicators\0" + b"\0" * (
    len(V12_LOCAL_EVENT_HANDLER_ORIGINAL) - len(b"gIndicators\0")
)
V12_RESET_METHOD_STRING_OFFSET = 0x2048
V12_RESET_METHOD_ORIGINAL_REGION = b"ResetNetworkOperation\0\0\0"
V12_RESET_METHOD_PATCHED_REGION = b"HideLoadingIcon\0" + b"\0" * (
    len(V12_RESET_METHOD_ORIGINAL_REGION) - len(b"HideLoadingIcon\0")
)

# v13: preserve StopAudio at intro start, then release Loading exactly where
# _StartIntroAudio originally performed its own StopAudio call.
INTRO_STARTED_HELPER_RECEIVER_OPERAND_OFFSET = 0x2526
INTRO_STARTED_HELPER_RECEIVER_ORIGINAL = 0x39      # constant 57: gFutHelpers
INTRO_STARTED_HELPER_RECEIVER_PATCHED = 0xB2       # constant 178: gAudio_Helper
INTRO_STARTED_METHOD_CONSTANT_WORD_OFFSET = 0x2528
INTRO_STARTED_METHOD_CONSTANT_ORIGINAL = 0x0118    # constant 280: ResetNetworkOperation
INTRO_STARTED_METHOD_CONSTANT_PATCHED = 0x00B3     # constant 179: StopAudio
INTRO_STARTED_HELPER_CONTEXT = bytes.fromhex("59 B9 02 AF 39 A3 18 01 5D 59")

START_AUDIO_STOP_RECEIVER_OPERAND_OFFSET = 0x1E59
START_AUDIO_STOP_RECEIVER_ORIGINAL = 0xB2           # constant 178: gAudio_Helper
START_AUDIO_STOP_RECEIVER_PATCHED = 0xB1            # constant 177, repurposed to gIndicators
START_AUDIO_STOP_METHOD_OPERAND_OFFSET = 0x1E5B
START_AUDIO_STOP_METHOD_ORIGINAL = 0xB3             # constant 179: StopAudio
START_AUDIO_STOP_METHOD_PATCHED = 0xC7              # constant 199, repurposed to HideLoadingIcon
START_AUDIO_STOP_CONTEXT = bytes.fromhex("59 B9 02 AF B2 B2 B3 B9 01 AF B4")

START_INTRO_AUDIO_STRING_OFFSET = 0x1994
START_INTRO_AUDIO_ORIGINAL = b"futPackSelect::_StartIntroAudio()\0"
START_INTRO_AUDIO_PATCHED = b"gIndicators\0" + b"\0" * (
    len(START_INTRO_AUDIO_ORIGINAL) - len(b"gIndicators\0")
)
LOCAL_EVENT_HANDLER_STRING_OFFSET = 0x1B20
LOCAL_EVENT_HANDLER_ORIGINAL = b"futPackSelect::_LocalEventHandler()\0"
LOCAL_EVENT_HANDLER_PATCHED = b"HideLoadingIcon\0" + b"\0" * (
    len(LOCAL_EVENT_HANDLER_ORIGINAL) - len(b"HideLoadingIcon\0")
)

# v16: stop after the retail camera-adjustment call. The tail begins at the
# next complete opcode and ends exactly at the _AnimateIn function boundary.
ANIMATE_IN_CINEMATIC_TAIL_START = 0x1ABE
ANIMATE_IN_CINEMATIC_TAIL_END = 0x1AF9
ANIMATE_IN_CINEMATIC_TAIL_ORIGINAL = bytes.fromhex(
    "A2 76 87 00 00 00 02 00 00 00 17 "
    "B9 01 AF 77 B9 01 B5 02 AE 0A AF 0B AF 0C A2 0D 52 "
    "B9 01 AF 78 B9 01 B5 02 AE 0A AF 0B AF 0C A2 0D 52 "
    "74 75 B9 02 B5 05 AE 20 AF 21 AF 79 B2 7A"
)
ANIMATE_IN_CINEMATIC_TAIL_PATCHED = b"\x00" * len(ANIMATE_IN_CINEMATIC_TAIL_ORIGINAL)
DOCK_READY_START_CALLBACK_OPERAND_OFFSET = 0x1CF0
DOCK_READY_START_CALLBACK_ORIGINAL = 0x98  # _AnimatePacksIn
DOCK_READY_START_CALLBACK_PATCHED = 0x77   # _IntroMovieStartedCB
DOCK_READY_START_CALLBACK_CONTEXT = bytes.fromhex("59 B9 01 B2 98 4F B9 02 A2 98")

STATE_FILE = "fut-packselect-no-cinematic-dock-relay-v16-state.json"
RESTORE_STATE_FILE = "fut-packselect-no-cinematic-dock-relay-v16-restore-state.json"
REPAIR_STATE_FILE = "fut-packselect-retired-patch-repair-state.json"


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


def parse_bh_record(bh: bytes) -> dict:
    if len(bh) < 16 or bh[:4] != BH_MAGIC:
        raise ValueError("patch.bh is not a ViV4 index")
    count = struct.unpack_from(">I", bh, 8)[0]
    if RECORD_INDEX >= count:
        raise ValueError(f"patch.bh has only {count} records; expected index {RECORD_INDEX}")
    table_offset = 16 + RECORD_INDEX * 20
    offset, size, reserved, hash_hi, hash_lo = struct.unpack_from(">IIIII", bh, table_offset)
    path_hash = (hash_hi << 32) | hash_lo
    next_offset = struct.unpack_from(">I", bh, table_offset + 20)[0]
    if offset != RECORD_OFFSET:
        raise ValueError(f"futPackSelect offset changed: {offset} != {RECORD_OFFSET}")
    if size not in {RETAIL_RECORD_SIZE, 1_017_083, 1_017_086, PATCHED_RECORD_SIZE, LEGACY_V11_RECORD_SIZE, LEGACY_V5_RECORD_SIZE}:
        raise ValueError(
            "unsupported futPackSelect stored size "
            f"{size}; expected retail {RETAIL_RECORD_SIZE}, v16 {PATCHED_RECORD_SIZE}, legacy v13 1017086, "
            f"legacy v11/v7 {LEGACY_V11_RECORD_SIZE}, or legacy v5 {LEGACY_V5_RECORD_SIZE}"
        )
    if path_hash != RECORD_PATH_HASH:
        raise ValueError(f"futPackSelect path hash changed: {path_hash:016X} != {RECORD_PATH_HASH:016X}")
    if next_offset != NEXT_RECORD_OFFSET or next_offset - offset != SLOT_CAPACITY:
        raise ValueError(
            f"futPackSelect slot mismatch: next={next_offset}, capacity={next_offset-offset}, "
            f"expected next={NEXT_RECORD_OFFSET}, capacity={SLOT_CAPACITY}"
        )
    return {
        "index": RECORD_INDEX,
        "table_offset": table_offset,
        "size_field_offset": table_offset + 4,
        "offset": offset,
        "size": size,
        "reserved": reserved,
        "path_hash": path_hash,
        "next_offset": next_offset,
        "slot_capacity": next_offset - offset,
    }


def bh_with_size(bh: bytes, size: int) -> bytes:
    record = parse_bh_record(bh)
    if size not in {RETAIL_RECORD_SIZE, PATCHED_RECORD_SIZE}:
        raise ValueError(f"unsupported replacement record size {size}")
    output = bytearray(bh)
    struct.pack_into(">I", output, record["size_field_offset"], size)
    return bytes(output)


def decode_chunkzip(payload: bytes) -> tuple[bytes, dict]:
    if len(payload) < 40 or payload[:8] != CHUNKZIP_MAGIC:
        raise ValueError("futPackSelect record is not chunkzip")
    version, output_size, chunk_size, count, alignment, flag_a, flag_b, flag_c = struct.unpack_from(
        ">IIIIIIII", payload, 8
    )
    if version != 2 or alignment != 16 or flag_a or flag_b or flag_c:
        raise ValueError(
            f"unsupported chunkzip header version={version}, alignment={alignment}, flags={(flag_a, flag_b, flag_c)}"
        )
    pos = 40
    output = bytearray()
    chunks: list[dict] = []
    for index in range(count):
        if pos + 8 > len(payload):
            raise ValueError(f"truncated chunk descriptor {index}")
        descriptor_offset = pos
        stored_size, compression_type = struct.unpack_from(">II", payload, pos)
        data_start = pos + 8
        data_end = data_start + stored_size
        if data_end > len(payload):
            raise ValueError(f"truncated chunk {index}")
        stored = payload[data_start:data_end]
        if compression_type == 0:
            decoded = stored
        elif compression_type == 1:
            decoded = zlib.decompress(stored, -zlib.MAX_WBITS)
        else:
            raise ValueError(f"unsupported chunk compression type {compression_type}")
        output.extend(decoded)
        chunks.append({
            "index": index,
            "descriptor_offset": descriptor_offset,
            "stored_size": stored_size,
            "decoded_size": len(decoded),
            "compression_type": compression_type,
        })
        pos = align(data_end + 8) - 8
    if len(output) != output_size:
        raise ValueError(f"chunkzip decoded length {len(output)} != header {output_size}")
    return bytes(output), {
        "version": version,
        "output_size": output_size,
        "chunk_size": chunk_size,
        "chunk_count": count,
        "alignment": alignment,
        "chunks": chunks,
    }


def encode_chunkzip_like(decoded: bytes, layout: dict) -> bytes:
    if len(decoded) != layout["output_size"]:
        raise ValueError("cannot preserve chunkzip layout after decoded-size change")
    chunk_size = layout["chunk_size"]
    count = layout["chunk_count"]
    result = bytearray(CHUNKZIP_MAGIC + struct.pack(">IIIIIIII", 2, len(decoded), chunk_size, count, 16, 0, 0, 0))
    for index in range(count):
        chunk = decoded[index * chunk_size : min(len(decoded), (index + 1) * chunk_size)]
        compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
        compressed = compressor.compress(chunk) + compressor.flush()
        result.extend(struct.pack(">II", len(compressed), 1))
        result.extend(compressed)
        if index != count - 1:
            next_pos = align(len(result) + 8) - 8
            result.extend(b"\0" * (next_pos - len(result)))
    return bytes(result)


def parse_big(data: bytes) -> tuple[list[dict], dict]:
    if len(data) < 16 or data[:4] not in BIG_MAGICS:
        raise ValueError("decoded futPackSelect record is not BIG4/BIGF")
    count = struct.unpack_from(">I", data, 8)[0]
    header_size = struct.unpack_from(">I", data, 12)[0]
    if not 0 <= count <= 1_000_000 or not 16 <= header_size <= len(data):
        raise ValueError(f"invalid BIG header count={count}, header_size={header_size}, size={len(data)}")
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
            raise ValueError(f"BIG entry {index}:{name!r} lies outside package")
        entries.append({"index": index, "name": name, "offset": offset, "size": size})
    return entries, {
        "magic": data[:4].decode("ascii", errors="replace"),
        "entry_count": count,
        "header_size": header_size,
        "actual_size": len(data),
    }


def locate_entry(decoded: bytes, index: int, name: str, offset: int, size: int) -> tuple[dict, bytes]:
    entries, _ = parse_big(decoded)
    if index >= len(entries):
        raise ValueError(f"BIG has only {len(entries)} entries; expected entry {index}")
    entry = entries[index]
    expected = (index, name, offset, size)
    actual = (entry["index"], entry["name"], entry["offset"], entry["size"])
    if actual != expected:
        raise ValueError(f"futPackSelect BIG entry mismatch: actual={actual}, expected={expected}")
    return entry, decoded[offset : offset + size]


def locate_apt(decoded: bytes) -> tuple[dict, bytes]:
    entry, apt = locate_entry(decoded, APT_ENTRY_INDEX, APT_ENTRY_NAME, APT_ENTRY_OFFSET, APT_ENTRY_SIZE)
    if not apt.startswith(b"Apt Data:"):
        raise ValueError("verified APT entry does not contain Apt Data")
    return entry, apt


def locate_const(decoded: bytes) -> tuple[dict, bytes]:
    entry, const = locate_entry(decoded, CONST_ENTRY_INDEX, CONST_ENTRY_NAME, CONST_ENTRY_OFFSET, CONST_ENTRY_SIZE)
    if not const.startswith(b"Apt constant file"):
        raise ValueError("verified constant entry does not contain an APT constant file")
    return entry, const


def classify(decoded: bytes, stored: bytes) -> tuple[str, dict]:
    decoded_hash = sha256_bytes(decoded)
    stored_hash = sha256_bytes(stored)
    apt_entry, apt = locate_apt(decoded)
    const_entry, const = locate_const(decoded)
    apt_hash = sha256_bytes(apt)
    const_hash = sha256_bytes(const)
    identities = {
        (RETAIL_STORED_SHA256, RETAIL_DECODED_SHA256, RETAIL_APT_SHA256, RETAIL_CONST_SHA256): "retail-original",
        (UNSAFE_V4_STORED_SHA256, UNSAFE_V4_DECODED_SHA256, UNSAFE_V4_APT_SHA256, RETAIL_CONST_SHA256): "unsafe-v4-crash-patch",
        (V41_STORED_SHA256, V41_DECODED_SHA256, V41_APT_SHA256, RETAIL_CONST_SHA256): "retired-movie-stop-relay-v4.1",
        (V5_STORED_SHA256, V5_DECODED_SHA256, V5_APT_SHA256, V5_CONST_SHA256): "loading-symbol-repair-v5",
        (V6_STORED_SHA256, V6_DECODED_SHA256, V6_APT_SHA256, V6_CONST_SHA256): "cinematic-gate-relay-v6",
        (V7_STORED_SHA256, V7_DECODED_SHA256, V7_APT_SHA256, V7_CONST_SHA256): "completion-input-repair-v7",
        (V11_STORED_SHA256, V11_DECODED_SHA256, V11_APT_SHA256, V11_CONST_SHA256): "v6-baseline-loading-reset-v11",
        (V12_STORED_SHA256, V12_DECODED_SHA256, V12_APT_SHA256, V12_CONST_SHA256): "v6-baseline-direct-loading-hide-v12",
        (V13_STORED_SHA256, V13_DECODED_SHA256, V13_APT_SHA256, V13_CONST_SHA256): "v6-baseline-audio-stage-gate-release-v13",
        (V16_STORED_SHA256, V16_DECODED_SHA256, V16_APT_SHA256, V16_CONST_SHA256): "no-cinematic-dock-relay-v16",
    }
    status = identities.get((stored_hash, decoded_hash, apt_hash, const_hash), "unknown")
    return status, {
        "stored_sha256": stored_hash,
        "decoded_sha256": decoded_hash,
        "apt_sha256": apt_hash,
        "const_sha256": const_hash,
        "apt_entry": apt_entry,
        "const_entry": const_entry,
    }


def recover_retail_decoded(decoded: bytes, status: str) -> bytes:
    if status == "retail-original":
        return decoded

    output = bytearray(decoded)
    apt_entry, apt = locate_apt(decoded)
    const_entry, const = locate_const(decoded)
    recovered_apt = bytearray(apt)
    recovered_const = bytearray(const)

    if status == "unsafe-v4-crash-patch":
        if recovered_apt[UNSAFE_LOAD_DOCK_PATCH_OFFSET : UNSAFE_LOAD_DOCK_PATCH_OFFSET + len(UNSAFE_LOAD_DOCK_PATCHED)] != UNSAFE_LOAD_DOCK_PATCHED:
            raise ValueError("unsafe v4 _LoadDock bytes do not match the reviewed artifact")
        if recovered_apt[UNSAFE_DOCK_READY_METHOD_OFFSET] != UNSAFE_DOCK_READY_PATCHED:
            raise ValueError("unsafe v4 _onDockReady operand does not match the reviewed artifact")
        recovered_apt[UNSAFE_LOAD_DOCK_PATCH_OFFSET : UNSAFE_LOAD_DOCK_PATCH_OFFSET + len(UNSAFE_LOAD_DOCK_ORIGINAL)] = UNSAFE_LOAD_DOCK_ORIGINAL
        recovered_apt[UNSAFE_DOCK_READY_METHOD_OFFSET] = UNSAFE_DOCK_READY_ORIGINAL
    elif status == "retired-movie-stop-relay-v4.1":
        if recovered_apt[MOVIE_STOP_METHOD_CONSTANT_OFFSET] != MOVIE_STOP_PATCHED:
            raise ValueError("v4.1 movie-stop relay operand does not match the reviewed artifact")
        recovered_apt[MOVIE_STOP_METHOD_CONSTANT_OFFSET] = MOVIE_STOP_ORIGINAL
    elif status == "loading-symbol-repair-v5":
        if recovered_const[CONST_SYMBOL_OFFSET] != ord("s"):
            raise ValueError("v5 plural-symbol byte does not match the reviewed artifact")
        recovered_const[CONST_SYMBOL_OFFSET] = 0
    elif status == "cinematic-gate-relay-v6":
        if recovered_apt[PLAY_CINEMATIC_STOP_DELEGATE_OPERAND_OFFSET] != PLAY_CINEMATIC_STOP_DELEGATE_PATCHED:
            raise ValueError("v6 stop-delegate operand does not match")
        if recovered_apt[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] != STARTED_CB_FINAL_METHOD_PATCHED:
            raise ValueError("v6 started-callback operand does not match")
        recovered_apt[PLAY_CINEMATIC_STOP_DELEGATE_OPERAND_OFFSET] = PLAY_CINEMATIC_STOP_DELEGATE_ORIGINAL
        recovered_apt[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] = STARTED_CB_FINAL_METHOD_ORIGINAL
    elif status == "completion-input-repair-v7":
        if recovered_const[CONST_SYMBOL_OFFSET] != ord("s"):
            raise ValueError("v7 plural reset symbol byte does not match")
        if struct.unpack_from("<I", recovered_const, CONST_ENTRY_144_POINTER_OFFSET)[0] != CONST_ENTRY_144_RESET_POINTER:
            raise ValueError("v7 constant entry 144 pointer does not match")
        if recovered_apt[DOCK_READY_FINAL_METHOD_OFFSET] != DOCK_READY_FINAL_PATCHED:
            raise ValueError("v7 _onDockReady final method operand does not match")
        if recovered_apt[ALLOW_INTERACTION_BODY_OFFSET:ALLOW_INTERACTION_BODY_OFFSET + len(ALLOW_INTERACTION_BODY_PATCHED)] != ALLOW_INTERACTION_BODY_PATCHED:
            raise ValueError("v7 interaction-unlock body does not match")
        recovered_const[CONST_SYMBOL_OFFSET] = 0
        struct.pack_into("<I", recovered_const, CONST_ENTRY_144_POINTER_OFFSET, CONST_ENTRY_144_ORIGINAL_POINTER)
        recovered_apt[DOCK_READY_FINAL_METHOD_OFFSET] = DOCK_READY_FINAL_ORIGINAL
        recovered_apt[ALLOW_INTERACTION_BODY_OFFSET:ALLOW_INTERACTION_BODY_OFFSET + len(ALLOW_INTERACTION_BODY_ORIGINAL)] = ALLOW_INTERACTION_BODY_ORIGINAL
    elif status == "v6-baseline-loading-reset-v11":
        if recovered_const[CONST_SYMBOL_OFFSET] != ord("s"):
            raise ValueError("v11 plural reset symbol byte does not match")
        if struct.unpack_from("<I", recovered_const, CONST_ENTRY_144_POINTER_OFFSET)[0] != CONST_ENTRY_144_RESET_POINTER:
            raise ValueError("v11 constant entry 144 pointer does not match")
        if recovered_apt[PLAY_CINEMATIC_STOP_DELEGATE_OPERAND_OFFSET] != PLAY_CINEMATIC_STOP_DELEGATE_PATCHED:
            raise ValueError("v11/v6 stop-delegate operand does not match")
        if recovered_apt[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] != STARTED_CB_FINAL_METHOD_PATCHED:
            raise ValueError("v11/v6 started-callback operand does not match")
        recovered_const[CONST_SYMBOL_OFFSET] = 0
        struct.pack_into("<I", recovered_const, CONST_ENTRY_144_POINTER_OFFSET, CONST_ENTRY_144_ORIGINAL_POINTER)
        recovered_apt[PLAY_CINEMATIC_STOP_DELEGATE_OPERAND_OFFSET] = PLAY_CINEMATIC_STOP_DELEGATE_ORIGINAL
        recovered_apt[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] = STARTED_CB_FINAL_METHOD_ORIGINAL
    elif status == "v6-baseline-direct-loading-hide-v12":
        if recovered_apt[PLAY_CINEMATIC_STOP_DELEGATE_OPERAND_OFFSET] != PLAY_CINEMATIC_STOP_DELEGATE_PATCHED:
            raise ValueError("v12/v6 stop-delegate operand does not match")
        if recovered_apt[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] != STARTED_CB_FINAL_METHOD_PATCHED:
            raise ValueError("v12/v6 started-callback operand does not match")
        if recovered_apt[V12_INTRO_STARTED_HELPER_RECEIVER_OPERAND_OFFSET] != V12_INTRO_STARTED_HELPER_RECEIVER_PATCHED:
            raise ValueError("v12 gIndicators receiver operand does not match")
        if recovered_const[
            V12_LOCAL_EVENT_HANDLER_STRING_OFFSET:
            V12_LOCAL_EVENT_HANDLER_STRING_OFFSET + len(V12_LOCAL_EVENT_HANDLER_PATCHED)
        ] != V12_LOCAL_EVENT_HANDLER_PATCHED:
            raise ValueError("v12 gIndicators constant storage does not match")
        if recovered_const[
            V12_RESET_METHOD_STRING_OFFSET:
            V12_RESET_METHOD_STRING_OFFSET + len(V12_RESET_METHOD_PATCHED_REGION)
        ] != V12_RESET_METHOD_PATCHED_REGION:
            raise ValueError("v12 HideLoadingIcon constant storage does not match")
        recovered_apt[PLAY_CINEMATIC_STOP_DELEGATE_OPERAND_OFFSET] = PLAY_CINEMATIC_STOP_DELEGATE_ORIGINAL
        recovered_apt[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] = STARTED_CB_FINAL_METHOD_ORIGINAL
        recovered_apt[V12_INTRO_STARTED_HELPER_RECEIVER_OPERAND_OFFSET] = V12_INTRO_STARTED_HELPER_RECEIVER_ORIGINAL
        recovered_const[
            V12_LOCAL_EVENT_HANDLER_STRING_OFFSET:
            V12_LOCAL_EVENT_HANDLER_STRING_OFFSET + len(V12_LOCAL_EVENT_HANDLER_ORIGINAL)
        ] = V12_LOCAL_EVENT_HANDLER_ORIGINAL
        recovered_const[
            V12_RESET_METHOD_STRING_OFFSET:
            V12_RESET_METHOD_STRING_OFFSET + len(V12_RESET_METHOD_ORIGINAL_REGION)
        ] = V12_RESET_METHOD_ORIGINAL_REGION
    elif status == "v6-baseline-audio-stage-gate-release-v13":
        if recovered_apt[PLAY_CINEMATIC_STOP_DELEGATE_OPERAND_OFFSET] != PLAY_CINEMATIC_STOP_DELEGATE_PATCHED:
            raise ValueError("v13/v6 stop-delegate operand does not match")
        if recovered_apt[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] != STARTED_CB_FINAL_METHOD_PATCHED:
            raise ValueError("v13/v6 started-callback operand does not match")
        if recovered_apt[INTRO_STARTED_HELPER_RECEIVER_OPERAND_OFFSET] != INTRO_STARTED_HELPER_RECEIVER_PATCHED:
            raise ValueError("v13 intro-start StopAudio receiver does not match")
        if struct.unpack_from("<H", recovered_apt, INTRO_STARTED_METHOD_CONSTANT_WORD_OFFSET)[0] != INTRO_STARTED_METHOD_CONSTANT_PATCHED:
            raise ValueError("v13 intro-start StopAudio method constant does not match")
        if recovered_apt[START_AUDIO_STOP_RECEIVER_OPERAND_OFFSET] != START_AUDIO_STOP_RECEIVER_PATCHED:
            raise ValueError("v13 audio-stage gIndicators receiver does not match")
        if recovered_apt[START_AUDIO_STOP_METHOD_OPERAND_OFFSET] != START_AUDIO_STOP_METHOD_PATCHED:
            raise ValueError("v13 audio-stage HideLoadingIcon method does not match")
        if recovered_const[
            START_INTRO_AUDIO_STRING_OFFSET:
            START_INTRO_AUDIO_STRING_OFFSET + len(START_INTRO_AUDIO_PATCHED)
        ] != START_INTRO_AUDIO_PATCHED:
            raise ValueError("v13 gIndicators constant storage does not match")
        if recovered_const[
            LOCAL_EVENT_HANDLER_STRING_OFFSET:
            LOCAL_EVENT_HANDLER_STRING_OFFSET + len(LOCAL_EVENT_HANDLER_PATCHED)
        ] != LOCAL_EVENT_HANDLER_PATCHED:
            raise ValueError("v13 HideLoadingIcon constant storage does not match")
        recovered_apt[PLAY_CINEMATIC_STOP_DELEGATE_OPERAND_OFFSET] = PLAY_CINEMATIC_STOP_DELEGATE_ORIGINAL
        recovered_apt[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] = STARTED_CB_FINAL_METHOD_ORIGINAL
        recovered_apt[INTRO_STARTED_HELPER_RECEIVER_OPERAND_OFFSET] = INTRO_STARTED_HELPER_RECEIVER_ORIGINAL
        struct.pack_into("<H", recovered_apt, INTRO_STARTED_METHOD_CONSTANT_WORD_OFFSET, INTRO_STARTED_METHOD_CONSTANT_ORIGINAL)
        recovered_apt[START_AUDIO_STOP_RECEIVER_OPERAND_OFFSET] = START_AUDIO_STOP_RECEIVER_ORIGINAL
        recovered_apt[START_AUDIO_STOP_METHOD_OPERAND_OFFSET] = START_AUDIO_STOP_METHOD_ORIGINAL
        recovered_const[
            START_INTRO_AUDIO_STRING_OFFSET:
            START_INTRO_AUDIO_STRING_OFFSET + len(START_INTRO_AUDIO_ORIGINAL)
        ] = START_INTRO_AUDIO_ORIGINAL
        recovered_const[
            LOCAL_EVENT_HANDLER_STRING_OFFSET:
            LOCAL_EVENT_HANDLER_STRING_OFFSET + len(LOCAL_EVENT_HANDLER_ORIGINAL)
        ] = LOCAL_EVENT_HANDLER_ORIGINAL
    elif status == "no-cinematic-dock-relay-v16":
        if recovered_apt[
            ANIMATE_IN_CINEMATIC_TAIL_START:ANIMATE_IN_CINEMATIC_TAIL_END
        ] != ANIMATE_IN_CINEMATIC_TAIL_PATCHED:
            raise ValueError("v16 _AnimateIn cinematic tail does not match")
        if recovered_apt[DOCK_READY_START_CALLBACK_OPERAND_OFFSET] != DOCK_READY_START_CALLBACK_PATCHED:
            raise ValueError("v16 _onDockReady callback operand does not match")
        if recovered_apt[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] != STARTED_CB_FINAL_METHOD_PATCHED:
            raise ValueError("v16 started-callback completion operand does not match")
        recovered_apt[
            ANIMATE_IN_CINEMATIC_TAIL_START:ANIMATE_IN_CINEMATIC_TAIL_END
        ] = ANIMATE_IN_CINEMATIC_TAIL_ORIGINAL
        recovered_apt[DOCK_READY_START_CALLBACK_OPERAND_OFFSET] = DOCK_READY_START_CALLBACK_ORIGINAL
        recovered_apt[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] = STARTED_CB_FINAL_METHOD_ORIGINAL
    else:
        raise ValueError("refusing to reverse an unknown futPackSelect installation")

    recovered_apt = bytes(recovered_apt)
    recovered_const = bytes(recovered_const)
    if sha256_bytes(recovered_apt) != RETAIL_APT_SHA256:
        raise AssertionError("recovered APT does not match retail SHA-256")
    if sha256_bytes(recovered_const) != RETAIL_CONST_SHA256:
        raise AssertionError("recovered constant file does not match retail SHA-256")
    output[apt_entry["offset"] : apt_entry["offset"] + apt_entry["size"]] = recovered_apt
    output[const_entry["offset"] : const_entry["offset"] + const_entry["size"]] = recovered_const
    recovered = bytes(output)
    if sha256_bytes(recovered) != RETAIL_DECODED_SHA256:
        raise AssertionError("recovered decoded package does not match retail SHA-256")
    return recovered


def patch_retail_decoded(decoded: bytes) -> bytes:
    if sha256_bytes(decoded) != RETAIL_DECODED_SHA256:
        raise ValueError("refusing to patch an unverified decoded retail futPackSelect package")
    apt_entry, apt = locate_apt(decoded)
    _, const = locate_const(decoded)
    if sha256_bytes(apt) != RETAIL_APT_SHA256 or sha256_bytes(const) != RETAIL_CONST_SHA256:
        raise ValueError("retail APT/constant identities do not match")
    if apt[
        ANIMATE_IN_CINEMATIC_TAIL_START:ANIMATE_IN_CINEMATIC_TAIL_END
    ] != ANIMATE_IN_CINEMATIC_TAIL_ORIGINAL:
        raise ValueError("retail _AnimateIn cinematic tail does not match the reviewed APT")
    if apt[
        DOCK_READY_START_CALLBACK_OPERAND_OFFSET - 4:
        DOCK_READY_START_CALLBACK_OPERAND_OFFSET - 4 + len(DOCK_READY_START_CALLBACK_CONTEXT)
    ] != DOCK_READY_START_CALLBACK_CONTEXT:
        raise ValueError("retail _onDockReady final-call context does not match")
    if apt[DOCK_READY_START_CALLBACK_OPERAND_OFFSET] != DOCK_READY_START_CALLBACK_ORIGINAL:
        raise ValueError("retail _onDockReady does not end in _AnimatePacksIn")
    if apt[0x2568:0x2568 + len(STARTED_CB_CONTEXT)] != STARTED_CB_CONTEXT:
        raise ValueError("retail movie-start callback context does not match")
    if apt[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] != STARTED_CB_FINAL_METHOD_ORIGINAL:
        raise ValueError("retail movie-start callback does not end in _ResetLights")

    patched_apt = bytearray(apt)
    patched_apt[
        ANIMATE_IN_CINEMATIC_TAIL_START:ANIMATE_IN_CINEMATIC_TAIL_END
    ] = ANIMATE_IN_CINEMATIC_TAIL_PATCHED
    patched_apt[DOCK_READY_START_CALLBACK_OPERAND_OFFSET] = DOCK_READY_START_CALLBACK_PATCHED
    patched_apt[STARTED_CB_FINAL_METHOD_OPERAND_OFFSET] = STARTED_CB_FINAL_METHOD_PATCHED
    patched_apt = bytes(patched_apt)
    if sha256_bytes(patched_apt) != V16_APT_SHA256:
        raise AssertionError("patched APT does not match the reviewed v16 identity")

    output = bytearray(decoded)
    output[apt_entry["offset"]:apt_entry["offset"] + apt_entry["size"]] = patched_apt
    patched = bytes(output)
    if sha256_bytes(patched) != V16_DECODED_SHA256:
        raise AssertionError("patched BIG hash does not match the reviewed v16 artifact")
    _, patched_const = locate_const(patched)
    if sha256_bytes(patched_const) != RETAIL_CONST_SHA256:
        raise AssertionError("v16 unexpectedly changed the retail constant file")
    return patched

def read_payload(big_path: Path, size: int) -> bytes:
    with big_path.open("rb") as handle:
        handle.seek(RECORD_OFFSET)
        payload = handle.read(size)
    if len(payload) != size:
        raise ValueError(f"short read from patch.big: {len(payload)} != {size}")
    return payload


def write_slot(big_path: Path, payload: bytes) -> None:
    if len(payload) > SLOT_CAPACITY:
        raise ValueError(f"payload {len(payload)} exceeds slot capacity {SLOT_CAPACITY}")
    with big_path.open("r+b") as handle:
        handle.seek(RECORD_OFFSET)
        handle.write(payload)
        handle.write(b"\0" * (SLOT_CAPACITY - len(payload)))
        handle.flush()
        os.fsync(handle.fileno())


def inspect_install(game_root: Path) -> dict:
    big_path = game_root / "patch.big"
    bh_path = game_root / "patch.bh"
    if not big_path.is_file() or not bh_path.is_file():
        raise FileNotFoundError(f"patch.big/patch.bh not found under {game_root}")
    bh = bh_path.read_bytes()
    record = parse_bh_record(bh)
    stored = read_payload(big_path, record["size"])
    decoded, chunk_info = decode_chunkzip(stored)
    status, identity = classify(decoded, stored)
    if status == "loading-symbol-repair-v5" and record["size"] != LEGACY_V5_RECORD_SIZE:
        status = "unknown"
    if status == "cinematic-gate-relay-v6" and record["size"] != RETAIL_RECORD_SIZE:
        status = "unknown"
    if status in {"completion-input-repair-v7", "v6-baseline-loading-reset-v11"} and record["size"] != LEGACY_V11_RECORD_SIZE:
        status = "unknown"
    if status == "v6-baseline-direct-loading-hide-v12" and record["size"] != 1_017_083:
        status = "unknown"
    if status == "v6-baseline-audio-stage-gate-release-v13" and record["size"] != 1_017_086:
        status = "unknown"
    if status == "no-cinematic-dock-relay-v16" and record["size"] != PATCHED_RECORD_SIZE:
        status = "unknown"
    if status not in {
        "loading-symbol-repair-v5",
        "cinematic-gate-relay-v6",
        "completion-input-repair-v7",
        "v6-baseline-loading-reset-v11",
        "v6-baseline-direct-loading-hide-v12",
        "v6-baseline-audio-stage-gate-release-v13",
        "no-cinematic-dock-relay-v16",
        "unknown",
    } and record["size"] != RETAIL_RECORD_SIZE:
        status = "unknown"
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
        "chunkzip": chunk_info,
        "big_path": str(big_path),
        "bh_path": str(bh_path),
    }


def backup_paths(state_dir: Path) -> tuple[Path, Path, Path]:
    original_dir = state_dir / "original"
    return (
        original_dir / "patch-futpackselect.record.bin",
        original_dir / "patch.bh.bin",
        original_dir / "metadata.json",
    )


def validate_retail_pair(stored: bytes, bh: bytes) -> None:
    record = parse_bh_record(bh)
    if record["size"] != RETAIL_RECORD_SIZE or len(stored) != RETAIL_RECORD_SIZE:
        raise ValueError("rollback pair does not use the retail record size")
    decoded, _ = decode_chunkzip(stored)
    status, _ = classify(decoded, stored)
    if status != "retail-original":
        raise ValueError("rollback pair is not the verified retail futPackSelect record")


def load_verified_backup(state_dir: Path) -> tuple[bytes, bytes] | None:
    record_backup, bh_backup, metadata_path = backup_paths(state_dir)
    present = [path.exists() for path in (record_backup, bh_backup, metadata_path)]
    if not any(present):
        return None
    if not all(present):
        raise ValueError(f"incomplete rollback backup under {record_backup.parent}")
    stored = record_backup.read_bytes()
    bh = bh_backup.read_bytes()
    validate_retail_pair(stored, bh)
    return stored, bh


def synthesize_retail_pair(game_root: Path, current: dict) -> tuple[bytes, bytes, str]:
    current_bh = (game_root / "patch.bh").read_bytes()
    current_stored = read_payload(game_root / "patch.big", current["record"]["size"])
    current_decoded, layout = decode_chunkzip(current_stored)
    recovered_decoded = recover_retail_decoded(current_decoded, current["status"])
    retail_stored = encode_chunkzip_like(recovered_decoded, layout)
    if len(retail_stored) != RETAIL_RECORD_SIZE or sha256_bytes(retail_stored) != RETAIL_STORED_SHA256:
        raise AssertionError("deterministic reversal did not recover the retail stored record")
    retail_bh = bh_with_size(current_bh, RETAIL_RECORD_SIZE)
    validate_retail_pair(retail_stored, retail_bh)
    return retail_stored, retail_bh, "deterministic byte-for-byte reversal"


def ensure_retail_backup(game_root: Path, state_dir: Path, current: dict) -> dict:
    existing = load_verified_backup(state_dir)
    record_backup, bh_backup, metadata_path = backup_paths(state_dir)
    if existing is not None:
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    if current["status"] == "retail-original":
        retail_stored = read_payload(game_root / "patch.big", RETAIL_RECORD_SIZE)
        retail_bh = (game_root / "patch.bh").read_bytes()
        source = "live verified retail archive"
    elif current["status"] in {"unsafe-v4-crash-patch", "retired-movie-stop-relay-v4.1", "loading-symbol-repair-v5", "cinematic-gate-relay-v6", "completion-input-repair-v7", "v6-baseline-loading-reset-v11", "v6-baseline-direct-loading-hide-v12", "v6-baseline-audio-stage-gate-release-v13", "no-cinematic-dock-relay-v16"}:
        retail_stored, retail_bh, source = synthesize_retail_pair(game_root, current)
    else:
        raise ValueError("cannot create a rollback backup from an unknown futPackSelect installation")

    record_backup.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(record_backup, retail_stored)
    atomic_write(bh_backup, retail_bh)
    metadata = {
        "source": source,
        "record_backup": str(record_backup),
        "bh_backup": str(bh_backup),
        "record_index": RECORD_INDEX,
        "record_offset": RECORD_OFFSET,
        "record_size": RETAIL_RECORD_SIZE,
        "slot_capacity": SLOT_CAPACITY,
        "path_hash": f"{RECORD_PATH_HASH:016X}",
        "retail_stored_sha256": RETAIL_STORED_SHA256,
        "retail_decoded_sha256": RETAIL_DECODED_SHA256,
        "retail_apt_sha256": RETAIL_APT_SHA256,
        "retail_const_sha256": RETAIL_CONST_SHA256,
    }
    atomic_write(metadata_path, (json.dumps(metadata, indent=2) + "\n").encode("utf-8"))
    return metadata


def install_pair(game_root: Path, stored: bytes, bh: bytes, expected_status: str) -> dict:
    write_slot(game_root / "patch.big", stored)
    atomic_write(game_root / "patch.bh", bh)
    installed = inspect_install(game_root)
    if installed["status"] != expected_status:
        raise AssertionError(f"post-write verification expected {expected_status}, got {installed['status']}")
    return installed


def restore_retail(game_root: Path, state_dir: Path, current: dict) -> tuple[str, dict]:
    if current["status"] == "retail-original":
        return "already retail original", current
    if current["status"] not in {"unsafe-v4-crash-patch", "retired-movie-stop-relay-v4.1", "loading-symbol-repair-v5", "cinematic-gate-relay-v6", "completion-input-repair-v7", "v6-baseline-loading-reset-v11", "v6-baseline-direct-loading-hide-v12", "v6-baseline-audio-stage-gate-release-v13", "no-cinematic-dock-relay-v16"}:
        raise ValueError(
            "unknown futPackSelect identity; refusing to overwrite it. "
            f"stored={current['stored_sha256']} apt={current['apt_sha256']} const={current['const_sha256']}"
        )
    backup = load_verified_backup(state_dir)
    if backup is not None:
        retail_stored, retail_bh = backup
        method = "verified rollback backup"
    else:
        retail_stored, retail_bh, method = synthesize_retail_pair(game_root, current)
    installed = install_pair(game_root, retail_stored, retail_bh, "retail-original")
    return method, installed


def repair_retired(game_root: Path, state_dir: Path) -> dict:
    current = inspect_install(game_root)
    recognized_old = {
        "unsafe-v4-crash-patch",
        "retired-movie-stop-relay-v4.1",
        "loading-symbol-repair-v5",
        "cinematic-gate-relay-v6",
        "completion-input-repair-v7",
        "v6-baseline-loading-reset-v11",
        "v6-baseline-direct-loading-hide-v12",
        "v6-baseline-audio-stage-gate-release-v13",
    }
    if current["status"] in recognized_old:
        ensure_retail_backup(game_root, state_dir, current)
        method, installed = restore_retail(game_root, state_dir, current)
        changed = True
        reason = "removed recognized v4-v13 captain experiment before v16"
    elif current["status"] == "retail-original":
        method = "no retired experiment installed"
        installed = current
        changed = False
        reason = "installation is verified retail"
    elif current["status"] == "no-cinematic-dock-relay-v16":
        method = "v16 already installed"
        installed = current
        changed = False
        reason = "reviewed v16 is already installed"
    else:
        raise ValueError("unknown futPackSelect identity; refusing to modify it")
    result = {"action":"repair-retired","changed":changed,"reason":reason,"method":method,"install":installed}
    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(state_dir / REPAIR_STATE_FILE, (json.dumps(result, indent=2) + "\n").encode("utf-8"))
    return result

def apply_patch(game_root: Path, state_dir: Path) -> dict:
    repair = repair_retired(game_root, state_dir)
    current = inspect_install(game_root)
    if current["status"] == "no-cinematic-dock-relay-v16":
        return {
            "action":"apply",
            "changed":False,
            "reason":"no-cinematic dock relay v16 already installed",
            "repair":repair,
            "install":current,
        }
    if current["status"] != "retail-original":
        raise ValueError("futPackSelect is not verified retail after retired-patch repair")
    backup = ensure_retail_backup(game_root, state_dir, current)
    retail_stored = read_payload(game_root / "patch.big", RETAIL_RECORD_SIZE)
    retail_decoded, layout = decode_chunkzip(retail_stored)
    patched_decoded = patch_retail_decoded(retail_decoded)
    patched_stored = encode_chunkzip_like(patched_decoded, layout)
    if len(patched_stored) != PATCHED_RECORD_SIZE or sha256_bytes(patched_stored) != V16_STORED_SHA256:
        raise AssertionError("v16 stored record identity mismatch")
    patched_bh = bh_with_size((game_root / "patch.bh").read_bytes(), PATCHED_RECORD_SIZE)
    installed = install_pair(game_root, patched_stored, patched_bh, "no-cinematic-dock-relay-v16")
    result = {
        "action":"apply","changed":True,
        "controlled_changes":[
            "_AnimateIn returns immediately after retail camera adjustment, before CinematicManager.PlayCinematic",
            "the natural _onDockReady final call enters the existing _IntroMovieStartedCB",
            "the existing started callback completes through _IntroMovieStoppedCB and retail _AnimatePacksIn",
            "Martin Tyler PlayAudio, Skip Audio/InterruptAudio, input, RetrievePack and BuildSquad remain retail",
            "no native callback, runtime event rewrite, input memory, captain, club, squad, inventory, coins, or items are written"
        ],
        "repair":repair,"backup":backup,"install":installed,
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(state_dir / STATE_FILE, (json.dumps(result, indent=2) + "\n").encode("utf-8"))
    return result


def restore_patch(game_root: Path, state_dir: Path) -> dict:
    current = inspect_install(game_root)
    if current["status"] == "retail-original":
        method = "already retail original"
        installed = current
        changed = False
    else:
        method, installed = restore_retail(game_root, state_dir, current)
        changed = True
    result = {"action": "restore", "changed": changed, "method": method, "install": installed}
    state_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(state_dir / RESTORE_STATE_FILE, (json.dumps(result, indent=2) + "\n").encode("utf-8"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply/restore/verify FIFA 14 futPackSelect no-cinematic dock relay v16")
    parser.add_argument("--game-root", required=True, type=Path)
    parser.add_argument("--state-dir", type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--repair-unsafe", action="store_true", help="restore all recognized retired v4-v7 experiments")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--restore", action="store_true")
    action.add_argument("--verify", action="store_true")
    action.add_argument("--inspect", action="store_true")
    args = parser.parse_args()

    game_root = args.game_root.expanduser().resolve()
    state_dir = args.state_dir.expanduser().resolve() if args.state_dir else (Path(__file__).resolve().parents[1] / "artifacts" / "fut-packselect-ready")
    try:
        if args.repair_unsafe:
            result = repair_retired(game_root, state_dir)
        elif args.apply:
            result = apply_patch(game_root, state_dir)
        elif args.restore:
            result = restore_patch(game_root, state_dir)
        else:
            install = inspect_install(game_root)
            result = {"action": "verify" if args.verify else "inspect", "install": install}
            if args.verify and install["status"] != "no-cinematic-dock-relay-v16":
                raise ValueError("no-cinematic dock relay v16 is not installed; status=" + install["status"])
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error), "type": type(error).__name__}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
