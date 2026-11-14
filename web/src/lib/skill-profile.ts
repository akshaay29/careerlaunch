// Display, polling and input rules for the Skill Profile (pure, so they can be unit tested
// with node --test). Relative type-only import: node --test cannot resolve the "@/" alias.
import type { ProfileSkill, SkillProfileOut } from "../api/types.gen.ts";

export const SETTINGS_SKILLS_PATH = "/settings/skills";

/** While extraction is pending, poll every 2 s for at most 60 s. */
export const POLL_INTERVAL_MS = 2_000;
export const POLL_TIMEOUT_MS = 60_000;

/** The server's limit on one skill term (it is the authority; this only saves a 422). */
export const SKILL_TERM_MAX_CHARS = 50;

/** Fewer characters than this are not worth asking the server for suggestions. */
export const SUGGEST_MIN_CHARS = 1;

/** True while a pending profile should be fetched again. */
export function shouldKeepPolling(
  status: SkillProfileOut["status"],
  startedAt: number,
  now: number,
): boolean {
  return status === "pending" && now - startedAt < POLL_TIMEOUT_MS;
}

/** A skill term as the server stores it: trimmed, inner whitespace collapsed. */
export function cleanSkillTerm(raw: string): string {
  return raw.split(/\s+/).filter(Boolean).join(" ");
}

/** A message to show before sending a term, or null when it may be sent. */
export function precheckSkillTerm(raw: string): string | null {
  const term = cleanSkillTerm(raw);
  if (!term) return "Type a skill first.";
  if (term.length > SKILL_TERM_MAX_CHARS) {
    return `A skill can be at most ${SKILL_TERM_MAX_CHARS} characters.`;
  }
  return null;
}

/** Where an effective skill comes from, in words. */
export function skillSourceLabel(source: ProfileSkill["source"]): string {
  return source === "extracted" ? "from your Resume" : "added by you";
}

/** A one-line description of the profile's state (null when nothing needs saying). */
export function skillProfileStatusText(profile: SkillProfileOut): string | null {
  switch (profile.status) {
    case "none":
      return "No Resume has been read yet. Upload one, or add your skills below.";
    case "pending":
      return "Reading your Resume…";
    default:
      return null;
  }
}

/** The profile came from a Resume that has since been deleted (the profile is kept). */
export function sourceResumeDeleted(profile: SkillProfileOut): boolean {
  return profile.status !== "none" && profile.status !== "pending" && profile.resume_id === null;
}

/**
 * True when there are Student edits that "Reset to extracted" would drop. The server decides
 * (an added skill that is also extracted shows as "extracted" but is still an edit).
 */
export function hasEdits(profile: SkillProfileOut): boolean {
  return profile.has_edits;
}
