"""FR-4 / Files convention: the sandboxed parser child and the pure interpretation."""

import json
import subprocess
import sys
import time

import pytest

from app.modules.profile import extraction
from app.modules.profile.extraction import ParserFailed
from app.modules.profile.files import DOCX, PDF
from tests.resume_fixtures import (
    MALFORMED_PDF,
    RESUME_LINES,
    make_docx,
    make_image_only_pdf,
    make_text_pdf,
)

# --------------------------------------------------------------------------- child process


@pytest.mark.parametrize(
    ("data", "content_type"),
    [(make_text_pdf(RESUME_LINES), PDF), (make_docx(RESUME_LINES), DOCX)],
    ids=["pdf", "docx"],
)
async def test_text_resume_is_ready(data: bytes, content_type: str) -> None:
    result = await extraction.extract(data, content_type)
    assert result.status == "ready"
    assert {"javascript", "react", "postgresql", "docker"} <= set(result.skills)
    assert result.education == [
        "B.Tech in Computer Science, Example Institute of Technology, 2022-2026"
    ]
    assert result.experience == [
        "Software Engineering Intern, Example Labs, May 2025 - July 2025\n"
        "- Built a React dashboard backed by a PostgreSQL database",
        "Campus job board written in JavaScript",
    ]


async def test_image_only_pdf_is_unreadable() -> None:
    result = await extraction.extract(make_image_only_pdf(), PDF)
    assert result.status == "unreadable"
    assert result.skills == result.education == result.experience == []


async def test_malformed_pdf_fails_without_raising() -> None:
    result = await extraction.extract(MALFORMED_PDF, PDF)
    assert result.status == "failed" and result.reason == "exit"


async def test_garbage_docx_fails() -> None:
    result = await extraction.extract(b"PK\x03\x04 not really a zip", DOCX)
    assert result.status == "failed"


async def test_unsupported_type_fails() -> None:
    with pytest.raises(ParserFailed):
        await extraction.run_parser(b"x", "text/plain")


async def test_timeout_kills_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        extraction,
        "worker_command",
        lambda kind: [sys.executable, "-I", "-c", "import time; time.sleep(30)"],
    )
    started = time.monotonic()
    result = await extraction.extract(make_text_pdf(RESUME_LINES), PDF, timeout=1.0)
    assert result.status == "failed" and result.reason == "timeout"
    assert time.monotonic() - started < 10


async def test_bad_output_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        extraction,
        "worker_command",
        lambda kind: [sys.executable, "-I", "-c", "print('not json')"],
    )
    result = await extraction.extract(make_text_pdf(RESUME_LINES), PDF)
    assert result.status == "failed" and result.reason == "bad-output"


@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_AS is POSIX only")
async def test_memory_limit_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Far below what parsing needs: the child dies with MemoryError and exits non-zero.
    monkeypatch.setattr(extraction, "MAX_MEMORY_BYTES", 16 * 1024 * 1024)
    result = await extraction.extract(make_text_pdf(RESUME_LINES), PDF)
    assert result.status == "failed"


async def test_page_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    data = make_text_pdf(RESUME_LINES, ["PAGETWOMARKER Kubernetes"])
    assert "PAGETWOMARKER" in await extraction.run_parser(data, PDF)
    monkeypatch.setattr(extraction, "MAX_PAGES", 1)
    text = await extraction.run_parser(data, PDF)
    assert "PAGETWOMARKER" not in text and "Asha Verma" in text


def test_child_caps_its_own_output() -> None:
    """The worker itself returns at most ``max_chars`` (not only the parent's cut)."""
    argv = extraction.worker_command("pdf")
    argv[5] = "60"  # max_chars
    completed = subprocess.run(
        argv,
        input=make_text_pdf(RESUME_LINES),
        capture_output=True,
        timeout=30,
        check=True,
        env=extraction.child_env(),
    )
    payload = json.loads(completed.stdout)
    assert len(payload["text"]) == 60 and payload["pages"] == 1


async def test_docx_table_cells_are_read() -> None:
    data = make_docx(["Asha Verma " * 10], table=[["Tools", "Kubernetes"]])
    result = await extraction.extract(data, DOCX)
    assert "kubernetes" in result.skills


async def test_child_does_not_inherit_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret-service-role-value")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secret-db-password@h/db")
    dump = "import json, os, sys; sys.stdout.write(json.dumps({'text': json.dumps(dict(os.environ))}))"
    monkeypatch.setattr(
        extraction, "worker_command", lambda kind: [sys.executable, "-I", "-c", dump]
    )
    env = json.loads(await extraction.run_parser(b"%PDF-", PDF))
    assert "SUPABASE_SERVICE_ROLE_KEY" not in env and "DATABASE_URL" not in env
    assert set(env) - {"PATH", "SYSTEMROOT"} <= _PLATFORM_ADDED_VARS
    assert "secret" not in json.dumps(env)


# Variables a platform's process start-up may add on its own (not inherited from the API).
_PLATFORM_ADDED_VARS = {"LC_CTYPE", "__CF_USER_TEXT_ENCODING", "COMSPEC", "PATHEXT"}


def test_worker_command_is_isolated_and_shell_free() -> None:
    argv = extraction.worker_command("pdf")
    assert argv[0] == sys.executable and argv[1] == "-I"
    assert argv[2].endswith("extract_worker.py")
    assert argv[3:] == ["pdf", "50", "200000", str(256 * 1024 * 1024), "20"]


def test_parsers_are_never_imported_in_this_process() -> None:
    assert "pdfplumber" not in sys.modules
    assert "docx" not in sys.modules


# --------------------------------------------------------------------------- interpretation


def test_interpret_unreadable_below_50_non_whitespace_chars() -> None:
    assert extraction.interpret("a " * 49).status == "unreadable"
    assert extraction.interpret("a" * 49 + "\n\n   ").status == "unreadable"
    assert extraction.interpret("a" * 50).status == "ready"


def test_sections_by_headings() -> None:
    text = "\n".join(
        [
            "Summary",
            "Driven student",
            "EDUCATION:",
            "B.Sc Physics, 2024",
            "",
            "M.Sc Physics, 2026",
            "Work Experience",
            "Intern, Foo",
            "• Did things",
            "Intern, Bar",
            "Skills",
            "Python",
            "Internships",
            "Research intern, Lab",
        ]
    )
    sections = extraction.split_sections(text)
    assert sections["education"] == ["B.Sc Physics, 2024", "M.Sc Physics, 2026"]
    assert sections["experience"] == [
        "Intern, Foo\n• Did things",
        "Intern, Bar",
        "Research intern, Lab",
    ]


@pytest.mark.parametrize(
    ("heading", "section"),
    [
        ("Professional Experience", "experience"),
        ("WORK HISTORY:", "experience"),
        ("Employment History", "experience"),
        ("Internship", "experience"),
        ("Academic Projects", "experience"),
        ("Personal projects", "experience"),
        ("Academic Background", "education"),
        ("Education & Qualifications", "education"),
        ("Education and Qualifications", "education"),
    ],
)
def test_heading_variants(heading: str, section: str) -> None:
    sections = extraction.split_sections(f"{heading}\nThe entry")
    assert sections[section] == ["The entry"]


def test_sections_are_capped() -> None:
    lines = ["Experience"]
    for i in range(30):
        lines += [f"Role {i} " + "x" * 400, ""]
    sections = extraction.split_sections("\n".join(lines))
    assert len(sections["experience"]) == 20
    assert all(len(e) <= 300 for e in sections["experience"])
    assert sections["education"] == []
