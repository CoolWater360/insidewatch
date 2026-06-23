"""
Daily operations report — Phase 9.

Queries the database for a structured summary of scraper health, filing pipeline
throughput, and review queue depth.  Outputs JSON to stdout and optionally
emails the report via Resend.

Usage:
    python3 -m scraper.generate_daily_operations_report           # JSON stdout
    python3 -m scraper.generate_daily_operations_report --email   # + email

Required env vars:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
Optional:
    RESEND_API_KEY       — needed for --email
    ALERT_EMAIL          — needed for --email
    ALERT_FROM_EMAIL     — needed for --email
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RESEND_API = "https://api.resend.com/emails"


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


# ─── Latency calculation ──────────────────────────────────────────────────────

def _latency_seconds(row: dict, start: str, end: str) -> Optional[float]:
    s = row.get(start)
    e = row.get(end)
    if not s or not e:
        return None
    try:
        def _parse(ts: str) -> datetime:
            ts = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        return (_parse(e) - _parse(s)).total_seconds()
    except (ValueError, TypeError):
        return None


def _percentile(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    idx = (len(s) - 1) * p / 100
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (idx - lo), 1)


def _latency_stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "avg": None, "p50": None, "p95": None, "min": None, "max": None}
    return {
        "n":   len(values),
        "avg": round(sum(values) / len(values), 1),
        "p50": _percentile(values, 50),
        "p95": _percentile(values, 95),
        "min": round(min(values), 1),
        "max": round(max(values), 1),
    }


# ─── Report builder ───────────────────────────────────────────────────────────

def build_report(supabase_url: str, supabase_key: str) -> dict:
    now_utc = datetime.now(timezone.utc)
    ago_24h = (now_utc - timedelta(hours=24)).isoformat()
    ago_7d  = (now_utc - timedelta(days=7)).isoformat()

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

    # ── Latency metrics (last 7 days of completed filings) ────────────────────
    completed_rows = _select(
        supabase_url, supabase_key, "filings",
        "discovered_utc,downloaded_utc,parsed_utc,delivered_utc,source_published_utc",
        {"status": "eq.completed", "delivered_utc": f"gte.{ago_7d}", "limit": "500"},
    )

    e2e_vals, scrape_vals, dl_vals, parse_vals = [], [], [], []
    for row in completed_rows:
        e2e = _latency_seconds(row, "source_published_utc", "delivered_utc")
        scr = _latency_seconds(row, "discovered_utc", "delivered_utc")
        dl  = _latency_seconds(row, "discovered_utc", "downloaded_utc")
        pa  = _latency_seconds(row, "downloaded_utc", "parsed_utc")
        if e2e and e2e > 0:
            e2e_vals.append(e2e)
        if scr and scr > 0:
            scrape_vals.append(scr)
        if dl and dl > 0:
            dl_vals.append(dl)
        if pa and pa > 0:
            parse_vals.append(pa)

    latency = {
        "end_to_end_seconds":  _latency_stats(e2e_vals),
        "scraper_seconds":     _latency_stats(scrape_vals),
        "download_seconds":    _latency_stats(dl_vals),
        "parse_seconds":       _latency_stats(parse_vals),
    }

    # ── Transactions ─────────────────────────────────────────────────────────
    tx_24h = _count(supabase_url, supabase_key, "transactions",
                    {"created_at": f"gte.{ago_24h}"})
    tx_7d  = _count(supabase_url, supabase_key, "transactions",
                    {"created_at": f"gte.{ago_7d}"})

    # ── Review queue ──────────────────────────────────────────────────────────
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

    return {
        "generated_at": now_utc.isoformat(),
        "window": {"last_24h": ago_24h, "last_7d": ago_7d},
        "filings": filing_counts,
        "latency_last_7d": latency,
        "transactions": {
            "inserted_last_24h": tx_24h,
            "inserted_last_7d":  tx_7d,
        },
        "review_queue": {
            "transactions_pending_review": review_pending,
            "unmatched_issuers_pending":   unmatched_pending,
        },
    }


# ─── Email sender ─────────────────────────────────────────────────────────────

def _send_report_email(report: dict) -> bool:
    api_key   = os.getenv("RESEND_API_KEY", "")
    raw_to    = os.getenv("ALERT_EMAIL", "")
    from_addr = os.getenv("ALERT_FROM_EMAIL", "InsideWatch <onboarding@resend.dev>")

    if not api_key or not raw_to:
        logger.debug("Email env vars not set — skipping email")
        return False

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = report["filings"]
    rx = report["review_queue"]
    tx = report["transactions"]
    lat = report["latency_last_7d"]["scraper_seconds"]

    def _latrow(label: str, stats: dict) -> str:
        if stats["n"] == 0:
            return f"<tr><td>{label}</td><td colspan='4'>no data</td></tr>"
        return (
            f"<tr><td>{label}</td>"
            f"<td>{stats['avg']}s</td><td>{stats['p50']}s</td>"
            f"<td>{stats['p95']}s</td><td>{stats['n']}</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:monospace;background:#0F1623;color:#E8EDF7;padding:24px">
<h2 style="color:#60A5FA">InsideWatch — Daily Operations Report {today}</h2>
<h3>Filing Pipeline (last 24h / all-time)</h3>
<table border="1" style="border-collapse:collapse;width:100%;font-size:13px">
<tr><th>Status</th><th>Last 24h</th><th>Last 7d</th><th>All-time</th></tr>
{''.join(f"<tr><td>{s}</td><td>{f[s]['last_24h']}</td><td>{f[s]['last_7d']}</td><td>{f[s]['all_time']}</td></tr>" for s in f)}
</table>
<h3>Latency (last 7 days, scraper pipeline seconds)</h3>
<table border="1" style="border-collapse:collapse;width:100%;font-size:13px">
<tr><th>Stage</th><th>avg</th><th>p50</th><th>p95</th><th>n</th></tr>
{_latrow("end-to-end", report["latency_last_7d"]["end_to_end_seconds"])}
{_latrow("scraper total", report["latency_last_7d"]["scraper_seconds"])}
{_latrow("download", report["latency_last_7d"]["download_seconds"])}
{_latrow("parse", report["latency_last_7d"]["parse_seconds"])}
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
                "subject": f"InsideWatch Ops Report {today}",
                "html": html,
            },
            timeout=10.0,
        )
        if resp.status_code in (200, 201):
            logger.info("Ops report email sent")
            return True
        logger.warning("Resend %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Ops report email failed: %s", exc)
    return False


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Generate daily operations report.")
    parser.add_argument("--email", action="store_true", help="Send report via email")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)

    try:
        report = build_report(url, key)
    except Exception as exc:
        logger.error("Failed to build report: %s", exc)
        sys.exit(1)

    indent = 2 if args.pretty else None
    print(json.dumps(report, indent=indent, ensure_ascii=False))

    if args.email:
        _send_report_email(report)


if __name__ == "__main__":
    main()
