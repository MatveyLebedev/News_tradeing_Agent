"""
Multi-target outcome classifiers (Modification Plan Phase 11) — PURE functions used by
grade_results to fill the per-node target set. Grading measures outcomes only; every
input here is post-horizon data that was unknowable at as_of (that is the point).

Targets → which node they grade:
  worst_position_return       risk sizing / exit discipline (max adverse excursion)
  classify_fundamentals_realized  the fundamentals node's confirms/contradicts verdict
  classify_shift_outcome      shift characterization (did the news break persist?)
"""
from typing import Any, Dict, List, Optional, Tuple

# realized-fundamentals thresholds (percentage points): below = noise, not a trend call
REV_YOY_PP = 2.0
MARGIN_PP = 1.0

# post-window mean AFFECTS sentiment: |mean| below this is neither persistence nor reversal
SHIFT_SENT_BAND = 0.05


def worst_position_return(closes: List[Tuple[Any, float]], direction: str) -> Optional[float]:
    """Max adverse excursion: the WORST direction-adjusted position return over the price
    path [(date, close), entry first]. LONG suffers on drops, SHORT on rallies."""
    if not closes or len(closes) < 2 or direction not in ("LONG", "SHORT"):
        return None
    entry = closes[0][1]
    if not entry:
        return None
    sign = 1.0 if direction == "LONG" else -1.0
    return round(min(sign * (px - entry) / entry for _, px in closes[1:]), 4)


def classify_fundamentals_realized(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Compare the NEXT filed quarter's picture vs the one visible at as_of — the ground
    truth the fundamentals verdict implicitly predicted. Returns components + a label in
    the verdict vocabulary (improving/deteriorating/stable/unknown)."""
    rev_b, rev_a = before.get("revenue_yoy"), after.get("revenue_yoy")

    def _margin(f):
        series = f.get("margin_series") or []
        if not series:
            return None
        m = series[0] or {}
        return m.get("net_margin", m.get("operating_margin"))

    m_b, m_a = _margin(before), _margin(after)
    if rev_a is None and m_a is None:
        return {"label": "unknown", "note": "no post-horizon filing visible"}
    votes = []
    rev_d = m_d = None
    if rev_b is not None and rev_a is not None:
        rev_d = round(rev_a - rev_b, 2)
        votes.append(1 if rev_d > REV_YOY_PP else (-1 if rev_d < -REV_YOY_PP else 0))
    if m_b is not None and m_a is not None:
        m_d = round(m_a - m_b, 2)
        votes.append(1 if m_d > MARGIN_PP else (-1 if m_d < -MARGIN_PP else 0))
    if not votes:
        return {"label": "unknown", "note": "no comparable metric across filings"}
    score = sum(votes)
    label = "improving" if score > 0 else ("deteriorating" if score < 0 else "stable")
    return {"label": label, "revenue_yoy_delta_pp": rev_d, "margin_delta_pp": m_d,
            "revenue_yoy_before": rev_b, "revenue_yoy_after": rev_a}


def classify_shift_outcome(direction: str, post_daily_means: List[float]) -> Dict[str, Any]:
    """Did the detected sentiment break PERSIST or MEAN-REVERT over the horizon? Input is
    the post-as_of daily mean AFFECTS series (graph labels, ±1/0 — same estimator family
    as detection v2, so the target grades what detection actually measured)."""
    if not post_daily_means:
        return {"label": "unknown", "note": "no post-window articles"}
    mean_post = sum(post_daily_means) / len(post_daily_means)
    sign = {"positive": 1.0, "negative": -1.0}.get(direction)
    if sign is None:
        return {"label": "unknown", "note": f"direction {direction!r} not gradable"}
    aligned = sign * mean_post
    label = ("persisted" if aligned > SHIFT_SENT_BAND
             else "reverted" if aligned < -SHIFT_SENT_BAND else "mixed")
    return {"label": label, "post_mean_sentiment": round(mean_post, 3),
            "n_days": len(post_daily_means)}
