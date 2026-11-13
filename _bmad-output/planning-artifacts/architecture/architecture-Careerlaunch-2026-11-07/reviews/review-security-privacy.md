---
type: architecture-review
lens: security, privacy, compliance
target: ../ARCHITECTURE-SPINE.md
sources: ['../../../prds/prd-Careerlaunch-2026-10-24/prd.md (4.1, 4.3, 4.7, 5, 8)']
date: '2026-11-07'
reviewer: security/privacy lens (architecture level only)
---

# Security, Privacy and Compliance Review: CareerLaunch Architecture Spine

## Verdict

The spine gets the big structural choices right (single data gateway, owner scoping from the auth context, app-level secret encryption, one send guard, consent as one gate, deletion as jobs). It is **not yet safe to build from** for the third-party-data and cold-email parts. The do-not-contact and opt-out design breaks when Contacts are deleted. The opt-out footer can be edited out of the body. The job queue tables sit outside the RLS and deletion invariants. Untrusted file parsing, the OAuth connect flow and the LLM data path have no invariants at all. Two findings are critical and eight are high. Every finding below can be fixed by adding or tightening an AD rule. None needs a change of paradigm.

Severity scale: **critical** means a reachable breach of law or of isolation that a builder following the spine literally would ship. **High** means a likely serious defect, or a missing invariant on a sensitive path. **Medium** means a gap that defence in depth or a careful builder probably covers, but the spine does not require. **Low** means hygiene.

## Summary table

| # | Finding | Location | Severity |
| --- | --- | --- | --- |
| S-1 | Do-not-contact and opt-out tokens are tied to deletable, per-Student Contact rows | AD-10, AD-13, AD-17, ERD | critical |
| S-2 | Procrastinate and other non-Alembic tables are outside the RLS/no-grant invariant | AD-1, AD-4, AD-13 | critical |
| S-3 | The opt-out footer is a placeholder in a body the Student can edit; the send guard does not verify it | AD-10, AD-15 | high |
| S-4 | Opt-out endpoint design (unauthenticated, one-time vs idempotent, prefetch, hashing) is unspecified | AD-5, AD-10, deployment diagram | high |
| S-5 | Untrusted PDF/DOCX parsing runs in the single shared API process | Files convention, FR-3/FR-26 map | high |
| S-6 | Gmail OAuth connect flow has no state/PKCE binding, refresh or revocation invariants | AD-6, AD-16, FR-19 map | high |
| S-7 | Deletion is incomplete: Auth user, job payloads, audit FKs, in-flight jobs, Google grants, backups | AD-13, AD-17, Deferred (backups) | high |
| S-8 | LLM data exposure: Contact PII and Resume facts go to a free-tier provider that may train on them | AD-15, Stack | high |
| S-9 | No minimisation allowlist for Contact data or raw Apify output; the 90-day clock is undefined for never-contacted Contacts | AD-3, AD-12, AD-17 | high |
| S-10 | Abuse controls for a Student using the platform to spam or harass | AD-10, Deferred (rate limiting), FR-27 | high |
| S-11 | `/internal/jobs/drain` protection is under-specified (secret storage, comparison, rotation, exposure) | AD-4, Conventions (API), `supabase/` | medium |
| S-12 | IDOR gaps: child tables without `owner_id`, jobs with no auth context, JWT verification details | AD-5, AD-2 | medium |
| S-13 | Prompt injection and stored XSS from scraped job descriptions; email header injection | AD-15, AD-7, AD-16 | medium |
| S-14 | Master key handling: no AAD binding, co-location with backups, seed key scope | AD-6, AD-12 | medium |
| S-15 | Signed URL scope and content-type serving | Files convention | medium |
| S-16 | CORS/CSRF between Vercel and Render are not pinned | Conventions (API), AD-1 | medium |
| S-17 | Admin audit is not tamper-evident; admin role assignment is undefined; there is no misuse-report or suspend path | AD-5, AD-18, FR-27 | medium |
| S-18 | SSRF if FR-18 "add by URL" fetches the URL server-side | FR-18 map | medium |
| S-19 | Secrets and tokens in URLs and logs at the edge (Vercel/Render access logs) | Conventions (Logging) | low |
| S-20 | Sub-processor and transfer disclosure (Supabase, Render, Vercel, Brevo, Apify, LLM) | Stack, AD-11 | low |

---

## Findings

### S-1: The do-not-contact list and opt-out tokens are tied to deletable, per-Student Contact rows (critical)

- **Location:** AD-10 ("`outreach` owns the do-not-contact list and opt-out tokens"), AD-13 (`ON DELETE CASCADE` from `accounts` covers all Student-owned rows), AD-17 (Contact personal data purged after 90 days), ERD (`APPLICATIONS ||--o{ CONTACTS`, `DO_NOT_CONTACT }o--o{ CONTACTS`).
- **Risk:** Contacts are per-Application rows, so each Student has their own copy of the same person. The ERD links do-not-contact to Contacts. If DNC entries or opt-out tokens reference `contacts`, then:
  - a Student's account deletion cascades them away;
  - the 90-day purge removes them;
  - the PRD rule "marks the Contact do-not-contact **for all Students**" (FR-23) cannot be evaluated, because another Student's row for the same person has a different id.

  A person who opted out gets emailed again. That breaks CAN-SPAM opt-out honouring, the GDPR Art. 21 right to object, and the DPDP right to withdraw. A link in an old email that stops working after the sender deletes their account is a second breach: CAN-SPAM requires the mechanism to work for at least 30 days after sending.
- **AD-rule fix:** Add to AD-10: "The do-not-contact list is a standalone `outreach` table keyed by a normalised identifier hash (HMAC-SHA256 of the lower-cased email, plus the LinkedIn public profile URL hash), with no foreign key to `contacts` or `accounts`. It is excluded from account cascade and from Contact purges, and `send_outreach` and Discovery match against it by hash." Add: "Opt-out tokens live in their own table with no foreign key to Student-owned rows. Each holds the identifier hash, not a `contact_id`, so it stays valid after the Contact, the Outreach or the Student account is deleted."

### S-2: Procrastinate and other non-Alembic tables are outside the RLS/no-grant invariant (critical)

- **Location:** AD-1 ("Row-Level Security is enabled on every table with no `anon` or `authenticated` policies"), AD-4 (Procrastinate on Supabase Postgres), AD-13 ("Every schema change is an Alembic migration").
- **Risk:** Procrastinate creates its own tables, functions and triggers (`procrastinate_jobs`, `procrastinate_events`, ...) through its own schema tool, not Alembic. On Supabase, tables in the `public` schema are exposed through the Data API (PostgREST/GraphQL) to the `anon` key shipped in the web bundle, unless RLS is on or the grants are revoked. Job args hold entity ids and possibly personal data. With INSERT reachable, anyone could enqueue arbitrary tasks, such as purges, provider runs on a Student's key, or recompute storms. The same gap applies to any table created by an extension or by hand, and to Postgres functions callable via RPC.
- **AD-rule fix:** Add to AD-1: "All app and Procrastinate objects live in a non-exposed schema (e.g. `app`, `queue`) that is not in the Supabase Data API's exposed schemas. `anon` and `authenticated` have no USAGE on those schemas, and a migration revokes EXECUTE on their functions from `public`." Add to AD-13: "A CI test fails if any table in an exposed schema lacks RLS or grants any privilege to `anon` or `authenticated`. The Procrastinate schema is applied by an Alembic migration that wraps Procrastinate's SQL."

### S-3: The opt-out footer is a placeholder in a body the Student can edit, and the send guard does not verify it (high)

- **Location:** AD-15 (output "has a placeholder for the opt-out footer"), AD-10 (send guard checks list), FR-23 ("every email contains an opt-out link").
- **Risk:** The Student can edit Drafts (FR-22). Nothing in AD-10 stops an edited body from dropping the placeholder or faking the footer, and the template drafter path is not covered either. Every Outreach without a working opt-out breaches CAN-SPAM. The spine also says nothing about the other CAN-SPAM content duties: sender identification, a physical postal address for messages that could be classed as commercial, and a non-deceptive subject.
- **AD-rule fix:** Add to AD-10: "The opt-out footer (fresh token link plus sender identification and the configured postal address) is not part of the editable body. `send_outreach` appends it when the MIME message is built, for Outreach and Follow-ups, and refuses to send if footer construction fails." Add: "The body the Student edits and the body that was sent are stored separately, and the stored sent body includes the footer for evidence."

### S-4: The opt-out endpoint design is unspecified (high)

- **Location:** AD-5 ("FastAPI verifies the Supabase JWT on every request"), AD-10, deployment diagram (`contact -->|opt-out link| web`).
- **Risk:** The Contact is not a user, but AD-5 as written requires a JWT on every request, so a builder will either break opt-out or create an undocumented auth bypass. Other parts are missing:
  - **Token entropy and storage.** Tokens stored in plaintext are an enumeration target and a DB-leak risk.
  - **"One-time" semantics.** If a token stops working after first use, a second click shows an error and the person believes they are still subscribed. A token that works more than once lets a third party who holds the email opt the person out, which is acceptable.
  - **Link prefetch.** Mail security scanners and link previews issue GET requests and would trigger opt-out with no human click. Accidental opt-out is safe, but the event then misstates consent.
  - **Rate limiting.** Without it the endpoint can be brute-forced.
  - **Notice.** The confirmation page has no rule to give GDPR Art. 14 / DPDP notice: the data source, who processed it, and how to request erasure.
- **AD-rule fix:** Add to AD-10: "Opt-out tokens are 128-bit or larger CSPRNG values, one per Outreach message. Only their SHA-256 hash is stored. The public route `POST /api/v1/public/opt-out` is the only unauthenticated Student-API route, is listed in an explicit `PUBLIC_ROUTES` allowlist that AD-5's auth dependency honours, and is rate-limited per IP." Add: "GET renders a confirm page and only POST records the opt-out. Redeeming a token is idempotent, always returns success, and never reveals whether the token or Contact exists. The page shows the data source, the controller contact and a link to request erasure."

### S-5: Untrusted PDF/DOCX parsing runs in the single shared API process (high)

- **Location:** Files convention ("type and size checked before upload"), Stack (`pdfplumber`, `python-docx`), FR-3/FR-4/FR-26 mapping to `profile`.
- **Risk:** Resumes and Documents are attacker-controlled input. pdfminer (under pdfplumber) and the zip/lxml stack (under python-docx) are open to decompression bombs, deeply nested objects, huge page counts and zip bombs. XML external entity handling is not pinned. The API runs on one Render Free instance with 512 MB. A single malicious upload parsed inside a request can OOM-kill the process that also serves every other Student and runs the job drain. "Type checked" by extension or `Content-Type` is spoofable. DOCX files can carry macros or external relationships that are later served back to the owner.
- **AD-rule fix:** Add a Files rule to AD-17 or a new AD: "Uploads are validated by magic bytes and a structural check (a PDF header; a DOCX zip with total uncompressed size under 25 MB, at most 500 entries, a compression ratio under 100:1, and no `vbaProject` or external relationships) before storage. Text extraction never runs in the request: it is a `profile.extract_resume` job with a page cap (e.g. 10), a wall-time cap and a memory guard, run in a subprocess or with resource limits." Add: "XML parsing uses a parser with entity resolution and network access disabled. A parse failure yields 'no text extracted' (FR-4), never a 500 error."

### S-6: The Gmail OAuth connect flow has no state/PKCE binding, refresh or revocation invariants (high)

- **Location:** AD-6 (token encryption only), AD-16 (`gmail.send` only), FR-19 mapping to `accounts` and `adapters/gmail`.
- **Risk:** The spine covers storage only, not the OAuth flow:
  - **No `state` bound to the session and no PKCE on the callback.** This allows login-CSRF or account linking: an attacker's Gmail gets attached to a victim's account, or a victim's grant lands in the attacker's account.
  - **No owner of refresh concurrency.** Two jobs refreshing at once can rotate tokens and drop one.
  - **No rule for `invalid_grant`.** Queued sends then fail and retry forever.
  - **Disconnect and account deletion only delete the DB row.** The Google grant stays live, and the PRD says "tokens are deleted".
  - **Token reuse.** A builder may reuse the Supabase Google sign-in `provider_token`, which has different scopes and lifecycle.
- **AD-rule fix:** Add to AD-16: "Gmail connect is a separate OAuth client flow owned by `accounts`. It uses PKCE and a single-use `state` that is bound to the authenticated account id and expires in 10 minutes. The callback rejects any mismatch, and the Supabase Auth provider token is never used for sending." Add: "Disconnect and account deletion call Google's token revocation endpoint before deleting the ciphertext. On `invalid_grant` the connection is marked `revoked`, queued Outreach for that owner is paused (not retried), and the Student is notified."

### S-7: Deletion is incomplete (high)

- **Location:** AD-13 (cascade from `accounts`), AD-17 (account deletion = cascade + `purge_files`), AD-18 (`admin_audit`, `ops_events` FK to accounts), Deferred (scheduled `pg_dump` exports).
- **Risk:** "Delete account and all data within 30 days" (FR-2) is missed in several places.
  - **(a) Supabase Auth user.** `accounts.id` equals the Auth user id, but cascading from `accounts` does not delete `auth.users`. The email, password hash and identities survive.
  - **(b) Job queue rows.** Procrastinate rows (pending, finished and events) keep args, which may include ids, emails or text. The queue is outside the cascade and keeps finished jobs indefinitely.
  - **(c) In-flight jobs.** A `send_outreach` job already locked can still send after deletion unless it aborts when its row is missing.
  - **(d) `purge_files` source.** If `purge_files` takes its file list from rows the cascade just deleted, it has nothing to purge.
  - **(e) Audit and ops FKs.** `admin_audit` and `ops_events` either cascade, which destroys the audit trail, or block deletion.
  - **(f) Google grants.** These are not revoked (S-6).
  - **(g) Backups.** `pg_dump` exports and Supabase backups keep deleted data with no stated retention.
  - **(h) Contact purge scope.** The Contact purge does not name Outreach and Follow-up bodies, which contain the Contact's name.
- **AD-rule fix:** Rewrite AD-17's account rule as an ordered `accounts.delete_account` job:
  1. Cancel and delete all queue rows whose args reference the owner.
  2. Revoke the OAuth grants.
  3. Delete the DB rows by cascade.
  4. Delete the Supabase Auth user.
  5. Purge Storage by prefix `<owner_id>/`, not from DB rows.

  Every task's first step aborts if its entity row no longer exists. Add these rules:
  - Job args carry only ids, never personal data or bodies.
  - Finished jobs are deleted after 7 days.
  - `ops_events` and `admin_audit` reference the actor by a nullable FK with `ON DELETE SET NULL` plus a pseudonymous id.
  - Off-platform backups keep data for no more than 30 days, so deletion takes effect within the PRD window.
  - The Contact purge covers Contacts, Outreach and Follow-up bodies, and opt-out token rows except their hash (S-1).

### S-8: LLM data exposure to a free-tier provider (high)

- **Location:** AD-15 (Drafter inputs include "the Contact's name and role" plus Resume facts), Stack ("Groq or Gemini, free tier").
- **Risk:** Under Google's terms, Gemini API free-tier content may be used to improve Google products and may be reviewed by humans. Groq's terms differ. The Contact never consented and is never told that their name is sent to an LLM, which fails the minimisation the PRD promises in §5 Privacy and §8. The Student's `llm_drafting` consent can only cover their own data. The spine pins neither the provider's retention and training terms nor a region.
- **AD-rule fix:** Add to AD-15: "The Drafter never receives Contact personal data. The Contact's name is passed as the token `{{contact_name}}` and substituted after generation, and only the Contact's role or title text is sent." Add: "The configured LLM provider and tier must contractually exclude training on API inputs and retain them no longer than 30 days. This is recorded in config next to the pinned model, and if no compliant free tier exists, the template drafter is the default."

### S-9: No minimisation allowlist for Contact data or raw Apify output (high)

- **Location:** AD-3 (`ProviderRunner` for discovery and lookup), AD-12 (`provider_runs` records cost and outcome), AD-17 (purge "90 days after the last Outreach activity").
- **Risk:** Apify LinkedIn and people-search actors return full profiles: photos, location, education, work history, and sometimes personal emails and phone numbers. Nothing in the spine says what may be kept. Raw actor datasets can end up in `provider_runs`, `ops_events` or job args. Contacts that are proposed but dismissed, or never contacted, have no "last Outreach activity", so the 90-day clock never starts and the data lives forever. Personal (non-work) emails returned by a lookup are a further risk.
- **AD-rule fix:** Add to AD-10 or a new AD: "The `ProviderRunner` adapter maps actor output to a `ContactDraft` allowlist (name, current title, company, public profile URL, work email, email confidence, proposal reason, source, `collected_at`) and discards everything else in memory. Raw datasets are never persisted, logged or put in job args, and emails on free-mail domains are dropped." Add to AD-17: "Dismissed Contacts are hard-deleted at once. The Contact retention clock starts at `max(collected_at, last_outreach_at)`, and the purge job enforces a 90-day limit on both."

### S-10: Abuse controls for a Student using the platform to spam or harass (high)

- **Location:** AD-10 (per-Student caps), Deferred ("API rate limiting: add if abuse appears"), FR-27 ("review reports of misuse"), PRD §8 Anti-spam.
- **Risk:**
  - **Caps are per account.** Sign-up is free and nothing in the spine requires a verified email or limits accounts per person or Gmail.
  - **"Applied" is self-asserted.** A Student can create a manual Listing for any company, mark it Applied and run Discovery, so the "3 per company" cap only limits one Application.
  - **Manual email entry.** A Student can type in any address, so the platform can be pointed at a person who is not an employee of that company.
  - **No suspension path.** No complaint or opt-out rate triggers a pause (SM-C1), and Admins cannot suspend a Student's outreach.
  - **Shared LLM quota.** One Student can exhaust it for everyone.
  - **Rate limiting deferred.** If it waits until abuse is seen, it arrives after the harm is done and after Google flags the OAuth app.
- **AD-rule fix:** Add to AD-10:
  - Outreach requires a verified email on the account and one connected Gmail account per CareerLaunch account; a Gmail account cannot be connected to two accounts.
  - `send_outreach` enforces a per-company cap per Student across all Applications (e.g. 5 per 30 days), on top of the per-Application cap.
  - A Student whose opt-outs exceed a threshold (e.g. 3 in 7 days) is automatically set to `outreach_suspended`, and the Student is notified.

  Add to AD-18/AD-5: "Admins can suspend and unsuspend a Student's outreach through `outreach.service` (audited). The send guard checks the suspension flag, and LLM drafting and Discovery have per-Student daily quotas enforced in their services."

### S-11: `/internal/jobs/drain` protection is under-specified (medium)

- **Location:** AD-4 ("with a shared secret header"), Conventions API ("`/internal/...` for scheduler only (secret header, not in the public schema)"), source tree (`supabase/` holds "pg_cron schedule SQL").
- **Risk:**
  - **Secret in the repo.** The pg_cron SQL checked into `supabase/` is the natural place for a builder to paste the secret.
  - **Secret readable in the DB.** It is also readable in `cron.job` by anyone with DB access.
  - **Comparison.** Nothing requires a constant-time compare.
  - **Repeat calls.** Nothing limits how often the endpoint can be hit. Calls are bounded but can be repeated, so a leaked secret lets anyone drive Apify spend timing and wake-up load.
  - **No rotation plan.**
  - **Not mounted under the API.** "Not in the public schema" hides the route but does not protect it.
- **AD-rule fix:** Add to AD-4: "The drain secret is a 256-bit random value. It is stored in Supabase Vault and read by the cron SQL at runtime, never in repo SQL. The API compares it with `hmac.compare_digest` against one of two configured values, current and next, so it can be rotated." Add: "The drain endpoint takes no parameters, always returns 202, runs at most one worker at a time (an advisory lock, so extra calls are no-ops) and is mounted on a separate router excluded from OpenAPI and CORS."

### S-12: IDOR gaps (medium)

- **Location:** AD-5 ("every Student-owned table has a non-null `owner_id`"), AD-2 (cross-module service calls), Tasks layer.
- **Risk:**
  - **Child tables.** Tables such as `followups`, `outreach`, `contacts`, `application_status_history` and `criteria_overrides` are owned indirectly. A builder may leave out `owner_id` and authorise by parent id only, or not at all, for example in `GET /followups/{id}`.
  - **Jobs.** Jobs have no auth context, so tasks may trust an `owner_id` in their args.
  - **Cross-module calls.** Calls between services may pass ids without an owner.
  - **JWT verification.** The algorithm, `aud` and `iss` checks and the JWKS source are not pinned. Supabase's JWT also carries a `role` claim (`authenticated` or `service_role`) that a builder could confuse with the app role.
- **AD-rule fix:** Tighten AD-5:
  - Every table holding Student data, including child tables, has a non-null `owner_id`, and a composite FK `(parent_id, owner_id)` keeps it consistent with the parent.
  - Every public service function takes `owner_id` as its first parameter.
  - Tasks load the entity and take `owner_id` from the row, never from job args.
  - JWTs are verified against Supabase's JWKS (asymmetric) with `aud=authenticated`, a pinned `iss` and an explicit algorithm allowlist. A missing `accounts` row means 401.

  Add a test: each Student route is called with another Student's id and must return 404.

### S-13: Prompt injection and stored XSS from scraped job descriptions; email header injection (medium)

- **Location:** AD-15 (trimmed JD passed to Drafter; validation is length, company and Listing name only), AD-7 (descriptions stored), AD-16 (Mailer).
- **Risk:**
  - **Prompt injection.** Scraped JDs and manual Listing text are attacker-controllable, since anyone can post a LinkedIn job. Injected instructions can make drafts include phishing links, contact details or false claims. Student approval is the last gate, but the validator does not catch them.
  - **Stored XSS.** JD text, Contact names and company names rendered as HTML in `web` lead to stored XSS that runs with the Student's session token.
  - **Header injection.** Contact names, emails or company names with CR/LF placed into headers or the subject allow header injection in Mailer.
- **AD-rule fix:** Add to AD-15: "Untrusted text (JD, company, Contact role) is passed to the model only inside delimited data fields with a system instruction to treat it as data. Validation rejects output with URLs, email addresses or phone numbers that are not in the Student's own profile or the Listing URL, and falls back to the template." Add a convention: "`web` renders all scraped or user text as text (no `dangerouslySetInnerHTML`, no markdown-to-HTML without a sanitiser), and `Mailer` builds messages only with the `email` library's header API and rejects header values containing CR or LF."

### S-14: Master key handling (medium)

- **Location:** AD-6 (AES-GCM, master key in the Render environment, key-version prefix), Deferred (rotation), AD-12 (owner Apify key in env).
- **Risk:**
  - **Swappable ciphertext.** Without associated data, ciphertext can be moved between rows or owners: someone with DB write access could swap Student A's token ciphertext into Student B's row and it would decrypt.
  - **Key exposure.** The key is visible to anyone with Render dashboard access, and if it is ever stored next to the `pg_dump` exports one leak exposes everything.
  - **Nonce handling.** Nonces are not pinned to random 96-bit values per encryption.
  - **No compromise plan.** Rotation is deferred and nothing says what happens if the key leaks.
  - **Seed key reach.** The owner's Apify seed key in env is reachable by any code path that calls `start_provider_run`.
- **AD-rule fix:** Add to AD-6:
  - Encrypt with AES-256-GCM using a random 96-bit nonce per encryption, and use `owner_id || field_name || row_id` as associated data, so moved ciphertext fails to decrypt.
  - The master key exists only in Render env vars, never in backups, the repo or the DB.
  - Render and Supabase accounts require 2FA.
  - The response to a key compromise is to revoke all OAuth grants and mark all Provider Keys invalid. No re-encryption tooling is needed in v1.

  Add to AD-12: "The owner key is loaded only by a `listings.seed_pool` command that Admins run, and never by Student-triggered paths."

### S-15: Signed URL scope and content-type serving (medium)

- **Location:** Files convention (private bucket, `<owner_id>/<kind>/<uuid>`, signed URLs of 5 minutes or less).
- **Risk:**
  - **Bearer URLs.** A signed URL works for anyone who has it, until it expires. If issuance takes a storage path from the client, rather than a DB row checked by `owner_id`, path manipulation is an IDOR.
  - **Inline rendering.** A file served inline from the Supabase storage origin with a sniffable type, such as HTML disguised as `.txt`, can be used for phishing or content spoofing.
  - **Leaky file names.** Original file names may carry personal data into paths and logs.
- **AD-rule fix:** Add to the Files convention: "Signed URLs are issued only by `profile.service.get_download_url(owner_id, document_id)`, which resolves the path from the owner-scoped row. Clients never send storage paths." Add: "Objects are uploaded with a server-set `Content-Type` from the magic-byte check, and downloads force `Content-Disposition: attachment`. Original file names are stored only in the DB row, never in the object path."

### S-16: CORS and CSRF between Vercel and Render are not pinned (medium)

- **Location:** Conventions API, AD-1, deployment diagram.
- **Risk:**
  - **CSRF.** Bearer-token auth is immune to CSRF only if the API never accepts cookies. Supabase SSR helpers put sessions in cookies, and a builder may add cookie auth for Next.js server components.
  - **CORS.** A permissive setting such as `*`, or a regex like `*.vercel.app` that matches any Vercel customer's app, together with `allow_credentials`, exposes Student data.
  - **Callback.** The OAuth callback and opt-out routes are cross-origin by nature and need their own handling.
- **AD-rule fix:** Add to AD-5: "The API authenticates only from the `Authorization: Bearer` header and ignores cookies, and `allow_credentials` is false." Add: "CORS allows an exact list of origins from config (the production domain plus, optionally, the project's own preview domain pattern), never `*` or `*.vercel.app`. The OAuth callback relies on `state` (S-6) and the opt-out route on its token (S-4)."

### S-17: Admin audit is not tamper-evident; admin assignment and the misuse path are undefined (medium)

- **Location:** AD-5 (role loaded from `accounts`), AD-18 (`admin_audit` "append-only"), FR-27 ("review reports of misuse").
- **Risk:**
  - **Append-only in name only.** "Append-only" is a convention, but the API's DB role can UPDATE or DELETE audit rows.
  - **Role assignment.** Nothing says how an account becomes Admin, so a builder may expose a role field in a Student-editable profile DTO.
  - **Audit coverage.** Role changes, DNC removals and source kill-switch changes are not named as audited.
  - **Misuse reports.** No component receives misuse reports, and Admins cannot see Outreach bodies (correctly), so they have no way to act.
- **AD-rule fix:** Add to AD-18: "A migration revokes UPDATE and DELETE on `admin_audit` from the application role, and audit rows are written in the same transaction as the admin action." Add to AD-5: "The `role` column is set only by a CLI or migration, not by any API route, and every role change, DNC addition or removal, Source pause and outreach suspension writes `admin_audit`." Add an `admin.misuse_reports` table, fed by an unauthenticated report link on the opt-out page. Each report shows Admins metadata only (sender account id, timestamps, counts) and can trigger the S-10 suspension.

### S-18: SSRF if FR-18 "add by URL" fetches the URL server-side (medium)

- **Location:** FR-18 mapping to `tracker` and `listings`. The spine is silent on whether the URL is fetched.
- **Risk:** If the API fetches a Student-supplied URL to prefill the details, it can be pointed at internal addresses: Render metadata, Supabase internals or localhost services.
- **AD-rule fix:** Add to AD-7: "Manual Listing URLs are stored as given and never fetched by the server in v1. If prefill is added later, it goes through a single `UrlFetcher` port that allows only `https`, resolves DNS and blocks private, link-local and loopback ranges, and caps size and time."

### S-19: Secrets and tokens in URLs and logs at the edge (low)

- **Location:** Conventions Logging (app-level redaction only).
- **Risk:** Opt-out tokens, OAuth `code`/`state` and signed-URL tokens in query strings are recorded in Vercel and Render access logs, which the app's redaction never sees.
- **AD-rule fix:** Add to the Logging convention: "Tokens never travel in query strings to our own hosts. The opt-out token goes in the POST body after the confirm page reads it from the URL fragment or path, and OAuth callback query parameters are stripped from request logging."

### S-20: Sub-processor and transfer disclosure (low)

- **Location:** Stack (Supabase, Render, Vercel, Brevo, Apify, LLM provider), AD-11 (consent purposes), Deferred OQ-4.
- **Risk:** GDPR and DPDP notices must list the processors and the cross-border transfers. The Supabase region and the US-based Render are not pinned, and the consent records do not record which processor list a Student agreed to.
- **AD-rule fix:** Add to AD-11: "Each consent record stores the version of the privacy notice, which lists the sub-processors and their regions. Changing the processor list bumps that version and requires fresh consent for the affected purposes." Pin the Supabase project region in the environment table.

---

## What the spine already gets right (keep)

- AD-1: the single gateway and RLS-with-no-policies posture (once S-2 extends it to every schema).
- AD-5: role loaded from the DB, never from claims; admins blocked from Resumes, bodies, keys and tokens at the service layer.
- AD-6: encryption confined to `core/crypto`, a key-version prefix, and only `has_key` and the last 4 characters exposed.
- AD-10: one send path, re-checking consent, DNC, caps and status right before sending; cancellation in the same transaction.
- AD-11: consent checked at enqueue and again in the job, and revocation cancels pending jobs.
- AD-16: the `gmail.send` scope only, with no read scopes.
- AD-17/AD-18: deletion and retention as idempotent jobs, and no secrets or bodies in ops logs.

## Suggested order

Fix S-1, S-2, S-3 and S-4 before the outreach epic. Fix S-5 before the Resume upload story in weeks 1–2. Fix S-12 and S-16 at scaffold. Fix S-6 before FR-19. Fix S-7, S-8, S-9 and S-10 before launch. The rest can wait for the hardening weeks.
