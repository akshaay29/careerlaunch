"""Resume fixtures built in the tests: a minimal valid text PDF, an image-only PDF, a
malformed PDF and a DOCX. Nothing here imports a parser."""

import io
import zipfile
from xml.sax.saxutils import escape


def _pdf(objects: list[bytes]) -> bytes:
    """A PDF with correct xref offsets; ``objects[i]`` is object ``i + 1``."""
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _page_stream(lines: list[str]) -> bytes:
    ops = ["BT", "/F1 11 Tf", "14 TL", "50 780 Td"]
    for line in lines:
        ops.append(f"({_escape(line)}) Tj T*")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    return f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"


def make_text_pdf(*pages: list[str]) -> bytes:
    """Helvetica text, one page per argument, one line per entry (ASCII)."""
    count = len(pages)
    # Objects: 1 catalog, 2 pages, 3 font, then (page, content) per page.
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(count))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, lines in enumerate(pages):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> "
            + f"/Contents {5 + 2 * i} 0 R >>".encode()
        )
        objects.append(_page_stream(lines))
    return _pdf(objects)


def make_image_only_pdf() -> bytes:
    """A page that only paints a (tiny, grey) image: no text at all, like a scan."""
    stream = b"q 500 0 0 700 50 80 cm /Im1 Do Q"
    pixels = bytes([128]) * 4
    return _pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
            b"/Resources << /XObject << /Im1 5 0 R >> >> /Contents 4 0 R >>",
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
            b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray "
            b"/BitsPerComponent 8 /Length 4 >>\nstream\n" + pixels + b"\nendstream",
        ]
    )


# Starts like a PDF (so the upload is accepted) but is not one: the parser raises.
MALFORMED_PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def _para(text: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def make_docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    """A minimal valid DOCX, one paragraph per entry, plus an optional table (rows of
    cells). Built by hand, so the test process never imports python-docx either."""
    body = "".join(_para(text) for text in paragraphs)
    if table:
        rows = "".join(
            "<w:tr>" + "".join(f"<w:tc>{_para(cell)}</w:tc>" for cell in row) + "</w:tr>"
            for row in table
        )
        body += f"<w:tbl>{rows}</w:tbl>"
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


RESUME_LINES = [
    "Asha Verma",
    "Skills: JS, React, PostgreSQL, Docker",
    "Education",
    "B.Tech in Computer Science, Example Institute of Technology, 2022-2026",
    "Experience",
    "Software Engineering Intern, Example Labs, May 2025 - July 2025",
    "- Built a React dashboard backed by a PostgreSQL database",
    "Projects",
    "Campus job board written in JavaScript",
]
