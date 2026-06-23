/**
 * Thin wrappers around the Postgres RPC functions defined in
 * db/migrations/009_internal_rpc.sql.
 *
 * Each function calls a single stored procedure that performs the business
 * UPDATE and the internal_audit_log INSERT in one implicit PL/pgSQL
 * transaction.  If either statement fails the entire transaction rolls back
 * and the function returns { ok: false, error }.  Route handlers surface
 * that as HTTP 500 — no partial state is ever committed.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

export interface ActionResult {
  ok: boolean;
  error?: string;
}

function fromRpc(rpcError: { message: string } | null): ActionResult {
  if (rpcError) return { ok: false, error: rpcError.message };
  return { ok: true };
}

export async function confirmTransaction(
  db: SupabaseClient,
  transactionId: number,
  actor: string
): Promise<ActionResult> {
  const { error } = await db.rpc("internal_confirm_transaction", {
    p_transaction_id: transactionId,
    p_actor: actor,
  });
  return fromRpc(error);
}

export async function rejectTransaction(
  db: SupabaseClient,
  transactionId: number,
  actor: string
): Promise<ActionResult> {
  const { error } = await db.rpc("internal_reject_transaction", {
    p_transaction_id: transactionId,
    p_actor: actor,
  });
  return fromRpc(error);
}

export async function reclassifyTransaction(
  db: SupabaseClient,
  transactionId: number,
  transactionType: string,
  rationale: string,
  actor: string
): Promise<ActionResult> {
  const { error } = await db.rpc("internal_reclassify_transaction", {
    p_transaction_id:   transactionId,
    p_transaction_type: transactionType,
    p_rationale:        rationale,
    p_actor:            actor,
  });
  return fromRpc(error);
}

export async function addReviewNote(
  db: SupabaseClient,
  transactionId: number,
  note: string,
  actor: string
): Promise<ActionResult> {
  const { error } = await db.rpc("internal_add_review_note", {
    p_transaction_id: transactionId,
    p_note:           note,
    p_actor:          actor,
  });
  return fromRpc(error);
}

export async function retryFiling(
  db: SupabaseClient,
  filingId: number,
  actor: string
): Promise<ActionResult> {
  const { error } = await db.rpc("internal_retry_filing", {
    p_filing_id: filingId,
    p_actor:     actor,
  });
  return fromRpc(error);
}

export async function resolveIssuer(
  db: SupabaseClient,
  unmatchedId: number,
  issuerId: number,
  actor: string
): Promise<ActionResult> {
  const { error } = await db.rpc("internal_resolve_issuer", {
    p_unmatched_id: unmatchedId,
    p_issuer_id:    issuerId,
    p_actor:        actor,
  });
  return fromRpc(error);
}

export async function rejectIssuer(
  db: SupabaseClient,
  unmatchedId: number,
  actor: string
): Promise<ActionResult> {
  const { error } = await db.rpc("internal_reject_issuer", {
    p_unmatched_id: unmatchedId,
    p_actor:        actor,
  });
  return fromRpc(error);
}
