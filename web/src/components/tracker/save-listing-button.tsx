"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { saveListing } from "@/api";
import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth-paths";
import { SAVED_LABEL, SAVE_LABEL, SAVING_LABEL, trackerPath } from "@/lib/tracker";

/**
 * "Save" for a Listing. Saving is idempotent on the API, so a card does not need to know the
 * saved state first: once saved (or when `applicationId` is given) it becomes "Saved · Open",
 * a link to the Application. `applicationId` is read once: remount (key) to change it.
 */
export function SaveListingButton({
  listingId,
  applicationId = null,
}: {
  listingId: string;
  applicationId?: string | null;
}) {
  const [saved, setSaved] = useState<string | null>(applicationId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [signedOut, setSignedOut] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  async function onSave() {
    setBusy(true);
    setError(null);
    let message: string | null = null;
    let id: string | null = null;
    let ended = false;
    try {
      const { data, response } = await saveListing({ body: { listing_id: listingId } });
      if (data) id = data.id;
      else if (response?.status === 401) ended = true;
      else if (response?.status === 404) message = "This Listing is no longer available.";
      else message = "Could not save. Try again.";
    } catch {
      message = "Could not save. Try again.";
    }
    if (!mounted.current) return;
    setBusy(false);
    if (id) setSaved(id);
    setSignedOut(ended);
    setError(message);
  }

  if (saved) {
    return (
      <Button asChild size="sm" variant="outline">
        <Link href={trackerPath(saved)} data-testid="listing-saved">
          {SAVED_LABEL}
        </Link>
      </Button>
    );
  }
  return (
    <span className="flex flex-col items-end gap-1">
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={busy}
        onClick={onSave}
        data-testid="listing-save"
      >
        {busy ? SAVING_LABEL : SAVE_LABEL}
      </Button>
      {signedOut ? (
        <span role="alert" className="text-xs">
          Your session has ended.{" "}
          <Link className="underline" href={LOGIN_PATH}>
            Log in again
          </Link>
        </span>
      ) : null}
      {error ? (
        <span role="alert" className="text-xs text-destructive">
          {error}
        </span>
      ) : null}
    </span>
  );
}
