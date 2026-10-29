import base64
import email as email_lib
import io
import mimetypes
import os
from email.message import EmailMessage
from email.mime.text import MIMEText
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

MAX_ATTACH_BYTES = 25 * 1024 * 1024  # Gmail's limit for attachments in one message


def _service(creds):
    return build("gmail", "v1", credentials=creds)


def _header(msg, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _body_text(payload: dict) -> str:
    if payload.get("filename"):  # an attached .txt is not the body
        return ""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        text = _body_text(part)
        if text:
            return text
    return ""


def _attachments(payload: dict) -> list[dict]:
    """Every part with a file name, depth-first in message order; `read` and `attachment`
    share this walk, so the index printed by one is the index the other accepts."""
    found = []

    def walk(part):
        if part.get("filename"):
            body = part.get("body", {})
            found.append({"name": part["filename"], "mime": part.get("mimeType", ""),
                          "size": body.get("size", 0), "id": body.get("attachmentId"),
                          "data": body.get("data")})
            return  # an attached message is one file
        for sub in part.get("parts", []):
            walk(sub)

    walk(payload)
    return found


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _safe_name(name: str, index: int) -> str:
    name = name.replace("/", "_").replace("\\", "_").strip().lstrip(".")
    return name or f"attachment-{index}"


def _free_path(path: Path) -> Path:
    """`path`, or `name-1.ext`, `name-2.ext`, ... when it is taken; never overwrite."""
    if not path.exists():
        return path
    i = 1
    while True:
        candidate = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def gmail_list(creds, query: str = "in:inbox", count: int = 10):
    svc = _service(creds)
    result = svc.users().messages().list(userId="me", q=query, maxResults=count).execute()
    messages = result.get("messages", [])
    if not messages:
        print("No messages found.")
        return
    for i, m in enumerate(messages, 1):
        msg = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                         metadataHeaders=["Subject", "From", "Date"]).execute()
        subject = _header(msg, "Subject") or "(no subject)"
        sender = _header(msg, "From")
        date = _header(msg, "Date")
        print(f"{i}. [{m['id']}] {subject}")
        print(f"   From: {sender}  |  {date}")


def gmail_read(creds, msg_id: str):
    svc = _service(creds)
    msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
    print(f"Subject : {_header(msg, 'Subject')}")
    print(f"From    : {_header(msg, 'From')}")
    print(f"Date    : {_header(msg, 'Date')}")
    print()
    print(_body_text(msg.get("payload", {})) or "(no plain text body)")
    attachments = _attachments(msg.get("payload", {}))
    if attachments:
        print()
        print("Attachments:")
        for i, a in enumerate(attachments, 1):
            print(f"{i}. {a['name']} ({a['mime']}, {_human_size(a['size'])})")


def gmail_attachment(creds, msg_id: str, index: int | None = None, all_: bool = False,
                     out: str | None = None):
    if (index is None) == (not all_):
        raise ValueError("give an attachment index or --all (not both)")
    svc = _service(creds)
    msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
    attachments = _attachments(msg.get("payload", {}))
    if not attachments:
        raise ValueError(f"message {msg_id} has no attachments")
    if all_:
        chosen = list(enumerate(attachments, 1))
    else:
        if not 1 <= index <= len(attachments):
            raise ValueError(f"no attachment {index}: message {msg_id} has {len(attachments)} "
                             f"attachment{'s' if len(attachments) != 1 else ''}")
        chosen = [(index, attachments[index - 1])]

    target = Path(out) if out else Path(".")
    to_dir = all_ or not out or target.is_dir() or out.endswith(("/", os.sep))
    if to_dir:
        if target.exists() and not target.is_dir():
            raise ValueError(f"--out {out} is a file; --all needs a directory")
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)

    for i, a in chosen:
        data = a["data"]
        if data is None:
            data = svc.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=a["id"]).execute()["data"]
        path = _free_path(target / _safe_name(a["name"], i) if to_dir else target)
        path.write_bytes(_b64decode(data))
        print(f"Saved {path} ({path.stat().st_size} bytes)")


def _check_attachments(paths: list[str]) -> list[Path]:
    """Fail before anything is sent: every file exists and is readable, total at most 25 MB."""
    files = [Path(p).expanduser() for p in paths]
    for f in files:
        if not f.is_file():
            raise FileNotFoundError(f"attachment not found: {f}")
        if not os.access(f, os.R_OK):
            raise PermissionError(f"attachment not readable: {f}")
    total = sum(f.stat().st_size for f in files)
    if total > MAX_ATTACH_BYTES:
        raise ValueError(f"attachments are {_human_size(total)}, Gmail allows 25 MB; "
                         f"upload them with `drive upload` and send the link instead")
    return files


def _with_attachments(to: str, subject: str, body: str, files: list[Path]) -> bytes:
    msg = EmailMessage()  # default policy: RFC 2231 file names, so Cyrillic names survive
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    for f in files:
        mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(f.read_bytes(), maintype=maintype, subtype=subtype, filename=f.name)
    return msg.as_bytes()


def gmail_send(creds, to: str, subject: str, body: str, attach: list[str] | None = None):
    files = _check_attachments(attach or [])
    svc = _service(creds)
    if not files:
        mime = MIMEText(body)
        mime["to"] = to
        mime["subject"] = subject
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        print(f"Sent to {to}.")
        return
    # media upload takes messages up to 35 MB; a base64 `raw` JSON body is far smaller
    media = MediaIoBaseUpload(io.BytesIO(_with_attachments(to, subject, body, files)),
                              mimetype="message/rfc822", resumable=True)
    svc.users().messages().send(userId="me", body={}, media_body=media).execute()
    print(f"Sent to {to}. {len(files)} attachment{'s' if len(files) != 1 else ''}.")


def gmail_archive_read(creds, msg_id: str):
    svc = _service(creds)
    svc.users().messages().modify(
        userId="me", id=msg_id,
        body={"removeLabelIds": ["UNREAD", "INBOX"]},
    ).execute()
    print(f"Message {msg_id} marked as read and archived.")


def gmail_delete(creds, msg_id: str):
    svc = _service(creds)
    svc.users().messages().trash(userId="me", id=msg_id).execute()
    print(f"Message {msg_id} moved to trash.")
