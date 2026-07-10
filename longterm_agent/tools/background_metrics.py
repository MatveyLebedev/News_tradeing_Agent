"""
Quantitative shift metrics (§ shift_detection) — computed BEFORE any LLM reasoning so
the model interprets numbers, not vibes.

Given the weekly bucket summaries + raw bucket counts, splits the window into a recent
slice (last `shift_window_days`) and a baseline (everything earlier) and computes:

  - sentiment z-score : (recent_mean − baseline_mean) / baseline_std   ← the headline metric
  - attention spike   : recent articles/day ÷ baseline articles/day
  - narrative turnover: 1 − Jaccard(recent themes, baseline themes)
  - new stories       : Neo4j Stories tied to the ticker that started in the recent window
  - inflection week   : the bucket with the largest jump vs the running baseline mean

Everything degrades gracefully (Neo4j optional; tiny/empty baselines handled).
"""
import re
from statistics import pstdev
from typing import Any, Dict, List
from datetime import datetime, timedelta


def _weighted_mean(pairs):
    """pairs = [(value, weight)]; ignores zero-weight entries."""
    num = sum(v * w for v, w in pairs if w)
    den = sum(w for _, w in pairs if w)
    return (num / den) if den else 0.0


def _split_recent_baseline(buckets_with_idx, shift_window_days, bucket_days):
    """Return (recent_indices, baseline_indices) — last ceil(window/bucket) buckets are recent."""
    n_recent = max(1, round(shift_window_days / bucket_days))
    n = len(buckets_with_idx)
    recent = set(range(max(0, n - n_recent), n))
    baseline = set(range(0, max(0, n - n_recent)))
    return recent, baseline


def _new_stories(ticker, recent_start, as_of, neo4j_manager) -> List[Dict[str, Any]]:
    if neo4j_manager is None:
        return []
    try:
        rows = neo4j_manager.query(
            """
            MATCH (s:Story)
            WHERE $t IN s.tickers
              AND s.start_date >= datetime($rs) AND s.start_date <= datetime($ao)
            RETURN s.label AS label, toString(s.start_date) AS start_date,
                   s.article_count AS article_count
            ORDER BY coalesce(s.article_count, 0) DESC
            LIMIT 10
            """,
            {"t": ticker.upper(), "rs": recent_start.isoformat(), "ao": as_of.isoformat()},
        )
        return rows or []
    except Exception as e:
        print(f"  ⚠ new-story query failed: {e}")
        return []


def compute_shift_metrics(
    bucket_summaries: List[Dict[str, Any]],
    period_buckets: List[Dict[str, Any]],
    as_of: datetime,
    shift_window_days: int,
    bucket_days: int,
    ticker: str,
    z_threshold: float = 1.0,
    min_abs_delta: float = 0.2,
    attention_spike_ratio: float = 2.0,
    neo4j_manager: Any = None,
) -> Dict[str, Any]:
    # align summaries with raw buckets by index
    rows = []
    for i, (s, b) in enumerate(zip(bucket_summaries, period_buckets)):
        rows.append({
            "i": i,
            "sentiment": float(s.get("sentiment", 0.0) or 0.0),
            "count": int(b.get("count", 0) or 0),
            "themes": [t.lower() for t in (s.get("themes") or [])],
            "week": s.get("_week", ""),
        })

    recent_idx, baseline_idx = _split_recent_baseline(rows, shift_window_days, bucket_days)
    recent = [r for r in rows if r["i"] in recent_idx]
    baseline = [r for r in rows if r["i"] in baseline_idx]

    # --- sentiment z-score (count-weighted means; baseline std over non-empty buckets) ---
    recent_mean = _weighted_mean([(r["sentiment"], r["count"]) for r in recent])
    baseline_pairs = [(r["sentiment"], r["count"]) for r in baseline if r["count"] > 0]
    baseline_mean = _weighted_mean(baseline_pairs)
    baseline_samples = [r["sentiment"] for r in baseline if r["count"] > 0]
    baseline_std = pstdev(baseline_samples) if len(baseline_samples) >= 2 else 0.0
    delta = recent_mean - baseline_mean
    if baseline_std > 1e-6:
        z = delta / baseline_std
    else:
        # no baseline variance: fall back to a coarse delta-based signal
        z = (delta / 0.3) if abs(delta) > 1e-6 else 0.0

    # --- attention spike (articles/day recent vs baseline) ---
    recent_days = max(1, sum(1 for r in recent) * bucket_days)
    baseline_days = max(1, sum(1 for r in baseline) * bucket_days)
    recent_rate = sum(r["count"] for r in recent) / recent_days
    baseline_rate = sum(r["count"] for r in baseline) / baseline_days
    spike_ratio = (recent_rate / baseline_rate) if baseline_rate > 1e-6 else (recent_rate and 999.0)

    # --- narrative turnover (Jaccard distance on theme WORD TOKENS) ---
    # Theme strings are free-text LLM output and almost never exact-match across buckets,
    # so set-Jaccard on whole themes is always ~1.0 (useless). Tokenize to words and drop
    # stopwords so "AI infrastructure" vs "AI demand" register meaningful overlap.
    _STOP = {"and", "the", "of", "in", "for", "to", "a", "on", "s", "with", "&"}

    def _tokens(themes):
        out = set()
        for t in themes:
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                if w not in _STOP and len(w) > 1:
                    out.add(w)
        return out

    recent_tok = _tokens(t for r in recent for t in r["themes"])
    baseline_tok = _tokens(t for r in baseline for t in r["themes"])
    if recent_tok or baseline_tok:
        inter = len(recent_tok & baseline_tok)
        union = len(recent_tok | baseline_tok) or 1
        turnover = 1.0 - inter / union
    else:
        turnover = 0.0

    # --- inflection: bucket with the largest jump vs running baseline mean ---
    inflection_week = None
    if rows:
        run_base = baseline_mean if baseline else rows[0]["sentiment"]
        best = max(rows, key=lambda r: abs(r["sentiment"] - run_base) if r["count"] else 0.0)
        if best["count"]:
            inflection_week = best["week"]

    # --- new stories in the recent window ---
    recent_start = as_of - timedelta(days=shift_window_days)
    new_stories = _new_stories(ticker, recent_start, as_of, neo4j_manager)

    # shift_flag keys on the robust quantitative signals (sentiment break, attention spike).
    # The sentiment break needs BOTH statistical significance (|z|) AND a meaningful absolute
    # move (|delta|) — against a very stable baseline a tiny 0.1 move is "significant" by z
    # alone but isn't a tradeable shift (the steady-negative case). Turnover is reported as
    # supporting context but does NOT auto-trigger the flag (even tokenized it's noisy).
    sentiment_break = (abs(z) >= z_threshold) and (abs(delta) >= min_abs_delta)
    attention_break = bool(spike_ratio) and spike_ratio >= attention_spike_ratio
    shift_flag = sentiment_break or attention_break

    # Continuous shift strength (0-1), deterministic — synthesis weights this instead of
    # relying on the hard binary, which is what flip-flops near the z threshold.
    sent_comp = min(abs(z) / 2.0, 1.0) * (1.0 if abs(delta) >= min_abs_delta else abs(delta) / min_abs_delta)
    att_comp = min(max((spike_ratio or 0.0) - 1.0, 0.0) / 3.0, 1.0)
    shift_score = round(min(max(sent_comp, att_comp), 1.0), 2)

    direction = None
    if shift_flag and abs(delta) > 1e-6:
        direction = "positive" if delta > 0 else "negative"

    return {
        "sentiment": {
            "recent_mean": round(recent_mean, 3),
            "baseline_mean": round(baseline_mean, 3),
            "baseline_std": round(baseline_std, 3),
            "delta": round(delta, 3),
            "z": round(z, 2),
        },
        "attention": {
            "recent_per_day": round(recent_rate, 2),
            "baseline_per_day": round(baseline_rate, 2),
            "spike_ratio": round(spike_ratio, 2) if spike_ratio else 0.0,
        },
        "narrative_turnover": round(turnover, 2),
        "new_stories": new_stories,
        "inflection_week": inflection_week,
        "recent_buckets": len(recent),
        "baseline_buckets": len(baseline),
        "shift_flag": bool(shift_flag),
        "shift_score": shift_score,
        "direction": direction,
    }
