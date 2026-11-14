"""profile.files: pure helpers for uploaded files (type sniffing, names, storage paths).

The type of an upload comes from its CONTENT only, never from the filename or the
client's content type. Resumes are PDF or DOCX; Documents (FR-26) may also be plain text.
"""

import io
import re
import unicodedata
import uuid
import zipfile
from typing import Literal

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT = "text/plain"
ResumeContentType = Literal[
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]
DocumentContentType = Literal[
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
]

# 5 MiB, the same limit as the Storage bucket (supabase/config.toml, ensure_bucket.py).
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILENAME_CHARS = 255

_ZIP_MAGIC = b"PK\x03\x04"
# Control, format (bidi overrides, zero-width), surrogate, line and paragraph separators.
_REMOVED_CATEGORIES = ("Cc", "Cf", "Cs", "Zl", "Zp")
_DEFAULT_NAMES = {PDF: "resume.pdf", DOCX: "resume.docx"}
_DOCUMENT_DEFAULT_STEM = "document"
# Extensions a Document's stored name may keep for its sniffed type; the first one is
# appended when the name has none of them.
_DOCUMENT_EXTENSIONS = {
    PDF: (".pdf",),
    DOCX: (".docx",),
    TEXT: (".txt", ".md", ".csv", ".log"),
}
_UTF8_BOM = b"\xef\xbb\xbf"
# Control characters plain text may not contain: C0 except tab, LF, FF and CR; DEL; C1.
_DISALLOWED_TEXT_CONTROLS = re.compile("[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]")

FileKind = Literal["resume", "document"]


def sniff_resume_type(data: bytes) -> ResumeContentType | None:
    """``application/pdf`` for data starting with ``%PDF-``; the DOCX type for a ZIP archive
    that contains ``word/document.xml``; otherwise None. Only the ZIP central directory is
    read (no member is decompressed)."""
    if data.startswith(b"%PDF-"):
        return PDF
    if data.startswith(_ZIP_MAGIC):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
        except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, EOFError, OSError):
            return None
        if "word/document.xml" in names:
            return DOCX
    return None


def is_plain_text(data: bytes) -> bool:
    """True for non-empty, strict UTF-8 (a leading BOM allowed) without NUL bytes, whose only
    control characters (C0, DEL and C1) are tab, line feed, carriage return and form feed.
    There are no magic bytes for text, so this is what rejects binaries named ``.txt``."""
    body = data[len(_UTF8_BOM) :] if data.startswith(_UTF8_BOM) else data
    if not body or b"\x00" in body:
        return False
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    # One regex scan (C speed): a 5 MB file must not block the event loop.
    return _DISALLOWED_TEXT_CONTROLS.search(text) is None


def sniff_document_type(data: bytes) -> DocumentContentType | None:
    """PDF and DOCX exactly as :func:`sniff_resume_type`; otherwise ``text/plain`` when
    :func:`is_plain_text`; otherwise None."""
    sniffed = sniff_resume_type(data)
    if sniffed is not None:
        return sniffed
    if is_plain_text(data):
        return TEXT
    return None


def _clean_name(raw: str | None) -> str:
    """Path stripped, control, format and line/paragraph separator characters removed,
    surrounding whitespace trimmed; "" when nothing usable is left."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch) not in _REMOVED_CATEGORIES)
    name = name.strip()
    return "" if name in (".", "..") else name


def sanitize_filename(raw: str | None, content_type: str) -> str:
    """The display / download name: path stripped, control, format and line/paragraph
    separator characters removed, surrounding whitespace trimmed, at most 255 characters
    (the extension is kept when truncating). Falls back to ``resume.pdf`` /
    ``resume.docx`` when nothing is left."""
    name = _clean_name(raw)
    if not name:
        return _DEFAULT_NAMES.get(content_type, "file")
    if len(name) > MAX_FILENAME_CHARS:
        # Keep a short extension (".pdf", ".docx") when truncating.
        stem, dot, ext = name.rpartition(".")
        suffix = f".{ext}" if dot and stem and 0 < len(ext) <= 10 else ""
        name = name[: MAX_FILENAME_CHARS - len(suffix)].rstrip() + suffix
    return name or _DEFAULT_NAMES.get(content_type, "file")


def document_filename(raw: str | None, content_type: str) -> str:
    """A Document's display / download name, cleaned as :func:`sanitize_filename`, whose
    extension always matches the sniffed type: a matching extension (``.pdf``; ``.docx``;
    ``.txt``, ``.md``, ``.csv`` or ``.log`` for text) is kept, otherwise the type's own is
    appended (``cover.html`` -> ``cover.html.txt``). At most 255 characters, the extension
    kept. Falls back to ``document.pdf`` / ``.docx`` / ``.txt``."""
    allowed = _DOCUMENT_EXTENSIONS[content_type]
    name = _clean_name(raw)
    lowered = name.lower()
    ext = next((e for e in allowed if lowered.endswith(e)), None)
    if ext is not None:
        stem, suffix = name[: -len(ext)], name[-len(ext) :]
    else:
        stem, suffix = name, allowed[0]
    stem = stem[: MAX_FILENAME_CHARS - len(suffix)].rstrip()
    return (stem or _DOCUMENT_DEFAULT_STEM) + suffix


def storage_path(owner_id: uuid.UUID, kind: FileKind, file_id: uuid.UUID) -> str:
    """``<owner_id>/<kind>/<file_id>`` in the private bucket. Server-chosen only."""
    return f"{owner_id}/{kind}/{file_id}"
