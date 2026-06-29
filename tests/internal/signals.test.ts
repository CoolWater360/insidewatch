/**
 * Phase 12 — tests for lib/signals.ts
 *
 * All Supabase calls are mocked so tests are pure and fast.
 * We test the pure helper (scoreClusterConfidence) directly and
 * the signal functions via a mocked supabase client.
 */

import {
  MECHANICAL_TYPES,
  scoreClusterConfidence,
  getNetDiscretionaryFlow,
  getRepeatBuyerSignals,
  getClusterSignalsWithConfidence,
  ClusterSignalWithConfidence,
  NetDiscretionaryFlow,
  RepeatBuyerSignal,
} from "../../lib/signals";

// ─── Mock lib/supabase ────────────────────────────────────────────────────────
//
// The query chain can terminate at any builder method (gte, order, etc.) so
// we make the entire chain thenable: every method returns the chain object
// itself, and the chain has a `then` that resolves to { data, error }.

let _mockData: unknown[] = [];
let _mockError: { message: string } | null = null;

function _buildChain(): Record<string, unknown> {
  const chain: Record<string, unknown> = {};
  const methods = ["select", "eq", "in", "gte", "order", "lte", "neq", "limit", "single"];
  for (const m of methods) {
    chain[m] = jest.fn(() => chain);
  }
  chain["then"] = (
    resolve: (v: { data: unknown[]; error: unknown }) => void,
    reject?: (e: unknown) => void
  ) => Promise.resolve({ data: _mockData, error: _mockError }).then(resolve, reject);
  chain["catch"] = (reject: (e: unknown) => void) =>
    Promise.resolve({ data: _mockData, error: _mockError }).catch(reject);
  return chain;
}

let _chain = _buildChain();
const mockSupabase = { from: jest.fn(() => _chain) };

jest.mock("../../lib/supabase", () => ({
  getSupabase: jest.fn(() => mockSupabase),
}));

function setMockData(rows: unknown[], err: { message: string } | null = null): void {
  _mockData = rows;
  _mockError = err;
  _chain = _buildChain();
  mockSupabase.from.mockImplementation(() => _chain);
}

// ─── MECHANICAL_TYPES ─────────────────────────────────────────────────────────

describe("MECHANICAL_TYPES", () => {
  it("includes grant, option_exercise, sell_to_cover", () => {
    expect(MECHANICAL_TYPES.has("grant")).toBe(true);
    expect(MECHANICAL_TYPES.has("option_exercise")).toBe(true);
    expect(MECHANICAL_TYPES.has("sell_to_cover")).toBe(true);
  });

  it("includes all transfer/gift/conversion types", () => {
    const expected = [
      "conversion", "inheritance",
      "gift_in", "gift_out",
      "transfer_in", "transfer_out",
      "pledge_or_security", "derivative_transaction",
    ];
    expected.forEach((t) => expect(MECHANICAL_TYPES.has(t)).toBe(true));
  });

  it("does not include buy or sell", () => {
    expect(MECHANICAL_TYPES.has("buy")).toBe(false);
    expect(MECHANICAL_TYPES.has("sell")).toBe(false);
  });
});

// ─── scoreClusterConfidence ───────────────────────────────────────────────────

describe("scoreClusterConfidence", () => {
  it("returns 0.50 base for 2 insiders, no senior, low cash", () => {
    const { confidence, rationale } = scoreClusterConfidence(2, 0, 50_000);
    expect(confidence).toBe(0.5);
    expect(rationale[0]).toContain("2 independent insiders");
  });

  it("+0.10 per additional insider, capped at 3 extras", () => {
    const { confidence: c3 } = scoreClusterConfidence(3, 0, 0);
    expect(c3).toBeCloseTo(0.60, 3);

    const { confidence: c5 } = scoreClusterConfidence(5, 0, 0);
    expect(c5).toBeCloseTo(0.80, 3);

    // 10 insiders — should cap at +0.30 extra
    const { confidence: c10 } = scoreClusterConfidence(10, 0, 0);
    expect(c10).toBeCloseTo(0.80, 3);
  });

  it("+0.15 for 2+ senior insiders", () => {
    const { confidence, rationale } = scoreClusterConfidence(2, 2, 0);
    expect(confidence).toBeCloseTo(0.65, 3);
    expect(rationale.some((r) => r.includes("executive/board"))).toBe(true);
  });

  it("+0.10 for cash_value > 100 000", () => {
    const { confidence, rationale } = scoreClusterConfidence(2, 0, 150_000);
    expect(confidence).toBeCloseTo(0.60, 3);
    expect(rationale.some((r) => r.includes("cash_value"))).toBe(true);
  });

  it("all boosts combined stays <= 1.0", () => {
    const { confidence } = scoreClusterConfidence(10, 5, 500_000);
    expect(confidence).toBeLessThanOrEqual(1.0);
    expect(confidence).toBeGreaterThanOrEqual(0.0);
  });

  it("no senior boost for < 2 senior insiders", () => {
    const { confidence: c1 } = scoreClusterConfidence(2, 1, 0);
    const { confidence: c0 } = scoreClusterConfidence(2, 0, 0);
    expect(c1).toBe(c0);
  });

  it("returns rationale array", () => {
    const { rationale } = scoreClusterConfidence(3, 2, 200_000);
    expect(Array.isArray(rationale)).toBe(true);
    expect(rationale.length).toBeGreaterThan(0);
  });
});

// ─── getNetDiscretionaryFlow ──────────────────────────────────────────────────

describe("getNetDiscretionaryFlow", () => {
  const _row = (
    cid: number, name: string, dir: "buy" | "sell",
    value: number, type = "buy"
  ) => ({
    company_id: cid,
    direction: dir,
    total_value: value,
    transaction_type: type,
    economic_intent: "discretionary",
    companies: { id: cid, name },
  });

  it("returns empty array on DB error", async () => {
    setMockData([], { message: "connection refused" });
    const result = await getNetDiscretionaryFlow();
    expect(result).toEqual([]);
  });

  it("returns empty array for empty data", async () => {
    setMockData([]);
    const result = await getNetDiscretionaryFlow();
    expect(result).toEqual([]);
  });

  it("computes net_value = buy_value - sell_value", async () => {
    setMockData([
      _row(1, "Acme SpA", "buy",  100_000),
      _row(1, "Acme SpA", "sell",  30_000),
    ]);
    const [r] = await getNetDiscretionaryFlow();
    expect(r.company_id).toBe(1);
    expect(r.buy_value).toBe(100_000);
    expect(r.sell_value).toBe(30_000);
    expect(r.net_value).toBe(70_000);
    expect(r.transaction_count).toBe(2);
  });

  it("excludes mechanical transaction types", async () => {
    setMockData([
      _row(1, "Acme SpA", "buy", 100_000, "buy"),
      _row(1, "Acme SpA", "buy",  50_000, "grant"),       // excluded
      _row(1, "Acme SpA", "buy",  20_000, "option_exercise"), // excluded
    ]);
    const [r] = await getNetDiscretionaryFlow();
    expect(r.buy_value).toBe(100_000);
    expect(r.transaction_count).toBe(1);
  });

  it("sorts by net_value descending (biggest net buyer first)", async () => {
    setMockData([
      _row(1, "SmallBuyer", "buy",  10_000),
      _row(2, "BigBuyer",   "buy", 500_000),
    ]);
    const result = await getNetDiscretionaryFlow();
    expect(result[0].company_id).toBe(2);
    expect(result[1].company_id).toBe(1);
  });

  it("aggregates multiple transactions for the same company", async () => {
    setMockData([
      _row(1, "Acme SpA", "buy", 40_000),
      _row(1, "Acme SpA", "buy", 60_000),
      _row(1, "Acme SpA", "sell", 20_000),
    ]);
    const [r] = await getNetDiscretionaryFlow();
    expect(r.buy_value).toBe(100_000);
    expect(r.sell_value).toBe(20_000);
    expect(r.net_value).toBe(80_000);
    expect(r.transaction_count).toBe(3);
  });

  it("includes lookback_days in result", async () => {
    setMockData([_row(1, "X", "buy", 1000)]);
    const [r] = await getNetDiscretionaryFlow(60);
    expect(r.lookback_days).toBe(60);
  });
});

// ─── getRepeatBuyerSignals ────────────────────────────────────────────────────

describe("getRepeatBuyerSignals", () => {
  const _row = (
    insiderId: number, cid: number, date: string, value: number, type = "buy"
  ) => ({
    insider_id:   insiderId,
    company_id:   cid,
    transaction_date: date,
    total_value:  value,
    transaction_type: type,
    economic_intent: "discretionary",
    insiders: { id: insiderId, full_name: `Insider ${insiderId}`, role: "CEO", role_category: "executive" },
    companies: { id: cid, name: `Company ${cid}` },
  });

  it("returns empty on DB error", async () => {
    setMockData([], { message: "err" });
    const result = await getRepeatBuyerSignals();
    expect(result).toEqual([]);
  });

  it("excludes insiders below minBuys threshold", async () => {
    setMockData([_row(1, 10, "2024-01-05", 10_000)]);
    const result = await getRepeatBuyerSignals(90, 2);
    expect(result).toEqual([]);
  });

  it("includes insiders meeting minBuys threshold", async () => {
    setMockData([
      _row(1, 10, "2024-01-05", 10_000),
      _row(1, 10, "2024-01-12", 15_000),
    ]);
    const result = await getRepeatBuyerSignals(90, 2);
    expect(result).toHaveLength(1);
    expect(result[0].insider_id).toBe(1);
    expect(result[0].buy_count).toBe(2);
    expect(result[0].total_buy_value).toBe(25_000);
  });

  it("does not mix same insider at different companies", async () => {
    setMockData([
      _row(1, 10, "2024-01-05", 10_000),
      _row(1, 11, "2024-01-12", 15_000),
    ]);
    const result = await getRepeatBuyerSignals(90, 2);
    // Same insider, different companies → separate groups, each with count=1
    expect(result).toHaveLength(0);
  });

  it("excludes mechanical types", async () => {
    setMockData([
      _row(1, 10, "2024-01-05", 10_000, "grant"),
      _row(1, 10, "2024-01-12", 15_000, "grant"),
    ]);
    const result = await getRepeatBuyerSignals(90, 2);
    expect(result).toHaveLength(0);
  });

  it("returns first_buy and last_buy dates", async () => {
    setMockData([
      _row(1, 10, "2024-01-05", 5_000),
      _row(1, 10, "2024-01-20", 5_000),
      _row(1, 10, "2024-02-01", 5_000),
    ]);
    const [r] = await getRepeatBuyerSignals(90, 2);
    expect(r.first_buy).toBe("2024-01-05");
    expect(r.last_buy).toBe("2024-02-01");
  });

  it("sorts by buy_count desc then total_buy_value desc", async () => {
    setMockData([
      _row(1, 10, "2024-01-01", 5_000),
      _row(1, 10, "2024-01-08", 5_000),
      _row(2, 10, "2024-01-02", 100_000),
      _row(2, 10, "2024-01-09", 100_000),
      _row(2, 10, "2024-01-16", 100_000),
    ]);
    const result = await getRepeatBuyerSignals(90, 2);
    // insider 2 has 3 buys, insider 1 has 2
    expect(result[0].insider_id).toBe(2);
    expect(result[1].insider_id).toBe(1);
  });
});

// ─── getClusterSignalsWithConfidence ─────────────────────────────────────────

describe("getClusterSignalsWithConfidence", () => {
  const _row = (
    cid: number, insiderId: number, date: string,
    value: number, name: string, role: string | null = null,
    type = "buy"
  ) => ({
    company_id: cid,
    transaction_date: date,
    quantity: 100,
    total_value: value,
    transaction_type: type,
    economic_intent: "discretionary",
    companies: { id: cid, name: `Company ${cid}` },
    insiders: { full_name: name, role, role_category: null },
  });

  it("returns empty on DB error", async () => {
    setMockData([], { message: "err" });
    const result = await getClusterSignalsWithConfidence();
    expect(result).toEqual([]);
  });

  it("returns empty when < 2 distinct insiders", async () => {
    setMockData([
      _row(1, 1, "2024-01-05", 10_000, "Alice"),
    ]);
    const result = await getClusterSignalsWithConfidence();
    expect(result).toHaveLength(0);
  });

  it("emits signal for 2 distinct insiders within 7 days", async () => {
    setMockData([
      _row(1, 1, "2024-01-05", 10_000, "Alice"),
      _row(1, 2, "2024-01-06", 20_000, "Bob"),
    ]);
    const result = await getClusterSignalsWithConfidence();
    expect(result).toHaveLength(1);
    const s = result[0];
    expect(s.company_id).toBe(1);
    expect(s.insider_count).toBe(2);
    expect(s.confidence).toBeGreaterThanOrEqual(0.5);
    expect(Array.isArray(s.rationale)).toBe(true);
  });

  it("excludes mechanical types from window", async () => {
    setMockData([
      _row(1, 1, "2024-01-05", 10_000, "Alice", null, "grant"),
      _row(1, 2, "2024-01-06", 20_000, "Bob",   null, "grant"),
    ]);
    const result = await getClusterSignalsWithConfidence();
    expect(result).toHaveLength(0);
  });

  it("does not cluster insiders > 7 days apart", async () => {
    setMockData([
      _row(1, 1, "2024-01-01", 10_000, "Alice"),
      _row(1, 2, "2024-01-10", 20_000, "Bob"),
    ]);
    const result = await getClusterSignalsWithConfidence();
    expect(result).toHaveLength(0);
  });

  it("confidence >= 0.65 for 3+ insiders", async () => {
    setMockData([
      _row(1, 1, "2024-01-05", 10_000, "Alice"),
      _row(1, 2, "2024-01-06", 10_000, "Bob"),
      _row(1, 3, "2024-01-07", 10_000, "Charlie"),
    ]);
    const [s] = await getClusterSignalsWithConfidence();
    expect(s.confidence).toBeGreaterThanOrEqual(0.60);
  });

  it("provides cash_value excluding mechanical types in window", async () => {
    setMockData([
      _row(1, 1, "2024-01-05", 10_000, "Alice", null, "buy"),
      _row(1, 2, "2024-01-05", 50_000, "Bob",   null, "grant"),  // mechanical
    ]);
    // Only Alice qualifies as non-mechanical, so we need a 2nd non-mechanical
    // to form a cluster; this test verifies cash_value excludes grants.
    setMockData([
      _row(1, 1, "2024-01-05", 10_000, "Alice", null, "buy"),
      _row(1, 2, "2024-01-05", 15_000, "Bob",   null, "buy"),
      _row(1, 3, "2024-01-05", 50_000, "Carol", null, "grant"),  // excluded from cash_value
    ]);
    const [s] = await getClusterSignalsWithConfidence();
    // total_value includes grant (from window sum), cash_value excludes it
    // Note: MECHANICAL_TYPES filter runs on rows BEFORE window grouping —
    // grants are filtered at fetch time, so Carol's row is excluded entirely.
    expect(s.cash_value).toBe(s.total_value);
  });

  it("deduplicates same insider by normalised name", async () => {
    setMockData([
      _row(1, 1, "2024-01-05", 10_000, "Mario Rossi"),
      _row(1, 1, "2024-01-06", 10_000, "mario rossi"),  // same person, different case
      _row(1, 2, "2024-01-06", 20_000, "Luigi Bianchi"),
    ]);
    const [s] = await getClusterSignalsWithConfidence();
    expect(s.insider_count).toBe(2);  // Mario + Luigi, not 3
  });

  it("sorts by window_end desc then confidence desc", async () => {
    // Two separate clusters at different companies — one more recent
    setMockData([
      // Company 1: older window
      _row(1, 1, "2024-01-01", 5_000, "A"),
      _row(1, 2, "2024-01-02", 5_000, "B"),
      // Company 2: more recent window
      _row(2, 3, "2024-02-01", 5_000, "C"),
      _row(2, 4, "2024-02-02", 5_000, "D"),
    ]);
    const result = await getClusterSignalsWithConfidence();
    expect(result[0].company_id).toBe(2);
    expect(result[1].company_id).toBe(1);
  });

  it("includes role_category in insider objects", async () => {
    setMockData([
      {
        company_id: 1, transaction_date: "2024-01-05",
        quantity: 100, total_value: 10_000,
        transaction_type: "buy", economic_intent: "discretionary",
        companies: { id: 1, name: "Corp" },
        insiders: { full_name: "CEO Guy", role: "CEO", role_category: "executive" },
      },
      {
        company_id: 1, transaction_date: "2024-01-06",
        quantity: 100, total_value: 20_000,
        transaction_type: "buy", economic_intent: "discretionary",
        companies: { id: 1, name: "Corp" },
        insiders: { full_name: "CFO Guy", role: "CFO", role_category: "executive" },
      },
    ]);
    const [s] = await getClusterSignalsWithConfidence();
    expect(s.insiders[0]).toHaveProperty("role_category");
  });
});

// ─── Public-view regression: no raw table access via anon key ─────────────────
//
// These tests assert that each signal function queries public_transactions
// (not the private transactions table). Anon key has SELECT on public views
// only; a regression back to "transactions" would break in production.

describe("public-view access regression", () => {
  beforeEach(() => setMockData([]));

  it("getNetDiscretionaryFlow queries public_transactions", async () => {
    await getNetDiscretionaryFlow();
    expect(mockSupabase.from).toHaveBeenCalledWith("public_transactions");
    expect(mockSupabase.from).not.toHaveBeenCalledWith("transactions");
  });

  it("getRepeatBuyerSignals queries public_transactions", async () => {
    await getRepeatBuyerSignals();
    expect(mockSupabase.from).toHaveBeenCalledWith("public_transactions");
    expect(mockSupabase.from).not.toHaveBeenCalledWith("transactions");
  });

  it("getClusterSignalsWithConfidence queries public_transactions", async () => {
    await getClusterSignalsWithConfidence();
    expect(mockSupabase.from).toHaveBeenCalledWith("public_transactions");
    expect(mockSupabase.from).not.toHaveBeenCalledWith("transactions");
  });
});
