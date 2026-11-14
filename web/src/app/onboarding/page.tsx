import type { Metadata } from "next";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { MeCard } from "@/components/me-card";

export const metadata: Metadata = { title: "Onboarding · CareerLaunch" };

// Placeholder (Story 1.3): the proxy sends signed-out visitors to /login. Consent, Resume,
// Skill Profile and Preferences steps arrive in Stories 1.4-1.7.
export default function OnboardingPage() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Welcome to CareerLaunch</h1>
        <SignOutButton />
      </div>
      <MeCard />
    </main>
  );
}
