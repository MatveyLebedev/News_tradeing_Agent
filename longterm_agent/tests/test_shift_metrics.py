"""
Synthetic-series tests for compute_shift_metrics — deterministically prove the
quantitative shift trigger fires (and stays quiet) without needing live data.
"""
from datetime import datetime

from longterm_agent.tools.background_metrics import compute_shift_metrics

AS_OF = datetime(2026, 6, 12)


def _series(sentiments, count=10, themes=None):
    """Build aligned (bucket_summaries, period_buckets) from a sentiment list (old→new)."""
    summaries, buckets = [], []
    for i, s in enumerate(sentiments):
        summaries.append({"sentiment": s, "themes": themes or ["industry"], "_week": f"w{i}"})
        buckets.append({"count": count})
    return summaries, buckets


def _run(sentiments, **kw):
    bs, pb = _series(sentiments, **{k: v for k, v in kw.items() if k in ("count", "themes")})
    return compute_shift_metrics(bs, pb, AS_OF, shift_window_days=14, bucket_days=7,
                                 ticker="TEST", z_threshold=1.0, neo4j_manager=None)


def test_positive_break_fires():
    # baseline ~0 with small variance, recent 2 buckets strongly positive
    m = _run([0.0, 0.1, -0.1, 0.05, -0.05, 0.0, 0.8, 0.8])
    assert m["shift_flag"] is True
    assert m["direction"] == "positive"
    assert m["sentiment"]["z"] > 1.0


def test_negative_break_fires():
    m = _run([0.0, 0.1, -0.1, 0.05, -0.05, 0.0, -0.8, -0.8])
    assert m["shift_flag"] is True
    assert m["direction"] == "negative"
    assert m["sentiment"]["z"] < -1.0


def test_flat_series_no_shift():
    m = _run([0.0, 0.05, -0.05, 0.0, 0.05, -0.05, 0.0, 0.0])
    assert m["shift_flag"] is False
    assert abs(m["sentiment"]["z"]) < 1.0


def test_steady_negative_is_not_a_shift():
    # already negative and staying negative → not a *break* (the CAT case)
    m = _run([-0.5, -0.45, -0.55, -0.5, -0.48, -0.52, -0.6, -0.6])
    assert m["shift_flag"] is False


def test_attention_spike_fires_without_sentiment_break():
    # flat sentiment but recent volume 5x baseline → spike trips the flag
    bs, pb = _series([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    for b in pb[:6]:
        b["count"] = 5
    for b in pb[6:]:
        b["count"] = 40
    m = compute_shift_metrics(bs, pb, AS_OF, 14, 7, "TEST", z_threshold=1.0, neo4j_manager=None)
    assert m["attention"]["spike_ratio"] >= 2.0
    assert m["shift_flag"] is True


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
