"""
Quality-evaluation metrics — Phase 13.

Pure functions that compute accuracy, calibration, and breakdown metrics
from quality_reviews truth labels.  No DB access; all functions accept
plain dicts (as returned by the Supabase client) so they can be tested
without a live database.

Accuracy model
--------------
- outcome='confirmed'   → parser was correct; field counts as correct.
- outcome='corrected'   → check corrected_* vs original_*:
    - corrected field is None     → that field was not the error; counts as correct.
    - corrected field != original → that field was wrong; counts as incorrect.
- outcome='rejected'    → transaction should not exist; excluded from both
                           numerator and denominator.

Confidence calibration
----------------------
Uses original_classification_confidence as the proxy for overall parser
certainty.  A well-calibrated parser produces higher accuracy in higher-
confidence buckets.

Public API
----------
accuracy_from_reviews(reviews)       -> dict
calibration_table(reviews)           -> list[dict]
unknown_direction_breakdown(reviews) -> list[dict]
completeness_stats(transactions)     -> dict
issuer_resolution_stats(rows)        -> dict
"""

from typing import Any

# Confidence buckets: (label, inclusive_lo, exclusive_hi)
_CONFIDENCE_BUCKETS: list[tuple[str, float, float]] = [
    ("0.9–1.0",  0.90, 1.01),
    ("0.7–0.9",  0.70, 0.90),
    ("0.5–0.7",  0.50, 0.70),
    ("<0.5",    -0.01, 0.50),
]

# Fields that must be present and non-empty for extraction to be considered complete.
_COMPLETENESS_FIELDS = ["isin", "insider_name", "company_name", "unit_price", "quantity"]


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _direction_correct(review: dict) -> bool:
    """
    True if the original direction was correct according to the review outcome.

    'rejected' reviews must be excluded from the call site before calling this,
    but this function returns False for them defensively.
    """
    outcome = review.get("outcome", "confirmed")
    if outcome == "confirmed":
        return True
    if outcome == "rejected":
        return False
    # corrected: field is correct unless a non-None corrected_direction differs
    corr = review.get("corrected_direction")
    if corr is None:
        return True  # direction was not the field that was wrong
    return corr == review.get("original_direction")


def _type_correct(review: dict) -> bool:
    """True if the original transaction_type was correct."""
    outcome = review.get("outcome", "confirmed")
    if outcome == "confirmed":
        return True
    if outcome == "rejected":
        return False
    corr = review.get("corrected_transaction_type")
    if corr is None:
        return True
    return corr == review.get("original_transaction_type")


def _is_missing(value: Any) -> bool:
    """True if a transaction field value is absent or a known sentinel."""
    return value is None or value == "" or value == "Unknown" or value == 0.0


# ─── Public API ───────────────────────────────────────────────────────────────

def accuracy_from_reviews(reviews: list[dict]) -> dict:
    """
    Compute direction and transaction-type accuracy over a list of review dicts.

    Rejected transactions are excluded from both numerator and denominator.
    Returns a dict with:
        total_reviewed          int
        eligible                int   (total minus rejected)
        rejected                int
        direction_correct       int | None
        direction_accuracy_pct  float | None
        type_correct            int | None
        type_accuracy_pct       float | None
    """
    eligible = [r for r in reviews if r.get("outcome") != "rejected"]
    total = len(eligible)
    n_rejected = len(reviews) - total

    if total == 0:
        return {
            "total_reviewed": len(reviews),
            "eligible": 0,
            "rejected": n_rejected,
            "direction_correct": None,
            "direction_accuracy_pct": None,
            "type_correct": None,
            "type_accuracy_pct": None,
        }

    n_dir_ok  = sum(1 for r in eligible if _direction_correct(r))
    n_type_ok = sum(1 for r in eligible if _type_correct(r))

    return {
        "total_reviewed": len(reviews),
        "eligible": total,
        "rejected": n_rejected,
        "direction_correct": n_dir_ok,
        "direction_accuracy_pct": round(100 * n_dir_ok / total, 1),
        "type_correct": n_type_ok,
        "type_accuracy_pct": round(100 * n_type_ok / total, 1),
    }


def calibration_table(reviews: list[dict]) -> list[dict]:
    """
    Compute direction accuracy grouped by original_classification_confidence bucket.

    Returns a list of dicts (one per occupied bucket), sorted highest-confidence first:
        bucket          str    label ("0.9–1.0", etc.)
        n               int    reviews in this bucket
        direction_correct int  reviews where direction was correct
        accuracy_pct    float  direction accuracy for this bucket
    """
    eligible = [r for r in reviews if r.get("outcome") != "rejected"]

    results = []
    for label, lo, hi in _CONFIDENCE_BUCKETS:
        bucket_rows = [
            r for r in eligible
            if (c := r.get("original_classification_confidence")) is not None
            and lo <= c < hi
        ]
        if not bucket_rows:
            continue
        n = len(bucket_rows)
        n_correct = sum(1 for r in bucket_rows if _direction_correct(r))
        results.append({
            "bucket": label,
            "n": n,
            "direction_correct": n_correct,
            "accuracy_pct": round(100 * n_correct / n, 1),
        })
    return results


def unknown_direction_breakdown(reviews: list[dict]) -> list[dict]:
    """
    Group reviews whose original_direction was 'unknown' by the
    classification_rationale prefix (the text before the first ':').

    Returns a list of {rationale_prefix, count} sorted by count descending.
    This identifies the most common root causes of unknown-direction output.
    """
    counts: dict[str, int] = {}
    for r in reviews:
        if r.get("original_direction") != "unknown":
            continue
        rationale = r.get("original_classification_rationale") or "no_rationale"
        prefix = rationale.split(":")[0].strip()
        counts[prefix] = counts.get(prefix, 0) + 1

    return sorted(
        [{"rationale_prefix": k, "count": v} for k, v in counts.items()],
        key=lambda x: -x["count"],
    )


def completeness_stats(transactions: list[dict]) -> dict:
    """
    Compute missing-field rates over a list of transaction dicts.

    A field is considered missing if its value is None, empty string, "Unknown",
    or 0.0 (for numeric fields where zero is not a valid value).

    Returns {total, <field>_missing_pct, ...} for each field in
    _COMPLETENESS_FIELDS.
    """
    total = len(transactions)
    if total == 0:
        return {"total": 0}

    result: dict[str, Any] = {"total": total}
    for field in _COMPLETENESS_FIELDS:
        n_missing = sum(1 for r in transactions if _is_missing(r.get(field)))
        result[f"{field}_missing_pct"] = round(100 * n_missing / total, 1)
    return result


def issuer_resolution_stats(unmatched_rows: list[dict]) -> dict:
    """
    Summarise unmatched_issuers rows by status and false-match tracking.

    Returns:
        total               int
        pending             int   awaiting operator action
        resolved            int   correctly linked to an issuer
        rejected            int   confirmed as not an issuer (test data, parse error)
        false_match         int   rows where a previous match was flagged as wrong
        resolution_rate_pct float resolved / total * 100
    """
    total    = len(unmatched_rows)
    pending  = sum(1 for r in unmatched_rows if r.get("status") == "pending")
    resolved = sum(1 for r in unmatched_rows if r.get("status") == "resolved")
    rejected = sum(1 for r in unmatched_rows if r.get("status") == "rejected")
    false_match = sum(
        1 for r in unmatched_rows if r.get("false_match_issuer_id") is not None
    )

    return {
        "total": total,
        "pending": pending,
        "resolved": resolved,
        "rejected": rejected,
        "false_match": false_match,
        "resolution_rate_pct": round(100 * resolved / max(total, 1), 1),
    }
