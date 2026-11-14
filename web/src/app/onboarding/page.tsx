import type { Metadata } from "next";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { OnboardingConsentStep } from "@/components/consent/onboarding-consent-step";

export const metadata: Metadata = { title: "Onboarding · CareerLaunch" };

// The proxy sends signed-out visitors to /login. Step 1 is consent (Story 1.4); Resume, Skill
// Profile and Preferences steps arrive in Stories 1.5-1.7.
export default function OnboardingPage() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <p className="text-sm text-muted-foreground">Step 1 · Consent</p>
          <h1 className="text-2xl font-semibold tracking-tight">Welcome to CareerLaunch</h1>
        </div>
        <SignOutButton />
      </div>
      <OnboardingConsentStep />
    </main>
  );
}
