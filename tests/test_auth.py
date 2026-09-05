import json

import pytest

from gworkspace import auth


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(auth, "CREDS_FILE", tmp_path / "credentials.json")
    (tmp_path / "tokens").mkdir()
    return tmp_path


def write_token(config_dir, profile, scopes):
    (config_dir / "tokens" / f"{profile}.json").write_text(json.dumps({
        "token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s",
        "scopes": scopes, "token_uri": "https://oauth2.googleapis.com/token",
    }))


def test_missing_scopes_by_name():
    granted = [auth.SCOPES["gmail"], auth.SCOPES["calendar"]]
    assert auth.missing_scopes(granted, ("gmail",)) == []
    assert auth.missing_scopes(granted, ("gmail", "drive")) == [auth.SCOPES["drive"]]
    assert auth.missing_scopes(None, ("calendar",)) == [auth.SCOPES["calendar"]]


def test_no_token_is_explicit(config_dir):
    with pytest.raises(auth.AuthError) as exc:
        auth.get_credentials("work", required=("gmail",))
    msg = str(exc.value)
    assert "no token for profile 'work'" in msg
    assert "gworkspace auth --profile work" in msg


def test_token_lacking_scope_is_rejected_before_any_api_call(config_dir):
    old = [s for name, s in auth.SCOPES.items() if name != "drive"]
    write_token(config_dir, "work", old)
    with pytest.raises(auth.AuthError) as exc:
        auth.get_credentials("work", required=("drive",))
    msg = str(exc.value)
    assert "token for profile 'work' lacks https://www.googleapis.com/auth/drive" in msg
    assert "gworkspace auth --profile work" in msg


def test_token_with_scope_passes_the_check(config_dir, monkeypatch):
    write_token(config_dir, "work", auth.ALL_SCOPES)
    # google-auth treats a token without expiry as expired -> it would refresh over the network.
    # Stub the refresh: we only test the scope gate and that the token is written back.
    refreshed = []
    monkeypatch.setattr(auth.Credentials, "refresh", lambda self, request: refreshed.append(self))
    creds = auth.get_credentials("work", required=("drive", "gmail"))
    assert len(refreshed) == 1
    assert set(creds.scopes) == set(auth.ALL_SCOPES)
    assert json.loads((config_dir / "tokens" / "work.json").read_text())["scopes"] == auth.ALL_SCOPES
