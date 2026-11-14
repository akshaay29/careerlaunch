"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { getProviderUsage, type ProviderUsage, putSpendCaps } from "@/api";
import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth-paths";
import {
  capsFailure,
  formatDateTime,
  formatUsd,
  monthLine,
  pausedBanner,
  precheckCaps,
  runOutcome,
} from "@/lib/provider-usage";

type Load =
  | { kind: "loading" }
  | { kind: "ok"; usage: ProviderUsage }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

async function fetchUsage(): Promise<Load> {
  try {
    const { data, response } = await getProviderUsage();
    if (response?.status === 401) return { kind: "signed-out" };
    if (data) return { kind: "ok", usage: data };
    return { kind: "error", message: `HTTP ${response?.status ?? "?"}` };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

/**
 * The Student's collection usage (FR-28, AD-12): a paused banner, this month's spend, the
 * spend caps and the latest runs. Never shows run ids, search parameters or tokens.
 */
export function ProviderUsageSection({ refreshKey = 0 }: { refreshKey?: number } = {}) {
  const perRunId = useId();
  const monthlyId = useId();
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [perRun, setPerRun] = useState("");
  const [monthly, setMonthly] = useState("");
  const [pending, setPending] = useState(false);
  const busy = useRef(false);
  const [capsError, setCapsError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // The first load fills the cap form; a refetch (the key was saved or deleted) only updates
  // the status, month and runs, and a failed refetch keeps what is already shown.
  useEffect(() => {
    let active = true;
    fetchUsage().then((next) => {
      if (!active) return;
      if (refreshKey === 0) {
        setLoad(next);
        if (next.kind === "ok") {
          setPerRun(next.usage.caps.per_run_usd.toFixed(2));
          setMonthly(next.usage.caps.monthly_usd.toFixed(2));
        }
      } else if (next.kind === "ok" || next.kind === "signed-out") {
        setLoad(next);
      }
    });
    return () => {
      active = false;
    };
  }, [refreshKey]);

  if (load.kind === "loading") {
    return <p className="text-muted-foreground">Loading your usage…</p>;
  }
  if (load.kind === "signed-out") {
    return (
      <p className="text-sm">
        Your session has ended.{" "}
        <Link className="underline" href={LOGIN_PATH}>
          Log in again
        </Link>
      </p>
    );
  }
  if (load.kind === "error") {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load your usage ({load.message}).
      </p>
    );
  }

  const { usage } = load;
  const banner = pausedBanner(usage.status);

  async function onSaveCaps(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || busy.current) return;
    setNotice(null);
    const check = precheckCaps(perRun, monthly);
    if (!check.ok) {
      setCapsError(check.message);
      return;
    }
    setCapsError(null);
    busy.current = true;
    setPending(true);
    try {
      const { data, error, response } = await putSpendCaps({
        body: { per_run_usd: check.perRun, monthly_usd: check.monthly },
      });
      if (data) {
        setNotice("Your caps were saved. They apply from the next run.");
        // Refresh the figures; if that fails, keep the usage already shown.
        const next = await fetchUsage();
        if (next.kind === "ok") {
          setLoad(next);
          setPerRun(next.usage.caps.per_run_usd.toFixed(2));
          setMonthly(next.usage.caps.monthly_usd.toFixed(2));
        }
      } else if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
      } else {
        setCapsError(capsFailure(response?.status, error));
      }
    } catch {
      setCapsError("Could not reach the server. Try again.");
    } finally {
      busy.current = false;
      setPending(false);
    }
  }

  return (
    <section className="flex flex-col gap-4 rounded-lg border p-5" data-testid="provider-usage">
      <h2 className="text-lg font-medium">Usage and spend caps</h2>

      {banner ? (
        <div
          role="alert"
          className="flex flex-col gap-1 rounded-md border border-destructive/50 p-3"
          data-testid="provider-usage-paused"
        >
          <p className="text-sm font-medium">{banner.title}</p>
          <p className="text-sm">{banner.body}</p>
          <p className="text-xs text-muted-foreground">You can still browse the shared pool.</p>
        </div>
      ) : null}

      <p className="text-sm" data-testid="provider-usage-month">
        {monthLine(usage.month)}
      </p>

      <form className="flex flex-col gap-3" onSubmit={onSaveCaps} noValidate>
        <p className="text-sm text-muted-foreground">
          A run is skipped before it starts when its estimate is above your per-run cap, or when
          it could take this month&apos;s spend above your monthly cap.
          {usage.caps.is_default ? " You're using the default caps." : null}
        </p>
        <div className="flex flex-wrap gap-4">
          <label htmlFor={perRunId} className="flex flex-col gap-1 text-sm font-medium">
            Per-run cap (USD)
            <input
              id={perRunId}
              inputMode="decimal"
              value={perRun}
              onChange={(e) => {
                setPerRun(e.target.value);
                setCapsError(null);
              }}
              disabled={pending}
              className="w-32 rounded-md border bg-background px-3 py-2 text-sm"
            />
          </label>
          <label htmlFor={monthlyId} className="flex flex-col gap-1 text-sm font-medium">
            Monthly cap (USD)
            <input
              id={monthlyId}
              inputMode="decimal"
              value={monthly}
              onChange={(e) => {
                setMonthly(e.target.value);
                setCapsError(null);
              }}
              disabled={pending}
              className="w-32 rounded-md border bg-background px-3 py-2 text-sm"
            />
          </label>
        </div>
        {capsError ? (
          <p role="alert" className="text-sm text-destructive">
            {capsError}
          </p>
        ) : null}
        {notice ? (
          <p role="status" className="text-sm">
            {notice}
          </p>
        ) : null}
        <div>
          <Button type="submit" disabled={pending}>
            {pending ? "Saving…" : "Save caps"}
          </Button>
        </div>
      </form>

      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Recent runs</h3>
        {usage.runs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No collection runs yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm" data-testid="provider-usage-runs">
              <thead className="text-xs text-muted-foreground">
                <tr>
                  <th className="py-1 pr-3 font-medium">When</th>
                  <th className="py-1 pr-3 font-medium">Outcome</th>
                  <th className="py-1 pr-3 font-medium">Estimate</th>
                  <th className="py-1 pr-3 font-medium">Actual</th>
                  <th className="py-1 font-medium">Items</th>
                </tr>
              </thead>
              <tbody>
                {usage.runs.map((run, i) => (
                  <tr key={`${run.created_at}-${i}`} className="border-t">
                    <td className="py-1 pr-3 whitespace-nowrap">{formatDateTime(run.created_at)}</td>
                    <td className="py-1 pr-3">{runOutcome(run)}</td>
                    <td className="py-1 pr-3 whitespace-nowrap">{formatUsd(run.estimated_cost_usd)}</td>
                    <td className="py-1 pr-3 whitespace-nowrap">{formatUsd(run.cost_usd)}</td>
                    <td className="py-1">{run.items ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
