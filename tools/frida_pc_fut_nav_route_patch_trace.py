from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_FIFA_IMAGE_SIZE = 0x04B7A000
CA_FUNCTION_RVA = 0x00D99790
CA_FUNCTION_SIGNATURE = bytes.fromhex("55 8b ec 8b 45 0c 8b 4d 08 6a 01 50")
UPDATE_RVA = 0x00D9A0B0
UPDATE_SIGNATURE = bytes.fromhex("55 8b ec 53 56 57 8b 7d 08 83 bf 20")
SCREEN_EVENT_DISPATCHER_RVA = 0x0010ECA0
SCREEN_EVENT_DISPATCHER_SIGNATURE = bytes.fromhex("55 8b ec 81 ec 14 02 00 00 56 57 8d")
NAV_TARGETS = (
    ("NAV::loadView", 0x006E1A40, bytes.fromhex("55 8b ec e8 98 17 a5 00 8b 10 8b c8")),
    ("NAV::unloadView", 0x006E1AA0, bytes.fromhex("55 8b ec e8 38 17 a5 00 8b 10 8b c8")),
    ("NAV::sendScreenEvent", 0x006B54A0, bytes.fromhex("55 8b ec 8b 45 10 85 c0 74 23 80 38")),
    ("NAV::sendAction", 0x006FDA50, bytes.fromhex("55 8b ec 51 53 56 57 c7 45 fc ff ff ff ff")),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _js_bytes(value: bytes) -> str:
    return ", ".join(f"0x{item:02x}" for item in value)


def build_agent(ca_bytes: bytes) -> str:
    encoded_ca = base64.b64encode(ca_bytes).decode("ascii")
    agent = r"""
'use strict';

const EXPECTED_FIFA_IMAGE_SIZE = 0x04b7a000;
// BETA 2.25.0: the functional FUT hooks are now separated from the old
// reverse-engineering telemetry.  Accurate backtraces/pointer probes on hot
// Store + ResetMatch paths were firing dozens of times per second and stalling
// the render thread while packs were being browsed.
const RUNTIME_PERFORMANCE_MODE = true;
const CA_FUNCTION_RVA = 0x00d99790;
const UPDATE_RVA = 0x00d9a0b0;
const SCREEN_EVENT_DISPATCHER_RVA = 0x0010eca0;
const CA_FUNCTION_SIGNATURE = [__CA_SIGNATURE__];
const UPDATE_SIGNATURE = [__UPDATE_SIGNATURE__];
const SCREEN_EVENT_DISPATCHER_SIGNATURE = [__DISPATCH_SIGNATURE__];
const FIFA_NAV_TARGETS = [
  {name:'NAV::loadView', rva:0x006e1a40, signature:[0x55,0x8b,0xec,0xe8,0x98,0x17,0xa5,0x00,0x8b,0x10,0x8b,0xc8]},
  {name:'NAV::unloadView', rva:0x006e1aa0, signature:[0x55,0x8b,0xec,0xe8,0x38,0x17,0xa5,0x00,0x8b,0x10,0x8b,0xc8]},
  {name:'NAV::sendScreenEvent', rva:0x006b54a0, signature:[0x55,0x8b,0xec,0x8b,0x45,0x10,0x85,0xc0,0x74,0x23,0x80,0x38]},
  {name:'NAV::sendAction', rva:0x006fda50, signature:[0x55,0x8b,0xec,0x51,0x53,0x56,0x57,0xc7,0x45,0xfc,0xff,0xff,0xff,0xff]}
];
const REMOTE_REDIRECTOR_PORT = 42127;
const LOCAL_REDIRECTOR_PORT = 42129;
const REDIRECT_PORTS = new Set([REMOTE_REDIRECTOR_PORT, 44125, 8099]);
const DYNAMIC_FALLBACK_HOST = 'pg.fifa13.test.easportsworld.ea.com';
const DYNAMIC_FALLBACK_PORT = 8306;
const TRUSTED_WINDOW_MS = 180000;
const WEBSESSION_GLOBAL_RVA = 0x001d7c7c;
const DYNAMIC_MESSAGES_GLOBAL_RVA = 0x001d7c80;
const LOCSTRINGS_OBJECT_OFFSET = 0x00002948;
const FIFA_STATIC_ASSET_BRIDGE_RVA = 0x00ebdce0;
const FIFA_STATIC_ASSET_BRIDGE_SIGNATURE = [0x55,0x8b,0xec,0x8b,0x55,0x10,0x56,0x52,0x8b,0x55,0x0c,0x8b,0xf1];
const FIFA_PLUGIN_LOADER_RVAS = [
  {name:'PluginLoaderSetEvent', rva:0x0010a450, signature:[0x55,0x8b,0xec,0x51,0x53,0x56,0x57,0x8b,0xf9]},
  {name:'PluginLoaderActionCreating', rva:0x0010a870, signature:[0x55,0x8b,0xec,0x56,0x8b,0x75,0x0c]},
  {name:'PluginLoaderActionStartUnload', rva:0x0010a8d0, signature:[0x55,0x8b,0xec,0x56,0x8b,0x75,0x0c]},
  {name:'PluginLoaderActionDestroying', rva:0x0010a920, signature:[0x55,0x8b,0xec,0x56,0x8b,0x75,0x0c]},
  {name:'PluginLoaderActionUnloadComplete', rva:0x0010a980, signature:[0x55,0x8b,0xec,0x56,0x8b,0x75,0x0c]},
  {name:'PluginLoaderLoadAndProbe', rva:0x0010a9c0, signature:[0x55,0x8b,0xec,0x81,0xec,0x0c,0x01,0x00,0x00,0x53,0x56,0x57]},
  {name:'PluginLoaderActionStartLoad', rva:0x0010abf0, signature:[0x55,0x8b,0xec,0x56,0x8b,0x75,0x0c]}
];

const FIFA_MODULE_WRAPPER_RVAS = [
  {name:'ModuleWrapperFactory', rva:0x00205360, signature:[0x55,0x8b,0xec,0x8b,0x45,0x0c,0x81,0xec,0x04,0x02,0x00,0x00]},
  {name:'ModuleWrapperAssign', rva:0x00205430, signature:[0x55,0x8b,0xec,0x53,0x8b,0xd9,0x56,0x8b,0x75,0x08]},
  {name:'ModuleWrapperDestructor', rva:0x00205490, signature:[0x56,0x8b,0xf1,0x57,0x8b,0xbe,0x20,0x01,0x00,0x00]},
  {name:'ModuleWrapperRelease', rva:0x002054f0, signature:[0x83,0xc1,0x04,0x8d,0x51,0x04,0x56,0x8b,0xf2]},
  {name:'ModuleWrapperDeletingDestructor', rva:0x00205520, signature:[0x55,0x8b,0xec,0x56,0x8b,0xf1,0x57,0x8b,0xbe,0x20,0x01,0x00,0x00]},
  {name:'ModuleWrapperHasPlugin', rva:0x00205310, signature:[0x83,0xb9,0x20,0x01,0x00,0x00,0x00,0x0f,0x95,0xc0]},
  {name:'ModuleWrapperGetState', rva:0x00205300, signature:[0x8b,0x81,0x00,0x01,0x00,0x00,0xc3]}
];
const SERVICE_API_SLOTS = [
  {name: 'StaffStats', slot: 0xc0, operation_index: 8},
  {name: 'SquadList', slot: 0xe0, operation_index: 16},
  {name: 'GetSettings', slot: 0xf8, operation_index: 22},
  {name: 'Authentication', slot: 0xfc, operation_index: 23},
  {name: 'Login', slot: 0x100, operation_index: 24},
  {name: 'Logout', slot: 0x104, operation_index: 25},
  {name: 'ResetUser', slot: 0x108, operation_index: 26},
  {name: 'CreateUser', slot: 0x110, operation_index: 28},
  {name: 'GetUserInfo', slot: 0x114, operation_index: 29},
  {name: 'SetUserInfo', slot: 0x118, operation_index: 30},
  {name: 'GetHistorical', slot: 0x11c, operation_index: 31},
  {name: 'GetTutData', slot: 0x120, operation_index: 32},
  {name: 'GetPileData', slot: 0x138, operation_index: 38},
  {name: 'GetUserData', slot: 0x140, operation_index: 40},
  {name: 'ViewCards', slot: 0x144, operation_index: 41},
  {name: 'ActivateCard', slot: 0x154, operation_index: 45},
  {name: 'CreateMatch', slot: 0x170, operation_index: 52},
  {name: 'MatchReady', slot: 0x174, operation_index: 53},
  {name: 'DestroyMatch', slot: 0x178, operation_index: 54},
  {name: 'PlayGame', slot: 0x17c, operation_index: 55},
  {name: 'ResetMatch', slot: 0x180, operation_index: 56},
  {name: 'UpdateTournament', slot: 0x190, operation_index: 60},
  {name: 'PurchasedItems', slot: 0x1c4, operation_index: 73},
  {name: 'GetPhishingQuestion', slot: 0x204, operation_index: 89},
  {name: 'GetValidatePhishingAnswer', slot: 0x20c, operation_index: 91},
  {name: 'GetTrustedConsoleList', slot: 0x210, operation_index: 92},
  {name: 'UpdateUserAction', slot: 0x214, operation_index: 93},
  {name: 'GetUserAction', slot: 0x218, operation_index: 94},
  {name: 'GetManagerQuest', slot: 0x21c, operation_index: 95},
  {name: 'SetManagerQuest', slot: 0x220, operation_index: 96},
  {name: 'GetHubData', slot: 0x224, operation_index: 97},
  {name: 'LoadFUTLocalizationAsset', slot: 0x94, operation_index: null},
  {name: 'GetFUTLocalizationAssetState', slot: 0x98, operation_index: null}
];
const CARDS_EXPECTED_IMAGE_SIZE = 0x00202000;
// BETA 2.22 keeps the native offline-match venue bridge. BETA 2.10 proved that the
// provider global is still null at LoginToFUT and that a one-shot hook is too
// early. Poll the exact provider global for the bounded match-preparation
// window, then hook its +0x78/+0x7c methods as soon as the host creates it.
const GET_STADIUM_ID_WRAPPER_RVA = 0x0007db70;
const SET_ONLINE_STADIUM_ID_WRAPPER_RVA = 0x0007db90;
const STADIUM_PROVIDER_GLOBAL_RVA = 0x001d70b0;
const SCRIPT_INT_RETURN_RVA = 0x0001ab00;
const LOCAL_OFFLINE_STADIUM_ID = 34;
// BETA 2.22: BETA 2.15 captured the exact native Make Active crash at
// CardsDLL+0x1366ed. That instruction dereferences the first entry of an empty
// ViewCards id vector. Trace the retail wrapper/path builders and repair only
// that impossible empty request with the most recent ActivateCard id. For the
// focused home-kit test, retain the current club's known home item id as a
// bounded fallback so the retail path can continue far enough to reveal the
// next real contract instead of crashing before HTTP.
const VIEW_CARDS_WRAPPER_RVA = 0x00053b50;
const VIEW_CARDS_PATH_BUILDER_RVA = 0x001366a0;
const ACTIVATE_CARD_PATH_BUILDER_RVA = 0x00147620;
const BETA216_HOME_KIT_ITEM_ID_LOW = 0x79154c8b;
const BETA216_HOME_KIT_ITEM_ID_HIGH = 0x0000002c;
// BETA 2.22: tournament session transition diagnostics.  The BETA 2.16
// capture accepted POST /match but then emitted only ResetMatch calls and
// unloaded FUT ~16 s later.  These are the exact retail frontend handlers
// registered beside CreateMatch/MatchReady plus the native two-field
// FutCreateMatchServerResponse parser.  Hooks are passive diagnostics only.
const CREATE_MATCH_FRONTEND_WRAPPER_RVA = 0x0008e550;
const MATCH_READY_FRONTEND_WRAPPER_RVA = 0x0008e580;
const SERVICE_CREATE_SESSION_WRAPPER_RVA = 0x0008e5b0;
const CREATE_MATCH_RESPONSE_PARSER_RVA = 0x0012c3b0;
// BETA 2.22: GetOfflineTournamentInfo().NAME is built from a separate static
// tournament-name object; the HTTP tournament-list parser does not consume a
// `name` member. This exact-build hook supplies local names only when the
// retail name pointer is null/empty, leaving any real non-empty metadata alone.
const TOURNAMENT_INFO_BUILDER_RVA = 0x000ced30;
// BETA 2.22: local-record synchronization bridge. The retail frontend object
// builder at +0x72e60 publishes WINS/LOSSES/DRAWS from CardsDLL's internal
// manager, which stayed at 0-0-0 even though the persistent local backend had
// recorded the DNF. These exact-build hooks preserve the native builder but
// replace the three values at their final PUSH sites with the current local DB
// record supplied over Frida RPC. Signature checks are relocation-safe.
const USER_RECORD_BUILDER_RVA = 0x00072e60;
const USER_RECORD_COPY_RVA = 0x00072c10;
const USER_RECORD_BUILDER_COPY_RETURN_RVA = 0x00072e8b;
// BETA 2.22: the old hooks targeted indirect `call edx` instructions, which
// Frida correctly refused to treat as function entries. Hook the enclosing
// retail builders instead, discover their UI-writer vtable, then intercept the
// real setter targets. This keeps the original publish path intact.
const MAIN_HUB_RECORD_WRAPPER_RVA = 0x00045ad0;
const BADGE_UI_WRAPPER_RVAS = [0x000573f0, 0x0006caa0, 0x00071d20];
// Actual registered ION action wrapper for GetOfflineTournamentInfo. BETA 2.22
// targeted an unrelated static-info builder; this wrapper is traced so the next
// capture exposes the live tournament metadata object used for NAME/art.
const GET_OFFLINE_TOURNAMENT_INFO_RVA = 0x00062af0;
const USER_RECORD_WINS_PUSH_RVA = 0x00072ed8;
const USER_RECORD_LOSSES_PUSH_RVA = 0x00072eef;
const USER_RECORD_DRAWS_PUSH_RVA = 0x00072f06;
const LOCAL_TOURNAMENT_NAMES = {
  1:'Starter Cup',
  2:'Bronze Cup',
  3:'Silver Cup',
  4:'Gold Cup'
};
let localRecordOverride = {wins:0, draws:0, losses:0};
let localRecordOverrideReady = false;
let localRecordText = Memory.allocUtf8String('0-0-0');
let localBadgeOverride = {assetId:0, resourceId:0};
let localBadgeOverrideReady = false;
const OP89_DESCRIPTOR_RVA = 0x001d3918;
const OP91_DESCRIPTOR_RVA = 0x001d3948;
const OP92_DESCRIPTOR_RVA = 0x001d3960;
// Exact FIFA 14 Authentication WebService/WebSession chain recovered from the
// uploaded CardsDLLzf.dll.  The request start routine allocates exactly 0x3E8
// (1000) bytes, builds the CAS JSON, and submits operation descriptor 23.
const AUTH_WEBSERVICE_VTABLE_RVA = 0x001a72b8;
const AUTH_WEBSESSION_VTABLE_RVA = 0x001aee80;
const AUTH_WEBSERVICE_CONSTRUCTOR_RVA = 0x00131620;
const AUTH_WEBSERVICE_INITIALIZE_RVA = 0x001316e0;
const AUTH_REQUEST_START_RVA = 0x0015f490;
const AUTH_JSON_BUILDER_RVA = 0x0015ef70;
const AUTH_REQUEST_SUBMIT_RVA = 0x0015ee20;
// Exact null gates inside the retail CAS JSON builder.  The builder aborts
// before serialization when the host credential provider returns no values.
const AUTH_EASW_SESSION_NULL_CHECK_RVA = 0x0015f2b4;
const AUTH_EASW_TOKEN_NULL_CHECK_RVA = 0x0015f2fa;
const AUTH_OPERATION_DESCRIPTOR_RVA = 0x001d32e8;
// Exact desktop FIFA 14 Authentication response parser chain. The mapper at
// +0x17EB00 recognizes only sid, serverTime, and lastOnlineTime.
const AUTH_RESPONSE_CONSTRUCTOR_RVA = 0x0017e830;
const AUTH_RESPONSE_PARSER_RVA = 0x0017e910;
const AUTH_RESPONSE_SCALAR_CALLBACK_RVA = 0x0017e9a0;
const AUTH_RESPONSE_KEY_MAPPER_RVA = 0x0017eb00;
const AUTH_WRAPPER_SCAN_LIMIT = 0x00020000;
const PHISHING_QUESTION_ENDPOINT_SUFFIX = '/question?deviceId=%s';
const PHISHING_VALIDATE_ENDPOINT_SUFFIX = '/validate?deviceId=%s&answer=%s';
const TRUSTED_ENDPOINT_SUFFIX = '/trusteddevice?deviceId=%s';

const CARDS_TARGETS = [
  {name: 'PlugInitialize_', rva: 0x00003720, sigOffset: 0, signature: [0x55,0x8b,0xec]},
  {name: 'PlugDeinitialize_', rva: 0x00003970, sigOffset: 0, signature: [0x56,0xe8,0xda,0xfd,0xff,0xff]},
  {name: 'GetPhishingQuestion launch', rva: 0x00052680, sigOffset: 0, signature: [0x55,0x8b,0xec,0x83,0xec,0x2c,0x56,0x8b,0xf1,0x8b,0x4d,0x08]},
  {name: 'RetrievePhishingQuestion callback', rva: 0x0004f1b0, sigOffset: 0, signature: [0x55,0x8b,0xec,0x83,0xec,0x10,0x53,0x56,0x57,0x8b,0x7d,0x08]},
  {name: 'Phishing-question response allocator', rva: 0x00132c00, sigOffset: 0, signature: [0x8b,0x49,0x04,0x8b,0x01,0x8b,0x50,0x08,0x56,0x6a,0x00,0x68]},
  {name: 'Phishing-question response parser', rva: 0x00132c50, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0xbc,0x00,0x00,0x00,0x56,0x57,0x6a,0x00]},
  {name: 'Phishing-question URL builder', rva: 0x00132ea0, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0x18,0x02,0x00,0x00,0x68,0xff,0x01,0x00,0x00]},
  {name: 'Operation 89 descriptor status setter', rva: 0x00130490, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8a,0x45,0x08]},
  {name: 'ValidatePhishingAnswer launch', rva: 0x00052860, sigOffset: 0, signature: [0x55,0x8b,0xec,0x83,0xec,0x44,0x53,0x56,0x57,0x8b,0xf1]},
  {name: 'ValidatePhishingAnswer callback', rva: 0x0004f2a0, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8b,0x45,0x08,0x83,0x78,0x18,0x00,0x56,0x8b,0xf1]},
  {name: 'Phishing-validation response allocator', rva: 0x00139a20, sigOffset: 0, signature: [0x8b,0x49,0x04,0x8b,0x01,0x8b,0x50,0x08,0x56,0x6a,0x00,0x68]},
  {name: 'Phishing-validation response parser', rva: 0x00139a50, sigOffset: 0, signature: [0xb0,0x01,0xc2,0x04,0x00]},
  {name: 'Phishing-validation URL builder', rva: 0x00139ba0, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0x30,0x01,0x00,0x00,0x56,0x68,0xff,0x00,0x00,0x00]},
  {name: 'Operation 91 descriptor status setter', rva: 0x001304b0, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8a,0x45,0x08]},
  {name: 'GetTrustedConsoleList launch', rva: 0x00052990, sigOffset: 0, signature: [0x55,0x8b,0xec,0x83,0xec,0x2c,0x56,0x8b,0xf1,0x8b,0x4d,0x08]},
  {name: 'GetTrustedConsoleList dispatch checkpoint', rva: 0x000529ff, sigOffset: 0, signature: [0xff,0xd2]},
  {name: 'RetrieveTrustedConsoleList callback', rva: 0x0004f310, sigOffset: 0, signature: [0x55,0x8b,0xec,0x83,0xec,0x08,0x53,0x8b,0x5d,0x08,0x8b,0x43,0x18]},
  {name: 'Trusted-console response allocator', rva: 0x00132fc0, sigOffset: 0, signature: [0x8b,0x49,0x04,0x8b,0x01,0x8b,0x50,0x08,0x53,0x56,0x33,0xdb]},
  {name: 'Trusted-console status handler', rva: 0x00133000, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8b,0x45,0x08,0x3d,0xf7,0x01,0x00,0x00]},
  {name: 'Trusted-console response parser', rva: 0x00133020, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0xbc,0x00,0x00,0x00,0x56,0x57,0x6a]},
  {name: 'Trusted-console URL builder', rva: 0x00133290, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0x18,0x02,0x00,0x00,0x68,0xff,0x01]},
  {name: 'Trusted-console formatted suffix checkpoint', rva: 0x001332dc, sigOffset: 0, signature: [0x8b,0x55,0x08,0x8d,0x8d,0xe8,0xfd,0xff,0xff]},
  {name: 'Device ID accessor', rva: 0x0013f210, sigOffset: 6, signature: [0x00,0x0f,0x85,0x1c,0x01,0x00,0x00,0x6a,0x10]},
  {name: 'JSON key mapper', rva: 0x00166a00, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8b,0x45,0x08,0x6a,0x00,0x68,0xc5,0x9d,0x1c,0x81]},
  {name: 'GetUserInfo response parser', rva: 0x00132440, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0xb8,0x00,0x00,0x00,0x56,0x57,0x6a,0x00,0x8b,0xf1]},
  {name: 'GetUserInfo user subparser', rva: 0x00143410, sigOffset: 0, signature: [0x55,0x8b,0xec,0x83,0xec,0x20,0x56,0x8b,0x75,0x0c,0x8b,0xce]},
  {name: 'FUT login stage handler', rva: 0x00084d50, sigOffset: 0, signature: [0x55,0x8b,0xec,0x83,0xec,0x0c,0x53,0x56,0x57,0x33,0xf6,0x8b,0xf9]},
  {name: 'FUT login stage helper A', rva: 0x000848c0, sigOffset: 0, signature: [0x55,0x8b,0xec,0xa1,0x68,0x73,0x1d,0x10,0x8b,0x55,0x08]},
  {name: 'FUT login stage helper B', rva: 0x00084910, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8b,0x45,0x08,0x39,0x05,0x60,0x73,0x1d,0x10]},
  {name: 'FUT login stage helper C', rva: 0x000850f0, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8b,0x45,0x0c,0x53,0x56,0x57,0x8b,0xf1]},
  {name: 'FUT_IcebreakerManager BuildSquad wrapper', rva: 0x00048690, sigOffset: 0, signature: [0x53,0x56,0x57,0xe8,0x48,0xd2,0x0c,0x00,0x8b,0x10,0x8b,0xc8,0x8b,0x42,0x18]},
  {name: 'FUT_IcebreakerManager ClearSquad wrapper', rva: 0x00048780, sigOffset: 0, signature: [0x53,0x56,0x57,0xe8,0x58,0xd1,0x0c,0x00,0x8b,0x10,0x8b,0xc8,0x8b,0x42,0x18]},
  {name: 'FUT_IcebreakerManager RetrievePack wrapper', rva: 0x00049690, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0x3c,0x04,0x00,0x00,0x53,0x56,0x57,0x6a,0x00]},
  {name: 'FUT_IcebreakerManager RetrievePackList wrapper', rva: 0x00049960, sigOffset: 0, signature: [0x55,0x8b,0xec,0x83,0xec,0x28,0x53,0x56,0x57,0x6a,0x00]},
  {name: 'Icebreaker pack-list path handler', rva: 0x00165d10, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8b,0x4d,0x08,0x68,0x20,0xf9,0x1a,0x10,0x68,0xf8,0xf8,0x1a,0x10]},
  {name: 'Icebreaker pack-entry parser', rva: 0x00165d30, sigOffset: 0, signature: [0x55,0x8b,0xec,0x83,0xec,0x1c,0x56,0x8b,0x75,0x0c,0x8b,0xce,0xc7,0x45,0xfc,0x66]},
  {name: 'Icebreaker pack-list response parser', rva: 0x00166360, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0x9c,0x02,0x00,0x00,0x56,0x6a,0x00,0x6a,0x00]},
  {name: 'Operation 92 descriptor status setter', rva: 0x001304c0, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8a,0x45,0x08]},
  {name: 'Authentication WebService constructor', rva: 0x00131620, sigOffset: 0, signature: [0x8b,0xc1,0x33,0xc9,0xc7,0x00,0xb8,0x72,0x1a,0x10]},
  {name: 'Authentication WebService initialize', rva: 0x001316e0, sigOffset: 0, signature: [0x55,0x8b,0xec,0x83,0xec,0x0c,0x56,0x57,0x8b,0xf1]},
  {name: 'Authentication request start', rva: 0x0015f490, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0xe8,0x03,0x00,0x00,0x53,0x56,0x6a,0x00]},
  {name: 'Authentication JSON builder', rva: 0x0015ef70, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0x6c,0x02,0x00,0x00,0x53,0x56,0x57]},
  {name: 'Authentication EASW-Session null check', rva: 0x0015f2b4, sigOffset: 0, signature: [0x85,0xf6,0x0f,0x84,0x62,0x01,0x00,0x00]},
  {name: 'Authentication EASW-Token null check', rva: 0x0015f2fa, sigOffset: 0, signature: [0x85,0xf6,0x0f,0x84,0xb3,0x00,0x00,0x00]},
  {name: 'Authentication request submit', rva: 0x0015ee20, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0x30,0x0b,0x00,0x00,0x53,0x56,0x57,0x6a,0x17]},
  {name: 'Authentication SID accessor', rva: 0x0015ee10, sigOffset: 0, signature: [0x8b,0x81,0xac,0x01,0x00,0x00,0xc3]},
  {name: 'Authentication response constructor', rva: AUTH_RESPONSE_CONSTRUCTOR_RVA, sigOffset: 0, signature: [0x56,0x68,0x66,0x65,0x64,0x6d]},
  {name: 'Authentication response parser', rva: AUTH_RESPONSE_PARSER_RVA, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0x00,0x04,0x00,0x00,0x56]},
  {name: 'Authentication response scalar callback', rva: AUTH_RESPONSE_SCALAR_CALLBACK_RVA, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8b,0x89,0x08,0x01,0x00,0x00]},
  {name: 'Authentication response key mapper', rva: AUTH_RESPONSE_KEY_MAPPER_RVA, sigOffset: 0, signature: [0x55,0x8b,0xec,0x56,0x8b,0x75,0x08,0xb9,0x70,0x2c,0x1b,0x10]},
  {name: 'Operation 23 descriptor status setter', rva: 0x00130070, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8a,0x45,0x08,0xa2,0xe9,0x32,0x1d,0x10]},
  {name: 'FUT static-asset state setter', rva: 0x0015ba80, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8b,0x45,0x08,0x89,0x41,0x38]},
  {name: 'FUT static-asset state getter', rva: 0x0015ba90, sigOffset: 0, signature: [0x8b,0x41,0x38,0xc3]},
  {name: 'FUT locstrings response parser', rva: 0x0015bc40, sigOffset: 0, signature: [0x55,0x8b,0xec,0x81,0xec,0x14,0x01,0x00,0x00,0x56,0x6a,0x00,0x6a,0x00]},
  {name: 'FUT locstrings path builder', rva: 0x0015bd20, sigOffset: 0, signature: [0x55,0x8b,0xec,0x83,0xec,0x58,0x56,0x8b,0xf1]},
  {name: 'FUT static-asset completion dispatcher', rva: 0x0016c270, sigOffset: 0, signature: [0x55,0x8b,0xec,0x8b,0x55,0x0c,0x56,0x8b,0xf1,0x8b,0x4d,0x10]}
];

const counters = Object.create(null);
const parserDepthByThread = Object.create(null);
let cardsHooksInstalled = false;
let cardsBase = null;
let stadiumScriptHooksInstalled = false;
let stadiumProviderTargetsHooked = Object.create(null);
let stadiumProviderPollStarted = false;
let stadiumProviderPollAttempts = 0;
let stadiumProviderLastSnapshotObject = null;
let matchBridgeTargetsHooked = Object.create(null);
let matchBridgePollStarted = false;
let activateCardTraceHooked = false;
let activateCardTracePollStarted = false;
let viewCardsTraceInstalled = false;
let lastActivateCardIdLow = 0;
let lastActivateCardIdHigh = 0;
let lastActivateCardIdTime = 0;
let beta222SyntheticIdBuffers = [];
let crashExceptionTraceInstalled = false;
let tournamentSessionTraceInstalled = false;
let tournamentNameFallbackInstalled = false;
let tournamentNameObjects = Object.create(null);
let userRecordTraceInstalled = false;
let mainHubRecordOverrideInstalled = false;
let badgeUiOverrideInstalled = false;
let uiWriterTargetsHooked = Object.create(null);
let lastUiWriterDiscoveryKey = null;
// BETA 2.25.0: the discovered writer vtable is shared by a huge amount of FUT
// UI.  Keeping its string/int setters globally "hot" means every pack card,
// label and animation crosses Frida even when we only care about MATCH_RECORD
// and BADGE_ID.  Restrict the expensive key decode/override to the exact retail
// wrapper call that requested those two fields.
let recordUiWriterActiveDepth = 0;
let badgeUiWriterActiveDepth = 0;
const recordUiWriterDepthByThread = Object.create(null);
const badgeUiWriterDepthByThread = Object.create(null);
let tournamentInfoActionTraceInstalled = false;
let trustedWindowActive = false;
let trustedWindowGeneration = 0;
let dispatchTargetsHooked = Object.create(null);
let controlledOverrideApplied = false;
let controlledQuestionOverrideApplied = false;
let controlledValidationOverrideApplied = false;
let dynamicFallbackDnsSeen = false;
let serviceApiTargetsHooked = Object.create(null);
let authSubobjectTargetsHooked = Object.create(null);
let firstLoginAttemptSeen = false;
let locstringsAccepted = false;
let authStartArmed = false;
let authStartCalled = false;
let authStartInProgress = false;
let authStartUpdateCountdown = 0;
let authCandidateAttempts = 0;
let authCandidate = null;
let authWrapperFromConstructor = null;
let authWrapperFromInitialize = null;
let authBuilderCalls = 0;
let authSubmitCalls = 0;
const authBuilderDepthByThread = Object.create(null);
let localEaswSessionPointer = null;
let localEaswTokenPointer = null;
let easwSessionFixups = 0;
let easwTokenFixups = 0;
const getUserInfoParserDepthByThread = Object.create(null);
const getUserInfoSubparserDepthByThread = Object.create(null);
const icebreakerPackListParserDepthByThread = Object.create(null);
const icebreakerPackEntryParserDepthByThread = Object.create(null);
let getUserInfoParserCompleted = false;
let getUserInfoParserSucceeded = false;
let phishingValidationSucceeded = false;
let fccLogin1Active = false;
let fccLogin2Active = false;
let futPackSelectActive = false;
let authenticatedIcebreakerSent = false;
let authenticatedIcebreakerNotBeforeMs = 0;
let competitionTraceUntilMs = 0;
let competitionTraceKind = '';
let competitionTraceGeneration = 0;
let competitionTraceLastHttpKey = '';
let competitionTraceLastHttpMs = 0;
let competitionOperationIndexes = Object.create(null);
let navSendActionFunction = null;
// BETA 2.23: an offline DestroyMatch can complete successfully while the retail
// ActionScript return handler still believes the in-game connection was lost.
// The uploaded BETA 2.22 trace proves this is a frontend-route decision: the
// original Blaze peer is still answering immediately before /match/end, then
// fcc_logout is loaded about 5.5 seconds after the HTTP 200. Arm a one-shot
// return guard from the *outgoing* DestroyMatch request so ordinary/manual FUT
// logout remains untouched.
let postMatchReturnGuardUntilMs = 0;
let postMatchReturnGuardGeneration = 0;
let postMatchReturnGuardLastArmMs = 0;
let postMatchFccLogoutRedirects = 0;
const POST_MATCH_RETURN_GUARD_MS = 45000;
const ACTIVE_MATCH_RETURN_GUARD_MS = 45 * 60 * 1000;
const postMatchStayInFutView = Memory.allocUtf8String('external/ion_fut/screens/gameHub/GameHub');
const authenticatedIcebreakerAction = Memory.allocUtf8String('iceBreaker');
const authenticatedIcebreakerEmptyParameter = Memory.allocUtf8String('');

function nowMs() { return Date.now(); }
function postMatchReturnGuardActive() { return postMatchReturnGuardUntilMs > nowMs(); }
function armPostMatchReturnGuard(source, durationMs) {
  const currentMs = nowMs();
  const windowMs = Math.max(1000, Number(durationMs || POST_MATCH_RETURN_GUARD_MS));
  // send and WSASend may both expose the same request. Do not create two guard
  // generations for one packet; simply keep/extend the existing window.
  if (postMatchReturnGuardActive() && (currentMs - postMatchReturnGuardLastArmMs) < 1000) {
    postMatchReturnGuardUntilMs = Math.max(postMatchReturnGuardUntilMs, currentMs + windowMs);
    return;
  }
  postMatchReturnGuardGeneration += 1;
  postMatchReturnGuardLastArmMs = currentMs;
  postMatchReturnGuardUntilMs = currentMs + windowMs;
  emit('fifa-postmatch-return-guard-armed-beta223', {
    generation:postMatchReturnGuardGeneration, source:String(source || 'socket'),
    window_ms:windowMs, until_ms:postMatchReturnGuardUntilMs, thread_id:tid()
  });
}
function competitionTraceActive() { return competitionTraceUntilMs > nowMs(); }
function armCompetitionTrace(kind, reason, durationMs) {
  competitionTraceGeneration += 1;
  competitionTraceKind = kind || 'competition';
  const windowMs = Math.max(1000, durationMs || 15000);
  competitionTraceUntilMs = nowMs() + windowMs;
  emit('cards-competition-trace-armed', {
    kind: competitionTraceKind, reason: reason, generation: competitionTraceGeneration,
    window_ms: windowMs, until_ms: competitionTraceUntilMs, thread_id: tid()
  });
}
function noteCompetitionHttpRequest(text, source) {
  if (text === null || text === undefined) return;
  const lowered = String(text).toLowerCase();
  let kind = '';
  if (lowered.indexOf('/ut/game/fifa14/match/end') >= 0) {
    kind = 'match-end';
    armPostMatchReturnGuard(source, POST_MATCH_RETURN_GUARD_MS);
  }
  else if (lowered.indexOf(' /ut/game/fifa14/season/list') >= 0) kind = 'season-list';
  else if (lowered.indexOf(' /ut/game/fifa14/season/user') >= 0) kind = 'season-user';
  else if (lowered.indexOf(' /ut/game/fifa14/tournament/list') >= 0) kind = 'tournament-list';
  else if (lowered.indexOf(' /ut/game/fifa14/tournament/teams') >= 0) kind = 'tournament-teams';
  else if (lowered.indexOf(' /ut/game/fifa14/tournament/user/list') >= 0) kind = 'tournament-user-list';
  else if (lowered.indexOf(' /ut/game/fifa14/tournament/user/') >= 0) kind = 'tournament-user-update';
  else if (lowered.indexOf(' /ut/game/fifa14/match') >= 0 && lowered.indexOf('/match/reset') < 0) {
    kind = 'match-create-or-result';
    // A completed PC match may submit DestroyMatch through a network path that
    // our send/WSASend hook does not observe after gameplay. Pre-arm the one-shot
    // return repair when CreateMatch is observed and keep it valid for a normal
    // match duration. This still only rewrites the first fcc_logout view.
    armPostMatchReturnGuard(String(source || 'socket') + ':active-match', ACTIVE_MATCH_RETURN_GUARD_MS);
  }
  if (!kind) return;
  const currentMs = nowMs();
  // send/WSASend can expose the same request through more than one layer. Do
  // not churn generations for a duplicate observation in the same second.
  if (competitionTraceLastHttpKey === kind && (currentMs - competitionTraceLastHttpMs) < 1000) return;
  competitionTraceLastHttpKey = kind;
  competitionTraceLastHttpMs = currentMs;
  armCompetitionTrace(kind, String(source || 'socket') + ' observed outgoing ' + kind + ' HTTP request', 15000);
}
function tid() { try { return Process.getCurrentThreadId(); } catch (_) { return null; } }
function safePtr(value) { try { return value === null ? null : value.toString(); } catch (_) { return null; } }
function listBytes(buffer) { return buffer === null ? null : Array.from(new Uint8Array(buffer)); }
function hexBytes(bytes) { return bytes === null ? null : bytes.map(v => v.toString(16).padStart(2, '0')).join(' '); }
function readU8(base, off) { try { return base.add(off).readU8(); } catch (_) { return null; } }
function readU16(base, off) { try { return base.add(off).readU16(); } catch (_) { return null; } }
function readU32(base, off) { try { return base.add(off).readU32(); } catch (_) { return null; } }
function readable(pointer) {
  try {
    if (pointer === null || pointer.isNull()) return null;
    const range = Process.findRangeByAddress(pointer);
    if (range === null || range.protection.indexOf('r') < 0) return null;
    return {base: range.base.toString(), size: range.size, protection: range.protection, file: range.file ? range.file.path : null};
  } catch (_) { return null; }
}
function raw(pointer, length) {
  try {
    if (pointer === null || pointer.isNull()) return null;
    const range = Process.findRangeByAddress(pointer);
    if (range === null || range.protection.indexOf('r') < 0) return null;
    const remaining = Number(range.base.add(range.size).sub(pointer));
    const count = Math.max(0, Math.min(length, remaining));
    return count > 0 ? listBytes(pointer.readByteArray(count)) : null;
  } catch (_) { return null; }
}
function cstring(pointer, maxLength) {
  try {
    if (pointer === null || pointer.isNull()) return null;
    const range = Process.findRangeByAddress(pointer);
    if (range === null || range.protection.indexOf('r') < 0) return null;
    const remaining = Number(range.base.add(range.size).sub(pointer));
    const limit = Math.max(0, Math.min(maxLength || 512, remaining));
    const bytes = [];
    for (let i = 0; i < limit; i++) {
      const value = pointer.add(i).readU8();
      if (value === 0) break;
      bytes.push(value);
    }
    if (bytes.length === 0) return '';
    let text = '';
    for (const value of bytes) {
      if (value >= 0x20 && value <= 0x7e) text += String.fromCharCode(value);
      else if (value === 9) text += '\\t';
      else if (value === 10) text += '\\n';
      else if (value === 13) text += '\\r';
      else text += '[0x' + value.toString(16).padStart(2, '0') + ']';
    }
    return text;
  } catch (_) { return null; }
}
function utf16(pointer, maxChars) {
  try {
    if (pointer === null || pointer.isNull()) return null;
    return pointer.readUtf16String(maxChars || 256);
  } catch (_) { return null; }
}
function pointerProbe(pointer, bytesLength) {
  const out = {pointer: safePtr(pointer), range: readable(pointer), cstring: cstring(pointer, 256)};
  out.bytes = hexBytes(raw(pointer, bytesLength || 64));
  return out;
}
function pointerFields(pointer, byteLength) {
  const out = [];
  if (pointer === null || pointer.isNull()) return out;
  for (let off = 0; off < byteLength; off += Process.pointerSize) {
    try {
      const candidate = pointer.add(off).readPointer();
      const range = readable(candidate);
      if (range === null) continue;
      const text = cstring(candidate, 256);
      if (text === null || text.length === 0) continue;
      out.push({offset: '0x' + off.toString(16), pointer: candidate.toString(), text: text, range: range});
    } catch (_) {}
  }
  return out;
}
function stackArgs(context, count) {
  const out = [];
  const sp = ptr(context.esp);
  for (let i = 0; i < count; i++) {
    let value = ptr(0);
    try { value = sp.add(4 + i * 4).readPointer(); } catch (_) {}
    out.push({index: i, value: safePtr(value), probe: pointerProbe(value, 48)});
  }
  return out;
}
function emit(kind, payload) {
  const count = (counters[kind] || 0) + 1;
  counters[kind] = count;
  if (count <= 1000) send({kind: kind, index: count, time_ms: nowMs(), ...payload});
  else if (count === 1001) send({kind: kind + '-suppressed', limit: 1000});
}
function verify(address, signature) {
  try {
    const actual = listBytes(address.readByteArray(signature.length));
    return {matched: actual.length === signature.length && actual.every((v, i) => v === signature[i]), actual: hexBytes(actual)};
  } catch (error) { return {matched: false, actual: null, error: String(error)}; }
}
function beginTrustedWindow(reason) {
  trustedWindowGeneration += 1;
  const generation = trustedWindowGeneration;
  trustedWindowActive = true;
  dynamicFallbackDnsSeen = false;
  emit('trusted-console-window-open', {reason: reason, generation: generation, thread_id: tid()});
  setTimeout(function () {
    if (trustedWindowActive && trustedWindowGeneration === generation) {
      trustedWindowActive = false;
      emit('trusted-console-window-timeout', {reason: reason, generation: generation});
    }
  }, TRUSTED_WINDOW_MS);
}
function endTrustedWindow(reason) {
  if (!trustedWindowActive) return;
  trustedWindowActive = false;
  dynamicFallbackDnsSeen = false;
  emit('trusted-console-window-close', {reason: reason, generation: trustedWindowGeneration, thread_id: tid()});
}
function normalizedBacktrace(context) {
  try {
    return Thread.backtrace(context, Backtracer.ACCURATE).slice(0, 32).map(function (address) {
      const module = Process.findModuleByAddress(address);
      return {
        address: address.toString(),
        module: module ? module.name : null,
        offset: module ? '0x' + address.sub(module.base).toString(16) : null
      };
    });
  } catch (error) {
    return [{error: String(error)}];
  }
}
function webSessionSnapshot(module, source) {
  const globalAddress = module.base.add(WEBSESSION_GLOBAL_RVA);
  let service = ptr(0), vtable = ptr(0);
  try { service = globalAddress.readPointer(); } catch (_) {}
  try { if (!service.isNull()) vtable = service.readPointer(); } catch (_) {}
  const slots = [];
  SERVICE_API_SLOTS.forEach(function(spec) {
    if (spec.operation_index === null) return;
    let target = ptr(0);
    try { if (!vtable.isNull()) target = vtable.add(spec.slot).readPointer(); } catch (_) {}
    slots.push({name:spec.name, operation_index:spec.operation_index, slot:'0x'+spec.slot.toString(16), target:target.toString(), range:readable(target)});
  });
  return {
    source: source,
    global_address: globalAddress.toString(),
    service: service.toString(),
    service_range: readable(service),
    service_bytes: hexBytes(raw(service, 0x300)),
    service_pointer_fields: pointerFields(service, 0x300),
    vtable: vtable.toString(),
    operation_slots: slots
  };
}
function hookDynamicDispatchTargets(module) {
  const snapshot = webSessionSnapshot(module, 'postauth-startup-dispatch-install');
  emit('cards-websession-snapshot', snapshot);
  const service = ptr(snapshot.service);
  const vtable = ptr(snapshot.vtable);
  if (service.isNull() || vtable.isNull()) return;
  SERVICE_API_SLOTS.forEach(function(spec) {
    if (spec.operation_index === null) return;
    let target = ptr(0);
    try { target = vtable.add(spec.slot).readPointer(); } catch (_) {}
    const targetInfo = {name:spec.name, operation_index:spec.operation_index, slot:'0x'+spec.slot.toString(16), target:target.toString(), target_range:readable(target), target_bytes:hexBytes(raw(target,24))};
    emit('cards-startup-operation-slot', targetInfo);
    if (!pointerInModule(module, target) || !executable(target)) return;
    const key = target.toString();
    if (dispatchTargetsHooked[key]) {
      emit('cards-startup-operation-target-alias', {...targetInfo, existing_key:key});
      return;
    }
    try {
      Interceptor.attach(target, {
        onEnter(args) {
          this.service = ptr(this.context.ecx);
          this.request = args[0];
          this.callback = args[1];
          this.callNumber = (counters['startup-operation-' + spec.operation_index] || 0) + 1;
          counters['startup-operation-' + spec.operation_index] = this.callNumber;
          emit('cards-startup-operation-enter', {
            name:spec.name, operation_index:spec.operation_index, slot:'0x'+spec.slot.toString(16), call_number:this.callNumber,
            thread_id:tid(), target:target.toString(), return_address:safePtr(this.returnAddress),
            service:pointerProbe(this.service,0x300), request_wrapper:pointerProbe(this.request,0x180), callback_wrapper:pointerProbe(this.callback,0x180),
            request_pointer_fields:pointerFields(this.request,0x180), callback_pointer_fields:pointerFields(this.callback,0x180),
            state:cardsServiceStateSnapshot(module,spec.name+' dispatch enter'), backtrace:normalizedBacktrace(this.context)
          });
        },
        onLeave(retval) {
          emit('cards-startup-operation-leave', {
            name:spec.name, operation_index:spec.operation_index, slot:'0x'+spec.slot.toString(16), call_number:this.callNumber,
            thread_id:tid(), target:target.toString(), retval:retval.toString(), retval_i32:retval.toInt32(),
            retval_probe:readable(retval) ? pointerProbe(retval,0x180) : null,
            state:cardsServiceStateSnapshot(module,spec.name+' dispatch leave')
          });
        }
      });
      dispatchTargetsHooked[key] = true;
      emit('cards-startup-operation-hook-ready', targetInfo);
    } catch (error) {
      emit('cards-startup-operation-hook-error', {...targetInfo, error:String(error)});
    }
  });
}


function pointerInModule(module, pointer) {
  try {
    if (pointer === null || pointer.isNull()) return false;
    return pointer.compare(module.base) >= 0 && pointer.compare(module.base.add(module.size)) < 0;
  } catch (_) { return false; }
}
function executable(pointer) {
  try {
    const range = Process.findRangeByAddress(pointer);
    return range !== null && range.protection.indexOf('x') >= 0;
  } catch (_) { return false; }
}
function cardsServiceStateSnapshot(module, source) {
  const globalAddress = module.base.add(WEBSESSION_GLOBAL_RVA);
  let service = ptr(0);
  try { service = globalAddress.readPointer(); } catch (_) {}
  if (service.isNull()) return {source: source, global_address: globalAddress.toString(), service: '0x0'};
  const locstrings = service.add(LOCSTRINGS_OBJECT_OFFSET);
  return {
    source: source,
    global_address: globalAddress.toString(),
    service: service.toString(),
    service_range: readable(service),
    service_head: hexBytes(raw(service, 0x80)),
    locstrings_object: locstrings.toString(),
    locstrings_state_38: readU32(locstrings, 0x38),
    locstrings_kind_3c: readU32(locstrings, 0x3c),
    locstrings_bytes: hexBytes(raw(locstrings, 0x60)),
    login_region_34e0: hexBytes(raw(service.add(0x34e0), 0x240)),
    login_region_pointer_fields: pointerFields(service.add(0x34e0), 0x240),
    auth_region_42e0: hexBytes(raw(service.add(0x42e0), 0x180)),
    auth_region_pointer_fields: pointerFields(service.add(0x42e0), 0x180)
  };
}
function hookAuthSubobjectMethods(module, objectPointer, source) {
  try {
    if (objectPointer === null || objectPointer.isNull() || readable(objectPointer) === null) return;
    const vtable = objectPointer.readPointer();
    if (!pointerInModule(module, vtable) || readable(vtable) === null) {
      emit('cards-auth-subobject-no-vtable', {
        source: source, object: pointerProbe(objectPointer, 0x100),
        candidate_vtable: safePtr(vtable), candidate_range: readable(vtable)
      });
      return;
    }
    const slots = [];
    for (let i = 0; i < 12; i++) {
      let target = ptr(0);
      try { target = vtable.add(i * Process.pointerSize).readPointer(); } catch (_) {}
      slots.push({slot_index: i, slot: '0x' + (i * Process.pointerSize).toString(16), target: target.toString(), range: readable(target)});
      if (!pointerInModule(module, target) || !executable(target)) continue;
      const key = target.toString();
      if (authSubobjectTargetsHooked[key]) continue;
      try {
        Interceptor.attach(target, {
          onEnter(args) {
            this.self = ptr(this.context.ecx);
            let runtimeVtable = ptr(0);
            try { runtimeVtable = this.self.readPointer(); } catch (_) {}
            this.relevant = runtimeVtable.equals(vtable);
            if (!this.relevant) return;
            emit('cards-auth-subobject-method-enter', {
              source: source, slot_index: i, target: target.toString(), thread_id: tid(),
              return_address: safePtr(this.returnAddress), self: pointerProbe(this.self, 0x100),
              args: stackArgs(this.context, 8), backtrace: normalizedBacktrace(this.context)
            });
          },
          onLeave(retval) {
            if (!this.relevant) return;
            emit('cards-auth-subobject-method-leave', {
              source: source, slot_index: i, target: target.toString(), thread_id: tid(),
              retval: retval.toString(), retval_i32: retval.toInt32(),
              retval_probe: pointerProbe(retval, 0x100)
            });
          }
        });
        authSubobjectTargetsHooked[key] = true;
      } catch (error) {
        emit('cards-auth-subobject-hook-error', {source: source, slot_index: i, target: target.toString(), error: String(error)});
      }
    }
    emit('cards-auth-subobject-vtable', {
      source: source, object: pointerProbe(objectPointer, 0x180),
      vtable: vtable.toString(), vtable_range: readable(vtable), slots: slots
    });
  } catch (error) {
    emit('cards-auth-subobject-inspection-error', {source: source, object: safePtr(objectPointer), error: String(error)});
  }
}
function hookOperationStatusSetters(module) {
  const operationIndexes = [8,16,22,23,24,25,26,28,29,30,31,32,38,40,56,73,89,91,92,93,94,95,96,97];
  operationIndexes.forEach(function (operationIndex) {
    const descriptor = module.base.add(0x001d30c0 + operationIndex * 0x18);
    let handler = ptr(0), namePointer = ptr(0), configPointer = ptr(0);
    try { handler = descriptor.add(4).readPointer(); } catch (_) {}
    try { namePointer = descriptor.add(8).readPointer(); } catch (_) {}
    try { configPointer = descriptor.add(0x10).readPointer(); } catch (_) {}
    const operationName = cstring(namePointer, 128);
    emit('cards-operation-descriptor-auth-range', {
      operation_index: operationIndex, operation_name: operationName,
      descriptor: descriptor.toString(), descriptor_bytes: hexBytes(raw(descriptor, 0x18)),
      handler: handler.toString(), category: readU32(descriptor, 0x0c), config_suffix: cstring(configPointer, 128)
    });
    if (!pointerInModule(module, handler) || !executable(handler)) return;
    const key = 'status-' + handler.toString();
    if (serviceApiTargetsHooked[key]) return;
    try {
      Interceptor.attach(handler, {
        onEnter(args) {
          this.before = hexBytes(raw(descriptor, 0x18));
          emit('cards-operation-status-set-enter', {
            operation_index: operationIndex, operation_name: operationName,
            value: args[0].toUInt32() & 0xff, descriptor_before: this.before,
            thread_id: tid(), return_address: safePtr(this.returnAddress),
            backtrace: normalizedBacktrace(this.context)
          });
        },
        onLeave(retval) {
          emit('cards-operation-status-set-leave', {
            operation_index: operationIndex, operation_name: operationName,
            descriptor_after: hexBytes(raw(descriptor, 0x18)), thread_id: tid()
          });
        }
      });
      serviceApiTargetsHooked[key] = true;
    } catch (error) {
      emit('cards-operation-status-hook-error', {operation_index: operationIndex, operation_name: operationName, handler: handler.toString(), error: String(error)});
    }
  });
}
function hookCompetitionOperationDescriptors(module, service, vtable) {
  if (service.isNull() || vtable.isNull()) return;
  for (let operationIndex = 0; operationIndex <= 120; operationIndex++) {
    const descriptor = module.base.add(0x001d30c0 + operationIndex * 0x18);
    let handler = ptr(0), namePointer = ptr(0), configPointer = ptr(0);
    try { handler = descriptor.add(4).readPointer(); } catch (_) {}
    try { namePointer = descriptor.add(8).readPointer(); } catch (_) {}
    try { configPointer = descriptor.add(0x10).readPointer(); } catch (_) {}
    const operationName = cstring(namePointer, 160) || '';
    const configSuffix = cstring(configPointer, 160) || '';
    const searchable = (operationName + ' ' + configSuffix).toLowerCase();
    if (searchable.indexOf('season') < 0 && searchable.indexOf('tournament') < 0) continue;

    const slot = 0xa0 + operationIndex * Process.pointerSize;
    let target = ptr(0);
    try { target = vtable.add(slot).readPointer(); } catch (_) {}
    competitionOperationIndexes[String(operationIndex)] = {
      name: operationName, config_suffix: configSuffix, slot: slot, target: target.toString()
    };
    emit('cards-competition-operation-descriptor', {
      operation_index: operationIndex, operation_name: operationName, config_suffix: configSuffix,
      descriptor: descriptor.toString(), descriptor_bytes: hexBytes(raw(descriptor, 0x18)),
      handler: handler.toString(), category: readU32(descriptor, 0x0c),
      service_slot: '0x' + slot.toString(16), service_target: target.toString(),
      service_target_range: readable(target), service_target_bytes: hexBytes(raw(target, 24))
    });

    if (pointerInModule(module, handler) && executable(handler)) {
      const statusKey = 'competition-status-' + handler.toString();
      if (!serviceApiTargetsHooked[statusKey]) {
        try {
          Interceptor.attach(handler, {
            onEnter(args) {
              this.active = competitionTraceActive();
              if (!this.active) return;
              this.before = hexBytes(raw(descriptor, 0x18));
              emit('cards-competition-operation-status-enter', {
                competition_kind: competitionTraceKind, operation_index: operationIndex,
                operation_name: operationName, config_suffix: configSuffix,
                value: args[0].toUInt32() & 0xff, descriptor_before: this.before,
                thread_id: tid(), return_address: safePtr(this.returnAddress),
                backtrace: normalizedBacktrace(this.context)
              });
            },
            onLeave(retval) {
              if (!this.active) return;
              emit('cards-competition-operation-status-leave', {
                competition_kind: competitionTraceKind, operation_index: operationIndex,
                operation_name: operationName, config_suffix: configSuffix,
                descriptor_after: hexBytes(raw(descriptor, 0x18)), thread_id: tid()
              });
            }
          });
          serviceApiTargetsHooked[statusKey] = true;
        } catch (error) {
          emit('cards-competition-operation-status-hook-error', {
            operation_index: operationIndex, operation_name: operationName,
            handler: handler.toString(), error: String(error)
          });
        }
      }
    }

    if (pointerInModule(module, target) && executable(target)) {
      const apiKey = 'competition-api-' + target.toString();
      const standardApiKey = 'api-' + target.toString();
      if (!serviceApiTargetsHooked[apiKey] && !serviceApiTargetsHooked[standardApiKey]) {
        try {
          Interceptor.attach(target, {
            onEnter(args) {
              this.callNumber = (counters['competition-api-' + operationIndex] || 0) + 1;
              counters['competition-api-' + operationIndex] = this.callNumber;
              this.logCall = this.callNumber <= 30;
              if (!this.logCall) return;
              emit('cards-competition-service-api-enter', {
                operation_index: operationIndex, operation_name: operationName,
                config_suffix: configSuffix, service_slot: '0x' + slot.toString(16),
                target: target.toString(), call_number: this.callNumber,
                competition_trace_active: competitionTraceActive(), competition_kind: competitionTraceKind,
                thread_id: tid(), return_address: safePtr(this.returnAddress),
                self: pointerProbe(ptr(this.context.ecx), 0x100), args: stackArgs(this.context, 10),
                backtrace: normalizedBacktrace(this.context)
              });
            },
            onLeave(retval) {
              if (!this.logCall) return;
              emit('cards-competition-service-api-leave', {
                operation_index: operationIndex, operation_name: operationName,
                config_suffix: configSuffix, service_slot: '0x' + slot.toString(16),
                target: target.toString(), call_number: this.callNumber,
                thread_id: tid(), retval: retval.toString(), retval_i32: retval.toInt32(),
                retval_probe: readable(retval) ? pointerProbe(retval, 0x180) : null
              });
            }
          });
          serviceApiTargetsHooked[apiKey] = true;
        } catch (error) {
          emit('cards-competition-service-api-hook-error', {
            operation_index: operationIndex, operation_name: operationName,
            target: target.toString(), error: String(error)
          });
        }
      }
    }
  }
}
function hookServiceApiTargets(module) {
  const snapshot = webSessionSnapshot(module, 'auth-gate-service-api-install');
  let service = ptr(snapshot.service), vtable = ptr(snapshot.vtable);
  emit('cards-auth-service-api-snapshot', {
    snapshot: snapshot, state: cardsServiceStateSnapshot(module, 'auth-gate-service-api-install')
  });
  if (service.isNull() || vtable.isNull()) return;
  SERVICE_API_SLOTS.forEach(function (spec) {
    let target = ptr(0);
    try { target = vtable.add(spec.slot).readPointer(); } catch (_) {}
    const targetInfo = {
      name: spec.name, operation_index: spec.operation_index, slot: '0x' + spec.slot.toString(16),
      target: target.toString(), target_range: readable(target), target_bytes: hexBytes(raw(target, 24))
    };
    emit('cards-auth-service-api-slot', targetInfo);
    if (!pointerInModule(module, target) || !executable(target)) return;
    const key = 'api-' + target.toString();
    if (serviceApiTargetsHooked[key]) {
      emit('cards-auth-service-api-alias', {...targetInfo, existing_key: key});
      return;
    }
    try {
      Interceptor.attach(target, {
        onEnter(args) {
          this.self = ptr(this.context.ecx);
          this.callNumber = (counters['api-call-' + spec.name] || 0) + 1;
          counters['api-call-' + spec.name] = this.callNumber;
          this.logCall = this.callNumber <= 40;
          if (!this.logCall) return;
          emit('cards-auth-service-api-enter', {
            name: spec.name, operation_index: spec.operation_index, slot: '0x' + spec.slot.toString(16),
            target: target.toString(), call_number: this.callNumber, thread_id: tid(),
            return_address: safePtr(this.returnAddress), self: pointerProbe(this.self, 0x100),
            args: stackArgs(this.context, 10), state: cardsServiceStateSnapshot(module, spec.name + ' enter'),
            backtrace: normalizedBacktrace(this.context)
          });
        },
        onLeave(retval) {
          if (!this.logCall) return;
          const retvalRange = readable(retval);
          emit('cards-auth-service-api-leave', {
            name: spec.name, operation_index: spec.operation_index, slot: '0x' + spec.slot.toString(16),
            target: target.toString(), call_number: this.callNumber, thread_id: tid(),
            retval: retval.toString(), retval_i32: retval.toInt32(), retval_range: retvalRange,
            retval_probe: retvalRange ? pointerProbe(retval, 0x180) : null,
            state: cardsServiceStateSnapshot(module, spec.name + ' leave')
          });
          if (retvalRange !== null && (spec.operation_index === 23 || spec.operation_index === 28 || spec.operation_index === 29 || spec.operation_index === 32)) {
            hookAuthSubobjectMethods(module, retval, spec.name + ' return');
          }
        }
      });
      serviceApiTargetsHooked[key] = true;
      emit('cards-auth-service-api-hook-ready', targetInfo);
    } catch (error) {
      emit('cards-auth-service-api-hook-error', {...targetInfo, error: String(error)});
    }
  });
  hookOperationStatusSetters(module);
  hookCompetitionOperationDescriptors(module, service, vtable);
}

function responseSnapshot(response, source) {
  if (response === null || response.isNull()) return {source: source, response: null};
  return {
    source: source,
    response: response.toString(),
    status_18: readU32(response, 0x18),
    bool_20: readU8(response, 0x20),
    bool_21: readU8(response, 0x21),
    bool_22: readU8(response, 0x22),
    bool_23: readU8(response, 0x23),
    bytes_0_3f: hexBytes(raw(response, 0x40)),
    pointer_fields_0_40: pointerFields(response, 0x40)
  };
}

function dynamicMessagesSnapshot(module, source) {
  const globalAddress = module.base.add(DYNAMIC_MESSAGES_GLOBAL_RVA);
  let objectPointer = ptr(0);
  let basePointer = ptr(0);
  try { objectPointer = globalAddress.readPointer(); } catch (_) {}
  if (!objectPointer.isNull()) {
    try { basePointer = objectPointer.add(0x14).readPointer(); } catch (_) {}
  }
  const snapshot = {
    source: source,
    global_address: globalAddress.toString(),
    object: objectPointer.toString(),
    object_range: readable(objectPointer),
    object_bytes: hexBytes(raw(objectPointer, 0x40)),
    base_pointer: basePointer.toString(),
    base_url: cstring(basePointer, 1024),
    base_range: readable(basePointer)
  };
  emit('cards-dynamic-messages-config-snapshot', snapshot);
  return snapshot;
}

function noteDynamicFallbackDns(node) {
  try {
    if (node !== null && String(node).toLowerCase() === DYNAMIC_FALLBACK_HOST) {
      dynamicFallbackDnsSeen = true;
      emit('dynamic-messages-fallback-dns-armed', {host: node, port: DYNAMIC_FALLBACK_PORT, thread_id: tid()});
    }
  } catch (_) {}
}

function hookConnect(name) {
  const address = Module.findGlobalExportByName(name);
  if (address === null) { emit('trusted-console-hook-missing', {export: name}); return; }
  Interceptor.attach(address, {
    onEnter(args) {
      const sockaddr = args[1];
      if (sockaddr.isNull()) return;
      try {
        if (sockaddr.readU16() !== 2) return;
        const port = (sockaddr.add(2).readU8() << 8) | sockaddr.add(3).readU8();
        const originalIp = [0,1,2,3].map(i => sockaddr.add(4 + i).readU8()).join('.');
        if (trustedWindowActive) {
          emit('trusted-console-connect', {export: name, socket: args[0].toUInt32(), original_ip: originalIp, port: port, thread_id: tid()});
        }
        const exactDynamicFallback = trustedWindowActive && dynamicFallbackDnsSeen && port === DYNAMIC_FALLBACK_PORT;
        if (REDIRECT_PORTS.has(port) || exactDynamicFallback) {
          let localPort = port;
          if (port === REMOTE_REDIRECTOR_PORT) {
            localPort = LOCAL_REDIRECTOR_PORT;
            sockaddr.add(2).writeU8((localPort >> 8) & 0xff);
            sockaddr.add(3).writeU8(localPort & 0xff);
          }
          sockaddr.add(4).writeByteArray([127,0,0,1]);
          emit('local-connect-redirect', {
            export: name, original_ip: originalIp, port: port, local_port: localPort, thread_id: tid(),
            reason: port === REMOTE_REDIRECTOR_PORT ? '42127 occupied by Windows; remapped to local redirector 42129' : (exactDynamicFallback ? 'observed FUT dynamic-messages fallback after exact DNS host' : 'known local service port')
          });
        }
      } catch (error) { emit('trusted-console-connect-error', {export: name, error: String(error)}); }
    }
  });
}
function hookDnsA(name) {
  const address = Module.findGlobalExportByName(name);
  if (address === null) return;
  Interceptor.attach(address, {onEnter(args) {
    if (!trustedWindowActive) return;
    const node = cstring(args[0], 512);
    noteDynamicFallbackDns(node);
    emit('trusted-console-dns', {export: name, node: node, service: cstring(args[1], 64), thread_id: tid()});
  }});
}
function hookDnsW(name) {
  const address = Module.findGlobalExportByName(name);
  if (address === null) return;
  Interceptor.attach(address, {onEnter(args) {
    if (!trustedWindowActive) return;
    const node = utf16(args[0], 256);
    noteDynamicFallbackDns(node);
    emit('trusted-console-dns', {export: name, node: node, service: utf16(args[1], 64), thread_id: tid()});
  }});
}
function hookHostByName() {
  const address = Module.findGlobalExportByName('gethostbyname');
  if (address === null) return;
  Interceptor.attach(address, {onEnter(args) {
    if (!trustedWindowActive) return;
    const node = cstring(args[0], 512);
    noteDynamicFallbackDns(node);
    emit('trusted-console-dns', {export: 'gethostbyname', node: node, thread_id: tid()});
  }});
}
function hookSend() {
  const address = Module.findGlobalExportByName('send');
  if (address === null) return;
  Interceptor.attach(address, {onEnter(args) {
    const length = args[2].toInt32();
    const count = Math.max(0, Math.min(length, 4096));
    const previewText = cstring(args[1], count);
    // Competition tracing must be armed from the request, not the response.
    // BETA 2.4 armed from the later scalar response callback, after the season
    // list parser had already performed ten reward-item lookups. Inspect only
    // for the four competition request lines outside the trusted-device window.
    noteCompetitionHttpRequest(previewText, 'send');
    if (!trustedWindowActive) return;
    emit('trusted-console-send', {socket: args[0].toUInt32(), length: length, preview_text: previewText, preview_hex: hexBytes(raw(args[1], count)), thread_id: tid()});
  }});
}
function hookWSASend() {
  const address = Module.findGlobalExportByName('WSASend');
  if (address === null) return;
  Interceptor.attach(address, {onEnter(args) {
    const count = Math.min(args[2].toUInt32(), 3);
    const buffers = [];
    for (let i = 0; i < count; i++) {
      try {
        const item = args[1].add(i * 8);
        const length = item.readU32();
        const buffer = item.add(4).readPointer();
        const preview = Math.min(length, 4096);
        const text = cstring(buffer, preview);
        noteCompetitionHttpRequest(text, 'WSASend');
        if (trustedWindowActive) buffers.push({length: length, text: text, hex: hexBytes(raw(buffer, preview))});
      } catch (_) {}
    }
    if (!trustedWindowActive) return;
    emit('trusted-console-send', {export: 'WSASend', socket: args[0].toUInt32(), buffers: buffers, thread_id: tid()});
  }});
}
function hookRecv() {
  const address = Module.findGlobalExportByName('recv');
  if (address === null) return;
  Interceptor.attach(address, {
    onEnter(args) {
      this.capture = trustedWindowActive;
      if (this.capture) { this.socket = args[0].toUInt32(); this.buffer = args[1]; }
    },
    onLeave(retval) {
      if (!this.capture) return;
      const length = retval.toInt32();
      // ProtoHttp often reads HTTP status/header bytes one at a time. V2.35
      // exhausted the per-kind event budget before the later My Club/static-art
      // traffic arrived. Ignore those one-byte reads and preserve the useful
      // multi-byte body/response events.
      if (length === 1) return;
      const preview = Math.max(0, Math.min(length, 4096));
      emit('trusted-console-recv', {socket: this.socket, length: length, preview_text: preview > 0 ? cstring(this.buffer, preview) : null, preview_hex: preview > 0 ? hexBytes(raw(this.buffer, preview)) : null, thread_id: tid()});
    }
  });
}
function hookWSARecv() {
  const address = Module.findGlobalExportByName('WSARecv');
  if (address === null) return;
  Interceptor.attach(address, {
    onEnter(args) {
      this.capture = trustedWindowActive;
      if (!this.capture) return;
      this.socket = args[0].toUInt32();
      this.buffers = args[1];
      this.bufferCount = Math.min(args[2].toUInt32(), 3);
      this.bytesPointer = args[3];
      this.overlapped = args[5];
    },
    onLeave(retval) {
      if (!this.capture) return;
      let transferred = null;
      try { if (!this.bytesPointer.isNull()) transferred = this.bytesPointer.readU32(); } catch (_) {}
      if (transferred === 1) return;
      const buffers = [];
      for (let i = 0; i < this.bufferCount; i++) {
        try {
          const item = this.buffers.add(i * 8);
          const capacity = item.readU32();
          const buffer = item.add(4).readPointer();
          const available = transferred === null ? 0 : Math.min(capacity, transferred);
          const preview = Math.min(available, 2048);
          buffers.push({capacity: capacity, available: available, text: preview > 0 ? cstring(buffer, preview) : null, hex: preview > 0 ? hexBytes(raw(buffer, preview)) : null});
        } catch (_) {}
      }
      emit('trusted-console-recv', {
        export: 'WSARecv', socket: this.socket, retval: retval.toInt32(), transferred: transferred,
        overlapped: safePtr(this.overlapped), buffers: buffers, thread_id: tid()
      });
    }
  });
}

function installCardsHook(module, spec, callbacks) {
  const address = module.base.add(spec.rva);
  const check = verify(address.add(spec.sigOffset || 0), spec.signature);
  emit('cards-operation92-signature', {
    name: spec.name,
    rva: '0x' + spec.rva.toString(16),
    address: address.toString(),
    expected: hexBytes(spec.signature),
    actual: check.actual,
    matched: check.matched,
    error: check.error || null
  });
  if (!check.matched) return false;
  try {
    Interceptor.attach(address, callbacks);
    emit('cards-operation92-hook-ready', {name: spec.name, rva: '0x' + spec.rva.toString(16), address: address.toString()});
    return true;
  } catch (error) {
    emit('cards-operation92-hook-error', {name: spec.name, address: address.toString(), error: String(error)});
    return false;
  }
}


function pointerPattern32(pointer) {
  try {
    const value = pointer.toUInt32();
    const bytes = [value & 0xff, (value >>> 8) & 0xff, (value >>> 16) & 0xff, (value >>> 24) & 0xff];
    return bytes.map(v => v.toString(16).padStart(2, '0')).join(' ');
  } catch (_) { return null; }
}

function authDescriptorSnapshot(module, source) {
  const descriptor = module.base.add(AUTH_OPERATION_DESCRIPTOR_RVA);
  return {
    source: source,
    address: descriptor.toString(),
    bytes: hexBytes(raw(descriptor, 0x20)),
    state_0: readU32(descriptor, 0),
    state_byte_1: readU8(descriptor, 1),
    setter_4: (function(){try{return descriptor.add(4).readPointer().toString();}catch(_){return null;}})(),
    operation_name: (function(){try{return cstring(descriptor.add(8).readPointer(),128);}catch(_){return null;}})(),
    category_c: readU32(descriptor, 0x0c),
    config_suffix: (function(){try{return cstring(descriptor.add(0x10).readPointer(),128);}catch(_){return null;}})(),
    resolved_base_14: (function(){try{return descriptor.add(0x14).readPointer().toString();}catch(_){return null;}})()
  };
}

function validateAuthWrapper(module, wrapper, source) {
  try {
    if (wrapper === null || wrapper.isNull() || readable(wrapper) === null) return null;
    const wrapperVtable = wrapper.readPointer();
    const expectedWrapperVtable = module.base.add(AUTH_WEBSERVICE_VTABLE_RVA);
    if (!wrapperVtable.equals(expectedWrapperVtable)) return null;
    const request = wrapper.add(0x10).readPointer();
    if (request.isNull() || readable(request) === null) return null;
    const requestVtable = request.readPointer();
    const expectedRequestVtable = module.base.add(AUTH_WEBSESSION_VTABLE_RVA);
    if (!requestVtable.equals(expectedRequestVtable)) return null;
    const startSlot = requestVtable.readPointer();
    const expectedStart = module.base.add(AUTH_REQUEST_START_RVA);
    if (!startSlot.equals(expectedStart)) return null;
    return {
      source: source,
      wrapper: wrapper,
      wrapper_vtable: wrapperVtable,
      transport: (function(){try{return wrapper.add(8).readPointer();}catch(_){return ptr(0);}})(),
      auxiliary: (function(){try{return wrapper.add(0x0c).readPointer();}catch(_){return ptr(0);}})(),
      request: request,
      request_vtable: requestVtable,
      start_slot: startSlot
    };
  } catch (_) { return null; }
}

function findAuthWebSession(module, source) {
  const globals = module.base.add(WEBSESSION_GLOBAL_RVA);
  let service = ptr(0);
  try { service = globals.readPointer(); } catch (_) {}
  const serviceRange = readable(service);
  const expectedWrapperVtable = module.base.add(AUTH_WEBSERVICE_VTABLE_RVA);
  const resultBase = {
    source: source,
    global_address: globals.toString(),
    service: service.toString(),
    service_range: serviceRange,
    expected_wrapper_vtable: expectedWrapperVtable.toString(),
    expected_request_vtable: module.base.add(AUTH_WEBSESSION_VTABLE_RVA).toString(),
    expected_start: module.base.add(AUTH_REQUEST_START_RVA).toString()
  };
  if (service.isNull() || serviceRange === null) {
    emit('cards-authenticated-icebreaker-object-search', {...resultBase, found: false, reason: 'service-global-unavailable'});
    return null;
  }

  // First validate wrappers captured by constructor/initialize hooks, in case
  // this build initializes the subobject after LoginToFUT.
  for (const captured of [authWrapperFromInitialize, authWrapperFromConstructor]) {
    if (captured !== null) {
      const checked = validateAuthWrapper(module, captured, source + ' captured-wrapper');
      if (checked !== null) {
        emit('cards-authenticated-icebreaker-object-search', {...resultBase, found: true, method: 'captured-wrapper', wrapper: checked.wrapper.toString(), request: checked.request.toString()});
        return checked;
      }
    }
  }

  const rangeEnd = ptr(serviceRange.base).add(serviceRange.size);
  const remaining = Number(rangeEnd.sub(service));
  const scanLength = Math.max(0, Math.min(AUTH_WRAPPER_SCAN_LIMIT, remaining));
  const pattern = pointerPattern32(expectedWrapperVtable);
  if (scanLength < 4 || pattern === null) {
    emit('cards-authenticated-icebreaker-object-search', {...resultBase, found: false, reason: 'insufficient-readable-range', remaining: remaining, scan_length: scanLength});
    return null;
  }

  let hits = [];
  try { hits = Memory.scanSync(service, scanLength, pattern); }
  catch (error) {
    emit('cards-authenticated-icebreaker-object-search', {...resultBase, found: false, reason: 'memory-scan-error', scan_length: scanLength, error: String(error)});
    return null;
  }
  const candidates = [];
  for (const hit of hits) {
    const checked = validateAuthWrapper(module, hit.address, source + ' service-scan');
    candidates.push({address: hit.address.toString(), verified: checked !== null});
    if (checked !== null) {
      emit('cards-authenticated-icebreaker-object-search', {...resultBase, found: true, method: 'service-vtable-scan', scan_length: scanLength, hit_count: hits.length, candidates: candidates, wrapper: checked.wrapper.toString(), request: checked.request.toString()});
      return checked;
    }
  }
  emit('cards-authenticated-icebreaker-object-search', {...resultBase, found: false, reason: 'no-verified-auth-wrapper', scan_length: scanLength, hit_count: hits.length, candidates: candidates});
  return null;
}

function attemptNativeAuthStart(module, updateThreadId) {
  if (!authStartArmed || authStartCalled || authStartInProgress) return;
  if (!controlledOverrideApplied || !locstringsAccepted) return;
  if (authStartUpdateCountdown > 0) {
    authStartUpdateCountdown -= 1;
    return;
  }
  if (authCandidateAttempts >= 40) {
    authStartArmed = false;
    emit('cards-authenticated-icebreaker-start-abandoned', {thread_id: updateThreadId, attempts: authCandidateAttempts, reason: 'verified auth WebSession object was not found before retry limit'});
    return;
  }
  authCandidateAttempts += 1;
  const candidate = findAuthWebSession(module, 'frontend update attempt ' + authCandidateAttempts);
  if (candidate === null) {
    authStartUpdateCountdown = 3;
    return;
  }
  authCandidate = candidate;
  const startAddress = module.base.add(AUTH_REQUEST_START_RVA);
  const startCheck = verify(startAddress, [0x55,0x8b,0xec,0x81,0xec,0xe8,0x03,0x00,0x00,0x53,0x56,0x6a,0x00]);
  if (!startCheck.matched) {
    authStartArmed = false;
    emit('cards-authenticated-icebreaker-start-blocked', {thread_id: updateThreadId, reason: 'start-signature-mismatch', address: startAddress.toString(), actual: startCheck.actual});
    return;
  }
  authStartInProgress = true;
  authStartCalled = true;
  let result = null;
  let error = null;
  emit('cards-authenticated-icebreaker-start-call-enter', {
    thread_id: updateThreadId,
    wrapper: candidate.wrapper.toString(),
    request: candidate.request.toString(),
    request_vtable: candidate.request_vtable.toString(),
    start_address: startAddress.toString(),
    request_bytes: hexBytes(raw(candidate.request, 0x240)),
    descriptor: authDescriptorSnapshot(module, 'before exact Authentication request start'),
    rationale: 'Call the statically verified FIFA 14 Authentication WebSession start method on its verified live object. This exact method allocates a 1000-byte JSON buffer and submits operation 23, matching the observed FIFA 13 /ut/auth request size.'
  });
  try {
    const start = new NativeFunction(startAddress, 'uchar', ['pointer'], 'thiscall');
    result = start(candidate.request);
  } catch (caught) {
    error = String(caught);
  }
  authStartInProgress = false;
  authStartArmed = false;
  emit('cards-authenticated-icebreaker-start-call-leave', {
    thread_id: updateThreadId,
    result: result,
    error: error,
    builder_calls: authBuilderCalls,
    submit_calls: authSubmitCalls,
    request_bytes_after: hexBytes(raw(candidate.request, 0x240)),
    descriptor: authDescriptorSnapshot(module, 'after exact Authentication request start')
  });
}

function attemptAuthenticatedIcebreaker(updateThreadId) {
  // Passive route-patch experiment: never submit a competing manual NAV action.
  return;
  if (authenticatedIcebreakerSent || authenticatedIcebreakerNotBeforeMs <= 0) return;
  if (nowMs() < authenticatedIcebreakerNotBeforeMs) return;
  const prerequisites = {
    thread_id: updateThreadId,
    fcc_login1_active: fccLogin1Active,
    fcc_login2_active: fccLogin2Active,
    phishing_validation_succeeded: phishingValidationSucceeded,
    locstrings_accepted: locstringsAccepted,
    get_user_info_parser_completed: getUserInfoParserCompleted,
    get_user_info_parser_succeeded: getUserInfoParserSucceeded,
    auth_submit_calls: authSubmitCalls,
    nav_send_action_ready: navSendActionFunction !== null,
    already_sent: authenticatedIcebreakerSent
  };
  emit('authenticated-icebreaker-route-check', prerequisites);
  if (!fccLogin1Active || fccLogin2Active || !phishingValidationSucceeded ||
      !locstringsAccepted || !getUserInfoParserCompleted || !getUserInfoParserSucceeded ||
      authSubmitCalls < 1 || navSendActionFunction === null) {
    authenticatedIcebreakerNotBeforeMs = 0;
    emit('authenticated-icebreaker-route-not-sent', {
      ...prerequisites,
      rationale: 'The gate fires only while retail fcc_login1 is still active after genuine /ut/auth, successful security validation, accepted localization, and the exact GetUserInfo parser completion.'
    });
    return;
  }
  authenticatedIcebreakerSent = true;
  authenticatedIcebreakerNotBeforeMs = 0;
  emit('authenticated-icebreaker-action-enter', {
    ...prerequisites,
    action: 'iceBreaker',
    rationale: 'futLogInFlow.nav maps the documented fcc_login1 iceBreaker action to futIcebreakerFlow. This calls the original NAV route only after authentication and GetUserInfo; it does not load a view directly or create a club, squad, player, item, pack, or currency.'
  });
  try {
    navSendActionFunction(ptr(0), authenticatedIcebreakerAction, authenticatedIcebreakerEmptyParameter);
    emit('authenticated-icebreaker-action-leave', {thread_id: updateThreadId, action: 'iceBreaker', invoked: true});
  } catch (error) {
    authenticatedIcebreakerSent = false;
    emit('authenticated-icebreaker-action-error', {thread_id: updateThreadId, action: 'iceBreaker', error: String(error)});
  }
}

// V2.40.9: exact local Store validation + expanded passive frontend handoff trace.  The retail UI asks the
// FUT_Store object whether an offer is available and whether the selected
// currency can be used before it ever calls PurchasePack.  The v2.40.4 live
// trace proved the click stopped before *any* POST/PUT reached localhost.
// Keep the override extremely narrow: only the three Store boolean wrappers
// below, only while their result is being pushed back to the script VM.
const STORE_LOCAL_WRAPPERS = [
  {name:'ValidateCoinPurchase', forceLocalTrue:true, rva:0x00064150, kind:'validate'},
  {name:'ValidatePointsPurchase', forceLocalTrue:false, rva:0x00064190, kind:'validate'},
  {name:'IsPackAvailable', forceLocalTrue:true, rva:0x00064330, kind:'available'},
  {name:'PurchasePack', forceLocalTrue:false, rva:0x000640d0, kind:'purchase'}
];
// V2.40.9: passive coverage of every other Store script wrapper surrounding the
// confirm/purchase path. These hooks never alter arguments or return values; they
// only tell us which frontend command the retail Store chooses after the coin
// check succeeds.
const STORE_PASSIVE_WRAPPERS = [
  {name:'EnterStore', rva:0x00064110, sigKind:'singleton8b0d'},
  {name:'ExitStore', rva:0x00064130, sigKind:'singleton8b0d'},
  {name:'ValidateSales', rva:0x000641d0, sigKind:'singletonA1'},
  {name:'GetDockData', rva:0x00064200, sigKind:'56e8'},
  {name:'GetCategoryForPackID', rva:0x00064260, sigKind:'singletonA1'},
  {name:'UpdateTimeRemaining', rva:0x00064290, sigKind:'56e8'},
  {name:'UpdatePackQuantities', rva:0x00064300, sigKind:'singletonA1'},
  {name:'MyPacksCount', rva:0x00064360, sigKind:'singleton8b0d'},
  {name:'ConsumeUserCode', rva:0x00064380, sigKind:'singletonA1'},
  {name:'GetCategoryIDByEnum', rva:0x000643b0, sigKind:'singletonA1'},
  {name:'SeasonTicketPurchaseResultCallback', rva:0x00064400, sigKind:'singletonA1'},
  {name:'GetCurrencyList', rva:0x00064430, sigKind:'56e8'},
  {name:'IsTokenRedemptionEnabled', rva:0x00064490, sigKind:'singleton8b0d'},
  {name:'IsDDP', rva:0x000644b0, sigKind:'singleton8b0d'},
  {name:'DoesRegionMatch', rva:0x000644d0, sigKind:'singleton8b0d'},
  {name:'Show1PStoreLogo', rva:0x000644f0, sigKind:'singletonA1'}
];
let storePassiveHooksInstalled = false;
const STORE_SCRIPT_BOOL_PUSH_RVA = 0x0001ab40;
const STORE_SCRIPT_NUMBER_ARG_RVA = 0x0001aa50;
const USER_CREDITS_PARSER_RVA = 0x0012f500;
let storeLocalHooksInstalled = false;
let storeNumberArgHookInstalled = false;
let userCreditsParserHookInstalled = false;
const storeGateStackByThread = Object.create(null);
const storeWrapperStackByThread = Object.create(null);

function ptr32le(address) {
  const value = address.toUInt32();
  return [value & 0xff, (value >>> 8) & 0xff, (value >>> 16) & 0xff, (value >>> 24) & 0xff];
}

function storeWrapperSignature(module, spec) {
  // These instructions contain ASLR-relocated absolute pointers.  v2.40.5
  // compared them with the preferred-base bytes, so every hook was silently
  // rejected whenever CardsDLL loaded anywhere except 0x10000000.
  const singleton = ptr32le(module.base.add(0x001d6650));
  if (spec.kind === 'validate') return [0x55,0x8b,0xec,0x51,0xa1].concat(singleton, [0x56]);
  if (spec.kind === 'available') return [0xa1].concat(singleton, [0x56,0x8b,0x30,0x6a,0x00]);
  return [0xa1].concat(singleton, [0x56,0x8b,0x30,0x6a,0x01]);
}

function storeBoolPushSignature(module) {
  return [0x55,0x8b,0xec,0x8b,0x0d].concat(ptr32le(module.base.add(0x001d5390)));
}

function storeNumberArgSignature(module) {
  return [0x55,0x8b,0xec,0x8b,0x0d].concat(ptr32le(module.base.add(0x001d5390)), [0x8b,0x01,0x8b,0x55,0x08]);
}

function installStoreNumberArgTrace(module) {
  if (storeNumberArgHookInstalled) return;
  const address = module.base.add(STORE_SCRIPT_NUMBER_ARG_RVA);
  const signature = storeNumberArgSignature(module);
  const check = verify(address, signature);
  emit('cards-store-script-number-arg-signature', {address:address.toString(), matched:check.matched, actual:check.actual, expected:hexBytes(signature), error:check.error || null});
  if (!check.matched) return;
  try {
    Interceptor.attach(address, {
      onEnter(args) {
        const stack = storeWrapperStackByThread[String(tid())] || [];
        if (stack.length === 0) return;
        this.active = true;
        this.wrapper = stack[stack.length - 1];
        this.arg_index = args[0].toUInt32();
      },
      onLeave(retval) {
        if (!this.active) return;
        emit('cards-store-script-number-arg', {thread_id:tid(), wrapper:this.wrapper, arg_index:this.arg_index, value_u32:retval.toUInt32(), value_i32:retval.toInt32(), value_hex:retval.toString()});
      }
    });
    storeNumberArgHookInstalled = true;
    emit('cards-store-script-number-arg-hook-ready', {address:address.toString()});
  } catch (error) {
    emit('cards-store-script-number-arg-hook-error', {address:address.toString(), error:String(error)});
  }
}

function installUserCreditsParserTrace(module) {
  if (userCreditsParserHookInstalled) return;
  const address = module.base.add(USER_CREDITS_PARSER_RVA);
  const signature = [0x55,0x8b,0xec,0x81,0xec,0xe0,0x00,0x00,0x00,0x53,0x56,0x57,0x33,0xdb];
  const check = verify(address, signature);
  emit('cards-user-credits-parser-signature', {address:address.toString(), matched:check.matched, actual:check.actual, expected:hexBytes(signature), error:check.error || null});
  if (!check.matched) return;
  try {
    Interceptor.attach(address, {
      onEnter(args) {
        this.object = ptr(this.context.ecx);
        emit('cards-user-credits-parser-enter', {thread_id:tid(), object:safePtr(this.object), coins_20:readU32(this.object,0x20), points_24:readU32(this.object,0x24), field_28:readU32(this.object,0x28), field_2c:readU32(this.object,0x2c), input:safePtr(args[0])});
      },
      onLeave(retval) {
        emit('cards-user-credits-parser-leave', {thread_id:tid(), object:safePtr(this.object), result_u8:retval.toUInt32() & 0xff, coins_20:readU32(this.object,0x20), points_24:readU32(this.object,0x24), field_28:readU32(this.object,0x28), field_2c:readU32(this.object,0x2c)});
      }
    });
    userCreditsParserHookInstalled = true;
    emit('cards-user-credits-parser-hook-ready', {address:address.toString(), legacy_currency_literals:['coins','points']});
  } catch (error) {
    emit('cards-user-credits-parser-hook-error', {address:address.toString(), error:String(error)});
  }
}

function passiveStoreWrapperSignature(module, spec) {
  const singleton = ptr32le(module.base.add(0x001d6650));
  if (spec.sigKind === 'singletonA1') return [0xa1].concat(singleton, [0x56]);
  if (spec.sigKind === 'singleton8b0d') return [0x8b,0x0d].concat(singleton);
  if (spec.sigKind === '56e8') return [0x56,0xe8];
  return [];
}

function installPassiveStoreWrapperTrace(module) {
  if (storePassiveHooksInstalled) return;
  let readyCount = 0;
  STORE_PASSIVE_WRAPPERS.forEach(spec => {
    const address = module.base.add(spec.rva);
    const signature = passiveStoreWrapperSignature(module, spec);
    const check = verify(address, signature);
    emit('cards-store-passive-wrapper-signature', {name:spec.name, address:address.toString(), matched:check.matched, actual:check.actual, expected:hexBytes(signature), error:check.error || null});
    if (!check.matched) return;
    try {
      Interceptor.attach(address, {
        onEnter(args) {
          this.thread = String(tid());
          this.name = spec.name;
          if (!storeWrapperStackByThread[this.thread]) storeWrapperStackByThread[this.thread] = [];
          storeWrapperStackByThread[this.thread].push(spec.name);
          const singletonAddress = module.base.add(0x001d6650);
          let storeObject = ptr(0);
          let storeVtable = ptr(0);
          try { storeObject = singletonAddress.readPointer(); } catch (_) {}
          try { if (!storeObject.isNull()) storeVtable = storeObject.readPointer(); } catch (_) {}
          emit('cards-store-passive-wrapper-enter', {thread_id:tid(), name:spec.name, store_object:safePtr(storeObject), store_vtable:safePtr(storeVtable), return_address:safePtr(this.returnAddress), backtrace:normalizedBacktrace(this.context)});
        },
        onLeave(retval) {
          const wrapperStack = storeWrapperStackByThread[this.thread] || [];
          if (wrapperStack.length > 0) wrapperStack.pop();
          if (wrapperStack.length === 0) delete storeWrapperStackByThread[this.thread];
          emit('cards-store-passive-wrapper-leave', {thread_id:tid(), name:this.name, retval:retval.toString()});
        }
      });
      readyCount += 1;
    } catch (error) {
      emit('cards-store-passive-wrapper-hook-error', {name:spec.name, address:address.toString(), error:String(error)});
    }
  });
  storePassiveHooksInstalled = readyCount > 0;
  emit('cards-store-passive-hooks-ready', {wrapper_hooks:readyCount, expected:STORE_PASSIVE_WRAPPERS.length});
}

function installLocalStoreGateHooks(module) {
  if (storeLocalHooksInstalled) return;
  const pushBool = module.base.add(STORE_SCRIPT_BOOL_PUSH_RVA);
  const pushSignature = storeBoolPushSignature(module);
  const pushCheck = verify(pushBool, pushSignature);
  emit('cards-store-local-bool-push-signature', {
    address: pushBool.toString(), matched: pushCheck.matched, actual: pushCheck.actual,
    expected: hexBytes(pushSignature), error: pushCheck.error || null
  });
  if (!pushCheck.matched) return;

  let readyCount = 0;
  STORE_LOCAL_WRAPPERS.forEach(spec => {
    const address = module.base.add(spec.rva);
    const signature = storeWrapperSignature(module, spec);
    const check = verify(address, signature);
    emit('cards-store-local-wrapper-signature', {name:spec.name, address:address.toString(), matched:check.matched, actual:check.actual, expected:hexBytes(signature), error:check.error || null});
    if (!check.matched) return;
    try {
      Interceptor.attach(address, {
        onEnter(args) {
          this.thread = String(tid());
          this.name = spec.name;
          if (!storeWrapperStackByThread[this.thread]) storeWrapperStackByThread[this.thread] = [];
          storeWrapperStackByThread[this.thread].push(spec.name);
          if (spec.name !== 'PurchasePack') {
            if (!storeGateStackByThread[this.thread]) storeGateStackByThread[this.thread] = [];
            storeGateStackByThread[this.thread].push({name:spec.name, forceLocalTrue:spec.forceLocalTrue === true});
          }
          const singletonAddress = module.base.add(0x001d6650);
          let storeObject = ptr(0);
          let storeVtable = ptr(0);
          try { storeObject = singletonAddress.readPointer(); } catch (_) {}
          try { if (!storeObject.isNull()) storeVtable = storeObject.readPointer(); } catch (_) {}
          if (!RUNTIME_PERFORMANCE_MODE) {
            emit('cards-store-local-wrapper-enter', {thread_id:tid(), name:spec.name, store_object:safePtr(storeObject), store_vtable:safePtr(storeVtable), return_address:safePtr(this.returnAddress), backtrace:normalizedBacktrace(this.context)});
          }
        },
        onLeave(retval) {
          if (this.name !== 'PurchasePack') {
            const stack = storeGateStackByThread[this.thread] || [];
            if (stack.length > 0) stack.pop();
            if (stack.length === 0) delete storeGateStackByThread[this.thread];
          }
          const wrapperStack = storeWrapperStackByThread[this.thread] || [];
          if (wrapperStack.length > 0) wrapperStack.pop();
          if (wrapperStack.length === 0) delete storeWrapperStackByThread[this.thread];
          if (!RUNTIME_PERFORMANCE_MODE) {
            emit('cards-store-local-wrapper-leave', {thread_id:tid(), name:this.name, retval:retval.toString()});
          }
        }
      });
      readyCount += 1;
    } catch (error) {
      emit('cards-store-local-wrapper-hook-error', {name:spec.name, address:address.toString(), error:String(error)});
    }
  });

  try {
    Interceptor.attach(pushBool, {
      onEnter(args) {
        const key = String(tid());
        const stack = storeGateStackByThread[key] || [];
        if (stack.length === 0) return;
        const gateInfo = stack[stack.length - 1];
        const gate = gateInfo.name;
        const original = args[0].toUInt32() & 0xff;
        // All offers supplied by this localhost backend are intentionally
        // available, and the user's local balance is sufficient.  Preserve a
        // native true result; only normalize a false local gate to true.
        const overridden = gateInfo.forceLocalTrue && original === 0;
        if (overridden) args[0] = ptr(1);
        // In performance mode only report a mutation. Native-true checks can
        // happen repeatedly while the pack carousel is animating.
        if (overridden || !RUNTIME_PERFORMANCE_MODE) {
          emit('cards-store-local-gate-result', {thread_id:tid(), gate:gate, original:original, emitted:overridden ? 1 : original, overridden:overridden, force_local_true:gateInfo.forceLocalTrue});
        }
      }
    });
    storeLocalHooksInstalled = readyCount > 0;
    emit('cards-store-local-hooks-ready', {wrapper_hooks:readyCount, bool_push_hook:true});
  } catch (error) {
    emit('cards-store-local-bool-push-hook-error', {address:pushBool.toString(), error:String(error)});
  }
  if (!RUNTIME_PERFORMANCE_MODE) installPassiveStoreWrapperTrace(module);
}

function installNativeOfflineStadiumHooks(module, source) {
  function installProvider() {
    let object = ptr(0), vtable = ptr(0), getter = ptr(0), setter = ptr(0);
    try { object = module.base.add(STADIUM_PROVIDER_GLOBAL_RVA).readPointer(); } catch (_) {}
    try { if (!object.isNull()) vtable = object.readPointer(); } catch (_) {}
    try { if (!vtable.isNull()) getter = vtable.add(0x78).readPointer(); } catch (_) {}
    try { if (!vtable.isNull()) setter = vtable.add(0x7c).readPointer(); } catch (_) {}
    const objectKey = safePtr(object);
    if (stadiumProviderLastSnapshotObject !== objectKey) {
      stadiumProviderLastSnapshotObject = objectKey;
      emit('cards-stadium-provider-snapshot-beta222', {
        source: source, object: objectKey, vtable: safePtr(vtable),
        getter: safePtr(getter), setter: safePtr(setter), forced_stadium_id: LOCAL_OFFLINE_STADIUM_ID
      });
    }
    if (!getter.isNull()) {
      const key = 'g:' + getter.toString();
      if (!stadiumProviderTargetsHooked[key]) {
        try {
          Interceptor.attach(getter, {
            onLeave(retval) {
              const original = retval.toInt32();
              if (original !== LOCAL_OFFLINE_STADIUM_ID) retval.replace(ptr(LOCAL_OFFLINE_STADIUM_ID));
              emit('cards-stadium-provider-get-beta222', {
                thread_id: tid(), original: original, emitted: LOCAL_OFFLINE_STADIUM_ID,
                overridden: original !== LOCAL_OFFLINE_STADIUM_ID
              });
            }
          });
          stadiumProviderTargetsHooked[key] = true;
          emit('cards-stadium-provider-get-hook-ready-beta222', {address:getter.toString()});
        } catch (error) {
          emit('cards-stadium-provider-get-hook-error-beta222', {address:getter.toString(), error:String(error)});
        }
      }
    }
    if (!setter.isNull()) {
      const key = 's:' + setter.toString();
      if (!stadiumProviderTargetsHooked[key]) {
        try {
          Interceptor.attach(setter, {
            onEnter(args) {
              let original = 0;
              try { original = args[0].toInt32(); } catch (_) {}
              if (original !== LOCAL_OFFLINE_STADIUM_ID) args[0] = ptr(LOCAL_OFFLINE_STADIUM_ID);
              emit('cards-stadium-provider-set-beta222', {
                thread_id: tid(), original: original, emitted: LOCAL_OFFLINE_STADIUM_ID,
                overridden: original !== LOCAL_OFFLINE_STADIUM_ID
              });
            }
          });
          stadiumProviderTargetsHooked[key] = true;
          emit('cards-stadium-provider-set-hook-ready-beta222', {address:setter.toString()});
        } catch (error) {
          emit('cards-stadium-provider-set-hook-error-beta222', {address:setter.toString(), error:String(error)});
        }
      }
    }
    return !getter.isNull();
  }

  if (stadiumScriptHooksInstalled) {
    installProvider();
    return;
  }
  const getAddress = module.base.add(GET_STADIUM_ID_WRAPPER_RVA);
  const setAddress = module.base.add(SET_ONLINE_STADIUM_ID_WRAPPER_RVA);
  const getCheck = verify(getAddress, [0x8b,0x0d]);
  const setCheck = verify(setAddress, [0xa1]);
  emit('cards-get-stadium-wrapper-signature-beta222', {address:getAddress.toString(), matched:getCheck.matched, actual:getCheck.actual, error:getCheck.error || null});
  emit('cards-set-stadium-wrapper-signature-beta222', {address:setAddress.toString(), matched:setCheck.matched, actual:setCheck.actual, error:setCheck.error || null});
  if (!getCheck.matched) return;
  try {
    const makeScriptInt = new NativeFunction(module.base.add(SCRIPT_INT_RETURN_RVA), 'pointer', ['int'], 'mscdecl');
    Interceptor.attach(getAddress, {
      onEnter(args) { installProvider(); },
      onLeave(retval) {
        let replacement = ptr(0);
        try { replacement = makeScriptInt(LOCAL_OFFLINE_STADIUM_ID); } catch (error) {
          emit('cards-get-stadium-script-convert-error-beta222', {error:String(error)});
          return;
        }
        const original = retval;
        if (!replacement.isNull()) retval.replace(replacement);
        emit('cards-get-stadium-script-result-beta222', {
          thread_id:tid(), original:safePtr(original), emitted:safePtr(replacement),
          stadium_id:LOCAL_OFFLINE_STADIUM_ID
        });
      }
    });
    if (setCheck.matched) {
      Interceptor.attach(setAddress, { onEnter(args) { installProvider(); } });
    }
    stadiumScriptHooksInstalled = true;
    emit('cards-native-offline-stadium-hooks-ready-beta222', {stadium_id:LOCAL_OFFLINE_STADIUM_ID});
  } catch (error) {
    emit('cards-native-offline-stadium-hooks-error-beta222', {error:String(error)});
  }
  const installedNow = installProvider();
  if (!installedNow && !stadiumProviderPollStarted) {
    stadiumProviderPollStarted = true;
    stadiumProviderPollAttempts = 0;
    const timer = setInterval(function () {
      stadiumProviderPollAttempts += 1;
      const ok = installProvider();
      if (ok || stadiumProviderPollAttempts >= 3000) {
        clearInterval(timer);
        emit('cards-stadium-provider-poll-finished-beta222', {
          installed: ok, attempts: stadiumProviderPollAttempts,
          elapsed_ms: stadiumProviderPollAttempts * 200
        });
      } else if (stadiumProviderPollAttempts === 1 || stadiumProviderPollAttempts % 25 === 0) {
        let current = ptr(0);
        try { current = module.base.add(STADIUM_PROVIDER_GLOBAL_RVA).readPointer(); } catch (_) {}
        emit('cards-stadium-provider-poll-beta222', {
          attempt: stadiumProviderPollAttempts, object: safePtr(current)
        });
      }
    }, 200);
  }
}


function installCrashExceptionTrace() {
  if (crashExceptionTraceInstalled) return;
  crashExceptionTraceInstalled = true;
  try {
    Process.setExceptionHandler(function (details) {
      let context = null;
      try {
        context = {
          eip: safePtr(details.context.eip), esp: safePtr(details.context.esp),
          ebp: safePtr(details.context.ebp), eax: safePtr(details.context.eax),
          ebx: safePtr(details.context.ebx), ecx: safePtr(details.context.ecx),
          edx: safePtr(details.context.edx), esi: safePtr(details.context.esi),
          edi: safePtr(details.context.edi)
        };
      } catch (_) {}
      let memory = null;
      try {
        if (details.memory) {
          memory = {
            operation: details.memory.operation || null,
            address: safePtr(details.memory.address)
          };
        }
      } catch (_) {}
      emit('fifa14-native-exception-beta222', {
        type: details.type || null, address: safePtr(details.address),
        memory: memory, context: context,
        backtrace: details.context ? normalizedBacktrace(details.context) : []
      });
      // Preserve the game's/Windows' normal exception handling. This is
      // diagnostic only and must never swallow the fault.
      return false;
    });
    emit('fifa14-native-exception-trace-ready-beta222', {});
  } catch (error) {
    emit('fifa14-native-exception-trace-error-beta222', {error:String(error)});
  }
}

function beta222U64String(low, high) {
  try {
    const value = (Number(high >>> 0) * 4294967296) + Number(low >>> 0);
    return String(Math.floor(value));
  } catch (_) { return null; }
}

function installViewCardsEmptyListGuard(module, source) {
  if (viewCardsTraceInstalled) return true;
  const wrapper = module.base.add(VIEW_CARDS_WRAPPER_RVA);
  const pathBuilder = module.base.add(VIEW_CARDS_PATH_BUILDER_RVA);
  const activatePathBuilder = module.base.add(ACTIVATE_CARD_PATH_BUILDER_RVA);
  const wrapperCheck = verify(wrapper, [0x55,0x8b,0xec,0x83,0xec,0x50,0x53,0x56,0x57]);
  const pathCheck = verify(pathBuilder, [0x55,0x8b,0xec,0x8b,0x41,0x14,0x2b,0x41,0x10,0x83,0xec,0x24]);
  const activateCheck = verify(activatePathBuilder, [0x55,0x8b,0xec,0x83,0xec,0x20,0x56,0x33,0xc0,0x8b,0xf1]);
  emit('cards-view-cards-guard-signatures-beta222', {
    source:source,
    wrapper:{address:wrapper.toString(), matched:wrapperCheck.matched, actual:wrapperCheck.actual, error:wrapperCheck.error || null},
    path_builder:{address:pathBuilder.toString(), matched:pathCheck.matched, actual:pathCheck.actual, error:pathCheck.error || null},
    activate_path_builder:{address:activatePathBuilder.toString(), matched:activateCheck.matched, actual:activateCheck.actual, error:activateCheck.error || null}
  });
  if (!wrapperCheck.matched || !pathCheck.matched || !activateCheck.matched) {
    emit('cards-view-cards-guard-refused-beta222', {reason:'signature mismatch'});
    return false;
  }

  try {
    Interceptor.attach(activatePathBuilder, {
      onEnter(args) {
        const request = ptr(this.context.ecx);
        let low = 0, high = 0;
        try { low = request.add(0x8).readU32(); } catch (_) {}
        try { high = request.add(0xc).readU32(); } catch (_) {}
        if ((low >>> 0) !== 0 || (high >>> 0) !== 0) {
          lastActivateCardIdLow = low >>> 0;
          lastActivateCardIdHigh = high >>> 0;
          lastActivateCardIdTime = Date.now();
        }
        emit('cards-activate-card-path-beta222', {
          thread_id:tid(), request:request.toString(),
          id_low:'0x'+(low>>>0).toString(16), id_high:'0x'+(high>>>0).toString(16),
          id:beta222U64String(low,high), return_address:safePtr(this.returnAddress),
          backtrace:normalizedBacktrace(this.context)
        });
      }
    });
  } catch (error) {
    emit('cards-activate-card-path-hook-error-beta222', {address:activatePathBuilder.toString(), error:String(error)});
  }

  try {
    Interceptor.attach(wrapper, {
      onEnter(args) {
        this.injected = false;
        const sp = ptr(this.context.esp);
        let ids = ptr(0), count = 0;
        try { ids = sp.add(4).readPointer(); } catch (_) {}
        try { count = sp.add(8).readU32(); } catch (_) {}
        const beforeIds = [];
        if (!ids.isNull() && count > 0 && count <= 16 && readable(ids)) {
          for (let i=0; i<count; i++) {
            try {
              const low=ids.add(i*8).readU32(), high=ids.add(i*8+4).readU32();
              beforeIds.push(beta222U64String(low,high));
            } catch (_) { break; }
          }
        }
        let sourceId = null;
        if (count === 0) {
          let low = lastActivateCardIdLow >>> 0;
          let high = lastActivateCardIdHigh >>> 0;
          let sourceKind = 'recent-activate-card';
          if ((low === 0 && high === 0) || (Date.now() - lastActivateCardIdTime) > 5000) {
            low = BETA216_HOME_KIT_ITEM_ID_LOW >>> 0;
            high = BETA216_HOME_KIT_ITEM_ID_HIGH >>> 0;
            sourceKind = 'focused-home-kit-fallback';
          }
          const synthetic = Memory.alloc(8);
          synthetic.writeU32(low);
          synthetic.add(4).writeU32(high);
          beta222SyntheticIdBuffers.push(synthetic);
          if (beta222SyntheticIdBuffers.length > 128) beta222SyntheticIdBuffers.shift();
          try { sp.add(4).writePointer(synthetic); sp.add(8).writeU32(1); } catch (_) {}
          this.injected = true;
          sourceId = {kind:sourceKind, id:beta222U64String(low,high), low:'0x'+low.toString(16), high:'0x'+high.toString(16)};
        }
        emit('cards-view-cards-wrapper-enter-beta222', {
          thread_id:tid(), self:safePtr(this.context.ecx), original_ids:safePtr(ids),
          original_count:count, original_values:beforeIds, injected:this.injected,
          synthetic:sourceId, return_address:safePtr(this.returnAddress),
          backtrace:normalizedBacktrace(this.context)
        });
      },
      onLeave(retval) {
        emit('cards-view-cards-wrapper-leave-beta222', {
          thread_id:tid(), injected:this.injected, retval:retval.toString()
        });
      }
    });
  } catch (error) {
    emit('cards-view-cards-wrapper-hook-error-beta222', {address:wrapper.toString(), error:String(error)});
    return false;
  }

  try {
    Interceptor.attach(pathBuilder, {
      onEnter(args) {
        const request=ptr(this.context.ecx);
        let begin=ptr(0), end=ptr(0), cap=ptr(0), count=0;
        try { begin=request.add(0x10).readPointer(); } catch (_) {}
        try { end=request.add(0x14).readPointer(); } catch (_) {}
        try { cap=request.add(0x18).readPointer(); } catch (_) {}
        const values=[];
        try {
          if (!begin.isNull() && !end.isNull() && end.compare(begin) >= 0) {
            count = Number(end.sub(begin)) / 8;
            if (count >= 0 && count <= 16) {
              for (let i=0; i<count; i++) {
                const low=begin.add(i*8).readU32(), high=begin.add(i*8+4).readU32();
                values.push(beta222U64String(low,high));
              }
            }
          }
        } catch (_) {}
        emit('cards-view-cards-path-enter-beta222', {
          thread_id:tid(), request:request.toString(), begin:safePtr(begin), end:safePtr(end), cap:safePtr(cap),
          count:count, ids:values, return_address:safePtr(this.returnAddress),
          backtrace:normalizedBacktrace(this.context)
        });
      }
    });
  } catch (error) {
    emit('cards-view-cards-path-hook-error-beta222', {address:pathBuilder.toString(), error:String(error)});
    return false;
  }

  viewCardsTraceInstalled = true;
  emit('cards-view-cards-empty-list-guard-ready-beta222', {
    wrapper:wrapper.toString(), path_builder:pathBuilder.toString(), activate_path_builder:activatePathBuilder.toString(),
    focused_home_item_id:beta222U64String(BETA216_HOME_KIT_ITEM_ID_LOW,BETA216_HOME_KIT_ITEM_ID_HIGH)
  });
  return true;
}

function installActivateCardTrace(module, source) {
  function descriptorSnapshot() {
    const descriptor = module.base.add(0x001d30c0 + 45 * 0x18);
    let handler = ptr(0), namePointer = ptr(0), configPointer = ptr(0);
    try { handler = descriptor.add(4).readPointer(); } catch (_) {}
    try { namePointer = descriptor.add(8).readPointer(); } catch (_) {}
    try { configPointer = descriptor.add(0x10).readPointer(); } catch (_) {}
    return {
      address: descriptor.toString(), bytes: hexBytes(raw(descriptor, 0x18)),
      status_dword_0: readU32(descriptor, 0),
      status_handler: safePtr(handler),
      operation_name: cstring(namePointer, 128),
      category: readU32(descriptor, 0x0c),
      config_suffix: cstring(configPointer, 128)
    };
  }

  function tryInstall() {
    let service = ptr(0), vtable = ptr(0), target = ptr(0);
    try { service = module.base.add(WEBSESSION_GLOBAL_RVA).readPointer(); } catch (_) {}
    try { if (!service.isNull()) vtable = service.readPointer(); } catch (_) {}
    try { if (!vtable.isNull()) target = vtable.add(0x154).readPointer(); } catch (_) {}
    emit('cards-activate-card-snapshot-beta222', {
      source: source, service: safePtr(service), vtable: safePtr(vtable),
      slot: '0x154', target: safePtr(target), descriptor: descriptorSnapshot()
    });
    if (service.isNull() || vtable.isNull() || !pointerInModule(module, target) || !executable(target)) return false;

    if (!activateCardTraceHooked) {
      try {
        Interceptor.attach(target, {
          onEnter(args) {
            this.active = true;
            emit('cards-activate-card-accessor-enter-beta222', {
              thread_id: tid(), target: target.toString(),
              self: pointerProbe(ptr(this.context.ecx), 0x80),
              args: stackArgs(this.context, 10),
              return_address: safePtr(this.returnAddress),
              descriptor: descriptorSnapshot(),
              backtrace: normalizedBacktrace(this.context)
            });
          },
          onLeave(retval) {
            if (!this.active) return;
            emit('cards-activate-card-accessor-leave-beta222', {
              thread_id: tid(), retval: retval.toString(),
              retval_probe: readable(retval) ? pointerProbe(retval, 0x100) : null,
              descriptor: descriptorSnapshot()
            });
          }
        });
        activateCardTraceHooked = true;
        emit('cards-activate-card-accessor-hook-ready-beta222', {
          slot:'0x154', target:target.toString(), descriptor:descriptorSnapshot()
        });
      } catch (error) {
        emit('cards-activate-card-accessor-hook-error-beta222', {target:target.toString(), error:String(error)});
        return false;
      }
    }

    // The exact operation-45 descriptor status setter is independent of the
    // WebSession accessor and tells us whether ActivateCard actually launches.
    const descriptor = module.base.add(0x001d30c0 + 45 * 0x18);
    let statusHandler = ptr(0), operationName = null;
    try { statusHandler = descriptor.add(4).readPointer(); } catch (_) {}
    try { operationName = cstring(descriptor.add(8).readPointer(), 128); } catch (_) {}
    const statusKey = 'activate-status-' + statusHandler.toString();
    if (pointerInModule(module, statusHandler) && executable(statusHandler) && !serviceApiTargetsHooked[statusKey]) {
      try {
        Interceptor.attach(statusHandler, {
          onEnter(args) {
            this.before = descriptorSnapshot();
            emit('cards-activate-card-status-enter-beta222', {
              operation_index:45, operation_name:operationName,
              value:args[0].toUInt32() & 0xff, thread_id:tid(),
              descriptor_before:this.before, return_address:safePtr(this.returnAddress),
              backtrace:normalizedBacktrace(this.context)
            });
          },
          onLeave(retval) {
            emit('cards-activate-card-status-leave-beta222', {
              operation_index:45, operation_name:operationName,
              thread_id:tid(), descriptor_after:descriptorSnapshot()
            });
          }
        });
        serviceApiTargetsHooked[statusKey] = true;
        emit('cards-activate-card-status-hook-ready-beta222', {
          operation_index:45, operation_name:operationName,
          handler:statusHandler.toString(), descriptor:descriptorSnapshot()
        });
      } catch (error) {
        emit('cards-activate-card-status-hook-error-beta222', {handler:statusHandler.toString(), error:String(error)});
      }
    }
    return true;
  }

  if (tryInstall()) return;
  if (activateCardTracePollStarted) return;
  activateCardTracePollStarted = true;
  let attempts = 0;
  const timer = setInterval(function () {
    attempts += 1;
    if (tryInstall() || attempts >= 2400) {
      clearInterval(timer);
      emit('cards-activate-card-poll-finished-beta222', {attempts:attempts, elapsed_ms:attempts*250});
    }
  }, 250);
}

function installMatchBridgeTrace(module, source) {
  function tryInstall() {
    let service = ptr(0), vtable = ptr(0);
    try { service = module.base.add(WEBSESSION_GLOBAL_RVA).readPointer(); } catch (_) {}
    try { if (!service.isNull()) vtable = service.readPointer(); } catch (_) {}
    emit('cards-match-bridge-snapshot-beta222', {
      source: source, service: safePtr(service), vtable: safePtr(vtable),
      stadium_provider: (function(){ try { return safePtr(module.base.add(STADIUM_PROVIDER_GLOBAL_RVA).readPointer()); } catch (_) { return '0x0'; } })()
    });
    if (service.isNull() || vtable.isNull()) return false;
    let installed = 0;
    SERVICE_API_SLOTS.forEach(function (spec) {
      if ([52,53,54,55,56,60].indexOf(spec.operation_index) < 0) return;
      let target = ptr(0);
      try { target = vtable.add(spec.slot).readPointer(); } catch (_) {}
      if (!pointerInModule(module, target) || !executable(target)) return;
      const key = spec.operation_index + ':' + target.toString();
      if (matchBridgeTargetsHooked[key]) { installed += 1; return; }
      try {
        Interceptor.attach(target, {
          onEnter(args) {
            this.active = true;
            this.callNo = (counters['match-bridge-' + spec.operation_index] || 0) + 1;
            counters['match-bridge-' + spec.operation_index] = this.callNo;
            installNativeOfflineStadiumHooks(module, 'match-bridge-' + spec.name);
            let provider = ptr(0);
            try { provider = module.base.add(STADIUM_PROVIDER_GLOBAL_RVA).readPointer(); } catch (_) {}
            emit('cards-match-bridge-enter-beta222', {
              name: spec.name, operation_index: spec.operation_index, slot: '0x' + spec.slot.toString(16),
              target: target.toString(), call_number: this.callNo, thread_id: tid(),
              self: pointerProbe(ptr(this.context.ecx), 0x80),
              args: stackArgs(this.context, 8), return_address: safePtr(this.returnAddress),
              stadium_provider: safePtr(provider), backtrace: normalizedBacktrace(this.context)
            });
          },
          onLeave(retval) {
            if (!this.active) return;
            emit('cards-match-bridge-leave-beta222', {
              name: spec.name, operation_index: spec.operation_index, call_number: this.callNo,
              thread_id: tid(), retval: retval.toString(), retval_i32: retval.toInt32(),
              retval_probe: readable(retval) ? pointerProbe(retval, 0x80) : null
            });
          }
        });
        matchBridgeTargetsHooked[key] = true;
        installed += 1;
        emit('cards-match-bridge-hook-ready-beta222', {
          name:spec.name, operation_index:spec.operation_index, slot:'0x'+spec.slot.toString(16), target:target.toString()
        });
      } catch (error) {
        emit('cards-match-bridge-hook-error-beta222', {name:spec.name, operation_index:spec.operation_index, target:target.toString(), error:String(error)});
      }
    });
    return installed > 0;
  }
  const now = tryInstall();
  if (!now && !matchBridgePollStarted) {
    matchBridgePollStarted = true;
    let attempts = 0;
    const timer = setInterval(function () {
      attempts += 1;
      if (tryInstall() || attempts >= 2400) {
        clearInterval(timer);
        emit('cards-match-bridge-poll-finished-beta222', {attempts:attempts, elapsed_ms:attempts*250});
      }
    }, 250);
  }
}

function hookUiWriterTarget(target, kind, source) {
  if (target.isNull() || !executable(target)) return false;
  const key = kind + ':' + target.toString();
  if (uiWriterTargetsHooked[key]) return true;
  try {
    Interceptor.attach(target, {
      onEnter(args) {
        try {
          // The setters discovered below are generic UI writer methods shared
          // by many FUT screens.  Do not even decode a key unless the exact
          // record/badge wrapper is currently executing on this thread.
          // Global depth is checked first so hot generic setters on unrelated
          // screens return without even paying for a native thread-id lookup.
          if (kind === 'string' && recordUiWriterActiveDepth <= 0) return;
          if (kind === 'int' && badgeUiWriterActiveDepth <= 0) return;
          const threadKey = String(tid());
          if (kind === 'string' && (recordUiWriterDepthByThread[threadKey] || 0) <= 0) return;
          if (kind === 'int' && (badgeUiWriterDepthByThread[threadKey] || 0) <= 0) return;
          const sp = ptr(this.context.esp);
          if (kind === 'string') {
            const keyPtr = sp.add(4).readPointer();
            const field = cstring(keyPtr, 96);
            if (field !== 'MATCH_RECORD') return;
            const beforePtr = sp.add(8).readPointer();
            const before = beforePtr.isNull() ? '' : (cstring(beforePtr, 64) || '');
            if (localRecordOverrideReady && localRecordText !== null) {
              sp.add(8).writePointer(localRecordText);
            }
            emit('cards-ui-writer-match-record-beta222', {
              field:field, before:before,
              after:localRecordOverrideReady ? (String(localRecordOverride.wins)+'-'+String(localRecordOverride.draws)+'-'+String(localRecordOverride.losses)) : before,
              override_ready:localRecordOverrideReady, target:target.toString(),
              writer:safePtr(ptr(this.context.ecx)), thread_id:tid(), return_address:safePtr(this.returnAddress)
            });
          } else if (kind === 'int') {
            // Badge writers use (index/context, key, value): stack = ret,index,key,value.
            const keyPtr = sp.add(8).readPointer();
            const field = cstring(keyPtr, 96);
            if (field !== 'BADGE_ID') return;
            const before = sp.add(12).readU32() >>> 0;
            if (localBadgeOverrideReady && Number(localBadgeOverride.assetId) > 0) {
              sp.add(12).writeU32(Number(localBadgeOverride.assetId) >>> 0);
            }
            emit('cards-ui-writer-badge-id-beta222', {
              field:field, before:before,
              after:localBadgeOverrideReady ? Number(localBadgeOverride.assetId) >>> 0 : before,
              resource_id:localBadgeOverrideReady ? Number(localBadgeOverride.resourceId) >>> 0 : 0,
              override_ready:localBadgeOverrideReady, target:target.toString(),
              writer:safePtr(ptr(this.context.ecx)), thread_id:tid(), return_address:safePtr(this.returnAddress)
            });
          }
        } catch (error) {
          emit('cards-ui-writer-override-error-beta222', {kind:kind, target:target.toString(), error:String(error), thread_id:tid()});
        }
      }
    });
    uiWriterTargetsHooked[key] = true;
    emit('cards-ui-writer-target-hook-ready-beta222', {kind:kind, target:target.toString(), source:source});
    return true;
  } catch (error) {
    emit('cards-ui-writer-target-hook-error-beta222', {kind:kind, target:target.toString(), source:source, error:String(error)});
    return false;
  }
}

function discoverUiWriter(writer, source) {
  try {
    writer = ptr(writer);
    if (writer.isNull() || readable(writer) === null) return false;
    const vt = writer.readPointer();
    if (vt.isNull() || readable(vt) === null) return false;
    const intSetter = vt.add(0x0c).readPointer();
    const stringSetter = vt.add(0x10).readPointer();
    const discoveryKey = writer.toString() + ':' + vt.toString() + ':' + intSetter.toString() + ':' + stringSetter.toString();
    if (discoveryKey !== lastUiWriterDiscoveryKey) {
      lastUiWriterDiscoveryKey = discoveryKey;
      emit('cards-ui-writer-discovered-beta222', {
        source:source, writer:writer.toString(), vtable:vt.toString(),
        int_setter:safePtr(intSetter), string_setter:safePtr(stringSetter)
      });
    }
    hookUiWriterTarget(stringSetter, 'string', source);
    hookUiWriterTarget(intSetter, 'int', source);
    return true;
  } catch (error) {
    emit('cards-ui-writer-discovery-error-beta222', {source:source, error:String(error)});
    return false;
  }
}

function installMainHubRecordOverride(module, source) {
  if (mainHubRecordOverrideInstalled) return true;
  const wrapper = module.base.add(MAIN_HUB_RECORD_WRAPPER_RVA);
  const check = verify(wrapper, [0x55,0x8b,0xec]);
  emit('cards-main-hub-record-wrapper-signature-beta222', {source:source,address:wrapper.toString(),matched:check.matched,actual:check.actual,error:check.error||null});
  if (!check.matched || !executable(wrapper)) return false;
  try {
    Interceptor.attach(wrapper, {
      onEnter(args) {
        this.writerThread = tid();
        this.writerThreadKey = String(this.writerThread);
        recordUiWriterActiveDepth += 1;
        recordUiWriterDepthByThread[this.writerThreadKey] = (recordUiWriterDepthByThread[this.writerThreadKey] || 0) + 1;
        discoverUiWriter(args[0], 'main-hub-record-wrapper');
        if (!RUNTIME_PERFORMANCE_MODE) {
          emit('cards-main-hub-record-wrapper-enter-beta222', {writer:safePtr(args[0]),self:safePtr(ptr(this.context.ecx)),override_ready:localRecordOverrideReady,thread_id:this.writerThread});
        }
      },
      onLeave(retval) {
        if (this.writerThreadKey) {
          recordUiWriterDepthByThread[this.writerThreadKey] = Math.max(0, (recordUiWriterDepthByThread[this.writerThreadKey] || 1) - 1);
          recordUiWriterActiveDepth = Math.max(0, recordUiWriterActiveDepth - 1);
        }
      }
    });
    mainHubRecordOverrideInstalled = true;
    emit('cards-main-hub-record-wrapper-hook-ready-beta222', {source:source,address:wrapper.toString()});
    return true;
  } catch (error) {
    emit('cards-main-hub-record-wrapper-hook-error-beta222', {source:source,address:wrapper.toString(),error:String(error)});
    return false;
  }
}

function installBadgeUiOverride(module, source) {
  if (badgeUiOverrideInstalled) return true;
  let installed = 0;
  BADGE_UI_WRAPPER_RVAS.forEach(function(rva) {
    const wrapper = module.base.add(rva);
    if (!executable(wrapper)) return;
    try {
      Interceptor.attach(wrapper, {
        onEnter(args) {
          this.writerThread = tid();
          this.writerThreadKey = String(this.writerThread);
          badgeUiWriterActiveDepth += 1;
          badgeUiWriterDepthByThread[this.writerThreadKey] = (badgeUiWriterDepthByThread[this.writerThreadKey] || 0) + 1;
          discoverUiWriter(args[0], 'badge-wrapper-0x'+rva.toString(16));
          if (!RUNTIME_PERFORMANCE_MODE) {
            emit('cards-badge-ui-wrapper-enter-beta222', {rva:'0x'+rva.toString(16),writer:safePtr(args[0]),self:safePtr(ptr(this.context.ecx)),override_ready:localBadgeOverrideReady,thread_id:this.writerThread});
          }
        },
        onLeave(retval) {
          if (this.writerThreadKey) {
            badgeUiWriterDepthByThread[this.writerThreadKey] = Math.max(0, (badgeUiWriterDepthByThread[this.writerThreadKey] || 1) - 1);
            badgeUiWriterActiveDepth = Math.max(0, badgeUiWriterActiveDepth - 1);
          }
        }
      });
      installed += 1;
    } catch (error) {
      emit('cards-badge-ui-wrapper-hook-error-beta222', {rva:'0x'+rva.toString(16),address:wrapper.toString(),error:String(error)});
    }
  });
  badgeUiOverrideInstalled = installed > 0;
  emit('cards-badge-ui-wrapper-hooks-ready-beta222', {source:source,installed:installed});
  return badgeUiOverrideInstalled;
}

function installUserRecordTrace(module, source) {
  if (userRecordTraceInstalled) return true;
  const builder = module.base.add(USER_RECORD_BUILDER_RVA);
  const builderCheck = verify(builder, [0x55,0x8b,0xec,0x81,0xec,0xcc,0x00,0x00,0x00,0x53,0x56,0x57]);
  emit('cards-native-record-builder-signature-beta222', {
    source:source, address:builder.toString(), matched:builderCheck.matched,
    actual:builderCheck.actual, error:builderCheck.error || null
  });
  if (!builderCheck.matched || !executable(builder)) return false;

  // The three old PUSH-site hooks were useful diagnostics, but Interceptor
  // never observed them in BETA 2.19.  The record builder first copies the
  // manager's 0-0-0 record object through +0x72c10 and only then serializes
  // WINS/DRAWS/LOSSES.  Override that copied stack object at its exact native
  // field offsets so every later retail read sees the persistent local record.
  const copy = module.base.add(USER_RECORD_COPY_RVA);
  const copyCheck = verify(copy, [0x55,0x8b,0xec,0x56,0x8b,0xf1,0xc7,0x06]);
  emit('cards-native-record-copy-signature-beta222', {
    source:source, address:copy.toString(), matched:copyCheck.matched,
    actual:copyCheck.actual, error:copyCheck.error || null,
    fields:{wins:'+0x40',draws:'+0x44',losses:'+0x48'}
  });
  if (!copyCheck.matched || !executable(copy)) return false;

  try {
    Interceptor.attach(builder, {
      onEnter(args) {
        emit('cards-native-record-builder-enter-beta222', {
          override_ready:localRecordOverrideReady,
          override:localRecordOverrideReady ? localRecordOverride : null,
          thread_id:tid(), return_address:safePtr(this.returnAddress)
        });
      }
    });
  } catch (error) {
    emit('cards-native-record-builder-hook-error-beta222', {address:builder.toString(), error:String(error)});
  }

  try {
    Interceptor.attach(copy, {
      onEnter(args) {
        this.dest = ptr(this.context.ecx);
        this.sourceRecord = args[0];
        const expectedReturn = module.base.add(USER_RECORD_BUILDER_COPY_RETURN_RVA);
        let fromBuilder = false;
        try { fromBuilder = this.returnAddress.equals(expectedReturn); } catch (_) {}
        this.fromRecordBuilder = fromBuilder;
        if (fromBuilder) {
          const sourceBefore = {
            wins:readU32(this.sourceRecord,0x40),
            draws:readU32(this.sourceRecord,0x44),
            losses:readU32(this.sourceRecord,0x48)
          };
          emit('cards-native-record-copy-enter-beta222', {
            dest:safePtr(this.dest), source_record:safePtr(this.sourceRecord),
            source_wins:sourceBefore.wins,
            source_draws:sourceBefore.draws,
            source_losses:sourceBefore.losses,
            override_ready:localRecordOverrideReady,
            thread_id:tid(), return_address:safePtr(this.returnAddress)
          });

          // Patch the manager-owned source object too.  BETA 2.19 proved the
          // backend record persisted while the frontend continued to display
          // 0-0-0.  Updating only a transient serialized copy would not repair
          // any HUD code that re-reads the manager's record later in the same
          // FUT session.  This exact caller is the retail WINS/DRAWS/LOSSES
          // builder, so these three DWORDs are the narrowest safe persistent
          // native state bridge we have recovered.
          if (localRecordOverrideReady) {
            try {
              const sourceRange = Process.findRangeByAddress(this.sourceRecord);
              if (sourceRange !== null && sourceRange.protection.indexOf('w') >= 0) {
                this.sourceRecord.add(0x40).writeS32(Number(localRecordOverride.wins) | 0);
                this.sourceRecord.add(0x44).writeS32(Number(localRecordOverride.draws) | 0);
                this.sourceRecord.add(0x48).writeS32(Number(localRecordOverride.losses) | 0);
                emit('cards-native-record-source-override-beta222', {
                  source_record:safePtr(this.sourceRecord), before:sourceBefore,
                  after:{wins:Number(localRecordOverride.wins)|0,draws:Number(localRecordOverride.draws)|0,losses:Number(localRecordOverride.losses)|0},
                  thread_id:tid()
                });
              } else {
                emit('cards-native-record-source-override-error-beta222', {
                  source_record:safePtr(this.sourceRecord), reason:'source-not-writable',
                  protection:sourceRange === null ? null : sourceRange.protection
                });
              }
            } catch (error) {
              emit('cards-native-record-source-override-error-beta222', {source_record:safePtr(this.sourceRecord), error:String(error)});
            }
          }
        }
      },
      onLeave(retval) {
        if (!this.fromRecordBuilder || !localRecordOverrideReady) return;
        const dest = this.dest;
        try {
          const range = Process.findRangeByAddress(dest);
          if (range === null || range.protection.indexOf('w') < 0) {
            emit('cards-native-record-copy-override-error-beta222', {
              dest:safePtr(dest), reason:'destination-not-writable',
              protection:range === null ? null : range.protection
            });
            return;
          }
          const before = {
            wins:dest.add(0x40).readS32(),
            draws:dest.add(0x44).readS32(),
            losses:dest.add(0x48).readS32()
          };
          dest.add(0x40).writeS32(Number(localRecordOverride.wins) | 0);
          dest.add(0x44).writeS32(Number(localRecordOverride.draws) | 0);
          dest.add(0x48).writeS32(Number(localRecordOverride.losses) | 0);
          emit('cards-native-record-copy-override-beta222', {
            dest:safePtr(dest), before:before,
            after:{wins:Number(localRecordOverride.wins)|0,draws:Number(localRecordOverride.draws)|0,losses:Number(localRecordOverride.losses)|0},
            thread_id:tid()
          });
        } catch (error) {
          emit('cards-native-record-copy-override-error-beta222', {dest:safePtr(dest), error:String(error)});
        }
      }
    });
  } catch (error) {
    emit('cards-native-record-copy-hook-error-beta222', {address:copy.toString(), error:String(error)});
    return false;
  }

  userRecordTraceInstalled = true;
  emit('cards-native-record-trace-ready-beta222', {source:source, installed:1, strategy:'record-copy-object-override'});
  return true;
}


rpc.exports = {
  setrecord(wins, draws, losses) {
    localRecordOverride = {
      wins:Number(wins) | 0,
      draws:Number(draws) | 0,
      losses:Number(losses) | 0
    };
    localRecordText = Memory.allocUtf8String(
      String(localRecordOverride.wins) + '-' + String(localRecordOverride.draws) + '-' + String(localRecordOverride.losses)
    );
    localRecordOverrideReady = true;
    emit('cards-native-record-override-beta222', {
      wins:localRecordOverride.wins,
      draws:localRecordOverride.draws,
      losses:localRecordOverride.losses,
      text:String(localRecordOverride.wins) + '-' + String(localRecordOverride.draws) + '-' + String(localRecordOverride.losses)
    });
    return true;
  },
  setbadge(assetId, resourceId) {
    localBadgeOverride = {
      assetId:Number(assetId) | 0,
      resourceId:Number(resourceId) | 0
    };
    localBadgeOverrideReady = localBadgeOverride.assetId > 0;
    emit('cards-native-badge-override-beta222', {
      asset_id:localBadgeOverride.assetId,
      resource_id:localBadgeOverride.resourceId,
      ready:localBadgeOverrideReady
    });
    return true;
  }
};

function installTournamentNameFallback(module, source) {
  if (tournamentInfoActionTraceInstalled) return true;
  const wrapper = module.base.add(GET_OFFLINE_TOURNAMENT_INFO_RVA);
  const check = verify(wrapper, [0x56,0x57,0xe8]);
  emit('cards-get-offline-tournament-info-signature-beta222', {source:source,address:wrapper.toString(),matched:check.matched,actual:check.actual,error:check.error||null});
  if (!check.matched || !executable(wrapper)) return false;
  try {
    Interceptor.attach(wrapper, {
      onEnter(args) {
        this.ecx = ptr(this.context.ecx);
        emit('cards-get-offline-tournament-info-enter-beta222', {
          self:pointerProbe(this.ecx,0x80), args:stackArgs(this.context,8),
          thread_id:tid(), return_address:safePtr(this.returnAddress), backtrace:normalizedBacktrace(this.context)
        });
      },
      onLeave(retval) {
        emit('cards-get-offline-tournament-info-leave-beta222', {
          retval:retval.toString(), retval_i32:retval.toInt32(),
          retval_probe:readable(retval)!==null ? pointerProbe(retval,0x100) : null,
          thread_id:tid()
        });
      }
    });
    tournamentInfoActionTraceInstalled = true;
    tournamentNameFallbackInstalled = true;
    emit('cards-get-offline-tournament-info-hook-ready-beta222', {source:source,address:wrapper.toString(),names:LOCAL_TOURNAMENT_NAMES});
    return true;
  } catch (error) {
    emit('cards-get-offline-tournament-info-hook-error-beta222', {source:source,address:wrapper.toString(),error:String(error)});
    return false;
  }
}

function installTournamentSessionTrace(module, source) {
  if (tournamentSessionTraceInstalled) return true;
  const specs = [
    {name:'CreateMatchFrontend', rva:CREATE_MATCH_FRONTEND_WRAPPER_RVA,
     signature:[0xa1,0xb0,0x73,0x1d,0x10,0x56,0x8b,0x30,0x6a,0x01]},
    {name:'MatchReadyFrontend', rva:MATCH_READY_FRONTEND_WRAPPER_RVA,
     signature:[0xa1,0xb0,0x73,0x1d,0x10,0x56,0x8b,0x30,0x6a,0x01]},
    {name:'ServiceCreateSession', rva:SERVICE_CREATE_SESSION_WRAPPER_RVA,
     signature:[0x56,0xe8,0x0a,0xc4,0xf8,0xff,0x8b,0x10,0x8b,0xc8,0x8b,0x42,0x44,0xff,0xd0]},
  ];
  let installed = 0;
  specs.forEach(function(spec) {
    const target = module.base.add(spec.rva);
    const check = verify(target, spec.signature);
    emit('cards-tournament-session-handler-signature-beta222', {
      source:source, name:spec.name, address:target.toString(), matched:check.matched,
      actual:check.actual, error:check.error || null
    });
    if (!check.matched || !executable(target)) return;
    try {
      Interceptor.attach(target, {
        onEnter(args) {
          this.name = spec.name;
          emit('cards-tournament-session-handler-enter-beta222', {
            source:source, name:spec.name, address:target.toString(), thread_id:tid(),
            ecx: pointerProbe(ptr(this.context.ecx), 0x80),
            stack_args: stackArgs(this.context, 8), return_address:safePtr(this.returnAddress),
            backtrace: normalizedBacktrace(this.context)
          });
        },
        onLeave(retval) {
          emit('cards-tournament-session-handler-leave-beta222', {
            name:this.name, thread_id:tid(), retval:retval.toString(),
            retval_i32:retval.toInt32(), retval_probe:readable(retval) ? pointerProbe(retval,0x80) : null
          });
        }
      });
      installed += 1;
    } catch (error) {
      emit('cards-tournament-session-handler-hook-error-beta222', {name:spec.name, address:target.toString(), error:String(error)});
    }
  });

  const parser = module.base.add(CREATE_MATCH_RESPONSE_PARSER_RVA);
  const parserCheck = verify(parser, [0x55,0x8b,0xec,0x81,0xec,0xbc,0x00,0x00,0x00,0x56,0x57,0x6a,0x00,0x8b,0xf9,0x6a,0x00]);
  emit('cards-create-match-response-parser-signature-beta222', {
    source:source, address:parser.toString(), matched:parserCheck.matched,
    actual:parserCheck.actual, error:parserCheck.error || null
  });
  if (parserCheck.matched && executable(parser)) {
    try {
      Interceptor.attach(parser, {
        onEnter(args) {
          this.obj = ptr(this.context.ecx);
          emit('cards-create-match-response-parser-enter-beta222', {
            thread_id:tid(), object:safePtr(this.obj), object_before:pointerProbe(this.obj,0x40),
            input:stackArgs(this.context,3), return_address:safePtr(this.returnAddress),
            backtrace:normalizedBacktrace(this.context)
          });
        },
        onLeave(retval) {
          let low = null, high = null;
          try { low = this.obj.add(0x20).readU32(); high = this.obj.add(0x24).readU32(); } catch (_) {}
          emit('cards-create-match-response-parser-leave-beta222', {
            thread_id:tid(), retval:retval.toString(), retval_i32:retval.toInt32(), object:safePtr(this.obj),
            object_after:pointerProbe(this.obj,0x40), startDateTimeLow:low, startDateTimeHigh:high
          });
        }
      });
      installed += 1;
    } catch (error) {
      emit('cards-create-match-response-parser-hook-error-beta222', {address:parser.toString(), error:String(error)});
    }
  }
  tournamentSessionTraceInstalled = installed > 0;
  emit('cards-tournament-session-trace-ready-beta222', {source:source, installed:installed});
  return tournamentSessionTraceInstalled;
}

function attachCardsHooksOnce(reason) {
  if (cardsHooksInstalled) return true;
  let module = null;
  try { module = Process.getModuleByName('CardsDLLzf.dll'); } catch (_) {}
  if (module === null) {
    // Some FIFA/DiME builds expose the mapped image without registering a normal
    // Windows module entry.  Try the DLL's verified preferred image base once;
    // this is neither module enumeration nor polling.
    try {
      const preferred = ptr('0x10000000');
      if (preferred.readU16() === 0x5a4d && readable(preferred) !== null) {
        module = {name: 'CardsDLLzf.dll (preferred-base mapping)', path: null, base: preferred, size: CARDS_EXPECTED_IMAGE_SIZE};
        emit('cards-operation92-preferred-base-fallback', {reason: reason, base: preferred.toString(), size: CARDS_EXPECTED_IMAGE_SIZE});
      }
    } catch (_) {}
  }
  if (module === null) {
    emit('cards-operation92-module-not-found', {reason: reason, requested_name: 'CardsDLLzf.dll', preferred_base_checked: '0x10000000', thread_id: tid()});
    return false;
  }
  cardsBase = module.base;
  emit('cards-operation92-module-found', {reason: reason, name: module.name, path: module.path, base: module.base.toString(), size: module.size, expected_size: CARDS_EXPECTED_IMAGE_SIZE, size_ok: module.size === CARDS_EXPECTED_IMAGE_SIZE});

  installCrashExceptionTrace();
  installNativeOfflineStadiumHooks(module, reason);
  installViewCardsEmptyListGuard(module, reason);
  installActivateCardTrace(module, reason);
  if (!RUNTIME_PERFORMANCE_MODE) installMatchBridgeTrace(module, reason);
  installMainHubRecordOverride(module, reason);
  installBadgeUiOverride(module, reason);
  installUserRecordTrace(module, reason);
  installTournamentNameFallback(module, reason);
  installTournamentSessionTrace(module, reason);

  const descriptor = module.base.add(OP92_DESCRIPTOR_RVA);
  emit('cards-operation92-descriptor', {
    address: descriptor.toString(),
    raw: hexBytes(raw(descriptor, 0x18)),
    status_dword_0: readU32(descriptor, 0),
    handler_4: safePtr((function(){try{return descriptor.add(4).readPointer();}catch(_){return ptr(0);}})()),
    operation_name_8: (function(){try{return cstring(descriptor.add(8).readPointer(),128);}catch(_){return null;}})(),
    category_c: readU32(descriptor, 0x0c),
    config_suffix_10: (function(){try{return cstring(descriptor.add(0x10).readPointer(),128);}catch(_){return null;}})(),
    resolved_base_14: safePtr((function(){try{return descriptor.add(0x14).readPointer();}catch(_){return ptr(0);}})())
  });

  dynamicMessagesSnapshot(module, 'LoginToFUT module resolution');
  // V26 deliberately does not attach the generic service-vtable/operation
  // hooks. V25 proved those hooks amplified BuildSquad from a normal local
  // operation into a ~54-second stall by snapshotting/backtracing every player
  // insertion twice. Keep only the exact authentication, security, pack-parser
  // and top-level Icebreaker wrapper hooks required to prove flow progress.
  emit('cards-heavy-operation-tracing-disabled-v26', {
    reason: 'preserve retail BuildSquad timing; BETA 2.22 adds focused operation-45 ActivateCard diagnostics plus the existing lightweight match bridge'
  });

  const byName = Object.create(null);
  CARDS_TARGETS.forEach(item => { byName[item.name] = item; });

  if (localEaswSessionPointer === null) {
    localEaswSessionPointer = Memory.alloc(64);
    localEaswSessionPointer.writeUtf8String('LOCAL-FIFA14-EASW-SESSION');
    localEaswTokenPointer = Memory.alloc(64);
    localEaswTokenPointer.writeUtf8String('LOCAL-FIFA14-EASW-TOKEN');
    emit('cards-authenticated-icebreaker-local-credentials-allocated', {
      session_pointer: localEaswSessionPointer.toString(),
      session_value: cstring(localEaswSessionPointer, 64),
      token_pointer: localEaswTokenPointer.toString(),
      token_value: cstring(localEaswTokenPointer, 64)
    });
  }

  installCardsHook(module, byName['Authentication WebService constructor'], {
    onEnter(args) {
      this.wrapper = ptr(this.context.ecx);
      authWrapperFromConstructor = this.wrapper;
      emit('cards-authenticated-icebreaker-wrapper-constructor-enter', {thread_id: tid(), wrapper: pointerProbe(this.wrapper, 0x30), return_address: safePtr(this.returnAddress), backtrace: normalizedBacktrace(this.context)});
    },
    onLeave(retval) {
      emit('cards-authenticated-icebreaker-wrapper-constructor-leave', {thread_id: tid(), wrapper: pointerProbe(this.wrapper, 0x30), retval: retval.toString()});
    }
  });
  installCardsHook(module, byName['Authentication WebService initialize'], {
    onEnter(args) {
      this.wrapper = ptr(this.context.ecx);
      authWrapperFromInitialize = this.wrapper;
      emit('cards-authenticated-icebreaker-wrapper-initialize-enter', {thread_id: tid(), wrapper: pointerProbe(this.wrapper, 0x30), return_address: safePtr(this.returnAddress), backtrace: normalizedBacktrace(this.context)});
    },
    onLeave(retval) {
      const verified = validateAuthWrapper(module, this.wrapper, 'initialize leave');
      emit('cards-authenticated-icebreaker-wrapper-initialize-leave', {
        thread_id: tid(), wrapper: pointerProbe(this.wrapper, 0x30), retval: retval.toString(),
        verified: verified !== null,
        request: verified === null ? null : pointerProbe(verified.request, 0x240)
      });
    }
  });
  installCardsHook(module, byName['Authentication request start'], {
    onEnter(args) {
      this.request = ptr(this.context.ecx);
      emit('cards-authenticated-icebreaker-request-start-enter', {
        thread_id: tid(), request: pointerProbe(this.request, 0x240),
        return_address: safePtr(this.returnAddress), descriptor: authDescriptorSnapshot(module, 'request-start enter'),
        backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      emit('cards-authenticated-icebreaker-request-start-leave', {thread_id: tid(), request: safePtr(this.request), retval_u8: retval.toUInt32() & 0xff, descriptor: authDescriptorSnapshot(module, 'request-start leave')});
    }
  });
  installCardsHook(module, byName['Authentication JSON builder'], {
    onEnter(args) {
      authBuilderCalls += 1;
      this.callNumber = authBuilderCalls;
      this.builderThread = tid();
      const key = String(this.builderThread);
      authBuilderDepthByThread[key] = (authBuilderDepthByThread[key] || 0) + 1;
      this.output = args[0];
      this.capacity = args[1].toUInt32();
      this.arg2 = args[2].toUInt32();
      this.arg3 = args[3].toUInt32();
      emit('cards-authenticated-icebreaker-json-builder-enter', {
        thread_id: this.builderThread, call_number: this.callNumber, output: safePtr(this.output), capacity: this.capacity,
        arg2: this.arg2, arg3: this.arg3, builder_depth: authBuilderDepthByThread[key],
        return_address: safePtr(this.returnAddress), backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      const success = (retval.toUInt32() & 0xff) !== 0;
      const key = String(this.builderThread);
      emit('cards-authenticated-icebreaker-json-builder-leave', {
        thread_id: this.builderThread, call_number: this.callNumber, success: success, retval_u8: retval.toUInt32() & 0xff,
        capacity: this.capacity, session_fixups_total: easwSessionFixups, token_fixups_total: easwTokenFixups,
        body_text: success ? cstring(this.output, Math.min(this.capacity, 4096)) : null,
        body_hex: success ? hexBytes(raw(this.output, Math.min(this.capacity, 1200))) : null
      });
      authBuilderDepthByThread[key] = Math.max(0, (authBuilderDepthByThread[key] || 1) - 1);
    }
  });

  installCardsHook(module, byName['Authentication EASW-Session null check'], {
    onEnter(args) {
      const thread = tid();
      const key = String(thread);
      const depth = authBuilderDepthByThread[key] || 0;
      const original = ptr(this.context.esi);
      let applied = false;
      if (depth > 0 && original.isNull() && localEaswSessionPointer !== null) {
        this.context.esi = localEaswSessionPointer;
        easwSessionFixups += 1;
        applied = true;
      }
      emit('cards-authenticated-icebreaker-session-null-gate', {
        thread_id: thread, builder_depth: depth, original_esi: original.toString(),
        applied: applied, replacement: applied ? localEaswSessionPointer.toString() : null,
        replacement_value: applied ? cstring(localEaswSessionPointer, 64) : null,
        fixups_total: easwSessionFixups
      });
    }
  });

  installCardsHook(module, byName['Authentication EASW-Token null check'], {
    onEnter(args) {
      const thread = tid();
      const key = String(thread);
      const depth = authBuilderDepthByThread[key] || 0;
      const original = ptr(this.context.esi);
      let applied = false;
      if (depth > 0 && original.isNull() && localEaswTokenPointer !== null) {
        this.context.esi = localEaswTokenPointer;
        easwTokenFixups += 1;
        applied = true;
      }
      emit('cards-authenticated-icebreaker-token-null-gate', {
        thread_id: thread, builder_depth: depth, original_esi: original.toString(),
        applied: applied, replacement: applied ? localEaswTokenPointer.toString() : null,
        replacement_value: applied ? cstring(localEaswTokenPointer, 64) : null,
        fixups_total: easwTokenFixups
      });
    }
  });

  installCardsHook(module, byName['Authentication request submit'], {
    onEnter(args) {
      authSubmitCalls += 1;
      this.request = ptr(this.context.ecx);
      this.body = args[0];
      emit('cards-authenticated-icebreaker-request-submit-enter', {
        thread_id: tid(), call_number: authSubmitCalls,
        request: pointerProbe(this.request, 0x240), body_pointer: safePtr(this.body),
        body_text: cstring(this.body, 4096), body_hex: hexBytes(raw(this.body, 1200)),
        descriptor: authDescriptorSnapshot(module, 'request-submit enter'),
        return_address: safePtr(this.returnAddress), backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      emit('cards-authenticated-icebreaker-request-submit-leave', {
        thread_id: tid(), call_number: authSubmitCalls, retval: retval.toString(),
        descriptor: authDescriptorSnapshot(module, 'request-submit leave')
      });
    }
  });
  installCardsHook(module, byName['Authentication SID accessor'], {
    onEnter(args) { this.request = ptr(this.context.ecx); },
    onLeave(retval) {
      emit('cards-authenticated-icebreaker-sid-accessor', {
        thread_id: tid(), request: safePtr(this.request), sid_pointer: retval.toString(),
        sid: cstring(retval, 256), request_sid_field_1ac: (function(){try{return this.request.add(0x1ac).readPointer().toString();}catch(_){return null;}})()
      });
    }
  });

  installCardsHook(module, byName['Authentication response constructor'], {
    onEnter(args) {
      this.response = ptr(this.context.ecx);
      emit('cards-authenticated-icebreaker-response-constructor-enter', {
        thread_id: tid(), response: pointerProbe(this.response, 0x120),
        return_address: safePtr(this.returnAddress), backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      emit('cards-authenticated-icebreaker-response-constructor-leave', {
        thread_id: tid(), response: pointerProbe(this.response, 0x120), retval: retval.toString()
      });
    }
  });
  if (!RUNTIME_PERFORMANCE_MODE) {
  installCardsHook(module, byName['Authentication response parser'], {
      onEnter(args) {
        this.response = ptr(this.context.ecx);
        this.arg0 = args[0]; this.arg1 = args[1]; this.arg2 = args[2]; this.arg3 = args[3]; this.arg4 = args[4];
        emit('cards-authenticated-icebreaker-response-parser-enter', {
          thread_id: tid(), response: pointerProbe(this.response, 0x120),
          args: stackArgs(this.context, 6),
          arg0_text: cstring(this.arg0, 4096), arg0_hex: hexBytes(raw(this.arg0, 1024)),
          return_address: safePtr(this.returnAddress), backtrace: normalizedBacktrace(this.context)
        });
      },
      onLeave(retval) {
        emit('cards-authenticated-icebreaker-response-parser-leave', {
          thread_id: tid(), response: pointerProbe(this.response, 0x120),
          retval: retval.toString(), retval_i32: retval.toInt32(),
          sid_buffer_8: cstring(this.response.add(8), 256),
          callback_108: (function(){try{return this.response.add(0x108).readPointer().toString();}catch(_){return null;}})(),
          callback_mode_10c: readU8(this.response, 0x10c)
        });
      }
    });
    installCardsHook(module, byName['Authentication response scalar callback'], {
      onEnter(args) {
        this.response = ptr(this.context.ecx);
        this.value = args[0];
        this.valueText = cstring(this.value, 16384);
        if (this.valueText !== null && !competitionTraceActive()) {
          // Response-body detection is retained only as a fallback. Normal BETA
          // 2.5 arming happens on the outgoing request early enough to capture
          // the actual season-list/tournament-list parser keys.
          if (this.valueText.indexOf('"seasons"') >= 0 || this.valueText.indexOf('"seasonId"') >= 0) {
            armCompetitionTrace('season-response-fallback', 'generic FUT scalar response contains Seasons keys', 10000);
          } else if (this.valueText.indexOf('"tournament"') >= 0 || this.valueText.indexOf('"tournamentId"') >= 0) {
            armCompetitionTrace('tournament-response-fallback', 'generic FUT scalar response contains Tournament keys', 10000);
          }
        }
        emit('cards-authenticated-icebreaker-response-scalar-enter', {
          thread_id: tid(), response: pointerProbe(this.response, 0x120),
          value_pointer: safePtr(this.value), value_text: this.valueText,
          callback_108: (function(){try{return this.response.add(0x108).readPointer().toString();}catch(_){return null;}})(),
          return_address: safePtr(this.returnAddress), backtrace: normalizedBacktrace(this.context)
        });
      },
      onLeave(retval) {
        emit('cards-authenticated-icebreaker-response-scalar-leave', {
          thread_id: tid(), response: safePtr(this.response), retval: retval.toString(), sid_buffer_8: cstring(this.response.add(8), 256)
        });
      }
    });
    installCardsHook(module, byName['Authentication response key mapper'], {
      onEnter(args) {
        this.keyPointer = args[0];
        this.key = cstring(this.keyPointer, 128);
      },
      onLeave(retval) {
        emit('cards-authenticated-icebreaker-response-key-map', {
          thread_id: tid(), key_pointer: safePtr(this.keyPointer), key: this.key,
          mapped_index: retval.toInt32(), mapped_index_u32: retval.toUInt32()
        });
      }
    });
  
    } else {
    emit('cards-generic-response-tracing-disabled-beta2244', {
      reason:'The shared FUT response parser/scalar callbacks execute for Store/New Items responses; pack runtime keeps outgoing competition arming instead.'
    });
  }

  installCardsHook(module, byName['Operation 23 descriptor status setter'], {
    onEnter(args) {
      this.value = args[0].toUInt32() & 0xff;
      emit('cards-authenticated-icebreaker-descriptor-status-set', {thread_id: tid(), value: this.value, before: authDescriptorSnapshot(module, 'op23 status setter enter'), backtrace: normalizedBacktrace(this.context)});
    },
    onLeave(retval) {
      emit('cards-authenticated-icebreaker-descriptor-status-after', {thread_id: tid(), value: this.value, after: authDescriptorSnapshot(module, 'op23 status setter leave')});
    }
  });

  installCardsHook(module, byName['PlugInitialize_'], {
    onEnter(args) { this.args = stackArgs(this.context, 8); emit('cards-plug-initialize-enter', {thread_id: tid(), ecx: safePtr(this.context.ecx), args: this.args}); },
    onLeave(retval) { emit('cards-plug-initialize-leave', {thread_id: tid(), retval: retval.toInt32(), retval_pointer: retval.toString()}); }
  });
  installCardsHook(module, byName['PlugDeinitialize_'], {
    onEnter(args) {
      this.thread = tid();
      emit('cards-plug-deinitialize-enter', {
        thread_id: this.thread, ecx: safePtr(this.context.ecx), args: stackArgs(this.context, 8),
        return_address: safePtr(this.returnAddress), backtrace: normalizedBacktrace(this.context),
        websession: webSessionSnapshot(module, 'PlugDeinitialize enter'),
        dynamic_messages: dynamicMessagesSnapshot(module, 'PlugDeinitialize enter'),
        state: cardsServiceStateSnapshot(module, 'PlugDeinitialize enter')
      });
    },
    onLeave(retval) {
      emit('cards-plug-deinitialize-leave', {
        thread_id: this.thread, retval: retval.toInt32(),
        state_after: cardsServiceStateSnapshot(module, 'PlugDeinitialize leave')
      });
    }
  });

  installCardsHook(module, byName['GetPhishingQuestion launch'], {
    onEnter(args) {
      beginTrustedWindow('GetPhishingQuestion launch');
      this.self = ptr(this.context.ecx);
      this.action = cstring(args[0], 256);
      emit('cards-operation89-launch-enter', {
        thread_id: tid(), return_address: safePtr(this.returnAddress), ecx: safePtr(this.self),
        action_name: this.action, args: stackArgs(this.context, 6),
        self_bytes: hexBytes(raw(this.self, 0x60)), self_pointer_fields: pointerFields(this.self, 0x60),
        websession: webSessionSnapshot(module, 'operation89-launch'), backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) { emit('cards-operation89-launch-leave', {thread_id: tid(), action_name: this.action, retval: retval.toString()}); }
  });
  installCardsHook(module, byName['RetrievePhishingQuestion callback'], {
    onEnter(args) {
      this.response = args[0];
      this.originalStatus = readU32(this.response, 0x18);
      this.originalRawError = readU32(this.response, 0x1c);
      let actionName = null;
      try { actionName = cstring(ptr(this.context.ecx).add(0x1c).readPointer(), 256); } catch (_) {}
      if (this.originalStatus === 999 && this.originalRawError === 0xfffffffe) {
        this.response.add(0x18).writeU32(0);
        this.response.add(0x1c).writeU32(0);
        this.response.add(0x20).writeU32(0);
        this.response.add(0x24).writeU32(5);
        this.response.add(0x28).writeU32(20);
        controlledQuestionOverrideApplied = true;
        beginTrustedWindow('controlled operation89 local-failure normalization');
        emit('cards-operation89-controlled-success-override', {
          thread_id: tid(), action_name: actionName,
          original_status_18: this.originalStatus, original_raw_error_1c: this.originalRawError,
          normalized_status_18: readU32(this.response, 0x18), normalized_raw_error_1c: readU32(this.response, 0x1c),
          question_20: readU32(this.response, 0x20), attempts_24: readU32(this.response, 0x24), recover_attempts_28: readU32(this.response, 0x28),
          rationale: 'Operation 89 followed operation 92 into the same local -2/999 WebSession failure. Normalize only this exact response to question 0 with five attempts and twenty recovery attempts.'
        });
      }
      this.status = readU32(this.response, 0x18);
      emit('cards-operation89-callback-enter', {
        thread_id: tid(), return_address: safePtr(this.returnAddress), callback_this: safePtr(this.context.ecx),
        action_name: actionName, original_status_18: this.originalStatus, original_raw_error_1c: this.originalRawError,
        status_18: this.status, question_20: readU32(this.response, 0x20), attempts_24: readU32(this.response, 0x24), recover_attempts_28: readU32(this.response, 0x28),
        response: responseSnapshot(this.response, 'operation89-callback-enter'), backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      emit('cards-operation89-callback-leave', {
        thread_id: tid(), status_18: this.status, retval: retval.toString(),
        question_20: readU32(this.response, 0x20), attempts_24: readU32(this.response, 0x24), recover_attempts_28: readU32(this.response, 0x28),
        response: responseSnapshot(this.response, 'operation89-callback-leave')
      });
    }
  });
  installCardsHook(module, byName['Phishing-question response allocator'], {
    onEnter(args) { emit('cards-operation89-response-allocator-enter', {thread_id: tid(), ecx: safePtr(this.context.ecx)}); },
    onLeave(retval) { emit('cards-operation89-response-allocator-leave', {thread_id: tid(), retval: retval.toString(), response: responseSnapshot(retval, 'operation89-allocator-leave')}); }
  });
  installCardsHook(module, byName['Phishing-question response parser'], {
    onEnter(args) { this.response = ptr(this.context.ecx); emit('cards-operation89-parser-enter', {thread_id: tid(), response: responseSnapshot(this.response, 'operation89-parser-enter'), input: pointerProbe(args[0], 0x80)}); },
    onLeave(retval) { emit('cards-operation89-parser-leave', {thread_id: tid(), retval: retval.toUInt32(), question_20: readU32(this.response, 0x20), attempts_24: readU32(this.response, 0x24), recover_attempts_28: readU32(this.response, 0x28), response: responseSnapshot(this.response, 'operation89-parser-leave')}); }
  });
  installCardsHook(module, byName['Phishing-question URL builder'], {
    onEnter(args) { this.destination = args[0]; emit('cards-operation89-url-builder-enter', {thread_id: tid(), destination: pointerProbe(this.destination, 0x100), args: stackArgs(this.context, 3)}); },
    onLeave(retval) { emit('cards-operation89-url-builder-leave', {thread_id: tid(), retval: retval.toString(), destination: pointerProbe(this.destination, 0x180), built_path: cstring(this.destination, 0x180)}); }
  });
  installCardsHook(module, byName['Operation 89 descriptor status setter'], {
    onEnter(args) { const descriptor = module.base.add(OP89_DESCRIPTOR_RVA); emit('cards-operation89-descriptor-status-set', {thread_id: tid(), value: args[0].toUInt32() & 0xff, before: hexBytes(raw(descriptor, 0x18))}); },
    onLeave(retval) { const descriptor = module.base.add(OP89_DESCRIPTOR_RVA); emit('cards-operation89-descriptor-status-after', {thread_id: tid(), after: hexBytes(raw(descriptor, 0x18))}); }
  });

  installCardsHook(module, byName['ValidatePhishingAnswer launch'], {
    onEnter(args) {
      beginTrustedWindow('ValidatePhishingAnswer launch');
      this.self = ptr(this.context.ecx);
      this.answer = cstring(args[0], 256);
      this.action = cstring(args[6], 256);
      emit('cards-operation91-launch-enter', {
        thread_id: tid(), return_address: safePtr(this.returnAddress), ecx: safePtr(this.self),
        answer: this.answer, action_name: this.action, args: stackArgs(this.context, 12),
        self_bytes: hexBytes(raw(this.self, 0x70)), self_pointer_fields: pointerFields(this.self, 0x70),
        websession: webSessionSnapshot(module, 'operation91-launch'), backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) { emit('cards-operation91-launch-leave', {thread_id: tid(), action_name: this.action, retval: retval.toString()}); }
  });
  installCardsHook(module, byName['ValidatePhishingAnswer callback'], {
    onEnter(args) {
      this.response = args[0];
      this.originalStatus = readU32(this.response, 0x18);
      this.originalRawError = readU32(this.response, 0x1c);
      let actionName = null;
      try { actionName = cstring(ptr(this.context.ecx).add(0x4c).readPointer(), 256); } catch (_) {}
      if (this.originalStatus === 999 && this.originalRawError === 0xfffffffe) {
        this.response.add(0x18).writeU32(0);
        this.response.add(0x1c).writeU32(0);
        controlledValidationOverrideApplied = true;
        beginTrustedWindow('controlled operation91 local-failure normalization');
        emit('cards-operation91-controlled-success-override', {
          thread_id: tid(), action_name: actionName,
          original_status_18: this.originalStatus, original_raw_error_1c: this.originalRawError,
          normalized_status_18: readU32(this.response, 0x18), normalized_raw_error_1c: readU32(this.response, 0x1c),
          rationale: 'Operation 91 used the same local -2/999 failure path. Normalize only this exact validation response to SUCCESS so the retail frontend can continue.'
        });
      }
      this.status = readU32(this.response, 0x18);
      emit('cards-operation91-callback-enter', {
        thread_id: tid(), return_address: safePtr(this.returnAddress), callback_this: safePtr(this.context.ecx),
        action_name: actionName, original_status_18: this.originalStatus, original_raw_error_1c: this.originalRawError,
        status_18: this.status, predicted_branch: this.status === 0 ? 'SUCCESS' : 'FAIL',
        response: responseSnapshot(this.response, 'operation91-callback-enter'), backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      emit('cards-operation91-callback-leave', {thread_id: tid(), status_18: this.status, retval: retval.toString(), response: responseSnapshot(this.response, 'operation91-callback-leave')});
      if (this.status === 0) dynamicMessagesSnapshot(module, 'operation91 success callback');
    }
  });
  installCardsHook(module, byName['Phishing-validation response allocator'], {
    onEnter(args) { emit('cards-operation91-response-allocator-enter', {thread_id: tid(), ecx: safePtr(this.context.ecx)}); },
    onLeave(retval) { emit('cards-operation91-response-allocator-leave', {thread_id: tid(), retval: retval.toString(), response: responseSnapshot(retval, 'operation91-allocator-leave')}); }
  });
  installCardsHook(module, byName['Phishing-validation response parser'], {
    onEnter(args) { this.response = ptr(this.context.ecx); emit('cards-operation91-parser-enter', {thread_id: tid(), response: responseSnapshot(this.response, 'operation91-parser-enter'), input: pointerProbe(args[0], 0x80)}); },
    onLeave(retval) { emit('cards-operation91-parser-leave', {thread_id: tid(), retval: retval.toUInt32(), response: responseSnapshot(this.response, 'operation91-parser-leave')}); }
  });
  installCardsHook(module, byName['Phishing-validation URL builder'], {
    onEnter(args) { this.destination = args[0]; emit('cards-operation91-url-builder-enter', {thread_id: tid(), destination: pointerProbe(this.destination, 0x100), args: stackArgs(this.context, 3)}); },
    onLeave(retval) { emit('cards-operation91-url-builder-leave', {thread_id: tid(), retval: retval.toString(), destination: pointerProbe(this.destination, 0x180), built_path: cstring(this.destination, 0x180)}); }
  });
  installCardsHook(module, byName['Operation 91 descriptor status setter'], {
    onEnter(args) { const descriptor = module.base.add(OP91_DESCRIPTOR_RVA); emit('cards-operation91-descriptor-status-set', {thread_id: tid(), value: args[0].toUInt32() & 0xff, before: hexBytes(raw(descriptor, 0x18))}); },
    onLeave(retval) { const descriptor = module.base.add(OP91_DESCRIPTOR_RVA); emit('cards-operation91-descriptor-status-after', {thread_id: tid(), after: hexBytes(raw(descriptor, 0x18))}); }
  });

  installCardsHook(module, byName['GetTrustedConsoleList launch'], {
    onEnter(args) {
      beginTrustedWindow('GetTrustedConsoleList launch');
      this.self = ptr(this.context.ecx);
      this.action = cstring(args[0], 256);
      emit('cards-operation92-launch-enter', {
        thread_id: tid(), return_address: safePtr(this.returnAddress), ecx: safePtr(this.self),
        action_name: this.action, args: stackArgs(this.context, 6),
        self_bytes: hexBytes(raw(this.self, 0xa0)), self_pointer_fields: pointerFields(this.self, 0xa0),
        websession: webSessionSnapshot(module, 'operation92-launch'), backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) { emit('cards-operation92-launch-leave', {thread_id: tid(), action_name: this.action, retval: retval.toString()}); }
  });
  installCardsHook(module, byName['GetTrustedConsoleList dispatch checkpoint'], {
    onEnter(args) {
      const sp = ptr(this.context.esp);
      let request = ptr(0), callback = ptr(0);
      try { request = sp.readPointer(); } catch (_) {}
      try { callback = sp.add(4).readPointer(); } catch (_) {}
      emit('cards-operation92-dispatch', {
        thread_id: tid(), service_ecx: safePtr(this.context.ecx), method_edx: safePtr(this.context.edx),
        request_wrapper: pointerProbe(request, 0x80), request_pointer_fields: pointerFields(request, 0x100),
        callback_wrapper: pointerProbe(callback, 0x80), callback_pointer_fields: pointerFields(callback, 0x80)
      });
    }
  });
  installCardsHook(module, byName['RetrieveTrustedConsoleList callback'], {
    onEnter(args) {
      this.response = args[0];
      this.originalStatus = readU32(this.response, 0x18);
      this.originalRawError = readU32(this.response, 0x1c);
      let actionName = null;
      try { actionName = cstring(ptr(this.context.ecx).add(0x64).readPointer(), 256); } catch (_) {}
      if (this.originalStatus === 999 && this.originalRawError === 0xfffffffe) {
        this.response.add(0x18).writeU32(0);
        this.response.add(0x1c).writeU32(0);
        // Exact field mapping recovered from the retail parser/key table:
        // +0x20 trusted, +0x21 changed, +0x22 exists, +0x23 locked.
        // Model an existing, unchanged, unlocked security profile on a trusted PC.
        this.response.add(0x20).writeU8(1); // trusted
        this.response.add(0x21).writeU8(0); // changed
        this.response.add(0x22).writeU8(1); // exists
        this.response.add(0x23).writeU8(0); // locked
        controlledOverrideApplied = true;
        beginTrustedWindow('controlled operation92 pre-trusted-device normalization');
        emit('cards-operation92-pretrusted-success-override', {
          thread_id: tid(), action_name: actionName,
          original_status_18: this.originalStatus, original_raw_error_1c: this.originalRawError,
          normalized_status_18: readU32(this.response, 0x18), normalized_raw_error_1c: readU32(this.response, 0x1c),
          flags: {trusted: 1, changed: 0, exists: 1, locked: 0},
          expected_frontend_payloads: ['SUCCESS', 'TRUE', 'FALSE', 'TRUE', 'FALSE'],
          rationale: 'Operation 92 built a valid trusteddevice URI but failed locally with -2 before parser/network. Normalize only this exact response to the retail pre-trusted, unchanged, existing, unlocked device state.'
        });
      }
      this.status = readU32(this.response, 0x18);
      emit('cards-operation92-callback-enter', {
        thread_id: tid(), return_address: safePtr(this.returnAddress), callback_this: safePtr(this.context.ecx),
        action_name: actionName, original_status_18: this.originalStatus, original_raw_error_1c: this.originalRawError,
        predicted_branch: this.status === 0 ? 'SUCCESS' : (this.status === 0x6f ? 'SPECIAL_0x6F' : 'FAIL'),
        response: responseSnapshot(this.response, 'callback-enter'), backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      emit('cards-operation92-callback-leave', {thread_id: tid(), status_18: this.status, retval: retval.toString(), response: responseSnapshot(this.response, 'callback-leave')});
      if (!controlledOverrideApplied) endTrustedWindow('RetrieveTrustedConsoleList callback returned');
    }
  });
  installCardsHook(module, byName['Trusted-console response allocator'], {
    onEnter(args) { this.owner = ptr(this.context.ecx); emit('cards-operation92-response-allocator-enter', {thread_id: tid(), ecx: safePtr(this.owner), owner: pointerProbe(this.owner, 0x40)}); },
    onLeave(retval) { emit('cards-operation92-response-allocator-leave', {thread_id: tid(), retval: retval.toString(), response: responseSnapshot(retval, 'allocator-leave')}); }
  });
  installCardsHook(module, byName['Trusted-console status handler'], {
    onEnter(args) { emit('cards-operation92-status-handler', {thread_id: tid(), status_arg: args[0].toUInt32(), status_i32: args[0].toInt32(), status_hex: '0x' + args[0].toUInt32().toString(16), stack: stackArgs(this.context, 3), backtrace: normalizedBacktrace(this.context), websession: webSessionSnapshot(module, 'status-handler')}); }
  });
  installCardsHook(module, byName['Trusted-console response parser'], {
    onEnter(args) {
      const key = String(tid());
      parserDepthByThread[key] = (parserDepthByThread[key] || 0) + 1;
      this.threadKey = key;
      this.response = ptr(this.context.ecx);
      emit('cards-operation92-parser-enter', {thread_id: tid(), response: responseSnapshot(this.response, 'parser-enter'), input: pointerProbe(args[0], 0x80), input_pointer_fields: pointerFields(args[0], 0x80)});
    },
    onLeave(retval) {
      emit('cards-operation92-parser-leave', {thread_id: tid(), retval: retval.toUInt32(), response: responseSnapshot(this.response, 'parser-leave')});
      parserDepthByThread[this.threadKey] = Math.max(0, (parserDepthByThread[this.threadKey] || 1) - 1);
    }
  });
  installCardsHook(module, byName['Trusted-console URL builder'], {
    onEnter(args) {
      this.destination = args[0];
      emit('cards-operation92-url-builder-enter', {thread_id: tid(), destination: pointerProbe(this.destination, 0x80), destination_pointer_fields: pointerFields(this.destination, 0x100), args: stackArgs(this.context, 3)});
    },
    onLeave(retval) { emit('cards-operation92-url-builder-leave', {thread_id: tid(), retval: retval.toString(), destination: pointerProbe(this.destination, 0x80), destination_pointer_fields: pointerFields(this.destination, 0x100)}); }
  });
  installCardsHook(module, byName['Trusted-console formatted suffix checkpoint'], {
    onEnter(args) {
      const frame = ptr(this.context.ebp);
      const suffix = frame.sub(0x218);
      emit('cards-operation92-formatted-suffix', {thread_id: tid(), suffix_pointer: suffix.toString(), suffix: cstring(suffix, 0x200), suffix_hex: hexBytes(raw(suffix, 0x200))});
    }
  });
  installCardsHook(module, byName['Device ID accessor'], {
    onEnter(args) { this.active = trustedWindowActive; },
    onLeave(retval) { if (this.active) emit('cards-operation92-device-id', {thread_id: tid(), pointer: retval.toString(), value: cstring(retval, 256), bytes: hexBytes(raw(retval, 128))}); }
  });
  installCardsHook(module, byName['GetUserInfo response parser'], {
    onEnter(args) {
      const key = String(tid());
      getUserInfoParserDepthByThread[key] = (getUserInfoParserDepthByThread[key] || 0) + 1;
      this.threadKey = key;
      this.response = ptr(this.context.ecx);
      this.input = args[0];
      emit('cards-get-user-info-parser-enter', {
        thread_id: tid(), response: pointerProbe(this.response, 0x80),
        input: pointerProbe(this.input, 0x100), input_pointer_fields: pointerFields(this.input, 0x100),
        fcc_login1_active: fccLogin1Active, backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      const rawResult = retval.toUInt32();
      const succeeded = (rawResult & 0xff) !== 0;
      getUserInfoParserCompleted = true;
      getUserInfoParserSucceeded = succeeded;
      emit('cards-get-user-info-parser-leave', {
        thread_id: tid(), retval_u32: rawResult, succeeded: succeeded,
        response: pointerProbe(this.response, 0x180), response_pointer_fields: pointerFields(this.response, 0x200),
        fcc_login1_active: fccLogin1Active
      });
      getUserInfoParserDepthByThread[this.threadKey] = Math.max(0, (getUserInfoParserDepthByThread[this.threadKey] || 1) - 1);
      if (false && succeeded && fccLogin1Active && !authenticatedIcebreakerSent) {
        authenticatedIcebreakerNotBeforeMs = nowMs() + 900;
        emit('authenticated-icebreaker-route-armed', {
          thread_id: tid(), delay_ms: 900, not_before_ms: authenticatedIcebreakerNotBeforeMs,
          rationale: 'The exact operation-29 GetUserInfo response parser completed while authenticated fcc_login1 remained active. Delay the retail iceBreaker NAV action for 900 ms so the native callback, userdata, store-transaction, and tutorial-popup startup work can progress before navigation changes.'
        });
      }
    }
  });
  installCardsHook(module, byName['GetUserInfo user subparser'], {
    onEnter(args) {
      const key = String(tid());
      getUserInfoSubparserDepthByThread[key] = (getUserInfoSubparserDepthByThread[key] || 0) + 1;
      this.threadKey = key;
      this.self = ptr(this.context.ecx);
      this.outUser = ptr(args[0]);
      emit('cards-get-user-info-subparser-enter', {
        thread_id: tid(), self: pointerProbe(this.self, 0x100),
        arg0: pointerProbe(args[0], 0x100), arg1: pointerProbe(args[1], 0x100),
        backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      emit('cards-get-user-info-subparser-leave', {
        thread_id: tid(), retval_u32: retval.toUInt32(), self: pointerProbe(this.self, 0x180),
        output: pointerProbe(this.outUser, 0x300), output_pointer_fields: pointerFields(this.outUser, 0x300)
      });
      getUserInfoSubparserDepthByThread[this.threadKey] = Math.max(0, (getUserInfoSubparserDepthByThread[this.threadKey] || 1) - 1);
    }
  });

  installCardsHook(module, byName['JSON key mapper'], {
    onEnter(args) {
      const key = String(tid());
      this.operation92Active = (parserDepthByThread[key] || 0) > 0;
      this.getUserInfoActive = (getUserInfoParserDepthByThread[key] || 0) > 0 || (getUserInfoSubparserDepthByThread[key] || 0) > 0;
      this.icebreakerPackListActive = (icebreakerPackListParserDepthByThread[key] || 0) > 0;
      this.icebreakerPackEntryActive = (icebreakerPackEntryParserDepthByThread[key] || 0) > 0;
      this.competitionActive = competitionTraceActive();
      this.active = this.operation92Active || this.getUserInfoActive || this.icebreakerPackListActive || this.icebreakerPackEntryActive || this.competitionActive;
      if (this.active) {
        this.keyName = cstring(args[0], 256);
        this.mapperReturnAddress = safePtr(this.returnAddress);
        this.mapperBacktrace = normalizedBacktrace(this.context);
        this.competitionKind = competitionTraceKind;
        this.competitionGeneration = competitionTraceGeneration;
      }
    },
    onLeave(retval) {
      if (!this.active) return;
      const payload = {thread_id: tid(), key: this.keyName, id: retval.toUInt32(), id_hex: '0x' + retval.toUInt32().toString(16)};
      if (this.operation92Active) emit('cards-operation92-json-key', payload);
      if (this.getUserInfoActive) emit('cards-get-user-info-json-key', payload);
      if (this.icebreakerPackListActive || this.icebreakerPackEntryActive) {
        emit('cards-icebreaker-packlist-json-key', {
          ...payload,
          list_parser_active: this.icebreakerPackListActive,
          entry_parser_active: this.icebreakerPackEntryActive
        });
      }
      if (this.competitionActive) {
        emit('cards-competition-json-key', {
          ...payload, competition_kind: this.competitionKind,
          generation: this.competitionGeneration, return_address: this.mapperReturnAddress,
          backtrace: this.mapperBacktrace
        });
      }
    }
  });
  installCardsHook(module, byName['Operation 92 descriptor status setter'], {
    onEnter(args) {
      const descriptor = module.base.add(OP92_DESCRIPTOR_RVA);
      emit('cards-operation92-descriptor-status-set', {thread_id: tid(), value: args[0].toUInt32() & 0xff, before: hexBytes(raw(descriptor, 0x18))});
    },
    onLeave(retval) {
      const descriptor = module.base.add(OP92_DESCRIPTOR_RVA);
      emit('cards-operation92-descriptor-status-after', {thread_id: tid(), after: hexBytes(raw(descriptor, 0x18))});
    }
  });

  [
    'FUT_IcebreakerManager BuildSquad wrapper',
    'FUT_IcebreakerManager ClearSquad wrapper',
    'FUT_IcebreakerManager RetrievePack wrapper',
    'FUT_IcebreakerManager RetrievePackList wrapper'
  ].forEach(function(name) {
    installCardsHook(module, byName[name], {
      onEnter(args) {
        this.thread = tid();
        let scriptBridge = ptr(0);
        try { scriptBridge = module.base.add(0x001d5fa4).readPointer(); } catch (_) {}
        emit('cards-icebreaker-manager-wrapper-enter', {
          name: name, thread_id: this.thread,
          ecx: safePtr(this.context.ecx), eax: safePtr(this.context.eax),
          args: stackArgs(this.context, 12),
          script_bridge: pointerProbe(scriptBridge, 0x100),
          script_bridge_pointer_fields: pointerFields(scriptBridge, 0x140),
          return_address: safePtr(this.returnAddress), backtrace: normalizedBacktrace(this.context)
        });
      },
      onLeave(retval) {
        emit('cards-icebreaker-manager-wrapper-leave', {
          name: name, thread_id: this.thread, retval: retval.toString(), retval_u32: retval.toUInt32()
        });
      }
    });
  });

  installCardsHook(module, byName['Icebreaker pack-list path handler'], {
    onEnter(args) {
      emit('cards-icebreaker-packlist-path-handler-enter', {
        thread_id: tid(), ecx: safePtr(this.context.ecx),
        arg0: pointerProbe(args[0], 0x100), args: stackArgs(this.context, 6),
        return_address: safePtr(this.returnAddress), backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      emit('cards-icebreaker-packlist-path-handler-leave', {thread_id: tid(), retval: retval.toString()});
    }
  });
  installCardsHook(module, byName['Icebreaker pack-list response parser'], {
    onEnter(args) {
      const key = String(tid());
      icebreakerPackListParserDepthByThread[key] = (icebreakerPackListParserDepthByThread[key] || 0) + 1;
      this.threadKey = key;
      this.self = ptr(this.context.ecx);
      this.input0 = args[0];
      this.input1 = args[1];
      emit('cards-icebreaker-packlist-response-parser-enter', {
        thread_id: tid(), self: pointerProbe(this.self, 0x180),
        arg0: pointerProbe(this.input0, 0x180), arg1: pointerProbe(this.input1, 0x180),
        args: stackArgs(this.context, 8), return_address: safePtr(this.returnAddress),
        backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      emit('cards-icebreaker-packlist-response-parser-leave', {
        thread_id: tid(), retval_u32: retval.toUInt32(), self: pointerProbe(this.self, 0x280),
        self_pointer_fields: pointerFields(this.self, 0x300)
      });
      icebreakerPackListParserDepthByThread[this.threadKey] = Math.max(0, (icebreakerPackListParserDepthByThread[this.threadKey] || 1) - 1);
    }
  });
  installCardsHook(module, byName['Icebreaker pack-entry parser'], {
    onEnter(args) {
      const key = String(tid());
      icebreakerPackEntryParserDepthByThread[key] = (icebreakerPackEntryParserDepthByThread[key] || 0) + 1;
      this.threadKey = key;
      this.self = ptr(this.context.ecx);
      this.input0 = args[0];
      this.input1 = args[1];
      emit('cards-icebreaker-pack-entry-parser-enter', {
        thread_id: tid(), self: pointerProbe(this.self, 0x1e0),
        arg0: pointerProbe(this.input0, 0x180), arg1: pointerProbe(this.input1, 0x180),
        args: stackArgs(this.context, 8), return_address: safePtr(this.returnAddress),
        backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      emit('cards-icebreaker-pack-entry-parser-leave', {
        thread_id: tid(), retval_u32: retval.toUInt32(), self: pointerProbe(this.self, 0x1e0),
        self_bytes: hexBytes(raw(this.self, 0x1e0)),
        self_pointer_fields: pointerFields(this.self, 0x1e0)
      });
      icebreakerPackEntryParserDepthByThread[this.threadKey] = Math.max(0, (icebreakerPackEntryParserDepthByThread[this.threadKey] || 1) - 1);
    }
  });

  ['FUT login stage handler', 'FUT login stage helper A', 'FUT login stage helper B', 'FUT login stage helper C'].forEach(function(name) {
    installCardsHook(module, byName[name], {
      onEnter(args) {
        this.self = ptr(this.context.ecx);
        this.thread = tid();
        emit('cards-login-stage-enter', {
          name: name, thread_id: this.thread, self: pointerProbe(this.self, 0x240),
          args: stackArgs(this.context, 8), return_address: safePtr(this.returnAddress),
          fcc_login1_active: fccLogin1Active, fcc_login2_active: fccLogin2Active,
          get_user_info_completed: getUserInfoParserCompleted,
          backtrace: normalizedBacktrace(this.context)
        });
      },
      onLeave(retval) {
        emit('cards-login-stage-leave', {
          name: name, thread_id: this.thread, retval: retval.toString(),
          self: pointerProbe(this.self, 0x240),
          fcc_login1_active: fccLogin1Active, fcc_login2_active: fccLogin2Active
        });
      }
    });
  });

  if (!RUNTIME_PERFORMANCE_MODE) {
  installCardsHook(module, byName['FUT static-asset state setter'], {
      onEnter(args) {
        this.self = ptr(this.context.ecx);
        this.before = readU32(this.self, 0x38);
        this.value = args[0].toUInt32();
        emit('cards-static-asset-state-set-enter', {
          thread_id: tid(), self: safePtr(this.self), before: this.before, requested: this.value,
          asset_kind_3c: readU32(this.self, 0x3c), return_address: safePtr(this.returnAddress),
          backtrace: normalizedBacktrace(this.context)
        });
      },
      onLeave(retval) {
        emit('cards-static-asset-state-set-leave', {
          thread_id: tid(), self: safePtr(this.self), before: this.before,
          requested: this.value, after: readU32(this.self, 0x38)
        });
      }
    });
    installCardsHook(module, byName['FUT static-asset state getter'], {
      onEnter(args) {
        this.self = ptr(this.context.ecx);
        this.callNumber = (counters['locstrings-state-getter-calls'] || 0) + 1;
        counters['locstrings-state-getter-calls'] = this.callNumber;
        this.logCall = this.callNumber <= 80;
        if (this.logCall) {
          emit('cards-static-asset-state-get-enter', {
            thread_id: tid(), call_number: this.callNumber, self: safePtr(this.self),
            state_38: readU32(this.self, 0x38), asset_kind_3c: readU32(this.self, 0x3c),
            return_address: safePtr(this.returnAddress), backtrace: normalizedBacktrace(this.context)
          });
        }
      },
      onLeave(retval) {
        if (!this.logCall) return;
        emit('cards-static-asset-state-get-leave', {
          thread_id: tid(), call_number: this.callNumber, retval: retval.toUInt32(),
          state_38: readU32(this.self, 0x38)
        });
      }
    });
  
    installCardsHook(module, byName['FUT locstrings path builder'], {
      onEnter(args) {
        this.self = ptr(this.context.ecx);
        this.output = args[0];
        emit('cards-locstrings-path-builder-enter', {
          thread_id: tid(), return_address: safePtr(this.returnAddress), self: safePtr(this.self),
          asset_kind_3c: readU32(this.self, 0x3c), output_object: pointerProbe(this.output, 0x60),
          output_pointer_fields: pointerFields(this.output, 0x80), backtrace: normalizedBacktrace(this.context)
        });
      },
      onLeave(retval) {
        emit('cards-locstrings-path-builder-leave', {
          thread_id: tid(), asset_kind_3c: readU32(this.self, 0x3c), retval: retval.toString(),
          output_object: pointerProbe(this.output, 0x60), output_pointer_fields: pointerFields(this.output, 0x80)
        });
      }
    });
  
    installCardsHook(module, byName['FUT static-asset completion dispatcher'], {
      onEnter(args) {
        this.self = ptr(this.context.ecx);
        this.arg0 = args[0];
        this.buffer = args[1];
        this.length = args[2].toUInt32();
        const preview = Math.min(this.length, 4096);
        emit('cards-static-asset-completion-enter', {
          thread_id: tid(), return_address: safePtr(this.returnAddress), self: safePtr(this.self),
          status_arg: this.arg0.toUInt32(), buffer: safePtr(this.buffer), length: this.length,
          preview_text: preview > 0 ? cstring(this.buffer, preview) : null,
          preview_hex: preview > 0 ? hexBytes(raw(this.buffer, preview)) : null,
          self_bytes: hexBytes(raw(this.self, 0x50)), backtrace: normalizedBacktrace(this.context)
        });
      },
      onLeave(retval) {
        emit('cards-static-asset-completion-leave', {
          thread_id: tid(), status_arg: this.arg0.toUInt32(), length: this.length,
          retval: retval.toString(), self_bytes: hexBytes(raw(this.self, 0x50))
        });
      }
    });
  
    } else {
    emit('cards-static-asset-diagnostics-disabled-beta2244', {reason:'Static asset state/path/completion hooks are read-only diagnostics and sit on the Store/pack asset path.'});
  }

  installCardsHook(module, byName['FUT locstrings response parser'], {
    onEnter(args) {
      this.self = ptr(this.context.ecx);
      this.buffer = args[0];
      this.length = args[1].toUInt32();
      const preview = Math.min(this.length, 4096);
      emit('cards-locstrings-parser-enter', {
        thread_id: tid(), return_address: safePtr(this.returnAddress), self: safePtr(this.self),
        asset_kind_3c: readU32(this.self, 0x3c), buffer: safePtr(this.buffer), length: this.length,
        preview_text: preview > 0 ? cstring(this.buffer, preview) : null,
        preview_hex: preview > 0 ? hexBytes(raw(this.buffer, preview)) : null,
        backtrace: normalizedBacktrace(this.context)
      });
    },
    onLeave(retval) {
      const accepted = (retval.toUInt32() & 0xff) !== 0;
      emit('cards-locstrings-parser-leave', {
        thread_id: tid(), asset_kind_3c: readU32(this.self, 0x3c),
        accepted: accepted, retval_u32: retval.toUInt32(),
        length: this.length
      });
      if (accepted) {
        locstringsAccepted = true;
        emit('cards-authenticated-icebreaker-locstrings-ready-for-natural-auth', {
          thread_id: tid(),
          reason: 'retail Authentication request start is observed naturally; no direct start call is armed',
          descriptor: authDescriptorSnapshot(module, 'locstrings accepted')
        });
      }
    }
  });

  installLocalStoreGateHooks(module);
  if (!RUNTIME_PERFORMANCE_MODE) {
    installStoreNumberArgTrace(module);
    installUserCreditsParserTrace(module);
  }
  emit('cards-runtime-performance-mode-beta2244', {enabled:RUNTIME_PERFORMANCE_MODE, disabled_hot_traces:['match-bridge','store-passive-wrappers','store-number-arg','user-credits-parser','generic-response-parser','generic-response-scalar','generic-response-key-map','static-asset-state-path-completion','fifa-static-asset-bridge','fifa-module-wrapper','fifa-plugin-loader','full-nav-backtraces'], ui_writer_scope:'MATCH_RECORD/BADGE_ID exact-wrapper windows only'});

  cardsHooksInstalled = true;
  emit('cards-operation92-hooks-installed', {reason: reason, base: module.base.toString(), hook_count: CARDS_TARGETS.length});
  return true;
}


function moduleWrapperSnapshot(self, source) {
  const plugin = (function(){ try { return self.add(0x120).readPointer(); } catch (_) { return ptr(0); } })();
  let pluginObject = ptr(0), pluginVtable = ptr(0), pluginRelease = ptr(0), pluginDeinit = ptr(0);
  try { if (!plugin.isNull()) pluginObject = plugin.readPointer(); } catch (_) {}
  try { if (!pluginObject.isNull()) pluginVtable = pluginObject.readPointer(); } catch (_) {}
  try { if (!plugin.isNull()) pluginRelease = plugin.add(4).readPointer(); } catch (_) {}
  try { if (!pluginVtable.isNull()) pluginDeinit = pluginVtable.add(4).readPointer(); } catch (_) {}
  return {
    source: source,
    self: pointerProbe(self, 0x140),
    state_100: readU32(self, 0x100),
    flag_104: readU8(self, 0x104),
    field_108: readU32(self, 0x108),
    plugin_holder_120: pointerProbe(plugin, 0x30),
    plugin_object: pointerProbe(pluginObject, 0x40),
    plugin_vtable: safePtr(pluginVtable),
    plugin_release_target: safePtr(pluginRelease),
    plugin_deinitialize_target: safePtr(pluginDeinit)
  };
}


function pluginLoaderSnapshot(pointer, source) {
  if (pointer.isNull()) return {source:source, pointer:'0x0'};
  return {
    source: source,
    pointer: safePtr(pointer),
    object: pointerProbe(pointer, 0x1a0),
    state_8: readU32(pointer, 0x8),
    pending_0c: readU8(pointer, 0x0c),
    plugin_interface_170: pointerProbe((function(){try{return pointer.add(0x170).readPointer();}catch(_){return ptr(0);}})(), 0x40)
  };
}

function installFifaPluginLoaderHooks(fifa) {
  FIFA_PLUGIN_LOADER_RVAS.forEach(function(item) {
    const address = fifa.base.add(item.rva);
    const check = verify(address, item.signature);
    emit('fifa-plugin-loader-signature', {
      name:item.name, rva:'0x'+item.rva.toString(16), address:address.toString(),
      matched:check.matched, expected:hexBytes(item.signature), actual:check.actual, error:check.error||null
    });
    if (!check.matched) return;
    try {
      Interceptor.attach(address, {
        onEnter(args) {
          this.thread = tid();
          this.ecx = ptr(this.context.ecx);
          this.loader = this.ecx;
          if (item.name.indexOf('Action') >= 0) {
            try { this.loader = this.context.ebp.add(0x0c).readPointer(); } catch (_) {}
          }
          const payload = {
            name:item.name, thread_id:this.thread, return_address:safePtr(this.returnAddress),
            ecx:safePtr(this.ecx), loader:safePtr(this.loader), stack_args:stackArgs(this.context, 8),
            loader_state:pluginLoaderSnapshot(this.loader, item.name+' enter'),
            backtrace:normalizedBacktrace(this.context)
          };
          if (item.name === 'PluginLoaderSetEvent') payload.event_id = args[0].toUInt32();
          emit('fifa-plugin-loader-enter', payload);
        },
        onLeave(retval) {
          emit('fifa-plugin-loader-leave', {
            name:item.name, thread_id:this.thread, retval:retval.toString(), retval_u32:retval.toUInt32(),
            loader_state:pluginLoaderSnapshot(this.loader, item.name+' leave')
          });
        }
      });
    } catch (error) {
      emit('fifa-plugin-loader-hook-error', {name:item.name, address:address.toString(), error:String(error)});
    }
  });
}

function installFifaNavHooks(fifa) {
  const checks = [];
  FIFA_NAV_TARGETS.forEach(function(item) {
    const address = fifa.base.add(item.rva);
    const check = verify(address, item.signature);
    checks.push(check.matched);
    emit('fifa-nav-signature', {name:item.name, rva:'0x'+item.rva.toString(16), address:address.toString(), matched:check.matched, expected:hexBytes(item.signature), actual:check.actual, error:check.error||null});
    if (!check.matched) return;
    try {
      Interceptor.attach(address, {
        onEnter(args) {
          const common = RUNTIME_PERFORMANCE_MODE ? {name:item.name, thread_id:tid()} : {name:item.name, thread_id:tid(), return_address:safePtr(this.returnAddress), hidden_arg0:safePtr(args[0]), ecx:safePtr(this.context.ecx), backtrace:normalizedBacktrace(this.context)};
          if (item.name === 'NAV::loadView') {
            const originalView = cstring(args[2],768);
            let view = originalView;
            let postMatchRedirected = false;
            // BETA 2.23 one-shot offline post-match return repair.  Do not make
            // fcc_logout globally unreachable: only rewrite the first logout
            // view requested shortly after a real /match/end transaction.
            if (view && view.toLowerCase().endsWith('/fcc_logout') && postMatchReturnGuardActive()) {
              const guardGeneration = postMatchReturnGuardGeneration;
              const remainingMs = Math.max(0, postMatchReturnGuardUntilMs - nowMs());
              args[2] = postMatchStayInFutView;
              view = 'external/ion_fut/screens/gameHub/GameHub';
              postMatchReturnGuardUntilMs = 0;
              postMatchFccLogoutRedirects += 1;
              postMatchRedirected = true;
              emit('fifa-postmatch-fcc-logout-redirect-beta223', {
                generation:guardGeneration, original_view:originalView, replacement_view:view,
                remaining_ms:remainingMs, redirect_count:postMatchFccLogoutRedirects,
                rationale:'DestroyMatch completed locally; keep CardsDLL/FUT loaded instead of taking the stale disconnected-from-in-game logout route.',
                thread_id:tid(), return_address:safePtr(this.returnAddress)
              });
            }
            if (view && view.toLowerCase().endsWith('/fcc_login1')) { fccLogin1Active = true; fccLogin2Active = false; }
            if (view && view.toLowerCase().endsWith('/fcc_login2')) { fccLogin2Active = true; fccLogin1Active = false; }
            if (view && view.toLowerCase().indexOf('/icebreaker/futpackselect') >= 0) futPackSelectActive = true;
            if (!RUNTIME_PERFORMANCE_MODE || postMatchRedirected || (view && (view.toLowerCase().endsWith('/fcc_login1') || view.toLowerCase().endsWith('/fcc_login2') || view.toLowerCase().indexOf('/icebreaker/futpackselect') >= 0))) {
              emit('fifa-nav-load-view', {...common, layer:RUNTIME_PERFORMANCE_MODE?null:cstring(args[1],256), view:view, original_view:originalView, post_match_redirected:postMatchRedirected, post_match_guard_active:postMatchReturnGuardActive(), arg3:RUNTIME_PERFORMANCE_MODE?null:cstring(args[3],512), arg4:RUNTIME_PERFORMANCE_MODE?null:cstring(args[4],512), fcc_login1_active:fccLogin1Active, fcc_login2_active:fccLogin2Active, fut_packselect_active:futPackSelectActive});
            }
          } else if (item.name === 'NAV::unloadView') {
            const view = cstring(args[2],768);
            if (view && view.toLowerCase().endsWith('/fcc_login1')) fccLogin1Active = false;
            if (view && view.toLowerCase().endsWith('/fcc_login2')) fccLogin2Active = false;
            if (view && view.toLowerCase().indexOf('/icebreaker/futpackselect') >= 0) futPackSelectActive = false;
            if (!RUNTIME_PERFORMANCE_MODE || (view && (view.toLowerCase().endsWith('/fcc_login1') || view.toLowerCase().endsWith('/fcc_login2') || view.toLowerCase().indexOf('/icebreaker/futpackselect') >= 0))) {
              emit('fifa-nav-unload-view', {...common, layer:RUNTIME_PERFORMANCE_MODE?null:cstring(args[1],256), view:view, arg3:RUNTIME_PERFORMANCE_MODE?null:cstring(args[3],512), fcc_login1_active:fccLogin1Active, fcc_login2_active:fccLogin2Active, fut_packselect_active:futPackSelectActive});
            }
          } else if (item.name === 'NAV::sendAction') {
            if (!RUNTIME_PERFORMANCE_MODE) emit('fifa-nav-send-action', {...common, action:cstring(args[1],512), parameter:cstring(args[2],768)});
          } else {
            if (!RUNTIME_PERFORMANCE_MODE) emit('fifa-nav-send-screen-event', {...common, event:cstring(args[1],512), payload:cstring(args[2],1024)});
          }
        }
      });
      emit('fifa-nav-hook-ready', {name:item.name, address:address.toString()});
    } catch (error) {
      emit('fifa-nav-hook-error', {name:item.name, address:address.toString(), error:String(error)});
    }
  });
  return checks.every(function(value){ return value; });
}

function installFifaModuleWrapperHooks(fifa) {
  FIFA_MODULE_WRAPPER_RVAS.forEach(function(item) {
    const address = fifa.base.add(item.rva);
    const check = verify(address, item.signature);
    emit('fifa-module-wrapper-signature', {
      name:item.name, rva:'0x'+item.rva.toString(16), address:address.toString(),
      matched:check.matched, expected:hexBytes(item.signature), actual:check.actual, error:check.error||null
    });
    if (!check.matched) return;
    try {
      Interceptor.attach(address, {
        onEnter(args) {
          this.self = ptr(this.context.ecx);
          this.thread = tid();
          this.before = moduleWrapperSnapshot(this.self, item.name+' enter');
          const payload = {
            name:item.name, thread_id:this.thread, return_address:safePtr(this.returnAddress),
            ecx:safePtr(this.self), args:stackArgs(this.context, 8), before:this.before,
            backtrace:normalizedBacktrace(this.context)
          };
          if (item.name === 'ModuleWrapperRelease') {
            payload.refcount_address = safePtr(this.self.add(8));
            payload.refcount_before = readU32(this.self, 8);
          }
          emit('fifa-module-wrapper-enter', payload);
        },
        onLeave(retval) {
          emit('fifa-module-wrapper-leave', {
            name:item.name, thread_id:this.thread, retval:retval.toString(),
            after:moduleWrapperSnapshot(this.self, item.name+' leave')
          });
        }
      });
    } catch (error) {
      emit('fifa-module-wrapper-hook-error', {name:item.name, address:address.toString(), error:String(error)});
    }
  });
}

hookConnect('connect');
hookConnect('WSAConnect');
hookDnsA('getaddrinfo');
hookDnsW('GetAddrInfoW');
hookHostByName();
hookSend();
hookWSASend();
hookRecv();
hookWSARecv();

const fifa = Process.getModuleByName('fifa14.exe');
const fifaSizeOk = fifa.size === EXPECTED_FIFA_IMAGE_SIZE;
const caCheck = verify(fifa.base.add(CA_FUNCTION_RVA), CA_FUNCTION_SIGNATURE);
const updateCheck = verify(fifa.base.add(UPDATE_RVA), UPDATE_SIGNATURE);
const dispatcherCheck = verify(fifa.base.add(SCREEN_EVENT_DISPATCHER_RVA), SCREEN_EVENT_DISPATCHER_SIGNATURE);
const navSignatureChecks = FIFA_NAV_TARGETS.map(function(item){ return verify(fifa.base.add(item.rva), item.signature); });
const buildOk = fifaSizeOk && caCheck.matched && updateCheck.matched && dispatcherCheck.matched && navSignatureChecks.every(function(item){ return item.matched; });
emit('native-authenticated-icebreaker-fifa-build', {
  pid: Process.id, base: fifa.base.toString(), size: fifa.size, expected_size: EXPECTED_FIFA_IMAGE_SIZE,
  size_ok: fifaSizeOk, ca_signature_ok: caCheck.matched, update_signature_ok: updateCheck.matched,
  dispatcher_signature_ok: dispatcherCheck.matched, nav_signatures_ok:navSignatureChecks.every(function(item){ return item.matched; })
});

if (buildOk) {
  if (!RUNTIME_PERFORMANCE_MODE) installFifaModuleWrapperHooks(fifa);
  else emit('fifa-module-wrapper-diagnostics-disabled-beta2244', {reason:'pack runtime fast path'});
  installFifaNavHooks(fifa);
  try {
    navSendActionFunction = new NativeFunction(fifa.base.add(0x006fda50), 'void', ['pointer','pointer','pointer'], 'stdcall');
    emit('authenticated-icebreaker-nav-send-action-callable', {address:fifa.base.add(0x006fda50).toString(), calling_convention:'stdcall', ignored_arg0:true});
  } catch (error) {
    emit('authenticated-icebreaker-nav-send-action-callable-error', {error:String(error)});
  }
  if (!RUNTIME_PERFORMANCE_MODE) installFifaPluginLoaderHooks(fifa);
  else emit('fifa-plugin-loader-diagnostics-disabled-beta2244', {reason:'pack runtime fast path'});
  const bridgeAddress = fifa.base.add(FIFA_STATIC_ASSET_BRIDGE_RVA);
  const bridgeCheck = verify(bridgeAddress, FIFA_STATIC_ASSET_BRIDGE_SIGNATURE);
  emit('fifa-static-asset-bridge-signature', {
    rva: '0x' + FIFA_STATIC_ASSET_BRIDGE_RVA.toString(16),
    address: bridgeAddress.toString(), matched: bridgeCheck.matched,
    expected: hexBytes(FIFA_STATIC_ASSET_BRIDGE_SIGNATURE), actual: bridgeCheck.actual,
    error: bridgeCheck.error || null
  });
  if (bridgeCheck.matched && !RUNTIME_PERFORMANCE_MODE) {
    try {
      Interceptor.attach(bridgeAddress, {
        onEnter(args) {
          this.self = ptr(this.context.ecx);
          this.arg0 = args[0]; this.buffer = args[1]; this.length = args[2].toUInt32();
          let interfacePointer = ptr(0);
          try { interfacePointer = this.self.add(0x138).readPointer(); } catch (_) {}
          const preview = Math.min(this.length, 4096);
          emit('fifa-static-asset-bridge-enter', {
            thread_id: tid(), self: pointerProbe(this.self, 0x180),
            interface_pointer: pointerProbe(interfacePointer, 0x80),
            arg0: this.arg0.toUInt32(), buffer: safePtr(this.buffer), length: this.length,
            preview_text: preview > 0 ? cstring(this.buffer, preview) : null,
            preview_hex: preview > 0 ? hexBytes(raw(this.buffer, preview)) : null,
            return_address: safePtr(this.returnAddress), backtrace: normalizedBacktrace(this.context)
          });
        },
        onLeave(retval) {
          emit('fifa-static-asset-bridge-leave', {
            thread_id: tid(), retval: retval.toString(),
            cards_state: cardsBase === null ? null : cardsServiceStateSnapshot({base: cardsBase, size: CARDS_EXPECTED_IMAGE_SIZE}, 'fifa static-asset bridge leave')
          });
        }
      });
      emit('fifa-static-asset-bridge-hook-ready', {address: bridgeAddress.toString()});
    } catch (error) {
      emit('fifa-static-asset-bridge-hook-error', {address: bridgeAddress.toString(), error: String(error)});
    }
  } else if (bridgeCheck.matched && RUNTIME_PERFORMANCE_MODE) {
    emit('fifa-static-asset-bridge-disabled-beta2244', {address:bridgeAddress.toString(), reason:'read-only Store/pack asset trace'});
  }
  const caArray = (function decodeBase64(text) {
    const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
    const clean = text.replace(/=+$/, '');
    const output = []; let bits = 0; let value = 0;
    for (let i = 0; i < clean.length; i++) {
      value = (value << 6) | alphabet.indexOf(clean[i]); bits += 6;
      if (bits >= 8) { bits -= 8; output.push((value >>> bits) & 0xff); }
    }
    return output;
  })('__CA_BASE64__');
  const caBuffer = Memory.alloc(caArray.length + 1);
  caBuffer.writeByteArray(caArray); caBuffer.add(caArray.length).writeU8(0);
  const setCACert = new NativeFunction(fifa.base.add(CA_FUNCTION_RVA), 'int', ['pointer','int'], 'mscdecl');
  let caLoaded = false, caInProgress = false, caAttempts = 0;
  Interceptor.attach(fifa.base.add(UPDATE_RVA), {onEnter(args) {
    const updateThreadId = tid();
    if (!caLoaded && !caInProgress && caAttempts < 8) {
      caInProgress = true; caAttempts += 1;
      let result = null, error = null;
      try { result = setCACert(caBuffer, caArray.length); if (result !== null && result > 0) caLoaded = true; }
      catch (caught) { error = String(caught); }
      caInProgress = false;
      emit('local-ca-load-on-update-result', {attempt: caAttempts, loaded: caLoaded, thread_id: updateThreadId, ca_bytes: caArray.length, result: result, error: error});
    }
    // Authentication is natural. The only controlled continuation is the
    // original futLogInFlow.nav iceBreaker action, gated after the exact
    // GetUserInfo parser completes on an authenticated first-use account.
    attemptAuthenticatedIcebreaker(updateThreadId);

  }});

  Interceptor.attach(fifa.base.add(SCREEN_EVENT_DISPATCHER_RVA), {onEnter(args) {
    const scope = cstring(args[0], 128);
    const event = cstring(args[1], 256);
    const baselineEvent = event === 'LoginToFUT' || event === 'RetrieveTrustedConsoleListResult' || event === 'RetrievePhishingQuestionResult' || event === 'ValidatePhishingAnswerResult' || event === 'FutDllUnloaded' || event === 'FUTCfgFileDownloadResult' || (futPackSelectActive && (event === 'ShowLoadingIcon' || event === 'HideLoadingIcon' || event === 'ResetLoadingIcon' || event === 'StartNetworkOperation' || event === 'EndNetworkOperation'));
    const postOverrideEvent = (controlledOverrideApplied || controlledQuestionOverrideApplied || controlledValidationOverrideApplied) && trustedWindowActive && event !== 'updateTimeCount';
    if (baselineEvent || postOverrideEvent) {
      const payloads = [];
      try {
        const vector = args[2];
        if (!vector.isNull()) {
          for (let i = 0; i < 8; i++) {
            const item = vector.add(i * Process.pointerSize).readPointer();
            if (item.isNull()) break;
            payloads.push(cstring(item, 256));
          }
        }
      } catch (_) {}
      emit('trusted-console-screen-event', {thread_id: tid(), scope: scope, event: event, payloads: payloads});
    }
    if (event === 'ValidatePhishingAnswerResult') {
      const payloads = [];
      try {
        const vector = args[2];
        if (!vector.isNull()) {
          for (let i = 0; i < 8; i++) {
            const item = vector.add(i * Process.pointerSize).readPointer();
            if (item.isNull()) break;
            payloads.push(cstring(item, 256));
          }
        }
      } catch (_) {}
      phishingValidationSucceeded = payloads.some(function(value){ return String(value).toUpperCase() === 'SUCCESS'; });
      emit('authenticated-icebreaker-security-state', {thread_id:tid(), event:event, payloads:payloads, succeeded:phishingValidationSucceeded});
    }
    if (event === 'FutDllUnloaded') {
      cardsHooksInstalled = false;
      cardsBase = null;
      // BETA 2.25.8: every Interceptor attached inside CardsDLL disappears
      // when FutDllUnloaded unmaps the module.  The old booleans survived, so
      // the second LoginToFUT believed record/badge/consumable/store hooks were
      // still installed and skipped reattaching them.  Rearm every CardsDLL-
      // scoped installer and clear its transient wrapper state.
      activateCardTraceHooked = false;
      activateCardTracePollStarted = false;
      viewCardsTraceInstalled = false;
      tournamentSessionTraceInstalled = false;
      tournamentNameFallbackInstalled = false;
      tournamentNameObjects = Object.create(null);
      userRecordTraceInstalled = false;
      mainHubRecordOverrideInstalled = false;
      badgeUiOverrideInstalled = false;
      uiWriterTargetsHooked = Object.create(null);
      lastUiWriterDiscoveryKey = null;
      recordUiWriterActiveDepth = 0;
      badgeUiWriterActiveDepth = 0;
      Object.keys(recordUiWriterDepthByThread).forEach(function(key){ delete recordUiWriterDepthByThread[key]; });
      Object.keys(badgeUiWriterDepthByThread).forEach(function(key){ delete badgeUiWriterDepthByThread[key]; });
      tournamentInfoActionTraceInstalled = false;
      storePassiveHooksInstalled = false;
      storeLocalHooksInstalled = false;
      storeNumberArgHookInstalled = false;
      userCreditsParserHookInstalled = false;
      Object.keys(storeGateStackByThread).forEach(function(key){ delete storeGateStackByThread[key]; });
      Object.keys(storeWrapperStackByThread).forEach(function(key){ delete storeWrapperStackByThread[key]; });
      stadiumScriptHooksInstalled = false;
      stadiumProviderTargetsHooked = Object.create(null);
      stadiumProviderPollStarted = false;
      stadiumProviderPollAttempts = 0;
      stadiumProviderLastSnapshotObject = null;
      matchBridgeTargetsHooked = Object.create(null);
      matchBridgePollStarted = false;
      dispatchTargetsHooked = Object.create(null);
      serviceApiTargetsHooked = Object.create(null);
      authSubobjectTargetsHooked = Object.create(null);
      controlledOverrideApplied = false;
      controlledQuestionOverrideApplied = false;
      controlledValidationOverrideApplied = false;
      locstringsAccepted = false;
      authStartArmed = false;
      authStartInProgress = false;
      authStartUpdateCountdown = 0;
      authCandidateAttempts = 0;
      authCandidate = null;
      authWrapperFromConstructor = null;
      authWrapperFromInitialize = null;
      getUserInfoParserCompleted = false;
      getUserInfoParserSucceeded = false;
      phishingValidationSucceeded = false;
      fccLogin1Active = false;
      fccLogin2Active = false;
      futPackSelectActive = false;
      authenticatedIcebreakerSent = false;
      authenticatedIcebreakerNotBeforeMs = 0;
      competitionTraceUntilMs = 0;
      competitionTraceKind = '';
      competitionTraceGeneration = 0;
      competitionTraceLastHttpKey = '';
      competitionTraceLastHttpMs = 0;
      competitionOperationIndexes = Object.create(null);
      endTrustedWindow('FutDllUnloaded');
      emit('cards-security-challenge-rearm-after-unload', {thread_id: tid()});
    }
    if (event === 'LoginToFUT') attachCardsHooksOnce('LoginToFUT dispatcher entry');
    if (event === 'RetrieveTrustedConsoleListResult' && !controlledOverrideApplied) endTrustedWindow('frontend result event');
  }});
}

emit('native-authenticated-icebreaker-mode-armed', {trusted: true, changed: false, exists: true, locked: false, easfc_component_expected: 0x081d});

send({
  kind: 'native-fut-nav-route-patch-trace-ready', pid: Process.id, hooks_enabled: buildOk,
  contract_revision: 'captain-v6-plus-fcc-login1-no-loading-v16-competition-v3-match-postmatch-v1',
  instrumentation: buildOk ? [
    'connect/WSAConnect localhost redirect', 'CA update hook', 'narrow LoginToFUT event trigger',
    'native GetStadiumID/provider +0x78/+0x7c Town Park bridge for offline matches',
    'focused operation-45 ActivateCard accessor/status tracing and pass-through native exception capture',
    'passive CardsDLL WINS/LOSSES/DRAWS frontend-record value trace around post-match refresh',
    'local offline tournament-name fallback for Starter/Bronze/Silver/Gold cups when retail metadata is empty',
    'short startup attachment delay (15 seconds by default) with natural Authentication request start tracing',
    'controlled null-only EASW-Session and EASW-Token pointer bridge inside the retail CAS JSON builder',
    'FIFA 14 1000-byte CAS JSON builder and operation-23 /ut/auth submit tracing',
    'generic FUT WebSession response allocation/scalar callbacks plus SID propagation tracing',
    'early outgoing season/tournament HTTP request arming (15-second parser window) with unrestricted parser-scoped JSON key IDs/return addresses/backtraces',
    'one-shot post-match /match/end guard that rewrites only the stale fcc_logout return to the normal FUT GameHub view',
    'read-only enumeration and targeted hooks for season/tournament operation descriptors and service API slots',
    'CardsDLL operations 89, 91 and 92 launch/callback paths', 'trusteddevice, phishing question and validation URL builders',
    'response allocation/parsing and accurate backtraces', 'dynamic WebSession dispatch target and state',
    'controlled operation 92 -2/999 to pre-trusted existing unlocked success normalization',
    'controlled operation 89 -2/999 to question 0 with five attempts',
    'controlled operation 91 -2/999 to validation success',
    'CardsDLL dynamic-messages base snapshot at LoginToFUT and after validation',
    'retail leaderboards/icebreaker localization path builder, state setter/getter and XML parser',
    'FIFA NAV loadView/unloadView/sendAction/sendScreenEvent tracing and startup operations 8/16/22-40/56/73/89-97',
    'exact GetUserInfo response parser/subparser/key mapping and login-stage handler tracing',
    'FUT_IcebreakerManager RetrievePackList/RetrievePack/BuildSquad/ClearSquad script wrappers',
    'icebreaker pack-list response parser, per-entry parser and parser-scoped JSON key mapping',
    'passive futPackSelect loading screen-event and native icebreaker manager telemetry',
    'passive observation of the archive-patched futLogIn1 route, exact v6 captain baseline, and offline fcc_login1 no-Loading substitution; no manual NAV action is submitted',
    'operation-descriptor status setters across the complete observed first-use startup batch',
    'safe vtable inspection for returned authentication/user subobjects',
    'accurate PlugDeinitialize caller backtrace and pre-unload service-state snapshots',
    'decrypted fifa14.exe EA::Plug::Module wrapper factory/assignment/refcount/destructor lifecycle',
    'PluginSingleLoaderImpl SetEvent and Creating/StartLoad/StartUnload/Destroying/UnloadComplete actions',
    'Plugin loader LoadAndProbe return value and exact unload-trigger caller backtraces',
    'decrypted fifa14.exe static-asset callback bridge',
    'DNS/connect/send/recv during 180-second post-validation window',
    'localhost redirect arms TCP 8306 only after the exact observed dynamic-messages DNS host'
  ] : [],
  excluded: [
    'direct EnterFUT2 call', 'direct Authentication request start call', 'manual NAV action submission', 'direct captain-screen load', 'synthetic club/squad/player inventory or completed pack ownership',
    'loader API hooks', 'module observer', 'module enumeration', 'module polling', 'watchpoints',
    'DiME hooks', 'second Frida session'
  ]
});
"""
    return (
        agent.replace("__CA_BASE64__", encoded_ca)
        .replace("__CA_SIGNATURE__", _js_bytes(CA_FUNCTION_SIGNATURE))
        .replace("__UPDATE_SIGNATURE__", _js_bytes(UPDATE_SIGNATURE))
        .replace("__DISPATCH_SIGNATURE__", _js_bytes(SCREEN_EVENT_DISPATCHER_SIGNATURE))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="FIFA 14 FUT passive authenticated NAV route-patch tracer")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--ca-file", required=True)
    parser.add_argument("--log")
    parser.add_argument("--identity-db")
    parser.add_argument("--run-seconds", type=int, default=1800)
    parser.add_argument("--print-agent", action="store_true")
    args = parser.parse_args()

    def read_local_record() -> tuple[int, int, int] | None:
        if not args.identity_db:
            return None
        database = Path(args.identity_db)
        if not database.is_file():
            return None
        try:
            with sqlite3.connect(database, timeout=0.1) as connection:
                connection.row_factory = sqlite3.Row
                identity = connection.execute("SELECT persona_id FROM identity WHERE singleton=1").fetchone()
                if identity is None:
                    return None
                rows = connection.execute(
                    "SELECT result,status FROM beta_match_sessions WHERE persona_id=? AND settled=1",
                    (int(identity["persona_id"]),),
                ).fetchall()
        except sqlite3.Error:
            return None
        wins = draws = losses = 0
        for row in rows:
            result = str(row["result"] or "").upper()
            status = str(row["status"] or "").lower()
            if result == "WIN": wins += 1
            elif result == "DRAW": draws += 1
            elif result in {"LOSS", "DNF"} or status == "dnf": losses += 1
        return wins, draws, losses

    def read_local_badge() -> tuple[int, int] | None:
        if not args.identity_db:
            return None
        database = Path(args.identity_db)
        if not database.is_file():
            return None
        try:
            with sqlite3.connect(database, timeout=0.1) as connection:
                connection.row_factory = sqlite3.Row
                identity = connection.execute("SELECT persona_id FROM identity WHERE singleton=1").fetchone()
                if identity is None:
                    return None
                persona_id = int(identity["persona_id"])
                club = connection.execute("SELECT badge_id FROM clubs WHERE persona_id=?", (persona_id,)).fetchone()
                settings = connection.execute(
                    "SELECT badge_resource_id FROM beta_club_settings WHERE persona_id=?", (persona_id,)
                ).fetchone()
        except sqlite3.Error:
            return None
        asset_id = 0 if club is None else int(club["badge_id"] or 0)
        resource_id = 0 if settings is None else int(settings["badge_resource_id"] or 0)
        return (asset_id, resource_id) if asset_id > 0 else None

    agent = build_agent(Path(args.ca_file).read_bytes())
    if args.print_agent:
        print(agent)
        return 0
    if args.pid is None:
        parser.error("--pid is required unless --print-agent is used")
    if not args.log:
        parser.error("--log is required unless --print-agent is used")

    import frida

    output = Path(args.log)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)

    # BETA 2.25.0: keep the trace useful without turning every Frida message
    # into an open/write/close + console flush.  The old synchronous recorder
    # amplified hot Store/ResetMatch hooks into visible frame-time spikes.
    log_handle = output.open("a", encoding="utf-8", buffering=262144)
    pending_log_lines = 0
    last_log_flush = time.monotonic()

    def record(event: dict) -> None:
        nonlocal pending_log_lines, last_log_flush
        line = json.dumps({"outer_time": utc_now(), **event}, ensure_ascii=False)
        kind = str(event.get("kind", ""))
        log_handle.write(line + "\n")
        pending_log_lines += 1
        important = (
            "error" in kind or "exception" in kind or "ready" in kind or
            "attached" in kind or "runtime-performance-mode" in kind
        )
        now = time.monotonic()
        if important or pending_log_lines >= 64 or (now - last_log_flush) >= 0.5:
            log_handle.flush()
            pending_log_lines = 0
            last_log_flush = now
        # Stdout is redirected by the launcher; retain only useful lifecycle or
        # failure lines instead of synchronously flushing thousands of traces.
        if important:
            print(line, flush=True)

    session = frida.get_local_device().attach(args.pid)
    script = session.create_script(agent)

    def on_message(message, data) -> None:
        if message.get("type") == "send":
            payload = message.get("payload")
            record(payload if isinstance(payload, dict) else {"kind": "frida-send", "payload": payload})
        else:
            record({"kind": "frida-message", "message": message})

    script.on("message", on_message)
    script.load()
    record({"kind": "fut-nav-route-patch-helper-attached", "pid": args.pid})

    last_record: tuple[int, int, int] | None = None
    last_badge: tuple[int, int] | None = None
    deadline = time.monotonic() + max(1, args.run_seconds)
    try:
        while time.monotonic() < deadline:
            current_record = read_local_record()
            if current_record is not None and current_record != last_record:
                try:
                    script.exports_sync.setrecord(*current_record)
                    record({
                        "kind": "cards-native-record-override-published-beta222",
                        "wins": current_record[0], "draws": current_record[1], "losses": current_record[2]
                    })
                    last_record = current_record
                except Exception as error:
                    record({"kind": "cards-native-record-override-publish-error-beta222", "error": str(error)})
            current_badge = read_local_badge()
            if current_badge is not None and current_badge != last_badge:
                try:
                    script.exports_sync.setbadge(*current_badge)
                    record({
                        "kind": "cards-native-badge-override-published-beta222",
                        "asset_id": current_badge[0], "resource_id": current_badge[1]
                    })
                    last_badge = current_badge
                except Exception as error:
                    record({"kind": "cards-native-badge-override-publish-error-beta222", "error": str(error)})
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            script.unload()
        except Exception:
            pass
        try:
            session.detach()
        except Exception:
            pass
        try:
            log_handle.flush()
            log_handle.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
