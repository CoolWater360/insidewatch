"""
Phase 11 — unit tests for scraper/latency.py and scraper/quality.py.

All tests are pure (no network, no DB).  Quality module DB calls are
monkey-patched so tests remain fast and deterministic.
"""

import pytest
from unittest.mock import MagicMock, patch

from scraper.latency import (
    LATENCY_COLUMNS,
    PIPELINE_STAGES,
    _parse_ts,
    aggregate_stage_stats,
    compute_all_stages,
    coverage_report,
    numeric_stats,
    stage_seconds,
)
from scraper.quality import (
    ALERT_THRESHOLDS,
    check_alert_thresholds,
    compute_quality_metrics,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

# A complete filing row with all timestamps populated.
_FULL_ROW = {
    "source_published_utc": "2024-01-01T08:00:00+00:00",
    "discovered_utc":        "2024-01-01T10:00:00+00:00",  # +7200 s after source
    "downloaded_utc":        "2024-01-01T10:00:10+00:00",  # +10 s after discovery
    "stored_utc":            "2024-01-01T10:00:10.400+00:00",  # +0.4 s
    "parsed_utc":            "2024-01-01T10:00:11.600+00:00",  # +1.2 s
    "delivered_utc":         "2024-01-01T10:00:12.500+00:00",  # +0.9 s
}

# A pre-014 row: stored_utc is absent (historical).
_PRE_014_ROW = {
    "source_published_utc": "2023-06-01T09:00:00+00:00",
    "discovered_utc":        "2023-06-01T11:00:00+00:00",
    "downloaded_utc":        "2023-06-01T11:00:15+00:00",
    "stored_utc":            None,
    "parsed_utc":            "2023-06-01T11:00:17+00:00",
    "delivered_utc":         "2023-06-01T11:00:18+00:00",
}

# A row with Z-suffix timestamps (as Supabase sometimes returns).
_Z_ROW = {
    "source_published_utc": "2024-03-15T06:00:00Z",
    "discovered_utc":        "2024-03-15T08:00:00Z",
    "downloaded_utc":        "2024-03-15T08:00:05Z",
    "stored_utc":            "2024-03-15T08:00:05.300Z",
    "parsed_utc":            "2024-03-15T08:00:06.100Z",
    "delivered_utc":         "2024-03-15T08:00:06.900Z",
}


# ─── _parse_ts ────────────────────────────────────────────────────────────────

class TestParseTs:
    def test_iso_with_offset(self):
        dt = _parse_ts("2024-01-01T10:00:00+00:00")
        assert dt is not None
        assert dt.year == 2024

    def test_z_suffix(self):
        dt = _parse_ts("2024-01-01T10:00:00Z")
        assert dt is not None

    def test_none_input(self):
        assert _parse_ts(None) is None

    def test_empty_string(self):
        assert _parse_ts("") is None

    def test_garbage(self):
        assert _parse_ts("not-a-date") is None

    def test_returns_aware(self):
        from datetime import timezone
        dt = _parse_ts("2024-01-01T10:00:00+00:00")
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0


# ─── stage_seconds ────────────────────────────────────────────────────────────

class TestStageSeconds:
    def test_full_row_end_to_end(self):
        secs = stage_seconds(_FULL_ROW, "source_published_utc", "delivered_utc")
        # 2 hours + ~12.5 seconds
        assert secs is not None
        assert 7200 < secs < 7220

    def test_download_to_stored(self):
        secs = stage_seconds(_FULL_ROW, "downloaded_utc", "stored_utc")
        assert secs is not None
        assert abs(secs - 0.4) < 0.01

    def test_missing_start_col(self):
        row = dict(_FULL_ROW)
        row["source_published_utc"] = None
        assert stage_seconds(row, "source_published_utc", "delivered_utc") is None

    def test_missing_end_col(self):
        row = dict(_FULL_ROW)
        row["delivered_utc"] = None
        assert stage_seconds(row, "source_published_utc", "delivered_utc") is None

    def test_negative_duration_returns_none(self):
        row = {
            "downloaded_utc": "2024-01-01T10:00:10+00:00",
            "stored_utc":     "2024-01-01T09:00:00+00:00",  # stored before downloaded — impossible
        }
        assert stage_seconds(row, "downloaded_utc", "stored_utc") is None

    def test_pre_014_stored_utc_null(self):
        assert stage_seconds(_PRE_014_ROW, "downloaded_utc", "stored_utc") is None

    def test_pre_014_other_stages_computable(self):
        secs = stage_seconds(_PRE_014_ROW, "parsed_utc", "delivered_utc")
        assert secs is not None
        assert abs(secs - 1.0) < 0.01

    def test_z_suffix_row(self):
        secs = stage_seconds(_Z_ROW, "source_published_utc", "delivered_utc")
        assert secs is not None
        assert 7200 < secs < 7210


# ─── compute_all_stages ───────────────────────────────────────────────────────

class TestComputeAllStages:
    def test_returns_all_stage_names(self):
        result = compute_all_stages(_FULL_ROW)
        expected = {name for name, _, _ in PIPELINE_STAGES}
        assert set(result.keys()) == expected

    def test_full_row_no_none(self):
        result = compute_all_stages(_FULL_ROW)
        assert all(v is not None for v in result.values())

    def test_pre_014_two_stages_none(self):
        result = compute_all_stages(_PRE_014_ROW)
        assert result["download_to_stored"] is None
        assert result["stored_to_parsed"] is None
        # These should still compute
        assert result["detection_to_download"] is not None
        assert result["parsed_to_delivered"] is not None
        assert result["scraper_total"] is not None

    def test_empty_row_all_none(self):
        result = compute_all_stages({})
        assert all(v is None for v in result.values())


# ─── numeric_stats ────────────────────────────────────────────────────────────

class TestNumericStats:
    def test_empty_list(self):
        s = numeric_stats([])
        assert s["n"] == 0
        assert s["avg"] is None
        assert s["p50"] is None
        assert s["p95"] is None

    def test_single_value(self):
        s = numeric_stats([42.0])
        assert s["n"] == 1
        assert s["avg"] == 42.0
        assert s["p50"] == 42.0
        assert s["p95"] == 42.0
        assert s["min"] == 42.0
        assert s["max"] == 42.0

    def test_simple_sequence(self):
        # [1, 2, 3, 4, 5]
        s = numeric_stats([1.0, 2.0, 3.0, 4.0, 5.0])
        assert s["n"] == 5
        assert abs(s["avg"] - 3.0) < 0.001
        assert abs(s["p50"] - 3.0) < 0.001
        assert s["min"] == 1.0
        assert s["max"] == 5.0

    def test_none_values_filtered(self):
        s = numeric_stats([None, 10.0, None, 20.0])
        assert s["n"] == 2
        assert abs(s["avg"] - 15.0) < 0.001

    def test_p95_100_values(self):
        values = list(range(1, 101))  # 1..100
        s = numeric_stats(values)
        # p95 of 1..100 should be near 95
        assert 94 <= s["p95"] <= 96

    def test_confidence_scores_0_to_1(self):
        scores = [0.30, 0.50, 0.65, 0.80, 0.85, 0.90, 0.90]
        s = numeric_stats(scores)
        assert 0.0 <= s["avg"] <= 1.0
        assert s["min"] == pytest.approx(0.30, abs=0.001)
        assert s["max"] == pytest.approx(0.90, abs=0.001)


# ─── aggregate_stage_stats ────────────────────────────────────────────────────

class TestAggregateStageStats:
    def test_empty_input(self):
        stats = aggregate_stage_stats([])
        for name, _, _ in PIPELINE_STAGES:
            assert stats[name]["n"] == 0

    def test_single_full_row(self):
        stats = aggregate_stage_stats([_FULL_ROW])
        assert stats["end_to_end"]["n"] == 1
        assert stats["download_to_stored"]["n"] == 1
        assert stats["stored_to_parsed"]["n"] == 1

    def test_pre_014_row_stages(self):
        stats = aggregate_stage_stats([_PRE_014_ROW])
        assert stats["download_to_stored"]["n"] == 0
        assert stats["stored_to_parsed"]["n"] == 0
        assert stats["detection_to_download"]["n"] == 1
        assert stats["parsed_to_delivered"]["n"] == 1
        assert stats["scraper_total"]["n"] == 1

    def test_mixed_rows_partial_coverage(self):
        # Two full rows + one pre-014 row
        rows = [_FULL_ROW, _Z_ROW, _PRE_014_ROW]
        stats = aggregate_stage_stats(rows)
        assert stats["download_to_stored"]["n"] == 2  # only full + Z
        assert stats["stored_to_parsed"]["n"] == 2
        assert stats["scraper_total"]["n"] == 3       # all three
        assert stats["end_to_end"]["n"] == 3

    def test_returns_all_stage_keys(self):
        stats = aggregate_stage_stats([_FULL_ROW])
        expected = {name for name, _, _ in PIPELINE_STAGES}
        assert set(stats.keys()) == expected


# ─── coverage_report ──────────────────────────────────────────────────────────

class TestCoverageReport:
    def test_empty(self):
        report = coverage_report([])
        assert all(v == 0.0 for v in report.values())

    def test_full_coverage(self):
        rows = [_FULL_ROW, _Z_ROW]
        report = coverage_report(rows)
        for name, _, _ in PIPELINE_STAGES:
            assert report[name] == 1.0, f"Expected full coverage for {name}"

    def test_partial_coverage_stored_utc(self):
        rows = [_FULL_ROW, _PRE_014_ROW]  # 1 of 2 has stored_utc
        report = coverage_report(rows)
        assert report["download_to_stored"] == pytest.approx(0.5, abs=0.001)
        assert report["stored_to_parsed"]   == pytest.approx(0.5, abs=0.001)
        assert report["scraper_total"]      == pytest.approx(1.0, abs=0.001)

    def test_coverage_values_in_0_1(self):
        rows = [_FULL_ROW, _PRE_014_ROW, _Z_ROW]
        report = coverage_report(rows)
        for name, v in report.items():
            assert 0.0 <= v <= 1.0, f"{name} coverage out of range: {v}"


# ─── LATENCY_COLUMNS ─────────────────────────────────────────────────────────

class TestLatencyColumns:
    def test_all_six_columns_present(self):
        cols = LATENCY_COLUMNS.split(",")
        expected = {
            "source_published_utc", "discovered_utc", "downloaded_utc",
            "stored_utc", "parsed_utc", "delivered_utc",
        }
        assert set(cols) == expected

    def test_no_extra_whitespace(self):
        for col in LATENCY_COLUMNS.split(","):
            assert col == col.strip()


# ─── check_alert_thresholds ───────────────────────────────────────────────────

class TestCheckAlertThresholds:
    def _ok_quality(self):
        return {
            "review_rate_pct":        5.0,
            "unknown_direction_pct":  2.0,
            "stale_in_progress_count": 0,
            "failed_filings_7d":       3,
            "window_days":            30,
        }

    def _ok_latency(self):
        return {
            "end_to_end": {"n": 10, "avg": 500.0, "p50": 450.0, "p95": 900.0, "min": 100.0, "max": 1000.0}
        }

    def test_no_alerts_when_all_ok(self):
        alerts = check_alert_thresholds(self._ok_quality(), self._ok_latency())
        assert alerts == []

    def test_review_rate_breach(self):
        q = self._ok_quality()
        q["review_rate_pct"] = 25.0
        alerts = check_alert_thresholds(q, self._ok_latency())
        assert any(a["metric"] == "review_rate_pct" for a in alerts)

    def test_unknown_direction_breach(self):
        q = self._ok_quality()
        q["unknown_direction_pct"] = 6.0
        alerts = check_alert_thresholds(q, self._ok_latency())
        assert any(a["metric"] == "unknown_direction_pct" for a in alerts)

    def test_stale_in_progress_breach(self):
        q = self._ok_quality()
        q["stale_in_progress_count"] = 8
        alerts = check_alert_thresholds(q, self._ok_latency())
        assert any(a["metric"] == "stale_in_progress_count" for a in alerts)

    def test_failed_7d_breach(self):
        q = self._ok_quality()
        q["failed_filings_7d"] = 25
        alerts = check_alert_thresholds(q, self._ok_latency())
        assert any(a["metric"] == "failed_filings_7d" for a in alerts)

    def test_p95_latency_breach(self):
        latency = {
            "end_to_end": {"n": 10, "avg": 3000.0, "p50": 2000.0, "p95": 4000.0,
                           "min": 100.0, "max": 5000.0}
        }
        alerts = check_alert_thresholds(self._ok_quality(), latency)
        assert any(a["metric"] == "p95_end_to_end_seconds" for a in alerts)

    def test_at_threshold_no_alert(self):
        # Exactly at threshold — should NOT fire (must be strictly greater than)
        q = self._ok_quality()
        q["review_rate_pct"] = ALERT_THRESHOLDS["review_rate_pct"]  # == 20.0
        alerts = check_alert_thresholds(q, self._ok_latency())
        assert not any(a["metric"] == "review_rate_pct" for a in alerts)

    def test_none_value_skipped(self):
        q = self._ok_quality()
        q["review_rate_pct"] = None   # unknown — no alert
        alerts = check_alert_thresholds(q, self._ok_latency())
        assert not any(a["metric"] == "review_rate_pct" for a in alerts)

    def test_custom_thresholds(self):
        q = self._ok_quality()
        q["review_rate_pct"] = 3.0   # below default threshold
        custom = dict(ALERT_THRESHOLDS)
        custom["review_rate_pct"] = 2.0   # lower custom threshold
        alerts = check_alert_thresholds(q, self._ok_latency(), thresholds=custom)
        assert any(a["metric"] == "review_rate_pct" for a in alerts)

    def test_alert_structure(self):
        q = self._ok_quality()
        q["review_rate_pct"] = 99.0
        alerts = check_alert_thresholds(q, self._ok_latency())
        a = alerts[0]
        assert "metric" in a
        assert "value" in a
        assert "threshold" in a
        assert "message" in a
        assert isinstance(a["message"], str)

    def test_missing_end_to_end_in_latency(self):
        # If end_to_end stage is absent, no P95 alert is raised (graceful).
        alerts = check_alert_thresholds(self._ok_quality(), {})
        assert not any(a["metric"] == "p95_end_to_end_seconds" for a in alerts)


# ─── compute_quality_metrics (mocked DB) ──────────────────────────────────────

def _make_mock_count(values: dict):
    """Factory: returns a _count side-effect that dispatches on table + params."""
    call_count = [0]

    def _count(url, key, table, params):
        idx = call_count[0]
        call_count[0] += 1
        return list(values.values())[idx] if idx < len(values) else 0

    return _count


class TestComputeQualityMetrics:
    def _mock_counts(self, total=100, needs_review=10, unknown_dir=3,
                     unknown_type=5, corrections=2, stale=1, fail_7d=4):
        return [total, needs_review, unknown_dir, unknown_type, corrections, stale, fail_7d]

    def _mock_conf_rows(self):
        return [
            {"extraction_confidence": 0.85, "classification_confidence": 0.75},
            {"extraction_confidence": 0.90, "classification_confidence": 0.80},
            {"extraction_confidence": None,  "classification_confidence": 0.70},
        ]

    def test_rates_computed_correctly(self):
        counts = self._mock_counts()
        conf_rows = self._mock_conf_rows()
        with patch("scraper.quality._count", side_effect=counts), \
             patch("scraper.quality._select", return_value=conf_rows):
            q = compute_quality_metrics("http://x", "key", window_days=30)

        assert q["total_transactions"]  == 100
        assert q["needs_review_count"]  == 10
        assert q["review_rate_pct"]     == pytest.approx(10.0, abs=0.01)
        assert q["unknown_direction_count"] == 3
        assert q["unknown_direction_pct"]   == pytest.approx(3.0, abs=0.01)
        assert q["correction_count"]    == 2
        assert q["correction_rate_pct"] == pytest.approx(2.0, abs=0.01)

    def test_confidence_stats_populated(self):
        counts = self._mock_counts()
        conf_rows = self._mock_conf_rows()
        with patch("scraper.quality._count", side_effect=counts), \
             patch("scraper.quality._select", return_value=conf_rows):
            q = compute_quality_metrics("http://x", "key", window_days=30)

        ext = q["confidence"]["extraction"]
        cls_ = q["confidence"]["classification"]
        assert ext["n"] == 2    # one None filtered out
        assert cls_["n"] == 3
        assert ext["avg"] == pytest.approx(0.875, abs=0.001)

    def test_zero_transactions_no_division(self):
        # total=0 — should not raise ZeroDivisionError
        with patch("scraper.quality._count", return_value=0), \
             patch("scraper.quality._select", return_value=[]):
            q = compute_quality_metrics("http://x", "key", window_days=30)

        assert q["total_transactions"]  == 0
        assert q["review_rate_pct"]     is None
        assert q["unknown_direction_pct"] is None
        assert q["correction_rate_pct"] is None

    def test_stale_and_failures(self):
        counts = self._mock_counts(stale=7, fail_7d=25)
        with patch("scraper.quality._count", side_effect=counts), \
             patch("scraper.quality._select", return_value=[]):
            q = compute_quality_metrics("http://x", "key", window_days=30)

        assert q["stale_in_progress_count"] == 7
        assert q["failed_filings_7d"]       == 25

    def test_window_days_respected(self):
        with patch("scraper.quality._count", return_value=0) as mock_count, \
             patch("scraper.quality._select", return_value=[]):
            compute_quality_metrics("http://x", "key", window_days=7)
        # All _count calls should have been made (we don't assert exact params here
        # since the HTTP call is mocked at _count level — just verify it ran).
        assert mock_count.call_count > 0
