"""Tests for the Phase 17B.2 controlled ownership-event pilot.

Fixtures are minimal extracts from the three OPERATOR-SUPPLIED official CONSOB
archive pages (tests/fixtures/ownership/*.html) plus one CLEARLY-SYNTHETIC,
FICTIONAL TR-1 PDF (no real entity / no real disclosure) used solely to
exercise the PDF adapter, since no real TR-1 was supplied.

Covered:
  * HTML archive extraction (azioni + aggregate tables)
  * TR-1 PDF extraction
  * missing / partial fields
  * exact issuer resolution + unresolved issuer path
  * deterministic identity
  * dry-run performs no writes
  * idempotent re-run
  * no duplicate current ownership event
  * source provenance enforcement
  * no control / UBO inference
"""

import os
from unittest.mock import MagicMock

import pytest

from scraper.ownership import archive_html, collector, tr1_pdf
from scraper.ownership.models import (
    SOURCE_FORMAT_ARCHIVE_HTML,
    SOURCE_FORMAT_TR1_PDF,
    classify_event_type,
    derive_crossed_threshold,
    detect_direct_indirect,
    parse_italian_date,
    parse_pct,
)
from scraper.ownership.resolver import (
    find_or_create_entity,
    infer_entity_type,
    resolve_issuer_exact,
)
from scraper.ownership.validate import validate_record

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "ownership")


def _read(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


CUCINELLI_URL = "https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-2026-05-06"
MEDIOBANCA_URL = "https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-2025-09-23"
ITALMOBILIARE_URL = "https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-2026-06-03"


# ─── shared model helpers ──────────────────────────────────────────────────────

class TestModelHelpers:
    def test_parse_italian_date(self):
        assert parse_italian_date("28/04/2026 [2]") == "2026-04-28"
        assert parse_italian_date("no date here") is None

    def test_parse_pct(self):
        assert parse_pct("3.069% -INDIRETTA") == pytest.approx(3.069)
        assert parse_pct("ZERO") == 0.0
        assert parse_pct("nothing") is None

    def test_detect_direct_indirect(self):
        assert detect_direct_indirect("3.001% -INDIRETTA PROPRIETA'") == "indirect"
        assert detect_direct_indirect("62.299% -DIRETTA PROPRIETA'") == "direct"
        assert detect_direct_indirect("4.760%") is None

    def test_derive_crossed_threshold_initial(self):
        # initial disclosure at 3.069 → crossed the 3% threshold
        assert derive_crossed_threshold(None, 3.069) == 3.0

    def test_derive_crossed_threshold_none_when_subthreshold(self):
        # 3.1 → 3.5 crosses no threshold
        assert derive_crossed_threshold(3.1, 3.5) is None

    def test_classify_initial_disclosure(self):
        et, notes = classify_event_type(
            pct_before=None, pct_after=3.069, has_prior_notification=False
        )
        assert et == "initial_disclosure"

    def test_classify_ambiguous_before_is_other(self):
        et, notes = classify_event_type(
            pct_before=None, pct_after=5.588,
            has_prior_notification=True, ambiguous_before=True,
        )
        assert et == "other"
        assert notes  # carries an explanatory note

    def test_classify_subthreshold_change_is_other(self):
        et, notes = classify_event_type(
            pct_before=3.1, pct_after=3.5, has_prior_notification=True
        )
        assert et == "other"


# ─── HTML archive extraction: AZIONI table ─────────────────────────────────────

class TestArchiveAzioni:
    def test_italmobiliare_single_vehicle_initial_disclosure(self):
        recs = archive_html.parse_archive_page(
            _read("italmobiliare_azioni_2026-06-03.html").decode("utf-8"),
            source_url=ITALMOBILIARE_URL,
            issuer_filter="ITALMOBILIARE",
        )
        # The fixture has two Italmobiliare rows (3.001% target + 2.916% neighbour);
        # the parser returns both — select_target narrows to the supplied pct.
        assert len(recs) == 2
        r = next(x for x in recs if x.voting_pct_after == pytest.approx(3.001))
        assert r.source_format == SOURCE_FORMAT_ARCHIVE_HTML
        assert r.issuer_raw_name == "ITALMOBILIARE SPA"
        assert r.declarant_raw_name == "MORGAN STANLEY"
        assert r.voting_pct_after == pytest.approx(3.001)
        assert r.voting_pct_before is None        # empty prior cell
        assert r.has_prior_notification is False
        assert r.event_type == "initial_disclosure"
        assert r.direct_or_indirect == "indirect"
        assert r.event_date == "2026-05-25"
        assert r.publication_date == "2026-06-03"
        assert r.single_named_vehicle == "Morgan Stanley & Co. International plc"

    def test_mediobanca_multi_vehicle_flagged_ambiguous(self):
        recs = archive_html.parse_archive_page(
            _read("mediobanca_azioni_2025-09-23.html").decode("utf-8"),
            source_url=MEDIOBANCA_URL,
            issuer_filter="MEDIOBANCA",
        )
        assert len(recs) == 1
        r = recs[0]
        assert r.voting_pct_after == pytest.approx(3.069)
        assert r.event_type == "initial_disclosure"
        assert r.named_vehicle_count == 2
        # multiple named vehicles must be flagged unresolved (no single FK)
        assert any("multiple named vehicles" in a for a in r.ambiguous_fields)
        assert r.single_named_vehicle is None

    def test_issuer_filter_excludes_other_rows(self):
        # The Italmobiliare fixture has a second (2.916%) row; filtering by a
        # non-present issuer yields nothing.
        recs = archive_html.parse_archive_page(
            _read("italmobiliare_azioni_2026-06-03.html").decode("utf-8"),
            source_url=ITALMOBILIARE_URL,
            issuer_filter="NONEXISTENT SPA",
        )
        assert recs == []


# ─── HTML archive extraction: AGGREGATE table ──────────────────────────────────

class TestArchiveAggregate:
    def test_cucinelli_aggregate_ambiguous_no_named_vehicle(self):
        recs = archive_html.parse_archive_page(
            _read("cucinelli_aggregate_2026-05-06.html").decode("utf-8"),
            source_url=CUCINELLI_URL,
            issuer_filter="BRUNELLO CUCINELLI",
        )
        assert len(recs) == 1
        r = recs[0]
        assert r.declarant_raw_name == "FMR LLC"
        assert r.voting_pct_after == pytest.approx(5.588)
        # a)/b) split prior → before ambiguous, event_type undetermined
        assert r.voting_pct_before is None
        assert "voting_pct_before" in r.ambiguous_fields
        assert r.event_type == "other"
        # aggregate table exposes only numeric DETTAGLI — NO named vehicle
        assert r.named_vehicle_count == 0
        assert any("aggregate" in a for a in r.ambiguous_fields)
        assert r.direct_or_indirect == "indirect"

    def test_aggregate_footnote_row_skipped(self):
        # The (*) footnote continuation row must not become a record.
        recs = archive_html.parse_archive_page(
            _read("cucinelli_aggregate_2026-05-06.html").decode("utf-8"),
            source_url=CUCINELLI_URL,
        )
        # Only the single data row, never the colspan footnote row.
        assert len(recs) == 1


# ─── missing / partial fields ──────────────────────────────────────────────────

class TestPartialFields:
    def test_row_without_prior_is_initial(self):
        html = """
        <table><tr><th>x SOCIETA'CONTROLLATA TITOLO DI POSSESSO</th></tr>
        <tr><td>01/02/2026</td><td>ACME LLC</td><td>TARGET SPA</td>
        <td>5.100% -INDIRETTA PROPRIETA'</td><td>** 5.100% ACME SUB SRL</td>
        <td></td></tr></table>
        """
        recs = archive_html.parse_archive_page(html, source_url="http://x", publication_date="2026-02-01")
        assert len(recs) == 1
        assert recs[0].event_type == "initial_disclosure"
        assert recs[0].voting_pct_before is None

    def test_missing_after_pct_blocks_apply(self):
        html = """
        <table><tr><th>x SOCIETA'CONTROLLATA TITOLO DI POSSESSO</th></tr>
        <tr><td>01/02/2026</td><td>ACME LLC</td><td>TARGET SPA</td>
        <td>-INDIRETTA</td><td></td><td></td></tr></table>
        """
        recs = archive_html.parse_archive_page(html, source_url="http://x")
        r = recs[0]
        assert r.voting_pct_after is None
        from scraper.ownership.resolver import IssuerResolution, EntityResolution
        s = validate_record(
            r, IssuerResolution(1, "isin"),
            EntityResolution(7, "exact_name", "ACME LLC"), [],
        )
        assert s.safe_to_apply is False
        assert any("voting_pct_after" in b for b in s.blocking_reasons)


# ─── TR-1 PDF extraction ───────────────────────────────────────────────────────

class TestTr1Pdf:
    def test_extract_and_parse_synthetic_tr1(self):
        pdf_bytes = _read("synthetic_tr1_example.pdf")
        text = tr1_pdf.extract_text_from_pdf(pdf_bytes)
        assert "ESEMPIO QUOTATA TEST" in text
        rec = tr1_pdf.parse_tr1(text, source_url="http://example/tr1")
        assert rec.source_format == SOURCE_FORMAT_TR1_PDF
        assert "ESEMPIO QUOTATA TEST" in rec.issuer_raw_name
        assert rec.issuer_isin == "IT0000000TEST"
        assert rec.voting_pct_after == pytest.approx(5.120)
        assert rec.voting_pct_before == pytest.approx(3.880)
        assert rec.has_prior_notification is True
        # 3.880 → 5.120 crosses the 5% threshold
        assert rec.event_type == "threshold_crossing_up"
        assert rec.direct_or_indirect == "indirect"
        assert rec.declarant_lei == "5299000000000000TEST"

    def test_tr1_missing_fields_warns(self):
        rec = tr1_pdf.parse_tr1("totally unrelated text", source_url="http://x")
        assert rec.voting_pct_after is None
        assert any("voting_pct_after" in w for w in rec.parse_warnings)


# ─── issuer resolution (exact only) ────────────────────────────────────────────

def _resolver_client(securities=None, aliases=None, issuers=None, entities=None,
                     entity_insert_id=99):
    client = MagicMock()

    def _table(name):
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.ilike.return_value = tbl
        tbl.limit.return_value = tbl
        tbl.insert.return_value = tbl
        data_map = {
            "securities": securities, "issuer_aliases": aliases,
            "issuers": issuers, "entities": entities,
        }
        result = MagicMock()
        if name == "entities" and entities is None:
            # default: not found
            result.data = []
        else:
            result.data = data_map.get(name) or []
        # insert path for entities
        insert_result = MagicMock()
        insert_result.data = [{"id": entity_insert_id}]

        def _execute():
            return result
        tbl.execute.side_effect = None
        tbl.execute.return_value = result

        # Distinguish insert().execute() by returning insert_result when insert called
        def insert(_payload):
            tbl.execute.return_value = insert_result
            return tbl
        tbl.insert.side_effect = insert
        return tbl

    client.table.side_effect = _table
    return client


class TestIssuerResolution:
    def test_exact_isin(self):
        c = _resolver_client(securities=[{"issuer_id": 42}])
        res = resolve_issuer_exact(c, "ANY SPA", "IT000TEST")
        assert res.issuer_id == 42 and res.method == "isin"

    def test_exact_alias(self):
        c = _resolver_client(aliases=[{"issuer_id": 7}])
        res = resolve_issuer_exact(c, "MEDIOBANCA SPA", None)
        assert res.issuer_id == 7 and res.method == "alias"

    def test_exact_canonical(self):
        c = _resolver_client(issuers=[{"id": 5}])
        res = resolve_issuer_exact(c, "ITALMOBILIARE SPA", None)
        assert res.issuer_id == 5 and res.method == "canonical"

    def test_unresolved_path(self):
        c = _resolver_client()  # nothing matches
        res = resolve_issuer_exact(c, "UNKNOWN SPA", "IT999")
        assert res.issuer_id is None and res.method == "unresolved"

    def test_db_unavailable_when_client_none(self):
        res = resolve_issuer_exact(None, "X SPA", None)
        assert res.issuer_id is None and res.method == "db_unavailable"

    def test_infer_entity_type(self):
        assert infer_entity_type("FMR LLC") == "company"
        assert infer_entity_type("ESEMPIO HOLDING SA") == "holding_company"
        assert infer_entity_type("Mario Rossi") == "natural_person"


class TestEntityResolution:
    def test_would_create_in_dry_run(self):
        c = _resolver_client(entities=[])
        res = find_or_create_entity(c, "NEW ENTITY SPA", apply=False)
        assert res.entity_id is None and res.method == "would_create"

    def test_created_in_apply(self):
        c = _resolver_client(entities=[], entity_insert_id=123)
        res = find_or_create_entity(c, "NEW ENTITY SPA", apply=True)
        assert res.entity_id == 123 and res.method == "created"

    def test_exact_existing(self):
        c = _resolver_client(entities=[{"id": 55}])
        res = find_or_create_entity(c, "EXISTING SPA", apply=True)
        assert res.entity_id == 55 and res.method == "exact_name"


# ─── deterministic identity + validation ───────────────────────────────────────

class TestValidation:
    def _italmobiliare_record(self):
        return archive_html.parse_archive_page(
            _read("italmobiliare_azioni_2026-06-03.html").decode("utf-8"),
            source_url=ITALMOBILIARE_URL, issuer_filter="ITALMOBILIARE",
        )[0]

    def test_unresolved_issuer_blocks_apply(self):
        from scraper.ownership.resolver import IssuerResolution, EntityResolution
        r = self._italmobiliare_record()
        s = validate_record(
            r, IssuerResolution(None, "unresolved"),
            EntityResolution(None, "would_create", "MORGAN STANLEY"), [],
        )
        assert s.safe_to_apply is False
        assert any("issuer_id unresolved" in b for b in s.blocking_reasons)

    def test_resolved_record_safe_and_identity_deterministic(self):
        from scraper.ownership.resolver import IssuerResolution, EntityResolution
        r = self._italmobiliare_record()
        s1 = validate_record(r, IssuerResolution(5, "canonical"),
                             EntityResolution(8, "exact_name", "MORGAN STANLEY"), [])
        s2 = validate_record(r, IssuerResolution(5, "canonical"),
                             EntityResolution(8, "exact_name", "MORGAN STANLEY"), [])
        assert s1.safe_to_apply is True
        assert s1.proposed_identity == s2.proposed_identity
        assert "issuer_id=5" in s1.proposed_identity
        assert "entity_id=8" in s1.proposed_identity
        assert "event_date=2026-05-25" in s1.proposed_identity
        assert "event_type=initial_disclosure" in s1.proposed_identity


# ─── no control / UBO inference ────────────────────────────────────────────────

class TestNoInference:
    def test_aggregate_no_named_vehicle_no_relationship_proposed(self):
        r = archive_html.parse_archive_page(
            _read("cucinelli_aggregate_2026-05-06.html").decode("utf-8"),
            source_url=CUCINELLI_URL, issuer_filter="BRUNELLO CUCINELLI",
        )[0]
        # Footnote says "4 controlled companies" but names none → no vehicle entity
        assert r.named_vehicle_count == 0
        assert r.joined_vehicle_names is None

    def test_no_stake_economic_inference(self):
        # voting % is recorded; economic stake is NOT inferred from it
        r = archive_html.parse_archive_page(
            _read("mediobanca_azioni_2025-09-23.html").decode("utf-8"),
            source_url=MEDIOBANCA_URL, issuer_filter="MEDIOBANCA",
        )[0]
        assert r.voting_pct_after == pytest.approx(3.069)
        assert r.stake_pct_after is None
        assert r.stake_pct_before is None


# ─── dry-run performs no writes ────────────────────────────────────────────────

class _FakeWriteClient:
    """Records every .table(name) access; fails the test if a write is attempted."""
    def __init__(self):
        self.writes = []
        self.reads = []

    def table(self, name):
        return _FakeTable(name, self)


class _FakeTable:
    def __init__(self, name, parent):
        self.name = name
        self.parent = parent

    def select(self, *a, **k):
        self.parent.reads.append(self.name)
        return self

    def insert(self, *a, **k):
        self.parent.writes.append(("insert", self.name, a))
        return self

    def update(self, *a, **k):
        self.parent.writes.append(("update", self.name, a))
        return self

    def upsert(self, *a, **k):
        self.parent.writes.append(("upsert", self.name, a))
        return self

    def eq(self, *a, **k):
        return self

    def ilike(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        res = MagicMock()
        res.data = []
        return res


def _fixture_fetcher(spec):
    mapping = {
        CUCINELLI_URL: "cucinelli_aggregate_2026-05-06.html",
        MEDIOBANCA_URL: "mediobanca_azioni_2025-09-23.html",
        ITALMOBILIARE_URL: "italmobiliare_azioni_2026-06-03.html",
    }
    return lambda url: _read(mapping[url])


class TestDryRunNoWrites:
    def test_dry_run_does_not_write(self):
        spec = collector.PILOT_RECORDS[2]  # Italmobiliare
        client = _FakeWriteClient()
        summaries = collector.process_record(
            spec, client=client, apply=False, fetcher=_fixture_fetcher(spec),
        )
        assert len(summaries) == 1
        assert client.writes == []   # absolutely no writes in dry-run

    def test_dry_run_with_no_client_still_validates(self):
        spec = collector.PILOT_RECORDS[1]  # Mediobanca
        summaries = collector.process_record(
            spec, client=None, apply=False, fetcher=_fixture_fetcher(spec),
        )
        assert len(summaries) == 1
        # issuer unresolved (no DB) → not safe to apply
        assert summaries[0].safe_to_apply is False
        assert summaries[0].issuer.method == "db_unavailable"


# ─── apply: insert, idempotent re-run, no duplicate, provenance ───────────────

class _StatefulClient:
    """Minimal in-memory Supabase double supporting the collector's write path."""
    def __init__(self, issuer_id=5):
        self.issuer_id = issuer_id
        self.context_sources = []
        self.entities = []
        self.ownership_events = []
        self.entity_relationships = []
        self._seq = {"context_sources": 0, "entities": 0,
                     "ownership_events": 0, "entity_relationships": 0}

    def table(self, name):
        return _StatefulTable(name, self)


class _StatefulTable:
    def __init__(self, name, db):
        self.name = name
        self.db = db
        self._filters = {}
        self._op = None
        self._payload = None

    # query building
    def select(self, *a, **k):
        self._op = "select"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def upsert(self, payload, **k):
        self._op = "upsert"
        self._payload = payload
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def ilike(self, col, val):
        self._filters[col] = ("ilike", val)
        return self

    def limit(self, *a, **k):
        return self

    def _rows(self):
        return getattr(self.db, self.name)

    def _match(self, row):
        for col, val in self._filters.items():
            if isinstance(val, tuple) and val[0] == "ilike":
                if str(row.get(col, "")).upper() != str(val[1]).upper():
                    return False
            elif row.get(col) != val:
                return False
        return True

    def execute(self):
        res = MagicMock()
        if self._op in ("select",):
            res.data = [r for r in self._rows() if self._match(r)]
        elif self._op == "insert":
            self.db._seq[self.name] += 1
            row = dict(self._payload)
            row["id"] = self.db._seq[self.name]
            self._rows().append(row)
            res.data = [row]
        elif self._op == "upsert":
            # context_sources upsert-by-url semantics are handled by helper;
            # here we just insert.
            self.db._seq[self.name] += 1
            row = dict(self._payload)
            row["id"] = self.db._seq[self.name]
            self._rows().append(row)
            res.data = [row]
        elif self._op == "update":
            updated = []
            for r in self._rows():
                if self._match(r):
                    r.update(self._payload)
                    updated.append(r)
            res.data = updated
        else:
            res.data = []
        return res


class TestApplyWrites:
    def _client_with_issuer(self):
        c = _StatefulClient()
        # Pre-seed the issuer canonical match for Italmobiliare.
        c.issuers = [{"id": 5, "canonical_name": "ITALMOBILIARE SPA"}]

        # patch table() to serve issuers/securities/aliases reads
        orig_table = c.table

        def table(name):
            t = orig_table(name)
            if name == "issuers":
                t._rows = lambda: c.issuers  # type: ignore
            if name == "securities":
                t._rows = lambda: []  # type: ignore
            if name == "issuer_aliases":
                t._rows = lambda: []  # type: ignore
            return t

        c.table = table  # type: ignore
        return c

    def test_apply_inserts_then_idempotent(self):
        spec = collector.PILOT_RECORDS[2]  # Italmobiliare (clean record)
        c = self._client_with_issuer()
        fetch = _fixture_fetcher(spec)

        s1 = collector.process_record(spec, client=c, apply=True, fetcher=fetch)
        assert s1[0].safe_to_apply is True
        assert len(c.ownership_events) == 1
        ev = c.ownership_events[0]
        # provenance: source_id set, confidence parsed_fact, pending review
        assert ev["source_id"] is not None
        assert ev["confidence"] == "parsed_fact"
        assert ev["review_status"] == "pending_review"
        assert ev["raw_entity_name"] == "MORGAN STANLEY"
        assert len(c.context_sources) == 1

        # second run → no duplicate current ownership event
        collector.process_record(spec, client=c, apply=True, fetcher=fetch)
        current = [e for e in c.ownership_events if e.get("is_current")]
        assert len(current) == 1

    def test_apply_creates_explicit_control_relationship(self):
        spec = collector.PILOT_RECORDS[2]  # single named vehicle
        c = self._client_with_issuer()
        collector.process_record(spec, client=c, apply=True, fetcher=_fixture_fetcher(spec))
        # declarant 'controls' the single named vehicle — explicit, not inferred
        assert len(c.entity_relationships) == 1
        rel = c.entity_relationships[0]
        assert rel["relationship_type"] == "controls"
        assert rel["confidence"] == "parsed_fact"
        assert rel["review_status"] == "pending_review"

    def test_apply_skips_unresolved_issuer(self):
        spec = collector.PILOT_RECORDS[0]  # Cucinelli — but issuer not seeded
        c = _StatefulClient()  # no issuers → unresolved
        summaries = collector.process_record(spec, client=c, apply=True, fetcher=_fixture_fetcher(spec))
        assert summaries[0].safe_to_apply is False
        assert len(c.ownership_events) == 0  # nothing written


# ─── source provenance enforcement ─────────────────────────────────────────────

class TestProvenance:
    def test_assert_provenance_requires_source_id(self):
        from scraper.context_db import assert_source_provenance
        with pytest.raises(ValueError):
            assert_source_provenance({"confidence": "parsed_fact"}, "ownership_events")

    def test_assert_provenance_accepts_valid(self):
        from scraper.context_db import assert_source_provenance
        # should not raise
        assert_source_provenance(
            {"source_id": 1, "confidence": "parsed_fact"}, "ownership_events"
        )
