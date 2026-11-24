"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import {
  type ListingOut,
  type MatchScoreItem,
  getMatchScores,
  listListings,
  searchListings,
} from "@/api";
import { MatchScoreBadge } from "@/components/matching/score-breakdown-view";
import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth-paths";
import { matchScoreIds, mergeScores } from "@/lib/breakdown";
import {
  LISTINGS_EMPTY_MESSAGE,
  SEARCH_PAGE_SIZE,
  checkSearchQuery,
  mergeListingPages,
  resultCountMessage,
  searchEmptyMessage,
  searchHref,
} from "@/lib/listings";

import { ListingCard } from "./listing-card";

const PAGE_SIZE = 20;

/** Where the next page starts: a browse cursor, or a search offset. */
type Next = { cursor: string } | { offset: number } | null;

type Load =
  | { kind: "loading" }
  | { kind: "ok"; items: ListingOut[]; next: Next }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

async function fetchBrowse(cursor?: string): Promise<Load> {
  try {
    const { data, response } = await listListings({
      query: cursor ? { limit: PAGE_SIZE, cursor } : { limit: PAGE_SIZE },
    });
    if (data) {
      return {
        kind: "ok",
        items: data.items,
        next: data.next_cursor ? { cursor: data.next_cursor } : null,
      };
    }
    if (response?.status === 401) return { kind: "signed-out" };
    return { kind: "error", message: `HTTP ${response?.status ?? "?"}` };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

async function fetchSearch(q: string, offset: number): Promise<Load> {
  try {
    const { data, response } = await searchListings({
      query: { q, limit: SEARCH_PAGE_SIZE, offset },
    });
    if (data) {
      const next = data.next_offset == null ? null : { offset: data.next_offset };
      return { kind: "ok", items: data.items, next };
    }
    if (response?.status === 401) return { kind: "signed-out" };
    return { kind: "error", message: `HTTP ${response?.status ?? "?"}` };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

/** The Student's Match Scores of one search results page (one batch call). Optional: on any
 * failure there are simply no badges. */
async function fetchScores(items: ListingOut[]): Promise<MatchScoreItem[]> {
  const ids = matchScoreIds(items.map((item) => item.id));
  if (ids.length === 0) return [];
  try {
    const { data } = await getMatchScores({ query: { ids } });
    return data?.items ?? [];
  } catch {
    return [];
  }
}

function fetchPage(query: string, next?: Next): Promise<Load> {
  if (query) return fetchSearch(query, next && "offset" in next ? next.offset : 0);
  return fetchBrowse(next && "cursor" in next ? next.cursor : undefined);
}

/**
 * The search box: a label, the input, Search and Clear. Submitting puts the query in the `q`
 * URL parameter (so a search can be shared and reloaded); Clear returns to browsing.
 */
function SearchForm({ query }: { query: string }) {
  const router = useRouter();
  const inputId = useId();
  const errorId = useId();
  const [text, setText] = useState(query);
  const [error, setError] = useState<string | null>(null);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (text.trim() === "") {
      setError(null);
      if (query) router.push(searchHref(""));
      return;
    }
    const checked = checkSearchQuery(text);
    if (!checked.ok) {
      setError(checked.message);
      return;
    }
    setError(null);
    router.push(searchHref(checked.query));
  }

  function onClear() {
    setText("");
    setError(null);
    if (query) router.push(searchHref(""));
  }

  return (
    <form
      role="search"
      onSubmit={onSubmit}
      className="flex flex-col gap-2"
      data-testid="listing-search"
    >
      <label htmlFor={inputId} className="text-sm font-medium">
        Search Listings
      </label>
      <div className="flex flex-wrap gap-2">
        <input
          id={inputId}
          type="search"
          name="q"
          value={text}
          onChange={(e) => setText(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          placeholder="e.g. backend intern"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className="h-9 min-w-0 flex-1 rounded-lg border border-input bg-background px-3 text-sm"
        />
        <Button type="submit">Search</Button>
        <Button type="button" variant="outline" onClick={onClear} disabled={!text && !query}>
          Clear
        </Button>
      </div>
      {error ? (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </form>
  );
}

/**
 * The shared Listing pool: newest first, or, with a search query (the `q` URL parameter), the
 * best matches first. "Load more" in both. Used by /listings, which remounts it (key) when the
 * query changes.
 */
export function ListingBrowser({ query = "" }: { query?: string }) {
  const checked = query ? checkSearchQuery(query) : null;
  const usable = checked === null || checked.ok;
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  // Search results only: Match Scores by Listing id (browsing shows none).
  // Scores belong to the query they were fetched for: another query's scores are never shown.
  const [scores, setScores] = useState<{ query: string; byId: Record<string, number> }>({
    query,
    byId: {},
  });
  const shownScores = scores.query === query ? scores.byId : {};
  // The query being shown (null once unmounted): a response for any other query is dropped.
  const current = useRef<string | null>(query);

  useEffect(() => {
    current.current = query;
    return () => {
      current.current = null;
    };
  }, [query]);

  /** Fetches the scores of a search results page after it is shown (search only). */
  function scorePage(forQuery: string, items: ListingOut[]) {
    if (!forQuery) return;
    void fetchScores(items).then((found) => {
      if (current.current !== forQuery) return;
      setScores((prev) => ({
        query: forQuery,
        byId: mergeScores(prev.query === forQuery ? prev.byId : {}, found),
      }));
    });
  }

  useEffect(() => {
    if (!usable) return;
    let active = true;
    const forQuery = query;
    fetchPage(forQuery).then((next) => {
      if (!active) return;
      setLoad(next);
      if (next.kind === "ok") scorePage(forQuery, next.items);
    });
    return () => {
      active = false;
    };
  }, [query, usable]);

  async function retry() {
    const forQuery = query;
    setBusy(true);
    const next = await fetchPage(forQuery);
    if (current.current !== forQuery) return;
    setBusy(false);
    setLoad(next);
    if (next.kind === "ok") scorePage(forQuery, next.items);
  }

  async function loadMore(from: Next) {
    const forQuery = query;
    setBusy(true);
    setMessage(null);
    const next = await fetchPage(forQuery, from);
    if (current.current !== forQuery) return;
    setBusy(false);
    if (next.kind === "ok") {
      scorePage(forQuery, next.items);
      setLoad((prev) =>
        prev.kind === "ok"
          ? { kind: "ok", items: mergeListingPages(prev.items, next.items), next: next.next }
          : next,
      );
    } else if (next.kind === "signed-out") {
      setLoad(next);
    } else {
      setMessage("Could not load more Listings. Try again.");
    }
  }

  const form = <SearchForm query={query} />;

  if (checked && !checked.ok) {
    return (
      <div className="flex flex-col gap-4">
        {form}
        <p role="alert" className="text-sm text-destructive" data-testid="search-invalid">
          {checked.message}
        </p>
      </div>
    );
  }
  if (load.kind === "loading") {
    return (
      <div className="flex flex-col gap-4">
        {form}
        <p role="status" className="text-muted-foreground">
          {query ? "Searching…" : "Loading Listings…"}
        </p>
      </div>
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
        {form}
        <p role="alert" className="text-destructive">
          Could not load Listings ({load.message}).
        </p>
        <Button type="button" variant="outline" disabled={busy} onClick={retry}>
          {busy ? "Loading…" : "Try again"}
        </Button>
      </div>
    );
  }

  const { items, next } = load;
  if (items.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        {form}
        <p
          role="status"
          className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground break-words"
          data-testid="listings-empty"
        >
          {query ? searchEmptyMessage(query) : LISTINGS_EMPTY_MESSAGE}
        </p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4" data-testid="listing-browser">
      {form}
      {query ? (
        <p role="status" className="sr-only">
          {resultCountMessage(items.length, next !== null)}
        </p>
      ) : null}
      <ul className="flex flex-col gap-3">
        {items.map((listing) => (
          <li key={listing.id}>
            <ListingCard
              listing={listing}
              badge={
                query && listing.id in shownScores ? (
                  <MatchScoreBadge listingId={listing.id} score={shownScores[listing.id]} />
                ) : undefined
              }
            />
          </li>
        ))}
      </ul>
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
