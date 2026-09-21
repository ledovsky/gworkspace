import json
import socket
import threading
import time
import urllib.parse
import urllib.request

import pytest

from gworkspace import zoom
from gworkspace.cli import build_parser, cmd_zoom_check

API_CFG = {"account_id": "acc", "client_id": "cid", "client_secret": "sec", "user": "me@example.com"}
USER_CFG = {"client_id": "public-cid"}


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "zoom.json"
    monkeypatch.setattr(zoom, "ZOOM_CONFIG_FILE", path)
    monkeypatch.setattr(zoom, "ZOOM_TOKEN_FILE", tmp_path / "zoom-token.json")
    monkeypatch.setattr(zoom, "CONFIG_DIR", tmp_path)
    for key in ("ACCOUNT_ID", "CLIENT_ID", "CLIENT_SECRET", "USER", "JOIN_URL"):
        monkeypatch.delenv(f"ZOOM_{key}", raising=False)
    return path


@pytest.fixture
def requests(monkeypatch):
    calls = []

    def fake_request(method, url, headers, data=None):
        calls.append({"method": method, "url": url, "headers": headers, "data": data})
        if url.startswith(zoom.TOKEN_URL):
            form = dict(urllib.parse.parse_qsl((data or b"").decode()))
            calls[-1]["form"] = form
            if form["grant_type"] == "account_credentials":
                return {"access_token": "tok", "scope": "meeting:write:meeting:admin meeting:delete:meeting:admin"}
            n = sum(1 for c in calls if "form" in c)
            return {"access_token": f"user-tok-{n}", "refresh_token": f"refresh-{n}", "expires_in": 3600,
                    "scope": "meeting:write:meeting meeting:delete:meeting"}
        return {"id": 123, "join_url": "https://zoom.us/j/123", "password": "pw"}

    monkeypatch.setattr(zoom, "_request", fake_request)
    return calls


def test_missing_config_explains_both_modes(config_file):
    with pytest.raises(zoom.ZoomError) as exc:
        zoom.load_config()
    assert "account_id" in str(exc.value) and "join_url" in str(exc.value)


def test_static_mode_never_calls_zoom(config_file, requests):
    config_file.write_text(json.dumps({"join_url": "https://zoom.us/j/1", "meeting_id": "1"}))
    meeting = zoom.create_meeting("T", "2026-01-01T10:00:00", "2026-01-01T10:30:00", "Europe/Moscow")
    assert meeting["join_url"] == "https://zoom.us/j/1" and meeting["static"] is True
    assert zoom.check() == {"mode": "static", "join_url": "https://zoom.us/j/1"}
    assert requests == []


def test_create_meeting_posts_to_configured_host(config_file, requests):
    config_file.write_text(json.dumps(API_CFG))
    meeting = zoom.create_meeting("Sync", "2026-01-01T10:00:00", "2026-01-01T10:45:00", "Europe/Moscow",
                                  host="other@example.com")
    assert meeting["join_url"] == "https://zoom.us/j/123"
    token_call, create_call = requests
    assert token_call["form"] == {"grant_type": "account_credentials", "account_id": "acc"}
    assert token_call["headers"]["Authorization"].startswith("Basic ")
    assert create_call["url"] == f"{zoom.API_BASE}/users/me%40example.com/meetings"
    assert create_call["headers"]["Authorization"] == "Bearer tok"
    body = json.loads(create_call["data"])
    assert body == {"topic": "Sync", "type": 2, "start_time": "2026-01-01T10:00:00", "duration": 45,
                    "timezone": "Europe/Moscow", "agenda": ""}


def test_host_falls_back_to_argument(config_file, requests):
    config_file.write_text(json.dumps({k: v for k, v in API_CFG.items() if k != "user"}))
    zoom.create_meeting("T", "2026-01-01T10:00:00", "2026-01-01T10:30:00", "UTC", host="g@example.com")
    assert requests[1]["url"].endswith("/users/g%40example.com/meetings")


def test_check_reports_scopes(config_file, requests):
    config_file.write_text(json.dumps(API_CFG))
    info = zoom.check()
    assert info["mode"] == "s2s" and info["host"] == "me@example.com" and info["can_create"] is True
    assert "meeting:delete:meeting:admin" in info["scopes"]
    assert len(requests) == 1  # a token request only, no meeting is touched


def test_zoom_check_command_fails_without_write_scope(config_file, monkeypatch, capsys):
    config_file.write_text(json.dumps(API_CFG))
    monkeypatch.setattr(zoom, "_request", lambda *a, **kw: {"access_token": "tok", "scope": "user:read:user:admin"})
    args = build_parser().parse_args(["zoom", "check"])
    assert args.func is cmd_zoom_check
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert "meeting:write:meeting:admin" in capsys.readouterr().err


def _write_token(path, expires_in):
    path.write_text(json.dumps({"access_token": "old-access", "refresh_token": "old-refresh",
                                "expires_at": int(time.time()) + expires_in, "scope": "meeting:write:meeting"}))


def test_user_mode_needs_auth_first(config_file, requests):
    config_file.write_text(json.dumps(USER_CFG))
    with pytest.raises(zoom.ZoomError, match="zoom auth"):
        zoom.create_meeting("T", "2026-01-01T10:00:00", "2026-01-01T10:30:00", "UTC")
    assert requests == []


def test_user_mode_reuses_a_fresh_token_and_hosts_as_me(config_file, requests):
    config_file.write_text(json.dumps(USER_CFG))
    _write_token(zoom.ZOOM_TOKEN_FILE, expires_in=1800)
    zoom.create_meeting("T", "2026-01-01T10:00:00", "2026-01-01T10:30:00", "UTC", host="g@example.com")
    (create_call,) = requests
    assert create_call["url"] == f"{zoom.API_BASE}/users/me/meetings"
    assert create_call["headers"]["Authorization"] == "Bearer old-access"


def test_user_mode_refresh_stores_the_rotated_refresh_token(config_file, requests):
    config_file.write_text(json.dumps(USER_CFG))
    _write_token(zoom.ZOOM_TOKEN_FILE, expires_in=-10)
    assert zoom.get_access_token(zoom.load_config()) == "user-tok-1"
    assert requests[0]["form"] == {"grant_type": "refresh_token", "refresh_token": "old-refresh",
                                   "client_id": "public-cid"}
    assert "Authorization" not in requests[0]["headers"]  # public client: no secret anywhere
    stored = json.loads(zoom.ZOOM_TOKEN_FILE.read_text())
    assert stored["refresh_token"] == "refresh-1" and stored["expires_at"] > time.time() + 3000
    assert zoom.ZOOM_TOKEN_FILE.stat().st_mode & 0o777 == 0o600
    assert zoom.check()["can_create"] is True and len(requests) == 1  # the new token is reused


def test_zoom_auth_roundtrip(config_file, requests, capsys):
    config_file.write_text(json.dumps(USER_CFG))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    errors = []

    def run():
        try:
            zoom.run_auth(port=port, open_browser=False)
        except Exception as e:  # surfaced in the main thread below
            errors.append(e)

    thread = threading.Thread(target=run)
    thread.start()
    consent_url = ""
    for _ in range(100):
        consent_url = next((l for l in capsys.readouterr().out.splitlines() if l.startswith("https://")), consent_url)
        if consent_url:
            break
        time.sleep(0.05)
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(consent_url).query))
    assert query["redirect_uri"] == f"http://127.0.0.1:{port}/callback" and query["code_challenge_method"] == "S256"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=the-code&state={query['state']}").read()
            break
        except OSError:
            time.sleep(0.05)
    thread.join(timeout=10)
    assert not errors and not thread.is_alive()
    form = requests[0]["form"]
    assert form["grant_type"] == "authorization_code" and form["code"] == "the-code"
    assert form["client_id"] == "public-cid" and query["client_id"] == "public-cid"
    assert form["redirect_uri"] == query["redirect_uri"] and len(form["code_verifier"]) >= 43
    assert json.loads(zoom.ZOOM_TOKEN_FILE.read_text())["refresh_token"] == "refresh-1"


def test_zoom_auth_rejects_other_modes(config_file):
    config_file.write_text(json.dumps(API_CFG))
    with pytest.raises(zoom.ZoomError, match="user mode only"):
        zoom.run_auth(port=1)
