// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { ResumeVersion } from "../api/types.gen.ts";
import {
  RESUME_MAX_BYTES,
  RESUME_TOO_LARGE_MESSAGE,
  formatBytes,
  mergeResumePages,
  precheckResumeFile,
  problemDetail,
  resumeTypeLabel,
} from "./resumes.ts";

function version(id: string, is_active = false): ResumeVersion {
  return {
    id,
    filename: `${id}.pdf`,
    content_type: "application/pdf",
    size_bytes: 10,
    is_active,
    created_at: "2026-11-14T00:00:00Z",
  };
}

test("precheck rejects only files over 5 MiB", () => {
  assert.equal(precheckResumeFile({ size: RESUME_MAX_BYTES }), null);
  assert.equal(precheckResumeFile({ size: 0 }), null);
  assert.equal(precheckResumeFile({ size: RESUME_MAX_BYTES + 1 }), RESUME_TOO_LARGE_MESSAGE);
});

test("type labels", () => {
  assert.equal(resumeTypeLabel("application/pdf"), "PDF");
  assert.equal(
    resumeTypeLabel("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "DOCX",
  );
});

test("formatBytes", () => {
  assert.equal(formatBytes(812), "812 B");
  assert.equal(formatBytes(35_328), "34.5 KB");
  assert.equal(formatBytes(RESUME_MAX_BYTES), "5.0 MB");
  assert.equal(formatBytes(-1), "?");
  assert.equal(formatBytes(Number.NaN), "?");
});

test("problemDetail shows the server's detail as given", () => {
  assert.equal(
    problemDetail({ type: "/problems/conflict", detail: "Delete an old version first" }, "x", 409),
    "Delete an old version first",
  );
  assert.equal(problemDetail({ detail: "<b>not html</b>" }, "x"), "<b>not html</b>");
});

test("problemDetail falls back for missing details and server errors", () => {
  assert.equal(problemDetail(undefined, "fallback"), "fallback");
  assert.equal(problemDetail("text body", "fallback"), "fallback");
  assert.equal(problemDetail({ detail: 42 }, "fallback"), "fallback");
  assert.equal(problemDetail({ detail: "   " }, "fallback"), "fallback");
  assert.equal(
    problemDetail({ detail: "An unexpected error occurred." }, "fallback", 500),
    "fallback",
  );
});

test("mergeResumePages appends without duplicates", () => {
  const merged = mergeResumePages([version("c", true), version("b")], [version("b"), version("a")]);
  assert.deepEqual(
    merged.map((r) => r.id),
    ["c", "b", "a"],
  );
});
