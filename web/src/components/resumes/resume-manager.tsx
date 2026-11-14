"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  type ResumeVersion,
  activateResume,
  deleteResume,
  getResumeDownload,
  listResumes,
  uploadResume,
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
  RESUME_ACCEPT,
  formatBytes,
  mergeResumePages,
  precheckResumeFile,
  problemDetail,
  resumeTypeLabel,
} from "@/lib/resumes";

const PAGE_SIZE = 20;

type Load =
  | { kind: "loading" }
  | { kind: "ok"; items: ResumeVersion[]; nextCursor: string | null }
  | { kind: "signed-out" }
  | { kind: "error"; message: string };

async function fetchPage(cursor?: string): Promise<Load> {
  try {
    const { data, response } = await listResumes({
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
 * Upload Resume versions and manage them: make one active, download (short-lived signed
 * link), delete (confirmed). Used by onboarding step 2 and /settings/resumes. Filenames and
 * server messages are rendered as plain text.
 */
export function ResumeManager() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => setLoad(await fetchPage()), []);

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
    const precheck = precheckResumeFile(file);
    if (precheck) {
      setMessage(precheck);
      // Clear the input so choosing the same file again fires onChange.
      if (fileInput.current) fileInput.current.value = "";
      return;
    }
    start("upload");
    try {
      const { data, error, response } = await uploadResume({ body: { file } });
      if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
        return;
      }
      if (data) {
        setNotice(
          data.is_active
            ? `Uploaded "${data.filename}". It is your active Resume.`
            : `Uploaded "${data.filename}". Choose "Make active" to use it instead.`,
        );
        await refresh();
      } else {
        setMessage(problemDetail(error, "Could not upload your Resume. Try again.", response?.status));
      }
    } catch {
      setMessage("Could not upload your Resume. Try again.");
    } finally {
      setBusy(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function makeActive(item: ResumeVersion) {
    start(item.id);
    try {
      const { data, error, response } = await activateResume({ path: { resume_id: item.id } });
      if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
        return;
      }
      if (data) await refresh();
      else setMessage(problemDetail(error, "Could not change your active Resume. Try again.", response?.status));
    } catch {
      setMessage("Could not change your active Resume. Try again.");
    } finally {
      setBusy(null);
    }
  }

  async function download(item: ResumeVersion) {
    start(item.id);
    try {
      const { data, error, response } = await getResumeDownload({ path: { resume_id: item.id } });
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

  async function remove(item: ResumeVersion) {
    start(item.id);
    try {
      const { error, response } = await deleteResume({ path: { resume_id: item.id } });
      if (response?.status === 401) {
        setLoad({ kind: "signed-out" });
        return;
      }
      if (response?.ok) {
        setNotice(
          item.is_active
            ? `Deleted "${item.filename}". You have no active Resume now: make another version active or upload one.`
            : `Deleted "${item.filename}".`,
        );
        await refresh();
      } else {
        setMessage(problemDetail(error, "Could not delete this version. Try again.", response?.status));
      }
    } catch {
      setMessage("Could not delete this version. Try again.");
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
          ? { kind: "ok", items: mergeResumePages(prev.items, next.items), nextCursor: next.nextCursor }
          : next,
      );
    } else if (next.kind === "signed-out") {
      setLoad(next);
    } else {
      setMessage("Could not load more versions. Try again.");
    }
  }

  if (load.kind === "loading") {
    return <p className="text-muted-foreground">Loading your Resumes…</p>;
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
        Could not load your Resumes ({load.message}).
      </p>
    );
  }

  const { items, nextCursor } = load;
  return (
    <div className="flex flex-col gap-6" data-testid="resume-manager">
      <section className="flex flex-col gap-3 rounded-lg border p-5">
        <h2 className="text-lg font-medium">Upload a Resume</h2>
        <p className="text-sm text-muted-foreground">
          PDF or DOCX, 5 MB or smaller. You can keep several versions; one of them is active.
        </p>
        <div>
          <input
            ref={fileInput}
            type="file"
            accept={RESUME_ACCEPT}
            className="sr-only"
            id="resume-file"
            data-testid="resume-file"
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
        <p role="alert" className="text-sm text-destructive" data-testid="resume-error">
          {message}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className="text-sm" data-testid="resume-notice">
          {notice}
        </p>
      ) : null}

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Your versions</h2>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">You have not uploaded a Resume yet.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {items.map((item) => (
              <li
                key={item.id}
                className="flex flex-col gap-3 rounded-lg border p-4"
                data-testid={`resume-${item.id}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <p className="font-medium break-all">{item.filename}</p>
                  {item.is_active ? (
                    <span className="rounded-full border px-2 py-0.5 text-xs" data-testid="resume-active">
                      Active
                    </span>
                  ) : null}
                </div>
                <p className="text-xs text-muted-foreground">
                  {resumeTypeLabel(item.content_type)} · {formatBytes(item.size_bytes)} · uploaded{" "}
                  {formatTime(item.created_at)}
                </p>
                <div className="flex flex-wrap gap-2">
                  {!item.is_active ? (
                    <Button type="button" onClick={() => makeActive(item)} disabled={busy !== null}>
                      Make active
                    </Button>
                  ) : null}
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
                        <AlertDialogTitle>Delete this version?</AlertDialogTitle>
                        <AlertDialogDescription>
                          {item.is_active
                            ? `"${item.filename}" is your active Resume. After deleting it, no version will be active until you choose one or upload another. This cannot be undone.`
                            : `"${item.filename}" will be deleted. This cannot be undone.`}
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
              Show older versions
            </Button>
          </div>
        ) : null}
      </section>
    </div>
  );
}
