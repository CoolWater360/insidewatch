"""
Daily review queue summary — Phase 8.

Queries the three review queues (pending transactions, failed filings,
unmatched issuers) and sends a concise HTML digest via Resend.

Run via GitHub Actions at the end of each trading day.

Required env vars:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY  (service role needed to read all tables)
  RESEND_API_KEY
  ALERT_EMAIL
  ALERT_FROM_EMAIL
"""

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RESEND_API = "https://api.resend.com/emails"


# ─── Supabase REST query helper ───────────────────────────────────────────────

def _supabase_select(
    url: str, key: str, table: str, params: dict, columns: str = "id"
) -> list[dict]:
    endpoint = f"{url}/rest/v1/{table}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "count=exact",
    }
    params["select"] = columns
    r = httpx.get(endpoint, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json() if isinstance(r.json(), list) else []


def _supabase_count(url: str, key: str, table: str, params: dict) -> int:
    endpoint = f"{url}/rest/v1/{table}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Prefer": "count=exact",
    }
    params["select"] = "id"
    params["limit"] = "0"
    r = httpx.get(endpoint, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    content_range = r.headers.get("content-range", "*/0")
    try:
        return int(content_range.split("/")[-1])
    except (ValueError, IndexError):
        return 0


# ─── Queue queries ────────────────────────────────────────────────────────────

def _get_counts(url: str, key: str) -> dict:
    pending_tx = _supabase_count(
        url, key, "transactions",
        {
            "needs_review": "eq.true",
            "or": "(review_status.is.null,review_status.in.(pending_review,under_review))",
        },
    )
    failed_filings = _supabase_count(
        url, key, "filings",
        {"status": "eq.failed"},
    )
    skipped_filings = _supabase_count(
        url, key, "filings",
        {"status": "eq.skipped"},
    )
    pending_issuers = _supabase_count(
        url, key, "unmatched_issuers",
        {"status": "eq.pending"},
    )
    return {
        "pending_transactions": pending_tx,
        "failed_filings": failed_filings,
        "skipped_filings": skipped_filings,
        "pending_issuers": pending_issuers,
    }


def _get_sample_transactions(url: str, key: str, limit: int = 5) -> list[dict]:
    return _supabase_select(
        url, key, "transactions",
        {
            "needs_review": "eq.true",
            "or": "(review_status.is.null,review_status.in.(pending_review,under_review))",
            "order": "extraction_confidence.asc.nullsfirst",
            "limit": str(limit),
        },
        columns="id,transaction_date,direction,transaction_type,extraction_confidence,review_reason",
    )


def _get_sample_filings(url: str, key: str, limit: int = 5) -> list[dict]:
    return _supabase_select(
        url, key, "filings",
        {
            "status": "in.(failed,skipped)",
            "order": "last_attempted_at.desc.nullsfirst",
            "limit": str(limit),
        },
        columns="id,company_name,filing_date,status,attempt_count,last_error",
    )


# ─── Email rendering ──────────────────────────────────────────────────────────

def _render_html(counts: dict, sample_tx: list[dict], sample_filings: list[dict]) -> str:
    total = counts["pending_transactions"] + counts["failed_filings"] + counts["skipped_filings"] + counts["pending_issuers"]
    date_str = datetime.now(timezone.utc).strftime("%d %b %Y")

    status_color = "#F59E0B" if total > 0 else "#10B981"
    status_text = f"{total} item{'s' if total != 1 else ''} need attention" if total > 0 else "All queues clear"

    tx_rows = ""
    for tx in sample_tx:
        conf = f"{tx.get('extraction_confidence', 0) * 100:.0f}%" if tx.get("extraction_confidence") is not None else "—"
        tx_rows += (
            f"<tr>"
            f"<td style='padding:6px 8px;color:#9CA3AF'>{tx.get('transaction_date','—')}</td>"
            f"<td style='padding:6px 8px'>{tx.get('transaction_type','—')}</td>"
            f"<td style='padding:6px 8px;color:#F59E0B'>{conf}</td>"
            f"<td style='padding:6px 8px;color:#9CA3AF;font-size:11px'>{(tx.get('review_reason') or '—')[:60]}</td>"
            f"</tr>"
        )

    filing_rows = ""
    for f in sample_filings:
        err = (f.get("last_error") or "—")[:80]
        filing_rows += (
            f"<tr>"
            f"<td style='padding:6px 8px'>{f.get('company_name','—')}</td>"
            f"<td style='padding:6px 8px;color:#9CA3AF'>{f.get('filing_date','—')}</td>"
            f"<td style='padding:6px 8px'>{f.get('status','—')}</td>"
            f"<td style='padding:6px 8px;color:#9CA3AF;font-size:11px'>{err}</td>"
            f"</tr>"
        )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:system-ui,sans-serif;background:#0B1120;color:#E8EDF7;max-width:680px;margin:0 auto;padding:24px">
  <div style="border-bottom:1px solid #1E2D45;padding-bottom:16px;margin-bottom:24px">
    <span style="font-size:13px;font-weight:600;color:{status_color};text-transform:uppercase;letter-spacing:.08em">
      InsideWatch — Review Queue — {date_str}
    </span>
    <p style="margin:8px 0 0;color:{status_color};font-size:18px;font-weight:700">{status_text}</p>
  </div>

  <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
    <tr>
      <td style="padding:16px;background:#111827;border:1px solid #1E2D45;border-radius:6px;text-align:center">
        <div style="font-size:28px;font-weight:700;color:#F59E0B">{counts['pending_transactions']}</div>
        <div style="font-size:12px;color:#9CA3AF;margin-top:4px">Transactions pending review</div>
      </td>
      <td style="width:8px"></td>
      <td style="padding:16px;background:#111827;border:1px solid #1E2D45;border-radius:6px;text-align:center">
        <div style="font-size:28px;font-weight:700;color:#EF4444">{counts['failed_filings']}</div>
        <div style="font-size:12px;color:#9CA3AF;margin-top:4px">Failed filings (retryable)</div>
      </td>
      <td style="width:8px"></td>
      <td style="padding:16px;background:#111827;border:1px solid #1E2D45;border-radius:6px;text-align:center">
        <div style="font-size:28px;font-weight:700;color:#6B7280">{counts['skipped_filings']}</div>
        <div style="font-size:12px;color:#9CA3AF;margin-top:4px">Skipped filings (max retries)</div>
      </td>
      <td style="width:8px"></td>
      <td style="padding:16px;background:#111827;border:1px solid #1E2D45;border-radius:6px;text-align:center">
        <div style="font-size:28px;font-weight:700;color:#E8EDF7">{counts['pending_issuers']}</div>
        <div style="font-size:12px;color:#9CA3AF;margin-top:4px">Unmatched issuers</div>
      </td>
    </tr>
  </table>

  {f'''<div style="margin-bottom:24px">
    <p style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:#9CA3AF;margin:0 0 8px">Lowest-confidence transactions (sample)</p>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead>
        <tr style="border-bottom:1px solid #1E2D45">
          <th style="padding:6px 8px;text-align:left;color:#6B7280;font-weight:500">Date</th>
          <th style="padding:6px 8px;text-align:left;color:#6B7280;font-weight:500">Type</th>
          <th style="padding:6px 8px;text-align:left;color:#6B7280;font-weight:500">Conf</th>
          <th style="padding:6px 8px;text-align:left;color:#6B7280;font-weight:500">Reason</th>
        </tr>
      </thead>
      <tbody>{tx_rows}</tbody>
    </table>
  </div>''' if sample_tx else ''}

  {f'''<div style="margin-bottom:24px">
    <p style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:#9CA3AF;margin:0 0 8px">Recent failed &amp; skipped filings (sample)</p>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead>
        <tr style="border-bottom:1px solid #1E2D45">
          <th style="padding:6px 8px;text-align:left;color:#6B7280;font-weight:500">Company</th>
          <th style="padding:6px 8px;text-align:left;color:#6B7280;font-weight:500">Date</th>
          <th style="padding:6px 8px;text-align:left;color:#6B7280;font-weight:500">Status</th>
          <th style="padding:6px 8px;text-align:left;color:#6B7280;font-weight:500">Error</th>
        </tr>
      </thead>
      <tbody>{filing_rows}</tbody>
    </table>
  </div>''' if sample_filings else ''}

  <p style="font-size:11px;color:#4B5563;border-top:1px solid #1E2D45;padding-top:16px;margin-top:8px">
    InsideWatch internal digest · {date_str} · auto-generated
  </p>
</body>
</html>"""


# ─── Send ─────────────────────────────────────────────────────────────────────

def _send_email(subject: str, html: str) -> None:
    api_key = os.getenv("RESEND_API_KEY", "")
    to_addr = os.getenv("ALERT_EMAIL", "")
    from_addr = os.getenv("ALERT_FROM_EMAIL", "InsideWatch <noreply@example.com>")

    if not api_key or not to_addr:
        logger.warning("RESEND_API_KEY or ALERT_EMAIL not set — skipping email.")
        return

    recipients = [a.strip() for a in to_addr.split(",") if a.strip()]
    payload = {
        "from": from_addr,
        "to": recipients,
        "subject": subject,
        "html": html,
    }
    r = httpx.post(
        RESEND_API,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    if r.status_code >= 400:
        logger.error("Resend error %d: %s", r.status_code, r.text[:200])
        r.raise_for_status()
    logger.info("Review summary sent to %s (status %d)", recipients, r.status_code)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
        sys.exit(1)

    logger.info("Querying review queues…")
    counts = _get_counts(url, key)
    logger.info("Counts: %s", counts)

    total = sum(counts.values())

    sample_tx: list[dict] = []
    sample_filings: list[dict] = []

    if counts["pending_transactions"] > 0:
        sample_tx = _get_sample_transactions(url, key, limit=5)
    if counts["failed_filings"] > 0 or counts["skipped_filings"] > 0:
        sample_filings = _get_sample_filings(url, key, limit=5)

    date_str = datetime.now(timezone.utc).strftime("%d %b %Y")
    subject = (
        f"InsideWatch review queue — {total} item{'s' if total != 1 else ''} — {date_str}"
        if total > 0
        else f"InsideWatch review queue — clear — {date_str}"
    )

    html = _render_html(counts, sample_tx, sample_filings)
    _send_email(subject, html)
    logger.info("Done.")


if __name__ == "__main__":
    main()
