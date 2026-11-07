"use client";

import { useEffect, useState } from "react";

import { getHealth } from "@/api";
import { Button } from "@/components/ui/button";
import { API_URL } from "@/lib/api-client";

type State = { kind: "loading" } | { kind: "ok" } | { kind: "error"; message: string };

async function fetchHealth(): Promise<State> {
  try {
    const { data, response } = await getHealth();
    if (data?.status === "ok") return { kind: "ok" };
    return { kind: "error", message: `Unexpected response (HTTP ${response?.status ?? "?"})` };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

export function HealthStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    fetchHealth().then((next) => {
      if (active) setState(next);
    });
    return () => {
      active = false;
    };
  }, [attempt]);

  const label =
    state.kind === "loading" ? "Checking…" : state.kind === "ok" ? "ok" : state.message;

  return (
    <div className="flex flex-col items-start gap-3">
      <p className="text-sm text-muted-foreground">
        API: <code className="font-mono">{API_URL}</code>
      </p>
      <p className="text-lg" data-testid="api-health" data-state={state.kind}>
        API health:{" "}
        <span
          className={
            state.kind === "ok"
              ? "font-semibold text-green-600"
              : state.kind === "error"
                ? "font-semibold text-destructive"
                : "text-muted-foreground"
          }
        >
          {label}
        </span>
      </p>
      <Button
        variant="outline"
        onClick={() => {
          setState({ kind: "loading" });
          setAttempt((n) => n + 1);
        }}
      >
        Check again
      </Button>
    </div>
  );
}
