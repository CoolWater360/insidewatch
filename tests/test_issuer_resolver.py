"""
Tests for scraper/issuer_resolver.py.

Uses mock Supabase clients — no live DB required.
Covers: ISIN hit, alias hit, ilike suggestion, no-match queuing,
link_company_to_issuer, _strip_legal_suffix, create_issuer helpers.
"""

import pytest
from unittest.mock import MagicMock, call, patch

from scraper.issuer_resolver import (
    ResolveResult,
    resolve_issuer,
    link_company_to_issuer,
    get_unmatched,
    mark_resolved,
    mark_rejected,
    _strip_legal_suffix,
    _queue_unmatched,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _mock_client(securities_data=None, alias_data=None, issuer_data=None, upsert_ok=True):
    """Return a mock Supabase client wired to return the given table data."""
    client = MagicMock()

    def _table(name):
        tbl = MagicMock()

        def _chain(*args, **kwargs):
            return tbl

        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.ilike.return_value = tbl
        tbl.limit.return_value = tbl
        tbl.order.return_value = tbl
        tbl.update.return_value = tbl
        tbl.insert.return_value = tbl
        tbl.upsert.return_value = tbl

        data_map = {
            "securities": securities_data,
            "issuer_aliases": alias_data,
            "issuers": issuer_data,
        }

        result = MagicMock()
        result.data = data_map.get(name) or []
        tbl.execute.return_value = result
        return tbl

    client.table.side_effect = _table
    return client


# ─── _strip_legal_suffix ──────────────────────────────────────────────────────

class TestStripLegalSuffix:
    def test_spa(self):
        assert _strip_legal_suffix("Eni S.p.A.") == "Eni"

    def test_spa_nospace(self):
        assert _strip_legal_suffix("Eni SPA") == "Eni"

    def test_nv(self):
        assert _strip_legal_suffix("Ferrari N.V.") == "Ferrari"

    def test_srl(self):
        assert _strip_legal_suffix("Acme S.r.l.") == "Acme"

    def test_no_suffix(self):
        assert _strip_legal_suffix("Eni") == "Eni"

    def test_multiword(self):
        assert _strip_legal_suffix("Intesa Sanpaolo S.p.A.") == "Intesa Sanpaolo"


# ─── resolve_issuer: ISIN path ────────────────────────────────────────────────

class TestResolveByIsin:
    def test_isin_hit_returns_issuer_id(self):
        client = _mock_client(securities_data=[{"issuer_id": 42}])
        result = resolve_issuer(client, "Eni S.p.A.", "IT0003132476")
        assert result == ResolveResult(issuer_id=42, method="isin", needs_review=False)

    def test_isin_hit_no_review_needed(self):
        client = _mock_client(securities_data=[{"issuer_id": 7}])
        result = resolve_issuer(client, "Unknown Name", "IT0003132476")
        assert result.needs_review is False

    def test_no_isin_skips_securities_lookup(self):
        client = _mock_client(alias_data=[{"issuer_id": 5}])
        result = resolve_issuer(client, "Eni S.p.A.", None)
        # Must have hit alias path, not securities
        assert result.method == "alias"
        assert result.issuer_id == 5


# ─── resolve_issuer: alias path ───────────────────────────────────────────────

class TestResolveByAlias:
    def test_alias_hit_returns_issuer_id(self):
        # securities misses, alias hits
        client = _mock_client(securities_data=[], alias_data=[{"issuer_id": 99}])
        result = resolve_issuer(client, "ENI SPA", "IT9999999999")
        assert result == ResolveResult(issuer_id=99, method="alias", needs_review=False)

    def test_alias_hit_without_isin(self):
        client = _mock_client(securities_data=[], alias_data=[{"issuer_id": 12}])
        result = resolve_issuer(client, "Enel S.p.A.", None)
        assert result.issuer_id == 12
        assert result.method == "alias"


# ─── resolve_issuer: suggestion / unmatched ───────────────────────────────────

class TestResolveUnmatched:
    def test_no_match_with_suggestion_returns_suggestion_method(self):
        client = _mock_client(
            securities_data=[],
            alias_data=[],
            issuer_data=[{"id": 3}],  # ilike suggestion hit
        )
        result = resolve_issuer(client, "Eni Nuova", "IT0001111111")
        assert result.issuer_id is None
        assert result.method == "suggestion"
        assert result.needs_review is True

    def test_no_match_no_suggestion_returns_unmatched(self):
        client = _mock_client(securities_data=[], alias_data=[], issuer_data=[])
        result = resolve_issuer(client, "Unknown Corp", None)
        assert result.issuer_id is None
        assert result.method == "unmatched"
        assert result.needs_review is True

    def test_db_error_returns_error_result(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("DB down")
        result = resolve_issuer(client, "Foo S.p.A.", "IT0001234567")
        assert result.issuer_id is None
        assert result.method == "error"
        assert result.needs_review is True


# ─── _queue_unmatched ─────────────────────────────────────────────────────────

class TestQueueUnmatched:
    def test_upsert_called_with_raw_name(self):
        client = MagicMock()
        tbl = MagicMock()
        tbl.upsert.return_value = tbl
        tbl.execute.return_value = MagicMock(data=[])
        client.table.return_value = tbl

        _queue_unmatched(client, "Foo S.p.A.", "IT0001234567", filing_id=10, suggestion_issuer_id=2)

        client.table.assert_called_with("unmatched_issuers")
        tbl.upsert.assert_called_once()
        payload = tbl.upsert.call_args[0][0]
        assert payload["raw_name"] == "Foo S.p.A."
        assert payload["raw_isin"] == "IT0001234567"
        assert payload["filing_id"] == 10
        assert payload["suggestion_issuer_id"] == 2

    def test_queue_failure_does_not_raise(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("DB error")
        # Should not raise
        _queue_unmatched(client, "Foo", None, None, None)


# ─── link_company_to_issuer ───────────────────────────────────────────────────

class TestLinkCompanyToIssuer:
    def test_links_when_issuer_id_is_null(self):
        client = MagicMock()
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.limit.return_value = tbl
        tbl.update.return_value = tbl
        tbl.execute.return_value = MagicMock(data=[{"issuer_id": None}])
        client.table.return_value = tbl

        link_company_to_issuer(client, company_id=5, issuer_id=99)

        tbl.update.assert_called_once_with({"issuer_id": 99})

    def test_skips_when_already_linked(self):
        client = MagicMock()
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.limit.return_value = tbl
        tbl.update.return_value = tbl
        tbl.execute.return_value = MagicMock(data=[{"issuer_id": 99}])
        client.table.return_value = tbl

        link_company_to_issuer(client, company_id=5, issuer_id=99)

        tbl.update.assert_not_called()

    def test_failure_does_not_raise(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("DB error")
        # Should not raise
        link_company_to_issuer(client, company_id=5, issuer_id=99)


# ─── mark_resolved / mark_rejected ────────────────────────────────────────────

class TestMarkResolvedRejected:
    def _make_client(self):
        client = MagicMock()
        tbl = MagicMock()
        tbl.update.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.execute.return_value = MagicMock(data=[])
        client.table.return_value = tbl
        return client, tbl

    def test_mark_resolved_sets_status(self):
        client, tbl = self._make_client()
        mark_resolved(client, unmatched_id=3, issuer_id=10)
        update_payload = tbl.update.call_args[0][0]
        assert update_payload["status"] == "resolved"
        assert update_payload["resolved_to"] == 10
        assert "resolved_at" in update_payload

    def test_mark_rejected_sets_status(self):
        client, tbl = self._make_client()
        mark_rejected(client, unmatched_id=4)
        update_payload = tbl.update.call_args[0][0]
        assert update_payload["status"] == "rejected"
        assert update_payload.get("resolved_to") is None


# ─── get_unmatched ─────────────────────────────────────────────────────────────

class TestGetUnmatched:
    def test_returns_data(self):
        client = MagicMock()
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.order.return_value = tbl
        tbl.limit.return_value = tbl
        tbl.execute.return_value = MagicMock(data=[{"id": 1, "raw_name": "Foo"}])
        client.table.return_value = tbl

        rows = get_unmatched(client, status="pending")
        assert len(rows) == 1
        assert rows[0]["raw_name"] == "Foo"

    def test_returns_empty_list_on_no_data(self):
        client = MagicMock()
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.order.return_value = tbl
        tbl.limit.return_value = tbl
        tbl.execute.return_value = MagicMock(data=None)
        client.table.return_value = tbl

        rows = get_unmatched(client)
        assert rows == []
