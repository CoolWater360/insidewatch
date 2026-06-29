"""Static guard for the Phase 17B.3 pilot issuer seed.

No database required.  These tests assert that data/seed_pilot_issuers.csv is
well-formed and — crucially — that for each supplied CONSOB pilot notice, the
exact issuer raw name parsed from the source resolves (case-insensitively) to a
seeded canonical name or alias.  This proves the seed will make the ownership
dry run resolve each issuer by EXACT match, without fuzzy matching.
"""

import csv
import os

import pytest

from scraper.ownership import archive_html

REPO = os.path.dirname(os.path.dirname(__file__))
SEED = os.path.join(REPO, "data", "seed_pilot_issuers.csv")
FIX = os.path.join(REPO, "tests", "fixtures", "ownership")

PILOT_ISINS = {"IT0004764699", "IT0000062957", "IT0005253205"}

# (fixture file, issuer filter, expected pct) — mirrors collector.PILOT_RECORDS
PILOT_FIXTURES = [
    ("cucinelli_aggregate_2026-05-06.html", "BRUNELLO CUCINELLI", 5.588,
     "https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-2026-05-06"),
    ("mediobanca_azioni_2025-09-23.html", "MEDIOBANCA", 3.069,
     "https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-2025-09-23"),
    ("italmobiliare_azioni_2026-06-03.html", "ITALMOBILIARE", 3.001,
     "https://www.consob.it/web/area-pubblica/w/comunicazioni-relative-a-partecipazioni-rilevanti-2026-06-03"),
]


def _rows():
    with open(SEED, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _seed_match_strings(row):
    """All strings that resolve_issuer_exact would match (canonical + aliases)."""
    out = [row["canonical_name"].strip()]
    out += [a.strip() for a in row.get("aliases", "").split("|") if a.strip()]
    return out


class TestSeedFileShape:
    def test_exactly_three_pilot_issuers(self):
        rows = _rows()
        assert len(rows) == 3

    def test_required_columns_present(self):
        for row in _rows():
            assert row["canonical_name"].strip()
            assert row["isin"].strip() in PILOT_ISINS
            assert row["ticker"].strip()
            assert row["country"].strip() == "IT"

    def test_isins_are_the_authoritative_set(self):
        assert {r["isin"].strip() for r in _rows()} == PILOT_ISINS

    def test_no_lei_inferred(self):
        # LEIs are deliberately omitted (not GLEIF-verified in this pass).
        for row in _rows():
            assert row.get("lei", "").strip() == ""

    def test_canonical_names(self):
        canon = {r["canonical_name"].strip() for r in _rows()}
        assert canon == {
            "Brunello Cucinelli S.p.A.",
            "Mediobanca S.p.A.",
            "Italmobiliare S.p.A.",
        }


class TestSeedResolvesEachPilotNotice:
    """The decisive guard: each CONSOB raw issuer name maps to a seeded string."""

    @pytest.mark.parametrize("fixture,issuer_filter,expected_pct,url", PILOT_FIXTURES)
    def test_raw_name_has_exact_seed_match(self, fixture, issuer_filter, expected_pct, url):
        with open(os.path.join(FIX, fixture), encoding="utf-8") as f:
            html = f.read()
        recs = archive_html.parse_archive_page(
            html, source_url=url, issuer_filter=issuer_filter
        )
        target = next(
            (r for r in recs if r.voting_pct_after == pytest.approx(expected_pct)),
            None,
        )
        assert target is not None, f"target record not parsed from {fixture}"
        raw = target.issuer_raw_name.strip().upper()

        # Collect every seeded match-string across all rows (case-insensitive).
        all_match = []
        for row in _rows():
            all_match += [s.upper() for s in _seed_match_strings(row)]

        assert raw in all_match, (
            f"CONSOB raw issuer name {target.issuer_raw_name!r} has no exact "
            f"seed match — ownership resolution would stay blocked."
        )

    def test_cucinelli_raw_form_is_seeded_alias(self):
        # Explicit: the exact uppercase CONSOB form must be a seeded alias.
        all_aliases = []
        for row in _rows():
            all_aliases += [a.strip().upper() for a in row.get("aliases", "").split("|")]
        assert "BRUNELLO CUCINELLI SPA" in all_aliases
        assert "MEDIOBANCA - BANCA DI CREDITO FINANZIARIO S.P.A." in all_aliases
        assert "ITALMOBILIARE SPA" in all_aliases
