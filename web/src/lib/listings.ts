// Display rules for Listings (pure, so they can be unit tested with node --test).
// Relative imports: node --test erases type-only imports and cannot resolve the "@/" alias.
import type { ListingOut } from "../api/types.gen.ts";
import { LISTINGS_PATH } from "./auth-paths.ts";

export { LISTINGS_PATH };

export function listingPath(id: string): string {
  return `${LISTINGS_PATH}/${encodeURIComponent(id)}`;
}

export const NOT_AFFILIATED_NOTICE = "CareerLaunch is not affiliated with LinkedIn or any job board.";
export const LISTINGS_EMPTY_MESSAGE = "No Listings yet — new ones arrive as collection runs.";

/** The Source of Listings a Student added by hand (Epic 4): no job board to credit. */
export const MANUAL_SOURCE = "manual";

/** "via LinkedIn" attribution for a Listing's Source; null for a manual Listing. */
export function sourceAttribution(
  listing: Pick<ListingOut, "source" | "source_label">,
): string | null {
  return listing.source === MANUAL_SOURCE ? null : `via ${listing.source_label}`;
}

/** "Apply on LinkedIn", or plain "Apply" for a manual Listing. */
export function applyLabel(listing: Pick<ListingOut, "source" | "source_label">): string {
  return listing.source === MANUAL_SOURCE ? "Apply" : `Apply on ${listing.source_label}`;
}

/** Company · location · job type, skipping the parts the Source did not give. */
export function listingMeta(listing: Pick<ListingOut, "company" | "location" | "job_type">): string {
  return [listing.company, listing.location, formatJobType(listing.job_type)]
    .filter((part): part is string => Boolean(part && part.trim()))
    .join(" · ");
}

/** "Internship", "Full-time", or null when the Source did not say. */
export function formatJobType(jobType: ListingOut["job_type"]): string | null {
  if (jobType === "internship") return "Internship";
  if (jobType === "full_time") return "Full-time";
  return null;
}

const DATE_FORMAT: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", year: "numeric" };

/**
 * A calendar date ("2026-12-15", a deadline) as "15 Dec 2026". Formatted in UTC so the day
 * never shifts with the viewer's time zone. Null for a missing or malformed value.
 */
export function formatDate(value: string | null | undefined, locale = "en-GB"): string | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  if (Number.isNaN(date.getTime()) || date.getUTCDate() !== Number(match[3])) return null;
  return date.toLocaleDateString(locale, { ...DATE_FORMAT, timeZone: "UTC" });
}

/** A timestamp (posted date) as a date in the viewer's time zone (or ``timeZone``). */
export function formatTimestampDate(
  value: string | null | undefined,
  locale = "en-GB",
  timeZone?: string,
): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString(locale, { ...DATE_FORMAT, timeZone });
}

/** "Posted 1 Nov 2026 · Apply by 15 Dec 2026" (each part only when known). */
export function listingDates(
  listing: Pick<ListingOut, "posted_at" | "deadline">,
  locale = "en-GB",
  timeZone?: string,
): string | null {
  const parts: string[] = [];
  const posted = formatTimestampDate(listing.posted_at, locale, timeZone);
  const deadline = formatDate(listing.deadline, locale);
  if (posted) parts.push(`Posted ${posted}`);
  if (deadline) parts.push(`Apply by ${deadline}`);
  return parts.length ? parts.join(" · ") : null;
}

/**
 * The Listing's source URL when it is a safe absolute http(s) link, else null (then no
 * "Apply" link is rendered). The server only stores http(s) URLs; this is defence in depth
 * against `javascript:` and similar schemes.
 */
export function safeExternalUrl(url: string | null | undefined): string | null {
  if (!url || /[\u0000-\u001f\u007f\s\\]/.test(url)) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    if (!parsed.hostname) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

/** Newest first, as the API returns them; merges a further page without duplicates. */
export function mergeListingPages(current: ListingOut[], next: ListingOut[]): ListingOut[] {
  const seen = new Set(current.map((l) => l.id));
  return [...current, ...next.filter((l) => !seen.has(l.id))];
}
