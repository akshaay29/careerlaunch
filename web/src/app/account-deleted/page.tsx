import type { Metadata } from "next";
import Link from "next/link";

import { SIGNUP_PATH } from "@/lib/auth-paths";

export const metadata: Metadata = { title: "Account deleted · CareerLaunch" };

// Public (not under a protected prefix): the user was signed out when the account was deleted.
export default function AccountDeletedPage() {
  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">Your account has been deleted</h1>
      <p className="text-sm text-muted-foreground">
        Your account and its data have been deleted, and you have been signed out. Stored files
        and your sign-in are usually removed within minutes. If that fails, we retry
        automatically every day until they are gone.
      </p>
      <Link className="text-sm underline" href={SIGNUP_PATH}>
        Create a new account
      </Link>
    </main>
  );
}
