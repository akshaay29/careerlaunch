// Display rules for the ranked feed (Story 3.3; pure, so they can be unit tested with node --test).
// Relative imports: node --test erases type-only imports and cannot resolve the "@/" alias.
import type { Feed, FeedItem } from "../api/types.gen.ts";
import { FEED_PATH } from "./auth-paths.ts";

export { FEED_PATH };

export const FEED_PAGE_SIZE = 20;

/** The URL parameter of the "Show below my threshold" toggle: `below=1`. */
export const BELOW_PARAM = "below";

export const SHOW_BELOW_LABEL = "Show below my threshold";
export const BELOW_THRESHOLD_LABEL = "Below your threshold";
export const PROVISIONAL_MESSAGE =
  "Scores are being prepared. These are early matches from the newest Listings.";
export const SETUP_NEEDED_MESSAGE =
  "Add your skills and job preferences to see Listings ranked for you.";
export const BELOW_THRESHOLD_EMPTY_MESSAGE =
  "No Listings meet your threshold yet. You can show the ones below it.";
export const NOTHING_TO_RANK_MESSAGE = "No Listings match your Preferences yet.";

/** Whether the toggle is on: only `below=1` (the first value when repeated). */
export function belowFromParam(value: string | string[] | null | undefined): boolean {
  const first = Array.isArray(value) ? value[0] : value;
  return first === "1";
}

/** The feed page with the toggle on (`/feed?below=1`) or off (`/feed`). */
export function feedHref(showBelow: boolean): string {
  return showBelow ? `${FEED_PATH}?${BELOW_PARAM}=1` : FEED_PATH;
}

/** The `getFeed` query for one page. */
export function feedQuery(
  showBelow: boolean,
  cursor?: string | null,
): { limit: number; show_below_threshold: boolean; cursor?: string } {
  const query: { limit: number; show_below_threshold: boolean; cursor?: string } = {
    limit: FEED_PAGE_SIZE,
    show_below_threshold: showBelow,
  };
  if (cursor) query.cursor = cursor;
  return query;
}

/** The score badge: "72 / 100". */
export function scoreBadge(score: number): string {
  return `${score} / 100`;
}

/** Appends a page, dropping Listings already shown. */
export function mergeFeedPages(current: FeedItem[], next: FeedItem[]): FeedItem[] {
  const seen = new Set(current.map((item) => item.id));
  return [...current, ...next.filter((item) => !seen.has(item.id))];
}

/** The neutral status line above the list (provisional only), or null. */
export function feedNotice(state: Feed["state"]): string | null {
  return state === "provisional" ? PROVISIONAL_MESSAGE : null;
}

/**
 * What an empty feed says. `setup_needed` gets the finish-setup prompt. The toggle is offered
 * only when the API says Listings are hidden below the threshold (`hidden_below_threshold`).
 */
export function feedEmptyMessage(state: Feed["state"], hiddenBelowThreshold: boolean): string {
  if (state === "setup_needed") return SETUP_NEEDED_MESSAGE;
  return hiddenBelowThreshold ? BELOW_THRESHOLD_EMPTY_MESSAGE : NOTHING_TO_RANK_MESSAGE;
}

/** Whether a further page no longer fits the one shown (its state or threshold changed), so
 * the feed should reload from the first page instead of appending. */
export function feedChanged(
  current: Pick<Feed, "state" | "threshold">,
  next: Pick<Feed, "state" | "threshold">,
): boolean {
  return current.state !== next.state || current.threshold !== next.threshold;
}
