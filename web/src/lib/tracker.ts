// Display rules for Applications (Story 4.1), pure so they can be unit tested with node --test.
// Relative imports: node --test erases type-only imports and cannot resolve the "@/" alias.
import type { ApplicationOut, StatusChange } from "../api/types.gen.ts";
import { LISTINGS_PATH, TRACKER_PATH } from "./auth-paths.ts";

export { LISTINGS_PATH, TRACKER_PATH };

export type ApplicationStatus = ApplicationOut["status"];

export function trackerPath(id: string): string {
  return `${TRACKER_PATH}/${encodeURIComponent(id)}`;
}

export const SAVE_LABEL = "Save";
export const SAVING_LABEL = "Saving…";
export const SAVED_LABEL = "Saved · Open";
export const NOT_AVAILABLE_NOTICE = "This Listing is no longer available";
export const NOTES_MAX = 4000;
export const DELETE_CONFIRM_TEXT =
  "This Application and its history will be deleted. This cannot be undone.";

const STATUS_LABELS: Record<ApplicationStatus, string> = {
  saved: "Saved",
  applied: "Applied",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

/** "Saved", "Applied", … */
export function statusLabel(status: ApplicationStatus): string {
  return STATUS_LABELS[status] ?? status;
}

const CLOSED: readonly ApplicationStatus[] = ["rejected", "withdrawn"];

export function isClosed(status: ApplicationStatus): boolean {
  return CLOSED.includes(status);
}

const ACTION_LABELS: Record<ApplicationStatus, string> = {
  saved: "Saved",
  applied: "Mark as applied",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdraw",
};

/** The button label for moving from `from` to `to`. Leaving Rejected or Withdrawn is "Reopen". */
export function transitionLabel(from: ApplicationStatus, to: ApplicationStatus): string {
  if (isClosed(from)) return "Reopen";
  return ACTION_LABELS[to] ?? to;
}

export type StatusAction = { status: ApplicationStatus; label: string };

/** One button per Status the API allows next, in the API's order. */
export function statusActions(
  application: Pick<ApplicationOut, "status" | "allowed_next">,
): StatusAction[] {
  return application.allowed_next.map((to) => ({
    status: to,
    label: transitionLabel(application.status, to),
  }));
}

const TIMESTAMP_FORMAT: Intl.DateTimeFormatOptions = {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};

/** A history timestamp as "24 Nov 2026, 14:05" in the viewer's time zone (or `timeZone`). */
export function formatChangedAt(value: string, locale = "en-GB", timeZone?: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(locale, { ...TIMESTAMP_FORMAT, timeZone });
}

export type HistoryLine = { key: string; status: string; when: string };

/** History entries as display lines, newest first (the API's order is kept). */
export function historyLines(
  items: StatusChange[],
  locale = "en-GB",
  timeZone?: string,
): HistoryLine[] {
  return items.map((item, index) => ({
    key: `${item.changed_at}-${index}`,
    status: statusLabel(item.to_status),
    when: formatChangedAt(item.changed_at, locale, timeZone),
  }));
}

/** The length the API counts: code points after trimming (an emoji is one, not two). */
export function notesLength(notes: string): number {
  return [...notes.trim()].length;
}

/** An error for notes over the limit (after trimming), or null. */
export function notesError(notes: string): string | null {
  return notesLength(notes) > NOTES_MAX ? `Notes must be ${NOTES_MAX} characters or fewer.` : null;
}

/** The earliest applied date the API accepts (the date input's min). */
export const MIN_APPLIED_ON = "2000-01-01";

/** Offer cannot be left once set: the button asks for confirmation. */
export function needsConfirmation(to: ApplicationStatus): boolean {
  return to === "offer";
}

export const OFFER_CONFIRM_TEXT =
  "An Offer is final: this Application's Status cannot be changed after this.";

/** The message for a refused Status change (409), naming what is allowed. */
export function invalidTransitionMessage(allowed: readonly ApplicationStatus[]): string {
  if (allowed.length === 0) return "This Status cannot be changed any more.";
  return `That change is not allowed. Next: ${allowed.map(statusLabel).join(", ")}.`;
}
