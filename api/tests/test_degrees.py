"""FR-5 / AD-21: core.degrees, the one canonical degree list and normaliser."""

import importlib
import re

import psycopg
import pytest

from app.core import degrees

EXPECTED_IDS = [
    "btech", "mtech", "bsc", "msc", "bca", "mca", "bba", "mba", "pgdm",
    "bcom", "mcom", "ba", "ma", "barch", "bdes", "bpharm", "mpharm",
    "mbbs", "bds", "llb", "llm", "integrated", "phd", "diploma", "other",
]  # fmt: skip


def test_canonical_ids_in_display_order() -> None:
    assert [d.id for d in degrees.all_degrees()] == EXPECTED_IDS
    assert list(degrees.DEGREE_IDS) == EXPECTED_IDS
    for degree_id in EXPECTED_IDS:
        assert re.fullmatch(r"[a-z]+", degree_id)
        assert degrees.is_degree_id(degree_id)
    assert not degrees.is_degree_id("xyz")
    assert not degrees.is_degree_id("BTECH")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("B.E.", "btech"),
        ("BE", "btech"),
        ("b.e", "btech"),
        ("B.Tech", "btech"),
        ("B. Tech", "btech"),
        ("btech", "btech"),
        ("Bachelor of Technology", "btech"),
        ("Bachelor of Engineering", "btech"),
        ("B.Tech in Computer Science", "btech"),
        ("B.E. (ECE)", "btech"),
        ("M.Tech", "mtech"),
        ("M.E.", "mtech"),
        ("Master of Engineering", "mtech"),
        ("B.Sc", "bsc"),
        ("Bachelor of Science in Physics", "bsc"),
        ("M.Sc - Chemistry", "msc"),
        ("MS", "msc"),
        ("BCA", "bca"),
        ("Master of Computer Applications", "mca"),
        ("BBA", "bba"),
        ("MBA, Finance", "mba"),
        ("PGDM", "pgdm"),
        ("PGDBA", "pgdm"),
        ("PG Diploma", "pgdm"),
        ("B.Arch", "barch"),
        ("Bachelor of Design", "bdes"),
        ("B.Pharm", "bpharm"),
        ("M.Pharma", "mpharm"),
        ("MBBS", "mbbs"),
        ("BDS", "bds"),
        ("LL.B", "llb"),
        ("BA LLB", "llb"),
        ("LLM", "llm"),
        ("Integrated M.Tech", "integrated"),
        ("BS-MS", "integrated"),
        ("Dual Degree", "integrated"),
        ("B.Tech-CSE", "btech"),
        ("M.Sc-Physics", "msc"),
        ("MBA–Finance", "mba"),
        ("B.Com", "bcom"),
        ("M.Com", "mcom"),
        ("B.A.", "ba"),
        ("Master of Arts", "ma"),
        ("Ph.D", "phd"),
        ("PhD", "phd"),
        ("Doctor of Philosophy", "phd"),
        ("Diploma", "diploma"),
        ("Polytechnic Diploma", "diploma"),
        ("Other", "other"),
    ],
)
def test_normalize(text: str, expected: str) -> None:
    assert degrees.normalize(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "xyz", "Class 12", "in", "-CSE", "Foo-Bar"])
def test_normalize_unknown_is_none(text: str) -> None:
    assert degrees.normalize(text) is None


def test_label() -> None:
    assert degrees.label("btech") == "B.Tech / B.E."
    assert degrees.label("mtech") == "M.Tech / M.E."
    assert degrees.label("other") == "Other"
    assert degrees.label("not-a-degree") == "not-a-degree"
    # Every label normalises back to its id.
    for degree in degrees.all_degrees():
        assert degrees.normalize(degree.label) == degree.id


def test_migration_check_lists_the_canonical_ids(sync_conn: psycopg.Connection) -> None:
    """The DB backstop accepts exactly the canonical ids (a new id needs a new migration)."""
    migration = importlib.import_module("migrations.versions.0008_preferences")
    assert list(migration.DEGREE_IDS) == EXPECTED_IDS
    (definition,) = sync_conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'preferences_degree_check'"
    ).fetchone()  # type: ignore[misc]
    assert sorted(re.findall(r"'([a-z]+)'::text", definition)) == sorted(EXPECTED_IDS)
