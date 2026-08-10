#!/usr/bin/env python3
"""Restore FIFA 14's retail captain selector and satisfy its missing intro VP6.

This deliberately does not modify ActionScript.  The retail futPackSelect screen
asks CinematicManager to play:

    data/ui/external/ion_fut/artAssets/movies/futIntroVideo.vp6

The installer finds a valid EA VP6 from the local FIFA installation (loose file
first, then ViV4 BIG/BH archives), or downloads the tiny public FFmpeg EA-VP6
sample as a final fallback.  The chosen file is installed at the exact retail
path so the game's own movie-start/movie-stop delegates can release the dock,
commentary and input state naturally.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import os
from pathlib import Path
import shutil
import struct
import tempfile
import urllib.request
import zlib

TARGET_RELATIVE = Path("data/ui/external/ion_fut/artAssets/movies/futIntroVideo.vp6")
STATE_FILE = "fut-intro-vp6-v19-state.json"
BACKUP_FILE = "futIntroVideo.before-v19.bin"
DOWNLOAD_FILE = "ffmpeg-blank.vp6"
DOWNLOAD_URLS = (
    "https://samples.ffmpeg.org/game-formats/ea-vp6/NFSU2/blank.vp6",
    "http://samples.ffmpeg.org/game-formats/ea-vp6/NFSU2/blank.vp6",
    "https://samples.ffmpeg.org/game-formats/ea-vp6/NFSU2/THX_logo.vp6",
)
BH_MAGIC = b"ViV4"
CHUNKZIP_MAGIC = b"chunkzip"
EA_PROBE_TAGS = (
    b"ISNh", b"SCHl", b"SEAD", b"SHEN", b"kVGT", b"MADk", b"MPCh",
    b"MVhd", b"MVIh", b"AVP6",
)
VP6_HEADER_TAGS = (b"MVhd", b"AVhd", b"AVP6")
VP6_FRAME_TAGS = (b"MV0K", b"MV0F", b"AV0K", b"AV0F", b"AVP6")
VP6_TAGS = VP6_HEADER_TAGS + VP6_FRAME_TAGS
MAX_ARCHIVE_DECODE = 128 * 1024 * 1024
KNOWN_LOOSE = (
    "data/movies/POW.vp6",
    "data/movies/POW_fr.vp6",
    "data/movies/FIFA_ATTR.vp6",
    "data/movies/bootflowintro_MARKER.vp6",
    "data/movies/bootflowintro.vp6",
    "data/movies/bootflowoutro.vp6",
    "data/movies/GTN_TUTORIAL.vp6",
    "data/movies/GTN_TUTORIAL_FR.vp6",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _ea_chunk_size(data: bytes) -> tuple[int | None, str | None]:
    if len(data) < 8:
        return None, None
    little = int.from_bytes(data[4:8], "little")
    if 8 <= little <= 0xFFFFF:
        return little, "little"
    big = int.from_bytes(data[4:8], "big")
    if 8 <= big <= 0xFFFFF:
        return big, "big"
    return None, None


def vp6_info_bytes(data: bytes, source: str = "") -> dict:
    prefix = data[: min(len(data), 512 * 1024)]
    first_tag = data[:4] if len(data) >= 4 else b""
    first_chunk_size, size_endian = _ea_chunk_size(data)
    tags = [tag.decode("ascii") for tag in VP6_TAGS if tag in prefix]
    has_vp6_header = any(tag in prefix for tag in VP6_HEADER_TAGS)
    has_vp6_frame = any(tag in prefix for tag in VP6_FRAME_TAGS)
    # Match FFmpeg's EA demuxer probe rather than assuming every EA VP6 starts
    # with SCHl. NFS-era VP6 files commonly begin directly with MVhd or AVP6.
    valid = (
        len(data) >= 16
        and first_tag in EA_PROBE_TAGS
        and first_chunk_size is not None
        and first_chunk_size <= len(data)
        and has_vp6_header
        and has_vp6_frame
    )
    return {
        "source": source,
        "valid": valid,
        "size": len(data),
        "sha256": sha256_bytes(data),
        "magic": data[:8].hex() if data else "",
        "first_tag": first_tag.decode("ascii", "replace") if first_tag else "",
        "first_chunk_size": first_chunk_size,
        "size_endian": size_endian,
        "tags": tags,
    }


def vp6_info_file(path: Path) -> dict:
    if not path.is_file():
        return {"source": str(path), "valid": False, "reason": "not a file"}
    data = path.read_bytes()
    return vp6_info_bytes(data, str(path))


def align(value: int, boundary: int = 16) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def decode_chunkzip(payload: bytes) -> bytes:
    if len(payload) < 40 or payload[:8] != CHUNKZIP_MAGIC:
        raise ValueError("not chunkzip")
    version, output_size, chunk_size, count, alignment, flag_a, flag_b, flag_c = struct.unpack_from(
        ">IIIIIIII", payload, 8
    )
    if version != 2 or alignment != 16 or flag_a or flag_b or flag_c:
        raise ValueError("unsupported chunkzip layout")
    if output_size > MAX_ARCHIVE_DECODE:
        raise ValueError("decoded record exceeds scan limit")
    pos = 40
    output = bytearray()
    for _ in range(count):
        if pos + 8 > len(payload):
            raise ValueError("truncated chunk descriptor")
        stored_size, compression_type = struct.unpack_from(">II", payload, pos)
        start = pos + 8
        end = start + stored_size
        if end > len(payload):
            raise ValueError("truncated chunk")
        stored = payload[start:end]
        if compression_type == 0:
            decoded = stored
        elif compression_type == 1:
            decoded = zlib.decompress(stored, -zlib.MAX_WBITS)
        else:
            raise ValueError("unsupported chunk compression")
        output.extend(decoded)
        if len(output) > MAX_ARCHIVE_DECODE:
            raise ValueError("decoded record exceeds scan limit")
        pos = align(end + 8) - 8
    if len(output) != output_size:
        raise ValueError("chunkzip output-size mismatch")
    return bytes(output)


def local_candidates(game_root: Path, target: Path, explicit: Path | None) -> list[tuple[str, Path]]:
    seen: set[Path] = set()
    output: list[tuple[str, Path]] = []

    def add(kind: str, path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in seen or resolved == target.resolve() or not resolved.is_file():
            return
        seen.add(resolved)
        output.append((kind, resolved))

    if explicit is not None:
        add("explicit", explicit)
    for relative in KNOWN_LOOSE:
        add("known-loose", game_root / Path(relative))
    data_root = game_root / "data"
    if data_root.is_dir():
        try:
            for path in data_root.rglob("*.vp6"):
                add("loose-scan", path)
        except OSError:
            pass
    output.sort(key=lambda item: (0 if item[0] == "explicit" else 1, item[1].stat().st_size, str(item[1]).lower()))
    return output


def iter_bh_records(bh_path: Path):
    data = bh_path.read_bytes()
    if len(data) < 16 or data[:4] != BH_MAGIC:
        return
    count = struct.unpack_from(">I", data, 8)[0]
    if count <= 0 or 16 + count * 20 > len(data):
        return
    for index in range(count):
        pos = 16 + index * 20
        offset, size, reserved, hash_hi, hash_lo = struct.unpack_from(">IIIII", data, pos)
        if size <= 0:
            continue
        yield {
            "index": index,
            "offset": offset,
            "size": size,
            "reserved": reserved,
            "path_hash": (hash_hi << 32) | hash_lo,
        }


def archive_candidates(game_root: Path):
    pairs: list[tuple[Path, Path]] = []
    for bh in sorted(game_root.glob("*.bh")):
        big = bh.with_suffix(".big")
        if big.is_file():
            pairs.append((bh, big))
    for bh, big in pairs:
        try:
            with big.open("rb") as handle:
                with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                    length = len(mapped)
                    for record in iter_bh_records(bh) or ():
                        offset = record["offset"]
                        size = record["size"]
                        if offset < 0 or size < 32 or offset + size > length:
                            continue
                        prefix = mapped[offset : offset + min(size, 512 * 1024)]
                        decoded = None
                        if prefix[:4] in EA_PROBE_TAGS and any(tag in prefix for tag in VP6_HEADER_TAGS):
                            candidate = bytes(mapped[offset : offset + size])
                            if vp6_info_bytes(candidate).get("valid"):
                                decoded = candidate
                        elif prefix[:8] == CHUNKZIP_MAGIC and size <= MAX_ARCHIVE_DECODE:
                            try:
                                payload = bytes(mapped[offset : offset + size])
                                candidate = decode_chunkzip(payload)
                                info = vp6_info_bytes(candidate)
                                if info["valid"]:
                                    decoded = candidate
                            except (ValueError, zlib.error):
                                pass
                        if decoded is not None:
                            info = vp6_info_bytes(decoded, f"{big.name}:record:{record['index']}:hash:{record['path_hash']:016X}")
                            if info["valid"]:
                                yield info, decoded
        except (OSError, ValueError):
            continue


def download_candidate(cache_path: Path) -> tuple[dict, bytes]:
    failures: list[dict] = []
    for url in DOWNLOAD_URLS:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 FIFA14-FUT-retail-cinematic-recovery-v20",
                "Accept": "application/octet-stream,*/*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                status = getattr(response, "status", None)
                final_url = response.geturl()
                content_type = response.headers.get("Content-Type", "")
                data = response.read(MAX_ARCHIVE_DECODE + 1)
            if len(data) > MAX_ARCHIVE_DECODE:
                raise ValueError("downloaded VP6 exceeds safety limit")
            info = vp6_info_bytes(data, final_url)
            info.update({
                "requested_url": url,
                "http_status": status,
                "content_type": content_type,
            })
            if info["valid"]:
                atomic_write(cache_path, data)
                return info, data
            failures.append({
                **info,
                "reason": "response did not pass the EA VP6 structural probe",
                "first_ascii": data[:96].decode("ascii", "replace"),
            })
        except Exception as error:
            failures.append({
                "requested_url": url,
                "reason": str(error),
                "type": type(error).__name__,
            })
    detail = json.dumps(failures, ensure_ascii=False)
    raise ValueError("all VP6 download fallbacks failed validation: " + detail)


def choose_candidate(game_root: Path, target: Path, explicit: Path | None, state_dir: Path, allow_download: bool) -> tuple[dict, bytes]:
    rejected = []
    for kind, path in local_candidates(game_root, target, explicit):
        info = vp6_info_file(path)
        info["candidate_kind"] = kind
        if info.get("valid"):
            return info, path.read_bytes()
        rejected.append(info)

    archive_found = list(archive_candidates(game_root))
    if archive_found:
        archive_found.sort(key=lambda item: (item[0]["size"], item[0]["source"]))
        info, data = archive_found[0]
        info["candidate_kind"] = "viv4-archive"
        return info, data

    if allow_download:
        info, data = download_candidate(state_dir / DOWNLOAD_FILE)
        info["candidate_kind"] = "ffmpeg-public-sample"
        return info, data

    detail = "; ".join(f"{item.get('source')}: {item.get('reason', 'invalid EA VP6')}" for item in rejected[:8])
    raise FileNotFoundError(
        "No valid local EA VP6 was found and downloading was disabled. "
        "Pass --vp6-source with a valid FIFA/EA .vp6 file. " + detail
    )


def load_state(state_dir: Path) -> dict | None:
    path = state_dir / STATE_FILE
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def install(game_root: Path, state_dir: Path, explicit: Path | None, allow_download: bool) -> dict:
    target = game_root / TARGET_RELATIVE
    existing = vp6_info_file(target)
    state_dir.mkdir(parents=True, exist_ok=True)
    if existing.get("valid"):
        result = {
            "action": "install",
            "changed": False,
            "method": "retail target already contains a valid EA VP6",
            "target": existing,
            "target_relative": TARGET_RELATIVE.as_posix(),
        }
        atomic_write(state_dir / STATE_FILE, (json.dumps(result, indent=2) + "\n").encode("utf-8"))
        return result

    prior_exists = target.is_file()
    prior_hash = sha256_file(target) if prior_exists else None
    prior_size = target.stat().st_size if prior_exists else None
    if prior_exists:
        atomic_write(state_dir / BACKUP_FILE, target.read_bytes())

    selected, data = choose_candidate(game_root, target, explicit, state_dir, allow_download)
    atomic_write(target, data)
    installed = vp6_info_file(target)
    if not installed.get("valid") or installed.get("sha256") != selected.get("sha256"):
        raise AssertionError("installed VP6 failed post-write validation")

    result = {
        "action": "install",
        "changed": True,
        "method": "installed retail cinematic asset without modifying futPackSelect APT",
        "target_relative": TARGET_RELATIVE.as_posix(),
        "target": installed,
        "selected_source": selected,
        "prior": {"existed": prior_exists, "sha256": prior_hash, "size": prior_size},
        "backup": str(state_dir / BACKUP_FILE) if prior_exists else None,
    }
    atomic_write(state_dir / STATE_FILE, (json.dumps(result, indent=2) + "\n").encode("utf-8"))
    return result


def verify(game_root: Path) -> dict:
    target = game_root / TARGET_RELATIVE
    info = vp6_info_file(target)
    if not info.get("valid"):
        raise ValueError(f"retail intro VP6 is missing or invalid: {target}")
    return {
        "action": "verify",
        "target_relative": TARGET_RELATIVE.as_posix(),
        "target": info,
        "apt_mutation": False,
    }


def restore(game_root: Path, state_dir: Path) -> dict:
    target = game_root / TARGET_RELATIVE
    state = load_state(state_dir)
    if state is None:
        return {"action": "restore", "changed": False, "method": "no v19 asset state exists"}
    if not state.get("changed"):
        return {"action": "restore", "changed": False, "method": "v19 did not replace the pre-existing valid target"}

    current_hash = sha256_file(target) if target.is_file() else None
    expected_hash = state.get("target", {}).get("sha256")
    if current_hash is not None and expected_hash and current_hash != expected_hash:
        raise ValueError("target changed after v19 installation; refusing destructive restore")

    prior = state.get("prior", {})
    if prior.get("existed"):
        backup = state_dir / BACKUP_FILE
        if not backup.is_file() or sha256_file(backup) != prior.get("sha256"):
            raise ValueError("verified pre-v19 VP6 backup is unavailable")
        atomic_write(target, backup.read_bytes())
        method = "restored pre-v19 target"
    else:
        target.unlink(missing_ok=True)
        method = "removed v19-created target"
    return {"action": "restore", "changed": True, "method": method, "target": str(target)}


def inspect(game_root: Path, state_dir: Path, explicit: Path | None) -> dict:
    target = game_root / TARGET_RELATIVE
    local = []
    for kind, path in local_candidates(game_root, target, explicit):
        info = vp6_info_file(path)
        info["candidate_kind"] = kind
        local.append(info)
    archive = []
    for info, _data in archive_candidates(game_root):
        archive.append(info)
        if len(archive) >= 20:
            break
    return {
        "action": "inspect",
        "target": vp6_info_file(target),
        "local_candidates": local,
        "archive_candidates": archive,
        "state": load_state(state_dir),
        "download_fallbacks": list(DOWNLOAD_URLS),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="FIFA 14 retail captain cinematic VP6 recovery v20")
    parser.add_argument("--game-root", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--vp6-source", type=Path)
    parser.add_argument("--no-download", action="store_true")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--install", action="store_true")
    actions.add_argument("--verify", action="store_true")
    actions.add_argument("--restore", action="store_true")
    actions.add_argument("--inspect", action="store_true")
    args = parser.parse_args()

    game_root = args.game_root.expanduser().resolve()
    state_dir = args.state_dir.expanduser().resolve()
    explicit = args.vp6_source.expanduser().resolve() if args.vp6_source else None
    try:
        if args.install:
            result = install(game_root, state_dir, explicit, not args.no_download)
        elif args.verify:
            result = verify(game_root)
        elif args.restore:
            result = restore(game_root, state_dir)
        else:
            result = inspect(game_root, state_dir, explicit)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error), "type": type(error).__name__}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
