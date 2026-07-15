"""Phase 11 tests: snapshots, multi-target classifiers, export join, few-shot flag.

Pure — synthetic state/rows, no DB, no network.
"""
from datetime import datetime, date
from types import SimpleNamespace

from longterm_agent.export_training_data import build_examples
from longterm_agent.snapshots import build_snapshot_doc
from longterm_agent.targets import (classify_fundamentals_realized, classify_shift_outcome,
                                    worst_position_return)


class _Cfg:
    DETECTION_VERSION = "0.2.0"
    PIPELINE_VERSION = "0.8.0"

    @classmethod
    def provenance(cls):
        return {"detection_version": cls.DETECTION_VERSION, "git_revision": "test"}


def _state():
    return {
        "ticker": "NVDA", "as_of": datetime(2026, 6, 22), "lookback_days": 60,
        "article_count": 120, "no_data": False,
        "news_corpus": [{"id": 1, "title": "t1", "summary": "s" * 500},
                        {"id": 2, "title": "t2", "summary": "s" * 500}],
        "corpus_provenance": {"graph": 100, "total": 120},
        "bucket_summaries": [{"sentiment": 0.4, "themes": ["ai"]}],
        "background_profile": {"summary": "AI demand"},
        "background_shift": {"has_shifted": True, "direction": "positive"},
        "fundamentals": {"trend": {"verdict": "confirms"}, "revenue_yoy": 55.0},
        "shift_analogs": {"n_analogs": 3},
        "enriched_analysis": {"summary": "deep"},
        "signal": {"direction": "LONG", "conviction": 0.7},
        "filtered_signal": {"direction": "LONG", "conviction": 0.7},
        "messages": [{"name": "synthesis", "content": "LONG"}],
        "background_metrics": {"sentiment": {"z": 1.4}},
        "technical_longterm": {"trend": "uptrend"},
        "risk_flags": [], "risk_checks": {"regime_context": {"available": False}},
    }


def test_snapshot_doc_references_not_copies():
    doc = build_snapshot_doc(_state(), _Cfg)
    assert doc["corpus_ref"]["news_ids"] == [1, 2]        # ids only…
    assert "articles" not in doc and "news_corpus" not in doc  # …bodies never copied
    # LLM outputs stored in full; provenance stamped
    assert doc["bucket_summaries"][0]["themes"] == ["ai"]
    assert doc["signal"]["direction"] == "LONG"
    assert doc["messages"][0]["name"] == "synthesis"
    assert doc["provenance"]["detection_version"] == "0.2.0"
    assert doc["identity"]["ticker"] == "NVDA"


def test_worst_position_return_direction_adjusted():
    path = [(date(2026, 6, 1), 100.0), (date(2026, 6, 2), 92.0), (date(2026, 6, 3), 105.0)]
    assert worst_position_return(path, "LONG") == -0.08    # the dip hurts the LONG
    assert worst_position_return(path, "SHORT") == -0.05   # the rally hurts the SHORT
    assert worst_position_return(path, "NEUTRAL") is None
    assert worst_position_return([], "LONG") is None


def test_classify_fundamentals_realized():
    before = {"revenue_yoy": 10.0, "margin_series": [{"net_margin": 20.0}]}
    up = {"revenue_yoy": 15.0, "margin_series": [{"net_margin": 23.0}]}
    down = {"revenue_yoy": 4.0, "margin_series": [{"net_margin": 17.0}]}
    flat = {"revenue_yoy": 10.5, "margin_series": [{"net_margin": 20.2}]}
    assert classify_fundamentals_realized(before, up)["label"] == "improving"
    assert classify_fundamentals_realized(before, down)["label"] == "deteriorating"
    assert classify_fundamentals_realized(before, flat)["label"] == "stable"
    assert classify_fundamentals_realized(before, {})["label"] == "unknown"


def test_classify_shift_outcome():
    assert classify_shift_outcome("negative", [-0.4, -0.2, -0.3])["label"] == "persisted"
    assert classify_shift_outcome("negative", [0.4, 0.3])["label"] == "reverted"
    assert classify_shift_outcome("positive", [0.01, -0.02])["label"] == "mixed"
    assert classify_shift_outcome("positive", [])["label"] == "unknown"
    assert classify_shift_outcome("sideways", [0.5])["label"] == "unknown"


def test_export_join_builds_gradable_examples_only():
    snap = build_snapshot_doc(_state(), _Cfg)
    row = {"fwd_return_2w": 0.02, "benchmark_return_2w": 0.01,
           "fwd_return_4w": 0.05, "benchmark_return_4w": 0.01,
           "fwd_return_12w": None, "benchmark_return_12w": None,
           "sector_benchmark": "XLK", "sector_benchmark_return_4w": 0.03,
           "fwd_return_stopped": 0.05, "exit_reason": "horizon", "exit_days": 28,
           "worst_position_return_4w": -0.04,
           "aux_targets": {"shift_outcome": {"label": "persisted", "post_mean_sentiment": 0.3},
                           "fundamentals_realized": {"label": "improving"}}}
    ex = build_examples(snap, row)
    assert ex["synthesis"]["targets"]["excess_4w"] == 0.04
    assert ex["synthesis"]["targets"]["excess_sector_4w"] == 0.02   # beat the sector too
    assert ex["synthesis"]["targets"]["excess_12w"] is None         # not matured → None
    assert ex["synthesis"]["output"]["direction"] == "LONG"
    assert ex["fundamentals"]["targets"]["label"] == "improving"
    assert ex["shift"]["targets"]["label"] == "persisted"
    # ungraded aux → those datasets skip the example
    row2 = dict(row, aux_targets={"shift_outcome": {"label": "unknown"}})
    ex2 = build_examples(snap, row2)
    assert ex2["shift"] is None and ex2["fundamentals"] is None


def test_fewshot_block_flag_gated(monkeypatch):
    from longterm_agent.agent import nodes
    prompts = []

    def _capture(llm, schema, prompt):
        prompts.append(prompt)
        return schema(direction="NEUTRAL", conviction=0.1, horizon="4w", thesis="t")

    monkeypatch.setattr(nodes, "_invoke_structured", _capture)
    state = _state()
    state["shift_analogs"] = {"n_analogs": 5, "insufficient": False,
                              "median_fwd_return_4w": 0.03, "share_positive": 0.6,
                              "nearest_cases": ["AMD 2026-05-01 positive ret4w=+6.00%"]}
    cfg_on = SimpleNamespace(FEWSHOT_ANALOGS=True, MAX_REFLECTION_ITERATIONS=0)
    cfg_off = SimpleNamespace(FEWSHOT_ANALOGS=False, MAX_REFLECTION_ITERATIONS=0)
    nodes.synthesis_node(dict(state), llm=None, config=cfg_on)
    nodes.synthesis_node(dict(state), llm=None, config=cfg_off)
    assert "PAST GRADED ANALOGS" in prompts[0] and "AMD 2026-05-01" in prompts[0]
    assert "PAST GRADED ANALOGS" not in prompts[1]          # flag off → byte-identical prompt
    # insufficient analogs are never shown as examples, even with the flag on
    state["shift_analogs"]["insufficient"] = True
    nodes.synthesis_node(dict(state), llm=None, config=cfg_on)
    assert "PAST GRADED ANALOGS" not in prompts[2]
