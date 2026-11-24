- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-project-scaffold-ci-and-deploy-pipeline.md`
  summary: Add a web test runner (e.g. vitest) and a test that renders HealthStatus against a stubbed /api/v1/health, asserting the ok and error states and that requests go to NEXT_PUBLIC_API_URL.
  evidence: Story 1.1 review (verification-gap) found no web tests at all; CI's web job runs only lint, typecheck and build, so the web→api wiring can break unnoticed. Deferred because web has no runner yet; best added in the first story with real web logic.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-background-jobs-that-survive-a-sleeping-server.md`
  summary: Run migration 0003 (pg_cron + pg_net + Vault drain schedule) in CI, e.g. a CI job on the Supabase CLI Postgres image, and execute DRAIN_COMMAND against a stub endpoint.
  evidence: test_pg_cron_drain_job always skips on CI's plain postgres:17; a broken header or Vault name would ship unnoticed (it passed locally on the Supabase CLI stack).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-background-jobs-that-survive-a-sleeping-server.md`
  summary: Add retention (AD-17) for finished procrastinate_jobs/procrastinate_events and old ops_events, as an idempotent scheduled job.
  evidence: the worker runs with DeleteJobCondition.NEVER and every drain appends an ops_events row; the tables grow without limit on the 0.5 GB free tier.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-background-jobs-that-survive-a-sleeping-server.md`
  summary: Check the per-drain DB connection count (lock connection + Procrastinate pool max 4 + SQLAlchemy pool) against the hosted Supavisor session-pooler client limit.
  evidence: unverified (medium if true). Settle it by reading the Supabase project's pooler limits once provisioned and counting connections during a drain.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-3-sign-up-log-in-and-roles.md`
  summary: Add a "Forgot password?" flow (Supabase recovery email → /auth/confirm type=recovery → set new password page) and point the recovery/email-change templates at /auth/confirm.
  evidence: Story 1.3 review: email/password users have no way to reset a forgotten password; no story in the epics covers it.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-3-sign-up-log-in-and-roles.md`
  summary: Test the web API client's bearer-token hook (createClientConfig auth → Authorization header on getMe) with a mocked Supabase browser client.
  evidence: Story 1.3 verification-gap review; typecheck/build pass even if the token is never attached.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-3-sign-up-log-in-and-roles.md`
  summary: Make record_event tolerate an owner_id whose account was deleted (e.g. insert with owner_id NULL), so Story 1.9 purge jobs don't lose their ops_events rows.
  evidence: ops_events.owner_id now has an FK to accounts; job events for already-deleted owners fail to insert (caught and logged, event lost).
- source_spec: `_bmad-output/implementation-artifacts/spec-1-4-consent-for-provider-runs-and-the-shared-pool.md`
  summary: Add a component or e2e harness to `web` and test ConsentPanel's 409 reload and that Withdraw is shown only on /settings/consent.
  evidence: The web tests are only pure-logic `node --test` tests, so a UI regression such as losing Withdraw would ship undetected.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-4-consent-for-provider-runs-and-the-shared-pool.md`
  summary: Add grievance and contact details to the DPDP rights notice, and keep its deletion promise true once Story 1.9 ships (bump RIGHTS_NOTICE_VERSION).
  evidence: DPDP notices must say how to exercise rights. No contact address exists yet; the product owner must supply one.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-4-consent-for-provider-runs-and-the-shared-pool.md`
  summary: When Epic 2 defines pool and run cancellation, update the consent texts (with a version bump) to say what happens to shared Listings and in-progress runs on withdrawal.
  evidence: Informed consent needs those facts, and the behaviour isn't decided yet.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-4-consent-for-provider-runs-and-the-shared-pool.md`
  summary: Unverified (medium if true): work can pass `require_consent` just before a withdrawal commits. Epic 2 callers must re-check the gate inside the job at the side effect, or lock the consent row.
  evidence: The gate is an unlocked read. It is settled by how the first callers (Epic 2 provider runs) use it.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-5-upload-and-manage-resume-versions.md`
  summary: Story 1.6 must decide what happens when the active Resume is deleted (no version active), e.g. publish an `ActiveResumeCleared` event that the Skill Profile and later scoring react to.
  evidence: `delete_resume` publishes nothing, so any consumer of "the active Resume" would keep using a deleted version.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-5-upload-and-manage-resume-versions.md`
  summary: Add component tests for `ResumeManager` (precheck, upload/activate/delete flows, delete confirm) once `web` has a component or e2e harness.
  evidence: Only the pure helpers in `lib/resumes.ts` are tested.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-skill-profile-extracted-from-my-resume.md`
  summary: Give `core.jobs` an on-exhausted hook so `profile.extract_resume` can mark the profile `failed` when `FileStore.get` keeps failing past the retry policy.
  evidence: Today a Storage outage longer than the retry window leaves the profile `pending` forever.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-skill-profile-extracted-from-my-resume.md`
  summary: The first change to `VOCAB_VERSION` must add a backfill job that re-normalises stored skill sets (and promotes custom skills that entered the vocabulary), as AD-8 requires.
  evidence: `vocab_version` is stored but nothing acts on it. Raw text isn't kept, so re-extraction needs the file.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-skill-profile-extracted-from-my-resume.md`
  summary: Add component tests for `SkillProfileEditor` (polling, timeout, 401, reset confirm) once `web` has a component or e2e harness.
  evidence: Only `lib/skill-profile.ts` helpers are tested.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-7-set-my-preferences.md`
  summary: Add component or e2e tests for the whole onboarding flow (consent → Resume → Skill Profile → Preferences → "You're set up") and the settings hub links, once `web` has a harness.
  evidence: Only pure helpers are tested, so dropping `onSaved` or a Continue button would break SM-6 onboarding silently.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-store-and-download-my-other-documents.md`
  summary: Add component tests for `DocumentManager` (delete confirm, signed-out path, load-more merge) once `web` has a harness.
  evidence: Only the pure helpers in `lib/documents.ts` are tested.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-9-delete-my-account-and-all-my-data.md`
  summary: Require recent authentication (Supabase reauthenticate / OTP, or an `iat` freshness check) and a rate limit on `DELETE /api/v1/me`.
  evidence: The only confirmation is the account email, which the page displays, so a stolen or left-open session can delete the account.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-9-delete-my-account-and-all-my-data.md`
  summary: Add component tests for `DeleteAccount` (204 redirect, 422 on the field, 401 path, disabled while pending) once `web` has a harness.
  evidence: Only the helpers in `lib/account-deletion.ts` are tested.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-1-shared-listing-pool-source-adapter-and-browsing.md`
  summary: Add component tests for `ListingBrowser` / `ListingDetail` (signed-out, not-found, load-more merge, error retry) once `web` has a harness.
  evidence: Only `lib/listings.ts` helpers are tested.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-2-add-my-apify-provider-key.md`
  summary: Story 2.3 must decide how re-saving a Provider Key resumes the `listings.collect` schedules that deleting it paused, without overriding pauses from caps or provider status (2.4).
  evidence: `delete_credential` pauses the schedules, and `store_credential` never resumes them.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-2-add-my-apify-provider-key.md`
  summary: Add component tests for the `ConsentPanel` `next` return and `ProviderKeyManager` (replace, cancel, delete dialog, consent round trip) once `web` has a harness.
  evidence: Only the `lib/provider-key.ts` helpers are tested.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-collect-linkedin-listings-on-a-schedule.md`
  summary: On account deletion, abort in-flight Apify runs, and decide whether anonymised `provider_runs` cost history should outlive the account (SET NULL plus a purpose marker) for spend accounting.
  evidence: `provider_runs.owner_id` CASCADEs, so cost rows vanish and a running actor keeps billing until its max charge.

- source_spec: `_bmad-output/implementation-artifacts/spec-2-4-see-my-usage-set-spend-caps-and-pause-on-failure.md`
  summary: Show the per-run estimate next to the per-run cap field so a Student does not set a cap below it (every run would be refused `run_cap`).
  evidence: The minimum per-run cap (USD 0.05) is below the default estimate (100 items x USD 0.001 = 0.10); the reason is visible but the form gives no hint.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-4-see-my-usage-set-spend-caps-and-pause-on-failure.md`
  summary: Re-probe `account_blocked` and `not_permitted` pauses, which only a key re-save clears today.
  evidence: These pauses have no `exhausted_until`; a Student whose Apify account was unblocked stays paused until they replace the token.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-4-see-my-usage-set-spend-caps-and-pause-on-failure.md`
  summary: Collapse repeated daily `refused` rows in the usage list and index `provider_runs (created_at)` if the platform cap is ever enabled at scale.
  evidence: A paused Student gets a refused row each day, which pushes real runs out of the latest 30; the platform-wide month scan has no created_at index.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-4-see-my-usage-set-spend-caps-and-pause-on-failure.md`
  summary: Emit an ops event when a Student is paused or changes caps, notify them in-app or by email, and add accessibility attributes and component tests to the caps form.
  evidence: Only the failed run row records a pause today; the caps form ignores the `field` that `precheckCaps` returns; web has no component test harness.

- source_spec: `_bmad-output/implementation-artifacts/spec-2-5-expire-evict-and-take-down-listings.md`
  summary: Keep a durable record of what each takedown covered (kind and value, including after `unblock`), e.g. an audit detail column or a block history table.
  evidence: `admin_audit` records only the action name and actors; `unblock` deletes the `listing_blocks` row, so nothing shows what was lifted.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-5-expire-evict-and-take-down-listings.md`
  summary: Add a `--dry-run` preview to `block_listing.py`, an index for the expire scan, an alert when one expire pass removes an unusually large share of the pool, and daily quota alerts with an escalation level and a way to reach the owner.
  evidence: The CLI deletes irreversibly and prints only a count; the expire query has no supporting index; the quota alert is an `ops_events` row nobody is notified about.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-5-expire-evict-and-take-down-listings.md`
  summary: Clear `expired_at` on a re-sighting by a non-contributing collector, re-read the active count each eviction batch, and record each job's ops event in the same transaction as its last batch.
  evidence: The upsert clears `expired_at` only for an authorised writer, so a still-seen Listing can be purged; evict reads the count once; a retried partial job undercounts.

- source_spec: `_bmad-output/implementation-artifacts/spec-2-6-search-listings-by-keyword.md`
  summary: Make typo tolerance work when a misspelling is mixed with another word ("bakend intrn python"), e.g. trigram match per token, and decide how short language names ("c++", ".net", "c#") are searched.
  evidence: Trigram `word_similarity` compares the whole phrase to title and company only, so extra tokens dilute it; the 2-character rule and tokenizer reject or split such queries.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-6-search-listings-by-keyword.md`
  summary: Run the search tests in CI with an `extensions` schema present (the Supabase placement of `pg_trgm`), and add a component test for the `/listings?q=` wiring once `web` has a harness.
  evidence: CI places `pg_trgm` in `public`; the Supabase branch was verified by hand only. No web component harness exists.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-6-search-listings-by-keyword.md`
  summary: Tell the Student when results stop at the offset cap, treat combining marks as part of words, share tokenizer test vectors between the API and web, and add rate limiting for search.
  evidence: `next_offset` becomes null at 200 with no message; `str.isalnum` and `[\p{L}\p{N}]` split Indic words; the two tokenizers are mirrored by hand; no endpoint is rate limited yet.

- source_spec: `_bmad-output/implementation-artifacts/spec-3-1-deterministic-match-score-with-a-breakdown.md`
  summary: Rebalance the text component so a broad profile is not penalised (coverage over role-title terms, or capped term count) and matched skills are not counted again in text; speed up lexeme matching for batch scoring (set lookup first).
  evidence: `lexeme_coverage` divides by all the Student's terms including every skill label; Story 3.2 will score many Listings per Student.

- source_spec: `_bmad-output/implementation-artifacts/spec-3-2-keep-my-scores-fresh.md`
  summary: Re-mark every Student stale when `ALGO_VERSION`, the weights version or `VOCAB_VERSION` changes (deploy-time check or task).
  evidence: Stored rows record their versions but nothing triggers a recompute when they change.
- source_spec: `_bmad-output/implementation-artifacts/spec-3-2-keep-my-scores-fresh.md`
  summary: Reduce recompute cost at scale: mark only Students whose filters match the ingested Listings, batch outbox inserts, skip unchanged score rows, use the `<%` operator so the trigram index serves the role filter, and count changed rows in the ops event.
  evidence: Each pool ingest marks almost every Student and each recompute rewrites up to 500 rows and scans the visible pool.
- source_spec: `_bmad-output/implementation-artifacts/spec-3-2-keep-my-scores-fresh.md`
  summary: Story 3.3's feed must hide expired or taken-down Listings even when stored scores still reference them.
  evidence: Expiry and takedown do not trigger a recompute; stored scores are pruned only on the Student's next recompute.

- source_spec: `_bmad-output/implementation-artifacts/spec-3-3-my-ranked-feed.md`
  summary: Cap manual Listings in the provisional feed, share one visibility/expiry predicate and one default threshold across modules, type `FeedItem.from_listing`, and extend the feed perf test (stale rows, larger score sets, HTTP timing).
  evidence: `candidate_set` adds every manual Listing beyond the cap; predicates and the threshold default are repeated in `matching`, `listings` and `profile`.

- source_spec: `_bmad-output/implementation-artifacts/spec-3-4-see-why-a-listing-scored-what-it-did.md`
  summary: Define SM-4's denominator ("active Students"), add a per-Student daily dedupe or rate limit and a retention rule for `matching.breakdown_opened`, and add component or e2e tests for the breakdown view, search badges and detail badge once `web` has a harness.
  evidence: The event is a plain authenticated POST with no limit; the web wiring is only covered by pure-helper tests.

- source_spec: `_bmad-output/implementation-artifacts/spec-3-4-see-why-a-listing-scored-what-it-did.md`
  summary: Make `test_listing_search_perf.py` robust on loaded machines (e.g. compare against a calibration query, or run it only in CI with a fixed budget), for Story 4.4.
  evidence: On the developer's OneDrive-synced laptop the two-word query measured 242 ms, then 399 ms and 623 ms under background load with no change to the search code, so the 500 ms assertion fails intermittently locally.
