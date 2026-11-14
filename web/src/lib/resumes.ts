// Display and error rules for Resume versions (pure, so they can be unit tested with node --test).
// Relative type-only import: node --test erases it and cannot resolve the "@/" alias.
import type { ResumeVersion } from "../api/types.gen.ts";

export const SETTINGS_RESUMES_PATH = "/settings/resumes";

/** The server's limit (5 MiB). The server is the authority; this only saves a doomed upload. */
export const RESUME_MAX_BYTES = 5 * 1024 * 1024;
export const RESUME_TOO_LARGE_MESSAGE = "Resume must be 5 MB or smaller";

/** What the file picker offers. The server still checks the content itself. */
export const RESUME_ACCEPT =
  ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

/** A message to show before uploading, or null when the file may be sent. */
export function precheckResumeFile(file: { size: number }): string | null {
  return file.size > RESUME_MAX_BYTES ? RESUME_TOO_LARGE_MESSAGE : null;
}

/** "PDF" or "DOCX" for a stored version's content type. */
export function resumeTypeLabel(contentType: ResumeVersion["content_type"]): string {
  return contentType === "application/pdf" ? "PDF" : "DOCX";
}

/** Human-readable size: "812 B", "34.5 KB", "4.8 MB". */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "?";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * The server's problem+json `detail` (shown as plain text), or `fallback` when the error has
 * none (network failure, unexpected body, or a 500 whose detail is not meant for users).
 */
export function problemDetail(error: unknown, fallback: string, status?: number): string {
  if (status !== undefined && status >= 500) return fallback;
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return fallback;
}

/** Newest first, as the API returns them; merges a further page without duplicates. */
export function mergeResumePages(current: ResumeVersion[], next: ResumeVersion[]): ResumeVersion[] {
  const seen = new Set(current.map((r) => r.id));
  return [...current, ...next.filter((r) => !seen.has(r.id))];
}
