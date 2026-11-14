import Link from "next/link";

import type { ListingOut } from "@/api";
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

/** One Listing in the browse list. All scraped text is rendered as plain text. */
export function ListingCard({ listing }: { listing: ListingOut }) {
  const dates = listingDates(listing);
  const meta = listingMeta(listing);
  return (
    <article className="flex flex-col gap-3 rounded-lg border p-4" data-testid={`listing-${listing.id}`}>
      <div className="flex flex-col gap-1">
        <h2 className="font-medium break-words">
          <Link className="hover:underline" href={listingPath(listing.id)}>
            {listing.title}
          </Link>
        </h2>
        {meta ? <p className="text-sm text-muted-foreground break-words">{meta}</p> : null}
        {dates ? <p className="text-xs text-muted-foreground">{dates}</p> : null}
      </div>
      <SkillChips skills={listing.skills} />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <ListingAttribution listing={listing} />
        <ApplyLink listing={listing} />
      </div>
    </article>
  );
}
