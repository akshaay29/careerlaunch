---
type: architecture-review
lens: adversary (compliant-but-incompatible unit pairs)
target: ../ARCHITECTURE-SPINE.md
context: ../../../prds/prd-Careerlaunch-2026-10-24/prd.md
date: 2026-11-07
---

# Adversarial Review: CareerLaunch Architecture Spine

## Verdict

The spine sets module boundaries and the queue model well. But it leaves the shared identities (Contact, skill_id, company, "day"), the direction of dependencies between modules, and the meaning of "same transaction" across modules undefined. Two teams can each follow every AD exactly and still build epics that fail together. Three of the holes can cause an email that is legally prohibited or sent twice.

## Method

Each finding names two units one level below the spine (epics or stories), shows how each one follows every AD and convention as written, shows where they diverge, gives a severity, and proposes an AD rule that closes the hole and can be enforced. Severity: **critical** means a legal or anti-spam breach, a duplicate external side effect, or a system that cannot be built as specified. **High** means wrong user-visible behaviour or data corruption. **Medium** means rework or confusing UX. **Low** means friction.

## Summary table

| # | Pair | Hole | Severity |
| --- | --- | --- | --- |
| 1 | Discovery story vs Opt-out / DNC story | Contact identity is per-Application; global do-not-contact has no cross-Student key and is lost on cascade or purge | critical |
| 2 | Send task story vs Daily-cap story | Send is not idempotent across the network call; the cap check races | critical |
| 3 | Tracker epic vs Outreach epic | Circular service calls; "cancel in the same transaction" across modules is undefined | critical |
| 4 | Accounts consent story vs Outreach / Listings jobs | Revoking consent "cancels pending jobs" in other modules' queues | high |
| 5 | Outreach send cap vs Matching digest | "Day" has no timezone; the backend never knows the Student's timezone | high |
| 6 | Profile epic vs Matching epic (vs Listings ingestion) | Who defers recompute, and the idempotency key that collapses newer changes | high |
| 7 | Profile extraction vs Listings JD extraction vs FR-12 insights | skill_id type and vocabulary version are mixed | high |
| 8 | Job framework story vs every task author | Idempotency key format, queueing lock, and self-reschedule conflict | high |
| 9 | Any service vs Procrastinate setup | "Deferred in the same DB transaction" with two connection pools | high |
| 10 | Listings eligibility (FR-14/29) vs Profile preferences vs Matching candidate set | Shape of the criterion and Student attributes; who decides Eligible | high |
| 11 | Matching alerts vs Accounts notifications | One notifications row per Listing vs per digest; the unique key breaks digests | medium-high |
| 12 | Account deletion / retention vs Outreach audit and caps | Cascade and the 90-day purge erase the cap history and the opt-out tokens | high (folded into 1, separate fix) |
| 13 | Listings eviction vs Tracker / Matching | Eviction needs Application and relevance data from modules that depend on listings | medium |
| 14 | Tracker manual Listing story vs Listings ingestion | ListingDraft shape, nullable dedup key, deadline type | medium |
| 15 | Outreach per-company cap vs Tracker snapshot / Discovery | "Company" identity is a free string in three places | medium |
| 16 | Tracker status story vs Outreach discovery and follow-ups | No Application status state machine; reversals and skipped Applied | medium |
| 17 | Tracker reminders vs Listings re-ingestion / overrides | Which deadline is used, and rescheduling when it changes | medium |
| 18 | Matching breakdown vs Web score panel vs FR-12 | Breakdown JSON is untyped in OpenAPI | medium |
| 19 | Outreach Mailer vs Accounts connected accounts (and ProviderRunner vs provider keys) | Refreshed OAuth tokens can't be written back; a lookup failure pauses ingestion | medium |
| 20 | Admin DNC / Source pause vs Outreach / Listings owner-scoped repos | Global entities in an API where every call is owner-scoped; misuse reports have no owner | low-medium |
| 21 | Matching alerts vs re-scoring | "New Listings" vs "newly crossing the threshold" | low |

---

## Findings

### 1. Contact identity across Students and the global do-not-contact list — CRITICAL

**Units:** Outreach "Employee Discovery" story (FR-20) vs Outreach "Opt-out page and DNC" story (FR-23), and Admin "manage DNC" story (FR-27).

**How both comply:** The ERD makes `CONTACTS` a child of `APPLICATIONS`, and AD-5 requires `owner_id` on every Student-owned table. So the Discovery story creates a new `contacts` row per (Student, Application) for each person returned. The DNC story models `DO_NOT_CONTACT }o--o{ CONTACTS` exactly as drawn: a join from `do_not_contact` to `contact_id`. The opt-out token is "one-time per Contact", so it is per `contact_id`.

**Divergence:** Jane Doe opts out after Student A's email. That marks contact row `c1` (A's). Student B's Discovery then finds Jane and creates `c2`. The FR-20 filter "never proposed if on DNC" checks `c2`, finds no entry, and proposes her. The send guard (AD-10) checks the same way and sends. Both stories follow the ERD exactly. Worse, AD-13 `ON DELETE CASCADE` from `accounts` → `applications` → `contacts` means deleting Student A's account (or the Application) **deletes the DNC link**, and AD-17 purges Contact personal data after 90 days, which removes the email the DNC needed. This breaks FR-23 ("never emailed again by any Student") and §8.

**Close the hole (new AD-19 Contact identity):** `outreach` owns a global `contact_identities` table that has no `owner_id` and is keyed by a normalised, salted hash of the lower-cased email and, separately, of the canonical LinkedIn profile URL. Per-Student `contacts` reference it. `do_not_contact` and opt-out tokens key on `contact_identity_id` or on those hashes, never on `contacts.id`. These rows are excluded from the account cascade and from the 90-day purge, and the purge keeps only the hashes. Discovery and the send guard check DNC by hash.

### 2. Send idempotency and the daily-cap race — CRITICAL

**Units:** Outreach "send_outreach task" story vs Outreach "daily and per-company caps" story (with the Follow-up story).

**How both comply:** AD-4 says tasks are idempotent "keyed by entity id + action" and retried. The Transactions convention forbids network calls inside a DB transaction. So the send task does this: tx1 reads the state and checks the caps (`count(sent today) < 20`) and commits; then it calls `Mailer` outside any transaction; then tx2 transitions to Sent. The caps story counts rows in `Sent` state. Follow-ups use the same task.

**Divergence:** (a) The worker crashes, or the Render instance sleeps, after Gmail accepts the message and before tx2. The lock expires, AD-4 retries, the state is still Queued, and the email is sent twice. An idempotency key on the job does not make an external send idempotent. (b) Two drain workers (one bounded drain overlapping the next cron tick) each run tx1 for different Outreach rows while the count is 19. Both pass and 21 emails go out. (c) One story counts Queued toward the cap and the other counts only Sent, so "queued to next day" happens at different thresholds. (d) FR-23 says "not retried more than once" on a bounce, but AD-4 says "safe to retry with backoff", so the two rules give opposite retry policies.

**Close the hole (tighten AD-10):** Add a `Sending` state. tx1 atomically claims a cap slot by inserting into `send_ledger(owner_id, send_day, slot_no)` with a unique constraint (slot_no 1–20), plus a per-(application, company) count under `SELECT … FOR UPDATE` on the Student's row, and moves the item to `Sending` with a stored RFC 5322 `Message-ID`. A retry that finds `Sending` must not call `Mailer` blindly: it marks the item `SendUnknown` for the Student to resolve. Retries of `Mailer` errors are capped at 1, which overrides the AD-4 default for this task.

### 3. Tracker ↔ Outreach circular dependency and the cross-module transaction — CRITICAL

**Units:** Tracker "status change" story (FR-16) vs Outreach "follow-up cancellation" and "send guard" stories (FR-23/24).

**How both comply:** The layer table lets a service call "other modules' services". AD-10 says a Rejected or Withdrawn status "cancels [follow-ups] in the same transaction", so the Tracker story calls `outreach.service.cancel_for_application(app_id)` from `tracker.service.change_status()`. AD-10 also says the send guard re-checks Application status, so `outreach.service` calls `tracker.service.get_status()`. Discovery needs "Applied", and the Contact FK points to Applications. Each call is allowed on its own.

**Divergence:** `tracker.service` imports `outreach.service` and the reverse, which gives a Python import cycle or ad-hoc lazy imports. Worse, the Transactions convention says "one unit of work per service call". If `outreach.service.cancel_for_application` opens its own unit of work (as every outreach story does to follow the convention), the cancel commits separately from the status change. A failure between them leaves a Rejected Application with live follow-ups, which breaks AD-10's "same transaction". The same pattern repeats in profile ↔ matching (recompute trigger vs reading the profile), listings ↔ matching (search scores on request vs candidate set reading listings), listings ↔ tracker (eviction checks Applications vs Applications snapshotting Listings), and accounts → everyone (consent cancel, #4).

**Close the hole (new AD-20 Dependency direction and the unit of work):** Declare a DAG of modules, `accounts ← profile ← listings ← matching ← tracker ← outreach ← admin` (a module may call only services to its left). A call against that direction goes through in-process domain events (`core/events`) that the caller publishes inside its open session. Subscribers run in the same session and transaction, synchronously, before commit. Only the outermost entry point (router or task) opens and commits the unit of work. Services take the session as a parameter and never commit. Add an import-linter contract to CI.

### 4. Consent revocation that cancels other modules' jobs — HIGH

**Units:** Accounts "consent management" story (FR-2) vs Outreach "send" and Listings "provider run" stories.

**How both comply:** AD-11 says "revoking consent cancels pending jobs for that purpose". The Accounts story follows it by deleting or cancelling Procrastinate jobs filtered by task name (`outreach.send_outreach`, `listings.run_ingestion`). No module owns the `procrastinate_jobs` table, so AD-2 doesn't stop it. The Outreach story keeps domain state in `outreach.state = Queued`.

**Divergence:** Cancelling only the job leaves Outreach rows in `Queued` with no job behind them: the UI shows "Queued" forever and the Follow-ups appear pending. If accounts instead calls `outreach.service.cancel_all(owner)`, accounts (the base module) depends on outreach, which creates the cycle in #3. If the Student re-grants consent, nothing re-defers the jobs.

**Close the hole (tighten AD-11):** Revoking consent publishes a `ConsentRevoked(owner_id, purpose)` domain event (AD-20), and each owning module moves its own entities to `Cancelled` (or `Paused`) through its own state machine in the same transaction. No module other than `jobs.py` reads or writes the Procrastinate tables. The in-job `require_consent` re-check stays the backstop.

### 5. Timezone of "daily" (send cap, digest, reminders, next-day queue) — HIGH

**Units:** Outreach "20/day cap and next-day reschedule" story vs Matching "daily alert digest" story (AD-16), plus Tracker "N days before deadline" reminder.

**How both comply:** The convention says `timestamptz` in UTC and "local time only in web". So the backend has no Student timezone. The Outreach story defines the day as the UTC calendar day and reschedules to 00:00 UTC (05:30 IST). The Matching story runs one digest job per Student per day with the key `(owner_id, date)`, but computes the date when the job is deferred, which on a "rolling 24h" reading means 24 hours after the last digest. The Tracker computes "2 days before deadline" from a `date` deadline at UTC midnight.

**Divergence:** An Indian Student's evening sends roll over at 05:30 local time, the digest arrives at a different hour each day or twice in one local day (which breaks FR-11's "at most one alert message per day" from the Student's view), and reminders arrive at 05:30. The cap and the digest don't agree on what a day is, so a support question like "why did I get 2 digests today" has no consistent answer.

**Close the hole (tighten the IDs and time convention):** `profile.preferences` stores an IANA `timezone` (default from the browser at signup). Every "per day" rule (send cap, digest, next-day queue, reminder send time) uses the Student's local calendar day, computed only by `core/clock.local_day(owner_id, ts)`. Day-scoped idempotency keys include that local date. Next-day sends and digests run at a fixed local hour (for example 09:00).

### 6. Who triggers re-scoring, and dedup that drops newer changes — HIGH

**Units:** Profile "Resume / Skill Profile / Preferences edit" stories vs Matching "recompute job" story, vs Listings "ingestion upsert".

**How both comply:** AD-9 says "recompute is a job triggered by a change to …". The Profile story defers `matching.recompute_student(owner_id)` itself in its transaction (AD-4 "in the same transaction as the state change"). The Matching story instead exposes `matching.service.request_recompute()` and expects callers to use it. The Listings story defers `matching.score_listing(listing_id)` once per upserted Listing, which can mean thousands of jobs per ingestion run. AD-4 keys idempotency on "entity id + action", so the key is `recompute:{owner_id}`.

**Divergence:** (a) Profile defers a task that another module owns (AD-2 is about tables, so this is allowed) and couples itself to matching's task signature. (b) If the dedup key acts as a queueing lock, a second profile edit made while the first recompute is running gets dropped as a duplicate. Or, under Procrastinate's lock semantics, it is rejected while one is still `todo`. Either way the stored scores reflect the earlier profile, which breaks FR-9's 5-minute target and "identical inputs give the same score". (c) A per-listing fan-out at ingestion versus a per-student batch at drain gives a different candidate-set size and different quota behaviour. Criteria overrides and a vocabulary bump (AD-8) are not listed as triggers.

**Close the hole (tighten AD-9):** Only `matching.service.mark_stale(owner_id | listing_ids, reason)` triggers scoring. It is called through domain events (AD-20) and sets a `scores_stale_since` watermark. The recompute job is keyed `matching.recompute:{owner_id}`, is coalesced, and re-runs if `scores_stale_since` is later than its start time. New Listings are scored in batches per ingestion run, never one job per Listing. Triggers also include criteria overrides, Preferences timezone changes and vocabulary version bumps.

### 7. skill_id shape and vocabulary version — HIGH

**Units:** Profile "Skill Profile extraction" (FR-4) vs Listings "JD skill extraction" (FR-29 / AD-8) vs Matching "FR-12 skill-gap insights" story.

**How both comply:** AD-8 gives `normalize(text) -> set[skill_id]` and nothing more. Profile stores `skill_ids` as `text[]` slugs (`"python"`). Listings stores them as `int[]` (row ids in a `skills` table it seeded from the canonical list). Neither keeps its own alias table, so both comply. Profile also lets a Student add a skill manually by free text that doesn't match the vocabulary (FR-4 "confirm/edit"), stored with a `custom:` prefix.

**Divergence:** The overlap is always empty, or matching has to translate between the two forms. During a vocabulary bump, the re-extraction jobs finish profiles before the 30k Listings, so for hours matching compares a v4 profile against v3 Listings and scores drop for everyone. FR-12 aggregates `missing_skills` from breakdowns written under different vocabulary versions, so a renamed skill is counted twice. Nobody says whether custom skills score.

**Close the hole (tighten AD-8):** `skill_id` is a stable lower-kebab slug string. It is never reused, and renames are handled by alias. Every stored skill set (profile, listing, score breakdown) records `vocab_version`. `score()` refuses (or first re-normalises through the alias map) inputs whose versions differ. Custom skills are stored separately and never scored in v1.

### 8. Idempotency key format and queueing-lock conflicts — HIGH

**Units:** Platform "jobs.py framework" story vs each task author (Outreach send, Follow-up send, Matching digest, Listings schedule, Tracker reminder).

**How both comply:** AD-4 says "keyed by entity id + action". The send story uses `send_outreach:{outreach_id}` for both the initial send and its Follow-ups, because the Follow-up's entity is the Outreach. The daily-cap reschedule re-defers the same key from inside the running job. The digest uses `digest:{owner_id}`. The ingestion scheduler uses `ingest:{owner_id}`. Reminders use `remind:{application_id}`.

**Divergence:** With Procrastinate `queueing_lock`, the day-5 Follow-up defer collides with the still-`todo` initial send, which is waiting for the next-day slot, and fails to defer at approval time. That breaks AD-10's "created and approved together". With `lock`, initial and Follow-up are serialised, which happens to be correct. With an app-level dedup table, the self-reschedule is rejected as a duplicate and the send is lost. A digest key without a date blocks tomorrow's digest while today's sits waiting to be retried. Each choice follows AD-4 and they don't fit together.

**Close the hole (tighten AD-4):** The idempotency key is `<task_name>:<entity_type>:<entity_id>[:<local_date|attempt_scope>]`, using the specific entity (`followup_id`, not `outreach_id`). It maps to Procrastinate `queueing_lock` only for periodic or day-scoped tasks, and side-effect dedup lives in the domain row's state (#2), not in the queue. Rescheduling is done by deferring a new job with a new scope (the new local date) before returning success.

### 9. "Deferred in the same DB transaction" with two connection pools — HIGH

**Units:** Any service story that uses SQLAlchemy sessions vs the "Procrastinate app" scaffold story.

**How both comply:** The stack lists SQLAlchemy 2.1 and Procrastinate 3.10. The scaffold configures Procrastinate's own `PsycopgConnector` pool (the documented default). Service stories call `task.defer_async()` inside `async with session.begin()`. Both look like they follow AD-4.

**Divergence:** The defer goes out on a different connection and commits on its own. A rollback of the state change still leaves the job, and a job that runs before the outer commit sees the old state. This is exactly the duplicate or lost side effect AD-4 exists to prevent. Separately, the Supavisor transaction pooler (port 6543) does not support the session-level `LISTEN` and advisory locks Procrastinate uses.

**Close the hole (tighten AD-4):** Jobs are deferred only through `core/db.defer(session, task, **kwargs)`, which inserts on the session's own connection (using Procrastinate's connector bound to the SQLAlchemy connection, or a raw `procrastinate_defer_jobs_v1` call). All app and worker DB traffic uses the session pooler, never the transaction pooler. A test asserts that a rolled-back transaction leaves no job.

### 10. Eligibility criterion shape and who decides Eligible — HIGH

**Units:** Listings "FR-29 criteria extraction" + "FR-14 eligibility filter" vs Profile "FR-5 Eligibility attributes" vs Matching "candidate set" (AD-9).

**How both comply:** Listings owns `listing_criteria`. Its story stores `{kind, op, value_text, snippet}` with CGPA as text ("7.5/10", "3.2 GPA"), batch year as a range "2025-2026", and degree as raw text ("B.Tech/BE"). Profile stores the Student's `cgpa numeric` (10-point), `batch_year int` and `degree enum`. AD-9 says candidate sets filter by "hard Preferences such as role, location and job type". The Matching story reads that literally and also drops Not-eligible Listings to save quota (it decides "such as" includes eligibility). Listings computes Eligible/Not/Unknown itself in its search query, using a join to preferences (a "read-only search query", which AD-2 allows).

**Divergence:** There are two eligibility evaluators with different rules: matching drops a "3.0 GPA" listing that listings shows as Unknown. Criteria overrides (owned by listings) change what search shows but not the candidate set or the alerts. Listings joins `profile.preferences` directly, which follows the letter of AD-2's search exception but couples to profile's column names. Scale conversion (4-point vs 10-point) belongs to no one.

**Close the hole (new AD rule in Listings):** `listings.service.evaluate_eligibility(owner_id, listing_ids) -> {listing_id: (Eligible|NotEligible|Unknown, conflicting_criteria[])}` is the only evaluator, and it applies overrides. Criteria are stored typed (`kind` enum; `cgpa_min` normalised to a 10-point decimal with the raw value kept; `batch_years int4range`; `degree` canonical enum plus raw). Student attributes are read through `profile.service`. The matching candidate set never excludes on eligibility.

### 11. Notifications ownership and the alert unique key — MEDIUM-HIGH

**Units:** Matching "threshold alerts + daily digest" story vs Accounts "notifications + Notifier" story.

**How both comply:** AD-16 says `Notifier` writes an in-app `notifications` row first, with unique `(owner_id, listing_id, kind)`. The spine says Matching calls Notifier "through the accounts service". The Accounts story treats one notifications row as one message and sends one Brevo email per row. The Matching story inserts one row per (Student, Listing, `alert`) to enforce "at most one alert per Listing", then the digest job calls `accounts.service.notify(owner, kind='digest', listing_id=NULL)`.

**Divergence:** A daily-frequency Student gets N emails (one per row) plus a digest. Or, if the digest row has `listing_id NULL`, Postgres treats NULLs as distinct and the unique key no longer guarantees one digest per day. The "write row first, then email" order spans a network call, so it's unclear which transaction holds the row and whether a Brevo failure rolls the row back (that would re-alert) or leaves it (the email is lost).

**Close the hole (tighten AD-16):** Split the tables: `alert_items(owner_id, listing_id)` unique, owned by `matching`, which records eligibility for an alert; and `notifications(owner_id, kind, dedup_key)` unique, owned by `accounts`, one row per message, with `dedup_key` = `digest:<local_date>` or `reminder:<application_id>`. The email is a separate `accounts.send_notification` job deferred in the row's transaction.

### 12. Deletion cascade and purge erase the outreach safety state — HIGH

**Units:** Accounts "account deletion" story (AD-17, AD-13 cascade) vs Outreach "caps, opt-out tokens, SM-3 metrics".

**How both comply:** "`ON DELETE CASCADE` from `accounts` covers all Student-owned rows." Opt-out tokens and DNC rows link to contacts, which cascade from applications. The 90-day purge "removes Contact personal data".

**Divergence:** Beyond #1, a Contact who clicks a 4-month-old opt-out link gets a 404 because the token was purged with the Contact. `ops_events` (admin) for sends survive and may reference deleted ids. SM-3 and SM-C1 lose their denominators.

**Close the hole (tighten AD-17):** Opt-out tokens live at least as long as the identity hash (#1) and resolve to it, never to a per-Student row. Retention purges null out PII columns and keep the hashed identity, timestamps and state, so metrics and DNC survive. `ops_events` hold only ids and counts, never PII.

### 13. Listing eviction needs data from modules that depend on listings — MEDIUM

**Units:** Listings "expiry and cap eviction job" (FR-8, AD-7) vs Tracker "Applications" and Matching "scores".

**How both comply:** AD-7 says eviction must skip Listings with a saved Application and rank by lowest relevance. The Listings story checks Applications with a SQL `NOT EXISTS` against `applications` and uses `max(match_scores.score)` for relevance. It calls this a "read-only query inside the owning module's repository". The Tracker story deletes Applications freely. `match_scores` FK to listings: Matching's migration uses `ON DELETE RESTRICT` to protect breakdowns, while Listings assumes `CASCADE`.

**Divergence:** The AD-2 exception covers "feed and search queries", and the eviction job is neither, so one reviewer accepts the query and another rejects it. Eviction fails on the FK if the actions differ. There's a race between the eviction check and a Student saving the Listing at the same moment.

**Close the hole (tighten AD-2/AD-7):** Every FK into `listings` declares its `ON DELETE` action in AD-7 (`match_scores`, `alert_items`, `notifications`, `criteria_overrides`: CASCADE; `applications.listing_id`: SET NULL with the snapshot kept). Eviction takes `SELECT … FOR UPDATE SKIP LOCKED` on candidate Listings, and tracker's "save" takes `FOR SHARE` on the Listing. Relevance comes from `matching.service.relevance_for(listing_ids)`.

### 14. ListingDraft shape and manual Listings — MEDIUM

**Units:** Listings "Apify SourceAdapter" story vs Tracker "add external Listing" story (FR-18; the map puts FR-16–18 in both `tracker` and `listings`).

**How both comply:** AD-3 says `SourceAdapter` yields a "normalized `ListingDraft`" but defines no fields. The Apify story's draft has `deadline: datetime | None` (UTC), `posted_at` required, `company` raw. The Tracker story builds its own `ManualListingIn` DTO with `deadline: date`, no `posted_at`, and calls `listings.service.create_manual()`, which skips JD skill extraction and FR-29 extraction because "manual Listings aren't ingested". AD-7's unique `(source, source_job_id)` with `source_job_id NULL` for manual entries lets duplicates through (fine), but the Apify story sets `source_job_id` = URL hash for a listing whose URL a Student also added by hand. Nothing links the two.

**Divergence:** Manual Listings score lower (no skills extracted), which breaks FR-18's "scored like any other". Ties break on `posted_at` and it is null. Deadlines are a date in one place and a timestamp in another, so reminders are off by a day across timezones.

**Close the hole (tighten AD-3):** `ListingDraft` is a Pydantic model in `ports/` with explicit fields (`source`, `source_job_id`, `url`, `title`, `company_raw`, `location_raw`, `job_type`, `description`, `posted_at: datetime | None`, `deadline: date | None`, `raw: dict`). Manual entries are built as a `ListingDraft` and go through the same `listings.service.ingest_draft()` pipeline (trim, skills, criteria) with `source='manual'` and an `owner_id`.

### 15. Company identity for the per-company cap — MEDIUM

**Units:** Outreach "3 per company per Application" cap vs Tracker snapshot vs Discovery Contacts.

**How both comply:** The cap is "per company per Application". Outreach counts by `contacts.company` (as returned by the discovery actor, for example "Google LLC"). The Tracker snapshot holds `company` = "Google". Both are strings, which no AD forbids.

**Divergence:** If the count is keyed by Application, it's trivially per-Application. If keyed by company string, near-duplicates escape it. Teams pick different keys and the guard enforces a different rule from the UI counter.

**Close the hole (tighten AD-10):** The per-company cap counts sends grouped by `application_id` alone (the Application fixes the company). Any cross-Application company rule uses `listings.company_key` (normalised lowercase, legal suffixes removed), computed only in `listings`.

### 16. No Application status state machine — MEDIUM

**Units:** Tracker "status change" story vs Outreach "discovery gate" and "follow-up cancellation".

**How both comply:** AD-10 defines the Outreach state machine, but nothing defines Application transitions. AD-13 only requires a history row. The Tracker story allows any-to-any changes (Saved → Interview, Rejected → Interview). Outreach gates Discovery on `status == Applied`.

**Divergence:** A Student who goes straight to Interview never unlocks Discovery. Moving Rejected → Interview doesn't restore cancelled Follow-ups, and Outreach treats Cancelled as terminal. Re-opening a Withdrawn Application while an Outreach is still in `Sending` is undefined.

**Close the hole (new tracker AD):** `tracker.service.transition()` is the only writer of Application status, with an explicit transition table. "Applied or later" (Applied, Interview, Offer) unlocks Discovery. Entering Rejected or Withdrawn publishes `ApplicationClosed`, leaving it publishes `ApplicationReopened`, and Outreach does not resurrect Cancelled Follow-ups (the Student re-approves).

### 17. Which deadline drives reminders — MEDIUM

**Units:** Tracker "deadline reminder" story (FR-15) vs Listings re-ingestion / FR-29 overrides.

**How both comply:** AD-7 says Tracker reads the snapshot (deadline captured at creation). The Tracker defers `remind:{application_id}` at `deadline − 2d` when the Application is saved. Listings later re-ingests and updates the deadline, or the Student corrects it through `criteria_overrides` (FR-29 extracts the deadline as a criterion).

**Divergence:** The reminder fires at the stale time, or fires for a Listing that now has no deadline. The snapshot and the live Listing disagree and nothing reconciles them.

**Close the hole (tighten AD-7):** The snapshot is a fallback used only when the Listing is gone. While the Listing exists, the effective deadline is `listings.service.effective_deadline(owner_id, listing_id)` (override > extracted > source). A deadline change publishes `DeadlineChanged`, and tracker reschedules the reminder job (key including the deadline date).

### 18. Breakdown JSON untyped — MEDIUM

**Units:** Matching "score storage" vs Web "score breakdown panel" (FR-9) vs Matching "FR-12 insights".

**How both comply:** AD-9 says "a JSON breakdown (matched skills, missing skills, per-component points)". The Matching story returns `breakdown: dict`, which OpenAPI renders as `{[key: string]: unknown}`, so the generated client (AD-14) is "correct". Web hand-types the fields. FR-12 SQL reads `breakdown->'missing'` while the scorer writes `missing_skills`.

**Divergence:** Typed-client protection is lost at the one place SM-4 measures, and the keys drift between `algo_version`s.

**Close the hole (tighten AD-9):** The breakdown is a Pydantic model `ScoreBreakdown{algo_version, weights_version, vocab_version, matched_skills: list[skill_id], missing_skills: list[skill_id], components: list[{name, points, max_points}]}`, exposed in OpenAPI. FR-12 reads a normalised `match_score_missing_skills(owner_id, listing_id, skill_id)` table that matching writes, not JSON paths.

### 19. Token refresh write-back and provider-run pause scope — MEDIUM

**Units:** Outreach "send via GmailMailer" vs Accounts "connected accounts"; Outreach "email lookup via ProviderRunner" vs Listings "collection pause" (AD-12).

**How both comply:** AD-6 says decryption happens only inside the adapter. Adapters depend only on ports and SDKs. So `GmailMailer` refreshes an expired access token and cannot persist it (no service access), and every send refreshes again. Outreach calls `listings.service.start_provider_run()` for email lookup, as AD-12 requires. On a credit error AD-12 sets "the Student's collection to paused".

**Divergence:** Refresh-token rotation or a revocation is silently lost. One failed email lookup pauses Listing collection, and a team that reads "collection" narrowly leaves lookups running on an exhausted key.

**Close the hole (tighten AD-6/AD-12):** Ports return refreshed credentials as an opaque `CredentialUpdate` that the calling service persists through `accounts.service.store_credential()`. AD-12's pause is a per-Student `provider_status` (owned by accounts) that blocks all `ProviderRunner` purposes, and the pause reason is shown to the Student in both places.

### 20. Admin acting on global entities through owner-scoped services — LOW-MEDIUM

**Units:** Admin "DNC management / Source pause / misuse reports" vs Outreach and Listings services under AD-5.

**How both comply:** AD-5 says every repository method on a Student-owned table takes `owner_id` from the auth context. AD-10 says admin changes DNC only through the outreach service. Outreach's service functions all take `owner_id`, so the admin story passes the admin's own id. Misuse reports (FR-27) have no owning table in the ERD, so the Admin epic creates `misuse_reports` and Outreach creates `contact_reports`.

**Divergence:** The DNC rows record the admin's id as their "owner", which then conflicts with #1. Two report tables appear.

**Close the hole (tighten AD-5/AD-2):** Global (non-Student-owned) tables are listed in the ERD with no `owner_id` (`contact_identities`, `do_not_contact`, `sources`, `misuse_reports`→`outreach`). Their service functions take an `actor` (`Student|Admin`) instead of `owner_id` and call `record_audit` for Admin actors.

### 21. "New Listings" vs re-scored crossings — LOW

**Units:** Matching alerts vs Matching recompute.

**How both comply:** FR-11 alerts on "new Listings" reaching the threshold. One story alerts on any first crossing (including after a profile change re-scores old Listings). Another alerts only on Listings ingested after the last digest.

**Close the hole (tighten AD-16):** A Listing is alert-eligible when its stored score first crosses the threshold and the Listing has `first_seen_at` within the last 7 days. `alert_items` uniqueness (#11) prevents repeats.

---

## Proposed new or tightened rules (consolidated)

1. **AD-19 Contact identity (new):** global hashed `contact_identities`; DNC and opt-out tokens key on it; exempt from cascade and purge. (#1, #12)
2. **AD-20 Dependency DAG and unit of work (new):** fixed module order; calls against the order go through same-transaction domain events; only the entry point commits; services take the session. (#3, #4, #6, #13)
3. **AD-10 tightened:** `Sending` state, a ledger of cap slots with a unique constraint, a stored Message-ID, at most one retry of a send, and the per-company cap keyed by `application_id`. (#2, #15)
4. **AD-4 tightened:** `core/db.defer(session, …)` on the same connection; key format `<task>:<entity_type>:<id>[:<scope>]`; side-effect dedup lives in domain state; session pooler only. (#8, #9)
5. **Time convention tightened:** IANA timezone in Preferences; `core/clock.local_day`; every daily rule is local-day. (#5)
6. **AD-8 / AD-9 tightened:** slug `skill_id`, `vocab_version` on every skill set, a typed `ScoreBreakdown`, `mark_stale` as the only trigger with a watermark re-run. (#6, #7, #18)
7. **Listings rules:** one eligibility evaluator with typed criteria; a typed `ListingDraft` with manual entries going through the same pipeline; `effective_deadline`; explicit FK delete actions. (#10, #13, #14, #17)
8. **AD-16 tightened:** separate `alert_items` from per-message `notifications(dedup_key)`; the email sent as its own job. (#11, #21)
9. **Tracker state machine:** `tracker.service.transition()` with a table; "Applied or later" unlocks Discovery; close and reopen events. (#16)
10. **AD-6 / AD-12 / AD-5 tightened:** credential write-back through accounts; provider pause per Student across purposes; actor-scoped services for global tables. (#19, #20)
