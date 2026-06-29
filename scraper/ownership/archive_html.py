"""Adapter for pre-2026-06-15 CONSOB dated-archive HTML notices.

These pages publish, for a single publication date, two tables:

  PARTECIPAZIONI IN AZIONI
    columns: DATA OPERAZIONE | DICHIARANTE | SOCIETA' PARTECIPATA |
             PERCENTUALE SUI DIRITTI DI VOTO E TITOLO DI POSSESSO |
             SOCIETA' CONTROLLATA ... (named holding vehicles) |
             COMUNICAZIONE PRECEDENTE

  PARTECIPAZIONI IN STRUMENTI FINANZIARI E AGGREGATE
    columns: DATA OPERAZIONE | DICHIARANTE | SOCIETA' PARTECIPATA |
             PERCENTUALE ... E MODALITA' DI POSSESSO |
             <3 numeric DETTAGLI columns, NO named vehicle> |
             COMUNICAZIONE PRECEDENTE (Aggregata (A) / strumenti (B))

The two tables are NOT interchangeable: in the aggregate table column 5+ is a
numeric breakdown, so no structured holding-vehicle name is extracted.

This adapter never enumerates or follows links.  It parses exactly the one
page it is handed and returns records for the requested issuer only.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from .models import (
    SOURCE_FORMAT_ARCHIVE_HTML,
    HoldingVehicle,
    ParsedOwnershipRecord,
    all_pcts,
    classify_event_type,
    derive_crossed_threshold,
    detect_direct_indirect,
    parse_italian_date,
    parse_pct,
)

logger = logging.getLogger(__name__)

_DATE_CELL_RE = re.compile(r"^\s*\d{2}/\d{2}/\d{4}")
_AB_SPLIT_RE = re.compile(r"\b[ab]\)", re.IGNORECASE)


def _norm(text: str) -> str:
    """Collapse runs of whitespace to single spaces and strip."""
    return re.sub(r"\s+", " ", text or "").strip()


def _table_kind(header_text: str) -> Optional[str]:
    """Classify a table by its header text. Returns 'azioni' | 'aggregate' | None."""
    up = header_text.upper()
    if "DETTAGLI DELLA PARTECIPAZIONE" in up or "MODALITA' DI POSSESSO" in up:
        return "aggregate"
    if "TITOLO DI POSSESSO" in up or "SOCIETA'CONTROLLATA" in up or "SOCIETA' CONTROLLATA" in up:
        return "azioni"
    return None


def _parse_vehicles(cell_text: str) -> list[HoldingVehicle]:
    """Parse the 'società controllata' cell of an AZIONI row into named vehicles.

    Format: '** 3.068% GOLDMAN SACHS INTERNATIONAL ** 0.001% GOLDMAN SACHS BANK ...'
    """
    text = _norm(cell_text)
    if not text:
        return []
    vehicles: list[HoldingVehicle] = []
    chunks = [c.strip() for c in text.split("**") if c.strip()]
    for chunk in chunks:
        m = re.match(r"^(\d+(?:[.,]\d+)?)\s*%\s*(.+)$", chunk)
        if m:
            pct = float(m.group(1).replace(",", "."))
            name = _norm(m.group(2))
            if name:
                vehicles.append(HoldingVehicle(raw_name=name, pct=pct))
        else:
            # No leading percentage — keep the raw name only.
            vehicles.append(HoldingVehicle(raw_name=_norm(chunk), pct=None))
    return vehicles


def _parse_prior(cell_text: str) -> tuple[bool, Optional[float], bool]:
    """Interpret the COMUNICAZIONE PRECEDENTE cell.

    Returns (has_prior, before_pct, ambiguous).
      * empty cell                      -> (False, None, False)  [initial disclosure]
      * 'DD/MM/YYYY 6.347%'             -> (True, 6.347, False)
      * 'DD/MM/YYYY a)6.520% b)0.173%'  -> (True, None, True)    [ambiguous split]
      * date only, no percentage        -> (True, None, False)
    """
    text = _norm(cell_text)
    if not text:
        return False, None, False
    pcts = all_pcts(text)
    if _AB_SPLIT_RE.search(text) or len(pcts) > 1:
        return True, None, True
    if len(pcts) == 1:
        return True, pcts[0], False
    return True, None, False


def _is_data_row(cells: list[str], tds) -> bool:
    """A data row has a date in cell 0 and no colspan footnote cell."""
    if not cells:
        return False
    if not _DATE_CELL_RE.match(cells[0]):
        return False
    # Footnote / continuation rows use colspan.
    for td in tds:
        if td.has_attr("colspan"):
            return False
    return True


def _build_record(
    *,
    kind: str,
    cells: list[str],
    raw_tds,
    source_url: str,
    publication_date: Optional[str],
) -> ParsedOwnershipRecord:
    date_cell = cells[0]
    declarant = cells[1] if len(cells) > 1 else ""
    issuer = cells[2] if len(cells) > 2 else ""
    pct_cell = cells[3] if len(cells) > 3 else ""
    prior_cell = cells[-1] if len(cells) > 4 else ""

    vehicles: list[HoldingVehicle] = []
    if kind == "azioni" and len(cells) >= 6:
        vehicles = _parse_vehicles(cells[4])
    # Aggregate rows expose only numeric DETTAGLI columns — no named vehicle.

    voting_after = parse_pct(pct_cell)
    direct_indirect = detect_direct_indirect(pct_cell)
    has_prior, voting_before, ambiguous = _parse_prior(prior_cell)
    event_date = parse_italian_date(date_cell)

    ambiguous_fields: list[str] = []
    if ambiguous:
        ambiguous_fields.append("voting_pct_before")
    if kind == "aggregate":
        ambiguous_fields.append("holding_vehicle (aggregate breakdown, not named)")
    if kind == "azioni" and len(vehicles) > 1:
        ambiguous_fields.append("holding_vehicle (multiple named vehicles)")

    event_type, notes = classify_event_type(
        pct_before=voting_before,
        pct_after=voting_after,
        has_prior_notification=has_prior,
        ambiguous_before=ambiguous,
        raw_percent_text=pct_cell,
    )

    evidence = " | ".join(c for c in cells if c)

    rec = ParsedOwnershipRecord(
        source_url=source_url,
        source_format=SOURCE_FORMAT_ARCHIVE_HTML,
        publication_date=publication_date,
        raw_text=evidence,
        issuer_raw_name=issuer,
        declarant_raw_name=declarant,
        holding_vehicles=vehicles,
        event_date=event_date,
        voting_pct_after=voting_after,
        voting_pct_before=voting_before,
        direct_or_indirect=direct_indirect,
        event_type=event_type,
        has_prior_notification=has_prior,
        crossed_threshold=derive_crossed_threshold(voting_before, voting_after),
        ambiguous_fields=ambiguous_fields,
        parse_warnings=list(notes),
    )
    return rec


def parse_archive_page(
    html: str,
    *,
    source_url: str,
    publication_date: Optional[str] = None,
    issuer_filter: Optional[str] = None,
) -> list[ParsedOwnershipRecord]:
    """Parse a single CONSOB dated-archive page into ownership records.

    Args:
        html: the raw page HTML (already fetched — this adapter never fetches).
        source_url: canonical URL of the page (stored as provenance).
        publication_date: ISO date of the page; if None, derived from the URL.
        issuer_filter: if given, only rows whose issuer name contains this
            string (case-insensitive) are returned.  Used to honour the
            "explicit input only" rule: callers name the issuer they supplied.

    Returns:
        A list of ParsedOwnershipRecord (possibly empty).
    """
    if publication_date is None:
        publication_date = parse_italian_date(source_url) or _date_from_slug(source_url)

    soup = BeautifulSoup(html, "html.parser")
    needle = (issuer_filter or "").strip().upper()
    records: list[ParsedOwnershipRecord] = []

    for table in soup.find_all("table"):
        header_text = _norm(table.get_text(" "))
        kind = _table_kind(header_text)
        if kind is None:
            continue
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue
            cells = [_norm(td.get_text(" ")) for td in tds]
            if not _is_data_row(cells, tds):
                continue
            issuer = cells[2] if len(cells) > 2 else ""
            if needle and needle not in issuer.upper():
                continue
            rec = _build_record(
                kind=kind,
                cells=cells,
                raw_tds=tds,
                source_url=source_url,
                publication_date=publication_date,
            )
            records.append(rec)

    logger.info(
        "archive_html: parsed %d record(s) from %s (filter=%r)",
        len(records), source_url, issuer_filter,
    )
    return records


def _date_from_slug(url: str) -> Optional[str]:
    """Extract YYYY-MM-DD from a '...-partecipazioni-rilevanti-YYYY-MM-DD' slug."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", url or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None
