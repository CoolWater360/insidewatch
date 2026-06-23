"""
Alert dispatcher for InsideWatch.

Single entry point: dispatch(payload, client, company_tier)
Currently sends email via Resend.  Telegram seam is stubbed — add it
by uncommenting the import and call at the bottom of dispatch().

Required env vars:
  RESEND_API_KEY       — Resend secret key
  ALERT_EMAIL          — comma-separated recipient addresses
  ALERT_FROM_EMAIL     — verified sender (e.g. "InsideWatch <alerts@yourdomain.com>")
  TELEGRAM_BOT_TOKEN   — Telegram bot token from @BotFather
  TELEGRAM_CHANNEL_ID  — Channel username or numeric chat ID
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

RESEND_API = "https://api.resend.com/emails"


# ─── Payload ──────────────────────────────────────────────────────────────────

@dataclass
class AlertPayload:
    company_name: str
    company_id: int
    insider_name: str
    insider_role: str
    direction: str          # buy | sell
    transaction_type: str   # buy | sell | grant | option_exercise | sell_to_cover | other
    quantity: float
    unit_price: float
    total_value: float
    currency: str
    transaction_date: str   # YYYY-MM-DD
    filed_date: str
    source_url: str
    transaction_id: int


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_value(v: float, currency: str = "EUR") -> str:
    """Italian-locale EUR formatting: 1.234.567 EUR"""
    if v == 0:
        return "—"
    return f"{v:,.0f} {currency}".replace(",", ".")


def _fmt_date(iso: str) -> str:
    """YYYY-MM-DD → GG/MM/AAAA"""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return iso


# ─── Cluster detection ────────────────────────────────────────────────────────

def check_cluster(client, company_id: int, tx_date: str) -> Optional[dict]:
    """
    Return cluster info if 2+ distinct verified insiders bought from the same
    company within the 7 days ending on tx_date.  None if no cluster.
    """
    since = (
        datetime.fromisoformat(tx_date) - timedelta(days=7)
    ).strftime("%Y-%m-%d")

    try:
        result = (
            client.table("transactions")
            .select("insider_id, total_value, insiders(insider_verified)")
            .eq("company_id", company_id)
            .eq("direction", "buy")
            .eq("needs_review", False)
            .gte("transaction_date", since)
            .lte("transaction_date", tx_date)
            .execute()
        )
        verified_rows = [
            r for r in result.data
            if r.get("insiders") and r["insiders"].get("insider_verified")
        ]
        distinct = {r["insider_id"] for r in verified_rows}
        if len(distinct) >= 2:
            combined = sum(r.get("total_value") or 0 for r in verified_rows)
            return {"n_insiders": len(distinct), "combined_value": combined}
    except Exception as exc:
        logger.warning("Cluster check failed for company %d: %s", company_id, exc)

    return None


# ─── Email formatting ─────────────────────────────────────────────────────────

_DIR_LABEL = {"buy": "ACQUISTO", "sell": "VENDITA"}
_TYPE_LABEL = {
    "buy":             "Acquisto",
    "sell":            "Vendita",
    "grant":           "Assegnazione gratuita",
    "option_exercise": "Esercizio opzioni",
    "sell_to_cover":   "Vendita (sell-to-cover)",
    "other":           "Altro",
}


def _build_email(payload: AlertPayload, cluster: Optional[dict]) -> dict:
    """Return {"subject": str, "html": str, "text": str}."""
    dir_label  = _DIR_LABEL.get(payload.direction, payload.direction.upper())
    type_label = _TYPE_LABEL.get(payload.transaction_type, payload.transaction_type)
    value_str  = _fmt_value(payload.total_value, payload.currency)

    if cluster and payload.direction == "buy":
        subject = (
            f"[CLUSTER BUY] {payload.company_name} — "
            f"{cluster['n_insiders']} insider · {_fmt_value(cluster['combined_value'])}"
        )
    else:
        bracket = "Buy" if payload.direction == "buy" else "Sell"
        subject = f"[{bracket}] {payload.company_name} — {payload.insider_name} {value_str}"

    rows = [
        ("Società",            payload.company_name),
        ("Insider",            payload.insider_name),
        ("Ruolo",              payload.insider_role or "—"),
        ("Tipo operazione",    type_label),
        ("Quantità",           f"{payload.quantity:,.0f} azioni".replace(",", ".")),
        ("Prezzo unitario",    _fmt_value(payload.unit_price, payload.currency) if payload.unit_price else "—"),
        ("Valore totale",      value_str),
        ("Data operazione",    _fmt_date(payload.transaction_date)),
        ("Data comunicazione", _fmt_date(payload.filed_date) if payload.filed_date else "—"),
    ]
    if cluster and payload.direction == "buy":
        rows.append((
            "🔔 Cluster",
            f"{cluster['n_insiders']} insider distinti · "
            f"{_fmt_value(cluster['combined_value'])} combinati (7 giorni)",
        ))

    badge_color = "#4ADE80" if payload.direction == "buy" else "#F75C4F"

    trs = "".join(
        f'<tr>'
        f'<td style="padding:6px 16px 6px 24px;color:#94A3B8;font-size:13px;white-space:nowrap;vertical-align:top">{k}</td>'
        f'<td style="padding:6px 24px 6px 0;color:#E8EDF7;font-size:13px">{v}</td>'
        f'</tr>'
        for k, v in rows
    )

    html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#0F1623;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <div style="max-width:580px;margin:32px auto;background:#1A2235;border-radius:12px;overflow:hidden;border:1px solid rgba(255,255,255,0.08)">
    <div style="padding:20px 24px;background:#111827;border-bottom:1px solid rgba(255,255,255,0.06)">
      <span style="font-size:11px;font-weight:700;letter-spacing:0.1em;color:{badge_color};text-transform:uppercase">{dir_label}</span>
      <h1 style="margin:4px 0 0;font-size:20px;font-weight:700;color:#E8EDF7;line-height:1.3">{payload.company_name}</h1>
    </div>
    <table style="width:100%;border-collapse:collapse">{trs}</table>
    <div style="padding:16px 24px;border-top:1px solid rgba(255,255,255,0.06)">
      <a href="{payload.source_url}"
         style="display:inline-block;padding:8px 18px;background:rgba(96,165,250,0.15);color:#60A5FA;
                text-decoration:none;border-radius:8px;font-size:13px;font-weight:600;
                border:1px solid rgba(96,165,250,0.3)">
        Visualizza PDF →
      </a>
      <p style="margin:12px 0 0;font-size:11px;color:#475569">
        InsideWatch · Operazioni di Internal Dealing · MAR Art. 19
      </p>
    </div>
  </div>
</body>
</html>"""

    text = "\n".join(
        [subject, ""] + [f"{k}: {v}" for k, v in rows] + ["", f"PDF: {payload.source_url}"]
    )

    return {"subject": subject, "html": html, "text": text}


# ─── Email sender ─────────────────────────────────────────────────────────────

def send_email(payload: AlertPayload, cluster: Optional[dict]) -> bool:
    """Send alert via Resend API.  Returns True on success."""
    api_key   = os.getenv("RESEND_API_KEY", "")
    raw_to    = os.getenv("ALERT_EMAIL", "")
    from_addr = os.getenv("ALERT_FROM_EMAIL", "InsideWatch <onboarding@resend.dev>")

    if not api_key:
        logger.debug("RESEND_API_KEY not set — skipping email")
        return False
    if not raw_to:
        logger.debug("ALERT_EMAIL not set — skipping email")
        return False

    recipients = [r.strip() for r in raw_to.split(",") if r.strip()]
    email = _build_email(payload, cluster)

    try:
        resp = httpx.post(
            RESEND_API,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": from_addr,
                "to": recipients,
                "subject": email["subject"],
                "html": email["html"],
                "text": email["text"],
            },
            timeout=10.0,
        )
        if resp.status_code in (200, 201):
            logger.info("Alert sent: %s", email["subject"])
            return True
        logger.warning("Resend %d: %s", resp.status_code, resp.text[:300])
    except Exception as exc:
        logger.warning("Email alert failed: %s", exc)

    return False


# ─── Central dispatcher ───────────────────────────────────────────────────────

def dispatch(payload: AlertPayload, client, company_tier: int) -> None:
    """
    Fire all alert channels for a newly inserted transaction.

    Conditions checked here (caller should pre-filter insider_verified and
    needs_review, but we enforce tier and direction):
      · company_tier <= 2  (Tier 1 FTSE MIB or Tier 2 Mid Cap only)
      · direction in (buy, sell)  — grants/unknown are skipped
    """
    if company_tier > 2:
        return
    if payload.direction not in ("buy", "sell"):
        return

    cluster = None
    if payload.direction == "buy":
        cluster = check_cluster(client, payload.company_id, payload.transaction_date)

    # ── Email (active) ───────────────────────────────────────────────────────
    send_email(payload, cluster)

    # ── Telegram ────────────────────────────────────────────────────────────
    from .telegram import send_telegram
    send_telegram(payload, cluster)
