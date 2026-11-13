# Glossary: CareerLaunch

These names are canonical. Code, UI and stories use these terms.

- **Student**: a signed-in job seeker; the only end-user role.
- **Admin**: an operator who manages Sources, runs, misuse reports and the do-not-contact list. Not a hiring-side role.
- **Resume**: a document the Student uploads. A Student can hold several versions; one is active for scoring.
- **Skill Profile**: the skills, education and experience extracted from the active Resume and confirmed by the Student.
- **Preferences**: the Student's target roles, locations, job types, Eligibility attributes, Match Score threshold, alert settings and time zone.
- **Listing**: a job or internship collected from a Source, stored once and deduplicated.
- **Source**: where Listings come from. LinkedIn, collected through Apify, is the v1 Source.
- **Source Adapter**: the interface that isolates a Source so another can be added or swapped.
- **Shared pool**: the single deduplicated set of Listings collected under any consenting Student's Provider Key and visible to every Student whose Preferences match.
- **Match Score**: a 0–100 score of how well the active Resume fits a Listing, with an explainable breakdown.
- **Eligibility**: a Listing's hard criteria (batch year, minimum CGPA, degree, location, work authorisation) compared against the Student's attributes; shown as Eligible, Not eligible or Unknown.
- **Application**: a Student's tracked record for a Listing, from Saved to outcome. Applying happens outside the platform.
- **Status**: the stage of an Application: Saved, Applied, Interview, Offer, Rejected, Withdrawn.
- **Tracker**: the Student's view of all Applications, deadlines, Outreach and Follow-ups.
- **Contact**: a person at a target company found by Employee Discovery. Belongs to the Student who discovered them.
- **Employee Discovery**: finding Contacts at a company related to an Application.
- **Outreach**: a referral or cold email to a Contact. Student-facing states are Draft, Approved, Sent, Replied, Failed and Cancelled; Queued and Sending are used internally while a send is pending.
- **Follow-up**: a repeat Outreach to the same Contact, drafted in advance and approved together with the first Outreach.
- **Do-not-contact list**: the platform-wide list of people who opted out or were blocked by an Admin; no Student can email them.
- **Document**: a file a Student uploads other than a Resume, such as a cover letter.
- **Connected Account**: a Student's authorised link to Gmail (sending) or LinkedIn (identity).
- **Provider Key**: a Student's own scoped Apify API token; scraping and lookups for that Student run and are billed under their Apify account.
