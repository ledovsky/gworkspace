"""Minimal Zoom REST client (Server-to-Server OAuth) used to mint meetings for calendar events.

Two modes, chosen by what ~/.config/gworkspace/zoom.json contains:

1. API mode - a fresh meeting is minted per event (needs a Server-to-Server OAuth app, which a
   Zoom admin must create or allow you to create; scope meeting:write:admin or meeting:write):
       {"account_id": "...", "client_id": "...", "client_secret": "...", "user": "you@company.com"}
   Env vars ZOOM_ACCOUNT_ID / ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET / ZOOM_USER override the file.

2. Static mode - no Zoom API at all; the same link (typically your Personal Meeting ID) is
   attached to every event:
       {"join_url": "https://company.zoom.us/j/1234567890?pwd=...", "meeting_id": "1234567890", "password": "..."}
   (meeting_id / password are optional and only used for the text in the event description.)
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from .auth import CONFIG_DIR

ZOOM_CONFIG_FILE = CONFIG_DIR / "zoom.json"
TOKEN_URL = "https://zoom.us/oauth/token"
API_BASE = "https://api.zoom.us/v2"


class ZoomError(RuntimeError):
    pass


def load_config() -> dict:
    cfg = {}
    if ZOOM_CONFIG_FILE.exists():
        cfg = json.loads(ZOOM_CONFIG_FILE.read_text())
    for key in ("account_id", "client_id", "client_secret", "user", "join_url"):
        env = os.environ.get(f"ZOOM_{key.upper()}")
        if env:
            cfg[key] = env
    has_api = all(cfg.get(k) for k in ("account_id", "client_id", "client_secret"))
    if not has_api and not cfg.get("join_url"):
        raise ZoomError(
            f"No Zoom configuration found in {ZOOM_CONFIG_FILE}.\n"
            "Either (API mode) save Server-to-Server OAuth credentials:\n"
            '  {"account_id": "...", "client_id": "...", "client_secret": "...", "user": "you@company.com"}\n'
            "or (static mode) save a permanent link such as your Personal Meeting ID:\n"
            '  {"join_url": "https://company.zoom.us/j/1234567890?pwd=...", "meeting_id": "1234567890", "password": "..."}'
        )
    cfg["_mode"] = "api" if has_api else "static"
    return cfg


def _request(method: str, url: str, headers: dict, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise ZoomError(f"Zoom API {method} {url} -> HTTP {e.code}: {detail}") from e


def get_access_token(cfg: dict) -> str:
    basic = base64.b64encode(f"{cfg['client_id']}:{cfg['client_secret']}".encode()).decode()
    params = urllib.parse.urlencode({"grant_type": "account_credentials", "account_id": cfg["account_id"]})
    tok = _request(
        "POST",
        f"{TOKEN_URL}?{params}",
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        data=b"",
    )
    return tok["access_token"]


def _minutes_between(start: str, end: str) -> int:
    delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    return max(1, int(delta.total_seconds() // 60))


def create_meeting(topic: str, start: str, end: str, timezone: str,
                   agenda: str = "", host: str | None = None) -> dict:
    """Schedule a Zoom meeting; returns Zoom's meeting object (id, join_url, password, ...).

    `start`/`end` are local ISO datetimes in `timezone` (same strings we send to Google Calendar).
    Host resolution: zoom.json "user" -> `host` argument -> "me" (S2S apps generally need a real user).
    """
    cfg = load_config()
    if cfg["_mode"] == "static":
        return {
            "id": cfg.get("meeting_id", ""),
            "join_url": cfg["join_url"],
            "password": cfg.get("password", ""),
            "static": True,
        }
    token = get_access_token(cfg)
    host = cfg.get("user") or host or "me"
    body = {
        "topic": topic,
        "type": 2,  # scheduled meeting
        "start_time": datetime.fromisoformat(start).strftime("%Y-%m-%dT%H:%M:%S"),
        "duration": _minutes_between(start, end),
        "timezone": timezone,
        "agenda": agenda or "",
    }
    return _request(
        "POST",
        f"{API_BASE}/users/{urllib.parse.quote(host)}/meetings",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(body).encode(),
    )


def delete_meeting(meeting_id) -> None:
    """Best-effort cleanup (used when the calendar insert fails after Zoom already minted a meeting)."""
    try:
        cfg = load_config()
        if cfg["_mode"] == "static" or not meeting_id:
            return
        token = get_access_token(cfg)
        _request("DELETE", f"{API_BASE}/meetings/{meeting_id}",
                 headers={"Authorization": f"Bearer {token}"})
    except ZoomError:
        pass
