from datetime import date, timedelta

from gworkspace.utils import fmt_table, parse_date


def test_parse_date_keywords():
    today = date.today()
    assert parse_date("today") == today
    assert parse_date("") == today
    assert parse_date("tomorrow") == today + timedelta(days=1)


def test_parse_date_iso():
    assert parse_date("2026-01-02") == date(2026, 1, 2)


def test_fmt_table_markdown():
    out = fmt_table([("Standup", "10:00–10:15"), ("1:1", "11:00–11:30")], headers=("Event", "Time"))
    assert out.splitlines() == [
        "| Event | Time |",
        "|---|---|",
        "| Standup | 10:00–10:15 |",
        "| 1:1 | 11:00–11:30 |",
    ]
