import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { FeedView } from "@/components/feed/feed-view";
import { LISTINGS_PATH, SETTINGS_PATH } from "@/lib/auth-paths";
import { belowFromParam } from "@/lib/feed";
import { NOT_AFFILIATED_NOTICE } from "@/lib/listings";

export const metadata: Metadata = { title: "My feed · CareerLaunch" };

// The `below` search param (the "Show below my threshold" toggle) is read inside <Suspense> so
// the page shell can still be prerendered. The feed is keyed by the toggle, so switching it
// starts from a clean first page.
async function FeedFromSearchParams({
  searchParams,
}: {
  searchParams: PageProps<"/feed">["searchParams"];
}) {
  const { below } = await searchParams;
  const showBelow = belowFromParam(below);
  return <FeedView key={showBelow ? "below" : "default"} showBelow={showBelow} />;
}

// Protected by the proxy (signed-out visitors go to /login).
export default function FeedPage(props: PageProps<"/feed">) {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Link className="text-sm text-muted-foreground underline" href={SETTINGS_PATH}>
            ‹ Settings
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">My feed</h1>
        </div>
        <SignOutButton />
      </div>
      <p className="text-sm text-muted-foreground">
        Listings ranked by how well they match your skills and Preferences, best match first.
        You can also{" "}
        <Link className="underline" href={LISTINGS_PATH}>
          browse all Listings
        </Link>
        . &ldquo;Apply&rdquo; opens the original posting. {NOT_AFFILIATED_NOTICE}
      </p>
      <Suspense fallback={<p className="text-muted-foreground">Loading your feed…</p>}>
        <FeedFromSearchParams searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}
