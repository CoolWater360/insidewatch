"""Internal review workflow for the ownership pilot (Phase 17B.5).

Read-only by default.  Lists the pending-review entities, ownership events, and
entity relationships created by the pilot, and produces *recommendations* for
entity-type corrections where a heuristic clearly mislabeled an organisation as
a natural person.

Design principles:
  * Recommendations are SUGGESTIONS, never facts.  Nothing is reclassified
    automatically.  An explicit operator command (`--approve-entity-type`)
    applies a single correction, one id at a time.
  * Pure logic (recommendation, report rendering) is separated from DB access
    so it can be unit-tested without a database.
  * No new collection, no UI, no public views, no RLS changes.

Usage:
    # List pending records + entity-type recommendations (read-only)
    python3 -m scraper.ownership.review

    # Apply ONE operator-approved entity-type correction (explicit; not automatic)
    python3 -m scraper.ownership.review --approve-entity-type 3 --to company
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# entities.entity_type CHECK vocabulary (migration 016).
ENTITY_TYPES = (
    "natural_person", "company", "holding_company", "trust",
    "fiduciary", "foundation", "fund", "nominee", "other",
)

# Tokens that clearly indicate an ORGANISATION, used only to flag entities that
# the ingestion suffix-heuristic tagged 'natural_person' for operator review.
# These never auto-mutate data — they only produce a recommendation + reason.
ORG_NAME_MARKERS = (
    "BANK", "BANCA", "GROUP", "HOLDING", "CAPITAL", "PARTNERS", "SECURITIES",
    "INVESTMENT", "ASSET MANAGEMENT", "MANAGEMENT", "ADVISORS", "ADVISERS",
    "INTERNATIONAL", "GLOBAL", "FUND", "FUNDS", "SICAV", "SGR", "TRUST",
    "ASSICURAZIONI", "INSURANCE", "FINANCIAL", "FINANCE", "GOLDMAN SACHS",
    "MORGAN STANLEY", "BLACKROCK", "JPMORGAN", "BARCLAYS", "VANGUARD",
    "& CO", "AND CO", "CO.", "COMPANY", "CORP", "INCORPORATED",
)


@dataclass
class EntityTypeRecommendation:
    entity_id: int
    legal_name: str
    current_type: str
    proposed_type: str
    reason: str
    requires_operator_approval: bool = True


# ── Pure recommendation logic (no DB) ──────────────────────────────────────────

def recommend_entity_type(
    legal_name: str, current_type: str
) -> Optional[tuple[str, str]]:
    """Return (proposed_type, reason) if the entity is mislabeled, else None.

    Only fires when an entity tagged 'natural_person' contains an explicit
    organisation indicator in its legal name.  Conservative: it proposes the
    generic 'company' type and asks the operator to confirm the exact form
    (holding_company / fund / etc.).  It never proposes downgrading a
    non-person type to natural_person.
    """
    if current_type != "natural_person":
        return None
    up = f" {(legal_name or '').upper()} "
    for marker in ORG_NAME_MARKERS:
        if marker in up:
            return (
                "company",
                f"legal name contains organisation indicator '{marker.strip()}' "
                f"but is tagged 'natural_person' (ingestion suffix-heuristic miss); "
                f"operator to confirm exact organisation type",
            )
    return None


def build_entity_recommendations(
    entities: list[dict],
) -> list[EntityTypeRecommendation]:
    """Build entity-type recommendations for a list of entity rows (pure)."""
    recs: list[EntityTypeRecommendation] = []
    for e in entities:
        rec = recommend_entity_type(e.get("legal_name", ""), e.get("entity_type", ""))
        if rec:
            proposed, reason = rec
            recs.append(EntityTypeRecommendation(
                entity_id=e["id"],
                legal_name=e.get("legal_name", ""),
                current_type=e.get("entity_type", ""),
                proposed_type=proposed,
                reason=reason,
            ))
    return recs


def render_report(
    entities: list[dict],
    events: list[dict],
    relationships: list[dict],
    recommendations: list[EntityTypeRecommendation],
) -> str:
    """Render a concise text review report (pure)."""
    lines: list[str] = []
    lines.append("OWNERSHIP PILOT — INTERNAL REVIEW")
    lines.append("=" * 60)

    lines.append(f"\nEntities pending review: {len(entities)}")
    for e in entities:
        lines.append(
            f"  id={e['id']:<3} type={e.get('entity_type',''):<15} "
            f"{e.get('legal_name','')!r}"
        )

    lines.append(f"\nOwnership events pending review: {len(events)}")
    for ev in events:
        lines.append(
            f"  id={ev['id']:<3} issuer_id={ev.get('issuer_id')} "
            f"declarant={ev.get('raw_entity_name','')!r} "
            f"event_type={ev.get('event_type')} "
            f"voting_after={ev.get('voting_pct_after')}"
        )

    lines.append(f"\nRelationships pending review: {len(relationships)}")
    for r in relationships:
        lines.append(
            f"  id={r['id']:<3} subject={r.get('subject_entity_id')} "
            f"-{r.get('relationship_type')}-> "
            f"object_entity={r.get('object_entity_id')} "
            f"object_issuer={r.get('object_issuer_id')}"
        )

    lines.append(f"\nEntity-type RECOMMENDATIONS (require operator approval): "
                 f"{len(recommendations)}")
    if not recommendations:
        lines.append("  (none)")
    for rec in recommendations:
        lines.append(
            f"  entity id={rec.entity_id} {rec.legal_name!r}: "
            f"{rec.current_type} -> {rec.proposed_type}"
        )
        lines.append(f"      reason: {rec.reason}")
        lines.append(
            f"      apply with: python3 -m scraper.ownership.review "
            f"--approve-entity-type {rec.entity_id} --to {rec.proposed_type}"
        )
    return "\n".join(lines)


# ── DB access (read-only listing) ──────────────────────────────────────────────

def get_pending_entities(client: Any) -> list[dict]:
    return (
        client.table("entities").select("*")
        .eq("review_status", "pending_review").order("id").execute().data or []
    )


def get_pending_ownership_events(client: Any) -> list[dict]:
    return (
        client.table("ownership_events").select("*")
        .eq("review_status", "pending_review").eq("is_current", True)
        .order("id").execute().data or []
    )


def get_pending_relationships(client: Any) -> list[dict]:
    return (
        client.table("entity_relationships").select("*")
        .eq("review_status", "pending_review").eq("is_current", True)
        .order("id").execute().data or []
    )


# ── Operator-approved correction (explicit; one id at a time) ──────────────────

def approve_entity_type(
    client: Any, entity_id: int, new_type: str, *, approved_by: str = "operator"
) -> bool:
    """Apply ONE operator-approved entity-type correction.

    This is the only write path and is invoked explicitly by an operator via
    the CLI.  It updates entity_type and marks the entity review_status as
    'confirmed' (the operator has reviewed it).  Returns True on success.
    """
    if new_type not in ENTITY_TYPES:
        raise ValueError(f"invalid entity_type {new_type!r}; must be one of {ENTITY_TYPES}")
    now = datetime.now(timezone.utc).isoformat()
    result = (
        client.table("entities")
        .update({
            "entity_type": new_type,
            "review_status": "confirmed",
            "updated_at": now,
        })
        .eq("id", entity_id)
        .execute()
    )
    if result.data:
        logger.info(
            "entity %d: entity_type -> %r, review_status -> confirmed (by %s)",
            entity_id, new_type, approved_by,
        )
        return True
    logger.warning("entity %d not found — no change", entity_id)
    return False


# ── CLI ────────────────────────────────────────────────────────────────────────

def _get_client():
    from supabase import create_client
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.", file=sys.stderr)
        sys.exit(1)
    return create_client(url, key)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ownership pilot internal review (read-only by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--approve-entity-type", type=int, metavar="ID",
                        help="Apply an operator-approved entity-type correction to entity ID")
    parser.add_argument("--to", choices=ENTITY_TYPES, metavar="TYPE",
                        help="New entity_type (used with --approve-entity-type)")
    parser.add_argument("--approved-by", default="operator")
    args = parser.parse_args(argv)

    client = _get_client()

    if args.approve_entity_type is not None:
        if not args.to:
            print("ERROR: --approve-entity-type requires --to <type>", file=sys.stderr)
            return 2
        ok = approve_entity_type(client, args.approve_entity_type, args.to,
                                 approved_by=args.approved_by)
        return 0 if ok else 1

    entities = get_pending_entities(client)
    events = get_pending_ownership_events(client)
    relationships = get_pending_relationships(client)
    recommendations = build_entity_recommendations(entities)
    print(render_report(entities, events, relationships, recommendations))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
