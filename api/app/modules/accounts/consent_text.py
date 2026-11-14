"""Consent texts and the rights notice (AD-11, FR-2). Owned by ``accounts``, served by the API.

Every purpose has a ``version``. A stored consent row records the version the Student agreed
to, and ``require_consent`` passes only on the current version, so a stored version always
names text the server actually showed.

To change a text: edit its paragraphs AND set its ``version`` to the date of the change
(``YYYY-MM-DD``). Every Student's existing consent for that purpose then shows ``outdated``
and the gate blocks until they agree again. Editing the rights notice bumps
``RIGHTS_NOTICE_VERSION`` the same way (it needs no agreement).

Texts are plain text: a tuple of paragraphs, never HTML or Markdown.
"""

from dataclasses import dataclass
from enum import StrEnum


class ConsentPurpose(StrEnum):
    """Phase 1 purposes. Phase 2 adds ``linkedin_link``, ``outreach`` and ``llm_drafting``
    (with a migration widening ``consents_purpose_check``)."""

    PROVIDER_RUNS = "provider_runs"
    POOL_CONTRIBUTION = "pool_contribution"


@dataclass(frozen=True)
class ConsentText:
    purpose: ConsentPurpose
    version: str
    title: str
    body: tuple[str, ...]


@dataclass(frozen=True)
class RightsNotice:
    version: str
    title: str
    body: tuple[str, ...]


CONSENT_TEXTS: dict[ConsentPurpose, ConsentText] = {
    ConsentPurpose.PROVIDER_RUNS: ConsentText(
        purpose=ConsentPurpose.PROVIDER_RUNS,
        version="2026-11-14",
        title="Run job searches under your own Apify account",
        body=(
            "CareerLaunch can collect job Listings for you by starting searches (called runs) "
            "on Apify, a third-party data collection service, using an Apify API token that "
            "you provide.",
            "Every run happens under your own Apify account. Apify charges the cost of each "
            "run to your Apify account under your Apify plan. CareerLaunch does not pay for "
            "these runs and cannot refund them. We limit the size and cost of each run, but "
            "your Apify usage remains your responsibility.",
            "Some runs collect Listings from LinkedIn. LinkedIn's terms of use restrict "
            "automated collection (scraping) of its site. By agreeing, you accept that risk "
            "for runs made under your account.",
            "You can withdraw this consent at any time in Settings. After you withdraw it, no "
            "new runs start under your account.",
        ),
    ),
    ConsentPurpose.POOL_CONTRIBUTION: ConsentText(
        purpose=ConsentPurpose.POOL_CONTRIBUTION,
        version="2026-11-14",
        title="Share the Listings collected under your key",
        body=(
            "Job Listings collected by runs under your Apify key can be added to a shared "
            "pool. Other CareerLaunch Students whose Preferences match a Listing can then "
            "see it.",
            "Only the job Listing is shared: its title, company, location, job type, dates, "
            "link, and the skills and requirements found in it. Your Resume, Skill Profile, "
            "Preferences and contact details are not shared.",
            "Without this consent, Listings collected under your key stay visible only to you.",
            "You can withdraw this consent at any time in Settings.",
        ),
    ),
}

RIGHTS_NOTICE_VERSION = "2026-11-14"

RIGHTS_NOTICE = RightsNotice(
    version=RIGHTS_NOTICE_VERSION,
    title="Your rights under India's Digital Personal Data Protection Act, 2023",
    body=(
        "Access: you can ask for a summary of the personal data we hold about you and how we "
        "use it.",
        "Correction: you can ask us to correct, complete or update your personal data.",
        "Erasure: you can delete your account at any time. Your account and all of its data, "
        "including stored files, are deleted within 30 days.",
        "Withdrawing consent: you can withdraw any consent at any time, as easily as you gave "
        "it. Withdrawing does not affect processing that happened before.",
        "Grievance: you can raise a grievance with us about how your personal data is handled. "
        "If it is not resolved, you can complain to the Data Protection Board of India.",
    ),
)


def current_text(purpose: ConsentPurpose | str) -> ConsentText:
    """The text currently shown for ``purpose``. Raises ``ValueError`` for an unknown purpose."""
    return CONSENT_TEXTS[ConsentPurpose(purpose)]
