"""
PDF parser for MAR Art 19 internal dealing notifications.

Borsa Italiana PDFs follow the ESMA standard bilingual form (Italian/English).
Two distinct page layouts exist in the wild — this parser handles both with
fallback patterns. Where a field cannot be reliably extracted, a warning is
recorded rather than raising an exception, so a bad PDF never crashes the run.

Layout 1 (older/smaller companies, e.g. Emak):
    - Section 1a: "Nome: Paolo Cognome: Zambelli"
    - Section 2a: "Ruolo: Persona che esercita funzioni di amministrazione"
    - Section 4b direction: standalone line "CESSIONE"
    - Prices:    "0.911 EUR 10000" (price EUR qty_shares)
    - Aggregated: "Volume aggregato: 34000" / "Prezzo: 0.9066 EUR"

Layout 2 (larger/newer companies, e.g. Eni):
    - Section 1a: "a) Nome/First Name EMMA MARCEGAGLIA"
    - Section 2a: "...PERSONA CHE ESERCITA FUNZIONI DI AMMINISTRAZIONE/..."
    - Section 4b direction: inline "ACQUISTO/PURCHASE"
    - Prices:    "98,7590 % 300000 EUR" (price% face_value_EUR) for bonds
    - Aggregated: repeated price/volume row
"""

import hashlib
import io
import logging
import re
from typing import Optional

import pdfplumber

from .insiders import assess_insider, normalize_name
from .models import ParsedTransaction

logger = logging.getLogger(__name__)

# ─── Direction lookup ────────────────────────────────────────────────────────
# Italian and English terms that appear in Section 4b.
_DIRECTION_MAP = {
    "ACQUISTO": "buy",
    "PURCHASE": "buy",
    "SOTTOSCRIZIONE": "buy",       # subscription (new shares)
    "ESERCIZIO": "buy",            # exercise of options/warrants
    "ASSEGNAZIONE": "buy",         # award (free share grants)
    "CESSIONE": "sell",
    "VENDITA": "sell",
    "SALE": "sell",
    "TRASFERIMENTO": "sell",       # transfer (treated as sell for flagging)
}


# ─── Number parsing ──────────────────────────────────────────────────────────

def _parse_number(s: str) -> float:
    """
    Parse a number that may use Italian or standard decimal formatting.

    Italian:  1.234,56  →  1234.56
    Standard: 1234.56   →  1234.56
    Mixed:    98,7590   →  98.759   (comma as decimal, no thousands dot)
    """
    s = s.strip()
    if "." in s and "," in s:
        # Italian thousands separator + decimal comma: "1.234,56"
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        # Comma is decimal separator: "98,7590"
        s = s.replace(",", ".")
    # else: standard "0.911" — leave as-is
    try:
        return float(s)
    except ValueError:
        return 0.0


# ─── Text extraction ─────────────────────────────────────────────────────────

def extract_text(pdf_bytes: bytes) -> str:
    """Extract all text from all PDF pages, joined by newlines."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(pages)


# ─── Field extractors ────────────────────────────────────────────────────────

def _parse_insider_name(text: str) -> tuple[Optional[str], list[str]]:
    warnings: list[str] = []

    # Layout 1: "Nome: Paolo Cognome: Zambelli" (separate first/last)
    m = re.search(r"Nome:\s+(\S+)\s+Cognome:\s+(\S+)", text)
    if m:
        return f"{m.group(1)} {m.group(2)}", warnings

    # Layout 2: "a) Nome/First Name EMMA MARCEGAGLIA"
    # The name is everything after the label on the same line.
    m = re.search(r"Nome/First\s+Name\s+([A-Z][A-Z\s]+?)(?:\n|$)", text)
    if m:
        return m.group(1).strip(), warnings

    # Layout 3 (compact 2016/523 form): "a) Nome Massimo Guerra"
    # Only look in section 1 (before section 3 "a) Nome COMPANY").
    section1 = text.split("Dati relativi all")[1] if "Dati relativi all" in text else text
    # After splitting, the first "a) Nome" is always the person.
    m = re.search(r"a\)\s+Nome\s+([A-Z][a-zA-Z\s\.]+?)(?:\n|$)", section1)
    if m:
        return m.group(1).strip(), warnings

    # Fallback: AVVISO "Oggetto : Internal dealing - NAME"
    m = re.search(r"Oggetto\s*:.*?dealing[^\n]*-\s*([A-Z][A-Z\s]+?)(?:\n|$)", text, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        warnings.append(f"insider_name extracted from AVVISO subject line: '{name}'")
        return name, warnings

    warnings.append("Could not extract insider name")
    return None, warnings


def _parse_role(text: str) -> tuple[str, list[str]]:
    warnings: list[str] = []

    # Layout 1: "Ruolo: Persona che esercita funzioni di amministrazione"
    # followed by English translation "Role: ..."
    m = re.search(r"Ruolo:\s+(.+?)(?:\nRole:|$)", text, re.DOTALL)
    if m:
        return m.group(1).strip(), warnings

    # Layout 2: "...PERSONA CHE ESERCITA FUNZIONI DI AMMINISTRAZIONE/..."
    # The pattern is: label line ends with " - ITALIAN_ROLE/ENGLISH_ROLE"
    m = re.search(
        r"Posizione[^\n]+-\s+(PERSONA[^/\n]+?)(?:/[A-Z]|\n)",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip(), warnings

    # Layout 3 (compact form): "a) Posizione/qualifica Direttore Generale"
    m = re.search(r"Posizione/qualifica\s+(.+?)(?:\n|$)", text)
    if m:
        return m.group(1).strip(), warnings

    warnings.append("Could not extract role")
    return "Unknown", warnings


def _parse_company(text: str) -> tuple[Optional[str], list[str]]:
    warnings: list[str] = []

    # Most reliable: AVVISO page header "Mittente del comunicato : COMPANY"
    m = re.search(r"Mittente del comunicato\s*:\s*(.+?)(?:\n|$)", text)
    if m:
        return m.group(1).strip(), warnings

    warnings.append("Could not extract company name from AVVISO header")
    return None, warnings


def _parse_isin(tx_block: str) -> Optional[str]:
    """Extract ISIN or instrument identification code from a transaction block."""
    # Layout 1: "ISIN: IT0001237053"
    m = re.search(r"\bISIN:\s+([A-Z]{2}[A-Z0-9]{10})\b", tx_block)
    if m:
        return m.group(1)

    # Layout 2: "Identification code XS3262549754"
    m = re.search(r"Identification code\s+([A-Z]{2}[A-Z0-9]{10})\b", tx_block)
    if m:
        return m.group(1)

    # Layout 3 (compact): bare code on its own line, e.g. "IT0005623845"
    m = re.search(r"Codice di identificazione\s+([A-Z]{2}[A-Z0-9]{10})\b", tx_block)
    if m:
        return m.group(1)

    return None


def _parse_instrument_type(tx_block: str) -> str:
    """Extract human-readable instrument type."""
    # Layout 2 first: "tipo di STRUMENTO DI DEBITO/DEBT INSTRUMENT"
    # "tipo di" precedes the instrument type in the Italian section label.
    m = re.search(r"tipo di\s+([A-Z][A-Z /]+?)(?:/[A-Z]|\n)", tx_block, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        # Skip if it's just the bare word "strumento" (the section label itself, not a value)
        if candidate.lower() != "strumento" and len(candidate) > 3:
            return candidate

    # Layout 1: "Description of the Azioni Ordinarie"
    # The instrument type follows directly on the same line.
    # Exclude "financial" (bilingual table header in Layout 2) and "transaction"
    # (which appears in footnotes). Also cap length to avoid matching footnote sentences.
    m = re.search(
        r"Description of the\s+(?!financial\b)(?!transaction\b)([^\n]{1,60})",
        tx_block,
        re.IGNORECASE,
    )
    if m:
        candidate = m.group(1).strip()
        if len(candidate) <= 50:
            return candidate

    # Layout 3 (compact 2016/523): value appears between "Descrizione dello" and
    # "strumento finanziario" in the label column split across PDF rows.
    m = re.search(r"Descrizione dello\s+(.+?)\s*strumento finanziario", tx_block, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        # Guard against matching the long Italian description text in Layouts 1/2
        if candidate and len(candidate) <= 60 and not any(
            kw in candidate.lower() for kw in ["indicare", "natura", "operazione"]
        ):
            return candidate

    return "Unknown"


def _parse_direction(tx_block: str) -> tuple[str, list[str]]:
    warnings: list[str] = []

    # Strategy 1: extract just the section 4b area to avoid false matches.
    # Layout 1 has the direction word alone on its own line between the label
    # and the "A norma..." regulatory footnote.
    # Layout 2 has it inline: "Nature of the ACQUISTO/PURCHASE".
    section_4b = _extract_section_4b(tx_block)
    target = section_4b if section_4b else tx_block

    # Within the isolated section 4b text we can safely use IGNORECASE:
    # we've already filtered out the explanatory body text that contained
    # lowercase "acquisto, vendita" etc. in mid-sentence context.
    for term, direction in _DIRECTION_MAP.items():
        if re.search(r"\b" + term + r"\b", target, re.IGNORECASE):
            return direction, warnings

    warnings.append("Could not determine transaction direction (buy/sell)")
    return "unknown", warnings


def _extract_section_4b(tx_block: str) -> Optional[str]:
    """
    Extract the text of section 4b (Nature of the transaction) from a block.

    This isolates the area where the direction keyword lives, preventing false
    matches against the same words used in the bilingual explanatory footnotes.
    """
    # Layout 3 (compact 2016/523 form): "b) Natura dell'operazione Acquisto"
    # Direction is the ONLY word on the same line after the label (inline, mixed case).
    m = re.search(r"Natura dell.operazione\s+(\w+)\s*$", tx_block, re.MULTILINE)
    if m:
        return m.group(1)

    # Layout 1: direction sits between "Natura dell'operazione" label and
    # the standalone "A norma dell'articolo..." regulatory footnote.
    # Do NOT use IGNORECASE here: the body text contains "a norma" (lowercase)
    # mid-sentence which would cause a premature stop before the direction value.
    m = re.search(
        r"Natura dell.operazione(.+?)(?:A norma dell|Pursuant to Article)",
        tx_block,
        re.DOTALL,
    )
    if m:
        return m.group(1)

    # Layout 2: direction is inline on the "Nature of the" line
    m = re.search(r"Nature of the\s+(.+?)(?:\n|$)", tx_block)
    if m:
        return m.group(1)

    return None


def _parse_price_volume(tx_block: str) -> tuple[float, float, str, list[str]]:
    """
    Extract (quantity, unit_price, currency, warnings) from a transaction block.

    Tries the aggregated section first (section 4d), falls back to computing
    from individual rows in section 4c.

    For equities:  price_EUR qty_shares → unit_price in EUR
    For bonds/pct: price% face_value_EUR → unit_price stored as %, quantity in EUR
    The caller should check instrument_type to interpret correctly.
    """
    warnings: list[str] = []

    # ── Try aggregated section (4d) ──────────────────────────────────────────
    # Layout 1: "Volume aggregato: 34000" ... "Prezzo: 0.9066 EUR"
    vol_m = re.search(r"Volume aggregato[^:]*:\s*([\d\.,]+)", tx_block)
    price_m = re.search(r"Prezzo:\s*([\d\.,]+)\s+([A-Z%]+)", tx_block)

    if vol_m and price_m:
        qty = _parse_number(vol_m.group(1))
        price = _parse_number(price_m.group(1))
        currency = price_m.group(2) if price_m.group(2) not in ("%",) else "EUR"
        return qty, price, currency, warnings

    # Layout 2 aggregated: "— 98,7590 % 300000 EUR" on one line
    # Matches: price% face_value EUR  OR  price EUR qty
    agg_m = re.search(
        r"Aggregated volume\s*[—–-]+\s*([\d\.,]+)\s*(%|EUR)\s+([\d\.,]+)(?:\s+EUR)?",
        tx_block,
        re.IGNORECASE,
    )
    if agg_m:
        price_str, price_unit, vol_str = agg_m.group(1), agg_m.group(2), agg_m.group(3)
        price = _parse_number(price_str)
        qty = _parse_number(vol_str)
        return qty, price, "EUR", warnings

    # ── Fall back: collect individual price/volume rows (section 4c) ─────────
    # Look for the price/volume table between the section 4c header and 4d header.
    section_c = re.search(
        r"Price\(s\) and [Vv]olume\(s\)(.+?)(?:Aggregated information|d\)|$)",
        tx_block,
        re.DOTALL,
    )
    block = section_c.group(1) if section_c else tx_block

    # Equity rows: "0.911 EUR 10000"
    equity_rows = re.findall(r"([\d\.]+)\s+EUR\s+([\d\.,]+)", block)
    if equity_rows:
        prices = [_parse_number(p) for p, _ in equity_rows]
        vols = [_parse_number(v) for _, v in equity_rows]
        total_qty = sum(vols)
        if total_qty > 0:
            w_avg = sum(p * v for p, v in zip(prices, vols)) / total_qty
        else:
            w_avg = prices[0] if prices else 0.0
        return total_qty, w_avg, "EUR", warnings

    # Bond rows: "98,7590 % 300000 EUR"
    bond_rows = re.findall(r"([\d\.,]+)\s*%\s+([\d\.,]+)\s+EUR", block)
    if bond_rows:
        prices = [_parse_number(p) for p, _ in bond_rows]
        vols = [_parse_number(v) for _, v in bond_rows]
        total_qty = sum(vols)
        if total_qty > 0:
            w_avg = sum(p * v for p, v in zip(prices, vols)) / total_qty
        else:
            w_avg = prices[0] if prices else 0.0
        warnings.append("Bond price stored as % of face value; quantity is face value in EUR")
        return total_qty, w_avg, "EUR", warnings

    # Layout 3 (compact 2016/523 form): rows are "DD-MM-YYYY price volume [time]"
    # Sections 4c and 4d both contain such rows, so we must target section 4d
    # (the aggregate) to avoid double-counting.
    section_d = re.search(
        r"(?:Informazioni aggregate|Volume aggregato)(.+)",
        tx_block,
        re.DOTALL,
    )
    layout3_search_area = section_d.group(1) if section_d else tx_block
    layout3_rows = re.findall(
        r"\d{2}-\d{2}-\d{4}\s+([\d\.,]+)\s+([\d\.,]+)", layout3_search_area
    )
    if layout3_rows:
        prices = [_parse_number(p) for p, _ in layout3_rows]
        vols = [_parse_number(v) for _, v in layout3_rows]
        total_qty = sum(vols)
        if total_qty > 0:
            w_avg = sum(p * v for p, v in zip(prices, vols)) / total_qty
        else:
            w_avg = prices[0] if prices else 0.0
        return total_qty, w_avg, "EUR", warnings

    warnings.append("Could not parse price/volume data")
    return 0.0, 0.0, "EUR", warnings


def _parse_transaction_date(tx_block: str) -> tuple[Optional[str], list[str]]:
    warnings: list[str] = []

    # Layouts 1 & 2: ISO 8601 date after the section 4e label.
    m = re.search(r"Data dell[a']operazione[^\n]*\b(\d{4}-\d{2}-\d{2})\b", tx_block)
    if m:
        return m.group(1), warnings

    # Fallback: any ISO date anywhere in the block.
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", tx_block)
    if m:
        warnings.append("Transaction date found via fallback ISO pattern")
        return m.group(1), warnings

    # Layout 3 (compact form): date is in the aggregated table as DD-MM-YYYY.
    # e.g. "03-06-2026 0,084 56978"
    m = re.search(r"\b(\d{2})-(\d{2})-(\d{4})\b", tx_block)
    if m:
        iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        warnings.append(f"Transaction date converted from DD-MM-YYYY: {m.group(0)} → {iso}")
        return iso, warnings

    warnings.append("Could not extract transaction date")
    return None, warnings


# ─── raw_hash ────────────────────────────────────────────────────────────────

def _compute_hash(
    insider_name: str,
    company: str,
    tx_date: str,
    quantity: float,
    unit_price: float,
) -> str:
    key = f"{insider_name}|{company}|{tx_date}|{quantity}|{unit_price}"
    return hashlib.sha256(key.encode()).hexdigest()


# ─── Transaction block splitter ───────────────────────────────────────────────

def _split_transaction_blocks(text: str) -> tuple[str, list[str]]:
    """
    Split the PDF text into (header_section, list_of_transaction_blocks).

    Each block starts after a marker like "Operazione - 1",
    "Transaction - 1", or "Operazione/Operation - 1".
    """
    # Split ONLY on "Operazione - N" / "Operazione/Operation - N".
    # Do NOT split on standalone "Transaction - N" (the English mirror line in
    # Layout 1 PDFs) — that would create a spurious empty block between the
    # bilingual pair that appears on consecutive lines.
    pattern = re.compile(
        r"Operazione(?:/Operation)?\s*[-–]\s*\d+",
        re.IGNORECASE,
    )
    parts = pattern.split(text)
    if len(parts) == 1:
        # No transaction block markers — treat whole text as one block
        return text, [text]
    return parts[0], parts[1:]


# ─── Main entry point ────────────────────────────────────────────────────────

def parse_pdf(
    pdf_bytes: bytes,
    source_url: str,
    filing_date: str,
) -> list[ParsedTransaction]:
    """
    Parse a MAR Art 19 PDF notification.

    Returns a list of ParsedTransaction objects (one PDF can contain
    multiple transactions for different dates/venues/instruments).
    On any per-transaction parse error, logs a warning and continues.
    """
    try:
        full_text = extract_text(pdf_bytes)
    except Exception as exc:
        logger.error("Failed to extract text from %s: %s", source_url, exc)
        return []

    header, tx_blocks = _split_transaction_blocks(full_text)

    # Extract header-level fields shared across all transactions in this PDF
    company, company_warns = _parse_company(header + tx_blocks[0] if tx_blocks else header)
    insider_name, name_warns = _parse_insider_name(header + (tx_blocks[0] if tx_blocks else ""))
    role, role_warns = _parse_role(header + (tx_blocks[0] if tx_blocks else ""))

    # Normalise and verify insider
    if insider_name:
        insider_name = normalize_name(insider_name)
    assessment = assess_insider(insider_name or "", role)

    results: list[ParsedTransaction] = []

    for i, block in enumerate(tx_blocks):
        tx_num = i + 1
        all_warnings: list[str] = company_warns + name_warns + role_warns

        try:
            isin = _parse_isin(block)
            instrument_type = _parse_instrument_type(block)
            direction, dir_warns = _parse_direction(block)
            quantity, unit_price, currency, pv_warns = _parse_price_volume(block)
            tx_date, date_warns = _parse_transaction_date(block)

            all_warnings += dir_warns + pv_warns + date_warns

            if tx_date is None:
                all_warnings.append(f"Transaction {tx_num}: skipping — no date found")
                logger.warning("No date in transaction %d of %s — skipping", tx_num, source_url)
                continue

            # total_value: for bonds price is in %, so actual EUR = qty * (price/100)
            if "%" in currency or _is_percentage_price(block):
                total_value = quantity * (unit_price / 100.0)
            else:
                total_value = quantity * unit_price

            raw_hash = _compute_hash(
                insider_name or "",
                company or "",
                tx_date,
                quantity,
                unit_price,
            )

            results.append(ParsedTransaction(
                insider_name=insider_name or "Unknown",
                role=role,
                company_name=company or "Unknown",
                isin=isin,
                instrument_type=instrument_type,
                direction=direction,
                transaction_date=tx_date,
                quantity=quantity,
                unit_price=unit_price,
                currency=currency,
                total_value=round(total_value, 2),
                filing_date=filing_date,
                source_url=source_url,
                raw_hash=raw_hash,
                insider_verified=assessment["verified"],
                role_category=assessment["role_category"],
                parse_warnings=all_warnings,
            ))

        except Exception as exc:
            logger.error(
                "Unexpected error parsing transaction %d of %s: %s",
                tx_num,
                source_url,
                exc,
                exc_info=True,
            )

    return results


def _is_percentage_price(tx_block: str) -> bool:
    """Heuristic: is the price in this block expressed as a percentage?"""
    return bool(re.search(r"[\d\.,]+\s*%\s+[\d\.,]+\s+EUR", tx_block))
