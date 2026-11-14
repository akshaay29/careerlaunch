// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { DocumentOut } from "../api/types.gen.ts";
import {
  DOCUMENT_ACCEPT,
  DOCUMENT_MAX_BYTES,
  DOCUMENT_TOO_LARGE_MESSAGE,
  DOCUMENT_WRONG_TYPE_MESSAGE,
  documentTypeLabel,
  mergeDocumentPages,
  precheckDocumentFile,
} from "./documents.ts";

const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

function doc(id: string): DocumentOut {
  return {
    id,
    filename: `${id}.txt`,
    content_type: "text/plain",
    size_bytes: 10,
    created_at: "2026-11-14T00:00:00Z",
  };
}

test("precheck accepts PDF, DOCX and plain text up to 5 MiB", () => {
  assert.equal(precheckDocumentFile({ name: "a.pdf", size: 1, type: "application/pdf" }), null);
  assert.equal(precheckDocumentFile({ name: "a.docx", size: 1, type: DOCX }), null);
  assert.equal(precheckDocumentFile({ name: "notes.TXT", size: 1, type: "" }), null);
  assert.equal(
    precheckDocumentFile({ name: "notes", size: DOCUMENT_MAX_BYTES, type: "text/plain; charset=utf-8" }),
    null,
  );
});

test("precheck lets any text type or an unknown type through to the server", () => {
  for (const file of [
    { name: "readme.md", size: 1, type: "text/markdown" },
    { name: "grades.csv", size: 1, type: "text/csv" },
    { name: "build.log", size: 1, type: "" },
    { name: "noext", size: 1, type: "" },
    { name: "page.html", size: 1, type: "text/html" },
  ]) {
    assert.equal(precheckDocumentFile(file), null, file.name);
  }
  assert.equal(
    precheckDocumentFile({ name: "notes.md", size: DOCUMENT_MAX_BYTES + 1, type: "text/markdown" }),
    DOCUMENT_TOO_LARGE_MESSAGE,
  );
});

test("precheck rejects other types", () => {
  assert.equal(
    precheckDocumentFile({ name: "photo.png", size: 1, type: "image/png" }),
    DOCUMENT_WRONG_TYPE_MESSAGE,
  );
  assert.equal(
    precheckDocumentFile({ name: "archive.zip", size: 1, type: "application/zip" }),
    DOCUMENT_WRONG_TYPE_MESSAGE,
  );
  assert.equal(
    precheckDocumentFile({ name: "app.exe", size: 1, type: "application/x-msdownload" }),
    DOCUMENT_WRONG_TYPE_MESSAGE,
  );
});

test("precheck rejects files over 5 MiB", () => {
  assert.equal(
    precheckDocumentFile({ name: "a.pdf", size: DOCUMENT_MAX_BYTES + 1, type: "application/pdf" }),
    DOCUMENT_TOO_LARGE_MESSAGE,
  );
});

test("the picker offers the three types", () => {
  for (const part of [".pdf", ".docx", ".txt", ".md", "application/pdf", DOCX, "text/plain", "text/*"]) {
    assert.ok(DOCUMENT_ACCEPT.split(",").includes(part), part);
  }
});

test("type labels", () => {
  assert.equal(documentTypeLabel("application/pdf"), "PDF");
  assert.equal(documentTypeLabel(DOCX), "DOCX");
  assert.equal(documentTypeLabel("text/plain"), "Text");
});

test("mergeDocumentPages appends without duplicates", () => {
  const merged = mergeDocumentPages([doc("c"), doc("b")], [doc("b"), doc("a")]);
  assert.deepEqual(
    merged.map((d) => d.id),
    ["c", "b", "a"],
  );
});
