// The ONLY way `web` talks to the backend: the generated client in src/api/ (AD-1, AD-14).
// `web` never touches Postgres or Storage; Supabase is used for Auth only (Story 1.3).
// The generated client calls createClientConfig() on initialisation (openapi-ts
// `runtimeConfigPath`), so every SDK call gets the base URL regardless of import order.
import type { CreateClientConfig } from "@/api/client.gen";

// `||` (not `??`): an empty NEXT_PUBLIC_API_URL also falls back to localhost.
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(
  /\/+$/,
  "",
);

export const createClientConfig: CreateClientConfig = (config) => ({
  ...config,
  baseUrl: API_URL,
});
