// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { StatusChange } from "../api/types.gen.ts";
import {
  NOTES_MAX,
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
