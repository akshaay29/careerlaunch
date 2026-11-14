"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { type ListingOut, listListings } from "@/api";
import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth-paths";
import { LISTINGS_EMPTY_MESSAGE, mergeListingPages } from "@/lib/listings";

import { ListingCard } from "./listing-card";

const PAGE_SIZE = 20;

type Load =
  | { kind: "loading" }
  | { kind: "ok"; items: ListingOut[]; nextCursor: string | null }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

async function fetchPage(cursor?: string): Promise<Load> {
  try {
    const { data, response } = await listListings({
      query: cursor ? { limit: PAGE_SIZE, cursor } : { limit: PAGE_SIZE },
    });
    if (data) return { kind: "ok", items: data.items, nextCursor: data.next_cursor ?? null };
    if (response?.status === 401) return { kind: "signed-out" };
    return { kind: "error", message: `HTTP ${response?.status ?? "?"}` };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

/** The shared Listing pool, newest first, with "Load more". Used by /listings. */
export function ListingBrowser() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchPage().then((next) => {
      if (active) setLoad(next);
    });
    return () => {
      active = false;
    };
  }, []);

  async function retry() {
    setBusy(true);
    const next = await fetchPage();
    setBusy(false);
    setLoad(next);
  }

  async function loadMore(cursor: string) {
    setBusy(true);
    setMessage(null);
    const next = await fetchPage(cursor);
    setBusy(false);
    if (next.kind === "ok") {
      setLoad((prev) =>
        prev.kind === "ok"
          ? { kind: "ok", items: mergeListingPages(prev.items, next.items), nextCursor: next.nextCursor }
          : next,
      );
    } else if (next.kind === "signed-out") {
      setLoad(next);
    } else {
      setMessage("Could not load more Listings. Try again.");
    }
  }

  if (load.kind === "loading") {
    return <p className="text-muted-foreground">Loading Listings…</p>;
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
          Could not load Listings ({load.message}).
        </p>
        <Button type="button" variant="outline" disabled={busy} onClick={retry}>
          {busy ? "Loading…" : "Try again"}
        </Button>
      </div>
    );
  }

  const { items, nextCursor } = load;
  if (items.length === 0) {
    return (
      <p
        className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground"
        data-testid="listings-empty"
      >
        {LISTINGS_EMPTY_MESSAGE}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4" data-testid="listing-browser">
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
      {nextCursor ? (
        <div>
          <Button type="button" variant="outline" disabled={busy} onClick={() => loadMore(nextCursor)}>
            {busy ? "Loading…" : "Load more"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
