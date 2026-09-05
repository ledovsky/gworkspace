import argparse
import sys

from . import __version__
from .auth import AuthError, get_credentials, run_auth
from .drive import CREATE_TYPES, DOWNLOAD_FORMATS


# ── shared --profile flag ──────────────────────────────────────────────────
def _profile_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--profile", required=True,
                   help="Account profile (e.g. personal, work)")
    return p


# ── subcommand handlers ────────────────────────────────────────────────────
def cmd_auth(args):
    run_auth(args.profile, port=args.port, open_browser=not args.no_browser,
             login_hint=args.login_hint)


def cmd_whoami(args):
    from googleapiclient.discovery import build
    creds = get_credentials(args.profile, required=("gmail",))
    svc = build("gmail", "v1", credentials=creds)
    profile = svc.users().getProfile(userId="me").execute()
    print(f"Profile : {args.profile}")
    print(f"Email   : {profile.get('emailAddress')}")


# Gmail
def cmd_gmail_list(args):
    from .gmail import gmail_list
    creds = get_credentials(args.profile, required=("gmail",))
    gmail_list(creds, query=args.query, count=args.count)


def cmd_gmail_read(args):
    from .gmail import gmail_read
    creds = get_credentials(args.profile, required=("gmail",))
    gmail_read(creds, args.id)


def cmd_gmail_send(args):
    from .gmail import gmail_send
    creds = get_credentials(args.profile, required=("gmail",))
    gmail_send(creds, args.to, args.subject, args.body)


def cmd_gmail_archive_read(args):
    from .gmail import gmail_archive_read
    creds = get_credentials(args.profile, required=("gmail",))
    gmail_archive_read(creds, args.id)


def cmd_gmail_delete(args):
    from .gmail import gmail_delete
    creds = get_credentials(args.profile, required=("gmail",))
    gmail_delete(creds, args.id)


# Calendar
def cmd_cal_list(args):
    from .gcalendar import calendar_list
    creds = get_credentials(args.profile, required=("calendar",))
    calendar_list(creds, date_str=args.date, calendar_id=args.calendar, show_ids=args.ids)


def cmd_cal_create(args):
    from .gcalendar import calendar_create
    # the Zoom path looks up the account email through Gmail
    required = ("calendar", "gmail") if args.conferencing == "zoom" else ("calendar",)
    creds = get_credentials(args.profile, required=required)
    attendees = [e.strip() for e in args.attendees.split(",")] if args.attendees else []
    calendar_create(creds, args.title, args.start, args.end, attendees,
                    description=args.description or "", all_day=args.all_day,
                    conferencing=args.conferencing)


def cmd_cal_availability(args):
    from .gcalendar import calendar_availability
    creds = get_credentials(args.profile, required=("calendar",))
    calendar_availability(creds, args.email, date_str=args.date)


def cmd_cal_accept(args):
    from .gcalendar import calendar_accept
    creds = get_credentials(args.profile, required=("calendar", "gmail"))
    calendar_accept(creds, args.id)


def cmd_cal_decline(args):
    from .gcalendar import calendar_decline
    creds = get_credentials(args.profile, required=("calendar", "gmail"))
    calendar_decline(creds, args.id)


def cmd_cal_reschedule(args):
    from .gcalendar import calendar_reschedule
    creds = get_credentials(args.profile, required=("calendar",))
    calendar_reschedule(creds, args.id, args.start, args.end)


def cmd_cal_add_attendee(args):
    from .gcalendar import calendar_add_attendee
    creds = get_credentials(args.profile, required=("calendar",))
    calendar_add_attendee(creds, args.id, args.email)


def cmd_cal_cancel(args):
    from .gcalendar import calendar_cancel
    creds = get_credentials(args.profile, required=("calendar",))
    calendar_cancel(creds, args.id)


# People
def cmd_people_search(args):
    from .people import people_search
    creds = get_credentials(args.profile, required=("contacts", "directory"))
    people_search(creds, args.query)


# Drive
def cmd_drive_list(args):
    from .drive import drive_list
    creds = get_credentials(args.profile, required=("drive",))
    drive_list(creds, count=args.count, folder=args.folder)


def cmd_drive_search(args):
    from .drive import drive_search
    creds = get_credentials(args.profile, required=("drive",))
    drive_search(creds, args.text, count=args.count)


def cmd_drive_read(args):
    from .drive import drive_read
    creds = get_credentials(args.profile, required=("drive",))
    drive_read(creds, args.file)


def cmd_drive_download(args):
    from .drive import drive_download
    creds = get_credentials(args.profile, required=("drive",))
    drive_download(creds, args.file, out=args.out, fmt=args.format)


def cmd_drive_upload(args):
    from .drive import drive_upload
    creds = get_credentials(args.profile, required=("drive",))
    drive_upload(creds, args.path, folder=args.folder, name=args.name)


def cmd_drive_create(args):
    from .drive import drive_create
    creds = get_credentials(args.profile, required=("drive",))
    drive_create(creds, args.name, args.type, folder=args.folder, from_file=args.from_file)


# ── parser ─────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gworkspace",
        description="Google Workspace CLI — Gmail, Calendar, People, Drive",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    top = parser.add_subparsers(dest="command", metavar="COMMAND")
    top.required = True

    # auth / whoami
    p = top.add_parser("auth", parents=[_profile_parser()],
                       help="Authenticate a profile (browser consent; see --no-browser for headless machines)")
    p.add_argument("--port", type=int, default=0,
                   help="Fixed loopback port for the OAuth callback (default: random). "
                        "Headless: pick one and forward it with `ssh -N -L PORT:127.0.0.1:PORT host`")
    p.add_argument("--no-browser", action="store_true",
                   help="Do not open a browser; print the consent URL to open elsewhere")
    p.add_argument("--login-hint", default=None,
                   help="Email to preselect on the Google account chooser")
    p.set_defaults(func=cmd_auth)

    p = top.add_parser("whoami", parents=[_profile_parser()], help="Print authenticated account")
    p.set_defaults(func=cmd_whoami)

    # ── gmail ──────────────────────────────────────────────────────────────
    gmail = top.add_parser("gmail", help="Gmail commands")
    gmail_sub = gmail.add_subparsers(dest="gmail_cmd", metavar="SUBCOMMAND")
    gmail_sub.required = True

    p = gmail_sub.add_parser("list", parents=[_profile_parser()], help="List emails")
    p.add_argument("--query", "-q", default="in:inbox", help="Gmail search query")
    p.add_argument("--count", "-n", type=int, default=10, help="Number of results")
    p.set_defaults(func=cmd_gmail_list)

    p = gmail_sub.add_parser("read", parents=[_profile_parser()], help="Read an email")
    p.add_argument("id", help="Message ID")
    p.set_defaults(func=cmd_gmail_read)

    p = gmail_sub.add_parser("send", parents=[_profile_parser()], help="Send an email")
    p.add_argument("--to", required=True, help="Recipient email")
    p.add_argument("--subject", required=True, help="Subject")
    p.add_argument("--body", required=True, help="Body text")
    p.set_defaults(func=cmd_gmail_send)

    p = gmail_sub.add_parser("archive-read", parents=[_profile_parser()], help="Mark as read and archive")
    p.add_argument("id", help="Message ID")
    p.set_defaults(func=cmd_gmail_archive_read)

    p = gmail_sub.add_parser("delete", parents=[_profile_parser()], help="Move to trash")
    p.add_argument("id", help="Message ID")
    p.set_defaults(func=cmd_gmail_delete)

    # ── calendar ───────────────────────────────────────────────────────────
    cal = top.add_parser("calendar", help="Calendar commands")
    cal_sub = cal.add_subparsers(dest="cal_cmd", metavar="SUBCOMMAND")
    cal_sub.required = True

    p = cal_sub.add_parser("list", parents=[_profile_parser()], help="List events")
    p.add_argument("--date", default="today", help="Date: today, tomorrow, or YYYY-MM-DD")
    p.add_argument("--calendar", default="primary",
                   help="Calendar id or email (default: primary). Requires read access for non-primary.")
    p.add_argument("--ids", action="store_true", help="Show event IDs")
    p.set_defaults(func=cmd_cal_list)

    p = cal_sub.add_parser("create", parents=[_profile_parser()], help="Create an event")
    p.add_argument("--title", required=True)
    p.add_argument("--start", required=True,
                   help="ISO datetime (2026-05-02T10:00:00), or a date (2026-05-02) with --all-day")
    p.add_argument("--end", default="",
                   help="ISO datetime; required unless --all-day. With --all-day: last day (inclusive), "
                        "defaults to --start for a single day")
    p.add_argument("--all-day", action="store_true",
                   help="Create an all-day event. --start/--end are dates (YYYY-MM-DD); "
                        "--end is the inclusive last day for multi-day events")
    p.add_argument("--attendees", default="", help="Comma-separated emails")
    p.add_argument("--description", default="", help="Event description")
    p.add_argument("--conferencing", choices=["meet", "zoom", "none"], default="meet",
                   help="Video conferencing to attach (default: meet). "
                        "'zoom' mints the meeting via Zoom API - needs ~/.config/gworkspace/zoom.json (see zoom.py).")
    p.set_defaults(func=cmd_cal_create)

    p = cal_sub.add_parser("availability", parents=[_profile_parser()], help="Check someone's free/busy")
    p.add_argument("--email", required=True, help="Email to check")
    p.add_argument("--date", default="today")
    p.set_defaults(func=cmd_cal_availability)

    p = cal_sub.add_parser("reschedule", parents=[_profile_parser()], help="Change start/end time of an event")
    p.add_argument("id", help="Event ID")
    p.add_argument("--start", required=True, help="New start datetime, e.g. 2026-06-03T14:35:00")
    p.add_argument("--end", required=True, help="New end datetime")
    p.set_defaults(func=cmd_cal_reschedule)

    p = cal_sub.add_parser("accept", parents=[_profile_parser()], help="Accept an event invitation")
    p.add_argument("id", help="Event ID")
    p.set_defaults(func=cmd_cal_accept)

    p = cal_sub.add_parser("decline", parents=[_profile_parser()], help="Decline an event invitation")
    p.add_argument("id", help="Event ID")
    p.set_defaults(func=cmd_cal_decline)

    p = cal_sub.add_parser("add-attendee", parents=[_profile_parser()], help="Add attendee to event")
    p.add_argument("id", help="Event ID")
    p.add_argument("--email", required=True)
    p.set_defaults(func=cmd_cal_add_attendee)

    p = cal_sub.add_parser("cancel", parents=[_profile_parser()], help="Cancel (delete) an event you organize")
    p.add_argument("id", help="Event ID")
    p.set_defaults(func=cmd_cal_cancel)

    # ── people ─────────────────────────────────────────────────────────────
    people = top.add_parser("people", help="People / contacts commands")
    people_sub = people.add_subparsers(dest="people_cmd", metavar="SUBCOMMAND")
    people_sub.required = True

    p = people_sub.add_parser("search", parents=[_profile_parser()], help="Search contacts")
    p.add_argument("query", help="Name to search for")
    p.set_defaults(func=cmd_people_search)

    # ── drive ──────────────────────────────────────────────────────────────
    drive = top.add_parser("drive", help="Google Drive commands")
    drive_sub = drive.add_subparsers(dest="drive_cmd", metavar="SUBCOMMAND")
    drive_sub.required = True
    file_help = "File id or Google URL (Docs/Sheets/Slides link, drive.google.com/file/d/..., folder link)"

    p = drive_sub.add_parser("list", parents=[_profile_parser()], help="Recently modified files")
    p.add_argument("--count", "-n", type=int, default=10, help="Number of results")
    p.add_argument("--folder", default=None, help="Only files in this folder (id or URL)")
    p.set_defaults(func=cmd_drive_list)

    p = drive_sub.add_parser("search", parents=[_profile_parser()], help="Search by name and content")
    p.add_argument("text", help="Text to search for")
    p.add_argument("--count", "-n", type=int, default=20, help="Number of results")
    p.set_defaults(func=cmd_drive_search)

    p = drive_sub.add_parser("read", parents=[_profile_parser()],
                             help="Print a file as text (Doc -> Markdown, Sheet -> CSV, Slides -> text, folder -> listing)")
    p.add_argument("file", help=file_help)
    p.set_defaults(func=cmd_drive_read)

    p = drive_sub.add_parser("download", parents=[_profile_parser()], help="Save a file locally")
    p.add_argument("file", help=file_help)
    p.add_argument("--out", "-o", default=None, help="Target path or directory (default: the file name)")
    p.add_argument("--format", "-f", default=None, choices=sorted(DOWNLOAD_FORMATS),
                   help="Export format for Google Docs/Sheets/Slides (default: docx/xlsx/pptx)")
    p.set_defaults(func=cmd_drive_download)

    p = drive_sub.add_parser("upload", parents=[_profile_parser()], help="Upload a local file as is")
    p.add_argument("path", help="Local file")
    p.add_argument("--folder", default=None, help="Destination folder (id or URL); default: My Drive root")
    p.add_argument("--name", default=None, help="Name in Drive (default: the local file name)")
    p.set_defaults(func=cmd_drive_upload)

    p = drive_sub.add_parser("create", parents=[_profile_parser()],
                             help="Create a folder, a Google Doc or a Google Sheet")
    p.add_argument("--name", required=True, help="Name of the new item")
    p.add_argument("--type", required=True, choices=sorted(CREATE_TYPES), help="folder, doc or sheet")
    p.add_argument("--folder", default=None, help="Parent folder (id or URL); default: My Drive root")
    p.add_argument("--from-file", default=None,
                   help="Convert a local file into the Doc (md/txt/html/docx) or Sheet (csv/tsv/xlsx)")
    p.set_defaults(func=cmd_drive_create)

    return parser


# ── entry point ────────────────────────────────────────────────────────────
def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)
    except (AuthError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
