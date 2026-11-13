# Reconciliation: Problem Statement and Later User Decisions vs PRD

**Input reconciled:** original problem statement ("CareerLaunch — Jobs, Internships & Placement Tracker", 6-10 weeks; students, fresh graduates, recruiters; aggregate, track, match, recruiter dashboard, eligibility filters, deadlines; interview value: full-text search, relational DB design, document uploads, role-based permissions, matching algorithms) plus the user's later decisions (no recruiter side; LinkedIn via Apify as primary source with each student's own key; manual apply; post-apply referral/cold email from the student's own Gmail/Outlook, student-approved; free-tier hosting; shared bounded pool; all-in scope, solo).

**Compared against:** `prd.md` (updated 2026-11-06) and `addendum.md`. prd.md was not modified.

**Already covered correctly (no action):** recruiter side dropped and stated as a Non-User and Non-Goal (§2.2, §6); manual apply (§6, FR-16); own Apify key (FR-28, FR-6); shared bounded pool (FR-8, §4.3 assumption); free-tier constraints (§5); full-text search (FR-13); document uploads (FR-3, FR-26); skill-to-JD matching with explanation (FR-9); deadlines (FR-15); all-in scope with solo risk (§7.1, §8).

## Primary gaps

### G1. Eligibility filters and deadlines have no stated data source (under-specified)
- Problem statement asks explicitly for eligibility filters and application deadlines.
- FR-6 stores title, company, location, description, posted date, deadline "when present", URL. No eligibility fields (batch year, min CGPA, degree, work authorisation) are ingested, and no requirement says they are extracted from the JD text.
- LinkedIn is the primary source and rarely carries structured CGPA or batch criteria or a deadline. FR-14 therefore risks showing "Unknown" for nearly everything, which would hollow out UJ-2.
- Glossary defines Eligibility as including work authorisation, but FR-5 (Preferences) and FR-14 (filters) omit it.
- Suggested fix: add a requirement for extracting eligibility criteria and deadline from description text (with an Unknown fallback and a manual-edit path via FR-18), and align the attribute list across Glossary, FR-5 and FR-14.

### G2. Interview-value goals are not visible as requirements (silently dropped framing)
- The problem statement's "Interview value" line (relational DB design, role-based permissions, matching algorithms) appears nowhere as a goal. The only uses of "interview" in the PRD are the Interview Status and the "not interview-prep" non-goal.
- Relational DB design: a single NFR line (§5 "Data model") with no entity list, key relationships or integrity rules. No requirement or metric makes it demonstrable.
- Role-based permissions: with the recruiter role removed, RBAC shrinks to Student and Admin in FR-1, with two consequences. There is no permission matrix, and no statement that ownership checks (a Student can only touch their own rows) apply to every resource, including Contacts, Outreach and uploaded files.
- Matching algorithm: FR-9 defers the algorithm entirely to architecture, and Open Question 5 leans toward "any use of LLMs". For interview value the user presumably wants an algorithm the builder can explain and defend (deterministic, explainable, tested), not a black box.
- No success metric or acceptance item covers any of these.
- Suggested fix: add a short "Showcase requirements" note (or Vision sentence) naming the demonstrable engineering goals, a permission matrix, a minimum entity list with constraints, and a requirement that scoring is deterministic and explainable without an external LLM call.

### G3. Contradiction: platform provider account vs Student's own Provider Key
- §4.7 description, FR-21, §3 Glossary and FR-28 say Employee Discovery and email lookup run under the Student's own Apify key.
- §11 Assumptions Index says "§4.7 — Employee Discovery uses the platform's provider account, not Students' LinkedIn sessions" (stale).
- §8 mitigation for LinkedIn ToS says "scraping through a provider that carries the risk operationally", while the Provider Key risk bullet says the exposure now sits on the Student's Apify account. These two statements pull in opposite directions.
- FR-6 also lets an Admin set a "platform-wide cap", which is odd if the platform bears no scraping cost; its meaning is unclear.
- Suggested fix: correct §11 and §8 to the Student-key model; either define or remove the platform-wide cap.

### G4. Follow-ups may bypass the user's "student-approved" decision (contradiction)
- User decision: outreach is student-approved. FR-23 enforces approval for Outreach, but FR-24 and UJ-3 say the platform "schedules a follow-up in 5 days" with no approval step, and the Glossary defines Follow-up as a "scheduled repeat Outreach".
- Vision says "let the platform run the outreach", which reinforces the ambiguity.
- Suggested fix: state explicitly whether each Follow-up draft needs Student approval (or approval of the whole sequence up front, with a visible preview and cancel), and that this still respects the "no email without Approved state" consequence.

### G5. Qualitative value and feel are asserted but not specified or measured
- Time-saving is the product's core promise (Vision: "only sees roles worth their time", "short loop"; JTBD: "don't waste hours scrolling"), but no metric or requirement measures time saved (for example, time from signup to first ranked feed, weekly review time, applications per hour of use). SM-1 to SM-5 are activity metrics.
- No tone or feel guidance anywhere: nothing on the voice of outreach drafts (FR-22 only says "personalised"; no requirement for concise, honest, student-voice, non-spammy drafts), nothing on calm vs noisy feed behaviour, or on plain-language transparency beyond the consent screen.
- Related friction contradiction: FR-28 says "Realizes UJ-1", but UJ-1's path has no Provider Key step, and "within the first scheduled run he sees a ranked feed" assumes a key and a populated pool. For a Student without a key on a cold pool, SM-1 (first ranked feed within a day, 70%) is at risk. The journey does not show the key-setup friction or the no-key experience (FR-6 last consequence).
- Suggested fix: add an experience-principles block (tone, low-effort, transparency), a time-to-value metric, tone criteria for drafts, and a Provider Key step and no-key state in UJ-1.

## Minor gaps (lower priority)
- **Aggregation breadth:** problem statement says "aggregate"; user said LinkedIn is "primary". PRD has one source plus FR-18 manual entry and never says whether a keyless secondary source (addendum lists Greenhouse, Lever, Adzuna, RemoteOK) is in or out of v1. This matters for LinkedIn-block resilience and cold start.
- **Manual listings vs shared pool:** FR-18 does not say whether manually added Listings are private to the Student or enter the shared pool (affects the cap, dedup and the redistribution question in Open Question 3).
- **6-10 weeks without checkpoints:** all-in solo scope is accepted, but there are no week-level milestones or ordering other than the one-line build-order assumption; the only mitigation is "simple default technology and sequencing".
- **Document uploads:** FR-26 covers cover letters and other documents but gives no type, size or retention rules beyond Resumes (FR-3: 5 MB, PDF/DOCX), and no deletion linkage to FR-2 beyond Resumes.
