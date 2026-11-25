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


def test_calendar_zoom_recording_flag():
    base = ["calendar", "create", "--profile", "p", "--title", "t",
            "--start", "2026-01-01T10:00", "--end", "2026-01-01T10:30", "--conferencing", "zoom"]
    assert parse(base).zoom_recording == "none"
    assert parse(base + ["--zoom-recording", "cloud"]).zoom_recording == "cloud"
    with pytest.raises(SystemExit):
        parse(base + ["--zoom-recording", "always"])


def test_calendar_timezone_flags():
    create = parse(["calendar", "create", "--profile", "p", "--title", "t",
                    "--start", "2026-01-01T10:00", "--end", "2026-01-01T10:30",
                    "--timezone", "Europe/Moscow"])
    assert create.timezone == "Europe/Moscow"
    reschedule = parse(["calendar", "reschedule", "--profile", "p", "event-id",
                        "--start", "2026-01-01T10:00", "--end", "2026-01-01T10:30",
                        "--timezone", "Europe/Moscow"])
    assert reschedule.timezone == "Europe/Moscow"


@pytest.mark.parametrize(
    "argv",
    [
        ["calendar", "list", "--profile", "p"],
        ["calendar", "availability", "--profile", "p", "--email", "a@x.com"],
        ["calendar", "create", "--profile", "p", "--title", "t",
         "--start", "2026-01-01T10:00", "--end", "2026-01-01T10:30"],
        ["calendar", "reschedule", "--profile", "p", "event-id",
         "--start", "2026-01-01T10:00", "--end", "2026-01-01T10:30"],
        ["calendar", "accept", "--profile", "p", "event-id"],
        ["calendar", "decline", "--profile", "p", "event-id"],
        ["calendar", "add-attendee", "--profile", "p", "event-id", "--email", "a@x.com"],
        ["calendar", "cancel", "--profile", "p", "event-id"],
    ],
)
def test_every_calendar_command_has_timezone(argv):
    assert parse(argv).timezone == "Europe/Moscow"
    assert parse(argv + ["--timezone", "+5"]).timezone == "+5"


def test_bad_timezone_fails_before_google(monkeypatch, capsys):
    from gworkspace import cli

    calls = []
    monkeypatch.setattr(cli, "get_credentials", lambda *a, **kw: calls.append(a))
    with pytest.raises(SystemExit) as exc:
        cli.main(["calendar", "list", "--profile", "p", "--timezone", "Mars/Base"])
    assert exc.value.code == 2
    assert "Mars/Base" in capsys.readouterr().err
    assert calls == []


def test_gmail_attachment_flags():
    from gworkspace.cli import cmd_gmail_attachment
    args = parse(["gmail", "attachment", "--profile", "p", "m1", "2", "--out", "/tmp/x"])
    assert args.func is cmd_gmail_attachment
    assert (args.id, args.index, args.all, args.out) == ("m1", 2, False, "/tmp/x")
    args = parse(["gmail", "attachment", "--profile", "p", "m1", "--all"])
    assert (args.index, args.all, args.out) == (None, True, None)


def test_gmail_send_attach_is_repeatable():
    base = ["gmail", "send", "--profile", "p", "--to", "a@x.com", "--subject", "s", "--body", "b"]
    assert parse(base).attach == []
    assert parse(base + ["--attach", "a.pdf", "--attach", "b.jpg"]).attach == ["a.pdf", "b.jpg"]
