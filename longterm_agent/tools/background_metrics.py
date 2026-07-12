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

Two sentiment estimators (Phase 5, selected by `metrics_version`):

  v1 — weekly bucket LLM sentiments, count-weighted mean / population std. ~8 scalars per
       window; a map-model change silently moves the series.
  v2 — PER-ARTICLE sentiment (graph AFFECTS label → ±1/0; unlabeled articles fall back to
       their bucket's LLM sentiment — the hybrid path, ~12% of the universe corpus),
       aggregated to a DAILY mean series, then a robust median/MAD recent-vs-baseline
       break test. Hundreds of points instead of ~8, and the decision decouples from
       map-LLM variance where graph labels exist. Sparse-series guards (min daily points)
       suppress the sentiment branch rather than fabricate a z from 2 points.

The attention-spike branch, `min_abs_delta` magnitude guard, turnover, new-stories and
inflection logic are IDENTICAL across versions. Everything degrades gracefully
(Neo4j optional; tiny/empty baselines handled).
"""
import re
from statistics import median, pstdev
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

# v2 sparse-series guards: below these the sentiment break is not trusted (attention
# branch still active). Part of the detection definition — changing them is a release.
MIN_RECENT_DAYS = 3
MIN_BASELINE_DAYS = 5
# Same coarse fallback scale v1 uses when the baseline has no variance: a delta of 0.3
# maps to z=1.0. Keeps the zero-MAD case (e.g. neutral-dominated payment networks)
# bounded instead of exploding.
_FALLBACK_SCALE = 0.3
# MAD → σ-equivalent under normality, so z_threshold keeps one scale across v1/v2.
_MAD_CONSISTENCY = 1.4826

SENTIMENT_NUM = {"positive": 1.0, "bullish": 1.0, "negative": -1.0, "bearish": -1.0, "neutral": 0.0}


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


def _scaled_mad(xs: List[float], center: float) -> float:
    """Median absolute deviation around `center`, scaled to σ-equivalent (v2)."""
    if len(xs) < 2:
        return 0.0
    return _MAD_CONSISTENCY * median([abs(x - center) for x in xs])


def _daily_sentiment_series(articles, bucket_summaries, period_buckets):
    """Per-article sentiment → daily-mean series (v2 primary input).

    Article sentiment: graph AFFECTS label via SENTIMENT_NUM (±1/0); an unlabeled article
    inherits its bucket's LLM sentiment (hybrid fallback — coverage measured at 88% on the
    universe, so this path carries ~1 article in 8).

    Returns (series, label_coverage) where series = [(date, mean, count)] sorted by date.
    """
    bucket_sent = {}
    for i, s in enumerate(bucket_summaries):
        bucket_sent[s.get("_bucket_index", i)] = float(s.get("sentiment", 0.0) or 0.0)
    art_bucket = {}
    for b in period_buckets:
        for aid in b.get("article_ids", []):
            art_bucket[aid] = b["index"]

    by_day: Dict[Any, List[float]] = {}
    labeled = total = 0
    for a in articles:
        pub = a.get("published")
        if pub is None:
            continue
        total += 1
        label = a.get("affects_sentiment")
        if label in SENTIMENT_NUM:
            s = SENTIMENT_NUM[label]
            labeled += 1
        else:
            s = bucket_sent.get(art_bucket.get(a.get("id")), 0.0)
        day = pub.date() if hasattr(pub, "date") else pub
        by_day.setdefault(day, []).append(s)

    series = [(d, sum(v) / len(v), len(v)) for d, v in sorted(by_day.items())]
    return series, (labeled / total if total else 0.0)


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
    metrics_version: int = 1,
    articles: Optional[List[Dict[str, Any]]] = None,
    sector_baseline: Optional[Dict[Any, float]] = None,
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

    # --- v1 sentiment z-score (count-weighted means; baseline std over non-empty buckets).
    # Computed in BOTH versions: it's the v1 decision input, and the inflection heuristic
    # below keys on the weekly running baseline either way.
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
        z = (delta / _FALLBACK_SCALE) if abs(delta) > 1e-6 else 0.0
    v1_baseline_mean = baseline_mean  # inflection anchor (weekly scale, both versions)

    # --- v2 sentiment: per-article daily series, median/MAD robust break test.
    # Replaces the DECISION inputs (delta/z) and the reported sentiment stats; the same
    # dict keys keep their names so downstream readers (prompts, prints, stored JSON)
    # work unchanged — `estimator` marks the semantics.
    sentiment_extra: Dict[str, Any] = {}
    sector_stats: Dict[str, Any] = {}
    sparse = False
    if metrics_version >= 2 and articles is not None:
        series, coverage = _daily_sentiment_series(articles, bucket_summaries, period_buckets)
        cutoff = (as_of - timedelta(days=shift_window_days)).date()
        as_of_day = as_of.date() if hasattr(as_of, "date") else as_of

        # --- v3 (Phase 13): subtract the sector baseline day-wise → EXCESS series.
        # A sector-wide wave moves company and baseline together (excess ≈ 0 → no company
        # shift); days without sector data use baseline 0 (neutral). The baseline itself
        # gets the same robust break test → sector_break, reported as CONTEXT.
        if metrics_version >= 3 and sector_baseline:
            series = [(d, m - sector_baseline.get(d, 0.0), c) for d, m, c in series]
            b_recent = [v for d, v in sector_baseline.items() if cutoff < d <= as_of_day]
            b_base = [v for d, v in sector_baseline.items() if d <= cutoff]
            if len(b_recent) >= MIN_RECENT_DAYS and len(b_base) >= MIN_BASELINE_DAYS:
                b_rm, b_bm = median(b_recent), median(b_base)
                b_mad = _scaled_mad(b_base, b_bm)
                b_delta = b_rm - b_bm
                b_z = (b_delta / b_mad) if b_mad > 1e-6 else (
                    (b_delta / _FALLBACK_SCALE) if abs(b_delta) > 1e-6 else 0.0)
                sector_stats = {
                    "available": True, "n_days": len(sector_baseline),
                    "recent_median": round(b_rm, 3), "baseline_median": round(b_bm, 3),
                    "delta": round(b_delta, 3), "z": round(b_z, 2),
                    "sector_break": (abs(b_z) >= z_threshold and abs(b_delta) >= min_abs_delta),
                    "direction": ("positive" if b_delta > 0 else "negative") if abs(b_delta) > 1e-6 else None,
                }
            else:
                sector_stats = {"available": True, "n_days": len(sector_baseline),
                                "sector_break": False, "note": "sector series too sparse to test"}
        elif metrics_version >= 3:
            sector_stats = {"available": False,
                            "note": "no sector baseline (unmapped/thin) — excess == company series"}

        recent_pts = [m for d, m, _ in series if cutoff < d <= as_of_day]
        base_pts = [m for d, m, _ in series if d <= cutoff]
        recent_mean = median(recent_pts) if recent_pts else 0.0
        baseline_mean = median(base_pts) if base_pts else 0.0
        baseline_std = _scaled_mad(base_pts, baseline_mean)
        delta = recent_mean - baseline_mean
        if baseline_std > 1e-6:
            z = delta / baseline_std
        else:
            z = (delta / _FALLBACK_SCALE) if abs(delta) > 1e-6 else 0.0
        # Sparse series → the sentiment branch abstains (attention branch still live).
        sparse = len(recent_pts) < MIN_RECENT_DAYS or len(base_pts) < MIN_BASELINE_DAYS
        sentiment_extra = {
            "estimator": ("daily_median_mad_sector_excess"
                          if metrics_version >= 3 and sector_baseline else "daily_median_mad"),
            "n_recent_days": len(recent_pts),
            "n_baseline_days": len(base_pts),
            "label_coverage": round(coverage, 3),
            "sparse": sparse,
        }

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
    # (anchored on the WEEKLY v1 baseline in both versions — descriptive, not a decision)
    inflection_week = None
    if rows:
        run_base = v1_baseline_mean if baseline else rows[0]["sentiment"]
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
    sentiment_break = (abs(z) >= z_threshold) and (abs(delta) >= min_abs_delta) and not sparse
    attention_break = bool(spike_ratio) and spike_ratio >= attention_spike_ratio
    shift_flag = sentiment_break or attention_break

    # Continuous shift strength (0-1), deterministic — synthesis weights this instead of
    # relying on the hard binary, which is what flip-flops near the z threshold.
    if sparse:
        sent_comp = 0.0  # abstaining sentiment branch contributes nothing (v2 guard)
    else:
        sent_comp = min(abs(z) / 2.0, 1.0) * (1.0 if abs(delta) >= min_abs_delta else abs(delta) / min_abs_delta)
    att_comp = min(max((spike_ratio or 0.0) - 1.0, 0.0) / 3.0, 1.0)
    shift_score = round(min(max(sent_comp, att_comp), 1.0), 2)

    direction = None
    if shift_flag and abs(delta) > 1e-6:
        direction = "positive" if delta > 0 else "negative"

    out_sector = {"sector": sector_stats} if sector_stats else {}
    return {
        "metrics_version": metrics_version,
        **out_sector,
        "sentiment": {
            "recent_mean": round(recent_mean, 3),
            "baseline_mean": round(baseline_mean, 3),
            "baseline_std": round(baseline_std, 3),
            "delta": round(delta, 3),
            "z": round(z, 2),
            **sentiment_extra,
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
