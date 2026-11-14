import type { Metadata } from "next";
import Link from "next/link";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { SETTINGS_ACCOUNT_PATH } from "@/lib/account-deletion";
import { ONBOARDING_PATH } from "@/lib/auth-paths";
import { SETTINGS_CONSENT_PATH } from "@/lib/consent";
import { SETTINGS_DOCUMENTS_PATH } from "@/lib/documents";
import { SETTINGS_PREFERENCES_PATH } from "@/lib/preferences";
import { SETTINGS_RESUMES_PATH } from "@/lib/resumes";
import { SETTINGS_SKILLS_PATH } from "@/lib/skill-profile";

export const metadata: Metadata = { title: "Settings · CareerLaunch" };

const PAGES = [
  {
    href: SETTINGS_CONSENT_PATH,
    title: "Consent",
    description: "Agree to or withdraw consent for provider runs and the shared pool.",
  },
  {
    href: SETTINGS_RESUMES_PATH,
    title: "Resumes",
    description: "Upload Resume versions and choose the active one.",
  },
  {
    href: SETTINGS_DOCUMENTS_PATH,
    title: "Documents",
    description: "Store cover letters, transcripts and certificates, and download them.",
  },
  {
    href: SETTINGS_SKILLS_PATH,
    title: "Skill Profile",
    description: "Check the skills read from your Resume and add or remove skills.",
  },
  {
    href: SETTINGS_PREFERENCES_PATH,
    title: "Preferences",
    description: "Roles, locations, eligibility, Match Score threshold, alerts and time zone.",
  },
  {
    href: SETTINGS_ACCOUNT_PATH,
    title: "Account",
    description: "Delete your account and all of your data.",
  },
];

// Protected by the proxy (signed-out visitors go to /login). Every settings page's "Back"
// link comes here.
export default function SettingsPage() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Link className="text-sm text-muted-foreground underline" href={ONBOARDING_PATH}>
            ‹ Onboarding
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        </div>
        <SignOutButton />
      </div>
      <ul className="flex flex-col gap-3" data-testid="settings-hub">
        {PAGES.map((page) => (
          <li key={page.href}>
            <Link
              href={page.href}
              className="flex flex-col gap-1 rounded-lg border p-5 hover:bg-muted/50"
            >
              <span className="font-medium">{page.title}</span>
              <span className="text-sm text-muted-foreground">{page.description}</span>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
