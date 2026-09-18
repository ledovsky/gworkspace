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
