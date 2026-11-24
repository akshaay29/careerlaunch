import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { ListingBrowser } from "@/components/listings/listing-browser";
import { SETTINGS_PATH } from "@/lib/auth-paths";
import { NOT_AFFILIATED_NOTICE, queryFromParam } from "@/lib/listings";

export const metadata: Metadata = { title: "Listings · CareerLaunch" };

// The `q` search param (a keyword search; absent = browse) is read inside <Suspense> so the
// page shell can still be prerendered. The browser is keyed by the query, so a new search
// starts from a clean page.
async function ListingsFromSearchParams({
  searchParams,
}: {
  searchParams: PageProps<"/listings">["searchParams"];
}) {
  const { q } = await searchParams;
  const query = queryFromParam(q);
  return <ListingBrowser key={query} query={query} />;
}

// Protected by the proxy (signed-out visitors go to /login).
export default function ListingsPage(props: PageProps<"/listings">) {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Link className="text-sm text-muted-foreground underline" href={SETTINGS_PATH}>
            ‹ Settings
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Listings</h1>
        </div>
        <SignOutButton />
      </div>
      <p className="text-sm text-muted-foreground">
        Jobs and internships from the shared pool, newest first, or best match first when you
        search. &ldquo;Apply&rdquo; opens the original posting. {NOT_AFFILIATED_NOTICE}
      </p>
      <Suspense fallback={<p className="text-muted-foreground">Loading Listings…</p>}>
        <ListingsFromSearchParams searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}
