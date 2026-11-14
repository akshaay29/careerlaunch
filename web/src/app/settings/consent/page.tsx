import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { ConsentPanel } from "@/components/consent/consent-panel";
import { SETTINGS_PATH } from "@/lib/auth-paths";
import { consentReturnTarget } from "@/lib/consent";

export const metadata: Metadata = { title: "Consent settings · CareerLaunch" };

// `?next=` (a safe local path only, e.g. /settings/provider-key) is where the Student returns
// after agreeing to `?purpose=` (e.g. provider_runs); both are required. The search params are read inside <Suspense> so the page shell can still be
// prerendered.
async function ConsentFromSearchParams({
  searchParams,
}: {
  searchParams: PageProps<"/settings/consent">["searchParams"];
}) {
  const { next, purpose } = await searchParams;
  const returnTo = consentReturnTarget(
    typeof next === "string" ? next : null,
    typeof purpose === "string" ? purpose : null,
  );
  return (
    <>
      {returnTo ? (
        <p className="text-sm" data-testid="consent-return-note">
          After you agree to this purpose, you will go back to where you were.
        </p>
      ) : null}
      <ConsentPanel allowWithdraw returnTo={returnTo} />
    </>
  );
}

// Protected by the proxy (signed-out visitors go to /login).
export default function ConsentSettingsPage(props: PageProps<"/settings/consent">) {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Link className="text-sm text-muted-foreground underline" href={SETTINGS_PATH}>
            ‹ Back
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Consent</h1>
        </div>
        <SignOutButton />
      </div>
      <p className="text-sm text-muted-foreground">
        Agree to or withdraw each consent at any time. Withdrawing stops the work that depends
        on it.
      </p>
      <Suspense fallback={<p className="text-muted-foreground">Loading consent options…</p>}>
        <ConsentFromSearchParams searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}
