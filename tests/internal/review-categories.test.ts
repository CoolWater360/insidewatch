/**
 * Tests for lib/review-categories.ts — pure categorization logic.
 * No Supabase mock needed: all functions are pure TypeScript.
 *
 * Also tests lib/review-mutations.ts — client-side mutation dispatch,
 * using a global fetch mock.
 */

import {
  categorizeTransaction,
  groupTransactions,
  countsByCategory,
  QUEUE_CATEGORIES,
} from "../../lib/review-categories";
import {
  dispatchTransactionAction,
  dispatchFilingRetry,
  dispatchIssuerAction,
} from "../../lib/review-mutations";
import type { ReviewTransaction, ReviewFiling, UnmatchedIssuer } from "../../lib/review";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

function makeTx(overrides: Partial<ReviewTransaction> = {}): ReviewTransaction {
  return {
    id: 1,
    transaction_date: "2026-01-15",
    direction: "buy",
    transaction_type: "buy",
    economic_intent: "discretionary",
    quantity: 1000,
    unit_price: 10.5,
    total_value: 10500,
    currency: "EUR",
    needs_review: true,
    review_status: null,
    review_reason: "low_confidence",
    extraction_confidence: 0.45,
    classification_rationale: null,
    raw_nature_text: null,
    classification_override: false,
    isin: null,
    source_url: null,
    source_filing_id: null,
    companies: { id: 1, name: "Acme SpA" },
    insiders: { full_name: "Mario Rossi", role: "CEO" },
    ...overrides,
  };
}

// ─── categorizeTransaction ────────────────────────────────────────────────────

describe("categorizeTransaction", () => {
  it("direction_unknown — direction is 'unknown'", () => {
    expect(categorizeTransaction(makeTx({ direction: "unknown" }))).toBe("direction_unknown");
  });

  it("direction_unknown — direction is empty string (null coerced)", () => {
    expect(categorizeTransaction(makeTx({ direction: "" }))).toBe("direction_unknown");
  });

  it("direction_unknown — review_reason is ambiguous_direction", () => {
    expect(categorizeTransaction(makeTx({ review_reason: "ambiguous_direction" }))).toBe("direction_unknown");
  });

  it("type_unknown — transaction_type is null", () => {
    expect(categorizeTransaction(makeTx({ direction: "buy", transaction_type: null }))).toBe("type_unknown");
  });

  it("type_unknown — transaction_type is 'unknown'", () => {
    expect(categorizeTransaction(makeTx({ direction: "buy", transaction_type: "unknown" }))).toBe("type_unknown");
  });

  it("type_unknown — review_reason is unknown_type", () => {
    expect(categorizeTransaction(makeTx({
      direction: "buy",
      transaction_type: "buy", // type set but reason says unknown
      review_reason: "unknown_type",
    }))).toBe("type_unknown");
  });

  it("corporate_action — grant", () => {
    expect(categorizeTransaction(makeTx({ transaction_type: "grant" }))).toBe("corporate_action");
  });

  it("corporate_action — option_exercise", () => {
    expect(categorizeTransaction(makeTx({ transaction_type: "option_exercise" }))).toBe("corporate_action");
  });

  it("corporate_action — transfer_in", () => {
    expect(categorizeTransaction(makeTx({ transaction_type: "transfer_in" }))).toBe("corporate_action");
  });

  it("corporate_action — gift_out", () => {
    expect(categorizeTransaction(makeTx({ transaction_type: "gift_out" }))).toBe("corporate_action");
  });

  it("vehicle_fiduciary — raw_nature_text contains 'fiduciaria'", () => {
    expect(
      categorizeTransaction(makeTx({
        direction: "buy",
        transaction_type: "buy",
        raw_nature_text: "Acquisto tramite società fiduciaria Alfa",
      }))
    ).toBe("vehicle_fiduciary");
  });

  it("vehicle_fiduciary — raw_nature_text contains 'veicolo'", () => {
    expect(
      categorizeTransaction(makeTx({
        direction: "buy",
        transaction_type: "buy",
        raw_nature_text: "Cessione tramite veicolo societario",
      }))
    ).toBe("vehicle_fiduciary");
  });

  it("low_confidence — ordinary buy with low confidence (catch-all)", () => {
    expect(
      categorizeTransaction(makeTx({
        direction: "buy",
        transaction_type: "buy",
        extraction_confidence: 0.45,
        raw_nature_text: null,
      }))
    ).toBe("low_confidence");
  });

  it("direction_unknown has priority over type_unknown", () => {
    expect(
      categorizeTransaction(makeTx({ direction: "unknown", transaction_type: null }))
    ).toBe("direction_unknown");
  });

  it("type_unknown has priority over corporate_action", () => {
    // transaction_type is null but review_reason says unknown_type — should be type_unknown
    expect(
      categorizeTransaction(makeTx({
        direction: "buy",
        transaction_type: null,
        review_reason: "unknown_type",
      }))
    ).toBe("type_unknown");
  });
});

// ─── groupTransactions ────────────────────────────────────────────────────────

describe("groupTransactions", () => {
  it("partitions transactions into correct buckets", () => {
    const txs: ReviewTransaction[] = [
      makeTx({ id: 1, direction: "unknown" }),
      makeTx({ id: 2, direction: "buy", transaction_type: null }),
      makeTx({ id: 3, direction: "buy", transaction_type: "grant" }),
      makeTx({ id: 4, direction: "buy", transaction_type: "buy" }),
    ];
    const groups = groupTransactions(txs);
    expect(groups.direction_unknown).toHaveLength(1);
    expect(groups.type_unknown).toHaveLength(1);
    expect(groups.corporate_action).toHaveLength(1);
    expect(groups.low_confidence).toHaveLength(1);
  });

  it("returns all 9 category keys even when empty", () => {
    const groups = groupTransactions([]);
    expect(Object.keys(groups)).toHaveLength(9);
    expect(groups.issuer_unmatched).toHaveLength(0);
    expect(groups.relationship).toHaveLength(0);
    expect(groups.failed_filing).toHaveLength(0);
    expect(groups.fixture_candidate).toHaveLength(0);
  });

  it("does not double-count — each transaction appears exactly once", () => {
    const txs = [
      makeTx({ id: 1 }),
      makeTx({ id: 2, direction: "unknown" }),
      makeTx({ id: 3, transaction_type: "grant" }),
    ];
    const groups = groupTransactions(txs);
    const total = Object.values(groups).reduce((n, arr) => n + arr.length, 0);
    expect(total).toBe(txs.length);
  });
});

// ─── countsByCategory ─────────────────────────────────────────────────────────

describe("countsByCategory", () => {
  const filings: ReviewFiling[] = [
    { id: 1, pdf_url: "http://x", filing_date: null, company_name: "A", status: "failed",
      attempt_count: 2, last_attempted_at: null, last_error: "err", scraper_version: null,
      transactions_inserted: 0 },
    { id: 2, pdf_url: "http://y", filing_date: null, company_name: "B", status: "skipped",
      attempt_count: 1, last_attempted_at: null, last_error: null, scraper_version: null,
      transactions_inserted: 0 },
  ];

  const issuers: UnmatchedIssuer[] = [
    { id: 1, raw_name: "Acme", raw_isin: null, status: "pending",
      suggestion_issuer_id: null, created_at: "2026-01-01" },
  ];

  it("issuer_unmatched count equals issuers array length", () => {
    const groups = groupTransactions([]);
    const counts = countsByCategory(groups, filings, issuers);
    expect(counts.issuer_unmatched).toBe(1);
  });

  it("failed_filing count equals filings array length", () => {
    const groups = groupTransactions([]);
    const counts = countsByCategory(groups, filings, issuers);
    expect(counts.failed_filing).toBe(2);
  });

  it("planned categories always report 0", () => {
    const groups = groupTransactions([]);
    const counts = countsByCategory(groups, [], []);
    expect(counts.relationship).toBe(0);
    expect(counts.fixture_candidate).toBe(0);
  });
});

// ─── QUEUE_CATEGORIES metadata ────────────────────────────────────────────────

describe("QUEUE_CATEGORIES", () => {
  it("has exactly 9 entries", () => {
    expect(QUEUE_CATEGORIES).toHaveLength(9);
  });

  it("every category has valid coverage value", () => {
    for (const cat of QUEUE_CATEGORIES) {
      expect(["live", "partial", "planned"]).toContain(cat.coverage);
    }
  });

  it("relationship and fixture_candidate are planned", () => {
    const rel = QUEUE_CATEGORIES.find((c) => c.id === "relationship")!;
    const fix = QUEUE_CATEGORIES.find((c) => c.id === "fixture_candidate")!;
    expect(rel.coverage).toBe("planned");
    expect(fix.coverage).toBe("planned");
  });

  it("vehicle_fiduciary is partial coverage", () => {
    const cat = QUEUE_CATEGORIES.find((c) => c.id === "vehicle_fiduciary")!;
    expect(cat.coverage).toBe("partial");
  });
});

// ─── Mutation path: dispatchTransactionAction ─────────────────────────────────

describe("dispatchTransactionAction — mutation path", () => {
  const fetchMock = jest.fn();

  beforeAll(() => {
    global.fetch = fetchMock as typeof global.fetch;
  });

  afterEach(() => {
    fetchMock.mockReset();
  });

  it("POSTs to /review with action=confirm for the confirm action", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    await dispatchTransactionAction(42, "confirm");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/internal/transactions/42/review",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "confirm" }),
      })
    );
  });

  it("POSTs to /review with action=reject", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    await dispatchTransactionAction(7, "reject");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/internal/transactions/7/review",
      expect.objectContaining({ body: JSON.stringify({ action: "reject" }) })
    );
  });

  it("POSTs to /review with action=classify and type+rationale", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    await dispatchTransactionAction(5, "classify", {
      transaction_type: "grant",
      rationale: "Grant corrected",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/internal/transactions/5/review",
      expect.objectContaining({
        body: JSON.stringify({
          action: "classify",
          transaction_type: "grant",
          rationale: "Grant corrected",
        }),
      })
    );
  });

  it("POSTs to /note endpoint for note action", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    await dispatchTransactionAction(3, "note", { note: "needs check" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/internal/transactions/3/note",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ note: "needs check" }),
      })
    );
  });

  it("returns { ok: false, error } when API returns non-ok", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: "invalid action" }),
    });
    const result = await dispatchTransactionAction(1, "unknown_action");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toBe("invalid action");
  });

  it("falls back to HTTP status string when error body has no message", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => { throw new Error("no json"); },
    });
    const result = await dispatchTransactionAction(1, "confirm");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toBe("HTTP 500");
  });

  it("returns { ok: false } on network error", async () => {
    fetchMock.mockRejectedValue(new Error("Network error"));
    const result = await dispatchTransactionAction(1, "confirm");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toBe("Network error");
  });
});

// ─── Mutation path: dispatchFilingRetry ───────────────────────────────────────

describe("dispatchFilingRetry — mutation path", () => {
  const fetchMock = jest.fn();

  beforeAll(() => {
    global.fetch = fetchMock as typeof global.fetch;
  });

  afterEach(() => {
    fetchMock.mockReset();
  });

  it("POSTs to /filings/:id/retry", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    await dispatchFilingRetry(99);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/internal/filings/99/retry",
      expect.objectContaining({ method: "POST" })
    );
  });
});

// ─── Mutation path: dispatchIssuerAction ─────────────────────────────────────

describe("dispatchIssuerAction — mutation path", () => {
  const fetchMock = jest.fn();

  beforeAll(() => {
    global.fetch = fetchMock as typeof global.fetch;
  });

  afterEach(() => {
    fetchMock.mockReset();
  });

  it("POSTs resolve action with issuer_id", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    await dispatchIssuerAction(10, "resolve", 42);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/internal/issuers/10/resolve",
      expect.objectContaining({
        body: JSON.stringify({ action: "resolve", issuer_id: 42 }),
      })
    );
  });

  it("POSTs reject action without issuer_id", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    await dispatchIssuerAction(10, "reject");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/internal/issuers/10/resolve",
      expect.objectContaining({
        body: JSON.stringify({ action: "reject" }),
      })
    );
  });
});
