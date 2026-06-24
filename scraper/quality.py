"""
Quality metrics — Phase 11.

Queries the database for parser quality signals and returns structured
metrics for embedding in the operations report or for alerting.

Metrics computed over a configurable lookback window:
    total_transactions           int
    review_rate_pct              float  % of transactions with needs_review=true
    unknown_direction_pct        float  % with direction='unknown'
    unknown_type_pct             float  % with transaction_type='unknown'
    correction_rate_pct          float  % with classification_override=true
    confidence.extraction        dict   numeric_stats of extraction_confidence
    confidence.classification    dict   numeric_stats of classification_confidence
    stale_in_progress_count      int    filings currently stuck in in_progress
    failed_filings_7d            int    filings that entered 'failed' in last 7 days

Alert thresholds (configurable; used by check_alert_thresholds()):
    review_rate_pct        > 20 %
    unknown_direction_pct  >  5 %
    stale_in_progress       >  5
    failed_filings_7d       > 20
    P95 end_to_end (s)     > 3600

Usage:
    from scraper.quality import compute_quality_metrics, check_alert_thresholds
    from scraper.latency import aggregate_stage_stats

    quality = compute_quality_metrics(url, key, window_days=30)
    latency = aggregate_stage_stats(rows)
    alerts  = check_alert_thresholds(quality, latency)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from .latency import numeric_stats

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_DAYS = 30

# Thresholds — exceeding any of these triggers an alert entry in the report.
ALERT_THRESHOLDS: dict = {
    "review_rate_pct":        20.0,   # > 20 % of recent transactions need review
    "unknown_direction_pct":   5.0,   # > 5 % have unknown direction
    "stale_in_progress_count": 5,     # > 5 filings stuck in_progress right now
    "failed_filings_7d":      20,     # > 20 filing failures in the past 7 days
    "p95_end_to_end_seconds": 3600.0, # > 3600 s (1 h) P95 end-to-end latency
}


def _headers(key: str) -> dict:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "count=exact",
    }


def _count(url: str, key: str, table: str, params: dict) -> int:
    try:
        r = httpx.get(
            f"{url}/rest/v1/{table}",
            headers=_headers(key),
            params={"select": "id", "limit": "0", **params},
            timeout=15,
        )
        r.raise_for_status()
        return int(r.headers.get("content-range", "*/0").split("/")[-1])
    except Exception as exc:
        logger.warning("quality._count(%s) failed: %s", table, exc)
        return 0


def _select(url: str, key: str, table: str, columns: str, params: dict) -> list:
    try:
        r = httpx.get(
            f"{url}/rest/v1/{table}",
            headers=_headers(key),
            params={"select": columns, **params},
            timeout=20,
        )
        r.raise_for_status()
        result = r.json()
        return result if isinstance(result, list) else []
    except Exception as exc:
        logger.warning("quality._select(%s) failed: %s", table, exc)
        return []


def compute_quality_metrics(
    supabase_url: str,
    supabase_key: str,
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
) -> dict:
    """
    Compute quality metrics over the last ``window_days`` days.

    Returns a JSON-serialisable dict suitable for embedding in the operations
    report and for passing to check_alert_thresholds().
    """
    now  = datetime.now(timezone.utc)
    since = (now - timedelta(days=window_days)).isoformat()
    ago_7d = (now - timedelta(days=7)).isoformat()

    bp = {"created_at": f"gte.{since}"}   # base params for transaction queries

    total        = _count(supabase_url, supabase_key, "transactions", bp)
    needs_review = _count(supabase_url, supabase_key, "transactions",
                          {"needs_review": "eq.true", **bp})
    unknown_dir  = _count(supabase_url, supabase_key, "transactions",
                          {"direction": "eq.unknown", **bp})
    unknown_type = _count(supabase_url, supabase_key, "transactions",
                          {"transaction_type": "eq.unknown", **bp})
    corrections  = _count(supabase_url, supabase_key, "transactions",
                          {"classification_override": "eq.true", **bp})

    stale   = _count(supabase_url, supabase_key, "filings",
                     {"status": "eq.in_progress"})
    fail_7d = _count(supabase_url, supabase_key, "filings",
                     {"status": "eq.failed", "updated_at": f"gte.{ago_7d}"})

    # Confidence sample — up to 1 000 recent rows; enough for meaningful stats.
    conf_rows = _select(
        supabase_url, supabase_key, "transactions",
        "extraction_confidence,classification_confidence",
        {"created_at": f"gte.{since}", "limit": "1000"},
    )
    ext_vals = [r["extraction_confidence"]     for r in conf_rows
                if r.get("extraction_confidence")     is not None]
    cls_vals = [r["classification_confidence"] for r in conf_rows
                if r.get("classification_confidence") is not None]

    def _pct(n: int, d: int) -> Optional[float]:
        return round(100 * n / d, 2) if d else None

    return {
        "window_days":               window_days,
        "total_transactions":        total,
        "needs_review_count":        needs_review,
        "review_rate_pct":           _pct(needs_review, total),
        "unknown_direction_count":   unknown_dir,
        "unknown_direction_pct":     _pct(unknown_dir, total),
        "unknown_type_count":        unknown_type,
        "unknown_type_pct":          _pct(unknown_type, total),
        "correction_count":          corrections,
        "correction_rate_pct":       _pct(corrections, total),
        "confidence": {
            "extraction":     numeric_stats(ext_vals),
            "classification": numeric_stats(cls_vals),
        },
        "stale_in_progress_count":   stale,
        "failed_filings_7d":         fail_7d,
    }


def check_alert_thresholds(
    quality: dict,
    latency_stage_stats: dict,
    *,
    thresholds: dict = None,
) -> list:
    """
    Return a list of alert dicts for any metric that exceeds its threshold.

    Each alert:
        {"metric": str, "value": float|int, "threshold": float|int, "message": str}

    Pass custom thresholds to override the module-level ALERT_THRESHOLDS.
    """
    t = thresholds or ALERT_THRESHOLDS
    alerts = []

    scalar_checks = [
        ("review_rate_pct",        quality.get("review_rate_pct"),        t["review_rate_pct"],        "% review rate (last {w}d)"),
        ("unknown_direction_pct",  quality.get("unknown_direction_pct"),   t["unknown_direction_pct"],  "% unknown direction"),
        ("stale_in_progress_count", quality.get("stale_in_progress_count"), t["stale_in_progress_count"], "stale in_progress filings"),
        ("failed_filings_7d",      quality.get("failed_filings_7d"),       t["failed_filings_7d"],       "filing failures in 7 days"),
    ]

    e2e_p95 = latency_stage_stats.get("end_to_end", {}).get("p95")
    if e2e_p95 is not None:
        scalar_checks.append((
            "p95_end_to_end_seconds", e2e_p95,
            t["p95_end_to_end_seconds"], "s P95 end-to-end latency",
        ))

    w = quality.get("window_days", _DEFAULT_WINDOW_DAYS)
    for metric, value, threshold, unit_tmpl in scalar_checks:
        if value is None:
            continue
        if value > threshold:
            unit = unit_tmpl.format(w=w)
            alerts.append({
                "metric":    metric,
                "value":     value,
                "threshold": threshold,
                "message":   f"ALERT {metric}: {value} {unit} (threshold: {threshold})",
            })

    return alerts
