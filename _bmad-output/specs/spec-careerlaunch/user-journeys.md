# User Journeys: CareerLaunch

Persona throughout: **Aarav**, a final-year CS student with no offers yet, targeting software internships and entry-level roles in Bangalore and remote.

## UJ-1: Set up once, get a ranked feed (CAP-1 to CAP-5, CAP-9, CAP-10, CAP-11, CAP-28)
- **Entry:** a new visitor on the web, with no account.
- **Path:**
  1. Sign up.
  2. Upload a PDF Resume.
  3. Correct two of the extracted skills.
  4. Set Preferences: role titles, locations, job type, threshold 70, graduation year 2026.
  5. Paste a scoped Apify token, following the in-app steps.
  6. Give consent.
- **Climax:** a ranked feed from the shared pool appears immediately, each Listing with a Match Score and a breakdown of matched and missing skills. His first collection run adds fresh Listings within the hour.
- **Resolution:** digest alerts arrive when new Listings clear his threshold.
- **Edge cases:**
  - A scanned Resume with no text gets an extraction-failed message, with the choice to upload a text PDF or enter skills manually.
  - A Student without a key sees only the shared pool and is reminded that a key unlocks fresh collection.

## UJ-2: Filter by eligibility, watch deadlines (CAP-13, CAP-14, CAP-15, CAP-29)
- **Entry:** logged in, on the feed. Aarav has a 7.8 CGPA and is in the 2026 batch.
- **Path:**
  1. Search "backend intern".
  2. Filter by batch year, CGPA, location, and deadline within 7 days.
  3. Open a Listing.
- **Climax:** the Listing shows its deadline and criteria (7.5 cut-off, 2026 batch) and is marked Eligible.
- **Resolution:** he saves it and gets a reminder two days before the deadline.

## UJ-3: Apply, then the platform runs outreach (CAP-16, CAP-19 to CAP-25)
- **Entry:** on a Listing page, Status Saved, just after applying on the company's site.
- **Path:**
  1. Mark the Application Applied.
  2. The platform proposes Contacts (engineers, hiring managers, alumni), finds work emails, and drafts a referral email per Contact.
  3. Aarav edits one draft and approves two, along with their Follow-ups.
- **Climax:** the emails go out from his own Gmail within the daily cap, and the Tracker shows Outreach Sent.
- **Resolution:** Follow-ups send at day 5 and day 10 after the first send, unless the Contact replies, opts out, or he changes the Application to Rejected or Withdrawn.
- **Edge case:** if no email is found, the platform says so and offers a LinkedIn message draft for him to send himself.

## UJ-4: Track everything and improve (CAP-3, CAP-12, CAP-16, CAP-17, CAP-26)
- **Entry:** three weeks in, with 18 Applications, in the Tracker view.
- **Path:**
  1. View Applications by Status.
  2. Move one to Interview.
  3. Upload a tailored Resume version.
  4. Check which skills are missing most often across low-scoring Listings.
- **Climax:** the top skill gaps show him what to learn.
- **Resolution:** he re-uploads an improved Resume, and his scores refresh.
