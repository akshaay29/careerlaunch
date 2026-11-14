import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { ListingDetail } from "@/components/listings/listing-detail";
import { LISTINGS_PATH } from "@/lib/listings";

export const metadata: Metadata = { title: "Listing · CareerLaunch" };

// Protected by the proxy (signed-out visitors go to /login). The Listing is fetched in the
// browser with the Student's token (only the API decides what is visible). The params are
// read inside <Suspense> so the page shell can still be prerendered.
async function ListingFromParams({ params }: { params: PageProps<"/listings/[id]">["params"] }) {
  const { id } = await params;
  return <ListingDetail id={id} />;
}

export default function ListingPage(props: PageProps<"/listings/[id]">) {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <Link className="text-sm text-muted-foreground underline" href={LISTINGS_PATH}>
          ‹ All Listings
        </Link>
        <SignOutButton />
      </div>
      <Suspense fallback={<p className="text-muted-foreground">Loading Listing…</p>}>
        <ListingFromParams params={props.params} />
      </Suspense>
    </main>
  );
}
