# Review: Technology Currency and Fit — CareerLaunch Architecture Spine

- **Reviewed:** `ARCHITECTURE-SPINE.md` (draft, 2026-11-07) and `.memlog.md`
- **Review date:** 2026-11-07
- **Method:** Live registry queries (PyPI JSON API, npm registry, nodejs.org dist index, Node Release schedule, endlife.date), the Procrastinate 3.10.0 sdist source, the create-next-app 16.4.5 tarball, and web/doc checks against vendor pages. Where only third-party or community sources were found, the finding says so.

## Verdict

The library pins are real and current: every one matches the registry's latest version as of today. The runtime picks are already behind, though. Python 3.13 went security-only on 2026-10-01, and Node 24 moved to Maintenance LTS on 2026-10-20. Several platform claims the spine relies on were never checked or are wrong. The biggest is the 7-day Gmail refresh-token expiry in OAuth Testing mode, which the spine misses. Next come two Procrastinate claims that its docs and source do not support (stalled-job recovery and a job-count bound). Both the spine and the memlog also leave out how UUID v7 IDs get generated.

## Top findings

| # | Severity | Finding |
| --- | --- | --- |
| F1 | **High** | Google OAuth apps in **Testing** status get refresh tokens that **expire after 7 days** (gmail.send is not exempt). The prod plan of "testing mode until verified" therefore breaks every Student's Gmail connection weekly, and follow-ups scheduled more than 7 days out will fail. This is not in the spine or the memlog. |
| F2 | **High** | AD-4 says "stalled jobs are retried after their lock expires", but Procrastinate has no lock expiry. It detects stalled workers by heartbeat (`stalled_worker_timeout`, default 30 s). Stalled jobs are **not** retried automatically: the app must call `job_manager.get_stalled_jobs()` and then `retry_job()`. |
| F3 | **Medium** | AD-4's "worker with `wait=False`, bounded by job count and wall time" is only half supported. `wait=False` exits once the queues are caught up. Procrastinate has **no job-count limit**, so the wall-time bound has to be built by the app (an asyncio timeout plus `shutdown_graceful_timeout`). |
| F4 | **Medium** | UUID v7 has no source. Supabase hosted projects run Postgres 17, which has no native `uuidv7()` (Postgres 18 is not GA on hosted Supabase per community and maintainer reports). Python 3.13 has no `uuid.uuid7()` either; it arrived in Python 3.14. One of three fixes is needed: move to Python 3.14, add a dependency (e.g. `uuid-utils`), or drop v7. |
| F5 | **Medium** | The runtime rows are stale. Python 3.13's bugfix support ended 2026-10-01; Render's default for new services is **3.14.10**. Node 24 has been Maintenance LTS since 2026-10-20; **Node 26 is Active LTS** (since 2026-10-28) and is Vercel's default. |
| F6 | Medium | AD-4's "deferred in the same DB transaction" works with SQLAlchemy 2.1 + psycopg 3, but only by pulling out the raw `psycopg.AsyncConnection` and passing it to `task.configure(connection=...)`. Procrastinate's SQLAlchemy contrib connector is **psycopg2-only** and must not be used. The spine should record this pattern. |
| F7 | Medium | A pg_cron drain every 5 minutes means Render never reaches its 15-minute idle spin-down. The service runs about 24/7 (~720–744 h/month) against Render's **750 free instance hours per workspace**. Any second free service (e.g. staging) would push the workspace over, and all free services would be suspended. |
| F8 | Medium | Supabase direct connections are **IPv6-only on Free** (confirmed in Supabase docs). The session pooler is therefore required, not "may be". The spine still lists this as a deferred ops check. Use the **session** pooler (port 5432), never the transaction pooler (6543). The transaction pooler drops LISTEN/NOTIFY and prepared statements, and psycopg 3 auto-prepares statements. |
| F9 | Low | pg_net's `timeout_milliseconds` defaults to 2,000 ms (Supabase docs) or 5,000 ms (current pg_net README). The cap is `pg_net.max_timeout_ms`, default 600,000 ms. Set it to about 120,000 ms explicitly in the cron SQL so a Render cold start (~1 min) is not recorded as a failure. |
| F10 | Low | Supabase Free includes **no** automatic backups (the spine says "limited backups"). File uploads are capped at **50 MB**, and Storage is 1 GB. Brevo Free (300/day, Brevo branding) caps daily alert digests at about 300 Students per day. |
| F11 | Low | The TypeScript 5.9.3 pin is reasonable. The create-next-app 16.4.5 template sets `"typescript": "^5"`, which resolves to 5.9.3, the last 5.x. The memlog missed that TS **6.0.x** (6.0.3, Apr 2026) exists between 5.9 and 7.0. |

## 1. Stack row verification (registries, queried 2026-11-07)

| Stack row | Spine | Registry latest | Release date | Requires | Status |
| --- | --- | --- | --- | --- | --- |
| FastAPI | 0.162.0 | 0.162.0 | 2026-10-23 | py>=3.10; starlette>=0.46, pydantic>=2.9 | Current |
| Pydantic | 2.14.3 | 2.14.3 | 2026-11-05 | py>=3.10 | Current |
| Uvicorn | 0.54.0 | 0.54.0 | 2026-09-25 | py>=3.10 | Current |
| SQLAlchemy | 2.1.8 | 2.1.8 | 2026-11-04 | py>=3.11; psycopg extra `psycopg>=3.0.7,!=3.1.15` | Current |
| Alembic | 1.20.1 | 1.20.1 | 2026-10-22 | py>=3.10 | Current |
| psycopg | 3.3.8 | 3.3.8 | 2026-11-04 | py>=3.10 (classifiers list 3.10–3.15) | Current |
| Procrastinate | 3.10.0 | 3.10.0 | 2026-09-23 | py>=3.10; `psycopg[pool]` unpinned; extra `sqlalchemy~=2.0` | Current |
| cryptography | 50.0.2 | 50.0.2 | 2026-09-30 | py>=3.9 | Current |
| pdfplumber | 0.11.10 | 0.11.10 | 2026-06-15 | py>=3.8 | Current |
| python-docx | 1.2.0 | 1.2.0 | 2025-06-16 | py>=3.9 | Current (no newer release; package is slow-moving) |
| next | 16.4.5 | 16.4.5 | — | node>=20.9.0; peer react ^18.2 or ^19 | Current |
| react | 19.3.1 | 19.3.1 | — | — | Current; satisfies Next peer `^19.0.0` |
| typescript | 5.9.3 | 7.0.2 (`latest`) | 5.9.3: 2025-09-30; 7.0.2: 2026-07-08 | — | Reasonable pin (see §1.3) |
| @hey-api/openapi-ts | 0.99.0 | 0.99.0 | — | node>=22.18.0; peer typescript >=5.5.3 | Current |
| @supabase/supabase-js | 2.119.0 | 2.119.0 | — | node>=22.0.0 | Current |
| create-next-app | (16.4.x) | 16.4.5 | — | node>=20.9.0 | — |

**Gap:** `pydantic-settings` is required by the Config convention but is not a Stack row. `psycopg_pool` is pulled in by Procrastinate (`psycopg[pool]`). If a UUID v7 library is chosen (F4), add it too.

### 1.1 Mutual compatibility: SQLAlchemy 2.1 / Procrastinate 3.10 / psycopg 3.3

- Procrastinate 3.10.0 `requires_dist`: `psycopg[pool]` (no version bound) and, for its optional `sqlalchemy` extra, `sqlalchemy~=2.0` (that is, >=2.0,<3). **SQLAlchemy 2.1.8 and psycopg 3.3.8 both satisfy these.** The 3.10 core connector is `PsycopgConnector` (psycopg 3 `AsyncConnectionPool`); `SyncPsycopgConnector` is also available.
- **Caveat (F6):** `procrastinate.contrib.sqlalchemy` only provides `SQLAlchemyPsycopg2Connector`. Its README says it is for "synchronous applications that already use SQLAlchemy and Psycopg2". It does not fit an async SQLAlchemy + psycopg 3 stack. To defer in the same transaction, use the documented external-connection path, `task.configure(connection=<psycopg.AsyncConnection>).defer_async(...)`. The raw connection is obtained from the SQLAlchemy `AsyncSession` (`(await session.connection()).get_raw_connection().driver_connection`). Both writes then commit together. Also note: a job insert error (e.g. a queueing-lock violation) aborts the whole transaction unless it is wrapped in a savepoint, and NOTIFY fires only on commit. Source: [Procrastinate – Using an external database connection](https://procrastinate.readthedocs.io/en/stable/howto/production/external_connection.html); verified in source (`procrastinate/jobs.py` and `psycopg_connector.py` accept a `connection` parameter).

### 1.2 Runtimes

| Runtime | Spine | Status on 2026-11-07 | Recommendation |
| --- | --- | --- | --- |
| Python | 3.13 | 3.13 entered **security-only** on 2026-10-01 (EOL 2029-10). 3.14 (latest 3.14.10) is in bugfix support until 2027-10. 3.15.2 is out. **Render's default Python for new services is 3.14.10** (services created on or after 2026-11-03). | Change to **3.14**. It adds stdlib `uuid.uuid7()` (fixes F4 with no new dependency) and is Render's default. cp314 wheels confirmed on PyPI for pydantic-core 2.50.4, cryptography 50.0.2, psycopg-binary 3.3.8, pypdfium2 5.15.0 and lxml 6.1.3. If you stay on 3.13, record it as a deliberate choice and pin `PYTHON_VERSION`. |
| Node.js | 24 LTS | v24 "Krypton" is in **Maintenance LTS since 2026-10-20** (EOL 2028-04-30). **v26 "Lithium" has been Active LTS since 2026-10-28** (EOL 2029-04-30). **Vercel's default is 26.x**; 22.x, 24.x and 26.x are all available. | 24 is still supported and acceptable, but the row should read "24 (Maintenance LTS)" or move to **26 LTS**. Whichever you choose, pin it via `engines.node` so Vercel does not silently build on 26 while local dev runs 24. All npm deps need node>=22.18, which both satisfy. |

### 1.3 TypeScript pin

- The create-next-app 16.4.5 dist (tarball inspected) writes `typescript: "^5"` into the generated `package.json`. A fresh scaffold therefore resolves to **5.9.3**, the last 5.x release. It does **not** default to 7.x.
- TS 6.0.x (6.0.2 Mar 2026, 6.0.3 Apr 2026) is the transition release; 7.0.x is the native (Go) compiler.
- `@hey-api/openapi-ts` 0.99.0 peer: `typescript >=5.5.3 || >=6.0.0`.
- **Conclusion:** pinning 5.9.3 matches the template and is reasonable. The memlog's condition ("unless the template defaults to 7") is now settled: it does not. Revisit when Next's template moves off `^5`.

## 2. Fit claims

| Claim the spine depends on | Result | Evidence |
| --- | --- | --- |
| Supabase Free includes Postgres, Auth, Storage | **Confirmed.** 500 MB DB, 1 GB file storage, 5 GB egress, 50k MAU, 2 active projects, **50 MB upload cap**, **no automatic backups** | [supabase.com/pricing](https://supabase.com/pricing) |
| pg_cron, pg_net, pg_trgm on Free | **Confirmed in practice, not tied to a plan in the docs.** The extension pages do not gate by plan; pricing does not list them as paid features. Supabase Cron supports intervals from 1 s up, and recommends ≤8 concurrent jobs, each ≤10 min. History is kept in `cron.job_run_details`. | [Supabase Cron](https://supabase.com/docs/guides/cron), [pg_net docs](https://supabase.com/docs/guides/database/extensions/pg_net), [Extensions](https://supabase.com/docs/guides/database/extensions) |
| pg_net timeout configurable | **Confirmed.** `timeout_milliseconds` defaults to 2,000 ms (Supabase docs) or 5,000 ms (current pg_net README). The maximum is `pg_net.max_timeout_ms`, default **600,000 ms** (superuser-only). Rated for about 200 req/s. | [Supabase pg_net](https://supabase.com/docs/guides/database/extensions/pg_net), [github.com/supabase/pg_net](https://github.com/supabase/pg_net) |
| Direct connection IPv6-only; Supavisor session pooler | **Confirmed.** "Direct connections are on IPv6, or on IPv4 if the project has the IPv4 add-on" (Pro+ only). The shared pooler is IPv4. Session mode on 5432 supports prepared statements. Transaction mode on 6543 does **not** support prepared statements or LISTEN/NOTIFY. | [Connecting to Postgres](https://supabase.com/docs/guides/database/connecting-to-postgres) |
| Free project pausing | **Confirmed:** "Free projects are paused after 1 week of inactivity." **Not confirmed:** whether pg_cron's own activity counts. Community reports conflict, and a paused project stops pg_cron. In this design, Render queries the DB on every drain, which very likely counts as activity, but this is not officially documented. | [pricing](https://supabase.com/pricing); community: [travisvn/supabase-pause-prevention](https://github.com/travisvn/supabase-pause-prevention), [cronic blog](https://cronic.pages.dev/blog/preventing-supabase-free-tier-pausing/) |
| Render Free: 512 MB, 15-min sleep | **Confirmed.** 512 MB RAM, 0.1 CPU. Spins down after 15 min with no inbound traffic; spin-up takes about 1 min. **750 instance-hours per workspace per month** (when exhausted, all free services are suspended). Ephemeral FS, single instance, "may restart at any time". | [render.com/docs/free](https://render.com/docs/free), [compute plans](https://render.com/docs/compute-plans.md) |
| Render outbound SMTP | **Blocked on Free** (ports 25, 465 and 587). The Brevo adapter **must use the HTTPS transactional API**, never SMTP relay. Gmail goes over the HTTPS API, so it is unaffected. | [render.com/docs/free](https://render.com/docs/free) |
| Render request timeout | **Not in official docs.** Render staff on the community forum state 100 minutes. Not critical, because the drain returns 202. | [community.render.com thread](https://community.render.com/t/need-to-increase-requests-timeout-of-5-minutes/4001) |
| Procrastinate `run_worker_async(wait=False)` | **Confirmed it exists.** "If False, the worker will terminate as soon as it has caught up with the queues." **No job-count limit.** Other options: `shutdown_graceful_timeout`, `listen_notify` (default True; allocates a pool connection for LISTEN), `fetch_job_polling_interval`, heartbeat options. | Procrastinate 3.10.0 source `procrastinate/app.py`; [worker how-to](https://procrastinate.readthedocs.io/en/stable/howto/basics/worker.html) |
| Procrastinate with Supavisor | **Works with the session pooler; the transaction pooler is unsafe.** Job fetch uses row locks inside a transaction (fine), but `listen_notify` needs a persistent session, and psycopg 3 server-side prepares are not supported in transaction mode. For the short-lived drain, set `listen_notify=False` and keep the pool small (e.g. min 1, max 3). Procrastinate's docs have no Supavisor-specific guidance, so this is an inference from Supabase's pooler feature matrix. | [Supabase pooler modes](https://supabase.com/docs/guides/database/connecting-to-postgres); [PgBouncer feature matrix](https://pigsty.io/docs/pgbouncer/features/index.md) |
| Brevo free transactional API | **Confirmed by Brevo marketing pages and third parties** (the official pricing page did not render): 300 emails/day, SMTP **and** transactional API included, Brevo branding on free emails. | [brevo.com/free-smtp-server](https://www.brevo.com/free-smtp-server), [costbench Brevo free plan](https://costbench.com/software/marketing-automation/brevo/free-plan), [dreamlit Brevo review](https://dreamlit.ai/blog/brevo-review) |
| gmail.send is sensitive; 100-user cap | **Confirmed.** `gmail.send` is listed under "Sensitive scopes". Unverified apps are capped at 100 users. **New finding:** in Testing status, refresh tokens expire in 7 days unless the only scopes are openid, email and profile. | [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), [Unverified apps](https://support.google.com/cloud/answer/7454865), [OAuth2 – refresh token expiration](https://developers.google.com/identity/protocols/oauth2) |
| Vercel Hobby non-commercial | **Confirmed:** "the Hobby plan restricts users to non-commercial, personal use only." Function max duration 300 s. 1 hour of runtime logs. | [vercel.com/docs/plans/hobby](https://vercel.com/docs/plans/hobby) |
| UUID v7 in Supabase Postgres | **Not available natively.** Postgres 18 (released 2025-09-25) adds `uuidv7()`. Hosted Supabase is on Postgres 17: the upgrade guide only lists 15→17, and a maintainer said in May 2026 that PG18 would come "eventually in 2026". No official GA announcement found. `pg_uuidv7` is not confirmed on Supabase. **Generate in Python**: `uuid.uuid7()` in 3.14+, or a library on 3.13. | [PostgreSQL 18 release](https://postgresql.org/about/news/postgresql-18-released-3142/), [supabase discussion #42681](https://github.com/orgs/supabase/discussions/42681), [Python uuid docs](https://docs.python.org/3/library/uuid.html) |

## 3. Detailed findings and proposed wording

### F1 (High): Gmail refresh tokens expire after 7 days in Testing mode

**Problem.** The spine's prod row says "Google OAuth app in testing mode (up to 100 test users) until verified". Deferred says to start verification "once the send flow is demoable". Google issues refresh tokens that expire in 7 days to external apps in Testing status, unless the only scopes requested are openid, email and profile. With `gmail.send`, every Student must reconnect weekly. AD-10's scheduled follow-ups (and the next-day queue after a cap) will fail with `invalid_grant` whenever they run more than 7 days after the connection.

**Should say:**
- Add to AD-16 (or the Mailer port contract): "`GmailMailer` maps `invalid_grant` to `ConnectionExpired`. The send job then marks the Connected Account `expired`, reschedules (not cancels) queued Outreach and Follow-ups, and notifies the Student to reconnect. While the OAuth app is in Testing, refresh tokens last 7 days."
- Deferred, Google OAuth verification: "Start sensitive-scope verification early (it takes weeks). Until it is verified, Gmail connections expire every 7 days and follow-up schedules beyond 7 days depend on the Student reconnecting."

### F2 (High): Stalled-job recovery is described wrongly

**Problem.** AD-4 says "Stalled jobs are retried after their lock expires." Procrastinate 3.x has no lock-expiry mechanism. Workers write heartbeats (`update_heartbeat_interval` 10 s), and a worker with no heartbeat for `stalled_worker_timeout` (30 s) is stalled. `get_stalled_jobs()` returns jobs still in `doing` whose worker is stalled or missing, but nothing re-queues them automatically. On Render Free ("may restart at any time"; an instance restart mid-drain is plausible), jobs would stay stuck in `doing` forever.

**Should say:** "At the start of each drain (or as a periodic task), the drain calls `job_manager.get_stalled_jobs()` and re-queues each job with `retry_job()`. A job is stalled when its worker's heartbeat is older than `stalled_worker_timeout`." Idempotency (already required) makes this safe.

### F3 (Medium): The drain bound is not built into Procrastinate

**Should say:** "The drain runs `run_worker_async(wait=False, listen_notify=False, install_signal_handlers=False, concurrency=N)` inside `asyncio.wait_for(..., timeout=T)` with `shutdown_graceful_timeout`. If a job-count bound is needed, it is enforced by a worker middleware or counter, not by Procrastinate." Keep T well under 15 minutes.

### F4 (Medium): No source for UUID v7

**Should say (Consistency Conventions, IDs):** "UUID v7 primary keys are generated in the application (`uuid.uuid7()`, Python 3.14+) and passed on insert. They are not DB-generated, because Supabase Postgres 17 has no `uuidv7()`. `accounts.id` is the Supabase Auth uid (v4) and is the exception." The Auth exception matters: AD-5 makes `accounts.id` equal the Supabase Auth user id, which Supabase generates as v4, so the "UUID v7 primary keys" convention already has an unstated exception.

### F5 (Medium): Runtime rows

**Should say:** `Python | 3.14` (or `3.13 (security-only since 2026-10; deliberate)`) and `Node.js | 24 (Maintenance LTS) — pinned via engines.node` (or `26 LTS`). The memlog line "Python 3.13, Node 24 LTS chosen as runtimes" has no registry or lifecycle check behind it. It is the only Stack row decided without one.

### F6 (Medium): Same-transaction defer pattern

Add to AD-4 or `core/db`: "Jobs are deferred with `task.configure(connection=<raw psycopg AsyncConnection from the current SQLAlchemy session>)`, so the job row commits or rolls back with the state change. Do not use `procrastinate.contrib.sqlalchemy` (psycopg2-only). Job-insert errors are isolated with a savepoint when the business write must survive."

### F7 (Medium): Render instance hours

A request every 5 minutes means the 15-minute idle timer never fires, so the service runs about 24/7 (31 d × 24 h = 744 h, against 750 h per workspace). **Should say:** add to Deferred or the Environment row: "The Render workspace hosts exactly one free service; staging needs a separate workspace or a paid instance. Alternatively, drain every 20 minutes at night to save hours." This also makes the ~1-minute cold start mostly irrelevant after the first wake. The memlog's cold-start worry applies only to the first call after a deploy or restart.

### F8 (Medium): The pooler is a rule, not a deferred check

Move it from "Ops checks at scaffold" into the Config convention or AD-4: "`DATABASE_URL` uses the Supavisor **session** pooler (`aws-*.pooler.supabase.com:5432`, user `postgres.<ref>`). The direct host is IPv6-only on Free, and the transaction pooler (6543) is not used (no LISTEN/NOTIFY or prepared statements). Alembic and the drain use the same URL."

### F9 (Low): pg_net timeout value

Remove the hedge from Deferred and state: "`net.http_post(..., timeout_milliseconds := 120000)`. Max allowed is `pg_net.max_timeout_ms` (default 600000)." Also note that pg_net records results asynchronously in `net._http_response`. Check for `timed_out` rows when monitoring.

### F10 (Low): Supabase and Brevo specifics

- Deferred, backups: change "Free Supabase has limited backups" to "**Free Supabase has no automatic backups**".
- Files convention: add "max 50 MB per object (Supabase Free cap)". The PRD limits are probably lower; check them.
- Notifier: "Brevo Free = 300 emails/day with Brevo branding. Daily digests (one per Student) plus reminders must fit in that, or digests are deferred to the next day by the job."

### F11 (Low): TypeScript

No change to the pin. Update the memlog note: the template uses `^5`, and TS 6.0.x exists between 5.9.3 and 7.0.

## 4. Items not re-verified (out of scope or flagged as already open)

- Groq and Gemini free-tier limits and data-use terms: the memlog already marks these as volatile. The spine pins "model in config", so no stack change.
- Apify third-party token billing (OQ-1): still open; correctly a blocker in Deferred.
- Supabase Free connection limits for pooler clients: not verified. Keep the API and drain pools small (e.g. SQLAlchemy pool_size 3–5, Procrastinate max_size ≤3).

## Sources

- PyPI JSON API: `https://pypi.org/pypi/<pkg>/json` for fastapi, pydantic, uvicorn, sqlalchemy, alembic, psycopg, procrastinate, cryptography, pdfplumber, python-docx, pydantic-core, pypdfium2, psycopg-binary, lxml (queried 2026-11-07)
- npm registry: `https://registry.npmjs.org/<pkg>/latest` for next, react, typescript, @hey-api/openapi-ts, @supabase/supabase-js, create-next-app; tarball https://registry.npmjs.org/create-next-app/-/create-next-app-16.4.5.tgz
- Node releases: https://nodejs.org/dist/index.json, https://raw.githubusercontent.com/nodejs/Release/main/schedule.json
- Python lifecycle: https://endoflife.date/api/python.json; https://docs.python.org/3/library/uuid.html
- Procrastinate 3.10.0 sdist (source of `app.py`, `manager.py`, `sql/queries.sql`, `contrib/sqlalchemy/README.md`); https://procrastinate.readthedocs.io/en/stable/howto/production/external_connection.html; https://procrastinate.readthedocs.io/en/stable/howto/basics/worker.html
- Supabase: https://supabase.com/pricing; https://supabase.com/docs/guides/database/connecting-to-postgres; https://supabase.com/docs/guides/database/extensions/pg_net; https://github.com/supabase/pg_net; https://supabase.com/docs/guides/cron; https://supabase.com/docs/guides/database/extensions; https://github.com/orgs/supabase/discussions/42681
- PostgreSQL 18: https://postgresql.org/about/news/postgresql-18-released-3142/
- Render: https://render.com/docs/free; https://render.com/docs/compute-plans.md; https://render.com/docs/python-version; https://community.render.com/t/need-to-increase-requests-timeout-of-5-minutes/4001
- Vercel: https://vercel.com/docs/plans/hobby; https://vercel.com/docs/functions/runtimes/node-js/node-js-versions
- Google: https://developers.google.com/workspace/gmail/api/auth/scopes; https://support.google.com/cloud/answer/7454865; https://developers.google.com/identity/protocols/oauth2
- Brevo: https://www.brevo.com/free-smtp-server; https://costbench.com/software/marketing-automation/brevo/free-plan; https://dreamlit.ai/blog/brevo-review
- Pooler behavior: https://pigsty.io/docs/pgbouncer/features/index.md; community pausing reports: https://github.com/travisvn/supabase-pause-prevention, https://cronic.pages.dev/blog/preventing-supabase-free-tier-pausing/
