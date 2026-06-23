/**
 * Shared auth + rate-limit guard for /api/v1/* route handlers.
 *
 * Returns null if the request is allowed, or a NextResponse to return
 * immediately (401, 429, 503).
 */

import type { NextRequest } from "next/server";
import type { SupabaseClient } from "@supabase/supabase-js";
import { checkApiKey, hashApiKey } from "./api-auth";
import { checkRateLimit } from "./api-rate-limit";
import { apiError } from "./api-response";

export interface GuardContext {
  apiKeyHash: string;
  startMs: number;
}

export async function apiGuard(
  request: NextRequest,
  db: SupabaseClient,
  endpoint: string
): Promise<{ error: ReturnType<typeof apiError>; ctx: null } | { error: null; ctx: GuardContext }> {
  const startMs = performance.now();
  const apiKey = process.env.INSIDEWATCH_API_KEY ?? null;
  const authResult = checkApiKey(request.headers.get("authorization"), apiKey);

  if (authResult === "no-key") {
    return { error: apiError("API not configured.", 503), ctx: null };
  }
  if (authResult !== "ok") {
    return {
      error: new Response(JSON.stringify({ error: "Unauthorized." }), {
        status: 401,
        headers: {
          "Content-Type": "application/json",
          "WWW-Authenticate": 'Bearer realm="InsideWatch API v1"',
        },
      }) as ReturnType<typeof apiError>,
      ctx: null,
    };
  }

  const apiKeyHash = hashApiKey(apiKey!);
  const url = new URL(request.url);
  const rl = await checkRateLimit(db, apiKeyHash, endpoint, startMs, {
    queryParams: url.search || undefined,
  });

  if (!rl.allowed) {
    return {
      error: apiError("Rate limit exceeded. Try again later.", 429, {
        retry_after_seconds: 60,
      }),
      ctx: null,
    };
  }

  return { error: null, ctx: { apiKeyHash, startMs } };
}
