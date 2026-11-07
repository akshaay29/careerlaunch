// The ONLY way `web` talks to the backend: the generated client in src/api/ (AD-1, AD-14).
// `web` never touches Postgres or Storage; Supabase is used for Auth only (Story 1.3).
import { client } from "@/api/client.gen";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

client.setConfig({ baseUrl: API_URL });

export { client };
