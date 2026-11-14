// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import { canAgree, canWithdraw, consentStatusLabel, type ConsentStatusName } from "./consent.ts";

const ALL: ConsentStatusName[] = ["granted", "revoked", "none", "outdated"];

test("every status has a label", () => {
  for (const status of ALL) assert.ok(consentStatusLabel(status).length > 0, status);
});

test("agree is offered unless already agreed to the current text", () => {
  assert.deepEqual(
    ALL.filter(canAgree),
    ["revoked", "none", "outdated"],
  );
});

test("withdraw is offered only while a consent is active", () => {
  assert.deepEqual(ALL.filter(canWithdraw), ["granted", "outdated"]);
});
