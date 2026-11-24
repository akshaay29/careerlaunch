"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import {
  type TrackerItemOut,
  type UpcomingDeadlineOut,
  listApplications,
  listUpcomingDeadlines,
} from "@/api";
import { MatchScoreBadge } from "@/components/matching/score-breakdown-view";
import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth-paths";
import { ADDED_BY_YOU, formatDate } from "@/lib/listings";
import {
  type DateBy,
  type TrackerFilters,
  DEADLINE_DAYS,
  EMPTY_FILTERS,
  MIN_APPLIED_ON,
  NOT_IN_POOL_LABEL,
  NO_DEADLINES_MESSAGE,
  STATUSES,
  applicationCount,
  daysLeftText,
  filtersError,
  mergeTrackerPages,
  normalizeFilters,
  serializeTrackerFilters,
  statusLabel,
  trackerEmptyMessage,
  trackerHref,
  trackerPath,
  trackerQuery,
} from "@/lib/tracker";

function SignedOut() {
  return (
    <p>
      Your session has ended.{" "}
      <Link className="underline" href={LOGIN_PATH}>
        Log in again
      </Link>
    </p>
  );
}

function StatusChip({ status }: { status: TrackerItemOut["status"] }) {
  return (
    <span className="rounded-full border bg-muted/50 px-2 py-0.5 text-xs" data-testid="status-chip">
      {statusLabel(status)}
    </span>
  );
}

// --------------------------------------------------------------------------- upcoming deadlines

type DeadlinesLoad =
  | { kind: "loading" }
  | { kind: "ok"; items: UpcomingDeadlineOut[] }
  | { kind: "signed-out" }
  | { kind: "error" };

async function fetchDeadlines(): Promise<DeadlinesLoad> {
  try {
    const { data, response } = await listUpcomingDeadlines({ query: { days: DEADLINE_DAYS } });
    if (data) return { kind: "ok", items: data.items };
    if (response?.status === 401) return { kind: "signed-out" };
    return { kind: "error" };
  } catch {
    return { kind: "error" };
  }
}

/** "Upcoming deadlines": open Applications due in the next 30 days, soonest first. */
function UpcomingDeadlines() {
  const [load, setLoad] = useState<DeadlinesLoad>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    fetchDeadlines().then((next) => {
      if (active) setLoad(next);
    });
    return () => {
      active = false;
    };
  }, []);

  let body;
  if (load.kind === "loading") {
    body = (
      <p role="status" className="text-sm text-muted-foreground">
        Loading deadlines…
      </p>
    );
  } else if (load.kind === "signed-out") {
    body = <SignedOut />;
  } else if (load.kind === "error") {
    body = (
      <p role="alert" className="text-sm text-destructive">
        Could not load upcoming deadlines.
      </p>
    );
  } else if (load.items.length === 0) {
    body = (
      <p className="text-sm text-muted-foreground" data-testid="deadlines-empty">
        {NO_DEADLINES_MESSAGE}
      </p>
    );
  } else {
    body = (
      <ul className="flex flex-col gap-2">
        {load.items.map((item) => (
          <li
            key={item.id}
            className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-sm"
          >
            <span className="min-w-0 break-words">
              <Link className="font-medium hover:underline" href={trackerPath(item.id)}>
                {item.title}
              </Link>
              {item.company ? <span className="text-muted-foreground"> · {item.company}</span> : null}
            </span>
            <span className="text-muted-foreground tabular-nums">
              {formatDate(item.deadline) ?? item.deadline} ({daysLeftText(item.days_left)})
            </span>
          </li>
        ))}
      </ul>
    );
  }
  return (
    <section
      className="flex flex-col gap-3 rounded-lg border p-4"
      aria-labelledby="deadlines-heading"
      data-testid="upcoming-deadlines"
    >
      <h2 id="deadlines-heading" className="font-medium">
        Upcoming deadlines
      </h2>
      {body}
    </section>
  );
}

// --------------------------------------------------------------------------- filters

/** Status checkboxes, company, from/to dates and "saved / applied", kept in the URL.
 * Checkboxes and the selector apply at once; the company and dates on blur or Apply. A
 * draft that cannot be applied (a range ending before it starts, an over-long company) stays
 * as typed with a message. */
function FilterBar({ filters }: { filters: TrackerFilters }) {
  const router = useRouter();
  const ids = useId();
  const applied = serializeTrackerFilters(filters);
  // The draft follows the URL whenever the URL changes (Clear, back/forward, an apply).
  const [draft, setDraft] = useState<TrackerFilters>(filters);
  const [shown, setShown] = useState(applied);
  const [error, setError] = useState<string | null>(null);
  if (shown !== applied) {
    setShown(applied);
    setDraft(filters);
    setError(null);
  }
  // The last URL asked for: a blur followed by a click on Apply navigates once.
  const requested = useRef(applied);
  useEffect(() => {
    requested.current = applied;
  }, [applied]);

  /** The one apply path: validate the draft (with any change merged in), then navigate. */
  function apply(change: Partial<TrackerFilters> = {}) {
    const next = { ...draft, ...change };
    setDraft(next);
    const problem = filtersError(next);
    setError(problem);
    if (problem) return;
    const href = trackerHref(normalizeFilters(next));
    const search = serializeTrackerFilters(normalizeFilters(next));
    if (search === requested.current) return;
    requested.current = search;
    router.replace(href);
  }

  function toggleStatus(status: TrackerFilters["statuses"][number], checked: boolean) {
    const statuses = checked
      ? [...draft.statuses, status]
      : draft.statuses.filter((s) => s !== status);
    apply({ statuses });
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    apply();
  }

  function onClear() {
    setDraft(EMPTY_FILTERS);
    setError(null);
    requested.current = "";
    router.replace(trackerHref(EMPTY_FILTERS));
  }

  const dirty = applied !== "" || serializeTrackerFilters(draft) !== "";
  return (
    <form
      onSubmit={onSubmit}
      className="flex flex-col gap-3 rounded-lg border p-4"
      aria-label="Filter Applications"
      data-testid="tracker-filters"
    >
      <fieldset className="flex flex-wrap gap-x-4 gap-y-2">
        <legend className="mb-1 text-sm font-medium">Status</legend>
        {STATUSES.map((status) => (
          <label key={status} className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              className="size-4"
              checked={draft.statuses.includes(status)}
              onChange={(e) => toggleStatus(status, e.target.checked)}
            />
            {statusLabel(status)}
          </label>
        ))}
      </fieldset>
      <div className="flex flex-col gap-1">
        <label htmlFor={`${ids}-company`} className="text-sm font-medium">
          Company
        </label>
        <input
          id={`${ids}-company`}
          type="search"
          value={draft.company}
          onChange={(e) => setDraft((d) => ({ ...d, company: e.target.value }))}
          onBlur={() => apply()}
          autoComplete="off"
          spellCheck={false}
          className="h-9 rounded-lg border border-input bg-background px-3 text-sm"
        />
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <label htmlFor={`${ids}-by`} className="text-sm font-medium">
            Dates are
          </label>
          <select
            id={`${ids}-by`}
            value={draft.dateBy}
            onChange={(e) => apply({ dateBy: e.target.value as DateBy })}
            className="h-9 rounded-lg border border-input bg-background px-2 text-sm"
          >
            <option value="saved">saved</option>
            <option value="applied">applied</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={`${ids}-from`} className="text-sm font-medium">
            From
          </label>
          <input
            id={`${ids}-from`}
            type="date"
            min={MIN_APPLIED_ON}
            value={draft.dateFrom}
            onChange={(e) => setDraft((d) => ({ ...d, dateFrom: e.target.value }))}
            onBlur={() => apply()}
            className="h-9 rounded-lg border border-input bg-background px-2 text-sm"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={`${ids}-to`} className="text-sm font-medium">
            To
          </label>
          <input
            id={`${ids}-to`}
            type="date"
            min={MIN_APPLIED_ON}
            value={draft.dateTo}
            onChange={(e) => setDraft((d) => ({ ...d, dateTo: e.target.value }))}
            onBlur={() => apply()}
            className="h-9 rounded-lg border border-input bg-background px-2 text-sm"
          />
        </div>
        <Button type="submit" variant="outline">
          Apply
        </Button>
        <Button type="button" variant="outline" onClick={onClear} disabled={!dirty}>
          Clear
        </Button>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </form>
  );
}

// --------------------------------------------------------------------------- the list

type ListLoad =
  | { kind: "loading" }
  | { kind: "ok"; items: TrackerItemOut[]; total: number; next: string | null }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

async function fetchList(filters: TrackerFilters, cursor?: string): Promise<ListLoad> {
  try {
    const { data, response } = await listApplications({ query: trackerQuery(filters, cursor) });
    if (data) {
      return { kind: "ok", items: data.items, total: data.total, next: data.next_cursor };
    }
    if (response?.status === 401) return { kind: "signed-out" };
    return { kind: "error", message: `HTTP ${response?.status ?? "?"}` };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

function ApplicationRow({ item }: { item: TrackerItemOut }) {
  const deadline = formatDate(item.deadline);
  return (
    <article
      className="flex items-start justify-between gap-3 rounded-lg border p-4"
      data-testid={`application-${item.id}`}
    >
      <div className="flex min-w-0 flex-col gap-1">
        <h3 className="font-medium break-words">
          <Link className="hover:underline" href={trackerPath(item.id)}>
            {item.title}
          </Link>
        </h3>
        {item.company ? (
          <p className="text-sm text-muted-foreground break-words">{item.company}</p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <StatusChip status={item.status} />
          {deadline ? <span>Apply by {deadline}</span> : null}
          {item.is_manual ? <span data-testid="added-by-you">{ADDED_BY_YOU}</span> : null}
          {item.in_pool ? null : <span data-testid="not-in-pool">{NOT_IN_POOL_LABEL}</span>}
        </div>
      </div>
      {item.score !== null && item.listing_id ? (
        <MatchScoreBadge listingId={item.listing_id} score={item.score} />
      ) : null}
    </article>
  );
}

/** The filtered Applications with their count and "Load more". Remounted (key) per filters. */
function ApplicationList({ filters }: { filters: TrackerFilters }) {
  const [load, setLoad] = useState<ListLoad>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    fetchList(filters).then((next) => {
      if (active) setLoad(next);
    });
    return () => {
      active = false;
    };
    // The list is keyed by the serialised filters: one fetch per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function retry() {
    setBusy(true);
    const next = await fetchList(filters);
    if (!mounted.current) return;
    setBusy(false);
    setLoad(next);
  }

  async function loadMore(cursor: string) {
    setBusy(true);
    setMessage(null);
    const next = await fetchList(filters, cursor);
    if (!mounted.current) return;
    setBusy(false);
    if (next.kind === "ok") {
      setLoad((prev) =>
        prev.kind === "ok"
          ? { ...next, items: mergeTrackerPages(prev.items, next.items) }
          : next,
      );
    } else if (next.kind === "signed-out") {
      setLoad(next);
    } else {
      setMessage("Could not load more Applications. Try again.");
    }
  }

  if (load.kind === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading Applications…
      </p>
    );
  }
  if (load.kind === "signed-out") return <SignedOut />;
  if (load.kind === "error") {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-destructive">
          Could not load your Applications ({load.message}).
        </p>
        <Button type="button" variant="outline" disabled={busy} onClick={retry}>
          {busy ? "Loading…" : "Try again"}
        </Button>
      </div>
    );
  }

  const { items, total, next } = load;
  return (
    <div className="flex flex-col gap-3" data-testid="tracker-list">
      <p role="status" className="text-sm font-medium" data-testid="tracker-count">
        {applicationCount(total)}
      </p>
      {items.length === 0 ? (
        <p
          className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground"
          data-testid="tracker-empty"
        >
          {trackerEmptyMessage(filters)}
        </p>
      ) : (
        <ul className="flex flex-col gap-3">
          {items.map((item) => (
            <li key={item.id}>
              <ApplicationRow item={item} />
            </li>
          ))}
        </ul>
      )}
      {message ? (
        <p role="alert" className="text-sm text-destructive">
          {message}
        </p>
      ) : null}
      {next ? (
        <div>
          <Button type="button" variant="outline" disabled={busy} onClick={() => loadMore(next)}>
            {busy ? "Loading…" : "Load more"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * My Tracker: upcoming deadlines, the filter bar (state in the URL) and the filtered list.
 * Used by /tracker. All Listing text is rendered as plain text.
 */
export function TrackerView({ filters }: { filters: TrackerFilters }) {
  const key = serializeTrackerFilters(filters) || "all";
  return (
    <div className="flex flex-col gap-6">
      <UpcomingDeadlines />
      <FilterBar filters={filters} />
      <ApplicationList key={`list-${key}`} filters={filters} />
    </div>
  );
}
