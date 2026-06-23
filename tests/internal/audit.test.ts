/**
 * Tests for audit record creation in lib/internal-audit.ts.
 *
 * Verifies that logAudit() writes the correct action_type, entity_type,
 * entity_id, actor, before_values, and after_values for each action that
 * a route handler can perform.
 *
 * Uses a lightweight mock Supabase client — no real DB connection needed.
 */

import { logAudit, getActor, AuditEvent } from "../../lib/internal-audit";
import type { SupabaseClient } from "@supabase/supabase-js";

// ─── Mock Supabase client ─────────────────────────────────────────────────────

function makeMockDb() {
  const insertMock = jest.fn().mockResolvedValue({ error: null });
  const fromMock = jest.fn().mockReturnValue({ insert: insertMock });
  const db = { from: fromMock } as unknown as SupabaseClient;
  return { db, fromMock, insertMock };
}

// Helper: extract the object passed to insert()
function capturedInsert(insertMock: jest.Mock) {
  expect(insertMock).toHaveBeenCalledTimes(1);
  return insertMock.mock.calls[0][0] as Record<string, unknown>;
}

// ─── logAudit — core contract ─────────────────────────────────────────────────

describe("logAudit — core contract", () => {
  it("writes to internal_audit_log table", async () => {
    const { db, fromMock } = makeMockDb();
    await logAudit(db, { actionType: "confirm", entityType: "transaction", entityId: 1, actor: "test" });
    expect(fromMock).toHaveBeenCalledWith("internal_audit_log");
  });

  it("does not throw when the DB insert fails (non-fatal)", async () => {
    const insertMock = jest.fn().mockResolvedValue({ error: { message: "DB down" } });
    const db = { from: jest.fn().mockReturnValue({ insert: insertMock }) } as unknown as SupabaseClient;
    // Must not throw
    await expect(logAudit(db, { actionType: "confirm", entityType: "transaction", entityId: 1, actor: "op" }))
      .resolves.toBeUndefined();
  });

  it("sets before_values and after_values to null when omitted", async () => {
    const { db, insertMock } = makeMockDb();
    await logAudit(db, { actionType: "confirm", entityType: "transaction", entityId: 1, actor: "op" });
    const row = capturedInsert(insertMock);
    expect(row.before_values).toBeNull();
    expect(row.after_values).toBeNull();
  });
});

// ─── Action type: confirm ─────────────────────────────────────────────────────

describe("audit record — confirm", () => {
  it("writes correct fields", async () => {
    const { db, insertMock } = makeMockDb();
    const event: AuditEvent = {
      actionType:   "confirm",
      entityType:   "transaction",
      entityId:     42,
      actor:        "shared-admin",
      beforeValues: { review_status: null },
      afterValues:  { review_status: "confirmed" },
    };
    await logAudit(db, event);
    const row = capturedInsert(insertMock);
    expect(row.action_type).toBe("confirm");
    expect(row.entity_type).toBe("transaction");
    expect(row.entity_id).toBe(42);
    expect(row.actor).toBe("shared-admin");
    expect(row.before_values).toEqual({ review_status: null });
    expect(row.after_values).toEqual({ review_status: "confirmed" });
  });
});

// ─── Action type: reject ──────────────────────────────────────────────────────

describe("audit record — reject", () => {
  it("writes correct fields", async () => {
    const { db, insertMock } = makeMockDb();
    await logAudit(db, {
      actionType:   "reject",
      entityType:   "transaction",
      entityId:     7,
      actor:        "shared-admin",
      beforeValues: { review_status: "pending_review" },
      afterValues:  { review_status: "rejected" },
    });
    const row = capturedInsert(insertMock);
    expect(row.action_type).toBe("reject");
    expect(row.before_values).toEqual({ review_status: "pending_review" });
    expect(row.after_values).toEqual({ review_status: "rejected" });
  });
});

// ─── Action type: reclassify ──────────────────────────────────────────────────

describe("audit record — reclassify", () => {
  it("records before and after classification fields", async () => {
    const { db, insertMock } = makeMockDb();
    await logAudit(db, {
      actionType:   "reclassify",
      entityType:   "transaction",
      entityId:     99,
      actor:        "shared-admin",
      beforeValues: { transaction_type: "unknown", economic_intent: "unclear", classification_rationale: "undetermined: direction=unknown, hint=other" },
      afterValues:  { transaction_type: "buy", economic_intent: "discretionary", classification_rationale: "operator_correction: verified open-market purchase" },
    });
    const row = capturedInsert(insertMock);
    expect(row.action_type).toBe("reclassify");
    expect((row.before_values as Record<string, unknown>).transaction_type).toBe("unknown");
    expect((row.after_values as Record<string, unknown>).transaction_type).toBe("buy");
  });
});

// ─── Action type: retry_filing ────────────────────────────────────────────────

describe("audit record — retry_filing", () => {
  it("records filing status transition", async () => {
    const { db, insertMock } = makeMockDb();
    await logAudit(db, {
      actionType:   "retry_filing",
      entityType:   "filing",
      entityId:     301,
      actor:        "shared-admin",
      beforeValues: { status: "failed", attempt_count: 2 },
      afterValues:  { status: "pending" },
    });
    const row = capturedInsert(insertMock);
    expect(row.action_type).toBe("retry_filing");
    expect(row.entity_type).toBe("filing");
    expect(row.entity_id).toBe(301);
    expect((row.before_values as Record<string, unknown>).status).toBe("failed");
    expect((row.after_values as Record<string, unknown>).status).toBe("pending");
  });
});

// ─── Action type: resolve_issuer ──────────────────────────────────────────────

describe("audit record — resolve_issuer", () => {
  it("records issuer linkage", async () => {
    const { db, insertMock } = makeMockDb();
    await logAudit(db, {
      actionType:   "resolve_issuer",
      entityType:   "unmatched_issuer",
      entityId:     55,
      actor:        "shared-admin",
      beforeValues: { status: "pending", raw_name: "Eni S.p.A.", isin: "IT0003132476" },
      afterValues:  { status: "resolved", issuer_id: 3 },
    });
    const row = capturedInsert(insertMock);
    expect(row.action_type).toBe("resolve_issuer");
    expect(row.entity_type).toBe("unmatched_issuer");
    expect((row.after_values as Record<string, unknown>).issuer_id).toBe(3);
  });
});

// ─── Action type: reject_issuer ───────────────────────────────────────────────

describe("audit record — reject_issuer", () => {
  it("records rejection of an unmatched issuer", async () => {
    const { db, insertMock } = makeMockDb();
    await logAudit(db, {
      actionType:   "reject_issuer",
      entityType:   "unmatched_issuer",
      entityId:     56,
      actor:        "shared-admin",
      beforeValues: { status: "pending", raw_name: "Not a real company" },
      afterValues:  { status: "rejected" },
    });
    const row = capturedInsert(insertMock);
    expect(row.action_type).toBe("reject_issuer");
    expect((row.before_values as Record<string, unknown>).raw_name).toBe("Not a real company");
    expect((row.after_values as Record<string, unknown>).status).toBe("rejected");
  });
});

// ─── Action type: add_review_note ─────────────────────────────────────────────

describe("audit record — add_review_note", () => {
  it("records before and after note text", async () => {
    const { db, insertMock } = makeMockDb();
    await logAudit(db, {
      actionType:   "add_review_note",
      entityType:   "transaction",
      entityId:     12,
      actor:        "shared-admin",
      beforeValues: { review_notes: null },
      afterValues:  { review_notes: "Confirmed via Borsa Italiana source PDF." },
    });
    const row = capturedInsert(insertMock);
    expect(row.action_type).toBe("add_review_note");
    expect((row.before_values as Record<string, unknown>).review_notes).toBeNull();
    expect((row.after_values as Record<string, unknown>).review_notes).toBe("Confirmed via Borsa Italiana source PDF.");
  });
});

// ─── getActor ─────────────────────────────────────────────────────────────────

describe("getActor", () => {
  const ORIGINAL = process.env.INTERNAL_ACTOR_LABEL;

  afterEach(() => {
    if (ORIGINAL === undefined) delete process.env.INTERNAL_ACTOR_LABEL;
    else process.env.INTERNAL_ACTOR_LABEL = ORIGINAL;
  });

  it("returns 'shared-admin' when INTERNAL_ACTOR_LABEL is not set", () => {
    delete process.env.INTERNAL_ACTOR_LABEL;
    expect(getActor()).toBe("shared-admin");
  });

  it("returns the configured label when INTERNAL_ACTOR_LABEL is set", () => {
    process.env.INTERNAL_ACTOR_LABEL = "ops-team-london";
    expect(getActor()).toBe("ops-team-london");
  });
});
