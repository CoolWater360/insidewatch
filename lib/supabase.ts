import { createClient, SupabaseClient } from "@supabase/supabase-js";

// Read server-side credentials (shared with the Python scraper's .env.local).
// Falls back to the conventional NEXT_PUBLIC_* names if those are used instead.
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
