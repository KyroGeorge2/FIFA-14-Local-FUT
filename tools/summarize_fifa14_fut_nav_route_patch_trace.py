#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


def load_json_lines(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.is_file():
        return events
    for number, raw_line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            item.setdefault("_line", number)
            events.append(item)
    return events


def text(event: dict[str, Any], key: str) -> str:
    value = event.get(key)
    return "" if value is None else str(value)


def contains_path(event: dict[str, Any], needle: str) -> bool:
    needle = needle.lower()
    return any(needle in text(event, key).lower() for key in ("path", "url", "request_path", "body_text"))


def compact(event: dict[str, Any], source: str) -> dict[str, Any]:
    result: dict[str, Any] = {"source": source, "line": event.get("_line"), "kind": event.get("kind")}
    for key in (
        "outer_time", "time", "time_ms", "view", "layer", "event", "action", "name",
        "operation", "operation_index", "path", "method", "status", "succeeded", "result",
        "retval_u32", "fcc_login1_active", "fcc_login2_active", "fut_packselect_active", "message",
        "gate", "original", "emitted", "overridden", "force_local_true", "thread_id",
        "original_view", "replacement_view", "post_match_redirected", "post_match_guard_active",
        "generation", "redirect_count", "remaining_ms",
    ):
        if key in event:
            result[key] = event[key]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize the passive FIFA 14 FUT NAV route-patch experiment")
    parser.add_argument("--helper-log", required=True, type=Path)
    parser.add_argument("--probe-log", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    helper = load_json_lines(args.helper_log)
    probe = load_json_lines(args.probe_log)
    loads = [e for e in helper if e.get("kind") == "fifa-nav-load-view"]
    views = [text(e, "view") for e in loads]
    lower_views = [v.lower() for v in views]

    auth_requests = [e for e in probe if e.get("kind") == "fut-ut-auth-request" or contains_path(e, "/ut/auth")]
    delete_auth = [e for e in probe if contains_path(e, "/ut/delete/auth")]
    get_user_success = [
        e for e in helper
        if e.get("kind") == "cards-get-user-info-parser-leave" and bool(e.get("succeeded"))
    ]
    manual_actions = [
        e for e in helper
        if e.get("kind") in {
            "authenticated-icebreaker-action-enter",
            "authenticated-icebreaker-action-leave",
            "authenticated-icebreaker-action-error",
        }
    ]
    store_wrapper_events = [e for e in helper if e.get("kind") in {"cards-store-local-wrapper-enter", "cards-store-local-wrapper-leave"}]
    store_gate_events = [e for e in helper if e.get("kind") == "cards-store-local-gate-result"]
    store_number_args = [e for e in helper if e.get("kind") == "cards-store-script-number-arg"]
    user_credits_parser_leaves = [e for e in helper if e.get("kind") == "cards-user-credits-parser-leave"]
    purchase_pack_enters = [e for e in helper if e.get("kind") == "cards-store-local-wrapper-enter" and text(e, "name") == "PurchasePack"]
    store_write_requests = [
        e for e in probe
        if text(e, "method").upper() in {"POST", "PUT"} and contains_path(e, "/store/")
    ]
    hub_data_responses = [e for e in probe if contains_path(e, "/clientdata/userHubData") and e.get("kind") == "fut-http-response"]
    packlist_requests = [e for e in probe if contains_path(e, "icebreakerpacklist.json")]
    packlist_responses = [e for e in probe if e.get("kind") == "fut-icebreaker-packlist-response"]
    locstrings_responses = [e for e in probe if e.get("kind") == "fut-locstrings-http-response"]
    packlist_response_parser_enters = [
        e for e in helper if e.get("kind") == "cards-icebreaker-packlist-response-parser-enter"
    ]
    packlist_response_parser_leaves = [
        e for e in helper if e.get("kind") == "cards-icebreaker-packlist-response-parser-leave"
    ]
    pack_entry_parser_enters = [
        e for e in helper if e.get("kind") == "cards-icebreaker-pack-entry-parser-enter"
    ]
    pack_entry_parser_leaves = [
        e for e in helper if e.get("kind") == "cards-icebreaker-pack-entry-parser-leave"
    ]
    packlist_json_keys = [
        e for e in helper if e.get("kind") == "cards-icebreaker-packlist-json-key"
    ]
    icebreaker_wrapper_enters = [
        e for e in helper if e.get("kind") == "cards-icebreaker-manager-wrapper-enter"
    ]
    retrieve_pack_wrapper_enters = [
        e for e in icebreaker_wrapper_enters if "RetrievePack wrapper" in text(e, "name")
    ]
    retrieve_packlist_wrapper_enters = [
        e for e in icebreaker_wrapper_enters if "RetrievePackList wrapper" in text(e, "name")
    ]
    build_squad_wrapper_enters = [
        e for e in icebreaker_wrapper_enters if "BuildSquad wrapper" in text(e, "name")
    ]
    clear_squad_wrapper_enters = [
        e for e in icebreaker_wrapper_enters if "ClearSquad wrapper" in text(e, "name")
    ]
    loading_screen_events = [
        e for e in helper
        if e.get("kind") == "trusted-console-screen-event"
        and text(e, "event") in {"ShowLoadingIcon", "HideLoadingIcon", "ResetLoadingIcon", "StartNetworkOperation", "EndNetworkOperation"}
    ]
    loading_event_names = [text(e, "event") for e in loading_screen_events]
    saw_end_network_operation = "EndNetworkOperation" in loading_event_names
    saw_loading_hide_or_reset = any(name in {"HideLoadingIcon", "ResetLoadingIcon"} for name in loading_event_names)

    reached_login1 = any("fcc_login1" in v for v in lower_views)
    reached_login2 = any("fcc_login2" in v for v in lower_views)
    reached_logout = any("fcc_logout" in v for v in lower_views)
    postmatch_guard_armed = [e for e in helper if e.get("kind") == "fifa-postmatch-return-guard-armed-beta223"]
    postmatch_logout_redirects = [e for e in helper if e.get("kind") == "fifa-postmatch-fcc-logout-redirect-beta223"]
    match_end_requests = [
        e for e in probe
        if text(e, "kind") == "http-probe"
        and text(e, "method").upper() == "PUT"
        and contains_path(e, "/ut/game/fifa14/match/end")
    ]
    reached_pack_select = any("futpackselect" in v for v in lower_views)
    menu_loading_views = [v for v in views if "menu_loading" in v.lower()]
    reached_create_club = any("futcreateclub" in v for v in lower_views)
    reached_fut_hub = any(
        any(token in v for token in ("futgamehub", "screens/fut_hub", "screens/futhub"))
        for v in lower_views
    )

    if manual_actions:
        verdict = "NAV_ROUTE_PATCH_TEST_CONTAMINATED_BY_MANUAL_ACTION"
    elif reached_fut_hub:
        verdict = "NAV_ROUTE_PATCH_REACHED_FUT_HUB"
    elif reached_create_club:
        verdict = "NAV_ROUTE_PATCH_COMPLETED_ICEBREAKER_TO_CREATE_CLUB"
    elif build_squad_wrapper_enters:
        verdict = "CAPTAIN_PACK_RETRIEVED_AND_BUILD_SQUAD_ENTERED"
    elif retrieve_pack_wrapper_enters:
        verdict = "CAPTAIN_SELECTION_SUBMITTED_RETRIEVE_PACK_PENDING"
    elif reached_pack_select and saw_end_network_operation and saw_loading_hide_or_reset:
        verdict = "CAPTAIN_SELECTOR_LOADING_GATE_CLEARED_AWAITING_OR_MISSING_SELECTION"
    elif reached_pack_select and saw_end_network_operation:
        verdict = "CAPTAIN_NETWORK_OPERATION_ENDED_BUT_LOADING_HIDE_NOT_OBSERVED"
    elif reached_pack_select and len(pack_entry_parser_leaves) >= 4:
        verdict = "CAPTAIN_CONTRACT_PARSED_FOUR_RETAIL_ENTRIES"
    elif reached_pack_select:
        verdict = "NAV_ROUTE_PATCH_REACHED_CAPTAIN_SELECTION"
    elif packlist_requests:
        verdict = "NAV_ROUTE_PATCH_ENTERED_ICEBREAKER_PACK_PIPELINE_WITHOUT_VISIBLE_PACK_SELECT"
    elif reached_login2:
        verdict = "NATURAL_ADVANCE_STILL_REACHED_FCC_LOGIN2"
    elif reached_login1 and get_user_success:
        verdict = "AUTHENTICATED_FCC_LOGIN1_STALLED_BEFORE_ANY_NEXT_VIEW"
    elif auth_requests:
        verdict = "UT_AUTH_REACHED_BUT_FCC_LOGIN1_NOT_REACHED"
    else:
        verdict = "UT_AUTH_NOT_OBSERVED"

    interesting_helper = []
    wanted_kinds = {
        "native-fut-nav-route-patch-trace-ready",
        "fifa-nav-load-view",
        "fifa-nav-unload-view",
        "cards-get-user-info-parser-leave",
        "cards-icebreaker-packlist-path-handler-enter",
        "cards-icebreaker-packlist-path-handler-leave",
        "cards-icebreaker-packlist-response-parser-enter",
        "cards-icebreaker-packlist-response-parser-leave",
        "cards-icebreaker-pack-entry-parser-enter",
        "cards-icebreaker-pack-entry-parser-leave",
        "cards-icebreaker-packlist-json-key",
        "cards-icebreaker-manager-wrapper-enter",
        "cards-icebreaker-manager-wrapper-leave",
        "cards-operation-descriptor-status",
        "cards-plugin-deinitialize-enter",
        "cards-store-local-bool-push-signature",
        "cards-store-local-wrapper-signature",
        "cards-store-local-hooks-ready",
        "cards-store-local-wrapper-enter",
        "cards-store-local-wrapper-leave",
        "cards-store-local-gate-result",
        "cards-store-script-number-arg-signature",
        "cards-store-script-number-arg-hook-ready",
        "cards-store-script-number-arg",
        "cards-user-credits-parser-signature",
        "cards-user-credits-parser-hook-ready",
        "cards-user-credits-parser-enter",
        "cards-user-credits-parser-leave",
        "trusted-console-screen-event",
        "fifa-postmatch-return-guard-armed-beta223",
        "fifa-postmatch-fcc-logout-redirect-beta223",
    }
    for event in helper:
        kind = text(event, "kind")
        if kind in wanted_kinds or "icebreaker" in kind.lower() or "logout" in kind.lower():
            interesting_helper.append(compact(event, "helper"))

    interesting_probe = []
    for event in probe:
        if (
            contains_path(event, "/ut/auth")
            or contains_path(event, "icebreaker")
            or contains_path(event, "/squad/list")
            or contains_path(event, "/ut/delete/auth")
            or contains_path(event, "/clientdata/userHubData")
            or contains_path(event, "/store/")
            or contains_path(event, "/user/credits")
        ):
            interesting_probe.append(compact(event, "probe"))

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "experiment": {
            "route_patch": "futLogIn1 / natural advance: futLogIn2 -> iceBreaker",
            "manual_nav_action_expected": False,
            "direct_screen_load_expected": False,
            "packselect_patch": "exact non-crashing v6 cinematic baseline",
            "login1_patch": "same-length fcc_login1 ActionScript method substitution: ShowLoadingIcon -> HideLoadingIcon",
        },
        "indicators": {
            "ut_auth_requests": len(auth_requests),
            "get_user_info_successes": len(get_user_success),
            "manual_icebreaker_action_events": len(manual_actions),
            "store_wrapper_events": len(store_wrapper_events),
            "store_gate_events": len(store_gate_events),
            "store_script_number_args": len(store_number_args),
            "user_credits_parser_leaves": len(user_credits_parser_leaves),
            "last_user_credits_native_fields": ({
                key: user_credits_parser_leaves[-1].get(key)
                for key in ("coins_20", "points_24", "field_28", "field_2c", "result_u8")
            } if user_credits_parser_leaves else None),
            "purchase_pack_wrapper_enters": len(purchase_pack_enters),
            "store_post_put_requests": len(store_write_requests),
            "hub_data_responses": len(hub_data_responses),
            "reached_fcc_login1": reached_login1,
            "reached_fcc_login2": reached_login2,
            "reached_fcc_logout": reached_logout,
            "postmatch_match_end_requests": len(match_end_requests),
            "postmatch_return_guard_arms": len(postmatch_guard_armed),
            "postmatch_fcc_logout_redirects": len(postmatch_logout_redirects),
            "postmatch_return_guard_triggered": bool(postmatch_logout_redirects),
            "reached_fut_pack_select": reached_pack_select,
            "menu_loading_view_count": len(menu_loading_views),
            "icebreaker_packlist_requests": len(packlist_requests),
            "icebreaker_packlist_responses": len(packlist_responses),
            "icebreaker_locstrings_responses": len(locstrings_responses),
            "icebreaker_packlist_response_parser_enters": len(packlist_response_parser_enters),
            "icebreaker_packlist_response_parser_leaves": len(packlist_response_parser_leaves),
            "icebreaker_pack_entry_parser_enters": len(pack_entry_parser_enters),
            "icebreaker_pack_entry_parser_leaves": len(pack_entry_parser_leaves),
            "icebreaker_packlist_json_key_events": len(packlist_json_keys),
            "icebreaker_packlist_json_keys": sorted({text(e, "key") for e in packlist_json_keys if text(e, "key")}),
            "retrieve_packlist_wrapper_enters": len(retrieve_packlist_wrapper_enters),
            "retrieve_pack_wrapper_enters": len(retrieve_pack_wrapper_enters),
            "build_squad_wrapper_enters": len(build_squad_wrapper_enters),
            "clear_squad_wrapper_enters": len(clear_squad_wrapper_enters),
            "packselect_end_network_operation_observed": saw_end_network_operation,
            "packselect_loading_hide_or_reset_observed": saw_loading_hide_or_reset,
            "packselect_loading_screen_events": loading_event_names,
            "reached_create_club": reached_create_club,
            "reached_fut_hub": reached_fut_hub,
            "delete_auth_requests": len(delete_auth),
        },
        "served_packlist_documents": [e.get("response_document") for e in packlist_responses],
        "loaded_views": views,
        "helper_kind_counts": dict(Counter(text(e, "kind") for e in helper)),
        "probe_kind_counts": dict(Counter(text(e, "kind") for e in probe)),
        "timeline": sorted(
            interesting_helper + interesting_probe,
            key=lambda e: (text(e, "outer_time") or text(e, "time"), e.get("source", ""), e.get("line", 0)),
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
