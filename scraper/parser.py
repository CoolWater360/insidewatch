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

# Increment this when the parser logic changes in a way that may produce
# different output from the same PDF.  Format: '<major>.<minor>.<patch>'
# Legacy records (pre-Phase 1) are labelled '0.0.0' via migration 001.
PARSER_VERSION = "1.0.0"

# ─── Direction + transaction-type lookup ─────────────────────────────────────
# Maps keyword → (direction, transaction_type).
# direction:        buy | sell | unknown
# transaction_type: buy | sell | grant | option_exercise | sell_to_cover | other
_DIRECTION_MAP: dict[str, tuple[str, str]] = {
    "ACQUISTO":       ("buy",  "buy"),
    "PURCHASE":       ("buy",  "buy"),
    "SOTTOSCRIZIONE": ("buy",  "buy"),            # subscription (new shares)
    "ASSEGNAZIONE":   ("buy",  "grant"),          # free share assignment
    "ATTRIBUZIONE":   ("buy",  "grant"),          # free share attribution
    "ASSIGNMENT":     ("buy",  "grant"),
    "ESERCIZIO":      ("buy",  "option_exercise"),# option / warrant exercise
    "EXERCISE":       ("buy",  "option_exercise"),
    "CESSIONE":       ("sell", "sell"),
    "VENDITA":        ("sell", "sell"),
    "SALE":           ("sell", "sell"),
    "TRASFERIMENTO":  ("sell", "sell"),           # transfer
    "DONAZIONE":      ("sell", "sell"),           # donation
    "PERMUTA":        ("sell", "sell"),           # exchange
    "SUCCESSIONE":    ("buy",  "other"),          # inheritance
}


# ─── Two-column degarbler ────────────────────────────────────────────────────

def _degarble(text: str) -> str:
    """
    Recover text garbled by two-column PDF character interleaving.

    Layout 2 PDFs have Italian text in the left column and English in the right
    column side-by-side. pdfplumber reads across rows and interleaves chars:
        ALTRO/OTHER → "A U L N T D R E O R"
    Taking every other character (even indices) recovers the Italian word:
        AULNTDREOR → A(0) L(2) T(4) R(6) O(8) = ALTRO ✓
    Only applied to runs of 5+ space-separated single uppercase chars,
    so normal text is not affected.
    """
    def _fix(m: re.Match) -> str:
        chars = re.sub(r"\s", "", m.group(0))
        return chars[::2]  # even-index chars = Italian column

    return re.sub(r"(?:[A-Z0-9/,\.–-] ){5,}[A-Z0-9/,\.–-]", _fix, text)


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

    # Layout 1: "Nome: Paolo Cognome: Zambelli"
    # Bug fix: original used \S+ for surname, truncating multi-word surnames
    # like "Dal Bo", "De Nora", "Di Palma". Capture the full rest of the line.
    m = re.search(r"Nome:\s+(\S+)\s+Cognome:\s+([^\n]+?)(?:\n|$)", text)
    if m:
        first = m.group(1).strip()
        last = " ".join(m.group(2).split())  # collapse internal whitespace
        return f"{first} {last}", warnings

    # Layout 2: "a) Nome/First Name EMMA MARCEGAGLIA"
    # Apply degarble first — two-column PDFs interleave chars so
    # "EMMA MARCEGAGLIA" may arrive as "E M M A  M A R C E G A G L I A".
    degarbled = _degarble(text)
    m = re.search(r"Nome/First\s+Name\s+([A-Z][A-Z\s\-\']{2,80}?)(?:\n|$)", degarbled)
    if m:
        return " ".join(m.group(1).split()), warnings

    # Layout 3 (compact 2016/523 form): "a) Nome Massimo Guerra"
    # Only look in section 1 (before section 3 "a) Nome COMPANY").
    section1 = text.split("Dati relativi all")[1] if "Dati relativi all" in text else text
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


def _parse_direction(tx_block: str) -> tuple[str, str, list[str]]:
    """
    Return (direction, transaction_type, warnings).

    Strategy:
      1. Isolate section 4b to avoid false matches in bilingual footnotes.
      2. Degarble in case of two-column interleaving (Layout 2).
      3. Match keyword map → (direction, transaction_type).
      4. If "Altro/Other", inspect the description text for sub-type keywords.
      5. If linked to share option programme (SI/YES) → option_exercise.
      6. Fall back to unknown/other + flag needs_review.
    """
    warnings: list[str] = []
    direction = "unknown"
    tx_type = "other"

    section_4b = _extract_section_4b(tx_block)
    target_raw = section_4b if section_4b else tx_block
    target = _degarble(target_raw)

    # ── Step 1: direct keyword match ─────────────────────────────────────────
    for term, (d, t) in _DIRECTION_MAP.items():
        if re.search(r"\b" + term + r"\b", target, re.IGNORECASE):
            return d, t, warnings

    # ── Step 2: "Altro / Other" with description ──────────────────────────────
    # The filing uses "Altro/Other" as the top-level label and puts the real
    # nature in the description text that follows. Parse that description.
    # Use IGNORECASE so degarbled "ALTRO" (all-caps) is also matched.
    altro_m = re.search(
        r"altro\s*/?\s*other\s*[-–—]?\s*(.{0,400})",
        target,
        re.DOTALL | re.IGNORECASE,
    )
    if altro_m:
        desc = altro_m.group(1).upper()
        if any(k in desc for k in ("ASSEGNAZIONE", "ATTRIBUZIONE", "GRATUITO", "AWARD", "ATTRIBUTION")):
            if "VENDITA" in desc or "SELL" in desc or "COPERTURA" in desc or "COVER" in desc:
                # Sell-to-cover: selling free shares to cover tax liability
                direction, tx_type = "sell", "sell_to_cover"
            else:
                direction, tx_type = "buy", "grant"
        elif any(k in desc for k in ("ESERCIZIO", "EXERCISE", "OPZION", "OPTION", "WARRANT")):
            direction, tx_type = "buy", "option_exercise"
        elif any(k in desc for k in ("VENDITA", "CESSIONE", "SELL", "SALE")):
            direction, tx_type = "sell", "sell"
        elif any(k in desc for k in ("ACQUISTO", "PURCHASE", "ACQUIS")):
            direction, tx_type = "buy", "buy"
        else:
            # Altro with unrecognised description — keep unknown but note it
            warnings.append(f"Altro/Other with unrecognised description: {desc[:80]}")

        if direction != "unknown":
            return direction, tx_type, warnings

    # ── Step 3: option programme SI/YES signal ────────────────────────────────
    # The form asks "is this transaction linked to a share option programme?"
    # If answered SI/YES and we still haven't found a direction, it is probably
    # an option exercise — but sell-to-cover transactions (which arise from the
    # same plan) also answer SI/YES.  Mark as needs_review so no alert fires.
    if re.search(r"\bSI\b|\bYES\b", tx_block) and re.search(
        r"opzion|option programme|programma.*opzion", tx_block, re.IGNORECASE
    ):
        warnings.append("Direction inferred from option programme SI/YES flag — needs_review")
        return "buy", "option_exercise", warnings

    warnings.append("Could not determine transaction direction — flagged for review")
    return "unknown", "other", warnings


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


# ─── New field calculators ────────────────────────────────────────────────────

def _compute_economic_intent(transaction_type: str) -> str:
    """
    Map transaction_type → economic_intent.
    discretionary = the insider chose to transact; mechanical = automatic/contractual.
    Kept separate from transaction_type so signal logic can filter precisely.
    """
    if transaction_type in ("buy", "sell"):
        return "discretionary"
    if transaction_type in ("grant", "option_exercise", "sell_to_cover"):
        return "mechanical"
    return "unclear"


def _compute_extraction_confidence(
    insider_name: Optional[str],
    company: Optional[str],
    tx_date: Optional[str],
    quantity: float,
    unit_price: float,
    transaction_type: str,
    parse_warnings: list[str],
) -> float:
    """
    Rule-based extraction confidence score (0.0–1.0).
    Penalises each required field that was not successfully extracted.
    Phase 5 will replace this with per-field granular scoring.
    """
    score = 1.0
    if not insider_name or insider_name == "Unknown":
        score -= 0.25
    if not company or company == "Unknown":
        score -= 0.25
    if not tx_date:
        score -= 0.25
    # Zero qty/price is expected for grants; penalise only for non-grants
    if quantity == 0 and transaction_type != "grant":
        score -= 0.15
    if unit_price == 0 and transaction_type != "grant":
        score -= 0.10
    # Each warning is a signal of extraction difficulty (cap at 4 penalties)
    score -= min(len(parse_warnings), 4) * 0.05
    return round(max(0.0, min(1.0, score)), 3)


def _compute_classification_confidence(
    direction: str,
    transaction_type: str,
    si_yes_fallback: bool,
) -> float:
    """
    Rule-based classification confidence score (0.0–1.0).
    Measures how certain we are that direction and transaction_type are correct.
    """
    score = 1.0
    if direction == "unknown":
        score -= 0.5
    if si_yes_fallback:
        score -= 0.30
    if transaction_type == "other":
        score -= 0.20
    return round(max(0.0, min(1.0, score)), 3)


def _compute_review_reason(
    direction: str,
    transaction_type: str,
    si_yes_fallback: bool,
    name_truncated: bool,
    company: Optional[str],
) -> Optional[str]:
    """
    Return a single structured tag string explaining the primary review trigger,
    or None if no review is needed.  Tags are stable identifiers — do not change
    existing tag strings as they may be stored in the database.
    """
    if direction == "unknown" and transaction_type != "grant":
        return "ambiguous_direction"
    if si_yes_fallback:
        return "si_yes_inferred"
    if name_truncated:
        return "truncated_name"
    if not company or company == "Unknown":
        return "missing_issuer"
    return None


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
    # Compute document hash once for all transactions from this PDF.
    # Used for integrity verification and Phase 4 re-parse idempotency.
    doc_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

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

    # Detect likely-truncated names: single word (no space) that isn't "Unknown"
    name_truncated = bool(
        insider_name
        and insider_name != "Unknown"
        and " " not in insider_name
    )
    if name_truncated:
        name_warns.append(f"Insider name looks truncated (single word): '{insider_name}'")

    results: list[ParsedTransaction] = []

    for i, block in enumerate(tx_blocks):
        tx_num = i + 1
        all_warnings: list[str] = company_warns + name_warns + role_warns

        try:
            isin = _parse_isin(block)
            instrument_type = _parse_instrument_type(block)
            direction, transaction_type, dir_warns = _parse_direction(block)
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

            # Zero-price rows are non-cash grants regardless of direction detection
            if unit_price == 0 and total_value == 0 and quantity > 0:
                transaction_type = "grant"

            # needs_review: unresolved direction, weak SI/YES guess, or truncated name
            si_yes_fallback = any("SI/YES flag" in w for w in dir_warns)
            needs_review = (
                (direction == "unknown" and transaction_type != "grant")
                or si_yes_fallback
                or name_truncated
            )

            # Derived fields (new in Phase 1)
            economic_intent = _compute_economic_intent(transaction_type)
            extraction_conf = _compute_extraction_confidence(
                insider_name, company, tx_date, quantity, unit_price,
                transaction_type, all_warnings,
            )
            classification_conf = _compute_classification_confidence(
                direction, transaction_type, si_yes_fallback,
            )
            review_status = "pending_review" if needs_review else "confirmed"
            review_reason = _compute_review_reason(
                direction, transaction_type, si_yes_fallback,
                name_truncated, company,
            )

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
                transaction_type=transaction_type,
                economic_intent=economic_intent,
                needs_review=needs_review,
                extraction_confidence=extraction_conf,
                classification_confidence=classification_conf,
                review_status=review_status,
                review_reason=review_reason,
                source_transaction_index=i,
                raw_document_sha256=doc_sha256,
                parser_version=PARSER_VERSION,
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
