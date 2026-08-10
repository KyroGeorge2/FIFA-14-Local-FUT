#!/usr/bin/env python3
"""FIFA 14 PC FUT Legend client-database injector (v2.40.22).

The FIFA 14 PC client resolves FUT player identities from cards_ng_db.db. This
utility can safely expand the fixed-capacity players table when the retail DB has
no spare rows, then rewrites/relocates the containing ViV4 record while updating
its .bh index. This is required by the user's November-2013 patch DB, whose
players table is completely full.

Safety properties:
* refuses compressed/unknown archive payloads;
* expands tables only by inserting whole fixed-size records and updates every later table offset;
* creates a byte-for-byte archive backup before the first write;
* verifies the expanded DB in memory and reparses the patched archive on disk;
* supports --restore;
* no Legend is considered client-ready unless all 42 IDs verify.

The low-level FIFA DB/BIG layout follows the public DBM Studio implementation
and the classic EA BIGF/BIG4 format.  No external Python packages are required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import zlib
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DB_HEADER = b"DB\x00\x08\x00\x00\x00\x00"
NO_COMPRESSED_STRINGS = 0xFFFFFFFF
BACKUP_SUFFIX = ".v24020-legends.bak"
LEGEND_TEAM_ID = 112658
BH_MAGIC = b"ViV4"
CHUNKZIP_MAGIC = b"chunkzip"
NAME_FIELDS = ("firstnameid", "lastnameid", "commonnameid", "playerjerseynameid")
CRITICAL_FIELDS = ("playerid",)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest().upper()


def is_compressed_payload(data: bytes) -> bool:
    if len(data) < 8:
        return False
    sig = data[:8]
    return sig.startswith(b"chunk") or sig.startswith(b"EASF") or data[:2] == b"\x10\xfb"


@dataclass
class BigEntry:
    name: str
    offset: int
    size: int
    compressed: bool


@dataclass(frozen=True)
class BhRecord:
    index: int
    offset: int
    size: int
    reserved: int
    path_hash: int
    table_offset: int


@dataclass
class ArchiveDbCandidate:
    big_path: Path
    bh_path: Path | None
    record: BhRecord | None
    stored: bytes
    decoded: bytes
    chunk_info: dict[str, Any] | None
    entry: BigEntry
    source_kind: str


def align(value: int, boundary: int = 16) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def parse_bh(data: bytes) -> list[BhRecord]:
    if len(data) < 16 or data[:4] != BH_MAGIC:
        raise ValueError("archive BH is not a ViV4 index")
    count = struct.unpack_from(">I", data, 8)[0]
    if 16 + count * 20 > len(data):
        raise ValueError("truncated ViV4 BH record table")
    out: list[BhRecord] = []
    pos = 16
    for index in range(count):
        table_offset = pos
        offset, size, reserved, hi, lo = struct.unpack_from(">IIIII", data, pos)
        pos += 20
        out.append(BhRecord(index, offset, size, reserved, (hi << 32) | lo, table_offset))
    return out


def decode_chunkzip(payload: bytes) -> tuple[bytes, dict[str, Any]]:
    if len(payload) < 40 or payload[:8] != CHUNKZIP_MAGIC:
        raise ValueError("not chunkzip")
    version, output_size, chunk_size, count, alignment, a, b, c = struct.unpack_from(">IIIIIIII", payload, 8)
    if version != 2 or alignment not in (8, 16) or a or b or c or count <= 0 or count > 4096:
        raise ValueError("unsupported chunkzip layout")
    pos = 40
    output = bytearray()
    chunks: list[dict[str, int]] = []
    for index in range(count):
        if pos + 8 > len(payload):
            raise ValueError("truncated chunkzip descriptor")
        stored_size, compression_type = struct.unpack_from(">II", payload, pos)
        start = pos + 8
        end = start + stored_size
        if end > len(payload):
            raise ValueError("truncated chunkzip data")
        raw = payload[start:end]
        if compression_type == 0:
            decoded = raw
        elif compression_type == 1:
            decoded = zlib.decompress(raw, -zlib.MAX_WBITS)
        else:
            raise ValueError(f"unsupported chunkzip compression type {compression_type}")
        output.extend(decoded)
        chunks.append({"index": index, "stored_size": stored_size, "decoded_size": len(decoded), "compression_type": compression_type})
        pos = align(end + 8, alignment) - 8
    if len(output) != output_size:
        raise ValueError(f"chunkzip decoded size {len(output)} != {output_size}")
    return bytes(output), {"version": version, "output_size": output_size, "chunk_size": chunk_size, "chunk_count": count, "alignment": alignment, "chunks": chunks}


def encode_chunkzip(decoded: bytes, info: dict[str, Any]) -> bytes:
    chunk_size = int(info.get("chunk_size") or 262144)
    alignment = int(info.get("alignment") or 16)
    original_chunks = list(info.get("chunks") or [])
    parts: list[bytes] = []
    # Preserve the original decoded chunk boundaries when the payload size is
    # unchanged. If the FIFA DB grew, rebuild the chunk list using the archive's
    # declared chunk size; chunkzip explicitly stores output size + chunk count.
    if len(decoded) == int(info.get("output_size", len(decoded))):
        cursor = 0
        for chunk in original_chunks:
            length = int(chunk.get("decoded_size", 0))
            parts.append(decoded[cursor:cursor+length])
            cursor += length
        if cursor != len(decoded):
            parts = []
    if not parts:
        parts = [decoded[i:i+chunk_size] for i in range(0, len(decoded), chunk_size)] or [b""]
    result = bytearray(CHUNKZIP_MAGIC + struct.pack(">IIIIIIII", 2, len(decoded), chunk_size, len(parts), alignment, 0, 0, 0))
    for index, part in enumerate(parts):
        preferred = int(original_chunks[index].get("compression_type", 1)) if index < len(original_chunks) else 1
        if preferred == 0:
            stored = part
            ctype = 0
        else:
            comp = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
            stored = comp.compress(part) + comp.flush()
            ctype = 1
        result.extend(struct.pack(">II", len(stored), ctype))
        result.extend(stored)
        if index != len(parts)-1:
            next_pos = align(len(result) + 8, alignment) - 8
            if next_pos > len(result):
                result.extend(b"\x00" * (next_pos-len(result)))
    return bytes(result)


def physical_capacity(records: list[BhRecord], record: BhRecord, big_size: int) -> int:
    next_offsets = [row.offset for row in records if row.offset > record.offset]
    next_offset = min(next_offsets) if next_offsets else big_size
    capacity = next_offset - record.offset
    if capacity < record.size:
        raise ValueError("BH record physical capacity is smaller than stored size")
    return capacity


def update_bh_size(bh: bytes, record: BhRecord, new_size: int) -> bytes:
    out = bytearray(bh)
    struct.pack_into(">I", out, record.table_offset + 4, new_size)
    return bytes(out)


def update_bh_location_and_size(bh: bytes, record: BhRecord, new_offset: int, new_size: int) -> bytes:
    out = bytearray(bh)
    struct.pack_into(">I", out, record.table_offset, new_offset)
    struct.pack_into(">I", out, record.table_offset + 4, new_size)
    return bytes(out)


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


def write_record_slot(big_path: Path, offset: int, capacity: int, payload: bytes, clear_to: int) -> None:
    if len(payload) > capacity:
        raise ValueError(f"patched archive record {len(payload)} exceeds physical slot capacity {capacity}")
    with big_path.open("r+b") as handle:
        handle.seek(offset)
        handle.write(payload)
        if len(payload) < clear_to:
            handle.write(b"\x00" * (clear_to-len(payload)))
        handle.flush(); os.fsync(handle.fileno())


def parse_big(data: bytes) -> list[BigEntry]:
    if len(data) < 16 or data[:4] not in (b"BIGF", b"BIG4"):
        raise ValueError("not a BIGF/BIG4 archive")
    count = struct.unpack_from(">I", data, 8)[0]
    cursor = 16
    entries: list[BigEntry] = []
    for _ in range(count):
        if cursor + 8 > len(data):
            raise ValueError("truncated BIG directory")
        offset, size = struct.unpack_from(">II", data, cursor)
        cursor += 8
        end = data.find(b"\x00", cursor)
        if end < 0:
            raise ValueError("unterminated BIG entry name")
        name = data[cursor:end].decode("utf-8", errors="replace")
        cursor = end + 1
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError(f"BIG entry outside archive: {name}")
        payload = data[offset : offset + size]
        entries.append(BigEntry(name=name, offset=offset, size=size, compressed=is_compressed_payload(payload)))
    return entries


def entry_basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1].lower()


def find_entries(entries: Iterable[BigEntry], basename: str) -> list[BigEntry]:
    wanted = basename.lower()
    return [entry for entry in entries if entry_basename(entry.name) == wanted]


def extract_entry(archive: bytes, entry: BigEntry) -> bytes:
    return archive[entry.offset : entry.offset + entry.size]


@dataclass
class XmlField:
    name: str
    short_name: str
    range_low: int = 0
    range_high: int = -1


@dataclass
class XmlTable:
    name: str
    short_name: str
    fields: dict[str, XmlField]
    by_short: dict[str, XmlField]


def _normalized_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _node_value(node: ET.Element, keys: tuple[str, ...]) -> str | None:
    lowered = {key.lower() for key in keys}
    for key, value in node.attrib.items():
        if key.lower() in lowered and value is not None:
            return str(value).strip()
    for child in list(node):
        if _normalized_tag(child.tag) in lowered and child.text:
            return child.text.strip()
    return None


def _to_int(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value, 0)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def parse_descriptor(xml_bytes: bytes) -> dict[str, XmlTable]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"invalid FIFA DB descriptor XML: {exc}") from exc
    tables: dict[str, XmlTable] = {}
    for node in root.iter():
        tag = _normalized_tag(node.tag)
        if "field" in tag:
            continue
        name = _node_value(node, ("name", "tablename", "tableName", "Name"))
        short = _node_value(node, ("shortname", "shortName", "ShortName"))
        if not name or not short or len(short) != 4:
            continue
        fields: dict[str, XmlField] = {}
        by_short: dict[str, XmlField] = {}
        for child in node.iter():
            ctag = _normalized_tag(child.tag)
            if "field" not in ctag:
                continue
            fname = _node_value(child, ("name", "fieldname", "fieldName", "Name"))
            fshort = _node_value(child, ("shortname", "shortName", "ShortName"))
            if not fname or not fshort or len(fshort) != 4:
                continue
            field = XmlField(
                name=fname,
                short_name=fshort,
                range_low=_to_int(_node_value(child, ("rangelow", "rangeLow", "RangeLow", "min", "Min")), 0),
                range_high=_to_int(_node_value(child, ("rangehigh", "rangeHigh", "RangeHigh", "max", "Max")), -1),
            )
            fields[fname.lower()] = field
            by_short[fshort] = field
        if fields:
            table = XmlTable(name=name, short_name=short, fields=fields, by_short=by_short)
            tables[name.lower()] = table
            tables[short] = table
    if not tables:
        raise ValueError("descriptor XML did not expose any tables")
    return tables


@dataclass
class DbField:
    name: str
    short_name: str
    db_type: int
    bit_offset: int
    depth: int
    range_low: int
    range_high: int


@dataclass
class DbTable:
    name: str
    short_name: str
    table_start: int
    table_end: int
    record_size: int
    records_count: int
    valid_records_count: int
    records_count_offset: int
    valid_count_offset: int
    records_offset: int
    compressed_length: int
    compressed_length_offset: int
    fields: list[DbField]

    @property
    def by_name(self) -> dict[str, DbField]:
        return {field.name.lower(): field for field in self.fields}


@dataclass
class FifaDb:
    data: bytearray
    start: int
    end: int
    tables: dict[str, DbTable]

    def table(self, name: str) -> DbTable | None:
        lname = name.lower()
        if lname in self.tables:
            return self.tables[lname]
        for table in self.tables.values():
            if table.name.lower() == lname:
                return table
        return None


def read_short_name(data: bytes | bytearray, offset: int) -> str:
    return bytes(data[offset : offset + 4]).decode("latin1")


def parse_fifa_db(db_bytes: bytes, descriptor: dict[str, XmlTable]) -> FifaDb:
    header = db_bytes.find(DB_HEADER)
    if header < 0:
        raise ValueError("raw FIFA DB header was not found (payload may be compressed)")
    data = bytearray(db_bytes)
    if header + 12 > len(data):
        raise ValueError("truncated FIFA DB header")
    declared_size = struct.unpack_from("<I", data, header + 8)[0]
    end = header + declared_size
    if declared_size <= 0 or end > len(data):
        end = len(data)
    cursor = header + 8
    _size = struct.unpack_from("<I", data, cursor)[0]
    cursor += 4
    cursor += 4
    table_count = struct.unpack_from("<I", data, cursor)[0]
    cursor += 4
    cursor += 4
    refs: list[tuple[str, int]] = []
    for _ in range(table_count):
        if cursor + 8 > end:
            raise ValueError("truncated FIFA DB table directory")
        short = read_short_name(data, cursor)
        offset = struct.unpack_from("<I", data, cursor + 4)[0]
        refs.append((short, offset))
        cursor += 8
    cursor += 4
    tables_start = cursor
    starts = sorted({tables_start + offset for _, offset in refs if tables_start + offset < end})
    tables: dict[str, DbTable] = {}
    for short, rel in refs:
        xdesc = descriptor.get(short)
        if xdesc is None:
            continue
        table_start = tables_start + rel
        if table_start + 32 > end:
            continue
        tcur = table_start + 4
        record_size = struct.unpack_from("<I", data, tcur)[0]; tcur += 4
        tcur += 4
        compressed_len_offset = tcur
        compressed_len_raw = struct.unpack_from("<I", data, tcur)[0]; tcur += 4
        compressed_len = 0 if compressed_len_raw == NO_COMPRESSED_STRINGS else compressed_len_raw
        records_count_offset = tcur
        records_count = struct.unpack_from("<H", data, tcur)[0]; tcur += 2
        valid_offset = tcur
        valid_count = struct.unpack_from("<H", data, tcur)[0]; tcur += 2
        tcur += 4
        fields_count = data[tcur]; tcur += 1
        tcur += 11
        fields: list[DbField] = []
        for _ in range(fields_count):
            if tcur + 16 > end:
                raise ValueError(f"truncated field directory for {xdesc.name}")
            db_type = struct.unpack_from("<I", data, tcur)[0]
            bit_offset = struct.unpack_from("<I", data, tcur + 4)[0]
            fshort = read_short_name(data, tcur + 8)
            depth = struct.unpack_from("<I", data, tcur + 12)[0]
            tcur += 16
            xf = xdesc.by_short.get(fshort)
            fields.append(DbField(
                name=(xf.name if xf else fshort), short_name=fshort, db_type=db_type,
                bit_offset=bit_offset, depth=depth,
                range_low=(xf.range_low if xf else 0), range_high=(xf.range_high if xf else -1),
            ))
        fields.sort(key=lambda f: f.bit_offset)
        records_offset = tcur
        next_start = next((s for s in starts if s > table_start), end)
        table = DbTable(
            name=xdesc.name, short_name=short, table_start=table_start, table_end=next_start,
            record_size=record_size, records_count=records_count, valid_records_count=valid_count,
            records_count_offset=records_count_offset, valid_count_offset=valid_offset, records_offset=records_offset,
            compressed_length=compressed_len, compressed_length_offset=compressed_len_offset, fields=fields,
        )
        tables[xdesc.name.lower()] = table
        tables[short] = table
    if not tables:
        raise ValueError("no descriptor-backed tables could be parsed")
    return FifaDb(data=data, start=header, end=end, tables=tables)


def _db_directory_entries(db: FifaDb) -> tuple[int, list[tuple[str, int, int]]]:
    """Return (tables_start, [(short, rel_offset, directory_offset), ...])."""
    data = db.data
    cursor = db.start + 8
    if cursor + 16 > len(data):
        raise ValueError("truncated FIFA DB directory header")
    cursor += 4  # declared size
    cursor += 4  # unknown/version
    table_count = struct.unpack_from("<I", data, cursor)[0]
    cursor += 4
    cursor += 4
    rows: list[tuple[str, int, int]] = []
    for _ in range(table_count):
        if cursor + 8 > len(data):
            raise ValueError("truncated FIFA DB table directory")
        short = read_short_name(data, cursor)
        rel = struct.unpack_from("<I", data, cursor + 4)[0]
        rows.append((short, rel, cursor + 4))
        cursor += 8
    cursor += 4
    return cursor, rows


def _insert_db_bytes(db: FifaDb, descriptor: dict[str, XmlTable], insertion: int, payload: bytes,
                     shift_table_starts_after: int) -> None:
    """Insert bytes into a FIFA DB and repair the DB directory + declared size.

    FIFA DB table directory offsets are relative to tables_start. Internal
    record offsets are implicit from each table header, so inserting complete
    records into one table only requires shifting directory entries of later
    tables. The caller chooses the table-start boundary used for that shift.
    """
    if not payload:
        return
    tables_start, refs = _db_directory_entries(db)
    old_len = len(db.data)
    db.data[insertion:insertion] = payload
    delta = len(payload)
    for _short, rel, field_offset in refs:
        absolute = tables_start + rel
        if absolute > shift_table_starts_after:
            struct.pack_into("<I", db.data, field_offset, rel + delta)
    declared = struct.unpack_from("<I", db.data, db.start + 8)[0]
    if declared <= 0 or db.start + declared > old_len:
        declared = old_len - db.start
    struct.pack_into("<I", db.data, db.start + 8, declared + delta)
    reparsed = parse_fifa_db(bytes(db.data), descriptor)
    db.data, db.start, db.end, db.tables = reparsed.data, reparsed.start, reparsed.end, reparsed.tables


def ensure_table_capacity(db: FifaDb, descriptor: dict[str, XmlTable], table_name: str,
                          required_spare: int, warnings: list[str]) -> DbTable:
    table = db.table(table_name)
    if table is None:
        raise ValueError(f"{table_name} table not found")
    spare = table.records_count - table.valid_records_count
    if spare >= required_spare:
        return table
    extra = required_spare - spare
    if table.records_count + extra > 0xFFFF:
        raise ValueError(f"{table_name} row capacity would exceed FIFA DB uint16 limit")
    insertion = table.records_offset + table.records_count * table.record_size
    _insert_db_bytes(db, descriptor, insertion, b"\x00" * (extra * table.record_size), table.table_start)
    grown = db.table(table_name)
    if grown is None:
        raise ValueError(f"{table_name} table disappeared after expansion")
    struct.pack_into("<H", db.data, grown.records_count_offset, grown.records_count + extra)
    # The reparse above saw the old capacity because the count field had not yet
    # been updated. Reparse once more after writing the new count.
    reparsed = parse_fifa_db(bytes(db.data), descriptor)
    db.data, db.start, db.end, db.tables = reparsed.data, reparsed.start, reparsed.end, reparsed.tables
    grown = db.table(table_name)
    if grown is None:
        raise ValueError(f"{table_name} table could not be reparsed after expansion")
    warnings.append(f"expanded {table_name} capacity by {extra} rows ({extra * grown.record_size} bytes)")
    return grown


def ensure_table_tail_bytes(db: FifaDb, descriptor: dict[str, XmlTable], table_name: str,
                            extra_bytes: int, warnings: list[str]) -> DbTable:
    if extra_bytes <= 0:
        table = db.table(table_name)
        if table is None:
            raise ValueError(f"{table_name} table not found")
        return table
    table = db.table(table_name)
    if table is None:
        raise ValueError(f"{table_name} table not found")
    extra = align(extra_bytes, 16)
    insertion = table.table_end
    _insert_db_bytes(db, descriptor, insertion, b"\x00" * extra, table.table_start)
    grown = db.table(table_name)
    if grown is None:
        raise ValueError(f"{table_name} table disappeared after tail expansion")
    warnings.append(f"expanded {table_name} table tail by {extra} bytes")
    return grown


def read_bits_le(record: bytes | bytearray, bit_offset: int, depth: int) -> int:
    value = 0
    for bit in range(depth):
        source = bit_offset + bit
        if (record[source >> 3] >> (source & 7)) & 1:
            value |= 1 << bit
    return value


def write_bits_le(record: bytearray, bit_offset: int, depth: int, value: int) -> None:
    if depth <= 0 or value < 0 or value >= (1 << depth):
        raise ValueError(f"value {value} does not fit in {depth} bits")
    for bit in range(depth):
        target = bit_offset + bit
        idx = target >> 3
        mask = 1 << (target & 7)
        if (value >> bit) & 1:
            record[idx] |= mask
        else:
            record[idx] &= (~mask) & 0xFF


def read_field(record: bytes | bytearray, field: DbField) -> Any:
    if field.db_type == 3:
        return read_bits_le(record, field.bit_offset, field.depth) + field.range_low
    if field.db_type == 4:
        off = field.bit_offset >> 3
        return struct.unpack_from("<f", record, off)[0]
    if field.db_type == 0:
        off = field.bit_offset >> 3
        length = (field.depth + 7) // 8
        raw = bytes(record[off : off + length]).split(b"\x00", 1)[0]
        return raw.decode("utf-8", errors="replace")
    if field.db_type in (13, 14):
        off = field.bit_offset >> 3
        return struct.unpack_from("<i", record, off)[0]
    return read_bits_le(record, field.bit_offset, field.depth) if field.depth > 0 else 0


def write_field(record: bytearray, field: DbField, value: Any) -> None:
    if field.db_type == 3:
        numeric = int(value) - field.range_low
        write_bits_le(record, field.bit_offset, field.depth, numeric)
        return
    if field.db_type == 4:
        struct.pack_into("<f", record, field.bit_offset >> 3, float(value))
        return
    if field.db_type == 0:
        off = field.bit_offset >> 3
        length = (field.depth + 7) // 8
        encoded = str(value).encode("utf-8")[:length]
        record[off : off + length] = encoded + b"\x00" * (length - len(encoded))
        return
    # We deliberately never synthesize compressed strings in-place. Copying a
    # raw offset between different Huffman blocks is unsafe.
    if field.db_type in (13, 14):
        raise ValueError(f"refusing to synthesize compressed field {field.name}")
    write_bits_le(record, field.bit_offset, field.depth, int(value))


def record_bytes(db: FifaDb, table: DbTable, index: int) -> bytes:
    off = table.records_offset + index * table.record_size
    return bytes(db.data[off : off + table.record_size])


def set_record_bytes(db: FifaDb, table: DbTable, index: int, record: bytes | bytearray) -> None:
    if len(record) != table.record_size:
        raise ValueError("record size mismatch")
    off = table.records_offset + index * table.record_size
    if off + table.record_size > len(db.data):
        raise ValueError("record outside DB")
    db.data[off : off + table.record_size] = record


def read_row(db: FifaDb, table: DbTable, index: int) -> dict[str, Any]:
    rec = record_bytes(db, table, index)
    return {field.name.lower(): read_field(rec, field) for field in table.fields if field.db_type not in (13, 14)}


def index_players(db: FifaDb) -> tuple[DbTable, dict[int, int]]:
    table = db.table("players")
    if table is None:
        raise ValueError("players table not found")
    fields = table.by_name
    if "playerid" not in fields:
        raise ValueError("players.playerid field not found")
    pid_field = fields["playerid"]
    result: dict[int, int] = {}
    for index in range(table.valid_records_count):
        rec = record_bytes(db, table, index)
        try:
            pid = int(read_field(rec, pid_field))
        except Exception:
            continue
        if pid > 0:
            result[pid] = index
    return table, result


def decode_huffman_string(buffer: bytes | bytearray, offset: int, output_len: int, tree: list[tuple[int,int,int,int]]) -> str:
    if output_len <= 0:
        return ""
    if not tree:
        return bytes(buffer[offset : offset + output_len]).split(b"\x00",1)[0].decode("utf-8", errors="replace")
    out = bytearray()
    node = 0
    cursor = offset
    while len(out) < output_len and cursor < len(buffer):
        byte = buffer[cursor]; cursor += 1
        for bit in range(7, -1, -1):
            direction = (byte >> bit) & 1
            c0,l0,c1,l1 = tree[node] if 0 <= node < len(tree) else (0,0,0,0)
            child = c1 if direction else c0
            leaf = l1 if direction else l0
            if child == 0:
                out.append(leaf)
                node = 0
                if len(out) >= output_len:
                    break
            else:
                node = child
    return bytes(out).split(b"\x00",1)[0].decode("utf-8", errors="replace")


def playername_map(db: FifaDb) -> tuple[dict[int, str], dict[str, int]]:
    table = db.table("playernames")
    if table is None:
        return {}, {}
    fields = table.by_name
    id_field = fields.get("nameid") or fields.get("playernameid") or fields.get("id")
    text_field = fields.get("name") or fields.get("playername")
    if id_field is None or text_field is None:
        return {}, {}
    if text_field.db_type not in (0,13,14):
        return {}, {}
    records = [record_bytes(db, table, i) for i in range(table.valid_records_count)]
    tree: list[tuple[int,int,int,int]] = []
    base = table.records_offset + table.records_count * table.record_size
    length = table.compressed_length
    if text_field.db_type in (13,14) and length > 0:
        minimum: int | None = None
        for rec in records:
            off = struct.unpack_from("<i", rec, text_field.bit_offset >> 3)[0]
            if off >= 0 and (minimum is None or off < minimum):
                minimum = off
        tree_size = minimum or 0
        if base + tree_size <= len(db.data):
            for n in range(tree_size // 4):
                p = base + n*4
                tree.append((db.data[p], db.data[p+1], db.data[p+2], db.data[p+3]))
    by_id: dict[int,str] = {}
    by_text: dict[str,int] = {}
    for rec in records:
        try:
            nid = int(read_field(rec, id_field))
        except Exception:
            continue
        if text_field.db_type == 0:
            text = str(read_field(rec, text_field))
        else:
            coff = struct.unpack_from("<i", rec, text_field.bit_offset >> 3)[0]
            if coff < 0 or base + coff >= len(db.data):
                text = ""
            else:
                p = base + coff
                if text_field.db_type == 14:
                    if p + 2 > len(db.data): text = ""
                    else:
                        olen = struct.unpack_from(">H", db.data, p)[0]
                        text = decode_huffman_string(db.data, p+2, olen, tree)
                else:
                    olen = db.data[p] if p < len(db.data) else 0
                    text = decode_huffman_string(db.data, p+1, olen, tree)
        if text:
            by_id[nid] = text
            by_text.setdefault(normalize_text(text), nid)
    return by_id, by_text


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in value.casefold() if ch.isalnum())



def _huffman_tree_for_table(db: FifaDb, table: DbTable, text_field: DbField) -> tuple[int, list[tuple[int,int,int,int]]]:
    if text_field.db_type not in (13, 14):
        return 0, []
    base = table.records_offset + table.records_count * table.record_size
    minimum: int | None = None
    for index in range(table.valid_records_count):
        rec = record_bytes(db, table, index)
        off = struct.unpack_from("<i", rec, text_field.bit_offset >> 3)[0]
        if off >= 0 and (minimum is None or off < minimum):
            minimum = off
    tree_size = minimum or 0
    tree: list[tuple[int,int,int,int]] = []
    if tree_size > 0 and base + tree_size <= len(db.data):
        for n in range(tree_size // 4):
            p = base + n * 4
            tree.append((db.data[p], db.data[p+1], db.data[p+2], db.data[p+3]))
    return tree_size, tree


def _huffman_codebook(tree: list[tuple[int,int,int,int]]) -> dict[int, tuple[int, ...]]:
    if not tree:
        return {}
    codes: dict[int, tuple[int, ...]] = {}
    visiting: set[int] = set()
    def walk(node: int, path: tuple[int, ...]) -> None:
        if node in visiting or node < 0 or node >= len(tree):
            return
        visiting.add(node)
        c0, l0, c1, l1 = tree[node]
        if c0 == 0:
            codes.setdefault(int(l0), path + (0,))
        else:
            walk(int(c0), path + (0,))
        if c1 == 0:
            codes.setdefault(int(l1), path + (1,))
        else:
            walk(int(c1), path + (1,))
        visiting.remove(node)
    walk(0, ())
    return codes


def _encode_huffman_bytes(raw: bytes, codes: dict[int, tuple[int, ...]]) -> bytes:
    bits: list[int] = []
    for byte in raw:
        code = codes.get(int(byte))
        if code is None:
            raise ValueError(f"Huffman tree cannot encode byte 0x{byte:02X}")
        bits.extend(code)
    out = bytearray((len(bits) + 7) // 8)
    for index, bit in enumerate(bits):
        if bit:
            out[index >> 3] |= 1 << (7 - (index & 7))
    return bytes(out)


def _compressed_string_end(buffer: bytes | bytearray, base: int, offset: int, db_type: int,
                           tree: list[tuple[int,int,int,int]]) -> int:
    if offset < 0 or base + offset >= len(buffer):
        return max(0, offset)
    p = base + offset
    if db_type == 14:
        if p + 2 > len(buffer):
            return offset
        output_len = struct.unpack_from(">H", buffer, p)[0]
        p += 2
        prefix = 2
    else:
        output_len = int(buffer[p])
        p += 1
        prefix = 1
    if output_len <= 0:
        return offset + prefix
    if not tree:
        return offset + prefix + output_len
    produced = 0
    node = 0
    cursor = p
    while produced < output_len and cursor < len(buffer):
        byte = buffer[cursor]
        cursor += 1
        for bit in range(7, -1, -1):
            direction = (byte >> bit) & 1
            if node < 0 or node >= len(tree):
                return cursor - base
            c0, l0, c1, l1 = tree[node]
            child = c1 if direction else c0
            if child == 0:
                produced += 1
                node = 0
                if produced >= output_len:
                    break
            else:
                node = int(child)
    return cursor - base


def ensure_playername_strings(db: FifaDb, texts: Iterable[str], warnings: list[str], descriptor: dict[str, XmlTable] | None = None) -> dict[str, int]:
    """Add missing Legend display names to playernames without changing DB size.

    FIFA's playernames table normally has spare record slots.  For compressed
    strings we reuse its existing Huffman tree and append encoded strings only
    into unused bytes already reserved before the next table.  If there is no
    safe capacity we fail instead of shifting/corrupting the database.
    """
    _by_id, by_text = playername_map(db)
    wanted: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        text = str(raw or "").strip()
        key = normalize_text(text)
        if not text or not key or key in by_text or key in seen:
            continue
        seen.add(key)
        wanted.append(text)
    if not wanted:
        return dict(by_text)
    table = db.table("playernames")
    if table is None:
        raise ValueError("playernames table is missing; cannot inject Legend names")
    fields = table.by_name
    id_field = fields.get("nameid") or fields.get("playernameid") or fields.get("id")
    text_field = fields.get("name") or fields.get("playername")
    if id_field is None or text_field is None:
        raise ValueError("playernames name-id/text fields are missing")
    spare = table.records_count - table.valid_records_count
    if spare < len(wanted):
        if descriptor is None:
            raise ValueError(f"playernames table has only {spare} spare rows; needs {len(wanted)}")
        table = ensure_table_capacity(db, descriptor, "playernames", len(wanted), warnings)
        fields = table.by_name
        id_field = fields.get("nameid") or fields.get("playernameid") or fields.get("id")
        text_field = fields.get("name") or fields.get("playername")
        if id_field is None or text_field is None:
            raise ValueError("playernames fields disappeared after capacity expansion")

    existing_ids = set(_by_id)
    max_id = max(existing_ids or {0})
    def next_name_id() -> int:
        nonlocal max_id
        while True:
            max_id += 1
            if max_id in existing_ids:
                continue
            test = bytearray(table.record_size)
            try:
                write_field(test, id_field, max_id)
            except Exception:
                raise ValueError("playernames ID field has no room for additional Legend names")
            existing_ids.add(max_id)
            return max_id

    base = table.records_offset + table.records_count * table.record_size
    tree_size, tree = _huffman_tree_for_table(db, table, text_field)
    codes = _huffman_codebook(tree)
    cursor = tree_size
    if text_field.db_type in (13, 14):
        for index in range(table.valid_records_count):
            rec = record_bytes(db, table, index)
            off = struct.unpack_from("<i", rec, text_field.bit_offset >> 3)[0]
            if off >= 0:
                cursor = max(cursor, _compressed_string_end(db.data, base, off, text_field.db_type, tree))
    limit = table.table_end - base
    if limit < 0:
        raise ValueError("playernames compressed-string area is outside its table")

    template = bytearray(record_bytes(db, table, 0)) if table.valid_records_count else bytearray(table.record_size)
    injected: dict[str, int] = {}
    for original in wanted:
        # Use exact UTF-8 when the table tree can encode it; otherwise fall back
        # to an ASCII transliteration (Pelé->Pele, Matthäus->Matthaus, etc.).
        stored_text = original
        raw = stored_text.encode("utf-8")
        if text_field.db_type in (13, 14):
            try:
                encoded_bits = _encode_huffman_bytes(raw, codes)
            except Exception:
                stored_text = unicodedata.normalize("NFKD", original).encode("ascii", "ignore").decode("ascii") or original
                raw = stored_text.encode("ascii", "ignore")
                encoded_bits = _encode_huffman_bytes(raw, codes)
            prefix = struct.pack(">H", len(raw)) if text_field.db_type == 14 else bytes([len(raw)])
            encoded = prefix + encoded_bits
            if cursor + len(encoded) > limit:
                if descriptor is None:
                    raise ValueError(
                        f"playernames compressed-string area has {max(0, limit-cursor)} spare bytes; "
                        f"needs at least {len(encoded)} more for {original!r}"
                    )
                need = cursor + len(encoded) - limit + 512
                table = ensure_table_tail_bytes(db, descriptor, "playernames", need, warnings)
                fields = table.by_name
                id_field = fields.get("nameid") or fields.get("playernameid") or fields.get("id")
                text_field = fields.get("name") or fields.get("playername")
                if id_field is None or text_field is None:
                    raise ValueError("playernames fields disappeared after tail expansion")
                base = table.records_offset + table.records_count * table.record_size
                limit = table.table_end - base
            db.data[base + cursor: base + cursor + len(encoded)] = encoded
            string_offset = cursor
            cursor += len(encoded)
        elif text_field.db_type == 0:
            string_offset = -1
        else:
            raise ValueError(f"unsupported playernames string field type {text_field.db_type}")

        record = bytearray(template)
        nid = next_name_id()
        write_field(record, id_field, nid)
        if text_field.db_type == 0:
            write_field(record, text_field, stored_text)
        else:
            struct.pack_into("<i", record, text_field.bit_offset >> 3, string_offset)
        idx = table.valid_records_count
        set_record_bytes(db, table, idx, record)
        table.valid_records_count += 1
        struct.pack_into("<H", db.data, table.valid_count_offset, table.valid_records_count)
        injected[normalize_text(original)] = nid
        injected[normalize_text(stored_text)] = nid

    if text_field.db_type in (13, 14):
        table.compressed_length = max(table.compressed_length, cursor)
        struct.pack_into("<I", db.data, table.compressed_length_offset, table.compressed_length)
    refreshed_id, refreshed = playername_map(db)
    refreshed.update(injected)
    warnings.append(f"injected {len(wanted)} Legend player-name strings into spare playernames rows")
    return refreshed


def copy_common_fields(source_db: FifaDb, source_table: DbTable, source_index: int,
                       target_db: FifaDb, target_table: DbTable, target_record: bytearray,
                       source_names: dict[int,str], target_names_by_text: dict[str,int], warnings: list[str]) -> None:
    src_rec = record_bytes(source_db, source_table, source_index)
    sfields = source_table.by_name
    tfields = target_table.by_name
    for name, tf in tfields.items():
        sf = sfields.get(name)
        if sf is None or sf.db_type in (13,14) or tf.db_type in (13,14):
            continue
        try:
            value = read_field(src_rec, sf)
            if name in NAME_FIELDS:
                text = source_names.get(int(value))
                mapped = target_names_by_text.get(normalize_text(text)) if text else None
                if mapped is not None:
                    value = mapped
            write_field(target_record, tf, value)
        except Exception as exc:
            # Many non-critical fields have version-specific ranges. Keeping the
            # donor value is safer than forcing an out-of-range source value.
            if name in CRITICAL_FIELDS:
                raise
            warnings.append(f"field-copy {name}: {exc}")


def donor_for_legend(legend: dict[str,Any], target_db: FifaDb, target_table: DbTable,
                     target_index: dict[int,int], base_by_asset: dict[int,dict[str,Any]]) -> int:
    pos = str(legend.get("position", "")).upper()
    nation = int(legend.get("nation", 0) or 0)
    candidates: list[tuple[int,int,int]] = []
    for asset, row in base_by_asset.items():
        idx = target_index.get(asset)
        if idx is None:
            continue
        same_pos = 0 if str(row.get("position","")).upper() == pos else 1
        same_nat = 0 if int(row.get("nation",0) or 0) == nation else 1
        rating_delta = abs(int(row.get("rating",70) or 70) - int(legend.get("rating",88) or 88))
        candidates.append((same_pos*10000 + same_nat*1000 + rating_delta, asset, idx))
    if not candidates:
        raise ValueError("no target FUT player row is available as a donor")
    candidates.sort()
    return candidates[0][2]


def set_if_present(record: bytearray, fields: dict[str,DbField], name: str, value: Any, warnings: list[str]) -> None:
    field = fields.get(name.lower())
    if field is None or field.db_type in (13,14):
        return
    try:
        write_field(record, field, value)
    except Exception as exc:
        warnings.append(f"set {name}={value}: {exc}")


def split_legend_name(legend: dict[str,Any]) -> tuple[str,str,str]:
    common = str(legend.get("knownAs") or legend.get("commonName") or legend.get("name") or "").strip()
    first = str(legend.get("firstName") or "").strip()
    last = str(legend.get("lastName") or "").strip()
    if not first and not last:
        parts = common.split()
        if len(parts) > 1:
            first, last = " ".join(parts[:-1]), parts[-1]
        elif parts:
            last = parts[0]
    return first, last, common


def apply_name_ids(record: bytearray, fields: dict[str,DbField], legend: dict[str,Any],
                   target_names: dict[str,int], warnings: list[str]) -> int:
    first, last, common = split_legend_name(legend)
    found = 0
    mapping = {
        "firstnameid": first,
        "lastnameid": last,
        "commonnameid": common,
        "playerjerseynameid": common or last,
    }
    for field_name, text in mapping.items():
        if not text or field_name not in fields:
            continue
        nid = target_names.get(normalize_text(text))
        if nid is not None:
            set_if_present(record, fields, field_name, nid, warnings)
            found += 1
    return found


def inject_legends_into_db(target_db_bytes: bytes, target_xml: bytes, legends: list[dict[str,Any]],
                           base_players: list[dict[str,Any]], source_db_bytes: bytes | None = None,
                           source_xml: bytes | None = None) -> tuple[bytes, dict[str,Any]]:
    desc = parse_descriptor(target_xml)
    target = parse_fifa_db(target_db_bytes, desc)
    ttable, tindex = index_players(target)
    missing_needed = sum(1 for l in legends if int(l["assetId"]) not in tindex)
    warnings: list[str] = []
    ttable = ensure_table_capacity(target, desc, "players", missing_needed, warnings)
    ttable, tindex = index_players(target)
    source = None; stable = None; sindex: dict[int,int] = {}; source_names: dict[int,str] = {}
    if source_db_bytes is not None and source_xml is not None:
        try:
            source = parse_fifa_db(source_db_bytes, parse_descriptor(source_xml))
            stable, sindex = index_players(source)
            source_names, _ = playername_map(source)
        except Exception:
            source = None; stable = None; sindex = {}; source_names = {}
    # The retail PC cards DB does not contain the Xbox-exclusive Legend names.
    # Add only the strings we need, reusing spare playernames rows and the DB's
    # existing Huffman codec.  This is the key difference from v2.40.20, which
    # could add numeric player IDs but deliberately refused to activate them
    # when no matching name already existed.
    wanted_names: list[str] = []
    for legend in legends:
        first, last, common = split_legend_name(legend)
        wanted_names.extend([first, last, common, common or last])
    target_names_by_text = ensure_playername_strings(target, wanted_names, warnings, desc)
    _target_names_by_id, _ = playername_map(target)
    base_by_asset = {int(row["assetId"]): row for row in base_players}
    details: list[dict[str,Any]] = []
    fields = ttable.by_name
    for legend in legends:
        asset = int(legend["assetId"])
        existing = tindex.get(asset)
        source_idx = sindex.get(asset)
        if existing is not None:
            idx = existing
            rec = bytearray(record_bytes(target, ttable, idx))
            mode = "existing"
        else:
            idx = ttable.valid_records_count
            donor_idx = donor_for_legend(legend, target, ttable, tindex, base_by_asset)
            rec = bytearray(record_bytes(target, ttable, donor_idx))
            mode = "source-copy" if source is not None and stable is not None and source_idx is not None else "donor-clone"
        local_warn: list[str] = []
        if source is not None and stable is not None and source_idx is not None:
            copy_common_fields(source, stable, source_idx, target, ttable, rec,
                               source_names, target_names_by_text, local_warn)
        name_hits = apply_name_ids(rec, fields, legend, target_names_by_text, local_warn)
        set_if_present(rec, fields, "playerid", asset, local_warn)
        set_if_present(rec, fields, "nationality", int(legend.get("nation",0) or 0), local_warn)
        set_if_present(rec, fields, "overallrating", int(legend.get("rating",88) or 88), local_warn)
        set_if_present(rec, fields, "potential", int(legend.get("rating",88) or 88), local_warn)
        set_if_present(rec, fields, "headassetid", asset, local_warn)
        # FUT Legends use the dedicated Legends team in the client DB when the
        # field exists. Team/league on the actual FUT item still comes from the
        # server response.
        set_if_present(rec, fields, "teamid", int(legend.get("teamId", LEGEND_TEAM_ID) or LEGEND_TEAM_ID), local_warn)
        # For donor clones, preserve donor position fields by selecting a donor
        # with the same preferred position. Source-copy rows bring their own.
        set_record_bytes(target, ttable, idx, rec)
        if existing is None:
            ttable.valid_records_count += 1
            struct.pack_into("<H", target.data, ttable.valid_count_offset, ttable.valid_records_count)
            tindex[asset] = idx
        warnings.extend(f"{legend.get('name',asset)}: {w}" for w in local_warn[:8])
        details.append({
            "assetId": asset, "name": legend.get("name"), "mode": mode,
            "sourceRowFound": source_idx is not None, "targetNameIdsMatched": name_hits,
        })
    # Reparse from bytes for verification.
    output = bytes(target.data)
    verified_db = parse_fifa_db(output, desc)
    _, verify_index = index_players(verified_db)
    requested = {int(row["assetId"]) for row in legends}
    found = sorted(requested.intersection(verify_index))
    missing = sorted(requested.difference(verify_index))
    semantically_ready = all(
        bool(row.get("sourceRowFound"))
        or str(row.get("mode")) == "existing"
        or int(row.get("targetNameIdsMatched", 0)) > 0
        for row in details
    )
    return output, {
        "requested": len(requested), "verified": len(found), "verifiedIds": found,
        "missingIds": missing, "allVerified": not missing,
        # Appending the numeric ID is not enough to claim the PC client can
        # render a Legend. Each new row also needs an authoritative hidden-main-
        # DB source row or at least a target-side name mapping. Pure donor clones
        # are useful for diagnostics but are not allowed to unlock My Club.
        "semanticReady": semantically_ready,
        "playersCapacity": ttable.records_count, "playersValidAfter": ttable.valid_records_count,
        "details": details, "warnings": warnings[:200],
    }


def _decode_record(stored: bytes) -> tuple[bytes, dict[str, Any] | None]:
    if stored.startswith(CHUNKZIP_MAGIC):
        return decode_chunkzip(stored)
    return stored, None


def scan_named_candidates(big_path: Path, bh_path: Path | None, basename: str) -> list[ArchiveDbCandidate]:
    wanted = basename.lower()
    result: list[ArchiveDbCandidate] = []
    if not big_path.is_file():
        return result
    # Modern FIFA archives are ViV4-indexed record stores. Each record can be a
    # chunkzip whose decoded payload is a named BIG package.
    if bh_path is not None and bh_path.is_file():
        try:
            bh = bh_path.read_bytes()
            records = parse_bh(bh)
            big_size = big_path.stat().st_size
            with big_path.open("rb") as handle:
                for record in records:
                    if record.size <= 0 or record.offset + record.size > big_size:
                        continue
                    handle.seek(record.offset)
                    stored = handle.read(record.size)
                    try:
                        decoded, chunk_info = _decode_record(stored)
                    except Exception:
                        continue
                    if decoded[:4] not in (b"BIGF", b"BIG4"):
                        continue
                    try:
                        entries = parse_big(decoded)
                    except Exception:
                        continue
                    for entry in entries:
                        if entry_basename(entry.name) == wanted and not entry.compressed:
                            result.append(ArchiveDbCandidate(
                                big_path=big_path, bh_path=bh_path, record=record,
                                stored=stored, decoded=decoded, chunk_info=chunk_info,
                                entry=entry, source_kind="viv4-record-big",
                            ))
        except Exception:
            pass
    # Some extracted/repacked installs expose a root BIGF/BIG4 directly.
    try:
        blob = big_path.read_bytes()
        entries = parse_big(blob)
        for entry in entries:
            if entry_basename(entry.name) == wanted and not entry.compressed:
                result.append(ArchiveDbCandidate(
                    big_path=big_path, bh_path=None, record=None, stored=blob,
                    decoded=blob, chunk_info=None, entry=entry, source_kind="root-big",
                ))
    except Exception:
        pass
    return result



def scan_viv4_direct_records(big_path: Path, bh_path: Path | None) -> list[ArchiveDbCandidate]:
    """Return decoded ViV4 records as synthetic whole-record candidates.

    Retail FIFA 14 often stores hashed files directly in BIG/BH records, so the
    original v2.40.20 named-inner-BIG scanner could never see cards_ng_db.db on
    the user's install.  We preserve the BH record boundary and treat the whole
    decoded payload as one candidate; later structure checks decide whether it
    is a descriptor or FIFA DB.
    """
    result: list[ArchiveDbCandidate] = []
    if not big_path.is_file() or bh_path is None or not bh_path.is_file():
        return result
    try:
        records = parse_bh(bh_path.read_bytes())
        big_size = big_path.stat().st_size
        with big_path.open("rb") as handle:
            for record in records:
                if record.size <= 0 or record.offset < 0 or record.offset + record.size > big_size:
                    continue
                handle.seek(record.offset)
                stored = handle.read(record.size)
                try:
                    decoded, chunk_info = _decode_record(stored)
                except Exception:
                    continue
                result.append(ArchiveDbCandidate(
                    big_path=big_path, bh_path=bh_path, record=record,
                    stored=stored, decoded=decoded, chunk_info=chunk_info,
                    entry=BigEntry(name=f"[viv4-record-{record.index}]", offset=0, size=len(decoded), compressed=False),
                    source_kind="viv4-record-direct",
                ))
    except Exception:
        return []
    return result


def _extract_descriptor_xml(blob: bytes) -> bytes | None:
    # Hashed ViV4 records can include alignment/padding around the XML payload.
    # Try the obvious trimmed payload first, then progressively trim at closing
    # tags. This stays read-only and only accepts a candidate if parse_descriptor
    # exposes FIFA tables.
    starts = [pos for pos in (blob.find(b"<?xml"), blob.find(b"<")) if pos >= 0]
    if not starts:
        return None
    start = min(starts)
    raw = blob[start:].rstrip(b"\x00\xff \r\n\t")
    attempts = [raw]
    close_ends: list[int] = []
    cursor = len(raw)
    while len(close_ends) < 64:
        pos = raw.rfind(b"</", 0, cursor)
        if pos < 0:
            break
        end = raw.find(b">", pos)
        if end >= 0:
            close_ends.append(end + 1)
        cursor = pos
    attempts.extend(raw[:end] for end in close_ends)
    for xml in attempts:
        try:
            parse_descriptor(xml)
            return xml
        except Exception:
            continue
    return None


def discover_descriptor_record(big_path: Path, bh_path: Path | None) -> tuple[bytes, dict[str, Any]] | None:
    best: tuple[int, bytes, dict[str, Any]] | None = None
    for candidate in scan_viv4_direct_records(big_path, bh_path):
        blob = candidate_blob(candidate)
        # Descriptors are small XML files; skip obvious binary/huge records.
        if len(blob) > 8 * 1024 * 1024 or b"<" not in blob[:512]:
            continue
        xml = _extract_descriptor_xml(blob)
        if xml is None:
            continue
        try:
            desc = parse_descriptor(xml)
        except Exception:
            continue
        names = {table.name.lower() for table in desc.values()}
        if "players" not in names:
            continue
        score = 10
        if "playernames" in names:
            score += 5
        if any("team" in name for name in names):
            score += 1
        meta = {
            "archive": big_path.name, "entry": candidate.entry.name,
            "sourceKind": "viv4-direct-descriptor",
            "recordIndex": None if candidate.record is None else candidate.record.index,
        }
        if best is None or score > best[0]:
            best = (score, xml, meta)
    return (best[1], best[2]) if best is not None else None


def discover_fifa_db_records(big_path: Path, bh_path: Path | None, descriptor_xml: bytes,
                              sample_ids: Iterable[int]) -> list[ArchiveDbCandidate]:
    try:
        desc = parse_descriptor(descriptor_xml)
    except Exception:
        return []
    samples = {int(x) for x in sample_ids if int(x) > 0}
    scored: list[tuple[int, int, ArchiveDbCandidate]] = []
    for candidate in scan_viv4_direct_records(big_path, bh_path):
        blob = candidate_blob(candidate)
        header_pos = blob.find(DB_HEADER, 0, min(len(blob), 4096))
        if header_pos < 0:
            continue
        db_len = 0
        if header_pos + 12 <= len(blob):
            db_len = struct.unpack_from("<I", blob, header_pos + 8)[0]
        if db_len <= 0 or header_pos + db_len > len(blob):
            db_len = len(blob) - header_pos
        db_blob = blob[header_pos:header_pos + db_len]
        try:
            db = parse_fifa_db(db_blob, desc)
            table, index = index_players(db)
        except Exception:
            continue
        if table.valid_records_count < 1:
            continue
        hits = len(samples.intersection(index))
        # A cards DB should contain essentially all sampled retail stars.  Keep
        # the threshold low enough for title-update delta databases while still
        # rejecting unrelated DB payloads.
        if hits < min(3, max(1, len(samples))):
            continue
        # Preserve the entire decoded ViV4 record, but patch only the exact DB
        # span if the record carries alignment/wrapper bytes around it.
        candidate.entry.offset = header_pos
        candidate.entry.size = db_len
        scored.append((hits, table.valid_records_count, candidate))
    if not scored:
        return []
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    best_hits, best_count, _ = scored[0]
    # Multiple copies with the same score/size can exist inside an archive; patch
    # all authoritative equivalents just like the named path did.
    out: list[ArchiveDbCandidate] = []
    for hits, count, candidate in scored:
        if hits == best_hits and count == best_count:
            candidate.entry.name = f"cards_ng_db.db@record{candidate.record.index if candidate.record else 'root'}"
            candidate.source_kind = "viv4-record-direct-db"
            out.append(candidate)
    return out


def candidate_blob(candidate: ArchiveDbCandidate) -> bytes:
    return candidate.decoded[candidate.entry.offset:candidate.entry.offset+candidate.entry.size]


def pair_descriptor(game_root: Path, archive_name: str, basenames: tuple[str, ...]) -> tuple[bytes, dict[str, Any]] | None:
    big_path = game_root / archive_name
    bh_path = big_path.with_suffix(".bh")
    for basename in basenames:
        rows = scan_named_candidates(big_path, bh_path, basename)
        for row in rows:
            blob = candidate_blob(row)
            try:
                desc = parse_descriptor(blob)
                if any(table.name.lower() == "players" for table in desc.values()):
                    return blob, {"archive": archive_name, "entry": row.entry.name, "sourceKind": row.source_kind}
            except Exception:
                continue
    return None


def load_named_payload(game_root: Path, archive_names: Iterable[str], basename: str) -> tuple[bytes, dict[str, Any]] | None:
    for archive_name in archive_names:
        big_path = game_root / archive_name
        bh_path = big_path.with_suffix(".bh")
        for row in scan_named_candidates(big_path, bh_path, basename):
            return candidate_blob(row), {"archive": archive_name, "entry": row.entry.name, "sourceKind": row.source_kind}
    return None


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + BACKUP_SUFFIX)


def ensure_backup(path: Path) -> Path:
    backup = backup_path(path)
    if path.is_file() and not backup.exists():
        shutil.copy2(path, backup)
    return backup


def restore(game_root: Path) -> dict[str,Any]:
    restored=[]
    for name in ("patch.big","patch.bh","cards0.big","cards0.bh"):
        path=game_root/name; backup=backup_path(path)
        if backup.is_file():
            shutil.copy2(backup,path)
            restored.append({"archive":str(path),"backup":str(backup),"sha256":sha256_file(path)})
    return {"schema":"fifa14-v24022-legend-client-db","action":"restore","restored":restored,"verifiedAllLegends":False,"verifiedLegendIds":[]}


def _replace_candidate(candidate: ArchiveDbCandidate, replacement: bytes) -> tuple[bytes, bytes | None]:
    before = candidate.decoded[:candidate.entry.offset]
    after = candidate.decoded[candidate.entry.offset+candidate.entry.size:]
    if candidate.record is None and len(replacement) != candidate.entry.size:
        # Rebuilding a named root BIG directory is intentionally outside this
        # patcher's write scope. The user's retail target is a hashed ViV4
        # record, where the BH record itself can be resized/relocated safely.
        raise ValueError("root BIG cards DB cannot be resized safely; ViV4/BH target required")
    desired_decoded = before + replacement + after
    if candidate.record is None:
        return desired_decoded, None
    desired_stored = encode_chunkzip(desired_decoded, candidate.chunk_info) if candidate.chunk_info else desired_decoded
    return desired_stored, desired_decoded


def _apply_candidate(candidate: ArchiveDbCandidate, replacement: bytes) -> dict[str, Any]:
    big_path = candidate.big_path
    ensure_backup(big_path)
    if candidate.record is None:
        desired, _ = _replace_candidate(candidate, replacement)
        before = big_path.read_bytes()
        atomic_write(big_path, desired)
        return {"archive": str(big_path), "entry": candidate.entry.name, "sourceKind": candidate.source_kind,
                "sha256Before": sha256_bytes(before), "sha256After": sha256_file(big_path)}

    assert candidate.bh_path is not None
    bh_path = candidate.bh_path
    ensure_backup(bh_path)
    bh = bh_path.read_bytes()
    records = parse_bh(bh)
    record = next((row for row in records if row.index == candidate.record.index), None)
    if record is None:
        raise ValueError("target BH record disappeared")
    capacity = physical_capacity(records, record, big_path.stat().st_size)
    desired_stored, _ = _replace_candidate(candidate, replacement)
    before_hash = sha256_file(big_path)
    relocated = False
    new_offset = record.offset
    try:
        if len(desired_stored) <= capacity:
            desired_bh = update_bh_size(bh, record, len(desired_stored))
            write_record_slot(big_path, record.offset, capacity, desired_stored, max(record.size, len(desired_stored)))
        else:
            # A full retail cards DB frequently has no physical slack before the
            # next ViV4 record. Relocate only this hashed record to the end of
            # patch.big and point its existing BH entry at the new location.
            current_size = big_path.stat().st_size
            new_offset = align(current_size, 16)
            with big_path.open("ab") as handle:
                if new_offset > current_size:
                    handle.write(b"\x00" * (new_offset - current_size))
                handle.write(desired_stored)
                handle.flush(); os.fsync(handle.fileno())
            desired_bh = update_bh_location_and_size(bh, record, new_offset, len(desired_stored))
            relocated = True
        atomic_write(bh_path, desired_bh)
    except Exception:
        # Whole-file backups make rollback deterministic even if a write failed
        # after the BIG record changed but before the BH index did.
        bbig=backup_path(big_path); bbh=backup_path(bh_path)
        if bbig.is_file(): shutil.copy2(bbig,big_path)
        if bbh.is_file(): shutil.copy2(bbh,bh_path)
        raise
    return {"archive": str(big_path), "index": str(bh_path), "recordIndex": record.index,
            "entry": candidate.entry.name, "sourceKind": candidate.source_kind,
            "storedSizeBefore": record.size, "storedSizeAfter": len(desired_stored), "capacity": capacity,
            "relocated": relocated, "recordOffsetBefore": record.offset, "recordOffsetAfter": new_offset,
            "sha256Before": before_hash, "sha256After": sha256_file(big_path)}


def _verify_archive_candidate(game_root: Path, archive_name: str, target_xml: bytes, legend_ids: set[int]) -> tuple[bool, list[int]]:
    big_path=game_root/archive_name; bh_path=big_path.with_suffix(".bh")
    found_union: set[int]=set()
    rows = scan_named_candidates(big_path,bh_path,"cards_ng_db.db")
    if not rows:
        rows = discover_fifa_db_records(big_path, bh_path, target_xml, [158023,20801,41236,167495,10535])
    for row in rows:
        try:
            db=parse_fifa_db(candidate_blob(row),parse_descriptor(target_xml))
            _,idx=index_players(db)
            found_union.update(legend_ids.intersection(idx))
        except Exception:
            continue
    return legend_ids.issubset(found_union), sorted(found_union)


def patch_game(game_root: Path, legends: list[dict[str,Any]], base_players: list[dict[str,Any]], apply: bool) -> dict[str,Any]:
    report: dict[str,Any]={
        "schema":"fifa14-v24022-legend-client-db","action":"apply" if apply else "scan",
        "gameRoot":str(game_root),"requestedLegends":len(legends),"archives":[],
        "verifiedAllLegends":False,"verifiedLegendIds":[],"warnings":[],
    }
    legend_ids={int(row["assetId"]) for row in legends}

    # v2.40.20 assumed the ViV4 records contained named inner BIG packages. The
    # user's retail install uses hashed direct BH records, so first discover a
    # descriptor structurally and then identify cards_ng_db by known FIFA 14
    # player IDs. Named entries remain the fast path for repacked/modded installs.
    target_xml_info = None
    for archive_name in ("patch.big", "cards0.big"):
        target_xml_info = pair_descriptor(game_root, archive_name, ("cards_ng_db-meta.xml", "cards_ng_db_meta.xml"))
        if target_xml_info is not None:
            break
    if target_xml_info is None:
        for archive_name in ("patch.big", "cards0.big"):
            big_path = game_root / archive_name
            blind = discover_descriptor_record(big_path, big_path.with_suffix(".bh"))
            if blind is not None:
                target_xml_info = blind
                break
    if target_xml_info is None:
        report["error"] = "cards_ng_db descriptor XML could not be discovered in patch/cards archives"
        return report
    target_xml, target_meta = target_xml_info
    report["targetDescriptor"] = target_meta

    sample_ids = [158023, 20801, 41236, 167495, 10535, 155862, 146530, 153079]
    target_archive: str | None = None
    target_candidates: list[ArchiveDbCandidate] = []
    for archive_name in ("patch.big", "cards0.big"):
        big_path = game_root / archive_name
        bh_path = big_path.with_suffix(".bh")
        rows = scan_named_candidates(big_path, bh_path, "cards_ng_db.db")
        if not rows:
            rows = discover_fifa_db_records(big_path, bh_path, target_xml, sample_ids)
        if rows:
            target_archive = archive_name
            target_candidates = rows
            break
    if not target_candidates or target_archive is None:
        report["error"] = "no writable cards_ng_db structure was found in patch.big or cards0.big"
        return report
    report["targetArchive"] = target_archive

    source_db_info=load_named_payload(game_root,("patch.big","data0.big","data1.big"),"fifa_ng_db.db")
    source_xml_info=None
    for archive_name in ("patch.big","data0.big","data1.big"):
        source_xml_info=pair_descriptor(game_root,archive_name,("fifa_ng_db-meta.xml","fifa_ng_db_meta.xml"))
        if source_xml_info is not None: break
    source_db=source_db_info[0] if source_db_info else None
    source_xml=source_xml_info[0] if source_xml_info else None
    report["sourceMainDbFound"] = bool(source_db and source_xml)
    if source_db_info: report["sourceMainDb"] = source_db_info[1]

    verified_dry: set[int]=set()
    # Patch every cards_ng_db.db copy in the authoritative archive record set.
    # Normally there is one; multiple copies must all verify before My Club is
    # allowed to receive Legends.
    all_candidate_results_ok=True
    for candidate in target_candidates:
        item={"archive":str(candidate.big_path),"entry":candidate.entry.name,"sourceKind":candidate.source_kind,
              "recordIndex":None if candidate.record is None else candidate.record.index}
        try:
            original_db=candidate_blob(candidate)
            replacement,result=inject_legends_into_db(original_db,target_xml,legends,base_players,source_db,source_xml)
            item.update(result)
            if not result["allVerified"]:
                raise ValueError(f"in-memory verification missing Legend IDs: {result['missingIds']}")
            if not result.get("semanticReady"):
                donor_only = [row.get("name") for row in result.get("details", []) if not row.get("sourceRowFound") and str(row.get("mode")) != "existing" and int(row.get("targetNameIdsMatched", 0)) <= 0]
                raise ValueError("Legend IDs can be appended but the PC DB has no authoritative identity/name source for: " + ", ".join(str(x) for x in donor_only[:12]) + (" ..." if len(donor_only) > 12 else ""))
            verified_dry.update(result["verifiedIds"])
            item["status"]="verified-dry-run"
            if apply:
                item.update(_apply_candidate(candidate,replacement))
                item["status"]="patched-awaiting-final-verify"
        except Exception as exc:
            all_candidate_results_ok=False
            item["status"]="error"; item["error"]=str(exc)
            report["warnings"].append(f"{candidate.big_path.name}/{candidate.entry.name}: {exc}")
        report["archives"].append(item)

    if apply and all_candidate_results_ok:
        verified_ok, verified_ids=_verify_archive_candidate(game_root,target_archive,target_xml,legend_ids)
        report["verifiedLegendIds"] = verified_ids
        report["verifiedAllLegends"] = bool(verified_ok)
        if verified_ok:
            for item in report["archives"]:
                if item.get("status") == "patched-awaiting-final-verify": item["status"]="patched-verified"
        else:
            report["warnings"].append("On-disk Legend verification failed; restoring archive backups.")
            restore(game_root)
    elif not apply:
        report["verifiedLegendIds"] = sorted(verified_dry)
        report["verifiedAllLegends"] = bool(all_candidate_results_ok and legend_ids.issubset(verified_dry))
    if apply and not report["verifiedAllLegends"]:
        # Never leave an unverified partial client DB patch active.
        restore(game_root)
        report["warnings"].append("Not all 42 Legends verified; original archives restored and Legend My Club seeding remains disabled.")
        report["verifiedLegendIds"] = []
    return report


def make_synthetic_db(capacity: int=50, valid: int=1) -> tuple[bytes,bytes]:
    xml=b'''<database>
<table name="players" shortname="play">
  <field name="playerid" shortname="pid0" RangeLow="0"/>
  <field name="nationality" shortname="nat0" RangeLow="0"/>
  <field name="overallrating" shortname="ovr0" RangeLow="0"/>
  <field name="teamid" shortname="tid0" RangeLow="0"/>
  <field name="firstnameid" shortname="fnm0" RangeLow="0"/>
  <field name="lastnameid" shortname="lnm0" RangeLow="0"/>
  <field name="commonnameid" shortname="cnm0" RangeLow="0"/>
  <field name="playerjerseynameid" shortname="jnm0" RangeLow="0"/>
</table>
<table name="playernames" shortname="pnam">
  <field name="nameid" shortname="nid0" RangeLow="0"/>
  <field name="name" shortname="nam0"/>
</table>
</database>'''

    def build_table(record_size: int, rows_capacity: int, rows_valid: int,
                    fields: list[tuple[int,int,bytes,int]], records: bytes) -> bytes:
        table=bytearray()
        table+=b"\x00"*4+struct.pack("<I",record_size)+b"\x00"*4+struct.pack("<I",NO_COMPRESSED_STRINGS)
        table+=struct.pack("<HH",rows_capacity,rows_valid)+b"\x00"*4+bytes([len(fields)])+b"\x00"*11
        for typ,bo,sn,depth in fields:
            table+=struct.pack("<II",typ,bo)+sn+struct.pack("<I",depth)
        table+=records
        return bytes(table)

    precord_size=16
    pfields=[
        (3,0,b"pid0",24),(3,24,b"nat0",8),(3,32,b"ovr0",8),(3,40,b"tid0",24),
        (3,64,b"fnm0",16),(3,80,b"lnm0",16),(3,96,b"cnm0",16),(3,112,b"jnm0",16),
    ]
    precs=bytearray(precord_size*capacity)
    rec=bytearray(precord_size)
    write_bits_le(rec,0,24,100); write_bits_le(rec,24,8,14); write_bits_le(rec,32,8,80); write_bits_le(rec,40,24,1)
    write_bits_le(rec,64,16,1); write_bits_le(rec,80,16,1); write_bits_le(rec,96,16,1); write_bits_le(rec,112,16,1)
    precs[:precord_size]=rec
    players=build_table(precord_size,capacity,valid,pfields,bytes(precs))

    ncapacity=100; nvalid=1; nrecord_size=32
    nfields=[(3,0,b"nid0",16),(0,16,b"nam0",240)]
    nrecs=bytearray(nrecord_size*ncapacity)
    nrec=bytearray(nrecord_size)
    write_bits_le(nrec,0,16,1)
    nrec[2:2+5]=b"Donor"
    nrecs[:nrecord_size]=nrec
    names=build_table(nrecord_size,ncapacity,nvalid,nfields,bytes(nrecs))

    table_count=2
    db=bytearray(DB_HEADER)+b"\x00"*4+b"\x00"*4+struct.pack("<I",table_count)+b"\x00"*4
    db+=b"play"+struct.pack("<I",0)
    db+=b"pnam"+struct.pack("<I",len(players))
    db+=b"\x00"*4
    db+=players+names
    struct.pack_into("<I",db,8,len(db))
    return bytes(db),xml


def self_test() -> dict[str,Any]:
    db,xml=make_synthetic_db()
    legends=[{"assetId":190043,"name":"Pelé","position":"CF","nation":54,"rating":95,"teamId":LEGEND_TEAM_ID},
             {"assetId":1109,"name":"Paolo Maldini","position":"CB","nation":27,"rating":92,"teamId":LEGEND_TEAM_ID}]
    bases=[{"assetId":100,"name":"Donor","position":"CF","nation":14,"rating":80}]
    # The real user's patch.big has a completely full players table. Exercise
    # that exact condition here: capacity=valid=1 must grow before two Legends
    # can be appended.
    full_db,_ = make_synthetic_db(capacity=1, valid=1)
    grown_out,grown_result=inject_legends_into_db(full_db,xml,legends,bases)
    grown_parsed=parse_fifa_db(grown_out,parse_descriptor(xml)); grown_table,grown_index=index_players(grown_parsed)
    if grown_table.records_count < 3 or grown_table.valid_records_count != 3 or not {190043,1109}.issubset(grown_index):
        raise RuntimeError("full players-table growth regression failed")
    out,result=inject_legends_into_db(db,xml,legends,bases)
    parsed=parse_fifa_db(out,parse_descriptor(xml)); table,index=index_players(parsed)
    if not result["allVerified"] or 190043 not in index or 1109 not in index or table.valid_records_count!=3:
        raise RuntimeError("Legend DB synthetic append verification failed")
    # BIG + ViV4/chunkzip directory parser/replacement invariants. This is the
    # archive shape used by the actual FIFA 14 *.big/*.bh files.
    pairs=[("data/db/cards_ng_db.db", out), ("data/db/cards_ng_db-meta.xml", xml)]
    dir_size=16+sum(8+len(name.encode("utf-8"))+1 for name,_ in pairs)
    payload_offset=dir_size
    big=bytearray(b"BIGF"+struct.pack(">III",0,len(pairs),dir_size))
    cursor=payload_offset
    for name,blob in pairs:
        big+=struct.pack(">II",cursor,len(blob))+name.encode("utf-8")+b"\x00"
        cursor+=len(blob)
    for _name,blob in pairs: big+=blob
    struct.pack_into(">I",big,4,len(big))
    entries=parse_big(bytes(big))
    if len(entries)!=2 or extract_entry(bytes(big),entries[0])!=out:
        raise RuntimeError("BIG parser self-test failed")
    with tempfile.TemporaryDirectory(prefix="fifa14-v24022-legend-selftest-") as td:
        root=Path(td)
        info={"output_size":len(big),"chunk_size":len(big),"alignment":16,
              "chunks":[{"decoded_size":len(big),"compression_type":1}]}
        stored=encode_chunkzip(bytes(big),info)
        capacity=max(len(stored)+4096,8192)
        (root/"cards0.big").write_bytes(stored+b"\x00"*(capacity-len(stored)))
        bh=bytearray(BH_MAGIC+b"\x00\x00\x00\x00"+struct.pack(">I",1)+b"\x00\x00\x00\x00")
        bh+=struct.pack(">IIIII",0,len(stored),0,0,1)
        (root/"cards0.bh").write_bytes(bh)
        rows=scan_named_candidates(root/"cards0.big",root/"cards0.bh","cards_ng_db.db")
        if len(rows)!=1 or candidate_blob(rows[0])!=out:
            raise RuntimeError("ViV4/chunkzip cards_ng_db discovery self-test failed")
        replacement,result2=inject_legends_into_db(db,xml,legends,bases)
        _apply_candidate(rows[0],replacement)
        rows2=scan_named_candidates(root/"cards0.big",root/"cards0.bh","cards_ng_db.db")
        parsed2=parse_fifa_db(candidate_blob(rows2[0]),parse_descriptor(xml)); _,idx2=index_players(parsed2)
        if not {190043,1109}.issubset(idx2):
            raise RuntimeError("ViV4/chunkzip in-place write verification failed")

    # Retail-style hashed direct ViV4 records: no inner BIG filenames are
    # available, so discovery must work from descriptor/DB structure alone.
    with tempfile.TemporaryDirectory(prefix="fifa14-v24022-direct-record-selftest-") as td:
        root=Path(td)
        def cz(blob: bytes) -> bytes:
            info={"output_size":len(blob),"chunk_size":max(1,len(blob)),"alignment":16,
                  "chunks":[{"decoded_size":len(blob),"compression_type":1}]}
            return encode_chunkzip(blob,info)
        sx=cz(xml); sd=cz(out)
        off0=0; off1=align(len(sx)+4096,4096); total=off1+len(sd)+8192
        big=bytearray(total); big[off0:off0+len(sx)]=sx; big[off1:off1+len(sd)]=sd
        (root/"cards0.big").write_bytes(big)
        bh=bytearray(BH_MAGIC+b"\x00\x00\x00\x00"+struct.pack(">I",2)+b"\x00\x00\x00\x00")
        bh+=struct.pack(">IIIII",off0,len(sx),0,0,0x1111)
        bh+=struct.pack(">IIIII",off1,len(sd),0,0,0x2222)
        (root/"cards0.bh").write_bytes(bh)
        desc_found=discover_descriptor_record(root/"cards0.big",root/"cards0.bh")
        if desc_found is None:
            raise RuntimeError("hashed direct-record descriptor discovery self-test failed")
        direct=discover_fifa_db_records(root/"cards0.big",root/"cards0.bh",desc_found[0],[100,190043,1109])
        if len(direct)!=1:
            raise RuntimeError("hashed direct-record cards DB discovery self-test failed")
        parsed3=parse_fifa_db(candidate_blob(direct[0]),parse_descriptor(desc_found[0])); _,idx3=index_players(parsed3)
        if not {190043,1109}.issubset(idx3):
            raise RuntimeError("hashed direct-record DB identity verification failed")
    return {"ok":True,"playersValid":table.valid_records_count,"legendIds":sorted([190043,1109]),
            "viv4Chunkzip":True,"hashedDirectRecords":True,"fullPlayersTableExpansion":True}


def main() -> int:
    ap=argparse.ArgumentParser(description="Patch FIFA 14 PC FUT cards_ng_db with the 42 FUT Legends")
    ap.add_argument("--game-root", type=Path)
    ap.add_argument("--legend-catalog", type=Path)
    ap.add_argument("--base-catalog", type=Path)
    ap.add_argument("--output-report", type=Path)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args=ap.parse_args()
    if args.self_test:
        print(json.dumps(self_test(),indent=2,ensure_ascii=False)); return 0
    if args.game_root is None or args.output_report is None:
        ap.error("--game-root and --output-report are required")
    root=args.game_root.expanduser().resolve()
    if args.restore:
        result=restore(root)
    else:
        if args.legend_catalog is None or args.base_catalog is None:
            ap.error("--legend-catalog and --base-catalog are required unless --restore")
        legends=json.loads(args.legend_catalog.read_text(encoding="utf-8")).get("players",[])
        bases=json.loads(args.base_catalog.read_text(encoding="utf-8")).get("players",[])
        if len(legends)!=42:
            raise RuntimeError(f"Legend catalogue must contain 42 players, got {len(legends)}")
        result=patch_game(root,legends,bases,args.apply)
    args.output_report.parent.mkdir(parents=True,exist_ok=True)
    args.output_report.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({"output":str(args.output_report),"verifiedAllLegends":bool(result.get("verifiedAllLegends")),"action":result.get("action"),"error":result.get("error")},ensure_ascii=False))
    # A failed/unsupported client DB patch is a controlled state, not a launcher
    # crash. The state-prep gate reads verifiedAllLegends and simply leaves
    # Legends disabled.
    return 0


if __name__=="__main__":
    raise SystemExit(main())
