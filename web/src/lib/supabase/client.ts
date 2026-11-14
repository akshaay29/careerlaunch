// Browser Supabase client (Auth only). The session lives in cookies (@supabase/ssr), so the
// proxy and route handlers see the same session. Create it lazily (in handlers/effects), never
// at module scope: the build has no Supabase env.
import { createBrowserClient } from "@supabase/ssr";

import { supabaseEnv } from "./env";

export function getSupabaseBrowserClient() {
  const { url, anonKey } = supabaseEnv();
  // createBrowserClient returns a singleton in the browser.
  return createBrowserClient(url, anonKey);
}
