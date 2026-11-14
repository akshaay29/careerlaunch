"""Files convention: Resume type from content (magic bytes), filename sanitising, paths."""

import io
import uuid
import zipfile

import pytest

from app.modules.profile.files import (
    DOCX,
    MAX_FILENAME_CHARS,
    PDF,
    sanitize_filename,
    sniff_resume_type,
    storage_path,
)


def make_docx(extra: dict[str, bytes] | None = None, *, document: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        if document:
            z.writestr("word/document.xml", "<w:document/>")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n", PDF),
        (b"%PDF-", PDF),
        (make_docx(), DOCX),
        (make_docx(document=False), None),  # a ZIP, but not a Word document
        (make_docx({"xl/workbook.xml": b"<x/>"}, document=False), None),  # XLSX
        (PNG, None),
        (b"", None),
        (b"%PDF", None),  # truncated magic
        (b" %PDF-1.7", None),  # magic not at the start
        (b"PK\x03\x04garbage-not-a-zip", None),
        (b"<html>%PDF-</html>", None),
        ("Résumé text".encode(), None),
    ],
    ids=[
        "pdf",
        "pdf-magic-only",
        "docx",
        "zip-without-document",
        "xlsx",
        "png",
        "empty",
        "truncated-pdf-magic",
        "leading-space",
        "broken-zip",
        "html",
        "plain-text",
    ],
)
def test_sniff_resume_type(data: bytes, expected: str | None) -> None:
    assert sniff_resume_type(data) == expected


@pytest.mark.parametrize(
    ("raw", "content_type", "expected"),
    [
        ("cv.pdf", PDF, "cv.pdf"),
        ("C:\\fakepath\\My CV.pdf", PDF, "My CV.pdf"),
        ("../../etc/passwd", PDF, "passwd"),
        ("/abs/path/cv.docx", DOCX, "cv.docx"),
        ("bad\x00na\x1fme\x7f.pdf", PDF, "badname.pdf"),
        ("evil\u202egnp.pdf", PDF, "evilgnp.pdf"),  # bidi override removed
        ("line\r\nbreak.pdf", PDF, "linebreak.pdf"),
        ("line sep para.pdf", PDF, "lineseppara.pdf"),  # Zl / Zp removed
        ("  spaced.pdf  ", PDF, "spaced.pdf"),
        ("", PDF, "resume.pdf"),
        (None, DOCX, "resume.docx"),
        ("..", PDF, "resume.pdf"),
        ("dir/", DOCX, "resume.docx"),
        ("Ünïcødé résumé.pdf", PDF, "Ünïcødé résumé.pdf"),
    ],
)
def test_sanitize_filename(raw: str | None, content_type: str, expected: str) -> None:
    assert sanitize_filename(raw, content_type) == expected


def test_sanitize_filename_caps_length_and_keeps_the_extension() -> None:
    name = sanitize_filename("a" * 1000 + ".pdf", PDF)
    assert len(name) == MAX_FILENAME_CHARS
    assert name == "a" * (MAX_FILENAME_CHARS - 4) + ".pdf"
    docx = sanitize_filename("b" * 300 + ".docx", DOCX)
    assert len(docx) == MAX_FILENAME_CHARS and docx.endswith(".docx")
    # No extension (or an implausibly long one): plain truncation.
    assert sanitize_filename("c" * 300, PDF) == "c" * MAX_FILENAME_CHARS
    assert sanitize_filename("x." + "y" * 300, PDF) == ("x." + "y" * 300)[:MAX_FILENAME_CHARS]


def test_storage_path_is_owner_kind_id() -> None:
    owner, file_id = uuid.uuid4(), uuid.uuid7()
    assert storage_path(owner, "resume", file_id) == f"{owner}/resume/{file_id}"
