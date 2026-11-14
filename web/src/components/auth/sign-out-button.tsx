"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth-paths";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export function SignOutButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onClick() {
    setPending(true);
    setError(null);
    let failed = false;
    try {
      // Clears the session cookies; the proxy then treats the browser as signed out.
      const { error } = await getSupabaseBrowserClient().auth.signOut();
      failed = Boolean(error);
    } catch {
      failed = true;
    }
    if (failed) {
      setError("Could not log out. Try again.");
      setPending(false);
      return;
    }
    router.replace(LOGIN_PATH);
    router.refresh();
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button type="button" variant="outline" onClick={onClick} disabled={pending}>
        Log out
      </Button>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
