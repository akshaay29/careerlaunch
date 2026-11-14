import { safeNextPath } from "@/lib/auth-paths";

/** Absolute URLs Supabase redirects back to. Browser only (uses window.location). */
export function authRedirects(next?: string | null) {
  const origin = window.location.origin;
  const target = safeNextPath(next);
  return {
    // OAuth and PKCE links (the default Supabase email template) come back here.
    callback: `${origin}/auth/callback?next=${encodeURIComponent(target)}`,
    next: target,
  };
}
