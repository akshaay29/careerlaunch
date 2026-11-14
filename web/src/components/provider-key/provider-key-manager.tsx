"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import {
  deleteProviderKey,
  getProviderKey,
  listConsents,
  type ProviderKeyMeta,
  putProviderKey,
} from "@/api";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth-paths";
import {
  hasProviderRunsConsent,
  maskedHint,
  precheckToken,
  PROVIDER_KEY_CONSENT_REDIRECT,
  saveFailure,
} from "@/lib/provider-key";

type Load =
  | { kind: "loading" }
  | { kind: "ok"; meta: ProviderKeyMeta; consented: boolean }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

async function fetchMeta(): Promise<Load> {
  try {
    // The consent status decides whether the paste form is offered at all, so a token is
    // never typed only to be lost on the redirect to the consent page.
    const [key, consents] = await Promise.all([getProviderKey(), listConsents()]);
    if (key.response?.status === 401 || consents.response?.status === 401) {
      return { kind: "signed-out" };
    }
    if (key.data && consents.data) {
      return {
        kind: "ok",
        meta: key.data,
        consented: hasProviderRunsConsent(consents.data.purposes),
      };
    }
    const status = key.data ? consents.response?.status : key.response?.status;
    return { kind: "error", message: `HTTP ${status ?? "?"}` };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

function formatTime(iso: string | null): string | null {
  return iso ? new Date(iso).toLocaleString() : null;
}

/**
 * Add, replace or delete the Student's Apify token (FR-28). The token is sent once, on save,
 * and never shown again: after saving, only the masked last-4 hint is displayed.
 */
export function ProviderKeyManager({ onChanged }: { onChanged?: () => void } = {}) {
  const router = useRouter();
  const inputId = useId();
  const errorId = `${inputId}-error`;
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [token, setToken] = useState("");
  const [replacing, setReplacing] = useState(false);
  const [pending, setPending] = useState(false);
  // Synchronous guard: a second click can arrive before `pending` re-renders.
  const busy = useRef(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchMeta().then((next) => {
      if (active) setLoad(next);
    });
    return () => {
      active = false;
    };
  }, []);

  if (load.kind === "loading") {
    return <p className="text-muted-foreground">Loading your Provider Key…</p>;
  }
  if (load.kind === "signed-out") {
    return (
      <p className="text-sm">
        Your session has ended.{" "}
        <Link className="underline" href={LOGIN_PATH}>
          Log in again
        </Link>
      </p>
    );
  }
  if (load.kind === "error") {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load your Provider Key ({load.message}).
      </p>
    );
  }

  const { meta, consented } = load;
  const showForm = !meta.has_key || replacing || meta.needs_reentry;
  const validatedAt = formatTime(meta.validated_at);

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || busy.current) return;
    setNotice(null);
    setFormError(null);
    const precheck = precheckToken(token);
    if (precheck) {
      setFieldError(precheck);
      return;
    }
    setFieldError(null);
    busy.current = true;
    setPending(true);
    try {
      const { data, error, response } = await putProviderKey({ body: { token: token.trim() } });
      if (data) {
        setToken("");
        setReplacing(false);
        setLoad({ kind: "ok", meta: data, consented: true });
        setNotice("Your token was checked with Apify and saved.");
        onChanged?.(); // saving a key clears a pause: the usage section must refetch
      } else if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
      } else {
        const outcome = saveFailure(response?.status, error);
        if (outcome.kind === "consent-required") {
          // Consent was withdrawn meanwhile (the form is only offered with consent).
          busy.current = false;
          router.push(outcome.redirect);
          return;
        }
        if (outcome.kind === "field") setFieldError(outcome.message);
        else setFormError(outcome.message);
      }
    } catch {
      setFormError("Could not reach the server. Try again.");
    }
    busy.current = false;
    setPending(false);
  }

  async function onDelete() {
    if (pending || busy.current) return;
    busy.current = true;
    setPending(true);
    setNotice(null);
    setFormError(null);
    try {
      const { response } = await deleteProviderKey();
      if (response?.status === 204) {
        setReplacing(false);
        setToken("");
        setLoad({
          kind: "ok",
          meta: {
            provider: "apify",
            has_key: false,
            last4: null,
            validated_at: null,
            needs_reentry: false,
          },
          consented,
        });
        setNotice("Your token was deleted. Collection under your key has stopped.");
        onChanged?.();
      } else if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
      } else {
        setFormError("Could not delete your token. Try again.");
      }
    } catch {
      setFormError("Could not reach the server. Try again.");
    }
    busy.current = false;
    setPending(false);
  }

  return (
    <section className="flex flex-col gap-4 rounded-lg border p-5" data-testid="provider-key">
      {notice ? (
        <p role="status" className="text-sm">
          {notice}
        </p>
      ) : null}

      {meta.has_key ? (
        <div className="flex flex-col gap-1">
          <p className="text-sm">
            Saved token:{" "}
            <span className="font-mono" data-testid="provider-key-hint">
              {maskedHint(meta.last4)}
            </span>
          </p>
          {validatedAt ? (
            <p className="text-xs text-muted-foreground">Checked with Apify {validatedAt}</p>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No token saved yet.</p>
      )}

      {meta.needs_reentry ? (
        <p role="alert" className="text-sm text-destructive" data-testid="provider-key-reentry">
          Re-enter your key: your saved token can no longer be read, so collection cannot use it.
        </p>
      ) : null}

      {showForm && !consented ? (
        <p className="text-sm" data-testid="provider-key-consent-first">
          Agree to provider runs first: CareerLaunch needs your consent before it can run Apify
          for you.{" "}
          <Link className="underline" href={PROVIDER_KEY_CONSENT_REDIRECT}>
            Review the provider runs consent
          </Link>
        </p>
      ) : null}

      {showForm && consented ? (
        <form className="flex flex-col gap-2" onSubmit={onSave} noValidate>
          <label htmlFor={inputId} className="text-sm font-medium">
            {meta.has_key ? "New Apify token" : "Apify token"}
          </label>
          <input
            id={inputId}
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={token}
            onChange={(e) => {
              setToken(e.target.value);
              setFieldError(null);
            }}
            aria-invalid={fieldError ? true : undefined}
            aria-describedby={fieldError ? errorId : undefined}
            disabled={pending}
            className="rounded-md border bg-background px-3 py-2 font-mono text-sm"
          />
          {fieldError ? (
            <p id={errorId} role="alert" className="text-sm text-destructive">
              {fieldError}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={pending}>
              {pending ? "Checking with Apify…" : "Save"}
            </Button>
            {replacing ? (
              <Button
                type="button"
                variant="outline"
                disabled={pending}
                onClick={() => {
                  setReplacing(false);
                  setToken("");
                  setFieldError(null);
                }}
              >
                Cancel
              </Button>
            ) : null}
          </div>
        </form>
      ) : null}

      {formError ? (
        <p role="alert" className="text-sm text-destructive">
          {formError}
        </p>
      ) : null}

      {meta.has_key && !replacing ? (
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" disabled={pending} onClick={() => setReplacing(true)}>
            Replace
          </Button>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button type="button" variant="destructive" disabled={pending}>
                Delete
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete your Apify token?</AlertDialogTitle>
                <AlertDialogDescription>
                  CareerLaunch will stop collecting Listings under your Apify account. You can
                  still browse the shared pool, and you can add a token again later.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={onDelete}>Delete</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      ) : null}
    </section>
  );
}
