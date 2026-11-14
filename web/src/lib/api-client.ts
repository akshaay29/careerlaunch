// The ONLY way `web` talks to the backend: the generated client in src/api/ (AD-1, AD-14).
// `web` never touches Postgres or Storage; Supabase is used for Auth only.
// The generated client calls createClientConfig() on initialisation (openapi-ts
// `runtimeConfigPath`), so every SDK call gets the base URL and the auth hook regardless of
// import order.
import type { CreateClientConfig } from "@/api/client.gen";

import { getSupabaseBrowserClient } from "./supabase/client";
import { hasSupabaseEnv } from "./supabase/env";

// `||` (not `??`): an empty NEXT_PUBLIC_API_URL also falls back to localhost.
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(
  /\/+$/,
  "",
);

/**
 * The access token of the cookie session, for operations whose OpenAPI security is the bearer
 * scheme (the generated SDK sends `Authorization: Bearer <token>`). Public operations declare
 * no security, so this is never called for them. Browser only for now: server-side callers
 * would pass `auth` explicitly.
 */
async function accessToken(): Promise<string | undefined> {
  if (typeof window === "undefined" || !hasSupabaseEnv()) return undefined;
  const { data } = await getSupabaseBrowserClient().auth.getSession();
  return data.session?.access_token ?? undefined;
}

export const createClientConfig: CreateClientConfig = (config) => ({
  ...config,
  baseUrl: API_URL,
  auth: accessToken,
});
