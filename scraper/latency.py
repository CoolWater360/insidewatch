"""
Latency measurement — Phase 11.

Computes per-stage durations for the filing pipeline and aggregates them
into percentile statistics.  All functions are pure (no DB I/O).

Pipeline stages (timestamp columns in the filings table):

    Stage name                  Start column          End column
    ─────────────────────────── ───────────────────── ─────────────────────
    publication_to_detection    source_published_utc  discovered_utc
    detection_to_download       discovered_utc        downloaded_utc
    download_to_stored          downloaded_utc        stored_utc
    stored_to_parsed            stored_utc            parsed_utc
    parsed_to_delivered         parsed_utc            delivered_utc
    end_to_end                  source_published_utc  delivered_utc
    scraper_total               discovered_utc        delivered_utc

All durations are in seconds.  None is returned for any stage where one
or both timestamps are absent or where the computed duration is negative
(clock skew / data error — never propagated as real data).

Usage (typical):
    from scraper.latency import PIPELINE_STAGES, aggregate_stage_stats

    rows = _select(url, key, "filings",
                   "source_published_utc,discovered_utc,downloaded_utc,"
                   "stored_utc,parsed_utc,delivered_utc",
                   {"status": "eq.completed", "limit": "500"})
    stats = aggregate_stage_stats(rows)
    # stats["end_to_end"]["p95"] → P95 end-to-end seconds (or None)
"""

from datetime import datetime, timezone
from typing import Optional

# Ordered list of (name, start_col, end_col).  Order matters for reports.
PIPELINE_STAGES = [
    ("publication_to_detection", "source_published_utc", "discovered_utc"),
    ("detection_to_download",    "discovered_utc",       "downloaded_utc"),
    ("download_to_stored",       "downloaded_utc",       "stored_utc"),
    ("stored_to_parsed",         "stored_utc",           "parsed_utc"),
    ("parsed_to_delivered",      "parsed_utc",           "delivered_utc"),
    ("end_to_end",               "source_published_utc", "delivered_utc"),
    ("scraper_total",            "discovered_utc",       "delivered_utc"),
]

# Columns that must be fetched from the DB to populate all stages.
LATENCY_COLUMNS = (
    "source_published_utc,discovered_utc,downloaded_utc,"
    "stored_utc,parsed_utc,delivered_utc"
)


def _parse_ts(ts: str) -> Optional[datetime]:
    """Parse an ISO 8601 string into a timezone-aware datetime, or None."""
    if not ts:
        return None
    try:
        ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def stage_seconds(row: dict, start_col: str, end_col: str) -> Optional[float]:
    """
    Return the duration in seconds between two timestamp columns in a filing
    row dict.

    Returns None when:
      · either timestamp column is absent / NULL / unparseable;
      · the computed duration is negative (clock skew or data error).
    """
    s = _parse_ts(row.get(start_col) or "")
    e = _parse_ts(row.get(end_col) or "")
    if s is None or e is None:
        return None
    diff = (e - s).total_seconds()
    return diff if diff >= 0 else None


def compute_all_stages(row: dict) -> dict:
    """
    Return {stage_name: seconds_or_None} for all PIPELINE_STAGES applied to
    a single filing row dict.
    """
    return {name: stage_seconds(row, start, end) for name, start, end in PIPELINE_STAGES}


def numeric_stats(values: list) -> dict:
    """
    Return descriptive statistics for a list of numeric values (durations,
    confidence scores, or any continuous metric).

    Returned keys:
        n    int   number of non-None values
        avg  float mean, or None when n=0
        p50  float median, or None when n=0
        p95  float 95th percentile, or None when n=0
        min  float minimum, or None when n=0
        max  float maximum, or None when n=0
    """
    valid = [v for v in values if v is not None]
    if not valid:
        return {"n": 0, "avg": None, "p50": None, "p95": None, "min": None, "max": None}
    s = sorted(valid)
    n = len(s)

    def _pct(p: float) -> float:
        idx = (n - 1) * p / 100
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        return round(s[lo] + (s[hi] - s[lo]) * (idx - lo), 3)

    return {
        "n":   n,
        "avg": round(sum(s) / n, 3),
        "p50": _pct(50),
        "p95": _pct(95),
        "min": round(s[0], 3),
        "max": round(s[-1], 3),
    }


def aggregate_stage_stats(rows: list) -> dict:
    """
    Aggregate latency statistics across a list of filing row dicts.

    Returns {stage_name: numeric_stats_dict} for all PIPELINE_STAGES.
    Stages with no valid timestamps in the sample will return n=0 stats.

    Example:
        {
            "publication_to_detection": {"n": 42, "avg": 7200.0, ...},
            "detection_to_download":    {"n": 42, "avg": 8.3, ...},
            "download_to_stored":       {"n": 18, "avg": 0.4, ...},  # only post-014 rows
            "stored_to_parsed":         {"n": 18, "avg": 1.2, ...},
            "parsed_to_delivered":      {"n": 42, "avg": 0.9, ...},
            "end_to_end":               {"n": 39, "avg": 7213.5, ...},
            "scraper_total":            {"n": 42, "avg": 10.2, ...},
        }
    """
    buckets: dict = {name: [] for name, _, _ in PIPELINE_STAGES}
    for row in rows:
        for name, start, end in PIPELINE_STAGES:
            v = stage_seconds(row, start, end)
            if v is not None:
                buckets[name].append(v)
    return {name: numeric_stats(buckets[name]) for name, _, _ in PIPELINE_STAGES}


def coverage_report(rows: list) -> dict:
    """
    Return the fraction of rows (0.0–1.0) that have both timestamps present
    for each stage.  Useful for diagnosing which historical stages are missing data.
    """
    if not rows:
        return {name: 0.0 for name, _, _ in PIPELINE_STAGES}
    result = {}
    for name, start, end in PIPELINE_STAGES:
        present = sum(
            1 for r in rows
            if r.get(start) and r.get(end)
        )
        result[name] = round(present / len(rows), 3)
    return result
