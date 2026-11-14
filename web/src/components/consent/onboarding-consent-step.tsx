"use client";

import Link from "next/link";
import { useState } from "react";

import { ResumeManager } from "@/components/resumes/resume-manager";
import { Button } from "@/components/ui/button";
import { SETTINGS_CONSENT_PATH } from "@/lib/consent";
import { SETTINGS_RESUMES_PATH } from "@/lib/resumes";

import { ConsentPanel } from "./consent-panel";

/**
 * Onboarding step 1: consent, then step 2: Resume. Consent is optional here (the gate blocks
 * the work itself, not the UI), so "Continue" always proceeds to the Resume step.
 */
export function OnboardingConsentStep() {
  const [done, setDone] = useState(false);

  if (done) {
    return (
      <div className="flex flex-col gap-6" data-testid="onboarding-resume">
        <div className="flex flex-col gap-1">
          <p className="text-sm text-muted-foreground">Step 2 · Resume</p>
          <h2 className="text-lg font-medium">Add your Resume</h2>
          <p className="text-sm text-muted-foreground">
            Upload the Resume you want CareerLaunch to use. You can add more versions and change
            the active one later in{" "}
            <Link className="underline" href={SETTINGS_RESUMES_PATH}>
              Settings › Resumes
            </Link>
            .
          </p>
        </div>
        <ResumeManager />
        <div>
          <Button type="button" variant="outline" onClick={() => setDone(false)}>
            Back to consent
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted-foreground">Step 1 · Consent</p>
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
