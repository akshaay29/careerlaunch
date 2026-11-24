"""NFR-3 (Story 3.2): scores refresh within 5 minutes of a Skill Profile edit. The edit goes
through the API and the router's after-response ``priority`` drain runs (for real, as in the
other router tests): one drain, no polling."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.main import create_app
from tests.conftest import AccountFactory, TokenFactory
from tests.fakes.file_store import FakeFileStore
from tests.listing_fixtures import draft
from tests.match_fixtures import MatchDb, ingest, mark, recompute
from tests.resume_fixtures import RESUME_LINES, make_text_pdf

pytestmark = pytest.mark.usefixtures("clean_jobs")

PROFILE = "/api/v1/skill-profile"
PREFS = "/api/v1/preferences"
RESUMES = "/api/v1/resumes"


@pytest.fixture
def app():
    return create_app(file_store=FakeFileStore())


def _headers(accounts: AccountFactory, tokens: TokenFactory, owner) -> dict[str, str]:
    _, email, _ = accounts.row(owner)  # type: ignore[misc]
    return tokens.headers(tokens.token(owner, email))


@pytest.fixture
def db(migrated_db: str) -> MatchDb:
    match_db = MatchDb(migrated_db)
    match_db.clear()
    yield match_db
    match_db.clear()


async def test_skill_edit_through_the_api_refreshes_stored_scores(
    client: AsyncClient,
    engine: AsyncEngine,
    accounts: AccountFactory,
    tokens: TokenFactory,
    db: MatchDb,
) -> None:
    # A seeded pool: two Backend Listings asking for Docker, one Listing outside the role.
    await ingest(
        engine,
        [
            draft(1, title="Backend Developer Intern"),
            draft(2, title="Backend Engineer"),
            draft(3, title="Pastry Chef"),
        ],
    )
    backend = [db.id_of("job-1"), db.id_of("job-2")]
    db.set_skills(backend[0], ["python", "docker"])
    db.set_skills(backend[1], ["python", "docker", "aws"])

    owner = accounts.create()
    _, email, _ = accounts.row(owner)  # type: ignore[misc]
    headers = tokens.headers(tokens.token(owner, email))
    db.set_preferences(owner, ["Backend Developer", "Backend Engineer"])
    db.set_profile(owner, ["python"])
    await mark(engine, owner)
    await recompute(owner)
    before = db.scores(owner)
    assert set(before) == set(backend)
    db.clear_outbox()

    edited_at = db.now()
    resp = await client.patch(PROFILE, json={"add": ["docker"]}, headers=headers)
    assert resp.status_code == 200, resp.text

    # The drain ran after the response: the stored scores already reflect the edit.
    after = db.scores(owner)
    assert set(after) == set(backend)
    assert all(after[i] > before[i] for i in backend)
    for _, _, breakdown, _, _, _, computed_at in db.score_rows(owner):
        assert computed_at > edited_at
        assert "docker" in [s["id"] for s in breakdown["matched_skills"]]
    # Missing-skill rows match the breakdowns; relevance holds the max score per Listing.
    assert db.missing(owner) == [(backend[1], "aws")]
    for listing_id in backend:
        top = db.execute(
            "SELECT max(score) FROM app.match_scores WHERE listing_id = %s", (listing_id,)
        )[0][0]
        assert db.relevance(listing_id) == top
    stale, computed, candidates = db.state(owner)  # type: ignore[misc]
    assert computed > stale > edited_at and candidates == 2
    assert [c for o, c in db.ops("matching.recompute") if o == owner][-1]["candidates"] == 2



async def test_preferences_save_through_the_api_refreshes_stored_scores(
    client: AsyncClient,
    engine: AsyncEngine,
    accounts: AccountFactory,
    tokens: TokenFactory,
    db: MatchDb,
) -> None:
    await ingest(engine, [draft(1, title="Backend Intern"), draft(2, title="Pastry Chef")])
    owner = accounts.create()
    db.set_profile(owner, ["python"])
    db.clear_outbox()
    saved_at = db.now()
    body = {
        "role_titles": ["Backend Intern"],
        "locations": ["Bengaluru"],
        "job_types": ["internship"],
        "batch_year": None,
        "cgpa": None,
        "degree": None,
        "degree_other": None,
        "work_authorisation": None,
        "threshold": 60,
        "alert_frequency": "daily",
        "timezone": "Asia/Kolkata",
    }
    resp = await client.put(PREFS, json=body, headers=_headers(accounts, tokens, owner))
    assert resp.status_code == 200, resp.text
    assert set(db.scores(owner)) == {db.id_of("job-1")}
    assert db.state(owner)[1] > saved_at  # type: ignore[index]


async def test_resume_activation_through_the_api_refreshes_stored_scores(
    client: AsyncClient,
    engine: AsyncEngine,
    accounts: AccountFactory,
    tokens: TokenFactory,
    db: MatchDb,
) -> None:
    """The activate route's after-response drain runs the extraction and the recomputes it
    triggers (the activation's own and the extracted Skill Profile's)."""
    await ingest(engine, [draft(1, title="Backend Developer")])
    listing = db.id_of("job-1")
    db.set_skills(listing, ["docker", "react"])
    owner = accounts.create()
    headers = _headers(accounts, tokens, owner)
    db.set_preferences(owner, ["Backend Developer"])
    pdf = make_text_pdf(RESUME_LINES)
    uploads = []
    for name in ("first.pdf", "second.pdf"):
        resp = await client.post(
            RESUMES, files={"file": (name, pdf, "application/octet-stream")}, headers=headers
        )
        assert resp.status_code == 201, resp.text
        uploads.append(resp.json()["id"])

    activated_at = db.now()
    resp = await client.post(f"{RESUMES}/{uploads[1]}/activate", headers=headers)
    assert resp.status_code == 200, resp.text
    stale, computed, candidates = db.state(owner)  # type: ignore[misc]
    assert stale > activated_at and computed > stale and candidates == 1
    ((_, value, breakdown, *_),) = db.score_rows(owner)
    # The extracted skills (React, Docker, ...) are in the stored score.
    assert {s["id"] for s in breakdown["matched_skills"]} == {"docker", "react"}
    assert value > 0
