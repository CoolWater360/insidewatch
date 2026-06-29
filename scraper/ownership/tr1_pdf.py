"""Adapter for post-2026-06-15 ESMA TR-1 / MJSHLD PDF notifications.

The new CONSOB system publishes each major-holding notification as a structured
ESMA TR-1 (MJSHLD) PDF.  This adapter has two clearly separated steps:

  extract_text_from_pdf(pdf_bytes) -> str   (thin pdfplumber I/O)
  parse_tr1(text, ...) -> ParsedOwnershipRecord   (pure, fully testable)

It is deliberately NOT shared with the archive-HTML adapter: the two source
formats expose different fields and must not be assumed interchangeable.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Optional

from .models import (
    SOURCE_FORMAT_TR1_PDF,
    HoldingVehicle,
    ParsedOwnershipRecord,
    classify_event_type,
    derive_crossed_threshold,
    parse_italian_date,
    parse_pct,
)

logger = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from a TR-1 PDF using pdfplumber. Thin I/O wrapper."""
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as doc:
        for page in doc.pages:
            txt = page.extract_text() or ""
            if txt:
                parts.append(txt)
    return "\n".join(parts)


def _norm_apostrophes(text: str) -> str:
    """Normalise curly apostrophes/quotes that PDF fonts introduce."""
    return (text or "").replace("’", "'").replace("‘", "'")


def _field(text: str, label_pattern: str) -> Optional[str]:
    """Return the trimmed remainder of the first line matching `label_pattern:`."""
    m = re.search(label_pattern + r"\s*:?\s*(.+)", text, re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip()


def parse_tr1(
    text: str,
    *,
    source_url: str,
    publication_date: Optional[str] = None,
    document_hash: Optional[str] = None,
) -> ParsedOwnershipRecord:
    """Parse extracted TR-1 text into an ownership record.

    Field extraction is label-driven and conservative: any field absent from
    the form stays None rather than being inferred.
    """
    text = _norm_apostrophes(text)
    warnings: list[str] = []
    ambiguous: list[str] = []

    issuer = _field(text, r"(?:1\.\s*)?Issuer")
    issuer_lei = _field(text, r"Issuer LEI")
    isin = _field(text, r"ISIN")
    declarant = (
        _field(text, r"person subject to notification obligation")
        or _field(text, r"Full name of shareholder\(s\)")
        or _field(text, r"Full name of shareholder")
    )
    declarant_lei = _field(text, r"Submitting entity LEI")

    event_date = parse_italian_date(
        _field(text, r"Date on which threshold was crossed or reached") or ""
    )
    notify_date = parse_italian_date(
        _field(text, r"Date on which issuer (?:was )?notified") or ""
    )

    voting_shares = parse_pct(
        _field(text, r"% of voting rights attached to shares") or ""
    )
    voting_total = parse_pct(_field(text, r"Total of both") or "")
    prior_raw = _field(text, r"Previous notified % of voting rights")
    voting_before = parse_pct(prior_raw or "")
    has_prior = prior_raw is not None and voting_before is not None

    voting_after = voting_total if voting_total is not None else voting_shares

    # Nature of holding + (optionally) a named vehicle.
    nature = _field(text, r"Nature of holding") or ""
    direct_indirect = None
    up = nature.upper()
    if "INDIRECT" in up:
        direct_indirect = "indirect"
    elif "DIRECT" in up:
        direct_indirect = "direct"

    vehicles: list[HoldingVehicle] = []
    vm = re.search(r"controlled vehicle\s+(.+)", nature, re.IGNORECASE)
    if vm:
        vehicles.append(HoldingVehicle(raw_name=vm.group(1).strip(), pct=None))

    event_type, notes = classify_event_type(
        pct_before=voting_before,
        pct_after=voting_after,
        has_prior_notification=has_prior,
        ambiguous_before=False,
        raw_percent_text=nature,
    )
    warnings.extend(notes)

    if issuer is None:
        warnings.append("issuer not found in TR-1 text")
    if voting_after is None:
        warnings.append("voting_pct_after not found in TR-1 text")
    if event_date is None:
        warnings.append("threshold-crossed date not found in TR-1 text")

    evidence_lines = [
        ln for ln in text.splitlines()
        if any(k in ln.lower() for k in (
            "issuer", "shareholder", "threshold", "voting rights", "total", "previous"
        ))
    ]

    return ParsedOwnershipRecord(
        source_url=source_url,
        source_format=SOURCE_FORMAT_TR1_PDF,
        publication_date=publication_date or notify_date,
        document_hash=document_hash,
        raw_text="\n".join(evidence_lines) if evidence_lines else text,
        issuer_raw_name=issuer or "",
        declarant_raw_name=declarant or "",
        holding_vehicles=vehicles,
        event_date=event_date,
        voting_pct_after=voting_after,
        voting_pct_before=voting_before,
        direct_or_indirect=direct_indirect,
        event_type=event_type,
        issuer_isin=isin,
        issuer_lei=issuer_lei,
        declarant_lei=declarant_lei,
        has_prior_notification=has_prior,
        crossed_threshold=derive_crossed_threshold(voting_before, voting_after),
        ambiguous_fields=ambiguous,
        parse_warnings=warnings,
    )
