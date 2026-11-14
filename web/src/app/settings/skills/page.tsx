import type { Metadata } from "next";
import Link from "next/link";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { SkillProfileEditor } from "@/components/skills/skill-profile-editor";
import { ONBOARDING_PATH } from "@/lib/auth-paths";

export const metadata: Metadata = { title: "Skills · CareerLaunch" };

// Protected by the proxy (signed-out visitors go to /login).
export default function SkillSettingsPage() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Link className="text-sm text-muted-foreground underline" href={ONBOARDING_PATH}>
            ‹ Back
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Skills</h1>
        </div>
        <SignOutButton />
      </div>
      <p className="text-sm text-muted-foreground">
        Your Skill Profile is read from your active Resume. Your changes are kept when you
        upload a new Resume, until you choose &ldquo;Reset to extracted&rdquo;.
      </p>
      <SkillProfileEditor />
    </main>
  );
}
