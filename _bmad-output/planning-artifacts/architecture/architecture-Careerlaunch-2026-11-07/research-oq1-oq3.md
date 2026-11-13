# Research: OQ-1 (Apify Provider Key model) and OQ-3 (shared listing pool)

Date: 2026-11-07. Sources were fetched on this date. These are summaries of what the documents say and an assessment of risk. They are not legal advice. Store prices change often, so check each actor's Pricing tab before you commit.

Labels: **[Official]** means Apify or LinkedIn wrote it. **[Actor page]** means the third-party developer's own listing on apify.com. **[Third-party]** means press, law-firm commentary or vendor blogs.

---

## OQ-1: Running actors with each Student's Apify token

### (a) Is a third-party app allowed to use an end user's personal token?

**What the terms say**
- **[Official] General Terms §4.2:** "you must safeguard your login credentials and ensure no third party accesses or uses your Account" and "You are responsible for all activity under your Account". The terms never mention API tokens.
  https://docs.apify.com/legal/general-terms-and-conditions
- **[Official] Acceptable Use Policy Art. 2.1(9)** prohibits "resale of any Platform features without obtaining Apify's prior written approval". CareerLaunch is free and resells nothing, so this clause probably does not apply.
  https://docs.apify.com/legal/acceptable-use-policy
- **[Official] The API integration docs approve of handing a token to a service, as long as it is a limited one.** They advise:
  - use a separate token for each service;
  - give a third party "a scoped token that can only run the Actor" it needs;
  - set an expiration date on tokens meant for "temporary access or third-party services".
  https://docs.apify.com/platform/integrations/api
- **[Official] The "Integrate with Apify" guide** describes token-based and OAuth 2.0 integrations: "Use OAuth 2.0 to allow users to authorize your integration without sharing their credentials". It asks every API request to identify the platform with the `x-apify-integration-platform` header (or a custom User-Agent). It also says to contact integrations@apify.com before publishing an integration.
  https://docs.apify.com/platform/integrations/integrate

**Reading:** Nothing prohibits the Provider Key model, and Apify's own docs describe users giving a token to a third-party service. The terms do have a tension. "No third party accesses or uses your Account" is written about login credentials, but a cautious reader could apply it to tokens. The documented guidance (scoped token, one per service, expiry) is how Apify expects that tension to be handled. Confidence: medium-high.

**Official mechanisms that beat storing a raw personal token**
1. **Scoped API tokens [Official]**
   - Permissions can be account-level ("run all Actors") or limited to one resource ("Run" on Actor X, "Read" on dataset Y).
   - Scoped tokens cannot create or modify Actors.
   - Run them in Restricted access mode so the Actor receives a token with the same scope. Full access mode gives the Actor an unscoped token.
   - Tokens can expire and can be rotated (the old token stays valid for 24 hours).
   - Caveats: Standby mode does not support Restricted access, and scheduled runs always use an unscoped token.

   For CareerLaunch: ask Students to create a scoped token with **Run** on only the 2-3 approved actors. The default dataset each run creates is readable by the token that created it, because "creating a new resource grants the token full access to that resource". https://docs.apify.com/platform/integrations/api
2. **Apify OAuth 2.0 [Official metadata]**
   - Apify publishes an OAuth authorization server at https://console-backend.apify.com/.well-known/oauth-authorization-server (apify.com redirects there).
   - Authorization endpoint `https://console.apify.com/authorize/oauth`, token endpoint `.../oauth/apps/token`, dynamic client registration `.../oauth/apps`.
   - Supports authorization code with PKCE (S256), refresh tokens and device code.
   - **The only advertised scope is `full_api_access`.**
   - [Third-party] Pipedream offers an "Apify (OAuth)" app built on this server: https://pipedream.com/apps/apify-oauth

   Apify does not publish a step-by-step guide to "Sign in with Apify" for arbitrary web apps. OAuth spares Students from copy-pasting tokens and supports revocation. However, the access it grants is broader than a scoped token, because `full_api_access` is the only scope.

**Recommendation**
- MVP: accept a **scoped, expiring token**. Add onboarding copy telling the Student to grant Run on only the listed actors.
- Store the token encrypted at rest and never log it.
- Send `x-apify-integration-platform: careerlaunch` on every request.
- At save time, validate the token with `GET /v2/users/me`.
- Consider OAuth (PKCE) later. Email integrations@apify.com if the app becomes public.

### (b) Billing and running out of credit
- **[Official] Actor Terms §2.1:** "Your use of Actors consumes your prepaid usage and resources, as billed to you". The run is charged to whichever account owns the token that starts it, which is the Student.
  https://docs.apify.com/legal/actor-terms-and-conditions
- **[Official] Pricing page:**
  - The Free plan includes USD 5/month of prepaid usage, at $0.20 per compute unit (CU).
  - Pay-per-event Actors also draw on the plan's usage credit.
  - Unused credit does not roll over.
  - When usage runs out on Free, "your access to Apify's services will be blocked until the beginning of the next monthly cycle". Pay-as-you-go overage is offered only on paid plans.

  https://apify.com/pricing
- **[Official] Billing docs:** "Apify platform services will be suspended to prevent incurring charges beyond your subscription plan." https://docs.apify.com/platform/console/billing
- **[Official] Store docs:** you can set a **maximum charge per run**, and "the Actor terminates gracefully when it reaches that limit". https://docs.apify.com/platform/actors/running/actors-in-store

**Implications**
- Pass a max-charge value (for example `maxTotalChargeUsd`) on every run.
- Read the Student's remaining usage before starting a run (`GET /v2/users/me/limits`).
- When credit runs out, expect run-start calls to fail with an error. Handle that gracefully: mark the Provider Key as "exhausted until <cycle date>" and keep serving the shared pool.

### (c) Candidate actors (checked 2026-11-07 [Actor page])

All of these are community actors, not built by Apify or LinkedIn. Users get the actor developer's terms (the Standard Actor Contract, unless the developer replaces it).

| Need | Actor | Pricing model | Listed price | Cookies? | Usage signals |
|---|---|---|---|---|---|
| Jobs | `curious_coder/linkedin-jobs-scraper` https://apify.com/curious_coder/linkedin-jobs-scraper | Pay per event, no separate platform charge | from $1.00 / 1,000 results | No cookie, session or `li_at` field in the input schema | ~180k total users, ~14.8k monthly, 4.5 stars |
| Jobs (alt) | `harvestapi/linkedin-job-search` https://apify.com/harvestapi/linkedin-job-search | Pay per event | from $1.00 / 1,000 job details | Not required. The schema has an **optional** cookie field, which CareerLaunch must never set | ~8.6k total, ~800 monthly, 3.8 stars |
| Jobs (alt 2) | `apimaestro/linkedin-jobs-scraper-api` https://apify.com/apimaestro/linkedin-jobs-scraper-api | Pay per event | from $5.00 / 1,000 | "No Cookies" | ~5.4k total, 3.7 stars |
| Employees / people | `harvestapi/linkedin-company-employees` https://apify.com/harvestapi/linkedin-company-employees | Pay per event | Short $4, Full $8, Full + email search $12 per 1,000 profiles, plus $0.02 per company start in batch mode. The headline also says "from $1.50/1k", so verify the current rate | "No cookies or account" | ~42.5k total, ~6.4k monthly, 4.0 stars, 99.8% run success |
| Employees (alt) | `artificially/linkedin-employees-scraper` https://apify.com/artificially/linkedin-employees-scraper | Pay per result (search snippet only) | from $7.00 / 1,000 | Claims no login or cookies (not verified) | not verified |
| Email finder | `overpowered/email-finder` https://apify.com/overpowered/email-finder | Pay per event **plus platform usage** | from $9.00 / 1,000 emails. The page does not say whether you pay per found email or per attempt | n/a (inputs are name, surname, domain) | ~2.2k total, 4.7 stars |
| Email (alt) | The "Full + email search" mode of `harvestapi/linkedin-company-employees` (above) | Pay per event | $12 / 1,000 profiles, and you are not charged when a profile is too incomplete to search | No | as above |
| Email (alt 2) | `davidsharadbhatt/email-finder-by-name-company-domain` https://apify.com/davidsharadbhatt/email-finder-by-name-company-domain | Pay per result (search snippet only) | $10 / 1,000 | n/a | not verified |

**Avoid**
- Rental actors (for example the "$19-35/month + usage" email finders). They cost more than the Free plan's credit.
- `khadinakbar/linkedin-company-employee-scraper`. Its search snippet mentions optional cookie fallbacks.
- Any actor that asks for an `li_at` or session cookie. That is disallowed in this product.

**Approximate cost for one Student "cycle"** (list prices; Free-plan users may pay the top tier):
- 100 job listings: about $0.10 with curious_coder or harvestapi, or about $0.50 with apimaestro.
- 10 employee profiles: about $0.04 (short) to $0.08 (full), plus $0.02 company start, with harvestapi.
- 10 email lookups: about $0.09 plus a few cents of platform usage with overpowered, or about $0.12 total using harvestapi's "Full + email" mode, which covers employees and emails together.
- **Total: about $0.25-$0.80 per cycle, so USD 5/month covers roughly 6-20 cycles.** That is enough for a typical student running a few searches a week.
- A daily automatic job pull at 100 jobs/day costs about $3/month at $1/1k, leaving little room for people or email lookups.

**Recommendations**
- Cap runs per key.
- Rely on the shared pool, so that one Student's run serves everyone. This is also the main cost lever.
- Set `maxItems` / `limitPerSource` and a max-charge value on every run.

---

## OQ-3: Redistributing scraped listings in a shared pool

### (a) Apify and actor terms on redistributing output
- **[Official] General Terms §5.8:**
  - "you retain all right, title, and interest in and to your Customer Data."
  - "You are solely responsible for the legality, accuracy, quality, appropriateness, and use of all Customer Data."
  - §6.2 says to process only data "you are authorized to access".
  - §11.1 makes you liable if you extract data "from unauthorized sources".
  - §5.2(ii) bars redistributing *the Services*, not your output.

  https://docs.apify.com/legal/general-terms-and-conditions
- **[Official] Actor Terms:**
  - §3.2: "you retain all intellectual property rights that subsist in your Actor Input and Actor Output". It also grants the developer a license to process them.
  - The terms contain no clause restricting redistribution of output.
  - §2.2 requires use consistent with the Creator's terms, the Acceptable Use Policy and "all applicable laws".
  - §7.1 makes you indemnify Apify for "your violation of any third-party's rights".

  https://docs.apify.com/legal/actor-terms-and-conditions
- **[Official] Store Publishing Terms §4.1:** the Standard Actor Contract applies by default. Developers may publish their own end-user terms, as long as those do not give "materially less protection". https://docs.apify.com/legal/store-publishing-terms-and-conditions
- **[Actor page]** None of the candidate actor pages checked here publish terms on how output may be used. They only say they are not affiliated with LinkedIn.

**Reading:** Apify and the actor terms do **not** restrict sharing output with other users of your platform. They push all legal responsibility for the data, and for any third-party rights such as LinkedIn's, onto the account holder. In this design that is the Student who owns the token, while CareerLaunch is the operator that actually republishes the data. Confidence: medium-high.

### (b) LinkedIn User Agreement (effective 3 Nov 2025) [Official]
https://www.linkedin.com/legal/user-agreement
- **§1.2:** "This Contract applies to Members and Visitors". This covers logged-out visitors too.
- **§8.2, things you must not do:**
  - **Item 2:** "Develop, support or use software, devices, scripts, robots or any other means or processes (such as crawlers, browser plugins and add-ons or any other technology) to scrape or copy the Services, including profiles and other data from the Services".
  - **Item 4:** "Copy, use, display or distribute any information (including content) obtained from the Services, whether directly or through third parties (such as search tools or data aggregators or brokers), without the consent of the content owner".
  - **Item 6:** do not copy or distribute others' posts or content except through LinkedIn's own sharing functionality.
  - **Item 11:** "Rent, lease, loan, trade, sell/re-sell or otherwise monetize the Services or related data ... without LinkedIn's consent".
  - **Item 13:** no "bots or other unauthorized automated methods to access the Services".
- **§1.1** defines "Content" to include "job postings". Under §3.1, posters license their content to LinkedIn, but members get no right to republish it.

**Reading:** LinkedIn's contract expressly forbids both the scraping and the republishing ("display or distribute") of information obtained from the service, job postings included. CareerLaunch does not scrape directly, but the Student uses an actor to scrape and CareerLaunch shows the result to others. That is the "through third parties ... data aggregators" pattern named in Item 4. Confidence: high (on what the text says).

**Case law context [Third-party; US-centric; not legal advice]**
- **hiQ v. LinkedIn**
  - The 9th Circuit (2022) held that scraping public data likely does not violate the CFAA.
  - On remand, the N.D. Cal. court (Nov 2022) granted LinkedIn summary judgment on **breach of the User Agreement**.
  - In the Dec 2022 consent judgment, hiQ accepted liability, a permanent injunction, and deletion of the scraped data.
- **Meta v. Bright Data (Jan 2024):** the court held that Meta's terms did not bind logged-out scraping of public data. The ruling turned on Meta's wording, and LinkedIn's §1.2 explicitly covers Visitors.
- Sources:
  - https://www.fbm.com/alex-reese/publications/what-recent-rulings-in-hiq-v-linkedin-and-other-cases-say-about-the-legality-of-data-scraping/
  - https://www.fbm.com/post/102kqw7/
  - https://techcrunch.com/2024/01/24/court-rules-in-favor-of-a-web-scraper-bright-data-which-meta-had-used-and-then-sued
- GDPR or India's DPDP Act may also apply to the **employee profile and email** data, because that is personal data about third parties who never consented.

### (c) Is a shared pool defensible, and what reduces risk?

**Risk assessment (not legal advice)**
- **Job listings in a shared pool:** low-to-moderate practical risk for a small, non-commercial, non-monetized student project. It is still a contractual breach of LinkedIn §8.2 on the face of the text, and being non-commercial does not cure it (Items 2, 4 and 13 do not depend on monetization). The realistic consequences are a cease-and-desist letter, IP or proxy blocking of the actors, or action against the scraping account, not damages. Enforcement usually targets commercial-scale aggregators.
- **Employee profiles and emails in a shared pool:** **high risk.** These are personal data about non-users. Redistributing them to every Student creates a data broker pattern (LinkedIn Item 4 names "data aggregators or brokers") and invites privacy-law exposure. **Do not pool people or email data.** Keep it private to the Student whose token produced it, and delete it after a short TTL.

**Mitigations that lower the risk of pooled job listings**
1. **Store minimal facts only:** title, company name, location, posted date, job type and seniority, plus the **canonical source URL**. Do not store the full description text, company logos or recruiter or poster names. Facts such as title, company and location are much less likely to be protected expression than the full description.
2. **Link back:** always link "Apply / View on LinkedIn" to the original posting, so the student applies on LinkedIn and the platform never substitutes for it.
3. **Attribution and disclaimer:** label each listing "Source: LinkedIn (public listing)" and state "not affiliated with or endorsed by LinkedIn".
4. **Expire listings:** remove pooled listings after N days (for example 30) or when the source URL returns 404 or "no longer accepting".
5. **Takedown path:** publish a contact or email for removal requests. Act on them quickly, block the URL or company from re-ingestion, and log the action.
6. **Non-commercial posture:** no ads, no paywall, no data export or API, and no selling. This keeps clear of Item 11 ("monetize ... related data").
7. **Provenance per listing:** record which Provider Key collected each row. That makes it possible to purge one Student's contributions, and to show that each Student consented to contributing in the onboarding terms.
8. **Diversify sources:** prefer job sources with an open policy (company career pages, ATS feeds such as Greenhouse or Lever public boards, government job portals), so that LinkedIn is not the only feed.
9. **Never use cookies or logged-in scraping.** Logged-in scraping greatly increases the risk under Items 2 and 13 and puts the Student's LinkedIn account at risk.

**Architecture implications**
- The **job-listing pool can stay shared**, using minimal fields, a source URL, a TTL, attribution and a takedown and blocklist table.
- **People and email results must be per-owner** (scoped to the Student whose token ran them). They should never enter the shared pool and should have a short retention period.
- Onboarding terms should get each Student's consent to contribute the job listings they collect to the pool, and should remind them that they are responsible for their Apify account's use (Apify §4.2 and §5.8).
