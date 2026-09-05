"""OAuth credentials for gworkspace profiles.

Files under ~/.config/gworkspace/:
  credentials.json        your own OAuth client (Desktop app) from Google Cloud Console
  tokens/<profile>.json   one user token per profile, written by `gworkspace auth`
"""
import json
import sys
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Scope name -> OAuth scope. `gworkspace auth` always requests all of them; every command declares
# the names it needs so a token minted before a scope was added keeps working for everything else.
SCOPES = {
    "gmail": "https://www.googleapis.com/auth/gmail.modify",
    "calendar": "https://www.googleapis.com/auth/calendar",
    "contacts": "https://www.googleapis.com/auth/contacts.readonly",
    "directory": "https://www.googleapis.com/auth/directory.readonly",
    "drive": "https://www.googleapis.com/auth/drive",
}
ALL_SCOPES = list(SCOPES.values())

CONFIG_DIR = Path.home() / ".config" / "gworkspace"
CREDS_FILE = CONFIG_DIR / "credentials.json"


class AuthError(Exception):
    """The profile cannot be used as is; the message says what to run."""


def token_path(profile: str) -> Path:
    return CONFIG_DIR / "tokens" / f"{profile}.json"


def _auth_hint(profile: str) -> str:
    return f"run `gworkspace auth --profile {profile}`"


def _ensure_dirs() -> None:
    (CONFIG_DIR / "tokens").mkdir(parents=True, exist_ok=True)


def _require_client_secrets() -> None:
    if not CREDS_FILE.exists():
        raise AuthError(
            f"credentials.json not found at {CREDS_FILE}\n"
            "Create an OAuth client (Desktop app) in Google Cloud Console -> APIs & Services -> "
            "Credentials, download its JSON and save it there (README: 'Google Cloud setup')."
        )


def missing_scopes(granted, required) -> list[str]:
    """Scopes (URLs) from the `required` scope names that are not in `granted` (URLs)."""
    granted = set(granted or [])
    return [SCOPES[name] for name in required if SCOPES[name] not in granted]


def get_credentials(profile: str, required=()) -> Credentials:
    """Load and, when expired, refresh the token of `profile`.

    `required` are scope names (keys of SCOPES) the calling command needs. A token lacking one of
    them is rejected up front with an explicit message instead of a 403 deep inside the API call.
    Never starts a browser flow: that is `gworkspace auth`.
    """
    _ensure_dirs()
    tok = token_path(profile)
    if not tok.exists():
        raise AuthError(f"no token for profile {profile!r} ({tok}); {_auth_hint(profile)}")

    info = json.loads(tok.read_text())
    missing = missing_scopes(info.get("scopes"), required)
    if missing:
        raise AuthError(
            f"token for profile {profile!r} lacks {', '.join(missing)}; "
            f"{_auth_hint(profile)} to re-consent with the current scopes"
        )

    creds = Credentials.from_authorized_user_info(info, scopes=info.get("scopes"))
    if creds.valid:
        return creds
    if not creds.refresh_token:
        raise AuthError(f"token for profile {profile!r} has no refresh token; {_auth_hint(profile)}")
    try:
        creds.refresh(Request())
    except RefreshError as e:
        raise AuthError(f"could not refresh the token for profile {profile!r}: {e}; {_auth_hint(profile)}") from e
    tok.write_text(creds.to_json())
    return creds


def run_auth(profile: str, port: int = 0, open_browser: bool = True, login_hint: str | None = None) -> None:
    """Interactive consent for `profile` with ALL_SCOPES; replaces the profile's token.

    Headless machine: pass a fixed `port` and `open_browser=False`, forward the port from a machine
    with a browser (`ssh -N -L PORT:127.0.0.1:PORT host`) and open the printed URL there.
    """
    _ensure_dirs()
    _require_client_secrets()
    flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, ALL_SCOPES)
    kwargs = {"prompt": "consent"}  # always mint a refresh token, also when re-consenting
    if login_hint:
        kwargs["login_hint"] = login_hint
    if not open_browser and hasattr(sys.stdout, "reconfigure"):
        # the consent URL usually goes to a log file (nohup); make it appear at once
        sys.stdout.reconfigure(line_buffering=True)
    creds = flow.run_local_server(port=port, open_browser=open_browser, **kwargs)
    tok = token_path(profile)
    tok.write_text(creds.to_json())
    tok.chmod(0o600)
    print(f"Authenticated. Token saved to {tok}")
