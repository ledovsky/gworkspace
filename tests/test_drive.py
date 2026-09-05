from pathlib import Path

import pytest

from gworkspace import drive
from gworkspace.cli import build_parser, cmd_drive_create, cmd_drive_read

ID = "1AbC_dEf-GhIjKlMnOpQrStUvWxYz0123456789"


@pytest.mark.parametrize("ref", [
    ID,
    f"https://docs.google.com/document/d/{ID}/edit?usp=sharing",
    f"https://docs.google.com/document/u/1/d/{ID}/edit",
    f"https://docs.google.com/spreadsheets/d/{ID}/edit#gid=0",
    f"https://docs.google.com/presentation/d/{ID}/edit#slide=id.p",
    f"https://drive.google.com/file/d/{ID}/view?usp=drive_link",
    f"https://drive.google.com/drive/folders/{ID}?usp=sharing",
    f"https://drive.google.com/drive/u/0/folders/{ID}",
    f"https://drive.google.com/open?id={ID}",
    f"https://drive.google.com/uc?export=download&id={ID}",
    f"  {ID}\n",
])
def test_file_id_accepts_ids_and_urls(ref):
    assert drive.file_id(ref) == ID


@pytest.mark.parametrize("ref", ["", "short", "https://example.com/x", "https://drive.google.com/drive/my-drive"])
def test_file_id_rejects_garbage(ref):
    with pytest.raises(ValueError):
        drive.file_id(ref)


def test_type_label():
    assert drive.type_label(drive.DOC) == "doc"
    assert drive.type_label(drive.FOLDER) == "folder"
    assert drive.type_label("application/vnd.google-apps.jam") == "jam"
    assert drive.type_label("application/pdf") == "pdf"
    assert drive.type_label("image/png") == "image/png"


def test_escape_query():
    assert drive.escape_query("O'Brien\\x") == "O\\'Brien\\\\x"


def test_import_mime_by_extension():
    assert drive.import_mime(Path("notes.md")) == "text/markdown"
    assert drive.import_mime(Path("data.CSV")) == "text/csv"
    assert drive.import_mime(Path("plain")) == "text/plain"


def test_cli_drive_read_and_create():
    args = build_parser().parse_args(["drive", "read", "--profile", "w", f"https://docs.google.com/document/d/{ID}/edit"])
    assert args.func is cmd_drive_read and args.file.endswith("/edit")
    args = build_parser().parse_args(["drive", "create", "--profile", "w", "--name", "n", "--type", "doc",
                                      "--from-file", "x.md", "--folder", ID])
    assert args.func is cmd_drive_create and (args.type, args.from_file, args.folder) == ("doc", "x.md", ID)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["drive", "create", "--profile", "w", "--name", "n", "--type", "slides"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["drive", "download", "--profile", "w", ID, "--format", "odt"])
