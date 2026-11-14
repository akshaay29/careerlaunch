---
title: 'Story 1.7: Set my Preferences'
type: 'feature'
created: '2026-11-14'
status: 'done'
baseline_commit: 'ecc8d8a1288f051acf612b34079032a01615a085'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CareerLaunch doesn't know what each Student is looking for. Collection (Epic 2) needs target roles, locations and job types. Eligibility and ranking (Epic 3) need batch year, CGPA, degree, work authorisation and the Match Score threshold. Alerts and every per-day rule need the alert frequency and the Student's time zone (FR-5, NFR4).

**Approach:**
- `profile` owns one `preferences` row per Student, validated in the API and in the database.
- Saving a real change publishes `PreferencesChanged` in the same transaction and starts the priority drain.
- `web` adds Preferences as the last onboarding step, then a finish screen. `/settings` becomes a small hub linking every settings page.

## Boundaries & Constraints

**Always:**
- **Table `app.preferences`:**
  - Key: `owner_id` (PK → accounts ON DELETE CASCADE).
  - Fields (database check constraints in brackets):
    - `role_titles text[]` (≤ 10 items, each 1–80 characters)
    - `locations text[]` (≤ 10 items, each 1–80 characters)
    - `job_types text[]` (each `internship` or `full_time`; may be empty, meaning any)
    - `batch_year int` (NULL or 2000–2100)
    - `cgpa numeric(4,2)` (NULL or 0–10)
    - `degree text` (NULL or a canonical degree id)
    - `degree_other text` (≤ 80 characters; only when `degree = 'other'`)
    - `work_authorisation text` (NULL, `india_authorised` or `needs_sponsorship`)
    - `threshold smallint NOT NULL DEFAULT 60` (CHECK 0–100)
    - `alert_frequency text NOT NULL DEFAULT 'daily'` (`daily`, `weekly` or `off`)
    - `timezone text NOT NULL DEFAULT 'Asia/Kolkata'`
    - `updated_at`
  - RLS via the helper.
- **`core.degrees`** (shared with listings' eligibility evaluator in Epic 2, the same way as `core.skills`) holds the canonical degree ids, labels and aliases:
  - Ids: `btech` (B.Tech / B.E.), `mtech` (M.Tech / M.E.), `bsc`, `msc`, `bca`, `mca`, `bba`, `mba`, `bcom`, `mcom`, `ba`, `ma`, `phd`, `diploma`, `other`.
  - Functions: `normalize(text) -> id | None` and `label(id)`.
  - No other module keeps a degree list.
- **API** (under `/api/v1`, token required):
  - `GET /preferences` returns the stored row, or the defaults with `saved: false`.
  - `PUT /preferences` replaces the whole object (every field present; nullable eligibility fields may be null).
    - Validation:
      - Role titles and locations are trimmed, blanks dropped, and deduplicated ignoring case.
      - `timezone` must be a valid IANA name (`zoneinfo`).
      - `degree_other` only when `degree == "other"`.
      - Every violation is a 422 problem+json with field-level `errors[].loc`. The threshold check is enforced by the database as well.
    - When the effective values differ from what is stored (or nothing was stored yet), it upserts, publishes `PreferencesChanged(owner_id)` (added to `core.events`), commits and starts the bounded `priority` drain after the response. When nothing differs: 200, no write, no event.
  - `GET /degrees` returns `[{id, label}]` for the form.
- **Web:**
  - **Onboarding:** step 4, Preferences, follows Skill Profile. The form shows the defaults pre-filled, and only fields the Student touches need input. "Save and finish" leads to a "You're set up" screen, a placeholder for the feed (Epic 3), noting that the Provider Key comes later and is optional.
  - **`/settings/preferences`** uses the same form.
  - **`/settings`** is a hub linking Consent, Resumes, Skill Profile and Preferences. Every settings page's "Back" link goes to `/settings`.
  - Field errors from the 422 are shown next to their fields.
  - The time zone input is a searchable list of IANA names from `Intl.supportedValuesOf('timeZone')`, defaulting to Asia/Kolkata.

**Never:**
- Provider Key entry (Epic 2). Collection scheduling, scoring or alerts (Epics 2–3).
- `core.clock` per-day helpers; they come with the first per-day rule.
- Per-field PATCH semantics.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Defaults | no row | `GET` returns `saved: false`, threshold 60, `daily`, `Asia/Kolkata`, empty lists, null eligibility | N/A |
| First save | valid full object | 200; one row; `PreferencesChanged` once, before commit | N/A |
| Unchanged save | same values again | 200; no write; no event | N/A |
| Change | threshold 60 → 75 | 200; row updated; event once | N/A |
| Normalisation | `role_titles: [" Backend Intern ", "backend intern", ""]` | stored `["Backend Intern"]` | N/A |
| Threshold bounds | −1 or 101 | 422 with `loc` ending in `threshold`; nothing written | field-level problem |
| DB backstop | direct UPDATE with threshold 101 | rejected by the CHECK constraint | DB error |
| Bad time zone | `"Mars/Base"` | 422 on `timezone` | field-level problem |
| Bad enum values | `job_types: ["contract"]`, `alert_frequency: "hourly"`, `degree: "xyz"`, `work_authorisation: "x"` | 422 on each field | field-level problem |
| CGPA and batch year | cgpa 10.5, or batch_year 1999 | 422 | field-level problem |
| `degree_other` | set while `degree != "other"` | 422 | field-level problem |
| Limits | 11 role titles; a title over 80 characters | 422 | field-level problem |
| Degree normalise | `core.degrees.normalize("B.E.")` | `btech` | N/A |
| Isolation and auth | another Student; no token | only own row; 401 | problem+json |
| Account deleted | account removed | preferences cascade | N/A |

</frozen-after-approval>

## Code Map

Builds on Stories 1.1–1.6. Reuse them; don't restructure.
- `api/app/core/events.py` -- add `PreferencesChanged(owner_id)`, following `SkillProfileChanged`.
- `api/app/core/skills.py` -- pattern for `core/degrees.py` (a pure data module with `normalize` and `label`).
- `api/app/core/errors.py` -- `InvalidInputError` (422 with `errors[].loc`) exists from Story 1.6. Pydantic validation errors already become the 422 problem in `_validation_exception`; prefer Pydantic validators so `loc` is automatic.
- `api/app/modules/profile/{models,repo,service,schemas,router}.py` -- add preferences beside resumes and skill profiles. `router.drain_priority` is the after-response drain. The service never commits; the router does. Story 1.6's `edit_skill_profile` is the pattern for "changed → publish".
- `api/migrations/versions/0007_skill_profiles.py` -- pattern for `0008_preferences.py`; bump `HEAD` in `api/tests/test_migrations.py`.
- `api/tests/test_skill_profile.py` -- fixtures (`student`, the `client` headers) and the event-capture pattern.
- `web/src/components/consent/onboarding-consent-step.tsx` -- holds the onboarding step state machine (steps 1–3). Add step 4 and the finish screen.
- `web/src/app/settings/{consent,resumes,skills}/page.tsx` -- their "Back" links point at `/onboarding`; repoint them to the new `/settings` hub.
- `web/src/components/skills/skill-profile-editor.tsx` -- the load, 401 and polling patterns; `web/src/lib/*.ts` plus `*.test.ts` are the pattern for pure helpers.

## Tasks & Acceptance

**Execution:**
- [x] `api/app/core/degrees.py`, `api/tests/test_degrees.py` -- the canonical degrees, `normalize` (B.E., BE, B.Tech, "Bachelor of Technology" → `btech`; and so on) and `label`.
- [x] `api/app/core/events.py` -- `PreferencesChanged`.
- [x] `api/migrations/versions/0008_preferences.py` -- the table, check constraints and RLS.
- [x] `api/app/modules/profile/*` -- the model, repo, service (`get_preferences`, `save_preferences -> (prefs, changed)`), Pydantic schemas with validators, and the `GET`/`PUT /preferences` and `GET /degrees` routes with the after-response drain.
- [x] `api/tests/test_preferences.py` -- every matrix row.
- [x] `web/**` -- regenerate the client; add the preferences form component, onboarding step 4 and the finish screen, `/settings/preferences`, the `/settings` hub, repointed Back links, and `node --test` tests for the pure helpers (form ↔ payload mapping, error-to-field mapping).

**Acceptance Criteria:**
- Given a new Student on the local stack, when they go through consent, Resume upload, Skill Profile and Preferences accepting the defaults, then they reach "You're set up" with nothing required beyond the Resume file. That is the SM-6 path: no Provider Key, no mandatory fields.
- Given `PreferencesChanged` is published, when a subscriber runs, then it sees the new row in the same session, before commit.

## Design Notes

- **Defaults:**
  - **Threshold 60:** the PRD's example Student uses 70. A slightly lower default shows more matches while the Student learns the score.
  - **Alert frequency:** `daily`, `weekly` or `off`. Digests are batched (AD-16), so there is no "instant".
  - **Work authorisation:** two values for the India-only launch (OQ-4). NULL means not stated, which Epic 3 treats as Unknown eligibility.
- **PUT, not PATCH.** The form always has the whole object, and a full replace keeps the change check simple. Comparing effective values avoids needless events, and needless events would cause needless re-scoring in Epic 3.

## Implementation Notes

## Spec Change Log

## Review Triage Log

Review pass 1 (blind, edge-case, verification-gap):

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | Time-zone validation depends on `tzdata`, which is only pulled in by other packages; without it `available_timezones()` is empty and every PUT gives 422 | medium | `uv.lock` lists `tzdata` only as a transitive dependency (one edge `win32`-only); `pyproject.toml` doesn't declare it | patch |
| 2 | Rejecting Unicode category `Cf` blocks ZWJ/ZWNJ (U+200D/U+200C), which Indic scripts need, so Hindi or Malayalam locations get 422 | medium | `_has_control_chars` rejects `Cf`; launch is India-only | patch |
| 3 | Concurrent identical first saves are untested; removing the advisory lock would let a double `PreferencesChanged` ship | medium | Gap filed pre-verified; no `gather` in `test_preferences.py` | patch |
| 4 | Common Indian degrees are missing (B.Arch, B.Des, B.Pharm/M.Pharm, MBBS, BDS, LLB/LLM, PGDM, integrated/dual), so they fall to `other` and eligibility is Unknown | medium | `core.degrees` list; the ids are frozen in a CHECK, but 0008 isn't deployed yet, so adding them now is free | patch |
| 5 | Lax numeric parsing: `threshold: true` is stored as 1; string numbers are accepted | low | Pydantic lax mode on `PreferencesIn`; direct `strict` fix | patch |
| 6 | Editor: `set()` uses a stale render closure, and field errors stay after the field is fixed | low | `setForm({...values})`; errors only cleared on submit; direct fix | patch |
| 7 | The migration docstring claims every API rule is a CHECK, but it isn't (timezone validity, dedupe, trimming, control characters); a blank `degree_other` passes the DB | low | Narrow the docstring, and add a `btrim` non-blank check for `degree_other` | patch |
| 8 | The client counts item length in UTF-16 units, the server in code points, so emoji-heavy items are rejected only on the client | low | `item.length` vs `char_length`; direct `[...item].length` | patch |
| 9 | `core.degrees.normalize` misses unspaced suffixes ("B.Tech-CSE", "M.Sc-Physics") | low | `_QUALIFIER` needs spaces around the dash; direct regex fix plus tests | patch |
| 10 | `Handler =Callable` formatting slip in `core/events.py` | low | Line 64; direct | patch |
| 11 | No component or e2e test for `PreferencesEditor`, onboarding step 4, or the settings Back links | medium | No harness in `web`; same deferral as Stories 1.4–1.6 | defer |
| 12 | Client `toLowerCase` vs server `casefold` dedupe differ (ß) | low | The server is the authority and the form resyncs from the response; rare | reject |
| 13 | A 422 "Entry N" index refers to the cleaned list, not the textarea line | low | Minor; the message names the field | reject |
| 14 | Unsaved edits are lost on "Back to skills"; onboarding progress isn't persisted on reload; the settings hub's exit goes to onboarding | low | Placeholder navigation until the signed-in home (Epic 3 feed) exists | reject |
| 15 | Every changing save drains even though nothing is queued | low | Cheap no-op drain; the spec mandates the priority path and Epic 3 adds the subscriber | reject |
| 16 | Browser and server time-zone lists differ (`Factory`, legacy aliases) | low | Edge names; a 422 explains it | reject |
| 17 | `GET /degrees` has no caching headers | low | Tiny static payload | reject |
| 18 | CGPA is rounded on the server only | low | Two decimals is conventional; the form shows the stored value | reject |
| 19 | Ambiguous "ME"/"MS" aliases | low | Deliberate: Indian usage (M.E. → mtech, M.S. → msc) | reject |
| 20 | Missing `updated_at`-advances and 64-character timezone tests | low | Trivial behaviour already covered by constraints | reject |
| 21 | The generated client and spec weren't in the review diff | false | The generated client is checked by the CI `gen:api` diff job; the spec isn't code | reject |

## Verification

**Commands:**
- `cd api && uv run pytest` -- all pass on `postgres:17` plus the roles SQL.
- `cd api && uv run lint-imports && uv run python scripts/check_exposed_schema.py` -- pass.
- `cd web && npm run lint && npm run typecheck && npm test && npm run build && npm run gen:api` -- pass, with no client diff after regeneration.

**Manual checks:**
- With the Supabase CLI stack, `uvicorn` and `next dev`, follow the first acceptance criterion.
