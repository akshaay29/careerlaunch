"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { addExternalJob } from "@/api";
import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth-paths";
import {
  ADD_EXTERNAL_JOB_PATH,
  DESCRIPTION_MAX,
  DRAFT_STORAGE_KEY,
  EMPTY_MANUAL_JOB,
  MIN_DEADLINE,
  type ManualJobErrors,
  type ManualJobField,
  type ManualJobForm,
  TITLE_REQUIRED_MESSAGE,
  URL_NOTE,
  addedJobPath,
  codePoints,
  counterAnnouncement,
  descriptionCounter,
  hasErrors,
  manualJobBody,
  parseStoredDraft,
  problemFieldErrors,
  titlePreview,
  validateManualJob,
} from "@/lib/manual-listing";

/** Keep the draft for after logging in again (best effort: storage may be blocked). */
function storeDraft(form: ManualJobForm): void {
  try {
    window.sessionStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(form));
  } catch {
    // Storage unavailable: the form still shows the draft until the page is left.
  }
}

/** The draft kept by a 401, removed once read. */
function takeDraft(): ManualJobForm | null {
  try {
    const draft = parseStoredDraft(window.sessionStorage.getItem(DRAFT_STORAGE_KEY));
    window.sessionStorage.removeItem(DRAFT_STORAGE_KEY);
    return draft;
  } catch {
    return null;
  }
}

const INPUT = "rounded-md border bg-transparent px-3 py-2 text-sm";
const FAILURE = "Could not add this job. Try again.";

// Form key -> the API field its error is reported on.
const FIELD_OF: Record<keyof ManualJobForm, ManualJobField> = {
  url: "url",
  title: "title",
  company: "company",
  location: "location",
  jobType: "job_type",
  deadline: "deadline",
  description: "description",
};

function Field({
  label,
  htmlFor,
  hint,
  error,
  errorId,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: ReactNode;
  error?: string;
  errorId: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={htmlFor} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      {error ? (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * "Add external job" (Story 4.3): a job found on another site, by link and/or details, with an
 * optional private description. The link is only stored (never visited). On success (201, or
 * 200 for a link already added, flagged with `?existing=1`) the Student lands on its
 * Application. Field errors come from the same rules as the API, then from the API's 422. A 401
 * keeps the draft (session storage) and offers to log in again; it is restored on the next load.
 */
export function AddExternalJobForm() {
  const router = useRouter();
  const [form, setForm] = useState<ManualJobForm>(EMPTY_MANUAL_JOB);
  const [errors, setErrors] = useState<ManualJobErrors>({});
  const [busy, setBusy] = useState(false);
  const [signedOut, setSignedOut] = useState(false);
  // One submit at a time, even before `busy` re-renders.
  const submitting = useRef(false);
  const base = useId();

  // Restore a draft kept by a 401 (browser only, after hydration).
  useEffect(() => {
    const draft = takeDraft();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time restore from storage
    if (draft) setForm(draft);
  }, []);

  const id = (field: ManualJobField) => `${base}-${field}`;
  const errorId = (field: ManualJobField) => `${base}-${field}-error`;

  function set<K extends keyof ManualJobForm>(key: K, value: ManualJobForm[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
    const field = FIELD_OF[key];
    setErrors((prev) => {
      // Typing a link also clears "title required" (but not another title error).
      const titleRequired = key === "url" && prev.title === TITLE_REQUIRED_MESSAGE;
      const clear = [field, ...(titleRequired ? (["title"] as const) : [])];
      if (!clear.some((f) => prev[f]) && !prev.form) return prev;
      const rest = { ...prev };
      for (const f of clear) delete rest[f];
      delete rest.form;
      return rest;
    });
  }

  const describedBy = (field: ManualJobField, extra?: string) =>
    [errors[field] ? errorId(field) : null, extra].filter(Boolean).join(" ") || undefined;
  const invalid = (field: ManualJobField) => (errors[field] ? true : undefined);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const found = validateManualJob(form);
    if (hasErrors(found)) {
      setErrors(found);
      return;
    }
    submitting.current = true;
    setBusy(true);
    setErrors({});
    setSignedOut(false);
    let leaving = false;
    try {
      const { data, error, response } = await addExternalJob({ body: manualJobBody(form) });
      if (data) {
        leaving = true;
        router.push(addedJobPath(data.id, response?.status === 200));
        return;
      }
      if (response?.status === 401) {
        storeDraft(form);
        setSignedOut(true);
        return;
      }
      if (response?.status === 422) {
        setErrors(problemFieldErrors(error, FAILURE));
        return;
      }
      setErrors({ form: FAILURE });
    } catch {
      setErrors({ form: FAILURE });
    } finally {
      submitting.current = false;
      // Stay disabled while navigating away.
      if (!leaving) setBusy(false);
    }
  }

  const preview = titlePreview(form);
  const urlNoteId = `${base}-url-note`;
  const counterId = `${base}-description-counter`;
  const descriptionOver = codePoints(form.description.trim()) > DESCRIPTION_MAX;
  const announcement = counterAnnouncement(form.description);
  const loginHref = `${LOGIN_PATH}?next=${encodeURIComponent(ADD_EXTERNAL_JOB_PATH)}`;

  return (
    <form className="flex flex-col gap-5" onSubmit={onSubmit} noValidate data-testid="add-external-job">
      <Field
        label="Link to the job"
        htmlFor={id("url")}
        error={errors.url}
        errorId={errorId("url")}
        hint={<span id={urlNoteId}>{URL_NOTE}</span>}
      >
        <input
          id={id("url")}
          type="url"
          inputMode="url"
          autoComplete="off"
          className={INPUT}
          value={form.url}
          placeholder="https://"
          aria-invalid={invalid("url")}
          aria-describedby={describedBy("url", urlNoteId)}
          disabled={busy}
          onChange={(e) => set("url", e.target.value)}
        />
      </Field>

      <Field
        label="Title"
        htmlFor={id("title")}
        error={errors.title}
        errorId={errorId("title")}
        hint={
          form.title.trim()
            ? undefined
            : preview
              ? `Leave empty to use "${preview}".`
              : "Required unless you give a link."
        }
      >
        <input
          id={id("title")}
          type="text"
          className={INPUT}
          value={form.title}
          aria-invalid={invalid("title")}
          aria-describedby={describedBy("title")}
          disabled={busy}
          onChange={(e) => set("title", e.target.value)}
        />
      </Field>

      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Company" htmlFor={id("company")} error={errors.company} errorId={errorId("company")}>
          <input
            id={id("company")}
            type="text"
            className={INPUT}
            value={form.company}
            aria-invalid={invalid("company")}
            aria-describedby={describedBy("company")}
            disabled={busy}
            onChange={(e) => set("company", e.target.value)}
          />
        </Field>
        <Field
          label="Location"
          htmlFor={id("location")}
          error={errors.location}
          errorId={errorId("location")}
        >
          <input
            id={id("location")}
            type="text"
            className={INPUT}
            value={form.location}
            aria-invalid={invalid("location")}
            aria-describedby={describedBy("location")}
            disabled={busy}
            onChange={(e) => set("location", e.target.value)}
          />
        </Field>
        <Field
          label="Job type"
          htmlFor={id("job_type")}
          error={errors.job_type}
          errorId={errorId("job_type")}
        >
          <select
            id={id("job_type")}
            className={INPUT}
            value={form.jobType}
            aria-invalid={invalid("job_type")}
            aria-describedby={describedBy("job_type")}
            disabled={busy}
            onChange={(e) => set("jobType", e.target.value as ManualJobForm["jobType"])}
          >
            <option value="">Not specified</option>
            <option value="internship">Internship</option>
            <option value="full_time">Full-time</option>
          </select>
        </Field>
        <Field
          label="Deadline"
          htmlFor={id("deadline")}
          error={errors.deadline}
          errorId={errorId("deadline")}
        >
          <input
            id={id("deadline")}
            type="date"
            min={MIN_DEADLINE}
            className={INPUT}
            value={form.deadline}
            aria-invalid={invalid("deadline")}
            aria-describedby={describedBy("deadline")}
            disabled={busy}
            onChange={(e) => set("deadline", e.target.value)}
          />
        </Field>
      </div>

      <Field
        label="Description (optional)"
        htmlFor={id("description")}
        error={errors.description}
        errorId={errorId("description")}
        hint={
          <span className="flex justify-between gap-4">
            <span>Only you can see it. It helps score this job against your skills.</span>
            <span id={counterId} className={descriptionOver ? "text-destructive" : undefined}>
              {descriptionCounter(form.description)}
            </span>
            {/* Announced only near or over the limit, not on every keystroke. */}
            <span className="sr-only" aria-live="polite">
              {announcement ?? ""}
            </span>
          </span>
        }
      >
        <textarea
          id={id("description")}
          className={`${INPUT} min-h-40`}
          value={form.description}
          aria-invalid={invalid("description")}
          aria-describedby={describedBy("description", counterId)}
          disabled={busy}
          onChange={(e) => set("description", e.target.value)}
        />
      </Field>

      {signedOut ? (
        <p role="alert" className="text-sm" data-testid="add-external-job-signed-out">
          Your session has ended. Your draft is kept in this tab.{" "}
          <Link className="underline" href={loginHref}>
            Log in again
          </Link>{" "}
          and come back to add it.
        </p>
      ) : null}

      {errors.form ? (
        <p role="alert" className="text-sm text-destructive">
          {errors.form}
        </p>
      ) : null}

      <div>
        <Button type="submit" disabled={busy}>
          {busy ? "Adding…" : "Add job"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        The job is saved to your Tracker and scored like any other Listing. Description up to{" "}
        {DESCRIPTION_MAX.toLocaleString("en-GB")} characters.
      </p>
    </form>
  );
}
