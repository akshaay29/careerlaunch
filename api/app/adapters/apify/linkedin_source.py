"""LinkedIn through Apify actors: the ``ProviderSource`` half of collection (Story 2.3).

The run itself is started and polled through the ProviderRunner port by
``listings.service``; this module only builds each allowlisted actor's input and maps the
actor's raw dataset items to :class:`ListingDraft`. Raw items are never stored, and an item
that cannot be mapped is counted, never raised.

Actors (checked against the Apify Store pages and their input schemas, 2026-11-14):

* ``curious_coder/linkedin-jobs-scraper`` (primary, pay per result). Input: ``urls`` (public
  LinkedIn job-search URLs; one per role title and location, with ``f_JT`` for job types and
  ``f_TPR`` for the last week), ``limitPerSource``, ``scrapeCompany`` (false: fewer requests),
  ``splitByLocation`` (false), ``autoConvertToAiSearch`` (true: LinkedIn's AI search keeps the
  job-type filter as search terms). It has no cookie or login input. Items: ``id``, ``link``,
  ``title``, ``companyName``, ``location`` (text), ``employmentType`` ("Internship",
  "Full-time", ...), ``postedAt`` ("YYYY-MM-DD"), ``expireAt`` (when available),
  ``descriptionText``.
* ``harvestapi/linkedin-job-search`` (fallback). Input: ``jobTitles``, ``locations``,
  ``maxItems`` (per title and location pair), ``employmentType`` (enum ``full-time``,
  ``internship``, ...), ``postedLimit`` (``week``), ``sortBy`` (``date``). Its optional
  ``cookie`` input is NEVER set. Items: ``id``, ``linkedinUrl``, ``title``, ``company.name``,
  ``location.linkedinText``, ``employmentType`` ("full_time", ...), ``postedDate`` (ISO),
  ``expireAt`` (ISO), ``descriptionText``.

``expireAt`` is LinkedIn's display expiry, NOT an application deadline, so it is not
mapped: ``deadline`` comes only from an explicit application-deadline field, which neither
actor provides today (always None). Expiry is by not-seen counts (Story 2.5).
"""

import math
import re
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from pydantic import ValidationError

from app.ports.source_adapter import (
    MAX_COMPANY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_LOCATION_CHARS,
    MAX_TITLE_CHARS,
    JobType,
    ListingDraft,
    SourceQuery,
)

SOURCE = "linkedin"
CURIOUS_CODER = "curious_coder/linkedin-jobs-scraper"
HARVESTAPI = "harvestapi/linkedin-job-search"

# One run never fans out into more searches than this (role titles x locations).
MAX_SEARCHES = 10
SEARCH_URL = "https://www.linkedin.com/jobs/search/"
JOB_URL = "https://www.linkedin.com/jobs/view/{}/"
# LinkedIn's classic job-type filter codes.
_F_JT: dict[str, str] = {"internship": "I", "full_time": "F"}
_HARVEST_TYPES: dict[str, str] = {"internship": "internship", "full_time": "full-time"}
_JOB_ID_RE = re.compile(r"^[0-9]{1,30}$")

# Never sent, whatever the actor's schema allows: an input key (at any depth) or a query
# parameter of a generated URL containing one of these is refused.
FORBIDDEN_INPUT_WORDS: tuple[str, ...] = (
    "cookie",
    "li_at",
    "login",
    "password",
    "session",
    "jsessionid",
    "token",
)


# --------------------------------------------------------------------------- input


def _search_lists(query: SourceQuery) -> tuple[list[str], list[str]]:
    """The role titles and locations one run searches, with
    ``len(titles) * max(len(locations), 1) <= MAX_SEARCHES``: up to ``MAX_SEARCHES`` locations,
    then as many titles as still fit (at least one)."""
    locations = list(query.locations)[:MAX_SEARCHES]
    per_title = max(len(locations), 1)
    titles = list(query.role_titles)[: max(1, MAX_SEARCHES // per_title)]
    return titles, locations


def _searches(query: SourceQuery) -> list[tuple[str, str | None]]:
    titles, locations = _search_lists(query)
    locs: list[str | None] = list(locations) or [None]
    return [(title, loc) for title in titles for loc in locs]


def _per_search(total: int, searches: int) -> int:
    return max(1, math.ceil(total / max(searches, 1)))


def _curious_coder_input(query: SourceQuery) -> dict[str, Any]:
    searches = _searches(query)
    job_types = ",".join(_F_JT[t] for t in query.job_types if t in _F_JT)
    urls: list[str] = []
    for title, location in searches:
        params = {"keywords": title}
        if location:
            params["location"] = location
        params["f_TPR"] = "r604800"  # posted in the last week
        if job_types:
            params["f_JT"] = job_types
        urls.append(f"{SEARCH_URL}?{urlencode(params)}")
    return {
        "urls": urls,
        "limitPerSource": _per_search(query.limit, len(urls)),
        "scrapeCompany": False,
        "splitByLocation": False,
        "autoConvertToAiSearch": True,
    }


def _harvestapi_input(query: SourceQuery) -> dict[str, Any]:
    titles, locations = _search_lists(query)
    pairs = len(titles) * max(len(locations), 1)
    run_input: dict[str, Any] = {
        "jobTitles": titles,
        "maxItems": _per_search(query.limit, pairs),
        "postedLimit": "week",
        "sortBy": "date",
    }
    if locations:
        run_input["locations"] = locations
    employment = [_HARVEST_TYPES[t] for t in query.job_types if t in _HARVEST_TYPES]
    if employment:
        run_input["employmentType"] = employment
    return run_input


# --------------------------------------------------------------------------- items


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _job_id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    if isinstance(value, str) and _JOB_ID_RE.fullmatch(value.strip()):
        return value.strip()
    return None


def _when(value: Any) -> datetime | None:
    """An ISO date or datetime string, or epoch milliseconds, as an aware datetime."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        try:
            return datetime.fromtimestamp(value / 1000, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        if len(raw) == 10:
            return datetime.combine(date.fromisoformat(raw), datetime.min.time(), UTC)
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _job_type(value: Any) -> JobType | None:
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str)), None)
    if not isinstance(value, str):
        return None
    key = re.sub(r"[\s-]+", "_", value.strip().lower())
    if key == "internship":
        return "internship"
    if key in ("full_time", "fulltime"):
        return "full_time"
    return None


def _draft(
    *,
    job_id: str | None,
    title: Any,
    company: Any,
    location: Any,
    job_type: Any,
    posted: Any,
    description: Any,
) -> ListingDraft | None:
    if job_id is None:
        return None
    try:
        return ListingDraft(
            source=SOURCE,
            source_job_id=job_id,
            # The canonical job URL (from the numeric id): no tracking parameters.
            url=JOB_URL.format(job_id),
            title=_text(title, MAX_TITLE_CHARS),
            company_raw=_text(company, MAX_COMPANY_CHARS),
            location_raw=_text(location, MAX_LOCATION_CHARS),
            job_type=_job_type(job_type),
            description=_text(description, MAX_DESCRIPTION_CHARS),
            posted_at=_when(posted),
            deadline=None,  # no actor exposes an application deadline (expireAt is not one)
        )
    except ValidationError:
        return None


def _curious_coder_item(item: dict[str, Any]) -> ListingDraft | None:
    return _draft(
        job_id=_job_id(item.get("id")),
        title=item.get("title"),
        company=item.get("companyName"),
        location=item.get("location"),
        job_type=item.get("employmentType"),
        posted=item.get("postedAt") or item.get("postedAtTimestamp"),
        description=item.get("descriptionText"),
    )


def _harvestapi_item(item: dict[str, Any]) -> ListingDraft | None:
    company = item.get("company")
    location = item.get("location")
    if isinstance(location, dict):
        parsed = location.get("parsed")
        location = location.get("linkedinText") or (
            parsed.get("text") if isinstance(parsed, dict) else None
        )
    return _draft(
        job_id=_job_id(item.get("id")),
        title=item.get("title"),
        company=company.get("name") if isinstance(company, dict) else item.get("companyName"),
        location=location,
        job_type=item.get("employmentType"),
        posted=item.get("postedDate"),
        description=item.get("descriptionText"),
    )


_ACTORS: dict[
    str,
    tuple[
        Callable[[SourceQuery], dict[str, Any]],
        Callable[[dict[str, Any]], ListingDraft | None],
    ],
] = {
    CURIOUS_CODER: (_curious_coder_input, _curious_coder_item),
    HARVESTAPI: (_harvestapi_input, _harvestapi_item),
}

SUPPORTED_ACTORS: tuple[str, ...] = tuple(_ACTORS)


def _mapping(actor: str):  # noqa: ANN202
    try:
        return _ACTORS[actor]
    except KeyError:
        raise ValueError(f"no LinkedIn mapping for actor {actor!r}") from None


def _forbidden(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in FORBIDDEN_INPUT_WORDS)


def check_no_credentials(value: Any, path: str = "input") -> None:
    """Raise ``ValueError`` when ``value`` holds a credential-like key at any depth, or a URL
    string whose query has a credential-like parameter."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or _forbidden(key):
                raise ValueError(f"credential-like field {key!r} in the actor input ({path})")
            check_no_credentials(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            check_no_credentials(item, f"{path}[{index}]")
    elif isinstance(value, str) and value.lower().startswith(("http://", "https://")):
        for name, _ in parse_qsl(urlsplit(value).query, keep_blank_values=True):
            if _forbidden(name):
                raise ValueError(f"credential-like URL parameter {name!r} ({path})")


def run_input(actor: str, query: SourceQuery) -> dict[str, Any]:
    """The input for ``actor``; never contains a cookie, login or session field (checked)."""
    built = _mapping(actor)[0](query)
    check_no_credentials(built)
    return built


def drafts_from_items(
    actor: str, items: Iterable[Any]
) -> tuple[list[ListingDraft], int]:
    """``(drafts, rejected_count)``. An item that is not an object, has no LinkedIn job id or
    title, or fails ``ListingDraft`` validation is counted as rejected. A job id seen twice in
    one dataset is kept once (not counted as rejected)."""
    to_draft = _mapping(actor)[1]
    drafts: dict[str, ListingDraft] = {}
    rejected = 0
    for item in items:
        mapped = to_draft(item) if isinstance(item, dict) else None
        if mapped is None:
            rejected += 1
        else:
            drafts.setdefault(mapped.source_job_id, mapped)
    return list(drafts.values()), rejected


class ApifyLinkedInSource:
    """The ``ProviderSource`` for LinkedIn (``source='linkedin'``)."""

    source = SOURCE

    def run_input(self, actor: str, query: SourceQuery) -> dict[str, Any]:
        return run_input(actor, query)

    def drafts_from_items(
        self, actor: str, items: list[dict[str, Any]]
    ) -> tuple[list[ListingDraft], int]:
        return drafts_from_items(actor, items)

    def __repr__(self) -> str:
        return "ApifyLinkedInSource()"
