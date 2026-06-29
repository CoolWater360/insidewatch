"""Validation + structured per-record reporting for the ownership pilot.

`validate_record` turns a parsed record plus its resolution results into a
ValidationSummary that records exactly what would be written, what is
unresolved, the proposed natural identity (migration 018), and whether the
record is safe to apply.

A record is safe to apply only when every NOT NULL requirement of
`ownership_events` can be satisfied (issuer_id, event_date, source) — NOT when
it is unambiguous.  Ambiguity never blocks a write; it keeps the row
`parsed_fact` + `pending_review` (never silently upgraded).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import ParsedOwnershipRecord
from .resolver import EntityResolution, IssuerResolution


@dataclass
class ValidationSummary:
    record: ParsedOwnershipRecord
    issuer: IssuerResolution
    declarant: EntityResolution
    vehicles: list[EntityResolution] = field(default_factory=list)

    unresolved: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)
    proposed_identity: str = ""
    confidence: str = "parsed_fact"
    review_status: str = "pending_review"

    @property
    def safe_to_apply(self) -> bool:
        return not self.blocking_reasons


def validate_record(
    record: ParsedOwnershipRecord,
    issuer: IssuerResolution,
    declarant: EntityResolution,
    vehicles: Optional[list[EntityResolution]] = None,
) -> ValidationSummary:
    vehicles = vehicles or []
    summary = ValidationSummary(
        record=record,
        issuer=issuer,
        declarant=declarant,
        vehicles=vehicles,
    )

    # ── Unresolved tracking (informational; does NOT block on its own) ─────────
    if issuer.issuer_id is None:
        summary.unresolved.append(f"issuer ({issuer.method})")
    if declarant.entity_id is None:
        summary.unresolved.append(f"declarant entity ({declarant.method})")
    for v in vehicles:
        if v.entity_id is None:
            summary.unresolved.append(f"vehicle entity {v.legal_name!r} ({v.method})")
    summary.unresolved.extend(record.ambiguous_fields)
    if record.voting_pct_before is None and record.has_prior_notification:
        summary.unresolved.append("voting_pct_before (prior exists, value not parsed)")

    # ── Blocking reasons (cannot satisfy ownership_events NOT NULL columns) ────
    if issuer.issuer_id is None:
        summary.blocking_reasons.append(
            "issuer_id unresolved — ownership_events.issuer_id is NOT NULL"
        )
    if not record.event_date:
        summary.blocking_reasons.append(
            "event_date missing — ownership_events.event_date is NOT NULL"
        )
    if not record.source_url:
        summary.blocking_reasons.append("source_url missing")
    if record.event_type is None:
        summary.blocking_reasons.append("event_type missing")
    if record.voting_pct_after is None:
        # Not a NOT NULL column, but a record with no after-% carries no signal.
        summary.blocking_reasons.append("voting_pct_after missing — no usable signal")

    # ── Proposed natural identity (migration 018) ──────────────────────────────
    iid = issuer.issuer_id if issuer.issuer_id is not None else "?"
    if declarant.entity_id is not None:
        summary.proposed_identity = (
            f"(issuer_id={iid}, entity_id={declarant.entity_id}, "
            f"event_date={record.event_date}, event_type={record.event_type})"
        )
    else:
        summary.proposed_identity = (
            f"(issuer_id={iid}, raw_entity_name={record.declarant_raw_name!r}, "
            f"event_date={record.event_date}, event_type={record.event_type}) "
            "[entity unresolved — DB unique index not enforceable; "
            "collector dedups on raw_entity_name]"
        )

    # Confidence is parsed_fact for parser-extracted fields; never auto-upgraded.
    summary.confidence = "parsed_fact"
    summary.review_status = "pending_review"
    return summary


def render_summary(summary: ValidationSummary, index: Optional[int] = None) -> str:
    """Render the required structured per-record validation summary."""
    r = summary.record
    lines: list[str] = []
    head = f"── Record {index} " if index is not None else "── Record "
    lines.append(head + "─" * max(0, 64 - len(head)))
    lines.append(f"  source URL            : {r.source_url}")
    lines.append(f"  source format         : {r.source_format}")
    lines.append(
        f"  issuer raw name       : {r.issuer_raw_name!r}"
    )
    lines.append(
        f"  resolved issuer       : "
        f"{summary.issuer.issuer_id} (method={summary.issuer.method})"
    )
    lines.append(f"  holder/entity raw     : {r.declarant_raw_name!r}")
    lines.append(
        f"  resolved declarant    : "
        f"{summary.declarant.entity_id} (method={summary.declarant.method})"
    )
    if r.holding_vehicles:
        vnames = ", ".join(
            f"{v.raw_name!r}" + (f" ({v.pct}%)" if v.pct is not None else "")
            for v in r.holding_vehicles
        )
        lines.append(f"  named holding vehicle : {vnames}")
    else:
        lines.append("  named holding vehicle : (none stated)")
    lines.append(f"  event type            : {r.event_type}")
    lines.append(f"  effective/event date  : {r.event_date}")
    lines.append(f"  publication date      : {r.publication_date}")
    lines.append(
        f"  threshold/percentage  : after={r.voting_pct_after}%  "
        f"before={r.voting_pct_before}%  "
        f"crossed≈{r.crossed_threshold}%"
    )
    lines.append(
        f"  voting-right fields   : voting_after={r.voting_pct_after}  "
        f"voting_before={r.voting_pct_before}  "
        f"(stake economic: {r.stake_pct_after}/{r.stake_pct_before} — not separately disclosed)"
    )
    lines.append(f"  direct/indirect       : {r.direct_or_indirect}")
    # Explicit relationship evidence (declarant -> named vehicle = 'controls')
    rels = [v.raw_name for v in r.holding_vehicles if v.raw_name]
    if rels:
        lines.append(
            "  explicit relationship : declarant 'controls' "
            + ", ".join(repr(x) for x in rels)
            + "  (società controllata dal dichiarante)"
        )
    else:
        lines.append("  explicit relationship : (none stated as a named controlled vehicle)")
    lines.append(
        f"  unresolved fields     : "
        f"{'; '.join(summary.unresolved) if summary.unresolved else '(none)'}"
    )
    if r.parse_warnings:
        lines.append(f"  parse warnings        : {'; '.join(r.parse_warnings)}")
    lines.append(f"  document hash         : {r.document_hash or '(computed at fetch)'}")
    lines.append(f"  proposed identity     : {summary.proposed_identity}")
    lines.append(f"  confidence/review     : {summary.confidence} / {summary.review_status}")
    safe = "YES" if summary.safe_to_apply else "NO"
    lines.append(f"  SAFE TO APPLY         : {safe}")
    if summary.blocking_reasons:
        for br in summary.blocking_reasons:
            lines.append(f"      ✗ blocked: {br}")
    return "\n".join(lines)
