"""Phase 5 tests: metrics v2 (per-article daily median/MAD break) + embed corpus source.

Pure/synthetic — no DB, no LLM. The corpus tests monkeypatch the three source functions.
"""
from datetime import datetime, timedelta

from longterm_agent.tools import news_corpus_tool as nct
from longterm_agent.tools.background_metrics import compute_shift_metrics

AS_OF = datetime(2026, 6, 12)
START = AS_OF - timedelta(days=56)  # 8 weekly buckets


# --- fixtures -----------------------------------------------------------------
def _weekly(sentiments, count=10):
    """Aligned (bucket_summaries, period_buckets) from weekly sentiments (old→new)."""
    summaries, buckets = [], []
    for i, s in enumerate(sentiments):
        summaries.append({"sentiment": s, "themes": ["industry"], "_week": f"w{i}",
                          "_bucket_index": i})
        buckets.append({"index": i, "count": count, "article_ids": []})
    return summaries, buckets


def _articles(day_labels):
    """Articles from {day_offset_from_start: [affects_sentiment|None, ...]}."""
    arts, aid = [], 1
    for off, labels in sorted(day_labels.items()):
        for lb in labels:
            arts.append({"id": aid, "published": START + timedelta(days=off, hours=12),
                         "affects_sentiment": lb})
            aid += 1
    return arts


def _run_v2(day_labels, weekly=None, **kw):
    bs, pb = _weekly(weekly or [0.0] * 8)
    arts = _articles(day_labels)
    # place articles into their weekly buckets (id → bucket) for the fallback path
    for a in arts:
        bi = min((a["published"] - START).days // 7, len(pb) - 1)
        pb[bi]["article_ids"].append(a["id"])
        pb[bi]["count"] = pb[bi].get("count", 0) + 1
    return compute_shift_metrics(bs, pb, AS_OF, shift_window_days=14, bucket_days=7,
                                 ticker="TEST", z_threshold=1.0, neo4j_manager=None,
                                 metrics_version=2, articles=arts, **kw)


def _spread(labels=("positive", "negative", "positive")):
    """A day with mixed labels → daily mean 1/3 → baseline gets variance."""
    return list(labels)


# --- metrics v2 -----------------------------------------------------------------
def test_v1_output_unchanged_by_new_args():
    bs, pb = _weekly([0.0, 0.1, -0.1, 0.05, -0.05, 0.0, 0.8, 0.8])
    base = compute_shift_metrics(bs, pb, AS_OF, 14, 7, "TEST", neo4j_manager=None)
    with_args = compute_shift_metrics(bs, pb, AS_OF, 14, 7, "TEST", neo4j_manager=None,
                                      metrics_version=1, articles=_articles({0: ["positive"]}))
    assert base["metrics_version"] == 1
    assert base["sentiment"] == with_args["sentiment"]  # v1 ignores articles
    assert base["shift_flag"] is True and base["direction"] == "positive"
    assert "estimator" not in base["sentiment"]


def test_v2_negative_break_fires():
    # baseline: mixed days (variance, median ≈ +1/3); recent 14d: solidly negative
    days = {o: _spread() for o in range(0, 42, 3)}
    days.update({o: ["negative", "negative", "bearish"] for o in range(43, 56, 2)})
    m = _run_v2(days)
    assert m["metrics_version"] == 2
    assert m["sentiment"]["estimator"] == "daily_median_mad"
    assert m["shift_flag"] is True
    assert m["direction"] == "negative"
    assert m["sentiment"]["z"] <= -1.0
    assert m["sentiment"]["label_coverage"] == 1.0


def test_v2_flat_series_stays_quiet():
    days = {o: _spread() for o in range(0, 56, 3)}
    m = _run_v2(days)
    assert m["shift_flag"] is False


def test_v2_robust_to_one_outlier_day():
    # v2's median/MAD must NOT flag a single catastrophic baseline day as a recent shift
    days = {o: _spread() for o in range(0, 56, 3)}
    days[10] = ["negative"] * 8  # one crazy day deep in the baseline
    m = _run_v2(days)
    assert m["shift_flag"] is False


def test_v2_hybrid_fallback_uses_bucket_sentiment():
    # ALL articles unlabeled → per-article values inherit weekly LLM sentiments;
    # recent buckets strongly negative → break still detectable through the fallback.
    weekly = [0.1, 0.0, 0.1, 0.0, 0.1, 0.0, -0.8, -0.8]
    days = {o: [None, None] for o in range(0, 56, 3)}
    m = _run_v2(days, weekly=weekly)
    assert m["sentiment"]["label_coverage"] == 0.0
    assert m["shift_flag"] is True and m["direction"] == "negative"


def test_v2_sparse_recent_abstains():
    # only 2 recent article-days (< MIN_RECENT_DAYS) → sentiment branch abstains
    days = {o: _spread() for o in range(0, 42, 3)}
    days[50] = ["negative", "negative"]
    days[52] = ["negative"]
    m = _run_v2(days)
    assert m["sentiment"]["sparse"] is True
    assert m["shift_flag"] is False  # no attention spike either
    assert m["shift_score"] == 0.0


def test_v2_zero_mad_falls_back_to_coarse_scale():
    # baseline days all identical (MAD=0) → z = delta/0.3, not an explosion
    days = {o: ["neutral", "neutral"] for o in range(0, 42, 3)}
    days.update({o: ["negative", "negative", "negative"] for o in range(43, 56, 2)})
    m = _run_v2(days)
    assert m["sentiment"]["baseline_std"] == 0.0
    assert m["sentiment"]["z"] == round(m["sentiment"]["delta"] / 0.3, 2)
    assert m["shift_flag"] is True and m["direction"] == "negative"


# --- embed corpus source -----------------------------------------------------------
def _patch_sources(monkeypatch, graph_map, ilike, embed):
    monkeypatch.setattr(nct, "_affects_news_ids", lambda *a, **k: dict(graph_map))
    monkeypatch.setattr(nct, "_ilike_articles", lambda *a, **k: [dict(x) for x in ilike])
    monkeypatch.setattr(nct, "_fetch_by_ids",
                        lambda ids, *a, **k: [{"id": i, "title": f"t{i}", "summary": "",
                                               "published": AS_OF} for i in ids])
    monkeypatch.setattr(nct, "_embed_articles", lambda *a, **k: [dict(x) for x in embed])


def test_embed_merges_and_tags_provenance(monkeypatch):
    ilike = [{"id": 1, "title": "a", "summary": "", "published": AS_OF}]
    embed = [{"id": 1, "title": "a", "summary": "", "published": AS_OF, "embed_sim": 0.9},
             {"id": 3, "title": "c", "summary": "", "published": AS_OF, "embed_sim": 0.7}]
    _patch_sources(monkeypatch, {2: "positive"}, ilike, embed)
    c = nct.get_company_news_corpus("TEST", AS_OF, 60, db_manager=None,
                                    neo4j_manager=object(), embed_enabled=True)
    by_id = {a["id"]: a for a in c["articles"]}
    assert set(by_id) == {1, 2, 3}
    assert by_id[1]["sources"] == ["ilike", "embed"] and by_id[1]["embed_sim"] == 0.9
    assert by_id[2]["sources"] == ["graph"]
    assert by_id[3]["sources"] == ["embed"]
    p = c["provenance"]
    assert (p["graph"], p["ilike"], p["embed"]) == (1, 1, 2)
    assert p["embed_only"] == 1 and p["both"] == 1 and p["total"] == 3


def test_embed_disabled_is_v1_corpus(monkeypatch):
    ilike = [{"id": 1, "title": "a", "summary": "", "published": AS_OF}]
    embed = [{"id": 9, "title": "x", "summary": "", "published": AS_OF, "embed_sim": 0.99}]
    _patch_sources(monkeypatch, {}, ilike, embed)
    c = nct.get_company_news_corpus("TEST", AS_OF, 60, db_manager=None,
                                    neo4j_manager=object(), embed_enabled=False)
    assert [a["id"] for a in c["articles"]] == [1]
    assert c["provenance"]["embed"] == 0 and c["provenance"]["embed_only"] == 0


def test_embed_query_text_deterministic():
    q1, q2 = nct.embed_query_text("NVDA"), nct.embed_query_text("NVDA")
    assert q1 == q2 and "Nvidia" in q1 and "NVDA" in q1


# --- version wiring -----------------------------------------------------------------
def test_embed_off_by_default_even_in_v2(monkeypatch):
    # Audit verdict (2026-07-11): embed failed the precision guard → off in ALL versions;
    # enabling it must be an explicit experiment flag, never a metrics-version side effect.
    import importlib
    monkeypatch.setenv("LT_METRICS_VERSION", "2")
    monkeypatch.delenv("LT_CORPUS_EMBED", raising=False)
    import longterm_agent.config as cfg_mod
    cfg = importlib.reload(cfg_mod)
    try:
        assert cfg.LongTermAgentConfig.METRICS_VERSION == 2
        assert cfg.LongTermAgentConfig.DETECTION_VERSION == "0.2.0"
        assert cfg.LongTermAgentConfig.CORPUS_EMBED_ENABLED is False
        # bucket cache is shared across detection versions (corpus/map identical)
        assert cfg.LongTermAgentConfig.BUCKET_CACHE_VERSION == "0.1.0"
    finally:
        monkeypatch.delenv("LT_METRICS_VERSION", raising=False)
        importlib.reload(cfg_mod)  # restore module-level defaults for other tests
