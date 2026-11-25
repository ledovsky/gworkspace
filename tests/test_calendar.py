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
    assert lines[0] == "Timezone: Europe/Moscow (UTC+03:00)"
    assert "Accepted" in lines[1] and "Organizer" in lines[1] and "ID" in lines[1]
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


# ── time zone (0.7.0) ──────────────────────────────────────────────────────
from datetime import date as _date

from gworkspace.utils import resolve_tz

MSK = resolve_tz("Europe/Moscow")


def _ev(start, end, summary="Block"):
    key = "dateTime" if "T" in start else "date"
    return {"id": summary.lower(), "summary": summary, "start": {key: start}, "end": {key: end}}


@pytest.mark.parametrize(
    ("event", "tz", "day", "expected"),
    [
        # colleague calendar in UTC+5 -> Moscow
        (_ev("2026-11-25T18:30:00+05:00", "2026-11-25T20:00:00+05:00"), MSK, _date(2026, 11, 25), "16:30–18:00"),
        # the same event from a UTC calendar and from a UTC+2 calendar
        (_ev("2026-11-25T09:30:00Z", "2026-11-25T10:00:00Z"), MSK, _date(2026, 11, 25), "12:30–13:00"),
        (_ev("2026-11-25T11:30:00+02:00", "2026-11-25T12:00:00+02:00"), MSK, _date(2026, 11, 25), "12:30–13:00"),
        # night block from the previous evening
        (_ev("2026-11-25T20:00:00+03:00", "2026-11-26T11:00:00+03:00"), MSK, _date(2026, 11, 26),
         "2026-11-25 20:00–11:00"),
        # evening block that ends at midnight
        (_ev("2026-11-26T20:00:00+03:00", "2026-11-27T00:00:00+03:00"), MSK, _date(2026, 11, 26), "20:00–24:00"),
        # the same block seen in UTC+5 on the next day
        (_ev("2026-11-25T20:00:00+03:00", "2026-11-26T00:00:00+03:00"), resolve_tz("+5"), _date(2026, 11, 26),
         "2026-11-25 22:00–02:00"),
        # all-day events
        (_ev("2026-11-25", "2026-11-26"), MSK, _date(2026, 11, 25), "2026-11-25"),
        (_ev("2026-11-24", "2026-11-28", "Vacation"), MSK, _date(2026, 11, 25), "2026-11-24 – 2026-11-27"),
    ],
)
def test_fmt_event_time_in_timezone(event, tz, day, expected):
    assert gcalendar._fmt_event_time(event, tz, day) == expected


def test_fmt_event_time_old_call_form_unchanged():
    assert gcalendar._fmt_event_time(_ev("2026-11-25T18:30:00+05:00", "2026-11-25T20:00:00+05:00")) == "18:30–20:00"
    assert gcalendar._fmt_event_time(_ev("2026-11-24", "2026-11-28")) == "2026-11-24"


class _CapturingEvents:
    def __init__(self, items=(), event=None):
        self.items = list(items)
        self.event = event or {}
        self.calls = {}

    def list(self, **kwargs):
        self.calls["list"] = kwargs
        return _Request({"items": self.items})

    def insert(self, **kwargs):
        self.calls["insert"] = kwargs
        return _Request({"id": "new", **kwargs["body"]})

    def get(self, **kwargs):
        return _Request(self.event)

    def patch(self, **kwargs):
        self.calls["patch"] = kwargs
        return _Request({**self.event, **kwargs["body"]})


class _CapturingService:
    def __init__(self, events_api, busy=()):
        self.events_api = events_api
        self.busy = list(busy)
        self.fb_body = None

    def events(self):
        return self.events_api

    def calendarList(self):
        return type("L", (), {"list": lambda _s: _Request({"items": [{"id": "primary"}]})})()

    def freebusy(self):
        svc = self

        class _FB:
            def query(self, body):
                svc.fb_body = body
                return _Request({"calendars": {body["items"][0]["id"]: {"busy": svc.busy}}})
        return _FB()


def test_calendar_list_converts_colleague_calendar(monkeypatch, capsys):
    events = _CapturingEvents([_ev("2026-11-25T18:30:00+05:00", "2026-11-25T20:00:00+05:00", "Busy +5")])
    monkeypatch.setattr(gcalendar, "_service", lambda creds: _CapturingService(events))
    gcalendar.calendar_list(object(), date_str="2026-11-25", calendar_id="col@x.com")
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Timezone: Europe/Moscow (UTC+03:00)"
    assert "| Busy +5 | 16:30–18:00 |" in out
    call = events.calls["list"]
    assert call["timeMin"] == "2026-11-25T00:00:00+03:00" and call["timeMax"] == "2026-11-26T00:00:00+03:00"
    assert call["timeZone"] == "Europe/Moscow"


def test_calendar_list_offset_has_no_timezone_param(monkeypatch, capsys):
    events = _CapturingEvents([])
    monkeypatch.setattr(gcalendar, "_service", lambda creds: _CapturingService(events))
    gcalendar.calendar_list(object(), date_str="2026-11-25", timezone_name="+5")
    out = capsys.readouterr().out
    assert out.splitlines() == ["Timezone: UTC+05:00 (UTC+05:00)", "No events on 2026-11-25."]
    assert "timeZone" not in events.calls["list"]
    assert events.calls["list"]["timeMin"] == "2026-11-25T00:00:00+05:00"


def test_calendar_list_cross_day_events(monkeypatch, capsys):
    items = [
        _ev("2026-11-25T20:00:00+03:00", "2026-11-26T00:00:00+03:00", "Ends at window start"),
        _ev("2026-11-25T20:00:00+03:00", "2026-11-26T11:00:00+03:00", "Night block"),
        _ev("2026-11-26T20:00:00+03:00", "2026-11-27T00:00:00+03:00", "Evening block"),
    ]
    monkeypatch.setattr(gcalendar, "_service", lambda creds: _CapturingService(_CapturingEvents(items)))
    gcalendar.calendar_list(object(), date_str="2026-11-26", calendar_id="col@x.com")
    out = capsys.readouterr().out
    assert "Ends at window start" not in out
    assert "| Night block | 2026-11-25 20:00–11:00 |" in out
    assert "| Evening block | 20:00–24:00 |" in out


def test_availability_converts_utc_and_cross_day(monkeypatch, capsys):
    svc = _CapturingService(_CapturingEvents(), busy=[
        {"start": "2026-11-25T17:00:00Z", "end": "2026-11-26T09:30:00Z"},
        {"start": "2026-11-26T09:00:00Z", "end": "2026-11-26T10:00:00Z"},
        {"start": "2026-11-25T08:00:00Z", "end": "2026-11-25T09:00:00Z"},  # previous day only
        {"start": "2026-11-26T21:00:00Z", "end": "2026-11-26T22:00:00Z"},  # next day only
    ])
    monkeypatch.setattr(gcalendar, "_service", lambda creds: svc)
    gcalendar.calendar_availability(object(), "col@x.com", date_str="2026-11-26")
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Timezone: Europe/Moscow (UTC+03:00)"
    assert "| Busy | 2026-11-25 20:00–12:30 |" in out
    assert "| Busy | 12:00–13:00 |" in out
    assert "2026-11-25 11:00" not in out and "2026-11-27" not in out
    assert svc.fb_body["timeMin"] == "2026-11-25T00:00:00+03:00"
    assert svc.fb_body["timeMax"] == "2026-11-28T00:00:00+03:00"


def _create(monkeypatch, **kw):
    events = _CapturingEvents()
    monkeypatch.setattr(gcalendar, "_service", lambda creds: _CapturingService(events))
    gcalendar.calendar_create(creds=object(), title="T", attendees=[], conferencing="none", **kw)
    return events.calls["insert"]["body"]


def test_create_default_is_moscow(monkeypatch, capsys):
    body = _create(monkeypatch, start="2026-11-26T12:00:00", end="2026-11-26T12:30:00")
    assert body["start"] == {"dateTime": "2026-11-26T12:00:00+03:00", "timeZone": "Europe/Moscow"}
    assert body["end"] == {"dateTime": "2026-11-26T12:30:00+03:00", "timeZone": "Europe/Moscow"}
    assert "When: 2026-11-26 12:00–12:30 (Europe/Moscow)" in capsys.readouterr().out


def test_create_with_offset_timezone(monkeypatch, capsys):
    body = _create(monkeypatch, start="2026-11-26T12:00:00", end="2026-11-26T12:30:00", timezone_name="+5")
    assert body["start"] == {"dateTime": "2026-11-26T12:00:00+05:00"}


def test_create_keeps_explicit_offset(monkeypatch, capsys):
    body = _create(monkeypatch, start="2026-11-26T12:00:00+05:00", end="2026-11-26T12:30:00+05:00")
    assert body["start"]["dateTime"] == "2026-11-26T12:00:00+05:00"
    assert "When: 2026-11-26 10:00–10:30 (Europe/Moscow)" in capsys.readouterr().out


def test_zoom_gets_moscow_or_utc(monkeypatch):
    from gworkspace import zoom

    calls = []
    monkeypatch.setattr(gcalendar, "_my_email", lambda creds: "me@example.com")
    monkeypatch.setattr(zoom, "create_meeting", lambda **kw: calls.append(kw) or {"id": 1, "join_url": "https://z/j/1"})
    for tz_name in (None, "+5"):
        events = _CapturingEvents()
        monkeypatch.setattr(gcalendar, "_service", lambda creds: _CapturingService(events))
        gcalendar.calendar_create(creds=object(), title="T", start="2026-11-26T12:00:00", end="2026-11-26T12:30:00",
                                  attendees=[], conferencing="zoom", timezone_name=tz_name)
    assert (calls[0]["start"], calls[0]["end"], calls[0]["timezone"]) == (
        "2026-11-26T12:00:00", "2026-11-26T12:30:00", "Europe/Moscow")
    assert (calls[1]["start"], calls[1]["timezone"]) == ("2026-11-26T07:00:00", "UTC")


def test_reschedule_default_is_moscow(monkeypatch, capsys):
    events = _CapturingEvents(event={"id": "e1", "summary": "Sync"})
    monkeypatch.setattr(gcalendar, "_service", lambda creds: _CapturingService(events))
    gcalendar.calendar_reschedule(object(), "e1", "2026-11-26T16:00:00", "2026-11-26T16:30:00")
    body = events.calls["patch"]["body"]
    assert body["start"] == {"dateTime": "2026-11-26T16:00:00+03:00", "timeZone": "Europe/Moscow"}
    assert "Sync → 2026-11-26 16:00–16:30 (Europe/Moscow)" in capsys.readouterr().out
