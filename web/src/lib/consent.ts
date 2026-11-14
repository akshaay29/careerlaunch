// Display rules for consent statuses (pure, so they can be unit tested with node --test).
// Relative type-only import: node --test erases it and cannot resolve the "@/" alias.
import type { ConsentStatus } from "../api/types.gen.ts";

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
