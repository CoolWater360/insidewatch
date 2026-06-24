"""
Alert dispatcher for InsideWatch.

Single entry point: dispatch(payload, client, company_tier)
Currently sends email via Resend.  Telegram seam is active.

Required env vars:
  RESEND_API_KEY       — Resend secret key
  ALERT_EMAIL          — comma-separated recipient addresses
  ALERT_FROM_EMAIL     — verified sender (e.g. "InsideWatch <alerts@yourdomain.com>")
  TELEGRAM_BOT_TOKEN   — Telegram bot token from @BotFather
  TELEGRAM_CHANNEL_ID  — Channel username or numeric chat ID

Alert quality filters (all configurable via env vars):
  ALERT_MIN_VALUE_EUR        — suppress alerts below this total value (default: 50000)
  ALERT_EXCLUDE_MECHANICAL   — skip non-discretionary event types (default: true)
  ALERT_MAX_FILING_AGE_DAYS  — suppress alerts for old filings (default: 5; 0 = disabled)

Pass urgent=True to dispatch() to bypass all quality filters (e.g. for
high-priority review-failure notifications).
"""

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

RESEND_API = "https://api.resend.com/emails"

# Transaction types that represent mechanical / non-discretionary events.
# Updated in sync with classifier.py's mechanical taxonomy.
MECHANICAL_TRANSACTION_TYPES: frozenset = frozenset({
    "grant",
    "option_exercise",
    "sell_to_cover",
    "conversion",
    "inheritance",
    "gift_in",
    "gift_out",
    "transfer_in",
    "transfer_out",
    "pledge_or_security",
    "derivative_transaction",
})


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
    filed_date: str         # DD/MM/YYYY (Italian listing page format) or YYYY-MM-DD
    source_url: str
    transaction_id: int


# ─── Alert quality filter ─────────────────────────────────────────────────────

@dataclass
class AlertConfig:
    """Quality thresholds for alert suppression. Loaded from environment."""
    # Minimum total transaction value (in reporting currency) to fire an alert.
    # Transactions below this threshold are suppressed as noise.
    # Default: 50 000 — the MAR Art.19 single-transaction reporting threshold.
    min_value_eur: float = 50_000.0

    # When True, mechanical/non-discretionary event types (grants, exercises,
    # transfers, conversions) are suppressed. These are not trading signals.
    exclude_mechanical: bool = True

    # Suppress alerts for filings older than this many calendar days.
    # Protects against alert floods during initial backfills and sweep runs.
    # 0 = disabled (all ages pass). Default: 5 (> MAR 3-business-day deadline).
    max_filing_age_days: int = 5


def _load_alert_config() -> AlertConfig:
    """Read AlertConfig from environment variables."""
    raw_min = os.getenv("ALERT_MIN_VALUE_EUR", "50000")
    raw_mech = os.getenv("ALERT_EXCLUDE_MECHANICAL", "true")
    raw_age = os.getenv("ALERT_MAX_FILING_AGE_DAYS", "5")

    try:
        min_value = float(raw_min)
    except (ValueError, TypeError):
        logger.warning("Invalid ALERT_MIN_VALUE_EUR=%r — using 50000", raw_min)
        min_value = 50_000.0

    exclude_mechanical = raw_mech.strip().lower() not in ("0", "false", "no")

    try:
        max_age = int(raw_age)
    except (ValueError, TypeError):
        logger.warning("Invalid ALERT_MAX_FILING_AGE_DAYS=%r — using 5", raw_age)
        max_age = 5

    return AlertConfig(
        min_value_eur=min_value,
        exclude_mechanical=exclude_mechanical,
        max_filing_age_days=max_age,
    )


def _parse_filed_date(filed_date: str) -> Optional[date]:
    """
    Parse filed_date from either YYYY-MM-DD or DD/MM/YYYY.
    Returns None if unparseable (caller treats as age unknown → pass).
    """
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(filed_date[:10], fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def should_dispatch(
    payload: AlertPayload,
    config: AlertConfig,
    urgent: bool = False,
) -> Tuple[bool, str]:
    """
    Return (True, "") if the alert should fire, (False, reason) if suppressed.

    urgent=True bypasses all quality filters.  Use this for high-priority
    failure notifications that must reach the operator regardless of value or age.
    """
    if urgent:
        return True, ""

    # ── Value threshold ───────────────────────────────────────────────────────
    if config.min_value_eur > 0 and payload.total_value < config.min_value_eur:
        return (
            False,
            f"below_min_value (€{payload.total_value:,.0f} < €{config.min_value_eur:,.0f})",
        )

    # ── Mechanical-type exclusion ─────────────────────────────────────────────
    if config.exclude_mechanical and payload.transaction_type in MECHANICAL_TRANSACTION_TYPES:
        return False, f"mechanical_type ({payload.transaction_type})"

    # ── Filing age ────────────────────────────────────────────────────────────
    if config.max_filing_age_days > 0 and payload.filed_date:
        filed = _parse_filed_date(payload.filed_date)
        if filed is not None:
            age_days = (datetime.utcnow().date() - filed).days
            if age_days > config.max_filing_age_days:
                return (
                    False,
                    f"filing_too_old ({age_days}d > {config.max_filing_age_days}d)",
                )

    return True, ""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_value(v: float, currency: str = "EUR") -> str:
    """Italian-locale EUR formatting: 1.234.567 EUR"""
    if v == 0:
        return "—"
    return f"{v:,.0f} {currency}".replace(",", ".")


def _fmt_date(iso: str) -> str:
    """YYYY-MM-DD → GG/MM/AAAA (pass-through if already DD/MM/YYYY)."""
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

def dispatch(
    payload: AlertPayload,
    client,
    company_tier: int,
    urgent: bool = False,
) -> None:
    """
    Fire all alert channels for a newly inserted transaction.

    Hard gates (always enforced):
      · company_tier <= 2  (Tier 1 FTSE MIB or Tier 2 Mid Cap only)
      · direction in (buy, sell)

    Quality filters (bypassed when urgent=True):
      · total_value >= ALERT_MIN_VALUE_EUR  (default 50 000 EUR)
      · transaction_type not in mechanical types  (default: exclude)
      · filed_date within ALERT_MAX_FILING_AGE_DAYS  (default: 5 days)

    Pass urgent=True for high-priority failure notifications where
    value/age/type filters must not suppress the alert.
    """
    # Hard gates — never bypassed
    if company_tier > 2:
        return
    if payload.direction not in ("buy", "sell"):
        return

    # Quality filters — bypassed when urgent=True
    config = _load_alert_config()
    ok, reason = should_dispatch(payload, config, urgent=urgent)
    if not ok:
        logger.info(
            "Alert suppressed [%s]: %s %s €%s",
            reason, payload.company_name, payload.direction,
            f"{payload.total_value:,.0f}",
        )
        return

    cluster = None
    if payload.direction == "buy":
        cluster = check_cluster(client, payload.company_id, payload.transaction_date)

    # ── Email (active) ───────────────────────────────────────────────────────
    send_email(payload, cluster)

    # ── Telegram ────────────────────────────────────────────────────────────
    from .telegram import send_telegram
    send_telegram(payload, cluster)
