import { getApplicationsForListings } from "@/api";
import { savedByListing, savedLookupBatches } from "@/lib/tracker";

/**
 * `{listing_id: application_id}` for the given Listings the Student has saved, so cards can
 * show "Saved · Open". Optional: any failure gives `{}` (the cards keep plain "Save", which is
 * idempotent anyway).
 */
export async function fetchSavedState(listingIds: readonly string[]): Promise<Record<string, string>> {
  const found: Record<string, string> = {};
  for (const ids of savedLookupBatches(listingIds)) {
    try {
      const { data } = await getApplicationsForListings({ query: { ids } });
      Object.assign(found, savedByListing(data?.items ?? []));
    } catch {
      // Keep plain Save for this batch.
    }
  }
  return found;
}
