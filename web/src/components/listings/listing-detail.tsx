"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { type ListingOut, getListing } from "@/api";
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

  useEffect(() => {
    let active = true;
    fetchListing(id).then((next) => {
      if (active) setLoad(next);
    });
    return () => {
      active = false;
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
            void fetchListing(id).then(setLoad);
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
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight break-words">{listing.title}</h1>
        {listing.company ? <p className="text-muted-foreground break-words">{listing.company}</p> : null}
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
