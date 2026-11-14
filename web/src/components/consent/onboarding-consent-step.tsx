"use client";

import Link from "next/link";
import { useState } from "react";

import { ResumeManager } from "@/components/resumes/resume-manager";
import { SkillProfileEditor } from "@/components/skills/skill-profile-editor";
import { Button } from "@/components/ui/button";
import { SETTINGS_CONSENT_PATH } from "@/lib/consent";
import { SETTINGS_RESUMES_PATH } from "@/lib/resumes";
import { SETTINGS_SKILLS_PATH } from "@/lib/skill-profile";

import { ConsentPanel } from "./consent-panel";

type Step = "consent" | "resume" | "skills";

/**
 * Onboarding: step 1 consent, step 2 Resume, step 3 Skill Profile. Consent is optional here
 * (the gate blocks the work itself, not the UI), so "Continue" always proceeds. The step
 * state lives here.
 */
export function OnboardingConsentStep() {
  const [step, setStep] = useState<Step>("consent");

  if (step === "skills") {
    return (
      <div className="flex flex-col gap-6" data-testid="onboarding-skills">
        <div className="flex flex-col gap-1">
          <p className="text-sm text-muted-foreground">Step 3 · Skill Profile</p>
          <h2 className="text-lg font-medium">Check your skills</h2>
          <p className="text-sm text-muted-foreground">
            These skills were read from your active Resume. Remove any that are wrong and add
            any that are missing. You can change them later in{" "}
            <Link className="underline" href={SETTINGS_SKILLS_PATH}>
              Settings › Skills
            </Link>
            .
          </p>
        </div>
        <SkillProfileEditor />
        <div>
          <Button type="button" variant="outline" onClick={() => setStep("resume")}>
            Back to Resume
          </Button>
        </div>
      </div>
    );
  }

  if (step === "resume") {
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
        <div className="flex flex-wrap justify-between gap-2">
          <Button type="button" variant="outline" onClick={() => setStep("consent")}>
            Back to consent
          </Button>
          <Button
            type="button"
            onClick={() => setStep("skills")}
            data-testid="onboarding-continue-skills"
          >
            Continue
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
        <Button type="button" onClick={() => setStep("resume")} data-testid="onboarding-continue">
          Continue
        </Button>
      </div>
    </div>
  );
}
