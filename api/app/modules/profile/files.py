"""profile.files: pure helpers for uploaded files (type sniffing, names, storage paths).

The type of an upload comes from its CONTENT only, never from the filename or the
client's content type.
"""

import io
import unicodedata
import uuid
import zipfile
from typing import Literal

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ResumeContentType = Literal[
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]

# 5 MiB, the same limit as the Storage bucket (supabase/config.toml, ensure_bucket.py).
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILENAME_CHARS = 255

_ZIP_MAGIC = b"PK\x03\x04"
# Control, format (bidi overrides, zero-width), surrogate, line and paragraph separators.
_REMOVED_CATEGORIES = ("Cc", "Cf", "Cs", "Zl", "Zp")
_DEFAULT_NAMES = {PDF: "resume.pdf", DOCX: "resume.docx"}

FileKind = Literal["resume"]


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


def sanitize_filename(raw: str | None, content_type: str) -> str:
    """The display / download name: path stripped, control, format and line/paragraph
    separator characters removed, surrounding whitespace trimmed, at most 255 characters
    (the extension is kept when truncating). Falls back to ``resume.pdf`` /
    ``resume.docx`` when nothing is left."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch) not in _REMOVED_CATEGORIES)
    name = name.strip()
    if name in ("", ".", ".."):
        return _DEFAULT_NAMES.get(content_type, "file")
    if len(name) > MAX_FILENAME_CHARS:
        # Keep a short extension (".pdf", ".docx") when truncating.
        stem, dot, ext = name.rpartition(".")
        suffix = f".{ext}" if dot and stem and 0 < len(ext) <= 10 else ""
        name = name[: MAX_FILENAME_CHARS - len(suffix)].rstrip() + suffix
    return name or _DEFAULT_NAMES.get(content_type, "file")


def storage_path(owner_id: uuid.UUID, kind: FileKind, file_id: uuid.UUID) -> str:
    """``<owner_id>/<kind>/<file_id>`` in the private bucket. Server-chosen only."""
    return f"{owner_id}/{kind}/{file_id}"
