"""
Telegram alert sender for InsideWatch.

Sends a formatted Markdown message to a channel/group via Bot API.

Required env vars (set as GitHub Secrets):
  TELEGRAM_BOT_TOKEN   — Bot token from @BotFather (e.g. 1234567890:ABC...)
  TELEGRAM_CHANNEL_ID  — Channel username or numeric chat ID (e.g. @myChannel or -1001234567890)
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"
_TIMEOUT = 10.0


def _fmt_value(v: float, currency: str = "EUR") -> str:
    if v == 0:
        return "—"
    return f"{v:,.0f} {currency}".replace(",", ".")


def _fmt_date(iso: str) -> str:
    """YYYY-MM-DD → GG/MM/AAAA"""
    try:
        from datetime import datetime
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return iso


def _build_message(payload, cluster: Optional[dict]) -> str:
    """Return a Markdown message string (Telegram's legacy Markdown v1 syntax)."""
    dir_emoji = "🟢" if payload.direction == "buy" else "🔴"
    dir_label = "ACQUISTO" if payload.direction == "buy" else "VENDITA"
    value_str = _fmt_value(payload.total_value, payload.currency)

    lines = [
        f"{dir_emoji} *{dir_label}* — {payload.company_name}",
        f"👤 {payload.insider_name} ({payload.insider_role or '—'})",
        f"💰 {value_str}",
        f"📅 {_fmt_date(payload.transaction_date)}",
    ]

    if cluster and payload.direction == "buy":
        combined = _fmt_value(cluster["combined_value"])
        lines.append(
            f"🔔 *CLUSTER BUY*: {cluster['n_insiders']} insider · {combined} combinati (7gg)"
        )

    lines.append(f"[Visualizza PDF →]({payload.source_url})")
    return "\n".join(lines)


def send_telegram(payload, cluster: Optional[dict]) -> bool:
    """Send alert via Telegram Bot API.  Returns True on success."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "")

    if not bot_token:
        logger.debug("TELEGRAM_BOT_TOKEN not set — skipping Telegram")
        return False
    if not channel_id:
        logger.debug("TELEGRAM_CHANNEL_ID not set — skipping Telegram")
        return False

    text = _build_message(payload, cluster)
    url = f"{_TELEGRAM_API}/bot{bot_token}/sendMessage"

    try:
        resp = httpx.post(
            url,
            json={
                "chat_id": channel_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            logger.info("Telegram alert sent for %s", payload.company_name)
            return True
        logger.warning("Telegram API %d: %s", resp.status_code, resp.text[:300])
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)

    return False
