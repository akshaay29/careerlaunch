"use client";

import { Dialog as DialogPrimitive } from "radix-ui";
import { type ReactNode, useRef, useState } from "react";

import { type ListingMatch, getListingMatch, recordBreakdownViewed } from "@/api";
import { Button } from "@/components/ui/button";
import {
  BREAKDOWN_ERROR,
  BREAKDOWN_TITLE,
  EMPTY_LIST_TEXT,
  MATCHED_HEADING,
  MISSING_HEADING,
  WHY_BUTTON_LABEL,
  componentLine,
  noSkillsNote,
  sourceNote,
} from "@/lib/breakdown";
import { scoreBadge } from "@/lib/feed";

type Load =
  | { kind: "loading" }
  | { kind: "ok"; match: ListingMatch }
  | { kind: "error" };

async function fetchMatch(listingId: string): Promise<Load> {
  try {
    const { data } = await getListingMatch({ path: { listing_id: listingId } });
    return data ? { kind: "ok", match: data } : { kind: "error" };
  } catch {
    return { kind: "error" };
  }
}

/** The count-only "opened" event: fire and forget, failures ignored. */
function recordOpened(listingId: string) {
  try {
    void recordBreakdownViewed({ path: { listing_id: listingId } }).catch(() => undefined);
  } catch {
    // ignored: the event is only a count
  }
}

/** A titled list of skill chips ("None" when empty). Labels are plain text. */
function SkillList({
  heading,
  skills,
}: {
  heading: string;
  skills: ListingMatch["breakdown"]["matched_skills"];
}) {
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-sm font-medium">{heading}</h3>
      {skills.length === 0 ? (
        <p className="text-sm text-muted-foreground">{EMPTY_LIST_TEXT}</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5" aria-label={heading}>
          {skills.map((skill) => (
            <li key={skill.id} className="rounded-full border bg-muted/50 px-2 py-0.5 text-xs">
              {skill.label}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function BreakdownBody({ load, onRetry }: { load: Load; onRetry: () => void }) {
  if (load.kind === "loading") {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading…
      </p>
    );
  }
  if (load.kind === "error") {
    return (
      <div className="flex flex-col items-start gap-2">
        <p role="alert" className="text-sm text-destructive">
          {BREAKDOWN_ERROR}
        </p>
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      </div>
    );
  }
  const { match } = load;
  const { breakdown } = match;
  const note = noSkillsNote(breakdown);
  const scored = sourceNote(match.source);
  return (
    <div className="flex flex-col gap-4" data-testid="score-breakdown">
      <div className="flex flex-col gap-0.5">
        <p className="text-2xl font-semibold tabular-nums">
          <span className="sr-only">Match Score </span>
          {scoreBadge(match.score)}
        </p>
        {scored ? <p className="text-xs text-muted-foreground">{scored}</p> : null}
      </div>
      <ul className="flex flex-col gap-1 text-sm">
        {breakdown.components.map((component) => (
          <li key={component.name}>{componentLine(component)}</li>
        ))}
      </ul>
      {note ? <p className="text-sm text-muted-foreground">{note}</p> : null}
      <SkillList heading={MATCHED_HEADING} skills={breakdown.matched_skills} />
      <SkillList heading={MISSING_HEADING} skills={breakdown.missing_skills} />
    </div>
  );
}

/**
 * "Why this score": a button that opens a dialog with the Listing's Match Score breakdown
 * (matched and missing skills, each component's points out of its maximum). The breakdown is
 * fetched on each open (the first open reuses `initial` when given), and each open whose
 * breakdown loads records one count-only event. Escape closes the dialog and focus returns
 * to the button.
 */
export function ScoreBreakdownView({
  listingId,
  initial,
}: {
  listingId: string;
  initial?: ListingMatch;
}) {
  const [open, setOpen] = useState(false);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  // Only the latest open (or Try again) may set the result.
  const request = useRef(0);
  const initialUsed = useRef(false);

  function fetchNow() {
    const mine = ++request.current;
    setLoad({ kind: "loading" });
    void fetchMatch(listingId).then((result) => {
      if (request.current !== mine) return;
      setLoad(result);
      if (result.kind === "ok") recordOpened(listingId);
    });
  }

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      request.current += 1;
      return;
    }
    if (initial && !initialUsed.current) {
      initialUsed.current = true;
      setLoad({ kind: "ok", match: initial });
      recordOpened(listingId);
      return;
    }
    fetchNow();
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Trigger asChild>
        <Button type="button" variant="link" size="xs" className="h-auto px-0" data-testid="why-score">
          {WHY_BUTTON_LABEL}
        </Button>
      </DialogPrimitive.Trigger>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <DialogPrimitive.Content className="fixed top-1/2 left-1/2 z-50 flex max-h-[calc(100%-2rem)] w-full max-w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 flex-col gap-4 overflow-y-auto rounded-lg border bg-background p-6 shadow-lg sm:max-w-md">
          <DialogPrimitive.Title className="text-lg font-semibold">
            {BREAKDOWN_TITLE}
          </DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">
            How this Listing&apos;s Match Score is made up.
          </DialogPrimitive.Description>
          <BreakdownBody load={load} onRetry={fetchNow} />
          <div className="flex justify-end">
            <DialogPrimitive.Close asChild>
              <Button type="button" variant="outline">
                Close
              </Button>
            </DialogPrimitive.Close>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/**
 * The score badge ("72 / 100") with its "Why this score" button, for the feed, the Listing
 * detail page and search results. `children` (e.g. "Below your threshold") goes underneath.
 */
export function MatchScoreBadge({
  listingId,
  score,
  initial,
  children,
}: {
  listingId: string;
  score: number;
  initial?: ListingMatch;
  children?: ReactNode;
}) {
  return (
    <div className="flex shrink-0 flex-col items-end gap-0.5" data-testid="match-score">
      <span className="rounded-full border px-2 py-0.5 text-sm font-medium tabular-nums">
        <span className="sr-only">Match Score </span>
        {scoreBadge(score)}
      </span>
      <ScoreBreakdownView listingId={listingId} initial={initial} />
      {children}
    </div>
  );
}
