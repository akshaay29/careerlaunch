"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getMe, type Me } from "@/api";
import { LOGIN_PATH } from "@/lib/auth-paths";

type State =
  | { kind: "loading" }
  | { kind: "ok"; me: Me }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

/** Shows GET /api/v1/me (the bearer token comes from the cookie session). */
export function MeCard() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    getMe()
      .then(({ data, response }) => {
        if (!active) return;
        if (data) setState({ kind: "ok", me: data });
        else if (response?.status === 401) setState({ kind: "signed-out" });
        else setState({ kind: "error", message: `HTTP ${response?.status ?? "?"}` });
      })
      .catch(() => {
        if (active) setState({ kind: "error", message: "API unreachable" });
      });
    return () => {
      active = false;
    };
  }, []);

  if (state.kind === "loading") {
    return <p className="text-muted-foreground">Loading your account…</p>;
  }
  if (state.kind === "signed-out") {
    return (
      <p>
        Your session has ended.{" "}
        <Link className="underline" href={LOGIN_PATH}>
          Log in again
        </Link>
      </p>
    );
  }
  if (state.kind === "error") {
    return (
      <p role="alert" className="text-destructive">
        Could not load your account ({state.message}).
      </p>
    );
  }
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1" data-testid="me">
      <dt className="text-muted-foreground">Email</dt>
      <dd>{state.me.email}</dd>
      <dt className="text-muted-foreground">Role</dt>
      <dd data-testid="me-role">{state.me.role}</dd>
      <dt className="text-muted-foreground">Account id</dt>
      <dd className="font-mono text-xs">{state.me.id}</dd>
    </dl>
  );
}
