#!/usr/bin/env python3
"""
Health check for the InsideWatch scraper pipeline.

Exit codes:
    0  HEALTHY    — all checks passed
    1  DEGRADED   — non-critical issues detected (e.g. stale scraper)
    2  CRITICAL   — serious issues (DB unreachable, pipeline stalled)

Usage:
    python3 -m scraper.run_health_check
    python3 -m scraper.run_health_check --json   # JSON output

Required env vars:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ─── Supabase REST helpers ────────────────────────────────────────────────────

def _get(url: str, key: str, table: str, columns: str, params: dict) -> list:
    endpoint = f"{url}/rest/v1/{table}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    r = httpx.get(endpoint, headers=headers,
                  params={"select": columns, **params}, timeout=10)
    r.raise_for_status()
    return r.json() if isinstance(r.json(), list) else []


# ─── Individual checks ────────────────────────────────────────────────────────

def check_db(url: str, key: str) -> dict:
    """Verify that the DB is reachable and the filings table exists."""
    try:
        _get(url, key, "filings", "id", {"limit": "1"})
        return {"ok": True, "message": "DB reachable"}
    except Exception as exc:
        return {"ok": False, "message": f"DB unreachable: {exc}"}


def check_recent_activity(url: str, key: str, max_hours: int = 25) -> dict:
    """
    Check that at least one filing was completed within the last max_hours.
    Allows 25 hours (not 24) to tolerate GitHub Actions scheduling jitter.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=max_hours)).isoformat()
    try:
        rows = _get(url, key, "filings", "id,delivered_utc,completed_at",
                    {"status": "eq.completed", "updated_at": f"gte.{since}", "limit": "1"})
        if rows:
            return {"ok": True, "message": f"1+ filing completed in last {max_hours}h"}
        # Fall back: check scraper_runs table
        runs = _get(url, key, "scraper_runs", "last_successful_run",
                    {"last_successful_run": f"gte.{since}", "limit": "1"})
        if runs:
            return {"ok": True, "message": f"scraper_runs updated in last {max_hours}h (no new filings is OK)"}
        return {
            "ok": False,
            "message": f"No completed filings or scraper_runs update in last {max_hours}h",
        }
    except Exception as exc:
        return {"ok": False, "message": f"Activity check failed: {exc}"}


def check_failed_filings(url: str, key: str, threshold: int = 20) -> dict:
    """Flag if there are too many permanently failed filings."""
    try:
        rows = _get(url, key, "filings", "id", {"status": "eq.skipped", "limit": str(threshold + 1)})
        n = len(rows)
        if n > threshold:
            return {"ok": False, "message": f"{n}+ permanently skipped filings (threshold: {threshold})"}
        return {"ok": True, "message": f"{n} permanently skipped filings (below threshold {threshold})"}
    except Exception as exc:
        return {"ok": False, "message": f"Failed filings check failed: {exc}"}


def check_review_queue(url: str, key: str, threshold: int = 200) -> dict:
    """Flag if the review queue has grown too large to manage."""
    try:
        rows = _get(url, key, "transactions", "id",
                    {
                        "needs_review": "eq.true",
                        "or": "(review_status.is.null,review_status.in.(pending_review,under_review))",
                        "limit": str(threshold + 1),
                    })
        n = len(rows)
        if n > threshold:
            return {"ok": False, "message": f"{n}+ transactions need review (threshold: {threshold})"}
        return {"ok": True, "message": f"{n} transactions need review"}
    except Exception as exc:
        return {"ok": False, "message": f"Review queue check failed: {exc}"}


# ─── Runner ───────────────────────────────────────────────────────────────────

def run_health_check(supabase_url: str, supabase_key: str) -> dict:
    checks = {
        "db":              check_db(supabase_url, supabase_key),
        "recent_activity": check_recent_activity(supabase_url, supabase_key),
        "failed_filings":  check_failed_filings(supabase_url, supabase_key),
        "review_queue":    check_review_queue(supabase_url, supabase_key),
    }

    all_ok      = all(c["ok"] for c in checks.values())
    db_ok       = checks["db"]["ok"]
    critical_ok = db_ok and checks["recent_activity"]["ok"]

    if all_ok:
        overall = "healthy"
        exit_code = 0
    elif critical_ok:
        overall = "degraded"
        exit_code = 1
    else:
        overall = "critical"
        exit_code = 2

    return {
        "status":       overall,
        "exit_code":    exit_code,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks":       checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Health check for the scraper pipeline.")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output JSON instead of plain text")
    args = parser.parse_args()

    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(2)

    result = run_health_check(url, key)

    if args.json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Status: {result['status'].upper()}")
        for name, check in result["checks"].items():
            icon = "✓" if check["ok"] else "✗"
            print(f"  {icon} {name}: {check['message']}")

    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
