// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { SkillProfileOut } from "../api/types.gen.ts";
import {
  POLL_TIMEOUT_MS,
  cleanSkillTerm,
  hasEdits,
  precheckSkillTerm,
  shouldKeepPolling,
  skillProfileStatusText,
  skillSourceLabel,
  sourceResumeDeleted,
} from "./skill-profile.ts";

function profile(overrides: Partial<SkillProfileOut> = {}): SkillProfileOut {
  return {
    status: "ready",
    resume_id: "r1",
    skills: [{ id: "react", label: "React", source: "extracted" }],
    removed: [],
    custom: [],
    education: [],
    experience: [],
    vocab_version: "2026.11.1",
    message: null,
    has_edits: false,
    ...overrides,
  };
}

test("polls only while pending and for at most 60 s", () => {
  assert.equal(shouldKeepPolling("pending", 0, 0), true);
  assert.equal(shouldKeepPolling("pending", 0, POLL_TIMEOUT_MS - 1), true);
  assert.equal(shouldKeepPolling("pending", 0, POLL_TIMEOUT_MS), false);
  for (const status of ["none", "ready", "unreadable", "failed"] as const) {
    assert.equal(shouldKeepPolling(status, 0, 0), false);
  }
});

test("cleanSkillTerm trims and collapses whitespace", () => {
  assert.equal(cleanSkillTerm("  Basket \t  weaving \n"), "Basket weaving");
  assert.equal(cleanSkillTerm("   "), "");
});

test("precheckSkillTerm", () => {
  assert.equal(precheckSkillTerm("JS"), null);
  assert.equal(precheckSkillTerm("x".repeat(50)), null);
  assert.equal(precheckSkillTerm(` ${"x".repeat(50)} `), null);
  assert.match(precheckSkillTerm("x".repeat(51)) ?? "", /at most 50/);
  assert.match(precheckSkillTerm("  ") ?? "", /Type a skill/);
});

test("source labels", () => {
  assert.equal(skillSourceLabel("extracted"), "from your Resume");
  assert.equal(skillSourceLabel("added"), "added by you");
});

test("status text", () => {
  assert.match(skillProfileStatusText(profile({ status: "none" })) ?? "", /No Resume/);
  assert.match(skillProfileStatusText(profile({ status: "pending" })) ?? "", /Reading/);
  assert.equal(skillProfileStatusText(profile()), null);
});

test("sourceResumeDeleted only when an extracted profile lost its Resume", () => {
  assert.equal(sourceResumeDeleted(profile({ resume_id: null })), true);
  assert.equal(sourceResumeDeleted(profile()), false);
  assert.equal(sourceResumeDeleted(profile({ status: "none", resume_id: null })), false);
  assert.equal(sourceResumeDeleted(profile({ status: "unreadable", resume_id: null })), true);
});

test("hasEdits follows the server's has_edits", () => {
  assert.equal(hasEdits(profile()), false);
  assert.equal(hasEdits(profile({ has_edits: true })), true);
  // An added skill that is also extracted shows as "extracted" but is still an edit.
  assert.equal(
    hasEdits(
      profile({
        skills: [{ id: "react", label: "React", source: "extracted" }],
        has_edits: true,
      }),
    ),
    true,
  );
});
