# Addendum — CareerLaunch

## Research digest (2026-11-06; third-party sources, verify before citing)
- Comparables: Huntr, Teal, Simplify (job-tracker CRMs, paid tiers), Handshake (school-gated), Wellfound, Internshala.
- Open source: srbhr/Resume-Matcher (resume/JD matching, FastAPI + Next.js, embeddings); python-jobspy (multi-board scraper, blocking risk).
- Job sources: Greenhouse `boards-api.greenhouse.io/v1/boards/{slug}/jobs`, Lever `api.lever.co/v0/postings/{company}`, Ashby posting API, Adzuna (free tier, key), RemoteOK JSON. Check each one's terms on redistribution.
- Legal: hiQ v. LinkedIn ended in a 2022 consent judgment after a ToS-breach finding; scraping LinkedIn carries ToS/account risk.

## Technical-how notes (not in PRD)
- Source Adapter: Apify LinkedIn jobs actor behind an interface; keep provider output mapped to the Listing schema.
- Matching reference: srbhr/Resume-Matcher (FastAPI + Next.js, embeddings). Start with skill-overlap plus embedding similarity.
- Search: PostgreSQL full-text (tsvector + pg_trgm) is enough at ~100k Listings; Meilisearch/Typesense is an alternative.
- Email: Gmail API / Microsoft Graph via OAuth; reply detection through mailbox thread polling or push.
- Queue/scheduler for ingestion, scoring, follow-ups (e.g. Celery/BullMQ).

## Email lookup and cost-model notes (2026-11-06; unverified, check before use)
- Apify email-finder actor candidates (run under the Student's key): davidsharadbhatt bulk email finder (name + domain), ryanclinton waterfall-contact-enrichment (multi-source with confidence), automation-lab email-enrichment (pattern + MX checks), toolzerhub professional-email-finder. Pricing ranges from per-1,000-results to monthly rental; status of each not verified.
- Open-source: tokenizer-decode/email-sleuth (Rust, pattern guessing + SMTP RCPT TO). Needs outbound port 25 (blocked on most free hosts); servers may flag rapid RCPT TO from one IP; may breach provider terms.
- Apify: tokens are per account; per-user billing for a third-party platform is not confirmed in official docs - verify.
- Free-tier hosting constraints: sleeping instances, limited background workers/cron, small DB quotas, blocked SMTP.

## Storage estimate (rough; free-tier quotas change, verify)
- Listing with description about 5-10 KB, about 1.5x with indexes and full-text index. 100k Listings is roughly 1-1.5 GB, over a typical 0.5 GB free Postgres/Mongo tier.
- About 20-30k active Listings is roughly 150-400 MB. 384-dim embeddings add about 1.5 KB each (about 45 MB at 30k).
- Retention (30 days after expiry), a pool cap, deduplication and trimmed descriptions keep it within quota. Resume files go to free object storage.
