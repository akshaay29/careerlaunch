"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

import { authRedirects } from "./auth-redirects";

type State = "idle" | "sending" | "sent" | "error";

/** "Resend the confirmation email" for an email/password sign-up. */
export function ResendConfirmation({ initialEmail = "" }: { initialEmail?: string }) {
  const [email, setEmail] = useState(initialEmail);
  const [state, setState] = useState<State>("idle");

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState("sending");
    try {
      const { error } = await getSupabaseBrowserClient().auth.resend({
        type: "signup",
        email: email.trim(),
        options: { emailRedirectTo: authRedirects().callback },
      });
      setState(error ? "error" : "sent");
    } catch {
      setState("error");
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-2" aria-label="Resend confirmation email">
      <label className="flex flex-col gap-1 text-sm">
        Email
        <input
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="h-9 rounded-lg border border-input bg-background px-3"
        />
      </label>
      <Button type="submit" variant="secondary" disabled={state === "sending"}>
        Resend confirmation email
      </Button>
      {state === "sent" ? (
        <p role="status" className="text-sm text-muted-foreground">
          If that address has a pending sign-up, a new link is on its way.
        </p>
      ) : null}
      {state === "error" ? (
        <p role="alert" className="text-sm text-destructive">
          Could not send the email. Wait a minute and try again.
        </p>
      ) : null}
    </form>
  );
}
