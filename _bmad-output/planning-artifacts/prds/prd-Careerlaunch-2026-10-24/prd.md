---
title: CareerLaunch — Jobs, Internships & Placement Tracker
status: final
created: 2026-10-24
updated: 2026-11-07
---

# PRD: CareerLaunch
*Working title — confirm.*

## 0. Document Purpose
This PRD defines CareerLaunch for the solo builder and for downstream workflows (UX, architecture, epics and stories). Features are grouped, FRs are nested under them with global IDs, and vocabulary is anchored in the Glossary. Inferred points are tagged `[ASSUMPTION]` inline and indexed in §11. "Product owner" means the solo builder. FR, UJ and SM IDs are stable labels, not positions in the document. Stack and vendor selection and reference repos live in `addendum.md`, not here.

## 1. Vision
CareerLaunch is a platform for students and fresh graduates who already know what they want: a job or internship. The student uploads a **Resume** and sets **Preferences**; the platform does the rest of the legwork. It pulls **Listings** from LinkedIn, scores each against the Resume, and delivers a ranked feed so the student only sees roles worth their time.

The student applies manually. After applying, the platform helps them stand out: it finds employees at the target company, drafts a personalised referral or cold email, sends it from the student's own mailbox once approved, and schedules follow-ups. Every application and outreach lands in one **Tracker**, so nothing falls through the cracks.

To keep the platform free to run, each Student supplies their own Apify key (**Provider Key**) and uses Apify's free monthly credit for scraping and lookups. A shared, size-limited pool of Listings means one Student's run benefits every Student with matching Preferences.

The goal is to turn a scattered, repetitive job hunt into a short loop: set preferences, review top matches, apply, let the platform run the outreach. The product is built for real use by students, as a solo major project with a 6–10 week delivery window. It is also a portfolio piece, so its engineering is meant to be defensible: a clean relational data model, role-based permissions, full-text search, document uploads, and a deterministic, explainable matching algorithm (FR-1, FR-9, FR-13, FR-26, §5). The main risk, LinkedIn's terms on scraping, is set out in §8 and FR-28.

## 2. Target User

### 2.1 Jobs To Be Done
- When I'm hunting for roles, I want only relevant openings ranked by how well I fit, so I don't waste hours scrolling.
- When I've applied, I want a referral or direct email to someone at the company, so my application isn't just another entry in a pile.
- I want to see all my applications, deadlines and follow-ups in one place, so I never miss a cut-off or forget to follow up.
- I want to know why a job scored low, so I can improve my Resume or skills.

### 2.2 Non-Users (v1)
- Recruiters and employers: no recruiter role, posting flow or dashboard.
- Experienced hires with highly bespoke career paths. They are not blocked, but the product is tuned for students and fresh graduates.

### 2.3 Key User Journeys

- **UJ-1. Aarav sets up once and gets a ranked feed.**
  - **Persona + context:** Aarav, a final-year CS student with no offers yet, wants software internships and entry-level roles in Bangalore and remote.
  - **Entry state:** new visitor on web, no account.
  - **Path:** signs up, uploads his PDF Resume, reviews the skills the platform extracted and corrects two, sets Preferences (role titles, locations, job type, minimum Match Score 70, graduation year 2026), pastes his Apify Provider Key (following the in-app steps to create a free Apify account), and consents to data collection.
  - **Climax:** he immediately sees a ranked feed from the shared Listing pool, each with a Match Score and a "why" breakdown (matched skills, missing skills); his first collection run adds fresh Listings within the hour.
  - **Resolution:** he gets a digest alert whenever new Listings clear his threshold.
  - **Edge case:** his Resume is a scanned image with no text; the platform says extraction failed and asks him to upload a text-based PDF or fix the skills manually. Another Student who skips the Provider Key sees only the shared pool and is reminded that adding a key unlocks fresh collection.

- **UJ-2. Aarav filters by eligibility and watches deadlines.**
  - **Persona + context:** Aarav, with a 7.8 CGPA, only qualifies for roles with a 7.5 cut-off and a 2026 batch.
  - **Entry state:** logged in, on the feed.
  - **Path:** he searches "backend intern", applies filters for batch year, CGPA, location and deadline within 7 days, and opens a Listing.
  - **Climax:** he sees the Listing's deadline and eligibility, and the platform marks it Eligible.
  - **Resolution:** he saves it and gets a reminder two days before the deadline.

- **UJ-3. Aarav applies, then the platform runs outreach.**
  - **Persona + context:** Aarav has just applied manually on the company's site for a role at a mid-size startup.
  - **Entry state:** on the Listing page, Application Status "Saved".
  - **Path:** he marks it Applied; the platform finds employees at the company (engineers, hiring managers, alumni), looks up work emails, and drafts a referral email per Contact using his Resume and the JD. He reviews and edits one draft and approves two, along with the follow-up drafts shown beneath each.
  - **Climax:** the emails go out from his own Gmail within his daily send cap, and the Tracker shows Outreach "Sent".
  - **Resolution:** the approved follow-ups send at day 5 and day 10 unless the Contact replies, opts out, or Aarav changes the Application to Rejected or Withdrawn.
  - **Edge case:** no email is found for any employee; the platform says so and offers a LinkedIn message draft for Aarav to send himself.

- **UJ-4. Aarav tracks everything and improves.**
  - **Persona + context:** three weeks in, Aarav has 18 applications.
  - **Entry state:** logged in, Tracker view.
  - **Path:** he views applications by Status (Saved, Applied, Interview, Offer, Rejected), updates one to Interview, uploads a tailored Resume version, and checks which skills are missing most often across low-scoring Listings.
  - **Climax:** he sees the top skill gaps and decides what to learn.
  - **Resolution:** he re-uploads an improved Resume and scores refresh.

## 3. Glossary
- **Student** — a signed-in job seeker. The only end-user role.
- **Admin** — an operator who manages sources, runs and abuse reports. Not a hiring-side role.
- **Resume** — a document the Student uploads. A Student can hold several versions; one is active for scoring.
- **Skill Profile** — the structured skills, education and experience extracted from the active Resume and confirmed by the Student.
- **Preferences** — the Student's target roles, locations, job types, Eligibility attributes, Match Score threshold and alert settings.
- **Listing** — a job or internship opportunity pulled from a source, stored once and deduplicated.
- **Source** — where Listings come from. LinkedIn, collected through Apify, is the primary Source, behind a Source Adapter.
- **Source Adapter** — the interface that isolates a Source so another can be added or swapped.
- **Match Score** — a 0–100 score of how well the active Resume fits a Listing, with an explainable breakdown.
- **Eligibility** — a Listing's hard criteria (batch year, minimum CGPA, degree, location, work authorisation) compared against the Student's attributes.
- **Application** — a Student's tracked record for a Listing, from Saved through the outcome. Applying itself happens outside the platform, manually.
- **Status** — the stage of an Application: Saved, Applied, Interview, Offer, Rejected, Withdrawn.
- **Tracker** — the Student's view of all Applications, deadlines, Outreach and follow-ups.
- **Contact** — a person at a target company found by Employee Discovery.
- **Employee Discovery** — finding Contacts at a company related to an Application.
- **Outreach** — a referral or cold email to a Contact. Student-facing states: Draft, Approved, Sent, Replied, Failed and Cancelled; the system also uses Queued and Sending internally while a send is pending.
- **Follow-up** — a repeat Outreach to the same Contact, drafted in advance and approved by the Student together with the first Outreach.
- **Shared pool** — the single deduplicated set of Listings collected under any Student's Provider Key and visible to every Student whose Preferences match.
- **Do-not-contact list** — the platform-wide list of Contacts who opted out or were blocked by an Admin; no Student can send them Outreach.
- **Document** — a file a Student uploads other than a Resume, such as a cover letter.
- **Connected Account** — a Student's authorised link to LinkedIn, Gmail or Outlook.
- **Provider Key** — a Student's own Apify API token. Scraping and email-lookup runs for that Student execute under that Student's own Apify account (see OQ-1, FR-28).

## 4. Features

### 4.1 Accounts, Roles and Consent
**Description:** Students sign up, manage their data and give explicit consent for data collection and email sending. Two roles exist: Student and Admin.

**Functional Requirements:**

#### FR-1: Account and roles
A visitor can register and log in with email and password or Google. Each account has one role, Student or Admin. Realizes UJ-1.

**Consequences (testable):**
- Admin-only routes return 403 for Students.
- A Student can read and write only their own Resumes, Documents, Applications, Contacts and Outreach, and read the shared Listing pool.
- An Admin can read ingestion runs, spend and storage, and manage the do-not-contact list, but cannot read Student Resumes, Documents, email drafts or Provider Keys.
- Role checks are enforced on the server for every request, not only hidden in the UI.

#### FR-2: Consent and data controls
Before any LinkedIn linkage or Outreach, the Student sees a plain-language consent screen describing what is collected and sent. A Student can revoke consent and delete their account and all data. `[ASSUMPTION: GDPR and India DPDP-style rights apply; deletion within 30 days.]`

**Consequences (testable):**
- Outreach and LinkedIn linkage are blocked until consent is recorded with a timestamp.
- Deleting an account removes Resumes, Applications, Outreach and Connected Account tokens.

### 4.2 Resume and Preferences
**Description:** The Student uploads Resumes and defines Preferences. The platform extracts a Skill Profile that the Student can correct.

**Functional Requirements:**

#### FR-3: Resume upload and versions
A Student can upload PDF or DOCX Resumes (up to 5 MB), keep multiple versions, and mark one active. Realizes UJ-1, UJ-4.

**Consequences (testable):**
- Unsupported types and files over 5 MB are rejected with a clear message.
- Changing the active Resume triggers re-scoring of the Student's Listings.

#### FR-4: Skill Profile extraction
The platform extracts skills, education and experience from the active Resume into a Skill Profile that the Student can edit. Realizes UJ-1.

**Consequences (testable):**
- If no text can be extracted, the Student is told and can enter skills manually.
- Student edits persist across Resume re-uploads unless explicitly reset.

#### FR-5: Preferences
A Student can set target role titles, locations, job types (internship, full-time), Eligibility attributes (batch year, CGPA, degree, work authorisation), Match Score threshold and alert frequency. Realizes UJ-1, UJ-2.

**Consequences (testable):**
- Match Score threshold must be between 0 and 100; invalid values are rejected.
- Changing Preferences updates the Student's feed and the scope of their next collection run.

### 4.3 Listing Ingestion
**Description:** The platform collects Listings from LinkedIn on a schedule driven by Students' Preferences, normalises and deduplicates them, and stores them in a searchable form. `[ASSUMPTION: LinkedIn is scraped through Apify, run under each Student's own Provider Key so the platform does not bear scraping cost; the product owner has accepted the terms-of-service risk, see §8.]` `[ASSUMPTION: Listings collected under any Student's Provider Key go into one shared, deduplicated pool visible to all Students whose Preferences match; confirm in architecture.]`

**Functional Requirements:**

#### FR-6: Scheduled LinkedIn ingestion
For each Student with a valid Provider Key, the platform runs scheduled collection jobs scoped to that Student's Preferences, under that Student's Provider Key, and stores each Listing with title, company, location, description, posted date, deadline when present, and source URL.

**Consequences (testable):**
- The same Listing collected twice is stored once.
- A failed run is logged, retried with backoff, and visible to Admins.
- Per-run and monthly spend caps set by the Student stop collection under their Provider Key when reached. An Admin may also set an optional platform-wide cap.
- A Student without a valid Provider Key sees only Listings already in the shared pool and can add Listings manually (FR-18).
- The pool is seeded before launch by an initial collection run under the product owner's own Apify account `[ASSUMPTION: the owner's free credit covers the initial seed run]`, so a new Student's first feed is never empty.

#### FR-7: Source Adapter
All Source access goes through a Source Adapter so another Source or provider can be added without changing matching, tracker or outreach logic.

**Consequences (testable):**
- A test double Source can supply Listings end to end with no change to scoring, Tracker or Outreach code.
- A Source that fails or is disabled by an Admin stops only its own collection; other Sources and existing Listings keep working.

#### FR-8: Listing expiry
Listings whose deadline has passed, or that are no longer found at the Source, are marked expired and hidden from the default feed. To keep the shared pool within free-tier storage, expired Listings are deleted after a grace period `[ASSUMPTION: 30 days]`, and the pool is capped `[ASSUMPTION: about 30,000 active Listings]`.

**Consequences (testable):**
- A Listing referenced by a Student's Application is never lost: a trimmed copy (title, company, link, deadline) is kept for the Tracker.
- When the cap is reached, the oldest Listings with no saved Applications and the lowest relevance to current Preferences are evicted first.
- Shared-pool Listings do not store the job description: it is used during ingestion to extract skills, Eligibility criteria (with short snippets) and search keywords, then discarded. Students read the full description at the source link. Manually added Listings may keep a private description of up to 4,000 characters.
- Each shared Listing shows its source with a not-affiliated notice, links to the source to apply, and can be removed by an Admin on request.

#### FR-28: Provider Key setup
A Student can add their Apify Provider Key in settings, with short instructions on getting one. Realizes UJ-1.

**Consequences (testable):**
- The setup steps ask the Student to create a scoped Apify token (limited to running the approved actors, with an expiry) rather than their full account token.
- The key is validated against Apify when saved; an invalid key is rejected with a clear message.
- The Student consents separately to Listings collected under their key being added to the shared pool; without it, those Listings stay visible only to them.
- The key is stored encrypted, is never displayed back or written to logs, and can be replaced or deleted at any time; deletion stops all runs under it.
- The consent screen (FR-2) states that scraping and lookups run under the Student's own Apify account, are charged to that account by Apify, and that LinkedIn's terms restrict scraping.
- The Student sees estimated and actual usage per run and can set spend caps (FR-6).
- If Apify rejects a run (invalid key, exhausted credit, blocked account), the Student sees the reason, collection pauses for that Student, and the shared pool keeps serving them.

### 4.4 Matching and Feed
**Description:** Each Listing is scored against the active Resume and Skill Profile, and ranked for the Student.

**Functional Requirements:**

#### FR-9: Match Score with explanation
The platform computes a Match Score for each Student–Listing pair and shows matched skills, missing skills and the main score drivers. The score is deterministic and explainable: weighted skill overlap plus text similarity between the Student's profile and the Listing's title and description. No embeddings or LLM are used to compute it in v1 (architecture AD-9). Realizes UJ-1.

**Consequences (testable):**
- Identical inputs always give the same score.
- Every score shown has a breakdown the Student can open: matched skills, missing skills, and each component's contribution to the score.
- Scores for a Student's feed refresh within 5 minutes of changing the active Resume or Skill Profile `[ASSUMPTION: 5-minute refresh target]`.

#### FR-10: Ranked feed
The Student sees Listings ranked by Match Score and filtered by their Preferences, with expired Listings hidden. Realizes UJ-1.

**Consequences (testable):**
- Listings are ordered by descending Match Score; ties are ordered by newest first.
- Listings below the Student's threshold are hidden by default and can be shown with one toggle.

#### FR-11: Threshold alerts
When new Listings reach the Student's Match Score threshold, the platform sends an alert by email or in-app, batched per the Student's chosen frequency. Realizes UJ-1.

**Consequences (testable):**
- A Listing triggers at most one alert per Student.
- With frequency set to daily, a Student receives at most one alert message per day.

#### FR-12: Skill-gap insights
The platform shows the missing skills that appear most often across the Student's lower-scoring Listings. Realizes UJ-4.

**Consequences (testable):**
- The view lists the top 10 missing skills with the count of Listings each affects.
- With fewer than 10 scored Listings, the view says there is not enough data.

### 4.5 Search, Eligibility and Deadlines
**Description:** Students can search all Listings and filter by Eligibility and deadline.

**Functional Requirements:**

#### FR-13: Full-text search
A Student can search Listings by keyword across title, company, extracted skills and description keywords, with ranked results and typo tolerance. Realizes UJ-2.

**Consequences (testable):**
- A query for "backend intern" returns Listings whose title, company, skills or description keywords contain those terms, ranked by relevance.
- Typical searches return in under 500 ms on the target data volume. `[ASSUMPTION: target about 30,000 active Listings.]`

#### FR-14: Eligibility filters
A Student can filter by batch year, minimum CGPA, degree, work authorisation, location, job type and deadline window. Each Listing shows Eligible, Not eligible or Unknown against the Student's attributes. Realizes UJ-2.

**Consequences (testable):**
- Listings with no stated criteria show Unknown, never Not eligible.
- A Listing shows Not eligible only when an extracted criterion (FR-29) conflicts with the Student's attribute, and the conflicting criterion is shown.

#### FR-29: Eligibility extraction
The platform extracts Eligibility criteria (batch year, minimum CGPA, degree, work authorisation) and a deadline from each Listing's text during ingestion, using rule-based pattern matching `[ASSUMPTION: rules, not an LLM; extraction quality is limited by how many Listings state criteria]`. Realizes UJ-2.

**Consequences (testable):**
- Each extracted criterion links to the text snippet it came from.
- A criterion that cannot be extracted is stored as unknown, not guessed.
- A Student can correct an extracted criterion on a Listing for their own view.

#### FR-15: Deadline reminders
A Student can save a Listing and receive a reminder a set number of days before its deadline. Realizes UJ-2.

**Consequences (testable):**
- The reminder is sent once, at the chosen number of days before the deadline `[ASSUMPTION: default 2 days]`.
- No reminder is sent for a Listing with no known deadline, or after the Application's Status is Applied or later.

### 4.6 Application Tracker
**Description:** The Student records Applications and manages their Status. The platform never submits applications.

**Functional Requirements:**

#### FR-16: Track Applications
A Student can save a Listing, mark it Applied, and move it through Status values, with notes, dates and the Resume version used. Realizes UJ-3, UJ-4.

**Consequences (testable):**
- Each Status change is timestamped and kept in a history the Student can view.
- Marking an Application Applied is what makes Employee Discovery (FR-20) available for it.

#### FR-17: Tracker view
The Student can view and filter all Applications by Status, company and date, and see upcoming deadlines and pending Follow-ups. Realizes UJ-4.

**Consequences (testable):**
- Filters combine, and the result count is shown.
- Applications whose Listing was removed from the pool still appear, using the trimmed copy (FR-8).

#### FR-18: Add external Listing
A Student can add a Listing manually by URL or details so Applications from other sources are tracked too. Realizes UJ-4.

**Consequences (testable):**
- A manually added Listing is visible only to the Student who added it and is not added to the shared pool.
- A manually added Listing is scored and tracked like any other.

### 4.7 Employee Discovery and Outreach
**Description:** After a Student marks an Application as Applied, the platform finds Contacts at the company, finds work emails, drafts personalised emails, and sends them from the Student's own mailbox after approval. `[ASSUMPTION: Employee Discovery and email lookup use Apify actors under the Student's Provider Key, not the Student's LinkedIn session; see §8.]`

**Functional Requirements:**

#### FR-19: Connected Accounts
A Student can connect Gmail or Outlook by OAuth for sending, and LinkedIn for identity and, optionally, a connections export upload. `[ASSUMPTION: LinkedIn OAuth gives only the Student's own profile; connections come from a CSV upload.]` Realizes UJ-3.

**Consequences (testable):**
- The platform stores OAuth tokens only, never passwords.
- A Student can disconnect at any time and tokens are deleted.
- Emails are sent through the Student's own connected mailbox, so mail-sending cost and sending limits sit with the Student's account, not the platform. `[ASSUMPTION: the platform has no shared sending domain.]`

#### FR-20: Employee Discovery
For an Application marked Applied, the platform proposes Contacts at the company, ranked by relevance (team, seniority, alumni or existing connection). Realizes UJ-3.

**Consequences (testable):**
- Discovery runs only after the Student has given consent (FR-2) and only for Applications marked Applied.
- The Student sees up to 10 proposed Contacts per Application `[ASSUMPTION: limit of 10 Contacts per Application]`, each with name, role and why they were proposed, and can dismiss any.
- A Contact on the do-not-contact list is never proposed.
- If Discovery returns no one, the Student is told and the Application is unaffected.

#### FR-21: Contact email lookup
The platform finds a work email for each Contact where possible and marks confidence. The method is an Apify email-finder actor run under the Student's Provider Key. There is no open-source pattern-guess-and-verify fallback, because the chosen host blocks outbound SMTP. The Student can also enter an email manually. Contacts without an email are flagged so the Student can use a LinkedIn message draft instead. Realizes UJ-3.

**Consequences (testable):**
- Every found email shows a confidence level; low-confidence emails require explicit Student confirmation before Outreach.
- Lookup runs stop when the Student's spend cap is reached.

#### FR-22: Draft Outreach
The platform drafts a personalised referral or cold email per Contact using the Resume, the Listing and the Contact's role, plus a Follow-up draft for each scheduled Follow-up. The Student can edit before approval. Realizes UJ-3.

**Consequences (testable):**
- Drafts state only facts present in the Student's Resume and Skill Profile; they never invent experience, and they are polite, concise and plain in tone.
- Each draft names the Listing and company it concerns, and is under 150 words `[ASSUMPTION: 150-word limit]`.
- A draft is a Draft until the Student approves it; editing a draft returns an approved one to Draft.

#### FR-23: Approved send with limits
Outreach is sent from the Student's mailbox only after the Student approves it. Daily and per-company send caps apply, and every email contains an opt-out link. Realizes UJ-3.

**Consequences (testable):**
- No email is sent without an Approved state.
- Send caps are 20 emails per day per Student and 3 per company per Application `[ASSUMPTION: caps of 20 per day and 3 per company]`; sends beyond the daily cap are queued to the next day.
- The opt-out link points to a page hosted by the platform with a one-time token per Contact; following it marks the Contact do-not-contact for all Students. This works without a shared sending domain.
- A Contact who opts out or is marked do-not-contact is never emailed again by any Student, and any queued Outreach to them is cancelled.
- A send rejected by the mail provider at send time is shown to the Student and is not retried more than once. Bounces that arrive later by email are not detected automatically (sending uses the send-only Gmail permission); the Student can mark an Outreach as bounced.
- If the platform cannot confirm whether a send went out, the Outreach is marked Failed for the Student to review and is never resent automatically.

#### FR-24: Follow-ups and reply detection
When a Student approves an Outreach, they also approve its Follow-up drafts, up to 2, at day 5 and day 10 after the first Outreach is actually sent `[ASSUMPTION: at most 2 Follow-ups, at day 5 and day 10]`, which they can edit or remove first. The platform sends each approved Follow-up on schedule and cancels the remaining ones when a reply, an opt-out, or a Status of Rejected or Withdrawn is recorded. A Student can mark a Contact as replied manually; automatic reply detection depends on OQ-6. Realizes UJ-3.

**Consequences (testable):**
- A Follow-up is never sent unless it was approved with its Outreach.
- Marking a Contact replied cancels all remaining Follow-ups to that Contact.
- Follow-ups count against the same daily send cap as other Outreach.

#### FR-25: LinkedIn message drafts
For Contacts, the platform produces a LinkedIn connection note or message draft for the Student to copy and send. The platform never sends LinkedIn messages or connection requests itself. Realizes UJ-3.

**Consequences (testable):**
- The draft is shown with a link to the Contact's public LinkedIn profile and a copy button; there is no send action.

### 4.8 Documents and Administration

#### FR-26: Document storage
Resumes and Documents (cover letters, other files) are stored securely, private to the Student, and downloadable by them. A tailored Resume is simply another Resume version (FR-3). Realizes UJ-4.

**Consequences (testable):**
- Allowed Document types are PDF, DOCX and plain text, up to 5 MB each `[ASSUMPTION: 5 MB limit and these file types]`; others are rejected with a clear message.
- Files are served only to their owner through authenticated, expiring links.
- Uploaded files are scanned or type-checked for validity before being stored `[ASSUMPTION: type and size checks only]`.
- Deleting a file or account removes the stored file.

#### FR-27: Admin console
An Admin can view ingestion runs and spend, pause or disable a Source or provider, review reports of misuse, and manage the do-not-contact list.

**Consequences (testable):**
- Pausing a Source stops new collection within one scheduling cycle and does not delete existing Listings.
- Every Admin action is recorded with who, what and when.

## 5. Cross-Cutting NFRs
- **Performance:** feed pages load in under 2 s; full-text search under 500 ms at the target volume. `[ASSUMPTION: targets for free-tier hosting with about 30,000 Listings]`
- **Time to value:** a new Student reaches a first ranked feed in about 10 minutes of effort, including Resume upload, Preferences and Provider Key. `[ASSUMPTION: 10-minute setup target]`
- **Tone:** the feed and alerts are neutral and factual; Outreach drafts are polite and honest (FR-22). Nothing nudges the Student to send more or apply more than they choose.
- **Reliability:** ingestion jobs are idempotent and retry safely; one failing Source does not take down the app.
- **Hosting and cost:** the product runs on public free-tier hosting `[ASSUMPTION: free tiers are available and sufficient]`. Design must tolerate instances that sleep, limited background workers and cron, and small database quotas, for example by using an external scheduler and a job queue that resumes after a restart.
- **Storage budget:** the database stays within a free-tier quota `[ASSUMPTION: about 0.5 GB]`; Resume and document files live in separate free object storage, not the database. Admins see storage use and get an alert at 80% of the quota.
- **Security and secrets:** per-Student data isolation; Provider Keys and OAuth tokens are encrypted at rest, never logged, and never sent to the client after entry.
- **Observability:** every ingestion run, score computation and send has a log entry visible to Admins.
- **Privacy:** collect the minimum personal data about Contacts; retain it only as long as needed for the Outreach, `[ASSUMPTION: 90 days]`.
- **Data model:** relational, normalised schema with enforced foreign keys and constraints, including Status history for Applications. Deduplication of Listings is enforced by a database constraint, not only by application code.

## 6. Non-Goals (Explicit)
- No recruiter, employer or posting side (see §2.2).
- No auto-applying to jobs. Applying is always manual.
- No automated LinkedIn connection requests or direct messages.
- No logging in as the Student on LinkedIn or storing their LinkedIn credentials.
- No mass or bulk email blasts; Outreach is per Contact, approved, and capped.
- Not a resume builder or an interview-prep tool.

## 7. Scope
### 7.1 In Scope
*Updated 2026-11-07: the product owner now wants an MVP as fast as possible.* All features in §4 remain in scope, delivered in two phases:
- **Phase 1 (MVP, about 3–4 weeks):** FR-1 to FR-10, FR-13, FR-16 to FR-18, FR-26, FR-28. Admin tasks are done directly in the database.
- **Phase 2:** FR-11, FR-12, FR-14, FR-15, FR-19 to FR-25, FR-27, FR-29.
- Launch is India only (India DPDP Act).

The week plan below describes the original full-scope order; the phase-1 order lives in the spec (`_bmad-output/specs/spec-careerlaunch/SPEC.md`).

`[ASSUMPTION: build order, to be refined in epics]`
- Weeks 1–2: accounts and roles, relational schema, Resume upload and Skill Profile, Preferences.
- Weeks 3–4: Provider Key, ingestion, Eligibility extraction, matching and ranked feed, full-text search.
- Weeks 5–6: Tracker, alerts, deadline reminders, filters. Core product usable here.
- Weeks 7–8: Employee Discovery, email lookup, Outreach drafts, approval and sending, Follow-ups.
- Weeks 9–10: Admin console, hardening, privacy and consent flows, launch.

`[ASSUMPTION: if the schedule slips, the first items to cut are automatic reply detection (Student marks replies manually), Outlook support, FR-12 skill-gap insights, and connections CSV upload.]`

### 7.2 Out of Scope
- Native mobile apps (responsive web only). `[ASSUMPTION: students mainly use laptops and mobile browsers]`
- Job Sources other than LinkedIn in v1. The Source Adapter (FR-7) allows them later, and manual entry (FR-18) covers other postings.
- Paid plans and billing.
- Languages other than English.

## 8. Constraints, Risks and Guardrails
These are stated plainly because the product's core features depend on them.
- **LinkedIn terms of service (high):** LinkedIn prohibits scraping and takes action against it, including blocking and legal claims. The product's main Source is LinkedIn, so it can lose access without notice. *Mitigations:* Source Adapter (FR-7), no use of Students' LinkedIn sessions, spend caps, an Admin kill switch, and Listings also accepted by manual entry (FR-18). The exposure is shared with Students, who run collection under their own Apify account and are told so (FR-28); it is not removed.
- **Personal data of Contacts (high):** discovering employees and emailing them processes third parties' personal data (GDPR, India DPDP). *Mitigations:* minimum data, retention limit, opt-out and global do-not-contact list, no sensitive attributes.
- **Anti-spam and deliverability (medium):** cold email is regulated (CAN-SPAM and equivalents) and can damage the Student's sender reputation. *Mitigations:* approval before send (including Follow-ups, FR-24), daily and per-company caps, a platform-hosted opt-out link (FR-23), stop-on-reply.
- **Third-party cost and terms (medium):** scraping and email-lookup actors charge per use and have their own terms. Cost is shifted to Students through their own Provider Keys. *Mitigations:* Student-set spend caps and usage display (FR-6, FR-28).
- **Provider Key friction and exposure (medium):** needing an Apify account and key adds onboarding friction, and Students rely on Apify's free monthly credit by default, which may run out under heavy use. The key is a secret (§5). *Mitigations:* clear setup instructions, a shared Listing pool so one Student's run benefits others, encryption of keys.
- **Free-tier hosting limits (medium):** see §5 Hosting and cost. Blocked SMTP port 25 also limits the open-source lookup fallback (FR-21).
- **Solo delivery (medium):** full scope in 6–10 weeks by one person. *Mitigation:* simple default technology and sequencing in epics.

## 9. Success Metrics
**Primary**
- **SM-1:** Share of Students who complete Resume and Preferences setup and see a first ranked feed within a day (from the shared pool; a Provider Key is not required). Target 70%. Validates FR-3, FR-4, FR-5, FR-10.
- **SM-1b:** Share of new Students who add a valid Provider Key within a week. Target 50% `[ASSUMPTION: 50% target]`. Validates FR-28.
- **SM-2:** Weekly active Students who record at least one Application. Target 40% of signups. Validates FR-16, FR-10.
- **SM-3:** Reply rate on sent Outreach, counting replies detected automatically or marked by the Student. Target 10%. Validates FR-20 to FR-24.
- **SM-6:** Median time from signup to first ranked feed. Target under 10 minutes of Student effort. Validates FR-3, FR-4, FR-5, FR-28 and the time-saving promise in §1.

**Secondary**
- **SM-4:** Share of Students who open the Match Score breakdown. Validates FR-9.
- **SM-5:** Ingestion success rate per scheduled run. Target 95%. Validates FR-6.

**Counter-metrics (do not optimize)**
- **SM-C1:** Outreach opt-out and spam-report rate; keep it low rather than maximising sends. Counterbalances SM-3.
- **SM-C2:** Average alerts per Student per week; more alerts is not better. Counterbalances SM-1 and SM-2.

## 10. Open Questions
- **OQ-1:** *Resolved (2026-11-07, architecture research):* runs bill the token owner's Apify account; Free accounts are blocked until the next cycle once the USD 5 credit is spent. Students use scoped tokens. Candidate actors and costs are in the architecture folder's `research-oq1-oq3.md`; the final pick happens in the ingestion epic.
- **OQ-2:** *Resolved in architecture (2026-11-07):* Supabase Free (Postgres, Auth, Storage, pg_cron), FastAPI on Render Free, Next.js on Vercel Hobby, with a Postgres job queue drained on a schedule. See the architecture spine.
- **OQ-3:** *Resolved (2026-11-07, architecture research; not legal advice):* Apify does not restrict sharing output, but LinkedIn's User Agreement bans scraping and redistribution through aggregators. The product owner accepts a minimal shared job pool (no stored descriptions, attribution, takedown, pool-contribution consent). Contacts and emails are never pooled.
- **OQ-4:** *Resolved (2026-11-07):* India only at launch; India's DPDP Act governs consent, deletion and retention.
- **OQ-5:** *Resolved in architecture (2026-11-07):* no embeddings or LLM in the score (exact weights set in the matching epic); Outreach drafts use a free-tier LLM with a template fallback, and the consent screen discloses that Resume content goes to that provider.
- **OQ-6:** *Resolved in architecture (2026-11-07):* Gmail only at launch with the send-only permission; Students mark replies and bounces themselves; Outlook deferred. Google verification starts in weeks 5–6, because unverified apps are limited to 100 test users and their Gmail connections expire every 7 days.

## 11. Assumptions Index
Every inline `[ASSUMPTION]` tag, grouped by area for confirmation:
- **Compliance:** GDPR/DPDP-style rights with 30-day deletion (FR-2); 90-day Contact data retention (§5).
- **Source and cost model:** LinkedIn scraped through Apify under each Student's Provider Key (§4.3); shared deduplicated Listing pool (§4.3); owner's free credit covers the initial seed run (FR-6).
- **Pool size and storage:** 30-day post-expiry retention, 30,000 active Listing cap, 4,000-character descriptions (FR-8); search target volume (FR-13); about 0.5 GB database quota (§5); free-tier hosting is sufficient (§5).
- **Matching and eligibility:** 5-minute refresh (FR-9); rule-based Eligibility extraction (FR-29); default 2-day deadline reminder (FR-15).
- **Outreach:** Employee Discovery and email lookup under the Student's Provider Key (§4.7); LinkedIn OAuth gives only own profile, connections via CSV (FR-19); no shared sending domain (FR-19); 10 Contacts per Application (FR-20); 150-word drafts (FR-22); send caps of 20 per day and 3 per company (FR-23); at most 2 Follow-ups at day 5 and 10 (FR-24).
- **Documents:** 5 MB limit and PDF/DOCX/text types, type and size checks only (FR-26).
- **Performance and targets:** performance targets for free-tier hosting (§5); 10-minute setup target (§5); SM-1b 50% Provider Key adoption (§9).
- **Scope:** build order by week, and the cut list if the schedule slips (§7.1); responsive web only (§7.2).
