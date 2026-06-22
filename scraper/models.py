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
    """One transaction extracted from a MAR Art 19 PDF notification."""
    # Who
    insider_name: str
    role: str
    # What company
    company_name: str
    # Instrument
    isin: Optional[str]
    instrument_type: str
    # Trade details
    direction: str      # 'buy' | 'sell' | 'unknown'
    transaction_date: str   # YYYY-MM-DD from the PDF (ISO 8601)
    quantity: float
    unit_price: float
    currency: str
    total_value: float
    # Provenance
    filing_date: str    # DD/MM/YYYY from the listing page
    source_url: str     # direct PDF URL
    raw_hash: str       # SHA-256 for deduplication
    # Insider verification (Phase 4)
    insider_verified: bool = True    # False if entity name or non-PDMR role detected
    role_category: str = "other"     # executive | board | major_shareholder | related_person | other
    # Transaction classification (Phase 1)
    transaction_type: str = "buy"   # buy | sell | grant | option_exercise | sell_to_cover | other
    needs_review: bool = False       # True when direction/type could not be determined
    # Parser quality
    parse_warnings: list[str] = field(default_factory=list)
