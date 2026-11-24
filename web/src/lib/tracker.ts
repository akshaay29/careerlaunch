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

// --------------------------------------------------------------------------- the Tracker (4.2)

export const STATUSES: readonly ApplicationStatus[] = [
  "saved",
  "applied",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
];

export type DateBy = "saved" | "applied";

/** The Tracker filters, as kept in the URL (`status` repeated, `company`, `from`, `to`, `by`). */
export type TrackerFilters = {
  statuses: ApplicationStatus[];
  company: string;
  dateFrom: string;
  dateTo: string;
  dateBy: DateBy;
};

export const EMPTY_FILTERS: TrackerFilters = {
  statuses: [],
  company: "",
  dateFrom: "",
  dateTo: "",
  dateBy: "saved",
};

export const COMPANY_MAX = 100;
/** The Upcoming deadlines window, in days (the fetch and the empty message). */
export const DEADLINE_DAYS = 30;
export const NO_DEADLINES_MESSAGE = `No deadlines in the next ${DEADLINE_DAYS} days.`;
export const NO_APPLICATIONS_MESSAGE = "No Applications yet. Save a Listing to start tracking it.";
export const NO_MATCHES_MESSAGE = "No Applications match these filters.";
export const NOT_IN_POOL_LABEL = "No longer in the pool";

type ParamValue = string | string[] | null | undefined;
type Params = Record<string, ParamValue> | URLSearchParams;

function all(params: Params, key: string): string[] {
  if (params instanceof URLSearchParams) return params.getAll(key);
  const value = params[key];
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
}

function first(params: Params, key: string): string {
  return all(params, key)[0] ?? "";
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** A real ISO date from 2000-01-01 on (the same floor as the applied date), else "". */
export function filterDate(value: string): string {
  if (!ISO_DATE.test(value) || value < MIN_APPLIED_ON) return "";
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value ? value : "";
}

/** Length in code points (as the API counts it: an emoji is one character). */
export function codePointLength(text: string): number {
  return [...text].length;
}

/** An error for a company filter over 100 characters (after trimming), or null. */
export function companyError(company: string): string | null {
  return codePointLength(company.trim()) > COMPANY_MAX
    ? `The company filter must be ${COMPANY_MAX} characters or fewer.`
    : null;
}

/** Draft filters ready to apply: trimmed company, dates before 2000 or invalid dropped. */
export function normalizeFilters(filters: TrackerFilters): TrackerFilters {
  return {
    ...filters,
    company: filters.company.trim(),
    dateFrom: filterDate(filters.dateFrom),
    dateTo: filterDate(filters.dateTo),
  };
}

/** The first problem that keeps draft filters from being applied, or null. */
export function filtersError(filters: TrackerFilters): string | null {
  return companyError(filters.company) ?? dateRangeError(normalizeFilters(filters));
}

/** The filters from URL search params. Unknown Statuses, invalid dates and an unknown `by`
 * are dropped; the company is trimmed and cut to 100 characters. */
export function parseTrackerFilters(params: Params): TrackerFilters {
  const statuses = STATUSES.filter((s) => all(params, "status").includes(s));
  const company = [...first(params, "company").trim()].slice(0, COMPANY_MAX).join("").trim();
  return {
    statuses,
    company,
    dateFrom: filterDate(first(params, "from")),
    dateTo: filterDate(first(params, "to")),
    dateBy: first(params, "by") === "applied" ? "applied" : "saved",
  };
}

/** Whether any filter narrows the list (the "saved / applied" selector alone does not). */
export function hasFilters(filters: TrackerFilters): boolean {
  return (
    filters.statuses.length > 0 ||
    filters.company.trim() !== "" ||
    filters.dateFrom !== "" ||
    filters.dateTo !== ""
  );
}

/** The URL search string of the filters (no leading "?"; empty for none). Statuses keep the
 * fixed order so the same filters always give the same URL. */
export function serializeTrackerFilters(filters: TrackerFilters): string {
  const params = new URLSearchParams();
  for (const status of STATUSES) {
    if (filters.statuses.includes(status)) params.append("status", status);
  }
  const company = filters.company.trim();
  if (company) params.set("company", company);
  if (filters.dateFrom) params.set("from", filters.dateFrom);
  if (filters.dateTo) params.set("to", filters.dateTo);
  if (filters.dateBy === "applied") params.set("by", "applied");
  return params.toString();
}

/** `/tracker` with the filters in the URL. */
export function trackerHref(filters: TrackerFilters): string {
  const search = serializeTrackerFilters(filters);
  return search ? `${TRACKER_PATH}?${search}` : TRACKER_PATH;
}

export type TrackerQuery = {
  status?: ApplicationStatus[];
  company?: string;
  date_from?: string;
  date_to?: string;
  date_by?: DateBy;
  limit?: number;
  cursor?: string;
};

/** The `listApplications` query for the filters (and an optional cursor). */
export function trackerQuery(filters: TrackerFilters, cursor?: string, limit = 20): TrackerQuery {
  const query: TrackerQuery = { limit };
  if (filters.statuses.length > 0) query.status = [...filters.statuses];
  const company = filters.company.trim();
  if (company) query.company = company;
  if (filters.dateFrom) query.date_from = filters.dateFrom;
  if (filters.dateTo) query.date_to = filters.dateTo;
  if (filters.dateFrom || filters.dateTo) query.date_by = filters.dateBy;
  if (cursor) query.cursor = cursor;
  return query;
}

/** An error for a date range that ends before it starts, or null. */
export function dateRangeError(filters: Pick<TrackerFilters, "dateFrom" | "dateTo">): string | null {
  if (filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo) {
    return "The start date is after the end date.";
  }
  return null;
}

/** "today", "in 1 day", "in 12 days". */
export function daysLeftText(days: number): string {
  if (days <= 0) return "today";
  return days === 1 ? "in 1 day" : `in ${days} days`;
}

/** "1 Application", "12 Applications". */
export function applicationCount(total: number): string {
  return total === 1 ? "1 Application" : `${total} Applications`;
}

/** The empty-state text: nothing saved at all, or nothing matching the filters. */
export function trackerEmptyMessage(filters: TrackerFilters): string {
  return hasFilters(filters) ? NO_MATCHES_MESSAGE : NO_APPLICATIONS_MESSAGE;
}

/** `{listing_id: application_id}` from a `getApplicationsForListings` response. */
export function savedByListing(
  items: readonly { listing_id: string; application_id: string }[],
): Record<string, string> {
  const byId: Record<string, string> = {};
  for (const item of items) byId[item.listing_id] = item.application_id;
  return byId;
}

export const SAVED_LOOKUP_MAX = 50;

/** Distinct Listing ids in batches of at most 50 (the API's limit per lookup). */
export function savedLookupBatches(ids: readonly string[]): string[][] {
  const distinct = [...new Set(ids)];
  const batches: string[][] = [];
  for (let i = 0; i < distinct.length; i += SAVED_LOOKUP_MAX) {
    batches.push(distinct.slice(i, i + SAVED_LOOKUP_MAX));
  }
  return batches;
}

/** Next pages appended without repeating an Application already shown. */
export function mergeTrackerPages<T extends { id: string }>(shown: T[], next: T[]): T[] {
  const seen = new Set(shown.map((item) => item.id));
  return [...shown, ...next.filter((item) => !seen.has(item.id))];
}
