/**
 * Tests for the Phase 17B.6 / 17B.7 Ownership Review Queue:
 *   - pure recommendation logic + readable labels + compact dates
 *     (lib/ownership-review)
 *   - server-side mutation actions routing through atomic audit RPCs
 *     (lib/ownership-review-actions)
 *   - migration 020 static content (audit hardening)
 *
 * (Route authorization/validation is covered in ownership-review-route.test.ts.)
 * The Supabase client is mocked throughout — no network, no DB.
 */

import * as fs from "fs";
import * as path from "path";
import type { SupabaseClient } from "@supabase/supabase-js";

import {
  recommendEntityType,
  isEntityType,
  ENTITY_TYPES,
  labelReviewStatus,
  labelEventType,
  labelRelationshipType,
  formatReviewDate,
} from "../../lib/ownership-review";
import {
  reviewEntity,
  setEntityType,
  reviewOwnershipEvent,
  reviewRelationship,
} from "../../lib/ownership-review-actions";

// ─── Pure recommendation logic ─────────────────────────────────────────────────

describe("recommendEntityType", () => {
  it("flags an organisation tagged natural_person", () => {
    const rec = recommendEntityType("GOLDMAN SACHS INTERNATIONAL", "natural_person");
    expect(rec).not.toBeNull();
    expect(rec!.proposedType).toBe("company");
    expect(rec!.reason).toContain("INTERNATIONAL");
  });

  it("flags MORGAN STANLEY", () => {
    expect(recommendEntityType("MORGAN STANLEY", "natural_person")?.proposedType).toBe("company");
  });

  it("does not flag a real person name", () => {
    expect(recommendEntityType("Mario Rossi", "natural_person")).toBeNull();
    expect(recommendEntityType("Brunello Cucinelli", "natural_person")).toBeNull();
  });

  it("never touches an entity already typed company", () => {
    expect(recommendEntityType("GOLDMAN SACHS BANK EUROPE SE", "company")).toBeNull();
  });

  it("never re-types a non-person", () => {
    expect(recommendEntityType("ANYTHING INTERNATIONAL", "holding_company")).toBeNull();
  });
});

describe("isEntityType", () => {
  it("accepts the valid vocabulary", () => {
    for (const t of ENTITY_TYPES) expect(isEntityType(t)).toBe(true);
  });
  it("rejects invalid values", () => {
    expect(isEntityType("person")).toBe(false);
    expect(isEntityType("")).toBe(false);
    expect(isEntityType("DROP TABLE")).toBe(false);
  });
});

// ─── Readable label mapping (Part A) ───────────────────────────────────────────

describe("readable labels", () => {
  it("maps review statuses", () => {
    expect(labelReviewStatus("pending_review")).toBe("Pending review");
    expect(labelReviewStatus("confirmed")).toBe("Confirmed");
    expect(labelReviewStatus("rejected")).toBe("Rejected");
  });

  it("maps event types incl. the ambiguous 'other'", () => {
    expect(labelEventType("initial_disclosure")).toBe("Initial disclosure");
    expect(labelEventType("other")).toBe("Other / classification pending");
    expect(labelEventType("threshold_crossing_up")).toBe("Threshold crossing (up)");
  });

  it("maps relationship types", () => {
    expect(labelRelationshipType("controls")).toBe("Controls");
    expect(labelRelationshipType("beneficially_owns")).toBe("Beneficially owns");
  });

  it("humanises unknown values rather than showing raw snake_case", () => {
    expect(labelEventType("brand_new_type")).toBe("Brand new type");
    expect(labelReviewStatus("")).toBe("—");
  });
});

// ─── Compact date formatting (Part A) ──────────────────────────────────────────

describe("formatReviewDate", () => {
  it("formats ISO dates compactly without timezone drift", () => {
    expect(formatReviewDate("2026-04-28")).toBe("28 Apr 2026");
    expect(formatReviewDate("2026-05-06")).toBe("6 May 2026");
    expect(formatReviewDate("2025-09-12")).toBe("12 Sep 2025");
  });

  it("accepts a full timestamp and uses the date part", () => {
    expect(formatReviewDate("2026-06-03T00:00:00+00:00")).toBe("3 Jun 2026");
  });

  it("returns an em dash for null/invalid", () => {
    expect(formatReviewDate(null)).toBe("—");
    expect(formatReviewDate("")).toBe("—");
    expect(formatReviewDate("not-a-date")).toBe("—");
  });
});

// ─── Mutation actions route through atomic audit RPCs ──────────────────────────

interface RpcCapture {
  fn?: string;
  params?: Record<string, unknown>;
  calls: number;
}

function rpcDb(error: { message: string } | null = null): {
  db: SupabaseClient;
  cap: RpcCapture;
} {
  const cap: RpcCapture = { calls: 0 };
  const db = {
    rpc: jest.fn((fn: string, params: Record<string, unknown>) => {
      cap.fn = fn;
      cap.params = params;
      cap.calls += 1;
      return Promise.resolve({ data: null, error });
    }),
  } as unknown as SupabaseClient;
  return { db, cap };
}

describe("reviewEntity", () => {
  it("approve → internal_review_ownership_entity RPC with decision=approve", async () => {
    const { db, cap } = rpcDb();
    const r = await reviewEntity(db, 1, "approve", "tester");
    expect(r.ok).toBe(true);
    expect(cap.fn).toBe("internal_review_ownership_entity");
    expect(cap.params).toEqual({ p_entity_id: 1, p_decision: "approve", p_actor: "tester" });
  });

  it("reject → decision=reject", async () => {
    const { db, cap } = rpcDb();
    await reviewEntity(db, 2, "reject", "tester");
    expect(cap.params!.p_decision).toBe("reject");
  });

  it("surfaces RPC errors", async () => {
    const { db } = rpcDb({ message: "boom" });
    expect(await reviewEntity(db, 1, "approve", "tester")).toEqual({ ok: false, error: "boom" });
  });
});

describe("setEntityType", () => {
  it("rejects an invalid type WITHOUT calling any RPC (writes nothing)", async () => {
    const { db, cap } = rpcDb();
    const r = await setEntityType(db, 1, "not-a-type", "tester");
    expect(r.ok).toBe(false);
    expect(cap.calls).toBe(0);
    expect((db.rpc as jest.Mock)).not.toHaveBeenCalled();
  });

  it("applies a valid type via internal_set_ownership_entity_type", async () => {
    const { db, cap } = rpcDb();
    const r = await setEntityType(db, 3, "company", "tester");
    expect(r.ok).toBe(true);
    expect(cap.fn).toBe("internal_set_ownership_entity_type");
    expect(cap.params).toEqual({ p_entity_id: 3, p_entity_type: "company", p_actor: "tester" });
  });
});

describe("reviewOwnershipEvent", () => {
  it("routes to internal_review_ownership_event", async () => {
    const { db, cap } = rpcDb();
    await reviewOwnershipEvent(db, 7, "approve", "tester");
    expect(cap.fn).toBe("internal_review_ownership_event");
    expect(cap.params).toEqual({ p_event_id: 7, p_decision: "approve", p_actor: "tester" });
  });
});

describe("reviewRelationship", () => {
  it("routes to internal_review_ownership_relationship", async () => {
    const { db, cap } = rpcDb();
    await reviewRelationship(db, 9, "reject", "tester");
    expect(cap.fn).toBe("internal_review_ownership_relationship");
    expect(cap.params).toEqual({ p_relationship_id: 9, p_decision: "reject", p_actor: "tester" });
  });
});

// ─── Migration 020 static content (audit hardening) ────────────────────────────

describe("migration 020 — ownership review audit hardening", () => {
  const sql = fs.readFileSync(
    path.join(__dirname, "../../db/migrations/020_ownership_review_audit.sql"),
    "utf-8"
  );

  it("adds reviewer attribution columns to entities", () => {
    expect(sql).toMatch(/ALTER TABLE entities ADD COLUMN IF NOT EXISTS reviewed_by/);
    expect(sql).toMatch(/ALTER TABLE entities ADD COLUMN IF NOT EXISTS reviewed_at/);
  });

  it("extends internal_audit_log entity_type to accept the three ownership kinds", () => {
    expect(sql).toContain("'ownership_entity'");
    expect(sql).toContain("'ownership_event'");
    expect(sql).toContain("'ownership_relationship'");
    // existing values preserved
    expect(sql).toContain("'transaction'");
    expect(sql).toContain("'filing'");
    expect(sql).toContain("'unmatched_issuer'");
  });

  it("defines the four atomic ownership review RPCs", () => {
    for (const fn of [
      "internal_review_ownership_entity",
      "internal_set_ownership_entity_type",
      "internal_review_ownership_event",
      "internal_review_ownership_relationship",
    ]) {
      expect(sql).toContain(`CREATE OR REPLACE FUNCTION ${fn}`);
    }
  });

  it("each RPC writes an internal_audit_log row (atomic audit)", () => {
    const inserts = sql.match(/INSERT INTO internal_audit_log/g) ?? [];
    expect(inserts.length).toBeGreaterThanOrEqual(4);
  });

  it("does NOT backfill historical reviewer data", () => {
    expect(sql).not.toMatch(/UPDATE entities\s+SET\s+reviewed_by/i);
  });
});
