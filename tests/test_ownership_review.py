"""Tests for the ownership-pilot review logic (Phase 17B.5).

Pure logic only — recommendation engine + report rendering.  No DB.
Ingestion semantics are not exercised or changed here.
"""

import pytest

from scraper.ownership.review import (
    EntityTypeRecommendation,
    build_entity_recommendations,
    recommend_entity_type,
    render_report,
)

# The six entities exactly as applied in Phase 17B.4.
PILOT_ENTITIES = [
    {"id": 1, "legal_name": "FMR LLC", "entity_type": "company"},
    {"id": 2, "legal_name": "THE GOLDMAN SACHS GROUP, INC.", "entity_type": "company"},
    {"id": 3, "legal_name": "GOLDMAN SACHS INTERNATIONAL", "entity_type": "natural_person"},
    {"id": 4, "legal_name": "GOLDMAN SACHS BANK EUROPE SE", "entity_type": "company"},
    {"id": 5, "legal_name": "MORGAN STANLEY", "entity_type": "natural_person"},
    {"id": 6, "legal_name": "Morgan Stanley & Co. International plc", "entity_type": "company"},
]


class TestRecommendEntityType:
    def test_flags_org_tagged_as_person(self):
        rec = recommend_entity_type("GOLDMAN SACHS INTERNATIONAL", "natural_person")
        assert rec is not None
        proposed, reason = rec
        assert proposed == "company"
        assert "INTERNATIONAL" in reason

    def test_flags_morgan_stanley(self):
        rec = recommend_entity_type("MORGAN STANLEY", "natural_person")
        assert rec is not None and rec[0] == "company"

    def test_does_not_touch_existing_company(self):
        # Already 'company' — never downgrade or re-flag.
        assert recommend_entity_type("GOLDMAN SACHS BANK EUROPE SE", "company") is None

    def test_real_person_not_flagged(self):
        # A genuine natural-person name with no org token → no recommendation.
        assert recommend_entity_type("Mario Rossi", "natural_person") is None
        assert recommend_entity_type("Brunello Cucinelli", "natural_person") is None

    def test_non_person_type_never_flagged(self):
        assert recommend_entity_type("ANYTHING INTERNATIONAL", "holding_company") is None


class TestBuildEntityRecommendations:
    def test_only_two_pilot_entities_flagged(self):
        recs = build_entity_recommendations(PILOT_ENTITIES)
        ids = sorted(r.entity_id for r in recs)
        assert ids == [3, 5]
        assert all(isinstance(r, EntityTypeRecommendation) for r in recs)
        assert all(r.proposed_type == "company" for r in recs)
        assert all(r.requires_operator_approval for r in recs)

    def test_correctly_typed_entities_untouched(self):
        recs = build_entity_recommendations(PILOT_ENTITIES)
        flagged = {r.entity_id for r in recs}
        for correct_id in (1, 2, 4, 6):
            assert correct_id not in flagged


class TestRenderReport:
    def test_report_contains_sections_and_recommendations(self):
        recs = build_entity_recommendations(PILOT_ENTITIES)
        events = [
            {"id": 1, "issuer_id": 1, "raw_entity_name": "FMR LLC",
             "event_type": "other", "voting_pct_after": 5.588},
        ]
        rels = [
            {"id": 1, "subject_entity_id": 2, "relationship_type": "controls",
             "object_entity_id": 3, "object_issuer_id": None},
        ]
        out = render_report(PILOT_ENTITIES, events, rels, recs)
        assert "Entities pending review: 6" in out
        assert "Ownership events pending review: 1" in out
        assert "Relationships pending review: 1" in out
        assert "RECOMMENDATIONS (require operator approval): 2" in out
        # The apply command must be shown but is operator-run, not automatic.
        assert "--approve-entity-type 3 --to company" in out
        assert "--approve-entity-type 5 --to company" in out

    def test_report_with_no_recommendations(self):
        clean = [{"id": 1, "legal_name": "FMR LLC", "entity_type": "company"}]
        out = render_report(clean, [], [], build_entity_recommendations(clean))
        assert "RECOMMENDATIONS (require operator approval): 0" in out
        assert "(none)" in out
