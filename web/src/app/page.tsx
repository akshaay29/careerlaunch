import Link from "next/link";

import { HealthStatus } from "@/components/health-status";
import { LOGIN_PATH, SIGNUP_PATH } from "@/lib/auth-paths";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-6 px-6 py-24">
      <h1 className="text-3xl font-semibold tracking-tight">CareerLaunch</h1>
      <nav className="flex gap-4">
        <Link className="underline" href={SIGNUP_PATH}>
          Sign up
        </Link>
        <Link className="underline" href={LOGIN_PATH}>
          Log in
        </Link>
      </nav>
      <HealthStatus />
    </main>
  );
}
