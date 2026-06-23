/**
 * Tests for the atomic audit guarantee introduced in Phase 8.
 *
 * Each internal-action function calls a single Postgres RPC (009_internal_rpc.sql)
 * that performs the business UPDATE and the internal_audit_log INSERT in one
 * implicit transaction.  These tests verify three invariants:
 *
 *   1. Middleware matcher: both /internal/* and /api/internal/* are protected.
 *   2. One RPC call per action (one transaction = one audit record).
 *   3. When the RPC fails (simulating an audit-write failure), the action
 *      function surfaces the error — the Postgres transaction rolls back both
 *      the business update and the audit insert atomically.
 */

import {
  confirmTransaction,
  rejectTransaction,
  reclassifyTransaction,
  addReviewNote,
  retryFiling,
  resolveIssuer,
  rejectIssuer,
} from "../../lib/internal-actions";
import type { SupabaseClient } from "@supabase/supabase-js";
import { config as middlewareConfig } from "../../middleware";

// ─── Mock helpers ─────────────────────────────────────────────────────────────

function mockDbOk(): { db: SupabaseClient; rpc: jest.Mock } {
  const rpc = jest.fn().mockResolvedValue({ data: null, error: null });
  return { db: { rpc } as unknown as SupabaseClient, rpc };
}

function mockDbFail(message = "simulated DB error"): { db: SupabaseClient; rpc: jest.Mock } {
  const rpc = jest.fn().mockResolvedValue({ data: null, error: { message } });
  return { db: { rpc } as unknown as SupabaseClient, rpc };
}

// ─── Middleware matcher coverage ──────────────────────────────────────────────

describe("middleware matcher coverage", () => {
  it("protects /internal/:path* routes", () => {
    expect(middlewareConfig.matcher).toContain("/internal/:path*");
  });

  it("protects /api/internal/:path* routes", () => {
    expect(middlewareConfig.matcher).toContain("/api/internal/:path*");
  });
});

// ─── confirmTransaction ───────────────────────────────────────────────────────

describe("confirmTransaction — atomic audit", () => {
  it("calls internal_confirm_transaction RPC exactly once", async () => {
    const { db, rpc } = mockDbOk();
    await confirmTransaction(db, 42, "test-actor");
    expect(rpc).toHaveBeenCalledTimes(1);
    expect(rpc).toHaveBeenCalledWith("internal_confirm_transaction", {
      p_transaction_id: 42,
      p_actor: "test-actor",
    });
  });

  it("returns ok:true on success", async () => {
    const { db } = mockDbOk();
    const result = await confirmTransaction(db, 42, "test-actor");
    expect(result.ok).toBe(true);
  });

  it("returns ok:false when RPC fails (business update also rolled back)", async () => {
    const { db } = mockDbFail("audit insert failed");
    const result = await confirmTransaction(db, 42, "test-actor");
    expect(result.ok).toBe(false);
    expect(result.error).toBe("audit insert failed");
  });
});

// ─── rejectTransaction ────────────────────────────────────────────────────────

describe("rejectTransaction — atomic audit", () => {
  it("calls internal_reject_transaction RPC exactly once", async () => {
    const { db, rpc } = mockDbOk();
    await rejectTransaction(db, 7, "test-actor");
    expect(rpc).toHaveBeenCalledTimes(1);
    expect(rpc).toHaveBeenCalledWith("internal_reject_transaction", {
      p_transaction_id: 7,
      p_actor: "test-actor",
    });
  });

  it("returns ok:true on success", async () => {
    const { db } = mockDbOk();
    expect((await rejectTransaction(db, 7, "a")).ok).toBe(true);
  });

  it("returns ok:false when RPC fails", async () => {
    const { db } = mockDbFail("constraint violation");
    const result = await rejectTransaction(db, 7, "a");
    expect(result.ok).toBe(false);
    expect(result.error).toBe("constraint violation");
  });
});

// ─── reclassifyTransaction ────────────────────────────────────────────────────

describe("reclassifyTransaction — atomic audit", () => {
  it("calls internal_reclassify_transaction RPC exactly once", async () => {
    const { db, rpc } = mockDbOk();
    await reclassifyTransaction(db, 99, "buy", "verified open-market purchase", "test-actor");
    expect(rpc).toHaveBeenCalledTimes(1);
    expect(rpc).toHaveBeenCalledWith("internal_reclassify_transaction", {
      p_transaction_id:   99,
      p_transaction_type: "buy",
      p_rationale:        "verified open-market purchase",
      p_actor:            "test-actor",
    });
  });

  it("returns ok:true on success", async () => {
    const { db } = mockDbOk();
    const result = await reclassifyTransaction(db, 99, "buy", "reason", "a");
    expect(result.ok).toBe(true);
  });

  it("returns ok:false when RPC fails (version snapshot and business update both rolled back)", async () => {
    const { db } = mockDbFail("snapshot insert failed");
    const result = await reclassifyTransaction(db, 99, "buy", "reason", "a");
    expect(result.ok).toBe(false);
    expect(result.error).toBe("snapshot insert failed");
  });
});

// ─── addReviewNote ────────────────────────────────────────────────────────────

describe("addReviewNote — atomic audit", () => {
  it("calls internal_add_review_note RPC exactly once", async () => {
    const { db, rpc } = mockDbOk();
    await addReviewNote(db, 12, "Confirmed via source PDF.", "test-actor");
    expect(rpc).toHaveBeenCalledTimes(1);
    expect(rpc).toHaveBeenCalledWith("internal_add_review_note", {
      p_transaction_id: 12,
      p_note:           "Confirmed via source PDF.",
      p_actor:          "test-actor",
    });
  });

  it("returns ok:true on success", async () => {
    const { db } = mockDbOk();
    expect((await addReviewNote(db, 12, "note", "a")).ok).toBe(true);
  });

  it("returns ok:false when RPC fails", async () => {
    const { db } = mockDbFail("db error");
    const result = await addReviewNote(db, 12, "note", "a");
    expect(result.ok).toBe(false);
  });
});

// ─── retryFiling ──────────────────────────────────────────────────────────────

describe("retryFiling — atomic audit", () => {
  it("calls internal_retry_filing RPC exactly once", async () => {
    const { db, rpc } = mockDbOk();
    await retryFiling(db, 301, "test-actor");
    expect(rpc).toHaveBeenCalledTimes(1);
    expect(rpc).toHaveBeenCalledWith("internal_retry_filing", {
      p_filing_id: 301,
      p_actor:     "test-actor",
    });
  });

  it("returns ok:true on success", async () => {
    const { db } = mockDbOk();
    expect((await retryFiling(db, 301, "a")).ok).toBe(true);
  });

  it("returns ok:false when RPC fails", async () => {
    const { db } = mockDbFail("db error");
    expect((await retryFiling(db, 301, "a")).ok).toBe(false);
  });
});

// ─── resolveIssuer ────────────────────────────────────────────────────────────

describe("resolveIssuer — atomic audit", () => {
  it("calls internal_resolve_issuer RPC exactly once", async () => {
    const { db, rpc } = mockDbOk();
    await resolveIssuer(db, 55, 3, "test-actor");
    expect(rpc).toHaveBeenCalledTimes(1);
    expect(rpc).toHaveBeenCalledWith("internal_resolve_issuer", {
      p_unmatched_id: 55,
      p_issuer_id:    3,
      p_actor:        "test-actor",
    });
  });

  it("returns ok:true on success", async () => {
    const { db } = mockDbOk();
    expect((await resolveIssuer(db, 55, 3, "a")).ok).toBe(true);
  });

  it("returns ok:false when RPC fails (backfill also rolled back)", async () => {
    const { db } = mockDbFail("backfill error");
    const result = await resolveIssuer(db, 55, 3, "a");
    expect(result.ok).toBe(false);
    expect(result.error).toBe("backfill error");
  });
});

// ─── rejectIssuer ─────────────────────────────────────────────────────────────

describe("rejectIssuer — atomic audit", () => {
  it("calls internal_reject_issuer RPC exactly once", async () => {
    const { db, rpc } = mockDbOk();
    await rejectIssuer(db, 56, "test-actor");
    expect(rpc).toHaveBeenCalledTimes(1);
    expect(rpc).toHaveBeenCalledWith("internal_reject_issuer", {
      p_unmatched_id: 56,
      p_actor:        "test-actor",
    });
  });

  it("returns ok:true on success", async () => {
    const { db } = mockDbOk();
    expect((await rejectIssuer(db, 56, "a")).ok).toBe(true);
  });

  it("returns ok:false when RPC fails", async () => {
    const { db } = mockDbFail("db error");
    expect((await rejectIssuer(db, 56, "a")).ok).toBe(false);
  });
});
