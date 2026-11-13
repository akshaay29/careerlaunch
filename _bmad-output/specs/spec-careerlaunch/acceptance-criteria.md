# Acceptance Criteria: CareerLaunch

The testable consequences for each capability, lifted from the PRD's FRs. `[A]` marks a default carried as an assumption in SPEC.md. Design rules live in `ARCHITECTURE-SPINE.md`, cited by AD id.

## CAP-1: Accounts and roles (FR-1; AD-1, AD-5)
- Visitors register and log in with email and password or Google; each account has one role, Student or Admin.
- Admin-only routes return 403 for Students.
- A Student reads and writes only their own Resumes, Documents, Applications, Contacts and Outreach, and reads the shared Listing pool.
- An Admin reads ingestion runs, spend and storage, and manages the do-not-contact list, but cannot read Student Resumes, Documents, email drafts or Provider Keys.
- Role checks run on the server for every request, not only in the UI.

## CAP-2: Consent and data controls (FR-2; AD-11, AD-17)
- A plain-language consent screen describes what is collected and sent, before any LinkedIn linkage or Outreach.
- Outreach and LinkedIn linkage are blocked until consent is recorded with a timestamp.
- Separate consents cover provider runs, pool contribution, LinkedIn linkage, Outreach and LLM drafting. The LLM consent names the provider and says that free-tier inputs may be used for training.
- The consent screen states that scraping and lookups run under the Student's own Apify account, are charged by Apify, and that LinkedIn's terms restrict scraping.
- A Student can revoke consent; pending work for that purpose is cancelled.
- Deleting an account removes Resumes, Applications, Outreach and Connected Account tokens, within 30 days `[A]`.

## CAP-3: Resume upload and versions (FR-3)
- PDF or DOCX up to 5 MB; multiple versions; one active.
- Unsupported types and files over 5 MB are rejected with a clear message.
- Changing the active Resume re-scores the Student's Listings.

## CAP-4: Skill Profile extraction (FR-4; AD-8)
- Skills, education and experience are extracted from the active Resume into an editable Skill Profile.
- If no text can be extracted, the Student is told and can enter skills manually.
- Student edits persist across Resume re-uploads unless explicitly reset.

## CAP-5: Preferences (FR-5)
- The Student sets target role titles, locations, job types (internship, full-time), Eligibility attributes (batch year, CGPA, degree, work authorisation), Match Score threshold, alert frequency, and time zone.
- A threshold outside 0–100 is rejected.
- Changing Preferences updates the feed and the scope of the next collection run.

## CAP-6: Scheduled LinkedIn ingestion (FR-6; AD-4, AD-7, AD-12)
- Scheduled runs scoped to the Student's Preferences, under their Provider Key, store title, company, location, posted date, deadline when present, and source URL.
- The same Listing collected twice is stored once.
- A failed run is logged, retried with backoff, and visible to Admins.
- Student-set per-run and monthly spend caps stop collection under their key; Admins may set a platform-wide cap.
- A Student without a valid key sees the shared pool and can add Listings manually (CAP-18).
- The pool is seeded before launch under the owner's own Apify account `[A]`.
- Saving a valid key triggers a first collection run; fresh Listings arrive within the hour.

## CAP-7: Source Adapter (FR-7; AD-3)
- A test-double Source supplies Listings end to end with no change to scoring, Tracker or Outreach code.
- A Source that fails or is disabled by an Admin stops only its own collection; other Sources and existing Listings keep working.

## CAP-8: Listing expiry and pool bounds (FR-8; AD-7, AD-17)
- Listings past their deadline, or no longer found at the Source, are marked expired and hidden from the default feed.
- Expired Listings are deleted after a 30-day grace period `[A]`; the pool is capped at about 30,000 active Listings `[A]`.
- A Listing referenced by an Application is never lost: a trimmed copy (title, company, link, deadline) is kept for the Tracker.
- At the cap, the oldest Listings with no saved Application and the lowest relevance to current Preferences are evicted first.
- Pool Listings store no description. It is used during ingestion to extract skills, Eligibility criteria (with short snippets) and search keywords, then discarded. Manual Listings may keep a private description of up to 4,000 characters.
- Each pool Listing shows its source with a not-affiliated notice, links to the source to apply, and can be removed by an Admin on request.
- Listings collected under a Student's key enter the pool only with that Student's pool-contribution consent; otherwise they stay visible only to them.

## CAP-9: Explainable Match Score (FR-9; AD-8, AD-9)
- The score is weighted skill overlap plus text similarity to the Listing's title and description keywords. No embeddings or LLM are used.
- Identical inputs always give the same score.
- Every score opens to matched skills, missing skills, and each component's contribution.
- Scores refresh within 5 minutes of changing the active Resume or Skill Profile.

## CAP-10: Ranked feed (FR-10)
- Ordered by Match Score descending; ties newest first.
- Below-threshold Listings are hidden by default and shown with one toggle; expired Listings are hidden.

## CAP-11: Threshold alerts (FR-11; AD-16)
- Alerts arrive in the app or by email, batched by the chosen frequency.
- A Listing triggers at most one alert per Student.
- On the daily setting, a Student receives at most one alert message per day (their local day).

## CAP-12: Skill-gap insights (FR-12; cut list)
- Lists the top 10 missing skills with the count of Listings each affects.
- With fewer than 10 scored Listings, says there is not enough data.

## CAP-13: Full-text search (FR-13)
- Search covers title, company, extracted skills and description keywords, ranked by relevance, with typo tolerance.
- "backend intern" returns Listings matching those terms in any of those fields.
- Typical searches return in under 500 ms at about 30,000 active Listings.

## CAP-14: Eligibility filters and status (FR-14; AD-21)
- Filters: batch year, minimum CGPA, degree, work authorisation, location, job type, deadline window.
- Each Listing shows Eligible, Not eligible or Unknown against the Student's attributes.
- No stated criteria → Unknown, never Not eligible.
- Not eligible only when an extracted criterion (CAP-29) conflicts, and the conflicting criterion is shown.

## CAP-15: Deadline reminders (FR-15; AD-16)
- Sent once, at the chosen number of days before the deadline (default 2 `[A]`).
- None for a Listing with no known deadline, or once the Application is Applied or later.

## CAP-16: Application tracking (FR-16; AD-20)
- Save, mark Applied, and move through Saved, Applied, Interview, Offer, Rejected, Withdrawn, with notes, dates and the Resume version used.
- Each Status change is timestamped in a history the Student can view.
- Applied or later (Applied, Interview, Offer) makes Employee Discovery available.

## CAP-17: Tracker view (FR-17)
- Filter by Status, company and date; see upcoming deadlines and pending Follow-ups.
- Filters combine and show the result count.
- Applications whose Listing left the pool still appear, using the trimmed copy.

## CAP-18: Manual Listings (FR-18; AD-3, AD-7)
- Added by URL or details; the URL is stored, not fetched by the server.
- Visible only to the Student who added it; never added to the shared pool.
- Scored and tracked like any other Listing.

## CAP-19: Connected Accounts (FR-19; AD-6)
- Gmail connected by OAuth with send-only permission; LinkedIn for identity, with an optional connections CSV upload (cut list). LinkedIn OAuth gives only the Student's own profile.
- Only OAuth tokens are stored, never passwords.
- Disconnecting deletes the tokens and revokes the grant at Google.
- Email is sent through the Student's own mailbox; there is no shared sending domain. Outlook is deferred (cut list).
- When a Gmail connection expires (weekly while the Google app is unverified), sends are held and the Student is asked to reconnect.

## CAP-20: Employee Discovery (FR-20; AD-12, AD-19)
- Runs only after consent and only for Applications at Applied or later.
- Up to 10 proposed Contacts per Application `[A]`, each with name, role and why proposed, ranked by relevance (team, seniority, alumni, existing connection); each can be dismissed.
- A do-not-contact person is never proposed.
- If no one is found, the Student is told and the Application is unaffected.
- Discovery results belong to the Student who ran them and are never shared with other Students.

## CAP-21: Contact email lookup (FR-21; AD-12)
- Done by an Apify email-finder actor under the Student's key; there is no SMTP pattern-guess fallback.
- Every found email shows a confidence level; low-confidence emails need explicit confirmation before Outreach.
- The Student can enter an email manually.
- Contacts without an email are flagged for a LinkedIn message draft (CAP-25).
- Lookups stop when the Student's spend cap is reached.

## CAP-22: Outreach drafts (FR-22; AD-15)
- One draft per Contact using the Resume, the Listing and the Contact's role, plus a draft for each scheduled Follow-up; editable before approval.
- Drafts state only facts from the Resume and Skill Profile, never invent experience, and are polite, concise and plain.
- Each names the Listing and company and is under 150 words `[A]`.
- A draft stays Draft until approved; editing an approved draft returns it to Draft.
- Drafting works without the LLM (template fallback).

## CAP-23: Approved send with limits (FR-23; AD-10, AD-19)
- No email is sent without Approved state.
- Caps: 20 emails per day per Student and 3 per company per Application `[A]`; sends beyond the daily cap are queued to the next day.
- Every email carries an opt-out link to a platform-hosted page with a token per Contact; using it marks the Contact do-not-contact for all Students, without a shared sending domain.
- A Contact who opts out or is marked do-not-contact is never emailed again by any Student, and their queued Outreach is cancelled.
- A send rejected by the mail provider at send time is shown to the Student and retried at most once. Later bounces are not detected automatically; the Student can mark an Outreach bounced.
- If a send's outcome cannot be confirmed, it is marked Failed for review and never resent automatically.

## CAP-24: Follow-ups and replies (FR-24; AD-10)
- Approving an Outreach also approves its Follow-up drafts: up to 2, at day 5 and day 10 after the first Outreach is sent `[A]`, editable or removable first.
- A Follow-up never sends unless approved with its Outreach.
- A reply, opt-out, or Status of Rejected or Withdrawn cancels remaining Follow-ups.
- Marking a Contact replied cancels all remaining Follow-ups to that Contact. Reply detection is manual at launch.
- Follow-ups count against the daily send cap.

## CAP-25: LinkedIn message drafts (FR-25)
- A connection note or message draft is shown with a link to the Contact's public profile and a copy button.
- There is no send action.

## CAP-26: Document storage (FR-26; AD-17)
- Resumes and Documents (cover letters, other files) are private to the Student and downloadable by them; a tailored Resume is another Resume version.
- Allowed: PDF, DOCX, plain text, up to 5 MB each `[A]`; others are rejected with a clear message.
- Files are served only to their owner through authenticated, expiring links.
- Files are type- and size-checked before storage.
- Deleting a file or account removes the stored file.

## CAP-27: Admin console (FR-27; AD-5, AD-18)
- An Admin views ingestion runs and spend, pauses or disables a Source or provider, reviews misuse reports, manages the do-not-contact list, and can suspend a Student's outreach.
- Pausing a Source stops new collection within one scheduling cycle and deletes no Listings.
- Every Admin action is recorded with who, what and when.
- Admins see storage use and get an alert at 80% of the database quota.

## CAP-28: Provider Key setup (FR-28; AD-6, AD-12)
- Settings include short instructions, asking for a scoped Apify token (limited to running approved actors, with an expiry) rather than a full account token.
- The key is validated against Apify on save; an invalid key is rejected with a clear message.
- The key is stored encrypted, never shown back or logged, and can be replaced or deleted any time; deletion stops all runs under it.
- The Student sees estimated and actual usage per run and can set spend caps.
- If Apify rejects a run (invalid key, exhausted credit, blocked account), the Student sees the reason, collection pauses for them, and the shared pool keeps serving them.

## CAP-29: Eligibility extraction (FR-29; AD-21)
- Batch year, minimum CGPA, degree, work authorisation and deadline are extracted from Listing text at ingestion by rule-based patterns, not an LLM.
- Each extracted criterion links to the text snippet it came from.
- A criterion that cannot be extracted is stored as unknown, not guessed.
- A Student can correct an extracted criterion on a Listing for their own view.

## Cross-cutting (PRD §5)
- Feed pages load in under 2 s.
- Every ingestion run, score computation and send has a log entry visible to Admins.
- Ingestion jobs are idempotent and retry safely; one failing Source does not take down the app.
- The design tolerates sleeping instances, limited workers and cron, and small database quotas.

## Metrics to instrument (PRD §9)
- SM-1: share of Students who complete Resume and Preferences setup and see a first ranked feed within a day; target 70%.
- SM-1b: share of new Students adding a valid Provider Key within a week; target 50% `[A]`.
- SM-2: weekly active Students recording at least one Application; target 40% of signups.
- SM-3: reply rate on sent Outreach (manual reply marks count); target 10%.
- SM-4: share of Students opening the Match Score breakdown.
- SM-5: ingestion success rate per scheduled run; target 95%.
- SM-6: median time from signup to first ranked feed; target under 10 minutes of effort.
- Counter-metrics, not to be optimised: SM-C1 opt-out and spam-report rate; SM-C2 alerts per Student per week.
