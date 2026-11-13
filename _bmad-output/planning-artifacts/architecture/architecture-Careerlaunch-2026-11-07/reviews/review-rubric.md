---
reviews: ../ARCHITECTURE-SPINE.md
reviewed: '2026-11-07'
method: good-spine checklist (items 1-7)
inputs: [ARCHITECTURE-SPINE.md, .memlog.md, prd-Careerlaunch-2026-10-24/prd.md]
---

# Spine Review: CareerLaunch (rubric)

## Verdict

This is a strong, disciplined spine: modules, ownership, ports, the job model, and the consent and send guards are all fixed in a form epics can build against. It is not ready to bind epics yet. Five gaps would let independently built stories diverge or fail in production. (1) The "defer in the same transaction" rule has no mechanism, and sync versus async data access is undecided. (2) The Outreach send is not safe against a crash in the middle of the external call, and the state machine has no failure states. (3) Nothing detects or recovers from a scheduler that silently stops. (4) The Procrastinate schema, the pg_cron SQL, and prod migrations sit outside the "Alembic owns the schema" rule. (5) The do-not-contact list has no identity key across Students' per-Application Contact rows. Most other findings are medium or low and can be fixed with a line or two each.

Severity counts: critical 0 · high 7 · medium 14 · low 10.

## Scorecard

| # | Checklist item | Result |
| --- | --- | --- |
| 1 | Fixes the real divergence points for epics, misses none | Partial: 6 missed divergence points (sync/async, day/timezone, eligibility evaluator, recurring-schedule mechanism, DNC identity, web auth/session pattern) |
| 2 | Every Rule is enforceable and prevents its divergence | Partial: AD-4 transactional defer, AD-2 join exception, AD-10 send idempotency, and the layer rules have no enforcement mechanism |
| 3 | Nothing Deferred lets units diverge | Partial: OQ-3 and CI are deferred too late; FR-12 is both deferred and mapped |
| 4 | Named tech verified-current | Mostly: memlog line 31 records registry checks for 13 of 17 pinned packages; supabase-js, Brevo, Groq/Gemini, and pg_cron/pg_net on the Free plan are not verified |
| 5 | Covers spec capabilities (FR-1..FR-29) | Mostly: all 29 FRs are mapped; there are gaps for FR-27 misuse reports, FR-19 LinkedIn link, FR-14 eligibility evaluation, the FR-9 5-minute refresh, and FR-23 bounces |
| 6 | Every owned dimension decided/deferred/open, esp. operational envelope | Partial: deployment and providers are decided; scheduler failure, prod migrations, and secret distribution are missing; backups and monitoring are deferred acceptably |
| 7 | Bloat; mermaid validity | Good: little bloat. All four diagrams are syntactically valid; one ERD cardinality is semantically wrong |

## Findings

Each finding gives the checklist item, the location, the severity, and the fix.

### High

**H1. Transactional defer has no mechanism; sync vs async data access is undecided.**
- Item: 2, 1 · Location: AD-4; Consistency Conventions "Transactions"; Stack (SQLAlchemy, psycopg, Procrastinate)
- Problem: AD-4 says to defer the job "in the same DB transaction as the state change". Procrastinate defers through its own connector and connection pool. A SQLAlchemy session has a different connection, so a naive `task.defer()` commits on its own and the rule silently fails. The spine also never says whether repositories and services are sync or async SQLAlchemy. Each module could pick differently, and that choice decides which Procrastinate connector works at all. This is the most important invariant in the spine, and nothing enforces it.
- Fix: Add to AD-4: "Services defer only via `core/jobs.defer(session, task_name, *, run_at=None, lock=None, queueing_lock=None, **args)`. It enqueues on the session's own connection inside its transaction, either by calling Procrastinate's defer SQL function or through an outbox table that the drain moves into Procrastinate. Calling `task.defer()` directly in `modules/` is forbidden, and a lint rule checks this." Add a convention row: "Data access is async SQLAlchemy 2.1 + psycopg 3 (or sync; pick one), the same for every module." Prove the mechanism in a scaffold spike.

**H2. The Outreach send is not idempotent against its external side effect, and the state machine has no failure states.**
- Item: 2, 5 · Location: AD-10, AD-4 ("every task is idempotent ... safe to retry")
- Problem: A send job can crash after Gmail accepts the message and before `→Sent` commits. The process is killed, Render recycles the instance, or the drain wall time runs out. Procrastinate then retries the stalled job, and the email goes out twice. An "entity id + action" key cannot prevent this, because the effect happens outside the database. FR-23 also requires that "a bounced or rejected send is shown ... not retried more than once". AD-10 has no `Sending`, `Failed` or `Bounced` state, so stories will invent their own.
- Fix: Extend AD-10's states to Draft → Approved → Queued → **Sending** → Sent | **Failed**, then Replied, plus Cancelled. The `send_outreach` task commits `Sending` before it calls `Mailer`, and records the Gmail message id on success. If a retry finds `Sending` with no message id, it marks the Outreach `Failed (unknown delivery)` for the Student to review and never resends on its own. A `Failed` send gets at most one manual or automatic retry. Add a general line to AD-4: "Tasks with external side effects use commit-before-call state; key-based idempotency covers DB effects only."

**H3. A silently failed scheduler goes undetected and has no fallback.**
- Item: 6 · Location: AD-4; Deferred "Ops checks at scaffold" and "uptime monitoring"
- Problem: Every follow-up, send, reminder, ingestion run and purge depends on one trigger: pg_cron inside a Supabase Free project. The memlog (line 33) notes that a paused project silently stops pg_cron. pg_net timeouts against a Render instance cold-starting are also possible. Nothing in the design notices that drains have stopped. The only open question is whether the drain prevents pausing, and that leaves "what if it doesn't" undecided.
- Fix: Add a scheduler-failure rule to AD-4. The drain writes `last_drain_at` (or an ops_events row). A second, independent trigger hits a public `/health` endpoint that returns 503 if `last_drain_at` is more than 15 minutes old. That trigger can be a GitHub Actions scheduled workflow or a free uptime pinger, and it also sends real traffic that keeps both the Supabase and Render projects active. An optional backup drain call from the same workflow covers the gap. Because jobs are idempotent and locked, two triggers are safe.

**H4. Schema ownership has holes: the Procrastinate schema, the pg_cron SQL, and prod migration execution.**
- Item: 3, 6, 2 · Location: AD-13; Source tree `supabase/` ("pg_cron schedule SQL"); Deferred "CI/CD"
- Problem: AD-13 says every schema change is an Alembic migration. But Procrastinate ships its own schema and versioned migration SQL, the pg_cron/pg_net schedule lives in `supabase/`, and RLS enablement (AD-1) is DDL too. Nothing says who applies these, or when, in prod. Render's free tier has no pre-deploy hook (verify), so `alembic upgrade head` has no defined step. The API (Render) and web (Vercel) also deploy independently, with no rule for compatible migrations.
- Fix: In AD-13, state that the Procrastinate schema and its upgrades, the pg_cron/pg_net extension and schedule, and RLS enablement are all applied by Alembic migrations, wrapping the vendor SQL. `supabase/` keeps CLI config only. Add to the Deployment table: "Prod migrations run from a GitHub Actions job (or a manual `alembic upgrade head` via the session pooler) *before* the Render deploy. Migrations are expand-then-contract, so the old API and the old web client keep working across a deploy."

**H5. The do-not-contact list has no identity key.**
- Item: 1, 2 · Location: AD-10; ERD `DO_NOT_CONTACT }o--o{ CONTACTS`
- Problem: Contacts hang off Applications, so the same person exists as many rows across many Students. FR-23 requires that an opt-out marks that person do-not-contact "for all Students", and FR-20 requires that a DNC person is never proposed. With no canonical key, discovery, lookup, send and admin will each match differently (by contact id, by raw email, by LinkedIn URL).
- Fix: Add to AD-10: "`do_not_contact` is keyed by normalized email (lower-cased, trimmed) and, separately, by normalized LinkedIn profile URL. `outreach.service.is_blocked(email=None, linkedin_url=None)` is the only check, and discovery, lookup, the send guard and admin all call it. Opt-out tokens are per Contact row, and redeeming one inserts that Contact's keys." Change the ERD to show DNC as a standalone table with no FK to Contacts.

**H6. There is no shared rule for "day" and time zone.**
- Item: 1 · Location: AD-10 (20/day cap, "next day"), AD-16 (daily digest), FR-15 reminders; Conventions "IDs and time" ("local time only in web")
- Problem: The daily send cap, the next-day requeue, the daily alert digest, and "N days before deadline" all need a day boundary. The conventions push local time into `web`, so the outreach epic may count days in UTC while the alerts epic uses the Student's local day. Students in India would see a cap reset at 05:30 local time.
- Fix: Add a convention: "Every Student has `preferences.timezone` (IANA, default `Asia/Kolkata`). All day-bucketed rules (send cap, next-day requeue, digests, reminders, deadline 'days before') use `core/clock.student_day(owner_id, instant)` and `next_local_time(owner_id, hh:mm)`. Storage stays UTC."

**H7. The mechanism for recurring schedules is unspecified.**
- Item: 1, 2 · Location: AD-4 ("ingestion schedules ... is a job with a scheduled run time")
- Problem: There are per-Student ingestion schedules, per-Source schedules, daily digests, the storage check, and purges. One epic could use Procrastinate `@app.periodic`, which depends on a long-running worker's periodic deferrer. With short `wait=False` drains every 5 minutes, ticks may be missed or fire irregularly (verify). Another epic could use self-rescheduling jobs. Mixed approaches mean duplicated or skipped runs.
- Fix: Add to AD-4: "Recurring work does not use `@app.periodic`. One `core.scheduler_tick` task runs at the start of every drain. It reads a `schedules` table (owner, kind, next_run_at) and defers the jobs that are due, each with a `queueing_lock` of kind + owner + period, then advances `next_run_at`. Each recurring kind registers a row." This also covers FR-27 ("pause a Source stops collection within one scheduling cycle"): pausing a Source pauses its schedule rows.

### Medium

**M1. The FR-9 5-minute score refresh is at risk.**
- Item: 5 · Location: AD-9, AD-4
- Problem: The worst case is a 5-minute drain interval plus a Render cold start of about 1 minute plus the recompute time, which is more than the FR-9 target of "refresh within 5 minutes". Ingestion-triggered recomputes for many Students can also queue ahead of it.
- Fix: When the active Resume, Skill Profile or Preferences change, the API also triggers an immediate bounded drain after commit (FastAPI `BackgroundTasks` calling the same drain routine). Alternatively, recompute jobs go on a high-priority `matching` queue that the drain empties first. If neither is acceptable, record the target as relaxed to 10 minutes.

**M2. No single evaluator for eligibility (FR-14/FR-29).**
- Item: 1, 5 · Location: Capability map rows FR-14 and FR-29 (cite AD-7 and AD-8, which do not cover this); ERD LISTING_CRITERIA, CRITERIA_OVERRIDES
- Problem: The feed (matching), search (listings) and the Listing detail view all need Eligible / Not eligible / Unknown. Nothing says where the tri-state logic lives or how Student overrides apply. Two implementations will disagree.
- Fix: Add an AD: "`listings.service.eligibility(listing_id, owner_id) -> {status, conflicts[]}` is the only evaluator. Missing criteria give Unknown. A Student override replaces the extracted criterion for that Student. Feed and search call it or its SQL equivalent in the listings repository. Criteria rows keep `source_snippet`."

**M3. Unauthenticated public endpoints are not in the API convention.**
- Item: 1, 6 · Location: Conventions "API"; System context (`contact -->|opt-out link| web`)
- Problem: The opt-out page (FR-23) and the OAuth callbacks are called without a Student JWT. The API convention defines only Student, Admin and `/internal` routes, so stories will invent their own prefixes and auth exemptions.
- Fix: Add `/api/v1/public/...` for token-authenticated or unauthenticated routes: opt-out redemption with a one-time token, rate-limited, idempotent. Add `/api/v1/oauth/<provider>/callback`. State that the web opt-out page calls only the public route.

**M4. The Gmail OAuth connection flow and the web session pattern are not fixed.**
- Item: 1 · Location: AD-1, AD-16, FR-19
- Problem: Supabase "Sign in with Google" does not give a usable long-lived `gmail.send` refresh token to FastAPI. Gmail connection must be a separate OAuth flow, and nothing says whether its redirect lands on web or api, or who exchanges the code. Separately, Next.js 16 App Router can call the API from server components or from the browser. Nothing fixes how the Supabase session reaches FastAPI (cookie via `@supabase/ssr` forwarded as a bearer token, or browser only), or the CORS policy.
- Fix: Add to AD-16: "Gmail connect is a FastAPI-owned OAuth code flow (`/api/v1/oauth/google/callback`, offline access, `gmail.send` only), separate from Supabase sign-in; the API exchanges and encrypts the tokens." Add a convention: "web keeps the Supabase session in cookies (`@supabase/ssr`) and sends `Authorization: Bearer <access_token>` on every API call from server or client. The API's CORS allows only the Vercel prod origin and localhost."

**M5. Distributing secrets to two platforms is undefined, and the master key is a single point of loss.**
- Item: 6 · Location: AD-6, AD-4 (shared secret header), Conventions "Config"
- Problem: The drain secret must exist both in Render's environment and in the pg_cron job inside Postgres. If it is written in `supabase/` SQL, it lands in the repo, which the conventions forbid. AD-6's master key lives only in Render's environment, and losing it makes every Provider Key and OAuth token unrecoverable.
- Fix: Add to AD-4/AD-6: "The drain secret is stored in Supabase Vault and read by the cron SQL; it is never committed." "The master key also has an offline copy in the owner's password manager. The prod secrets inventory is `.env.example` plus a 'where it lives' column."

**M6. OQ-3 is deferred to "before launch", but it is structural.**
- Item: 3 · Location: Deferred OQ-3
- Problem: If redistributing Listings is prohibited, AD-7's shared pool, AD-9's candidate sets, ingestion dedup and the cap and eviction logic all change ownership. Finding this out at week 9 would mean rework across three epics.
- Fix: Mark OQ-3 as a blocker for the ingestion and matching epics, like OQ-1. Or, from day one, add a nullable `visibility_owner_id` on pool Listings so going per-owner is a data change, not a structural one.

**M7. CI is deferred to weeks 9–10, but the spine's own rules depend on CI.**
- Item: 3, 2 · Location: Deferred "CI/CD"; AD-2, AD-3, AD-14
- Problem: "Generated client never hand-edited", "only adapters import vendor SDKs" and "repositories touch own tables" are only enforced if something checks them. If checks wait 8 weeks, drift builds up unseen.
- Fix: Move a minimal CI into the scaffold (a free GitHub Actions workflow): pytest, `import-linter` contracts for the layer table and vendor-SDK isolation, and an OpenAPI regeneration diff check (the job fails if the regenerated `web/src/api/` differs). Staging, backups and uptime can stay deferred.

**M8. The AD-2 cross-module join exception is ambiguous.**
- Item: 2 · Location: AD-2
- Problem: "Cross-module joins in SQL are allowed only for read-only feed and search queries inside the owning module's repository." The feed is in `matching` and joins `listings`, so which module is "owning"? This exception will grow with each epic.
- Fix: List the allowed joins explicitly: `matching.repo` may read `listings.listings` and `listings.listing_criteria` for the feed; `listings.repo` may read `matching.match_scores` for search ranking. Any other cross-module read goes through a service. Encode the list as a comment in the import-linter or SQL review checklist.

**M9. FR-27 misuse reports have no owner or table.**
- Item: 5 · Location: Capability map FR-27; ERD
- Problem: "Review reports of misuse" appears nowhere in the spine. Who files a report (a Contact on the opt-out page? a Student?) and which module owns it are undecided.
- Fix: Add a `misuse_reports` table owned by `admin`, filed through a public route (M3) from the opt-out page, and add it to the ERD. Or explicitly defer it with a reason.

**M10. The FR-19 LinkedIn link has a consent purpose but no port, owner or flow.**
- Item: 5 · Location: AD-11 (`linkedin_link` purpose), AD-3 port list, Capability map FR-19
- Problem: The CSV upload is deferred, but LinkedIn OAuth for identity is still in scope. It has no adapter and no stated use. FR-20's "existing connection" relevance ranking depends on it.
- Fix: Either add the LinkedIn OIDC identity link to `accounts` (`connected_accounts` kind `linkedin`, profile URL only) with its callback under M3's prefix, or move the LinkedIn link to Deferred along with the CSV, and drop the `linkedin_link` consent purpose until then.

**M11. Procrastinate locking and stalled-job recovery are asserted but not specified.**
- Item: 2 · Location: AD-4 ("idempotent (keyed by entity id + action)", "Stalled jobs are retried after their lock expires")
- Problem: In Procrastinate, stalled-job recovery is not automatic. A caller has to find stalled jobs and retry them (verify against 3.10 behaviour). "Keyed" could mean `lock`, `queueing_lock`, or an app-level table, and epics will pick differently.
- Fix: State the following. Every defer sets `queueing_lock = "<task>:<entity_id>[:<period>]"`. Tasks touching one entity set `lock = "<entity_type>:<id>"`. The drain starts by retrying stalled jobs older than N minutes, then runs the scheduler tick (H7), then works the queue. Max attempts and backoff are set per task in one registry.

**M12. Admin kill switches are not part of the run gate.**
- Item: 2, 5 · Location: AD-12
- Problem: FR-27 lets an Admin pause or disable a Source or provider. AD-12's pre-run checks cover caps only, so a paused provider can still be started through the one runner.
- Fix: Add to AD-12's pre-run checks: Source and provider enabled (admin flag), Student collection not `paused`, consent `provider_runs` (AD-11), and key present.

**M13. The vendor and plan claims for Free-tier capabilities are unverified.**
- Item: 4 · Location: Stack (Supabase pg_cron/pg_net on Free, Brevo, Groq or Gemini, supabase-js 2.119.0)
- Problem: Memlog line 31 registry-verifies the Python and Next packages, but not `@supabase/supabase-js` 2.119.0. pg_cron/pg_net on Free comes from a third-party source (memlog line 14). Brevo and Resend quotas say "confirm on official pricing" (line 32). The memlog says Gemini's free tier was cut in late 2025 (line 21), yet the Stack still lists "Groq or Gemini". "Groq or Gemini" is also not a decision.
- Fix: Registry-check supabase-js and add it to the memlog. Confirm pg_cron and pg_net on the official Supabase Free docs. Pick one Drafter default (Groq, per the memlog evidence) with the other as an optional adapter. Add `apify-client`, the Google API client or httpx, and the JWT library (Supabase JWKS / asymmetric keys) to the Stack with checked versions, since `core/auth` and the adapters depend on them.

**M14. Several ADs were never adopted by the user.**
- Item: 1 · Location: AD-8, AD-11, AD-12, AD-17, AD-18 (no `[ADOPTED]` tag); memlog line 34
- Problem: These were derived at distill time and never confirmed. Epics may treat them as optional.
- Fix: Get explicit adoption, or mark them `[PROPOSED]` and list them as an open question before the spine status moves from `draft`.

### Low

**L1. FR-12 is both deferred and mapped.**
- Item: 3, 5 · Location: Deferred row "Outlook, automatic reply detection, LinkedIn connections CSV, FR-12" vs Capability map FR-9 to FR-12 and AD-9 ("FR-12 insights all read stored scores")
- Fix: State that FR-12 is in scope and the first item cut if the schedule slips (PRD §7.1), and remove it from the Deferred row. Or keep it deferred and remove it from AD-9 and the map.

**L2. The candidate-set cap of 500 has no selection rule.**
- Item: 2 · Location: AD-9
- Fix: Add one line: "When more than 500 pool Listings pass the hard Preferences, keep the newest 500 by `posted_at` (non-expired)", or whatever the order is, and version it with `weights_version`.

**L3. Ingestion-triggered recompute is unbounded.**
- Item: 6 · Location: AD-9 ("or by new Listings")
- Fix: Recompute after ingestion is one job per affected Student, scoring only the new Listing ids against that Student's hard filters, with a `queueing_lock` per Student per drain window. This keeps the DB and the 512 MB worker within budget.

**L4. Account deletion does not mention the Supabase Auth user.**
- Item: 6 · Location: AD-17, AD-5
- Fix: Add: "Deletion also deletes the Supabase Auth user via the Admin API, in the same `purge` job after the DB cascade commits, retried idempotently." Decide whether `accounts.id` has an FK to `auth.users` (usually no, to keep Alembic independent of the auth schema).

**L5. Bootstrapping the first Admin is undefined.**
- Item: 6 · Location: AD-5
- Fix: "The first Admin is set by a one-off CLI / SQL migration seed reading `ADMIN_EMAILS` from env; there is no UI path to grant the admin role."

**L6. Vercel preview deployments point at the prod API.**
- Item: 6 · Location: Environments table
- Fix: State that previews are disabled, or that the API's CORS rejects preview origins. Also list Supabase Auth and Google OAuth redirect URLs per environment.

**L7. Connection limits and pooler mode are not budgeted.**
- Item: 6 · Location: Deferred "Ops checks at scaffold"
- Fix: Add a parameter: the API pool size is at most N and the drain worker concurrency is 1–2, kept under the Supavisor Free session-mode limit. Procrastinate's LISTEN/NOTIFY is not used (polling within the drain), so transaction-mode pooling remains possible.

**L8. The ERD has a wrong cardinality.**
- Item: 7 · Location: ERD `OPS_EVENTS }o--|| ACCOUNTS`
- Problem: Exactly-one means every ops event has an account, but system events (ingestion of a Source, the storage check) have none.
- Fix: `OPS_EVENTS }o--o| ACCOUNTS : "admin"`. Also consider showing SOURCES → PROVIDER_RUNS, and adding the `schedules` (H7) and `misuse_reports` (M9) entities.

**L9. Bloat that can be cut.**
- Item: 7 · Location: various
- Cut or shorten the following.
  - The "Job flow" sequence diagram restates AD-4 and AD-10 and fixes nothing new. Keep it only if it is updated to show the `Sending` state from H2. Otherwise move it to the academic report.
  - The Source tree largely repeats the layer table. Keep only the lines that add something: `jobs.py`, `migrations/`, `web/src/api/`, `supabase/`.
  - Remove the obvious parts of the conventions: "JSON over HTTPS", "TS components `PascalCase`", "pytest".
  - The Stack rows for Render, Vercel and Supabase repeat the Environments table. Keep one.
  - Each AD's "Prevents" line is fine and is not bloat. The rationale clauses in Deferred ("Low risk at student scale") are acceptable.

**L10. The "3 per company per Application" cap is ambiguous.**
- Item: 2 · Location: AD-10
- Fix: Say exactly what is counted, for example "at most 3 Outreach (excluding Follow-ups) to Contacts at one company for one Application". The PRD wording is ambiguous, so this needs to be fixed once here.

## Mermaid syntax check

| Diagram | Valid | Notes |
| --- | --- | --- |
| Invariants flowchart | Yes | The self-loop `services --> services` and the `-.implements.->` dotted label are valid. |
| System context flowchart | Yes | `auth` is defined on an edge before the subgraph lists it. Mermaid places it in the subgraph, which renders correctly. |
| Job flow sequence | Yes | Colons, arrows (→) and parentheses inside message text are fine. It needs semantic updating per H2. |
| Core entities ERD | Yes | All cardinality tokens are valid. One relationship is semantically wrong (L8). |

## Operational envelope summary (item 6)

| Dimension | Status in spine | Assessment |
| --- | --- | --- |
| Deployment & environments | Decided (local, prod) | OK. Add per-environment redirect URLs and the preview policy (L6). |
| Infra/provider strategy | Decided (Supabase, Render, Vercel, Brevo, LLM free tiers) | OK. Verify the Free-plan claims (M13). |
| Migrations in prod | **Missing** | H4 |
| Scheduler failure | **Missing** (only "whether drain prevents pause" is open) | H3 |
| Secrets management | Partly (env + `.env.example`) | M5: Vault for the drain secret, offline copy of the master key |
| Backups | Deferred to weeks 9–10 with a `pg_dump` plan | Acceptable. Add that Storage objects are not covered by `pg_dump`, and name where the dump runs (GitHub Actions) and where it is stored. |
| Monitoring / alerting | Deferred (uptime), ops_events in-app | Acceptable once H3's health check exists. Error alerting (for example a free Sentry tier) can stay deferred. |
| Rate limiting | Deferred | Acceptable, except the public opt-out route (M3), which should be rate-limited from day one. |
