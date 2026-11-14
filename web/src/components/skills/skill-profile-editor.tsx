"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";

import {
  type SkillProfileEdit,
  type SkillProfileOut,
  type SkillRef,
  editSkillProfile,
  getSkillProfile,
  resetSkillProfile,
  suggestSkills,
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
import { problemDetail } from "@/lib/resumes";
import {
  POLL_INTERVAL_MS,
  SKILL_TERM_MAX_CHARS,
  SUGGEST_MIN_CHARS,
  cleanSkillTerm,
  hasEdits,
  precheckSkillTerm,
  shouldKeepPolling,
  skillProfileStatusText,
  skillSourceLabel,
  sourceResumeDeleted,
} from "@/lib/skill-profile";

type Load =
  | { kind: "loading" }
  | { kind: "ok"; profile: SkillProfileOut }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

async function fetchProfile(): Promise<Load> {
  try {
    const { data, response } = await getSkillProfile();
    if (data) return { kind: "ok", profile: data };
    if (response?.status === 401) return { kind: "signed-out" };
    return { kind: "error", message: `HTTP ${response?.status ?? "?"}` };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

const SUGGEST_DEBOUNCE_MS = 200;

/**
 * The Student's Skill Profile: skills as removable chips (with where each came from), an
 * add-skill input with vocabulary suggestions, custom skills, and Education / Experience as
 * read-only text. Polls while the Resume is being read. Used by onboarding step 3 and
 * /settings/skills. Everything from the server is rendered as plain text.
 */
export function SkillProfileEditor() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [timedOut, setTimedOut] = useState(false);
  const [term, setTerm] = useState("");
  const [suggestions, setSuggestions] = useState<SkillRef[]>([]);
  const pollStartedAt = useRef<number | null>(null);
  const inputId = useId();
  const listId = useId();

  const refresh = useCallback(async () => setLoad(await fetchProfile()), []);

  useEffect(() => {
    let active = true;
    fetchProfile().then((next) => {
      if (active) setLoad(next);
    });
    return () => {
      active = false;
    };
  }, []);

  // Poll every 2 s while the extraction is pending, for at most 60 s.
  useEffect(() => {
    if (load.kind !== "ok" || load.profile.status !== "pending") {
      pollStartedAt.current = null;
      return;
    }
    if (timedOut) return;
    const startedAt = pollStartedAt.current ?? Date.now();
    pollStartedAt.current = startedAt;
    let active = true;
    const timer = setTimeout(async () => {
      if (!shouldKeepPolling("pending", startedAt, Date.now())) {
        if (active) setTimedOut(true);
        return;
      }
      const next = await fetchProfile();
      if (!active) return;
      if (next.kind === "error") {
        // A failed poll keeps what is shown; retry on the next tick (same start time).
        setLoad((prev) => (prev.kind === "ok" ? { kind: "ok", profile: prev.profile } : next));
        return;
      }
      setLoad(next);
    }, POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [load, timedOut]);

  // Vocabulary suggestions for the add-skill input (debounced).
  useEffect(() => {
    const q = cleanSkillTerm(term);
    let active = true;
    const timer = setTimeout(async () => {
      if (q.length < SUGGEST_MIN_CHARS || q.length > SKILL_TERM_MAX_CHARS) {
        if (active) setSuggestions([]);
        return;
      }
      try {
        const { data } = await suggestSkills({ query: { q } });
        if (active) setSuggestions(data ?? []);
      } catch {
        if (active) setSuggestions([]);
      }
    }, SUGGEST_DEBOUNCE_MS);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [term]);

  async function edit(body: SkillProfileEdit, failure: string): Promise<boolean> {
    setBusy(true);
    setMessage(null);
    try {
      const { data, error, response } = await editSkillProfile({ body });
      if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
        return false;
      }
      if (data) {
        setLoad({ kind: "ok", profile: data });
        return true;
      }
      setMessage(problemDetail(error, failure, response?.status));
      return false;
    } catch {
      setMessage(failure);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add(raw: string) {
    const precheck = precheckSkillTerm(raw);
    if (precheck) {
      setMessage(precheck);
      return;
    }
    if (await edit({ add: [cleanSkillTerm(raw)] }, "Could not add this skill. Try again.")) {
      setTerm("");
      setSuggestions([]);
    }
  }

  async function reset() {
    setBusy(true);
    setMessage(null);
    try {
      const { data, response } = await resetSkillProfile();
      if (response?.status === 401) setLoad({ kind: "signed-out" });
      else if (data) setLoad({ kind: "ok", profile: data });
      else setMessage("Could not reset your skills. Try again.");
    } catch {
      setMessage("Could not reset your skills. Try again.");
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void add(term);
  }

  async function checkAgain() {
    setTimedOut(false);
    pollStartedAt.current = null;
    await refresh();
  }

  if (load.kind === "loading") {
    return <p className="text-muted-foreground">Loading your Skill Profile…</p>;
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
        Could not load your Skill Profile ({load.message}).
      </p>
    );
  }

  const { profile } = load;
  const statusText = skillProfileStatusText(profile);
  return (
    <div className="flex flex-col gap-6" data-testid="skill-profile">
      {statusText ? (
        <p role="status" className="text-sm text-muted-foreground" data-testid="skill-status">
          {statusText}
        </p>
      ) : null}
      {profile.status === "pending" && timedOut ? (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm">This is taking longer than usual.</p>
          <Button type="button" variant="outline" onClick={checkAgain}>
            Check again
          </Button>
        </div>
      ) : null}
      {profile.message ? (
        <p role="alert" className="text-sm text-destructive" data-testid="skill-message">
          {profile.message}
        </p>
      ) : null}
      {sourceResumeDeleted(profile) ? (
        <p className="text-sm text-muted-foreground" data-testid="skill-source-deleted">
          The Resume these skills came from was deleted. Your skills are kept; making another
          Resume active reads it again.
        </p>
      ) : null}

      <section className="flex flex-col gap-3 rounded-lg border p-5">
        <h2 className="text-lg font-medium">Skills</h2>
        {profile.skills.length === 0 ? (
          <p className="text-sm text-muted-foreground">No skills yet. Add some below.</p>
        ) : (
          <ul className="flex flex-wrap gap-2" data-testid="skill-chips">
            {profile.skills.map((skill) => (
              <li
                key={skill.id}
                className="flex items-center gap-2 rounded-full border px-3 py-1 text-sm"
                data-testid={`skill-${skill.id}`}
              >
                <span>{skill.label}</span>
                <span className="text-xs text-muted-foreground">
                  {skillSourceLabel(skill.source)}
                </span>
                <button
                  type="button"
                  className="rounded-full px-1 text-muted-foreground hover:text-foreground disabled:opacity-50"
                  aria-label={`Remove ${skill.label}`}
                  disabled={busy}
                  onClick={() =>
                    void edit({ remove: [skill.id] }, "Could not remove this skill. Try again.")
                  }
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}

        <form className="flex flex-col gap-2" onSubmit={onSubmit}>
          <label htmlFor={inputId} className="text-sm font-medium">
            Add a skill
          </label>
          <div className="flex flex-wrap gap-2">
            <input
              id={inputId}
              className="min-w-0 flex-1 rounded-md border bg-transparent px-3 py-2 text-sm"
              value={term}
              maxLength={SKILL_TERM_MAX_CHARS}
              autoComplete="off"
              aria-controls={suggestions.length > 0 ? listId : undefined}
              placeholder="e.g. TypeScript"
              data-testid="skill-input"
              disabled={busy}
              onChange={(e) => setTerm(e.target.value)}
            />
            <Button type="submit" disabled={busy || !cleanSkillTerm(term)}>
              Add
            </Button>
          </div>
          {suggestions.length > 0 ? (
            <ul id={listId} className="flex flex-wrap gap-2" aria-label="Suggestions">
              {suggestions.map((s) => (
                <li key={s.id}>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => void add(s.label)}
                  >
                    {s.label}
                  </Button>
                </li>
              ))}
            </ul>
          ) : null}
          <p className="text-xs text-muted-foreground">
            Skills we don&apos;t recognise are kept as custom skills. They are shown on your
            profile but not used for matching.
          </p>
        </form>

        {message ? (
          <p role="alert" className="text-sm text-destructive" data-testid="skill-error">
            {message}
          </p>
        ) : null}
      </section>

      {profile.custom.length > 0 ? (
        <section className="flex flex-col gap-3">
          <h2 className="text-lg font-medium">Custom skills</h2>
          <ul className="flex flex-col gap-2" data-testid="custom-skills">
            {profile.custom.map((custom) => (
              <li key={custom} className="flex items-center justify-between gap-2 text-sm">
                <span className="break-all">{custom}</span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() =>
                    void edit(
                      { remove_custom: [custom] },
                      "Could not remove this skill. Try again.",
                    )
                  }
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {profile.removed.length > 0 ? (
        <section className="flex flex-col gap-3">
          <h2 className="text-lg font-medium">Removed skills</h2>
          <ul className="flex flex-wrap gap-2" data-testid="removed-skills">
            {profile.removed.map((skill) => (
              <li key={skill.id}>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => void add(skill.label)}
                >
                  Restore {skill.label}
                </Button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Education</h2>
        {profile.education.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing found in your Resume.</p>
        ) : (
          <ul className="flex flex-col gap-2" data-testid="education">
            {profile.education.map((entry, i) => (
              <li key={i} className="rounded-md border p-3 text-sm whitespace-pre-line">
                {entry}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Experience</h2>
        {profile.experience.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing found in your Resume.</p>
        ) : (
          <ul className="flex flex-col gap-2" data-testid="experience">
            {profile.experience.map((entry, i) => (
              <li key={i} className="rounded-md border p-3 text-sm whitespace-pre-line">
                {entry}
              </li>
            ))}
          </ul>
        )}
      </section>

      {hasEdits(profile) ? (
        <div>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button type="button" variant="outline" disabled={busy}>
                Reset to extracted
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Reset your skills?</AlertDialogTitle>
                <AlertDialogDescription>
                  Every skill you added, removed or entered as a custom skill will be dropped,
                  and your skills will be exactly what was read from your Resume.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={() => void reset()}>Reset</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      ) : null}
    </div>
  );
}
