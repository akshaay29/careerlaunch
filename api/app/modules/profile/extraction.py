"""profile.extraction: parse a Resume in a sandboxed child, then interpret its text (FR-4).

Parse in the child, interpret in the parent:

* :func:`run_parser` runs ``extract_worker.py`` in a separate Python process (isolated mode,
  no shell) with the bytes on stdin, a wall-time limit (the child is killed on timeout), an
  address-space limit applied by the child, a page cap and a character cap. Any crash,
  timeout, limit or non-zero exit raises :class:`ParserFailed`. The API process never imports
  pdfplumber or python-docx.
* :func:`interpret` is pure: canonical skills via ``core.skills.normalize`` and Education /
  Experience entries split by section headings.

No LLM and no network call happens here. The text itself is never stored or logged.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.core import skills
from app.modules.profile.files import DOCX, PDF

WORKER_PATH = Path(__file__).with_name("extract_worker.py")

# Must fit the after-response priority drain's work budget (router
# PRIORITY_DRAIN_TIMEOUT_SECONDS 30 - core.jobs WORKER_SHUTDOWN_GRACE_SECONDS 5 = 25 s), so the
# parser's own timeout fires (-> ``failed``) before the drain cancels the job.
TIMEOUT_SECONDS = 20.0
MAX_MEMORY_BYTES = 256 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_PAGES = 50
# Fewer non-whitespace characters than this means no usable text (e.g. a scanned PDF).
MIN_READABLE_CHARS = 50
MAX_SECTION_ENTRIES = 20
MAX_ENTRY_CHARS = 300

_KINDS = {PDF: "pdf", DOCX: "docx"}

Section = Literal["education", "experience"]
# Headings that start a captured section (compared case-insensitively, trailing ":" ignored).
SECTION_HEADINGS: dict[str, Section] = {
    "education": "education",
    "experience": "experience",
    "work experience": "experience",
    "internships": "experience",
    "projects": "experience",
    "professional experience": "experience",
    "work history": "experience",
    "employment": "experience",
    "employment history": "experience",
    "internship": "experience",
    "academic projects": "experience",
    "personal projects": "experience",
    "academic background": "education",
    "education & qualifications": "education",
    "education and qualifications": "education",
}
# Other common headings: they end the current section without starting a captured one.
OTHER_HEADINGS = frozenset(
    {
        "skills",
        "technical skills",
        "key skills",
        "core skills",
        "skills and interests",
        "summary",
        "profile",
        "professional summary",
        "objective",
        "career objective",
        "certifications",
        "certificates",
        "courses",
        "coursework",
        "relevant coursework",
        "achievements",
        "awards",
        "honors",
        "honours",
        "awards and achievements",
        "publications",
        "interests",
        "hobbies",
        "languages",
        "contact",
        "references",
        "activities",
        "extracurricular activities",
        "extra-curricular activities",
        "positions of responsibility",
        "volunteering",
        "volunteer experience",
        "leadership",
    }
)
_BULLETS = ("•", "·", "▪", "◦", "●", "-", "*", "–", "—", "»", "")
_WS = re.compile(r"\s+")


class ParserFailed(Exception):
    """The child crashed, timed out, hit a limit or returned bad output. The message is a
    short reason code only (never document content)."""


@dataclass(frozen=True)
class ExtractionResult:
    status: Literal["ready", "unreadable", "failed"]
    skills: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    experience: list[str] = field(default_factory=list)
    # Why parsing failed (a short code such as ``timeout``; never document content).
    reason: str | None = None


def child_env() -> dict[str, str]:
    """The child's whole environment: only what Python needs to start. The API's secrets
    (service-role key, database URL, ...) are never inherited."""
    names = ("PATH", "SYSTEMROOT") if sys.platform == "win32" else ("PATH",)
    return {name: os.environ[name] for name in names if name in os.environ}


def worker_command(kind: str) -> list[str]:
    """argv of the child. A list (never a shell string); tests may replace this function."""
    return [
        sys.executable,
        "-I",
        str(WORKER_PATH),
        kind,
        str(MAX_PAGES),
        str(MAX_TEXT_CHARS),
        str(MAX_MEMORY_BYTES),
        str(int(TIMEOUT_SECONDS)),
    ]


def _decode(stdout: bytes) -> str:
    try:
        payload = json.loads(stdout)
    except ValueError as exc:
        raise ParserFailed("bad-output") from exc
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        raise ParserFailed("bad-output")
    return text[:MAX_TEXT_CHARS]


async def _run_async(argv: Sequence[str], data: bytes, timeout: float) -> tuple[int, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=child_env(),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(data), timeout)
    except BaseException:  # timeout or cancellation: never leave the child running
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    return proc.returncode if proc.returncode is not None else -1, stdout


def _run_blocking(argv: Sequence[str], data: bytes, timeout: float) -> tuple[int, bytes]:
    # subprocess.run kills the child when the timeout expires.
    completed = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        list(argv),
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
        env=child_env(),
    )
    return completed.returncode, completed.stdout


async def run_parser(data: bytes, content_type: str, *, timeout: float | None = None) -> str:
    """The Resume's text (at most ``MAX_TEXT_CHARS``), parsed in the sandboxed child.
    Raises :class:`ParserFailed` on any child failure."""
    kind = _KINDS.get(content_type)
    if kind is None:
        raise ParserFailed("unsupported-type")
    argv = worker_command(kind)
    limit = TIMEOUT_SECONDS if timeout is None else timeout
    try:
        try:
            code, stdout = await _run_async(argv, data, limit)
        except NotImplementedError:
            # An event loop without subprocess support (Windows' selector loop, which psycopg
            # needs): same child, same limits, run from a worker thread.
            code, stdout = await asyncio.to_thread(_run_blocking, argv, data, limit)
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        raise ParserFailed("timeout") from exc
    except OSError as exc:
        raise ParserFailed("spawn") from exc
    if code != 0:
        raise ParserFailed("exit")
    return _decode(stdout)


# --------------------------------------------------------------------------- interpretation


def _heading(line: str) -> str:
    return _WS.sub(" ", line).strip().rstrip(":").strip().casefold()


def _is_bullet(line: str) -> bool:
    return line.startswith(_BULLETS)


def _entries(lines: list[str]) -> list[str]:
    """Group a section's lines into entries: a blank line ends an entry, bullet lines join
    the current entry, and a plain line after a bullet starts a new one."""
    entries: list[list[str]] = []
    current: list[str] = []
    last_was_bullet = False
    for raw in lines:
        line = _WS.sub(" ", raw).strip()
        if not line:
            if current:
                entries.append(current)
            current, last_was_bullet = [], False
            continue
        bullet = _is_bullet(line)
        if current and not bullet and last_was_bullet:
            entries.append(current)
            current = []
        current.append(line)
        last_was_bullet = bullet
    if current:
        entries.append(current)
    out: list[str] = []
    for entry in entries[:MAX_SECTION_ENTRIES]:
        text = "\n".join(entry)
        out.append(text[:MAX_ENTRY_CHARS].rstrip())
    return out


def split_sections(text: str) -> dict[Section, list[str]]:
    """Education and Experience entries (at most 20 per section, 300 characters each)."""
    collected: dict[Section, list[str]] = {"education": [], "experience": []}
    current: Section | None = None
    for line in text.splitlines():
        key = _heading(line)
        if key in SECTION_HEADINGS:
            current = SECTION_HEADINGS[key]
            # Separate entries of two headings mapped to the same section.
            collected[current].append("")
            continue
        if key in OTHER_HEADINGS:
            current = None
            continue
        if current is not None:
            collected[current].append(line)
    return {section: _entries(lines) for section, lines in collected.items()}


def interpret(text: str) -> ExtractionResult:
    """Pure: ``unreadable`` below 50 non-whitespace characters, else ``ready`` with skills
    and sections."""
    text = text[:MAX_TEXT_CHARS]
    if len(_WS.sub("", text)) < MIN_READABLE_CHARS:
        return ExtractionResult(status="unreadable")
    sections = split_sections(text)
    return ExtractionResult(
        status="ready",
        skills=skills.sorted_ids(skills.normalize(text)),
        education=sections["education"],
        experience=sections["experience"],
    )


async def extract(data: bytes, content_type: str, *, timeout: float | None = None) -> ExtractionResult:
    """Parse and interpret. Never raises for a bad file: a parser failure is ``failed``."""
    try:
        text = await run_parser(data, content_type, timeout=timeout)
    except ParserFailed as exc:
        return ExtractionResult(status="failed", reason=str(exc))
    return interpret(text)
