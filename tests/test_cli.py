import pytest

from gworkspace import __version__
from gworkspace.cli import build_parser, cmd_auth, cmd_gmail_list


def parse(argv):
    return build_parser().parse_args(argv)


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        parse(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"gworkspace {__version__}"


def test_auth_flags_defaults():
    args = parse(["auth", "--profile", "work"])
    assert args.func is cmd_auth
    assert (args.profile, args.port, args.no_browser, args.login_hint) == ("work", 0, False, None)


def test_auth_headless_flags():
    args = parse(["auth", "--profile", "work", "--port", "3336", "--no-browser",
                  "--login-hint", "me@example.com"])
    assert (args.port, args.no_browser, args.login_hint) == (3336, True, "me@example.com")


def test_profile_is_required():
    with pytest.raises(SystemExit) as exc:
        parse(["gmail", "list"])
    assert exc.value.code == 2


def test_gmail_list_flags():
    args = parse(["gmail", "list", "--profile", "p", "-n", "3", "-q", "is:unread"])
    assert args.func is cmd_gmail_list
    assert (args.count, args.query) == (3, "is:unread")


def test_calendar_create_conferencing_choices():
    args = parse(["calendar", "create", "--profile", "p", "--title", "t", "--start", "2026-01-01",
                  "--all-day", "--conferencing", "none"])
    assert args.all_day and args.conferencing == "none"
    with pytest.raises(SystemExit):
        parse(["calendar", "create", "--profile", "p", "--title", "t", "--start", "x",
               "--conferencing", "teams"])
