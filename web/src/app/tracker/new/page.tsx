import type { Metadata } from "next";
import Link from "next/link";

import { SignOutButton } from "@/components/auth/sign-out-button";
import { AddExternalJobForm } from "@/components/tracker/add-external-job-form";
import { TRACKER_PATH } from "@/lib/auth-paths";

export const metadata: Metadata = { title: "Add external job · CareerLaunch" };

// Protected by the proxy (signed-out visitors go to /login). The job is sent from the browser
// with the Student's token.
export default function AddExternalJobPage() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-6 py-16">
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Link className="text-sm text-muted-foreground underline" href={TRACKER_PATH}>
            ‹ My Tracker
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Add external job</h1>
        </div>
        <SignOutButton />
      </div>
      <p className="text-sm text-muted-foreground">
        Found a job on a company site or another board? Add it here to track it with the rest.
        Only you can see it.
      </p>
      <AddExternalJobForm />
    </main>
  );
}
