"""Phase 13 tests: sector-relative detection v3 (pure — synthetic series, no DB)."""
from datetime import datetime, timedelta

from longterm_agent.sector_map import SECTOR_GROUPS, SECTOR_OF, peers_for
from longterm_agent.tools.background_metrics import compute_shift_metrics

AS_OF = datetime(2026, 6, 12)
START = AS_OF - timedelta(days=56)


def _weekly(sentiments, count=10):
    summaries, buckets = [], []
    for i, s in enumerate(sentiments):
        summaries.append({"sentiment": s, "themes": ["industry"], "_week": f"w{i}",
                          "_bucket_index": i})
        buckets.append({"index": i, "count": count, "article_ids": []})
    return summaries, buckets


def _articles(day_labels):
    arts, aid = [], 1
    for off, labels in sorted(day_labels.items()):
        for lb in labels:
            arts.append({"id": aid, "published": START + timedelta(days=off, hours=12),
                         "affects_sentiment": lb})
            aid += 1
    return arts


def _run(day_labels, baseline=None, version=3):
    bs, pb = _weekly([0.0] * 8)
    arts = _articles(day_labels)
    for a in arts:
        bi = min((a["published"] - START).days // 7, len(pb) - 1)
        pb[bi]["article_ids"].append(a["id"])
    return compute_shift_metrics(bs, pb, AS_OF, shift_window_days=14, bucket_days=7,
                                 ticker="TEST", z_threshold=1.0, neo4j_manager=None,
                                 metrics_version=version, articles=arts,
                                 sector_baseline=baseline)


def _days(offsets, labels):
    return {o: list(labels) for o in offsets}


# --- the AMD/Broadcom shape: sector wave must NOT be a company shift -----------------
def test_sector_wave_suppressed_in_v3():
    # company turns negative in the recent window…
    days = _days(range(0, 42, 3), ("positive", "negative", "positive"))
    days.update(_days(range(43, 56, 2), ("negative", "negative", "bearish")))
    # …but the WHOLE SECTOR turned negative the same days (baseline tracks it)
    baseline = {START.date() + timedelta(days=o): (1 / 3 if o <= 42 else -1.0)
                for o in range(0, 56)}
    v2 = _run(days, baseline=None, version=2)
    v3 = _run(days, baseline=baseline, version=3)
    assert v2["shift_flag"] is True          # v2 sees a company shift
    assert v3["shift_flag"] is False         # v3: excess ≈ 0 → sector wave, not a shift
    assert v3["sector"]["sector_break"] is True
    assert v3["sector"]["direction"] == "negative"
    assert v3["sentiment"]["estimator"] == "daily_median_mad_sector_excess"


def test_idiosyncratic_shift_survives_v3():
    # company turns negative while the sector stays flat → the shift is REAL
    days = _days(range(0, 42, 3), ("positive", "negative", "positive"))
    days.update(_days(range(43, 56, 2), ("negative", "negative", "bearish")))
    flat_baseline = {START.date() + timedelta(days=o): 0.05 for o in range(0, 56)}
    v3 = _run(days, baseline=flat_baseline, version=3)
    assert v3["shift_flag"] is True
    assert v3["direction"] == "negative"
    assert v3["sector"]["sector_break"] is False


def test_v3_without_baseline_degrades_to_v2():
    days = _days(range(0, 42, 3), ("positive", "negative", "positive"))
    days.update(_days(range(43, 56, 2), ("negative", "negative", "bearish")))
    v2 = _run(days, baseline=None, version=2)
    v3 = _run(days, baseline=None, version=3)
    assert v3["shift_flag"] == v2["shift_flag"]
    assert v3["sentiment"]["z"] == v2["sentiment"]["z"]
    assert v3["sector"]["available"] is False  # honestly reported


def test_v2_ignores_sector_baseline_argument():
    days = _days(range(0, 56, 3), ("positive", "negative", "positive"))
    noisy = {START.date() + timedelta(days=o): -1.0 for o in range(0, 56)}
    a = _run(days, baseline=None, version=2)
    b = _run(days, baseline=noisy, version=2)
    assert a["sentiment"] == b["sentiment"] and "sector" not in a


# --- sector map integrity ------------------------------------------------------------
def test_sector_map_no_overlaps_and_twins_excluded():
    seen = {}
    for sec, tks in SECTOR_GROUPS.items():
        for tk in tks:
            assert tk not in seen, f"{tk} in both {seen.get(tk)} and {sec}"
            seen[tk] = sec
    assert SECTOR_OF["NVDA"] == "Technology"
    assert "GOOG" not in peers_for("GOOGL")      # share-class twin never a peer
    assert "GOOGL" in peers_for("META")
    assert peers_for("UNKNOWN_TICKER") == []


def test_baseline_groups_industry_granularity():
    # The AMD←Broadcom lesson: the contagion cluster is the SEMIS complex, not the
    # 26-name Technology sector whose median hides the wave.
    from longterm_agent.sector_map import BASELINE_GROUP_OF
    semis = set(peers_for("AMD"))
    assert {"NVDA", "AVGO", "INTC", "QCOM", "MU"} <= semis
    assert "MSFT" not in semis and "CRM" not in semis  # software ≠ semis baseline
    assert "AMAT" in semis                              # equipment merged into the complex
    assert BASELINE_GROUP_OF["AAPL"] == "Technology"    # no ≥4 industry group → sector
    # every mapped ticker resolves to a group with ≥3 peers (usable baseline)
    for tk in BASELINE_GROUP_OF:
        assert len(peers_for(tk)) >= 3, tk
