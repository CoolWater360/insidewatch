/**
 * Tests for lib/review.ts — review queue helpers.
 *
 * Verifies that getReviewQueueCounts and getTransactionsForReview:
 *   1. Apply identical filter conditions (same population)
 *   2. Return correct pagination metadata
 *   3. Do NOT select review_notes (migration 007 column absent from base schema)
 *   4. Surface PostgREST errors rather than fabricating empty results silently
 */

import {
  getReviewQueueCounts,
  getTransactionsForReview,
  getFilingById,
} from "../../lib/review";

// ─── Mock lib/supabase-server ─────────────────────────────────────────────────

let _mockData: unknown[] = [];
let _mockCount: number | null = null;
let _mockError: { message: string } | null = null;

function _buildChain(): Record<string, unknown> {
  const chain: Record<string, unknown> = {};
  for (const m of [
    "select", "eq", "or", "in", "order", "range", "limit", "single",
  ]) {
    chain[m] = jest.fn(() => chain);
  }
  chain["then"] = (
    resolve: (v: { data: unknown[]; count: number | null; error: unknown }) => void,
    reject?: (e: unknown) => void
  ) =>
    Promise.resolve({
      data: _mockData,
      count: _mockCount,
      error: _mockError,
    }).then(resolve, reject);
  chain["catch"] = (reject: (e: unknown) => void) =>
    Promise.resolve({
      data: _mockData,
      count: _mockCount,
      error: _mockError,
    }).catch(reject);
  return chain;
}

let _chain = _buildChain();
const mockSupabase = { from: jest.fn(() => _chain) };

jest.mock("../../lib/supabase-server", () => ({
  getSupabaseServer: jest.fn(() => mockSupabase),
}));

function setMock(
  rows: unknown[],
  count: number | null,
  err: { message: string } | null = null
): void {
  _mockData = rows;
  _mockCount = count;
  _mockError = err;
  _chain = _buildChain();
  mockSupabase.from.mockImplementation(() => _chain);
}

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const REVIEW_FILTER =
  "review_status.is.null,review_status.in.(pending_review,under_review)";

const TX_ROW = {
  id: 1,
  transaction_date: "2026-01-15",
  direction: "buy",
  transaction_type: "open_market",
  economic_intent: "discretionary",
  quantity: 1000,
  unit_price: 10.5,
  total_value: 10500,
  currency: "EUR",
  needs_review: true,
  review_status: null,
  review_reason: "low_confidence",
  extraction_confidence: 0.6,
  classification_rationale: "ACQUISTO keyword matched",
  raw_nature_text: "ACQUISTO SUL MERCATO",
  classification_override: false,
  isin: "IT0001234567",
  source_url: "https://example.euronext.com/filing/123.pdf",
  source_filing_id: 7,
  companies: { id: 42, name: "Acme SpA" },
  insiders: { full_name: "Mario Rossi", role: "CEO" },
};

const FILING_ROW = {
  id: 7,
  pdf_url: "https://example.euronext.com/filing/123.pdf",
  filing_date: "2026-01-15",
  company_name: "Acme SpA",
  status: "completed",
  attempt_count: 1,
  last_attempted_at: "2026-01-15T09:00:00Z",
  last_error: null,
  scraper_version: "1.2.0",
  transactions_inserted: 1,
  pdf_sha256: "abc123",
  storage_path: "filings/2026/01/abc123.pdf",
  file_size_bytes: 204800,
};

// ─── getReviewQueueCounts ─────────────────────────────────────────────────────

describe("getReviewQueueCounts", () => {
  it("returns pendingTransactions from the count header", async () => {
    setMock([], 282, null);
    const result = await getReviewQueueCounts();
    expect(result.pendingTransactions).toBe(282);
  });

  it("defaults to 0 when count is null", async () => {
    setMock([], null, null);
    const result = await getReviewQueueCounts();
    expect(result.pendingTransactions).toBe(0);
  });

  it("filters on needs_review=true and review_status population", async () => {
    setMock([], 10, null);
    await getReviewQueueCounts();
    expect(_chain.eq as jest.Mock).toHaveBeenCalledWith("needs_review", true);
    expect(_chain.or as jest.Mock).toHaveBeenCalledWith(REVIEW_FILTER);
  });
});

// ─── getTransactionsForReview ─────────────────────────────────────────────────

describe("getTransactionsForReview", () => {
  it("returns rows and total correctly", async () => {
    setMock([TX_ROW], 1, null);
    const result = await getTransactionsForReview(1, 25);
    expect(result.total).toBe(1);
    expect(result.rows).toHaveLength(1);
    expect(result.rows[0].id).toBe(1);
    expect(result.page).toBe(1);
    expect(result.pageSize).toBe(25);
    expect(result.totalPages).toBe(1);
  });

  it("uses the same filter as getReviewQueueCounts", async () => {
    setMock([TX_ROW], 1, null);
    await getTransactionsForReview(1, 25);
    expect(_chain.eq as jest.Mock).toHaveBeenCalledWith("needs_review", true);
    expect(_chain.or as jest.Mock).toHaveBeenCalledWith(REVIEW_FILTER);
  });

  it("computes range correctly for page 2", async () => {
    setMock([TX_ROW], 50, null);
    const result = await getTransactionsForReview(2, 25);
    expect(result.page).toBe(2);
    expect(result.totalPages).toBe(2);
    expect(_chain.range as jest.Mock).toHaveBeenCalledWith(25, 49);
  });

  it("totalPages rounds up correctly", async () => {
    setMock([TX_ROW], 26, null);
    const result = await getTransactionsForReview(1, 25);
    expect(result.totalPages).toBe(2);
  });

  it("selects source_url and source_filing_id", async () => {
    setMock([TX_ROW], 1, null);
    await getTransactionsForReview(1, 25);
    const selectArg = (_chain.select as jest.Mock).mock.calls[0][0] as string;
    expect(selectArg).toContain("source_url");
    expect(selectArg).toContain("source_filing_id");
  });

  it("does NOT select review_notes — migration 007 column absent from base schema", async () => {
    // This is the root-cause regression guard: if review_notes were re-added to
    // the SELECT and migration 007 is not applied in production, PostgREST
    // returns a 400, which the error handler silently converts to { rows: [], total: 0 }.
    setMock([TX_ROW], 1, null);
    await getTransactionsForReview(1, 25);
    const selectArg = (_chain.select as jest.Mock).mock.calls[0][0] as string;
    expect(selectArg).not.toContain("review_notes");
  });

  it("exposes queryError on PostgREST error instead of silently returning empty", async () => {
    const errMsg = "column transactions.review_notes does not exist";
    setMock([], null, { message: errMsg });
    const result = await getTransactionsForReview(1, 25);
    expect(result.rows).toHaveLength(0);
    expect(result.total).toBe(0);
    expect(result.totalPages).toBe(0);
    expect(result.queryError).toBe(errMsg);
  });

  it("queryError is undefined on success", async () => {
    setMock([TX_ROW], 1, null);
    const result = await getTransactionsForReview(1, 25);
    expect(result.queryError).toBeUndefined();
  });
});

// ─── getFilingById ────────────────────────────────────────────────────────────

describe("getFilingById", () => {
  it("returns FilingDetail with has_stored_document=true when storage_path set", async () => {
    setMock([FILING_ROW], null, null);
    // single() resolves the array to one item — mock returns the row in data
    _chain["single"] = jest.fn(() =>
      Promise.resolve({ data: FILING_ROW, error: null })
    );
    mockSupabase.from.mockImplementation(() => _chain);

    const result = await getFilingById(7);
    expect(result).not.toBeNull();
    expect(result!.id).toBe(7);
    expect(result!.pdf_url).toBe("https://example.euronext.com/filing/123.pdf");
    expect(result!.has_stored_document).toBe(true);
    expect(result!.file_size_bytes).toBe(204800);
    expect(result!.pdf_sha256).toBe("abc123");
  });

  it("has_stored_document is false when storage_path is null", async () => {
    const noStorage = { ...FILING_ROW, storage_path: null };
    _chain["single"] = jest.fn(() =>
      Promise.resolve({ data: noStorage, error: null })
    );
    mockSupabase.from.mockImplementation(() => _chain);

    const result = await getFilingById(7);
    expect(result!.has_stored_document).toBe(false);
  });

  it("returns null on error (filing not found)", async () => {
    _chain["single"] = jest.fn(() =>
      Promise.resolve({ data: null, error: { message: "No rows found" } })
    );
    mockSupabase.from.mockImplementation(() => _chain);

    const result = await getFilingById(9999);
    expect(result).toBeNull();
  });

  it("does not expose storage_path in the returned object", async () => {
    _chain["single"] = jest.fn(() =>
      Promise.resolve({ data: FILING_ROW, error: null })
    );
    mockSupabase.from.mockImplementation(() => _chain);

    const result = await getFilingById(7);
    expect(result).not.toBeNull();
    expect("storage_path" in result!).toBe(false);
  });
});
