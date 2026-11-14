"""Story 2.3: the LinkedIn ProviderSource mapping for each allowlisted actor (items shaped
like the actors' documented samples), and the actor inputs (never a cookie or login)."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from app.adapters.apify import ApifyLinkedInSource
from app.adapters.apify import linkedin_source as ls
from app.core.config import Settings
from app.ports.source_adapter import ProviderSource, SourceQuery
from tests.provider_run_fixtures import (
    DESCRIPTION_WORD,
    FALLBACK,
    PRIMARY,
    curious_dataset,
    curious_item,
    harvest_item,
)


def test_satisfies_the_port_and_covers_the_default_allowlist() -> None:
    assert isinstance(ApifyLinkedInSource(), ProviderSource)
    assert ApifyLinkedInSource().source == "linkedin"
    assert Settings().apify_actors == (PRIMARY, FALLBACK)
    assert set(Settings().apify_actors) <= set(ls.SUPPORTED_ACTORS)


# --------------------------------------------------------------------------- items


def test_curious_coder_item_maps_to_a_draft() -> None:
    (d,), rejected = ls.drafts_from_items(PRIMARY, [curious_item(1)])
    assert rejected == 0
    assert d.source == "linkedin" and d.source_job_id == "3692563201"
    assert d.url == "https://www.linkedin.com/jobs/view/3692563201/"  # no tracking params
    assert d.title == "Backend Engineering Intern 1"
    assert d.company_raw == "Facebook"
    assert d.location_raw == "Bengaluru, Karnataka, India"
    assert d.job_type == "internship"
    assert d.posted_at == datetime(2026, 11, 10, tzinfo=UTC)
    assert d.deadline is None
    assert DESCRIPTION_WORD in d.description
    # Only the ListingDraft fields survive: nothing else from the raw item.
    assert "Someone Private" not in repr(d) and "salary" not in d.model_dump()


def test_curious_coder_expiry_and_types() -> None:
    drafts, _ = ls.drafts_from_items(
        PRIMARY,
        [
            curious_item(1, employmentType="Full-time", expireAt="2026-12-01T00:00:00Z"),
            curious_item(2, employmentType="Contract", postedAt=None,
                         postedAtTimestamp=1_762_732_800_000),
            curious_item(3, id=3692563999),  # numeric id
        ],
    )
    assert [d.job_type for d in drafts] == ["full_time", None, "internship"]
    assert drafts[0].deadline is None  # expireAt is a display expiry, not a deadline
    assert drafts[1].posted_at == datetime.fromtimestamp(1_762_732_800, UTC)
    assert drafts[2].source_job_id == "3692563999"


def test_harvestapi_item_maps_to_a_draft() -> None:
    (d,), rejected = ls.drafts_from_items(FALLBACK, [harvest_item(1)])
    assert rejected == 0
    assert d.source_job_id == "4227647590"
    assert d.url == "https://www.linkedin.com/jobs/view/4227647590/"
    assert d.title == "Associate Data Engineer 1"
    assert d.company_raw == "East Daley Analytics"
    assert d.location_raw == "Greenwood Village, CO"
    assert d.job_type == "full_time"
    assert d.posted_at == datetime(2026, 11, 12, 17, 12, 41, tzinfo=UTC)
    assert d.deadline is None  # the sample's expireAt is not an application deadline


def test_harvestapi_location_falls_back_to_parsed_text() -> None:
    item = harvest_item(1, location={"parsed": {"text": "Pune, India"}}, employmentType="internship")
    (d,), _ = ls.drafts_from_items(FALLBACK, [item])
    assert d.location_raw == "Pune, India" and d.job_type == "internship"


def test_bad_items_are_counted_not_raised() -> None:
    drafts, rejected = ls.drafts_from_items(PRIMARY, curious_dataset(8, 2))
    assert len(drafts) == 8 and rejected == 2
    drafts, rejected = ls.drafts_from_items(PRIMARY, curious_dataset(0, 4))
    assert drafts == [] and rejected == 4


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": None},
        {"id": ""},
        {"id": "12ab"},
        {"id": True},
        {"title": None},
        {"title": "   "},
        {"title": "bad\x00title"},
    ],
)
def test_unusable_items(overrides: dict) -> None:
    assert ls.drafts_from_items(PRIMARY, [curious_item(1, **overrides)]) == ([], 1)


def test_unparseable_dates_are_dropped_not_rejected() -> None:
    (d,), rejected = ls.drafts_from_items(
        PRIMARY, [curious_item(1, postedAt="last tuesday", expireAt="soon")]
    )
    assert rejected == 0 and d.posted_at is None and d.deadline is None


def test_long_text_is_capped() -> None:
    (d,), _ = ls.drafts_from_items(
        PRIMARY, [curious_item(1, title="x" * 500, companyName="c" * 500)]
    )
    assert len(d.title) == 300 and len(d.company_raw) == 200


def test_duplicate_job_ids_kept_once() -> None:
    drafts, rejected = ls.drafts_from_items(PRIMARY, [curious_item(1), curious_item(1)])
    assert len(drafts) == 1 and rejected == 0


def test_unknown_actor_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        ls.drafts_from_items("someone/else", [curious_item(1)])
    with pytest.raises(ValueError):
        ls.run_input("someone/else", SourceQuery(role_titles=("x",)))


# --------------------------------------------------------------------------- input


QUERY = SourceQuery(
    role_titles=("Backend Intern", "Data Analyst"),
    locations=("Bengaluru", "Remote"),
    job_types=("internship", "full_time"),
    limit=100,
)


def _no_credentials(value: object) -> None:
    text = repr(value).lower()
    for word in ("cookie", "li_at", "login", "password", "session"):
        assert word not in text


def test_curious_coder_input() -> None:
    run_input = ls.run_input(PRIMARY, QUERY)
    _no_credentials(run_input)
    assert set(run_input) == {
        "urls",
        "limitPerSource",
        "scrapeCompany",
        "splitByLocation",
        "autoConvertToAiSearch",
    }
    assert run_input["scrapeCompany"] is False and run_input["splitByLocation"] is False
    assert len(run_input["urls"]) == 4 and run_input["limitPerSource"] == 25
    parts = urlsplit(run_input["urls"][0])
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.linkedin.com/jobs/search/"
    q = parse_qs(parts.query)
    assert q == {
        "keywords": ["Backend Intern"],
        "location": ["Bengaluru"],
        "f_TPR": ["r604800"],
        "f_JT": ["I,F"],
    }


def test_curious_coder_input_without_locations_or_types() -> None:
    run_input = ls.run_input(PRIMARY, SourceQuery(role_titles=("SDE",), limit=50))
    (url,) = run_input["urls"]
    assert parse_qs(urlsplit(url).query) == {"keywords": ["SDE"], "f_TPR": ["r604800"]}
    assert run_input["limitPerSource"] == 50


def test_searches_are_capped() -> None:
    query = SourceQuery(
        role_titles=tuple(f"t{i}" for i in range(5)),
        locations=tuple(f"l{i}" for i in range(5)),
        limit=100,
    )
    assert len(ls.run_input(PRIMARY, query)["urls"]) == ls.MAX_SEARCHES


def test_harvestapi_input_never_sets_the_cookie() -> None:
    run_input = ls.run_input(FALLBACK, QUERY)
    _no_credentials(run_input)
    assert "cookie" not in run_input
    assert run_input == {
        "jobTitles": ["Backend Intern", "Data Analyst"],
        "locations": ["Bengaluru", "Remote"],
        "maxItems": 25,
        "employmentType": ["internship", "full-time"],
        "postedLimit": "week",
        "sortBy": "date",
    }
    minimal = ls.run_input(FALLBACK, SourceQuery(role_titles=("SDE",), limit=10))
    assert minimal == {"jobTitles": ["SDE"], "maxItems": 10, "postedLimit": "week", "sortBy": "date"}


def test_harvestapi_input_respects_max_searches() -> None:
    query = SourceQuery(
        role_titles=tuple(f"t{i}" for i in range(6)),
        locations=("l0", "l1", "l2", "l3"),
        limit=100,
    )
    run_input = ls.run_input(FALLBACK, query)
    assert run_input["jobTitles"] == ["t0", "t1"] and run_input["locations"] == [
        "l0",
        "l1",
        "l2",
        "l3",
    ]
    assert len(run_input["jobTitles"]) * len(run_input["locations"]) <= ls.MAX_SEARCHES
    assert run_input["maxItems"] == 13  # ceil(100 / 8)
    # Same choice as the primary actor's searches.
    urls = ls.run_input(PRIMARY, query)["urls"]
    assert len(urls) == 8
    many_locations = SourceQuery(
        role_titles=("a", "b"), locations=tuple(f"l{i}" for i in range(12)), limit=10
    )
    capped = ls.run_input(FALLBACK, many_locations)
    assert capped["jobTitles"] == ["a"] and len(capped["locations"]) == ls.MAX_SEARCHES


@pytest.mark.parametrize(
    "bad",
    [
        {"cookie": "x"},
        {"LinkedInCookies": ["x"]},
        {"nested": {"li_at": "x"}},
        {"list": [{"sessionId": "x"}]},
        {"loginEmail": "a"},
        {"password": "p"},
        {"urls": ["https://www.linkedin.com/jobs/search/?keywords=x&session_token=y"]},
        {"urls": ["https://www.linkedin.com/jobs/search/?li_at=y"]},
    ],
)
def test_credential_guard_rejects(bad: dict) -> None:
    with pytest.raises(ValueError):
        ls.check_no_credentials(bad)


def test_credential_guard_accepts_real_inputs() -> None:
    for actor in (PRIMARY, FALLBACK):
        ls.check_no_credentials(ls.run_input(actor, QUERY))


def test_run_input_refuses_a_mapping_that_adds_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    def leaky(query: SourceQuery) -> dict:
        return {"jobTitles": ["x"], "proxy": {"cookie": "c"}}

    monkeypatch.setitem(ls._ACTORS, FALLBACK, (leaky, ls._harvestapi_item))
    with pytest.raises(ValueError):
        ls.run_input(FALLBACK, QUERY)
