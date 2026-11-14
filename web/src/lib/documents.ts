// Display and precheck rules for Documents (pure, so they can be unit tested with node --test).
// Relative imports: node --test erases type-only imports and cannot resolve the "@/" alias.
import type { DocumentOut } from "../api/types.gen.ts";

export const SETTINGS_DOCUMENTS_PATH = "/settings/documents";

/** The server's limit (5 MiB). The server is the authority; this only saves a doomed upload. */
export const DOCUMENT_MAX_BYTES = 5 * 1024 * 1024;
export const DOCUMENT_TOO_LARGE_MESSAGE = "Document must be 5 MB or smaller";
export const DOCUMENT_WRONG_TYPE_MESSAGE = "Upload a PDF, DOCX or plain-text file";

const PDF = "application/pdf";
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const TEXT = "text/plain";
const EXTENSIONS = [".pdf", ".docx", ".txt"];

/** What the file picker offers. The server still checks the content itself. */
export const DOCUMENT_ACCEPT = [...EXTENSIONS, ".md", ".csv", ".log", PDF, DOCX, TEXT, "text/*"].join(",");

/**
 * A message to show before uploading, or null when the file may be sent. A file is sent when
 * the browser reports PDF, DOCX, any `text/*` type or no type at all (e.g. `.md`, `.csv`,
 * `.log` or no extension), or its name ends in .pdf, .docx or .txt; the server then decides
 * by reading the content.
 */
export function precheckDocumentFile(file: { name: string; size: number; type: string }): string | null {
  const name = file.name.toLowerCase();
  const type = file.type.split(";")[0].trim().toLowerCase();
  const mayBeAccepted = type === "" || type === PDF || type === DOCX || type.startsWith("text/");
  if (!mayBeAccepted && !EXTENSIONS.some((ext) => name.endsWith(ext))) {
    return DOCUMENT_WRONG_TYPE_MESSAGE;
  }
  return file.size > DOCUMENT_MAX_BYTES ? DOCUMENT_TOO_LARGE_MESSAGE : null;
}

/** "PDF", "DOCX" or "Text" for a stored Document's content type. */
export function documentTypeLabel(contentType: DocumentOut["content_type"]): string {
  if (contentType === PDF) return "PDF";
  if (contentType === DOCX) return "DOCX";
  return "Text";
}

/** Newest first, as the API returns them; merges a further page without duplicates. */
export function mergeDocumentPages(current: DocumentOut[], next: DocumentOut[]): DocumentOut[] {
  const seen = new Set(current.map((d) => d.id));
  return [...current, ...next.filter((d) => !seen.has(d.id))];
}
