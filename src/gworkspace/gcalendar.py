from datetime import datetime, date, time, timedelta, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .utils import Tz, parse_date, day_range, fmt_table, resolve_tz


def _service(creds):
    return build("calendar", "v3", credentials=creds)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _tzinfo(tz):
    return tz.tz if isinstance(tz, Tz) else tz


def _fmt_organizer(event: dict) -> str:
    org = event.get("organizer", {})
    if org.get("self"):
        return "me"
    return org.get("displayName") or org.get("email", "?")


def _fmt_event_time(event: dict, tz=None, day: date | None = None) -> str:
    """Event time as `HH:MM–HH:MM`.

    Without `tz`: the wall-clock time in the offset Google returned (0.6.0 behaviour, kept for old
    callers). With `tz`: converted to that time zone; a start or end on another date than `day`
    (default: the start date) gets its date, an end at 00:00 of the next day prints as `24:00`,
    and a multi-day all-day event prints as `first – last`.
    """
    start = event.get("start", {})
    end = event.get("end", {})
    tz = _tzinfo(tz)
    if tz is None:
        if "dateTime" in start:
            s = datetime.fromisoformat(start["dateTime"]).strftime("%H:%M")
            e = datetime.fromisoformat(end["dateTime"]).strftime("%H:%M")
            return f"{s}–{e}"
        return start.get("date", "all-day")
    if "dateTime" not in start:
        if "date" not in start:
            return "all-day"
        first = date.fromisoformat(start["date"])
        last = date.fromisoformat(end["date"]) - timedelta(days=1) if end.get("date") else first
        return first.isoformat() if last <= first else f"{first.isoformat()} – {last.isoformat()}"
    s = _parse_dt(start["dateTime"]).astimezone(tz)
    e = _parse_dt(end["dateTime"]).astimezone(tz)
    day = day or s.date()
    s_txt = s.strftime("%H:%M") if s.date() == day else s.strftime("%Y-%m-%d %H:%M")
    if e.date() == day:
        e_txt = e.strftime("%H:%M")
    elif e.date() == day + timedelta(days=1) and e.time() == time(0, 0):
        e_txt = "24:00"
    else:
        e_txt = e.strftime("%Y-%m-%d %H:%M")
    return f"{s_txt}–{e_txt}"


def _overlaps_day(event: dict, tz, day: date) -> bool:
    """True when the event overlaps [day 00:00, day+1 00:00) in `tz`."""
    start, end = event.get("start", {}), event.get("end", {})
    if "dateTime" not in start:
        if "date" not in start:
            return True
        first = date.fromisoformat(start["date"])
        after = date.fromisoformat(end["date"]) if end.get("date") else first + timedelta(days=1)
        return first <= day < after
    tz = _tzinfo(tz)
    window_start = datetime.combine(day, time.min, tz)
    window_end = datetime.combine(day + timedelta(days=1), time.min, tz)
    return _parse_dt(start["dateTime"]) < window_end and _parse_dt(end["dateTime"]) > window_start


def _response_status(event: dict) -> str:
    """The calendar owner's RSVP status: accepted, declined, tentative, needsAction or unknown.

    Events the owner organizes count as accepted even when Google omits a self-attendee entry.
    """
    if event.get("organizer", {}).get("self"):
        return "accepted"
    me = next((a for a in event.get("attendees", []) if a.get("self")), None)
    if me:
        return me.get("responseStatus", "unknown")
    return "unknown"


_ACCEPTED_LABELS = {"accepted": "Yes", "declined": "No", "needsAction": "No", "tentative": "Tentative"}


def _fmt_accepted(event: dict) -> str:
    return _ACCEPTED_LABELS.get(_response_status(event), "Unknown")


def calendar_list(creds, date_str: str = "today", calendar_id: str = "primary", show_ids: bool = False,
                  show_status: bool = False, timezone_name: str | None = None):
    tz = resolve_tz(timezone_name)
    svc = _service(creds)
    date = parse_date(date_str, tz)
    time_min, time_max = day_range(date, tz)
    params = dict(calendarId=calendar_id, timeMin=time_min, timeMax=time_max,
                  singleEvents=True, orderBy="startTime")
    if tz.name:
        params["timeZone"] = tz.name
    result = svc.events().list(**params).execute()
    events = [e for e in result.get("items", []) if _overlaps_day(e, tz, date)]
    who = "" if calendar_id == "primary" else f" for {calendar_id}"
    print(tz.header())
    if not events:
        print(f"No events on {date}{who}.")
        return
    headers = ["Event", "Time"]
    if show_ids or show_status:
        headers.append("Organizer")
    if show_status:
        headers.append("Accepted")
    if show_ids:
        headers.append("ID")
    rows = []
    for e in events:
        row = [e.get("summary", "(no title)"), _fmt_event_time(e, tz, date)]
        if show_ids or show_status:
            row.append(_fmt_organizer(e))
        if show_status:
            row.append(_fmt_accepted(e))
        if show_ids:
            row.append(e["id"])
        rows.append(tuple(row))
    print(fmt_table(rows, headers=tuple(headers)))


def _localize(value: str, tz: Tz) -> datetime:
    """A --start/--end value: without an offset it is wall-clock time in `tz`; an own offset is kept."""
    dt = _parse_dt(value)
    return dt.replace(tzinfo=tz.tz) if dt.tzinfo is None else dt


def _event_time_body(dt: datetime, tz: Tz) -> dict:
    """Google event time: dateTime with an explicit offset; timeZone only for an IANA name."""
    body = {"dateTime": dt.isoformat()}
    if tz.name:
        body["timeZone"] = tz.name
    return body


def _zoom_times(start_dt: datetime, end_dt: datetime, tz: Tz) -> tuple[str, str, str]:
    """Zoom takes local times plus a zone name: the IANA name, or UTC for a fixed offset."""
    zone, name = (tz.tz, tz.name) if tz.name else (timezone.utc, "UTC")
    local = lambda dt: dt.astimezone(zone).replace(tzinfo=None).isoformat()
    return local(start_dt), local(end_dt), name


def _fmt_when(event: dict, tz: Tz) -> str:
    """`2026-11-26 12:00–12:30 (Europe/Moscow)` for a timed event."""
    start = event.get("start", {})
    if "dateTime" not in start:
        return _fmt_event_time(event, tz)
    day = _parse_dt(start["dateTime"]).astimezone(tz.tz).date()
    return f"{day.isoformat()} {_fmt_event_time(event, tz, day)} ({tz.label})"


def _date_only(value: str) -> date:
    """Parse a YYYY-MM-DD (or full ISO datetime) string into a date."""
    return datetime.fromisoformat(value).date()


def _my_email(creds) -> str:
    gmail_svc = build("gmail", "v1", credentials=creds)
    return gmail_svc.users().getProfile(userId="me").execute()["emailAddress"]


def _zoom_conference_data(meeting: dict) -> dict:
    """Explicit conferenceData block for an already-created Zoom meeting.

    Note: `createRequest` with type "addOn" is rejected by Calendar for regular OAuth clients
    ("Invalid conference type value") - only the add-on itself can trigger that. So we mint the
    meeting through Zoom's API and attach it as ready-made conference data instead.
    """
    join_url = meeting["join_url"]
    entry = {"entryPointType": "video", "uri": join_url, "label": join_url.split("://", 1)[-1]}
    if meeting.get("password"):
        entry["passcode"] = meeting["password"]
    return {
        "conferenceSolution": {"key": {"type": "addOn"}, "name": "Zoom Meeting"},
        "conferenceId": str(meeting.get("id", "")),
        "entryPoints": [entry],
    }


def _zoom_plain_text(meeting: dict) -> str:
    text = f"Join Zoom Meeting: {meeting['join_url']}"
    if meeting.get("id"):
        text += f"\nMeeting ID: {meeting['id']}"
    if meeting.get("password"):
        text += f"\nPasscode: {meeting['password']}"
    return text


def calendar_create(creds, title: str, start: str, end: str, attendees: list[str],
                    description: str = "", all_day: bool = False, conferencing: str = "meet",
                    timezone_name: str | None = None, zoom_recording: str = "none"):
    """Create an event on the primary calendar.

    conferencing: "meet" (Google Meet via Calendar's own createRequest), "zoom" (meeting minted via
    Zoom API, see zoom.py, then attached as conferenceData), or "none".
    zoom_recording: Zoom automatic recording ("none", "cloud", "local"); only with conferencing "zoom".
    Prints the created event; does not return it.
    """
    svc = _service(creds)

    if conferencing not in ("meet", "zoom", "none"):
        raise ValueError(f"Unknown --conferencing value: {conferencing!r} (expected meet, zoom, or none)")
    if zoom_recording != "none" and (conferencing != "zoom" or all_day):
        raise ValueError("--zoom-recording needs a timed event with --conferencing zoom")

    zoom_meeting = None
    if all_day:
        # All-day events use `date` (not `dateTime`); the end date is exclusive.
        # `end` is interpreted as the inclusive last day, so bump it by one.
        start_date = _date_only(start)
        last_day = _date_only(end) if end else start_date
        end_exclusive = last_day + timedelta(days=1)
        body = {
            "summary": title,
            "start": {"date": start_date.isoformat()},
            "end": {"date": end_exclusive.isoformat()},
            "description": description,
        }
    else:
        if not end:
            raise ValueError("--end is required for timed events (or pass --all-day)")
        tz = resolve_tz(timezone_name)
        start_dt, end_dt = _localize(start, tz), _localize(end, tz)
        body = {
            "summary": title,
            "start": _event_time_body(start_dt, tz),
            "end": _event_time_body(end_dt, tz),
            "description": description,
        }
        if conferencing == "meet":
            body["conferenceData"] = {
                "createRequest": {
                    "requestId": f"{title}-{start}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
        elif conferencing == "zoom":
            from .zoom import create_meeting
            zoom_start, zoom_end, zoom_tz = _zoom_times(start_dt, end_dt, tz)
            zoom_meeting = create_meeting(
                topic=title, start=zoom_start, end=zoom_end, timezone=zoom_tz,
                agenda=description, host=_my_email(creds), auto_recording=zoom_recording,
            )
            body["conferenceData"] = _zoom_conference_data(zoom_meeting)
            # Also as plain text, the way Zoom's own Calendar add-on does it: the Zoom desktop app and
            # non-Google calendar clients find the meeting by the link in location / description.
            body["location"] = zoom_meeting["join_url"]
            body["description"] = (f"{description}\n\n" if description else "") + _zoom_plain_text(zoom_meeting)

    if attendees:
        body["attendees"] = [{"email": e} for e in attendees]
    conf_version = 1 if "conferenceData" in body else 0

    try:
        try:
            event = svc.events().insert(
                calendarId="primary", body=body, sendUpdates="all", conferenceDataVersion=conf_version
            ).execute()
        except HttpError as e:
            if zoom_meeting is None or e.resp.status != 400:
                raise
            # Calendar refused the explicit add-on conferenceData; the plain link stays.
            body.pop("conferenceData", None)
            event = svc.events().insert(
                calendarId="primary", body=body, sendUpdates="all", conferenceDataVersion=0
            ).execute()
            print("Note: Calendar rejected add-on conferenceData; the Zoom link is in location/description only.")
    except Exception:
        if zoom_meeting is not None:
            from .zoom import delete_meeting
            delete_meeting(zoom_meeting.get("id"))
        raise

    conf_data = event.get("conferenceData", {})
    conf_link = conf_data.get("entryPoints", [{}])[0].get("uri", "") or event.get("location", "")
    conf_name = conf_data.get("conferenceSolution", {}).get("name") or ("Zoom" if zoom_meeting else "Conference")
    print(f"Created: {event.get('summary')} ({event.get('id')})")
    if all_day:
        print(f"When: {_fmt_event_time(event)} (all-day)")
    elif "dateTime" in event.get("start", {}):
        print(f"When: {_fmt_when(event, tz)}")
    if conf_link:
        print(f"{conf_name}: {conf_link}")
    elif conferencing != "none" and not all_day:
        # Conferences are created asynchronously; the link may not be back yet on insert().
        print("Conferencing requested but no link came back yet - check the event in Calendar.")


def calendar_availability(creds, email: str, date_str: str = "today", timezone_name: str | None = None):
    tz = resolve_tz(timezone_name)
    svc = _service(creds)
    date = parse_date(date_str, tz)
    time_min, time_max = day_range(date, tz)
    # Free/busy cuts slots at timeMin/timeMax, so ask for one more day on each side and keep the
    # slots that overlap the day: a block that began the evening before keeps its real start.
    wide_min, _ = day_range(date - timedelta(days=1), tz)
    _, wide_max = day_range(date + timedelta(days=1), tz)
    result = svc.freebusy().query(body={
        "timeMin": wide_min,
        "timeMax": wide_max,
        "items": [{"id": email}],
    }).execute()
    busy = [
        slot for slot in result.get("calendars", {}).get(email, {}).get("busy", [])
        if _parse_dt(slot["start"]) < _parse_dt(time_max) and _parse_dt(slot["end"]) > _parse_dt(time_min)
    ]
    print(tz.header())
    if not busy:
        print(f"{email} is free all day on {date}.")
        return
    rows = []
    for slot in busy:
        as_event = {"start": {"dateTime": slot["start"]}, "end": {"dateTime": slot["end"]}}
        rows.append(("Busy", _fmt_event_time(as_event, tz, date)))
    print(fmt_table(rows, headers=(email, "Busy slots")))


def _find_event(svc, event_id: str):
    """Return (calendarId, event) searching across all user calendars."""
    calendars = svc.calendarList().list().execute().get("items", [])
    for cal in calendars:
        cal_id = cal["id"]
        try:
            event = svc.events().get(calendarId=cal_id, eventId=event_id).execute()
            return cal_id, event
        except Exception:
            continue
    return None, None


def _update_rsvp(creds, event_id: str, status: str):
    svc = _service(creds)
    gmail_svc = build("gmail", "v1", credentials=creds)
    my_email = gmail_svc.users().getProfile(userId="me").execute()["emailAddress"]
    cal_id, event = _find_event(svc, event_id)
    if event is None:
        print(f"Event {event_id} not found in any calendar.")
        return
    attendees = event.get("attendees", [])
    updated = False
    for a in attendees:
        if a["email"].lower() == my_email.lower():
            a["responseStatus"] = status
            updated = True
    if not updated:
        attendees.append({"email": my_email, "responseStatus": status})
    svc.events().patch(
        calendarId=cal_id, eventId=event_id,
        body={"attendees": attendees}, sendUpdates="all",
    ).execute()
    print(f"RSVP {status}: {event.get('summary', event_id)}")


def calendar_accept(creds, event_id: str):
    _update_rsvp(creds, event_id, "accepted")


def calendar_decline(creds, event_id: str):
    _update_rsvp(creds, event_id, "declined")


def calendar_cancel(creds, event_id: str):
    svc = _service(creds)
    svc.events().delete(
        calendarId="primary", eventId=event_id, sendUpdates="all",
    ).execute()
    print(f"Cancelled event {event_id}.")


def calendar_reschedule(creds, event_id: str, start: str, end: str, timezone_name: str | None = None):
    svc = _service(creds)
    tz = resolve_tz(timezone_name)
    cal_id, event = _find_event(svc, event_id)
    if event is None:
        print(f"Event {event_id} not found.")
        return
    updated = svc.events().patch(
        calendarId=cal_id,
        eventId=event_id,
        body={
            "start": _event_time_body(_localize(start, tz), tz),
            "end": _event_time_body(_localize(end, tz), tz),
        },
        sendUpdates="all",
    ).execute()
    print(f"Rescheduled: {updated.get('summary')} → {_fmt_when(updated, tz)}")


def calendar_add_attendee(creds, event_id: str, email: str):
    svc = _service(creds)
    event = svc.events().get(calendarId="primary", eventId=event_id).execute()
    attendees = event.get("attendees", [])
    if any(a["email"].lower() == email.lower() for a in attendees):
        print(f"{email} is already an attendee.")
        return
    attendees.append({"email": email})
    svc.events().patch(
        calendarId="primary", eventId=event_id,
        body={"attendees": attendees}, sendUpdates="all",
    ).execute()
    print(f"Added {email} to event {event_id}.")
