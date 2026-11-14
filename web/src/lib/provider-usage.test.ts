// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProviderUsageRun } from "../api/types.gen.ts";
import {
  CAP_NUMBER_MESSAGE,
  CAP_ORDER_MESSAGE,
  CAPS_SAVE_FAILED_MESSAGE,
  capsFailure,
  formatDate,
  formatDateTime,
  formatUsd,
  monthLine,
  MONTHLY_RANGE_MESSAGE,
  pausedBanner,
  PER_RUN_RANGE_MESSAGE,
  precheckCaps,
  runOutcome,
} from "./provider-usage.ts";

test("money is shown in USD with two decimals", () => {
  assert.equal(formatUsd(0.1), "USD 0.10");
  assert.equal(formatUsd(4), "USD 4.00");
  assert.equal(formatUsd(null), "—");
  assert.equal(formatUsd(undefined), "—");
  assert.equal(formatUsd(0), "USD 0.00");
  assert.equal(formatUsd(0.0012), "USD 0.0012");
  assert.equal(formatUsd(0.001), "USD 0.001");
  assert.equal(formatUsd(0.0099), "USD 0.0099");
  assert.equal(formatUsd(0.00001), "< USD 0.0001");
  assert.equal(formatUsd(0.01), "USD 0.01");
});

test("an unparsable date shows a dash", () => {
  assert.equal(formatDate("not a date"), "—");
  assert.equal(formatDate(""), "—");
  assert.equal(formatDateTime("not a date"), "—");
  assert.equal(formatDateTime("2026-11-15T09:30:00Z"), "15 Nov, 09:30 UTC");
});

test("an active status shows no banner", () => {
  assert.equal(pausedBanner({ status: "active", reason: null, exhausted_until: null }), null);
});

test("each pause reason has its own copy", () => {
  const bodies = new Set<string>();
  for (const reason of ["key_invalid", "not_permitted", "account_blocked"]) {
    const banner = pausedBanner({ status: "paused", reason, exhausted_until: null });
    assert.ok(banner, reason);
    assert.equal(banner.title, "Collection is paused");
    assert.match(banner.body, /replace/i, reason);
    bodies.add(banner.body);
  }
  assert.equal(bodies.size, 3);
  const unknown = pausedBanner({ status: "paused", reason: "other", exhausted_until: null });
  assert.equal(unknown?.body, "Collection is paused for your account.");
});

test("exhausted credit shows the resume date (UTC)", () => {
  const banner = pausedBanner({
    status: "paused",
    reason: "credit_exhausted",
    exhausted_until: "2026-12-01T23:59:59.999Z",
  });
  assert.ok(banner);
  assert.match(banner.body, /credit/);
  assert.match(banner.body, /resumes on its own from 1 Dec 2026\./);
  assert.equal(formatDate("2026-12-01T23:59:59.999Z"), "1 Dec 2026");
});

function run(
  status: ProviderUsageRun["status"],
  error_kind: string | null = null,
  ingested: number | null = null,
) {
  return { status, error_kind, ingested };
}

test("every run gets a plain-language outcome", () => {
  assert.equal(runOutcome(run("starting")), "Starting");
  assert.equal(runOutcome(run("running")), "Running");
  assert.equal(runOutcome(run("succeeded", null, 8)), "Collected 8 Listings");
  assert.equal(runOutcome(run("succeeded", null, 1)), "Collected 1 Listing");
  assert.equal(runOutcome(run("succeeded")), "Collected 0 Listings");
  assert.equal(runOutcome(run("refused", "run_cap")), "Skipped: estimate above your per-run cap");
  assert.equal(runOutcome(run("refused", "monthly_cap")), "Skipped: would pass your monthly cap");
  assert.equal(
    runOutcome(run("refused", "platform_cap")),
    "Skipped: the platform's monthly limit is reached",
  );
  assert.equal(runOutcome(run("refused", "exhausted")), "Skipped: Apify credit used up");
  assert.equal(runOutcome(run("refused", "paused")), "Skipped: collection is paused");
  assert.equal(runOutcome(run("refused", "something_new")), "Skipped");
  assert.equal(runOutcome(run("failed", "key_invalid")), "Failed: Apify rejected your token");
  assert.equal(runOutcome(run("failed", "rejected")), "Failed: Apify refused the run");
  assert.equal(runOutcome(run("failed", "timeout")), "Failed: took longer than 3 hours");
  assert.equal(runOutcome(run("failed", "failed")), "Failed");
});

test("the month line mentions runs in progress only when they add to the spend", () => {
  assert.equal(
    monthLine({ spent_usd: 0.1, committed_usd: 0.1, monthly_cap_usd: 4 }),
    "USD 0.10 spent this month (UTC) of your USD 4.00 cap",
  );
  assert.equal(
    monthLine({ spent_usd: 0.1, committed_usd: 0.33, monthly_cap_usd: 4 }),
    "USD 0.10 spent this month (UTC) of your USD 4.00 cap (up to USD 0.33 with runs in progress)",
  );
});

test("caps are checked with the API's bounds", () => {
  assert.deepEqual(precheckCaps("0.25", "4"), { ok: true, perRun: 0.25, monthly: 4 });
  assert.deepEqual(precheckCaps(" .5 ", "100"), { ok: true, perRun: 0.5, monthly: 100 });
  assert.deepEqual(precheckCaps("", "4"), {
    ok: false,
    field: "per_run_usd",
    message: CAP_NUMBER_MESSAGE,
  });
  assert.deepEqual(precheckCaps("abc", "4"), {
    ok: false,
    field: "per_run_usd",
    message: CAP_NUMBER_MESSAGE,
  });
  assert.deepEqual(precheckCaps("3", "10"), {
    ok: false,
    field: "per_run_usd",
    message: PER_RUN_RANGE_MESSAGE,
  });
  assert.deepEqual(precheckCaps("0.01", "10"), {
    ok: false,
    field: "per_run_usd",
    message: PER_RUN_RANGE_MESSAGE,
  });
  assert.deepEqual(precheckCaps("0.25", "101"), {
    ok: false,
    field: "monthly_usd",
    message: MONTHLY_RANGE_MESSAGE,
  });
  assert.deepEqual(precheckCaps("1", "0.5"), {
    ok: false,
    field: "monthly_usd",
    message: CAP_ORDER_MESSAGE,
  });
  assert.deepEqual(precheckCaps("0.25", "-4"), {
    ok: false,
    field: "monthly_usd",
    message: CAP_NUMBER_MESSAGE,
  });
});

test("a failed caps save maps to a message", () => {
  assert.equal(
    capsFailure(422, { errors: [{ loc: ["body", "per_run_usd"], msg: "x" }] }),
    PER_RUN_RANGE_MESSAGE,
  );
  assert.equal(
    capsFailure(422, { errors: [{ loc: ["body", "monthly_usd"], msg: CAP_ORDER_MESSAGE }] }),
    CAP_ORDER_MESSAGE,
  );
  assert.equal(
    capsFailure(422, { errors: [{ loc: ["body", "monthly_usd"], msg: "too big" }] }),
    MONTHLY_RANGE_MESSAGE,
  );
  assert.equal(capsFailure(500, null), CAPS_SAVE_FAILED_MESSAGE);
  assert.equal(capsFailure(422, { errors: "nope" }), CAPS_SAVE_FAILED_MESSAGE);
});
