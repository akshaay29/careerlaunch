// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import { isProtectedPath, ONBOARDING_PATH, safeNextPath } from "./auth-paths.ts";

test("unsafe next values fall back to /onboarding", () => {
  const unsafe = [
    null,
    undefined,
    "",
    "https://evil.com",
    "//evil.com",
    "/\\evil.com",
    "/\t/evil.com",
    "/\n/evil.com",
    "/\r/evil.com",
    "/\u0000/evil.com",
    "/ok\\..\\evil",
    "onboarding",
  ];
  for (const next of unsafe) {
    assert.equal(safeNextPath(next), ONBOARDING_PATH, JSON.stringify(next));
  }
});

test("same-origin paths pass through", () => {
  assert.equal(safeNextPath("/onboarding/x?y=1"), "/onboarding/x?y=1");
  assert.equal(safeNextPath("/"), "/");
});

test("onboarding, settings and listings pages are protected", () => {
  for (const path of [
    "/onboarding",
    "/onboarding/x",
    "/settings",
    "/settings/consent",
    "/listings",
    "/listings/0193a1b2-0000-7000-8000-000000000001",
  ]) {
    assert.equal(isProtectedPath(path), true, path);
  }
  for (const path of ["/", "/login", "/signup", "/settingsx", "/listingsx", "/auth/error"]) {
    assert.equal(isProtectedPath(path), false, path);
  }
});
