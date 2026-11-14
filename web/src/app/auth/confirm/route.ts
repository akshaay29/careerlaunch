// Email confirmation link target (supabase/templates/confirmation.html):
// /auth/confirm?token_hash=...&type=email verifies the token hash and sets the cookie session.
import type { EmailOtpType } from "@supabase/supabase-js";
import { type NextRequest, NextResponse } from "next/server";

import { AUTH_ERROR_PATH, safeNextPath } from "@/lib/auth-paths";
import { getSupabaseServerClient } from "@/lib/supabase/server";

const EMAIL_OTP_TYPES: readonly EmailOtpType[] = [
  "email",
  "signup",
  "magiclink",
  "recovery",
  "invite",
  "email_change",
];

export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const tokenHash = searchParams.get("token_hash");
  const type = searchParams.get("type") as EmailOtpType | null;
  const next = safeNextPath(searchParams.get("next"));

  if (tokenHash && type && EMAIL_OTP_TYPES.includes(type)) {
    const supabase = await getSupabaseServerClient();
    const { error } = await supabase.auth.verifyOtp({ type, token_hash: tokenHash });
    if (!error) {
      return NextResponse.redirect(new URL(next, origin));
    }
  }
  const failed = new URL(AUTH_ERROR_PATH, origin);
  failed.searchParams.set("reason", "confirm");
  return NextResponse.redirect(failed);
}
