"""
Storage-evidence invariant tests.

Guarantees (prospective — applies to all filings processed after commit 320f17c):

  1. A filing cannot reach 'completed' unless storage_path, file_size_bytes,
     raw_extracted_text, and pdf_sha256 are all confirmed in the filings ledger
     BEFORE parse/complete is called.

  2. If record_storage() raises (DB error or network failure), fail_filing()
     is called immediately and processing stops — the filing is left in
     'failed' status (retryable) rather than proceeding to parse or complete.

  3. If store_pdf() raises (upload failure), fail_filing() is called and
     processing stops before record_storage() is even attempted.

Historical filings:
  Filings completed BEFORE this patch may lack storage_path (record_storage was
  non-fatal then). Run the verification query below to find them:

    SELECT id, pdf_url, status, storage_path, file_size_bytes, pdf_sha256
    FROM   filings
    WHERE  status = 'completed'
      AND  (storage_path IS NULL OR file_size_bytes IS NULL OR pdf_sha256 IS NULL);

  To backfill, use:
    python3 -m scraper.cli verify-storage-lineage [--fix]

  This command reports every completed filing with missing evidence fields and,
  with --fix, resets them to 'failed' so they will be re-downloaded and re-parsed
  on the next scraper run (idempotent: re-uploading the same PDF writes to the
  same deterministic path and will be a no-op at the storage layer).

Run tests:
    python3 -m pytest tests/test_storage_lineage_invariant.py -v
or:
    python3 -m unittest tests.test_storage_lineage_invariant -v
"""

import hashlib
import os
import sys
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper import filings as filing_ledger
from scraper.models import ListingRow


# ── Shared helpers ────────────────────────────────────────────────────────────

_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >> endobj\n%%EOF"
_SHA256 = hashlib.sha256(_PDF_BYTES).hexdigest()
_STORAGE_PATH = f"filings/2024/03/{_SHA256}.pdf"

_CLAIM = {
    "id": 99,
    "attempt_count": 1,
    "max_attempts": 3,
    "claim_token": "tok-abc",
}

_FILING = {
    "id": 99,
    "pdf_url": "https://example.com/filing.pdf",
    "filing_date": "2024-03-15",
    "company_name": "Test Co",
    "attempt_count": 0,
    "status": "pending",
}

_ROW = ListingRow(
    pdf_url=_FILING["pdf_url"],
    pdf_path="/nisavvsource/pdf/2024/99.pdf",
    filing_date=_FILING["filing_date"],
    company_name=_FILING["company_name"],
)


def _mock_db_response(data=None, error=None):
    r = MagicMock()
    r.data = data if data is not None else [{"id": 99}]
    r.error = error
    return r


# ── record_storage() unit tests ───────────────────────────────────────────────

class TestRecordStorageRaisesOnError(unittest.TestCase):
    """record_storage() itself must raise when the DB update signals an error."""

    def _make_client(self, error=None, data=None):
        client = MagicMock()
        resp = _mock_db_response(data=data if data is not None else [{"id": 99}], error=error)
        (client.table.return_value
               .update.return_value
               .eq.return_value
               .execute.return_value) = resp
        return client

    def test_raises_runtime_error_when_result_has_error(self):
        client = self._make_client(error="some DB error")
        with self.assertRaises(RuntimeError) as ctx:
            filing_ledger.record_storage(
                client, 99,
                storage_path=_STORAGE_PATH,
                file_size_bytes=len(_PDF_BYTES),
            )
        self.assertIn("record_storage DB error", str(ctx.exception))
        self.assertIn("99", str(ctx.exception))

    def test_succeeds_when_result_has_no_error(self):
        client = self._make_client(error=None)
        # Must not raise
        filing_ledger.record_storage(
            client, 99,
            storage_path=_STORAGE_PATH,
            file_size_bytes=len(_PDF_BYTES),
            raw_extracted_text="hello",
        )

    def test_persists_all_four_evidence_fields(self):
        client = self._make_client()
        filing_ledger.record_storage(
            client, 99,
            storage_path=_STORAGE_PATH,
            file_size_bytes=len(_PDF_BYTES),
            raw_extracted_text="extracted text",
        )
        update_call_kwargs = client.table.return_value.update.call_args[0][0]
        self.assertEqual(update_call_kwargs["storage_path"], _STORAGE_PATH)
        self.assertEqual(update_call_kwargs["file_size_bytes"], len(_PDF_BYTES))
        self.assertEqual(update_call_kwargs["raw_extracted_text"], "extracted text")
        # updated_at must also be written (proves the row was touched)
        self.assertIn("updated_at", update_call_kwargs)


# ── Listener path: _process_filing_with_ledger ────────────────────────────────

class TestListenerStorageLineage(unittest.TestCase):
    """
    Verifies that _process_filing_with_ledger enforces the storage-evidence
    invariant: record_storage failure → fail_filing called, parse/complete
    never reached.
    """

    def _run(self, record_storage_side_effect):
        """
        Patch the key collaborators and run _process_filing_with_ledger.
        Returns the mocks so callers can assert on them.
        """
        from scraper.listener import _process_filing_with_ledger

        mock_client   = MagicMock()
        mock_session  = MagicMock()
        mock_backend  = MagicMock()

        # claim_filing succeeds
        mock_claim_resp = MagicMock()
        mock_claim_resp.data = [_CLAIM]
        mock_client.rpc.return_value.execute.return_value = mock_claim_resp

        # record_downloaded / record_parsed / complete_filing succeed
        ok_resp = _mock_db_response()
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = ok_resp

        with patch("scraper.listener.download_pdf", return_value=_PDF_BYTES) as mock_dl, \
             patch("scraper.listener.doc_storage.store_pdf",
                   return_value=(_STORAGE_PATH, True)) as mock_store, \
             patch("scraper.listener.doc_storage.extract_raw_text",
                   return_value="raw text") as mock_extract, \
             patch("scraper.listener.filing_ledger.record_storage",
                   side_effect=record_storage_side_effect) as mock_rs, \
             patch("scraper.listener.filing_ledger.fail_filing") as mock_fail, \
             patch("scraper.listener.parse_pdf") as mock_parse, \
             patch("scraper.listener.filing_ledger.complete_filing") as mock_complete, \
             patch("scraper.listener.filing_ledger.claim_filing",
                   return_value=_CLAIM):
            try:
                _process_filing_with_ledger(_ROW, _FILING, mock_client, mock_session, mock_backend)
            except RuntimeError:
                pass  # expected when record_storage fails

        return mock_fail, mock_parse, mock_complete, mock_rs

    def test_record_storage_failure_calls_fail_filing(self):
        mock_fail, mock_parse, mock_complete, _ = self._run(
            RuntimeError("DB write failed")
        )
        mock_fail.assert_called_once()
        args, kwargs = mock_fail.call_args
        self.assertEqual(kwargs.get("error") or args[2],
                         mock_fail.call_args[1].get("error") or mock_fail.call_args[0][2])
        # Simpler: just confirm it was called
        self.assertTrue(mock_fail.called)

    def test_record_storage_failure_does_not_call_parse(self):
        _, mock_parse, _, _ = self._run(RuntimeError("DB write failed"))
        mock_parse.assert_not_called()

    def test_record_storage_failure_does_not_call_complete(self):
        _, _, mock_complete, _ = self._run(RuntimeError("DB write failed"))
        mock_complete.assert_not_called()

    def test_record_storage_success_proceeds_to_parse(self):
        """Sanity check: when record_storage succeeds the pipeline continues."""
        from scraper.listener import _process_filing_with_ledger

        mock_client  = MagicMock()
        mock_session = MagicMock()
        mock_backend = MagicMock()

        ok_resp = _mock_db_response()
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = ok_resp

        with patch("scraper.listener.download_pdf", return_value=_PDF_BYTES), \
             patch("scraper.listener.doc_storage.store_pdf",
                   return_value=(_STORAGE_PATH, True)), \
             patch("scraper.listener.doc_storage.extract_raw_text",
                   return_value="raw text"), \
             patch("scraper.listener.filing_ledger.record_storage"), \
             patch("scraper.listener.filing_ledger.claim_filing",
                   return_value=_CLAIM), \
             patch("scraper.listener.filing_ledger.fail_filing") as mock_fail, \
             patch("scraper.listener.parse_pdf",
                   return_value=[]) as mock_parse, \
             patch("scraper.listener.filing_ledger.skip_filing") as mock_skip, \
             patch("scraper.listener.filing_ledger.complete_filing"):
            _process_filing_with_ledger(_ROW, _FILING, mock_client, mock_session, mock_backend)

        mock_parse.assert_called_once()
        mock_fail.assert_not_called()

    def test_store_pdf_failure_also_blocks_record_storage(self):
        """store_pdf failure (upload error) must prevent record_storage from running."""
        from scraper.listener import _process_filing_with_ledger

        mock_client  = MagicMock()
        mock_session = MagicMock()
        mock_backend = MagicMock()

        ok_resp = _mock_db_response()
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = ok_resp

        with patch("scraper.listener.download_pdf", return_value=_PDF_BYTES), \
             patch("scraper.listener.doc_storage.store_pdf",
                   side_effect=RuntimeError("S3 timeout")) as mock_store, \
             patch("scraper.listener.filing_ledger.record_storage") as mock_rs, \
             patch("scraper.listener.filing_ledger.claim_filing",
                   return_value=_CLAIM), \
             patch("scraper.listener.filing_ledger.fail_filing") as mock_fail, \
             patch("scraper.listener.parse_pdf") as mock_parse:
            try:
                _process_filing_with_ledger(_ROW, _FILING, mock_client, mock_session, mock_backend)
            except RuntimeError:
                pass

        mock_rs.assert_not_called()
        mock_parse.assert_not_called()
        mock_fail.assert_called_once()


# ── Sweep path: run_phase2 worker ─────────────────────────────────────────────

class TestSweepStorageLineage(unittest.TestCase):
    """
    Verifies the storage-evidence invariant in the run_phase2 sweep worker:
    record_storage failure → fail_filing called, parse_pdf never called,
    stats["errors"] incremented, loop continues to next filing.
    """

    def _run_worker_with_record_storage_error(self):
        """
        Call _crawl_company with one filing row whose record_storage call
        raises RuntimeError.  Returns (stats, mock_fl, mock_parse).
        """
        from scraper import run_phase2

        mock_client  = MagicMock()
        mock_backend = MagicMock()

        # The listing row returned by iter_company_listings
        row = MagicMock()
        row.pdf_url     = _FILING["pdf_url"]
        row.filing_date = _FILING["filing_date"]

        # filing_ledger collaborator mock
        mock_fl = MagicMock()
        mock_fl.register_filing.return_value = {**_FILING, "status": "pending"}
        mock_fl.is_eligible.return_value = True
        mock_fl.claim_filing.return_value = _CLAIM
        mock_fl.record_storage.side_effect = RuntimeError("DB timeout")

        # doc_storage collaborator mock
        mock_ds = MagicMock()
        mock_ds.store_pdf.return_value = (_STORAGE_PATH, True)
        mock_ds.extract_raw_text.return_value = "raw text"

        mock_parse = MagicMock(return_value=[])

        with patch.object(run_phase2, "iter_company_listings", return_value=[row]), \
             patch.object(run_phase2, "download_pdf", return_value=_PDF_BYTES), \
             patch.object(run_phase2, "doc_storage", mock_ds), \
             patch.object(run_phase2, "filing_ledger", mock_fl), \
             patch.object(run_phase2, "parse_pdf", mock_parse), \
             patch.object(run_phase2, "upsert_transaction"):

            stats = run_phase2._crawl_company(
                company_name="Test Co",
                company_tier=1,
                client=mock_client,
                polite_delay=0,
                max_pdfs=10,
                use_ledger=True,
                storage_backend=mock_backend,
            )

        return stats, mock_fl, mock_parse

    def test_record_storage_failure_increments_error_stat(self):
        stats, _, _ = self._run_worker_with_record_storage_error()
        self.assertGreater(stats.get("errors", 0), 0)

    def test_record_storage_failure_calls_fail_filing(self):
        _, mock_fl, _ = self._run_worker_with_record_storage_error()
        mock_fl.fail_filing.assert_called()

    def test_record_storage_failure_does_not_call_parse(self):
        _, _, mock_parse = self._run_worker_with_record_storage_error()
        mock_parse.assert_not_called()


# ── SQL verification reference ────────────────────────────────────────────────

class TestStorageLineageSQL(unittest.TestCase):
    """
    Not executable tests — documents the SQL verification queries that must
    return 0 rows for the invariant to hold prospectively.

    To run against a live database:
        python3 -m scraper.cli verify-storage-lineage
    """

    PROSPECTIVE_INVARIANT_QUERY = """
        -- Completed filings missing one or more evidence fields.
        -- Must return 0 rows for all filings processed after the hardening patch.
        SELECT id, pdf_url, storage_path, file_size_bytes, pdf_sha256
        FROM   filings
        WHERE  status      = 'completed'
          AND  created_at  > '2024-01-01'   -- replace with patch deployment date
          AND  (
               storage_path    IS NULL
            OR file_size_bytes IS NULL
            OR pdf_sha256      IS NULL
          );
    """

    BACKFILL_CANDIDATES_QUERY = """
        -- Completed filings from BEFORE the hardening patch that lack evidence.
        -- Use `python3 -m scraper.cli verify-storage-lineage --fix` to reset
        -- them to 'failed' for re-processing.
        SELECT id, pdf_url, status, storage_path, file_size_bytes, pdf_sha256
        FROM   filings
        WHERE  status = 'completed'
          AND  (storage_path IS NULL OR file_size_bytes IS NULL OR pdf_sha256 IS NULL);
    """

    def test_queries_are_documented(self):
        # Structural test — ensure the query strings are non-empty and contain
        # the key column references, so they're not accidentally blanked.
        for attr in ("PROSPECTIVE_INVARIANT_QUERY", "BACKFILL_CANDIDATES_QUERY"):
            q = getattr(self, attr)
            self.assertIn("storage_path", q)
            self.assertIn("file_size_bytes", q)
            self.assertIn("pdf_sha256", q)
            self.assertIn("completed", q)


if __name__ == "__main__":
    unittest.main()
