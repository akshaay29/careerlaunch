"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

import {
  type ApplicationOut,
  type ApplicationUpdate,
  type ResumeVersion,
  type StatusChange,
  deleteApplication,
  getApplication,
  getApplicationHistory,
  listResumes,
  transitionApplication,
  updateApplication,
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
import { formatDate, listingPath, safeExternalUrl } from "@/lib/listings";
import {
  type ApplicationStatus,
  type StatusAction,
  DELETE_CONFIRM_TEXT,
  LISTINGS_PATH,
  MIN_APPLIED_ON,
  NOTES_MAX,
  NOT_AVAILABLE_NOTICE,
  OFFER_CONFIRM_TEXT,
  historyLines,
  invalidTransitionMessage,
  needsConfirmation,
  notesError,
  statusActions,
  statusLabel,
} from "@/lib/tracker";

const RESUME_PAGE = 50;

type Load =
  | { kind: "loading" }
  | { kind: "ok"; application: ApplicationOut; history: StatusChange[] }
  | { kind: "not-found" }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

/** The page state for an HTTP status that is not a success. */
function failure(status: number | undefined): Load {
  if (status === 401) return { kind: "signed-out" };
  // 404 (not mine or unknown) and 422 (not a valid id) look the same.
  if (status === 404 || status === 422) return { kind: "not-found" };
  return { kind: "error", message: `HTTP ${status ?? "?"}` };
}

async function fetchApplication(id: string): Promise<Load> {
  try {
    const [app, hist] = await Promise.all([
      getApplication({ path: { application_id: id } }),
      getApplicationHistory({ path: { application_id: id } }),
    ]);
    if (!app.data) return failure(app.response?.status);
    // The Application loaded but its history did not: report the history's own status.
    if (!hist.data) return failure(hist.response?.status);
    return { kind: "ok", application: app.data, history: hist.data.items };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

/** The Student's Resume versions for the picker; empty when they cannot be loaded. */
async function fetchResumes(): Promise<ResumeVersion[]> {
  try {
    const { data } = await listResumes({ query: { limit: RESUME_PAGE } });
    return data?.items ?? [];
  } catch {
    return [];
  }
}

/** The `errors[0].msg` of a 422 problem, or a generic message. */
function problemMessage(error: unknown, fallback: string): string {
  const errors = (error as { errors?: { msg?: string }[] } | undefined)?.errors;
  return errors?.[0]?.msg || fallback;
}

/** A Status button; Offer (final) asks for confirmation first. */
function StatusButton({
  action,
  disabled,
  onMove,
}: {
  action: StatusAction;
  disabled: boolean;
  onMove: (to: ApplicationStatus) => void;
}) {
  if (!needsConfirmation(action.status)) {
    return (
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={() => onMove(action.status)}
      >
        {action.label}
      </Button>
    );
  }
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button type="button" variant="outline" size="sm" disabled={disabled}>
          {action.label}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Mark as Offer?</AlertDialogTitle>
          <AlertDialogDescription>{OFFER_CONFIRM_TEXT}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={() => onMove(action.status)}>
            {action.label}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/**
 * One Application (Story 4.1): the snapshot of its Listing, the Status with buttons for the
 * allowed next Statuses only, notes, the applied date, the Resume version used, the history
 * (newest first) and Delete. All Listing text is rendered as plain text. Used by /tracker/[id].
 */
export function ApplicationView({ id }: { id: string }) {
  const router = useRouter();
  // The last load and the id it is for: a load for another id (the id changed) shows as
  // loading, so nothing has to be reset when the id changes.
  const [loaded, setLoaded] = useState<{ id: string; load: Load } | null>(null);
  const load: Load = loaded && loaded.id === id ? loaded.load : { kind: "loading" };
  const [resumes, setResumes] = useState<ResumeVersion[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  // The applied date being typed; committed on blur or with its Save button.
  const [appliedOn, setAppliedOn] = useState("");
  // Bumped by each full load (mount, id change, Try again) and on unmount: only the latest
  // load may set state.
  const request = useRef(0);
  const mounted = useRef(true);
  const notesId = useId();
  const appliedId = useId();
  const resumeId = useId();
  const statusId = useId();

  function setLoad(next: Load, forId: string = id) {
    setLoaded({ id: forId, load: next });
  }

  function show(next: Load, forId: string = id) {
    setLoad(next, forId);
    if (next.kind === "ok") {
      setNotes(next.application.notes ?? "");
      setAppliedOn(next.application.applied_on ?? "");
    }
  }

  /** Everything, from scratch: the Application, its history and the Resume versions. */
  async function loadAll(target: string) {
    const mine = ++request.current;
    const [next, versions] = await Promise.all([fetchApplication(target), fetchResumes()]);
    if (request.current !== mine) return;
    setMessage(null);
    setResumes(versions);
    show(next, target);
  }

  useEffect(() => {
    mounted.current = true;
    void loadAll(id);
    const current = request;
    return () => {
      mounted.current = false;
      current.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload only when the id changes
  }, [id]);

  /** The Application and history again, after a change. */
  async function reload() {
    const mine = request.current;
    const next = await fetchApplication(id);
    if (request.current === mine && mounted.current) show(next);
  }

  async function move(to: ApplicationStatus) {
    setBusy("status");
    setMessage(null);
    try {
      const { data, error, response } = await transitionApplication({
        path: { application_id: id },
        body: { status: to },
      });
      if (!mounted.current) return;
      if (data) {
        await reload();
      } else if (response?.status === 409) {
        const allowed = (error as { allowed?: ApplicationStatus[] } | undefined)?.allowed ?? [];
        setMessage(invalidTransitionMessage(allowed));
        await reload();
      } else if (response?.status === 401 || response?.status === 404) {
        setLoad(failure(response.status));
      } else {
        setMessage("Could not change the Status. Try again.");
      }
    } catch {
      if (mounted.current) setMessage("Could not change the Status. Try again.");
    }
    if (mounted.current) setBusy(null);
  }

  async function update(body: ApplicationUpdate, what: string) {
    setBusy(what);
    setMessage(null);
    try {
      const { data, error, response } = await updateApplication({
        path: { application_id: id },
        body,
      });
      if (!mounted.current) return;
      if (data) {
        setLoaded((prev) =>
          prev && prev.load.kind === "ok"
            ? { ...prev, load: { ...prev.load, application: data } }
            : prev,
        );
        if ("notes" in body) setNotes(data.notes ?? "");
        if ("applied_on" in body) setAppliedOn(data.applied_on ?? "");
      } else if (response?.status === 401 || response?.status === 404) {
        setLoad(failure(response.status));
      } else {
        setMessage(problemMessage(error, "Could not save. Try again."));
        if ("applied_on" in body && load.kind === "ok") {
          setAppliedOn(load.application.applied_on ?? "");
        }
      }
    } catch {
      if (mounted.current) setMessage("Could not save. Try again.");
    }
    if (mounted.current) setBusy(null);
  }

  function commitAppliedOn() {
    if (load.kind !== "ok") return;
    const value = appliedOn || null;
    if (value === load.application.applied_on) return;
    void update({ applied_on: value }, "applied_on");
  }

  async function remove() {
    setBusy("delete");
    setMessage(null);
    try {
      const { response } = await deleteApplication({ path: { application_id: id } });
      if (!mounted.current) return;
      if (response?.ok || response?.status === 404) {
        router.push(LISTINGS_PATH);
        return;
      }
      setMessage("Could not delete. Try again.");
    } catch {
      if (mounted.current) setMessage("Could not delete. Try again.");
    }
    if (mounted.current) setBusy(null);
  }

  if (load.kind === "loading") {
    return <p className="text-muted-foreground">Loading Application…</p>;
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
  if (load.kind === "not-found") {
    return (
      <p className="text-muted-foreground" data-testid="application-not-found">
        This Application does not exist.
      </p>
    );
  }
  if (load.kind === "error") {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-destructive">
          Could not load this Application ({load.message}).
        </p>
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            setLoad({ kind: "loading" });
            void loadAll(id);
          }}
        >
          Try again
        </Button>
      </div>
    );
  }

  const { application, history } = load;
  const href = safeExternalUrl(application.url);
  const deadline = formatDate(application.deadline);
  const actions = statusActions(application);
  const notesProblem = notesError(notes);
  const chosenMissing =
    application.resume_id !== null && !resumes.some((r) => r.id === application.resume_id);
  const appliedChanged = (appliedOn || null) !== application.applied_on;

  return (
    <article className="flex flex-col gap-6" data-testid="application">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight break-words">
          {application.listing_available && application.listing_id ? (
            <Link className="hover:underline" href={listingPath(application.listing_id)}>
              {application.title}
            </Link>
          ) : (
            application.title
          )}
        </h1>
        {application.company ? (
          <p className="text-muted-foreground break-words">{application.company}</p>
        ) : null}
        {deadline ? <p className="text-sm">Apply by {deadline}</p> : null}
        {!application.listing_available ? (
          <p className="text-sm text-muted-foreground" data-testid="application-unavailable">
            {NOT_AVAILABLE_NOTICE}
          </p>
        ) : null}
        {href ? (
          <div>
            <Button asChild size="sm">
              <a href={href} target="_blank" rel="noopener noreferrer nofollow">
                Apply
              </a>
            </Button>
          </div>
        ) : null}
      </header>

      <section className="flex flex-col gap-3" aria-labelledby={statusId}>
        <h2 id={statusId} className="text-sm font-medium">
          Status: <span data-testid="application-status">{statusLabel(application.status)}</span>
        </h2>
        {actions.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {actions.map((action) => (
              <StatusButton
                key={action.status}
                action={action}
                disabled={busy !== null}
                onMove={(to) => void move(to)}
              />
            ))}
          </div>
        ) : null}
      </section>

      {message ? (
        <p role="alert" className="text-sm text-destructive">
          {message}
        </p>
      ) : null}

      <section className="flex flex-col gap-2">
        <label htmlFor={notesId} className="text-sm font-medium">
          Notes
        </label>
        <textarea
          id={notesId}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={5}
          className="rounded-md border bg-background px-3 py-2 text-sm"
          aria-invalid={notesProblem ? true : undefined}
        />
        {notesProblem ? <p className="text-xs text-destructive">{notesProblem}</p> : null}
        <div>
          <Button
            type="button"
            size="sm"
            disabled={busy !== null || notesProblem !== null}
            onClick={() => update({ notes }, "notes")}
          >
            {busy === "notes" ? "Saving…" : "Save"}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">Up to {NOTES_MAX} characters.</p>
      </section>

      <section className="flex flex-col gap-2">
        <label htmlFor={appliedId} className="text-sm font-medium">
          Applied on
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <input
            id={appliedId}
            type="date"
            value={appliedOn}
            min={MIN_APPLIED_ON}
            max={application.today}
            disabled={busy !== null}
            onChange={(e) => setAppliedOn(e.target.value)}
            onBlur={commitAppliedOn}
            className="w-fit rounded-md border bg-background px-3 py-1.5 text-sm"
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy !== null || !appliedChanged}
            onClick={commitAppliedOn}
          >
            {busy === "applied_on" ? "Saving…" : "Save date"}
          </Button>
        </div>
      </section>

      <section className="flex flex-col gap-2">
        <label htmlFor={resumeId} className="text-sm font-medium">
          Resume version used
        </label>
        <select
          id={resumeId}
          value={application.resume_id ?? ""}
          disabled={busy !== null}
          onChange={(e) => update({ resume_id: e.target.value || null }, "resume_id")}
          className="w-fit max-w-full rounded-md border bg-background px-3 py-1.5 text-sm"
        >
          <option value="">None</option>
          {resumes.map((resume) => (
            <option key={resume.id} value={resume.id}>
              {resume.filename}
            </option>
          ))}
          {chosenMissing && application.resume_id ? (
            <option value={application.resume_id}>Another version</option>
          ) : null}
        </select>
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-medium">History</h2>
        <ol className="flex flex-col gap-1 text-sm" data-testid="application-history">
          {historyLines(history).map((line) => (
            <li key={line.key} className="flex gap-3">
              <span className="w-24 shrink-0">{line.status}</span>
              <span className="text-muted-foreground">{line.when}</span>
            </li>
          ))}
        </ol>
      </section>

      <div>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button type="button" variant="destructive" disabled={busy !== null}>
              Delete
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete this Application?</AlertDialogTitle>
              <AlertDialogDescription>{DELETE_CONFIRM_TEXT}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={remove}>Delete</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </article>
  );
}
