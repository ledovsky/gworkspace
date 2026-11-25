import re
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Europe/Moscow"

_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)


class Tz:
    """A resolved --timezone value: tzinfo, the IANA name (None for a fixed UTC offset) and a label."""

    def __init__(self, tz: tzinfo, name: str | None, label: str):
        self.tz = tz
        self.name = name
        self.label = label

    def offset_label(self, when: datetime | None = None) -> str:
        """`UTC+03:00` at the given moment (now by default)."""
        when = (when or datetime.now(self.tz)).astimezone(self.tz)
        return "UTC" + fmt_offset(when.utcoffset())

    def header(self) -> str:
        return f"Timezone: {self.label} ({self.offset_label()})"


def fmt_offset(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def resolve_tz(value: str | None = None) -> Tz:
    """Resolve an IANA name (`Europe/Moscow`) or a UTC offset (`+3`, `-4`, `+05:00`, `+0530`,
    `UTC+5`, `GMT+5`). Raises ValueError that names the value when it is neither."""
    value = (value or DEFAULT_TIMEZONE).strip()
    m = _OFFSET_RE.match(value)
    if m:
        sign, hours, minutes = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        delta = timedelta(hours=hours, minutes=minutes)
        limit = timedelta(hours=14 if sign == "+" else 12)
        if minutes >= 60 or delta > limit:
            raise ValueError(f"Unknown timezone {value!r}: the offset must be between -12:00 and +14:00")
        if sign == "-":
            delta = -delta
        return Tz(timezone(delta), None, "UTC" + fmt_offset(delta))
    try:
        return Tz(ZoneInfo(value), value, value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            f"Unknown timezone {value!r}: use an IANA name (Europe/Moscow) or a UTC offset (+3, +05:00)"
        ) from None


def _as_tzinfo(tz) -> tzinfo | None:
    return tz.tz if isinstance(tz, Tz) else tz


def parse_date(date_str: str, tz=None):
    """`today` / `tomorrow` / `YYYY-MM-DD`. With `tz`, today is the current date in that time zone."""
    tz = _as_tzinfo(tz)
    today = datetime.now(tz).date() if tz else datetime.now().date()
    if date_str in ("today", ""):
        return today
    if date_str == "tomorrow":
        return today + timedelta(days=1)
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def day_range(date, tz=None) -> tuple[str, str]:
    """The day 00:00-24:00 as ISO strings: in `tz` when given, else in the host time zone (as in 0.6.0)."""
    tz = _as_tzinfo(tz)
    if tz is None:
        local_tz = datetime.now().astimezone().tzinfo
        start = datetime(date.year, date.month, date.day, tzinfo=local_tz)
        return start.isoformat(), (start + timedelta(days=1)).isoformat()
    nxt = date + timedelta(days=1)
    start = datetime(date.year, date.month, date.day, tzinfo=tz)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return start.isoformat(), end.isoformat()


def fmt_table(rows: list[tuple], headers: tuple = ("Event", "Time")) -> str:
    sep = "|" + "|".join("---" for _ in headers) + "|"
    lines = [
        "| " + " | ".join(headers) + " |",
        sep,
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)
