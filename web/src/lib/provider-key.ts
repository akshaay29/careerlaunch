// Rules for the Provider Key settings page (pure, so they can be unit tested with node --test).
// Relative imports: node --test erases type-only imports and cannot resolve the "@/" alias.
import type { ConsentStatus } from "../api/types.gen.ts";
import { SETTINGS_CONSENT_PATH } from "./consent.ts";

export const SETTINGS_PROVIDER_KEY_PATH = "/settings/provider-key";

export const PROVIDER_RUNS_PURPOSE = "provider_runs";

/**
 * Where a Student without `provider_runs` consent is sent; the consent page returns here only
 * once that purpose is granted.
 */
export const PROVIDER_KEY_CONSENT_REDIRECT = `${SETTINGS_CONSENT_PATH}?${new URLSearchParams({
  next: SETTINGS_PROVIDER_KEY_PATH,
  purpose: PROVIDER_RUNS_PURPOSE,
}).toString()}`;

export const APIFY_CONSOLE_URL = "https://console.apify.com/";
export const APIFY_TOKENS_URL = "https://console.apify.com/settings/integrations";

export const TOKEN_MIN_LENGTH = 8;
export const TOKEN_MAX_LENGTH = 200;
export const TOKEN_EMPTY_MESSAGE = "Paste your Apify token.";
/** Same text as the API's 422 for a malformed token. */
export const TOKEN_FORMAT_MESSAGE =
  "This doesn't look like an Apify token: it must be 8 to 200 characters with no spaces or special characters. Copy it again from the Apify console.";
/** Printable ASCII without spaces, as the API requires. */
const TOKEN_CHARS = /^[\x21-\x7e]+$/;
export const INVALID_TOKEN_FALLBACK =
  "Apify didn't accept this token. Check that you copied it fully and that it hasn't expired.";
export const PROVIDER_UNAVAILABLE_MESSAGE =
  "Apify could not be reached. Try again in a few minutes.";
export const CREDENTIALS_UNAVAILABLE_MESSAGE =
  "Saving provider keys is temporarily unavailable. Try again later.";
export const SAVE_FAILED_MESSAGE = "Could not save your token. Try again.";

/** A message to show before sending, or null when the trimmed token may be sent. */
export function precheckToken(token: string): string | null {
  const trimmed = token.trim();
  if (!trimmed) return TOKEN_EMPTY_MESSAGE;
  if (
    trimmed.length < TOKEN_MIN_LENGTH ||
    trimmed.length > TOKEN_MAX_LENGTH ||
    !TOKEN_CHARS.test(trimmed)
  ) {
    return TOKEN_FORMAT_MESSAGE;
  }
  return null;
}

/** The paste form is offered only once `provider_runs` consent is granted on the current text. */
export function hasProviderRunsConsent(purposes: readonly ConsentStatus[]): boolean {
  return purposes.some((p) => p.purpose === PROVIDER_RUNS_PURPOSE && p.status === "granted");
}

/** The masked hint for a stored key: "••••" + its last four characters. */
export function maskedHint(last4: string | null | undefined): string {
  return `••••${last4 ?? ""}`;
}

function problemType(problem: unknown): string | null {
  if (!problem || typeof problem !== "object" || !("type" in problem)) return null;
  const type = (problem as { type: unknown }).type;
  return typeof type === "string" ? type : null;
}

function tokenFieldError(problem: unknown): string | null {
  const errors =
    problem && typeof problem === "object" && "errors" in problem
      ? (problem as { errors: unknown }).errors
      : undefined;
  if (!Array.isArray(errors)) return null;
  for (const err of errors) {
    if (!err || typeof err !== "object") continue;
    const { loc, msg } = err as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || !loc.map(String).includes("token")) continue;
    return typeof msg === "string" && msg.trim() ? msg.trim() : INVALID_TOKEN_FALLBACK;
  }
  return null;
}

export type SaveOutcome =
  | { kind: "consent-required"; redirect: string }
  | { kind: "field"; message: string }
  | { kind: "form"; message: string };

/** What to do with a failed `PUT /provider-key` (status plus problem+json body). */
export function saveFailure(status: number | undefined, problem: unknown): SaveOutcome {
  const type = problemType(problem);
  if (status === 403 && type === "/problems/consent-required") {
    return { kind: "consent-required", redirect: PROVIDER_KEY_CONSENT_REDIRECT };
  }
  if (status === 422) {
    return { kind: "field", message: tokenFieldError(problem) ?? INVALID_TOKEN_FALLBACK };
  }
  if (status === 503 && type === "/problems/provider-unavailable") {
    return { kind: "form", message: PROVIDER_UNAVAILABLE_MESSAGE };
  }
  if (status === 503 && type === "/problems/credentials-unavailable") {
    return { kind: "form", message: CREDENTIALS_UNAVAILABLE_MESSAGE };
  }
  return { kind: "form", message: SAVE_FAILED_MESSAGE };
}
