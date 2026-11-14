"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

import { authRedirects } from "./auth-redirects";

export function GoogleButton({ next }: { next?: string | null }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onClick() {
    setPending(true);
    setError(null);
    let failed = true;
    try {
      const { error } = await getSupabaseBrowserClient().auth.signInWithOAuth({
        provider: "google",
        options: { redirectTo: authRedirects(next).callback },
      });
      // On success the browser is already navigating to Google.
      failed = Boolean(error);
    } catch {
      failed = true;
    }
    if (failed) {
      setError("Google sign-in is unavailable right now. Try email and password.");
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <Button type="button" variant="outline" size="lg" onClick={onClick} disabled={pending}>
        Continue with Google
      </Button>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
