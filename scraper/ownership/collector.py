"""Ownership pilot collector — explicit-URL, dry-run-first orchestrator.

Boundaries enforced here:
  * EXPLICIT URL input only — a fixed PILOT_RECORDS list (the operator-supplied
    URLs) or a single --url.  No discovery, pagination, enumeration, or search.
  * 3-second minimum delay between official-source HTTP requests.
  * Descriptive User-Agent.
  * Dry-run by DEFAULT.  Writes require --apply.
  * Idempotent writes via context_db helpers + natural-identity dedup.
  * Never sets/exposes storage_path or private document locations.

Usage:
  # dry-run all three supplied pilot records (no writes):
  python -m scraper.ownership.collector

  # dry-run a single explicit URL:
  python -m scraper.ownership.collector --url <URL> --format archive_html \\
      --issuer "ITALMOBILIARE" --expected-pct 3.001

  # apply (writes) — only after operator approves the dry-run:
  python -m scraper.ownership.collector --apply
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .archive_html import parse_archive_page
from .models import (
    SOURCE_FORMAT_ARCHIVE_HTML,
    SOURCE_FORMAT_TR1_PDF,
    ParsedOwnershipRecord,
)
from .resolver import (
    EntityResolution,
    find_or_create_entity,
    resolve_issuer_exact,
)
from .tr1_pdf import extract_text_from_pdf, parse_tr1
from .validate import ValidationSummary, render_summary, validate_record

logger = logging.getLogger(__name__)

USER_AGENT = (
    "InsideWatch-OwnershipPilot/0.1 "
    "(+research/market-integrity; contact alessandrotudorconsorti@gmail.com)"
)
MIN_REQUEST_DELAY_S = 3.0
PARSER_VERSION = "ownership-pilot-0.1.0"

_last_request_ts: float = 0.0


# ── Explicit pilot records (operator-supplied; do NOT add to automatically) ────

@dataclass
class PilotSpec:
    url: str
    source_format: str
    issuer_filter: str
    expected_pct: Optional[float] = None
    label: str = ""


PILOT_RECORDS: list[PilotSpec] = [
    PilotSpec(
        url="https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-2026-05-06",
        source_format=SOURCE_FORMAT_ARCHIVE_HTML,
        issuer_filter="BRUNELLO CUCINELLI",
        expected_pct=5.588,
        label="Brunello Cucinelli (FMR LLC, aggregate)",
    ),
    PilotSpec(
        url="https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-2025-09-23",
        source_format=SOURCE_FORMAT_ARCHIVE_HTML,
        issuer_filter="MEDIOBANCA",
        expected_pct=3.069,
        label="Mediobanca (Goldman Sachs, azioni)",
    ),
    PilotSpec(
        url="https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-2026-06-03",
        source_format=SOURCE_FORMAT_ARCHIVE_HTML,
        issuer_filter="ITALMOBILIARE",
        expected_pct=3.001,
        label="Italmobiliare (Morgan Stanley, azioni)",
    ),
]


# ── Fetching (rate-limited, descriptive UA) ────────────────────────────────────

def _rate_limit() -> None:
    """Block until at least MIN_REQUEST_DELAY_S has elapsed since the last fetch."""
    global _last_request_ts
    now = time.monotonic()
    wait = MIN_REQUEST_DELAY_S - (now - _last_request_ts)
    if wait > 0:
        logger.debug("rate-limit: sleeping %.2fs", wait)
        time.sleep(wait)
    _last_request_ts = time.monotonic()


def http_fetch(url: str) -> bytes:
    """Fetch one URL with the descriptive UA and a 30s timeout. Rate-limited."""
    import requests

    _rate_limit()
    logger.info("fetch: %s", url)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.content


Fetcher = Callable[[str], bytes]


# ── Parsing (dispatch to the correct, separate adapter) ────────────────────────

def parse_source(spec: PilotSpec, raw_bytes: bytes) -> list[ParsedOwnershipRecord]:
    """Dispatch to the format-specific adapter. Formats are NOT interchangeable."""
    document_hash = hashlib.sha256(raw_bytes).hexdigest()

    if spec.source_format == SOURCE_FORMAT_ARCHIVE_HTML:
        html = raw_bytes.decode("utf-8", errors="replace")
        records = parse_archive_page(
            html, source_url=spec.url, issuer_filter=spec.issuer_filter
        )
    elif spec.source_format == SOURCE_FORMAT_TR1_PDF:
        text = extract_text_from_pdf(raw_bytes)
        rec = parse_tr1(text, source_url=spec.url, document_hash=document_hash)
        records = [rec]
        if spec.issuer_filter and spec.issuer_filter.upper() not in rec.issuer_raw_name.upper():
            records = []
    else:  # pragma: no cover - guarded by argparse choices
        raise ValueError(f"unknown source_format: {spec.source_format!r}")

    for r in records:
        r.document_hash = document_hash
    return records


def select_target(
    records: list[ParsedOwnershipRecord], spec: PilotSpec
) -> list[ParsedOwnershipRecord]:
    """Keep only records matching the operator-stated expected percentage.

    This is the safety check that we ingest exactly the supplied notification,
    not every row that mentions the issuer.
    """
    if spec.expected_pct is None:
        return records
    out = [
        r for r in records
        if r.voting_pct_after is not None
        and abs(r.voting_pct_after - spec.expected_pct) < 0.0005
    ]
    return out


# ── Per-record processing (resolve + validate; optionally apply) ───────────────

def process_record(
    spec: PilotSpec,
    *,
    client: Any,
    apply: bool,
    fetcher: Fetcher = http_fetch,
) -> list[ValidationSummary]:
    raw_bytes = fetcher(spec.url)
    parsed = parse_source(spec, raw_bytes)
    targets = select_target(parsed, spec)

    summaries: list[ValidationSummary] = []
    for rec in targets:
        issuer = resolve_issuer_exact(client, rec.issuer_raw_name, rec.issuer_isin)
        declarant = find_or_create_entity(client, rec.declarant_raw_name, apply=apply)
        vehicles = [
            find_or_create_entity(client, v.raw_name, apply=apply)
            for v in rec.holding_vehicles if v.raw_name
        ]
        summary = validate_record(rec, issuer, declarant, vehicles)
        summaries.append(summary)

        if apply and summary.safe_to_apply:
            _write_record(client, summary)
    return summaries


# ── Writes (idempotent; service-role client) ───────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_existing_event(
    client: Any,
    *,
    issuer_id: int,
    event_date: str,
    event_type: str,
    entity_id: Optional[int],
    raw_entity_name: str,
) -> Optional[dict]:
    """Return the current ownership_events row matching natural identity, if any."""
    q = (
        client.table("ownership_events").select("*")
        .eq("issuer_id", issuer_id)
        .eq("event_date", event_date)
        .eq("event_type", event_type)
        .eq("is_current", True)
    )
    if entity_id is not None:
        q = q.eq("entity_id", entity_id)
    else:
        # Entity unresolved: dedup on the preserved raw name so re-runs are
        # still idempotent (the DB unique index cannot apply with entity_id NULL).
        q = q.eq("raw_entity_name", raw_entity_name)
    res = q.execute()
    return res.data[0] if res.data else None


def _write_record(client: Any, summary: ValidationSummary) -> dict:
    """Apply one validated record idempotently. Returns an outcome dict."""
    from ..context_db import (
        assert_source_provenance,
        supersede_context_event,
        upsert_context_source,
    )

    r = summary.record
    issuer_id = summary.issuer.issuer_id
    assert issuer_id is not None  # guaranteed by safe_to_apply

    pub_ts = f"{r.publication_date}T00:00:00+00:00" if r.publication_date else None
    source_id = upsert_context_source(
        client,
        source_url=r.source_url,
        source_type="regulatory_filing",
        publisher="CONSOB",
        document_title=f"Partecipazioni rilevanti — {r.issuer_raw_name}",
        publication_timestamp=pub_ts,
        document_hash=r.document_hash,
        raw_text=r.raw_text,
        issuer_id=issuer_id,
        ingestion_run_id=PARSER_VERSION,
    )

    entity_id = summary.declarant.entity_id
    single_vehicle_id = (
        summary.vehicles[0].entity_id if len(summary.vehicles) == 1 else None
    )

    event_payload = {
        "issuer_id": issuer_id,
        "entity_id": entity_id,
        "raw_entity_name": r.declarant_raw_name,
        "holding_vehicle_entity_id": single_vehicle_id,
        "raw_vehicle_name": r.joined_vehicle_names,
        "event_type": r.event_type,
        "event_date": r.event_date,
        "voting_pct_before": r.voting_pct_before,
        "voting_pct_after": r.voting_pct_after,
        "direct_or_indirect": r.direct_or_indirect,
        "source_id": source_id,
        "evidence_text": (r.raw_text or "")[:500],
        "confidence": "parsed_fact",
        "review_status": "pending_review",
        "is_current": True,
        "version_number": 1,
        "updated_at": _now_utc(),
    }
    assert_source_provenance(event_payload, "ownership_events")

    existing = _find_existing_event(
        client,
        issuer_id=issuer_id,
        event_date=r.event_date,
        event_type=r.event_type,
        entity_id=entity_id,
        raw_entity_name=r.declarant_raw_name,
    )

    if existing:
        changed = (
            _ne(existing.get("voting_pct_after"), r.voting_pct_after)
            or existing.get("direct_or_indirect") != r.direct_or_indirect
        )
        if not changed:
            logger.info("ownership_events: unchanged (id=%s) — idempotent skip", existing["id"])
            return {"action": "skipped", "ownership_event_id": existing["id"], "source_id": source_id}
        # Superseded: insert the new version then mark the old one non-current.
        event_payload["version_number"] = existing.get("version_number", 1) + 1
        new_row = client.table("ownership_events").insert(event_payload).execute()
        new_id = new_row.data[0]["id"]
        supersede_context_event(
            client, "ownership_events", existing["id"], new_id,
            reason=f"reprocessed by {PARSER_VERSION}", changed_by=PARSER_VERSION,
        )
        _write_relationships(client, summary, source_id)
        return {"action": "superseded", "ownership_event_id": new_id, "source_id": source_id}

    new_row = client.table("ownership_events").insert(event_payload).execute()
    new_id = new_row.data[0]["id"]
    logger.info("ownership_events: inserted id=%s", new_id)
    _write_relationships(client, summary, source_id)
    # context_event_links: intentionally DEFERRED — no transaction linkage is
    # established in this pilot, and an issuer link would duplicate issuer_id.
    return {"action": "inserted", "ownership_event_id": new_id, "source_id": source_id}


def _write_relationships(client: Any, summary: ValidationSummary, source_id: int) -> None:
    """Create explicit 'controls' relationships: declarant -> named vehicle.

    Only when BOTH entities are resolved and the relationship is explicitly
    stated in the source (società controllata dal dichiarante).  No inferred
    control or holding chains.
    """
    r = summary.record
    subject_id = summary.declarant.entity_id
    if subject_id is None:
        return
    for v_res, v in zip(summary.vehicles, [v for v in r.holding_vehicles if v.raw_name]):
        if v_res.entity_id is None:
            continue
        existing = (
            client.table("entity_relationships").select("id")
            .eq("subject_entity_id", subject_id)
            .eq("object_entity_id", v_res.entity_id)
            .eq("relationship_type", "controls")
            .eq("is_current", True)
            .execute()
        )
        if existing.data:
            continue
        client.table("entity_relationships").insert({
            "subject_entity_id": subject_id,
            "object_entity_id": v_res.entity_id,
            "relationship_type": "controls",
            "confidence": "parsed_fact",
            "direct_or_indirect": r.direct_or_indirect,
            "source_id": source_id,
            "evidence_text": f"{r.declarant_raw_name} — società controllata: {v.raw_name}",
            "review_status": "pending_review",
            "is_current": True,
            "version_number": 1,
            "updated_at": _now_utc(),
        }).execute()
        logger.info(
            "entity_relationships: declarant %s controls vehicle %s",
            subject_id, v_res.entity_id,
        )


def _ne(a: Any, b: Any) -> bool:
    """Numeric-aware inequality (treats 3.069 == '3.069')."""
    if a is None and b is None:
        return False
    try:
        return abs(float(a) - float(b)) > 1e-9
    except (TypeError, ValueError):
        return a != b


# ── CLI ─────────────────────────────────────────────────────────────────────

def _get_client(no_db: bool) -> Any:
    if no_db:
        return None
    try:
        from ..db import get_supabase_client
        return get_supabase_client()
    except Exception as exc:
        logger.warning("Supabase client unavailable (%s) — resolution will be skipped", exc)
        return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CONSOB ownership-event pilot collector")
    parser.add_argument("--apply", action="store_true",
                        help="write to the database (default: dry-run, no writes)")
    parser.add_argument("--url", help="single explicit source URL")
    parser.add_argument("--format", choices=[SOURCE_FORMAT_ARCHIVE_HTML, SOURCE_FORMAT_TR1_PDF],
                        help="source format for --url")
    parser.add_argument("--issuer", help="issuer name filter for --url")
    parser.add_argument("--expected-pct", type=float, help="expected voting % for --url")
    parser.add_argument("--no-db", action="store_true",
                        help="skip DB entirely (pure parse + validate)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.url:
        if not args.format or not args.issuer:
            parser.error("--url requires --format and --issuer")
        specs = [PilotSpec(
            url=args.url, source_format=args.format,
            issuer_filter=args.issuer, expected_pct=args.expected_pct,
            label="(ad-hoc)",
        )]
    else:
        specs = PILOT_RECORDS

    client = _get_client(args.no_db)
    mode = "APPLY (writes enabled)" if args.apply else "DRY-RUN (no writes)"

    print("=" * 68)
    print(f"CONSOB ownership pilot collector — {mode}")
    print(f"records: {len(specs)}   client: {'connected' if client else 'NONE (resolution skipped)'}")
    print("=" * 68)

    total_safe = total_unsafe = total_found = 0
    for spec in specs:
        print()
        print(f"### {spec.label or spec.issuer_filter}")
        print(f"    URL: {spec.url}")
        try:
            summaries = process_record(spec, client=client, apply=args.apply)
        except Exception as exc:
            print(f"    ERROR: {exc}")
            logger.exception("processing failed for %s", spec.url)
            continue
        if not summaries:
            print("    (no record matched the supplied issuer + expected percentage)")
            continue
        for i, s in enumerate(summaries, start=1):
            total_found += 1
            total_safe += 1 if s.safe_to_apply else 0
            total_unsafe += 0 if s.safe_to_apply else 1
            print(render_summary(s, index=i))

    print()
    print("=" * 68)
    print(f"summary: {total_found} record(s) — "
          f"{total_safe} safe-to-apply, {total_unsafe} blocked")
    if not args.apply:
        print("DRY-RUN complete. No database writes were performed.")
        print("Re-run with --apply ONLY after operator approval of this output.")
    print("=" * 68)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
