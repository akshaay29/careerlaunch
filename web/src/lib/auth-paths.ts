// Route names shared by the proxy, pages and route handlers.

export const LOGIN_PATH = "/login";
export const SIGNUP_PATH = "/signup";
export const ONBOARDING_PATH = "/onboarding";
export const AUTH_ERROR_PATH = "/auth/error";

/** Pages only a signed-in user may see (the proxy redirects others to /login). */
export const PROTECTED_PREFIXES = [ONBOARDING_PATH];

/** Pages a signed-in user is sent away from (to /onboarding). */
export const GUEST_ONLY_PATHS = [LOGIN_PATH, SIGNUP_PATH];

export function isProtectedPath(pathname: string): boolean {
  return PROTECTED_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

// Control characters (URL parsers strip tabs/newlines, turning "/\t/x" into "//x") and
// backslashes (treated as "/" by URL parsers) are never valid in a destination.
const UNSAFE_CHARS = /[\u0000-\u001f\u007f\\]/;
const PROBE_ORIGIN = "http://x";

/**
 * A post-login destination taken from the URL. Only same-origin absolute paths are allowed,
 * so `?next=` cannot be used as an open redirect.
 */
export function safeNextPath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || UNSAFE_CHARS.test(next)) {
    return ONBOARDING_PATH;
  }
  try {
    if (new URL(next, PROBE_ORIGIN).origin !== PROBE_ORIGIN) return ONBOARDING_PATH;
  } catch {
    return ONBOARDING_PATH;
  }
  return next;
}
