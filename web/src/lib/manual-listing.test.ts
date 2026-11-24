// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ADD_EXTERNAL_JOB_PATH,
  DESCRIPTION_MAX,
  EMPTY_MANUAL_JOB,
  INVALID_LINK_MESSAGE,
  type ManualJobForm,
  TITLE_REQUIRED_MESSAGE,
  addedJobPath,
  codePoints,
  counterAnnouncement,
  derivedTitle,
  parseStoredDraft,
  descriptionCounter,
  hasErrors,
  linkHost,
  manualJobBody,
  problemFieldErrors,
  titlePreview,
  validateManualJob,
} from "./manual-listing.ts";

function form(overrides: Partial<ManualJobForm> = {}): ManualJobForm {
  return { ...EMPTY_MANUAL_JOB, ...overrides };
}

test("the page lives under the Tracker", () => {
  assert.equal(ADD_EXTERNAL_JOB_PATH, "/tracker/new");
});

test("derivedTitle names the host of an http(s) link, lower-cased", () => {
  assert.equal(derivedTitle("https://Jobs.Example.com/123"), "Job at jobs.example.com");
  assert.equal(derivedTitle("  http://example.org:8080/a?b=c  "), "Job at example.org");
  assert.equal(derivedTitle("https://[::1]:8080/x"), "Job at [::1]");
  for (const bad of ["", "ftp://x", "javascript:alert(1)", "jobs.example.com/1", "https://a b.com"]) {
    assert.equal(derivedTitle(bad), null, bad);
  }
  assert.equal(linkHost("https://"), null);
  assert.equal(linkHost("https://x.com\\@evil.com"), null);
});

test("linkHost takes the raw host like the API (no URL normalisation)", () => {
  // An IDN host stays as typed (new URL() would turn it into punycode).
  assert.equal(derivedTitle("https://Bücher.Example/jobs/1"), "Job at bücher.example");
  // A single slash is no authority, as for the API.
  assert.equal(linkHost("http:/x.com"), null);
  // A port new URL() refuses is the API's call, not the form's.
  assert.equal(linkHost("https://x.com:99999/job"), "x.com");
  assert.deepEqual(validateManualJob(form({ url: "https://x.com:99999/job" })), {});
  assert.equal(linkHost("HTTPS://user:pw@Jobs.Example.com:8443/a?b#c"), "jobs.example.com");
  assert.equal(linkHost("https://x.com?q=1"), "x.com");
  assert.equal(linkHost("https://[::1/x"), null);
  assert.equal(linkHost("https://user@/x"), null);
});

test("counterAnnouncement speaks only near or over the limit", () => {
  assert.equal(counterAnnouncement("x".repeat(3799)), null);
  assert.equal(counterAnnouncement("x".repeat(3800)), "3,800 / 4,000");
  assert.equal(counterAnnouncement("x".repeat(4001)), "4,001 / 4,000: over the limit");
});

test("a stored draft is restored field by field", () => {
  const draft = form({ url: "https://x.com/1", title: "T", jobType: "full_time", description: "d" });
  assert.deepEqual(parseStoredDraft(JSON.stringify(draft)), draft);
  assert.deepEqual(parseStoredDraft(JSON.stringify({ title: 5, jobType: "contract" })), EMPTY_MANUAL_JOB);
  assert.equal(parseStoredDraft("not json"), null);
  assert.equal(parseStoredDraft(null), null);
  assert.equal(parseStoredDraft("null"), null);
});

test("addedJobPath flags an existing link", () => {
  assert.equal(addedJobPath("a b", false), "/tracker/a%20b");
  assert.equal(addedJobPath("abc", true), "/tracker/abc?existing=1");
});

test("titlePreview prefers the typed title, then the link", () => {
  assert.equal(titlePreview({ title: "  Data Intern ", url: "https://x.com" }), "Data Intern");
  assert.equal(titlePreview({ title: "  ", url: "https://x.com/1" }), "Job at x.com");
  assert.equal(titlePreview({ title: "", url: "nope" }), null);
});

test("a title or a link is required", () => {
  assert.deepEqual(validateManualJob(form()), { title: TITLE_REQUIRED_MESSAGE });
  assert.deepEqual(validateManualJob(form({ title: "   " })), { title: TITLE_REQUIRED_MESSAGE });
  assert.deepEqual(validateManualJob(form({ title: "Data Intern" })), {});
  assert.deepEqual(validateManualJob(form({ url: "https://jobs.example.com/123" })), {});
});

test("a bad link is an error on url", () => {
  for (const url of ["ftp://x", "javascript:alert(1)", "https://a b.com"]) {
    assert.deepEqual(validateManualJob(form({ url, title: "T" })), { url: INVALID_LINK_MESSAGE });
  }
  const long = `https://x.com/${"a".repeat(2048)}`;
  assert.match(validateManualJob(form({ url: long, title: "T" })).url ?? "", /2,048/);
});

test("length limits count code points after trimming", () => {
  assert.equal(codePoints("😀😀"), 2);
  const fits = form({ title: "T", description: ` ${"😀".repeat(DESCRIPTION_MAX)} ` });
  assert.deepEqual(validateManualJob(fits), {});
  const over = validateManualJob(form({ title: "T", description: "😀".repeat(DESCRIPTION_MAX + 1) }));
  assert.match(over.description ?? "", /4,000/);
  assert.ok(validateManualJob(form({ title: "x".repeat(301) })).title);
  assert.ok(validateManualJob(form({ title: "T", company: "x".repeat(201) })).company);
  assert.ok(validateManualJob(form({ title: "T", location: "x".repeat(201) })).location);
});

test("the deadline is a real date from 2000-01-01", () => {
  assert.ok(validateManualJob(form({ title: "T", deadline: "1999-12-31" })).deadline);
  assert.ok(validateManualJob(form({ title: "T", deadline: "2026-02-30" })).deadline);
  assert.deepEqual(validateManualJob(form({ title: "T", deadline: "2000-01-01" })), {});
});

test("descriptionCounter shows code points out of 4,000", () => {
  assert.equal(descriptionCounter(""), "0 / 4,000");
  assert.equal(descriptionCounter("  😀ab  "), "3 / 4,000");
  assert.equal(descriptionCounter("x".repeat(1234)), "1,234 / 4,000");
});

test("manualJobBody trims and sends empty fields as null", () => {
  assert.deepEqual(
    manualJobBody(
      form({ url: " https://x.com/1 ", title: "  ", jobType: "internship", deadline: "2030-01-31" }),
    ),
    {
      url: "https://x.com/1",
      title: null,
      company: null,
      location: null,
      job_type: "internship",
      deadline: "2030-01-31",
      description: null,
    },
  );
  assert.equal(manualJobBody(form({ title: "T" })).job_type, null);
});

test("problemFieldErrors maps 422 errors to fields", () => {
  const problem = {
    errors: [
      { loc: ["body", "url"], msg: "This job can't be added." },
      { loc: ["body", "url"], msg: "second" },
      { loc: ["body", "deadline"], msg: "too old" },
      { loc: ["body"], msg: "" },
    ],
  };
  assert.deepEqual(problemFieldErrors(problem, "Could not add"), {
    url: "This job can't be added.",
    deadline: "too old",
    form: "Could not add",
  });
  assert.deepEqual(problemFieldErrors(undefined, "Could not add"), { form: "Could not add" });
  assert.deepEqual(problemFieldErrors({ errors: "x" }, "Could not add"), { form: "Could not add" });
  assert.equal(hasErrors({}), false);
  assert.equal(hasErrors({ title: "x" }), true);
});
