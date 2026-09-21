"""Minimal Zoom REST client used to mint meetings for calendar events.

Three modes, chosen by what ~/.config/gworkspace/zoom.json contains:

1. User mode - a fresh meeting per event, hosted by the Zoom user who consented. Needs a
   user-managed General app (any Zoom developer can create one, no admin involved; it stays in
   Development) with "Use Public Client OAuth" on, the scopes meeting:write:meeting and
   meeting:delete:meeting, and the redirect URL http://127.0.0.1:<port>/callback. Zoom accepts a
   loopback redirect only for public (PKCE) clients, so there is no client secret:
       {"client_id": "<Public Client ID>"}
   then `gworkspace zoom auth --port <port>` once; the token lives in zoom-token.json.

2. Server-to-Server mode - a fresh meeting per event through a Server-to-Server OAuth app (the Zoom
   role needs admin-level meeting permissions). Scopes: meeting:write:meeting:admin to create and
   meeting:delete:meeting:admin for the cleanup in delete_meeting (classic scopes:
   meeting:write:admin covers both):
       {"account_id": "...", "client_id": "...", "client_secret": "...", "user": "you@company.com"}
   The app acts for the whole account, so "user" pins the host of new meetings.

3. Static mode - no Zoom API at all; the same link (typically your Personal Meeting ID) is
   attached to every event:
       {"join_url": "https://company.zoom.us/j/1234567890?pwd=...", "meeting_id": "1234567890", "password": "..."}
   (meeting_id / password are optional and only used for the text in the event description.)

Env vars ZOOM_ACCOUNT_ID / ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET / ZOOM_USER / ZOOM_JOIN_URL override the file.
"""
import base64
import fcntl
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from .auth import CONFIG_DIR

ZOOM_CONFIG_FILE = CONFIG_DIR / "zoom.json"
ZOOM_TOKEN_FILE = CONFIG_DIR / "zoom-token.json"
AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
TOKEN_URL = "https://zoom.us/oauth/token"
API_BASE = "https://api.zoom.us/v2"
CREATE_SCOPES = ("meeting:write:meeting", "meeting:write:meeting:admin", "meeting:write", "meeting:write:admin")


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
    if cfg.get("client_id") and cfg.get("client_secret") and cfg.get("account_id"):
        cfg["_mode"] = "s2s"
    elif cfg.get("client_id") and not cfg.get("client_secret") and not cfg.get("account_id"):
        cfg["_mode"] = "user"
    elif cfg.get("join_url"):
        cfg["_mode"] = "static"
    else:
        raise ZoomError(
            f"No Zoom configuration found in {ZOOM_CONFIG_FILE}.\n"
            "Either (user mode) save the Public Client ID of a user-managed General app and run `gworkspace zoom auth`:\n"
            '  {"client_id": "<Public Client ID>"}\n'
            "or (Server-to-Server mode) save Server-to-Server OAuth credentials:\n"
            '  {"account_id": "...", "client_id": "...", "client_secret": "...", "user": "you@company.com"}\n'
            "or (static mode) save a permanent link such as your Personal Meeting ID:\n"
            '  {"join_url": "https://company.zoom.us/j/1234567890?pwd=...", "meeting_id": "1234567890", "password": "..."}'
        )
    return cfg


def _request(method: str, url: str, headers: dict, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise ZoomError(f"Zoom API {method} {url.split('?', 1)[0]} -> HTTP {e.code}: {detail}") from e


def _token_request(cfg: dict, params: dict) -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if cfg["_mode"] == "user":
        # public client: no secret and no Authorization header, the client id travels in the body
        params = {**params, "client_id": cfg["client_id"]}
    else:
        basic = base64.b64encode(f"{cfg['client_id']}:{cfg['client_secret']}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    return _request("POST", TOKEN_URL, headers=headers, data=urllib.parse.urlencode(params).encode())


# ── user mode: stored token with a rotating refresh token ──────────────────
@contextmanager
def _token_lock():
    """Zoom invalidates a refresh token the moment it is used, so two commands refreshing at once
    would strand one of them; serialize read-refresh-write across processes."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_DIR / "zoom-token.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _save_token(tok: dict) -> dict:
    stored = {
        "access_token": tok["access_token"],
        "refresh_token": tok["refresh_token"],
        "expires_at": int(time.time()) + int(tok.get("expires_in", 3600)),
        "scope": tok.get("scope", ""),
    }
    tmp = ZOOM_TOKEN_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(stored, f)
    os.replace(tmp, ZOOM_TOKEN_FILE)  # never leave a half-written file: the old refresh token is already dead
    return stored


def _user_token(cfg: dict) -> dict:
    with _token_lock():
        if not ZOOM_TOKEN_FILE.exists():
            raise ZoomError("Zoom is not authorized yet. Run: gworkspace zoom auth --port <port>")
        stored = json.loads(ZOOM_TOKEN_FILE.read_text())
        if stored.get("expires_at", 0) - 60 > time.time():
            return stored
        try:
            tok = _token_request(cfg, {"grant_type": "refresh_token", "refresh_token": stored["refresh_token"]})
        except ZoomError as e:
            raise ZoomError(f"{e}\nThe Zoom consent expired or was revoked. Run: gworkspace zoom auth --port <port>") from e
        return _save_token(tok)


def _token(cfg: dict) -> dict:
    """{"access_token", "scope"} for either API mode."""
    if cfg["_mode"] == "user":
        return _user_token(cfg)
    return _token_request(cfg, {"grant_type": "account_credentials", "account_id": cfg["account_id"]})


def get_access_token(cfg: dict) -> str:
    return _token(cfg)["access_token"]


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" not in query and "error" not in query:
            self.send_error(404)
            return
        self.server.result = {k: v[0] for k, v in query.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"gworkspace: Zoom authorization received, you can close this tab.")

    def log_message(self, *args):
        pass


def run_auth(port: int, open_browser: bool = True) -> None:
    """Interactive consent for user mode; replaces zoom-token.json.

    The redirect URL http://127.0.0.1:<port>/callback must be registered in the Zoom app. Headless
    machine: `open_browser=False`, forward the port from a machine with a browser
    (`ssh -N -L PORT:127.0.0.1:PORT host`) and open the printed URL there.
    """
    cfg = load_config()
    if cfg["_mode"] != "user":
        raise ZoomError(f"`zoom auth` is for user mode only; {ZOOM_CONFIG_FILE} is in {cfg['_mode']} mode.")
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cfg["client_id"], "redirect_uri": redirect_uri,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })

    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.result = None
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    print(f"Open this URL and approve with your Zoom account:\n{url}")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        while server.result is None:
            server.handle_request()
    finally:
        server.server_close()

    result = server.result
    if result.get("error"):
        raise ZoomError(f"Zoom refused the authorization: {result['error']}")
    if result.get("state") != state:
        raise ZoomError("OAuth state mismatch; run `gworkspace zoom auth` again.")
    tok = _token_request(cfg, {"grant_type": "authorization_code", "code": result["code"],
                               "redirect_uri": redirect_uri, "code_verifier": verifier})
    with _token_lock():
        _save_token(tok)
    print(f"Authorized. Token saved to {ZOOM_TOKEN_FILE}")


def check() -> dict:
    """Validate the configuration without touching any meeting.

    The API modes obtain a token (proves the credentials, the consent, and that the app is active;
    in user mode it also renews the refresh token) and report the granted scopes; static mode only
    echoes the configured link.
    """
    cfg = load_config()
    if cfg["_mode"] == "static":
        return {"mode": "static", "join_url": cfg["join_url"]}
    scopes = _token(cfg).get("scope", "").split()
    return {
        "mode": cfg["_mode"],
        "host": "the user who ran `zoom auth`" if cfg["_mode"] == "user" else cfg.get("user") or "",
        "scopes": scopes,
        "can_create": any(s in scopes for s in CREATE_SCOPES),
    }


def _minutes_between(start: str, end: str) -> int:
    delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    return max(1, int(delta.total_seconds() // 60))


def create_meeting(topic: str, start: str, end: str, timezone: str,
                   agenda: str = "", host: str | None = None) -> dict:
    """Schedule a Zoom meeting; returns Zoom's meeting object (id, join_url, password, ...).

    `start`/`end` are local ISO datetimes in `timezone` (same strings we send to Google Calendar).
    Host: user mode -> the consenting user ("me"); Server-to-Server -> zoom.json "user", else the
    `host` argument (such apps need a real user).
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
    host = "me" if cfg["_mode"] == "user" else cfg.get("user") or host or "me"
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
