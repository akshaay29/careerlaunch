---
status: final
stepsCompleted: [1, 2, 3, 4]
inputDocuments:
  - _bmad-output/specs/spec-careerlaunch/SPEC.md
  - _bmad-output/specs/spec-careerlaunch/acceptance-criteria.md
  - _bmad-output/specs/spec-careerlaunch/glossary.md
  - _bmad-output/specs/spec-careerlaunch/user-journeys.md
  - _bmad-output/planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/prds/prd-Careerlaunch-2026-10-24/prd.md
scope: 'Phase 1 (MVP, core loop) only. Phase 2 capabilities (CAP-11, 12, 14, 15, 19-25, 27, 29) are excluded.'
---

# CareerLaunch - Epic Breakdown

## Overview

This document breaks the **Phase 1 MVP** of CareerLaunch into epics and stories. It draws on the spec (`SPEC.md` and its acceptance criteria) and the architecture spine. Requirement IDs match the spec: FR-N = CAP-N. There is no UX design document, so stories specify screens by behaviour and states.

## Requirements Inventory

### Functional Requirements

FR1: A visitor registers and logs in with email and password or Google. Each account has exactly one role, Student or Admin, and role checks are enforced on the server. Students access only their own records plus the shared Listing pool. Admin-only routes return 403 to Students.
FR2: Before any provider run or pool contribution, a Student gives timestamped plain-language consent. It states that runs execute and are billed under their own Apify account and that LinkedIn's terms restrict scraping. A Student can revoke consent (which cancels pending work for that purpose) and delete their account and all data, within 30 days under India's DPDP Act.
FR3: A Student uploads PDF or DOCX Resumes of 5 MB or less, keeps several versions, and marks one active. Wrong types or sizes are rejected with a clear message. Changing the active Resume re-scores the Student's Listings.
FR4: Skills, education and experience are extracted from the active Resume into an editable Skill Profile. If no text can be extracted, the Student is told and can enter skills manually. Student edits persist across re-uploads unless the Student resets them.
FR5: A Student sets target role titles, locations, job types (internship, full-time), Eligibility attributes (batch year, CGPA, degree, work authorisation), a Match Score threshold (0–100, else rejected), an alert frequency, and a time zone (default Asia/Kolkata). A change updates the feed and the scope of the next collection run.
FR6: For each Student with a valid Provider Key and consent, scheduled collection runs scoped to their Preferences execute under their Apify account. Each run stores title, company, location, job type, posted date, deadline if present, and source URL. Rules:
- A duplicate Listing is stored once.
- A failed run is logged, retried with backoff, and visible to the owner.
- Per-run and monthly spend caps stop collection.
- A Student without a key still sees the shared pool.
- The pool is seeded before launch under the owner's account.
- Saving a valid key triggers a first run.
FR7: All Source access goes through a Source Adapter. A test-double Source supplies Listings end to end with no change to scoring or Tracker code. A failed or disabled Source stops only its own collection.
FR8: Rules for the Listing pool:
- Listings past their deadline, or no longer found at the Source, are marked expired and hidden from the default feed, then deleted after a 30-day grace period.
- The pool is capped at about 30,000 active Listings. The oldest, least relevant Listings with no saved Application are evicted first.
- Pool Listings store no description. Skills, criteria snippets and search keywords are extracted at ingestion, and the text is discarded.
- An Application keeps a trimmed snapshot (title, company, link, deadline).
- Each Listing shows its source with a not-affiliated notice and links to the source to apply. The owner can take one down.
- Listings enter the shared pool only with the collecting Student's pool-contribution consent.
FR9: The Match Score (0–100) for each Student–Listing pair is deterministic: weighted skill overlap plus text similarity, with no embeddings or LLM. Identical inputs give identical scores. Every score opens to its matched skills, missing skills and per-component points. Scores refresh within 5 minutes of a change to the active Resume, Skill Profile or Preferences.
FR10: The feed lists Listings filtered by Preferences, sorted by Match Score descending with ties newest first. Expired Listings are hidden. Below-threshold Listings are hidden by default and shown with one toggle.
FR13: Keyword search covers title, company, extracted skills and description keywords, ranked by relevance, with typo tolerance. "backend intern" returns matching Listings. Typical searches take under 500 ms at about 30,000 Listings.
FR16: A Student saves a Listing, marks it Applied, and moves it through Saved, Applied, Interview, Offer, Rejected and Withdrawn, with notes, dates and the Resume version used. Each Status change is timestamped in a viewable history.
FR17: The Tracker lists all Applications with combined filters by Status, company and date, shows the result count, and shows upcoming deadlines. Applications whose Listing left the pool still appear, from the snapshot.
FR18: A Student adds a Listing manually by URL or details. The URL is stored, never fetched by the server. A manual Listing is visible only to its owner, never enters the pool, and is scored and tracked like any other Listing.
FR26: Resumes and Documents (cover letters, other files) are private and downloadable only by their owner through authenticated, expiring links. Allowed types are PDF, DOCX and plain text up to 5 MB, type- and size-checked before storage. Deleting a file or account removes the stored file.
FR28: A Student adds a scoped Apify token in settings, with short instructions for creating one (Run permission on approved actors only, with an expiry). The key is:
- validated against Apify on save, with an invalid key rejected clearly;
- stored encrypted, never shown again or logged;
- replaceable or deletable, with deletion stopping all runs.

The Student sees estimated and actual usage per run and can set spend caps. If Apify rejects a run (invalid key, exhausted credit, blocked account), the Student sees the reason, collection pauses for them, and the shared pool keeps serving them.

### NonFunctional Requirements

NFR1: Feed pages load in under 2 s.
NFR2: Full-text search returns in under 500 ms at about 30,000 active Listings.
NFR3: Scores refresh within 5 minutes of a Resume, Skill Profile or Preferences change.
NFR4: Time to value: a median new Student reaches a first ranked feed within 10 minutes of effort (SM-6), with no Provider Key required.
NFR5: Reliability: ingestion jobs are idempotent and retry safely, and one failing Source doesn't take down the app.
NFR6: Hosting runs on free tiers only and tolerates sleeping instances, no always-on worker, and small quotas.
NFR7: Storage: the database stays within about 0.5 GB; files go to object storage; the owner is alerted at 80% of the quota.
NFR8: Security: per-Student isolation is enforced on the server. Provider Keys are encrypted at rest, never logged and never sent to the client.
NFR9: Observability: every ingestion run and score computation is logged and visible to the owner.
NFR10: Data model: a relational, normalised schema with DB-enforced foreign keys and constraints, Listing dedup by constraint, and Application Status history.
NFR11: Privacy and compliance: India only under the DPDP Act, with minimal data and deletion within 30 days.
NFR12: Tone: the feed is neutral and factual, with no nudges to apply more.
NFR13: Responsive web for laptop and mobile browsers, in English only.

### Additional Requirements

From the architecture spine (phase 1 subset; AD IDs cited).

- **Scaffold (Epic 1, Story 1). There is no starter repo.** Set up a monorepo:
  - `api/`: FastAPI on Python 3.14 with async SQLAlchemy 2.1 + psycopg 3, Alembic, Procrastinate 3.10, and pydantic-settings.
  - `web/`: Next.js 16.4.5 from `create-next-app`, with TypeScript 5.9.3 and Node 26 LTS.
  - `supabase/`: Supabase CLI config for local development.
  - `.github/workflows/`: CI.

  Pin the versions listed in the spine's Stack table.
- **Data access:** FastAPI is the only component that touches Postgres and Storage. `web` calls the API only through the generated OpenAPI client and uses Supabase only for sign-in (AD-1, AD-14).
- **Database exposure:** all tables live in schema `app`, which the Supabase Data API doesn't expose, with RLS on and no grants to `anon`/`authenticated`. A CI check fails if a table appears in an exposed schema (AD-1).
- **Module structure:** a modular monolith with `accounts`, `profile`, `listings`, `matching`, `tracker` (plus `outreach` and `admin` reserved). Each module has router / service / repo / tasks layers and owns its tables. The module order `accounts ← profile ← listings ← matching ← tracker` is enforced by import-linter. Reverse-direction effects use same-transaction domain events, and only routers and tasks commit (AD-2, AD-20).
- **Ports and adapters:** `SourceAdapter` (yielding a typed `ListingDraft`), `ProviderRunner`, `FileStore` and `AuthAdmin`, each with an in-memory fake. Vendor SDKs may be imported only in `adapters/` (AD-3).
- **Durable job queue (AD-4):**
  - Jobs are enqueued through `core.jobs.enqueue(session, …)` into `job_outbox` in the same transaction.
  - The drain endpoint `/internal/jobs/drain` is called by `pg_cron` + `pg_net` every 5 minutes (timeout of at least 90 s), with the secret held in Supabase Vault.
  - A GitHub Actions health check runs every 15 minutes and drains as a backup.
  - Recurring work runs from a `schedules` table and a `scheduler_tick`.
  - Each drain recovers stalled jobs and runs under a 4-minute cap.
  - A priority queue plus an in-process post-commit drain serves Student-triggered recomputes.
  - Job arguments are ids only, and key format is `<module>.<task>:<entity_type>:<id>[:<scope>]`.
- **Identity:** `accounts.id` equals the Supabase Auth uid, and the JWT is verified on every request. The role comes from the DB, and admins are created only by a CLI script. Every Student-owned table has `owner_id` (AD-5).
- **Secrets:** Provider Keys are encrypted with AES-GCM using additional authenticated data (AAD) and a key-version prefix, under a master key held in the Render env. APIs expose only `has_key` and a last-4 hint (AD-6).
- **Listing pool (AD-7):**
  - Unique `(source, source_job_id)` with upsert.
  - Manual Listings use `source='manual'` + `owner_id`.
  - Pool rows are minimal (no description), keeping a `search_doc` tsvector and `collected_by`.
  - `pin`/`unpin` from tracker, `set_relevance` from matching, and explicit `ON DELETE` actions.
  - An Application snapshot, and `effective_deadline`.
- **Skill vocabulary:** `core.skills`, with slug `skill_id`s, `vocab_version`, and Student edits stored as added/removed sets (AD-8).
- **Match Score:** a pure `score()` function with a typed `ScoreBreakdown`, plus a `match_score_missing_skills` table. The candidate set is capped at 500 per Student, with on-request scoring outside it. Recompute is triggered only by `mark_stale` (AD-9).
- **Consent:** a single gate, `accounts.service.require_consent(owner_id, purpose)`, with phase 1 purposes `provider_runs` and `pool_contribution` (AD-11).
- **Apify runner (AD-12):**
  - The single path is `listings.service.start_provider_run`.
  - Before each run it checks caps and status.
  - Each run gets a max charge and max items, using an actor allowlist and the `x-apify-integration-platform` header.
  - Runs start asynchronously and are collected by a poll job; there is no blocking wait.
  - `provider_status` is tracked per Student (paused, or exhausted_until).
- **Schema ownership:** Alembic owns all DDL, including the Procrastinate schema, the `pg_cron` schedule, RLS and grants. Prod migrations run from GitHub Actions before the Render deploy hook, as expand-then-contract (AD-13).
- **API conventions:** RFC 9457 problem+json errors, cursor pagination, and route classes `/api/v1`, `/api/v1/admin`, `/api/v1/public`, `/internal`. Bearer JWT is sent from `web` via `@supabase/ssr`, and CORS allows only the Vercel origin and localhost (AD-14).
- **Retention and deletion jobs (AD-17):** account deletion cascades, then deletes the Supabase Auth user and purges files. Finished jobs and outbox rows are purged after 7 days and `ops_events` after 90 days. An 80% quota alert runs as a job.
- **Ops log:** `core.ops.record_event()` writes to `ops_events` and `admin_audit` (AD-18).
- **Conventions:**
  - UUID v7 generated in the app (`accounts.id` is the exception).
  - UTC `timestamptz`, with `core.clock.local_day`.
  - Session pooler (port 5432) only.
  - Structured JSON logs with no secrets.
  - Uploads are checked by magic bytes and size, and parsing runs in a subprocess job with memory and time limits.
  - Signed URLs expire in 5 minutes or less.
  - Untrusted text is rendered as plain text.
- **CI from day one:** pytest, import-linter, the exposed-schema check, and the OpenAPI client diff.
- **Deployment:** one Render Free service, one Vercel Hobby project, one Supabase Free project. Environments are local and prod.

### UX Design Requirements

None. There is no UX design document for phase 1. Screen behaviour is defined in each story's acceptance criteria.

### FR Coverage Map

FR1: Epic 1 - Sign-up, login, roles, server-side checks
FR2: Epic 1 - Consent (provider runs, pool contribution), revoke, account deletion
FR3: Epic 1 - Resume upload, versions, active Resume
FR4: Epic 1 - Skill Profile extraction and edits
FR5: Epic 1 - Preferences
FR26: Epic 1 - Private file storage and expiring downloads
FR6: Epic 2 - Scheduled LinkedIn ingestion under the Student's key
FR7: Epic 2 - Source Adapter
FR8: Epic 2 - Minimal pool content, expiry, cap, attribution, takedown
FR13: Epic 2 - Full-text search with typo tolerance
FR28: Epic 2 - Provider Key setup, usage and caps
FR9: Epic 3 - Deterministic, explainable Match Score and refresh
FR10: Epic 3 - Ranked feed with threshold toggle
FR16: Epic 4 - Applications, Status values and history
FR17: Epic 4 - Tracker view and filters
FR18: Epic 4 - Manual Listings

## Epic List

### Epic 1: Students sign up and build their profile
A Student creates an account, gives consent, uploads a Resume, confirms the extracted skills, sets Preferences, manages their files, and can delete everything. Includes the project scaffold, CI and the durable job queue.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR26

### Epic 2: Fresh LinkedIn jobs in a searchable shared pool
A Student adds a scoped Apify key, and Listings matching their Preferences arrive in the shared pool, deduplicated and expiring on schedule. Any Student can browse and search the pool.
**FRs covered:** FR6, FR7, FR8, FR13, FR28

### Epic 3: A ranked, explained feed
Every Student sees pool Listings ranked by Match Score, can open the breakdown, and sees scores refresh within 5 minutes of a change.
**FRs covered:** FR9, FR10

### Epic 4: Track every application
A Student saves Listings, moves them through Status values with history, adds external Listings, and manages everything in a filterable Tracker. Ends with launch hardening.
**FRs covered:** FR16, FR17, FR18

## Epic 1: Students sign up and build their profile

A Student creates an account, gives consent, uploads a Resume, confirms the extracted skills, sets Preferences, manages their files, and can delete everything. Includes the project scaffold, CI and the durable job queue.

### Story 1.1: Project scaffold, CI and deploy pipeline

*Implements: Architecture: AD-1, AD-3, AD-13, AD-14, AD-20; NFR6, NFR10*

As the developer,
I want a running, deployable skeleton that already follows the architecture rules,
So that every later story lands in the right place and drift is caught by CI.

**Acceptance Criteria:**

**Given** a fresh clone
**When** I follow the README
**Then** `api/` (FastAPI, Python 3.14, async SQLAlchemy 2.1 + psycopg 3, Alembic, Procrastinate 3.10, pydantic-settings), `web/` (Next.js 16.4.5, TypeScript 5.9.3, Node 26) and `supabase/` (CLI config) start locally against the Supabase CLI stack
**And** versions are pinned exactly as in the spine's Stack table
**And** `web` uses Tailwind CSS with shadcn/ui components as its UI styling standard (decided at sprint planning, 2026-11-07).

**Given** the api project
**When** I inspect it
**Then** it has `core/`, `ports/`, `adapters/` and `modules/{accounts,profile,listings,matching,tracker}`, each module with router/service/repo/models/schemas/tasks files
**And** `GET /api/v1/health` returns 200
**And** errors use RFC 9457 problem+json from one handler.

**Given** the first Alembic migration
**When** it runs
**Then** it creates schema `app`, sets `search_path=app`, enables RLS and revokes `anon`/`authenticated` grants by default
**And** no table exists in an exposed schema.

**Given** a push to GitHub
**When** CI runs
**Then** pytest runs against local Postgres
**And** import-linter enforces the layer rules, module order (accounts ← profile ← listings ← matching ← tracker) and the vendor-SDK-only-in-adapters rule
**And** the exposed-schema check passes
**And** the OpenAPI → `web/src/api/` client diff check passes.

**Given** a release
**When** the deploy workflow runs
**Then** `alembic upgrade head` runs through the Supabase session pooler (port 5432) before the Render deploy hook fires
**And** Vercel deploys `web`
**And** `.env.example` lists every key and where it lives.

### Story 1.2: Background jobs that survive a sleeping server

*Implements: Architecture: AD-4, AD-18; NFR5, NFR6, NFR9*

As the developer,
I want a durable job queue drained on a schedule,
So that Resume extraction and later ingestion and scoring run reliably on free hosting.

**Acceptance Criteria:**

**Given** a service in an open transaction
**When** it calls `core.jobs.enqueue(session, task, key, run_at=None, queue='default', **ids)`
**Then** a `job_outbox` row is written in that transaction
**And** a rolled-back transaction leaves no job (covered by a test).

**Given** a migration
**When** it runs
**Then** the Procrastinate schema is installed in `app` via Alembic
**And** a `schedules(kind, owner_id, next_run_at, paused)` table exists.

**Given** `POST /internal/jobs/drain` with a valid secret (constant-time compare, two values accepted during rotation)
**When** it is called
**Then** it returns 202
**And** in the background it flushes the outbox into Procrastinate, runs `scheduler_tick`, re-queues stalled jobs, and runs the worker with `wait=False` under an asyncio timeout of at most 4 minutes, `priority` queue first
**And** it writes `last_drain_at`.

**Given** a wrong or missing secret
**When** the drain is called
**Then** it returns 401 and runs nothing.

**Given** the `pg_cron` migration
**When** it is applied
**Then** `pg_cron` calls the drain every 5 minutes via `pg_net`, with `timeout_milliseconds` of at least 90000 and the secret read from Supabase Vault.

**Given** a GitHub Actions workflow every 15 minutes
**When** `GET /internal/health` finds `last_drain_at` older than 15 minutes
**Then** it returns 503, the workflow fails (alerting the owner), and the workflow triggers a backup drain.

**Given** any job
**When** it is enqueued
**Then** its arguments contain ids only
**And** its key follows `<module>.<task>:<entity_type>:<id>[:<scope>]`
**And** `core.ops.record_event()` logs its run to `ops_events` (ids and counts only); this story's migration creates the append-only `ops_events` table.

### Story 1.3: Sign up, log in and roles

*Implements: FR1; AD-5, AD-14*

As a visitor,
I want to create an account with email and password or Google and log in,
So that I have a private space on CareerLaunch.

**Acceptance Criteria:**

**Given** the sign-up page
**When** I register with email and password, or with Google, through Supabase Auth
**Then** an `accounts` row is created with `id` equal to the Supabase Auth uid and role `student`
**And** I land on onboarding.

**Given** I'm logged in
**When** `web` calls the API
**Then** it sends `Authorization: Bearer <access_token>` from the `@supabase/ssr` cookie session
**And** the API verifies signature, `aud` and `exp`, then loads the role from `accounts`, never from client claims.

**Given** an invalid or expired token
**When** any `/api/v1/*` route is called
**Then** the API returns 401 problem+json.

**Given** a Student
**When** they call any `/api/v1/admin/*` route
**Then** the API returns 403.

**Given** the owner
**When** they run the CLI script `promote-admin <email>`
**Then** that account's role becomes `admin`
**And** the change is written to `admin_audit` (insert-only table created by this story's migration)
**And** no API endpoint can grant the admin role.

**Given** CORS
**When** a request comes from an origin other than the Vercel production origin or localhost
**Then** it is rejected.

### Story 1.4: Consent for provider runs and the shared pool

*Implements: FR2; AD-11*

As a Student,
I want to see in plain language what CareerLaunch collects and does on my behalf, and choose to consent,
So that nothing runs under my Apify account or enters the shared pool without my agreement.

**Acceptance Criteria:**

**Given** onboarding or settings
**When** I open the consent screen
**Then** it explains, in plain language:
- **Provider runs:** scraping and lookups run under my own Apify account and are charged to it by Apify, and LinkedIn's terms restrict scraping.
- **Pool contribution:** Listings collected under my key are shared with other Students whose Preferences match.
- **Rights:** my rights under India's DPDP Act, including deletion within 30 days.

**When** I consent to a purpose
**Then** a versioned, timestamped consent record is stored for it.

**Given** any service needs a purpose
**When** it calls `accounts.service.require_consent(owner_id, purpose)` without a current consent
**Then** it raises a typed error mapped to 403 problem+json with type `consent-required`.

**Given** I revoke a consent
**When** I confirm
**Then** the revocation is timestamped
**And** a `ConsentRevoked(owner_id, purpose)` event is published in the same transaction, for later epics to subscribe to.

### Story 1.5: Upload and manage Resume versions

*Implements: FR3, FR26*

As a Student,
I want to upload my Resume as PDF or DOCX, keep versions and pick the active one,
So that scoring always uses the Resume I choose.

**Acceptance Criteria:**

**Given** I upload a PDF or DOCX of 5 MB or less
**When** its magic bytes and size are checked by `profile.service`
**Then** the file is stored through the `FileStore` port in the private Supabase Storage bucket at `<owner_id>/resume/<uuid>`
**And** a `resumes` row (`owner_id`, cascade from accounts) is created.

**Given** a file of another type, or over 5 MB
**When** I upload it
**Then** it is rejected with a clear message
**And** nothing is stored.

**Given** I have several Resumes
**When** I mark one active
**Then** a partial unique index keeps exactly one active Resume per Student
**And** a `ResumeActivated` event is published in the same transaction.

**Given** my Resume list
**When** I download a version
**Then** the API issues a signed URL that expires in 5 minutes or less, with `Content-Disposition: attachment`, only to me.

**Given** another Student's Resume id
**When** I request it
**Then** the API returns 404.

**Given** I delete a version
**When** I confirm
**Then** the row and the stored file are removed
**And** deleting the active Resume leaves no Resume active until I choose one.

### Story 1.6: Skill Profile extracted from my Resume

*Implements: FR4; AD-8*

As a Student,
I want my skills, education and experience pulled from my active Resume, and to correct them,
So that my matches reflect what I can actually do.

**Acceptance Criteria:**

**Given** `ResumeActivated`
**When** the event is handled
**Then** a `profile.extract_resume:resume:<id>` job is enqueued on the `priority` queue.

**Given** the extraction job
**When** it runs
**Then** pdfplumber or python-docx parses the file in a subprocess with memory and time limits
**And** skills are normalised through `core.skills.normalize()` to slug `skill_id`s, stored with `vocab_version`
**And** education and experience are stored in the Skill Profile.

**Given** a Resume with no extractable text (for example a scanned image)
**When** extraction finishes
**Then** I see "We couldn't read text from this Resume. Upload a text-based PDF or add your skills manually."
**And** I can add skills manually.

**Given** a parser crash, or a limit exceeded
**When** the job fails
**Then** I see the same message
**And** the API stays healthy.

**Given** the Skill Profile screen
**When** I add or remove skills
**Then** my edits are stored as `added` and `removed` sets applied over the extracted set
**And** `SkillProfileChanged` is published.

**Given** I re-upload or switch Resumes
**When** extraction re-runs
**Then** my added and removed edits still apply, unless I choose "Reset to extracted".

**Given** `core.skills`
**When** I inspect it
**Then** it holds a versioned canonical vocabulary with aliases (for example "JS", "javascript" and "ES6" map to `javascript`), and no module keeps its own alias table.

### Story 1.7: Set my Preferences

*Implements: FR5; NFR4*

As a Student,
I want to set target roles, locations, job types, my eligibility attributes, my score threshold, alert frequency and time zone,
So that collection and ranking focus on what I want.

**Acceptance Criteria:**

**Given** the Preferences form
**When** I save role titles, locations, job types (internship, full-time), batch year, CGPA (10-point), degree, work authorisation, threshold, alert frequency and time zone
**Then** one `preferences` row per Student is stored (`owner_id`, cascade)
**And** the time zone defaults to Asia/Kolkata.

**Given** a threshold below 0 or above 100
**When** I save
**Then** the API rejects it with a field-level problem+json error, enforced by a DB check constraint too.

**Given** a valid change
**When** I save
**Then** `PreferencesChanged` is published in the same transaction.

**Given** onboarding
**When** I finish Resume upload, Skill Profile and Preferences
**Then** the flow takes about 10 minutes or less of effort (SM-6)
**And** a Provider Key is optional at this point.

### Story 1.8: Store and download my other Documents

*Implements: FR26*

As a Student,
I want to keep cover letters and other files privately alongside my Resumes,
So that everything for my applications is in one place.

**Acceptance Criteria:**

**Given** I upload a PDF, DOCX or plain-text file of 5 MB or less
**When** its magic bytes and size pass
**Then** it is stored at `<owner_id>/document/<uuid>` with a `documents` row (`owner_id`, cascade).

**Given** another file type, or over 5 MB
**When** I upload it
**Then** it is rejected with a clear message.

**Given** my Documents list
**When** I download one
**Then** I get a signed URL that expires in 5 minutes or less, as an attachment, and no other user can obtain it.

**Given** I delete a Document
**When** I confirm
**Then** the row and the stored file are removed.

### Story 1.9: Delete my account and all my data

*Implements: FR2; AD-17; NFR11*

As a Student,
I want to delete my account and everything CareerLaunch holds about me,
So that I can leave completely, as the DPDP Act allows.

**Acceptance Criteria:**

**Given** settings
**When** I choose "Delete my account" and confirm by typing my email
**Then** in one transaction an `AccountDeleting` event is published, and the `accounts` row is deleted with every Student-owned row cascading
**And** `purge_files` and `AuthAdmin.delete_user` jobs are enqueued.

**Given** the purge jobs
**When** they run
**Then** every Storage object under `<owner_id>/` and the Supabase Auth user are deleted, idempotently
**And** a count-only `ops_events` row records it
**And** everything completes well within 30 days.

**Given** global rows that reference me (`ops_events`, `admin_audit`)
**When** I am deleted
**Then** their Student foreign keys are SET NULL
**And** they hold no personal data.

**Given** any table added in a later story with `owner_id`
**When** its migration is written
**Then** it declares `ON DELETE CASCADE` from `accounts` (this convention is checked in code review).

## Epic 2: Fresh LinkedIn jobs in a searchable shared pool

A Student adds a scoped Apify key, and Listings matching their Preferences arrive in the shared pool, deduplicated and expiring on schedule. Any Student can browse and search the pool.

### Story 2.1: Shared Listing pool, Source Adapter and browsing

*Implements: FR7, FR8; AD-3, AD-7*

As a Student,
I want to browse job and internship Listings from the shared pool,
So that I see real openings even before I add an Apify key.

**Acceptance Criteria:**

**Given** the ports
**When** I inspect `ports/`
**Then** `SourceAdapter` yields a Pydantic `ListingDraft` (`source, source_job_id, url, title, company_raw, location_raw, job_type, description, posted_at: datetime|None, deadline: date|None`)
**And** an in-memory fake `SourceAdapter` exists in `tests/fakes/`.

**Given** `listings.service.ingest_draft(draft, collected_by, contributes_to_pool)`
**When** it processes a draft
**Then** it upserts on the unique constraint `(source, source_job_id)`, so a duplicate Listing is stored once
**And** it extracts skills through `core.skills.normalize()` with `vocab_version`
**And** it builds the `search_doc` tsvector from title, company, skills and description
**And** it stores only title, company, location, job type, dates, source URL, skills, `search_doc`, `first_seen_at` and `collected_by` (SET NULL on account deletion)
**And** it discards the description text without persisting it
**And** it publishes `ListingsIngested` in the same transaction.

**Given** the collector has no `pool_contribution` consent
**When** a Listing is ingested
**Then** its `visibility_owner_id` is the collector, so only they see it
**And** if a consenting Student later collects the same Listing, it becomes visible to the pool (`visibility_owner_id` NULL).

**Given** the fake Source supplies 20 Listings end to end
**When** they are ingested
**Then** they appear in the browse list, newest first with cursor pagination, without any change to tracker or matching code (FR7).

**Given** a Listing card or detail
**When** it is shown
**Then** it displays the source ("via LinkedIn") and a not-affiliated notice, the "Apply" button opens the source URL, and all scraped text is rendered as plain text.

**Given** a Source row
**When** its `enabled` flag is false
**Then** collection from that Source stops and other Sources and existing Listings are unaffected.

### Story 2.2: Add my Apify Provider Key

*Implements: FR28; AD-6, AD-12*

As a Student,
I want to add a scoped Apify token with clear setup steps,
So that fresh Listings can be collected for me under my own free Apify credit.

**Acceptance Criteria:**

**Given** settings
**When** I open "Provider Key"
**Then** I see short steps:
1. Create a free Apify account.
2. Create a scoped token with Run permission on the approved actors only, restricted access, and an expiry.
3. Paste it here.

**And** the steps note that I have USD 5 of free monthly credit.

**Given** I paste a token
**When** I save
**Then** the API validates it with `GET /v2/users/me` through the `ProviderRunner` adapter, sending the `x-apify-integration-platform` header
**And** an invalid token is rejected with a clear message.

**Given** a valid token
**When** it is stored
**Then** it is encrypted with AES-GCM via `core.crypto` (additional authenticated data `table:row_id:owner_id`, key-version prefix, master key from env)
**And** the API returns only `has_key` and a last-4 hint, never the token
**And** log formatters redact it.

**Given** a stored key
**When** I replace or delete it
**Then** the new ciphertext replaces the old one, or deletion removes it
**And** my collection schedules are paused and no further runs start under it.

**Given** I have not given `provider_runs` consent
**When** I try to save a key
**Then** I am sent to the consent screen first.

### Story 2.3: Collect LinkedIn Listings on a schedule

*Implements: FR6; AD-4, AD-12*

As a Student with a Provider Key,
I want Listings matching my Preferences collected regularly under my Apify account,
So that my pool stays fresh without me searching LinkedIn.

**Acceptance Criteria:**

**Given** I save a valid key with `provider_runs` consent
**When** it is stored
**Then** a `schedules` row (kind `ingest`, `owner_id`) is created
**And** a first collection run is enqueued at once, so fresh Listings arrive within the hour.

**Given** `scheduler_tick` finds my ingest schedule due
**When** it enqueues `listings.collect:student:<id>:<local_date>`
**Then** the job calls `listings.service.start_provider_run(owner_id, 'jobs', params)` with my role titles, locations and job types.

**Given** `start_provider_run`
**When** it runs
**Then** it re-checks `require_consent(provider_runs)`, my `provider_status` (a per-Student status owned by `accounts`, created by this story with default `active`), and that the Source and provider are enabled
**And** it commits a `provider_runs` row as `starting`
**And** it starts an allowlisted Apify LinkedIn jobs actor asynchronously (allowlist: `curious_coder/linkedin-jobs-scraper` primary, `harvestapi/linkedin-job-search` fallback with its cookie field never set; confirm each still exists and check its input format when starting this story), with max charge and max items set and no cookie or login inputs, then stores the Apify run id
**And** it never starts a second run for a row that already has a run id.

**Given** a started run
**When** `listings.poll_provider_run:provider_run:<id>` runs (rescheduling itself with backoff)
**Then** on success it maps actor output to `ListingDraft`s via the Apify `SourceAdapter` and calls `ingest_draft` for each
**And** it records the actual cost and outcome on `provider_runs`
**And** no task ever waits synchronously on Apify.

**Given** a failed run
**When** polling sees the failure
**Then** it is logged to `ops_events` and retried with backoff
**And** the owner can see it in the database.

**Given** before launch
**When** the owner runs the seed command
**Then** a collection run executes under the owner's key from env
**And** the pool is non-empty for the first Students.

### Story 2.4: See my usage, set spend caps, and pause on failure

*Implements: FR28, FR6*

As a Student,
I want to see what each run costs and cap my spend,
So that collection never surprises me or drains my free credit.

**Acceptance Criteria:**

**Given** my Provider Key settings
**When** I view usage
**Then** I see each run's estimated and actual cost, item count, and outcome.

**Given** I set a per-run cap and a monthly cap
**When** a run would exceed either, or the platform-wide cap from env
**Then** `start_provider_run` refuses to start it
**And** it records the refusal reason, which I can see.

**Given** Apify rejects a run as invalid key, blocked account or exhausted credit
**When** polling or starting sees the error
**Then** my `provider_status` becomes `paused` with the reason shown to me
**And** exhausted credit sets `exhausted_until` to the next billing cycle
**And** collection stops for me while the shared pool keeps serving me.

**Given** a paused status
**When** I fix the key or the cycle resets
**Then** collection resumes on the next scheduler tick.

### Story 2.5: Expire, evict and take down Listings

*Implements: FR8; NFR7*

As a Student,
I want stale Listings gone and the pool kept healthy,
So that I only see live openings and the free database never fills up.

**Acceptance Criteria:**

**Given** a Listing past its deadline, or not seen at the Source for a configured number of runs
**When** the daily `listings.expire` job runs
**Then** the Listing is marked expired and hidden from the default browse view.

**Given** expired Listings older than 30 days
**When** the purge job runs
**Then** they are deleted, and the delete actions declared on every foreign key into `listings` apply.

**Given** this story's migration
**When** it runs
**Then** `listings` gains `pin_count` (default 0) and `relevance` (default 0, set later by matching), and `listings.service.pin()` / `unpin()` / `set_relevance()` exist for later epics to call.

**Given** more than about 30,000 active Listings
**When** the eviction job runs
**Then** it removes the oldest, least relevant unpinned Listings first, taking `FOR UPDATE SKIP LOCKED` on candidates
**And** it never removes a pinned Listing.

**Given** a takedown request
**When** the owner calls `listings.service.block(listing | url | company)` from the admin CLI or the database
**Then** matching Listings are removed and never re-ingested
**And** the action is written to `admin_audit`.

**Given** the database
**When** the daily quota check runs
**Then** an `ops_events` alert is raised at 80% of the database quota.

### Story 2.6: Search Listings by keyword

*Implements: FR13; NFR2*

As a Student,
I want to search the pool by keyword, even with typos,
So that I can find specific roles quickly.

**Acceptance Criteria:**

**Given** the search box
**When** I search "backend intern"
**Then** results match terms in title, company, extracted skills or `search_doc`, ranked by full-text relevance
**And** only Listings visible to me (pool, or my own private ones) and not expired are returned.

**Given** a typo such as "bakend intrn"
**When** I search
**Then** trigram similarity (`pg_trgm`) still returns relevant Listings.

**Given** about 30,000 active Listings (seeded fixture)
**When** typical queries run
**Then** they return in under 500 ms at the API (GIN indexes on `search_doc` and trigram columns), checked by a performance test.

**Given** no results
**When** the search completes
**Then** I see a neutral empty state suggesting I broaden my terms.

## Epic 3: A ranked, explained feed

Every Student sees pool Listings ranked by Match Score, can open the breakdown, and sees scores refresh within 5 minutes of a change.

### Story 3.1: Deterministic Match Score with a breakdown

*Implements: FR9; AD-8, AD-9*

As a Student,
I want each Listing scored 0–100 against my profile in a consistent, explainable way,
So that I can trust the ranking and see why a job fits or doesn't.

**Acceptance Criteria:**

**Given** `matching.score(profile, listing, weights) -> (int 0..100, ScoreBreakdown)`
**When** it runs
**Then** it is a pure function (no I/O, clock or randomness) combining weighted canonical-skill overlap with lexical similarity of my target roles and skills against the Listing title and `search_doc`
**And** it uses no embeddings and no LLM.

**Given** identical inputs
**When** `score()` runs any number of times
**Then** it returns the identical score and breakdown (property test).

**Given** `ScoreBreakdown`
**When** it is defined
**Then** it is a Pydantic model exposed in OpenAPI: `algo_version, weights_version, vocab_version, matched_skills[], missing_skills[], components[{name, points, max_points}]`
**And** its component points sum to the score.

**Given** profile and Listing skill sets with different `vocab_version`s
**When** scored
**Then** both are first re-normalised through the alias map.

**Given** the weights
**When** they change
**Then** they live in versioned config, and `weights_version` changes with them
**And** `weights_version` 1 is α = 0.7 for skill overlap and 0.3 for text similarity, with equal per-skill weights (decided at sprint planning, 2026-11-07).

### Story 3.2: Keep my scores fresh

*Implements: FR9; NFR3*

As a Student,
I want my scores recomputed automatically when my Resume, skills, Preferences or the pool change,
So that my feed is always current, within 5 minutes of my own edits.

**Acceptance Criteria:**

**Given** a migration
**When** it runs
**Then** it creates `match_scores(owner_id, listing_id, score, breakdown, algo_version, weights_version, vocab_version, computed_at)` with unique `(owner_id, listing_id)`, a 0–100 check, CASCADE from `accounts` and from `listings`
**And** it creates `match_score_missing_skills(owner_id, listing_id, skill_id)`.

**Given** `ResumeActivated`, `SkillProfileChanged` or `PreferencesChanged`
**When** the event is handled
**Then** `matching.service.mark_stale(owner_id, reason)` sets `scores_stale_since`
**And** it enqueues `matching.recompute:student:<id>` on the `priority` queue
**And** the router starts an in-process bounded drain after the response.

**Given** the recompute job
**When** it runs
**Then** it builds my candidate set: visible, unexpired Listings matching my role, location and job type Preferences, plus my manual Listings, capped at 500
**And** it upserts their scores, deletes scores that dropped out of the set, and calls `listings.service.set_relevance()` (from Story 2.5) with the max score per Listing
**And** if `scores_stale_since` is newer than the job's start time, it runs again.

**Given** `ListingsIngested`
**When** handled
**Then** scoring of the new Listings is batched per ingestion run for the affected Students, never one job per Listing.

**Given** I change my Skill Profile
**When** I return to the feed
**Then** updated scores show within 5 minutes (integration test with the drain).

### Story 3.3: My ranked feed

*Implements: FR10; NFR1, NFR4*

As a Student,
I want a feed of Listings ranked by how well I fit, filtered by my Preferences,
So that I only spend time on roles worth it.

**Acceptance Criteria:**

**Given** stored scores
**When** I open the feed
**Then** Listings are ordered by score descending, ties by `posted_at` descending then `first_seen_at`, with cursor pagination
**And** expired Listings are hidden.

**Given** my threshold
**When** the feed loads
**Then** Listings below it are hidden by default
**And** one toggle, "Show below my threshold", reveals them.

**Given** I'm a new Student with no stored scores yet
**When** I open the feed after setting up my profile
**Then** the top candidate Listings are scored on request with the same `score()` and shown immediately, so I see a ranked feed at once (UJ-1).

**Given** a feed page
**When** it loads at target volume
**Then** it returns in under 2 s (one indexed query over `match_scores` joined to `listings`, the allowed cross-module read).

**Given** I have no Skill Profile or Preferences yet
**When** I open the feed
**Then** a neutral prompt asks me to finish setup, with no pressure wording.

### Story 3.4: See why a Listing scored what it did

*Implements: FR9*

As a Student,
I want to open any score and see matched skills, missing skills and how each part contributed,
So that I understand my fit and what to improve.

**Acceptance Criteria:**

**Given** a scored Listing in the feed or detail view
**When** I open "Why this score"
**Then** I see the matched skills, the missing skills, and each component's points out of its maximum, from the typed `ScoreBreakdown`.

**Given** a search result outside my candidate set
**When** it is displayed
**Then** it shows a score computed on request with the same function and an openable breakdown
**And** the score is not stored.

**Given** the breakdown view opens
**When** it is shown
**Then** a count-only event is recorded for SM-4 (share of Students who open breakdowns).

## Epic 4: Track every application

A Student saves Listings, moves them through Status values with history, adds external Listings, and manages everything in a filterable Tracker. Ends with launch hardening.

### Story 4.1: Save a Listing and track its Status

*Implements: FR16; AD-20*

As a Student,
I want to save Listings and move each Application through its Status, with notes and history,
So that I always know where every application stands.

**Acceptance Criteria:**

**Given** a Listing I can see
**When** I click "Save"
**Then** an `applications` row (`owner_id`, CASCADE from `accounts`, `listing_id` SET NULL) is created with Status Saved
**And** a snapshot of title, company, url and deadline is stored on it
**And** `listings.service.pin(listing_id)` is called in the same transaction.

**Given** an Application
**When** I change its Status
**Then** `tracker.service.transition()` (the only writer) applies the transition table:
- Saved → Applied or Withdrawn
- Applied → Interview, Rejected or Withdrawn
- Interview → Offer, Rejected or Withdrawn
- Reopening from Rejected or Withdrawn is allowed back to the prior Status

**And** an `application_status_history` row with a timestamp is written in the same transaction
**And** entering Rejected or Withdrawn publishes `ApplicationClosed`, while reopening publishes `ApplicationReopened`.

**Given** an invalid transition (for example Saved → Offer)
**When** I attempt it
**Then** the API returns a 409 problem+json error naming the allowed next Statuses.

**Given** an Application
**When** I add notes, an applied date, or pick the Resume version used
**Then** they are saved
**And** the Resume version must be one of my own Resumes.

**Given** I delete an Application
**When** I confirm
**Then** the row and its history are removed
**And** `listings.service.unpin()` is called.

**Given** an Application's history
**When** I open it
**Then** I see every Status change with its timestamp, newest first.

### Story 4.2: My Tracker view

*Implements: FR17*

As a Student,
I want one place listing all my Applications with filters and upcoming deadlines,
So that nothing falls through the cracks.

**Acceptance Criteria:**

**Given** the Tracker
**When** I filter by Status, company and date range together
**Then** the filters combine and the result count is shown.

**Given** Applications with deadlines
**When** I open the Tracker
**Then** an "Upcoming deadlines" section lists them by date, using `listings.service.effective_deadline(owner_id, listing_id)` (added by this story; in phase 1 it returns the Listing's source deadline, with overrides arriving in phase 2) while the Listing exists, and the snapshot deadline otherwise.

**Given** an Application whose Listing was evicted or purged
**When** the Tracker loads
**Then** it still appears from its snapshot (title, company, link, deadline), marked "no longer in the pool".

**Given** an Application whose Listing has a stored score
**When** it is shown
**Then** its Match Score appears with the same "Why this score" breakdown.

**Given** another Student's Application id
**When** I request it
**Then** the API returns 404.

### Story 4.3: Add a job from another site

*Implements: FR18*

As a Student,
I want to add a Listing by URL or by typing its details,
So that applications from other sources are tracked and scored too.

**Acceptance Criteria:**

**Given** the "Add external job" form
**When** I enter a URL and/or title, company, location, job type, deadline and an optional description
**Then** a `ListingDraft` with `source='manual'` is built and goes through `listings.service.ingest_draft()`, which runs skill extraction and builds `search_doc`
**And** the URL is stored but never fetched by the server
**And** an optional description of 4,000 characters or less is kept privately for me.

**Given** a manual Listing
**When** any other Student browses, searches or loads a feed
**Then** they never see it (`owner_id` set, never in the pool).

**Given** a manual Listing is created
**When** it commits
**Then** `mark_stale` makes it scored like any other within my candidate set
**And** it is saved as an Application with Status Saved by default.

### Story 4.4: Launch hardening

*Implements: NFR1, NFR2, NFR7, NFR8*

As the developer,
I want retention, isolation and performance verified before launch,
So that the MVP is safe to open to real Students on free tiers.

**Acceptance Criteria:**

**Given** the purge jobs
**When** they run daily
**Then** finished Procrastinate jobs and outbox rows older than 7 days and `ops_events` older than 90 days are deleted, idempotently.

**Given** an isolation test suite
**When** it runs in CI
**Then** for every Student-owned endpoint, a second Student gets 404 for the first Student's ids
**And** every admin route returns 403 to Students.

**Given** the exposed-schema check and the RLS and grant checks
**When** CI runs
**Then** no table is reachable with the Supabase anon key (an anon REST request to each table is refused).

**Given** the performance fixtures (about 30,000 Listings, 500 scores per Student)
**When** the checks run
**Then** feed pages return in under 2 s and search in under 500 ms.

**Given** production
**When** go-live is checked
**Then** the seed run has populated the pool
**And** the `pg_cron` drain and the GitHub Actions health check are green
**And** the master key has an offline copy
**And** the Render service stays within free instance hours
**And** a manual `pg_dump` backup has been taken.
