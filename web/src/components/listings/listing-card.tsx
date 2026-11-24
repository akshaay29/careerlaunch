import Link from "next/link";
import type { ReactNode } from "react";

import type { ListingOut } from "@/api";
import { SaveListingButton } from "@/components/tracker/save-listing-button";
import { listingDates, listingMeta, listingPath } from "@/lib/listings";

import { ApplyLink, ListingAttribution } from "./listing-attribution";

/** Skill chips. Labels are plain text. */
export function SkillChips({ skills }: { skills: ListingOut["skills"] }) {
  if (skills.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Skills">
      {skills.map((skill) => (
        <li key={skill.id} className="rounded-full border bg-muted/50 px-2 py-0.5 text-xs">
          {skill.label}
        </li>
      ))}
    </ul>
  );
}

/**
 * One Listing in the browse list or the feed. All scraped text is rendered as plain text.
 * `badge` (the Match Score with "Why this score", in the feed and search results) is shown
 * beside the title. "Save" (idempotent) turns into "Saved · Open" once the Listing is saved;
 * `applicationId` (from the page's batch saved-state lookup) shows it as saved from the start.
 */
export function ListingCard({
  listing,
  badge,
  applicationId = null,
}: {
  listing: ListingOut;
  badge?: ReactNode;
  applicationId?: string | null;
}) {
  const dates = listingDates(listing);
  const meta = listingMeta(listing);
  return (
    <article className="flex flex-col gap-3 rounded-lg border p-4" data-testid={`listing-${listing.id}`}>
      <div className="flex flex-col gap-1">
        <div className="flex items-start justify-between gap-3">
          <h2 className="min-w-0 font-medium break-words">
            <Link className="hover:underline" href={listingPath(listing.id)}>
              {listing.title}
            </Link>
          </h2>
          {badge}
        </div>
        {meta ? <p className="text-sm text-muted-foreground break-words">{meta}</p> : null}
        {dates ? <p className="text-xs text-muted-foreground">{dates}</p> : null}
      </div>
      <SkillChips skills={listing.skills} />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <ListingAttribution listing={listing} />
        <div className="flex flex-wrap items-start gap-2">
          <SaveListingButton
            key={applicationId ?? "unsaved"}
            listingId={listing.id}
            applicationId={applicationId}
          />
          <ApplyLink listing={listing} />
        </div>
      </div>
    </article>
  );
}
