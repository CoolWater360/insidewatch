/**
 * Tests for lib/filings.ts — pure filter-mapping helpers.
 * No Supabase mock needed: buildStatusFilter and buildSearchFilter are pure
 * functions that take FilingFilters and return query-parameter strings/arrays.
 *
 * Also tests the retry action path via lib/review-mutations.ts.
 */

import { buildStatusFilter, buildSearchFilter } from "../../lib/filings";
import { dispatchFilingRetry } from "../../lib/review-mutations";

// ─── buildStatusFilter ────────────────────────────────────────────────────────

describe("buildStatusFilter", () => {
  it("returns ['failed', 'skipped'] when only_failed_skipped is true", () => {
    expect(buildStatusFilter({ only_failed_skipped: true })).toEqual(["failed", "skipped"]);
  });

  it("only_failed_skipped takes precedence over status", () => {
    expect(buildStatusFilter({ only_failed_skipped: true, status: "completed" })).toEqual([
      "failed",
      "skipped",
    ]);
  });

  it("returns [status] when a specific status is set", () => {
    expect(buildStatusFilter({ status: "completed" })).toEqual(["completed"]);
  });

  it("returns [status] for each known status value", () => {
    const statuses = ["pending", "in_progress", "failed", "skipped", "retry_requested", "superseded"];
    for (const s of statuses) {
      expect(buildStatusFilter({ status: s })).toEqual([s]);
    }
  });

  it("returns undefined when no status filter is set", () => {
    expect(buildStatusFilter({})).toBeUndefined();
  });

  it("returns undefined when only q is set", () => {
    expect(buildStatusFilter({ q: "acme" })).toBeUndefined();
  });

  it("returns undefined when only no_storage is set", () => {
    expect(buildStatusFilter({ no_storage: true })).toBeUndefined();
  });
});

// ─── buildSearchFilter ────────────────────────────────────────────────────────

describe("buildSearchFilter", () => {
  it("constructs ilike OR filter for company_name and pdf_url", () => {
    expect(buildSearchFilter("Acme")).toBe(
      "company_name.ilike.%Acme%,pdf_url.ilike.%Acme%"
    );
  });

  it("handles multi-word query", () => {
    expect(buildSearchFilter("Banca Generali")).toBe(
      "company_name.ilike.%Banca Generali%,pdf_url.ilike.%Banca Generali%"
    );
  });

  it("passes through special regex characters literally", () => {
    const result = buildSearchFilter("Acme+SpA");
    expect(result).toBe("company_name.ilike.%Acme+SpA%,pdf_url.ilike.%Acme+SpA%");
  });

  it("includes both columns in the filter string", () => {
    const result = buildSearchFilter("test");
    expect(result).toContain("company_name.ilike.");
    expect(result).toContain("pdf_url.ilike.");
  });
});

// ─── dispatchFilingRetry — retry action path ──────────────────────────────────

describe("dispatchFilingRetry — retry action through RPC abstraction", () => {
  const fetchMock = jest.fn();

  beforeAll(() => {
    global.fetch = fetchMock as typeof global.fetch;
  });

  afterEach(() => {
    fetchMock.mockReset();
  });

  it("POSTs to /api/internal/filings/:id/retry", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    await dispatchFilingRetry(7);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/internal/filings/7/retry",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("returns { ok: true } on success", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    const result = await dispatchFilingRetry(7);
    expect(result.ok).toBe(true);
  });

  it("uses different filing IDs in the URL", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    await dispatchFilingRetry(123);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/internal/filings/123/retry",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("returns { ok: false, error } when the API returns non-ok with error body", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ error: "RPC failed" }),
    });
    const result = await dispatchFilingRetry(99);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toBe("RPC failed");
  });

  it("falls back to HTTP status string when response body has no error field", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ message: "not found" }),
    });
    const result = await dispatchFilingRetry(0);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toBe("HTTP 404");
  });

  it("returns { ok: false } on network failure", async () => {
    fetchMock.mockRejectedValue(new Error("connection refused"));
    const result = await dispatchFilingRetry(1);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toBe("connection refused");
  });
});
