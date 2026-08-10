from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def request_json(url: str, *, method: str = "GET", body: dict | None = None, timeout: float = 10.0) -> dict:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = response.read()
    if not payload:
        return {}
    return json.loads(payload.decode("utf-8"))


def fmt_int(value: object) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def fmt_uptime(seconds: object) -> str:
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        total = 0
    days, total = divmod(total, 86400)
    hours, total = divmod(total, 3600)
    minutes, _ = divmod(total, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    return f"{hours}h {minutes}m"


def embed(metrics: dict) -> dict:
    health = metrics.get("serviceHealth", {}) if isinstance(metrics.get("serviceHealth"), dict) else {}
    health_text = "\n".join(f"**{name}:** {state}" for name, state in health.items()) or "No health data"
    return {
        "title": "FIFA 14 Local FUT BETA",
        "description": f"Backend `{metrics.get('version', 'unknown')}`",
        "fields": [
            {"name": "Players Online", "value": fmt_int(metrics.get("playersOnline")), "inline": True},
            {"name": "Active Matches", "value": fmt_int(metrics.get("activeMatches")), "inline": True},
            {"name": "Uptime", "value": fmt_uptime(metrics.get("uptimeSeconds")), "inline": True},
            {"name": "Coins in Circulation", "value": fmt_int(metrics.get("coinsInCirculation")), "inline": True},
            {"name": "Players Packed", "value": fmt_int(metrics.get("playersPacked")), "inline": True},
            {"name": "Registered Clubs", "value": fmt_int(metrics.get("registeredClubs")), "inline": True},
            {"name": "Clubs Active Today", "value": fmt_int(metrics.get("clubsActiveToday")), "inline": True},
            {"name": "New Accounts Today", "value": fmt_int(metrics.get("newAccountsToday")), "inline": True},
            {"name": "Packs Opened Today", "value": fmt_int(metrics.get("packsOpenedToday")), "inline": True},
            {"name": "Highest Rated Packed Today", "value": fmt_int(metrics.get("highestRatedPackedToday")), "inline": True},
            {"name": "Matches Completed Today", "value": fmt_int(metrics.get("matchesCompletedToday")), "inline": True},
            {"name": "Matches Abandoned Today", "value": fmt_int(metrics.get("matchesAbandonedToday")), "inline": True},
            {"name": "Match Coins Awarded Today", "value": fmt_int(metrics.get("matchCoinsAwardedToday")), "inline": True},
            {"name": "Service Health", "value": health_text, "inline": False},
            {"name": "Notes", "value": "BETA 2: seasons/tournaments bootstrap + Store artwork + local progression. Transfer Market and Discord OAuth account linking are staged for later BETA milestones.", "inline": False},
        ],
        "footer": {"text": "FIFA 14 Local FUT revival • local/private BETA"},
    }


def webhook_message_url(webhook_url: str, message_id: str) -> str:
    parts = urlsplit(webhook_url)
    base_path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, f"{base_path}/messages/{message_id}", "", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish FIFA 14 Local FUT BETA metrics to a Discord webhook")
    parser.add_argument("--metrics-url", default="http://127.0.0.1:8099/local/beta/metrics")
    parser.add_argument("--webhook-url", default=os.environ.get("FIFA14_DISCORD_WEBHOOK_URL", ""))
    parser.add_argument("--state-file", default=str(Path.home() / ".fifa14-local-fut-discord-status.json"))
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.webhook_url:
        raise SystemExit("Set FIFA14_DISCORD_WEBHOOK_URL or pass --webhook-url. The URL is never stored in the FUT database.")
    state_path = Path(args.state_file)
    message_id = None
    if state_path.exists():
        try:
            message_id = json.loads(state_path.read_text(encoding="utf-8")).get("messageId")
        except Exception:
            message_id = None

    while True:
        try:
            metrics = request_json(args.metrics_url)
            payload = {"username": "FIFA 14 FUT BETA", "embeds": [embed(metrics)]}
            if message_id:
                request_json(webhook_message_url(args.webhook_url, str(message_id)), method="PATCH", body=payload)
            else:
                sep = "&" if "?" in args.webhook_url else "?"
                created = request_json(args.webhook_url + sep + "wait=true", method="POST", body=payload)
                message_id = created.get("id")
                if message_id:
                    state_path.write_text(json.dumps({"messageId": str(message_id)}), encoding="utf-8")
            print(f"Discord status updated: players={metrics.get('playersOnline', 0)} matches={metrics.get('activeMatches', 0)}")
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
            print(f"Discord status update failed: {error}")
        if args.once:
            return 0
        time.sleep(max(30, int(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
