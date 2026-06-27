"""
Regression tests for scraper/reprocess_historical.py.

Guards against schema mismatches between the script's Supabase queries and the
actual filings table columns.  The filings table uses pdf_url (not source_url)
for the document URL — this was the source of the 'column filings.source_url
does not exist' error when --filing-id was introduced.
"""

import pathlib
import re
from unittest.mock import MagicMock, patch, call

import pytest

# ── Source-level regression: select strings must name real filings columns ────

_REPROCESS_SRC = (
    pathlib.Path(__file__).parent.parent / "scraper" / "reprocess_historical.py"
).read_text()

_FILINGS_COLUMNS_IN_SELECT = re.findall(
    r'\.table\("filings"\)\s*\n?\s*\.select\("([^"]+)"',
    _REPROCESS_SRC,
)


class TestFilingsSelectColumns:
    """Verify that every filings .select() call uses real column names."""

    def test_at_least_one_filings_select_found(self):
        assert _FILINGS_COLUMNS_IN_SELECT, (
            "No .table('filings').select() found in reprocess_historical.py — "
            "test is broken or the query was removed"
        )

    def test_no_source_url_in_filings_select(self):
        for sel in _FILINGS_COLUMNS_IN_SELECT:
            cols = {c.strip() for c in sel.split(",")}
            assert "source_url" not in cols, (
                f"reprocess_historical.py selects 'source_url' from filings — "
                f"the real column is 'pdf_url'. Select string: {sel!r}"
            )

    def test_pdf_url_present_in_filings_select(self):
        combined = ",".join(_FILINGS_COLUMNS_IN_SELECT)
        assert "pdf_url" in combined, (
            "reprocess_historical.py must select 'pdf_url' from filings"
        )

    def test_required_columns_all_present(self):
        combined = ",".join(_FILINGS_COLUMNS_IN_SELECT)
        for col in ("id", "pdf_url", "pdf_sha256", "raw_extracted_text", "source_published_utc"):
            assert col in combined, (
                f"reprocess_historical.py filings select must include '{col}'"
            )


# ── _fetch_filings: filing_id path ───────────────────────────────────────────

class TestFetchFilingsFilingId:
    """
    Verify that the --filing-id path issues the right query and returns the
    correct dict shape (with pdf_url, not source_url).
    """

    def _make_client(self, data: list) -> MagicMock:
        """Build a minimal Supabase client mock that returns `data`."""
        result = MagicMock()
        result.data = data
        execute = MagicMock(return_value=result)
        limit   = MagicMock(return_value=MagicMock(execute=execute))
        eq2     = MagicMock(return_value=MagicMock(limit=limit))
        not_is  = MagicMock(return_value=MagicMock(eq=eq2))
        eq1     = MagicMock(return_value=MagicMock(**{"not_": MagicMock(is_=not_is)}))
        select  = MagicMock(return_value=MagicMock(eq=eq1))
        table   = MagicMock(return_value=MagicMock(select=select))
        client  = MagicMock()
        client.table = table
        return client, select

    def test_filing_id_path_returns_single_row(self):
        from scraper.reprocess_historical import _fetch_filings

        row = {
            "id": 325,
            "pdf_url": "https://www.borsaitaliana.it/nisavvsource/pdf/2025/8327.pdf",
            "pdf_sha256": "abc123",
            "raw_extracted_text": "some text",
            "source_published_utc": "2025-01-15T09:00:00Z",
        }
        client, select_mock = self._make_client([row])

        result = _fetch_filings(
            client,
            since=None,
            until=None,
            reprocess_all=False,
            current_parser_version="1.2.1",
            limit=100,
            filing_id=325,
        )

        assert result == [row]
        # Verify the select call included pdf_url (not source_url)
        select_args = select_mock.call_args
        assert select_args is not None
        select_str = select_args[0][0]
        assert "pdf_url" in select_str, (
            f"_fetch_filings select must include pdf_url; got: {select_str!r}"
        )
        assert "source_url" not in select_str, (
            f"_fetch_filings must not select source_url (column does not exist); got: {select_str!r}"
        )

    def test_filing_id_empty_result_returns_empty_list(self):
        from scraper.reprocess_historical import _fetch_filings

        client, _ = self._make_client([])
        result = _fetch_filings(
            client,
            since=None,
            until=None,
            reprocess_all=False,
            current_parser_version="1.2.1",
            limit=100,
            filing_id=999,
        )
        assert result == []


# ── _reprocess_filing: pdf_url used as source_url for parse_text ─────────────

class TestReprocessFilingPdfUrl:
    """
    Verify that _reprocess_filing passes the pdf_url value (not an empty
    string from a missing source_url key) to parse_text.
    """

    def test_pdf_url_passed_to_parse_text(self):
        from scraper.reprocess_historical import _reprocess_filing

        filing = {
            "id": 325,
            "pdf_url": "https://www.borsaitaliana.it/nisavvsource/pdf/2025/8327.pdf",
            "pdf_sha256": "",
            "raw_extracted_text": (
                "AVVISO n.8327 del 15 Gennaio 2025\n"
                "Mittente del comunicato : UniCredit S.p.A.\n"
                "Nome: Andrea Cognome: Orcel\n"
                "Ruolo: Persona che esercita funzioni di amministrazione\n"
                "Operazione - 1\n"
                "ISIN: IT0005239360\n"
                "Natura dell'operazione\n"
                "Other - DISPOSAL (DISPOSAL TO PAY OUT TAXES RELATED TO VARIABLE REMUNERATION)\n"
                "A norma dell'articolo 19 del Regolamento\n"
                "Volume aggregato: 5000\nPrezzo: 40.25 EUR\n"
                "Data dell'operazione 2025-01-14\n"
            ),
            "source_published_utc": "2025-01-15T09:00:00Z",
        }

        client = MagicMock()
        captured_url = []

        def fake_parse_text(raw_text, source_url, filing_date, doc_sha256=""):
            captured_url.append(source_url)
            return []  # return no transactions to skip upsert

        # parse_text is imported locally inside _reprocess_filing, so patch at source
        with patch("scraper.parser.parse_text", side_effect=fake_parse_text):
            # dry_run=True skips DB writes but still calls parse_text
            result = _reprocess_filing(client, filing, "1.2.1", dry_run=True)

        assert captured_url, "parse_text was not called"
        assert captured_url[0] == filing["pdf_url"], (
            f"parse_text was called with source_url={captured_url[0]!r} "
            f"instead of pdf_url={filing['pdf_url']!r}"
        )

    def test_missing_pdf_url_falls_back_to_empty_string(self):
        """If pdf_url is absent the script must not crash — empty string is acceptable."""
        from scraper.reprocess_historical import _reprocess_filing

        filing = {
            "id": 1,
            # pdf_url deliberately omitted (simulates a very old row)
            "pdf_sha256": "",
            "raw_extracted_text": "",
            "source_published_utc": None,
        }

        client = MagicMock()
        captured = []

        def fake_parse_text(raw_text, source_url, filing_date, doc_sha256=""):
            captured.append(source_url)
            return []

        with patch("scraper.parser.parse_text", side_effect=fake_parse_text):
            _reprocess_filing(client, filing, "1.2.1", dry_run=True)

        assert captured[0] == "", "Should fall back to empty string when pdf_url is missing"
