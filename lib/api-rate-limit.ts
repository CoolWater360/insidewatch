/**
 * Rate limiting and audit logging for /api/v1/* endpoints.
 *
 * Approach: every authenticated request is logged to api_audit_log, and
 * the row count in the last windowSeconds for the same api_key_hash is
 * compared against the limit.  For a private institutional API with a
 * small number of callers this is accurate and requires no external state.
 *
 * Limit: 60 requests per minute per API key (configurable via RATE_LIMIT_RPM).
 */

import type { SupabaseClient } from "@supabase/supabase-js";

export interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  limit: number;
  resetAt: string;
}

function rpmLimit(): number {
  const env = process.env.RATE_LIMIT_RPM;
  if (!env) return 60;
  const n = parseInt(env, 10);
  return isNaN(n) || n < 1 ? 60 : n;
}

/**
 * Check rate limit and log the request to api_audit_log.
 *
 * Call this AFTER checkApiKey() confirms auth.  Pass startMs from the
 * beginning of the request handler to record accurate response latency.
 */
export async function checkRateLimit(
  db: SupabaseClient,
  apiKeyHash: string,
  endpoint: string,
  startMs: number,
  opts?: { queryParams?: string; statusCode?: number; windowSeconds?: number }
): Promise<RateLimitResult> {
  const windowSeconds = opts?.windowSeconds ?? 60;
  const limit = rpmLimit();
  const windowStart = new Date(Date.now() - windowSeconds * 1000).toISOString();
  const resetAt = new Date(Date.now() + windowSeconds * 1000).toISOString();

  // Count requests in the current window.
  const { count } = await db
    .from("api_audit_log")
    .select("id", { count: "exact", head: true })
    .eq("api_key_hash", apiKeyHash)
    .gte("created_at", windowStart);

  const used = count ?? 0;

  // Always log the request (including rate-limited ones, at status 429).
  const responseMs = Math.round(performance.now() - startMs);
  void db
    .from("api_audit_log")
    .insert({
      api_key_hash: apiKeyHash,
      endpoint,
      method:       "GET",
      query_params: opts?.queryParams ?? null,
      status_code:  used >= limit ? 429 : (opts?.statusCode ?? null),
      response_ms:  responseMs,
    })
    .then(null, () => {
      // Non-fatal: audit log failure must not break the API response.
    });

  if (used >= limit) {
    return { allowed: false, remaining: 0, limit, resetAt };
  }

  return { allowed: true, remaining: limit - used - 1, limit, resetAt };
}
