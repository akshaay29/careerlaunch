// Next 16 proxy (formerly middleware): refreshes the Supabase session cookie on every page
// request and applies the auth redirects. Authorization itself is enforced by the API.
import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";

import {
  GUEST_ONLY_PATHS,
  isProtectedPath,
  LOGIN_PATH,
  ONBOARDING_PATH,
} from "@/lib/auth-paths";
import { hasSupabaseEnv, supabaseEnv } from "@/lib/supabase/env";

export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });
  if (!hasSupabaseEnv()) return response;

  const { url, anonKey } = supabaseEnv();
  const supabase = createServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet, headers) {
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        );
        Object.entries(headers ?? {}).forEach(([key, value]) => response.headers.set(key, value));
      },
    },
  });

  // Verifies the access token (refreshing it if needed). Do not run code between creating the
  // client and this call.
  const { data } = await supabase.auth.getClaims();
  const signedIn = Boolean(data?.claims?.sub);
  const { pathname, search } = request.nextUrl;

  const redirectTo = (target: URL) => {
    const redirect = NextResponse.redirect(target);
    // Keep any refreshed session cookies.
    response.cookies.getAll().forEach((cookie) => redirect.cookies.set(cookie));
    return redirect;
  };

  if (!signedIn && isProtectedPath(pathname)) {
    const target = request.nextUrl.clone();
    target.pathname = LOGIN_PATH;
    target.search = `?next=${encodeURIComponent(pathname + search)}`;
    return redirectTo(target);
  }
  if (signedIn && GUEST_ONLY_PATHS.includes(pathname)) {
    const target = request.nextUrl.clone();
    target.pathname = ONBOARDING_PATH;
    target.search = "";
    return redirectTo(target);
  }
  return response;
}

export const config = {
  matcher: [
    // Every page except Next internals and static files.
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
