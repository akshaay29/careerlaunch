import type { Metadata } from "next";
import Link from "next/link";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { PreferencesEditor } from "@/components/preferences/preferences-editor";
import { SETTINGS_PATH } from "@/lib/auth-paths";

export const metadata: Metadata = { title: "Preferences · CareerLaunch" };

// Protected by the proxy (signed-out visitors go to /login).
export default function PreferencesSettingsPage() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Link className="text-sm text-muted-foreground underline" href={SETTINGS_PATH}>
            ‹ Back
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Preferences</h1>
        </div>
        <SignOutButton />
      </div>
      <p className="text-sm text-muted-foreground">
        What you&apos;re looking for, your eligibility details, and how you hear about
        matches. Everything is optional.
      </p>
      <PreferencesEditor />
    </main>
  );
}
