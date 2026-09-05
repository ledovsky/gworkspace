from datetime import datetime, timedelta


def parse_date(date_str: str):
    today = datetime.now().date()
    if date_str in ("today", ""):
        return today
    if date_str == "tomorrow":
        return today + timedelta(days=1)
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def day_range(date) -> tuple[str, str]:
    local_tz = datetime.now().astimezone().tzinfo
    start = datetime(date.year, date.month, date.day, tzinfo=local_tz)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def fmt_table(rows: list[tuple], headers: tuple = ("Event", "Time")) -> str:
    sep = "|---|" * len(headers) + "|" if False else "|" + "|".join("---" for _ in headers) + "|"
    lines = [
        "| " + " | ".join(headers) + " |",
        sep,
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)
