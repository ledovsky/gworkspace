# gworkspace

Command-line client for Google Workspace, written for two kinds of users: a person in a terminal
and an AI agent that runs shell commands. Gmail, Calendar, People (contacts + company directory)
and Google Drive, several Google accounts side by side ("profiles"), plain-text output that is
easy to read and easy to parse.

- One binary, no daemon, no MCP server: `gworkspace calendar list --profile work --date tomorrow`.
- Bring your own Google Cloud OAuth client; tokens stay on your machine.
- Works on headless servers: the consent flow can run without a browser through an SSH tunnel.
- Ships an agent skill (`skills/gworkspace/SKILL.md`) that teaches Claude Code / OpenClaw-style
  agents how to use it.

## Install

Requires Python 3.11+. [uv](https://docs.astral.sh/uv/) is the recommended installer.

```bash
# run without installing
uvx --from git+https://github.com/ledovsky/gworkspace gworkspace --version

# install the `gworkspace` command into ~/.local/bin
uv tool install git+https://github.com/ledovsky/gworkspace
uv tool upgrade gworkspace          # later

# pin a release
uv tool install "git+https://github.com/ledovsky/gworkspace@v0.3.1"

# hack on it
git clone git@github.com:ledovsky/gworkspace.git ~/projects/gworkspace
uv tool install --editable ~/projects/gworkspace
```

Private repository over SSH (for example on a server with a deploy key):
`uv tool install git+ssh://git@github.com/ledovsky/gworkspace.git@v0.3.1`.

`pip install gworkspace` will work once the package is on PyPI.

## Google Cloud setup

gworkspace does not ship an OAuth client. Create your own once; it takes about five minutes.

1. Open <https://console.cloud.google.com/> and create a project (or pick an existing one).
2. **APIs & Services → Library**: enable *Gmail API*, *Google Calendar API*, *People API*,
   *Google Drive API*.
3. **APIs & Services → OAuth consent screen**: user type *External*, fill in the app name and your
   email. Either add the Google accounts you will use as *Test users* (tokens then expire after
   7 days) or publish the app (*In production*). An unverified production app shows a
   "Google hasn't verified this app" warning that you click through; for personal use that is fine.
   Add the scopes listed below or leave the list empty (the consent screen will still ask for them).
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**, application type
   *Desktop app*. Download the JSON and save it as `~/.config/gworkspace/credentials.json`.

Scopes requested by `gworkspace auth`:

| Scope | Used by |
|---|---|
| `https://www.googleapis.com/auth/gmail.modify` | `gmail *`, `whoami`, the Zoom variant of `calendar create` |
| `https://www.googleapis.com/auth/calendar` | `calendar *` |
| `https://www.googleapis.com/auth/contacts.readonly` | `people search` |
| `https://www.googleapis.com/auth/directory.readonly` | `people search` (Workspace directory) |
| `https://www.googleapis.com/auth/drive` | `drive *` |

`drive` and `gmail.modify` are *restricted* scopes: Google requires a security assessment only if
you distribute the app to other people. Using your own client for your own accounts needs nothing.

## Authentication and profiles

Every command takes `--profile <name>`. A profile is one Google account; name them as you like
(`personal`, `work`, ...).

```bash
gworkspace auth --profile personal     # opens the browser, saves the token
gworkspace whoami --profile personal   # which account is this?
```

### Files on disk

Everything lives under `~/.config/gworkspace/`; nothing is stored anywhere else and nothing is
sent anywhere but Google.

| Path | What | Written by |
|---|---|---|
| `credentials.json` | Your OAuth **client** (client id + secret) downloaded from Google Cloud. Identifies the app, not a user. | you, once |
| `tokens/<profile>.json` | The user **token** for that profile: refresh token, current access token, its expiry, the granted scopes and the account email. This is the credential that reads your mail and files; mode 600. | `gworkspace auth`; refreshed in place by every command when the access token has expired |
| `zoom.json` | Optional Zoom configuration for `calendar create --conferencing zoom`. | you |

Refresh tokens do not expire on their own (with the app *In production*). To revoke a profile,
delete its token file and remove the app under <https://myaccount.google.com/permissions>; a
machine that should stop having access needs the same two steps. Copying a token file to another
machine works, but the intended way is to run `gworkspace auth` on each machine so each holds
its own token and can be revoked separately.

### Headless machine (server, container)

Run the consent flow on the server with a fixed port and no browser, forward that port from a
machine that has a browser, and open the printed URL there:

```bash
# on the server
gworkspace auth --profile work --port 3336 --no-browser --login-hint you@company.com

# on your laptop, in another terminal
ssh -N -L 3336:127.0.0.1:3336 my-server
# then open the URL the server printed; Google redirects to http://localhost:3336/... which the
# tunnel delivers to the waiting gworkspace process
```

`--login-hint` preselects the account on Google's chooser; handy when several accounts are signed in.

### Scope errors

When gworkspace gains a scope (Drive was added in 0.3.0), tokens created earlier keep working for
everything they were granted, and the commands that need the new scope fail fast with:

```
Error: token for profile 'work' lacks https://www.googleapis.com/auth/drive; run `gworkspace auth --profile work` to re-consent with the current scopes
```

Re-run `auth` for that profile and you are done. Commands never start a browser flow on their own,
so an agent cannot hang on a consent prompt; a missing token is reported the same explicit way.

## Commands

All commands need `--profile`. Output is plain text; lists are Markdown tables or numbered lines.

### Account

```bash
gworkspace auth --profile P [--port N] [--no-browser] [--login-hint EMAIL]
gworkspace whoami --profile P
gworkspace --version
```

### Gmail

```bash
gworkspace gmail list --profile P [--query "in:inbox"] [--count 10]
gworkspace gmail read --profile P <message-id>
gworkspace gmail send --profile P --to EMAIL --subject "..." --body "..."
gworkspace gmail archive-read --profile P <message-id>     # mark read + remove from inbox
gworkspace gmail delete --profile P <message-id>           # move to trash
```

`--query` takes Gmail search syntax: `from:alice is:unread newer_than:2d`.

### Calendar

```bash
gworkspace calendar list --profile P [--date today|tomorrow|YYYY-MM-DD] [--calendar EMAIL] [--ids]
gworkspace calendar create --profile P --title "..." --start 2026-05-02T10:00:00 --end 2026-05-02T10:30:00 \
    [--attendees a@x.com,b@y.com] [--description "..."] [--conferencing meet|zoom|none]
gworkspace calendar create --profile P --title "..." --start 2026-05-02 [--end 2026-05-03] --all-day
gworkspace calendar availability --profile P --email EMAIL [--date ...]
gworkspace calendar reschedule --profile P <event-id> --start ... --end ...
gworkspace calendar accept --profile P <event-id>
gworkspace calendar decline --profile P <event-id>
gworkspace calendar add-attendee --profile P <event-id> --email EMAIL
gworkspace calendar cancel --profile P <event-id>
```

- Datetimes are local ISO 8601 without offset; the machine's timezone is sent to Google.
- `list --ids` adds *Organizer* and *ID* columns; the other commands need those IDs.
- `list` expands recurring events, so an ID like `abc_20260727T080000Z` is one occurrence;
  rescheduling it moves only that occurrence.
- `--all-day`: `--end` is the inclusive last day; omit it for a single day.
- `--conferencing meet` (default) attaches a Google Meet link. `zoom` mints a meeting through the
  Zoom API and attaches it; configure `~/.config/gworkspace/zoom.json` first, see the docstring in
  `src/gworkspace/zoom.py` for the two supported modes (Server-to-Server OAuth app, or a static
  personal meeting link).
- `list --calendar EMAIL` shows a colleague's events when their calendar is shared with you;
  `availability` only returns busy slots but works for anyone in your Workspace domain.

### People

```bash
gworkspace people search --profile P "<name>"
```

Searches your contacts and the Workspace directory. Directory names are stored in one script
(usually Latin); a short last-name fragment such as `petrov` is the most reliable query.

### Drive

```bash
gworkspace drive list --profile P [--count 10] [--folder <id|url>]
gworkspace drive search --profile P "<text>" [--count 20]
gworkspace drive read --profile P <id|url>
gworkspace drive download --profile P <id|url> [--out PATH] [--format docx|xlsx|pptx|pdf|md|csv|txt|html]
gworkspace drive upload --profile P PATH [--folder <id|url>] [--name NAME]
gworkspace drive create --profile P --name NAME --type folder|doc|sheet [--folder <id|url>] [--from-file PATH]
```

- Files are addressed by id or by any Google link: `docs.google.com/document/d/<id>/edit`,
  `docs.google.com/spreadsheets/d/<id>`, `drive.google.com/file/d/<id>/view`,
  `drive.google.com/drive/folders/<id>`, `drive.google.com/open?id=<id>`. A link somebody shared
  with you works as long as your account can open it in the browser.
- `list` and `search` print a table with name, type, modified time, id and link, newest first.
  `search` matches file names and full text. Shared drives are included.
- `read` prints text: Google Docs as Markdown, Sheets as CSV (first sheet), Slides as plain
  text, text files verbatim, a folder as its listing. Binary files get a hint to `download`.
- `download` saves the bytes; Google-native files are exported (`--format`, default docx/xlsx/pptx).
- `upload` stores a local file as is. `create --from-file` converts it instead: Markdown, text,
  HTML or docx into a Google Doc; CSV, TSV or xlsx into a Google Sheet.

## Agent skill

`skills/gworkspace/SKILL.md` is a ready-to-use skill for Claude Code, OpenClaw and similar agents:
copy or symlink the `skills/gworkspace` directory into the agent's skills folder
(`.claude/skills/` for Claude Code). It explains when to use which command and the pitfalls above.

## Development

```bash
git clone git@github.com:ledovsky/gworkspace.git && cd gworkspace
uv sync                 # creates .venv with the dev group (pytest)
uv run pytest
uv run gworkspace --version
```

Tests cover only pure functions (argument parsing, URL parsing, scope checks); anything that talks
to Google is verified by hand against real accounts.

Layout: `src/gworkspace/` with one module per API (`gmail.py`, `gcalendar.py`, `people.py`,
`drive.py`, `zoom.py`), `auth.py` for OAuth and `cli.py` for argparse wiring.

## License

MIT, see `LICENSE`.
