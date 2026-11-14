"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  type DocumentOut,
  deleteDocument,
  getDocumentDownload,
  listDocuments,
  uploadDocument,
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
import {
  DOCUMENT_ACCEPT,
  documentTypeLabel,
  mergeDocumentPages,
  precheckDocumentFile,
} from "@/lib/documents";
import { formatBytes, problemDetail } from "@/lib/resumes";

const PAGE_SIZE = 20;

type Load =
  | { kind: "loading" }
  | { kind: "ok"; items: DocumentOut[]; nextCursor: string | null }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

async function fetchPage(cursor?: string): Promise<Load> {
  try {
    const { data, response } = await listDocuments({
      query: cursor ? { limit: PAGE_SIZE, cursor } : { limit: PAGE_SIZE },
    });
    if (data) return { kind: "ok", items: data.items, nextCursor: data.next_cursor ?? null };
    if (response?.status === 401) return { kind: "signed-out" };
    return { kind: "error", message: `HTTP ${response?.status ?? "?"}` };
  } catch {
    return { kind: "error", message: "API unreachable" };
  }
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

/**
 * Store other application files (cover letters, transcripts, certificates) and manage them:
 * download (short-lived signed link) and delete (confirmed). Used by /settings/documents.
 * Filenames and server messages are rendered as plain text; contents are never previewed.
 */
export function DocumentManager() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // After an upload or delete: a failed reload keeps the current list (with a message)
  // instead of replacing the manager with a load error. A 401 still signs out.
  const refresh = useCallback(async () => {
    const next = await fetchPage();
    if (next.kind === "error") {
      setMessage("Could not refresh your Documents. Reload the page to see the latest list.");
    } else {
      setLoad(next);
    }
  }, []);

  useEffect(() => {
    let active = true;
    fetchPage().then((next) => {
      if (active) setLoad(next);
    });
    return () => {
      active = false;
    };
  }, []);

  function start(id: string) {
    setBusy(id);
    setMessage(null);
    setNotice(null);
  }

  async function upload(file: File) {
    const precheck = precheckDocumentFile(file);
    if (precheck) {
      setNotice(null);
      setMessage(precheck);
      // Clear the input so choosing the same file again fires onChange.
      if (fileInput.current) fileInput.current.value = "";
      return;
    }
    start("upload");
    try {
      const { data, error, response } = await uploadDocument({ body: { file } });
      if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
        return;
      }
      if (data) {
        setNotice(`Uploaded "${data.filename}".`);
        await refresh();
      } else {
        setMessage(problemDetail(error, "Could not upload your Document. Try again.", response?.status));
      }
    } catch {
      setMessage("Could not upload your Document. Try again.");
    } finally {
      setBusy(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function download(item: DocumentOut) {
    start(item.id);
    try {
      const { data, error, response } = await getDocumentDownload({
        path: { document_id: item.id },
      });
      if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
        return;
      }
      // The signed link is short-lived and downloads as an attachment; open it right away.
      if (data) window.location.assign(data.url);
      else setMessage(problemDetail(error, "Could not prepare the download. Try again.", response?.status));
    } catch {
      setMessage("Could not prepare the download. Try again.");
    } finally {
      setBusy(null);
    }
  }

  async function remove(item: DocumentOut) {
    start(item.id);
    try {
      const { error, response } = await deleteDocument({ path: { document_id: item.id } });
      if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
        return;
      }
      if (response?.ok) {
        setNotice(`Deleted "${item.filename}".`);
        await refresh();
      } else {
        setMessage(problemDetail(error, "Could not delete this Document. Try again.", response?.status));
      }
    } catch {
      setMessage("Could not delete this Document. Try again.");
    } finally {
      setBusy(null);
    }
  }

  async function loadMore(cursor: string) {
    start("more");
    const next = await fetchPage(cursor);
    setBusy(null);
    if (next.kind === "ok") {
      setLoad((prev) =>
        prev.kind === "ok"
          ? { kind: "ok", items: mergeDocumentPages(prev.items, next.items), nextCursor: next.nextCursor }
          : next,
      );
    } else if (next.kind === "signed-out") {
      setLoad(next);
    } else {
      setMessage("Could not load more Documents. Try again.");
    }
  }

  if (load.kind === "loading") {
    return <p className="text-muted-foreground">Loading your Documents…</p>;
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
        Could not load your Documents ({load.message}).
      </p>
    );
  }

  const { items, nextCursor } = load;
  return (
    <div className="flex flex-col gap-6" data-testid="document-manager">
      <section className="flex flex-col gap-3 rounded-lg border p-5">
        <h2 className="text-lg font-medium">Upload a Document</h2>
        <p className="text-sm text-muted-foreground">
          PDF, DOCX or UTF-8 plain text, 5 MB or smaller: cover letters, transcripts,
          certificates.
        </p>
        <div>
          <input
            ref={fileInput}
            type="file"
            accept={DOCUMENT_ACCEPT}
            className="sr-only"
            id="document-file"
            data-testid="document-file"
            disabled={busy !== null}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void upload(file);
            }}
          />
          <Button
            type="button"
            disabled={busy !== null}
            onClick={() => fileInput.current?.click()}
          >
            {busy === "upload" ? "Uploading…" : "Choose a file"}
          </Button>
        </div>
      </section>

      {message ? (
        <p role="alert" className="text-sm text-destructive" data-testid="document-error">
          {message}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className="text-sm" data-testid="document-notice">
          {notice}
        </p>
      ) : null}

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Your Documents</h2>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">You have not uploaded a Document yet.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {items.map((item) => (
              <li
                key={item.id}
                className="flex flex-col gap-3 rounded-lg border p-4"
                data-testid={`document-${item.id}`}
              >
                <p className="font-medium break-all">{item.filename}</p>
                <p className="text-xs text-muted-foreground">
                  {documentTypeLabel(item.content_type)} · {formatBytes(item.size_bytes)} · uploaded{" "}
                  {formatTime(item.created_at)}
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => download(item)}
                    disabled={busy !== null}
                  >
                    Download
                  </Button>
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button type="button" variant="destructive" disabled={busy !== null}>
                        Delete
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Delete this Document?</AlertDialogTitle>
                        <AlertDialogDescription>
                          {`"${item.filename}" will be deleted. This cannot be undone.`}
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={() => remove(item)}>Delete</AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              </li>
            ))}
          </ul>
        )}
        {nextCursor ? (
          <div>
            <Button
              type="button"
              variant="outline"
              disabled={busy !== null}
              onClick={() => loadMore(nextCursor)}
            >
              Show older Documents
            </Button>
          </div>
        ) : null}
      </section>
    </div>
  );
}
