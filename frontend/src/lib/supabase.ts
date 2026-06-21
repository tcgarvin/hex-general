import { createClient } from "@supabase/supabase-js";

/**
 * Browser-side Supabase client.
 *
 * Reads the project URL and publishable key from NEXT_PUBLIC_ env vars, which
 * Next.js inlines into the client bundle at build time. Both values are
 * browser-safe; row-level security on the database is what protects data.
 *
 * Env vars are validated here at the module boundary so a misconfigured
 * deployment fails loudly at startup rather than on the first query.
 */
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

if (!supabaseUrl) {
  throw new Error("Missing required env var: NEXT_PUBLIC_SUPABASE_URL");
}
if (!supabaseAnonKey) {
  throw new Error("Missing required env var: NEXT_PUBLIC_SUPABASE_ANON_KEY");
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey);
