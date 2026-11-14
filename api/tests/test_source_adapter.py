"""AD-3: the SourceAdapter port, ListingDraft validation and the in-memory fake (Story 2.1)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.ports.source_adapter import (
    MAX_COMPANY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_LOCATION_CHARS,
    MAX_SOURCE_JOB_ID_CHARS,
    MAX_TITLE_CHARS,
    MAX_URL_CHARS,
    ListingDraft,
    SourceAdapter,
    SourceQuery,
)
from tests.fakes.source_adapter import FakeSourceAdapter
from tests.listing_fixtures import draft


def test_valid_draft_is_stripped_and_frozen() -> None:
    d = draft(title="  Backend Intern  ", company_raw=" Acme ", location_raw="\tRemote\n")
    assert (d.title, d.company_raw, d.location_raw) == ("Backend Intern", "Acme", "Remote")
    with pytest.raises(ValidationError):
        d.title = "changed"  # type: ignore[misc]


def test_repr_hides_the_description() -> None:
    d = draft(description="secret description words")
    assert "secret description words" not in repr(d)
    assert "secret description words" not in str(d)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/job",
        "//example.com/job",
        "/jobs/view/1",
        "https://",
        "https://exa mple.com/job",
        "https://example.com/\njob",
        "",
    ],
)
def test_non_http_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        draft(url=url)


def test_http_and_https_urls_are_accepted() -> None:
    assert draft(url="http://example.com/a").url == "http://example.com/a"
    assert draft(url=" HTTPS://Example.com/a?b=1 ").url == "HTTPS://Example.com/a?b=1"


def test_url_length_cap() -> None:
    base = "https://example.com/"
    draft(url=base + "a" * (MAX_URL_CHARS - len(base)))
    with pytest.raises(ValidationError):
        draft(url=base + "a" * (MAX_URL_CHARS - len(base) + 1))


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("title", MAX_TITLE_CHARS),
        ("company_raw", MAX_COMPANY_CHARS),
        ("location_raw", MAX_LOCATION_CHARS),
        ("source_job_id", MAX_SOURCE_JOB_ID_CHARS),
        ("description", MAX_DESCRIPTION_CHARS),
    ],
)
def test_text_length_caps(field: str, limit: int) -> None:
    assert len(getattr(draft(**{field: "x" * limit}), field)) == limit
    # Surrounding whitespace is stripped before the cap applies.
    assert len(getattr(draft(**{field: "  " + "x" * limit + "  "}), field)) == limit
    with pytest.raises(ValidationError):
        draft(**{field: "x" * (limit + 1)})


@pytest.mark.parametrize("field", ["title", "source_job_id"])
def test_required_text_must_not_be_blank(field: str) -> None:
    with pytest.raises(ValidationError):
        draft(**{field: "   "})


@pytest.mark.parametrize("source", ["", "LinkedIn", "linked in", "1abc", "a" * 33])
def test_source_must_be_a_source_id(source: str) -> None:
    with pytest.raises(ValidationError):
        draft(source=source)


def test_job_type_values() -> None:
    assert draft(job_type=None).job_type is None
    assert draft(job_type="full_time").job_type == "full_time"
    with pytest.raises(ValidationError):
        draft(job_type="contract")


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        draft(raw_html="<b>x</b>")


def test_source_query_defaults_and_limit() -> None:
    q = SourceQuery()
    assert (q.role_titles, q.locations, q.job_types, q.limit) == ((), (), (), 100)
    with pytest.raises(ValidationError):
        SourceQuery(limit=0)
    with pytest.raises(ValidationError):
        SourceQuery(job_types=("contract",))


async def test_fake_yields_configured_drafts_and_records_calls() -> None:
    fake = FakeSourceAdapter(drafts=[draft(1), draft(2), draft(3)])
    assert isinstance(fake, SourceAdapter)
    got = [d async for d in fake.collect(SourceQuery(limit=2))]
    assert [d.source_job_id for d in got] == ["job-1", "job-2"]
    assert fake.calls == [SourceQuery(limit=2)]


@pytest.mark.parametrize(
    "field", ["title", "company_raw", "location_raw", "source_job_id", "description"]
)
def test_nul_is_rejected_in_every_text_field(field: str) -> None:
    with pytest.raises(ValidationError):
        draft(**{field: "abc\x00def"})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a\x00b",
        "https://example.com/a\tb",
        "https://example.com/a\u00a0b",
        "https://example.com/a\u2028b",
        "https://example.com\\evil.com",
        "https:\\\\evil.com",
    ],
)
def test_urls_with_whitespace_or_backslash_are_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        draft(url=url)


def test_naive_posted_at_is_utc() -> None:
    naive = draft(posted_at=datetime(2026, 11, 1, 9, 30))
    assert naive.posted_at == datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
    ist = timezone(timedelta(hours=5, minutes=30))
    aware = draft(posted_at=datetime(2026, 11, 1, 9, 30, tzinfo=ist))
    assert aware.posted_at is not None
    assert aware.posted_at.utcoffset() == timedelta(hours=5, minutes=30)
