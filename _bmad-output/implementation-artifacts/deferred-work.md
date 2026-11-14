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
