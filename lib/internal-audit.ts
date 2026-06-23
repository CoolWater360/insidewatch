/**
 * Shared security and audit utilities for /api/internal/* route handlers.
 *
 * Authentication model (temporary):
 *   All /internal routes share a single INTERNAL_SECRET (HTTP Basic Auth password).
 *   The acting identity is recorded as INTERNAL_ACTOR_LABEL in every audit row.
 *   This is a shared-admin model — it does NOT provide individual user attribution.
 *   Replace with per-user identity before expanding access beyond a single operator.
 *
 * CSRF protection:
 *   Browser-initiated cross-origin POSTs are rejected via Origin header check.
 *   Requests without an Origin header (curl, server-to-server) are allowed through.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

// ─── Actor ────────────────────────────────────────────────────────────────────

/**
 * Returns the actor label to record in audit rows.
 * Set INTERNAL_ACTOR_LABEL to identify which deployment or person is acting.
 * Defaults to "shared-admin" — a clear signal that attribution is not individual.
 */
export function getActor(): string {
  return process.env.INTERNAL_ACTOR_LABEL ?? "shared-admin";
}

// ─── Basic Auth helper ────────────────────────────────────────────────────────

function decodeBase64(s: string): string {
  // Buffer is available in Node.js (route handlers, tests).
  // atob is available in Edge Runtime (middleware).
  // This helper is imported by route handlers (Node.js), so Buffer is safe.
  if (typeof Buffer !== "undefined") {
    return Buffer.from(s, "base64").toString("utf-8");
  }
  // Fallback for Edge Runtime if this file is ever imported there.
  // eslint-disable-next-line no-undef
  return atob(s);
}

export type AuthResult = "ok" | "no-secret" | "no-auth" | "wrong-password";

/**
 * Pure function — testable without Next.js.
 * Checks an HTTP Basic Auth header against the configured secret.
 * Username is ignored; only the password is compared.
 */
export function checkBasicAuth(
  authHeader: string | null,
  secret: string | null
): AuthResult {
  if (!secret) return "no-secret";
  if (!authHeader || !authHeader.startsWith("Basic ")) return "no-auth";
  const decoded = decodeBase64(authHeader.slice(6));
  const colonIdx = decoded.indexOf(":");
  const password = colonIdx === -1 ? decoded : decoded.slice(colonIdx + 1);
  return password === secret ? "ok" : "wrong-password";
}

// ─── CSRF / Origin check ──────────────────────────────────────────────────────

/**
 * Pure function — testable without Next.js.
 * Returns false if a cross-origin browser request is detected.
 * Requests without an Origin header are permitted (non-browser / same-origin fetch).
 */
export function checkOrigin(origin: string | null, host: string): boolean {
  // No Origin header at all → server-to-server / curl / same-origin fetch; allow.
  // Empty string Origin header is anomalous — treat as invalid rather than absent.
  if (origin === null || origin === undefined) return true;
  if (origin === "") return false;
  try {
    const { host: originHost } = new URL(origin);
    return originHost === host;
  } catch {
    return false; // malformed Origin
  }
}

// ─── Audit log ────────────────────────────────────────────────────────────────

export type EntityType = "transaction" | "filing" | "unmatched_issuer";

export interface AuditEvent {
  actionType: string;       // 'confirm' | 'reject' | 'reclassify' | 'retry_filing' |
                            // 'resolve_issuer' | 'reject_issuer' | 'add_review_note'
  entityType: EntityType;
  entityId: number;
  actor: string;            // value of INTERNAL_ACTOR_LABEL
  beforeValues?: Record<string, unknown> | null;
  afterValues?: Record<string, unknown> | null;
}

/**
 * Appends an immutable row to internal_audit_log.
 * Non-fatal: logs on failure rather than propagating — the primary action
 * must not be blocked by an audit write failure.
 */
export async function logAudit(
  db: SupabaseClient,
  event: AuditEvent
): Promise<void> {
  const { error } = await db.from("internal_audit_log").insert({
    action_type:   event.actionType,
    entity_type:   event.entityType,
    entity_id:     event.entityId,
    actor:         event.actor,
    before_values: event.beforeValues ?? null,
    after_values:  event.afterValues  ?? null,
  });
  if (error) {
    // Audit failure is always logged but never blocks the action.
    console.error(
      `[audit] Failed to write ${event.actionType} on ${event.entityType}#${event.entityId}:`,
      error.message
    );
  }
}
