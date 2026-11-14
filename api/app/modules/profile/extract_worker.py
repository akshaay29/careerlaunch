"""Sandboxed Resume parser: the child-process entry point (Files convention, FR-4).

Run ONLY by ``profile.extraction`` as ``python -I <this file> <kind> <max_pages> <max_chars>
<max_memory_bytes> <max_cpu_seconds>``, with a minimal environment (no API secrets), with the file's bytes on stdin. It prints ``{"text": ..., "pages": n}`` as
JSON on stdout and exits 0, or exits non-zero on any failure (the parent treats every
non-zero exit, timeout or bad output as ``failed``).

* Self-contained on purpose: it imports only the standard library and the parser
  (pdfplumber or python-docx), never ``app``, so the API process never imports a parser and the
  child never loads settings or secrets. ``-I`` (isolated mode) also ignores ``PYTHON*``
  environment variables and the user site directory.
* The address-space (``RLIMIT_AS``) and CPU-time (``RLIMIT_CPU``) limits are applied after the
  parser is imported and before any untrusted byte is read. They are POSIX-only, skipped where
  ``resource`` is missing, and a platform that rejects one (macOS and ``RLIMIT_AS``) keeps
  the others; the parent's wall-time limit always applies.
* At most ``max_pages`` pages are parsed and at most ``max_chars`` characters returned.
* Nothing from the document is ever written to stderr.
"""

import io
import json
import sys
from typing import Any

PDF = "pdf"
DOCX = "docx"


def _set_limits(max_bytes: int, max_cpu_seconds: int) -> None:
    try:
        import resource  # POSIX only
    except ImportError:
        return
    for name, value in (("RLIMIT_AS", max_bytes), ("RLIMIT_CPU", max_cpu_seconds)):
        limit = getattr(resource, name, None)
        if limit is None:
            continue
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass


def _pdf_text(parser: Any, data: bytes, max_pages: int, max_chars: int) -> tuple[str, int]:
    parts: list[str] = []
    total = 0
    pages = 0
    with parser.open(io.BytesIO(data), pages=list(range(1, max_pages + 1))) as pdf:
        for page in pdf.pages[:max_pages]:
            pages += 1
            text = page.extract_text() or ""
            parts.append(text)
            total += len(text) + 1
            page.close()
            if total >= max_chars:
                break
    return "\n".join(parts)[:max_chars], pages


def _docx_text(parser: Any, data: bytes, max_chars: int) -> tuple[str, int]:
    document = parser.Document(io.BytesIO(data))
    parts: list[str] = []
    total = 0
    for paragraph in document.paragraphs:
        parts.append(paragraph.text)
        total += len(paragraph.text) + 1
        if total >= max_chars:
            return "\n".join(parts)[:max_chars], 1
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
                total += len(cell.text) + 1
                if total >= max_chars:
                    return "\n".join(parts)[:max_chars], 1
    return "\n".join(parts)[:max_chars], 1


def main(argv: list[str]) -> int:
    kind, max_pages, max_chars, max_memory = argv[0], int(argv[1]), int(argv[2]), int(argv[3])
    max_cpu = int(argv[4]) if len(argv) > 4 else 30
    if kind not in (PDF, DOCX):
        return 2
    # Limits first: everything the parser library does, including its import and native
    # code it maps, then runs inside the sandbox.
    _set_limits(max_memory, max_cpu)
    if kind == PDF:
        import pdfplumber as parser
    else:
        import docx as parser
    data = sys.stdin.buffer.read()
    if kind == PDF:
        text, pages = _pdf_text(parser, data, max_pages, max_chars)
    else:
        text, pages = _docx_text(parser, data, max_chars)
    sys.stdout.write(json.dumps({"text": text, "pages": pages}))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except BaseException:  # noqa: BLE001 -- MemoryError, parser errors: exit code only
        code = 1
    sys.exit(code)
