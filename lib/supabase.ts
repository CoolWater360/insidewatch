import { createClient, SupabaseClient } from "@supabase/supabase-js";

// ── Frontend Supabase client — ANON KEY ONLY ──────────────────────────────────
// This client is used exclusively for read operations from Next.js server
// components.  It must always use the anon/public key.
//
// IMPORTANT: Do NOT substitute SUPABASE_SERVICE_ROLE_KEY here, even server-side.
// The service-role key lives only in the Python scraper environment and GitHub
// Actions secrets (SUPABASE_SERVICE_ROLE_KEY).  See .env.local.example.
//
// Falls back to the conventional NEXT_PUBLIC_* names if preferred.
const url = process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.SUPABASE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const isSupabaseConfigured = Boolean(url && key);

let cached: SupabaseClient | null = null;

/**
 * Returns a singleton Supabase client, or null if credentials are not set.
 * Callers must handle the null case (the app renders a config notice).
 */
export function getSupabase(): SupabaseClient | null {
  if (!isSupabaseConfigured) return null;
  if (!cached) {
    cached = createClient(url as string, key as string, {
      auth: { persistSession: false },
    });
  }
  return cached;
}
