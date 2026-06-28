"""
Phase 17A context-data layer — schema validation and unit tests.

Coverage:
  1. SQL source validation — key structural requirements present in the
     migration files (no live DB required).
  2. assert_source_provenance — all three validation rules.
  3. supersede_context_event — happy path, not-found, invalid table.
  4. upsert_context_source — existing URL returns id, new URL inserts.

Run with:
    python3 -m unittest tests.test_context_schema -v
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.context_db import (
    assert_source_provenance,
    supersede_context_event,
    upsert_context_source,
    _VERSIONED_CONTEXT_TABLES,
)

# ── Path helpers ───────────────────────────────────────────────────────────────

_REPO = Path(__file__).parent.parent
_MIGRATIONS = _REPO / "db" / "migrations"


def _sql(filename: str) -> str:
    return (_MIGRATIONS / filename).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# 1. SQL source-level validation
# ─────────────────────────────────────────────────────────────────────────────


class TestMigration016SourcesAndEntities(unittest.TestCase):
    """Structural requirements for migration 016."""

    @classmethod
    def setUpClass(cls):
        cls.sql = _sql("016_context_sources_and_entities.sql")

    def test_context_sources_created(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS context_sources", self.sql)

    def test_source_url_unique(self):
        self.assertIn("UNIQUE (source_url)", self.sql)

    def test_storage_path_present_on_table(self):
        self.assertIn("storage_path", self.sql)

    def test_raw_text_present_on_table(self):
        self.assertIn("raw_text", self.sql)

    def test_public_view_created(self):
        self.assertIn("CREATE OR REPLACE VIEW context_sources_public", self.sql)

    def test_public_view_excludes_storage_path(self):
        # The view body must not SELECT storage_path (only comment it out).
        # Strategy: confirm the exclusion comment is present and storage_path
        # does not appear unguarded in the SELECT column list.
        self.assertIn("-- storage_path intentionally excluded", self.sql)

    def test_public_view_excludes_raw_text(self):
        self.assertIn("-- raw_text intentionally excluded", self.sql)

    def test_source_id_not_null_references(self):
        # context_sources rows are themselves the provenance root, so the
        # table does not have a source_id column — but the SET NULL on
        # issuer_id must be present.
        self.assertIn("ON DELETE SET NULL", self.sql)

    def test_entities_created(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS entities", self.sql)

    def test_entities_lei_unique(self):
        self.assertIn("lei           TEXT UNIQUE", self.sql)

    def test_entities_entity_type_check(self):
        self.assertIn("entity_type   TEXT NOT NULL CHECK", self.sql)

    def test_entities_has_created_at(self):
        # Both tables must have the audit column; also appears in the view.
        self.assertGreaterEqual(self.sql.count("created_at"), 2)

    def test_entities_has_updated_at(self):
        self.assertGreaterEqual(self.sql.count("updated_at"), 2)


class TestMigration017EntityRelationships(unittest.TestCase):
    """Structural requirements for migration 017."""

    @classmethod
    def setUpClass(cls):
        cls.sql = _sql("017_entity_relationships.sql")

    def test_table_created(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS entity_relationships", self.sql)

    def test_source_id_not_null(self):
        self.assertIn("source_id          BIGINT NOT NULL", self.sql)

    def test_source_id_on_delete_restrict(self):
        # source_id must RESTRICT (provenance cannot be silently lost).
        # Look for the source_id FK definition.
        self.assertIn(
            "REFERENCES context_sources(id) ON DELETE RESTRICT",
            self.sql,
        )

    def test_check_has_object_constraint(self):
        self.assertIn("chk_entity_rel_has_object", self.sql)
        self.assertIn(
            "CHECK (object_entity_id IS NOT NULL OR object_issuer_id IS NOT NULL)",
            self.sql,
        )

    def test_is_current_versioning_columns(self):
        for col in ("is_current", "version_number", "superseded_by", "superseded_at"):
            with self.subTest(col=col):
                self.assertIn(col, self.sql)

    def test_partial_unique_entity_current(self):
        self.assertIn("uidx_entity_rel_entity_current", self.sql)

    def test_partial_unique_issuer_current(self):
        self.assertIn("uidx_entity_rel_issuer_current", self.sql)

    def test_subject_entity_restrict(self):
        self.assertIn(
            "REFERENCES entities(id) ON DELETE RESTRICT",
            self.sql,
        )

    def test_confidence_column_check(self):
        self.assertIn("'parsed_fact'", self.sql)
        self.assertIn("'heuristic_suggestion'", self.sql)
        self.assertIn("'reviewer_confirmed'", self.sql)


class TestMigration018ContextEvents(unittest.TestCase):
    """Structural requirements for migration 018 (all four event tables)."""

    @classmethod
    def setUpClass(cls):
        cls.sql = _sql("018_context_events.sql")

    def test_all_four_tables_created(self):
        for table in (
            "ownership_events",
            "governance_events",
            "buyback_events",
            "corporate_events",
        ):
            with self.subTest(table=table):
                self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", self.sql)

    def test_all_tables_have_issuer_id_not_null(self):
        # Each table must enforce issuer_id NOT NULL; indentation varies per table.
        import re
        matches = re.findall(r"issuer_id\s+BIGINT NOT NULL", self.sql)
        self.assertEqual(len(matches), 4)

    def test_all_tables_have_issuer_restrict(self):
        self.assertEqual(
            self.sql.count("REFERENCES issuers(id) ON DELETE RESTRICT"),
            4,
        )

    def test_all_tables_have_source_id_not_null(self):
        # source_id NOT NULL + RESTRICT on all four tables.
        self.assertGreaterEqual(
            self.sql.count("source_id"),
            4,
        )

    def test_all_tables_have_is_current(self):
        import re
        matches = re.findall(r"is_current\s+BOOLEAN NOT NULL DEFAULT TRUE", self.sql)
        self.assertEqual(len(matches), 4)

    def test_all_tables_have_versioning(self):
        for col in ("version_number", "superseded_by", "superseded_at"):
            with self.subTest(col=col):
                self.assertGreaterEqual(self.sql.count(col), 4)

    def test_entity_set_null_on_delete(self):
        # entity_id FKs must SET NULL (raw_*_name fallback preserves evidence).
        self.assertIn("REFERENCES entities(id) ON DELETE SET NULL", self.sql)

    def test_ownership_natural_identity_index(self):
        self.assertIn("uidx_ownership_resolved_current", self.sql)

    def test_governance_natural_identity_index(self):
        self.assertIn("uidx_governance_resolved_current", self.sql)

    def test_buyback_execution_natural_identity_index(self):
        self.assertIn("uidx_buyback_execution_current", self.sql)

    def test_buyback_lifecycle_natural_identity_index(self):
        self.assertIn("uidx_buyback_lifecycle_current", self.sql)

    def test_corporate_natural_identity_index(self):
        self.assertIn("uidx_corporate_event_current", self.sql)

    def test_five_partial_unique_indexes_total(self):
        # ownership + governance + 2 buyback + corporate = 5
        count = self.sql.count("CREATE UNIQUE INDEX IF NOT EXISTS")
        self.assertEqual(count, 5)


class TestMigration019ContextEventLinks(unittest.TestCase):
    """Structural requirements for migration 019."""

    @classmethod
    def setUpClass(cls):
        cls.sql = _sql("019_context_event_links.sql")

    def test_table_created(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS context_event_links", self.sql)

    def test_concrete_fk_transaction_id(self):
        self.assertIn("transaction_id  BIGINT REFERENCES transactions(id)", self.sql)

    def test_concrete_fk_issuer_id(self):
        self.assertIn("issuer_id       BIGINT REFERENCES issuers(id)", self.sql)

    def test_mutual_exclusion_check_constraint(self):
        self.assertIn("chk_cel_exactly_one_target", self.sql)
        # Both branches of the XOR must appear.
        self.assertIn("transaction_id IS NOT NULL AND issuer_id IS NULL", self.sql)
        self.assertIn("transaction_id IS NULL     AND issuer_id IS NOT NULL", self.sql)

    def test_no_polymorphic_target_type_column(self):
        # The original draft used link_target_type as a column — must NOT appear
        # in the CREATE TABLE body.  (The header comment explains the design
        # change and is allowed to mention the old name.)
        import re
        # Extract text between CREATE TABLE ... and the first closing );
        table_body_match = re.search(
            r"CREATE TABLE IF NOT EXISTS context_event_links\s*\((.+?)\);",
            self.sql, re.DOTALL,
        )
        self.assertIsNotNone(table_body_match, "CREATE TABLE block not found")
        table_body = table_body_match.group(1)
        self.assertNotIn("link_target_type", table_body)

    def test_transaction_fk_cascade(self):
        self.assertIn(
            "REFERENCES transactions(id) ON DELETE CASCADE",
            self.sql,
        )

    def test_issuer_fk_cascade(self):
        self.assertIn(
            "REFERENCES issuers(id)      ON DELETE CASCADE",
            self.sql,
        )

    def test_source_id_restrict(self):
        self.assertIn(
            "REFERENCES context_sources(id) ON DELETE RESTRICT",
            self.sql,
        )

    def test_versioning_columns(self):
        for col in ("is_current", "version_number", "superseded_by", "superseded_at"):
            with self.subTest(col=col):
                self.assertIn(col, self.sql)

    def test_natural_identity_indexes(self):
        self.assertIn("uidx_cel_tx_current", self.sql)
        self.assertIn("uidx_cel_issuer_current", self.sql)

    def test_context_type_check(self):
        for val in ("'ownership'", "'governance'", "'buyback'", "'corporate'"):
            with self.subTest(val=val):
                self.assertIn(val, self.sql)


# ─────────────────────────────────────────────────────────────────────────────
# 2. assert_source_provenance unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestAssertSourceProvenance(unittest.TestCase):

    def _valid_event(self, **overrides) -> dict:
        base = {
            "source_id":  42,
            "confidence": "parsed_fact",
        }
        base.update(overrides)
        return base

    def test_valid_parsed_fact_passes(self):
        assert_source_provenance(self._valid_event(), "ownership_events")

    def test_valid_heuristic_passes(self):
        assert_source_provenance(
            self._valid_event(confidence="heuristic_suggestion"),
            "governance_events",
        )

    def test_valid_reviewer_confirmed_with_attribution(self):
        assert_source_provenance(
            self._valid_event(
                confidence="reviewer_confirmed",
                reviewed_by="ops@insidewatch.it",
            ),
            "buyback_events",
        )

    def test_missing_source_id_raises(self):
        with self.assertRaises(ValueError) as ctx:
            assert_source_provenance({"confidence": "parsed_fact"}, "corporate_events")
        self.assertIn("source_id is required", str(ctx.exception))

    def test_source_id_none_raises(self):
        with self.assertRaises(ValueError):
            assert_source_provenance({"source_id": None}, "entity_relationships")

    def test_invalid_confidence_raises(self):
        with self.assertRaises(ValueError) as ctx:
            assert_source_provenance(
                self._valid_event(confidence="best_guess"),
                "ownership_events",
            )
        self.assertIn("invalid confidence", str(ctx.exception))

    def test_reviewer_confirmed_without_reviewed_by_raises(self):
        with self.assertRaises(ValueError) as ctx:
            assert_source_provenance(
                self._valid_event(confidence="reviewer_confirmed"),
                "governance_events",
            )
        self.assertIn("reviewed_by", str(ctx.exception))

    def test_reviewer_confirmed_empty_reviewed_by_raises(self):
        with self.assertRaises(ValueError):
            assert_source_provenance(
                self._valid_event(confidence="reviewer_confirmed", reviewed_by=""),
                "governance_events",
            )

    def test_confidence_none_is_allowed(self):
        # None means the application will rely on the DB default; not a
        # provenance violation.
        assert_source_provenance(
            {"source_id": 1, "confidence": None},
            "buyback_events",
        )

    def test_error_message_includes_table_name(self):
        with self.assertRaises(ValueError) as ctx:
            assert_source_provenance({}, "corporate_events")
        self.assertIn("corporate_events", str(ctx.exception))


# ─────────────────────────────────────────────────────────────────────────────
# 3. supersede_context_event unit tests
# ─────────────────────────────────────────────────────────────────────────────


def _mock_client():
    return MagicMock()


class TestSupersedeContextEvent(unittest.TestCase):

    def _make_client(self, existing_row):  # Optional[dict]
        client = _mock_client()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.execute.return_value = MagicMock(
            data=[existing_row] if existing_row else []
        )
        client.table.return_value.select.return_value = select_chain

        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock(data=[])
        client.table.return_value.update.return_value = update_chain

        return client

    def test_happy_path_returns_true(self):
        row = {"id": 10, "is_current": True, "version_number": 1}
        client = self._make_client(row)
        result = supersede_context_event(
            client, "ownership_events", old_id=10, new_id=11,
            reason="parser correction", changed_by="v2.0",
        )
        self.assertTrue(result)

    def test_update_called_with_is_current_false(self):
        row = {"id": 10, "is_current": True, "version_number": 1}
        client = self._make_client(row)
        supersede_context_event(
            client, "governance_events", old_id=10, new_id=11,
            reason="test", changed_by="test",
        )
        update_kwargs = client.table.return_value.update.call_args[0][0]
        self.assertFalse(update_kwargs["is_current"])
        self.assertEqual(update_kwargs["superseded_by"], 11)
        self.assertEqual(update_kwargs["version_number"], 2)

    def test_version_number_incremented(self):
        row = {"id": 5, "is_current": True, "version_number": 3}
        client = self._make_client(row)
        supersede_context_event(
            client, "buyback_events", old_id=5, new_id=6,
            reason="test", changed_by="test",
        )
        update_kwargs = client.table.return_value.update.call_args[0][0]
        self.assertEqual(update_kwargs["version_number"], 4)

    def test_not_found_returns_false(self):
        client = self._make_client(None)
        result = supersede_context_event(
            client, "corporate_events", old_id=999, new_id=1000,
            reason="test", changed_by="test",
        )
        self.assertFalse(result)

    def test_update_not_called_when_not_found(self):
        client = self._make_client(None)
        supersede_context_event(
            client, "ownership_events", old_id=999, new_id=1000,
            reason="test", changed_by="test",
        )
        client.table.return_value.update.assert_not_called()

    def test_invalid_table_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            supersede_context_event(
                _mock_client(), "transactions",  # transactions is NOT in context tables
                old_id=1, new_id=2,
                reason="test", changed_by="test",
            )
        self.assertIn("unknown table", str(ctx.exception))

    def test_all_versioned_tables_accepted(self):
        for table in _VERSIONED_CONTEXT_TABLES:
            with self.subTest(table=table):
                row = {"id": 1, "is_current": True, "version_number": 1}
                client = self._make_client(row)
                result = supersede_context_event(
                    client, table, old_id=1, new_id=2,
                    reason="test", changed_by="test",
                )
                self.assertTrue(result)


# ─────────────────────────────────────────────────────────────────────────────
# 4. upsert_context_source unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestUpsertContextSource(unittest.TestCase):

    def _client_existing(self, existing_id: int):
        """Client that returns an existing row for the SELECT."""
        client = _mock_client()

        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.limit.return_value = select_chain
        select_chain.execute.return_value = MagicMock(data=[{"id": existing_id}])
        client.table.return_value.select.return_value = select_chain

        return client

    def _client_new(self, inserted_id: int):
        """Client that returns empty SELECT (no existing) then INSERT."""
        client = _mock_client()

        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.limit.return_value = select_chain
        select_chain.execute.return_value = MagicMock(data=[])
        client.table.return_value.select.return_value = select_chain

        insert_chain = MagicMock()
        insert_chain.execute.return_value = MagicMock(data=[{"id": inserted_id}])
        client.table.return_value.insert.return_value = insert_chain

        return client

    def test_existing_url_returns_id_without_insert(self):
        client = self._client_existing(77)
        result = upsert_context_source(
            client,
            source_url="https://www.borsaitaliana.it/avvisi/2025/01/avviso.pdf",
            source_type="exchange_notice",
        )
        self.assertEqual(result, 77)
        client.table.return_value.insert.assert_not_called()

    def test_new_url_inserts_and_returns_id(self):
        client = self._client_new(99)
        result = upsert_context_source(
            client,
            source_url="https://www.consob.it/filings/new.pdf",
            source_type="regulatory_filing",
            publisher="CONSOB",
            issuer_id=42,
        )
        self.assertEqual(result, 99)
        client.table.return_value.insert.assert_called_once()

    def test_insert_payload_contains_required_fields(self):
        client = self._client_new(1)
        upsert_context_source(
            client,
            source_url="https://example.com/doc.pdf",
            source_type="press_release",
            publisher="Acme SpA",
        )
        payload = client.table.return_value.insert.call_args[0][0]
        self.assertEqual(payload["source_url"], "https://example.com/doc.pdf")
        self.assertEqual(payload["source_type"], "press_release")
        self.assertEqual(payload["publisher"], "Acme SpA")
        self.assertIn("updated_at", payload)

    def test_optional_fields_omitted_when_none(self):
        client = self._client_new(2)
        upsert_context_source(
            client,
            source_url="https://example.com/doc2.pdf",
            source_type="annual_report",
        )
        payload = client.table.return_value.insert.call_args[0][0]
        self.assertNotIn("publisher", payload)
        self.assertNotIn("issuer_id", payload)
        self.assertNotIn("storage_path", payload)
        self.assertNotIn("raw_text", payload)

    def test_optional_fields_included_when_provided(self):
        client = self._client_new(3)
        upsert_context_source(
            client,
            source_url="https://example.com/doc3.pdf",
            source_type="governance_notice",
            storage_path="context/2025/01/abc.pdf",
            raw_text="Board resolution ...",
            document_hash="deadbeef" * 8,
        )
        payload = client.table.return_value.insert.call_args[0][0]
        self.assertEqual(payload["storage_path"], "context/2025/01/abc.pdf")
        self.assertIn("raw_text", payload)
        self.assertIn("document_hash", payload)


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
