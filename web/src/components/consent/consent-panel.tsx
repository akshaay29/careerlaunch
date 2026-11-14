"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  type ConsentOverview,
  type ConsentPurpose,
  type ConsentStatus,
  grantConsent,
  listConsents,
  revokeConsent,
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
import { canAgree, canWithdraw, consentStatusLabel } from "@/lib/consent";

type Load =
  | { kind: "loading" }
  | { kind: "ok"; overview: ConsentOverview }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

async function fetchOverview(): Promise<Load> {
  try {
    const { data, response } = await listConsents();
    if (data) return { kind: "ok", overview: data };
    if (response?.status === 401) return { kind: "signed-out" };
    return { kind: "error", message: `HTTP ${response?.status ?? "?"}` };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

function formatTime(iso: string | null): string | null {
  return iso ? new Date(iso).toLocaleString() : null;
}

/**
 * The consent texts, the Student's status on each, and the DPDP rights notice.
 * Used by /onboarding (agree only) and /settings/consent (agree and withdraw).
 * All text is rendered as plain text.
 */
export function ConsentPanel({ allowWithdraw = false }: { allowWithdraw?: boolean }) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [busy, setBusy] = useState<ConsentPurpose | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => setLoad(await fetchOverview()), []);

  useEffect(() => {
    let active = true;
    fetchOverview().then((next) => {
      if (active) setLoad(next);
    });
    return () => {
      active = false;
    };
  }, []);

  async function agree(item: ConsentStatus) {
    setBusy(item.purpose);
    setMessage(null);
    try {
      const { data, response } = await grantConsent({
        path: { purpose: item.purpose },
        body: { version: item.version },
      });
      if (data) {
        setLoad((prev) =>
          prev.kind === "ok"
            ? {
                kind: "ok",
                overview: {
                  ...prev.overview,
                  purposes: prev.overview.purposes.map((p) =>
                    p.purpose === data.purpose ? data : p,
                  ),
                },
              }
            : prev,
        );
      } else if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
        return;
      } else if (response?.status === 409) {
        setMessage("This text has just been updated. Please read the new version and agree again.");
        await refresh();
      } else {
        setMessage("Could not save your choice. Try again.");
      }
    } catch {
      setMessage("Could not save your choice. Try again.");
    } finally {
      setBusy(null);
    }
  }

  async function withdraw(item: ConsentStatus) {
    setBusy(item.purpose);
    setMessage(null);
    try {
      const { response } = await revokeConsent({ path: { purpose: item.purpose } });
      if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
        return;
      }
      if (response?.ok) await refresh();
      else setMessage("Could not withdraw your consent. Try again.");
    } catch {
      setMessage("Could not withdraw your consent. Try again.");
    } finally {
      setBusy(null);
    }
  }

  if (load.kind === "loading") {
    return <p className="text-muted-foreground">Loading consent options…</p>;
  }
  if (load.kind === "signed-out") {
    return (
      <p>
        Your session has ended.{" "}
        <Link className="underline" href={LOGIN_PATH}>
          Log in again
        </Link>
      </p>
    );
  }
  if (load.kind === "error") {
    return (
      <p role="alert" className="text-destructive">
        Could not load your consent options ({load.message}).
      </p>
    );
  }

  const { overview } = load;
  return (
    <div className="flex flex-col gap-6">
      {message ? (
        <p role="alert" className="text-sm text-destructive">
          {message}
        </p>
      ) : null}

      {overview.purposes.map((item) => {
        const grantedAt = formatTime(item.granted_at);
        const revokedAt = formatTime(item.revoked_at);
        return (
          <section
            key={item.purpose}
            className="flex flex-col gap-3 rounded-lg border p-5"
            data-testid={`consent-${item.purpose}`}
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <h2 className="text-lg font-medium">{item.title}</h2>
              <span
                className="rounded-full border px-2 py-0.5 text-xs"
                data-testid={`consent-status-${item.purpose}`}
              >
                {consentStatusLabel(item.status)}
              </span>
            </div>
            {item.body.map((paragraph, i) => (
              <p key={i} className="text-sm leading-relaxed">
                {paragraph}
              </p>
            ))}
            <p className="text-xs text-muted-foreground">
              Version {item.version}
              {item.status === "granted" && grantedAt ? ` · agreed ${grantedAt}` : null}
              {item.status === "revoked" && revokedAt ? ` · withdrawn ${revokedAt}` : null}
            </p>
            <div className="flex flex-wrap gap-2">
              {canAgree(item.status) ? (
                <Button type="button" onClick={() => agree(item)} disabled={busy !== null}>
                  I agree
                </Button>
              ) : null}
              {allowWithdraw && canWithdraw(item.status) ? (
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button type="button" variant="destructive" disabled={busy !== null}>
                      Withdraw consent
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Withdraw this consent?</AlertDialogTitle>
                      <AlertDialogDescription>
                        {`You are withdrawing consent for "${item.title}". Work that depends on it will stop. You can agree again later.`}
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction onClick={() => withdraw(item)}>Withdraw</AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              ) : null}
            </div>
          </section>
        );
      })}

      <section className="flex flex-col gap-2 rounded-lg bg-muted/50 p-5" data-testid="rights-notice">
        <h2 className="text-base font-medium">{overview.rights.title}</h2>
        <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
          {overview.rights.body.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">Notice version {overview.notice_version}</p>
      </section>
    </div>
  );
}
