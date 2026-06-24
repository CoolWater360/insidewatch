"""
Alert dispatcher for InsideWatch.

Single entry point: dispatch(payload, client, company_tier)
Sends email via Resend and Telegram via Bot API.

Required env vars:
  RESEND_API_KEY       — Resend secret key
  ALERT_EMAIL          — comma-separated recipient addresses
  ALERT_FROM_EMAIL     — verified sender (e.g. "InsideWatch <alerts@yourdomain.com>")
  TELEGRAM_BOT_TOKEN   — Telegram bot token from @BotFather
  TELEGRAM_CHANNEL_ID  — Channel username or numeric chat ID

Alert quality filters (configurable via env vars):
  ALERT_MIN_VALUE_EUR      — suppress alerts below this total value (default: 10000)
  ALERT_EXCLUDE_MECHANICAL — skip non-discretionary event types (default: true)

Ingestion context:
  ALERT_CONTEXT  — 'live' (default) or 'backfill'
  During 'backfill' context all transaction alerts are suppressed; use this
  when running reprocess_historical or an initial full-company sweep so that
  a large batch of historical inserts does not flood alert channels.
  During 'live' context alerts fire normally. Filings whose filed_date is
  older than _LATE_DISCOVERY_DAYS calendar days are labelled [LATE] in the
  subject and body so the operator knows the alert relates to a document that
  was not discovered on its original publication date.

Pass urgent=True to dispatch() to bypass all quality filters regardless of
context (hard gates on tier and direction still apply).
"""

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

RESEND_API = "https://api.resend.com/emails"

# Filings discovered more than this many calendar days after their filed_date
# are labelled [LATE] in live-context alerts.  This is a labelling threshold
# only — it never suppresses an alert.
# 5 days ≈ MAR 3-business-day disclosure deadline + 2 calendar-day buffer.
_LATE_DISCOVERY_DAYS = 5

# Transaction types that represent mechanical / non-discretionary events.
# Kept in sync with classifier.py's mechanical taxonomy.
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
    # Minimum total transaction value to fire an alert.
    # Default: 10 000 — practical noise floor for institutional signal workflows.
    min_value_eur: float = 10_000.0

    # When True, mechanical/non-discretionary event types (grants, exercises,
    # transfers, conversions) are suppressed. These are not trading signals.
    exclude_mechanical: bool = True


def _load_alert_config() -> AlertConfig:
    """Read AlertConfig from environment variables."""
    raw_min = os.getenv("ALERT_MIN_VALUE_EUR", "10000")
    raw_mech = os.getenv("ALERT_EXCLUDE_MECHANICAL", "true")

    try:
        min_value = float(raw_min)
    except (ValueError, TypeError):
        logger.warning("Invalid ALERT_MIN_VALUE_EUR=%r — using 10000", raw_min)
        min_value = 10_000.0

    exclude_mechanical = raw_mech.strip().lower() not in ("0", "false", "no")

    return AlertConfig(
        min_value_eur=min_value,
        exclude_mechanical=exclude_mechanical,
    )


def _parse_filed_date(filed_date: str) -> Optional[date]:
    """
    Parse filed_date from either YYYY-MM-DD or DD/MM/YYYY.
    Returns None if unparseable.
    """
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(filed_date[:10], fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _is_late_discovery(filed_date: str) -> bool:
    """
    Return True if the filing is older than _LATE_DISCOVERY_DAYS calendar days.

    Used in live context only: a True result adds a [LATE] label to the alert
    but never suppresses it.  Returns False when filed_date is unparseable
    (unknown age → not labelled as late).
    """
    if not filed_date:
        return False
    filed = _parse_filed_date(filed_date)
    if filed is None:
        return False
    return (datetime.utcnow().date() - filed).days > _LATE_DISCOVERY_DAYS


def should_dispatch(
    payload: AlertPayload,
    config: AlertConfig,
    urgent: bool = False,
) -> Tuple[bool, str]:
    """
    Return (True, "") if the alert should fire, (False, reason) if suppressed.

    Checks value threshold and mechanical-type exclusion.
    urgent=True bypasses both filters — use for high-priority failure
    notifications that must reach the operator regardless of value or type.

    Filing age is NOT checked here; age-based behaviour is handled by context
    (backfill → suppress all; live → label late but always send).
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


def _build_email(
    payload: AlertPayload,
    cluster: Optional[dict],
    late_discovery: bool = False,
) -> dict:
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

    if late_discovery:
        subject = f"[LATE] {subject}"

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
    if late_discovery and payload.filed_date:
        filed = _parse_filed_date(payload.filed_date)
        age_label = f"{(datetime.utcnow().date() - filed).days}g fa" if filed else ""
        rows.append((
            "⏰ Tarda scoperta",
            f"Pubblicata {age_label} · {_fmt_date(payload.filed_date)}".strip(" ·"),
        ))
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

def send_email(
    payload: AlertPayload,
    cluster: Optional[dict],
    late_discovery: bool = False,
) -> bool:
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
    email = _build_email(payload, cluster, late_discovery=late_discovery)

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
    context: str = "live",
) -> None:
    """
    Fire all alert channels for a newly inserted transaction.

    Hard gates (always enforced, even when urgent=True):
      · company_tier <= 2  (Tier 1 FTSE MIB or Tier 2 Mid Cap only)
      · direction in (buy, sell)

    Context gate (enforced before quality filters):
      · context='backfill'  → suppress all transaction alerts and return.
        Use when running reprocess_historical or a full-company initial sweep
        so historical inserts do not flood alert channels.
      · context='live' (default) → proceed normally.

    Quality filters (bypassed when urgent=True):
      · total_value >= ALERT_MIN_VALUE_EUR  (default 10 000 EUR)
      · transaction_type not in MECHANICAL_TRANSACTION_TYPES

    Late-discovery labelling (live context only, never suppresses):
      · Filings whose filed_date is > _LATE_DISCOVERY_DAYS old get a [LATE]
        prefix in the subject and a note in the body.  The alert still fires.
    """
    # Hard gates — never bypassed
    if company_tier > 2:
        return
    if payload.direction not in ("buy", "sell"):
        return

    # Context gate — suppress all transaction alerts during intentional backfills
    if context == "backfill":
        logger.info(
            "Alert suppressed (backfill context): %s %s €%s",
            payload.company_name, payload.direction,
            f"{payload.total_value:,.0f}",
        )
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

    # Late-discovery labelling (label-only, never suppresses)
    late = _is_late_discovery(payload.filed_date)
    if late:
        logger.info(
            "Late discovery alert: %s — filed %s (>%dd ago)",
            payload.company_name, payload.filed_date, _LATE_DISCOVERY_DAYS,
        )

    cluster = None
    if payload.direction == "buy":
        cluster = check_cluster(client, payload.company_id, payload.transaction_date)

    # ── Email (active) ───────────────────────────────────────────────────────
    send_email(payload, cluster, late_discovery=late)

    # ── Telegram ────────────────────────────────────────────────────────────
    from .telegram import send_telegram
    send_telegram(payload, cluster, late_discovery=late)
