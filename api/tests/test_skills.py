"""AD-8: core.skills, the one vocabulary and normaliser."""

import re

import pytest

from app.core import skills


def test_vocabulary_is_large_versioned_and_kebab() -> None:
    every = skills.all_skills()
    assert len(every) >= 150
    assert isinstance(skills.VOCAB_VERSION, str) and skills.VOCAB_VERSION
    ids = [s.id for s in every]
    assert len(ids) == len(set(ids))
    for skill_id in ids:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", skill_id), skill_id


@pytest.mark.parametrize("text", ["JS", "javascript", "ES6", "JavaScript", "Java Script", "js."])
def test_javascript_aliases(text: str) -> None:
    assert skills.normalize(text) == {"javascript"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("C++", {"cpp"}),
        ("c++17", {"cpp"}),
        ("C#", {"csharp"}),
        ("C", {"c"}),
        ("C, C++ and C#", {"c", "cpp", "csharp"}),
        (".NET", {"dotnet"}),
        ("built on .net core", {"dotnet"}),
        ("ASP.NET", {"asp-net"}),
        ("Node.js", {"nodejs"}),
        ("NodeJS", {"nodejs"}),
        ("CI/CD", {"ci-cd"}),
        ("ci/cd pipelines", {"ci-cd"}),
        ("React Native", {"react-native"}),
        ("React.js and Next.js", {"react", "nextjs"}),
        ("Machine   Learning", {"machine-learning"}),
        ("PL/SQL", {"oracle-database"}),
    ],
)
def test_symbols_and_multiword_aliases(text: str, expected: set[str]) -> None:
    assert skills.normalize(text) == expected


@pytest.mark.parametrize(
    ("text", "absent"),
    [
        ("JavaScript", "java"),
        ("Rarely ready for Rust", "r"),
        ("very good results", "go"),
        ("CI/CD", "c"),
        ("C++", "c"),
        ("C#", "c"),
        ("NoSQL", "sql"),
        ("MySQL", "sql"),
        ("TypeScript", "typescript-free"),  # sanity: unknown ids never appear
        ("Spring 2025 semester", "spring-framework"),
        ("I excel at teamwork", "microsoft-excel"),
        ("the rest of the team", "rest-api"),
    ],
)
def test_no_false_positives_inside_words(text: str, absent: str) -> None:
    assert absent not in skills.normalize(text)


@pytest.mark.parametrize("text", ["go live", "R&D", "Grade C", "C-suite"])
def test_ambiguous_short_names_in_prose_are_not_skills(text: str) -> None:
    assert skills.normalize(text) == set()


def test_ambiguous_short_names_still_match_as_skills() -> None:
    assert skills.normalize("Go, R and C") == {"go", "r", "c"}
    assert skills.normalize("GOLANG and golang") == {"go"}
    assert skills.normalize("Objective-C") == {"objective-c"}
    assert skills.normalize("C/C++") == {"c", "cpp"}


def test_mixed_text() -> None:
    text = "Skills: JS, React, PostgreSQL; Golang, R and Go. Tools: Git, Docker, k8s."
    assert skills.normalize(text) == {
        "javascript",
        "react",
        "postgresql",
        "go",
        "r",
        "git",
        "docker",
        "kubernetes",
    }


def test_normalize_empty() -> None:
    assert skills.normalize("") == set()
    assert skills.normalize("Basket weaving and origami") == set()


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("JS", "javascript"),
        ("  javascript ", "javascript"),
        ("JavaScript", "javascript"),
        ("javascript", "javascript"),
        ("node.JS", "nodejs"),
        ("C++", "cpp"),
        ("react   native", "react-native"),
        ("spring-boot", "spring-boot"),
        ("Basket weaving", None),
        ("", None),
    ],
)
def test_lookup(term: str, expected: str | None) -> None:
    assert skills.lookup(term) == expected


def test_label() -> None:
    assert skills.label("javascript") == "JavaScript"
    assert skills.label("cpp") == "C++"
    assert skills.label("not-a-skill") == "not-a-skill"


def test_suggest_prefix_first_and_capped() -> None:
    found = [s.id for s in skills.suggest("py")]
    assert "python" in found and "pytorch" in found
    by_sql = [s.id for s in skills.suggest("sql")]
    assert by_sql.index("sql") < by_sql.index("mysql")
    assert by_sql.index("sql") < by_sql.index("postgresql")
    assert len(skills.suggest("a", limit=20)) == 20
    assert skills.suggest("") == []
    assert skills.suggest("zzzz-nothing") == []
    # Alias matches count: "JS" suggests JavaScript.
    assert "javascript" in [s.id for s in skills.suggest("js")]


def test_sorted_ids_dedupes_by_label() -> None:
    assert skills.sorted_ids(["react", "cpp", "react", "aws"]) == ["aws", "cpp", "react"]
