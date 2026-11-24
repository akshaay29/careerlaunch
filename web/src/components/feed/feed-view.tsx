"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

import { type Feed, type FeedItem, getFeed } from "@/api";
import { ListingCard } from "@/components/listings/listing-card";
import { MatchScoreBadge } from "@/components/matching/score-breakdown-view";
import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth-paths";
import {
  BELOW_THRESHOLD_LABEL,
  SHOW_BELOW_LABEL,
  feedChanged,
  feedEmptyMessage,
  feedHref,
  feedNotice,
  feedQuery,
  mergeFeedPages,
} from "@/lib/feed";
import { SETTINGS_PREFERENCES_PATH } from "@/lib/preferences";
import { SETTINGS_SKILLS_PATH } from "@/lib/skill-profile";

type Load =
  | { kind: "loading" }
  | {
      kind: "ok";
      state: Feed["state"];
      threshold: number;
      hiddenBelow: boolean;
      items: FeedItem[];
      next: string | null;
    }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

async function fetchFeed(showBelow: boolean, cursor?: string): Promise<Load> {
  try {
    const { data, response } = await getFeed({ query: feedQuery(showBelow, cursor) });
    if (data) {
      return {
        kind: "ok",
        state: data.state,
        threshold: data.threshold,
        hiddenBelow: data.hidden_below_threshold,
        items: data.items,
        next: data.next_cursor,
      };
    }
    if (response?.status === 401) return { kind: "signed-out" };
    return { kind: "error", message: `HTTP ${response?.status ?? "?"}` };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

/** "Show below my threshold": one checkbox; its state lives in the `below=1` URL parameter. */
function BelowToggle({ showBelow }: { showBelow: boolean }) {
  const router = useRouter();
  const id = useId();
  return (
    <div className="flex items-center gap-2">
      <input
        id={id}
        type="checkbox"
        checked={showBelow}
        onChange={(e) => router.replace(feedHref(e.target.checked))}
        className="size-4"
        data-testid="feed-below-toggle"
      />
      <label htmlFor={id} className="text-sm">
        {SHOW_BELOW_LABEL}
      </label>
    </div>
  );
}

/** The score badge with "Why this score" and, when it applies, a muted "Below your
 * threshold" label. */
function ScoreBadge({ item }: { item: FeedItem }) {
  return (
    <MatchScoreBadge listingId={item.id} score={item.score}>
      {item.below_threshold ? (
        <span className="text-xs text-muted-foreground">{BELOW_THRESHOLD_LABEL}</span>
      ) : null}
    </MatchScoreBadge>
  );
}

/** The neutral finish-setup prompt (no urgency). */
function SetupPrompt({ message }: { message: string }) {
  return (
    <section
      className="flex flex-col gap-3 rounded-lg border border-dashed p-5 text-sm"
      data-testid="feed-setup-needed"
    >
      <p>{message}</p>
      <ul className="flex flex-wrap gap-4">
        <li>
          <Link className="underline" href={SETTINGS_SKILLS_PATH}>
            Skill Profile
          </Link>
        </li>
        <li>
          <Link className="underline" href={SETTINGS_PREFERENCES_PATH}>
            Preferences
          </Link>
        </li>
      </ul>
    </section>
  );
}

/**
 * My Listings ranked by Match Score, best first. Used by /feed, which remounts it (key) when
 * the toggle changes.
 */
export function FeedView({ showBelow }: { showBelow: boolean }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  // retry/loadMore/refresh finish after an await: never set state once unmounted.
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    fetchFeed(showBelow).then((next) => {
      if (active) setLoad(next);
    });
    return () => {
      active = false;
    };
  }, [showBelow]);

  /** Reloads the first page (Try again, Refresh, or a feed that changed under Load more). */
  async function reload() {
    setBusy(true);
    setMessage(null);
    const next = await fetchFeed(showBelow);
    if (!mounted.current) return;
    setBusy(false);
    setLoad(next);
  }

  async function loadMore(cursor: string) {
    setBusy(true);
    setMessage(null);
    const next = await fetchFeed(showBelow, cursor);
    if (!mounted.current) return;
    if (next.kind === "ok" && load.kind === "ok" && feedChanged(load, next)) {
      // The feed changed (e.g. scores became ready, or the threshold moved): start over.
      await reload();
      return;
    }
    setBusy(false);
    if (next.kind === "ok") {
      setLoad((prev) =>
        prev.kind === "ok"
          ? { ...next, items: mergeFeedPages(prev.items, next.items) }
          : next,
      );
    } else if (next.kind === "signed-out") {
      setLoad(next);
    } else {
      setMessage("Could not load more Listings. Try again.");
    }
  }

  if (load.kind === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading your feed…
      </p>
    );
  }
  if (load.kind === "signed-out") {
    return (
      <p>
        Your session has ended.{" "}
        <Link className="underline" href={LOGIN_PATH}>
          Log in again
        </Link>
      </p>
    );
  }
  if (load.kind === "error") {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-destructive">
          Could not load your feed ({load.message}).
        </p>
        <Button type="button" variant="outline" disabled={busy} onClick={reload}>
          {busy ? "Loading…" : "Try again"}
        </Button>
      </div>
    );
  }

  const { state, threshold, hiddenBelow, items, next } = load;
  if (state === "setup_needed") {
    return <SetupPrompt message={feedEmptyMessage(state, hiddenBelow)} />;
  }
  const notice = feedNotice(state);
  return (
    <div className="flex flex-col gap-4" data-testid="feed">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <BelowToggle showBelow={showBelow} />
        <p className="text-sm text-muted-foreground">Your threshold: {threshold}</p>
      </div>
      {notice ? (
        <div className="flex flex-wrap items-center gap-3" data-testid="feed-provisional">
          <p role="status" className="text-sm text-muted-foreground">
            {notice}
          </p>
          <Button type="button" variant="outline" size="sm" disabled={busy} onClick={reload}>
            {busy ? "Refreshing…" : "Refresh"}
          </Button>
        </div>
      ) : null}
      {items.length === 0 ? (
        <p
          role="status"
          className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground"
          data-testid="feed-empty"
        >
          {feedEmptyMessage(state, hiddenBelow)}
        </p>
      ) : (
        <ul className="flex flex-col gap-3">
          {items.map((item) => (
            <li key={item.id}>
              <ListingCard listing={item} badge={<ScoreBadge item={item} />} />
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
