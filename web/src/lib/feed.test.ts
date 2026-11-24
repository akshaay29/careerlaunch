// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { FeedItem } from "../api/types.gen.ts";
import {
  BELOW_THRESHOLD_EMPTY_MESSAGE,
  BELOW_THRESHOLD_LABEL,
  FEED_PAGE_SIZE,
  FEED_PATH,
  NOTHING_TO_RANK_MESSAGE,
  PROVISIONAL_MESSAGE,
  SETUP_NEEDED_MESSAGE,
  SHOW_BELOW_LABEL,
  belowFromParam,
  feedChanged,
  feedEmptyMessage,
  feedHref,
  feedNotice,
  feedQuery,
  mergeFeedPages,
  scoreBadge,
} from "./feed.ts";

function item(id: string, score = 70, overrides: Partial<FeedItem> = {}): FeedItem {
  return {
    id,
    title: "Backend Intern",
    company: "Acme",
    location: "Remote",
    job_type: "internship",
    posted_at: null,
    deadline: null,
    url: "https://www.linkedin.com/jobs/view/1",
    source: "linkedin",
    source_label: "LinkedIn",
    skills: [],
    first_seen_at: "2026-11-14T00:00:00Z",
    score,
    below_threshold: false,
    ...overrides,
  };
}

test("copy is neutral", () => {
  assert.equal(FEED_PATH, "/feed");
  assert.equal(SHOW_BELOW_LABEL, "Show below my threshold");
  assert.equal(BELOW_THRESHOLD_LABEL, "Below your threshold");
  assert.equal(
    PROVISIONAL_MESSAGE,
    "Scores are being prepared. These are early matches from the newest Listings.",
  );
  assert.equal(
    SETUP_NEEDED_MESSAGE,
    "Add your skills and job preferences to see Listings ranked for you.",
  );
  assert.equal(
    BELOW_THRESHOLD_EMPTY_MESSAGE,
    "No Listings meet your threshold yet. You can show the ones below it.",
  );
  const all = [
    PROVISIONAL_MESSAGE,
    SETUP_NEEDED_MESSAGE,
    BELOW_THRESHOLD_EMPTY_MESSAGE,
    NOTHING_TO_RANK_MESSAGE,
  ].join(" ");
  for (const pressure of [/apply more/i, /hurry/i, /now!/i, /don't miss/i, /urgent/i]) {
    assert.doesNotMatch(all, pressure);
  }
});

test("the toggle lives in below=1", () => {
  assert.equal(belowFromParam("1"), true);
  for (const value of [undefined, null, "", "0", "true", "yes", " 1"]) {
    assert.equal(belowFromParam(value), false, JSON.stringify(value));
  }
  assert.equal(belowFromParam(["1", "0"]), true);
  assert.equal(belowFromParam(["0", "1"]), false);
  assert.equal(feedHref(true), "/feed?below=1");
  assert.equal(feedHref(false), "/feed");
});

test("feedQuery passes the toggle and the cursor", () => {
  assert.deepEqual(feedQuery(false), { limit: FEED_PAGE_SIZE, show_below_threshold: false });
  assert.deepEqual(feedQuery(true, null), { limit: 20, show_below_threshold: true });
  assert.deepEqual(feedQuery(true, "abc"), {
    limit: 20,
    show_below_threshold: true,
    cursor: "abc",
  });
});

test("score badge", () => {
  assert.equal(scoreBadge(72), "72 / 100");
  assert.equal(scoreBadge(0), "0 / 100");
  assert.equal(scoreBadge(100), "100 / 100");
});

test("mergeFeedPages keeps order and drops repeats", () => {
  const merged = mergeFeedPages([item("a", 90), item("b", 80)], [item("b", 80), item("c", 70)]);
  assert.deepEqual(
    merged.map((i) => i.id),
    ["a", "b", "c"],
  );
});

test("notices and empty states", () => {
  assert.equal(feedNotice("provisional"), PROVISIONAL_MESSAGE);
  assert.equal(feedNotice("ready"), null);
  assert.equal(feedNotice("setup_needed"), null);
  assert.equal(feedEmptyMessage("setup_needed", false), SETUP_NEEDED_MESSAGE);
  assert.equal(feedEmptyMessage("setup_needed", true), SETUP_NEEDED_MESSAGE);
  // The toggle is offered only when Listings really are hidden below the threshold.
  assert.equal(feedEmptyMessage("ready", true), BELOW_THRESHOLD_EMPTY_MESSAGE);
  assert.equal(feedEmptyMessage("ready", false), NOTHING_TO_RANK_MESSAGE);
  assert.equal(feedEmptyMessage("provisional", true), BELOW_THRESHOLD_EMPTY_MESSAGE);
  assert.equal(feedEmptyMessage("provisional", false), NOTHING_TO_RANK_MESSAGE);
});

test("a page from a changed feed is not appended", () => {
  const ready = { state: "ready" as const, threshold: 60 };
  assert.equal(feedChanged(ready, { state: "ready", threshold: 60 }), false);
  assert.equal(feedChanged(ready, { state: "ready", threshold: 70 }), true);
  assert.equal(feedChanged(ready, { state: "provisional", threshold: 60 }), true);
});
