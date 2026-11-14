"""accounts module."""

# ``schedules.kind`` values that collect Listings under a Student's Provider Key. Deleting the
# key pauses the owner's rows of these kinds (FR-28). Story 2.3 creates them.
COLLECTION_SCHEDULE_KINDS: tuple[str, ...] = ("listings.collect",)
