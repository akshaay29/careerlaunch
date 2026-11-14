"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";

import { deleteMe, getMe } from "@/api";
import { Button } from "@/components/ui/button";
import { confirmationMatches, confirmEmailError } from "@/lib/account-deletion";
import { ACCOUNT_DELETED_PATH, LOGIN_PATH } from "@/lib/auth-paths";
import { problemDetail } from "@/lib/resumes";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

type Load =
  | { kind: "loading" }
  | { kind: "ok"; email: string }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

const FAILURE = "Could not delete your account. Try again.";

/**
 * The "Delete my account" danger section (FR-2). The Student types their account email; the
 * button stays disabled until it matches (ignoring case and surrounding spaces), and the
 * server checks it again. On success the browser session is cleared and the Student lands
 * on the public /account-deleted page.
 */
export function DeleteAccount() {
  const router = useRouter();
  const inputId = useId();
  const errorId = `${inputId}-error`;
  const titleId = `${inputId}-title`;
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getMe()
      .then(({ data, response }) => {
        if (!active) return;
        if (data) setLoad({ kind: "ok", email: data.email });
        else if (response?.status === 401) setLoad({ kind: "signed-out" });
        else setLoad({ kind: "error", message: `HTTP ${response?.status ?? "?"}` });
      })
      .catch(() => {
        if (active) setLoad({ kind: "error", message: "API unreachable" });
      });
    return () => {
      active = false;
    };
  }, []);

  if (load.kind === "loading") {
    return <p className="text-muted-foreground">Loading your account…</p>;
  }
  if (load.kind === "signed-out") {
    return (
      <p className="text-sm">
        You are signed out.{" "}
        <Link className="underline" href={LOGIN_PATH}>
          Log in
        </Link>
      </p>
    );
  }
  if (load.kind === "error") {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load your account ({load.message}).
      </p>
    );
  }

  const accountEmail = load.email;
  const matches = confirmationMatches(typed, accountEmail);

  async function onDelete() {
    if (!matches || pending) return;
    setPending(true);
    setFieldError(null);
    setFormError(null);
    try {
      const { response, error } = await deleteMe({ body: { confirm_email: typed } });
      if (response?.status === 204) {
        try {
          // The account is gone: clear the cookie session locally (no server call needed).
          await getSupabaseBrowserClient().auth.signOut({ scope: "local" });
        } catch {
          // The API refuses these tokens now anyway; go on to the goodbye page.
        }
        router.replace(ACCOUNT_DELETED_PATH);
        router.refresh();
        return;
      }
      if (response?.status === 422) {
        setFieldError(confirmEmailError(error) ?? problemDetail(error, FAILURE));
      } else if (response?.status === 401) {
        setFormError("Your session has ended. Log in again to delete your account.");
      } else {
        setFormError(problemDetail(error, FAILURE, response?.status));
      }
    } catch {
      setFormError("Could not reach the server. Try again.");
    }
    setPending(false);
  }

  return (
    <section
      aria-labelledby={titleId}
      className="flex flex-col gap-4 rounded-lg border border-destructive/50 p-5"
      data-testid="delete-account"
    >
      <h2 id={titleId} className="text-lg font-semibold text-destructive">
        Delete my account
      </h2>
      <div className="flex flex-col gap-2 text-sm">
        <p>Deleting your account permanently removes:</p>
        <ul className="list-disc pl-5">
          <li>your account and your sign-in</li>
          <li>your consents, Preferences and Skill Profile</li>
          <li>every Resume version and every Document you stored</li>
        </ul>
        <p>
          Stored files and your sign-in are usually removed within minutes. If that fails, we
          retry automatically every day until they are gone.{" "}
          <strong>This cannot be undone.</strong> There is no grace period, and nothing can be
          restored.
        </p>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={inputId} className="text-sm font-medium">
          To confirm, type your email address: <span className="font-mono">{accountEmail}</span>
        </label>
        <input
          id={inputId}
          type="email"
          autoComplete="off"
          spellCheck={false}
          value={typed}
          onChange={(e) => {
            setTyped(e.target.value);
            setFieldError(null);
          }}
          aria-invalid={fieldError ? true : undefined}
          aria-describedby={fieldError ? errorId : undefined}
          disabled={pending}
          className="rounded-md border bg-background px-3 py-2 text-sm"
        />
        {fieldError ? (
          <p id={errorId} role="alert" className="text-sm text-destructive">
            {fieldError}
          </p>
        ) : null}
      </div>
      {formError ? (
        <p role="alert" className="text-sm text-destructive">
          {formError}
        </p>
      ) : null}
      <div>
        <Button
          type="button"
          variant="destructive"
          onClick={onDelete}
          disabled={!matches || pending}
        >
          {pending ? "Deleting…" : "Delete my account"}
        </Button>
      </div>
    </section>
  );
}
