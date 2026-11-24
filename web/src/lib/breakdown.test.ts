// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { MatchScoreItem } from "../api/types.gen.ts";
import {
  EMPTY_LIST_TEXT,
  MATCHED_HEADING,
  MATCH_BATCH_MAX,
  MISSING_HEADING,
  NO_SKILLS_NOTE,
  ON_REQUEST_NOTE,
  WHY_BUTTON_LABEL,
  componentLabel,
  componentLine,
  matchScoreIds,
  mergeScores,
  noSkillsNote,
  sourceNote,
} from "./breakdown.ts";

test("copy is neutral and plain", () => {
  assert.equal(WHY_BUTTON_LABEL, "Why this score");
  assert.equal(MATCHED_HEADING, "Skills you have");
  assert.equal(MISSING_HEADING, "Skills this Listing asks for that you don't list");
  assert.equal(EMPTY_LIST_TEXT, "None");
  assert.equal(ON_REQUEST_NOTE, "Scored just now");
  assert.equal(
    NO_SKILLS_NOTE,
    "This Listing lists no skills, so the title and keywords count for the whole score.",
  );
  const all = [MATCHED_HEADING, MISSING_HEADING, ON_REQUEST_NOTE, NO_SKILLS_NOTE].join(" ");
  for (const pressure of [/apply more/i, /hurry/i, /now!/i, /don't miss/i, /urgent/i, /should/i]) {
    assert.doesNotMatch(all, pressure);
  }
});

test("component labels and point lines", () => {
  assert.equal(componentLabel("skills"), "Skills");
  assert.equal(componentLabel("text"), "Title and keywords");
  assert.equal(
    componentLine({ name: "skills", points: 35, max_points: 70 }),
    "Skills: 35 of 70 points",
  );
  assert.equal(
    componentLine({ name: "text", points: 20, max_points: 30 }),
    "Title and keywords: 20 of 30 points",
  );
});

test("the no-skills note appears only when skills max out at 0", () => {
  assert.equal(
    noSkillsNote({
      components: [
        { name: "skills", points: 0, max_points: 0 },
        { name: "text", points: 80, max_points: 100 },
      ],
    }),
    NO_SKILLS_NOTE,
  );
  assert.equal(
    noSkillsNote({
      components: [
        { name: "skills", points: 0, max_points: 70 },
        { name: "text", points: 10, max_points: 30 },
      ],
    }),
    null,
  );
});

test("scored just now only for on-request scores", () => {
  assert.equal(sourceNote("on_request"), ON_REQUEST_NOTE);
  assert.equal(sourceNote("stored"), null);
});

test("batch ids are de-duplicated and capped", () => {
  assert.deepEqual(matchScoreIds(["a", "b", "a"]), ["a", "b"]);
  const many = Array.from({ length: 60 }, (_, i) => `id-${i}`);
  assert.equal(matchScoreIds(many).length, MATCH_BATCH_MAX);
});

test("mergeScores adds a batch by id", () => {
  const items: MatchScoreItem[] = [
    { listing_id: "b", score: 40, source: "on_request" },
    { listing_id: "c", score: 90, source: "stored" },
  ];
  assert.deepEqual(mergeScores({ a: 70, b: 10 }, items), { a: 70, b: 40, c: 90 });
});
