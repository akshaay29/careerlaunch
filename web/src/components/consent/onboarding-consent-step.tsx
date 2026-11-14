"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { SETTINGS_CONSENT_PATH } from "@/lib/consent";

import { ConsentPanel } from "./consent-panel";

/**
 * Onboarding step 1: consent. Consent is optional here (the gate blocks the work itself, not
 * the UI), so "Continue" always proceeds.
 */
export function OnboardingConsentStep() {
  const [done, setDone] = useState(false);

  if (done) {
    return (
      <section className="flex flex-col gap-3 rounded-lg border p-5" data-testid="onboarding-next">
        <h2 className="text-lg font-medium">Next: your Resume</h2>
        <p className="text-sm text-muted-foreground">
          Resume upload is the next onboarding step and is coming soon. You can review or change
          your consent choices at any time in{" "}
          <Link className="underline" href={SETTINGS_CONSENT_PATH}>
            Settings › Consent
          </Link>
          .
        </p>
        <div>
          <Button type="button" variant="outline" onClick={() => setDone(false)}>
            Back to consent
          </Button>
        </div>
      </section>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted-foreground">
        Read each item and choose &ldquo;I agree&rdquo; for the ones you accept. Both are
        optional, and you can change them later in{" "}
        <Link className="underline" href={SETTINGS_CONSENT_PATH}>
          Settings › Consent
        </Link>
        .
      </p>
      <ConsentPanel />
      <div className="flex justify-end">
        <Button type="button" onClick={() => setDone(true)} data-testid="onboarding-continue">
          Continue
        </Button>
      </div>
    </div>
  );
}
