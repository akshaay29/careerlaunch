// Rules for the usage section of the Provider Key settings page (pure, so they can be unit
// tested with node --test). Relative imports: node --test erases type-only imports and cannot
// resolve the "@/" alias.
import type {
  ProviderUsageMonth,
  ProviderUsageRun,
  ProviderUsageStatus,
} from "../api/types.gen.ts";

/** Same bounds as the API (`PUT /spend-caps`). */
export const PER_RUN_MIN = 0.05;
export const PER_RUN_MAX = 2;
export const MONTHLY_MIN = 0.25;
export const MONTHLY_MAX = 100;

export const PER_RUN_RANGE_MESSAGE = "The per-run cap must be between USD 0.05 and 2.00.";
export const MONTHLY_RANGE_MESSAGE = "The monthly cap must be between USD 0.25 and 100.";
export const CAP_ORDER_MESSAGE = "The monthly cap must be at least the per-run cap.";
export const CAP_NUMBER_MESSAGE = "Enter an amount in US dollars, like 0.25.";
export const CAPS_SAVE_FAILED_MESSAGE = "Could not save your caps. Try again.";

/** Dates in the usage section are shown in UTC, like the billing cycle and the month. */
const DATE_FORMAT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});
const DATE_TIME_FORMAT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});

const UNKNOWN = "—";

function parseDate(iso: string): Date | null {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "1 Dec 2026" (UTC), or "—" for a string that is not a date. */
export function formatDate(iso: string): string {
  const date = parseDate(iso);
  return date ? DATE_FORMAT.format(date) : UNKNOWN;
}

/** "15 Nov, 09:30 UTC", or "—" for a string that is not a date. */
export function formatDateTime(iso: string): string {
  const date = parseDate(iso);
  return date ? `${DATE_TIME_FORMAT.format(date)} UTC` : UNKNOWN;
}

/**
 * "USD 0.10", or "—" when unknown. A small nonzero amount (a run costs about a tenth of a
 * cent per item) shows up to 4 decimals instead of rounding to "USD 0.00".
 */
export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return UNKNOWN;
  if (value !== 0 && Math.abs(value) < 0.01) {
    if (Math.abs(value) < 0.00005) return `< USD 0.0001`;
    let text = value.toFixed(4);
    while (text.endsWith("0") && text.length - text.indexOf(".") - 1 > 2) {
      text = text.slice(0, -1);
    }
    return `USD ${text}`;
  }
  return `USD ${value.toFixed(2)}`;
}

const PAUSE_COPY: Record<string, string> = {
  key_invalid:
    "Apify no longer accepts your token (it may have been revoked or expired). Replace it above to resume collection.",
  credit_exhausted: "Your Apify credit for this billing cycle is used up.",
  not_permitted:
    "Your token isn't allowed to run the collection actor. Give it the Run permission on the approved actors, then replace it above.",
  account_blocked:
    "Apify refused to run collection for your account. Check your Apify account, then replace your token above.",
};
const PAUSE_FALLBACK = "Collection is paused for your account.";

export type PausedBanner = { title: string; body: string };

/** The paused banner, or null when collection is active. */
export function pausedBanner(status: ProviderUsageStatus): PausedBanner | null {
  if (status.status !== "paused") return null;
  const body = (status.reason && PAUSE_COPY[status.reason]) || PAUSE_FALLBACK;
  if (status.reason === "credit_exhausted" && status.exhausted_until) {
    return {
      title: "Collection is paused",
      body: `${body} Collection resumes on its own from ${formatDate(status.exhausted_until)}.`,
    };
  }
  return { title: "Collection is paused", body };
}

const OUTCOMES: Record<string, string> = {
  // Refusals (no Apify call, nothing spent).
  no_preferences: "Skipped: add role titles in your Preferences",
  no_consent: "Skipped: provider runs consent is off",
  paused: "Skipped: collection is paused",
  exhausted: "Skipped: Apify credit used up",
  disabled: "Skipped: collection is switched off for everyone",
  source_disabled: "Skipped: LinkedIn collection is switched off",
  no_key: "Skipped: no token saved",
  no_actor: "Skipped: no collection actor configured",
  run_cap: "Skipped: estimate above your per-run cap",
  monthly_cap: "Skipped: would pass your monthly cap",
  platform_cap: "Skipped: the platform's monthly limit is reached",
};

const FAILURES: Record<string, string> = {
  key_invalid: "Failed: Apify rejected your token",
  rejected: "Failed: Apify refused the run",
  unavailable: "Failed: Apify could not be reached",
  start_unknown: "Failed: the start could not be confirmed",
  key_unreadable: "Failed: your saved token can't be read",
  credentials_unavailable: "Failed: tokens are temporarily unavailable",
  no_key: "Failed: no token saved",
  timeout: "Failed: took longer than 3 hours",
  timed_out: "Failed: timed out on Apify",
  aborted: "Failed: aborted on Apify",
  no_dataset: "Failed: Apify returned no results",
  poll_exhausted: "Failed: stopped checking on the run",
};

/** A plain-language outcome for one run. */
export function runOutcome(run: Pick<ProviderUsageRun, "status" | "error_kind" | "ingested">): string {
  switch (run.status) {
    case "starting":
      return "Starting";
    case "running":
      return "Running";
    case "succeeded": {
      const n = run.ingested ?? 0;
      return `Collected ${n} Listing${n === 1 ? "" : "s"}`;
    }
    case "refused":
      return (run.error_kind && OUTCOMES[run.error_kind]) || "Skipped";
    case "failed":
      return (run.error_kind && FAILURES[run.error_kind]) || "Failed";
    default:
      return "Unknown";
  }
}

/** The month spend line. */
export function monthLine(month: ProviderUsageMonth): string {
  const base = `${formatUsd(month.spent_usd)} spent this month (UTC) of your ${formatUsd(month.monthly_cap_usd)} cap`;
  if (month.committed_usd > month.spent_usd + 0.00005) {
    return `${base} (up to ${formatUsd(month.committed_usd)} with runs in progress)`;
  }
  return base;
}

export type CapsCheck =
  | { ok: true; perRun: number; monthly: number }
  | { ok: false; field: "per_run_usd" | "monthly_usd"; message: string };

function parseUsd(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^\d+(\.\d{1,4})?$|^\.\d{1,4}$/.test(trimmed)) return null;
  return Number(trimmed);
}

/** Check the cap form before sending, with the API's bounds. */
export function precheckCaps(perRunRaw: string, monthlyRaw: string): CapsCheck {
  const perRun = parseUsd(perRunRaw);
  if (perRun === null) return { ok: false, field: "per_run_usd", message: CAP_NUMBER_MESSAGE };
  if (perRun < PER_RUN_MIN || perRun > PER_RUN_MAX) {
    return { ok: false, field: "per_run_usd", message: PER_RUN_RANGE_MESSAGE };
  }
  const monthly = parseUsd(monthlyRaw);
  if (monthly === null) return { ok: false, field: "monthly_usd", message: CAP_NUMBER_MESSAGE };
  if (monthly < MONTHLY_MIN || monthly > MONTHLY_MAX) {
    return { ok: false, field: "monthly_usd", message: MONTHLY_RANGE_MESSAGE };
  }
  if (monthly < perRun) return { ok: false, field: "monthly_usd", message: CAP_ORDER_MESSAGE };
  return { ok: true, perRun, monthly };
}

/** The message for a failed `PUT /spend-caps` (status plus problem+json body). */
export function capsFailure(status: number | undefined, problem: unknown): string {
  if (status === 422 && problem && typeof problem === "object" && "errors" in problem) {
    const errors = (problem as { errors: unknown }).errors;
    if (Array.isArray(errors)) {
      for (const err of errors) {
        if (!err || typeof err !== "object") continue;
        const { loc } = err as { loc?: unknown };
        const where = Array.isArray(loc) ? loc.map(String) : [];
        if (where.includes("per_run_usd")) return PER_RUN_RANGE_MESSAGE;
        if (where.includes("monthly_usd")) {
          const msg = (err as { msg?: unknown }).msg;
          return msg === CAP_ORDER_MESSAGE ? CAP_ORDER_MESSAGE : MONTHLY_RANGE_MESSAGE;
        }
      }
    }
  }
  return CAPS_SAVE_FAILED_MESSAGE;
}
