"""Files convention: Resume and Document type from content (magic bytes, plain-text rules),
filename sanitising, paths."""

import io
import uuid
import zipfile

import pytest

from app.modules.profile.files import (
    DOCX,
    MAX_FILE_BYTES,
    MAX_FILENAME_CHARS,
    PDF,
    TEXT,
    document_filename,
    sanitize_filename,
    sniff_document_type,
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
    assert storage_path(owner, "document", file_id) == f"{owner}/document/{file_id}"


# --------------------------------------------------------------------------- Documents (FR-26)

BOM = b"\xef\xbb\xbf"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"%PDF-1.7\n1 0 obj\n", PDF),
        (make_docx(), DOCX),
        (b"Dear hiring manager,\r\n\tI am writing...\n\x0cPage 2\n", TEXT),
        ("Résumé — naïve 日本語 ✓".encode(), TEXT),
        (BOM + "with a BOM".encode(), TEXT),
        (b"x", TEXT),
        (b"", None),  # empty
        (BOM, None),  # only a BOM: empty text
        (b"text with a \x00 NUL", None),
        (b"\xff\xfeh\x00i\x00", None),  # UTF-16
        (b"caf\xe9", None),  # Latin-1, not UTF-8
        (b"\xed\xa0\x80", None),  # an encoded surrogate: not strict UTF-8
        (b"\xc0\xaf", None),  # overlong encoding
        (bytes(range(1, 32)) * 4, None),  # mostly control characters
        (b"bell\x07", None),
        (b"escape \x1b[31m red", None),
        (b"delete\x7f", None),
        ("c1 \u0085 next line".encode(), None),
        (b"\x0b vertical tab", None),
        (PNG, None),
        (make_docx(document=False), None),  # ZIP without word/document.xml
        (b"PK\x03\x04garbage-not-a-zip", None),
    ],
    ids=[
        "pdf",
        "docx",
        "text-with-whitespace-controls",
        "utf8-non-ascii",
        "utf8-bom",
        "one-char",
        "empty",
        "bom-only",
        "nul",
        "utf16",
        "latin1",
        "surrogate",
        "overlong",
        "mostly-controls",
        "bell",
        "escape",
        "del",
        "c1-nel",
        "vertical-tab",
        "png",
        "zip-without-document",
        "broken-zip",
    ],
)
def test_sniff_document_type(data: bytes, expected: str | None) -> None:
    assert sniff_document_type(data) == expected


def test_resume_sniffing_still_rejects_plain_text() -> None:
    assert sniff_resume_type(b"plain text") is None


@pytest.mark.parametrize(
    ("raw", "content_type", "expected"),
    [
        ("cover.pdf", PDF, "cover.pdf"),
        ("Cover.PDF", PDF, "Cover.PDF"),
        ("transcript.docx", DOCX, "transcript.docx"),
        ("notes.txt", TEXT, "notes.txt"),
        ("readme.md", TEXT, "readme.md"),
        ("grades.csv", TEXT, "grades.csv"),
        ("build.log", TEXT, "build.log"),
        ("cover.html", TEXT, "cover.html.txt"),  # never served as HTML
        ("noext", TEXT, "noext.txt"),
        ("scan.txt", PDF, "scan.txt.pdf"),  # the sniffed type wins
        ("letter.pdf", DOCX, "letter.pdf.docx"),
        ("C:\fakepath\My Letter.pdf", PDF, "My Letter.pdf"),
        ("bad\x00na\x1fme.txt", TEXT, "badname.txt"),
        ("", PDF, "document.pdf"),
        (None, DOCX, "document.docx"),
        ("..", TEXT, "document.txt"),
        ("dir/", TEXT, "document.txt"),
        (".txt", TEXT, "document.txt"),
    ],
)
def test_document_filename_extension_matches_type(
    raw: str | None, content_type: str, expected: str
) -> None:
    assert document_filename(raw, content_type) == expected


def test_document_filename_caps_length_and_keeps_the_extension() -> None:
    kept = document_filename("a" * 1000 + ".csv", TEXT)
    assert len(kept) == MAX_FILENAME_CHARS and kept.endswith(".csv")
    appended = document_filename("b" * 1000 + ".html", TEXT)
    assert len(appended) == MAX_FILENAME_CHARS and appended.endswith(".txt")
    assert document_filename("c" * 300, PDF) == "c" * (MAX_FILENAME_CHARS - 4) + ".pdf"


def test_plain_text_check_is_fast_on_5_mib() -> None:
    import time

    data = ("Lorem ipsum dolor sit amet — ✓\n" * 200_000).encode()[: MAX_FILE_BYTES - 8]
    data = data.decode("utf-8", errors="ignore").encode()
    started = time.perf_counter()
    assert sniff_document_type(data) == TEXT
    assert time.perf_counter() - started < 0.5
