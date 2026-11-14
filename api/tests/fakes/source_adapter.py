"""In-memory SourceAdapter fake (AD-3). Tests never call a real Source."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.ports.source_adapter import ListingDraft, SourceQuery


@dataclass
class FakeSourceAdapter:
    """Yields the configured ``drafts`` (at most ``query.limit``). Records every query in
    ``calls``, so a test can assert the adapter was (or was not) called."""

    source: str = "linkedin"
    drafts: list[ListingDraft] = field(default_factory=list)
    calls: list[SourceQuery] = field(default_factory=list)

    async def collect(self, query: SourceQuery) -> AsyncIterator[ListingDraft]:
        self.calls.append(query)
        for draft in self.drafts[: query.limit]:
            yield draft
