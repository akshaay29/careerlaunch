import type { Metadata } from "next";
import Link from "next/link";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { ResumeManager } from "@/components/resumes/resume-manager";
import { ONBOARDING_PATH } from "@/lib/auth-paths";

export const metadata: Metadata = { title: "Resumes · CareerLaunch" };

// Protected by the proxy (signed-out visitors go to /login).
export default function ResumeSettingsPage() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Link className="text-sm text-muted-foreground underline" href={ONBOARDING_PATH}>
            ‹ Back
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Resumes</h1>
        </div>
        <SignOutButton />
      </div>
      <p className="text-sm text-muted-foreground">
        Upload new versions, choose which one is active, download or delete them. Only you can
        see your Resumes.
      </p>
      <ResumeManager />
    </main>
  );
}
