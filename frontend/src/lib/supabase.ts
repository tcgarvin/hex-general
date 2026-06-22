import { createClient } from "@supabase/supabase-js";

/**
 * Browser-side Supabase client.
 *
 * Reads the project URL and publishable key from VITE_ env vars, which Vite
 * inlines into the client bundle at build time. Both values are browser-safe;
 * row-level security on the database is what protects data. The client holds the
 * Supabase Auth session (Phase 7) and refreshes the JWT the API client sends.
 *
 * Env vars are validated here at the module boundary so a misconfigured
 * deployment fails loudly at startup rather than on the first query.
 */
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!supabaseUrl) {
  throw new Error("Missing required env var: VITE_SUPABASE_URL");
}
if (!supabaseAnonKey) {
  throw new Error("Missing required env var: VITE_SUPABASE_ANON_KEY");
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

/**
 * Whether the app is pointed at a local `supabase start` stack. Gates the
 * email/password dev sign-in (local Auth has no Google OAuth — see
 * docs/LOCAL_DEV.md); the deployed app always uses Google SSO.
 */
export const isLocalSupabase = /\/\/(127\.0\.0\.1|localhost)\b/.test(supabaseUrl);
