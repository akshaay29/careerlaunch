"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { type ListingMatch, type ListingOut, getListing, getListingMatch } from "@/api";
import { MatchScoreBadge } from "@/components/matching/score-breakdown-view";
import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth-paths";
import { formatDate, formatJobType, formatTimestampDate } from "@/lib/listings";

import { ApplyLink, ListingAttribution } from "./listing-attribution";
import { SkillChips } from "./listing-card";

type Load =
  | { kind: "loading" }
  | { kind: "ok"; listing: ListingOut }
  | { kind: "not-found" }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

async function fetchListing(id: string): Promise<Load> {
  try {
    const { data, response } = await getListing({ path: { listing_id: id } });
    if (data) return { kind: "ok", listing: data };
    if (response?.status === 401) return { kind: "signed-out" };
    // 404 (not visible, expired or unknown) and 422 (not a valid id) look the same.
    if (response?.status === 404 || response?.status === 422) return { kind: "not-found" };
    return { kind: "error", message: `HTTP ${response?.status ?? "?"}` };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

/** The Student's Match Score of this Listing, or null when it cannot be had (no badge then). */
async function fetchScore(id: string): Promise<ListingMatch | null> {
  try {
    const { data } = await getListingMatch({ path: { listing_id: id } });
    return data ?? null;
  } catch {
    return null;
  }
}

/**
 * Loads the Listing, then, only when it loaded, its Match Score by the server's id (so the
 * badge never depends on the URL's spelling of the id). The score is optional: when it fails
 * the page renders without a badge.
 */
async function loadListing(
  id: string,
  request: { current: number },
  setLoad: (load: Load) => void,
  setMatch: (match: ListingMatch | null) => void,
) {
  const mine = ++request.current;
  const next = await fetchListing(id);
  if (request.current !== mine) return;
  setLoad(next);
  if (next.kind !== "ok") return;
  const found = await fetchScore(next.listing.id);
  if (request.current === mine && found) setMatch(found);
}

function Fact({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div className="flex gap-2 text-sm">
      <dt className="w-24 shrink-0 text-muted-foreground">{label}</dt>
      <dd className="break-words">{value}</dd>
    </div>
  );
}

/** One Listing by id. All scraped text is rendered as plain text. Used by /listings/[id]. */
export function ListingDetail({ id }: { id: string }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [match, setMatch] = useState<ListingMatch | null>(null);
  // Bumped by each load (mount, id change, Try again) and on unmount: only the latest load
  // may set state.
  const request = useRef(0);

  useEffect(() => {
    void loadListing(id, request, setLoad, setMatch);
    const current = request;
    return () => {
      current.current += 1;
    };
  }, [id]);

  if (load.kind === "loading") {
    return <p className="text-muted-foreground">Loading Listing…</p>;
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
  if (load.kind === "not-found") {
    return (
      <p className="text-muted-foreground" data-testid="listing-not-found">
        This Listing is not available. It may have expired.
      </p>
    );
  }
  if (load.kind === "error") {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-destructive">
          Could not load this Listing ({load.message}).
        </p>
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            setLoad({ kind: "loading" });
            void loadListing(id, request, setLoad, setMatch);
          }}
        >
          Try again
        </Button>
      </div>
    );
  }

  const { listing } = load;
  return (
    <article className="flex flex-col gap-5" data-testid="listing-detail">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-tight break-words">{listing.title}</h1>
          {listing.company ? <p className="text-muted-foreground break-words">{listing.company}</p> : null}
        </div>
        {match && match.listing_id === listing.id ? (
          <MatchScoreBadge listingId={listing.id} score={match.score} initial={match} />
        ) : null}
      </div>
      <dl className="flex flex-col gap-2">
        <Fact label="Location" value={listing.location || null} />
        <Fact label="Job type" value={formatJobType(listing.job_type)} />
        <Fact label="Posted" value={formatTimestampDate(listing.posted_at)} />
        <Fact label="Apply by" value={formatDate(listing.deadline)} />
      </dl>
      {listing.skills.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-medium">Skills</h2>
          <SkillChips skills={listing.skills} />
        </section>
      ) : null}
      <div>
        <ApplyLink listing={listing} />
      </div>
      <ListingAttribution listing={listing} />
    </article>
  );
}
