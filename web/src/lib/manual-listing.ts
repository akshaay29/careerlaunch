// The "Add external job" form (Story 4.3), pure so it can be unit tested with node --test. The
// rules mirror the API (`listings.service.create_manual_listing`); the API stays the judge.
// Relative imports: node --test erases type-only imports and cannot resolve the "@/" alias.
import type { ExternalJobIn } from "../api/types.gen.ts";
import { TRACKER_PATH } from "./auth-paths.ts";
import { ADDED_BY_YOU } from "./listings.ts";

export { ADDED_BY_YOU };

/** The page with the form. */
export const ADD_EXTERNAL_JOB_PATH = `${TRACKER_PATH}/new`;

export const URL_MAX = 2048;
export const TITLE_MAX = 300;
export const COMPANY_MAX = 200;
export const LOCATION_MAX = 200;
export const DESCRIPTION_MAX = 4000;
/** The earliest deadline accepted (a guard against typos such as year 0026). */
export const MIN_DEADLINE = "2000-01-01";

export const URL_NOTE = "We don't visit this link; it's saved so you can open it later.";
export const TITLE_REQUIRED_MESSAGE = "Enter a title or a link to the job.";
export const INVALID_LINK_MESSAGE = "Enter a full link starting with http:// or https://.";
export const DERIVED_TITLE_PREFIX = "Job at ";

export type ManualJobField =
  | "url"
  | "title"
  | "company"
  | "location"
  | "job_type"
  | "deadline"
  | "description";

export type ManualJobForm = {
  url: string;
  title: string;
  company: string;
  location: string;
  /** "" is "not specified". */
  jobType: "" | "internship" | "full_time";
  deadline: string;
  description: string;
};

export const EMPTY_MANUAL_JOB: ManualJobForm = {
  url: "",
  title: "",
  company: "",
  location: "",
  jobType: "",
  deadline: "",
  description: "",
};

/** Field errors; `form` holds an error that belongs to no field. */
export type ManualJobErrors = Partial<Record<ManualJobField | "form", string>>;

/** Length in code points (as the API counts it: an emoji is one character). */
export function codePoints(text: string): number {
  return [...text].length;
}

// The same rule as the API's `_http_url` and the web's `safeExternalUrl`.
const URL_UNSAFE = /[\u0000-\u001f\u007f\s\\]/;

const HTTP_PREFIX = /^https?:\/\//i;

/**
 * The host of an http(s) link as written, lower-cased, the same way as the API's `raw_host`:
 * the authority after `scheme://` up to `/`, `?` or `#`, without userinfo or `:port` (an IPv6
 * host keeps its brackets). No `new URL()` normalisation (an IDN host stays as typed, a large
 * port is not refused): the API is the judge of the rest. Null when the link cannot be one
 * the API accepts (no http(s) scheme, no host, whitespace, backslash or control characters).
 */
export function linkHost(raw: string): string | null {
  const url = raw.trim();
  if (!url || codePoints(url) > URL_MAX || URL_UNSAFE.test(url) || !HTTP_PREFIX.test(url)) {
    return null;
  }
  const rest = url.replace(HTTP_PREFIX, "");
  const authority = rest.split(/[/?#]/, 1)[0];
  const hostPort = authority.slice(authority.lastIndexOf("@") + 1);
  let host: string;
  if (hostPort.startsWith("[")) {
    const end = hostPort.indexOf("]");
    if (end < 0) return null;
    host = hostPort.slice(0, end + 1);
  } else {
    host = hostPort.split(":", 1)[0];
  }
  return host ? host.toLowerCase() : null;
}

/** "Job at jobs.example.com": the title of a job added by link only (null for a bad link). */
export function derivedTitle(url: string): string | null {
  const host = linkHost(url);
  return host ? `${DERIVED_TITLE_PREFIX}${host}` : null;
}

/**
 * The title the job will get: the typed title, else the one derived from the link, else null
 * (then the title is required).
 */
export function titlePreview(form: Pick<ManualJobForm, "title" | "url">): string | null {
  return form.title.trim() || derivedTitle(form.url);
}

/** Announce the counter only this close to the limit (or over it). */
export const COUNTER_ANNOUNCE_WITHIN = 200;

/**
 * What a screen reader should hear about the description length: the counter text when within
 * 200 of the limit or over it, else null (no announcement on every keystroke).
 */
export function counterAnnouncement(description: string, locale = "en-GB"): string | null {
  const used = codePoints(description.trim());
  if (used < DESCRIPTION_MAX - COUNTER_ANNOUNCE_WITHIN) return null;
  const counter = descriptionCounter(description, locale);
  return used > DESCRIPTION_MAX ? `${counter}: over the limit` : counter;
}

/** Where the draft waits while the Student logs in again (the browser session only). */
export const DRAFT_STORAGE_KEY = "careerlaunch.add-external-job.draft";

/** The form as stored, or null when the stored value is not a draft. */
export function parseStoredDraft(raw: string | null): ManualJobForm | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (!value || typeof value !== "object") return null;
    const text = (key: keyof ManualJobForm) =>
      typeof value[key] === "string" ? (value[key] as string) : "";
    const jobType = value.jobType === "internship" || value.jobType === "full_time" ? value.jobType : "";
    return {
      url: text("url"),
      title: text("title"),
      company: text("company"),
      location: text("location"),
      jobType,
      deadline: text("deadline"),
      description: text("description"),
    };
  } catch {
    return null;
  }
}

/** The Application page of a job, flagged when the link had already been added (200). */
export function addedJobPath(applicationId: string, existing: boolean): string {
  const path = `${TRACKER_PATH}/${encodeURIComponent(applicationId)}`;
  return existing ? `${path}?existing=1` : path;
}

export const ALREADY_ADDED_NOTICE = "You already added this link.";

/** "123 / 4,000" for the description counter (code points, after trimming). */
export function descriptionCounter(description: string, locale = "en-GB"): string {
  const used = codePoints(description.trim());
  return `${used.toLocaleString(locale)} / ${DESCRIPTION_MAX.toLocaleString(locale)}`;
}

function tooLong(label: string, max: number): string {
  return `${label} must be ${max.toLocaleString("en-GB")} characters or fewer.`;
}

/** A real ISO date ("2026-12-31"), else false. */
function isIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

/** The errors the API would report for this form (empty when it may be sent). */
export function validateManualJob(form: ManualJobForm): ManualJobErrors {
  const errors: ManualJobErrors = {};
  const url = form.url.trim();
  const title = form.title.trim();
  if (url) {
    if (codePoints(url) > URL_MAX) errors.url = tooLong("The link", URL_MAX);
    else if (!linkHost(url)) errors.url = INVALID_LINK_MESSAGE;
  }
  if (codePoints(title) > TITLE_MAX) errors.title = tooLong("The title", TITLE_MAX);
  else if (!title && !url) errors.title = TITLE_REQUIRED_MESSAGE;
  if (codePoints(form.company.trim()) > COMPANY_MAX) {
    errors.company = tooLong("The company", COMPANY_MAX);
  }
  if (codePoints(form.location.trim()) > LOCATION_MAX) {
    errors.location = tooLong("The location", LOCATION_MAX);
  }
  if (form.deadline) {
    if (!isIsoDate(form.deadline)) errors.deadline = "Enter a valid date.";
    else if (form.deadline < MIN_DEADLINE) {
      errors.deadline = "The deadline cannot be before 1 Jan 2000.";
    }
  }
  if (codePoints(form.description.trim()) > DESCRIPTION_MAX) {
    errors.description = tooLong("The description", DESCRIPTION_MAX);
  }
  return errors;
}

export function hasErrors(errors: ManualJobErrors): boolean {
  return Object.values(errors).some(Boolean);
}

/** The request body: text trimmed, empty fields sent as null. */
export function manualJobBody(form: ManualJobForm): ExternalJobIn {
  const text = (value: string) => value.trim() || null;
  return {
    url: text(form.url),
    title: text(form.title),
    company: text(form.company),
    location: text(form.location),
    job_type: form.jobType || null,
    deadline: form.deadline || null,
    description: text(form.description),
  };
}

const FIELDS: readonly ManualJobField[] = [
  "url",
  "title",
  "company",
  "location",
  "job_type",
  "deadline",
  "description",
];

/**
 * The field errors of a 422 problem (`errors: [{loc: ["body", field], msg}]`); an error on no
 * known field (or a problem without `errors`) goes to `form`.
 */
export function problemFieldErrors(problem: unknown, fallback: string): ManualJobErrors {
  const list = (problem as { errors?: unknown } | null | undefined)?.errors;
  const errors: ManualJobErrors = {};
  if (Array.isArray(list)) {
    for (const item of list) {
      const loc = (item as { loc?: unknown })?.loc;
      const msg = (item as { msg?: unknown })?.msg;
      const text = typeof msg === "string" && msg ? msg : fallback;
      const field = Array.isArray(loc) ? loc[1] : undefined;
      const key = FIELDS.find((f) => f === field) ?? "form";
      if (!errors[key]) errors[key] = text;
    }
  }
  return hasErrors(errors) ? errors : { form: fallback };
}
