"""
Storage adapter tests — Phase 3.

Tests the core behaviour of LocalStorageBackend and the path-generation logic
without requiring a real Supabase connection.  SupabaseStorageBackend is tested
with mocks.

Run with:
    python3 -m pytest tests/test_storage.py -v
or:
    python3 -m unittest tests.test_storage -v
"""

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Ensure the project root is on sys.path when running without pytest config.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.storage import (
    LocalStorageBackend,
    SupabaseStorageBackend,
    extract_raw_text,
    get_storage_backend,
    make_storage_path,
    store_pdf,
    verify_sha256,
)


# ── Minimal synthetic PDF bytes (valid enough for hashlib; not for pdfplumber) ─

_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >> endobj\n%%EOF"
_SHA256    = hashlib.sha256(_PDF_BYTES).hexdigest()


# ── make_storage_path ─────────────────────────────────────────────────────────

class TestMakeStoragePath(unittest.TestCase):

    def test_dated_filing_uses_year_and_month(self):
        path = make_storage_path("abc123", "2024-03-15")
        self.assertEqual(path, "filings/2024/03/abc123.pdf")

    def test_month_is_zero_padded(self):
        path = make_storage_path("abc123", "2024-01-05")
        self.assertEqual(path, "filings/2024/01/abc123.pdf")

    def test_none_date_falls_back_to_undated(self):
        path = make_storage_path("abc123", None)
        self.assertEqual(path, "filings/undated/abc123.pdf")

    def test_empty_string_date_falls_back_to_undated(self):
        path = make_storage_path("abc123", "")
        self.assertEqual(path, "filings/undated/abc123.pdf")

    def test_invalid_date_falls_back_to_undated(self):
        path = make_storage_path("abc123", "not-a-date")
        self.assertEqual(path, "filings/undated/abc123.pdf")

    def test_sha256_is_the_filename(self):
        sha = "a" * 64
        path = make_storage_path(sha, "2024-06-01")
        self.assertTrue(path.endswith(f"/{sha}.pdf"))

    def test_path_always_starts_with_filings(self):
        for date in ("2024-01-01", None, "bad"):
            with self.subTest(date=date):
                path = make_storage_path("sha", date)
                self.assertTrue(path.startswith("filings/"))


# ── LocalStorageBackend ───────────────────────────────────────────────────────

class TestLocalStorageBackend(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root    = self._tmpdir.name
        self.backend = LocalStorageBackend(root=self.root)
        self.path    = "filings/2024/03/test.pdf"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_upload_returns_true_for_new_file(self):
        is_new = self.backend.upload(_PDF_BYTES, self.path)
        self.assertTrue(is_new)

    def test_upload_creates_file_at_correct_path(self):
        self.backend.upload(_PDF_BYTES, self.path)
        dest = Path(self.root) / self.path
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_bytes(), _PDF_BYTES)

    def test_upload_returns_false_for_existing_file(self):
        self.backend.upload(_PDF_BYTES, self.path)
        is_new = self.backend.upload(_PDF_BYTES, self.path)
        self.assertFalse(is_new)

    def test_idempotent_upload_does_not_overwrite(self):
        self.backend.upload(_PDF_BYTES, self.path)
        other_bytes = b"different"
        self.backend.upload(other_bytes, self.path)  # should be silently skipped
        downloaded = self.backend.download(self.path)
        self.assertEqual(downloaded, _PDF_BYTES)     # original still there

    def test_exists_false_before_upload(self):
        self.assertFalse(self.backend.exists(self.path))

    def test_exists_true_after_upload(self):
        self.backend.upload(_PDF_BYTES, self.path)
        self.assertTrue(self.backend.exists(self.path))

    def test_download_returns_original_bytes(self):
        self.backend.upload(_PDF_BYTES, self.path)
        result = self.backend.download(self.path)
        self.assertEqual(result, _PDF_BYTES)

    def test_download_raises_for_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            self.backend.download("filings/undated/does_not_exist.pdf")

    def test_file_size_returns_correct_size(self):
        self.backend.upload(_PDF_BYTES, self.path)
        self.assertEqual(self.backend.file_size(self.path), len(_PDF_BYTES))

    def test_file_size_returns_zero_for_missing(self):
        self.assertEqual(self.backend.file_size("filings/undated/nope.pdf"), 0)

    def test_upload_creates_intermediate_directories(self):
        deep_path = "filings/2099/12/abcdef.pdf"
        self.backend.upload(_PDF_BYTES, deep_path)
        self.assertTrue((Path(self.root) / deep_path).exists())


# ── store_pdf helper ──────────────────────────────────────────────────────────

class TestStorePdf(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.backend = LocalStorageBackend(root=self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_returns_deterministic_path(self):
        path, _ = store_pdf(self.backend, _PDF_BYTES, _SHA256, "2024-03-15")
        self.assertEqual(path, f"filings/2024/03/{_SHA256}.pdf")

    def test_is_new_true_on_first_store(self):
        _, is_new = store_pdf(self.backend, _PDF_BYTES, _SHA256, "2024-03-15")
        self.assertTrue(is_new)

    def test_is_new_false_on_second_store(self):
        store_pdf(self.backend, _PDF_BYTES, _SHA256, "2024-03-15")
        _, is_new = store_pdf(self.backend, _PDF_BYTES, _SHA256, "2024-03-15")
        self.assertFalse(is_new)


# ── verify_sha256 ─────────────────────────────────────────────────────────────

class TestVerifySha256(unittest.TestCase):

    def test_correct_hash_returns_true(self):
        self.assertTrue(verify_sha256(_PDF_BYTES, _SHA256))

    def test_wrong_hash_returns_false(self):
        self.assertFalse(verify_sha256(_PDF_BYTES, "0" * 64))

    def test_wrong_bytes_returns_false(self):
        self.assertFalse(verify_sha256(b"other bytes", _SHA256))


# ── extract_raw_text ──────────────────────────────────────────────────────────

class TestExtractRawText(unittest.TestCase):

    def test_returns_string_for_invalid_pdf(self):
        # Minimal bytes that are not a valid PDF — should not raise.
        result = extract_raw_text(b"not a pdf")
        self.assertIsInstance(result, str)

    def test_empty_bytes_returns_string(self):
        result = extract_raw_text(b"")
        self.assertIsInstance(result, str)


# ── get_storage_backend factory ───────────────────────────────────────────────

class TestGetStorageBackend(unittest.TestCase):

    def test_default_is_local(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STORAGE_BACKEND", None)
            backend = get_storage_backend()
        self.assertIsInstance(backend, LocalStorageBackend)

    def test_local_env_var_returns_local(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            backend = get_storage_backend()
        self.assertIsInstance(backend, LocalStorageBackend)

    def test_supabase_without_client_raises(self):
        with patch.dict(os.environ, {"STORAGE_BACKEND": "supabase"}):
            with self.assertRaises(ValueError):
                get_storage_backend(supabase_client=None)

    def test_supabase_with_client_returns_supabase_backend(self):
        mock_client = MagicMock()
        mock_client.storage.from_.return_value = MagicMock()
        with patch.dict(os.environ, {"STORAGE_BACKEND": "supabase"}):
            backend = get_storage_backend(supabase_client=mock_client)
        self.assertIsInstance(backend, SupabaseStorageBackend)


# ── SupabaseStorageBackend (mocked) ──────────────────────────────────────────

class TestSupabaseStorageBackend(unittest.TestCase):

    def _make_backend(self, bucket_listing=None):
        client = MagicMock()
        bucket_mock = MagicMock()
        client.storage.from_.return_value = bucket_mock
        if bucket_listing is not None:
            bucket_mock.list.return_value = bucket_listing
        self.bucket_mock = bucket_mock
        return SupabaseStorageBackend(client, bucket="filings-pdfs")

    def test_upload_calls_bucket_upload_for_new_file(self):
        backend = self._make_backend(bucket_listing=[])
        backend.upload(_PDF_BYTES, "filings/2024/03/abc.pdf")
        self.bucket_mock.upload.assert_called_once()

    def test_upload_returns_true_for_new_file(self):
        backend = self._make_backend(bucket_listing=[])
        result = backend.upload(_PDF_BYTES, "filings/2024/03/abc.pdf")
        self.assertTrue(result)

    def test_upload_skips_when_file_already_listed(self):
        backend = self._make_backend(bucket_listing=[{"name": "abc.pdf"}])
        result = backend.upload(_PDF_BYTES, "filings/2024/03/abc.pdf")
        self.assertFalse(result)
        self.bucket_mock.upload.assert_not_called()

    def test_exists_false_when_listing_empty(self):
        backend = self._make_backend(bucket_listing=[])
        self.assertFalse(backend.exists("filings/2024/03/abc.pdf"))

    def test_exists_true_when_name_in_listing(self):
        backend = self._make_backend(bucket_listing=[{"name": "abc.pdf"}])
        self.assertTrue(backend.exists("filings/2024/03/abc.pdf"))

    def test_download_delegates_to_bucket(self):
        backend = self._make_backend()
        self.bucket_mock.download.return_value = _PDF_BYTES
        result = backend.download("filings/2024/03/abc.pdf")
        self.assertEqual(result, _PDF_BYTES)
        self.bucket_mock.download.assert_called_once_with("filings/2024/03/abc.pdf")

    def test_concurrent_upload_race_treated_as_idempotent(self):
        backend = self._make_backend(bucket_listing=[])
        self.bucket_mock.upload.side_effect = Exception("already exists: 409")
        result = backend.upload(_PDF_BYTES, "filings/2024/03/abc.pdf")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
