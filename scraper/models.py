"""Data models for the internal dealing scraper."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ListingRow:
    """A single row from the Borsa Italiana listing page."""
    company_name: str
    filing_date: str   # DD/MM/YYYY as shown on the listing page
    pdf_path: str      # e.g. /nisavvsource/pdf/2026/31839.pdf
    pdf_url: str       # full https:// URL


@dataclass
class ParsedTransaction:
    """
    One transaction extracted from a MAR Art 19 PDF notification.

    Fields map 1:1 to the transactions table columns.  Every field that
    exists in the DB schema must be present here so db.py can pass it
    through without implicit defaults.
    """
    # ── Who ──────────────────────────────────────────────────────────────────
    insider_name: str
    role: str

    # ── What company ─────────────────────────────────────────────────────────
    company_name: str

    # ── Instrument ───────────────────────────────────────────────────────────
    isin: Optional[str]
    instrument_type: str

    # ── Trade details ─────────────────────────────────────────────────────────
    direction: str      # 'buy' | 'sell' | 'unknown'
    transaction_date: str   # YYYY-MM-DD (ISO 8601)
    quantity: float
    unit_price: float
    currency: str
    total_value: float

    # ── Provenance ────────────────────────────────────────────────────────────
    filing_date: str    # DD/MM/YYYY from the listing page
    source_url: str     # direct PDF URL
    raw_hash: str       # SHA-256 for deduplication (redesigned in Phase 4)

    # ── Transaction classification ────────────────────────────────────────────
    # Current values: buy | sell | grant | option_exercise | sell_to_cover | other
    transaction_type: str = "buy"
    # discretionary = active choice; mechanical = automatic/contractual; unclear = unknown
    economic_intent: str = "unclear"

    # ── Insider verification (Phase 4) ────────────────────────────────────────
    # False when the name contains a legal-entity marker (S.p.A., Ltd, etc.)
    insider_verified: bool = True
    role_category: str = "other"   # executive|board|major_shareholder|related_person|other

    # ── Data quality flags ────────────────────────────────────────────────────
    needs_review: bool = False
    # Rule-based confidence scores (0.0–1.0).  Phase 5 adds full calculation.
    extraction_confidence: float = 0.5
    classification_confidence: float = 0.5

    # ── Review workflow ───────────────────────────────────────────────────────
    # pending_review | under_review | confirmed | rejected | corrected
    review_status: str = "confirmed"
    # Structured tag explaining review trigger: e.g. 'ambiguous_direction'
    review_reason: Optional[str] = None

    # ── Source lineage (Phase 2/4 will populate these from the filing ledger) ──
    # 0-indexed position of this transaction within the source PDF
    source_transaction_index: Optional[int] = None
    # SHA-256 hex of the raw PDF bytes at ingestion time
    raw_document_sha256: Optional[str] = None
    # FK to filings table — None until Phase 2 filing ledger ships
    source_filing_id: Optional[int] = None

    # ── Parser metadata ───────────────────────────────────────────────────────
    # Format: '<major>.<minor>.<patch>' — set by PARSER_VERSION constant in parser.py
    parser_version: str = "1.0.0"

    # ── Parser diagnostics (not persisted to DB) ──────────────────────────────
    parse_warnings: list[str] = field(default_factory=list)
