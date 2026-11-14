"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { LOGIN_PATH, SIGNUP_PATH } from "@/lib/auth-paths";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

import { authRedirects } from "./auth-redirects";
import { GoogleButton } from "./google-button";
import { ResendConfirmation } from "./resend-confirmation";

type Mode = "login" | "signup";
type State =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "error"; message: string; unconfirmed?: boolean }
  | { kind: "check-email"; email: string };

const inputClass = "h-9 rounded-lg border border-input bg-background px-3";

export function AuthForm({ mode }: { mode: Mode }) {
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState({ kind: "pending" });
    try {
      await submit();
    } catch {
      setState({ kind: "error", message: "Something went wrong. Try again in a moment." });
    }
  }

  async function submit() {
    const supabase = getSupabaseBrowserClient();
    const redirects = authRedirects(next);
    const address = email.trim();

    if (mode === "signup") {
      const { data, error } = await supabase.auth.signUp({
        email: address,
        password,
        options: { emailRedirectTo: redirects.callback },
      });
      if (error) {
        setState({ kind: "error", message: error.message });
        return;
      }
      if (!data.session) {
        // Email confirmation is required: no session until the link is clicked.
        setState({ kind: "check-email", email: address });
        return;
      }
    } else {
      const { error } = await supabase.auth.signInWithPassword({ email: address, password });
      if (error) {
        const unconfirmed = error.code === "email_not_confirmed";
        setState({
          kind: "error",
          unconfirmed,
          message: unconfirmed
            ? "Confirm your email first. Use the link we sent you, or send a new one."
            : "Wrong email or password.",
        });
        return;
      }
    }
    router.replace(redirects.next);
    router.refresh();
  }

  if (state.kind === "check-email") {
    return (
      <section className="flex flex-col gap-4" aria-live="polite">
        <h2 className="text-xl font-semibold">Check your email</h2>
        <p className="text-sm text-muted-foreground">
          We sent a confirmation link to <span className="font-medium">{state.email}</span>. Open
          it to finish signing up.
        </p>
        <ResendConfirmation initialEmail={state.email} />
      </section>
    );
  }

  const pending = state.kind === "pending";
  return (
    <div className="flex flex-col gap-6">
      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Email
          <input
            type="email"
            name="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Password
          <input
            type="password"
            name="password"
            required
            minLength={mode === "signup" ? 8 : undefined}
            autoComplete={mode === "signup" ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
          />
        </label>
        <Button type="submit" size="lg" disabled={pending}>
          {mode === "signup" ? "Sign up" : "Log in"}
        </Button>
        {state.kind === "error" ? (
          <p role="alert" className="text-sm text-destructive">
            {state.message}
          </p>
        ) : null}
      </form>
      {state.kind === "error" && state.unconfirmed ? (
        <ResendConfirmation initialEmail={email.trim()} />
      ) : null}
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="h-px flex-1 bg-border" />
        or
        <span className="h-px flex-1 bg-border" />
      </div>
      <GoogleButton next={next} />
      <p className="text-sm text-muted-foreground">
        {mode === "signup" ? (
          <>
            Already have an account?{" "}
            <Link className="underline" href={LOGIN_PATH}>
              Log in
            </Link>
          </>
        ) : (
          <>
            New to CareerLaunch?{" "}
            <Link className="underline" href={SIGNUP_PATH}>
              Sign up
            </Link>
          </>
        )}
      </p>
    </div>
  );
}
