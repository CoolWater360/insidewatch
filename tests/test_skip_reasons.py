"""
Tests for skip reason enrichment in scraper/run_phase2.py.

The skip reason passed to filing_ledger.skip_filing() should distinguish:
  1. raw_text is None  → generic fallback (storage not used / no storage backend)
  2. raw_text is empty → text extraction failure (pdfplumber / download corruption)
  3. raw_text is non-empty → parse/layout failure or genuinely empty filing

These are structural tests: they read the source of run_phase2.py to confirm
all three distinct reason strings are present and that the branching logic
references raw_text, without needing to run the full scraper stack.
"""

import pathlib
import re
import unittest

_SRC = pathlib.Path(__file__).parent.parent / "scraper" / "run_phase2.py"


class TestSkipReasonStrings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _SRC.read_text()

    def test_extraction_failure_reason_present(self):
        self.assertIn(
            "no text extracted from PDF",
            self.src,
            "Missing skip reason for text-extraction failure",
        )

    def test_parse_layout_failure_reason_present(self):
        self.assertIn(
            "no transactions in extracted text",
            self.src,
            "Missing skip reason for parse/layout failure",
        )

    def test_generic_fallback_reason_present(self):
        self.assertIn(
            "no transactions parsed from PDF",
            self.src,
            "Missing generic skip reason fallback",
        )

    def test_branching_on_raw_text(self):
        # The skip reason block must branch on raw_text, not a hardcoded string
        self.assertRegex(
            self.src,
            r"if raw_text is None",
            "Expected 'if raw_text is None' branch for skip reason selection",
        )

    def test_raw_text_initialized_before_storage_block(self):
        # raw_text = None must appear before the storage block so it is always
        # defined even when storage_backend is None
        init_pos = self.src.find("raw_text = None")
        storage_pos = self.src.find("raw_text = doc_storage.extract_raw_text")
        self.assertGreater(init_pos, -1, "raw_text = None initialisation not found")
        self.assertGreater(storage_pos, -1, "raw_text assignment in storage block not found")
        self.assertLess(
            init_pos, storage_pos,
            "raw_text = None must appear before the storage block assignment",
        )

    def test_combined_old_reason_not_sole_reason(self):
        # Old single-string reason must appear exactly as the fallback branch,
        # not as the only reason (which would mean the branching was reverted)
        occurrences = self.src.count('"no transactions parsed from PDF"')
        self.assertEqual(
            occurrences, 1,
            "Expected exactly one occurrence of the fallback reason string",
        )


if __name__ == "__main__":
    unittest.main()
