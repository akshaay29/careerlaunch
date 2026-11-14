import type { Metadata } from "next";
import Link from "next/link";

import { ResendConfirmation } from "@/components/auth/resend-confirmation";
import { LOGIN_PATH } from "@/lib/auth-paths";

export const metadata: Metadata = { title: "Sign-in link problem · CareerLaunch" };

export default function AuthErrorPage() {
  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">This link is invalid or has expired</h1>
      <p className="text-sm text-muted-foreground">
        Confirmation and sign-in links work once and expire after a while. Send yourself a new
        confirmation email, or log in if you already confirmed.
      </p>
      <ResendConfirmation />
      <Link className="text-sm underline" href={LOGIN_PATH}>
        Back to log in
      </Link>
    </main>
  );
}
