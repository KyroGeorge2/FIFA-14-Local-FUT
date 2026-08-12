from __future__ import annotations

import argparse
import base64
import itertools
import json
import os
import re
import secrets
import shutil
import ssl
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

SERVER_DIRECTORY = Path(__file__).resolve().parent
if str(SERVER_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SERVER_DIRECTORY))

from local_identity import LocalIdentityStore, PLAYER_CATALOG, PLAYER_BY_ASSET, PLAYER_REFERENCE_BY_ASSET
from beta_identity import BetaIdentityStore


def emit(kind: str, **fields) -> None:
    print(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}), flush=True)


def build_fut_account_info(account_mode: str = "new") -> dict:
    """Return a genuinely unused local FIFA 14 FUT persona.

    ``userClubList`` must be empty. A placeholder entry with zero IDs is still
    interpreted as an existing/founder-style FUT identity by the FIFA 14 home
    tile and prevents the intended first-use "Play FUT Now" presentation.
    """
    persona = {
        "personaId": 1_000_001,
        "personaName": "LocalFUT",
        "returningUser": 0,
        "onlineAccess": True,
        "trial": False,
        "userState": None,
        "userClubList": [],
        "trialFree": False,
    }
    return {"userAccountInfo": {"personas": [persona]}}


def build_fut_account_info_payload(account_mode: str = "new") -> bytes:
    return (
        json.dumps(build_fut_account_info(account_mode), separators=(",", ":")) + "\n"
    ).encode("utf-8")


def build_fut_json_payload(document: dict) -> bytes:
    return (json.dumps(document, separators=(",", ":")) + "\n").encode("utf-8")


ICEBREAKER_FIXTURE_PATH = SERVER_DIRECTORY / "icebreakerpacklist.v27.json"
ICEBREAKER_PLAYER_ARRAY_FIELDS = (
    "squad",
    "teamId",
    "Rating",
    "Rare",
    "playStyle",
    "Attribute1",
    "Attribute2",
    "Attribute3",
    "Attribute4",
    "Attribute5",
    "Attribute6",
)
ICEBREAKER_ROLE_ARRAY_FIELDS = ("kicktakers", "squadActives")
ICEBREAKER_FORMATIONS = {
    "f3412", "f3421", "f343", "f352", "f41212", "f4231",
    "f4222", "f4312", "f4321", "f433", "f4411", "f442",
    "f451", "f5212", "f5221", "f532", "f541", "f41212a",
    "f4141", "f4231a", "f433a", "f433b", "f433c", "f433d",
    "f442a", "f451a",
}


def validate_icebreaker_fixture(document: object) -> dict:
    """Validate the complete retail captain pack-entry contract.

    CardsDLLzf.dll parses each entry into a fixed 0x1E0-byte native record.
    The player-facing arrays have exactly 23 elements; kick takers and their
    active flags have exactly six. Rejecting malformed fixtures in Python is
    safer than letting the legacy native parser consume null/default values.
    """
    if not isinstance(document, dict):
        raise ValueError("fixture root must be a JSON object")
    pack_list = document.get("packList")
    if not isinstance(pack_list, list) or len(pack_list) != 4:
        raise ValueError("packList must contain exactly four captain entries")

    seen_ids: set[int] = set()
    seen_images: set[int] = set()
    for pack_index, pack in enumerate(pack_list):
        if not isinstance(pack, dict):
            raise ValueError(f"packList[{pack_index}] must be an object")
        for scalar in ("id", "image", "manager"):
            value = pack.get(scalar)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"packList[{pack_index}].{scalar} must be a non-negative integer")
        formation = pack.get("formation")
        if formation not in ICEBREAKER_FORMATIONS:
            raise ValueError(
                f"packList[{pack_index}].formation must be a retail formation token; got {formation!r}"
            )
        if pack["id"] in seen_ids or pack["image"] in seen_images:
            raise ValueError("captain id and image values must be unique")
        seen_ids.add(pack["id"])
        seen_images.add(pack["image"])

        for field in ICEBREAKER_PLAYER_ARRAY_FIELDS:
            values = pack.get(field)
            if not isinstance(values, list) or len(values) != 23:
                raise ValueError(f"packList[{pack_index}].{field} must contain exactly 23 integers")
            for value_index, value in enumerate(values):
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(
                        f"packList[{pack_index}].{field}[{value_index}] must be an integer"
                    )
                if field in {"squad", "teamId"} and value <= 0:
                    raise ValueError(
                        f"packList[{pack_index}].{field}[{value_index}] must be non-zero"
                    )
                if field == "Rating" or field.startswith("Attribute"):
                    if value < 0 or value > 99:
                        raise ValueError(
                            f"packList[{pack_index}].{field}[{value_index}] must be in 0..99"
                        )
                elif field in {"Rare", "playStyle"} and not 0 <= value <= 255:
                    raise ValueError(
                        f"packList[{pack_index}].{field}[{value_index}] must be in 0..255"
                    )

        for field in ICEBREAKER_ROLE_ARRAY_FIELDS:
            values = pack.get(field)
            if not isinstance(values, list) or len(values) != 6:
                raise ValueError(f"packList[{pack_index}].{field} must contain exactly six integers")
            for value_index, value in enumerate(values):
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(
                        f"packList[{pack_index}].{field}[{value_index}] must be a non-negative integer"
                    )

        if len(set(pack["squad"])) != 23:
            raise ValueError(f"packList[{pack_index}].squad must contain 23 unique player resource IDs")

    if seen_ids != {0, 1, 2, 3} or seen_images != {0, 1, 2, 3}:
        raise ValueError("captain id/image values must be exactly 0, 1, 2 and 3")
    return document


def load_icebreaker_fixture(path: Path = ICEBREAKER_FIXTURE_PATH) -> dict:
    if not path.is_file():
        raise ValueError(f"Icebreaker fixture is missing: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read Icebreaker fixture {path}: {error}") from error
    return validate_icebreaker_fixture(document)


def build_fut_auth_response(sid: str) -> dict:
    """Return the exact three top-level fields recognized by FIFA 14 PC.

    Static reversing of the uploaded CardsDLL maps only ``sid``,
    ``serverTime`` and ``lastOnlineTime`` in the Authentication response.
    Historical FIFA 14 clients also deserialize the session identifier from
    the JSON body, so the SID is deliberately returned in both body and header.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {
        "sid": sid,
        "serverTime": now.isoformat().replace("+00:00", "Z"),
        "lastOnlineTime": "1970-01-01T00:00:00Z",
    }


def build_fut_settings_response() -> dict:
    """Fields read by FIFA 14's exact FutSettings response parser."""
    return {
        "maximumTradePileSize": 30,
        "getOperationTimeoutSec": 300,
        "clubCreateThreshold": 0,
        "fifaPointsCancelTransactionFix": 1,
        "tokenRedemptionEnabled": 0,
        "enableWorldCupMode": 0,
    }


def build_fut_no_transaction_response() -> dict:
    # Prevent an empty object from being interpreted as transaction id zero.
    return {"transactionId": 0, "state": "NOTRANSACTION"}


def build_fut_empty_pile_sizes_response() -> dict:
    # BETA 2.25.0: an empty pile-size response leaves the Transfer List at 0/0
    # and causes every add to be treated as full. These are the retail-capacity
    # entries used by the working FIFA 14 revival implementation.
    return {"entries": [{"key": 2, "value": 20000}, {"key": 3, "value": 20000}, {"key": 4, "value": 20000}]}


def build_fut_empty_user_data_response() -> dict:
    return {"userData": []}


def build_fut_boot_config_payload() -> bytes:
    """Return the smallest FutCfg document accepted by FIFA 14's native parser.

    The nesting and scalar fields come from read-only disassembly of the running
    1.7.0.0 PC client.  Non-zero cfg/minor/revision IDs satisfy the four fields
    checked after parsing; every identifier remains synthetic and local-only.
    """
    return b"""<?xml version="1.0" encoding="utf-8"?>
<FutCfg>
  <cfgVersion>1</cfgVersion>
  <futDlc>
    <fut12>
      <minorVersion>1</minorVersion>
      <bootString>fut12</bootString>
      <futNotAvailable>0</futNotAvailable>
      <revision>
        <futSubVersion>1</futSubVersion>
        <Language>
          <dimeUniqueId>1</dimeUniqueId>
          <size>1</size>
        </Language>
      </revision>
      <key>
        <dimeUniqueId>2</dimeUniqueId>
        <futKeyType>0</futKeyType>
      </key>
    </fut12>
  </futDlc>
</FutCfg>
"""


def build_fut_phishing_question(account_mode: str = "new") -> dict:
    """Return the FIFA-era security-question status document."""
    if account_mode == "existing":
        return {
            "debug": "Already answered question.",
            "token": "LOCAL-FIFA14-PHISHING",
        }
    return {
        "question": 0,
        "attempts": 5,
        "recoverAttempts": 20,
    }


def build_fut_phishing_validation() -> dict:
    """Accept a local-only onboarding answer and establish a trusted session."""
    return {
        "debug": "Answer is correct.",
        "string": "OK",
        "code": "200",
        "reason": "Answer is correct.",
        "token": "LOCAL-FIFA14-PHISHING",
    }


def build_fut_trusted_console_list() -> dict:
    """Return an untrusted/new-device result for CardsDLL operation 92.

    The retail parser initializes all four trusted-console booleans to false and
    treats absent optional fields as valid.  An empty 200 JSON object therefore
    preserves the native new-device path without inventing a trusted console.
    """
    return {}


def describe_tcp_payload(payload: bytes) -> dict:
    if not payload:
        return {"protocol_guess": "empty"}

    first = payload[0]
    if first == 0x16 and len(payload) >= 5:
        version_major, version_minor = payload[1], payload[2]
        record_len = int.from_bytes(payload[3:5], "big")
        return {
            "protocol_guess": "tls-handshake",
            "tls_record_version": f"{version_major}.{version_minor}",
            "tls_record_length": record_len,
        }

    if len(payload) >= 12:
        length, component, command, error = struct.unpack_from(">HHHH", payload, 0)
        frame_type = payload[8] >> 4
        options = payload[9] >> 4
        sequence = struct.unpack_from(">H", payload, 10)[0]
        plausible = length <= max(0, len(payload) - 12) or length < 65535
        return {
            "protocol_guess": "blaze-fire" if plausible else "unknown",
            "fire_length": length,
            "fire_component": component,
            "fire_command": command,
            "fire_error": error,
            "fire_type": frame_type,
            "fire_options": options,
            "fire_sequence": sequence,
        }

    return {"protocol_guess": "unknown"}


TDF_VAR_INT = 0x0
TDF_STRING = 0x1
TDF_BLOB = 0x2
TDF_GROUP = 0x3
TDF_LIST = 0x4
TDF_MAP = 0x5
TDF_TAGGED_UNION = 0x6
TDF_VAR_INT_LIST = 0x7
TDF_OBJECT_TYPE = 0x8
TDF_OBJECT_ID = 0x9


# Cross-platform FIFA 14 Blaze routes recovered from the public Xbox 360
# revival project.  The wire format and generated Blaze component schemas are
# shared with the PC title even though the Xbox launch patches and
# Authentication2 flow are not.  Advertising these services lets the PC
# client ask for its normal post-login OSDK bootstrap instead of being limited
# to the small observation-only component set used by the first probe.
AUTHENTICATION_COMPONENT = 1
STATS_COMPONENT = 7
CENSUS_COMPONENT = 10
CLUBS_COMPONENT = 11
MESSAGING_COMPONENT = 15
ROOMS_COMPONENT = 21
ASSOCIATION_LISTS_COMPONENT = 25
GAME_REPORTING_COMPONENT = 28
GAME_REPORTING_RESULT_NOTIFICATION = 114
SPONSORED_EVENTS_COMPONENT = 0x081C
# Retail FIFA 14 generated Blaze::EASFC component. Static component constructor
# uses component id 0x081D; commands 1-4 are purchaseGameWin/Match/Loss/Draw.
EASFC_COMPONENT = 0x081D
CARDHOUSE_COMPONENT = 2148
OSDK_SETTINGS_COMPONENT = 2249
OSDK_ONLINE_PASS_COMPONENT = 2268

FIFA14_SHARED_BOOTSTRAP_COMPONENTS = (
    CENSUS_COMPONENT,
    CLUBS_COMPONENT,
    ROOMS_COMPONENT,
    SPONSORED_EVENTS_COMPONENT,
    EASFC_COMPONENT,
    CARDHOUSE_COMPONENT,
    OSDK_SETTINGS_COMPONENT,
    OSDK_ONLINE_PASS_COMPONENT,
)


# Keep the active login shape explicit in the trace.  This is the Blaze 3
# OriginLogin response used by Pocket Relay: one stable local player/account ID,
# a server-issued session token, and a non-empty local account identity.
ORIGIN_LOGIN_VARIANTS = itertools.cycle(("pocket-relay-origin",))


# FIFA 14 requests these five maps before OriginLogin.  The names and keys below
# are present in the retail PC executable.  Values intentionally keep optional
# EA web features local or disabled while allowing the OSDK login state machine
# to finish its configuration phase.
FIFA14_CLIENT_CONFIGS: dict[str, tuple[tuple[str, str], ...]] = {
    "OSDK_CORE": (
        ("JOIN_GAME_TIMEOUT", "60000"),
        ("OSDK_DISTBUFFERSIZE_IN", "32768"),
        ("OSDK_DISTBUFFERSIZE_OUT", "32768"),
        ("OSDK_KEEPALIVEINTERVAL", "30000"),
        ("OSDK_MATCHUP_TIMEOUT", "60000"),
        ("OSDK_MAXGAMES", "100"),
        ("OSDK_MAXROOMS", "100"),
        ("OSDK_PEERBUFFERSIZE", "32768"),
        ("OSDK_REGISTER_PRODUCT", "0"),
        ("OSDK_TICKER_COUNT", "0"),
    ),
    "OSDK_CLIENT": (
        # Exact expanded PC profile from the 2026-08-03 runs that fetched
        # FutCfg and then emitted native accountinfo with a populated local
        # Blaze session.  Keep this controlled baseline intact.
        ("FUTBOOTCFGFILE_URL", "http://127.0.0.1:8080/futBoot.xml"),
        ("FUT_RS4_BASE_URL", "http://127.0.0.1:8099/"),
        ("FUT_URI", "http://127.0.0.1:8099/"),
        ("CARDS/DIRECTED_BLAZEENV", "prod"),
        ("FCC/FUT_DEPLOY_LANGUAGE", "en_US"),
        ("FUT_ENABLE_MENU", "1"),
        ("FUT_RS4_APIURL_PC", "http://127.0.0.1:8099/"),
        ("FUT_RS4_URL_PC", "http://127.0.0.1:8099/"),
        # The retail CardsDLL falls back to a dead fifa13 test host on TCP
        # 8306 when this key is absent.  It appends /fut/ itself, so the base
        # intentionally has no trailing slash.
        ("FUTDYNAMICMESSAGES_URL_BASE", "http://127.0.0.1:8099"),
        ("FUTDYNAMICMESSAGES_URL_GET_MESSAGES", "/messages"),
        ("FUTDYNAMICMESSAGES_TUTORIAL_MSG_URL", "/tutorials"),
        ("FUTDYNAMICMESSAGES_REQUEST_TIMEOUT", "5000"),
        ("FUTDYNAMICMESSAGES_REFRESH_INTERVAL", "300000"),
        ("FUT/MODULE_BASEURL_PC", "http://127.0.0.1:8099/"),
        ("FUT/SINGLE_BASEURL_PC", "http://127.0.0.1:8099/"),
        ("ONLINE/NO_AUTO_SQUAD", "0"),
        ("FUT/FORCE_TUTORIALS", "1"),
        ("FUT/DISABLE_TUTORIALS", "0"),
        ("FUT/ALWAYS_SHOW_SMART_TUTORIALS", "1"),
        ("FUT/IS_RETURNING_USER", "0"),
        ("FUT_SKIP_ICEBREAKER_FLOW", "0"),
        ("OSDK_DDP_UPGRADE_TO_DDR_ENABLED", "0"),
        ("OSDK_REGISTER_PRODUCT", "0"),
        ("OSDK_TOLLBOOTH_DDP_COMMERCE_ENABLED", "0"),
        ("OSDK_TOLLBOOTH_DDR_ONLINE_PASS_ENABLED", "0"),
        ("OSDK_TOLLBOOTH_ONLINE_PASS_ENABLED", "0"),
        ("OSDK_TOLLBOOTH_SEASON_TICKET_ENABLED", "0"),
        ("OSDK_TOLLBOOTH_SHOW_SEASON_TICKET_AT_LOGIN", "0"),
    ),
    "OSDK_NUCLEUS": (
        ("NUCLEUS_ADDED_URL", ""),
        ("NUCLEUS_CREATE_INFO_URL", ""),
        ("NUCLEUS_CREATE_URL", ""),
        ("NUCLEUS_DEACTIVATED_INFO_URL", ""),
        ("NUCLEUS_DUPACCT_INFO_URL", ""),
        ("NUCLEUS_INCOMPLETE_URL", ""),
        ("OSDK_EASW_ALLOWED_LOCALES", "en_US,en_GB"),
        ("OSDK_EASW_CONNECT_RETRY_PERIOD", "5"),
        ("OSDK_REGISTER_PRODUCT", "0"),
    ),
    "OSDK_WEBOFFER": (
        ("FAQ_URL", ""),
        ("MENU_ESPN_URL", ""),
        ("MENU_WEBGM0_URL", ""),
        ("MENU_WEBGM1_URL", ""),
        ("MENU_WEBGM2_URL", ""),
        ("NEWS_URL", ""),
        ("TOSAC_URL", ""),
        ("TOSA_URL", ""),
        ("WEB_OFFER_URL", ""),
    ),
    "OSDK_XMS_ABUSE_REPORTING": (
        ("OSDK_ABUSE_NUM_TYPES", "0"),
        ("OSDK_XMS_DEFAULT_VIEW_URL", ""),
    ),
}

# These names are embedded in the retail client and are exposed as an explicit
# controlled experiment. They ask FIFA to use its own FUT direct/bootstrap
# route; they do not patch code, suppress ownership checks, or alter the game
# files. Keep them out of the baseline response for a clean A/B comparison.
FIFA14_FUT_DIRECT_BOOT_CONFIG: tuple[tuple[str, str], ...] = (
    ("LoadFUTSkipBlaze", "1"),
    ("DirectBootFUT", "1"),
    ("FUT_DIRECT_BOOT", "1"),
    ("FUT_ENABLE_MENU", "1"),
)


def tdf_tag(tag: bytes, value_type: int) -> bytes:
    """Encode a Blaze/TDF tag header.

    Mirrors the tdf 0.4.0 Tagged::serialize_raw implementation used by
    PocketRelay. Tags must be ASCII and at most four bytes.
    """
    if not tag or len(tag) > 4:
        raise ValueError(f"invalid TDF tag {tag!r}")
    out = [0, 0, 0, value_type & 0xFF]
    length = len(tag)
    if length > 0:
        out[0] |= (tag[0] & 0x40) << 1
        out[0] |= (tag[0] & 0x10) << 2
        out[0] |= (tag[0] & 0x0F) << 2
    if length > 1:
        out[0] |= (tag[1] & 0x40) >> 5
        out[0] |= (tag[1] & 0x10) >> 4
        out[1] |= (tag[1] & 0x0F) << 4
    if length > 2:
        out[1] |= (tag[2] & 0x40) >> 3
        out[1] |= (tag[2] & 0x10) >> 2
        out[1] |= (tag[2] & 0x0C) >> 2
        out[2] |= (tag[2] & 0x03) << 6
    if length > 3:
        out[2] |= (tag[3] & 0x40) >> 1
        out[2] |= tag[3] & 0x1F
    return bytes(out)


def tdf_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative TDF varints are not used here")
    if value < 0x40:
        return bytes([value])
    out = bytearray()
    out.append((value & 0x3F) | 0x80)
    value >>= 6
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def read_tdf_varint(payload: bytes, offset: int = 0) -> tuple[int, int]:
    if offset >= len(payload):
        raise ValueError("missing TDF varint")
    first = payload[offset]
    offset += 1
    value = first & 0x3F
    shift = 6
    current = first
    while current & 0x80:
        if offset >= len(payload):
            raise ValueError("truncated TDF varint")
        current = payload[offset]
        offset += 1
        value |= (current & 0x7F) << shift
        shift += 7
    return value, offset


def decode_tdf_tag(encoded: bytes) -> str:
    """Decode the three-byte Blaze tag representation into its ASCII name."""
    if len(encoded) != 3:
        raise ValueError("a TDF tag must contain exactly three encoded bytes")
    values = (
        (encoded[0] >> 2) & 0x3F,
        ((encoded[0] & 0x03) << 4) | ((encoded[1] >> 4) & 0x0F),
        ((encoded[1] & 0x0F) << 2) | ((encoded[2] >> 6) & 0x03),
        encoded[2] & 0x3F,
    )
    return "".join(chr(value + 0x20) for value in values).rstrip()


def _decode_tdf_value(payload: bytes, offset: int, value_type: int) -> tuple[object, int]:
    """Decode one untagged TDF value; used by fields, lists, and maps."""
    if value_type == TDF_VAR_INT:
        return read_tdf_varint(payload, offset)
    if value_type == TDF_STRING:
        length, offset = read_tdf_varint(payload, offset)
        end = offset + length
        if length < 1 or end > len(payload) or payload[end - 1] != 0:
            raise ValueError("invalid or truncated TDF string")
        try:
            value = payload[offset : end - 1].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("invalid UTF-8 in TDF string") from error
        return value, end
    if value_type == TDF_BLOB:
        length, offset = read_tdf_varint(payload, offset)
        end = offset + length
        if end > len(payload):
            raise ValueError("truncated TDF blob")
        return payload[offset:end], end
    if value_type == TDF_GROUP:
        return _decode_tdf_fields(payload, offset, stop_at_group_end=True)
    if value_type == TDF_LIST:
        if offset >= len(payload):
            raise ValueError("missing TDF list element type")
        element_type = payload[offset]
        count, offset = read_tdf_varint(payload, offset + 1)
        values = []
        for _ in range(count):
            value, offset = _decode_tdf_value(payload, offset, element_type)
            values.append(value)
        return {"element_type": element_type, "values": values}, offset
    if value_type == TDF_MAP:
        if offset + 2 > len(payload):
            raise ValueError("missing TDF map key/value types")
        key_type, mapped_type = payload[offset : offset + 2]
        count, offset = read_tdf_varint(payload, offset + 2)
        values = []
        for _ in range(count):
            key, offset = _decode_tdf_value(payload, offset, key_type)
            value, offset = _decode_tdf_value(payload, offset, mapped_type)
            values.append((key, value))
        return {"key_type": key_type, "value_type": mapped_type, "values": values}, offset
    if value_type == TDF_TAGGED_UNION:
        if offset >= len(payload):
            raise ValueError("missing TDF tagged-union discriminator")
        active_member = payload[offset]
        offset += 1
        if active_member == 0x7F:
            return {"active_member": active_member, "field": None}, offset
        fields, offset = _decode_tdf_fields(payload, offset, max_fields=1)
        if len(fields) != 1:
            raise ValueError("tagged union did not contain one active field")
        return {"active_member": active_member, "field": fields[0]}, offset
    if value_type == TDF_VAR_INT_LIST:
        count, offset = read_tdf_varint(payload, offset)
        values = []
        for _ in range(count):
            value, offset = read_tdf_varint(payload, offset)
            values.append(value)
        return values, offset
    raise ValueError(f"unsupported TDF value type 0x{value_type:02x}")


def _decode_tdf_fields(
    payload: bytes,
    offset: int = 0,
    *,
    stop_at_group_end: bool = False,
    max_fields: int | None = None,
) -> tuple[list[dict[str, object]], int]:
    fields: list[dict[str, object]] = []
    while offset < len(payload) and (max_fields is None or len(fields) < max_fields):
        if payload[offset] == 0:
            if not stop_at_group_end:
                raise ValueError(f"unexpected TDF group terminator at offset {offset}")
            return fields, offset + 1
        if offset + 4 > len(payload):
            raise ValueError("truncated TDF field header")
        tag = decode_tdf_tag(payload[offset : offset + 3])
        value_type = payload[offset + 3]
        value, next_offset = _decode_tdf_value(payload, offset + 4, value_type)
        fields.append({"tag": tag, "type": value_type, "value": value})
        offset = next_offset
    if stop_at_group_end and max_fields is None:
        raise ValueError("unterminated TDF group")
    return fields, offset


def decode_tdf_document(payload: bytes) -> list[dict[str, object]]:
    """Strictly decode an entire TDF document and reject trailing bytes."""
    fields, offset = _decode_tdf_fields(payload)
    if offset != len(payload):
        raise ValueError(f"trailing TDF bytes at offset {offset}")
    return fields


def validate_origin_login_body(payload: bytes) -> list[dict[str, object]]:
    """Validate the response against Blaze3 FullLoginResponse's exact schema."""
    fields = decode_tdf_document(payload)
    top_level = [(field["tag"], field["type"]) for field in fields]
    expected_top_level = [
        ("AGUP", TDF_VAR_INT),
        ("LDHT", TDF_STRING),
        ("NTOS", TDF_VAR_INT),
        ("PCTK", TDF_STRING),
        ("PRIV", TDF_STRING),
        ("SESS", TDF_GROUP),
        ("SPAM", TDF_VAR_INT),
        ("THST", TDF_STRING),
        ("TSUI", TDF_STRING),
        ("TURI", TDF_STRING),
    ]
    if top_level != expected_top_level:
        raise ValueError(f"FullLoginResponse field mismatch: {top_level!r}")
    session_fields = fields[5]["value"]
    if not isinstance(session_fields, list):
        raise ValueError("SESS did not decode as a group")
    session = [(field["tag"], field["type"]) for field in session_fields]
    expected_session = [
        ("BUID", TDF_VAR_INT),
        ("FRST", TDF_VAR_INT),
        ("KEY", TDF_STRING),
        ("LLOG", TDF_VAR_INT),
        ("MAIL", TDF_STRING),
        ("PDTL", TDF_GROUP),
        ("UID", TDF_VAR_INT),
    ]
    if session != expected_session:
        raise ValueError(f"SessionInfo field mismatch: {session!r}")
    persona_fields = session_fields[5]["value"]
    if not isinstance(persona_fields, list):
        raise ValueError("PDTL did not decode as a group")
    persona = [(field["tag"], field["type"]) for field in persona_fields]
    expected_persona = [
        ("DSNM", TDF_STRING),
        ("LAST", TDF_VAR_INT),
        ("PID", TDF_VAR_INT),
        ("STAS", TDF_VAR_INT),
        ("XREF", TDF_VAR_INT),
        ("XTYP", TDF_VAR_INT),
    ]
    if persona != expected_persona:
        raise ValueError(f"PersonaDetails field mismatch: {persona!r}")
    return fields


def tdf_u32(tag: bytes, value: int) -> bytes:
    return tdf_tag(tag, TDF_VAR_INT) + tdf_varint(value)


def tdf_u16(tag: bytes, value: int) -> bytes:
    return tdf_tag(tag, TDF_VAR_INT) + tdf_varint(value)


def tdf_bool(tag: bytes, value: bool) -> bytes:
    return tdf_tag(tag, TDF_VAR_INT) + bytes([1 if value else 0])


def tdf_raw_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return tdf_varint(len(encoded) + 1) + encoded + b"\x00"


def tdf_string(tag: bytes, value: str) -> bytes:
    return tdf_tag(tag, TDF_STRING) + tdf_raw_string(value)


def tdf_group(tag: bytes, body: bytes) -> bytes:
    return tdf_tag(tag, TDF_GROUP) + body + b"\x00"


def tdf_list_u32(tag: bytes, values: list[int] | tuple[int, ...]) -> bytes:
    return (
        tdf_tag(tag, TDF_LIST)
        + bytes([TDF_VAR_INT])
        + tdf_varint(len(values))
        + b"".join(tdf_varint(value) for value in values)
    )


def tdf_list_strings(tag: bytes, values: list[str] | tuple[str, ...]) -> bytes:
    return (
        tdf_tag(tag, TDF_LIST)
        + bytes([TDF_STRING])
        + tdf_varint(len(values))
        + b"".join(tdf_raw_string(value) for value in values)
    )


def tdf_list_groups(tag: bytes, values: list[bytes] | tuple[bytes, ...]) -> bytes:
    """Encode a TDF list of structures.

    List elements do not carry a tag of their own; every structure is its
    encoded field body followed by the standard group terminator.
    """
    return (
        tdf_tag(tag, TDF_LIST)
        + bytes([TDF_GROUP])
        + tdf_varint(len(values))
        + b"".join(value + b"\x00" for value in values)
    )


def tdf_map_strings(tag: bytes, values: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> bytes:
    return (
        tdf_tag(tag, TDF_MAP)
        + bytes([TDF_STRING, TDF_STRING])
        + tdf_varint(len(values))
        + b"".join(tdf_raw_string(key) + tdf_raw_string(value) for key, value in values)
    )


def tdf_empty_map(tag: bytes, key_type: int, value_type: int) -> bytes:
    return tdf_tag(tag, TDF_MAP) + bytes([key_type, value_type]) + tdf_varint(0)


def tdf_blob(tag: bytes, value: bytes = b"") -> bytes:
    return tdf_tag(tag, TDF_BLOB) + tdf_varint(len(value)) + value


def tdf_empty_list(tag: bytes, value_type: int) -> bytes:
    return tdf_tag(tag, TDF_LIST) + bytes([value_type]) + tdf_varint(0)


def tdf_empty_varint_list(tag: bytes) -> bytes:
    return tdf_tag(tag, TDF_VAR_INT_LIST) + tdf_varint(0)


def tdf_map_u32(tag: bytes, values: list[tuple[int, int]] | tuple[tuple[int, int], ...]) -> bytes:
    return (
        tdf_tag(tag, TDF_MAP)
        + bytes([TDF_VAR_INT, TDF_VAR_INT])
        + tdf_varint(len(values))
        + b"".join(tdf_varint(key) + tdf_varint(value) for key, value in values)
    )


def extract_tdf_string(payload: bytes, tag: bytes) -> str | None:
    marker = tdf_tag(tag, TDF_STRING)
    position = payload.find(marker)
    if position < 0:
        return None
    try:
        length, value_offset = read_tdf_varint(payload, position + len(marker))
    except ValueError:
        return None
    if length < 1 or value_offset + length > len(payload):
        return None
    encoded = payload[value_offset : value_offset + length - 1]
    try:
        return encoded.decode("utf-8")
    except UnicodeDecodeError:
        return None


def extract_tdf_string_list(payload: bytes, tag: bytes) -> tuple[str, ...]:
    """Return a top-level Blaze TDF string-list field, or an empty tuple."""
    try:
        fields = decode_tdf_document(payload)
    except ValueError:
        return ()
    wanted = tag.decode("ascii")
    for field in fields:
        if field.get("tag") != wanted or field.get("type") != TDF_LIST:
            continue
        value = field.get("value")
        if not isinstance(value, dict) or value.get("element_type") != TDF_STRING:
            continue
        values = value.get("values")
        if not isinstance(values, list):
            continue
        return tuple(item for item in values if isinstance(item, str) and item)
    return ()


def extract_tdf_u32(payload: bytes, tag: bytes) -> int | None:
    marker = tdf_tag(tag, TDF_VAR_INT)
    position = payload.find(marker)
    if position < 0:
        return None
    try:
        value, _ = read_tdf_varint(payload, position + len(marker))
    except ValueError:
        return None
    return value


def extract_tdf_varint_last(payload: bytes, tag: bytes) -> int | None:
    """Return the last matching integer field, useful for nested report GRID tags."""
    marker = tdf_tag(tag, TDF_VAR_INT)
    position = payload.rfind(marker)
    if position < 0:
        return None
    try:
        value, _ = read_tdf_varint(payload, position + len(marker))
    except ValueError:
        return None
    return value


def build_game_reporting_result_notification_body(game_reporting_id: int = 0) -> bytes:
    """Minimal Blaze::GameReporting::ResultNotification terminal-success body."""
    safe_id = max(0, int(game_reporting_id))
    return (
        tdf_u32(b"EROR", 0)
        + tdf_bool(b"FNL", True)
        + tdf_u32(b"GHID", safe_id)
        + tdf_u32(b"GRID", safe_id)
    )


def build_redirector_body(host: str = "127.0.0.1", port: int = 42128) -> bytes:
    ip_value = int(IPv4Address(host))
    valu_group = (
        tdf_tag(b"VALU", TDF_GROUP)
        + tdf_u32(b"IP", ip_value)
        + tdf_u16(b"PORT", port)
        + b"\x00"
    )
    return (
        tdf_tag(b"ADDR", TDF_TAGGED_UNION)
        + b"\x00"
        + valu_group
        + tdf_bool(b"SECU", False)
        + tdf_bool(b"XDNS", False)
    )


def build_pre_auth_body(service_name: str = "fifa-2014-pc") -> bytes:
    """Build the Blaze Util.PreAuth response used to bootstrap the local session."""
    component_ids = (
        0x1,
        0x19,
        0x4,
        0x1C,
        0x7,
        0x9,
        0xF802,
        0x7800,
        0xF,
        0x7801,
        0x7802,
        0x7803,
        0x7805,
        0x7806,
        0x7D0,
        *FIFA14_SHARED_BOOTSTRAP_COMPONENTS,
    )
    client_config = tdf_map_strings(
        b"CONF",
        (
            ("pingPeriod", "30s"),
            ("voipHeadsetUpdateRate", "1000"),
            ("xlspConnectionIdleTimeout", "300"),
        ),
    )
    qos_body = (
        tdf_group(
            b"BWPS",
            tdf_string(b"PSA", "0")
            + tdf_u16(b"PSP", 0)
            + tdf_string(b"SNA", "prod-sjc"),
        )
        # Blaze still advertises one probe even when the actual ping-site map
        # is disabled.  FIFA uses this response shape while constructing its
        # local session state.
        + tdf_u16(b"LNP", 1)
        + tdf_empty_map(b"LTPS", TDF_STRING, TDF_GROUP)
        + tdf_u32(b"SVID", 0x45410805)
    )
    return (
        tdf_bool(b"ANON", False)
        + tdf_string(b"ASRC", "303107")
        + tdf_list_u32(b"CIDS", component_ids)
        + tdf_string(b"CNGN", "")
        + tdf_group(b"CONF", client_config)
        + tdf_string(b"INST", service_name)
        + tdf_bool(b"MINR", False)
        + tdf_string(b"NASP", "cem_ea_id")
        + tdf_string(b"PILD", "")
        + tdf_string(b"PLAT", "pc")
        + tdf_string(b"PTAG", "")
        + tdf_group(b"QOSS", qos_body)
        + tdf_string(b"RSRC", "303107")
        + tdf_string(b"SVER", "Blaze 3.13 local FIFA 14\n")
    )


def fifa14_client_config_values(
    config_id: str | None,
    *,
    returning_user: bool = False,
    fut_direct_boot: bool = False,
) -> tuple[tuple[str, str], ...]:
    """Return the active config profile for the persisted local account.

    A club created after captain selection must not be sent back through the
    Icebreaker tutorial on its next launch. FIFA 14 reads these values before
    FUT HTTP authentication, so the Blaze server derives them from the same
    SQLite identity store used by the HTTP surface.
    """
    values = list(FIFA14_CLIENT_CONFIGS.get(config_id or "", ()))
    if config_id == "OSDK_CLIENT" and returning_user:
        replacements = {
            "FUT/FORCE_TUTORIALS": "0",
            "FUT/DISABLE_TUTORIALS": "1",
            "FUT/ALWAYS_SHOW_SMART_TUTORIALS": "0",
            "FUT/IS_RETURNING_USER": "1",
            "FUT_SKIP_ICEBREAKER_FLOW": "1",
        }
        values = [(key, replacements.get(key, value)) for key, value in values]
    if config_id == "OSDK_CLIENT" and fut_direct_boot:
        values.extend(FIFA14_FUT_DIRECT_BOOT_CONFIG)
    return tuple(values)


def build_fetch_config_body(
    config_id: str | None = None,
    *,
    fut_direct_boot: bool = False,
    returning_user: bool = False,
) -> bytes:
    """Build Util.FetchClientConfig for a FIFA configuration group."""
    values = fifa14_client_config_values(
        config_id,
        returning_user=returning_user,
        fut_direct_boot=fut_direct_boot,
    )
    return tdf_map_strings(b"CONF", values)


def build_ping_body() -> bytes:
    return tdf_u32(b"STIM", int(datetime.now(timezone.utc).timestamp()))


def build_post_auth_body(player_id: int = 1_000_001) -> bytes:
    """Build the standard Blaze Util.PostAuth bootstrap response.

    Optional telemetry, ticker, and player-sync targets remain loopback-only.
    The response shape follows the Blaze 3 protocol used by this client.
    """
    player_sync = (
        tdf_string(b"ADRS", "127.0.0.1")
        + tdf_blob(b"CSIG")
        + tdf_string(b"PJID", "303107")
        + tdf_u16(b"PORT", 443)
        + tdf_u32(b"RPRT", 0xF)
        + tdf_u32(b"TIID", 0)
    )
    telemetry = (
        tdf_string(b"ADRS", "127.0.0.1")
        + tdf_u32(b"ANON", 0)
        + tdf_string(b"DISA", "")
        + tdf_string(b"FILT", "-UION/****")
        + tdf_u32(b"LOC", 0x656E5553)
        + tdf_string(b"NOOK", "")
        + tdf_u16(b"PORT", 42129)
        + tdf_u16(b"SDLY", 15000)
        + tdf_string(b"SESS", "LOCAL-FIFA14-TELEMETRY")
        + tdf_string(b"SKEY", "")
        + tdf_u32(b"SPCT", 0)
        + tdf_string(b"STIM", "")
    )
    ticker = (
        tdf_string(b"ADRS", "127.0.0.1")
        + tdf_u16(b"PORT", 8999)
        + tdf_string(b"SKEY", "1000001,127.0.0.1:8999,fifa-2014-pc,0")
    )
    user_options = tdf_u32(b"TMOP", 1) + tdf_u32(b"UID", player_id)
    return (
        tdf_group(b"PSS", player_sync)
        + tdf_group(b"TELE", telemetry)
        + tdf_group(b"TICK", ticker)
        + tdf_group(b"UROP", user_options)
    )


def build_origin_login_body(
    player_id: int = 1_000_001,
    user_id: int | None = None,
    display_name: str = "LocalFUT",
    email: str = "",
    session_token: str = "LOCAL-FIFA14-SESSION",
    session_key: str = "11229301_9b171d92cc562b293e602ee8325612e7",
    login_time: int | None = None,
    persona_status: int = 0,
    is_of_legal_contact_age: bool = False,
    is_first_login: bool = False,
) -> bytes:
    """Build a local-only Authentication.OriginLogin response.

    FIFA supplies an obsolete EA token in the request.  The preservation server
    deliberately ignores it and creates a deterministic local persona instead.
    """
    if login_time is None:
        login_time = int(datetime.now(timezone.utc).timestamp())
    if user_id is None:
        user_id = player_id
    persona = (
        tdf_string(b"DSNM", display_name)
        + tdf_u32(b"LAST", login_time)
        + tdf_u32(b"PID", player_id)
        + tdf_u32(b"STAS", persona_status)
        + tdf_u32(b"XREF", 0)
        + tdf_u32(b"XTYP", 0)
    )
    session = (
        tdf_u32(b"BUID", player_id)
        + tdf_bool(b"FRST", is_first_login)
        + tdf_string(b"KEY", session_key)
        + tdf_u32(b"LLOG", login_time)
        + tdf_string(b"MAIL", email)
        + tdf_group(b"PDTL", persona)
        + tdf_u32(b"UID", user_id)
    )
    return (
        tdf_u32(b"AGUP", 0)
        + tdf_string(b"LDHT", "")
        + tdf_u32(b"NTOS", 0)
        + tdf_string(b"PCTK", session_token)
        + tdf_string(b"PRIV", "")
        + tdf_group(b"SESS", session)
        # Pocket Relay's known-good Blaze 3 OriginLogin response reports SPAM
        # as zero. Keep the preservation response byte-for-byte compatible
        # with that behavior unless a controlled experiment overrides it.
        + tdf_bool(b"SPAM", is_of_legal_contact_age)
        + tdf_string(b"THST", "")
        + tdf_string(b"TSUI", "")
        + tdf_string(b"TURI", "")
    )


def build_user_added_body(
    player_id: int = 1_000_001,
    user_id: int | None = None,
    display_name: str = "LocalFUT",
    locale: int = 0x656E5553,
    legacy: bool = False,
    me3_legacy: bool = False,
) -> bytes:
    """Build the standard UserSessions.UserAdded login notification."""
    if user_id is None:
        user_id = player_id
    if me3_legacy:
        session_data = (
            tdf_tag(b"ADDR", TDF_TAGGED_UNION)
            + b"\x7f"
            + tdf_string(b"BPS", "")
            + tdf_string(b"CTY", "")
            + tdf_empty_varint_list(b"CVAR")
            + tdf_map_u32(b"DMAP", ((0x70001, 0x22),))
            + tdf_u32(b"HWFG", 0)
            + tdf_group(
                b"QDAT",
                tdf_u32(b"DBPS", 0) + tdf_u32(b"NATT", 4) + tdf_u32(b"UBPS", 0),
            )
            + tdf_u32(b"UATT", 0)
        )
        user = (
            tdf_u32(b"AID", user_id)
            + tdf_u32(b"ALOC", locale)
            + tdf_blob(b"EXBB")
            + tdf_u32(b"EXID", 0)
            + tdf_u32(b"ID", player_id)
            + tdf_string(b"NAME", display_name)
        )
    elif legacy:
        session_data = (
            tdf_tag(b"ADDR", TDF_TAGGED_UNION)
            + b"\x7f"
            + tdf_string(b"BPS", "")
            + tdf_string(b"CTY", "")
            + tdf_map_u32(b"DMAP", ((0x70001, 55), (0x70002, 707)))
            + tdf_u32(b"HWFG", 0)
            + tdf_group(
                b"QDAT",
                # New-Blaze-Emulator's successful full-login sequence reports
                # NatType.Open (0) in this legacy notification shape.
                tdf_u32(b"DBPS", 0) + tdf_u32(b"NATT", 0) + tdf_u32(b"UBPS", 0),
            )
            + tdf_u32(b"UATT", 0)
        )
        user = (
            tdf_u32(b"AID", user_id)
            + tdf_u32(b"ALOC", locale)
            + tdf_u32(b"ID", player_id)
            + tdf_string(b"NAME", display_name)
        )
    else:
        session_data = (
            tdf_tag(b"ADDR", TDF_TAGGED_UNION)
            + b"\x7f"
            + tdf_string(b"BPS", "ea-sjc")
            + tdf_empty_map(b"CMAP", TDF_VAR_INT, TDF_VAR_INT)
            + tdf_string(b"CTY", "")
            + tdf_empty_varint_list(b"CVAR")
            + tdf_map_u32(b"DMAP", ((0x70001, 100),))
            + tdf_u32(b"HWFG", 0)
            + tdf_list_u32(b"PSLM", ())
            + tdf_group(
                b"QDAT",
                tdf_u32(b"DBPS", 0) + tdf_u32(b"NATT", 0) + tdf_u32(b"UBPS", 0),
            )
            + tdf_u32(b"UATT", 0)
            + tdf_empty_list(b"ULST", TDF_OBJECT_ID)
        )
        user = (
            tdf_u32(b"AID", user_id)
            + tdf_u32(b"ALOC", locale)
            + tdf_blob(b"EXBB")
            + tdf_u32(b"EXID", 0)
            + tdf_u32(b"ID", player_id)
            + tdf_string(b"NAME", display_name)
        )
    return tdf_group(b"DATA", session_data) + tdf_group(b"USER", user)


def build_user_authenticated_body(
    player_id: int = 1_000_001,
    user_id: int | None = None,
    display_name: str = "LocalFUT",
    locale: int = 0x656E5553,
    email: str = "",
    session_key: str = "11229301_9b171d92cc562b293e602ee8325612e7",
    login_time: int | None = None,
) -> bytes:
    """Build FIFA 14's UserSessions.UserAuthenticated notification.

    FIFA's local-user receiver needs this login-info notification before
    UserAdded.  UserAdded by itself creates a user record but does not commit
    the record to the front-end's local-session slot.
    """
    if login_time is None:
        login_time = int(datetime.now(timezone.utc).timestamp())
    if user_id is None:
        user_id = player_id
    return (
        tdf_u32(b"ALOC", locale)
        + tdf_u32(b"BUID", player_id)
        + tdf_string(b"DSNM", display_name)
        + tdf_bool(b"FRST", False)
        + tdf_string(b"KEY", session_key)
        + tdf_u32(b"LAST", 0)
        + tdf_u32(b"LLOG", login_time)
        + tdf_string(b"MAIL", email)
        + tdf_u32(b"PID", player_id)
        # ExternalSystemId::PC in the legacy Blaze SDK.
        + tdf_u32(b"PLAT", 4)
        + tdf_u32(b"UID", user_id)
        + tdf_u32(b"USTP", 1)
        + tdf_u32(b"XREF", 0)
    )


def build_user_extended_data_body(user_id: int = 1_000_001) -> bytes:
    """Build the subscription-bearing UserSessionExtendedDataUpdate."""
    data = (
        tdf_string(b"BPS", "ea-sjc")
        + tdf_string(b"CTY", "")
        + tdf_u32(b"HWFG", 0)
        + tdf_u32(b"UATT", 0)
    )
    return tdf_group(b"DATA", data) + tdf_bool(b"SUBS", True) + tdf_u32(b"USID", user_id)


def build_user_updated_body(player_id: int = 1_000_001) -> bytes:
    return tdf_u32(b"FLGS", 3) + tdf_u32(b"ID", player_id)


def build_cardhouse_login_body() -> bytes:
    """Return CardHouse's native new-player LoginResponse.

    NAME/ABBR/CVER are intentionally absent.  Their null state tells FIFA that
    no FUT club has been created yet, which is the prerequisite for the retail
    welcome/starter-pack onboarding rather than a fabricated existing club.
    """
    return b"".join(
        tdf_u32(tag, 0)
        for tag in (b"BNUS", b"DRRC", b"DRRL", b"DRRO", b"DRRW", b"RWRD", b"TNOW", b"TRBS", b"UID")
    )


def build_clubs_component_settings_body() -> bytes:
    return b"".join(
        tdf_u32(tag, 0)
        for tag in (b"CLDS", b"MXEV", b"MXRV", b"PUHR", b"SOVR", b"STRT")
    )


def build_stats_period_ids_body() -> bytes:
    # BlazeSDK's PeriodIds response has fourteen required scalar members.
    return b"".join(
        tdf_u32(tag, 0)
        for tag in (
            b"DBUF", b"DHOU", b"DLY", b"DRET", b"MBUF", b"MDAY", b"MHOU",
            b"MLY", b"MRET", b"WBUF", b"WDAY", b"WHOU", b"WLY", b"WRET",
        )
    )


def build_osdk_settings_body() -> bytes:
    ticker_filter = (
        tdf_string(b"ID", "O_TKfilter")
        + tdf_u32(b"LOCF", 0)
        + tdf_u32(b"TOGG", 0)
    )
    return tdf_list_groups(b"LSST", (ticker_filter,))


def build_osdk_setting_groups_body() -> bytes:
    ticker_group = (
        tdf_string(b"ID", "O_SG_TCKR")
        + tdf_list_strings(b"LSET", ("O_TKfilter",))
    )
    return tdf_list_groups(b"LGRP", (ticker_group,))


def build_entitlements_body(
    entitlement_tags: tuple[str, ...] = (
        "FIFA14PCBoxContent",
        "FIFA14PCFUTContentUnlocks",
    ),
    *,
    persona_id: int = 1_000_001,
    group_names: tuple[str, ...] | None = None,
) -> bytes:
    """Build Authentication.Entitlements for the local FIFA 14 persona.

    FIFA's generated Blaze type names the outer list ``NLST``.  Each element
    uses the stock Blaze 3 Entitlement member tags recovered independently
    from the retail FIFA 14 client and the public Blaze server references.
    Returning this typed document is important: an empty FIRE success packet
    leaves FIFA's ListEntitlements callback pending until Origin times out.
    """
    values: list[bytes] = []
    if group_names is None:
        group_names = entitlement_tags
    if len(group_names) != len(entitlement_tags):
        raise ValueError("group_names must match entitlement_tags")
    for entitlement_id, (entitlement_tag, group_name) in enumerate(
        zip(entitlement_tags, group_names),
        start=1,
    ):
        values.append(
            tdf_string(b"DEVI", "")
            + tdf_string(b"GDAY", "2013-09-01T00:00:00Z")
            + tdf_string(b"GNAM", group_name)
            + tdf_u32(b"ID", entitlement_id)
            + tdf_bool(b"ISCO", False)
            + tdf_u32(b"PID", persona_id)
            + tdf_string(b"PJID", "FIFA14")
            # Blaze ProductCatalog.OFB.
            + tdf_u32(b"PRCA", 2)
            + tdf_string(b"PRID", "fifa14_pc")
            # FIFA 14's embedded Blaze enum table maps PENDING=1 and ACTIVE=2.
            # Returning 1 decodes cleanly but leaves the grant unusable, so the
            # client waits on this callback while sending only keepalive pings.
            + tdf_u32(b"STAT", 2)
            # The adjacent StatusReason table maps UNKNOWN=0 and NONE=1.
            + tdf_u32(b"STRC", 1)
            + tdf_string(b"TAG", entitlement_tag)
            + tdf_string(b"TDAY", "")
            # EntitlementType.ONLINE_ACCESS.
            + tdf_u32(b"TYPE", 1)
            + tdf_u32(b"UCNT", 0)
            + tdf_u32(b"VER", 1)
        )
    return tdf_list_groups(b"NLST", tuple(values))


def build_shared_blaze_bootstrap_response(
    component: int,
    command: int,
    request_payload: bytes | None = None,
) -> tuple[bytes, str, int] | None:
    """Build typed FIFA 14 OSDK/CardHouse responses shared by PC and Xbox.

    The return tuple is ``(body, trace_name, error)``.  ``None`` leaves the
    request on the observation fallback so newly discovered routes remain
    visible instead of being guessed.
    """
    route = (component, command)
    if component == AUTHENTICATION_COMPONENT and command in {0x1D, 0x20}:
        # FIFA 14's ListEntitlements request filters by GNLS (group names),
        # not ETAG.  A grant whose GNAM does not match that filter is decoded
        # successfully but discarded by the generated client callback, which
        # leaves the front end spinning while only keepalive pings continue.
        requested_groups = extract_tdf_string_list(request_payload or b"", b"GNLS")
        groups = requested_groups or ("FIFA14PCBoxContent",)
        # GNLS is a group-name filter, while TAG identifies the actual grant.
        # They are not interchangeable.  The working FIFA-local contract uses
        # a FUTContentUnlocks tag inside the title's requested content group.
        # Returning the group name as TAG decodes successfully but does not
        # authorize CardsDLL, which leaves the shell on its spinner.
        entitlement_tags = tuple("FIFA14PCFUTContentUnlocks" for _ in groups)
        return (
            build_entitlements_body(entitlement_tags, group_names=groups),
            "authentication-list-entitlements",
            0,
        )
    typed: dict[tuple[int, int], tuple[bytes, str, int]] = {
        (MESSAGING_COMPONENT, 2): (
            tdf_u32(b"MCNT", 0),
            "messaging-fetch-count",
            0,
        ),
        (MESSAGING_COMPONENT, 5): (b"", "messaging-get-empty", 0),
        (ASSOCIATION_LISTS_COMPONENT, 6): (
            tdf_empty_list(b"LMAP", TDF_GROUP),
            "association-lists-empty",
            0,
        ),
        # FIFA 14 submits its terminal offline game report on component 28,
        # command 2. The command itself is fieldless success; the terminal result
        # is delivered asynchronously as notification 114 below.
        (GAME_REPORTING_COMPONENT, 2): (
            b"",
            "game-reporting-submit-offline-success",
            0,
        ),
        (CLUBS_COMPONENT, 2600): (
            build_clubs_component_settings_body(),
            "clubs-component-settings",
            0,
        ),
        (CLUBS_COMPONENT, 1600): (
            tdf_empty_list(b"CIST", TDF_GROUP),
            "clubs-invitations-empty",
            0,
        ),
        (STATS_COMPONENT, 15): (
            tdf_empty_map(b"KSIT", TDF_STRING, TDF_GROUP),
            "stats-key-scopes-empty",
            0,
        ),
        (STATS_COMPONENT, 3): (
            tdf_empty_list(b"GRPS", TDF_GROUP),
            "stats-groups-empty",
            0,
        ),
        (STATS_COMPONENT, 20): (
            build_stats_period_ids_body(),
            "stats-period-ids",
            0,
        ),
        (CENSUS_COMPONENT, 1): (b"", "census-subscribe", 0),
        (ROOMS_COMPONENT, 10): (b"", "rooms-select-view-updates", 0),
        (OSDK_SETTINGS_COMPONENT, 1): (
            build_osdk_settings_body(),
            "osdk-settings",
            0,
        ),
        (OSDK_SETTINGS_COMPONENT, 2): (
            build_osdk_setting_groups_body(),
            "osdk-setting-groups",
            0,
        ),
        (OSDK_ONLINE_PASS_COMPONENT, 3): (
            tdf_empty_list(b"LIST", TDF_GROUP),
            "osdk-online-pass-gates-empty",
            0,
        ),
        (SPONSORED_EVENTS_COMPONENT, 3): (
            tdf_string(b"URL", "http://127.0.0.1:8080/sponsored-events"),
            "sponsored-events-local-url",
            0,
        ),
        # The generated EASFC purchase-game response classes are fieldless.
        # Advertising component 0x081D and returning an empty success prevents
        # queued front-end activity from failing before the FUT attempt.
        (EASFC_COMPONENT, 1): (b"", "easfc-purchase-game-win-success", 0),
        (EASFC_COMPONENT, 2): (b"", "easfc-purchase-game-match-success", 0),
        (EASFC_COMPONENT, 3): (b"", "easfc-purchase-game-loss-success", 0),
        (EASFC_COMPONENT, 4): (b"", "easfc-purchase-game-draw-success", 0),
        (CARDHOUSE_COMPONENT, 101): (
            build_cardhouse_login_body(),
            "cardhouse-new-player-login",
            0,
        ),
        # Full Blaze error value is 0x00010864: local ordinal 1 in component
        # 0x0864.  The FIRE header carries the component separately, so the
        # 16-bit error field is one.
        (CARDHOUSE_COMPONENT, 104): (b"", "cardhouse-no-player-info", 1),
    }
    if route in typed:
        return typed[route]
    if component == CARDHOUSE_COMPONENT and command in {102, 103, 106, 301, 709}:
        return b"", "cardhouse-empty-success", 0
    if component == 0x7802 and command in {8, 20}:
        return b"", "user-sessions-update-ack", 0
    return None


def parse_fire_header(payload: bytes) -> dict:
    if len(payload) < 12:
        return {"protocol_guess": "short-fire"}
    length, component, command, error, type_options, options_raw, sequence = struct.unpack_from(">HHHHBBH", payload, 0)
    return {
        "protocol_guess": "blaze-fire",
        "fire_length": length,
        "fire_component": component,
        "fire_command": command,
        "fire_error": error,
        "fire_type": type_options >> 4,
        "fire_options": options_raw >> 4,
        "fire_sequence": sequence,
    }


def build_fire_response(request_frame: bytes, body: bytes) -> bytes:
    header = parse_fire_header(request_frame)
    component = int(header.get("fire_component", 0))
    command = int(header.get("fire_command", 0))
    sequence = int(header.get("fire_sequence", 0))
    return struct.pack(">HHHHBBH", len(body), component, command, 0, 0x10, 0x00, sequence) + body


def build_fire_error_response(request_frame: bytes, error: int, body: bytes = b"") -> bytes:
    """Build a Blaze error frame while preserving the request route and sequence."""
    header = parse_fire_header(request_frame)
    component = int(header.get("fire_component", 0))
    command = int(header.get("fire_command", 0))
    sequence = int(header.get("fire_sequence", 0))
    return struct.pack(">HHHHBBH", len(body), component, command, error & 0xFFFF, 0x30, 0x00, sequence) + body


def build_fire_notification(component: int, command: int, body: bytes) -> bytes:
    return struct.pack(">HHHHBBH", len(body), component, command, 0, 0x20, 0x00, 0) + body


def recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks = []
    remaining = length
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_fire_frame(sock: socket.socket) -> bytes:
    header = recv_exact(sock, 12)
    if len(header) < 12:
        return header
    length = int.from_bytes(header[0:2], "big")
    body = recv_exact(sock, length)
    return header + body


def parse_tls_client_hello(payload: bytes) -> dict:
    details = describe_tcp_payload(payload)
    if len(payload) < 9 or payload[0] != 0x16:
        return details

    record_len = int.from_bytes(payload[3:5], "big")
    if len(payload) < 5 + record_len:
        details["client_hello_incomplete"] = True
        return details

    body = payload[5 : 5 + record_len]
    if len(body) < 4 or body[0] != 0x01:
        details["handshake_type"] = body[0] if body else None
        return details

    pos = 4
    if len(body) < pos + 34:
        details["client_hello_incomplete"] = True
        return details

    details["handshake_type"] = "client_hello"
    details["client_version"] = f"{body[pos]}.{body[pos + 1]}"
    pos += 34

    if len(body) <= pos:
        details["client_hello_incomplete"] = True
        return details
    session_len = body[pos]
    pos += 1 + session_len

    if len(body) < pos + 2:
        details["client_hello_incomplete"] = True
        return details
    cipher_len = int.from_bytes(body[pos : pos + 2], "big")
    pos += 2
    cipher_bytes = body[pos : pos + cipher_len]
    ciphers = [f"0x{cipher_bytes[i]:02x}{cipher_bytes[i + 1]:02x}" for i in range(0, len(cipher_bytes) - 1, 2)]
    details["client_cipher_suites"] = ciphers
    pos += cipher_len

    if len(body) <= pos:
        return details
    compression_len = body[pos]
    pos += 1 + compression_len

    if len(body) < pos + 2:
        return details
    extensions_len = int.from_bytes(body[pos : pos + 2], "big")
    pos += 2
    end = min(len(body), pos + extensions_len)
    extensions = []
    server_name = None
    while pos + 4 <= end:
        ext_type = int.from_bytes(body[pos : pos + 2], "big")
        ext_len = int.from_bytes(body[pos + 2 : pos + 4], "big")
        ext_data = body[pos + 4 : pos + 4 + ext_len]
        extensions.append(f"0x{ext_type:04x}")
        if ext_type == 0 and len(ext_data) >= 5:
            name_len = int.from_bytes(ext_data[3:5], "big")
            server_name = ext_data[5 : 5 + name_len].decode("ascii", "replace")
        pos += 4 + ext_len

    details["client_extensions"] = extensions
    if server_name:
        details["client_sni"] = server_name
    return details


def peek_socket(sock: socket.socket, limit: int = 4096) -> bytes:
    previous_timeout = sock.gettimeout()
    try:
        sock.settimeout(1)
        return sock.recv(limit, socket.MSG_PEEK)
    except (OSError, TimeoutError):
        return b""
    finally:
        # MSG_PEEK is diagnostic only. Do not leak its short timeout into the
        # subsequent TLS handshake; heavily instrumented legacy clients can
        # legitimately need more than one second to process ServerHello.
        sock.settimeout(previous_timeout)


class TcpProbe(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(3)
        chunks = []
        try:
            while sum(map(len, chunks)) < 65536:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except TimeoutError:
            pass
        payload = b"".join(chunks)
        emit(
            "tcp-probe",
            name=getattr(self.server, "probe_name", "tcp"),
            local_port=getattr(self.server, "server_address", ("", None))[1],
            peer=self.client_address,
            bytes=len(payload),
            **describe_tcp_payload(payload),
            hex=payload[:512].hex(),
            truncated=len(payload) > 512,
        )


class BlazeProbe(socketserver.BaseRequestHandler):
    """Minimal persistent Blaze application server used to discover FIFA's bootstrap flow."""

    def handle(self) -> None:
        # FIFA may perform title-specific setup between requests.  The old
        # 15-second timeout was itself producing the generic server-down error.
        self.request.settimeout(300)
        client_locale = 0x656E5553
        origin_variant = "reference-local"
        origin_login_attempts = 0
        login_notifications_sent = False
        for request_index in range(64):
            try:
                frame = recv_fire_frame(self.request)
            except (OSError, TimeoutError) as error:
                emit(
                    "blaze-session-ended",
                    peer=self.client_address,
                    requests=request_index,
                    error=str(error),
                )
                return
            if not frame:
                emit("blaze-session-ended", peer=self.client_address, requests=request_index, error=None)
                return

            header = parse_fire_header(frame)
            component = int(header.get("fire_component", 0))
            command = int(header.get("fire_command", 0))
            request_hex = frame[:2048].hex()
            if component == 1 and command == 0x98:
                request_hex = "redacted-origin-auth-token"
            config_id = None
            if component == 9 and command == 1:
                config_id = extract_tdf_string(frame[12:], b"CFID")
            emit(
                "blaze-request",
                peer=self.client_address,
                request_index=request_index,
                bytes=len(frame),
                **header,
                hex=request_hex,
                truncated=len(frame) > 2048,
                config_id=config_id,
            )

            response_error = 0
            if component == 1 and command == 0x98:
                origin_login_attempts += 1
                origin_token = extract_tdf_string(frame[12:], b"AUTH") or ""
                origin_mode = getattr(self.server, "origin_login_mode", "success")
                should_error = origin_mode == "error" or (
                    origin_mode == "error-once" and origin_login_attempts == 1
                )
                if should_error:
                    body = b""
                    response_error = int(getattr(self.server, "origin_login_error", 0x000D))
                    response_name = f"authentication-origin-login-controlled-error-{response_error:04x}"
                    emit(
                        "origin-login-controlled-error",
                        peer=self.client_address,
                        request_index=request_index,
                        attempt=origin_login_attempts,
                        error=response_error,
                    )
                else:
                    origin_variant = next(ORIGIN_LOGIN_VARIANTS)
                    origin_first_login = bool(getattr(self.server, "origin_first_login", False))
                    if origin_variant == "pocket-relay-origin":
                        body = build_origin_login_body(
                            player_id=1_000_001,
                            # Match the reference Pocket Relay OriginLogin
                            # identity exactly: account, persona, and user ID
                            # are one stable local player.
                            user_id=1_000_001,
                            email="local@fifa14.invalid",
                            session_token="LOCAL-FIFA14-SESSION",
                            session_key="F4241",
                            login_time=0,
                            persona_status=0,
                            is_first_login=origin_first_login,
                        )
                    elif origin_variant == "legacy-empty":
                        body = build_origin_login_body(
                            email="local@fifa14.invalid",
                            session_token="",
                            session_key="",
                            login_time=0,
                            is_first_login=origin_first_login,
                        )
                    elif origin_variant == "echo-origin":
                        body = build_origin_login_body(
                            email="local@fifa14.invalid",
                            session_token=origin_token,
                            session_key="",
                            login_time=0,
                            is_first_login=origin_first_login,
                        )
                    else:
                        body = build_origin_login_body(is_first_login=origin_first_login)
                    decoded_login = validate_origin_login_body(body)
                    response_name = f"authentication-origin-login-{origin_variant}"
                    emit(
                        "origin-login-variant",
                        peer=self.client_address,
                        request_index=request_index,
                        variant=origin_variant,
                        client_locale=f"0x{client_locale:08x}",
                    )
                    emit(
                        "origin-login-schema-valid",
                        peer=self.client_address,
                        request_index=request_index,
                        top_level=[field["tag"] for field in decoded_login],
                        session=[field["tag"] for field in decoded_login[5]["value"]],
                        persona=[field["tag"] for field in decoded_login[5]["value"][5]["value"]],
                    )
            elif component == 9 and command == 7:
                observed_locale = extract_tdf_u32(frame[12:], b"LOC")
                if observed_locale is not None:
                    client_locale = observed_locale
                body = build_pre_auth_body()
                response_name = "util-pre-auth"
            elif component == 9 and command == 1:
                direct_boot_config = bool(
                    getattr(self.server, "enable_fut_direct_boot_config", False)
                )
                identity_store = getattr(self.server, "identity_store", None)
                returning_user = bool(
                    identity_store is not None and identity_store.has_club()
                )
                body = build_fetch_config_body(
                    config_id,
                    fut_direct_boot=direct_boot_config,
                    returning_user=returning_user,
                )
                if config_id == "OSDK_CLIENT":
                    profile = fifa14_client_config_values(
                        "OSDK_CLIENT",
                        returning_user=returning_user,
                        fut_direct_boot=direct_boot_config,
                    )
                    emit(
                        "osdk-client-profile",
                        peer=self.client_address,
                        request_index=request_index,
                        values=dict(profile),
                        profile_kind=(
                            identity_store.profile_kind()
                            if identity_store is not None
                            else "first-use-no-club-pc"
                        ),
                    )
                response_name = f"util-fetch-client-config-{config_id or 'unknown'}"
            elif component == 9 and command == 2:
                body = build_ping_body()
                response_name = "util-ping"
            elif component == 9 and command == 8:
                body = build_post_auth_body()
                response_name = "util-post-auth"
            elif component == 1 and command == 0x46:
                body = b""
                response_name = "authentication-logout"
            else:
                if component == EASFC_COMPONENT and command in {1, 2, 3, 4}:
                    identity_store = getattr(self.server, "identity_store", None)
                    if identity_store is not None and hasattr(identity_store, "record_easfc_signal"):
                        signal_state = identity_store.record_easfc_signal(command)
                        emit(
                            "beta-easfc-match-signal",
                            peer=self.client_address, request_index=request_index,
                            command=command, state=signal_state,
                        )
                shared_response = build_shared_blaze_bootstrap_response(
                    component,
                    command,
                    frame[12:],
                )
                if shared_response is None:
                    body = b""
                    response_name = "empty-success-observation"
                else:
                    body, response_name, response_error = shared_response

            if component == 1 and command == 0x98:
                delay_ms = int(getattr(self.server, "origin_login_delay_ms", 0))
                if delay_ms > 0:
                    threading.Event().wait(delay_ms / 1000)
            response = (
                build_fire_error_response(frame, response_error, body)
                if response_error
                else build_fire_response(frame, body)
            )
            try:
                self.request.sendall(response)
            except OSError as error:
                emit(
                    "blaze-send-error",
                    peer=self.client_address,
                    request_index=request_index,
                    response_name=response_name,
                    error=str(error),
                )
                return
            emit(
                "blaze-response",
                peer=self.client_address,
                request_index=request_index,
                response_name=response_name,
                bytes=len(response),
                component=component,
                command=command,
                sequence=header.get("fire_sequence"),
                hex=response[:2048].hex(),
                truncated=len(response) > 2048,
            )

            # The latest full-match capture sends GameReporting component 28,
            # command 2 at exactly the same instant as FUT /match/end. Returning
            # only an empty RPC success leaves the client's reporting job waiting
            # for ResultNotification and it later tears down the online session.
            # Complete that asynchronous handshake before the post-match UI exits.
            if component == GAME_REPORTING_COMPONENT and command == 2 and response_error == 0:
                reporting_id = extract_tdf_varint_last(frame[12:], b"GRID") or 0
                notification = build_fire_notification(
                    GAME_REPORTING_COMPONENT,
                    GAME_REPORTING_RESULT_NOTIFICATION,
                    build_game_reporting_result_notification_body(reporting_id),
                )
                threading.Event().wait(0.020)
                try:
                    self.request.sendall(notification)
                except OSError as error:
                    emit(
                        "blaze-send-error",
                        peer=self.client_address,
                        request_index=request_index,
                        response_name="game-reporting-result-notification",
                        error=str(error),
                    )
                    return
                emit(
                    "blaze-notification",
                    peer=self.client_address,
                    request_index=request_index,
                    notification_name="game-reporting-result",
                    component=GAME_REPORTING_COMPONENT,
                    command=GAME_REPORTING_RESULT_NOTIFICATION,
                    game_reporting_id=int(reporting_id),
                    bytes=len(notification),
                    hex=notification[:2048].hex(),
                    truncated=len(notification) > 2048,
                )

            # Full-login implementations differ on subscription timing. The
            # New-Blaze-Emulator sequence sends UserAdded/UserUpdated directly
            # after the successful FullLoginResponse. FIFA never requested
            # PostAuth in our captures and timed out at 30 seconds, which is
            # consistent with waiting for this local-user session event.
            send_login_notifications = (
                component == 1 and command == 0x98 and response_error == 0
            ) or (
                component == 9 and command == 8 and not login_notifications_sent
            )
            if send_login_notifications:
                # Let the OriginLogin response job finish before queueing the
                # asynchronous session events.  FIFA 14's main-thread packet
                # pump can otherwise see all three frames in one TLS read
                # before its UserSessions listeners are armed.
                if component == 1 and command == 0x98:
                    notification_delay_ms = int(
                        getattr(self.server, "login_notification_delay_ms", 250)
                    )
                    threading.Event().wait(max(0, notification_delay_ms) / 1000.0)
                notifications = (
                    (
                        "user-authenticated",
                        build_fire_notification(
                            0x7802,
                            8,
                            build_user_authenticated_body(
                                player_id=1_000_001,
                                user_id=1_000_001,
                                display_name="LocalFUT",
                                locale=client_locale,
                            ),
                        ),
                    ),
                    (
                        "user-added",
                        build_fire_notification(
                            0x7802,
                            2,
                            build_user_added_body(
                                player_id=1_000_001,
                                user_id=1_000_001,
                                locale=client_locale,
                                # Match the compact FIFA-era notification used
                                # by New-Blaze-Emulator (DATA + USER only).
                                legacy=True,
                            ),
                        ),
                    ),
                    (
                        "user-extended-data",
                        build_fire_notification(
                            0x7802,
                            1,
                            build_user_extended_data_body(user_id=1_000_001),
                        ),
                    ),
                )
                for notification_name, notification in notifications:
                    try:
                        self.request.sendall(notification)
                    except OSError as error:
                        emit(
                            "blaze-send-error",
                            peer=self.client_address,
                            request_index=request_index,
                            response_name=notification_name,
                            error=str(error),
                        )
                        return
                    emit(
                        "blaze-notification",
                        peer=self.client_address,
                        request_index=request_index,
                        notification_name=notification_name,
                        bytes=len(notification),
                        hex=notification[:2048].hex(),
                        truncated=len(notification) > 2048,
                    )
                    threading.Event().wait(0.050)
                login_notifications_sent = True


class TlsTcpProbe(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client_hello = peek_socket(self.request)
        try:
            context_factory = getattr(self.server, "ssl_context_factory", None)
            tls_context = context_factory() if context_factory is not None else self.server.ssl_context
            tls_socket = tls_context.wrap_socket(self.request, server_side=True)
        except (OSError, ssl.SSLError) as err:
            emit(
                "tls-handshake-error",
                name=getattr(self.server, "probe_name", "tls"),
                local_port=getattr(self.server, "server_address", ("", None))[1],
                peer=self.client_address,
                error=str(err),
                error_type=type(err).__name__,
                **parse_tls_client_hello(client_hello),
            )
            return

        tls_socket.settimeout(3)
        if getattr(self.server, "redirector_reply", "none") == "local":
            cipher = tls_socket.cipher()
            frame = b""
            response = b""
            response_sent = False
            try:
                frame = recv_fire_frame(tls_socket)
                emit(
                    "tls-probe",
                    name=getattr(self.server, "probe_name", "tls"),
                    local_port=getattr(self.server, "server_address", ("", None))[1],
                    peer=self.client_address,
                    bytes=len(frame),
                    cipher=cipher,
                    **parse_fire_header(frame),
                    hex=frame[:512].hex(),
                    truncated=len(frame) > 512,
                )
                header = parse_fire_header(frame)
                if header.get("fire_component") == 5 and header.get("fire_command") == 1:
                    body = build_redirector_body(getattr(self.server, "main_blaze_host", "127.0.0.1"), getattr(self.server, "main_blaze_port", 42128))
                    response = build_fire_response(frame, body)
                    tls_socket.sendall(response)
                    response_sent = True
                    emit(
                        "redirector-response",
                        peer=self.client_address,
                        target_host=getattr(self.server, "main_blaze_host", "127.0.0.1"),
                        target_port=getattr(self.server, "main_blaze_port", 42128),
                        bytes=len(response),
                        hex=response.hex(),
                    )
            except (OSError, ssl.SSLError, TimeoutError) as err:
                emit(
                    "tls-read-error",
                    name=getattr(self.server, "probe_name", "tls"),
                    local_port=getattr(self.server, "server_address", ("", None))[1],
                    peer=self.client_address,
                    error=str(err),
                    response_sent=response_sent,
                )
            finally:
                try:
                    tls_socket.close()
                except OSError:
                    pass
            return

        chunks = []
        try:
            while sum(map(len, chunks)) < 65536:
                chunk = tls_socket.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except TimeoutError:
            pass
        except ssl.SSLError as err:
            emit(
                "tls-read-error",
                name=getattr(self.server, "probe_name", "tls"),
                local_port=getattr(self.server, "server_address", ("", None))[1],
                peer=self.client_address,
                error=str(err),
            )
        finally:
            cipher = tls_socket.cipher()
            try:
                tls_socket.close()
            except OSError:
                pass

        payload = b"".join(chunks)
        emit(
            "tls-probe",
            name=getattr(self.server, "probe_name", "tls"),
            local_port=getattr(self.server, "server_address", ("", None))[1],
            peer=self.client_address,
            bytes=len(payload),
            cipher=cipher,
            **describe_tcp_payload(payload),
            hex=payload[:512].hex(),
            truncated=len(payload) > 512,
        )


class HttpProbe(BaseHTTPRequestHandler):
    server_version = "FIFA14LocalFUT/2.41.1-beta2.25.9"

    @staticmethod
    def _dynamic_messages_payload() -> bytes:
        # Generic FUT dynamic-message feed. Keep this distinct from the
        # localization documents requested under /fut/loc/... .
        return (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<MESSAGES>\n'
            b'</MESSAGES>\n'
        )

    @staticmethod
    def _locstrings_payload(strings: dict[str, str], *, target: str) -> bytes:
        # Values here are controlled constants. Keep the document deliberately
        # tiny: it supplements the retail game's built-in localization instead
        # of trying to recreate EA's complete 2014 language database.
        def xml_text(value: str) -> str:
            return (str(value).replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace('"', "&quot;"))
        rows = [f'<locstring id="{xml_text(key)}">{xml_text(value)}</locstring>' for key, value in strings.items()]
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<message_set target="{xml_text(target)}">\n  '
            + '\n  '.join(rows)
            + '\n</message_set>\n'
        ).encode("utf-8")

    @classmethod
    def _icebreaker_locstrings_payload(cls) -> bytes:
        # The retail futPackSelect APT resolves these four fixed tokens.
        return cls._locstrings_payload({
            "FUT_IB_CAPTAINNAME_0": "FALCAO",
            "FUT_IB_CAPTAINNAME_1": "MESSI",
            "FUT_IB_CAPTAINNAME_2": "EL SHAARAWY",
            "FUT_IB_CAPTAINNAME_3": "ALABA",
        }, target="fut-icebreaker-locstrings")

    @classmethod
    def _leaderboards_locstrings_payload(cls) -> bytes:
        # V2.35 incorrectly answered the returning-user leaderboards localization
        # request with the four Icebreaker captain names only. The user's capture
        # then showed literal *LeagueName_Abbr15 / *LAbbr_0 strings in My Club.
        # Supply the league/nation tokens used by our verified catalogue while
        # keeping the captain tokens too for a same-session route transition.
        leagues = {
            7: ("Liga do Brasil", "BRAZIL"),
            13: ("Barclays Premier League", "BPL"),
            16: ("Ligue 1", "LIGUE 1"),
            19: ("Bundesliga", "BUNDESLIGA"),
            31: ("Serie A", "SERIE A"),
            53: ("Liga BBVA", "LIGA BBVA"),
        }
        nations = {
            4: "Austria", 7: "Belgium", 14: "England", 18: "France",
            21: "Germany", 27: "Italy", 34: "Netherlands", 38: "Portugal",
            45: "Spain", 46: "Sweden", 52: "Argentina", 54: "Brazil",
            56: "Colombia", 60: "Uruguay",
        }
        strings: dict[str, str] = {
            "FUT_IB_CAPTAINNAME_0": "FALCAO",
            "FUT_IB_CAPTAINNAME_1": "MESSI",
            "FUT_IB_CAPTAINNAME_2": "EL SHAARAWY",
            "FUT_IB_CAPTAINNAME_3": "ALABA",
            # Safe fallbacks for the exact unresolved tokens in the V2.35 capture.
            "LeagueName_Abbr15": "FUT LEAGUE",
            "LAbbr_0": "FUT",
        }
        for league_id, (full_name, short_name) in leagues.items():
            strings[f"LeagueName_{league_id}"] = full_name
            strings[f"LeagueName_Abbr15_{league_id}"] = short_name
            strings[f"LeagueName_abbr15_{league_id}"] = short_name
            strings[f"LAbbr_{league_id}"] = short_name
        for nation_id, name in nations.items():
            strings[f"NationName_{nation_id}"] = name
        return cls._locstrings_payload(strings, target="fut-leaderboards-locstrings")

    @staticmethod
    def _is_icebreaker_locstrings_path(path_without_query: str) -> bool:
        return path_without_query.lower().endswith("/loc/pc/icebreaker.eng_us.xml")

    @staticmethod
    def _is_leaderboards_locstrings_path(path_without_query: str) -> bool:
        return path_without_query.lower().endswith("/loc/pc/leaderboards.eng_us.xml")

    @staticmethod
    def _is_fut_static_metadata_path(path_without_query: str) -> bool:
        lowered = path_without_query.lower()
        return "/2014/fut/items/web/" in lowered and lowered.endswith(".json")

    @staticmethod
    def _is_fut_static_image_path(path_without_query: str) -> bool:
        lowered = path_without_query.lower()
        return "/fut/items/images/" in lowered and lowered.endswith((".png", ".jpg", ".jpeg"))

    @staticmethod
    def _is_fut_static_archive_path(path_without_query: str) -> bool:
        # FUT static image bundles use EA's BIGF/BIG4 container format.
        lowered = path_without_query.lower()
        return "/fut/items/images/" in lowered and lowered.endswith(".big")

    @staticmethod
    def _is_fut_trophy_archive_path(path_without_query: str) -> bool:
        # Offline Seasons requests item.big.  BETA 2.6 also proved that selecting
        # a tournament with trophyResourceId=0 asks for the degenerate path
        # /trophies/pc/.big.  Both are retired trophy CDN bundles, so serve the
        # same parseable empty BIGF while the competition contract is isolated.
        lowered = path_without_query.lower()
        return "/fut/items/images/trophies/pc/" in lowered and lowered.endswith(".big")

    @staticmethod
    def _empty_bigf_archive() -> bytes:
        # Minimal structurally valid EA BIGF archive: magic + declared size +
        # zero directory entries + 16-byte directory/header size. The project's
        # own BIG parser/repacker uses the same big-endian header contract.
        # This is intentionally an empty compatibility response, not invented
        # trophy art.
        return b"BIGF" + (16).to_bytes(4, "big") + (0).to_bytes(4, "big") + (16).to_bytes(4, "big")

    @staticmethod
    def _fut_player_metadata_payload(path_without_query: str) -> dict | None:
        # The old EA CDN is gone. We only serve metadata for asset IDs we have
        # actually verified; image art is intentionally not fabricated.
        lowered = path_without_query.lower()
        if lowered.endswith("/2014/fut/items/web/players.json"):
            return {
                "players": [
                    {
                        "assetId": int(player["assetId"]),
                        "id": int(player["assetId"]),
                        "rating": int(player["rating"]),
                        "position": str(player["position"]),
                        "teamId": int(player["teamId"]),
                        "leagueId": int(player["leagueId"]),
                        "nation": int(player["nation"]),
                        "name": str(player.get("name", "")),
                        "commonName": str(player.get("commonName", player.get("name", ""))),
                        "resourceId": int(player.get("resourceId", player["assetId"])),
                        "rareFlag": int(player.get("rareFlag", 0)),
                    }
                    for player in PLAYER_REFERENCE_BY_ASSET.values()
                ]
            }
        name = Path(path_without_query).name
        stem = name.rsplit(".", 1)[0]
        if stem.isdigit():
            player = PLAYER_REFERENCE_BY_ASSET.get(int(stem))
            if player is not None:
                return {
                    "assetId": int(player["assetId"]),
                    "id": int(player["assetId"]),
                    "rating": int(player["rating"]),
                    "position": str(player["position"]),
                    "teamId": int(player["teamId"]),
                    "leagueId": int(player["leagueId"]),
                    "nation": int(player["nation"]),
                    "name": str(player.get("name", "")),
                    "commonName": str(player.get("commonName", player.get("name", ""))),
                    "resourceId": int(player.get("resourceId", player["assetId"])),
                    "rareFlag": int(player.get("rareFlag", 0)),
                }
        return None

    @staticmethod
    def _is_icebreaker_packlist_path(path_without_query: str) -> bool:
        """Match the retail captain-selection pack-list JSON asset.

        This must be handled before the broad /fut/* XML fallback. Returning
        XML for this .json path aborts the original Icebreaker flow. V27 serves
        a validated editable four-pack fixture with all 23 native player slots,
        allowing retail RetrievePack and BuildSquad to resolve non-null cards.
        """
        lowered = path_without_query.lower()
        return lowered.endswith(
            "/fut/packs/icebreaker/icebreakerpacklist.json"
        ) or lowered.endswith(
            "/packs/icebreaker/icebreakerpacklist.json"
        )

    @staticmethod
    def _is_store_pack_descriptions_path(path_without_query: str) -> bool:
        return "packs/loc/storepackdescriptions." in path_without_query.lower()

    @staticmethod
    def _is_fut_localization_path(path_without_query: str) -> bool:
        lowered = path_without_query.lower()
        return "/fut/loc/" in lowered and lowered.endswith(".xml")

    @classmethod
    def _local_ui_locstrings_payload(cls) -> bytes:
        strings: dict[str, str] = {}
        try:
            from local_identity import PACK_CATALOG_DOCUMENT
            for entry in PACK_CATALOG_DOCUMENT.get("packs", []):
                pack_type = int(entry.get("packType", 0))
                pack_id = int(entry.get("packId", pack_type))
                name = str(entry.get("name", f"Local Pack {pack_type}"))
                description = str(entry.get("description", name))
                strings[f"LOCAL_PACK_NAME_{pack_type}"] = name
                strings[f"LOCAL_PACK_DESC_{pack_type}"] = description
                strings[f"FUT_STORE_PACK_{pack_id}_DESC"] = description
        except Exception:
            pass
        try:
            from beta_identity import OFFLINE_TOURNAMENTS
            for entry in OFFLINE_TOURNAMENTS:
                tournament_id = int(entry.get("tournamentId", 0) or 0)
                if tournament_id > 0:
                    strings[f"LOCAL_TOURNAMENT_NAME_{tournament_id}"] = str(
                        entry.get("name") or f"Local Cup {tournament_id}"
                    )
        except Exception:
            pass
        return cls._locstrings_payload(strings, target="fifa14-local-ui-locstrings")

    @classmethod
    def _store_pack_descriptions_payload(cls) -> bytes:
        # FIFA 14's localization loader consumes the same <locstring> message
        # set used by the other FUT localization assets. v2.40.13 emitted
        # trans-unit/source rows, so the Store received HTTP 200 but displayed
        # literal green NOT FOUND placeholders.
        strings: dict[str, str] = {}
        try:
            from local_identity import PACK_CATALOG_DOCUMENT
            packs = PACK_CATALOG_DOCUMENT.get("packs", [])
        except Exception:
            packs = []
        for entry in packs:
            pack_type = int(entry.get("packType", 0))
            name = str(entry.get("name", f"Local Pack {pack_type}"))
            description = str(entry.get("description", name))
            pack_id = int(entry.get("packId", pack_type))
            strings[f"LOCAL_PACK_NAME_{pack_type}"] = name
            strings[f"LOCAL_PACK_DESC_{pack_type}"] = description
            # Retail Store offers bind the description member directly to this
            # token family. Keep the local aliases too for backward-compatible
            # diagnostics, but make the native key available to the frontend.
            strings[f"FUT_STORE_PACK_{pack_id}_DESC"] = description
        try:
            from beta_identity import OFFLINE_TOURNAMENTS
            for entry in OFFLINE_TOURNAMENTS:
                tournament_id = int(entry.get("tournamentId", 0) or 0)
                if tournament_id > 0:
                    strings[f"LOCAL_TOURNAMENT_NAME_{tournament_id}"] = str(
                        entry.get("name") or f"Local Cup {tournament_id}"
                    )
        except Exception:
            pass
        return cls._locstrings_payload(strings, target="storepackdescriptions")

    @staticmethod
    def _decode_store_purchase(body: bytes) -> dict[str, object]:
        if not body:
            return {}
        try:
            document = json.loads(body.decode("utf-8"))
            if isinstance(document, dict):
                # Some retail flows wrap the request in a single ``purchase``
                # object (or one-element array). Flatten that wrapper while
                # preserving any top-level transaction fields.
                # Retail Store scripts refer to both a purchase item and a
                # server ID. Accept the common wrapper names without changing
                # the native wire data when the request is already flat.
                for wrapper_name in ("purchase", "purchaseItem", "item", "offer"):
                    nested = document.get(wrapper_name)
                    if isinstance(nested, dict):
                        return {**document, **nested}
                    if isinstance(nested, list) and nested and isinstance(nested[0], dict):
                        return {**document, **nested[0]}
                return document
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        try:
            form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            return {}
        return {key: values[-1] if values else "" for key, values in form.items()}

    @staticmethod
    def _store_pack_selector(request: dict[str, object]) -> int | None:
        """Extract the numeric local pack/SKU id from retail purchase input."""
        for key in (
            "packId", "id", "packType", "purchasePackTypeId",
            # The retail APT names the normal handoff PurchasePack_ServerID;
            # tolerate the corresponding request spellings if CardsDLL emits
            # the selected offer's server identifier by name.
            "serverId", "serverID", "serverid", "assetId",
        ):
            value = request.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        # purchasePackType is normally the enum string CARDPACK, but tolerate
        # older/local callers that sent a numeric value here.
        value = request.get("purchasePackType")
        if value is not None and not isinstance(value, bool):
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _is_dynamic_messages_path(path_without_query: str) -> bool:
        lowered = path_without_query.lower()
        # Static metadata/art must never be answered with an XML MESSAGES body.
        # V2.37 gives those paths their own handler so future captures tell us
        # exactly which retired EA CDN asset is still required.
        if "/2014/fut/items/web/" in lowered or "/fut/items/images/" in lowered:
            return False
        return (
            lowered == "/fut"
            or lowered.startswith("/fut/")
            or (
                lowered.startswith("/fifa/fltonlineassets/")
                and "/fut/" in lowered
            )
        )

    def _handle(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(min(length, 1_048_576)) if length else b""
        emit(
            "http-probe",
            name=getattr(self.server, "probe_name", "http"),
            local_port=getattr(self.server, "server_address", ("", None))[1],
            peer=self.client_address,
            method=self.command,
            path=self.path,
            headers=dict(self.headers.items()),
            body_hex=body.hex(),
        )
        path_without_query = self.path.partition("?")[0]
        identity_store = getattr(self.server, "identity_store", None)
        effective_method = self.headers.get(
            "X-HTTP-Method-Override",
            self.command,
        ).upper()
        probe_name = getattr(self.server, "probe_name", "http")
        if probe_name == "fut-http" and path_without_query == "/__fifa14_local_fut_health":
            sample: dict[str, object] = {}
            if identity_store is not None:
                try:
                    squad_document = identity_store.squad_list()
                    squads = squad_document.get("squadList", []) if isinstance(squad_document, dict) else []
                    if squads and isinstance(squads[0], dict):
                        players = squads[0].get("players", [])
                        if players and isinstance(players[0], dict):
                            item = players[0].get("itemData", {})
                            if isinstance(item, dict):
                                sample = {
                                    key: item.get(key)
                                    for key in (
                                        "id", "itemId", "itemType", "assetId", "resourceId",
                                        "cardsubtypeid", "nation", "leagueId", "teamId",
                                        "preferredPosition", "rating", "resourceGameYear"
                                    )
                                }
                except Exception as error:
                    sample = {"error": str(error)}
            document = {
                "ok": True,
                "buildVersion": "2.41.1-beta2.25.9",
                "pid": os.getpid(),
                "instanceToken": str(getattr(self.server, "instance_token", "")),
                "probe": self.server_version,
                "identityDb": str(getattr(identity_store, "database", getattr(identity_store, "path", ""))) if identity_store is not None else "",
                "fullItemDataRequired": ["itemType", "cardsubtypeid", "nation", "leagueId", "resourceGameYear"],
                "samplePlayer": sample,
            }
            payload = build_fut_json_payload(document)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit("v237-health-response", path=self.path, pid=os.getpid(), sample=sample)
        elif (
            probe_name in {"fut-http", "dynamic-http"}
            and self._is_icebreaker_packlist_path(path_without_query)
        ):
            # V27 keeps the client-side captain screen retail and repairs the
            # server contract instead. The fixture is loaded on every request,
            # so developers can edit the 23-player packs without rebuilding the
            # package. Validation prevents the zero resource IDs that made the
            # retail CardsDLL card constructor dereference a null player object.
            try:
                document = load_icebreaker_fixture()
            except ValueError as error:
                document = {"error": "invalid-icebreaker-fixture", "detail": str(error)}
                payload = build_fut_json_payload(document)
                self.send_response(500)
                self.send_header("content-type", "application/json; charset=utf-8")
                self.send_header("cache-control", "no-store")
                self.send_header("connection", "close")
                emit(
                    "fut-icebreaker-packlist-fixture-error",
                    listener=probe_name,
                    method=self.command,
                    path=self.path,
                    status=500,
                    error=str(error),
                    fixture=str(ICEBREAKER_FIXTURE_PATH),
                )
            else:
                payload = build_fut_json_payload(document)
                self.send_response(200)
                self.send_header("content-type", "application/json; charset=utf-8")
                self.send_header("cache-control", "no-store")
                self.send_header("connection", "close")
                emit(
                    "fut-icebreaker-packlist-response",
                    listener=probe_name,
                    method=self.command,
                    path=self.path,
                    response_name="complete-four-captain-23-player-icebreaker-packlist-v27",
                    status=200,
                    bytes=len(payload),
                    fixture=str(ICEBREAKER_FIXTURE_PATH),
                    captain_ids=[entry["squad"][0] for entry in document["packList"]],
                    formations=[entry["formation"] for entry in document["packList"]],
                    squad_lengths=[len(entry["squad"]) for entry in document["packList"]],
                    response_document=document,
                )
        elif (
            probe_name in {"fut-http", "dynamic-http"}
            and self._is_icebreaker_locstrings_path(path_without_query)
        ):
            payload = self._icebreaker_locstrings_payload()
            self.send_response(200)
            self.send_header("content-type", "application/xml; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                "fut-locstrings-http-response",
                listener=probe_name, method=self.command, path=self.path,
                response_name="retail-icebreaker-captain-names-locstrings-xml",
                status=200, bytes=len(payload),
            )
        elif (
            probe_name in {"fut-http", "dynamic-http"}
            and self._is_leaderboards_locstrings_path(path_without_query)
        ):
            payload = self._leaderboards_locstrings_payload()
            self.send_response(200)
            self.send_header("content-type", "application/xml; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                "fut-locstrings-http-response",
                listener=probe_name, method=self.command, path=self.path,
                response_name="v237-returning-league-nation-locstrings-xml",
                status=200, bytes=len(payload),
            )
        elif (
            probe_name in {"fut-http", "dynamic-http"}
            and self._is_fut_static_metadata_path(path_without_query)
        ):
            document = self._fut_player_metadata_payload(path_without_query)
            if document is None:
                payload = build_fut_json_payload({"error": "unknown-fifa14-player-metadata"})
                self.send_response(404)
                status = 404
                response_name = "fut-static-player-metadata-miss"
            else:
                payload = build_fut_json_payload(document)
                self.send_response(200)
                status = 200
                response_name = "fut-static-player-metadata"
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                response_name, listener=probe_name, method=self.command,
                path=self.path, status=status, bytes=len(payload),
            )
        elif (
            probe_name in {"fut-http", "dynamic-http"}
            and self._is_fut_static_image_path(path_without_query)
        ):
            # Do not disguise a missing retired CDN PNG as XML. A 404 preserves
            # FIFA's normal missing-art fallback and gives us a precise trace
            # path to source later if portraits are the only thing still absent.
            payload = b""
            self.send_response(404)
            self.send_header("content-type", "image/png")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                "fut-static-image-miss", listener=probe_name,
                method=self.command, path=self.path, status=404, bytes=0,
            )
        elif (
            probe_name in {"fut-http", "dynamic-http"}
            and self._is_fut_static_archive_path(path_without_query)
        ):
            trophy_mode = os.environ.get("FIFA14_TROPHY_ARCHIVE_MODE", "emptybig").strip().lower()
            if (
                self._is_fut_trophy_archive_path(path_without_query)
                and trophy_mode not in {"miss", "404", "off", "disabled"}
            ):
                # BETA 2.6 changes exactly one Seasons runtime dependency from
                # the BETA 2.4 capture: the retired trophy item.big is now a
                # successful, parseable empty BIGF container instead of a 404.
                # If the screen advances, the old CDN failure was a gate. If it
                # does not, the early competition tracer below captures the real
                # season/list keys before reward-item lookups begin.
                payload = self._empty_bigf_archive()
                self.send_response(200)
                status = 200
                response_name = "fut-trophy-archive-empty-big-success"
            else:
                payload = b""
                self.send_response(404)
                status = 404
                response_name = "fut-static-archive-miss"
            self.send_header("content-type", "application/octet-stream")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                response_name, listener=probe_name, method=self.command,
                path=self.path, status=status, bytes=len(payload),
                trophy_archive=self._is_fut_trophy_archive_path(path_without_query),
                trophy_mode=trophy_mode,
                big_magic=(payload[:4].decode("ascii", errors="replace") if payload else None),
            )
        elif (
            probe_name in {"fut-http", "dynamic-http"}
            and self._is_store_pack_descriptions_path(path_without_query)
        ):
            payload = self._store_pack_descriptions_payload()
            self.send_response(200)
            self.send_header("content-type", "application/xml; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                "fut-store-pack-descriptions-response",
                listener=probe_name, method=self.command, path=self.path,
                status=200, bytes=len(payload),
            )
        elif (
            probe_name in {"fut-http", "dynamic-http"}
            and self._is_fut_localization_path(path_without_query)
        ):
            # Retired EA FUT localization URLs must not fall through to the
            # generic empty <MESSAGES> response. Serve our local UI tokens for
            # any locale filename (eng_us, eng_gb, etc.).
            payload = self._local_ui_locstrings_payload()
            self.send_response(200)
            self.send_header("content-type", "application/xml; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                "fut-local-ui-locstrings-response", listener=probe_name,
                method=self.command, path=self.path, status=200, bytes=len(payload),
            )
        elif (
            probe_name in {"fut-http", "dynamic-http"}
            and re.fullmatch(r"/fut/items/(?:pc|ps3|xbox360)/-?\d+\.json", path_without_query, re.IGNORECASE)
        ):
            # BETA 2.3 proved that the PC Offline Seasons screen asks for
            # /fut/items/pc/0.json once per division immediately after parsing
            # season/list, and it abandons the screen before season/user is
            # consumed.  A zero item ID is the client's "no reward item"
            # sentinel.  BETA 2.6 therefore performs one controlled
            # compatibility experiment: acknowledge item 0 with a syntactically
            # valid empty JSON document instead of turning the sentinel into an
            # HTTP failure.  Set FIFA14_SEASON_ITEM0_MODE=miss to restore the
            # BETA 2.3 404 behaviour.  Non-zero IDs remain strict misses until
            # their real PC schema is captured.
            match = re.fullmatch(
                r"/fut/items/(?:pc|ps3|xbox360)/(-?\d+)\.json",
                path_without_query,
                re.IGNORECASE,
            )
            item_id = 0 if match is None else int(match.group(1))
            item_zero_mode = os.environ.get("FIFA14_SEASON_ITEM0_MODE", "empty200").strip().lower()
            static_resource = None
            if identity_store is not None and hasattr(identity_store, "static_cosmetic_resource"):
                static_resource = identity_store.static_cosmetic_resource(item_id)
            if isinstance(static_resource, dict):
                payload = build_fut_json_payload(static_resource)
                self.send_response(200)
                status = 200
                response_name = "fut-local-cosmetic-resource"
            elif item_id in {0, -1} and item_zero_mode not in {"miss", "404", "off", "disabled"}:
                payload = b"{}"
                self.send_response(200)
                status = 200
                response_name = "fut-item-sentinel-empty-success"
            else:
                payload = b"{}"
                self.send_response(404)
                status = 404
                response_name = "fut-item-metadata-miss"
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                response_name, listener=probe_name,
                method=self.command, path=self.path, status=status, bytes=len(payload),
                item_id=item_id, item_zero_mode=item_zero_mode,
            )
        elif (
            probe_name in {"fut-http", "dynamic-http"}
            and self._is_dynamic_messages_path(path_without_query)
        ):
            payload = self._dynamic_messages_payload()
            self.send_response(200)
            self.send_header("content-type", "application/xml; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                "dynamic-messages-http-response",
                listener=probe_name,
                method=self.command,
                path=self.path,
                response_name="empty-messages-xml",
                status=200,
                bytes=len(payload),
            )
        elif (
            probe_name == "bootstrap-http"
            and path_without_query == "/futBoot.xml"
        ):
            payload = build_fut_boot_config_payload()
            self.send_response(200)
            self.send_header("content-type", "application/xml; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                "bootstrap-http-response",
                method=self.command,
                path=self.path,
                response_name="fut-boot-config",
                status=200,
                bytes=len(payload),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/user/accountinfo"
        ):
            account_mode = getattr(self.server, "fut_account_mode", "new")
            document = (
                identity_store.account_info()
                if identity_store is not None
                else build_fut_account_info(account_mode)
            )
            payload = build_fut_json_payload(document)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                path=self.path,
                response_name=(
                    identity_store.profile_kind()
                    if identity_store is not None
                    else "user-account-info-first-use-no-club"
                ),
                status=200,
                bytes=len(payload),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/auth"
        ):
            sid = (
                identity_store.start_session(body)
                if identity_store is not None
                else "LOCAL-FIFA14-SID"
            )
            response_document = build_fut_auth_response(sid)
            payload = build_fut_json_payload(response_document)
            try:
                auth_text = body.decode("utf-8")
            except UnicodeDecodeError:
                auth_text = None
            emit(
                "fut-ut-auth-request",
                method=self.command,
                path=self.path,
                host=self.headers.get("Host"),
                content_length=len(body),
                content_type=self.headers.get("Content-Type"),
                user_agent=self.headers.get("User-Agent"),
                body_text=auth_text,
                body_hex=body.hex(),
            )
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("x-ut-sid", sid)
            self.send_header("connection", "close")
            emit(
                "fut-http-response",
                method=self.command,
                path=self.path,
                response_name="ut-auth-json-session-with-x-ut-sid",
                status=200,
                bytes=len(payload),
                x_ut_sid=sid,
                response_body=payload.decode("utf-8", errors="replace"),
                response_document=response_document,
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query
            == "/ut/game/fifa14/phishing/trusteddevice"
        ):
            trusted_document = (
                identity_store.trusted_device()
                if identity_store is not None
                else build_fut_trusted_console_list()
            )
            payload = build_fut_json_payload(trusted_document)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                path=self.path,
                response_name=(
                    "trusted-console-persisted"
                    if trusted_document.get("trusted")
                    else "trusted-console-untrusted"
                ),
                status=200,
                bytes=len(payload),
                response_document=trusted_document,
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query
            in {
                "/ut/game/fifa14/phishing",
                "/ut/game/fifa14/phishing/question",
            }
        ):
            account_mode = getattr(self.server, "fut_account_mode", "new")
            response = (
                identity_store.phishing_question()
                if identity_store is not None
                else build_fut_phishing_question(account_mode)
            )
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            if "token" in response:
                self.send_header(
                    "set-cookie",
                    "FUTWebPhishing=LOCAL-FIFA14-PHISHING; Path=/; HttpOnly",
                )
            emit(
                "fut-http-response",
                method=self.command,
                path=self.path,
                response_name=f"phishing-question-{account_mode}",
                status=200,
                bytes=len(payload),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/phishing/validate"
        ):
            response = (
                identity_store.validate_phishing_answer()
                if identity_store is not None
                else build_fut_phishing_validation()
            )
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header(
                "set-cookie",
                "FUTWebPhishing=LOCAL-FIFA14-PHISHING; Path=/; HttpOnly",
            )
            emit(
                "fut-http-response",
                method=self.command,
                path=self.path,
                response_name="phishing-validation",
                status=200,
                bytes=len(payload),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/settings"
        ):
            response = build_fut_settings_response()
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name="fut-settings", status=200, bytes=len(payload), response_document=response)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/match/reset"
        ):
            request_document = {}
            if body:
                try:
                    parsed = json.loads(body.decode("utf-8"))
                    if isinstance(parsed, dict):
                        request_document = parsed
                except (UnicodeDecodeError, json.JSONDecodeError):
                    request_document = {}
            response = (
                identity_store.reset_match(request_document)
                if identity_store is not None and hasattr(identity_store, "reset_match")
                else {}
            )
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name="fut-match-reset-beta" if response else "fut-match-reset", status=200, bytes=len(payload), response_document=response)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/userdata"
        ):
            response = build_fut_empty_user_data_response()
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name="fut-userdata-empty", status=200, bytes=len(payload), response_document=response)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query.startswith("/ut/v2/game/fifa14/store/transaction")
        ):
            if effective_method == "GET":
                response = build_fut_no_transaction_response()
                response_name = "fut-no-store-transaction"
                status = 200
            else:
                request = self._decode_store_purchase(body)
                requested_state = str(request.get("state") or "").upper()
                tail = path_without_query.rsplit("/", 1)[-1]
                transaction_id = request.get("transactionId")
                if transaction_id is None and tail.isdigit():
                    transaction_id = int(tail)
                try:
                    transaction_id_int = int(transaction_id or 0)
                except (TypeError, ValueError):
                    transaction_id_int = 0

                # FIFA probes/cancels stale transactions during startup. That
                # cleanup must never create or charge for a pack.
                if requested_state == "TRANSACTIONCANCEL":
                    response = {"state": "TRANSACTIONCANCEL", "transactionId": transaction_id_int}
                    response_name = "fut-store-transaction-cancel-ack"
                    status = 200
                else:
                    # If this transaction was created by the Store POST step,
                    # return it idempotently rather than purchasing twice.
                    response = (
                        identity_store.purchase_transaction(transaction_id_int)
                        if identity_store is not None and transaction_id_int > 0
                        else None
                    )
                    if response is not None:
                        response_name = "fut-store-transaction-existing-purchase"
                        status = 200
                    else:
                        raw_pack_type = self._store_pack_selector(request)
                        if raw_pack_type is not None and identity_store is not None:
                            currency = str(
                                request.get("currency")
                                or request.get("currencyId")
                                or ("COINS" if request.get("useCredits", True) else "FIFA_POINTS")
                            )
                            try:
                                response = identity_store.purchase_pack(raw_pack_type, currency=currency)
                                response_name = "fut-store-transaction-pack-purchase"
                                status = 200
                            except (TypeError, ValueError) as error:
                                response = {"code": "400", "reason": str(error)}
                                response_name = "fut-store-transaction-pack-error"
                                status = 400
                        else:
                            # Unknown non-purchase transition: acknowledge the
                            # retail state without fabricating inventory.
                            response = {
                                "state": requested_state or "NOTRANSACTION",
                                "transactionId": transaction_id_int,
                            }
                            response_name = "fut-store-transaction-state-ack"
                            status = 200
            payload = build_fut_json_payload(response)
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name=response_name, status=status, bytes=len(payload), response_document=response)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/clientdata/pileSize"
        ):
            response = build_fut_empty_pile_sizes_response()
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name="fut-empty-pile-sizes", status=200, bytes=len(payload), response_document=response)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query in {
                "/ut/game/fifa14/clientdata/tutorialpopups",
                "/ut/game/fifa14/clientdata/userHubData",
                "/ut/game/fifa14/clientdata/managerquest",
            }
        ):
            data_key = path_without_query.rsplit("/", 1)[-1]
            try:
                if identity_store is not None and effective_method in {"PUT", "POST"}:
                    request = json.loads(body.decode("utf-8")) if body else {}
                    response = identity_store.save_client_data(data_key, request)
                    response_name = f"fut-clientdata-{data_key}-saved"
                elif identity_store is not None:
                    # clientdata/userHubData is a persisted client-data blob, not
                    # RS4:FutGetHubDataServerResponse.  Keep its proven retail
                    # boot contract untouched; the native hub summary is /hub.
                    response = identity_store.client_data(data_key)
                    response_name = f"fut-clientdata-{data_key}-loaded"
                else:
                    response = {}
                    response_name = "fut-clientdata-empty"
                payload = build_fut_json_payload(response)
                self.send_response(200)
                status = 200
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                payload = build_fut_json_payload({"code": "400", "reason": str(error)})
                self.send_response(400)
                response_name = "fut-clientdata-invalid"
                status = 400
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name=response_name, status=status, bytes=len(payload))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/managerquest"
        ):
            payload = build_fut_json_payload({})
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name="fut-managerquest-empty", status=200, bytes=len(payload))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query in {
                "/ut/game/fifa14/club/stats/year",
                "/ut/game/fifa14/club/stats/consumables",
                "/ut/game/fifa14/club/stats/newcards",
            }
            and identity_store is not None
        ):
            # FutStickerBookStats2ServerResponse indexes statistics by a retail
            # context enum, not by route-local zeroes.  CardsDLL maps these as:
            #   2=year, 5=newcards, 6=consumables.
            # The year context also retains its contextValue (2014); the two
            # aggregate contexts normalize contextValue to zero.
            if path_without_query.endswith("/year"):
                context_id, context_value = 2, 2014
            elif path_without_query.endswith("/newcards"):
                context_id, context_value = 5, 0
            else:
                context_id, context_value = 6, 0
            response = (
                identity_store.consumable_stats()
                if context_id == 6
                else identity_store.club_stats(context_id=context_id, context_value=context_value)
            )
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name=("fut-consumable-club-stats-beta2258" if context_id == 6 else "fut-player-club-stats"), status=200, bytes=len(payload), response_document=response)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and identity_store is not None
            and path_without_query.startswith("/ut/game/fifa14/club/consumables")
            and effective_method == "GET"
        ):
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            query["type"] = ["consumable"]
            response = identity_store.club_items(query)
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path,
                 response_name="fut-club-consumables-beta2258", status=200, bytes=len(payload),
                 total=int(response.get("total", 0)))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and identity_store is not None
            and re.fullmatch(r"/ut/game/fifa14/club/stats/(country|league)/\d+", path_without_query)
        ):
            parts = path_without_query.rsplit("/", 2)
            context_kind, context_number = parts[-2], int(parts[-1])
            if context_kind == "country":
                response = identity_store.club_stats(
                    context_id=3, context_value=context_number, nation=context_number
                )
            else:
                response = identity_store.club_stats(
                    context_id=4, context_value=context_number, league=context_number
                )
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name="fut-player-club-context-stats", status=200, bytes=len(payload), response_document=response)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/club/stats/staff"
        ):
            response = {"itemData": []}
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name="fut-empty-staff-stats", status=200, bytes=len(payload), response_document=response)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/user/credits"
        ):
            # The HUD balance model consumes the full FUT credits document,
            # including the named currency entries.  Keep /user itself on the
            # proven safe-entry shape and enrich only this dedicated endpoint.
            response = (
                identity_store.currencies()
                if identity_store is not None
                else {
                    "credits": 0,
                    "fifaPoints": 0,
                    "bidTokens": {"count": 0, "updateTime": 0},
                    "currencies": [
                        {"name": "coins", "funds": 0, "finalFunds": 0},
                        {"name": "points", "funds": 0, "finalFunds": 0},
                    ],
                    "unopenedPacks": {"preOrderPacks": 0, "recoveredPacks": 0},
                }
            )
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                path=self.path,
                response_name="user-credits",
                status=200,
                bytes=len(payload),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/user"
            and identity_store is not None
        ):
            try:
                persisted = None
                if effective_method in {"POST", "PUT"} and body:
                    request = json.loads(body.decode("utf-8"))
                    if isinstance(request, dict) and (request.get("clubName") or request.get("clubAbbr")):
                        persisted = identity_store.update_club_profile(request)
                response = identity_store.ensure_fut_user()
                payload = build_fut_json_payload(response)
                self.send_response(200)
                status = 200
                response_name = "fut-user-persisted" if persisted is not None else "fut-user"
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                payload = build_fut_json_payload({"code": "400", "reason": str(error)})
                self.send_response(400)
                status = 400
                response_name = "fut-user-invalid"
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response", method=self.command, effective_method=effective_method,
                path=self.path, response_name=response_name, status=status, bytes=len(payload),
                response_document=response if status == 200 else None,
                record=(identity_store.match_record() if status == 200 and hasattr(identity_store, "match_record") else None),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/user/action"
            and identity_store is not None
        ):
            # FutGetUserActionServerResponse requires an object root and scans
            # key names rather than their boolean values. Expose only completed
            # action keys; a new persona receives {}, never false INTRO_DONE or
            # CHARITY_MATCH_PLAYED keys that would incorrectly skip onboarding.
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            action_type = query.get("actionType", [""])[0].strip().upper()
            stored_actions = identity_store.user_actions()
            if action_type:
                completed_action_map = (
                    {action_type: True}
                    if stored_actions.get(action_type, False)
                    else {}
                )
            else:
                completed_action_map = {
                    name: True
                    for name, completed in sorted(stored_actions.items())
                    if completed
                }
            completed_actions = list(completed_action_map)
            payload = build_fut_json_payload(completed_action_map)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-user-action-query",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                action_type=action_type,
                action_type_blank=not bool(action_type),
                completed_actions=completed_actions,
                contract="completed-action-key-object",
            )
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name="fut-user-action-query",
                status=200,
                bytes=len(payload),
                action_type=action_type,
                completed_actions=completed_actions,
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query.startswith(
                "/ut/game/fifa14/user/action/"
            )
            and identity_store is not None
        ):
            action_name = path_without_query.rsplit("/", 1)[-1]
            try:
                provisioned = None
                if (
                    action_name.strip().upper()
                    == "ICEBREAKER_ENGLISH_CAPTAIN_SELECTED"
                    and effective_method != "DELETE"
                ):
                    # The retail action carries no selected pack body. Persist
                    # the validated Messi captain pack as a deterministic local
                    # active squad before the very next /user request. This is
                    # server state only; the on-screen selected squad remains
                    # the one built by retail RetrievePack/BuildSquad.
                    fixture = load_icebreaker_fixture()
                    provisioned = identity_store.provision_pack_play_club(
                        fixture["packList"][1]
                    )
                    emit(
                        "fut-icebreaker-local-club-provisioned",
                        path=self.path,
                        **provisioned,
                    )
                response = identity_store.update_user_action(
                    action_name,
                    completed=effective_method != "DELETE",
                )
                payload = build_fut_json_payload(response)
                self.send_response(200)
                response_name = f"fut-user-action-{action_name}"
            except ValueError as error:
                payload = build_fut_json_payload(
                    {"code": "400", "reason": str(error)}
                )
                self.send_response(400)
                response_name = "fut-user-action-invalid"
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name=response_name,
                status=200 if response_name != "fut-user-action-invalid" else 400,
                bytes=len(payload),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/user/club"
            and identity_store is not None
        ):
            try:
                request = json.loads(body.decode("utf-8")) if body else {}
                club = identity_store.update_club_profile(request)
                payload = build_fut_json_payload({"club": club})
                self.send_response(200)
                status = 200
                response_name = "fut-club-profile-saved"
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                payload = build_fut_json_payload({"code": "400", "reason": str(error)})
                self.send_response(400)
                status = 400
                response_name = "fut-club-profile-invalid"
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name=response_name, status=status, bytes=len(payload))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and (
                path_without_query
                in {
                    "/ut/game/fifa14/club",
                    "/ut/game/fifa14/clubUser",
                    "/ut/game/fifa14/item",
                    "/ut/game/fifa14/item/resource",
                    "/ut/delete/game/fifa14/item",
                }
                or re.fullmatch(r"/ut/game/fifa14/item(?:/resource)?/\d+", path_without_query, re.IGNORECASE)
            )
            and identity_store is not None
        ):
            if (
                path_without_query in {"/ut/game/fifa14/item", "/ut/game/fifa14/item/resource"} and effective_method == "DELETE"
            ) or (
                path_without_query == "/ut/delete/game/fifa14/item" and effective_method == "POST"
            ):
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                raw_ids: list[str] = []
                for key in ("itemIds", "itemId", "ids", "id"):
                    for value in query.get(key, []):
                        raw_ids.extend(part.strip() for part in str(value).split(",") if part.strip())
                if not raw_ids and body:
                    try:
                        document = json.loads(body.decode("utf-8"))
                        candidate = (
                            document.get("itemId", document.get("itemIds", document.get("itemData", [])))
                            if isinstance(document, dict) else []
                        )
                        if not isinstance(candidate, list):
                            candidate = [candidate]
                        for entry in candidate:
                            if isinstance(entry, dict):
                                raw_ids.append(str(entry.get("id", entry.get("itemId", ""))))
                            else:
                                raw_ids.append(str(entry))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
                item_ids = [int(value) for value in raw_ids if str(value).lstrip("-").isdigit()]
                response = identity_store.quick_sell(item_ids)
                status = 200
                response_name = "local-item-quicksell"
                emit("fut-local-item-quicksell", method=effective_method, path=self.path,
                     requested=len(item_ids), sold=len(response.get("items", [])), totalCredits=response.get("totalCredits"))
            elif (
                re.fullmatch(r"/ut/game/fifa14/item/resource/(\d+)", path_without_query, re.IGNORECASE)
                and effective_method == "POST"
            ):
                apply_match = re.fullmatch(
                    r"/ut/game/fifa14/item/resource/(\d+)", path_without_query, re.IGNORECASE
                )
                try:
                    document = json.loads(body.decode("utf-8")) if body else {}
                    apply_rows = document.get("apply", []) if isinstance(document, dict) else []
                    if isinstance(apply_rows, dict):
                        apply_rows = [apply_rows]
                    if not isinstance(apply_rows, list) or not apply_rows:
                        raise ValueError("missing consumable apply target")
                    target_ids = []
                    for row in apply_rows:
                        raw_id = row.get("id", row.get("itemId")) if isinstance(row, dict) else row
                        try:
                            target_ids.append(int(raw_id))
                        except (TypeError, ValueError):
                            continue
                    if not target_ids:
                        raise ValueError("missing consumable apply target")
                    resource_id = int(apply_match.group(1))
                    result = identity_store.apply_consumable(resource_id, target_ids)
                    # Retail FUT's apply-consumable call is success-by-status; keep
                    # the wire response empty/minimal and log the local mutation.
                    response = {}
                    status = 200
                    response_name = "local-consumable-apply-beta224"
                    emit(
                        "fut-local-consumable-apply-beta224",
                        method=effective_method,
                        path=self.path,
                        resource_id=resource_id,
                        target_ids=target_ids,
                        consumed_item_id=result.get("consumedItemId"),
                        effect=result.get("effect"),
                        changed_ids=[row.get("id") for row in result.get("itemData", []) if isinstance(row, dict)],
                    )
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                    response = {"code": "400", "reason": str(error)}
                    status = 400
                    response_name = "local-consumable-apply-error-beta224"
                    emit(
                        "fut-local-consumable-apply-error-beta224",
                        method=effective_method,
                        path=self.path,
                        reason=str(error),
                    )
            elif (
                path_without_query in {"/ut/game/fifa14/item", "/ut/game/fifa14/item/resource"}
                or re.fullmatch(r"/ut/game/fifa14/item(?:/resource)?/\d+", path_without_query, re.IGNORECASE)
            ) and effective_method in {"POST", "PUT"}:
                try:
                    document = json.loads(body.decode("utf-8")) if body else {}
                    updates = document.get("itemData", []) if isinstance(document, dict) else []
                    if isinstance(updates, dict):
                        updates = [updates]
                    # The retail "Make Active" delegate may address one item
                    # directly (/item/<id>) and send itemState at the document
                    # root instead of wrapping it in itemData.
                    direct_id_match = re.fullmatch(
                        r"/ut/game/fifa14/item(?:/resource)?/(\d+)",
                        path_without_query, re.IGNORECASE,
                    )
                    if isinstance(document, dict) and not updates and (
                        "itemState" in document or "state" in document or direct_id_match
                    ):
                        direct = dict(document)
                        if direct_id_match and "id" not in direct and "itemId" not in direct:
                            direct["id"] = int(direct_id_match.group(1))
                        updates = [direct]
                    if not isinstance(updates, list):
                        updates = []
                    response = identity_store.move_items(updates)
                    status = 200
                    requested_states = [
                        str(row.get("itemState", row.get("state", "")))
                        for row in updates if isinstance(row, dict)
                    ]
                    resolved_states = [
                        str(row.get("itemState", ""))
                        for row in response.get("itemData", []) if isinstance(row, dict)
                    ]
                    activation = any(
                        state.replace("_", "").replace("-", "").lower()
                        in {"activehomekit", "activeawaykit", "activestadium", "activebadge"}
                        for state in resolved_states
                    )
                    response_name = "local-club-item-activation-beta222" if activation else "local-item-pile-move"
                    emit(
                        "fut-local-item-pile-move",
                        method=effective_method,
                        path=self.path,
                        requested=len(updates),
                        requested_states=requested_states,
                        resolved_states=resolved_states,
                        activation=activation,
                        request_document=document,
                        successful=sum(1 for row in response.get("itemData", []) if row.get("success")),
                        response_document=response,
                    )
                except (ValueError, json.JSONDecodeError) as error:
                    response = {"code": "400", "reason": str(error)}
                    status = 400
                    response_name = "local-item-pile-move-error"
            else:
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                direct_view_match = re.fullmatch(
                    r"/ut/game/fifa14/item/(\d+)", path_without_query, re.IGNORECASE
                )
                view_ids: list[int] = []
                if effective_method == "GET" and direct_view_match:
                    view_ids.append(int(direct_view_match.group(1)))
                elif effective_method == "GET" and path_without_query == "/ut/game/fifa14/item":
                    for key in ("idList", "itemIds", "itemId", "ids", "id"):
                        for value in query.get(key, []):
                            for part in str(value).split(","):
                                part = part.strip()
                                if part.isdigit():
                                    view_ids.append(int(part))
                if view_ids:
                    response = identity_store.view_items(view_ids)
                    status = 200
                    response_name = "local-view-cards-beta222"
                    returned = response.get("itemData", []) if isinstance(response, dict) else []
                    emit(
                        "fut-view-cards-response-beta222",
                        method=effective_method,
                        path=self.path,
                        requested_ids=view_ids,
                        count=len(returned),
                        items=[{
                            "id": row.get("id"), "itemType": row.get("itemType"),
                            "itemState": row.get("itemState"), "assetId": row.get("assetId"),
                            "resourceId": row.get("resourceId"), "wireKeys": list(row.keys()),
                        } for row in returned if isinstance(row, dict)],
                    )
                else:
                    response = identity_store.club_items(
                        query, include_consumables_default=(path_without_query == "/ut/game/fifa14/clubUser")
                    )
                    status = 200
                    response_name = "local-club-item-collection-beta2258"
                    returned = response.get("itemData", []) if isinstance(response, dict) else []
                    emit(
                        "fut-club-query-response-v237",
                        method=effective_method,
                        path=self.path,
                        filters={key: values[-1] if values else "" for key, values in query.items()},
                        total=response.get("total", 0) if isinstance(response, dict) else 0,
                        count=response.get("count", 0) if isinstance(response, dict) else 0,
                        players=[{
                            "id": row.get("id"), "assetId": row.get("assetId"),
                            "resourceId": row.get("resourceId"), "itemType": row.get("itemType"),
                            "cardsubtypeid": row.get("cardsubtypeid"), "category": row.get("category"),
                            "rating": row.get("rating"), "quality": row.get("quality"),
                            "rareflag": row.get("rareflag"), "itemState": row.get("itemState"),
                            "position": row.get("preferredPosition"),
                            "teamId": row.get("teamId"), "leagueId": row.get("leagueId"),
                            "nation": row.get("nation"),
                            "wireKeys": list(row.keys()),
                            "hasCardSubtype": "cardsubtypeid" in row,
                        } for row in returned[:50] if isinstance(row, dict)],
                    )
            payload = build_fut_json_payload(response)
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name=response_name,
                status=status,
                bytes=len(payload),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and (
                path_without_query == "/ut/game/fifa14/squad"
                or path_without_query.startswith("/ut/game/fifa14/squad/")
            )
            and identity_store is not None
        ):
            try:
                request = None
                tail = path_without_query.rsplit("/", 1)[-1]
                requested_id = int(tail) if tail.isdigit() else None
                if effective_method in {"PUT", "POST"} and body:
                    request = json.loads(body.decode("utf-8"))
                    identity_store.save_squad(request, requested_id=requested_id)
                    response = identity_store.squad_detail(requested_id) if hasattr(identity_store, "squad_detail") else identity_store.active_squad_document()
                    response_name = "squad-saved-detail-beta222"
                elif path_without_query == "/ut/game/fifa14/squad/list":
                    response = identity_store.squad_list_compact() if hasattr(identity_store, "squad_list_compact") else identity_store.squad_list()
                    response_name = "squad-list-compact-beta222"
                elif path_without_query == "/ut/game/fifa14/squad/active":
                    response = identity_store.active_squad_document()
                    response_name = "squad-active-detail-beta222"
                elif requested_id is not None:
                    response = identity_store.squad_detail(requested_id) if hasattr(identity_store, "squad_detail") else identity_store.active_squad_document()
                    response_name = "squad-id-detail-beta222"
                else:
                    response = identity_store.squad_list_compact() if hasattr(identity_store, "squad_list_compact") else identity_store.squad_list()
                    response_name = "squad-list-compact-beta222"
                try:
                    incoming_players = request.get("players", []) if isinstance(request, dict) else []
                    incoming_filled = sum(1 for row in incoming_players if isinstance(row, dict) and isinstance(row.get("itemData"), dict) and int((row.get("itemData") or {}).get("id", (row.get("itemData") or {}).get("itemId", 0)) or 0) > 0)
                    if isinstance(response, dict) and isinstance(response.get("players"), list):
                        detail = response
                    else:
                        detail = identity_store.active_squad_document()
                    outgoing_players = detail.get("players", []) if isinstance(detail, dict) else []
                    outgoing_filled = sum(1 for row in outgoing_players if isinstance(row, dict) and isinstance(row.get("itemData"), dict) and int((row.get("itemData") or {}).get("id", (row.get("itemData") or {}).get("itemId", 0)) or 0) > 0)
                    emit("fut-squad-state-beta222", method=effective_method, path=self.path, response_name=response_name, incoming_filled=incoming_filled, outgoing_filled=outgoing_filled, response_keys=list(response.keys()) if isinstance(response, dict) else [], outgoing_chemistry=detail.get("chemistry") if isinstance(detail, dict) else None, outgoing_rating=detail.get("starRating") if isinstance(detail, dict) else None, outgoing_actives=[row.get("itemState") for row in detail.get("actives", []) if isinstance(row, dict)] if isinstance(detail, dict) else [])
                except Exception as diagnostic_error:
                    emit("fut-squad-state-diagnostic-error-beta222", error=str(diagnostic_error))
                payload = build_fut_json_payload(response)
                self.send_response(200)
                status = 200
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                payload = build_fut_json_payload({"code": "400", "reason": str(error)})
                self.send_response(400)
                response_name = "squad-invalid"
                status = 400
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name=response_name, status=status, bytes=len(payload), response_document=response if status == 200 and len(payload) < 12000 else None)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/transfermarket"
            and identity_store is not None
            and effective_method == "GET"
        ):
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            response = identity_store.market_search(query)
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path,
                 response_name="transfer-market-beta2250", status=200, bytes=len(payload),
                 auction_count=len(response.get("auctionInfo", [])), total=int(response.get("total", 0)))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query in {"/ut/game/fifa14/tradePile", "/ut/game/fifa14/tradepile"}
            and identity_store is not None
            and effective_method == "GET"
        ):
            response = identity_store.trade_pile()
            payload = build_fut_json_payload(response)
            self.send_response(200); self.send_header("content-type", "application/json; charset=utf-8"); self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command,effective_method=effective_method,path=self.path,response_name="trade-pile-beta2250",status=200,bytes=len(payload),total=int(response.get("total",0)))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query in {"/ut/game/fifa14/watchList", "/ut/game/fifa14/watchlist"}
            and identity_store is not None
        ):
            response = identity_store.empty_auctions(); payload = build_fut_json_payload(response)
            self.send_response(200); self.send_header("content-type", "application/json; charset=utf-8"); self.send_header("cache-control", "no-store")
            emit("fut-http-response",method=self.command,effective_method=effective_method,path=self.path,response_name="watchlist-empty-beta2250",status=200,bytes=len(payload))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/auctionhouse"
            and identity_store is not None
            and effective_method == "POST"
        ):
            try:
                request = json.loads(body.decode("utf-8")) if body else {}
                response = identity_store.list_for_sale(request if isinstance(request,dict) else {})
                status=200
            except (UnicodeDecodeError,json.JSONDecodeError,ValueError) as error:
                response={"reason":str(error),"code":"400"}; status=400
            payload=build_fut_json_payload(response); self.send_response(status); self.send_header("content-type","application/json; charset=utf-8"); self.send_header("cache-control","no-store")
            emit("fut-http-response",method=self.command,effective_method=effective_method,path=self.path,response_name="auction-list-beta2250",status=status,bytes=len(payload))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query.startswith("/ut/game/fifa14/auctionhouse/")
            and identity_store is not None
            and effective_method == "DELETE"
        ):
            try: trade_id=int(path_without_query.rsplit("/",1)[-1])
            except ValueError: trade_id=0
            response=identity_store.withdraw_listing(trade_id); payload=build_fut_json_payload(response)
            self.send_response(200); self.send_header("content-type","application/json; charset=utf-8"); self.send_header("cache-control","no-store")
            emit("fut-http-response",method=self.command,effective_method=effective_method,path=self.path,response_name="auction-withdraw-beta2250",status=200,bytes=len(payload))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/trade/status"
            and identity_store is not None
            and effective_method == "GET"
        ):
            query=parse_qs(urlsplit(self.path).query,keep_blank_values=True); trade_ids=[]
            for raw in query.get("tradeIds",[]):
                for piece in str(raw).split(","):
                    try: trade_ids.append(int(piece))
                    except ValueError: pass
            response=identity_store.market_status(trade_ids); payload=build_fut_json_payload(response)
            self.send_response(200); self.send_header("content-type","application/json; charset=utf-8"); self.send_header("cache-control","no-store")
            emit("fut-http-response",method=self.command,effective_method=effective_method,path=self.path,response_name="trade-status-beta2250",status=200,bytes=len(payload))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and re.fullmatch(r"/ut/game/fifa14/trade/\d+/offer", path_without_query, re.IGNORECASE)
            and identity_store is not None
            and effective_method == "GET"
        ):
            # BETA 2.25.4 accidentally advertised an auto-sold Buy Now as
            # offers=1. A stale retail UI can therefore ask to "View Offer".
            # Never fall through to the 54-byte empty-auction document: return
            # the known auction/status shape with offers=0 so CardsDLL has a
            # complete renderable record and can safely unwind the stale action.
            trade_id=int(path_without_query.split("/")[-2])
            response=identity_store.market_status([trade_id]); payload=build_fut_json_payload(response)
            self.send_response(200); self.send_header("content-type","application/json; charset=utf-8"); self.send_header("cache-control","no-store")
            emit("fut-http-response",method=self.command,effective_method=effective_method,path=self.path,response_name="trade-offer-safe-beta2255",status=200,bytes=len(payload),trade_id=trade_id,total=int(response.get("total",0)))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and re.fullmatch(r"/ut/game/fifa14/trade/\d+/(?:bid|offer)", path_without_query, re.IGNORECASE)
            and identity_store is not None
            and effective_method in {"PUT","POST"}
        ):
            trade_id=int(path_without_query.split("/")[-2])
            try: request=json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError,json.JSONDecodeError): request={}
            try: amount=int(request.get("bid") or request.get("buyNowPrice") or request.get("amount") or 0) if isinstance(request,dict) else 0
            except (TypeError,ValueError): amount=0
            response=identity_store.market_bid(trade_id,amount); payload=build_fut_json_payload(response)
            self.send_response(200); self.send_header("content-type","application/json; charset=utf-8"); self.send_header("cache-control","no-store")
            emit("fut-http-response",method=self.command,effective_method=effective_method,path=self.path,response_name="market-bid-beta2250",status=200,bytes=len(payload),trade_id=trade_id,amount=amount)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/trade"
            and identity_store is not None
            and effective_method == "POST"
        ):
            try:
                request=json.loads(body.decode("utf-8")) if body else {}; response=identity_store.list_for_sale(request if isinstance(request,dict) else {}); status=200
            except (UnicodeDecodeError,json.JSONDecodeError,ValueError) as error:
                response={"reason":str(error),"code":"400"}; status=400
            payload=build_fut_json_payload(response); self.send_response(status); self.send_header("content-type","application/json; charset=utf-8"); self.send_header("cache-control","no-store")
            emit("fut-http-response",method=self.command,effective_method=effective_method,path=self.path,response_name="trade-list-beta2250",status=status,bytes=len(payload))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and re.fullmatch(r"/ut/delete/game/fifa14/trade/\d+", path_without_query, re.IGNORECASE)
            and identity_store is not None
            and effective_method in {"GET", "DELETE", "POST"}
        ):
            trade_id = int(path_without_query.rsplit("/", 1)[-1])
            response = identity_store.withdraw_listing(trade_id)
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path,
                 response_name="trade-clear-sold-retail-route-beta2258", status=200, bytes=len(payload), trade_id=trade_id)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and re.fullmatch(r"/ut/game/fifa14/trade/\d+", path_without_query, re.IGNORECASE)
            and identity_store is not None
            and effective_method == "DELETE"
        ):
            trade_id=int(path_without_query.rsplit("/",1)[-1])
            response=identity_store.withdraw_listing(trade_id); payload=build_fut_json_payload(response)
            self.send_response(200); self.send_header("content-type","application/json; charset=utf-8"); self.send_header("cache-control","no-store")
            emit("fut-http-response",method=self.command,effective_method=effective_method,path=self.path,response_name="trade-clear-or-withdraw-beta2254",status=200,bytes=len(payload),trade_id=trade_id)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and (path_without_query == "/ut/game/fifa14/trade" or path_without_query.startswith("/ut/game/fifa14/trade/"))
            and identity_store is not None
        ):
            response=identity_store.empty_auctions(); payload=build_fut_json_payload(response)
            self.send_response(200); self.send_header("content-type","application/json; charset=utf-8"); self.send_header("cache-control","no-store")
            emit("fut-http-response",method=self.command,effective_method=effective_method,path=self.path,response_name="fut-trade-fallback-beta2250",status=200,bytes=len(payload))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query
            in {
                "/ut/game/fifa14/purchased",
                "/ut/game/fifa14/purchased/items",
            }
            and identity_store is not None
        ):
            # v2.40.11: the retail Store's CREATEPACK path does not POST to
            # /store at all. The successful v2.40.10 capture proves the actual
            # coin-purchase wire contract is:
            #   POST /ut/game/fifa14/purchased/items
            #   {"packId":N,"useCredits":1,"usePreOrder":0,"currency":"COINS"}
            #
            # Older local builds treated every method on this endpoint as a
            # read and returned empty_purchased_items(), which let the frontend
            # transition to New Items while charging nothing and returning zero
            # cards. Commit the purchase on POST/PUT, then keep GET as the
            # purchased/unassigned-pile retrieval path.
            if effective_method in {"POST", "PUT"}:
                request = self._decode_store_purchase(body)
                raw_pack_type = self._store_pack_selector(request)
                currency = str(
                    request.get("currency")
                    or request.get("currencyId")
                    or ("COINS" if request.get("useCredits", True) else "FIFA_POINTS")
                )
                try:
                    if raw_pack_type is None:
                        raise ValueError("purchased/items request did not include a numeric packId/id/packType/serverId")
                    response = identity_store.purchase_pack(raw_pack_type, currency=currency)
                    status = 200
                    response_name = "local-purchased-items-pack-purchase"
                    emit(
                        "fut-purchased-items-pack-purchased",
                        method=self.command,
                        effective_method=effective_method,
                        path=self.path,
                        pack_id=int(response.get("packId", raw_pack_type)),
                        transaction_id=int(response.get("transactionId", 0)),
                        item_count=len(response.get("itemData", [])),
                        duplicate_count=len(response.get("duplicateItemIdList", [])),
                        credits=int(response.get("credits", 0)),
                        # BETA 2.25.0: never serialize the entire pack payload
                        # into redirect-probe.log on the live Store request.
                        # Jumbo player packs can exceed ~90 KB and the same
                        # document was previously JSON-encoded twice.
                    )
                except (TypeError, ValueError) as error:
                    response = {"code": "400", "reason": str(error)}
                    status = 400
                    response_name = "local-purchased-items-pack-purchase-error"
                    emit(
                        "fut-purchased-items-pack-purchase-error",
                        method=self.command,
                        effective_method=effective_method,
                        path=self.path,
                        request_document=request,
                        error=str(error),
                    )
            else:
                response = identity_store.purchased_items()
                status = 200
                response_name = "local-purchased-items"
            payload = build_fut_json_payload(response)
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name=response_name,
                status=status,
                bytes=len(payload),
                # Keep pack runtime diagnostics O(1) in item count. The client
                # still receives the exact same payload; only disk telemetry is
                # summarized in BETA 2.25.0.
                item_count=len(response.get("itemData", [])) if isinstance(response, dict) else 0,
                duplicate_count=len(response.get("duplicateItemIdList", [])) if isinstance(response, dict) else 0,
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and (
                path_without_query.startswith("/ut/game/fifa14/store")
                or path_without_query.startswith("/ut/v2/game/fifa14/store")
            )
            and identity_store is not None
            and "/transaction" not in path_without_query
            and effective_method in {"POST", "PUT"}
        ):
            request = self._decode_store_purchase(body)
            raw_pack_type = self._store_pack_selector(request)
            currency = str(
                request.get("currency")
                or request.get("currencyId")
                or ("COINS" if request.get("useCredits", True) else "FIFA_POINTS")
            )
            try:
                if raw_pack_type is None:
                    raise ValueError("store purchase did not include a numeric packId/id/packType/serverId")
                response = identity_store.purchase_pack(raw_pack_type, currency=currency)
                payload = build_fut_json_payload(response)
                self.send_response(200)
                response_name = "local-store-pack-purchase"
                emit(
                    "fut-store-pack-purchased",
                    method=self.command, effective_method=effective_method, path=self.path,
                    request_document=request, response_document=response,
                )
            except (TypeError, ValueError) as error:
                response = {"code": "400", "reason": str(error)}
                payload = build_fut_json_payload(response)
                self.send_response(400)
                response_name = "local-store-pack-purchase-error"
                emit(
                    "fut-store-pack-purchase-error",
                    method=self.command, effective_method=effective_method, path=self.path,
                    request_document=request, error=str(error),
                )
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                "fut-http-response", method=self.command, effective_method=effective_method,
                path=self.path, response_name=response_name, status=200 if response_name == "local-store-pack-purchase" else 400, bytes=len(payload),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and (
                path_without_query.startswith("/ut/game/fifa14/store")
                or path_without_query.startswith("/ut/v2/game/fifa14/store")
            )
            and identity_store is not None
            and "/transaction" not in path_without_query
        ):
            if "quantity" in self.path.lower():
                response = identity_store.store_pack_quantities()
                response_name = "store-pack-quantities"
            else:
                response = identity_store.store_pack_types()
                response_name = "store-pack-types-v237"
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            emit(
                "fut-http-response", method=self.command, effective_method=effective_method,
                path=self.path, response_name=response_name, status=200, bytes=len(payload),
                response_document=response,
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and (
                path_without_query
                in {
                    "/ut/game/fifa14/clientdata",
                    "/ut/game/fifa14/activeMessage",
                    "/ut/game/fifa14/leaderboards",
                    "/ut/game/fifa14/leaderboards/options",
                }
                or path_without_query.startswith(
                    "/ut/game/fifa14/leaderboards/"
                )
            )
            and identity_store is not None
        ):
            payload = build_fut_json_payload({})
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name="empty-optional-fut-service",
                status=200,
                bytes=len(payload),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/hub"
            and identity_store is not None
        ):
            # Exact FIFA 14 CardsDLL pairing: the literal /hub sits beside
            # RS4:FutGetHubDataServerResponse, whose scalar keys are
            # auctionCount and clubPlayers.  Do not confuse this with the
            # unrelated persisted /clientdata/userHubData document.
            response = identity_store.hub_data()
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name="fut-hub-data-native",
                status=200,
                bytes=len(payload),
                response_document=response,
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/user/list"
            and identity_store is not None
        ):
            payload = build_fut_json_payload(
                {"userInfo": [identity_store.ensure_fut_user()]}
            )
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name="fut-user-list",
                status=200,
                bytes=len(payload),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and (
                path_without_query == "/ut/game/fifa14/season"
                or path_without_query.startswith("/ut/game/fifa14/season/")
            )
        ):
            if identity_store is not None and hasattr(identity_store, "offline_seasons_list"):
                if path_without_query == "/ut/game/fifa14/season/user":
                    response = identity_store.offline_season_user()
                    response_name = "fut-offline-season-user-beta2"
                else:
                    response = identity_store.offline_seasons_list()
                    response_name = "fut-offline-seasons-beta2"
            else:
                response = {"seasons": []}
                response_name = "fut-seasons-empty"
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name=response_name,
                status=200,
                bytes=len(payload),
                response_document=response,
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and (
                path_without_query == "/ut/game/fifa14/tournament"
                or path_without_query.startswith("/ut/game/fifa14/tournament/")
            )
        ):
            if identity_store is not None and hasattr(identity_store, "offline_tournaments_list"):
                tournament_mode = (
                    identity_store.tournament_wire_mode()
                    if hasattr(identity_store, "tournament_wire_mode")
                    else "legacy"
                )
                if path_without_query == "/ut/game/fifa14/tournament/teams":
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                    try:
                        requested_count = int((query.get("count") or ["15"])[-1])
                    except (TypeError, ValueError):
                        requested_count = 15
                    if hasattr(identity_store, "offline_tournament_teams"):
                        response = identity_store.offline_tournament_teams(requested_count)
                    else:
                        response = {"teamId": []}
                    response_name = "fut-offline-tournament-teams-beta28-native"
                elif path_without_query == "/ut/game/fifa14/tournament/user/list":
                    response = identity_store.offline_tournament_user_list()
                    response_name = f"fut-offline-tournament-user-beta28-{tournament_mode}"
                elif re.fullmatch(r"/ut/game/fifa14/tournament/user/\d+", path_without_query, re.IGNORECASE):
                    tournament_id = int(path_without_query.rsplit("/", 1)[-1])
                    request_document: dict[str, Any] = {}
                    if body:
                        try:
                            parsed = json.loads(body.decode("utf-8"))
                            if isinstance(parsed, dict):
                                request_document = parsed
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            request_document = {}
                    if effective_method in {"PUT", "POST"} and hasattr(identity_store, "update_offline_tournament_user"):
                        response = identity_store.update_offline_tournament_user(tournament_id, request_document)
                        response_name = "fut-offline-tournament-user-update-beta28"
                    elif hasattr(identity_store, "offline_tournament_user"):
                        response = identity_store.offline_tournament_user(tournament_id)
                        response_name = "fut-offline-tournament-user-read-beta28"
                    else:
                        response = {"tournamentId": tournament_id}
                        response_name = "fut-offline-tournament-user-minimal-beta28"
                else:
                    response = identity_store.offline_tournaments_list()
                    response_name = f"fut-offline-tournaments-beta28-{tournament_mode}"
            else:
                response = {"tournament": []}
                response_name = "fut-tournaments-empty"
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name=response_name,
                status=200,
                bytes=len(payload),
                response_document=response,
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/friendlyseason/user"
        ):
            payload = build_fut_json_payload({"userInfo": []})
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name="fut-friendly-seasons-empty",
                status=200,
                bytes=len(payload),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/match/end"
        ):
            request_document = {}
            if body:
                try:
                    parsed = json.loads(body.decode("utf-8"))
                    if isinstance(parsed, dict):
                        request_document = parsed
                except (UnicodeDecodeError, json.JSONDecodeError):
                    request_document = {}
            if identity_store is not None and hasattr(identity_store, "settle_match_end"):
                response = identity_store.settle_match_end(request_document)
                response_name = "fut-destroy-match-beta222-native"
            else:
                response = {
                    "endReason": str(request_document.get("endReason") or "NO_CONTEST").upper(),
                    "secondsPlayed": 0,
                    "matchDifficulty": 0,
                    "items": [],
                    "matchData": str(request_document.get("matchData") or ""),
                }
                response_name = "fut-destroy-match-local-empty"
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            record_after = None
            if identity_store is not None and hasattr(identity_store, "match_record"):
                try:
                    record_after = identity_store.match_record()
                except Exception:
                    record_after = None
            emit(
                "fut-match-end-beta222",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                end_reason=str(request_document.get("endReason") or ""),
                request_document=request_document,
                response_document=response,
                record_after=record_after,
                returned_match_stat_items=[
                    {"id": row.get("id"), "contract": row.get("contract"), "fitness": row.get("fitness")}
                    for row in response.get("items", []) if isinstance(response, dict) and isinstance(row, dict)
                ],
            )
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name=response_name,
                status=200,
                bytes=len(payload),
                response_document=response,
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/ut/game/fifa14/match"
        ):
            request_document = {}
            if body:
                try:
                    parsed = json.loads(body.decode("utf-8"))
                    if isinstance(parsed, dict):
                        request_document = parsed
                except (UnicodeDecodeError, json.JSONDecodeError):
                    request_document = {}
            result_markers = {
                "minutesPlayed", "minutes", "matchMinutes", "goals", "goalsScored",
                "goalsFor", "homeGoals", "goalsAgainst", "goalsConceded", "awayGoals",
                "result", "completed", "matchCompleted", "finished", "dnf", "didNotFinish",
                "quit", "abandoned", "shotsOnTarget", "passAccuracy", "possession", "successfulTackles",
            }
            is_create_match = (
                effective_method in {"POST", "PUT"}
                and "squadId" in request_document
                and not any(marker in request_document for marker in result_markers)
            )
            is_match_ready = (
                effective_method == "PUT"
                and isinstance(request_document.get("items"), list)
                and "squadId" not in request_document
                and not any(marker in request_document for marker in result_markers)
            )
            if identity_store is not None and hasattr(identity_store, "create_match") and is_create_match:
                response = identity_store.create_match(request_document)
                response_name = "fut-create-match-beta222-native"
            elif identity_store is not None and hasattr(identity_store, "match_ready") and is_match_ready:
                response = identity_store.match_ready(request_document)
                response_name = "fut-match-ready-beta222-native"
            elif identity_store is not None and hasattr(identity_store, "settle_match") and effective_method in {"POST", "PUT"}:
                response = identity_store.settle_match(request_document)
                response_name = "fut-match-beta-settled"
            elif identity_store is not None and hasattr(identity_store, "reset_match") and effective_method == "GET":
                response = {"match": None, "credits": identity_store.credits().get("credits", 0)}
                response_name = "fut-match-beta-status"
            else:
                response = {}
                response_name = "fut-match-local-ack"
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name=response_name,
                status=200,
                bytes=len(payload),
                response_document=response,
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query
            in {
                "/ut/game/fifa14",
                "/ut/game/fifa14/",
                "/utStats",
                "/ut/delete/auth",
            }
        ):
            payload = build_fut_json_payload({})
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name="fut-known-empty-ack",
                status=200,
                bytes=len(payload),
            )
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/local/beta/metrics"
            and identity_store is not None
            and hasattr(identity_store, "metrics")
        ):
            response = identity_store.metrics()
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name="beta-metrics", status=200, bytes=len(payload), response_document=response)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/local/beta/profile"
            and identity_store is not None
            and hasattr(identity_store, "beta_profile_summary")
        ):
            response = identity_store.beta_profile_summary()
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name="beta-profile", status=200, bytes=len(payload), response_document=response)
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/local/beta/wallet"
            and identity_store is not None
            and hasattr(identity_store, "wallet_ledger")
        ):
            response = identity_store.wallet_ledger()
            payload = build_fut_json_payload(response)
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit("fut-http-response", method=self.command, effective_method=effective_method, path=self.path, response_name="beta-wallet-ledger", status=200, bytes=len(payload))
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/local/managers"
            and identity_store is not None
        ):
            payload = build_fut_json_payload(identity_store.manager_reference_catalog())
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/local/identity"
            and identity_store is not None
        ):
            payload = build_fut_json_payload(identity_store.snapshot())
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query == "/local/onboarding/club"
            and self.command == "POST"
            and identity_store is not None
        ):
            try:
                request = json.loads(body.decode("utf-8"))
                club = identity_store.create_club(
                    request.get("clubName", ""),
                    request.get("clubAbbr", ""),
                    request.get("badgeId", 1),
                    request.get("teamId", 1),
                )
                payload = build_fut_json_payload({"club": club})
                self.send_response(200)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                payload = build_fut_json_payload(
                    {"code": "400", "reason": str(error)}
                )
                self.send_response(400)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
        elif self.path == "/health":
            payload = b'{"ok":true}\n'
            self.send_response(200)
            self.send_header("content-type", "application/json")
        elif (
            getattr(self.server, "probe_name", "http") == "fut-http"
            and path_without_query.startswith("/ut/")
        ):
            # Keep newly reached retail routes on localhost and make them
            # visible in the trace.  Explicit schemas above always take
            # precedence; this acknowledgement is intentionally not treated
            # as proof that an unknown parser contract has been implemented.
            payload = build_fut_json_payload({})
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            emit(
                "fut-http-response",
                method=self.command,
                effective_method=effective_method,
                path=self.path,
                response_name="unmapped-fut-route-local-ack",
                status=200,
                bytes=len(payload),
            )
        else:
            payload = b'{"error":"research probe only"}\n'
            self.send_response(501)
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle

    def log_message(self, *_args) -> None:
        return


class GoscaProbe(BaseHTTPRequestHandler):
    server_version = "FIFA14GoscaProbe/0.1"

    def _handle(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(min(length, 1_048_576)) if length else b""
        reply_mode = getattr(self.server, "reply_mode", "xml")
        emit(
            "gosca-probe",
            peer=self.client_address,
            method=self.command,
            path=self.path,
            headers=dict(self.headers.items()),
            body_text=body[:4096].decode("utf-8", "replace"),
            body_hex=body[:512].hex(),
            truncated=len(body) > 4096,
            reply_mode=reply_mode,
        )
        if reply_mode == "unavailable":
            self.send_response(503)
            self.send_header("content-length", "0")
            self.send_header("connection", "close")
            self.end_headers()
            return

        ca_pem = Path(self.server.ca_cert_file).read_bytes()
        ca_b64 = base64.b64encode(ca_pem).decode("ascii")
        payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<cacertificate>
  <certificatelist>
    <certificatelist>
      <name>FIFA14 Local Research CA</name>
      <host>gosredirector.ea.com</host>
      <bits>{ca_b64}</bits>
      <cert>{ca_b64}</cert>
      <base64>{ca_b64}</base64>
    </certificatelist>
  </certificatelist>
</cacertificate>
""".encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "text/xml; charset=utf-8")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _handle
    do_POST = _handle

    def log_message(self, *_args) -> None:
        return


class ReuseThreadingTCPServer(socketserver.ThreadingTCPServer):
    # Kept under the historical class name to minimise churn, but V2.37 must
    # own its protocol ports exclusively so an older launcher cannot answer a
    # subset of FIFA's requests.
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        return super().server_bind()


def certificate_hash(name: str):
    if name == "sha1":
        return hashes.SHA1()
    if name == "sha256":
        return hashes.SHA256()
    raise ValueError(f"Unsupported certificate hash {name!r}")


def find_openssl() -> Path:
    candidates = [shutil.which("openssl")]
    for base in (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs") if os.environ.get("LOCALAPPDATA") else None,
    ):
        if not base:
            continue
        candidates.extend([
            str(Path(base) / "Git" / "mingw64" / "bin" / "openssl.exe"),
            str(Path(base) / "Git" / "usr" / "bin" / "openssl.exe"),
        ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise RuntimeError(
        "FIFA 14 old-ProtoSSL certificate generation needs OpenSSL. "
        "Run INSTALL_PREREQUISITES.cmd from the build folder; it installs "
        "Git for Windows/OpenSSL automatically."
    )


def run_openssl(openssl: Path, args: list[str], directory: Path) -> None:
    environment = os.environ.copy()
    compatibility_config = directory / "openssl-old-protossl.cnf"
    if compatibility_config.exists():
        environment["OPENSSL_CONF"] = str(compatibility_config)
    result = subprocess.run(
        [str(openssl), *args],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"OpenSSL failed ({result.returncode}): {detail}")


def create_old_protossl_cert_files(hostname: str, directory: Path, force: bool = False) -> tuple[Path, Path, Path]:
    """Create the legacy malformed certificate accepted by old EA ProtoSSL.

    The public Bug_OldProtoSSL research shows that changing only the outer
    certificate signatureAlgorithm OID from md5WithRSAEncryption to
    rsaEncryption makes affected ProtoSSL builds compare a zero-length digest.
    This is restricted to the localhost redirector used by this research tool.
    """
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = hostname.replace("*", "star").replace(".", "_")
    cert_path = directory / f"{safe_name}.old-protossl.crt"
    key_path = directory / f"{safe_name}.old-protossl.key"
    ca_path = directory / "old-protossl-otg3-ca.pem"
    ca_key_path = directory / "old-protossl-otg3-ca.key"
    csr_path = directory / f"{safe_name}.old-protossl.csr"
    der_path = directory / f"{safe_name}.old-protossl.der"
    modified_der_path = directory / f"{safe_name}.old-protossl.modified.der"
    extensions_path = directory / f"{safe_name}.old-protossl.extensions.cnf"
    openssl = find_openssl()
    # Zamboni's legacy server guide enables OpenSSL's legacy provider and
    # SECLEVEL=0 for EA-era TLS. Keep the same compatibility isolated to this
    # temporary/local certificate directory.
    (directory / "openssl-old-protossl.cnf").write_text(
        "openssl_conf = openssl_init\n"
        "[openssl_init]\nproviders = provider_sect\nalg_section = algorithm_sect\nssl_conf = ssl_sect\n"
        "[provider_sect]\ndefault = default_sect\nlegacy = legacy_sect\n"
        "[default_sect]\nactivate = 1\n[legacy_sect]\nactivate = 1\n"
        "[algorithm_sect]\n[ssl_sect]\nsystem_default = system_default_sect\n"
        "[system_default_sect]\nCipherString = DEFAULT:@SECLEVEL=0\nMinProtocol = TLSv1\n",
        encoding="ascii",
    )

    if not force and cert_path.exists() and key_path.exists() and ca_path.exists():
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.set_ciphers("ALL:@SECLEVEL=0")
            context.load_cert_chain(certfile=cert_path, keyfile=key_path)
            return cert_path, key_path, ca_path
        except (OSError, ssl.SSLError):
            pass

    extensions_path.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        f"subjectAltName=DNS:{hostname},DNS:localhost,IP:127.0.0.1\n",
        encoding="ascii",
    )
    issuer = "/OU=Online Technology Group/O=Electronic Arts, Inc./L=Redwood City/ST=California/C=US/CN=OTG3 Certificate Authority"
    subject = f"/CN={hostname}/OU=Global Online Studio/O=Electronic Arts, Inc./ST=California/C=US"
    run_openssl(openssl, [
        "req", "-x509", "-newkey", "rsa:1024", "-md5", "-nodes",
        "-days", "3650", "-subj", issuer,
        "-keyout", str(ca_key_path), "-out", str(ca_path),
    ], directory)
    run_openssl(openssl, [
        "req", "-new", "-newkey", "rsa:1024", "-nodes", "-subj", subject,
        "-keyout", str(key_path), "-out", str(csr_path),
    ], directory)
    run_openssl(openssl, [
        "x509", "-req", "-in", str(csr_path), "-CA", str(ca_path),
        "-CAkey", str(ca_key_path), "-set_serial", "1", "-days", "3650",
        "-md5", "-extfile", str(extensions_path), "-out", str(cert_path),
    ], directory)
    run_openssl(openssl, [
        "x509", "-outform", "der", "-in", str(cert_path), "-out", str(der_path),
    ], directory)

    encoded = bytearray(der_path.read_bytes())
    md5_rsa_oid = bytes.fromhex("2a864886f70d010104")
    occurrences = []
    start = 0
    while True:
        index = encoded.find(md5_rsa_oid, start)
        if index < 0:
            break
        occurrences.append(index)
        start = index + 1
    if len(occurrences) < 2:
        raise RuntimeError(f"OldProtoSSL certificate patch expected two MD5-RSA OIDs, found {len(occurrences)}")
    # Preserve the inner TBSCertificate algorithm and alter only the outer
    # signatureAlgorithm from 1.2.840.113549.1.1.4 to rsaEncryption .1.
    encoded[occurrences[1] + len(md5_rsa_oid) - 1] = 0x01
    modified_der_path.write_bytes(encoded)
    run_openssl(openssl, [
        "x509", "-inform", "der", "-in", str(modified_der_path),
        "-out", str(cert_path),
    ], directory)
    return cert_path, key_path, ca_path


def create_sha1_cert_files(hostname: str, directory: Path, force: bool = False) -> tuple[Path, Path, Path]:
    """Create a legacy SHA-1 RSA chain for FIFA 14's embedded ProtoSSL.

    Modern cryptography releases intentionally refuse to sign SHA-1
    certificates, so this compatibility-only path uses the OpenSSL shipped
    with Git for Windows. The certificates are limited to this local probe.
    """
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = hostname.replace("*", "star").replace(".", "_")
    cert_path = directory / f"{safe_name}.crt"
    key_path = directory / f"{safe_name}.key"
    ca_path = directory / "ca.pem"
    ca_key_path = directory / "ca.key"

    openssl = find_openssl()
    csr_path = directory / f"{safe_name}.csr"
    extensions_path = directory / f"{safe_name}.extensions.cnf"
    extensions_path.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        f"subjectAltName=DNS:{hostname},DNS:localhost,IP:127.0.0.1\n",
        encoding="ascii",
    )

    ca_is_valid = False
    if not force and ca_path.exists() and ca_key_path.exists():
        try:
            ca_cert = x509.load_pem_x509_certificate(ca_path.read_bytes())
            ca_is_valid = ca_cert.signature_hash_algorithm.name == "sha1"
        except (OSError, ValueError):
            ca_is_valid = False

    if not ca_is_valid:
        run_openssl(
            openssl,
            [
                "req", "-x509", "-newkey", "rsa:2048", "-sha1", "-nodes",
                "-days", "365", "-subj", "/C=US/O=FIFA14 Local Research/CN=FIFA14 Local Research CA",
                "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign,digitalSignature",
                "-keyout", str(ca_key_path), "-out", str(ca_path),
            ],
            directory,
        )

    leaf_is_valid = False
    if ca_is_valid and cert_path.exists() and key_path.exists():
        try:
            server_cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            sans = server_cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            verify = subprocess.run(
                [str(openssl), "verify", "-CAfile", str(ca_path), str(cert_path)],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
            leaf_is_valid = (
                server_cert.signature_hash_algorithm.name == "sha1"
                and hostname in sans.get_values_for_type(x509.DNSName)
                and verify.returncode == 0
            )
        except (OSError, ValueError, x509.ExtensionNotFound):
            leaf_is_valid = False

    if leaf_is_valid:
        return cert_path, key_path, ca_path

    run_openssl(
        openssl,
        [
            "req", "-new", "-newkey", "rsa:2048", "-nodes",
            "-subj", f"/C=US/O=FIFA14 Local Research/CN={hostname}",
            "-keyout", str(key_path), "-out", str(csr_path),
        ],
        directory,
    )
    run_openssl(
        openssl,
        [
            "x509", "-req", "-in", str(csr_path),
            "-CA", str(ca_path), "-CAkey", str(ca_key_path),
            "-set_serial", f"0x{secrets.token_hex(16)}",
            "-days", "365", "-sha1", "-extfile", str(extensions_path),
            "-out", str(cert_path),
        ],
        directory,
    )
    return cert_path, key_path, ca_path


def create_ca_files(directory: Path, force: bool = False, hash_name: str = "sha256") -> tuple[Path, rsa.RSAPrivateKey, x509.Name]:
    directory.mkdir(parents=True, exist_ok=True)
    ca_path = directory / "ca.pem"
    ca_key_path = directory / "ca.key"
    if not force and ca_path.exists() and ca_key_path.exists():
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(ca_path.read_bytes())
        if not isinstance(ca_key, rsa.RSAPrivateKey):
            raise TypeError("Existing CA key is not RSA")
        if ca_cert.signature_hash_algorithm.name == hash_name:
            return ca_path, ca_key, ca_cert.subject
        force = True

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FIFA14 Local Research"),
            x509.NameAttribute(NameOID.COMMON_NAME, "FIFA14 Local Research CA"),
        ]
    )
    now = datetime.now(timezone.utc)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, certificate_hash(hash_name))
    )

    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    ca_key_path.write_bytes(
        ca_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return ca_path, ca_key, ca_name


def create_cert_files(hostname: str, directory: Path, force: bool = False, hash_name: str = "sha256") -> tuple[Path, Path, Path]:
    if hash_name == "old-protossl":
        return create_old_protossl_cert_files(hostname, directory, force=force)
    if hash_name == "sha1":
        return create_sha1_cert_files(hostname, directory, force=force)

    directory.mkdir(parents=True, exist_ok=True)
    safe_name = hostname.replace("*", "star").replace(".", "_")
    cert_path = directory / f"{safe_name}.crt"
    key_path = directory / f"{safe_name}.key"
    ca_path, ca_key, ca_name = create_ca_files(directory, force=force, hash_name=hash_name)
    if not force and cert_path.exists() and key_path.exists():
        existing = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if existing.signature_hash_algorithm.name == hash_name:
            return cert_path, key_path, ca_path
        force = True

    now = datetime.now(timezone.utc)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FIFA14 Local Research"),
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        ]
    )
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(hostname), x509.DNSName("localhost"), x509.IPAddress(IPv4Address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, certificate_hash(hash_name))
    )

    key_path.write_bytes(
        server_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, ca_path


def create_tls_context(hostname: str, cert_dir: str | None = None, hash_name: str = "sha256") -> tuple[ssl.SSLContext, tempfile.TemporaryDirectory | None, Path]:
    temp = None if cert_dir else tempfile.TemporaryDirectory(prefix="fifa14-redirector-cert-")
    directory = Path(cert_dir) if cert_dir else Path(temp.name)
    cert_path, key_path, ca_path = create_cert_files(hostname, directory, hash_name=hash_name)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers("AES256-SHA:AES128-SHA:DES-CBC3-SHA:@SECLEVEL=0")
    if hasattr(ssl, "OP_NO_TICKET"):
        ctx.options |= ssl.OP_NO_TICKET
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return ctx, temp, ca_path


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """HTTP listener that must own its port exclusively.

    Python's HTTPServer enables SO_REUSEADDR. On Windows that can allow an old
    FIFA-local Python server from a previous extracted build to remain bound to
    8080/8099 while a new build also starts. The game can then hit the stale
    process and receive an older ItemData contract. V2.37 deliberately disables
    address reuse and requests SO_EXCLUSIVEADDRUSE when Windows exposes it.
    """
    allow_reuse_address = False

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        return super().server_bind()


class TlsThreadingHTTPServer(ExclusiveThreadingHTTPServer):
    def __init__(self, server_address, request_handler_class, ssl_context):
        super().__init__(server_address, request_handler_class)
        self.socket = ssl_context.wrap_socket(self.socket, server_side=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local FIFA 14 protocol observation probes")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--instance-token", default="", help="Per-launch ownership token used by the PowerShell launcher")
    parser.add_argument("--blaze-port", type=int, default=42127)
    parser.add_argument("--main-blaze-port", type=int, default=42128)
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument(
        "--fut-http-port",
        type=int,
        default=8099,
        help="Loopback capture port for CardsDLL's historical FUT HTTP service.",
    )
    parser.add_argument(
        "--dynamic-http-port",
        type=int,
        default=0,
        help=(
            "Optional loopback fallback listener for the retail hard-coded "
            "FUT dynamic-messages service (observed on TCP 8306)."
        ),
    )
    parser.add_argument(
        "--fut-account-mode",
        choices=("new", "existing"),
        default="new",
        help="Return a first-use persona or a pre-created local test club.",
    )
    parser.add_argument(
        "--identity-db",
        default="",
        help="Persistent local-only identity database (default: artifacts/local-fut.sqlite3).",
    )
    parser.add_argument(
        "--beta-mode",
        action="store_true",
        help="Enable v2.41 progression BETA: fresh bronze starter club, wallet ledger, match settlement and metrics.",
    )
    parser.add_argument(
        "--enable-fut-direct-boot-config",
        action="store_true",
        help="A/B test FIFA's shipped FUT direct/bootstrap configuration switches.",
    )
    parser.add_argument("--gosca-port", type=int, default=44125)
    parser.add_argument("--enable-gosca", action="store_true")
    parser.add_argument("--gosca-reply", choices=("xml", "unavailable"), default="xml")
    parser.add_argument(
        "--lsx-port",
        type=int,
        default=3216,
        help="Historical local Origin Core/LSX TCP port.",
    )
    parser.add_argument(
        "--enable-lsx-probe",
        action="store_true",
        help=(
            "Bind a capture-only localhost Origin LSX listener. Leave disabled "
            "when EA Desktop already owns the port."
        ),
    )
    parser.add_argument("--redirector-mode", choices=("tcp", "tls"), default="tcp")
    parser.add_argument("--redirector-reply", choices=("none", "local"), default="none")
    parser.add_argument("--cert-hostname", default="gosredirector.ea.com")
    parser.add_argument("--cert-dir", help="Directory for persistent local CA and redirector certificate files")
    parser.add_argument("--cert-hash", choices=("old-protossl", "sha1", "sha256"), default="sha256")
    parser.add_argument(
        "--origin-login-mode",
        choices=("success", "error", "error-once"),
        default="success",
        help="Controlled OriginLogin result used to distinguish callback routing from response decoding.",
    )
    parser.add_argument("--origin-login-error", type=lambda value: int(value, 0), default=0x000D)
    parser.add_argument(
        "--origin-first-login",
        action="store_true",
        help="Controlled experiment: set SessionInfo.FRST without changing the default login fixture.",
    )
    parser.add_argument(
        "--origin-login-delay-ms",
        type=int,
        default=100,
        help="Small compatibility delay so a loopback reply cannot outrun the legacy client's job registration.",
    )
    parser.add_argument(
        "--login-notification-delay-ms",
        type=int,
        default=1500,
        help="Delay UserAdded/UserUpdated until FIFA's asynchronous login controller has installed its local session slot.",
    )
    args = parser.parse_args()

    cert_temp = None
    redirector_handler = TcpProbe
    if args.redirector_mode == "tls":
        ssl_context, cert_temp, ca_path = create_tls_context(args.cert_hostname, args.cert_dir, args.cert_hash)
        redirector_handler = TlsTcpProbe

    redirector = ReuseThreadingTCPServer((args.host, args.blaze_port), redirector_handler)
    redirector.daemon_threads = True
    redirector.probe_name = "redirector"
    if args.redirector_mode == "tls":
        redirector.ssl_context = ssl_context
        redirector.redirector_reply = args.redirector_reply
        redirector.main_blaze_host = args.host
        redirector.main_blaze_port = args.main_blaze_port
        # ProtoSSL may retry the redirector after a session failure. A fresh
        # SSLContext per connection prevents legacy session-resumption state
        # from reusing the deliberately malformed compatibility certificate.
        if args.cert_dir:
            redirector.ssl_context_factory = lambda: create_tls_context(
                args.cert_hostname, args.cert_dir, args.cert_hash
            )[0]

    main_blaze = ReuseThreadingTCPServer((args.host, args.main_blaze_port), BlazeProbe)
    main_blaze.daemon_threads = True
    main_blaze.probe_name = "main-blaze"
    main_blaze.origin_login_mode = args.origin_login_mode
    main_blaze.origin_login_error = args.origin_login_error
    main_blaze.origin_first_login = args.origin_first_login
    main_blaze.origin_login_delay_ms = max(0, args.origin_login_delay_ms)
    main_blaze.login_notification_delay_ms = max(0, args.login_notification_delay_ms)
    main_blaze.enable_fut_direct_boot_config = args.enable_fut_direct_boot_config
    http = ExclusiveThreadingHTTPServer((args.host, args.http_port), HttpProbe)
    http.probe_name = "bootstrap-http"
    fut_http = ExclusiveThreadingHTTPServer((args.host, args.fut_http_port), HttpProbe)
    fut_http.probe_name = "fut-http"
    fut_http.instance_token = args.instance_token
    fut_http.fut_account_mode = args.fut_account_mode
    identity_db = (
        Path(args.identity_db).resolve()
        if args.identity_db
        else (SERVER_DIRECTORY.parent / "artifacts" / "local-fut.sqlite3")
    )
    identity_store = (
        BetaIdentityStore(str(identity_db), args.fut_account_mode)
        if args.beta_mode
        else LocalIdentityStore(identity_db, args.fut_account_mode)
    )
    fut_http.identity_store = identity_store
    main_blaze.identity_store = identity_store
    servers = [redirector, main_blaze, http, fut_http]
    dynamic_http = None
    if args.dynamic_http_port > 0:
        dynamic_http = ExclusiveThreadingHTTPServer(
            (args.host, args.dynamic_http_port),
            HttpProbe,
        )
        dynamic_http.probe_name = "dynamic-http"
        servers.append(dynamic_http)
    lsx = None
    if args.enable_lsx_probe:
        lsx = ReuseThreadingTCPServer((args.host, args.lsx_port), TcpProbe)
        lsx.daemon_threads = True
        lsx.probe_name = "origin-lsx"
        servers.append(lsx)
    gosca = None
    gosca_temp = None
    if args.enable_gosca:
        gosca_ctx, gosca_temp, gosca_ca_path = create_tls_context("gosca.ea.com", args.cert_dir, args.cert_hash)
        gosca = TlsThreadingHTTPServer((args.host, args.gosca_port), GoscaProbe, gosca_ctx)
        gosca.ca_cert_file = str(gosca_ca_path)
        gosca.reply_mode = args.gosca_reply
        servers.append(gosca)

    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    emit(
        "started",
        build_version="2.41.1-beta2.25.9",
        pid=os.getpid(),
        instance_token=args.instance_token,
        probe_source=str(Path(__file__).resolve()),
        identity_source=str((SERVER_DIRECTORY / "local_identity.py").resolve()),
        host=args.host,
        redirector_port=args.blaze_port,
        redirector_mode=args.redirector_mode,
        redirector_reply=args.redirector_reply,
        main_blaze_port=args.main_blaze_port,
        http_port=args.http_port,
        fut_http_port=args.fut_http_port,
        dynamic_http_port=(
            args.dynamic_http_port if args.dynamic_http_port > 0 else None
        ),
        dynamic_http_enabled=args.dynamic_http_port > 0,
        fut_account_mode=args.fut_account_mode,
        beta_mode=bool(args.beta_mode),
        local_account_profile=identity_store.profile_kind(),
        local_account_snapshot=identity_store.snapshot(),
        first_use_contract={
            "returningUser": 1 if identity_store.has_club() else 0,
            "userClubList": identity_store.account_info()["userAccountInfo"]["personas"][0]["userClubList"],
            "syntheticClubSeeded": identity_store.has_club(),
            "syntheticSquadSeeded": bool(identity_store.snapshot().get("squadPlayerCount")),
            "starterPackClaimed": identity_store.has_club(),
            "completedActions": [
                name for name, completed in identity_store.user_actions().items() if completed
            ],
        },
        identity_db=str(identity_db),
        lsx_port=args.lsx_port if args.enable_lsx_probe else None,
        lsx_enabled=args.enable_lsx_probe,
        gosca_port=args.gosca_port if args.enable_gosca else None,
        gosca_enabled=args.enable_gosca,
        gosca_reply=args.gosca_reply if args.enable_gosca else None,
        origin_login_mode=args.origin_login_mode,
        origin_first_login=args.origin_first_login,
        origin_login_delay_ms=max(0, args.origin_login_delay_ms),
        login_notification_delay_ms=max(0, args.login_notification_delay_ms),
        fut_direct_boot_config=args.enable_fut_direct_boot_config,
        easfc_component_enabled=True,
        easfc_component_id=EASFC_COMPONENT,
        easfc_commands=[1, 2, 3, 4],
        ca_cert_file=str(ca_path) if args.redirector_mode == "tls" else None,
        certificate_mode=args.cert_hash if args.redirector_mode == "tls" else None,
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        redirector.shutdown()
        main_blaze.shutdown()
        http.shutdown()
        fut_http.shutdown()
        if dynamic_http is not None:
            dynamic_http.shutdown()
        if lsx is not None:
            lsx.shutdown()
        if gosca is not None:
            gosca.shutdown()
        if cert_temp is not None:
            cert_temp.cleanup()
        if gosca_temp is not None:
            gosca_temp.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
