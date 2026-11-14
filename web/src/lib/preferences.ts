// Form <-> payload mapping and error-to-field mapping for Preferences (pure, so they can be
// unit tested with node --test). Relative type-only import: node --test cannot resolve "@/".
import type { PreferencesIn, PreferencesOut } from "../api/types.gen.ts";

export const SETTINGS_PREFERENCES_PATH = "/settings/preferences";

export const DEFAULT_TIMEZONE = "Asia/Kolkata";

/** The server's limits (it is the authority; these only save a 422). */
export const PREFERENCE_MAX_ITEMS = 10;
export const PREFERENCE_ITEM_MAX_CHARS = 80;
export const THRESHOLD_MIN = 0;
export const THRESHOLD_MAX = 100;
export const BATCH_YEAR_MIN = 2000;
export const BATCH_YEAR_MAX = 2100;
export const CGPA_MAX = 10;

export type JobType = PreferencesIn["job_types"][number];
export type AlertFrequency = PreferencesIn["alert_frequency"];
export type WorkAuthorisation = NonNullable<PreferencesIn["work_authorisation"]>;

/** Every field of the payload; also the keys field errors are reported under. */
export type PreferenceField = keyof PreferencesIn;

export const JOB_TYPE_OPTIONS: { value: JobType; label: string }[] = [
  { value: "internship", label: "Internship" },
  { value: "full_time", label: "Full-time" },
];

export const ALERT_FREQUENCY_OPTIONS: { value: AlertFrequency; label: string }[] = [
  { value: "daily", label: "Daily digest" },
  { value: "weekly", label: "Weekly digest" },
  { value: "off", label: "Off" },
];

export const WORK_AUTHORISATION_OPTIONS: { value: WorkAuthorisation; label: string }[] = [
  { value: "india_authorised", label: "Authorised to work in India" },
  { value: "needs_sponsorship", label: "Needs visa sponsorship" },
];

/**
 * What the form edits. Text inputs hold strings (an empty string means "not stated");
 * role titles and locations are one per line.
 */
export type PreferencesForm = {
  roleTitles: string;
  locations: string;
  jobTypes: JobType[];
  batchYear: string;
  cgpa: string;
  degree: string;
  degreeOther: string;
  workAuthorisation: WorkAuthorisation | "";
  threshold: number;
  alertFrequency: AlertFrequency;
  timezone: string;
};

export type FieldErrors = Partial<Record<PreferenceField | "form", string[]>>;

/** The payload field each form field is sent as (and reported under in a 422). */
export const FORM_FIELD_TO_PAYLOAD: Record<keyof PreferencesForm, PreferenceField> = {
  roleTitles: "role_titles",
  locations: "locations",
  jobTypes: "job_types",
  batchYear: "batch_year",
  cgpa: "cgpa",
  degree: "degree",
  degreeOther: "degree_other",
  workAuthorisation: "work_authorisation",
  threshold: "threshold",
  alertFrequency: "alert_frequency",
  timezone: "timezone",
};

/** Length in Unicode code points, as the server counts it (not UTF-16 units). */
export function charCount(text: string): number {
  return [...text].length;
}

/** The form state for stored Preferences (or the defaults the API returns). */
export function toForm(prefs: PreferencesOut): PreferencesForm {
  return {
    roleTitles: prefs.role_titles.join("\n"),
    locations: prefs.locations.join("\n"),
    jobTypes: [...prefs.job_types],
    batchYear: prefs.batch_year === null ? "" : String(prefs.batch_year),
    cgpa: prefs.cgpa === null ? "" : String(prefs.cgpa),
    degree: prefs.degree ?? "",
    degreeOther: prefs.degree_other ?? "",
    workAuthorisation: prefs.work_authorisation ?? "",
    threshold: prefs.threshold,
    alertFrequency: prefs.alert_frequency,
    timezone: prefs.timezone,
  };
}

/** One item per non-blank line, trimmed and inner whitespace collapsed. */
export function splitLines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.split(/\s+/).filter(Boolean).join(" "))
    .filter(Boolean);
}

/** Duplicates ignoring case dropped, keeping the first (as the server does). */
function dedupe(items: string[]): string[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = item.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function checkItems(items: string[], noun: string): string[] {
  const errors: string[] = [];
  if (items.length > PREFERENCE_MAX_ITEMS) {
    errors.push(`Enter at most ${PREFERENCE_MAX_ITEMS} ${noun}.`);
  }
  if (items.some((item) => charCount(item) > PREFERENCE_ITEM_MAX_CHARS)) {
    errors.push(`Each entry can be at most ${PREFERENCE_ITEM_MAX_CHARS} characters.`);
  }
  return errors;
}

/** An optional number field: "" is null; otherwise a finite number or an error. */
function optionalNumber(text: string): { value: number | null } | { error: string } {
  const trimmed = text.trim();
  if (!trimmed) return { value: null };
  const value = Number(trimmed);
  return Number.isFinite(value) ? { value } : { error: "Enter a number." };
}

export type PayloadResult =
  | { ok: true; payload: PreferencesIn }
  | { ok: false; errors: FieldErrors };

/**
 * The PUT body for the form, or field errors for input that cannot be sent as it is
 * (not a number, out of range, too many entries). The server re-checks everything.
 */
export function toPayload(form: PreferencesForm): PayloadResult {
  const errors: FieldErrors = {};
  const add = (field: PreferenceField, ...messages: string[]) => {
    if (messages.length > 0) errors[field] = [...(errors[field] ?? []), ...messages];
  };

  const roleTitles = dedupe(splitLines(form.roleTitles));
  const locations = dedupe(splitLines(form.locations));
  add("role_titles", ...checkItems(roleTitles, "role titles"));
  add("locations", ...checkItems(locations, "locations"));

  let batchYear: number | null = null;
  const year = optionalNumber(form.batchYear);
  if ("error" in year) add("batch_year", year.error);
  else if (
    year.value !== null &&
    (!Number.isInteger(year.value) || year.value < BATCH_YEAR_MIN || year.value > BATCH_YEAR_MAX)
  ) {
    add("batch_year", `Enter a year between ${BATCH_YEAR_MIN} and ${BATCH_YEAR_MAX}.`);
  } else batchYear = year.value;

  let cgpa: number | null = null;
  const parsedCgpa = optionalNumber(form.cgpa);
  if ("error" in parsedCgpa) add("cgpa", parsedCgpa.error);
  else if (parsedCgpa.value !== null && (parsedCgpa.value < 0 || parsedCgpa.value > CGPA_MAX)) {
    add("cgpa", `Enter a CGPA between 0 and ${CGPA_MAX}.`);
  } else cgpa = parsedCgpa.value;

  if (
    !Number.isInteger(form.threshold) ||
    form.threshold < THRESHOLD_MIN ||
    form.threshold > THRESHOLD_MAX
  ) {
    add("threshold", `Choose a whole number from ${THRESHOLD_MIN} to ${THRESHOLD_MAX}.`);
  }

  const degree = form.degree || null;
  const degreeOther = degree === "other" ? splitLines(form.degreeOther).join(" ") || null : null;
  if (degreeOther !== null && charCount(degreeOther) > PREFERENCE_ITEM_MAX_CHARS) {
    add("degree_other", `Use at most ${PREFERENCE_ITEM_MAX_CHARS} characters.`);
  }

  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return {
    ok: true,
    payload: {
      role_titles: roleTitles,
      locations,
      job_types: JOB_TYPE_OPTIONS.map((o) => o.value).filter((t) => form.jobTypes.includes(t)),
      batch_year: batchYear,
      cgpa,
      degree,
      degree_other: degreeOther,
      work_authorisation: form.workAuthorisation || null,
      threshold: form.threshold,
      alert_frequency: form.alertFrequency,
      timezone: form.timezone.trim(),
    },
  };
}

const FIELDS: readonly PreferenceField[] = [
  "role_titles",
  "locations",
  "job_types",
  "batch_year",
  "cgpa",
  "degree",
  "degree_other",
  "work_authorisation",
  "threshold",
  "alert_frequency",
  "timezone",
];

function isField(value: unknown): value is PreferenceField {
  return typeof value === "string" && (FIELDS as readonly string[]).includes(value);
}

/** Pydantic prefixes custom messages with "Value error, "; the rest is the message. */
function cleanMessage(msg: string): string {
  const text = msg.replace(/^Value error,\s*/i, "").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "Invalid value.";
}

/**
 * Field errors from a 422 problem+json (`errors: [{loc, msg}]`, loc like
 * `["body", "role_titles", 3]`). Errors that name no known field go under `form`.
 */
export function fieldErrorsFromProblem(problem: unknown): FieldErrors {
  const result: FieldErrors = {};
  const push = (key: PreferenceField | "form", message: string) => {
    const list = result[key] ?? [];
    if (!list.includes(message)) list.push(message);
    result[key] = list;
  };
  const errors =
    problem && typeof problem === "object" && "errors" in problem
      ? (problem as { errors: unknown }).errors
      : undefined;
  if (!Array.isArray(errors)) return result;
  for (const err of errors) {
    if (!err || typeof err !== "object") continue;
    const { loc, msg } = err as { loc?: unknown; msg?: unknown };
    const message = cleanMessage(typeof msg === "string" ? msg : "");
    const path = Array.isArray(loc) ? loc.map(String) : [];
    const fieldAt = path.findIndex(isField);
    if (fieldAt === -1) {
      push("form", message);
      continue;
    }
    const field = path[fieldAt] as PreferenceField;
    const index = Number(path[fieldAt + 1]);
    push(field, Number.isInteger(index) ? `Entry ${index + 1}: ${message}` : message);
  }
  return result;
}

/**
 * The time zones to offer: the browser's IANA names, plus the default and the current value
 * (an engine may list an alias such as Asia/Calcutta instead), sorted, no duplicates.
 */
export function timeZoneOptions(supported: readonly string[], current: string): string[] {
  const all = new Set(supported);
  all.add(DEFAULT_TIMEZONE);
  if (current.trim()) all.add(current.trim());
  return [...all].sort((a, b) => a.localeCompare(b));
}

/** The browser's IANA time zone names (empty when the engine cannot list them). */
export function supportedTimeZones(): string[] {
  try {
    return typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  } catch {
    return [];
  }
}

/** A short description of the Match Score threshold for the slider. */
export function thresholdHint(threshold: number): string {
  return `Alerts and highlights for jobs scoring ${threshold} or more out of 100.`;
}
