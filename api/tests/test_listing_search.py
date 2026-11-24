"""FR-13, NFR-2, AD-5: keyword search over the Listing pool (Story 2.6). One test (or more)
per row of the I/O matrix."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import get_settings
from app.core.errors import InvalidInputError
from app.modules.listings import service
from app.ports.source_adapter import SourceQuery
from tests.conftest import AccountFactory, TokenFactory
from tests.fakes.source_adapter import FakeSourceAdapter
from tests.listing_fixtures import ListingDb, draft, listing_db  # noqa: F401 (fixture)

SEARCH = "/api/v1/listings/search"
LISTINGS = "/api/v1/listings"
PROBLEM = "application/problem+json"

pytestmark = pytest.mark.usefixtures("clean_jobs")

Student = tuple[uuid.UUID, dict[str, str]]


def _student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    account_id = accounts.create()
    _, email, _ = accounts.row(account_id)  # type: ignore[misc]
    return account_id, tokens.headers(tokens.token(account_id, email))


@pytest.fixture
def student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    return _student(accounts, tokens)


@pytest.fixture
def other_student(accounts: AccountFactory, tokens: TokenFactory) -> Student:
    return _student(accounts, tokens)


async def _collect(engine: AsyncEngine, drafts, *, collected_by=None) -> int:
    async with AsyncSession(engine) as session:
        count = await service.collect_and_ingest(
            session,
            FakeSourceAdapter(drafts=drafts),
            SourceQuery(limit=1000),
            collected_by=collected_by,
        )
        await session.commit()
    return count


async def _search(client: AsyncClient, headers: dict[str, str], q: str, **params) -> dict:
    resp = await client.get(SEARCH, params={"q": q, **params}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _titles(body: dict) -> list[str]:
    return [i["title"] for i in body["items"]]


def _plain(n: int, title: str, company: str = "Zeta Works", description: str = ""):
    return draft(n, title=title, company_raw=company, description=description)


# --------------------------------------------------------------------------- auth / route


async def test_signed_out_is_401(client: AsyncClient) -> None:
    resp = await client.get(SEARCH, params={"q": "intern"})
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM)


async def test_search_route_wins_over_the_detail_route(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [draft(1)])
    resp = await client.get(SEARCH, params={"q": "backend"}, headers=headers)
    assert resp.status_code == 200 and "items" in resp.json()
    item = resp.json()["items"][0]
    detail = await client.get(f"{LISTINGS}/{item['id']}", headers=headers)
    assert detail.status_code == 200 and detail.json() == item


async def test_item_shape_matches_browse(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [draft(1)])
    body = await _search(client, headers, "backend")
    assert set(body) == {"items", "next_offset"}
    browse = (await client.get(LISTINGS, headers=headers)).json()["items"]
    assert body["items"] == browse


# --------------------------------------------------------------------------- matching


async def test_plain_match_prefix_and_all_tokens(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(
        engine,
        [
            _plain(1, "Backend Intern", "Acme Corp"),
            _plain(2, "Frontend Intern", "Acme Corp"),
            _plain(3, "Backend Engineer", "Acme Corp"),
        ],
    )
    both = await _search(client, headers, "backend intern")
    assert _titles(both)[0] == "Backend Intern"
    assert "Frontend Intern" not in _titles(await _search(client, headers, "backend engineer"))
    # Prefix: "intern" matches "Internship" and "Intern".
    await _collect(engine, [_plain(4, "Software Internship", "Acme Corp")])
    prefix = _titles(await _search(client, headers, "intern"))
    assert {"Backend Intern", "Software Internship"} <= set(prefix)
    # A word from the company name matches too.
    assert len(_titles(await _search(client, headers, "acme"))) == 4


async def test_typo_tolerance(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(
        engine,
        [_plain(1, "Backend Intern", "Acme Corp"), _plain(2, "Florist Assistant", "Petal Inc")],
    )
    exact = await _search(client, headers, "backend intern")
    typo = await _search(client, headers, "bakend intrn")
    assert _titles(exact) == ["Backend Intern"]
    assert _titles(typo) == ["Backend Intern"]


async def test_skill_match(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(
        engine,
        [
            draft(1, title="Platform Role", description="We write Python every day."),
            draft(2, title="Gardening Role", description="Mowing lawns."),
        ],
    )
    assert _titles(await _search(client, headers, "python")) == ["Platform Role"]


async def test_title_match_outranks_description_only_match(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    # The description-only Listing is ingested LAST, so it is the newer one: relevance, not
    # recency, must put the title match first.
    await _collect(
        engine,
        [
            _plain(1, "Quokka Wrangler", "Zeta Works", "Daily tasks."),
            _plain(2, "Office Manager", "Zeta Works", "You will meet a quokka."),
        ],
    )
    assert _titles(await _search(client, headers, "quokka")) == ["Quokka Wrangler", "Office Manager"]


async def test_full_text_matches_rank_above_similarity_only(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    # "bakend" and "intrn" are not stems of "backend"/"intern": a full-text hit exists for the
    # Listing that literally contains them, and the similarity-only matches follow it.
    await _collect(
        engine,
        [_plain(1, "Bakend Intrn Lead"), _plain(2, "Backend Internship"), _plain(3, "Backend Interns")],
    )
    titles = _titles(await _search(client, headers, "bakend intrn"))
    assert titles[0] == "Bakend Intrn Lead"
    assert set(titles) >= {"Backend Internship", "Backend Interns"}


async def test_full_text_match_outranks_a_stronger_similarity_only_match(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    # The similarity-only Listing is ingested LAST, so it is the newer one: only the
    # full-text-first ordering can put "Office Manager" (a full-text match through its
    # description) ahead of it.
    await _collect(
        engine,
        [
            _plain(1, "Office Manager", "Zeta Works", "You will meet a quokka."),
            _plain(2, "Quoka Wrangler", "Zeta Works"),
        ],
    )
    (schema,) = listing_db.execute(
        "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
        "WHERE e.extname = 'pg_trgm'"
    )[0]
    (score,) = listing_db.execute(
        f'SELECT "{schema}".word_similarity(%s, title_company) FROM app.listings WHERE title = %s',
        ("quokka", "Quoka Wrangler"),
    )[0]
    assert score >= get_settings().search_trigram_threshold
    (is_full_text,) = listing_db.execute(
        "SELECT search_doc @@ to_tsquery('english', 'quokka:*') FROM app.listings WHERE title = %s",
        ("Quoka Wrangler",),
    )[0]
    assert is_full_text is False
    assert _titles(await _search(client, headers, "quokka")) == ["Office Manager", "Quoka Wrangler"]


async def test_unrelated_listings_are_not_returned(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(
        engine,
        [
            _plain(1, "Backend Intern", "Acme Corp"),
            _plain(2, "Kitchen Assistant", "Tasty Bites"),
            _plain(3, "Marketing Lead", "Brandworks"),
        ],
    )
    assert _titles(await _search(client, headers, "backend intern")) == ["Backend Intern"]
    assert _titles(await _search(client, headers, "bakend intrn")) == ["Backend Intern"]


# --------------------------------------------------------------------------- visibility


async def test_private_visibility(
    client: AsyncClient,
    engine: AsyncEngine,
    student: Student,
    other_student: Student,
    listing_db: ListingDb,
) -> None:
    owner_id, owner_headers = student
    _, other_headers = other_student
    await _collect(engine, [_plain(1, "Quokka Secretary Role")], collected_by=owner_id)
    await _collect(engine, [_plain(2, "Quokka Public Role")])
    assert set(_titles(await _search(client, owner_headers, "quokka"))) == {
        "Quokka Secretary Role",
        "Quokka Public Role",
    }
    assert _titles(await _search(client, other_headers, "quokka")) == ["Quokka Public Role"]
    # Trigram-only matching must not leak either.
    assert _titles(await _search(client, owner_headers, "secretry")) == ["Quokka Secretary Role"]
    assert (await _search(client, other_headers, "secretry"))["items"] == []


async def test_expired_and_taken_down_are_not_returned(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [_plain(1, "Quokka Old Role"), _plain(2, "Quokka Live Role")])
    listing_db.execute("UPDATE app.listings SET expired_at = now() WHERE title LIKE '%Old%'")
    assert _titles(await _search(client, headers, "quokka")) == ["Quokka Live Role"]
    assert _titles(await _search(client, headers, "quoka")) == ["Quokka Live Role"]
    try:
        async with AsyncSession(engine) as session:
            assert await service.block(session, "company", "Zeta Works") == 2
            await session.commit()
        assert (await _search(client, headers, "quokka"))["items"] == []
    finally:
        listing_db.execute("DELETE FROM app.listing_blocks")


# --------------------------------------------------------------------------- results and paging


async def test_no_results(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [draft(1)])
    assert await _search(client, headers, "zzzzqqqq") == {"items": [], "next_offset": None}


async def test_paging_is_stable(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [_plain(n, f"Quokka Role {n}") for n in range(1, 46)])
    seen: list[str] = []
    offset: int | None = 0
    sizes: list[int] = []
    while offset is not None:
        body = await _search(client, headers, "quokka", limit=20, offset=offset)
        sizes.append(len(body["items"]))
        seen.extend(i["id"] for i in body["items"])
        assert body["next_offset"] in (None, offset + 20)
        offset = body["next_offset"]
    assert sizes == [20, 20, 5]
    assert len(set(seen)) == 45
    # Same query again: same order.
    again = await _search(client, headers, "quokka", limit=50)
    assert [i["id"] for i in again["items"]] == seen
    assert again["next_offset"] is None


async def test_next_offset_stops_at_the_cap(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [_plain(n, f"Quokka Role {n}") for n in range(1, 8)])
    body = await _search(client, headers, "quokka", limit=3, offset=200)
    assert body["items"] == [] and body["next_offset"] is None
    # With more matches than fit under the cap, the last allowed page has no next_offset.
    listing_db.execute(
        """
        INSERT INTO app.listings (id, source, source_job_id, url, title, company, vocab_version,
                                  search_doc, first_seen_at, last_seen_at)
        SELECT gen_random_uuid(), 'manual', 'bulk-' || g, 'https://example.test/' || g,
               'Quokka Bulk ' || g, 'Zeta Works', 'v',
               to_tsvector('english', 'quokka bulk'), now(), now()
        FROM generate_series(1, 250) g
        """
    )
    body = await _search(client, headers, "quokka", limit=50, offset=200)
    assert len(body["items"]) == 50 and body["next_offset"] is None
    body = await _search(client, headers, "quokka", limit=50, offset=150)
    assert body["next_offset"] == 200


# --------------------------------------------------------------------------- validation


async def test_offset_and_limit_bounds(client: AsyncClient, student: Student) -> None:
    _, headers = student
    for params in ({"offset": 201}, {"offset": -1}, {"limit": 0}, {"limit": 51}):
        resp = await client.get(SEARCH, params={"q": "intern", **params}, headers=headers)
        assert resp.status_code == 422, params
        assert resp.headers["content-type"].startswith(PROBLEM)
    ok = await client.get(SEARCH, params={"q": "intern", "offset": 200}, headers=headers)
    assert ok.status_code == 200


@pytest.mark.parametrize("q", ["", "   ", "a", "!!!", " a ", "x" * 101, "\x00"])
async def test_bad_query_is_422(client: AsyncClient, student: Student, q: str) -> None:
    _, headers = student
    resp = await client.get(SEARCH, params={"q": q}, headers=headers)
    assert resp.status_code == 422, q
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert resp.json()["type"] == "/problems/validation-error"


async def test_missing_query_is_422(client: AsyncClient, student: Student) -> None:
    _, headers = student
    resp = await client.get(SEARCH, headers=headers)
    assert resp.status_code == 422


def test_tokens_are_cleaned_and_capped() -> None:
    assert service.search_tokens("  Backend,  INTERN!! ") == ["backend", "intern"]
    assert service.search_tokens("ＢＡＣＫＥＮＤ") == ["backend"]  # NFKC: full-width letters
    assert service.search_tokens("a:* & !b | c") == ["a", "b", "c"]
    many = service.search_tokens(" ".join(f"word{n}" for n in range(20)))
    assert many == [f"word{n}" for n in range(8)]
    assert service.search_tokens("y" * 90) == ["y" * 40]
    assert service.search_tokens("naïve café 数据") == ["naïve", "café", "数据"]


async def test_only_the_first_eight_tokens_are_used(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [_plain(1, "alpha beta gamma delta epsilon zeta eta theta")])
    q = "alpha beta gamma delta epsilon zeta eta theta nomatchtoken1 nomatchtoken2"
    assert len(q) <= 100
    assert _titles(await _search(client, headers, q)) == [
        "alpha beta gamma delta epsilon zeta eta theta"
    ]


# (query, accepted): accepted when at least 2 characters of word tokens remain.
HOSTILE = [
    ("'); drop table listings; --", True),
    ("a:* | !b", True),
    ("intern & (", True),
    ("ünïcödé 数据 \u200b", True),
    ("backend\x00intern", True),
    ("\\ ' \" % _ ;", False),
    ("!:*&|()<->", False),
]


@pytest.mark.parametrize(("q", "accepted"), HOSTILE)
async def test_hostile_text_is_plain_tokens(
    client: AsyncClient,
    engine: AsyncEngine,
    student: Student,
    listing_db: ListingDb,
    q: str,
    accepted: bool,
) -> None:
    _, headers = student
    await _collect(engine, [draft(1)])
    resp = await client.get(SEARCH, params={"q": q}, headers=headers)
    assert listing_db.count() == 1  # nothing dropped
    if accepted:
        assert resp.status_code == 200, resp.text
        assert set(resp.json()) == {"items", "next_offset"}
        if q == "backend\x00intern":
            assert len(resp.json()["items"]) == 1
    else:
        assert resp.status_code == 422, resp.text
        assert resp.json()["type"] == "/problems/validation-error"


@pytest.mark.parametrize("q", ["it", "to be", "the", "THE and", "the, a! of"])
async def test_stop_word_only_query_is_422(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb, q: str
) -> None:
    _, headers = student
    await _collect(engine, [draft(1)])
    resp = await client.get(SEARCH, params={"q": q}, headers=headers)
    assert resp.status_code == 422, q
    assert resp.json()["type"] == "/problems/validation-error"


async def test_a_stop_word_among_real_words_is_ignored(
    client: AsyncClient, engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    _, headers = student
    await _collect(engine, [_plain(1, "Intern at the Bakery", "Zeta Works")])
    assert _titles(await _search(client, headers, "the intern")) == ["Intern at the Bakery"]


async def test_service_rejects_bad_input_directly(
    engine: AsyncEngine, student: Student, listing_db: ListingDb
) -> None:
    account_id, _ = student
    async with AsyncSession(engine) as session:
        for q, limit, offset in (
            ("", 20, 0),
            ("a", 20, 0),
            ("the", 20, 0),
            ("x" * 101, 20, 0),
            ("intern", 20, 201),
            ("intern", 20, -1),
            ("intern", 0, 0),
            ("intern", 51, 0),
        ):
            with pytest.raises(InvalidInputError):
                await service.search(session, account_id, query=q, limit=limit, offset=offset)


async def test_the_query_text_is_not_logged(
    client: AsyncClient,
    student: Student,
    listing_db: ListingDb,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, headers = student
    with caplog.at_level("DEBUG"):
        await _search(client, headers, "secretkeywordzzz")
    # (The test client's own httpx log line shows the URL; the API must not log it.)
    api_logs = " ".join(r.getMessage() for r in caplog.records if r.name != "httpx")
    assert "secretkeywordzzz" not in api_logs
    assert "listings.search chars=16" in api_logs


async def test_the_trigram_threshold_is_configurable(
    client: AsyncClient,
    engine: AsyncEngine,
    student: Student,
    listing_db: ListingDb,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, headers = student
    await _collect(engine, [_plain(1, "Backend Intern", "Acme Corp")])
    assert _titles(await _search(client, headers, "bakend intrn")) == ["Backend Intern"]
    monkeypatch.setenv("SEARCH_TRIGRAM_THRESHOLD", "0.9")
    get_settings.cache_clear()
    try:
        assert (await _search(client, headers, "bakend intrn"))["items"] == []
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_the_threshold_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    for bad in ("0.05", "0.95"):
        monkeypatch.setenv("SEARCH_TRIGRAM_THRESHOLD", bad)
        get_settings.cache_clear()
        try:
            with pytest.raises(ValueError):
                get_settings()
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()
    assert get_settings().search_trigram_threshold == 0.35


def test_trigram_calls_quote_the_extension_schema() -> None:
    from sqlalchemy import Text, bindparam, literal_column
    from sqlalchemy.dialects import postgresql

    from app.modules.listings import repo

    phrase = bindparam("phrase", "x", type_=Text)
    column = literal_column("listings.title_company", type_=Text)
    for schema, quoted in (("extensions", '"extensions"'), ('My Ext"x', '"My Ext""x"')):
        similar, similarity = repo.trigram_expressions(schema, phrase, column)
        for expr, expected in (
            (similar, f"OPERATOR({quoted}.<%)"),
            (similarity, f"{quoted}.word_similarity("),
        ):
            sql = str(expr.compile(dialect=postgresql.dialect())).replace("%%", "%")
            assert expected in sql, sql
