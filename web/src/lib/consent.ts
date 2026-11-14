// Display rules for consent statuses (pure, so they can be unit tested with node --test).
// Relative type-only import: node --test erases it and cannot resolve the "@/" alias.
import type { ConsentPurpose, ConsentStatus } from "../api/types.gen.ts";
import { safeNextPath } from "./auth-paths.ts";

export type ConsentStatusName = ConsentStatus["status"];

export const SETTINGS_CONSENT_PATH = "/settings/consent";

/** Short, neutral label for a status badge. */
export function consentStatusLabel(status: ConsentStatusName): string {
  switch (status) {
    case "granted":
      return "Agreed";
    case "outdated":
      return "Text updated: please review";
    case "revoked":
      return "Withdrawn";
    case "none":
      return "Not agreed";
  }
}

/** "I agree" is offered unless the Student already agreed to the current text. */
export function canAgree(status: ConsentStatusName): boolean {
  return status !== "granted";
}

/** Withdrawing is offered while a consent is active (current or older text). */
export function canWithdraw(status: ConsentStatusName): boolean {
  return status === "granted" || status === "outdated";
}

const PURPOSES: readonly ConsentPurpose[] = ["provider_runs", "pool_contribution"];

/**
 * Where the consent page returns after a grant: the `?next=` value when it is a safe local
 * path (same rules as sign-in, via safeNextPath), otherwise null (stay on the page).
 */
export function consentReturnPath(next: string | null | undefined): string | null {
  if (!next) return null;
  const safe = safeNextPath(next);
  return safe === next ? safe : null;
}

export type ConsentReturn = { path: string; purpose: ConsentPurpose };

/**
 * The return target of the consent page: a safe `next` path plus the `purpose` whose grant
 * triggers it. Both are required, so agreeing to another purpose never bounces the Student
 * back (and back again); otherwise null.
 */
export function consentReturnTarget(
  next: string | null | undefined,
  purpose: string | null | undefined,
): ConsentReturn | null {
  const path = consentReturnPath(next);
  const known = PURPOSES.find((p) => p === purpose);
  return path && known ? { path, purpose: known } : null;
}

/** True when a grant response should send the Student back to `target`. */
export function shouldReturnAfterGrant(
  target: ConsentReturn | null,
  granted: Pick<ConsentStatus, "purpose" | "status">,
): boolean {
  return target !== null && granted.purpose === target.purpose && granted.status === "granted";
}
