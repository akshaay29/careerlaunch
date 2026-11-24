import type { ListingOut } from "@/api";
import { Button } from "@/components/ui/button";
import {
  NOT_AFFILIATED_NOTICE,
  applyLabel,
  safeExternalUrl,
  sourceLine,
} from "@/lib/listings";

/**
 * "via LinkedIn" plus the not-affiliated notice. Shown on every card and detail page; a
 * manual Listing (one the Student added) says "Added by you" instead.
 */
export function ListingAttribution({
  listing,
}: {
  listing: Pick<ListingOut, "source" | "source_label">;
}) {
  return (
    <div className="flex flex-col gap-0.5 text-xs text-muted-foreground" data-testid="listing-attribution">
      <span data-testid="listing-source">{sourceLine(listing)}</span>
      <span>{NOT_AFFILIATED_NOTICE}</span>
    </div>
  );
}

/**
 * "Apply" opens the Listing's source page in a new tab. Rendered only for an http(s) URL (a
 * job added without a link has none);
 * `noopener noreferrer nofollow` so the job board gets no handle on this page or referrer.
 */
export function ApplyLink({
  listing,
}: {
  listing: Pick<ListingOut, "url" | "source" | "source_label">;
}) {
  const href = safeExternalUrl(listing.url);
  if (!href) return null;
  return (
    <Button asChild size="sm">
      <a href={href} target="_blank" rel="noopener noreferrer nofollow" data-testid="listing-apply">
        {applyLabel(listing)}
      </a>
    </Button>
  );
}
