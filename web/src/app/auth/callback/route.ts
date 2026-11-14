// OAuth (Google) and PKCE redirect target: exchanges the `code` for a cookie session.
import { type NextRequest, NextResponse } from "next/server";

import { AUTH_ERROR_PATH, safeNextPath } from "@/lib/auth-paths";
import { getSupabaseServerClient } from "@/lib/supabase/server";

export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const code = searchParams.get("code");
  const next = safeNextPath(searchParams.get("next"));

  if (code) {
    const supabase = await getSupabaseServerClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(new URL(next, origin));
    }
  }
  const failed = new URL(AUTH_ERROR_PATH, origin);
  failed.searchParams.set("reason", "callback");
  return NextResponse.redirect(failed);
}
