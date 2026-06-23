import { createClient, SupabaseClient } from "@supabase/supabase-js";

// ── Server-only Supabase client — SERVICE ROLE KEY ────────────────────────────
// Used exclusively by Server Components and API Route Handlers inside
// app/api/internal/*.  NEVER import this file from a client component or
// from any file under app/internal/ that is marked "use client".
//
// The service-role key bypasses RLS and can read/write all tables.
// It must never be set as a NEXT_PUBLIC_* variable.

let cached: SupabaseClient | null = null;

export function getSupabaseServer(): SupabaseClient {
  if (cached) return cached;
  const url = process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    throw new Error(
      "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set for internal operations."
    );
  }
  cached = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  return cached;
}
