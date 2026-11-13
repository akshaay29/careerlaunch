---
review: reconcile-prd
target: ../ARCHITECTURE-SPINE.md
sources: ['../../../prds/prd-Careerlaunch-2026-10-24/prd.md', '../../../prds/prd-Careerlaunch-2026-10-24/addendum.md']
date: 2026-11-07
---

# PRD ↔ Spine Reconciliation

**Verdict:** The spine covers most of the PRD well (ownership, the job model, the send guard, consent, secrets, snapshots, status history, audit, the 80% storage alert). It has two real contradictions: async bounce detection is impossible with `gmail.send` only, and generic job retries clash with FR-23's "retry at most once". It also misses several quiet requirements: the 5-minute score refresh and the "immediate" first feed don't fit a 5-minute drain cadence; do-not-contact identity across Students isn't defined; there's no public opt-out endpoint class; and there's no runtime check for Source or provider disable.

Scope: I flag only items with architectural impact that the spine neither covers through an AD or convention nor lists under Deferred, plus places where the spine contradicts the PRD. Items I checked and found covered are listed at the end.

---

## A. Contradictions

### C-1. Bounce detection is impossible under `gmail.send` only (high)
- **PRD:** FR-23: "A bounced or rejected send is shown to the Student and is not retried more than once."
- **Issue:** AD-16 limits Gmail to the `gmail.send` scope, and replies are marked manually. Gmail reports most bounces later, as non-delivery messages in the Student's inbox, and reading those needs a read scope. Only synchronous API rejections are detectable. AD-16 therefore quietly narrows FR-23.
- **Fix (AD-16 rule):** "Only synchronous `Mailer` rejections are detected and shown as `send_failed`. Asynchronous bounces are not detected in v1, and the Student can mark an Outreach bounced manually. Automatic bounce detection is deferred with reply detection (OQ-6)." Add the matching Deferred row.

### C-2. Generic job retry vs. FR-23 "not retried more than once", and duplicate sends (high)
- **PRD:** FR-23 (retry at most once), FR-23/§8 (no spam), §5 Reliability.
- **Issue:** AD-4 says every task is "idempotent (keyed by entity id + action) and safe to retry with backoff". An email send is an external side effect that a key cannot make idempotent. If Gmail accepts the message and the worker dies or times out before the transition to Sent commits, a retry sends a duplicate. Unbounded backoff retries also break the "at most once" retry rule.
- **Fix (AD-10 rule):** "`send_outreach` moves the Outreach to `Sending` (or records `send_attempt_id`) in a committed transaction before calling `Mailer`. It has `retry=1` on definite transient errors only. A job that finds the Outreach already `Sending` with no result marks it `send_unknown` for the Student to resolve, and never resends automatically."

### C-3. Outreach state set differs from the Glossary (low)
- **PRD:** Glossary, Outreach: Draft, Approved, Sent, Replied, Cancelled.
- **Issue:** AD-10 adds `Queued`, and C-2 needs `Sending`/failed states. The Enumerations convention says "PRD Glossary names are the canonical values", so the spine contradicts its own convention.
- **Fix (convention):** "Outreach enum = Glossary states plus internal states `Queued`, `Sending`, `Failed`. Internal states are shown to Students as 'Approved' or 'Failed'." Feed this back to the PRD Glossary.

### C-4. Lexical similarity replaces "semantic similarity" (low)
- **PRD:** FR-9 assumption: "hybrid of weighted skill overlap and semantic similarity".
- **Issue:** AD-9 uses Postgres lexical similarity, with no embeddings. That is a legitimate architectural choice, but it departs from the PRD wording.
- **Fix:** Note in AD-9 that this deliberately replaces the PRD's semantic-similarity assumption (the Embedder is Deferred), and send it back to PRD §11.

---

## B. Gaps

### G-1. The 5-minute score refresh and the "immediate" first feed don't fit a 5-minute drain (high)
- **PRD:** FR-9 ("scores refresh within 5 minutes of changing the active Resume or Skill Profile"), UJ-1 ("immediately sees a ranked feed from the shared pool"), §5 Time to value / SM-6.
- **Issue:** AD-4 runs work only when `pg_cron` drains, every 5 minutes, and each drain is bounded by job count and wall time. Recompute (AD-9) is a job. The worst case is 5 minutes of wait, plus a Render cold start (about 1 minute), plus queue backlog, plus compute. That exceeds the target. A brand-new Student also has no stored scores until the first recompute runs, so the first feed is empty or unscored, not "immediate".
- **Fix (AD-4/AD-9 rule):** "Student-initiated recompute jobs (onboarding, a Resume, Skill Profile or Preferences change) go on a `priority` queue. The API kicks a bounded in-process drain of that queue right after commit, using a post-response background task. The 5-minute cron is only the safety net. Until stored scores exist, the feed falls back to on-request scoring of the top-N candidate Listings (same `score()`)."

### G-2. Do-not-contact identity across Students and retention is undefined (high)
- **PRD:** Glossary (platform-wide DNC), FR-20 ("a Contact on the do-not-contact list is never proposed"), FR-23 ("marks the Contact do-not-contact for all Students … any queued Outreach to them is cancelled"), §5 Privacy (90-day retention), FR-2 (account deletion).
- **Issue:** Contacts are per Application, and therefore per Student (ERD `APPLICATIONS ||--o{ CONTACTS`). The same person shows up as many Contact rows across Students. The ERD's `DO_NOT_CONTACT }o--o{ CONTACTS` doesn't say what DNC is keyed on. If DNC references contact ids, three things go wrong: (a) another Student's newly discovered row for the same person isn't blocked; (b) the 90-day Contact purge (AD-17) or the opting-out Student's account deletion (cascade) erases the DNC entry; (c) Discovery filtering (FR-20) has nothing to match against. Cancelling *other Students'* queued Outreach also crosses owner scope.
- **Fix (AD-10 rule):** "`do_not_contact` is keyed by a normalized-email hash and a LinkedIn-profile-URL hash, has no FK to `contacts` or `accounts`, and is exempt from the Contact purge and account cascade. Discovery results, email lookup results and `send_outreach` are all checked against it. Adding an entry runs a system job (outside owner scope) that moves every Queued or Approved Outreach and Follow-up matching the hash, for any owner, to Cancelled."

### G-3. The opt-out endpoint has no public, unauthenticated route class (high)
- **PRD:** FR-23: "The opt-out link points to a page hosted by the platform with a one-time token per Contact … This works without a shared sending domain."
- **Issue:** The spine draws `Contact → web`, but AD-1 forbids `web` from touching data. The API convention defines only authenticated `/api/v1`, `/api/v1/admin` and `/internal` routes. Nothing says where a tokenized, unauthenticated request lands, how tokens are stored, or how the link is built (it's on the platform domain, not the mail domain). Two builders could easily diverge here, for example by putting the token check in Next.js server code that uses a service key.
- **Fix (convention + AD-10):** "Add `/api/v1/public/...` for unauthenticated, token-bearing endpoints (opt-out only in v1). Opt-out tokens are random, stored hashed, single-use and non-expiring. The confirm action is a POST, so link scanners can't trigger opt-outs, and it is idempotent. The link URL uses the Vercel web origin from config, and that page calls the public API."

### G-4. Source or provider disable has no runtime check, and "one scheduling cycle" has no meaning (high)
- **PRD:** FR-7 ("A Source that fails or is disabled by an Admin stops only its own collection"), FR-27 ("Pausing a Source stops new collection within one scheduling cycle and does not delete existing Listings"), §8 (Admin kill switch is a LinkedIn mitigation), §5 Reliability.
- **Issue:** The ERD has `SOURCES`, but AD-12 checks only spend caps before a run, and no AD says that ingestion jobs re-read Source or provider state. A run already scheduled, or a retry, would still fire after a disable. A provider-level kill switch (all Apify runs, including discovery and lookup) isn't modelled at all. Nothing says that one Source's failing jobs are isolated from other Sources.
- **Fix (AD-12 rule):** "`start_provider_run()` and every ingestion task re-check `sources.status` and a `providers.status` (Apify) kill switch at run time, and exit as `skipped_disabled` if either is off. Disabling never touches `listings`. Jobs are partitioned per Source (queue or lock key), so failures and backoff in one Source never delay another. 'One scheduling cycle' = the next drain (≤5 min)."

### G-5. Send caps are race-prone under concurrent workers (medium)
- **PRD:** FR-23 (20/day, 3 per company per Application), FR-24 (Follow-ups count against the daily cap).
- **Issue:** AD-10 re-checks the caps in `send_outreach`. With overlapping drains, or more than one job in one worker, two sends for the same Student can both read 19 and both send. AD-10 also doesn't say what happens when the per-company cap is hit: reject at approval, or queue?
- **Fix (AD-10 rule):** "`send_outreach` runs with Procrastinate `lock=owner_id` (sends are serialized per Student). Caps are counted from Sent and Sending rows in the same transaction that claims the send. The per-company cap is enforced at approval (it can't be approved past 3) and re-checked at send, cancelling with reason `cap_company`."

### G-6. Follow-up timing anchors to approval, not to the actual send (medium)
- **PRD:** FR-24 (day 5 and day 10; "never sent unless approved with its Outreach"), UJ-3.
- **Issue:** AD-10 creates the Follow-up jobs at approval with a scheduled run time. If the first send is pushed to the next day by the cap, fails, or is cancelled, Follow-ups can fire at the wrong offset, or for an Outreach that was never sent.
- **Fix (AD-10 rule):** "Follow-ups are approved with their Outreach but scheduled only when the parent reaches Sent, with `run_at = sent_at + offset`. Failure or cancellation of the parent cancels them."

### G-7. Long Apify runs vs. a bounded, sleeping worker (medium)
- **PRD:** §5 Hosting (instances sleep, limited workers), FR-6, FR-20, FR-21, FR-28 (actual usage per run).
- **Issue:** Apify actor runs take minutes. AD-4 bounds the drain worker by wall time, on a host that can sleep. The spine doesn't say whether a task blocks until the run finishes or starts it and comes back later. Ingestion, discovery and lookup could each pick a different pattern, and a blocking one would hit the wall-time limit and retry, which means double spend.
- **Fix (AD-12 rule):** "`ProviderRunner.start()` returns a run id. A `listings.poll_provider_run` job (rescheduled with backoff) or an Apify webhook to `/internal` collects results and actual cost. No task waits synchronously on an Apify run. Starting is idempotent on `provider_runs.id`, so a retry never launches a second paid run."

### G-8. The DB quota is threatened by the score table, the job table and append-only logs (medium)
- **PRD:** §5 Storage budget (about 0.5 GB DB), FR-8.
- **Issue:** AD-9 stores up to 500 scores per Student, each with a JSON breakdown. At about 1 KB per row, 1,000 Students is about 500 MB, which is the entire quota. The addendum's estimate covers only Listings. Procrastinate's finished-job rows, `ops_events` (append-only), `notifications` and `provider_runs` have no retention rule.
- **Fix (AD-9 + AD-17 rule):** "Breakdowns are stored compactly (arrays of skill ids plus component integers, no text). The candidate-set cap is tuned against a stated per-Student byte budget. Finished Procrastinate jobs, `ops_events`, `notifications` and `provider_runs` older than N days are purged by an idempotent `admin` retention job. The 80% check counts all of these."

### G-9. Eligibility tri-state has no single owner or function (medium)
- **PRD:** FR-14 (Eligible / Not eligible / Unknown, never Not eligible without a stated criterion, conflicting criterion shown), FR-29 (snippet link, unknown not guessed, per-Student correction), UJ-2.
- **Issue:** The ERD has `LISTING_CRITERIA` and `CRITERIA_OVERRIDES`, but no AD defines who computes eligibility, or how overrides and Student attributes (owned by `profile`) combine. The feed (`matching`), search (`listings`), listing detail and filters could each implement the tri-state differently. Unlike AD-9 for scores, there is no "one function" rule.
- **Fix (new AD or AD-7 addition):** "`listings` owns one pure `eligibility(criteria, overrides, student_attrs) -> (state, conflicts[])`. Override beats extracted value, a missing value gives Unknown, and Not eligible requires at least one conflict carrying a snippet reference. Feed, search, detail and filters all call it, or read its SQL equivalent defined in one place."

### G-10. Misuse reports have no entity, owner or path, and may conflict with AD-5 (medium)
- **PRD:** Glossary (Admin manages abuse reports), FR-27 ("review reports of misuse").
- **Issue:** There's no table, no submitter path (Contact through the opt-out page? Student?), and no owning module. Reviewing misuse may need Outreach content, which AD-5 forbids Admins to see.
- **Fix (AD-18 or AD-10 addition):** "`admin` owns `misuse_reports` (reporter type, target owner_id or contact hash, reason, status). Contacts can report from the public opt-out page. Admins see metadata and counts only, never Outreach bodies (AD-5), and can suspend sending for an owner." Alternatively, list it as Deferred with a reason.

### G-11. Account deletion leaves the Auth user and audit rows unresolved (medium)
- **PRD:** FR-2 (delete account and all data, within 30 days), FR-27 (every Admin action recorded).
- **Issue:** AD-17 cascades app rows, but `accounts.id` = the Supabase Auth user id, and nothing deletes the `auth.users` record (email, identity). `ON DELETE CASCADE` from `accounts` would also delete `admin_audit` and `ops_events` rows tied to that account (ERD FKs), which destroys audit history.
- **Fix (AD-17 rule):** "The deletion job also deletes the Supabase Auth user through the admin API, cancels the owner's pending jobs, and keeps `do_not_contact` entries. `ops_events` and `admin_audit` reference accounts with `ON DELETE SET NULL` (actor id kept as an opaque value), not CASCADE."

### G-12. Skill Profile edit persistence has no data rule (medium)
- **PRD:** FR-4 ("Student edits persist across Resume re-uploads unless explicitly reset").
- **Issue:** One `SKILL_PROFILES` row per account, with no provenance. The re-extraction code (also triggered by AD-8 vocabulary bumps) and the editor can diverge: re-extraction overwrites user removals or additions.
- **Fix (AD-8 addition):** "Skill Profile entries carry `origin` (`extracted` | `user_added` | `user_removed`). Re-extraction replaces only `extracted` entries and never resurrects `user_removed` ones. 'Reset' clears user entries."

### G-13. Reminders and alerts don't re-check at fire time (medium)
- **PRD:** FR-15 ("No reminder … after the Application's Status is Applied or later"; sent once at N days before the deadline), FR-11 (frequency batching).
- **Issue:** AD-16 gives at-most-once keys but doesn't require the reminder job to re-check the status and the current deadline when it runs. A deadline changed by re-ingestion or a correction leaves a stale job. Only the daily alert frequency is specified.
- **Fix (AD-16 rule):** "Reminder and alert jobs re-read status, deadline and Preferences at run time and no-op if they're stale. A deadline change reschedules the job. Every alert frequency (immediate/daily/weekly) is one digest job per Student per period."

### G-14. FK behaviour when Listings are purged (low)
- **PRD:** FR-8 / FR-17 (the Tracker survives Listing removal).
- **Issue:** AD-7 snapshots the Listing and AD-13 specifies cascade only from `accounts`. The FK actions for `applications.listing_id`, `match_scores`, `criteria_overrides` and `notifications.listing_id` are unstated, so a purge could fail or cascade-delete Applications.
- **Fix (AD-13 addition):** "`applications.listing_id` is `ON DELETE SET NULL`. `match_scores`, `listing_criteria`, `criteria_overrides` and `notifications` cascade from `listings`."

### G-15. Contacts that were never contacted escape the 90-day purge (low)
- **PRD:** §5 Privacy (retain Contact data only as long as needed), §8.
- **Issue:** AD-17 purges "90 days after the last Outreach activity". Dismissed Contacts, or Contacts with no Outreach, have no such activity.
- **Fix (AD-17):** "The purge clock is `max(discovered_at, last_outreach_activity_at)`. Dismissed Contacts are purged at 30 days, or immediately."

### G-16. LinkedIn drafts could enter the send state machine (low)
- **PRD:** FR-25 ("no send action"), §6 Non-Goals.
- **Issue:** The spine has no LinkedIn messaging port (good), but it doesn't say whether LinkedIn drafts are Outreach rows. If they are, `transition()` could move them to Approved or Queued.
- **Fix (AD-10 addition):** "LinkedIn drafts are `channel='linkedin'` rows that `transition()` cannot move past Draft. No port or adapter for LinkedIn messaging or connection requests may exist."

### G-17. First collection on key save, Render warmth and free-hours budget (low)
- **PRD:** UJ-1 ("first collection run adds fresh Listings within the hour"), §5 Performance (feed under 2 s, search under 500 ms) under sleeping free hosts.
- **Issue:** Nothing triggers a first run when a key is saved. A Render cold start (about 1 minute) breaks the 2 s and 500 ms targets on the first request. The spine doesn't say whether the 5-minute drain keeps Render warm, or whether a 24/7 warm service fits Render's monthly free hours. The Deferred ops-check row covers only Supabase pausing.
- **Fix (Deferred row / AD-4 note):** "Saving a valid key defers an immediate first ingestion run. The ops check at scaffold adds: confirm the drain cadence keeps Render warm and that 24×7 uptime fits the free instance-hour quota. If not, accept cold-start exclusion from the performance targets."

### G-18. Search performance budget for on-request scoring (low)
- **PRD:** FR-13 / §5 (under 500 ms at about 30k Listings, typo tolerance).
- **Issue:** AD-9 scores search results outside the candidate set on request. With no limit on that, latency could exceed 500 ms. No index convention (GIN tsvector, trigram) is fixed.
- **Fix (AD-9 / capability map):** "On-request scoring is limited to the returned page (≤ 25 rows). `listings` keeps a stored generated `tsvector` with a GIN index and a trigram GIN index on title and company."

### G-19. FR-21 pattern-guess fallback isn't recorded as dropped (low)
- **PRD:** FR-21 (open-source pattern-guess-and-verify fallback where hosting allows).
- **Issue:** The spine doesn't mention it. Render Free blocks outbound SMTP, so it's effectively out of scope, but this isn't recorded.
- **Fix (Deferred row):** "FR-21 SMTP verify fallback: not built, because outbound port 25 is blocked on Render Free. Manual email entry covers it."

### G-20. How Admin accounts are created (low)
- **PRD:** FR-1 (one role per account; role checks on the server).
- **Issue:** AD-5 loads the role from `accounts`, but nothing says how `admin` is assigned. That leaves room for a self-service escalation path.
- **Fix (AD-5 addition):** "`role='admin'` is set only by a migration or CLI script. No API endpoint changes roles."

---

## Checked and covered (no finding)
Server-side role checks and 403 (AD-5). Admin cannot read private content (AD-5, AD-18). Key and token encryption, no logging, never returned (AD-6). Listing dedup by DB constraint (AD-7, AD-13). Manual Listings private and scored (AD-7, AD-9). Trimmed snapshot for the Tracker (AD-7). 4,000-char trim, 30-day grace, cap eviction (AD-7). Status history (AD-13). One active Resume (AD-13). Threshold 0–100 check (AD-13). One alert per Student per Listing (AD-13, AD-16). Deterministic score with versioned breakdown and tie-break (AD-9). Consent gating with timestamps (AD-11). Low-confidence email confirmation (AD-10 re-check). Daily cap rolls to the next day (AD-10). Opt-out cancels Follow-ups (AD-10). Stop on Rejected/Withdrawn (AD-10). Drafts limited to facts, under 150 words, with template fallback (AD-15). Spend caps, platform cap, pause on Apify error, seed run (AD-12). Usage recorded (AD-12). Admin audit and ops log (AD-18). 80% storage alert (AD-18). Expiring signed file URLs, type and size checks (Files convention). File purge on deletion (AD-17). Outlook, reply detection, CSV upload and FR-12 deferred (Deferred table). Source Adapter test double (AD-3).
