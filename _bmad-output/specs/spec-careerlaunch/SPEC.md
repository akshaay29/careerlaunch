---
id: SPEC-careerlaunch
companions:
  - acceptance-criteria.md
  - glossary.md
  - user-journeys.md
  - ../../planning-artifacts/architecture/architecture-Careerlaunch-2026-11-07/ARCHITECTURE-SPINE.md
sources:
  - ../../planning-artifacts/prds/prd-Careerlaunch-2026-10-24/prd.md
  - ../../planning-artifacts/prds/prd-Careerlaunch-2026-10-24/addendum.md
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# CareerLaunch: Jobs, Internships and Placement Tracker

## Why

**Pain to solve, and a portfolio piece to build.** Students and fresh graduates who know they want a job or internship waste hours scrolling irrelevant openings, rarely get past the application pile, and lose track of deadlines and follow-ups. CareerLaunch turns that into a short loop: set Preferences, review a ranked feed, apply manually, and let the platform run approved referral outreach, with everything in one Tracker. It is a solo major project, built for real student use at zero platform cost, and its engineering must be defensible to evaluators: relational data model, role-based permissions, full-text search, document uploads, and a deterministic, explainable matching algorithm.

## Capabilities

Each CAP-N traces to PRD FR-N. The full testable consequences are in `acceptance-criteria.md`; the binding design rules are in `ARCHITECTURE-SPINE.md`.

**Phasing.** **Phase 1 (MVP, the core loop):** CAP-1 to CAP-10, CAP-13, CAP-16 to CAP-18, CAP-26 and CAP-28. **Phase 2:** CAP-11, CAP-12, CAP-14, CAP-15, CAP-19 to CAP-25, CAP-27 and CAP-29. Phase 2 capabilities stay specified so the MVP doesn't design them out, but they are not built in phase 1.

- **CAP-1: Accounts and roles**
  - **intent:** A visitor registers and logs in with email and password or Google, and holds exactly one role, Student or Admin.
  - **success:** Admin routes return 403 to Students, Students reach only their own records plus the shared pool, Admins never see Students' private content, and every check is enforced on the server.
- **CAP-2: Consent and data controls**
  - **intent:** A Student gives plain-language consent before any data collection or sending, can revoke it, and can delete their account and all their data.
  - **success:** Outreach, LinkedIn linkage, provider runs, pool contribution and LLM drafting are blocked until consent is timestamped, and deletion removes Resumes, Applications, Outreach and tokens.
- **CAP-3: Resume upload and versions**
  - **intent:** A Student uploads PDF or DOCX Resumes, keeps several versions, and marks one active for scoring.
  - **success:** Wrong types and files over 5 MB are rejected with a clear message, and changing the active Resume re-scores the Student's Listings.
- **CAP-4: Skill Profile extraction**
  - **intent:** The platform extracts skills, education and experience from the active Resume into a Skill Profile the Student can correct.
  - **success:** A text-less Resume produces a clear message and manual entry, and the Student's edits survive re-uploads unless they reset them.
- **CAP-5: Preferences**
  - **intent:** A Student sets target roles, locations, job types, Eligibility attributes, a Match Score threshold, alert frequency and time zone.
  - **success:** A threshold outside 0–100 is rejected, and a change updates the feed and the scope of the next collection run.
- **CAP-6: Scheduled LinkedIn ingestion**
  - **intent:** For each Student with a valid Provider Key, the platform collects Listings matching their Preferences on a schedule, under that Student's own Apify account.
  - **success:** A Listing collected twice is stored once, failed runs are retried and visible to Admins, spend caps stop collection, Students without a key still get the shared pool, and the pool is seeded before launch.
- **CAP-7: Source Adapter**
  - **intent:** All Source access goes through one interface, so a Source can be added or swapped without changing matching, Tracker or Outreach.
  - **success:** A test-double Source supplies Listings end to end with no change to that code, and a failed or disabled Source stops only its own collection.
- **CAP-8: Listing expiry and pool bounds**
  - **intent:** Expired or vanished Listings are hidden and purged, and the shared pool stays within free-tier storage while carrying only minimal LinkedIn content.
  - **success:** Pool Listings store no description, the cap evicts the oldest and least relevant Listings with no saved Application, Tracker entries survive eviction via a snapshot, and each Listing shows its source with an Admin takedown path.
- **CAP-9: Explainable Match Score**
  - **intent:** The platform scores each Student–Listing pair from 0 to 100 and shows why.
  - **success:** Identical inputs give identical scores, every score opens to matched skills, missing skills and per-component points, and scores refresh within 5 minutes of a Resume or Skill Profile change.
- **CAP-10: Ranked feed**
  - **intent:** A Student sees Listings ranked by Match Score and filtered by their Preferences.
  - **success:** The feed is ordered by score descending with ties newest first, expired Listings are hidden, and below-threshold Listings show only via one toggle.
- **CAP-11: Threshold alerts**
  - **intent:** A Student is alerted, in the app or by email, when new Listings reach their threshold, batched by their chosen frequency.
  - **success:** A Listing alerts a Student at most once, and on the daily setting a Student gets at most one alert message per local day.
- **CAP-12: Skill-gap insights**
  - **intent:** A Student sees which missing skills recur most across their lower-scoring Listings.
  - **success:** The top 10 missing skills are shown with counts, or "not enough data" below 10 scored Listings.
- **CAP-13: Full-text search**
  - **intent:** A Student searches Listings by keyword, with ranked results and typo tolerance.
  - **success:** "backend intern" returns Listings matching in title, company, skills or description keywords, ranked by relevance, in under 500 ms at about 30,000 Listings.
- **CAP-14: Eligibility filters and status**
  - **intent:** A Student filters by batch year, CGPA, degree, work authorisation, location, job type and deadline window, and sees each Listing's eligibility.
  - **success:** No stated criteria shows Unknown, and Not eligible appears only with the extracted criterion that conflicts.
- **CAP-15: Deadline reminders**
  - **intent:** A Student saves a Listing and is reminded a set number of days before its deadline.
  - **success:** Exactly one reminder is sent at the chosen offset (default 2 days), and none once the deadline is unknown or the Application is Applied or later.
- **CAP-16: Application tracking**
  - **intent:** A Student saves Listings, marks them Applied, and moves them through Status values, with notes, dates and the Resume version used.
  - **success:** Each Status change is timestamped in a viewable history, and Applied or later unlocks Employee Discovery.
- **CAP-17: Tracker view**
  - **intent:** A Student views and filters all Applications and sees upcoming deadlines and pending Follow-ups.
  - **success:** Filters combine and show a result count, and Applications whose Listing left the pool still appear.
- **CAP-18: Manual Listings**
  - **intent:** A Student adds a Listing by URL or details so applications from other sources are tracked too.
  - **success:** A manual Listing is visible only to its owner, never enters the shared pool, and is scored and tracked like any other.
- **CAP-19: Connected Accounts**
  - **intent:** A Student connects Gmail for sending and LinkedIn for identity, and can disconnect at any time.
  - **success:** Only OAuth tokens are stored, disconnecting deletes them, and email goes out through the Student's own mailbox.
- **CAP-20: Employee Discovery**
  - **intent:** For an Application marked Applied or later, the platform proposes Contacts at the company, ranked by relevance.
  - **success:** It runs only after consent, shows up to 10 Contacts with name, role and reason (each dismissable), never proposes a do-not-contact person, and says so when it finds no one.
- **CAP-21: Contact email lookup**
  - **intent:** The platform finds a work email for each Contact where possible and marks its confidence.
  - **success:** Every email shows its confidence, low-confidence emails need the Student's confirmation, lookups stop at the spend cap, and Contacts without an email get a LinkedIn draft path.
- **CAP-22: Outreach drafts**
  - **intent:** The platform drafts a personalised referral or cold email per Contact, plus its Follow-ups, which the Student can edit.
  - **success:** Drafts state only facts from the Resume and Skill Profile, name the Listing and company, stay under 150 words, and return to Draft when an approved one is edited.
- **CAP-23: Approved send with limits**
  - **intent:** Approved Outreach is sent from the Student's own mailbox within caps, and every email lets the Contact opt out of all Students' emails.
  - **success:** Nothing sends without approval; caps of 20 per day and 3 per company per Application hold, with overflow queued to the next day; an opt-out blocks the Contact for everyone and cancels their queued Outreach; send-time rejections are retried at most once; unconfirmed sends are marked Failed and never resent automatically.
- **CAP-24: Follow-ups and replies**
  - **intent:** A Student approves up to 2 Follow-ups with each Outreach, sent on day 5 and day 10 after the first send unless something stops them.
  - **success:** Unapproved Follow-ups never send, a reply, opt-out, Rejected or Withdrawn cancels the rest, Students can mark replies manually, and Follow-ups count toward the daily cap.
- **CAP-25: LinkedIn message drafts**
  - **intent:** The platform drafts LinkedIn connection notes or messages for the Student to copy and send themselves.
  - **success:** Each draft shows a link to the public profile and a copy button, and there is no send action.
- **CAP-26: Document storage**
  - **intent:** Resumes and Documents are stored privately and are downloadable only by their owner.
  - **success:** Only PDF, DOCX and plain text up to 5 MB are accepted, files are served through authenticated expiring links, and deleting a file or account removes it.
- **CAP-27: Admin console**
  - **intent:** An Admin monitors ingestion runs and spend, pauses Sources or providers, handles misuse reports, manages the do-not-contact list, and can suspend a Student's outreach.
  - **success:** A paused Source stops new collection within one scheduling cycle without deleting Listings, and every Admin action is audited with who, what and when.
- **CAP-28: Provider Key setup**
  - **intent:** A Student adds a scoped Apify token in settings, guided by short instructions, and controls its use and spend.
  - **success:** The key is validated on save, stored encrypted, never shown or logged, and replaceable or deletable (deletion stops runs). Usage and spend caps are visible, and an Apify rejection shows its reason, pauses collection and keeps the pool serving.
- **CAP-29: Eligibility extraction**
  - **intent:** The platform extracts eligibility criteria and a deadline from each Listing's text during ingestion.
  - **success:** Each criterion links to its source snippet, anything that can't be extracted is stored as unknown, and a Student can correct a criterion for their own view.

## Constraints

- Zero platform cost: public free tiers only. Scraping and lookups bill each Student's own Apify account, and outreach mail uses the Student's own Gmail.
- Solo builder, MVP first and as fast as possible: Phase 1 in about 3–4 weeks. In Phase 1, the owner does admin tasks (pausing a Source, takedowns, reviewing failed runs) directly in the database. CAP-2 consents cover only provider runs and pool contribution.
- India only at launch. India's DPDP Act governs consent, deletion and retention. Students are in India (default time zone Asia/Kolkata), and the product is English only.
- Applying is always manual. The platform never submits applications, never sends LinkedIn messages or connection requests, and never uses a Student's LinkedIn credentials or session.
- No email without the Student's approval. Caps and the opt-out are mandatory.
- Contact data is minimal, retained 90 days, and never pooled across Students.
- The Match Score is deterministic and explainable. No LLM computes it.
- LinkedIn's terms prohibit scraping. The owner accepts this risk with mitigations: Source Adapter, minimal pool content, attribution, takedown, Admin kill switch, and manual entry.
- Data is relational and normalised, with DB-enforced integrity (Listing dedup, foreign keys, status history). The database stays within about 0.5 GB, and files live in object storage.
- Provider Keys and OAuth tokens are encrypted at rest, never logged, and never sent to the client. Per-Student isolation is enforced on the server.
- Tone: the feed and alerts are neutral and factual, and Outreach drafts are polite, concise and honest. Nothing nudges a Student to apply or send more than they choose.

## Non-goals

- Recruiter, employer or job-posting features.
- Auto-applying to jobs.
- Automated LinkedIn connection requests or messages, or logging in as the Student on LinkedIn.
- Bulk or mass email.
- Resume building or interview preparation.
- Native mobile apps (responsive web only).
- Job Sources other than LinkedIn in v1 (manual entry covers the rest).
- Paid plans, billing, and languages other than English.
- Not in Phase 1 (MVP): Outreach of any kind (Gmail connection, discovery, email lookup, drafts, sending, Follow-ups, LinkedIn drafts), alerts, deadline reminders, eligibility extraction and filters, skill-gap insights, and the Admin console UI.
- Launch outside India.

## Success signal

- **Phase 1:** a new Student reaches a ranked, explained feed in under 10 minutes of effort (median). 70% of Students reach one within a day of signing up, and 40% of signups record at least one Application weekly (SM-6, SM-1, SM-2).
- **Phase 2:** sent Outreach earns a 10% reply rate (SM-3), with low opt-out and spam rates and alert volume not maximised (SM-C1, SM-C2).

## Assumptions

- Under the DPDP Act, deletion completes within 30 days.
- Pool bounds: about 30,000 active Listings, purged 30 days after expiry.
- Defaults: 10-minute setup target, 2-day reminder, 10 Contacts per Application, 150-word drafts, at most 2 Follow-ups at day 5 and 10, 5 MB files.
- The owner's free Apify credit covers the pre-launch seed run.
- 50% of new Students add a valid Provider Key within a week (SM-1b).
- Free-tier quotas stay roughly as verified on 2026-11-07.
- MVP build order, refined in epics:
  - Week 1: accounts, schema, Resume upload and Skill Profile, Preferences.
  - Week 2: Provider Key, ingestion, Source Adapter, expiry.
  - Week 3: Match Score, feed, search.
  - Week 4: Tracker, manual Listings, account deletion, hardening, launch.
