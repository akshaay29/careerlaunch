"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useId, useState } from "react";

import { type ListingOut, listListings, searchListings } from "@/api";
import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth-paths";
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

  useEffect(() => {
    if (!usable) return;
    let active = true;
    fetchPage(query).then((next) => {
      if (active) setLoad(next);
    });
    return () => {
      active = false;
    };
  }, [query, usable]);

  async function retry() {
    setBusy(true);
    const next = await fetchPage(query);
    setBusy(false);
    setLoad(next);
  }

  async function loadMore(from: Next) {
    setBusy(true);
    setMessage(null);
    const next = await fetchPage(query, from);
    setBusy(false);
    if (next.kind === "ok") {
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
            <ListingCard listing={listing} />
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
