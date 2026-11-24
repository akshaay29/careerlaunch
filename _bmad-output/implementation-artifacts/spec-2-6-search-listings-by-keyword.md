---
title: 'Story 2.6: Search Listings by keyword'
type: 'feature'
created: '2026-11-15'
status: 'done'
baseline_commit: 'b7dcc07c3550bd1f1b384b94681036745a2637af'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-5-expire-evict-and-take-down-listings.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Student can only page through the pool newest-first. They cannot look for a specific role, and a typo ("bakend intrn") finds nothing (FR-13, NFR-2).

**Approach:**
- Keyword search over the visible, non-expired pool: full-text matching on `search_doc`, ranked by relevance, plus trigram similarity (`pg_trgm`) so near-misses still match.
- A search box on `/listings` with a neutral empty state.
- A performance test that holds search under 500 ms at about 30,000 Listings.

## Boundaries & Constraints

**Always:**
- **Endpoint:** `GET /api/v1/listings/search?q=&limit=&offset=` (`operation_id` `searchListings`), returning the existing `ListingOut` items, best match first. A new `ListingSearchResults` schema holds `items` and `next_offset` (null when there are no more).
  - `limit` defaults to 20, max 50. `offset` defaults to 0, max 200 (search shows the best matches, not the whole pool).
  - An empty or whitespace-only `q`, or one shorter than 2 characters after cleaning, is a 422 with a clear message. Over 100 characters is also a 422.
  - The route is declared so it is not captured by `/listings/{listing_id}`.
- **Visibility is the browse rule:** `visibility_owner_id IS NULL OR = actor`, and `expired_at IS NULL`. Search never returns another Student's private Listing or an expired one.
- **Query building:**
  - The text is Unicode-normalised, lower-cased and split into word tokens (letters and digits only).
  - At most 8 tokens of at most 40 characters each.
  - Tokens become a prefix `to_tsquery('english', 'a:* & b:*')` built only from those sanitised tokens, passed as a bind parameter. No raw user text ever reaches a tsquery or SQL string.
  - All tokens must match (AND). A query with no usable token is a 422.
- **Matching:** a Listing matches when its `search_doc` matches the tsquery, OR the trigram `word_similarity` of the normalised query against the Listing's `lower(title || ' ' || company)` is at least `SEARCH_TRIGRAM_THRESHOLD` (new config, default 0.35, range 0.1–0.9).
- **Ranking:** `ts_rank_cd` of `search_doc` against the tsquery plus the trigram similarity, with full-text matches ranking above similarity-only ones; ties break by `first_seen_at DESC, id DESC`, so pages are stable.
- **Typo tolerance (acceptance):** "bakend intrn" returns "Backend Intern" Listings.
- **Schema:**
  - Migration `0016_listing_search` enables `pg_trgm`.
  - It must work on plain Postgres 17 (CI) and on Supabase; follow how `0003_pg_cron_drain.py` handles extension schemas.
  - It adds a stored generated column (immutable expression, e.g. `lower(title || ' ' || company)`) on `listings` with a `gin (… gin_trgm_ops)` index.
  - The queries must find the `pg_trgm` functions and operators whatever schema the extension lives in, and `scripts/check_exposed_schema.py` must still pass.
  - `listings_search_doc_idx` (GIN) already exists.
  - Backfill is automatic for the generated column. Ingest needs no change.
- **Performance:** `api/tests/test_listing_search_perf.py` seeds about 30,000 active Listings with a SQL `generate_series` insert (realistic titles, companies, skills, mixed visibility), then asserts that typical queries (a two-word query, a typo query, a single common word, a rare word) each finish in under 500 ms at the service level, taking the best of a few runs after a warm-up.
- **Web:**
  - `/listings` gets a search form (label, input, submit, clear). With a query, results come from `searchListings` and replace the browse list; clearing returns to browse.
  - The query lives in the `q` URL parameter so a search can be shared and reloaded.
  - "Load more" uses `next_offset`.
  - Empty state: "No Listings match “…”. Try fewer or broader terms, or check the spelling." (rendered as plain text).
  - Pure helpers in `web/src/lib/listings.ts` (query cleaning that mirrors the API rules, the empty message) with `node --test` tests. Regenerate the client with `npm run gen:api`.
  - Listing cards reuse `ListingCard`, so attribution and the not-affiliated notice stay.
- Plain text everywhere (scraped text and the query are untrusted).

**Never:**
- Interpolating user text into SQL or a tsquery string.
- Returning a Listing that browse would hide.
- Storing queries or logging their text (log the length only).
- Ranking by Epic 3 scores, or personalising results.
- Any change to ingest, expiry or eviction.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Plain match | "backend intern" | Listings with those terms in title, company, skills or description lexemes; best first | N/A |
| Prefix | "intern" | matches "Internship" and "Intern" Listings | N/A |
| Typo | "bakend intrn" | returns "Backend Intern" Listings via trigram | N/A |
| Skill match | "python" | matches a Listing whose extracted skills include Python | N/A |
| Ranking | title match vs description-only match | the title match ranks first | N/A |
| Private visibility | another Student's private Listing matches | not returned | N/A |
| Own private | the Student's own private Listing matches | returned | N/A |
| Expired | an expired Listing matches | not returned | N/A |
| Blocked / deleted | a taken-down Listing | not returned | N/A |
| No results | "zzzzqqqq" | `items: []`, `next_offset: null` | N/A |
| Paging | 45 matches, `limit=20` | pages of 20, 20, 5 with stable order; `next_offset` null on the last | N/A |
| Offset cap | `offset=201` | 422 | validation |
| Bad query | "", "   ", "a", "!!!", 101 characters | 422 | validation |
| Long / many tokens | 20 words | only the first 8 tokens are used | N/A |
| Hostile text | `'); drop table listings; --`, `a:* | !b`, unicode, NUL | no SQL error; treated as plain tokens or 422 | N/A |
| Route | `/listings/search` vs `/listings/{uuid}` | search route wins; a UUID still gets the detail | N/A |
| Signed out | no token | 401 | N/A |
| Performance | about 30,000 active Listings | each typical query under 500 ms | N/A |
| Web empty state | no results | neutral message suggesting broader terms | N/A |
| Web URL | `/listings?q=intern` | the search runs on load and the input is filled | N/A |

</frozen-after-approval>

## Code Map

- `api/migrations/versions/0016_listing_search.py` -- after `0015_listing_maintenance`. Read `0003_pg_cron_drain.py` for the extension-schema handling and `0011_listings.py` for the `listings` columns and indexes. Bump the HEAD test. `check_exposed_schema.py` must still pass.
- `api/app/modules/listings/repo.py` -- add `search_page` next to `browse_page`/`_visible_to`; read `_visible_to` and the `search_doc` weights written by `_UPSERT`.
- `api/app/modules/listings/service.py` -- `search` next to `browse`: token cleaning, tsquery building, paging. `schemas.py`, `router.py` -- `ListingSearchResults` and the route (declare before `/listings/{listing_id}`).
- `api/app/core/config.py` -- `SEARCH_TRIGRAM_THRESHOLD`; `.env.example` and `render.yaml`.
- `api/tests/listing_fixtures.py` -- existing helpers to add Listings; `api/tests/` -- `test_listing_search.py` (matrix), `test_listing_search_perf.py`.
- `web/src/components/listings/listing-browser.tsx`, `web/src/app/listings/page.tsx`, `listing-card.tsx` -- add the search form, URL `q`, results and empty state (the app uses Cache Components: read `searchParams` inside `<Suspense>`, as the detail page does with `params`). `web/src/lib/listings.ts` + `listings.test.ts` -- helpers and tests.
- Fixtures: low-entropy fake ids and tokens only; GitGuardian scans every commit.

## Tasks & Acceptance

**Execution:**
- [x] Config and migration 0016 (`pg_trgm`, generated column, GIN index).
- [x] `listings` repo, service, schemas and route: `search`.
- [x] Web: search form, URL `q`, results, empty state, helpers and tests; `npm run gen:api`.
- [x] Tests: `test_listing_search.py` for every matrix row, and the performance test at about 30,000 Listings.

**Acceptance Criteria:**
- Given a seeded pool, when a signed-in Student searches "backend intern" and then "bakend intrn", then both return the Backend Intern Listings visible to them and nothing private to anyone else.
- Given the full API suite, import-linter, the schema check, and the web lint, typecheck, test and build, when they run, then everything passes.

## Design Notes

- **Full-text first, trigram as the safety net.** Prefix full-text handles stemming and partial words; trigram similarity on title and company catches misspellings. Extracted skills are covered by full-text only, because a trigram expression over an array is not immutable.
- **Offset paging, capped.** Ranked results have no stable keyset cursor; capping `offset` at 200 keeps the query cheap and matches how people use search.

## Implementation Notes

- **Extension schema.** 0016 creates `pg_trgm` in `extensions` when that schema exists, else in `public`; an already-enabled extension stays put. The migration builds the index with the schema read from `pg_extension`, and `repo.search_page` reads it once per process and qualifies the `<%` operator and `word_similarity()`, so nothing depends on `search_path` (which is `app`). `check_exposed_schema.py` passes with both placements (tested on plain Postgres 17 with and without an `extensions` schema).
- **Generated column** is `listings.title_company` (`lower(title || ' ' || company)`, stored) with `listings_title_company_trgm_idx`. It is not mapped on the `Listing` model; the search query refers to it as a literal column.
- **Matching** uses the `<%` operator (which the trigram GIN index serves; threshold from `pg_trgm.word_similarity_threshold`, set `LOCAL` from `SEARCH_TRIGRAM_THRESHOLD`) and `word_similarity()` for ranking. The same `set_config` call sets `random_page_cost` to 1.1 for the transaction: with Postgres' default (4) the planner chose a sequential scan that evaluated the trigram test on every row (130 to 200 ms at 30,000 Listings, even for no match); with 1.1 both GIN indexes answer and selective queries take under 60 ms. Measured best-of-3 at the service level on Postgres 17: two words about 190 to 260 ms, typo 80 to 150 ms, common word about 110 to 150 ms, rare word about 50 ms.
- **Query rules.** "Cleaning" is the tokens joined by single spaces: it must be at least 2 characters (so "a" and "!!!" are 422, "go" is fine; "c#" cleans to "c" and is 422). Token characters are `str.isalnum()` in Python and `[\p{L}\p{N}]` in the web helper. The 100-character limit is on the raw `q`. `next_offset` is null when the next page would start past the offset cap of 200.
- **Web.** `/listings` reads `q` inside `<Suspense>` and keys `ListingBrowser` by the query; the form pushes `/listings?q=...` (Clear and an empty submit push `/listings`). An invalid `q` in the URL shows the validation message without calling the API. The web changes were checked with lint, typecheck, `node --test` and `next build`, not in a browser.
- **Query text and logs.** The application does not log or store the query text (only its length); the URL still carries it, so access logs and browser history can. A query of only stop words ("it", "to be") is a 422 (the tsquery has no lexeme), and `service.search` validates `limit` (1 to 50) as well as `offset` for direct callers.
- **Trigram calls** are built by `repo.trigram_expressions` with the extension schema always quoted (operator and `word_similarity()`), covered by a compile-level test.
- **Perf seed** now gives each Listing about 400 varied terms (a ~6 KB `search_doc`), and the deep page (offset 200, limit 50) is timed as best of 3 like the rest.

- **Review follow-up (2026-11-24): candidates come from the indexes.** With realistic description sizes (about 4 KB of `search_doc` per row), one `WHERE full_text OR similar` made the planner scan the table and detoast every `search_doc` (about 400 ms for two-word, typo and common-word queries at 30,000 Listings). `search_page` now gathers candidate ids from the full-text GIN index and the trigram GIN index separately (`UNION ALL`, `bool_or` per id), then ranks only those, computing `ts_rank_cd` only for full-text candidates. Timings (best of 3): two words 242 ms, typo 59, common word 227, rare word 31, no match 30, deep page 242.

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | "Full-text matches rank above similarity-only" is not pinned by a test; deleting `full_text.desc()` leaves every ranking test green | medium | The only ranking tests compare two full-text matches, or a full-text hit that is also the best trigram match | patch |
| 2 | `word_similarity` is called through `getattr(func, schema)` unquoted while the operator is quoted, so a schema needing quotes breaks ranking | low | `repo.search_page` | patch |
| 3 | A query made only of stop words ("it", "to be") passes validation, builds an empty tsquery and returns loose trigram results instead of the documented 422 | medium | `to_tsquery('english','it:*')` is empty; usability is judged on length only | patch (reject when the tsquery has no lexeme) |
| 4 | `service.search` does not validate `limit` (offset is validated); a direct caller gets a SQL error or a non-null `next_offset` | low | `service.search` | patch |
| 5 | The perf seed uses ~200-character bodies, far smaller than real descriptions' lexeme sets, so the 500 ms result may not carry over; the deep-page test times one run | medium | `test_listing_search_perf.py` | patch (realistic seed size, best of 3 for every query) |
| 6 | README/spec claim the query is never logged, but `q` is in the URL, so access logs and browser history hold it | low | the test excludes the `httpx` logger | patch (qualify the wording) |
| 7 | `test_hostile_text_is_plain_tokens` only asserts 200 or 422; no test that unrelated titles do not match | low | weak assertion; threshold 0.35 untested for false positives | patch |
| 8 | No `role="status"` / `aria-live` for the searching, empty and result states | low | `listing-browser.tsx` | patch |
| 9 | Trigram similarity compares the whole phrase to title and company, so a typo plus an extra skill word ("bakend intrn python") may fall below the threshold | medium | `word_similarity(phrase, title_company)`; the spec's acceptance example has no extra token | defer |
| 10 | The `pg_trgm` `extensions`-schema placement is not exercised in CI; the web search wiring has no component test | medium | CI applies `supabase_roles.sql`, which has no `extensions` schema; `web` has no component harness | defer |
| 11 | Offset paging can skip or repeat rows when the pool changes between pages; the 200 cap ends results silently; tokenizers drop combining marks; the two tokenizers are mirrored by hand; short language names ("c++", ".net") are rejected | low | documented limits of the chosen design | defer |
| 12 | The `_trgm_schema` cache is never invalidated; `random_page_cost` stays set for the rest of the transaction; no rate limit or `statement_timeout` | low | the schema never moves in practice; the route is read-only and ends its transaction; no endpoint has a rate limit yet | reject |
| 13 | The sprint-status edit marks 2.5 done in this change | false | the repo convention marks the previous story done on the next story's branch | reject |

## Verification

**Commands:**
- `cd api && uv run pytest -q` -- expected: all pass (fresh Postgres 17 with the roles SQL and migrations applied)
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- expected: clean
- `cd web && npm run gen:api && npm run lint && npm run typecheck && npm test && npm run build` -- expected: clean (delete `web/.next` first if the build fails with EPERM)
