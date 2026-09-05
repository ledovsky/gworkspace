import os
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .utils import parse_date, day_range, fmt_table


def _service(creds):
    return build("calendar", "v3", credentials=creds)


def _local_tz_name() -> str:
    p = Path("/etc/localtime")
    if p.is_symlink():
        target = os.readlink(p)
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    return "UTC"


def _fmt_organizer(event: dict) -> str:
    org = event.get("organizer", {})
    if org.get("self"):
        return "me"
    return org.get("displayName") or org.get("email", "?")


def _fmt_event_time(event: dict) -> str:
    start = event.get("start", {})
    end = event.get("end", {})
    if "dateTime" in start:
        s = datetime.fromisoformat(start["dateTime"]).strftime("%H:%M")
        e = datetime.fromisoformat(end["dateTime"]).strftime("%H:%M")
        return f"{s}–{e}"
    return start.get("date", "all-day")


def calendar_list(creds, date_str: str = "today", calendar_id: str = "primary", show_ids: bool = False):
    svc = _service(creds)
    date = parse_date(date_str)
    time_min, time_max = day_range(date)
    result = svc.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    events = result.get("items", [])
    who = "" if calendar_id == "primary" else f" for {calendar_id}"
    if not events:
        print(f"No events on {date}{who}.")
        return
    if show_ids:
        rows = [(e.get("summary", "(no title)"), _fmt_event_time(e), _fmt_organizer(e), e["id"]) for e in events]
        print(fmt_table(rows, headers=("Event", "Time", "Organizer", "ID")))
    else:
        rows = [(e.get("summary", "(no title)"), _fmt_event_time(e)) for e in events]
        print(fmt_table(rows, headers=("Event", "Time")))


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
                    description: str = "", all_day: bool = False, conferencing: str = "meet"):
    """Create an event on the primary calendar.

    conferencing: "meet" (Google Meet via Calendar's own createRequest), "zoom" (meeting minted via
    Zoom API, see zoom.py, then attached as conferenceData), or "none".
    Prints the created event; does not return it.
    """
    svc = _service(creds)

    if conferencing not in ("meet", "zoom", "none"):
        raise ValueError(f"Unknown --conferencing value: {conferencing!r} (expected meet, zoom, or none)")

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
        tz_name = _local_tz_name()
        body = {
            "summary": title,
            "start": {"dateTime": start, "timeZone": tz_name},
            "end": {"dateTime": end, "timeZone": tz_name},
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
            zoom_meeting = create_meeting(
                topic=title, start=start, end=end, timezone=tz_name,
                agenda=description, host=_my_email(creds),
            )
            body["conferenceData"] = _zoom_conference_data(zoom_meeting)

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
            # Calendar refused the explicit add-on conferenceData; fall back to a plain link.
            body.pop("conferenceData", None)
            body["location"] = zoom_meeting["join_url"]
            body["description"] = (f"{description}\n\n" if description else "") + _zoom_plain_text(zoom_meeting)
            event = svc.events().insert(
                calendarId="primary", body=body, sendUpdates="all", conferenceDataVersion=0
            ).execute()
            print("Note: Calendar rejected add-on conferenceData; Zoom link placed in location/description.")
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
    if conf_link:
        print(f"{conf_name}: {conf_link}")
    elif conferencing != "none" and not all_day:
        # Conferences are created asynchronously; the link may not be back yet on insert().
        print("Conferencing requested but no link came back yet - check the event in Calendar.")


def calendar_availability(creds, email: str, date_str: str = "today"):
    svc = _service(creds)
    date = parse_date(date_str)
    time_min, time_max = day_range(date)
    result = svc.freebusy().query(body={
        "timeMin": time_min,
        "timeMax": time_max,
        "items": [{"id": email}],
    }).execute()
    busy = result.get("calendars", {}).get(email, {}).get("busy", [])
    if not busy:
        print(f"{email} is free all day on {date}.")
        return
    rows = []
    for slot in busy:
        s = datetime.fromisoformat(slot["start"]).strftime("%H:%M")
        e = datetime.fromisoformat(slot["end"]).strftime("%H:%M")
        rows.append(("Busy", f"{s}–{e}"))
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


def calendar_reschedule(creds, event_id: str, start: str, end: str):
    svc = _service(creds)
    tz_name = _local_tz_name()
    cal_id, event = _find_event(svc, event_id)
    if event is None:
        print(f"Event {event_id} not found.")
        return
    updated = svc.events().patch(
        calendarId=cal_id,
        eventId=event_id,
        body={
            "start": {"dateTime": start, "timeZone": tz_name},
            "end": {"dateTime": end, "timeZone": tz_name},
        },
        sendUpdates="all",
    ).execute()
    print(f"Rescheduled: {updated.get('summary')} → {_fmt_event_time(updated)}")


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
