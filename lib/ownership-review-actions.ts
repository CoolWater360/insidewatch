/**
 * Phase 17B.6 / 17B.7 — server-side mutation actions for the Ownership Review
 * Queue.
 *
 * Service-role only. Each action is EXPLICIT, named, and limited to:
 *   - review-status changes (approve → confirmed, reject → rejected), or
 *   - an operator-approved entity_type change (only when a type is selected).
 *
 * Raw source facts (percentages, dates, names, source_url, hashes) are NEVER
 * edited here.
 *
 * Atomicity & audit (Phase 17B.7): every action calls a Postgres RPC
 * (db/migrations/020_ownership_review_audit.sql) that performs the business
 * UPDATE and the internal_audit_log INSERT in one PL/pgSQL transaction —
 * mirroring db/migrations/009_internal_rpc.sql. If either statement fails the
 * whole call rolls back; no partial state, no un-audited change. The RPCs also
 * write reviewed_by / reviewed_at on entities, ownership_events, and
 * entity_relationships.
 */

import type { SupabaseClient } from "@supabase/supabase-js";
import { isEntityType } from "./ownership-review";

export interface ActionResult {
  ok: boolean;
  error?: string;
}

function fromRpc(rpcError: { message: string } | null): ActionResult {
  if (rpcError) return { ok: false, error: rpcError.message };
  return { ok: true };
}

// ─── Entities ───────────────────────────────────────────────────────────────

export async function reviewEntity(
  db: SupabaseClient,
  entityId: number,
  decision: "approve" | "reject",
  actor: string
): Promise<ActionResult> {
  const { error } = await db.rpc("internal_review_ownership_entity", {
    p_entity_id: entityId,
    p_decision: decision,
    p_actor: actor,
  });
  return fromRpc(error);
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
  const { error } = await db.rpc("internal_set_ownership_entity_type", {
    p_entity_id: entityId,
    p_entity_type: newType,
    p_actor: actor,
  });
  return fromRpc(error);
}

// ─── Ownership events ─────────────────────────────────────────────────────────

export async function reviewOwnershipEvent(
  db: SupabaseClient,
  eventId: number,
  decision: "approve" | "reject",
  actor: string
): Promise<ActionResult> {
  const { error } = await db.rpc("internal_review_ownership_event", {
    p_event_id: eventId,
    p_decision: decision,
    p_actor: actor,
  });
  return fromRpc(error);
}

// ─── Entity relationships ──────────────────────────────────────────────────────

export async function reviewRelationship(
  db: SupabaseClient,
  relationshipId: number,
  decision: "approve" | "reject",
  actor: string
): Promise<ActionResult> {
  const { error } = await db.rpc("internal_review_ownership_relationship", {
    p_relationship_id: relationshipId,
    p_decision: decision,
    p_actor: actor,
  });
  return fromRpc(error);
}
