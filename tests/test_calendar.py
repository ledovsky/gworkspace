import pytest

from gworkspace import gcalendar


class _Request:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class _Events:
    def __init__(self):
        self.insert_kwargs = None

    def insert(self, **kwargs):
        self.insert_kwargs = kwargs
        return _Request({"summary": "Test", "id": "event-id"})


class _Service:
    def __init__(self):
        self.events_api = _Events()

    def events(self):
        return self.events_api


def test_calendar_create_uses_explicit_timezone(monkeypatch):
    service = _Service()
    monkeypatch.setattr(gcalendar, "_service", lambda creds: service)

    gcalendar.calendar_create(
        creds=object(), title="Test", start="2026-01-01T10:00:00", end="2026-01-01T10:30:00",
        attendees=[], conferencing="none", timezone_name="Europe/Moscow",
    )

    body = service.events_api.insert_kwargs["body"]
    assert body["start"]["timeZone"] == "Europe/Moscow"
    assert body["end"]["timeZone"] == "Europe/Moscow"


def test_zoom_link_goes_into_conference_data_location_and_description(monkeypatch):
    from gworkspace import zoom

    service = _Service()
    monkeypatch.setattr(gcalendar, "_service", lambda creds: service)
    monkeypatch.setattr(gcalendar, "_my_email", lambda creds: "me@example.com")
    calls = []
    monkeypatch.setattr(zoom, "create_meeting", lambda **kw: calls.append(kw) or {
        "id": 123, "join_url": "https://zoom.us/j/123?pwd=x", "password": "pw"})

    gcalendar.calendar_create(
        creds=object(), title="Test", start="2026-01-01T10:00:00", end="2026-01-01T10:30:00",
        attendees=["a@example.com"], description="Agenda", conferencing="zoom", timezone_name="Europe/Moscow",
    )

    body = service.events_api.insert_kwargs["body"]
    assert body["conferenceData"]["entryPoints"][0]["uri"] == "https://zoom.us/j/123?pwd=x"
    assert body["location"] == "https://zoom.us/j/123?pwd=x"
    assert body["description"] == ("Agenda\n\nJoin Zoom Meeting: https://zoom.us/j/123?pwd=x\n"
                                   "Meeting ID: 123\nPasscode: pw")
    assert calls[0]["auto_recording"] == "none"


def test_zoom_recording_requires_zoom_conferencing(monkeypatch):
    monkeypatch.setattr(gcalendar, "_service", lambda creds: _Service())
    with pytest.raises(ValueError, match="--zoom-recording"):
        gcalendar.calendar_create(
            creds=object(), title="Test", start="2026-01-01T10:00:00", end="2026-01-01T10:30:00",
            attendees=[], conferencing="meet", zoom_recording="cloud",
        )


class _ListEvents:
    def __init__(self, items):
        self.items = items

    def list(self, **kwargs):
        return _Request({"items": self.items})


class _ListService:
    def __init__(self, items):
        self.events_api = _ListEvents(items)

    def events(self):
        return self.events_api


def _timed(summary, **extra):
    return {
        "id": summary.lower(),
        "summary": summary,
        "start": {"dateTime": "2026-11-12T10:00:00+03:00"},
        "end": {"dateTime": "2026-11-12T11:00:00+03:00"},
        **extra,
    }


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"organizer": {"self": True}}, "accepted"),
        ({"organizer": {"email": "x@y"}, "attendees": [{"self": True, "responseStatus": "declined"}]}, "declined"),
        ({"organizer": {"email": "x@y"}, "attendees": [{"email": "z@y", "responseStatus": "accepted"}]}, "unknown"),
        ({"organizer": {"email": "x@y"}}, "unknown"),
    ],
)
def test_response_status(event, expected):
    assert gcalendar._response_status(event) == expected


def test_calendar_list_status_columns(monkeypatch, capsys):
    items = [
        _timed("Mine", organizer={"self": True}),
        _timed("Invite", organizer={"displayName": "Bob"},
               attendees=[{"self": True, "responseStatus": "needsAction"}]),
        _timed("Maybe", organizer={"email": "c@y"},
               attendees=[{"self": True, "responseStatus": "tentative"}]),
        _timed("Group", organizer={"email": "d@y"}),
    ]
    monkeypatch.setattr(gcalendar, "_service", lambda creds: _ListService(items))

    gcalendar.calendar_list(object(), date_str="2026-11-12", show_status=True, show_ids=True)
    out = capsys.readouterr().out

    lines = out.splitlines()
    assert "Accepted" in lines[0] and "Organizer" in lines[0] and "ID" in lines[0]
    row = {line.split("|")[1].strip(): line for line in lines if line.startswith("|")}
    assert "| me" in row["Mine"] and "Yes" in row["Mine"]
    assert "Bob" in row["Invite"] and "| No" in row["Invite"]
    assert "Tentative" in row["Maybe"]
    assert "Unknown" in row["Group"]


def test_calendar_list_default_columns_unchanged(monkeypatch, capsys):
    monkeypatch.setattr(gcalendar, "_service", lambda creds: _ListService([_timed("Mine", organizer={"self": True})]))
    gcalendar.calendar_list(object(), date_str="2026-11-12")
    out = capsys.readouterr().out
    assert "Accepted" not in out and "Organizer" not in out
