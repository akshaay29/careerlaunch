import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { TrackerView } from "@/components/tracker/tracker-view";
import { FEED_PATH, LISTINGS_PATH, SETTINGS_PATH } from "@/lib/auth-paths";
import { parseTrackerFilters } from "@/lib/tracker";

export const metadata: Metadata = { title: "My Tracker · CareerLaunch" };

// The filters (search params) are read inside <Suspense> so the page shell can still be
// prerendered. Applications are fetched in the browser with the Student's token.
async function TrackerFromSearchParams({
  searchParams,
}: {
  searchParams: PageProps<"/tracker">["searchParams"];
}) {
  return <TrackerView filters={parseTrackerFilters(await searchParams)} />;
}

// Protected by the proxy (signed-out visitors go to /login).
export default function TrackerPage(props: PageProps<"/tracker">) {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Link className="text-sm text-muted-foreground underline" href={SETTINGS_PATH}>
            ‹ Settings
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">My Tracker</h1>
        </div>
        <SignOutButton />
      </div>
      <p className="text-sm text-muted-foreground">
        Every Listing you saved and where each application stands. Save more from{" "}
        <Link className="underline" href={FEED_PATH}>
          your feed
        </Link>{" "}
        or{" "}
        <Link className="underline" href={LISTINGS_PATH}>
          all Listings
        </Link>
        .
      </p>
      <Suspense fallback={<p className="text-muted-foreground">Loading your Tracker…</p>}>
        <TrackerFromSearchParams searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}
