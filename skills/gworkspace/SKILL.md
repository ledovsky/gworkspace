---
name: gworkspace
description: Use the `gworkspace` CLI to work with the user's Google accounts (Gmail, Calendar, People, Google Drive). Invoke when the user asks to read/send/archive/delete emails, list/create/accept/decline calendar events, check someone's availability, look up a contact's email, or list/search/read/download/upload Drive files and Google Docs. Multi-account via `--profile personal|work` (required on every command, no default).
---

# gworkspace skill

`gworkspace` is a CLI installed on PATH (usually `~/.local/bin/gworkspace`, via `uv tool install`).
Run it directly with the shell tool. `--profile <name>` selects the Google account and is
**required on every command** (e.g. `personal`, `work`). If the user does not say which account,
ask before running.

## When to use

- Email: read inbox, search messages, send, archive, delete
- Calendar: list events, create events, check free/busy, accept/decline invites, reschedule
- People: find someone's email address by name
- Drive: list recent files, search, read a Google Doc / Sheet / text file (also by a shared link),
  download, upload, create folders and documents

## Authentication

Tokens are cached in `~/.config/gworkspace/tokens/<profile>.json`. Commands never open a browser.
If a command fails with `Error: token for profile 'X' lacks <scope>` or `no token for profile 'X'`,
tell the user to run the printed `gworkspace auth --profile X` command (on a headless server:
`gworkspace auth --profile X --port 3336 --no-browser` plus an SSH tunnel, see the README).
Do not try to run `auth` yourself; it needs a browser.

Verify identity: `gworkspace whoami --profile <name>`

## Commands

### Gmail

```
gworkspace gmail list --profile P [--query "in:inbox"] [--count 10]
gworkspace gmail read --profile P <message-id>
gworkspace gmail send --profile P --to <email> --subject "<s>" --body "<b>"
gworkspace gmail archive-read --profile P <message-id>
gworkspace gmail delete --profile P <message-id>
```

`--query` accepts Gmail search syntax (`from:`, `is:unread`, `newer_than:2d`, etc.).

### Calendar

```
gworkspace calendar list --profile P [--date today|tomorrow|YYYY-MM-DD] [--calendar <email>] [--ids]
gworkspace calendar create --profile P --title "<t>" --start <iso> --end <iso> [--attendees a@x.com,b@y.com] [--conferencing meet|zoom|none]
gworkspace calendar create --profile P --title "<t>" --start <YYYY-MM-DD> [--end <YYYY-MM-DD>] --all-day
gworkspace calendar availability --profile P --email <email> [--date today|tomorrow|YYYY-MM-DD]
gworkspace calendar reschedule --profile P <event-id> --start <iso> --end <iso>
gworkspace calendar accept --profile P <event-id>
gworkspace calendar decline --profile P <event-id>
gworkspace calendar add-attendee --profile P <event-id> --email <email>
gworkspace calendar cancel --profile P <event-id>
```

Datetimes are local ISO 8601, e.g. `2026-05-16T10:00:00`.

**Getting event IDs:** `reschedule`, `accept`, `decline`, `add-attendee`, `cancel` need an event
ID. Get it with `calendar list --ids`, which adds an `Organizer` column (`me` when the user
organizes) and an `ID` column, so one call gives both the ID and the owner of each event.

**Rescheduling / moving a meeting:** use `reschedule` (not cancel+create). `list` expands
recurring events, so the ID of a **recurring** meeting is an instance ID with a
`_YYYYMMDDTHHMMSSZ` suffix (e.g. `6r9k..._20260727T080000Z`). Rescheduling that instance moves
**only that one occurrence**; the rest of the series is untouched. That is how to do a one-time move.

**You can only reschedule / cancel / add-attendee on events the user organizes.** A patch on
someone else's event does not move it for the other attendees. When a vague description matches
several events, run `list --ids` first and drop the ones whose organizer is not `me`; often one
candidate remains and there is no need to ask. Ask only if two or more owned events still match.

**All-day / multi-day events:** pass `--all-day` with plain dates (`YYYY-MM-DD`). `--end` is the
**inclusive** last day and may be omitted for a single day. One event spans the whole range; do
not create per-day blocks.
- One full day: `--start 2026-07-27 --all-day`
- Two full days (27–28 incl.): `--start 2026-07-27 --end 2026-07-28 --all-day`

No conference link is attached to all-day events. Timed events require `--end`.

**Seeing someone else's events:** two endpoints, different access levels:
- `calendar availability --email <email>` uses the free/busy API: only busy intervals, never
  titles. Works for anyone in the user's Workspace domain.
- `calendar list --calendar <email>` reads their calendar: titles and exact times. Requires
  "see all event details" access on their calendar (403 otherwise).

For "what is X doing on day Y?" try `list --calendar` first; on 403 fall back to `availability`.

### People

```
gworkspace people search --profile P "<name>"
```

Searches personal contacts and the Workspace directory.

**Transliteration:** Workspace directory profiles usually store names in Latin only; a query in
another script (e.g. Cyrillic) returns nothing even when that is how the colleague writes their
name, and Latin spellings vary (Anastasiya/Anastasia, Vitalii/Vitaly, Petrovskii/Petrovskiy).
Prefer a **partial last-name fragment in Latin** (`petrov`, `kuznets`, `sokolovsk`) and try
variants if the first misses.

### Drive

```
gworkspace drive list --profile P [--count 10] [--folder <id|url>]
gworkspace drive search --profile P "<text>" [--count 20]
gworkspace drive read --profile P <id|url>
gworkspace drive download --profile P <id|url> [--out PATH] [--format docx|xlsx|pptx|pdf|md|csv|txt|html]
gworkspace drive upload --profile P <local-path> [--folder <id|url>] [--name <name>]
gworkspace drive create --profile P --name <name> --type folder|doc|sheet [--folder <id|url>] [--from-file <local-path>]
```

- Any Google link the user pastes (Docs, Sheets, Slides, `drive.google.com/file/d/...`, folder
  links, `open?id=`) can be passed as is; no need to extract the id. Links shared by other people
  work too, as long as the account can open them.
- `read` is the way to get a document's content: Docs come back as Markdown, Sheets as CSV
  (first sheet only), Slides as text, folders as a listing. If it says the file is binary, use
  `download` and read the local file.
- `list`/`search` output is a table `| Name | Type | Modified | ID | Link |`, newest first; keep
  the link when reporting files so the user can click.
- To write a document: put the Markdown in a local file, then
  `drive create --type doc --name "<title>" --from-file <path> [--folder <url>]`. `upload` keeps
  the file as is (e.g. a PDF); `create --from-file` converts it into a native Doc/Sheet.
- Search text is matched against names and full text; try a shorter or Latin fragment when a
  query misses.

## Common workflows

**"List my meetings for tomorrow"**
```
gworkspace calendar list --profile <profile> --date tomorrow
```

**"Find a slot for me and Alice tomorrow"**
1. `gworkspace people search --profile P "Alice"` to get her email
2. `gworkspace calendar availability --profile P --email alice@... --date tomorrow`
3. `gworkspace calendar list --profile P --date tomorrow` (own schedule)
4. Propose a free slot; if the user confirms, `gworkspace calendar create ...` with `--attendees`

**"Summarize my recent emails"**
1. `gworkspace gmail list --profile P --query "in:inbox newer_than:2d" --count 20`
2. For each id of interest: `gworkspace gmail read --profile P <id>`
3. Summarize.

**"What is in this document?" (user pastes a Google link)**
```
gworkspace drive read --profile P "<link>"
```
Then summarize or answer from the printed text.

## Notes

- Always confirm before destructive or outward-facing actions (`delete`, `decline`, `send`,
  `cancel`) unless the user already specified the action explicitly. `cancel` emails every
  attendee a cancellation notice.
- Default output for events is a `| Event | Time |` table; keep that shape when summarizing.
- `--profile` is required on every command. If the user did not specify one, ask before running.
