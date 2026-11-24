// Display rules for the "Why this score" view (Story 3.4; pure, so they can be unit tested with
// node --test). Relative imports: node --test erases type-only imports and cannot resolve "@/".
import type {
  ListingMatch,
  MatchScoreItem,
  ScoreBreakdown,
  ScoreComponent,
} from "../api/types.gen.ts";

export const WHY_BUTTON_LABEL = "Why this score";
export const BREAKDOWN_TITLE = "Why this score";
export const MATCHED_HEADING = "Skills you have";
export const MISSING_HEADING = "Skills this Listing asks for that you don't list";
export const EMPTY_LIST_TEXT = "None";
export const ON_REQUEST_NOTE = "Scored just now";
export const NO_SKILLS_NOTE =
  "This Listing lists no skills, so the title and keywords count for the whole score.";
export const BREAKDOWN_ERROR = "Could not load the breakdown. Try again later.";

/** The most ids one `getMatchScores` call takes. */
export const MATCH_BATCH_MAX = 50;

const COMPONENT_LABELS: Record<ScoreComponent["name"], string> = {
  skills: "Skills",
  text: "Title and keywords",
};

/** The plain label of a score component. */
export function componentLabel(name: ScoreComponent["name"]): string {
  return COMPONENT_LABELS[name] ?? name;
}

/** One component's points out of its maximum: "Skills: 35 of 70 points". */
export function componentLine(component: ScoreComponent): string {
  return `${componentLabel(component.name)}: ${component.points} of ${component.max_points} points`;
}

/** The note shown when the skills component has a maximum of 0, else null. */
export function noSkillsNote(breakdown: Pick<ScoreBreakdown, "components">): string | null {
  const skills = breakdown.components.find((c) => c.name === "skills");
  return skills && skills.max_points === 0 ? NO_SKILLS_NOTE : null;
}

/** "Scored just now" for a score computed on request, else null. */
export function sourceNote(source: ListingMatch["source"]): string | null {
  return source === "on_request" ? ON_REQUEST_NOTE : null;
}

/** The `getMatchScores` ids of one results page: de-duplicated, at most 50. */
export function matchScoreIds(ids: readonly string[]): string[] {
  return [...new Set(ids)].slice(0, MATCH_BATCH_MAX);
}

/** Adds a batch of scores to the ones already known, by Listing id. */
export function mergeScores(
  current: Readonly<Record<string, number>>,
  items: readonly MatchScoreItem[],
): Record<string, number> {
  const next = { ...current };
  for (const item of items) next[item.listing_id] = item.score;
  return next;
}
