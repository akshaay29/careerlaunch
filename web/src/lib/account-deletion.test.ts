// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CONFIRM_EMAIL_MISMATCH_MESSAGE,
  confirmationMatches,
  confirmEmailError,
  normalizeEmail,
  SETTINGS_ACCOUNT_PATH,
} from "./account-deletion.ts";
import { ACCOUNT_DELETED_PATH, isProtectedPath, SETTINGS_PATH } from "./auth-paths.ts";

test("the confirmation ignores case and surrounding spaces", () => {
  assert.equal(normalizeEmail("  Student@X.com \n"), "student@x.com");
  assert.equal(confirmationMatches("  STUDENT@X.COM ", "student@x.com"), true);
  assert.equal(confirmationMatches("student@x.com", "Student@X.com"), true);
});

test("the confirmation must be the whole email", () => {
  assert.equal(confirmationMatches("", "student@x.com"), false);
  assert.equal(confirmationMatches("student@x.co", "student@x.com"), false);
  assert.equal(confirmationMatches("xstudent@x.com", "student@x.com"), false);
  assert.equal(confirmationMatches("stu dent@x.com", "student@x.com"), false);
  // An unknown account email never enables the button.
  assert.equal(confirmationMatches("", ""), false);
  assert.equal(confirmationMatches("  ", " "), false);
});

test("a 422 on confirm_email is shown on the field", () => {
  const problem = {
    type: "/problems/validation-error",
    errors: [{ loc: ["body", "confirm_email"], msg: "Type the email address of your account to confirm." }],
  };
  assert.equal(confirmEmailError(problem), "Type the email address of your account to confirm.");
  assert.equal(
    confirmEmailError({ errors: [{ loc: ["body", "confirm_email"], msg: "Field required" }] }),
    "Field required",
  );
  assert.equal(
    confirmEmailError({ errors: [{ loc: ["body", "confirm_email"], msg: "" }] }),
    CONFIRM_EMAIL_MISMATCH_MESSAGE,
  );
});

test("problems without a confirm_email error give null", () => {
  assert.equal(confirmEmailError(null), null);
  assert.equal(confirmEmailError({ detail: "x" }), null);
  assert.equal(confirmEmailError({ errors: [{ loc: ["body", "other"], msg: "x" }] }), null);
  assert.equal(confirmEmailError({ errors: "nope" }), null);
});

test("the deletion page is a protected settings page; the goodbye page is public", () => {
  assert.ok(SETTINGS_ACCOUNT_PATH.startsWith(`${SETTINGS_PATH}/`));
  assert.equal(isProtectedPath(SETTINGS_ACCOUNT_PATH), true);
  assert.equal(isProtectedPath(ACCOUNT_DELETED_PATH), false);
});
