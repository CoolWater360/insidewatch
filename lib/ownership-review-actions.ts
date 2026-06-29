/**
 * Phase 17B.6 — server-side mutation actions for the Ownership Review Queue.
 *
 * Service-role only. Each action is EXPLICIT, named, and limited to:
 *   - review-status changes (approve → confirmed, reject → rejected), or
 *   - an operator-approved entity_type change (only when a type is selected).
 *
 * Raw source facts (percentages, dates, names, source_url, hashes) are NEVER
 * edited here.
 *
 * Auditability: ownership_events and entity_relationships carry reviewed_by /
 * reviewed_at columns (migrations 017/018) — these are written on every action
 * so the who/when/state is recorded in-row. The entities table has no
 * reviewed_by/reviewed_at columns (migration 016); for entities only
 * review_status + updated_at are written. The central internal_audit_log table
 * is NOT used because its entity_type CHECK excludes ownership types — see
 * docs/ownership-review-ui.md ("Audit gap"). No broad new audit system is added.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { isEntityType } from "./ownership-review";

export interface ActionResult {
  ok: boolean;
  error?: string;
}

function nowUtc(): string {
  return new Date().toISOString();
}

// ─── Entities ───────────────────────────────────────────────────────────────

export async function reviewEntity(
  db: SupabaseClient,
  entityId: number,
  decision: "approve" | "reject",
  actor: string
): Promise<ActionResult> {
  const review_status = decision === "approve" ? "confirmed" : "rejected";
  const { error } = await db
    .from("entities")
    .update({ review_status, updated_at: nowUtc() })
    .eq("id", entityId);
  // actor is recorded centrally via getActor(); entities has no reviewed_by col.
  void actor;
  return error ? { ok: false, error: error.message } : { ok: true };
}

/**
 * Apply an operator-approved entity_type correction. Only fires when the
 * operator explicitly selects a valid type. Marks the entity confirmed.
 */
export async function setEntityType(
  db: SupabaseClient,
  entityId: number,
  newType: string,
  actor: string
): Promise<ActionResult> {
  if (!isEntityType(newType)) {
    return { ok: false, error: `invalid entity_type: ${newType}` };
  }
  void actor;
  const { error } = await db
    .from("entities")
    .update({
      entity_type: newType,
      review_status: "confirmed",
      updated_at: nowUtc(),
    })
    .eq("id", entityId);
  return error ? { ok: false, error: error.message } : { ok: true };
}

// ─── Ownership events ─────────────────────────────────────────────────────────

export async function reviewOwnershipEvent(
  db: SupabaseClient,
  eventId: number,
  decision: "approve" | "reject",
  actor: string
): Promise<ActionResult> {
  const review_status = decision === "approve" ? "confirmed" : "rejected";
  const now = nowUtc();
  const { error } = await db
    .from("ownership_events")
    .update({
      review_status,
      reviewed_by: actor,
      reviewed_at: now,
      updated_at: now,
    })
    .eq("id", eventId)
    .eq("is_current", true);
  return error ? { ok: false, error: error.message } : { ok: true };
}

// ─── Entity relationships ──────────────────────────────────────────────────────

export async function reviewRelationship(
  db: SupabaseClient,
  relationshipId: number,
  decision: "approve" | "reject",
  actor: string
): Promise<ActionResult> {
  const review_status = decision === "approve" ? "confirmed" : "rejected";
  const now = nowUtc();
  const { error } = await db
    .from("entity_relationships")
    .update({
      review_status,
      reviewed_by: actor,
      reviewed_at: now,
      updated_at: now,
    })
    .eq("id", relationshipId)
    .eq("is_current", true);
  return error ? { ok: false, error: error.message } : { ok: true };
}
