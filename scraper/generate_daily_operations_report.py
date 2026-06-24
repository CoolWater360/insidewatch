"""
Daily operations report — Phase 11.

Queries the database for a structured summary of scraper health, filing
pipeline throughput, latency across all five pipeline stages, and quality
metrics.  Outputs JSON to stdout and optionally emails the report.

Usage:
    python3 -m scraper.generate_daily_operations_report           # JSON stdout
    python3 -m scraper.generate_daily_operations_report --email   # + email
    python3 -m scraper.generate_daily_operations_report --pretty  # pretty JSON
    python3 -m scraper.generate_daily_operations_report \\
        --since 2025-01-01 --until 2025-01-31                    # replay window

Required env vars:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
Optional:
    RESEND_API_KEY       — needed for --email
    ALERT_EMAIL          — needed for --email
    ALERT_FROM_EMAIL     — needed for --email

Pipeline stages reported (seconds):
    publication_to_detection   source_published_utc → discovered_utc
    detection_to_download      discovered_utc → downloaded_utc
    download_to_stored         downloaded_utc → stored_utc       (post-014 only)
    stored_to_parsed           stored_utc → parsed_utc           (post-014 only)
    parsed_to_delivered        parsed_utc → delivered_utc
    end_to_end                 source_published_utc → delivered_utc
    scraper_total              discovered_utc → delivered_utc
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from .latency import LATENCY_COLUMNS, aggregate_stage_stats, coverage_report
from .quality import check_alert_thresholds, compute_quality_metrics

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RESEND_API = "https://api.resend.com/emails"

# Stage labels for the email report (ordered for display).
_STAGE_LABELS = [
    ("publication_to_detection", "Publication → Detection"),
    ("detection_to_download",    "Detection → Download"),
    ("download_to_stored",       "Download → Stored"),
    ("stored_to_parsed",         "Stored → Parsed"),
    ("parsed_to_delivered",      "Parsed → Delivered"),
    ("end_to_end",               "End-to-End (wall time)"),
    ("scraper_total",            "Scraper Total"),
]


# ─── Supabase REST helpers ────────────────────────────────────────────────────

def _headers(key: str) -> dict:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "count=exact",
    }


def _count(url: str, key: str, table: str, params: dict) -> int:
    endpoint = f"{url}/rest/v1/{table}"
    params = {"select": "id", "limit": "0", **params}
    r = httpx.get(endpoint, headers=_headers(key), params=params, timeout=15)
    r.raise_for_status()
    try:
        return int(r.headers.get("content-range", "*/0").split("/")[-1])
    except (ValueError, IndexError):
        return 0


def _select(url: str, key: str, table: str, columns: str, params: dict) -> list:
    endpoint = f"{url}/rest/v1/{table}"
    params = {"select": columns, **params}
    r = httpx.get(endpoint, headers=_headers(key), params=params, timeout=20)
    r.raise_for_status()
    return r.json() if isinstance(r.json(), list) else []


# ─── Report builder ───────────────────────────────────────────────────────────

def build_report(
    supabase_url: str,
    supabase_key: str,
    *,
    since_override: Optional[str] = None,
    until_override: Optional[str] = None,
) -> dict:
    """
    Build the full operations report dict.

    ``since_override`` / ``until_override`` allow replay of historical windows
    (ISO 8601 date strings, e.g. "2025-01-01").  Both default to the standard
    rolling windows (last 24h / last 7d from now).
    """
    now_utc = datetime.now(timezone.utc)
    ago_24h = (now_utc - timedelta(hours=24)).isoformat()
    ago_7d  = (now_utc - timedelta(days=7)).isoformat()

    latency_since = since_override or ago_7d
    latency_until = until_override  # None = no upper bound

    # ── Filing pipeline counts ────────────────────────────────────────────────
    statuses = ["completed", "failed", "skipped", "pending", "in_progress"]
    filing_counts: dict = {}
    for status in statuses:
        filing_counts[status] = {
            "last_24h": _count(supabase_url, supabase_key, "filings",
                               {"status": f"eq.{status}", "updated_at": f"gte.{ago_24h}"}),
            "last_7d":  _count(supabase_url, supabase_key, "filings",
                               {"status": f"eq.{status}", "updated_at": f"gte.{ago_7d}"}),
            "all_time": _count(supabase_url, supabase_key, "filings",
                               {"status": f"eq.{status}"}),
        }

    # ── Latency metrics ───────────────────────────────────────────────────────
    latency_params: dict = {
        "status":        "eq.completed",
        "delivered_utc": f"gte.{latency_since}",
        "limit":         "500",
    }
    if latency_until:
        latency_params["delivered_utc"] = f"gte.{latency_since}"
        latency_params["and"] = f"(delivered_utc.lte.{latency_until})"

    completed_rows = _select(
        supabase_url, supabase_key, "filings",
        LATENCY_COLUMNS,
        latency_params,
    )

    latency = {
        "window": {
            "since": latency_since,
            "until": latency_until or now_utc.isoformat(),
            "n_filings": len(completed_rows),
        },
        "stages": aggregate_stage_stats(completed_rows),
        "coverage": coverage_report(completed_rows),
    }

    # ── Transactions ─────────────────────────────────────────────────────────
    tx_24h = _count(supabase_url, supabase_key, "transactions",
                    {"created_at": f"gte.{ago_24h}"})
    tx_7d  = _count(supabase_url, supabase_key, "transactions",
                    {"created_at": f"gte.{ago_7d}"})

    # ── Review queue depth ────────────────────────────────────────────────────
    review_pending = _count(
        supabase_url, supabase_key, "transactions",
        {
            "needs_review": "eq.true",
            "or": "(review_status.is.null,review_status.in.(pending_review,under_review))",
        },
    )
    unmatched_pending = _count(
        supabase_url, supabase_key, "unmatched_issuers",
        {"status": "eq.pending"},
    )

    # ── Quality metrics ───────────────────────────────────────────────────────
    quality = compute_quality_metrics(supabase_url, supabase_key, window_days=30)

    # ── Alert thresholds ──────────────────────────────────────────────────────
    alerts = check_alert_thresholds(quality, latency["stages"])

    return {
        "generated_at": now_utc.isoformat(),
        "window": {"last_24h": ago_24h, "last_7d": ago_7d},
        "filings": filing_counts,
        "latency": latency,
        # Legacy key retained for backwards compatibility with downstream consumers.
        "latency_last_7d": {
            "end_to_end_seconds": latency["stages"]["end_to_end"],
            "scraper_seconds":    latency["stages"]["scraper_total"],
            "download_seconds":   latency["stages"]["detection_to_download"],
            "parse_seconds":      latency["stages"]["stored_to_parsed"],
        },
        "transactions": {
            "inserted_last_24h": tx_24h,
            "inserted_last_7d":  tx_7d,
        },
        "review_queue": {
            "transactions_pending_review": review_pending,
            "unmatched_issuers_pending":   unmatched_pending,
        },
        "quality": quality,
        "alerts":  alerts,
    }


# ─── Email sender ─────────────────────────────────────────────────────────────

def _send_report_email(report: dict) -> bool:
    api_key   = os.getenv("RESEND_API_KEY", "")
    raw_to    = os.getenv("ALERT_EMAIL", "")
    from_addr = os.getenv("ALERT_FROM_EMAIL", "InsideWatch <onboarding@resend.dev>")

    if not api_key or not raw_to:
        logger.debug("Email env vars not set — skipping email")
        return False

    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f      = report["filings"]
    rx     = report["review_queue"]
    tx     = report["transactions"]
    stages = report["latency"]["stages"]
    q      = report["quality"]
    alerts = report.get("alerts", [])

    def _latrow(key: str, label: str) -> str:
        s = stages.get(key, {})
        if s.get("n", 0) == 0:
            return f"<tr><td>{label}</td><td colspan='4' style='color:#6B7280'>no data</td></tr>"
        return (
            f"<tr><td>{label}</td>"
            f"<td>{s['avg']}s</td><td>{s['p50']}s</td>"
            f"<td>{s['p95']}s</td><td>{s['n']}</td></tr>"
        )

    alert_html = ""
    if alerts:
        rows = "".join(f"<li>{a['message']}</li>" for a in alerts)
        alert_html = (
            f"<h3 style='color:#EF4444'>⚠ Alerts ({len(alerts)})</h3>"
            f"<ul style='color:#FCA5A5'>{rows}</ul>"
        )

    def _qrow(label: str, value) -> str:
        if value is None:
            return f"<tr><td>{label}</td><td>—</td></tr>"
        return f"<tr><td>{label}</td><td>{value}</td></tr>"

    conf_ext = q["confidence"]["extraction"]
    conf_cls = q["confidence"]["classification"]

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:monospace;background:#0F1623;color:#E8EDF7;padding:24px">
<h2 style="color:#60A5FA">InsideWatch — Daily Operations Report {today}</h2>
{alert_html}
<h3>Filing Pipeline (24h / 7d / all-time)</h3>
<table border="1" style="border-collapse:collapse;width:100%;font-size:13px">
<tr><th>Status</th><th>Last 24h</th><th>Last 7d</th><th>All-time</th></tr>
{''.join(f"<tr><td>{s}</td><td>{f[s]['last_24h']}</td><td>{f[s]['last_7d']}</td><td>{f[s]['all_time']}</td></tr>" for s in f)}
</table>
<h3>Latency — All Pipeline Stages (last 7d, seconds)</h3>
<p style="font-size:11px;color:#9CA3AF">n = number of completed filings with both timestamps present.
Stages marked "no data" lack timestamps (historical or unavailable).</p>
<table border="1" style="border-collapse:collapse;width:100%;font-size:13px">
<tr><th>Stage</th><th>avg</th><th>p50</th><th>p95</th><th>n</th></tr>
{_latrow("publication_to_detection", "Publication → Detection")}
{_latrow("detection_to_download",    "Detection → Download")}
{_latrow("download_to_stored",       "Download → Stored")}
{_latrow("stored_to_parsed",         "Stored → Parsed")}
{_latrow("parsed_to_delivered",      "Parsed → Delivered")}
{_latrow("end_to_end",               "End-to-End (wall time)")}
{_latrow("scraper_total",            "Scraper Total")}
</table>
<h3>Quality (last 30 days, {q['total_transactions']} transactions)</h3>
<table border="1" style="border-collapse:collapse;width:100%;font-size:13px">
{_qrow("Review rate", f"{q['review_rate_pct']} %" if q['review_rate_pct'] is not None else None)}
{_qrow("Unknown direction", f"{q['unknown_direction_pct']} % ({q['unknown_direction_count']})")}
{_qrow("Unknown type", f"{q['unknown_type_pct']} % ({q['unknown_type_count']})")}
{_qrow("Correction rate", f"{q['correction_rate_pct']} % ({q['correction_count']})")}
{_qrow("Extraction confidence avg/p50/p95",
       f"{conf_ext['avg']}/{conf_ext['p50']}/{conf_ext['p95']} (n={conf_ext['n']})"
       if conf_ext['n'] else None)}
{_qrow("Classification confidence avg/p50/p95",
       f"{conf_cls['avg']}/{conf_cls['p50']}/{conf_cls['p95']} (n={conf_cls['n']})"
       if conf_cls['n'] else None)}
{_qrow("Stale in_progress filings", q['stale_in_progress_count'])}
{_qrow("Filing failures (7d)", q['failed_filings_7d'])}
</table>
<h3>Review Queue</h3>
<p>Transactions pending review: <b>{rx['transactions_pending_review']}</b><br>
Unmatched issuers pending: <b>{rx['unmatched_issuers_pending']}</b></p>
<h3>Transactions inserted</h3>
<p>Last 24h: <b>{tx['inserted_last_24h']}</b> &nbsp; Last 7d: <b>{tx['inserted_last_7d']}</b></p>
</body></html>"""

    recipients = [r.strip() for r in raw_to.split(",") if r.strip()]
    try:
        resp = httpx.post(
            RESEND_API,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": from_addr,
                "to": recipients,
                "subject": f"InsideWatch Ops Report {today}"
                           + (f" — {len(alerts)} ALERT(S)" if alerts else ""),
                "html": html,
            },
            timeout=10.0,
        )
        if resp.status_code in (200, 201):
            logger.info("Ops report email sent (alerts: %d)", len(alerts))
            return True
        logger.warning("Resend %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Ops report email failed: %s", exc)
    return False


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Generate daily operations report.")
    parser.add_argument("--email",  action="store_true", help="Send report via email")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--since",  metavar="YYYY-MM-DD",
                        help="Override latency window start (replay historical data)")
    parser.add_argument("--until",  metavar="YYYY-MM-DD",
                        help="Override latency window end (replay historical data)")
    args = parser.parse_args()

    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)

    try:
        report = build_report(url, key, since_override=args.since, until_override=args.until)
    except Exception as exc:
        logger.error("Failed to build report: %s", exc)
        sys.exit(1)

    indent = 2 if args.pretty else None
    print(json.dumps(report, indent=indent, ensure_ascii=False))

    if args.email:
        _send_report_email(report)


if __name__ == "__main__":
    main()
