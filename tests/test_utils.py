import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from gworkspace import utils
from gworkspace.utils import fmt_table, parse_date


def test_parse_date_keywords():
    today = date.today()
    assert parse_date("today") == today
    assert parse_date("") == today
    assert parse_date("tomorrow") == today + timedelta(days=1)


def test_parse_date_iso():
    assert parse_date("2026-01-02") == date(2026, 1, 2)


@pytest.mark.parametrize(
    ("value", "offset"),
    [
        ("+3", timedelta(hours=3)),
        ("-4", timedelta(hours=-4)),
        ("+05:00", timedelta(hours=5)),
        ("+0530", timedelta(hours=5, minutes=30)),
        ("UTC+5", timedelta(hours=5)),
        ("GMT+5", timedelta(hours=5)),
        ("+14", timedelta(hours=14)),
        ("-12", timedelta(hours=-12)),
    ],
)
def test_resolve_tz_offsets(value, offset):
    tz = utils.resolve_tz(value)
    assert tz.name is None
    assert tz.tz.utcoffset(None) == offset


def test_resolve_tz_names_and_default():
    tz = utils.resolve_tz("Europe/Moscow")
    assert tz.name == "Europe/Moscow"
    assert tz.header() == "Timezone: Europe/Moscow (UTC+03:00)"
    assert utils.resolve_tz(None).name == "Europe/Moscow"
    assert utils.resolve_tz("+5").header() == "Timezone: UTC+05:00 (UTC+05:00)"


@pytest.mark.parametrize("value", ["Mars/Base", "+15", "-13", "+5:75", "five"])
def test_resolve_tz_rejects_bad_values(value):
    with pytest.raises(ValueError, match=re.escape(repr(value))):
        utils.resolve_tz(value)


class _FrozenDatetime(datetime):
    """now() = 2026-11-25 23:30 in Berlin (2026-11-26 01:30 in Moscow)."""

    @classmethod
    def now(cls, tz=None):
        moment = datetime(2026, 11, 25, 22, 30, tzinfo=timezone.utc)
        return moment.astimezone(tz) if tz else moment.astimezone(ZoneInfo("Europe/Berlin")).replace(tzinfo=None)


def test_today_is_the_date_in_the_selected_timezone(monkeypatch):
    monkeypatch.setattr(utils, "datetime", _FrozenDatetime)
    moscow = utils.resolve_tz("Europe/Moscow")
    assert parse_date("today", moscow) == date(2026, 11, 26)
    assert parse_date("tomorrow", moscow) == date(2026, 11, 27)
    assert parse_date("today") == date(2026, 11, 25)  # host date without tz


def test_day_range_in_selected_timezone():
    start, end = utils.day_range(date(2026, 11, 26), utils.resolve_tz("Europe/Moscow"))
    assert start == "2026-11-26T00:00:00+03:00"
    assert end == "2026-11-27T00:00:00+03:00"
    start, end = utils.day_range(date(2026, 11, 26), utils.resolve_tz("+5"))
    assert (start, end) == ("2026-11-26T00:00:00+05:00", "2026-11-27T00:00:00+05:00")


def test_day_range_without_tz_keeps_host_behaviour():
    start, end = utils.day_range(date(2026, 11, 26))
    expected = datetime(2026, 11, 26, tzinfo=datetime.now().astimezone().tzinfo)
    assert start == expected.isoformat()
    assert end == (expected + timedelta(days=1)).isoformat()


def test_fmt_table_markdown():
    out = fmt_table([("Standup", "10:00–10:15"), ("1:1", "11:00–11:30")], headers=("Event", "Time"))
    assert out.splitlines() == [
        "| Event | Time |",
        "|---|---|",
        "| Standup | 10:00–10:15 |",
        "| 1:1 | 11:00–11:30 |",
    ]
