/**
 * Bearer-token authentication for /api/v1/* endpoints.
 *
 * The API key is stored in INSIDEWATCH_API_KEY (server-side only).
 * It is never exposed to the browser and must never appear in
 * NEXT_PUBLIC_* variables.
 *
 * Uses Node's timingSafeEqual to prevent timing-attack enumeration.
 */

import crypto from "crypto";

export type ApiAuthResult = "ok" | "no-key" | "no-auth" | "invalid";

/**
 * Pure function — testable without Next.js.
 * Validates a Bearer token against the configured API key.
 */
export function checkApiKey(
  authHeader: string | null,
  apiKey: string | null
): ApiAuthResult {
  if (!apiKey) return "no-key";
  if (!authHeader?.startsWith("Bearer ")) return "no-auth";

  const provided = authHeader.slice(7);
  if (!provided) return "no-auth";

  try {
    const a = Buffer.from(provided);
    const b = Buffer.from(apiKey);
    // timingSafeEqual requires equal-length buffers; pad to match before comparing.
    if (a.length !== b.length) return "invalid";
    return crypto.timingSafeEqual(a, b) ? "ok" : "invalid";
  } catch {
    return "invalid";
  }
}

/** SHA-256 hex digest of a key — safe to store in audit logs. */
export function hashApiKey(key: string): string {
  return crypto.createHash("sha256").update(key).digest("hex");
}
