"""Google Drive: list, search, read, download, upload, create.

Files are addressed by id or by any Google URL: Docs/Sheets/Slides links,
drive.google.com/file/d/<id>, drive.google.com/drive/folders/<id>, ...open?id=<id>.
"""
import mimetypes
import sys
from datetime import datetime
from pathlib import Path
import re

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from .utils import fmt_table

GOOGLE_PREFIX = "application/vnd.google-apps."
FOLDER = GOOGLE_PREFIX + "folder"
DOC = GOOGLE_PREFIX + "document"
SHEET = GOOGLE_PREFIX + "spreadsheet"
SLIDES = GOOGLE_PREFIX + "presentation"

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# `drive read`: Google-native type -> text export
READ_EXPORT = {DOC: "text/markdown", SHEET: "text/csv", SLIDES: "text/plain"}
# `drive download --format`: extension -> export mime
DOWNLOAD_FORMATS = {
    "docx": DOCX, "xlsx": XLSX, "pptx": PPTX, "pdf": "application/pdf",
    "md": "text/markdown", "csv": "text/csv", "txt": "text/plain", "html": "text/html",
}
DEFAULT_DOWNLOAD_FORMAT = {DOC: "docx", SHEET: "xlsx", SLIDES: "pptx"}
# `drive create --type`
CREATE_TYPES = {"folder": FOLDER, "doc": DOC, "sheet": SHEET}
# `drive create --from-file`: extension -> source mime Drive converts from
IMPORT_MIMES = {
    ".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain", ".html": "text/html",
    ".csv": "text/csv", ".tsv": "text/tab-separated-values", ".docx": DOCX, ".xlsx": XLSX,
}
TEXT_MIMES = {"application/json", "application/xml", "application/x-yaml", "application/yaml",
              "application/javascript", "application/x-sh"}
TYPE_LABELS = {
    FOLDER: "folder", DOC: "doc", SHEET: "sheet", SLIDES: "slides",
    GOOGLE_PREFIX + "form": "form", GOOGLE_PREFIX + "drawing": "drawing",
    GOOGLE_PREFIX + "shortcut": "shortcut", "application/pdf": "pdf",
    DOCX: "docx", XLSX: "xlsx", PPTX: "pptx",
}

FIELDS = "id,name,mimeType,modifiedTime,size,webViewLink"
HEADERS = ("Name", "Type", "Modified", "ID", "Link")

_URL_PATTERNS = [
    re.compile(r"docs\.google\.com/(?:document|spreadsheets|presentation|forms|drawings)/(?:u/\d+/)?d/([A-Za-z0-9_-]+)"),
    re.compile(r"drive\.google\.com/file/(?:u/\d+/)?d/([A-Za-z0-9_-]+)"),
    re.compile(r"drive\.google\.com/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]+)"),
    re.compile(r"[?&]id=([A-Za-z0-9_-]+)"),
]
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")


# ── pure helpers ───────────────────────────────────────────────────────────
def file_id(ref: str) -> str:
    """Drive file id from a bare id or a Google Docs/Drive URL."""
    ref = ref.strip()
    for pat in _URL_PATTERNS:
        m = pat.search(ref)
        if m:
            return m.group(1)
    if _BARE_ID.match(ref):
        return ref
    raise ValueError(f"not a Drive id or URL: {ref!r}")


def type_label(mime: str) -> str:
    if mime in TYPE_LABELS:
        return TYPE_LABELS[mime]
    if mime.startswith(GOOGLE_PREFIX):
        return mime[len(GOOGLE_PREFIX):]
    return mime


def escape_query(value: str) -> str:
    """Escape a string literal for the Drive `q` parameter."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def import_mime(path: Path) -> str:
    return IMPORT_MIMES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "text/plain"


def _fmt_time(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")


def _safe_name(name: str) -> str:
    return name.replace("/", "_")


# ── API ────────────────────────────────────────────────────────────────────
def _service(creds):
    return build("drive", "v3", credentials=creds)


def _get(svc, ref: str) -> dict:
    return svc.files().get(fileId=file_id(ref), fields=FIELDS, supportsAllDrives=True).execute()


def _list(svc, q: str, count: int, order: str | None = "modifiedTime desc") -> list[dict]:
    kwargs = dict(q=q, pageSize=count, fields=f"files({FIELDS})",
                  corpora="allDrives", supportsAllDrives=True, includeItemsFromAllDrives=True)
    if order:
        kwargs["orderBy"] = order
    try:
        return svc.files().list(**kwargs).execute().get("files", [])
    except HttpError as e:
        if order and e.resp.status == 400:  # some query shapes reject orderBy; take Drive's order
            kwargs.pop("orderBy")
            return svc.files().list(**kwargs).execute().get("files", [])
        raise


def _print_files(files: list[dict], empty: str) -> None:
    if not files:
        print(empty)
        return
    rows = [(f["name"], type_label(f["mimeType"]), _fmt_time(f["modifiedTime"]), f["id"],
             f.get("webViewLink", "")) for f in files]
    print(fmt_table(rows, headers=HEADERS))


def drive_list(creds, count: int = 10, folder: str | None = None):
    svc = _service(creds)
    q = "trashed = false"
    if folder:
        q = f"'{file_id(folder)}' in parents and " + q
    _print_files(_list(svc, q, count), "No files.")


def drive_search(creds, text: str, count: int = 20):
    svc = _service(creds)
    t = escape_query(text)
    q = f"(fullText contains '{t}' or name contains '{t}') and trashed = false"
    _print_files(_list(svc, q, count), f"No files match '{text}'.")


def drive_read(creds, ref: str):
    """Print the file as text: Docs as Markdown, Sheets as CSV (first sheet), Slides as text,
    text files verbatim, folders as a listing."""
    svc = _service(creds)
    f = _get(svc, ref)
    mime = f["mimeType"]
    if mime == FOLDER:
        _print_files(_list(svc, f"'{f['id']}' in parents and trashed = false", 100), "Empty folder.")
        return
    if mime in READ_EXPORT:
        data = svc.files().export(fileId=f["id"], mimeType=READ_EXPORT[mime]).execute()
    elif mime.startswith("text/") or mime in TEXT_MIMES:
        data = svc.files().get_media(fileId=f["id"], supportsAllDrives=True).execute()
    elif mime.startswith(GOOGLE_PREFIX):
        raise ValueError(f"{f['name']} is a Google {type_label(mime)}, not readable as text; "
                         f"try `drive download {f['id']} --format pdf`")
    else:
        size = f.get("size", "?")
        raise ValueError(f"{f['name']} is {mime} ({size} bytes), not text; "
                         f"save it with `drive download {f['id']}`")
    sys.stdout.write(data.decode("utf-8", errors="replace"))
    sys.stdout.flush()


def drive_download(creds, ref: str, out: str | None = None, fmt: str | None = None):
    svc = _service(creds)
    f = _get(svc, ref)
    mime = f["mimeType"]
    if mime == FOLDER:
        raise ValueError("folders cannot be downloaded; use `drive read <folder>` to list them")
    if mime.startswith(GOOGLE_PREFIX):
        fmt = fmt or DEFAULT_DOWNLOAD_FORMAT.get(mime, "pdf")
        if fmt not in DOWNLOAD_FORMATS:
            raise ValueError(f"unknown --format {fmt!r}; choose from {', '.join(DOWNLOAD_FORMATS)}")
        request = svc.files().export_media(fileId=f["id"], mimeType=DOWNLOAD_FORMATS[fmt])
        default_name = f"{_safe_name(f['name'])}.{fmt}"
    else:
        if fmt:
            raise ValueError("--format applies to Google Docs/Sheets/Slides only")
        request = svc.files().get_media(fileId=f["id"], supportsAllDrives=True)
        default_name = _safe_name(f["name"])
    path = Path(out) if out else Path(default_name)
    if path.is_dir():
        path = path / default_name
    with path.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    print(f"Saved {path} ({path.stat().st_size} bytes)")


def _media(path: Path, mime: str) -> MediaFileUpload:
    return MediaFileUpload(str(path), mimetype=mime, resumable=path.stat().st_size > 0)


def drive_upload(creds, path: str, folder: str | None = None, name: str | None = None):
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"{p} is not a file")
    svc = _service(creds)
    body = {"name": name or p.name}
    if folder:
        body["parents"] = [file_id(folder)]
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    f = svc.files().create(body=body, media_body=_media(p, mime), fields=FIELDS,
                           supportsAllDrives=True).execute()
    print(f"Uploaded {f['name']} ({type_label(f['mimeType'])})")
    print(f"ID   : {f['id']}")
    print(f"Link : {f.get('webViewLink', '')}")


def drive_create(creds, name: str, kind: str, folder: str | None = None, from_file: str | None = None):
    """Create a folder, an empty Google Doc/Sheet, or a Doc/Sheet converted from a local file
    (Markdown/text/HTML/docx -> Doc; CSV/TSV/xlsx -> Sheet)."""
    if kind not in CREATE_TYPES:
        raise ValueError(f"unknown --type {kind!r}; choose from {', '.join(CREATE_TYPES)}")
    media = None
    if from_file:
        if kind == "folder":
            raise ValueError("--from-file needs --type doc or sheet")
        p = Path(from_file)
        if not p.is_file():
            raise FileNotFoundError(f"{p} is not a file")
        media = _media(p, import_mime(p))
    svc = _service(creds)
    body = {"name": name, "mimeType": CREATE_TYPES[kind]}
    if folder:
        body["parents"] = [file_id(folder)]
    f = svc.files().create(body=body, media_body=media, fields=FIELDS, supportsAllDrives=True).execute()
    print(f"Created {type_label(f['mimeType'])} {f['name']}")
    print(f"ID   : {f['id']}")
    print(f"Link : {f.get('webViewLink', '')}")
