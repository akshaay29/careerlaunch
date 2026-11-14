"use client";

import Link from "next/link";
import { type FormEvent, type ReactNode, useEffect, useId, useMemo, useState } from "react";

import { type DegreeOut, getPreferences, listDegrees, savePreferences } from "@/api";
import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth-paths";
import {
  ALERT_FREQUENCY_OPTIONS,
  BATCH_YEAR_MAX,
  BATCH_YEAR_MIN,
  CGPA_MAX,
  FORM_FIELD_TO_PAYLOAD,
  type FieldErrors,
  JOB_TYPE_OPTIONS,
  type PreferenceField,
  type PreferencesForm,
  PREFERENCE_ITEM_MAX_CHARS,
  PREFERENCE_MAX_ITEMS,
  THRESHOLD_MAX,
  THRESHOLD_MIN,
  WORK_AUTHORISATION_OPTIONS,
  fieldErrorsFromProblem,
  supportedTimeZones,
  thresholdHint,
  timeZoneOptions,
  toForm,
  toPayload,
} from "@/lib/preferences";
import { problemDetail } from "@/lib/resumes";

type Load =
  | { kind: "loading" }
  | { kind: "ok"; degrees: DegreeOut[]; storedTimezone: string }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

type Loaded = { load: Load; form?: PreferencesForm };

async function fetchAll(): Promise<Loaded> {
  try {
    const [prefs, degrees] = await Promise.all([getPreferences(), listDegrees()]);
    if (prefs.data && degrees.data) {
      return {
        load: { kind: "ok", degrees: degrees.data, storedTimezone: prefs.data.timezone },
        form: toForm(prefs.data),
      };
    }
    if (prefs.response?.status === 401 || degrees.response?.status === 401) {
      return { load: { kind: "signed-out" } };
    }
    const status = prefs.data ? degrees.response?.status : prefs.response?.status;
    return { load: { kind: "error", message: `HTTP ${status ?? "?"}` } };
  } catch {
    return { load: { kind: "error", message: "API unreachable" } };
  }
}

const INPUT = "rounded-md border bg-transparent px-3 py-2 text-sm";

function FieldError({ id, messages }: { id: string; messages?: string[] }) {
  if (!messages || messages.length === 0) return null;
  return (
    <p id={id} role="alert" className="text-sm text-destructive">
      {messages.join(" ")}
    </p>
  );
}

function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      {htmlFor ? (
        <label htmlFor={htmlFor} className="text-sm font-medium">
          {label}
        </label>
      ) : (
        <span className="text-sm font-medium">{label}</span>
      )}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      {children}
    </div>
  );
}

/**
 * The Preferences form: what the Student is looking for, eligibility details, the Match Score
 * threshold, alerts and time zone. Shows the defaults pre-filled; nothing is required. Used by
 * onboarding step 4 and /settings/preferences. Field errors from the API's 422 are shown next
 * to their fields.
 */
export function PreferencesEditor({
  submitLabel = "Save",
  onSaved,
}: {
  submitLabel?: string;
  onSaved?: () => void;
}) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [form, setForm] = useState<PreferencesForm | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const zones = useMemo(() => supportedTimeZones(), []);
  const base = useId();
  const id = (field: PreferenceField) => `${base}-${field}`;
  const errorId = (field: PreferenceField) => `${base}-${field}-error`;
  const zoneListId = `${base}-zones`;

  useEffect(() => {
    let active = true;
    fetchAll().then((next) => {
      if (!active) return;
      setLoad(next.load);
      if (next.form) setForm(next.form);
    });
    return () => {
      active = false;
    };
  }, []);

  // The stored value is always offered; typing filters the list through the datalist.
  const storedTimezone = load.kind === "ok" ? load.storedTimezone : "";
  const zoneOptions = useMemo(
    () => timeZoneOptions(zones, storedTimezone),
    [zones, storedTimezone],
  );

  if (load.kind === "loading" || (load.kind === "ok" && form === null)) {
    return <p className="text-muted-foreground">Loading your Preferences…</p>;
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
  if (load.kind === "error" || form === null) {
    return (
      <p role="alert" className="text-destructive">
        Could not load your Preferences ({load.kind === "error" ? load.message : "unknown"}).
      </p>
    );
  }

  const values = form;
  // Functional updates, so several changes in one tick all apply. Editing a field clears
  // that field's error.
  const set = <K extends keyof PreferencesForm>(
    key: K,
    value: PreferencesForm[K] | ((prev: PreferencesForm[K]) => PreferencesForm[K]),
  ) => {
    setForm((prev) => {
      if (prev === null) return prev;
      const next =
        typeof value === "function"
          ? (value as (p: PreferencesForm[K]) => PreferencesForm[K])(prev[key])
          : value;
      return { ...prev, [key]: next };
    });
    const field = FORM_FIELD_TO_PAYLOAD[key];
    setErrors((prev) => {
      if (!prev[field]) return prev;
      const rest = { ...prev };
      delete rest[field];
      return rest;
    });
    setNotice(null);
  };
  const describedBy = (field: PreferenceField) => (errors[field] ? errorId(field) : undefined);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice(null);
    const result = toPayload(values);
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    setBusy(true);
    setErrors({});
    const failure = "Could not save your Preferences. Try again.";
    try {
      const { data, error, response } = await savePreferences({ body: result.payload });
      if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
        return;
      }
      if (data) {
        setForm(toForm(data));
        setLoad((prev) => (prev.kind === "ok" ? { ...prev, storedTimezone: data.timezone } : prev));
        setNotice("Saved.");
        onSaved?.();
        return;
      }
      if (response?.status === 422) {
        const fieldErrors = fieldErrorsFromProblem(error);
        setErrors(
          Object.keys(fieldErrors).length > 0 ? fieldErrors : { form: [problemDetail(error, failure)] },
        );
        return;
      }
      setErrors({ form: [problemDetail(error, failure, response?.status)] });
    } catch {
      setErrors({ form: [failure] });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="flex flex-col gap-6" onSubmit={onSubmit} noValidate data-testid="preferences">
      <section className="flex flex-col gap-4 rounded-lg border p-5">
        <h2 className="text-lg font-medium">What you&apos;re looking for</h2>
        <Field
          label="Role titles"
          htmlFor={id("role_titles")}
          hint={`One per line, up to ${PREFERENCE_MAX_ITEMS}. Leave empty for any role.`}
        >
          <textarea
            id={id("role_titles")}
            className={`${INPUT} min-h-20`}
            value={values.roleTitles}
            placeholder={"e.g. Backend Intern\nData Analyst"}
            aria-invalid={errors.role_titles ? true : undefined}
            aria-describedby={describedBy("role_titles")}
            disabled={busy}
            onChange={(e) => set("roleTitles", e.target.value)}
          />
          <FieldError id={errorId("role_titles")} messages={errors.role_titles} />
        </Field>
        <Field
          label="Locations"
          htmlFor={id("locations")}
          hint={`One per line, up to ${PREFERENCE_MAX_ITEMS}, ${PREFERENCE_ITEM_MAX_CHARS} characters each. Leave empty for anywhere.`}
        >
          <textarea
            id={id("locations")}
            className={`${INPUT} min-h-20`}
            value={values.locations}
            placeholder={"e.g. Bengaluru\nRemote"}
            aria-invalid={errors.locations ? true : undefined}
            aria-describedby={describedBy("locations")}
            disabled={busy}
            onChange={(e) => set("locations", e.target.value)}
          />
          <FieldError id={errorId("locations")} messages={errors.locations} />
        </Field>
        <fieldset className="flex flex-col gap-2" aria-describedby={describedBy("job_types")}>
          <legend className="text-sm font-medium">Job types</legend>
          <p className="text-xs text-muted-foreground">Leave both unticked for any.</p>
          <div className="flex flex-wrap gap-4">
            {JOB_TYPE_OPTIONS.map((option) => (
              <label key={option.value} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={values.jobTypes.includes(option.value)}
                  disabled={busy}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    set("jobTypes", (prev) =>
                      checked
                        ? [...prev.filter((t) => t !== option.value), option.value]
                        : prev.filter((t) => t !== option.value),
                    );
                  }}
                />
                {option.label}
              </label>
            ))}
          </div>
          <FieldError id={errorId("job_types")} messages={errors.job_types} />
        </fieldset>
      </section>

      <section className="flex flex-col gap-4 rounded-lg border p-5">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-medium">Eligibility</h2>
          <p className="text-xs text-muted-foreground">
            Optional. Used to tell you whether you meet a job&apos;s criteria; anything left
            blank shows as unknown.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Batch year"
            htmlFor={id("batch_year")}
            hint={`The year you graduate (${BATCH_YEAR_MIN}–${BATCH_YEAR_MAX}).`}
          >
            <input
              id={id("batch_year")}
              className={INPUT}
              inputMode="numeric"
              placeholder="e.g. 2027"
              value={values.batchYear}
              aria-invalid={errors.batch_year ? true : undefined}
              aria-describedby={describedBy("batch_year")}
              disabled={busy}
              onChange={(e) => set("batchYear", e.target.value)}
            />
            <FieldError id={errorId("batch_year")} messages={errors.batch_year} />
          </Field>
          <Field label={`CGPA (out of ${CGPA_MAX})`} htmlFor={id("cgpa")}>
            <input
              id={id("cgpa")}
              className={INPUT}
              inputMode="decimal"
              placeholder="e.g. 8.2"
              value={values.cgpa}
              aria-invalid={errors.cgpa ? true : undefined}
              aria-describedby={describedBy("cgpa")}
              disabled={busy}
              onChange={(e) => set("cgpa", e.target.value)}
            />
            <FieldError id={errorId("cgpa")} messages={errors.cgpa} />
          </Field>
          <Field label="Degree" htmlFor={id("degree")}>
            <select
              id={id("degree")}
              className={INPUT}
              value={values.degree}
              aria-invalid={errors.degree ? true : undefined}
              aria-describedby={describedBy("degree")}
              disabled={busy}
              onChange={(e) => set("degree", e.target.value)}
            >
              <option value="">Not stated</option>
              {load.degrees.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.label}
                </option>
              ))}
            </select>
            <FieldError id={errorId("degree")} messages={errors.degree} />
          </Field>
          {values.degree === "other" ? (
            <Field label="Your degree" htmlFor={id("degree_other")}>
              <input
                id={id("degree_other")}
                className={INPUT}
                value={values.degreeOther}
                maxLength={PREFERENCE_ITEM_MAX_CHARS}
                placeholder="e.g. B.Des"
                aria-invalid={errors.degree_other ? true : undefined}
                aria-describedby={describedBy("degree_other")}
                disabled={busy}
                onChange={(e) => set("degreeOther", e.target.value)}
              />
              <FieldError id={errorId("degree_other")} messages={errors.degree_other} />
            </Field>
          ) : (
            <FieldError id={errorId("degree_other")} messages={errors.degree_other} />
          )}
          <Field label="Work authorisation" htmlFor={id("work_authorisation")}>
            <select
              id={id("work_authorisation")}
              className={INPUT}
              value={values.workAuthorisation}
              aria-invalid={errors.work_authorisation ? true : undefined}
              aria-describedby={describedBy("work_authorisation")}
              disabled={busy}
              onChange={(e) =>
                set("workAuthorisation", e.target.value as PreferencesForm["workAuthorisation"])
              }
            >
              <option value="">Not stated</option>
              {WORK_AUTHORISATION_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <FieldError id={errorId("work_authorisation")} messages={errors.work_authorisation} />
          </Field>
        </div>
      </section>

      <section className="flex flex-col gap-4 rounded-lg border p-5">
        <h2 className="text-lg font-medium">Matches and alerts</h2>
        <Field
          label={`Match Score threshold: ${values.threshold}`}
          htmlFor={id("threshold")}
          hint={thresholdHint(values.threshold)}
        >
          <input
            id={id("threshold")}
            type="range"
            min={THRESHOLD_MIN}
            max={THRESHOLD_MAX}
            step={1}
            value={values.threshold}
            aria-invalid={errors.threshold ? true : undefined}
            aria-describedby={describedBy("threshold")}
            disabled={busy}
            onChange={(e) => set("threshold", Number(e.target.value))}
          />
          <FieldError id={errorId("threshold")} messages={errors.threshold} />
        </Field>
        <Field label="Alert emails" htmlFor={id("alert_frequency")}>
          <select
            id={id("alert_frequency")}
            className={INPUT}
            value={values.alertFrequency}
            aria-invalid={errors.alert_frequency ? true : undefined}
            aria-describedby={describedBy("alert_frequency")}
            disabled={busy}
            onChange={(e) =>
              set("alertFrequency", e.target.value as PreferencesForm["alertFrequency"])
            }
          >
            {ALERT_FREQUENCY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <FieldError id={errorId("alert_frequency")} messages={errors.alert_frequency} />
        </Field>
        <Field
          label="Time zone"
          htmlFor={id("timezone")}
          hint="Type to search. Your day, digests and reminders follow this time zone."
        >
          <input
            id={id("timezone")}
            className={INPUT}
            list={zoneListId}
            autoComplete="off"
            value={values.timezone}
            aria-invalid={errors.timezone ? true : undefined}
            aria-describedby={describedBy("timezone")}
            disabled={busy}
            onChange={(e) => set("timezone", e.target.value)}
          />
          <datalist id={zoneListId}>
            {zoneOptions.map((zone) => (
              <option key={zone} value={zone} />
            ))}
          </datalist>
          <FieldError id={errorId("timezone")} messages={errors.timezone} />
        </Field>
      </section>

      {errors.form ? (
        <p role="alert" className="text-sm text-destructive" data-testid="preferences-error">
          {errors.form.join(" ")}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className="text-sm text-muted-foreground">
          {notice}
        </p>
      ) : null}
      <div className="flex justify-end">
        <Button type="submit" disabled={busy} data-testid="preferences-save">
          {busy ? "Saving…" : submitLabel}
        </Button>
      </div>
    </form>
  );
}
