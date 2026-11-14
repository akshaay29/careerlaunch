import type { Metadata } from "next";
import Link from "next/link";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { ProviderKeyManager } from "@/components/provider-key/provider-key-manager";
import { SETTINGS_PATH } from "@/lib/auth-paths";
import { APIFY_CONSOLE_URL, APIFY_TOKENS_URL } from "@/lib/provider-key";

export const metadata: Metadata = { title: "Provider Key · CareerLaunch" };

// Protected by the proxy (signed-out visitors go to /login).
export default function ProviderKeySettingsPage() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Link className="text-sm text-muted-foreground underline" href={SETTINGS_PATH}>
            ‹ Back
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Provider Key</h1>
        </div>
        <SignOutButton />
      </div>
      <p className="text-sm text-muted-foreground">
        CareerLaunch collects fresh LinkedIn Listings for you through your own Apify account,
        so the runs are billed to that account. Your token is checked with Apify, stored
        encrypted, and never shown again. You can browse the shared pool without one.
      </p>
      <section className="flex flex-col gap-3 rounded-lg border p-5" data-testid="provider-key-steps">
        <h2 className="text-lg font-medium">Set it up in three steps</h2>
        <ol className="flex list-decimal flex-col gap-2 pl-5 text-sm">
          <li>
            Create a free Apify account at{" "}
            <a
              className="underline"
              href={APIFY_CONSOLE_URL}
              target="_blank"
              rel="noopener noreferrer"
            >
              the Apify console
            </a>
            .
          </li>
          <li>
            In{" "}
            <a
              className="underline"
              href={APIFY_TOKENS_URL}
              target="_blank"
              rel="noopener noreferrer"
            >
              Settings › API &amp; Integrations
            </a>
            , create a scoped token: give it the Run permission on the approved actors only,
            choose restricted access, and set an expiry date.
          </li>
          <li>Paste the token below and save it.</li>
        </ol>
        <p className="text-xs text-muted-foreground">
          Apify&apos;s free plan includes USD 5 of credit each month.
        </p>
      </section>
      <ProviderKeyManager />
    </main>
  );
}
