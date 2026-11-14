// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { ConsentStatus } from "../api/types.gen.ts";
import { consentReturnPath, consentReturnTarget, shouldReturnAfterGrant } from "./consent.ts";
import {
  CREDENTIALS_UNAVAILABLE_MESSAGE,
  hasProviderRunsConsent,
  INVALID_TOKEN_FALLBACK,
  maskedHint,
  precheckToken,
  PROVIDER_KEY_CONSENT_REDIRECT,
  PROVIDER_UNAVAILABLE_MESSAGE,
  SAVE_FAILED_MESSAGE,
  saveFailure,
  SETTINGS_PROVIDER_KEY_PATH,
  TOKEN_EMPTY_MESSAGE,
  TOKEN_FORMAT_MESSAGE,
} from "./provider-key.ts";

test("the token is trimmed and must be 8 to 200 printable ASCII characters", () => {
  assert.equal(precheckToken(""), TOKEN_EMPTY_MESSAGE);
  assert.equal(precheckToken("   \n"), TOKEN_EMPTY_MESSAGE);
  assert.equal(precheckToken("x".repeat(201)), TOKEN_FORMAT_MESSAGE);
  assert.equal(precheckToken("abcd"), TOKEN_FORMAT_MESSAGE);
  assert.equal(precheckToken("abcdefg"), TOKEN_FORMAT_MESSAGE);
  assert.equal(precheckToken("apify_api_\u201cq\u201d"), TOKEN_FORMAT_MESSAGE);
  assert.equal(precheckToken("apify_api_abc\u00a0def"), TOKEN_FORMAT_MESSAGE);
  assert.equal(precheckToken("apify_api_abc\ndef"), TOKEN_FORMAT_MESSAGE);
  assert.equal(precheckToken("apify_api_abc def"), TOKEN_FORMAT_MESSAGE);
  assert.equal(precheckToken(`  ${"x".repeat(200)}  `), null);
  assert.equal(precheckToken(" a!b~c#d9 "), null);
  assert.equal(precheckToken("apify_api_abc123XYZ"), null); // ggignore -- fake test token
});

function status(purpose: ConsentStatus["purpose"], s: ConsentStatus["status"]): ConsentStatus {
  return { purpose, status: s, title: "", body: [], version: "1", granted_at: null, revoked_at: null };
}

test("the paste form needs provider_runs consent on the current text", () => {
  assert.equal(hasProviderRunsConsent([status("provider_runs", "granted")]), true);
  for (const s of ["none", "revoked", "outdated"] as const) {
    assert.equal(hasProviderRunsConsent([status("provider_runs", s)]), false, s);
  }
  assert.equal(hasProviderRunsConsent([status("pool_contribution", "granted")]), false);
  assert.equal(hasProviderRunsConsent([]), false);
});

test("the masked hint shows only the last four characters", () => {
  assert.equal(maskedHint("WXYZ"), "••••WXYZ");
  assert.equal(maskedHint(null), "••••");
});

test("a missing consent redirects to the consent page, which returns here", () => {
  const outcome = saveFailure(403, {
    type: "/problems/consent-required",
    purpose: "provider_runs",
  });
  assert.deepEqual(outcome, { kind: "consent-required", redirect: PROVIDER_KEY_CONSENT_REDIRECT });
  const url = new URL(PROVIDER_KEY_CONSENT_REDIRECT, "http://x");
  assert.equal(url.pathname, "/settings/consent");
  const next = url.searchParams.get("next");
  const purpose = url.searchParams.get("purpose");
  assert.equal(next, SETTINGS_PROVIDER_KEY_PATH);
  assert.equal(purpose, "provider_runs");
  assert.equal(consentReturnPath(next), SETTINGS_PROVIDER_KEY_PATH);
  assert.deepEqual(consentReturnTarget(next, purpose), {
    path: SETTINGS_PROVIDER_KEY_PATH,
    purpose: "provider_runs",
  });
});

test("the consent page returns only after the named purpose is granted", () => {
  const target = consentReturnTarget("/settings/provider-key", "provider_runs");
  assert.equal(shouldReturnAfterGrant(target, { purpose: "provider_runs", status: "granted" }), true);
  assert.equal(
    shouldReturnAfterGrant(target, { purpose: "pool_contribution", status: "granted" }),
    false,
  );
  assert.equal(shouldReturnAfterGrant(target, { purpose: "provider_runs", status: "outdated" }), false);
  assert.equal(shouldReturnAfterGrant(null, { purpose: "provider_runs", status: "granted" }), false);
  // Both a safe path and a known purpose are required.
  assert.equal(consentReturnTarget("/settings/provider-key", null), null);
  assert.equal(consentReturnTarget("/settings/provider-key", "outreach"), null);
  assert.equal(consentReturnTarget("//evil.com", "provider_runs"), null);
});

test("a rejected token shows the API's message on the field", () => {
  const msg = "Apify didn't accept this token. Check that you copied it fully.";
  assert.deepEqual(
    saveFailure(422, { type: "/problems/validation-error", errors: [{ loc: ["body", "token"], msg }] }),
    { kind: "field", message: msg },
  );
  assert.deepEqual(saveFailure(422, {}), { kind: "field", message: INVALID_TOKEN_FALLBACK });
});

test("503s and other failures show a form message", () => {
  assert.deepEqual(saveFailure(503, { type: "/problems/provider-unavailable" }), {
    kind: "form",
    message: PROVIDER_UNAVAILABLE_MESSAGE,
  });
  assert.deepEqual(saveFailure(503, { type: "/problems/credentials-unavailable" }), {
    kind: "form",
    message: CREDENTIALS_UNAVAILABLE_MESSAGE,
  });
  assert.deepEqual(saveFailure(500, null), { kind: "form", message: SAVE_FAILED_MESSAGE });
  assert.deepEqual(saveFailure(403, { type: "/problems/forbidden" }), {
    kind: "form",
    message: SAVE_FAILED_MESSAGE,
  });
});

test("the consent page returns only to safe local paths", () => {
  assert.equal(consentReturnPath("/settings/provider-key"), "/settings/provider-key");
  for (const unsafe of [null, undefined, "", "https://evil.com", "//evil.com", "/\\evil.com", "x"]) {
    assert.equal(consentReturnPath(unsafe), null, String(unsafe));
  }
});
