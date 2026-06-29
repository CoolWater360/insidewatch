/**
 * Phase 5 — pure-function tests for quality-view.ts and ops-view.ts helpers.
 *
 * These cover only the deterministic, stateless helpers. Async server queries
 * require a live Supabase service-role key and are not unit-tested here.
 */

import {
  qualityOutcomeLabel,
  reviewCategoryLabel,
  classifyReviewHealth,
  formatConfidencePct,
  REVIEW_OUTCOME_LABELS,
  REVIEW_CATEGORY_LABELS,
} from "../../lib/quality-view";
import type { QualityReviewSummary } from "../../lib/quality-view";

import {
  filingStatusLabel,
  classifyFilingHealth,
  isRetryEligible,
  formatLatencyHours,
  computePercentiles,
  FILING_STATUS_LABELS,
} from "../../lib/ops-view";
import type { FilingStatusCounts } from "../../lib/ops-view";

// ─── quality-view: qualityOutcomeLabel ───────────────────────────────────────

describe("qualityOutcomeLabel", () => {
  it("maps known outcomes to Italian labels", () => {
    expect(qualityOutcomeLabel("confirmed")).toBe("Confermato");
    expect(qualityOutcomeLabel("corrected")).toBe("Corretto");
    expect(qualityOutcomeLabel("rejected")).toBe("Rifiutato");
  });

  it("passes through unknown outcome unchanged", () => {
    expect(qualityOutcomeLabel("unknown_outcome")).toBe("unknown_outcome");
    expect(qualityOutcomeLabel("")).toBe("");
  });

  it("covers all keys in REVIEW_OUTCOME_LABELS", () => {
    for (const [key, label] of Object.entries(REVIEW_OUTCOME_LABELS)) {
      expect(qualityOutcomeLabel(key)).toBe(label);
    }
  });
});

// ─── quality-view: reviewCategoryLabel ───────────────────────────────────────

describe("reviewCategoryLabel", () => {
  it("maps known categories to Italian labels", () => {
    expect(reviewCategoryLabel("unknown_direction")).toBe("Direzione non determinata");
    expect(reviewCategoryLabel("unknown_type")).toBe("Tipo sconosciuto");
    expect(reviewCategoryLabel("low_confidence")).toBe("Bassa confidenza");
    expect(reviewCategoryLabel("corporate_action")).toBe("Corporate action");
    expect(reviewCategoryLabel("vehicle_entity")).toBe("Veicolo / fiduciaria");
    expect(reviewCategoryLabel("issuer_resolution")).toBe("Emittente non risolto");
    expect(reviewCategoryLabel("other")).toBe("Altro");
  });

  it("passes through unknown category unchanged", () => {
    expect(reviewCategoryLabel("future_category")).toBe("future_category");
  });

  it("covers all keys in REVIEW_CATEGORY_LABELS", () => {
    for (const [key, label] of Object.entries(REVIEW_CATEGORY_LABELS)) {
      expect(reviewCategoryLabel(key)).toBe(label);
    }
  });
});

// ─── quality-view: classifyReviewHealth ──────────────────────────────────────

function makeSummary(overrides: Partial<QualityReviewSummary> = {}): QualityReviewSummary {
  return {
    total: 0,
    confirmed: 0,
    corrected: 0,
    rejected: 0,
    fixture_eligible_pending: 0,
    ...overrides,
  };
}

describe("classifyReviewHealth", () => {
  it("returns 'none' when there are no reviews at all", () => {
    expect(classifyReviewHealth(makeSummary({ total: 0 }))).toBe("none");
  });

  it("returns 'ok' when correction+rejection rate is <= 30%", () => {
    // 3 corrected + 0 rejected out of 10 = 30% → ok
    expect(classifyReviewHealth(makeSummary({ total: 10, confirmed: 7, corrected: 3, rejected: 0 }))).toBe("ok");
  });

  it("returns 'warn' when correction+rejection rate exceeds 30%", () => {
    // 4 corrected out of 10 = 40% → warn
    expect(classifyReviewHealth(makeSummary({ total: 10, confirmed: 6, corrected: 4, rejected: 0 }))).toBe("warn");
  });

  it("counts both corrected and rejected in the rate", () => {
    // 2 corrected + 2 rejected out of 10 = 40% → warn
    expect(classifyReviewHealth(makeSummary({ total: 10, confirmed: 6, corrected: 2, rejected: 2 }))).toBe("warn");
  });

  it("returns 'ok' for all-confirmed reviews", () => {
    expect(classifyReviewHealth(makeSummary({ total: 100, confirmed: 100 }))).toBe("ok");
  });
});

// ─── quality-view: formatConfidencePct ───────────────────────────────────────

describe("formatConfidencePct", () => {
  it("returns '—' for null", () => {
    expect(formatConfidencePct(null)).toBe("—");
  });

  it("rounds to nearest percent", () => {
    expect(formatConfidencePct(0.655)).toBe("66%");
    expect(formatConfidencePct(0.5)).toBe("50%");
    expect(formatConfidencePct(1.0)).toBe("100%");
    expect(formatConfidencePct(0.0)).toBe("0%");
  });

  it("handles values above 1 without crashing (edge case — caller is responsible for clamping)", () => {
    expect(typeof formatConfidencePct(1.05)).toBe("string");
  });
});

// ─── ops-view: filingStatusLabel ─────────────────────────────────────────────

describe("filingStatusLabel", () => {
  it("maps known statuses to Italian labels", () => {
    expect(filingStatusLabel("pending")).toBe("In attesa");
    expect(filingStatusLabel("in_progress")).toBe("In elaborazione");
    expect(filingStatusLabel("completed")).toBe("Completato");
    expect(filingStatusLabel("failed")).toBe("Fallito");
    expect(filingStatusLabel("skipped")).toBe("Saltato");
  });

  it("passes through unknown status unchanged", () => {
    expect(filingStatusLabel("unknown_status")).toBe("unknown_status");
  });

  it("covers all keys in FILING_STATUS_LABELS", () => {
    for (const [key, label] of Object.entries(FILING_STATUS_LABELS)) {
      expect(filingStatusLabel(key)).toBe(label);
    }
  });
});

// ─── ops-view: classifyFilingHealth ──────────────────────────────────────────

function makeCounts(overrides: Partial<FilingStatusCounts> = {}): FilingStatusCounts {
  return {
    pending: 0,
    in_progress: 0,
    completed: 0,
    failed: 0,
    skipped: 0,
    retry_eligible: 0,
    total: 0,
    ...overrides,
  };
}

describe("classifyFilingHealth", () => {
  it("returns 'ok' when no failed, skipped, or pending filings", () => {
    expect(classifyFilingHealth(makeCounts({ completed: 10 }))).toBe("ok");
  });

  it("returns 'error' when there are failed filings", () => {
    expect(classifyFilingHealth(makeCounts({ failed: 1 }))).toBe("error");
  });

  it("returns 'error' even when there are also completed filings", () => {
    expect(classifyFilingHealth(makeCounts({ completed: 50, failed: 2 }))).toBe("error");
  });

  it("returns 'warn' when there are skipped but no failed filings", () => {
    expect(classifyFilingHealth(makeCounts({ skipped: 3 }))).toBe("warn");
  });

  it("returns 'warn' when there are pending but no failed filings", () => {
    expect(classifyFilingHealth(makeCounts({ pending: 5 }))).toBe("warn");
  });

  it("failed takes priority over skipped", () => {
    expect(classifyFilingHealth(makeCounts({ failed: 1, skipped: 2 }))).toBe("error");
  });
});

// ─── ops-view: isRetryEligible ────────────────────────────────────────────────

describe("isRetryEligible", () => {
  it("returns true for failed filing with attempts remaining", () => {
    expect(isRetryEligible("failed", 1, 3)).toBe(true);
    expect(isRetryEligible("failed", 0, 1)).toBe(true);
  });

  it("returns false when attempt_count equals max_attempts", () => {
    expect(isRetryEligible("failed", 3, 3)).toBe(false);
  });

  it("returns false when attempt_count exceeds max_attempts", () => {
    expect(isRetryEligible("failed", 4, 3)).toBe(false);
  });

  it("returns false for non-failed statuses regardless of attempt count", () => {
    expect(isRetryEligible("pending",     0, 3)).toBe(false);
    expect(isRetryEligible("in_progress", 0, 3)).toBe(false);
    expect(isRetryEligible("completed",   0, 3)).toBe(false);
    expect(isRetryEligible("skipped",     0, 3)).toBe(false);
  });
});

// ─── ops-view: formatLatencyHours ────────────────────────────────────────────

describe("formatLatencyHours", () => {
  it("returns '—' for null", () => {
    expect(formatLatencyHours(null)).toBe("—");
  });

  it("formats sub-hour values in minutes", () => {
    expect(formatLatencyHours(0.5)).toBe("30 min");
    expect(formatLatencyHours(0.25)).toBe("15 min");
  });

  it("formats values under 24 hours in hours", () => {
    expect(formatLatencyHours(1.0)).toBe("1.0 h");
    expect(formatLatencyHours(12.5)).toBe("12.5 h");
    expect(formatLatencyHours(23.9)).toBe("23.9 h");
  });

  it("formats values 24 hours and above in days", () => {
    expect(formatLatencyHours(24)).toBe("1.0 gg");
    expect(formatLatencyHours(48)).toBe("2.0 gg");
    expect(formatLatencyHours(36)).toBe("1.5 gg");
  });

  it("formats zero as minutes", () => {
    expect(formatLatencyHours(0)).toBe("0 min");
  });
});

// ─── ops-view: computePercentiles ────────────────────────────────────────────

describe("computePercentiles", () => {
  it("returns null for empty array", () => {
    expect(computePercentiles([])).toEqual({ median: null, p95: null });
  });

  it("returns the single element as both median and p95", () => {
    const { median, p95 } = computePercentiles([5]);
    expect(median).toBe(5);
    expect(p95).toBe(5);
  });

  it("computes median for even-length sorted array", () => {
    const { median } = computePercentiles([1, 2, 3, 4]);
    expect(median).toBe(2.5); // (2 + 3) / 2
  });

  it("computes median for odd-length sorted array", () => {
    const { median } = computePercentiles([1, 2, 3, 4, 5]);
    expect(median).toBe(3);
  });

  it("computes p95 from sorted array", () => {
    const sorted = Array.from({ length: 100 }, (_, i) => i + 1); // 1..100
    const { p95 } = computePercentiles(sorted);
    expect(p95).toBe(96); // floor(100 * 0.95) = index 95 → value 96
  });

  it("assumes input is already sorted ascending", () => {
    // If the array is pre-sorted, [1, 10, 100] → median is 10
    const { median } = computePercentiles([1, 10, 100]);
    expect(median).toBe(10);
  });
});
