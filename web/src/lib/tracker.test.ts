// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { StatusChange } from "../api/types.gen.ts";
import {
  type ApplicationStatus,
  DEADLINE_DAYS,
  EMPTY_FILTERS,
  NO_DEADLINES_MESSAGE,
  codePointLength,
  companyError,
  filterDate,
  filtersError,
  normalizeFilters,
  NO_APPLICATIONS_MESSAGE,
  NO_MATCHES_MESSAGE,
  NOTES_MAX,
  applicationCount,
  dateRangeError,
  daysLeftText,
  hasFilters,
  mergeTrackerPages,
  parseTrackerFilters,
  savedByListing,
  savedLookupBatches,
  serializeTrackerFilters,
  trackerEmptyMessage,
  trackerHref,
  trackerQuery,
  formatChangedAt,
  historyLines,
  invalidTransitionMessage,
  isClosed,
  needsConfirmation,
  notesLength,
  notesError,
  statusActions,
  statusLabel,
  trackerPath,
  transitionLabel,
} from "./tracker.ts";

test("trackerPath encodes the id", () => {
  assert.equal(trackerPath("abc"), "/tracker/abc");
  assert.equal(trackerPath("a/b"), "/tracker/a%2Fb");
});

test("status labels", () => {
  assert.equal(statusLabel("saved"), "Saved");
  assert.equal(statusLabel("applied"), "Applied");
  assert.equal(statusLabel("interview"), "Interview");
  assert.equal(statusLabel("offer"), "Offer");
  assert.equal(statusLabel("rejected"), "Rejected");
  assert.equal(statusLabel("withdrawn"), "Withdrawn");
});

test("closed statuses", () => {
  assert.equal(isClosed("rejected"), true);
  assert.equal(isClosed("withdrawn"), true);
  assert.equal(isClosed("applied"), false);
  assert.equal(isClosed("offer"), false);
});

test("plain action labels", () => {
  assert.equal(transitionLabel("saved", "applied"), "Mark as applied");
  assert.equal(transitionLabel("saved", "withdrawn"), "Withdraw");
  assert.equal(transitionLabel("applied", "interview"), "Interview");
  assert.equal(transitionLabel("applied", "rejected"), "Rejected");
  assert.equal(transitionLabel("interview", "offer"), "Offer");
  assert.equal(transitionLabel("rejected", "applied"), "Reopen");
  assert.equal(transitionLabel("withdrawn", "saved"), "Reopen");
});

test("statusActions follows allowed_next only, in order", () => {
  assert.deepEqual(statusActions({ status: "saved", allowed_next: ["applied", "withdrawn"] }), [
    { status: "applied", label: "Mark as applied" },
    { status: "withdrawn", label: "Withdraw" },
  ]);
  assert.deepEqual(statusActions({ status: "interview", allowed_next: ["offer", "rejected", "withdrawn"] }), [
    { status: "offer", label: "Offer" },
    { status: "rejected", label: "Rejected" },
    { status: "withdrawn", label: "Withdraw" },
  ]);
  assert.deepEqual(statusActions({ status: "rejected", allowed_next: ["interview"] }), [
    { status: "interview", label: "Reopen" },
  ]);
  assert.deepEqual(statusActions({ status: "offer", allowed_next: [] }), []);
});

test("history lines keep the API order (newest first) with formatted timestamps", () => {
  const items: StatusChange[] = [
    { from_status: "applied", to_status: "rejected", changed_at: "2026-11-24T09:30:00Z" },
    { from_status: "saved", to_status: "applied", changed_at: "2026-11-20T04:00:00Z" },
    { from_status: null, to_status: "saved", changed_at: "2026-11-19T18:45:00Z" },
  ];
  const lines = historyLines(items, "en-GB", "UTC");
  assert.deepEqual(
    lines.map((l) => [l.status, l.when]),
    [
      ["Rejected", "24 Nov 2026, 09:30"],
      ["Applied", "20 Nov 2026, 04:00"],
      ["Saved", "19 Nov 2026, 18:45"],
    ],
  );
  assert.equal(new Set(lines.map((l) => l.key)).size, 3);
});

test("formatChangedAt uses the given time zone and passes malformed values through", () => {
  assert.equal(formatChangedAt("2026-11-24T20:00:00Z", "en-GB", "Asia/Kolkata"), "25 Nov 2026, 01:30");
  assert.equal(formatChangedAt("nope"), "nope");
});

test("notes limit counts trimmed characters", () => {
  assert.equal(notesError("a".repeat(NOTES_MAX)), null);
  assert.equal(notesError(`  ${"a".repeat(NOTES_MAX)}  `), null);
  assert.match(notesError("a".repeat(NOTES_MAX + 1)) ?? "", /4000 characters or fewer/);
});

test("notes length counts code points, not UTF-16 units", () => {
  assert.equal(notesLength("  héllo  "), 5);
  assert.equal(notesLength("\u{1F600}".repeat(3)), 3);
  assert.equal(notesError("\u{1F600}".repeat(NOTES_MAX)), null);
  assert.notEqual(notesError("\u{1F600}".repeat(NOTES_MAX + 1)), null);
});

test("only Offer asks for confirmation", () => {
  assert.equal(needsConfirmation("offer"), true);
  for (const status of ["saved", "applied", "interview", "rejected", "withdrawn"] as const) {
    assert.equal(needsConfirmation(status), false, status);
  }
});

test("invalid transition message names what is allowed", () => {
  assert.equal(
    invalidTransitionMessage(["applied", "withdrawn"]),
    "That change is not allowed. Next: Applied, Withdrawn.",
  );
  assert.equal(invalidTransitionMessage([]), "This Status cannot be changed any more.");
});

// --------------------------------------------------------------------------- the Tracker (4.2)

test("parseTrackerFilters reads the URL params and drops invalid values", () => {
  assert.deepEqual(parseTrackerFilters({}), EMPTY_FILTERS);
  assert.deepEqual(
    parseTrackerFilters({
      status: ["interview", "bogus", "applied"],
      company: "  Acme  ",
      from: "2026-03-01",
      to: "2026-02-30",
      by: "applied",
    }),
    {
      statuses: ["applied", "interview"],
      company: "Acme",
      dateFrom: "2026-03-01",
      dateTo: "",
      dateBy: "applied",
    },
  );
  const fromSearch = parseTrackerFilters(new URLSearchParams("status=saved&by=other&from=x"));
  assert.deepEqual(fromSearch, { ...EMPTY_FILTERS, statuses: ["saved"] });
  assert.equal(parseTrackerFilters({ company: "x".repeat(150) }).company.length, 100);
});

test("serializeTrackerFilters round-trips and trackerHref builds the URL", () => {
  const filters = {
    statuses: ["interview", "applied"] as ApplicationStatus[],
    company: " acme ",
    dateFrom: "2026-03-01",
    dateTo: "2026-03-31",
    dateBy: "applied" as const,
  };
  const search = serializeTrackerFilters(filters);
  assert.equal(
    search,
    "status=applied&status=interview&company=acme&from=2026-03-01&to=2026-03-31&by=applied",
  );
  assert.deepEqual(parseTrackerFilters(new URLSearchParams(search)), {
    ...filters,
    statuses: ["applied", "interview"],
    company: "acme",
  });
  assert.equal(trackerHref(EMPTY_FILTERS), "/tracker");
  assert.equal(trackerHref({ ...EMPTY_FILTERS, company: "a b" }), "/tracker?company=a+b");
});

test("trackerQuery sends only the filters in use", () => {
  assert.deepEqual(trackerQuery(EMPTY_FILTERS), { limit: 20 });
  assert.deepEqual(trackerQuery({ ...EMPTY_FILTERS, dateBy: "applied" }), { limit: 20 });
  assert.deepEqual(
    trackerQuery(
      { statuses: ["saved"], company: " x ", dateFrom: "2026-01-01", dateTo: "", dateBy: "saved" },
      "c1",
    ),
    { limit: 20, status: ["saved"], company: "x", date_from: "2026-01-01", date_by: "saved", cursor: "c1" },
  );
});

test("hasFilters, the empty-state choice and the range check", () => {
  assert.equal(hasFilters(EMPTY_FILTERS), false);
  assert.equal(hasFilters({ ...EMPTY_FILTERS, dateBy: "applied" }), false);
  assert.equal(hasFilters({ ...EMPTY_FILTERS, company: "a" }), true);
  assert.equal(hasFilters({ ...EMPTY_FILTERS, statuses: ["offer"] }), true);
  assert.equal(trackerEmptyMessage(EMPTY_FILTERS), NO_APPLICATIONS_MESSAGE);
  assert.equal(trackerEmptyMessage({ ...EMPTY_FILTERS, dateTo: "2026-01-01" }), NO_MATCHES_MESSAGE);
  assert.equal(
    NO_APPLICATIONS_MESSAGE,
    "No Applications yet. Save a Listing to start tracking it.",
  );
  assert.equal(NO_MATCHES_MESSAGE, "No Applications match these filters.");
  assert.equal(dateRangeError({ dateFrom: "2026-02-02", dateTo: "2026-02-01" }) !== null, true);
  assert.equal(dateRangeError({ dateFrom: "2026-02-01", dateTo: "2026-02-01" }), null);
  assert.equal(dateRangeError({ dateFrom: "", dateTo: "2026-02-01" }), null);
});

test("days-left text and the count", () => {
  assert.equal(daysLeftText(0), "today");
  assert.equal(daysLeftText(1), "in 1 day");
  assert.equal(daysLeftText(12), "in 12 days");
  assert.equal(applicationCount(0), "0 Applications");
  assert.equal(applicationCount(1), "1 Application");
  assert.equal(applicationCount(25), "25 Applications");
});

test("saved-state helpers batch by 50 and map listing to application", () => {
  const ids = Array.from({ length: 120 }, (_, i) => `l${i}`);
  const batches = savedLookupBatches([...ids, "l0"]);
  assert.deepEqual(
    batches.map((b) => b.length),
    [50, 50, 20],
  );
  assert.deepEqual(savedLookupBatches([]), []);
  assert.deepEqual(
    savedByListing([
      { listing_id: "l1", application_id: "a1" },
      { listing_id: "l2", application_id: "a2" },
    ]),
    { l1: "a1", l2: "a2" },
  );
});

test("mergeTrackerPages never repeats a row", () => {
  assert.deepEqual(
    mergeTrackerPages([{ id: "a" }, { id: "b" }], [{ id: "b" }, { id: "c" }]),
    [{ id: "a" }, { id: "b" }, { id: "c" }],
  );
});

test("dates before 2000 are ignored and the deadline window is shared", () => {
  assert.equal(filterDate("1999-12-31"), "");
  assert.equal(filterDate("2000-01-01"), "2000-01-01");
  assert.equal(filterDate("2026-02-30"), "");
  assert.equal(parseTrackerFilters({ from: "0026-03-01", to: "2026-03-01" }).dateFrom, "");
  assert.equal(DEADLINE_DAYS, 30);
  assert.equal(NO_DEADLINES_MESSAGE, "No deadlines in the next 30 days.");
});

test("company length is counted in code points", () => {
  const emoji = "😀".repeat(100);
  assert.equal(codePointLength(emoji), 100);
  assert.equal(companyError(emoji), null);
  assert.notEqual(companyError(`${emoji}x`), null);
  assert.equal(companyError(`  ${"a".repeat(100)}  `), null);
  assert.equal(parseTrackerFilters({ company: "😀".repeat(150) }).company, emoji);
});

test("draft filters: validated on apply, normalised before the URL", () => {
  const draft = {
    ...EMPTY_FILTERS,
    company: "  acme ",
    dateFrom: "2026-03-31",
    dateTo: "2026-03-01",
  };
  assert.equal(filtersError(draft), "The start date is after the end date.");
  assert.equal(filtersError({ ...draft, dateTo: "2026-04-01" }), null);
  assert.notEqual(filtersError({ ...EMPTY_FILTERS, company: "x".repeat(101) }), null);
  // A date before 2000 is ignored, so it cannot make the range invalid.
  assert.equal(filtersError({ ...draft, dateTo: "1999-01-01" }), null);
  assert.deepEqual(normalizeFilters({ ...draft, dateTo: "1999-01-01" }), {
    ...EMPTY_FILTERS,
    company: "acme",
    dateFrom: "2026-03-31",
    dateTo: "",
  });
});
