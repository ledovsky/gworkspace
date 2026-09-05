import base64
import email as email_lib
from email.mime.text import MIMEText

from googleapiclient.discovery import build


def _service(creds):
    return build("gmail", "v1", credentials=creds)


def _header(msg, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _body_text(payload: dict) -> str:
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        text = _body_text(part)
        if text:
            return text
    return ""


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


def gmail_send(creds, to: str, subject: str, body: str):
    svc = _service(creds)
    mime = MIMEText(body)
    mime["to"] = to
    mime["subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"Sent to {to}.")


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
