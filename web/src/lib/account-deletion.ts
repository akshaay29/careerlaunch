// Rules for the "Delete my account" form (pure, so they can be unit tested with node --test).
// The server checks the confirmation again; these only keep the button honest.

export const SETTINGS_ACCOUNT_PATH = "/settings/account";

export const CONFIRM_EMAIL_MISMATCH_MESSAGE = "Type the email address of your account to confirm.";

/** Trimmed and lower-cased, the way the API compares the confirmation. */
export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

/** True when the typed text confirms the account email (ignoring case and surrounding spaces). */
export function confirmationMatches(typed: string, accountEmail: string): boolean {
  const expected = normalizeEmail(accountEmail);
  return expected.length > 0 && normalizeEmail(typed) === expected;
}

/**
 * The message for the confirm_email field from a 422 problem+json (`errors: [{loc, msg}]`,
 * loc like `["body", "confirm_email"]`), or null when the problem names no such error.
 */
export function confirmEmailError(problem: unknown): string | null {
  const errors =
    problem && typeof problem === "object" && "errors" in problem
      ? (problem as { errors: unknown }).errors
      : undefined;
  if (!Array.isArray(errors)) return null;
  for (const err of errors) {
    if (!err || typeof err !== "object") continue;
    const { loc, msg } = err as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || !loc.map(String).includes("confirm_email")) continue;
    const text = typeof msg === "string" ? msg.replace(/^Value error,\s*/i, "").trim() : "";
    return text || CONFIRM_EMAIL_MISMATCH_MESSAGE;
  }
  return null;
}
