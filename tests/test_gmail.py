import base64
import email
from email.mime.text import MIMEText

import pytest

from gworkspace import gmail


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


PDF = b"%PDF-1.4 fake pdf"
JPG = b"\xff\xd8\xff fake jpeg"
TXT = b"notes, not the body"

PAYLOAD = {
    "mimeType": "multipart/mixed",
    "parts": [
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "body": {"data": b64(b"Hello body")}},
            {"mimeType": "text/html", "body": {"data": b64(b"<p>Hello body</p>")}},
        ]},
        {"mimeType": "application/pdf", "filename": "report.pdf",
         "body": {"attachmentId": "att-pdf", "size": 250_000}},
        {"mimeType": "image/jpeg", "filename": "photo.jpg",  # inline, but named: still listed
         "headers": [{"name": "Content-Disposition", "value": "inline"}],
         "body": {"data": b64(JPG), "size": len(JPG)}},
    ],
}


class FakeService:
    """Just enough of the Gmail API client for gmail.py."""

    def __init__(self, payload=None, attachments=None):
        self.payload = payload or {}
        self.attachment_data = attachments or {}
        self.sent = None

    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return self

    def get(self, userId, id, format=None, messageId=None, **_):
        if messageId is not None:  # attachments().get
            return Call({"data": b64(self.attachment_data[id])})
        headers = [{"name": "Subject", "value": "S"}, {"name": "From", "value": "a@x.com"},
                   {"name": "Date", "value": "Mon"}]
        return Call({"id": id, "payload": {**self.payload, "headers": headers}})

    def send(self, userId, body, media_body=None):
        self.sent = {"body": body, "media": media_body}
        return Call({"id": "sent"})


class Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


@pytest.fixture
def svc(monkeypatch):
    fake = FakeService(PAYLOAD, {"att-pdf": PDF})
    monkeypatch.setattr(gmail, "_service", lambda creds: fake)
    return fake


# ── attachment walk ────────────────────────────────────────────────────────
def test_attachments_walk_nested_and_inline():
    found = gmail._attachments(PAYLOAD)
    assert [(a["name"], a["mime"], a["id"]) for a in found] == [
        ("report.pdf", "application/pdf", "att-pdf"),
        ("photo.jpg", "image/jpeg", None),
    ]
    assert found[1]["data"] == b64(JPG)


def test_attachments_none():
    assert gmail._attachments({"mimeType": "text/plain", "body": {"data": b64(b"x")}}) == []
    assert gmail._attachments({}) == []


def test_named_text_part_is_not_the_body():
    payload = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "text/plain", "filename": "notes.txt", "body": {"data": b64(TXT)}},
        {"mimeType": "text/plain", "body": {"data": b64(b"real body")}},
    ]}
    assert gmail._body_text(payload) == "real body"


@pytest.mark.parametrize("n, text", [(0, "0 B"), (900, "900 B"), (250_000, "244 KB"),
                                     (3 * 1024 * 1024 + 200_000, "3.2 MB")])
def test_human_size(n, text):
    assert gmail._human_size(n) == text


# ── read ───────────────────────────────────────────────────────────────────
def test_read_lists_attachments(svc, capsys):
    gmail.gmail_read(None, "m1")
    out = capsys.readouterr().out
    assert "Hello body" in out
    assert out.rstrip().endswith("Attachments:\n1. report.pdf (application/pdf, 244 KB)\n"
                                 f"2. photo.jpg (image/jpeg, {len(JPG)} B)")


def test_read_without_attachments_is_unchanged(svc, capsys):
    svc.payload = {"mimeType": "text/plain", "body": {"data": b64(b"Just text")}}
    gmail.gmail_read(None, "m1")
    assert capsys.readouterr().out == "Subject : S\nFrom    : a@x.com\nDate    : Mon\n\nJust text\n"


def test_read_lists_attachments_without_plain_body(svc, capsys):
    svc.payload = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "text/html", "body": {"data": b64(b"<p>x</p>")}},
        {"mimeType": "application/pdf", "filename": "a.pdf", "body": {"attachmentId": "att-pdf", "size": 10}},
    ]}
    gmail.gmail_read(None, "m1")
    out = capsys.readouterr().out
    assert "(no plain text body)" in out and "1. a.pdf (application/pdf, 10 B)" in out


# ── attachment ─────────────────────────────────────────────────────────────
def test_save_one_to_directory(svc, tmp_path, capsys):
    gmail.gmail_attachment(None, "m1", index=1, out=str(tmp_path))
    assert (tmp_path / "report.pdf").read_bytes() == PDF
    assert capsys.readouterr().out == f"Saved {tmp_path / 'report.pdf'} ({len(PDF)} bytes)\n"


def test_save_one_to_file_name(svc, tmp_path):
    gmail.gmail_attachment(None, "m1", index=2, out=str(tmp_path / "sub" / "me.jpg"))
    assert (tmp_path / "sub" / "me.jpg").read_bytes() == JPG


def test_save_all_into_new_directory(svc, tmp_path, capsys):
    target = tmp_path / "mail"
    gmail.gmail_attachment(None, "m1", all_=True, out=str(target))
    assert (target / "report.pdf").read_bytes() == PDF
    assert (target / "photo.jpg").read_bytes() == JPG
    assert capsys.readouterr().out.count("Saved ") == 2


def test_save_to_current_directory(svc, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gmail.gmail_attachment(None, "m1", index=1)
    assert (tmp_path / "report.pdf").read_bytes() == PDF


def test_name_taken_gets_suffix(svc, tmp_path):
    (tmp_path / "report.pdf").write_bytes(b"old")
    (tmp_path / "report-1.pdf").write_bytes(b"older")
    gmail.gmail_attachment(None, "m1", index=1, out=str(tmp_path))
    assert (tmp_path / "report.pdf").read_bytes() == b"old"
    assert (tmp_path / "report-1.pdf").read_bytes() == b"older"
    assert (tmp_path / "report-2.pdf").read_bytes() == PDF


def test_unsafe_name_stays_in_directory(svc, tmp_path):
    svc.payload = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "application/pdf", "filename": "../../evil.pdf", "body": {"data": b64(PDF)}},
        {"mimeType": "application/pdf", "filename": "..", "body": {"data": b64(PDF)}},
    ]}
    target = tmp_path / "in"
    gmail.gmail_attachment(None, "m1", all_=True, out=str(target))
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == ["_.._evil.pdf", "attachment-2"]
    assert all(p.parent == target for p in target.iterdir())


def test_bad_index(svc, tmp_path):
    with pytest.raises(ValueError, match="has 2 attachments"):
        gmail.gmail_attachment(None, "m1", index=3, out=str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_no_attachments(svc, tmp_path):
    svc.payload = {"mimeType": "text/plain", "body": {"data": b64(b"x")}}
    with pytest.raises(ValueError, match="no attachments"):
        gmail.gmail_attachment(None, "m1", all_=True, out=str(tmp_path))


@pytest.mark.parametrize("index, all_", [(None, False), (1, True)])
def test_index_xor_all(svc, index, all_):
    with pytest.raises(ValueError, match="index or --all"):
        gmail.gmail_attachment(None, "m1", index=index, all_=all_)


def test_all_into_existing_file_fails(svc, tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(ValueError, match="needs a directory"):
        gmail.gmail_attachment(None, "m1", all_=True, out=str(f))


# ── send ───────────────────────────────────────────────────────────────────
def sent_message(svc) -> email.message.Message:
    media = svc.sent["media"]
    assert media.mimetype() == "message/rfc822"
    return email.message_from_bytes(media.getbytes(0, media.size()), policy=email.policy.default)


def test_send_without_attachments_is_unchanged(svc, capsys):
    gmail.gmail_send(None, "a@x.com", "Hi", "Body")
    expected = MIMEText("Body")
    expected["to"] = "a@x.com"
    expected["subject"] = "Hi"
    assert svc.sent == {"body": {"raw": base64.urlsafe_b64encode(expected.as_bytes()).decode()},
                        "media": None}
    assert capsys.readouterr().out == "Sent to a@x.com.\n"


def test_send_with_attachments(svc, tmp_path, capsys):
    pdf = tmp_path / "Договор.pdf"
    pdf.write_bytes(PDF)
    blob = tmp_path / "data.unknownext"
    blob.write_bytes(b"\x00\x01")
    gmail.gmail_send(None, "a@x.com", "Контракт", "See attached", attach=[str(pdf), str(blob)])
    msg = sent_message(svc)
    assert msg["To"] == "a@x.com" and msg["Subject"] == "Контракт"
    assert msg.get_body(("plain",)).get_content().strip() == "See attached"
    parts = list(msg.iter_attachments())
    assert [(p.get_filename(), p.get_content_type(), p.get_content()) for p in parts] == [
        ("Договор.pdf", "application/pdf", PDF),
        ("data.unknownext", "application/octet-stream", b"\x00\x01"),
    ]
    assert capsys.readouterr().out == "Sent to a@x.com. 2 attachments.\n"


def test_send_text_attachment(svc, tmp_path):
    f = tmp_path / "notes.txt"
    f.write_bytes(TXT)
    gmail.gmail_send(None, "a@x.com", "S", "B", attach=[str(f)])
    (part,) = sent_message(svc).iter_attachments()
    assert part.get_filename() == "notes.txt" and part.get_content_type() == "text/plain"


def test_send_missing_file_sends_nothing(svc, tmp_path):
    with pytest.raises(FileNotFoundError, match="nope.pdf"):
        gmail.gmail_send(None, "a@x.com", "S", "B", attach=[str(tmp_path / "nope.pdf")])
    assert svc.sent is None


def test_send_too_large_sends_nothing(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(gmail, "MAX_ATTACH_BYTES", 1000)
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"x" * 600)
    b.write_bytes(b"x" * 600)
    with pytest.raises(ValueError, match="drive upload"):
        gmail.gmail_send(None, "a@x.com", "S", "B", attach=[str(a), str(b)])
    assert svc.sent is None


def test_send_26_mb_is_too_large(svc, tmp_path):
    big = tmp_path / "big.bin"
    with big.open("wb") as fh:
        fh.truncate(26 * 1024 * 1024)
    with pytest.raises(ValueError, match="Gmail allows 25 MB"):
        gmail.gmail_send(None, "a@x.com", "S", "B", attach=[str(big)])
    assert svc.sent is None
