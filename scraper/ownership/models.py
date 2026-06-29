"""Data models and shared classification logic for the ownership pilot.

These models map onto the Phase 17A schema (migration 018 `ownership_events`,
migration 016 `context_sources` / `entities`).  The `event_type` vocabulary is
the migration-018 CHECK constraint vocabulary — NOT the design-doc vocabulary
(see docs/ownership-source-assessment.md §1.3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ── Source formats (kept distinct; adapters are never interchangeable) ──────────
SOURCE_FORMAT_ARCHIVE_HTML = "archive_html"  # pre-2026-06-15 dated archive page
SOURCE_FORMAT_TR1_PDF = "tr1_pdf"            # post-2026-06-15 ESMA TR-1/MJSHLD PDF

# ── Italian Art. 120 TUF voting-rights disclosure thresholds (percent) ─────────
# Used only to DERIVE an informational "crossed threshold" value; never to
# invent data.  PMI-specific lower thresholds are intentionally omitted from the
# pilot to avoid mis-classifying small-cap issuers.
CONSOB_THRESHOLDS = (3.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 50.0, 66.667, 90.0)

# ── ownership_events.event_type CHECK vocabulary (migration 018) ───────────────
OWNERSHIP_EVENT_TYPES = frozenset({
    "threshold_crossing_up",
    "threshold_crossing_down",
    "initial_disclosure",
    "cancellation",
    "change_in_nature",
    "pledge",
    "pledge_release",
    "other",
})


@dataclass
class HoldingVehicle:
    """A holding vehicle explicitly NAMED in the source (società controllata)."""
    raw_name: str
    pct: Optional[float] = None


@dataclass
class ParsedOwnershipRecord:
    """One ownership notification parsed from an official CONSOB source.

    Raw party names are ALWAYS preserved, independent of any later resolution.
    Percentage fields are CONSOB voting-rights percentages
    ("percentuale sui diritti di voto").  Economic (stake) percentages are
    populated only where the source states them separately — for Art. 120
    notifications they are not, so stake_pct_* stay None.
    """

    # ── Provenance ─────────────────────────────────────────────────────────────
    source_url: str
    source_format: str                       # SOURCE_FORMAT_*
    publication_date: Optional[str] = None   # YYYY-MM-DD (page / notification date)
    document_hash: Optional[str] = None      # SHA-256 hex of raw source bytes
    raw_text: Optional[str] = None           # INTERNAL verbatim evidence text

    # ── Parties (raw, always preserved) ────────────────────────────────────────
    issuer_raw_name: str = ""
    declarant_raw_name: str = ""
    holding_vehicles: list[HoldingVehicle] = field(default_factory=list)

    # ── Event ──────────────────────────────────────────────────────────────────
    event_date: Optional[str] = None         # YYYY-MM-DD (data operazione)
    voting_pct_after: Optional[float] = None
    voting_pct_before: Optional[float] = None
    stake_pct_after: Optional[float] = None  # economic; only where explicit
    stake_pct_before: Optional[float] = None
    direct_or_indirect: Optional[str] = None  # 'direct'|'indirect'|'both'|'unknown'
    event_type: Optional[str] = None          # migration-018 vocabulary

    # ── Identifiers (only where explicitly present in the source) ──────────────
    issuer_isin: Optional[str] = None         # present in TR-1, absent in archive HTML
    issuer_lei: Optional[str] = None
    declarant_lei: Optional[str] = None

    # ── Derived / bookkeeping ──────────────────────────────────────────────────
    crossed_threshold: Optional[float] = None  # informational only
    has_prior_notification: bool = False
    ambiguous_fields: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)

    @property
    def named_vehicle_count(self) -> int:
        return sum(1 for v in self.holding_vehicles if v.raw_name)

    @property
    def single_named_vehicle(self) -> Optional[str]:
        named = [v.raw_name for v in self.holding_vehicles if v.raw_name]
        return named[0] if len(named) == 1 else None

    @property
    def joined_vehicle_names(self) -> Optional[str]:
        named = [v.raw_name for v in self.holding_vehicles if v.raw_name]
        return "; ".join(named) if named else None


# ── Shared parsing helpers ─────────────────────────────────────────────────────

_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")


def parse_italian_date(text: str) -> Optional[str]:
    """Return the first DD/MM/YYYY in `text` as ISO YYYY-MM-DD, else None."""
    if not text:
        return None
    m = _DATE_RE.search(text)
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    return f"{yyyy}-{mm}-{dd}"


def parse_pct(text: str) -> Optional[float]:
    """Return the first percentage in `text` as a float, else None.

    'ZERO' (used by CONSOB for a fully-disposed holding) maps to 0.0.
    """
    if text is None:
        return None
    if "ZERO" in text.upper() and not _PCT_RE.search(text):
        return 0.0
    m = _PCT_RE.search(text)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def all_pcts(text: str) -> list[float]:
    """Return all percentages in `text` as floats (order preserved)."""
    if not text:
        return []
    return [float(x.replace(",", ".")) for x in _PCT_RE.findall(text)]


def detect_direct_indirect(text: str) -> Optional[str]:
    """Map CONSOB 'DIRETTA'/'INDIRETTA' wording to the schema vocabulary.

    Returns None when neither is stated (so the field stays unresolved rather
    than being guessed).
    """
    if not text:
        return None
    up = text.upper()
    has_dir = "DIRETTA" in up
    has_ind = "INDIRETTA" in up
    # 'INDIRETTA' contains 'DIRETTA' as a substring; test indirect first.
    if has_ind and "DIRETTA E INDIRETTA" in up:
        return "both"
    if has_ind:
        return "indirect"
    if has_dir:
        return "direct"
    return None


def derive_crossed_threshold(
    pct_before: Optional[float],
    pct_after: Optional[float],
) -> Optional[float]:
    """Return the regulatory threshold crossed between before and after, if any.

    Informational only.  For an initial disclosure (no before), returns the
    highest threshold at or below `pct_after`.
    """
    if pct_after is None:
        return None
    if pct_before is None:
        candidates = [t for t in CONSOB_THRESHOLDS if t <= pct_after + 1e-9]
        return max(candidates) if candidates else None
    lo, hi = sorted((pct_before, pct_after))
    crossed = [t for t in CONSOB_THRESHOLDS if lo + 1e-9 < t <= hi + 1e-9]
    if not crossed:
        return None
    # Return the threshold nearest the 'after' side (the one that triggered it).
    return max(crossed) if pct_after >= pct_before else min(crossed)


def classify_event_type(
    *,
    pct_before: Optional[float],
    pct_after: Optional[float],
    has_prior_notification: bool,
    ambiguous_before: bool = False,
    raw_percent_text: str = "",
) -> tuple[str, list[str]]:
    """Classify an ownership event into the migration-018 vocabulary.

    Conservative by design: when direction cannot be established from the
    source it returns 'other' with a note, never a guessed crossing.

    Returns (event_type, notes).
    """
    notes: list[str] = []
    t = (raw_percent_text or "").lower()

    # Pledge wording takes precedence (explicit in the source text).
    if "pegno" in t or "pledge" in t:
        return "pledge", notes
    if "svincol" in t:  # svincolo del pegno = pledge release
        return "pledge_release", notes

    if pct_after is None:
        return "other", ["voting_pct_after missing — direction undetermined"]

    if ambiguous_before:
        return "other", [
            "prior notification present but before% is ambiguous "
            "(a)/b) split or multiple values) — direction undetermined"
        ]

    if not has_prior_notification and pct_before is None:
        return "initial_disclosure", notes

    if pct_before is None:
        return "other", [
            "prior notification present but before% not parseable — "
            "direction undetermined"
        ]

    crossed = derive_crossed_threshold(pct_before, pct_after)
    if pct_after > pct_before:
        if crossed is not None:
            return "threshold_crossing_up", notes
        return "other", [
            f"increase {pct_before}->{pct_after}% crosses no regulatory "
            "threshold — not a threshold crossing"
        ]
    if pct_after < pct_before:
        if crossed is not None:
            return "threshold_crossing_down", notes
        return "other", [
            f"decrease {pct_before}->{pct_after}% crosses no regulatory "
            "threshold — not a threshold crossing"
        ]
    return "other", [f"no change in voting % ({pct_after}%) — nature change only"]
