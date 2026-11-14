// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { PreferencesOut } from "../api/types.gen.ts";
import {
  DEFAULT_TIMEZONE,
  FORM_FIELD_TO_PAYLOAD,
  type PreferencesForm,
  charCount,
  fieldErrorsFromProblem,
  splitLines,
  timeZoneOptions,
  toForm,
  toPayload,
} from "./preferences.ts";

function prefs(overrides: Partial<PreferencesOut> = {}): PreferencesOut {
  return {
    role_titles: [],
    locations: [],
    job_types: [],
    batch_year: null,
    cgpa: null,
    degree: null,
    degree_other: null,
    work_authorisation: null,
    threshold: 60,
    alert_frequency: "daily",
    timezone: "Asia/Kolkata",
    saved: false,
    updated_at: null,
    ...overrides,
  };
}

function form(overrides: Partial<PreferencesForm> = {}): PreferencesForm {
  return { ...toForm(prefs()), ...overrides };
}

test("the defaults map to a form and back to the same payload, with nothing filled in", () => {
  const result = toPayload(toForm(prefs()));
  assert.deepEqual(result, {
    ok: true,
    payload: {
      role_titles: [],
      locations: [],
      job_types: [],
      batch_year: null,
      cgpa: null,
      degree: null,
      degree_other: null,
      work_authorisation: null,
      threshold: 60,
      alert_frequency: "daily",
      timezone: "Asia/Kolkata",
    },
  });
});

test("stored Preferences round-trip through the form", () => {
  const stored = prefs({
    role_titles: ["Backend Intern", "Data Analyst"],
    locations: ["Bengaluru, Karnataka", "Remote"],
    job_types: ["internship", "full_time"],
    batch_year: 2027,
    cgpa: 8.5,
    degree: "other",
    degree_other: "B.Des",
    work_authorisation: "india_authorised",
    threshold: 75,
    alert_frequency: "weekly",
    timezone: "Europe/London",
    saved: true,
    updated_at: "2026-11-14T00:00:00Z",
  });
  const f = toForm(stored);
  assert.equal(f.roleTitles, "Backend Intern\nData Analyst");
  assert.equal(f.batchYear, "2027");
  assert.equal(f.cgpa, "8.5");
  const result = toPayload(f);
  assert.ok(result.ok);
  const expected: Record<string, unknown> = { ...stored };
  delete expected.saved;
  delete expected.updated_at;
  assert.deepEqual(result.payload, expected);
});

test("lines are trimmed, blanks dropped and duplicates dropped ignoring case", () => {
  assert.deepEqual(splitLines(" Backend   Intern \n\n  \r\nData Analyst"), [
    "Backend Intern",
    "Data Analyst",
  ]);
  const result = toPayload(form({ roleTitles: " Backend Intern \nbackend intern\n\n" }));
  assert.ok(result.ok);
  assert.deepEqual(result.payload.role_titles, ["Backend Intern"]);
});

test("job types are sent in a fixed order; degree_other only with degree other", () => {
  const result = toPayload(
    form({ jobTypes: ["full_time", "internship"], degree: "btech", degreeOther: "B.Des" }),
  );
  assert.ok(result.ok);
  assert.deepEqual(result.payload.job_types, ["internship", "full_time"]);
  assert.equal(result.payload.degree_other, null);
  const other = toPayload(form({ degree: "other", degreeOther: "   " }));
  assert.ok(other.ok);
  assert.equal(other.payload.degree_other, null);
});

test("input that cannot be sent gives field errors", () => {
  const result = toPayload(
    form({
      roleTitles: Array.from({ length: 11 }, (_, i) => `Role ${i}`).join("\n"),
      locations: "x".repeat(81),
      batchYear: "1999",
      cgpa: "ten",
      threshold: 101,
      degree: "other",
      degreeOther: "y".repeat(81),
    }),
  );
  assert.equal(result.ok, false);
  assert.ok(!result.ok);
  assert.deepEqual(Object.keys(result.errors).sort(), [
    "batch_year",
    "cgpa",
    "degree_other",
    "locations",
    "role_titles",
    "threshold",
  ]);
  assert.equal(toPayload(form({ cgpa: "10.5" })).ok, false);
  assert.equal(toPayload(form({ batchYear: "2027.5" })).ok, false);
  assert.equal(toPayload(form({ threshold: -1 })).ok, false);
  assert.equal(toPayload(form({ threshold: 0 })).ok, true);
  assert.equal(toPayload(form({ threshold: 100 })).ok, true);
});

test("422 errors map to their fields", () => {
  const errors = fieldErrorsFromProblem({
    type: "/problems/validation-error",
    status: 422,
    errors: [
      { loc: ["body", "threshold"], msg: "Input should be less than or equal to 100" },
      { loc: ["body", "timezone"], msg: "Value error, must be an IANA time zone name" },
      { loc: ["body", "role_titles", "2"], msg: "Value error, must be at most 80 characters" },
      { loc: ["body", "job_types", 0], msg: "Input should be 'internship' or 'full_time'" },
      { loc: ["body"], msg: "JSON decode error" },
    ],
  });
  assert.deepEqual(errors, {
    threshold: ["Input should be less than or equal to 100"],
    timezone: ["Must be an IANA time zone name"],
    role_titles: ["Entry 3: Must be at most 80 characters"],
    job_types: ["Entry 1: Input should be 'internship' or 'full_time'"],
    form: ["JSON decode error"],
  });
});

test("anything that is not a validation problem maps to no field errors", () => {
  assert.deepEqual(fieldErrorsFromProblem(undefined), {});
  assert.deepEqual(fieldErrorsFromProblem({ detail: "nope" }), {});
  assert.deepEqual(fieldErrorsFromProblem({ errors: "nope" }), {});
  assert.deepEqual(fieldErrorsFromProblem({ errors: [null, 3] }), {});
});

test("time zone options always include the default and the current value", () => {
  assert.deepEqual(timeZoneOptions(["Europe/London", "Asia/Calcutta"], "America/New_York"), [
    "America/New_York",
    "Asia/Calcutta",
    DEFAULT_TIMEZONE,
    "Europe/London",
  ]);
  assert.deepEqual(timeZoneOptions([], ""), [DEFAULT_TIMEZONE]);
  assert.deepEqual(timeZoneOptions([DEFAULT_TIMEZONE], DEFAULT_TIMEZONE), [DEFAULT_TIMEZONE]);
});

test("item length counts code points, as the server does", () => {
  const emoji = "\u{1F680}"; // 2 UTF-16 units, 1 code point
  assert.equal(emoji.length, 2);
  assert.equal(charCount(emoji), 1);
  assert.equal(toPayload(form({ locations: emoji.repeat(80) })).ok, true);
  assert.equal(toPayload(form({ locations: emoji.repeat(81) })).ok, false);
  assert.equal(toPayload(form({ degree: "other", degreeOther: emoji.repeat(80) })).ok, true);
  assert.equal(toPayload(form({ degree: "other", degreeOther: emoji.repeat(81) })).ok, false);
});

test("every form field maps to its payload field", () => {
  const payload = toPayload(form());
  assert.ok(payload.ok);
  assert.deepEqual(
    Object.values(FORM_FIELD_TO_PAYLOAD).sort(),
    Object.keys(payload.payload).sort(),
  );
});
