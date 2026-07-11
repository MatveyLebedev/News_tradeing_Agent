"""Phase 3 quick-win tests: version split, analog honesty (Wilson / min-n), config knobs."""
from datetime import datetime

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.tools.shift_library_tool import wilson_interval, _empty
from longterm_agent.tools.background_metrics import compute_shift_metrics
from longterm_agent.agent.nodes import _analog_desc


def test_version_split():
    # Phase 5: DETECTION_VERSION derives from METRICS_VERSION (1→0.1.0, 2→0.2.0).
    expected = {1: "0.1.0", 2: "0.2.0"}[LongTermAgentConfig.METRICS_VERSION]
    assert LongTermAgentConfig.DETECTION_VERSION == expected
    # v1 must stay the default until the v2 walk-forward gates pass (live cutover is an
    # explicit env change, never a code default flip hidden in a commit).
    import os
    if not os.getenv("LT_METRICS_VERSION"):
        assert LongTermAgentConfig.DETECTION_VERSION == "0.1.0"
    # DB rows carry the detection version in `agent_version` — the alias must not drift,
    # or existing shift_library analogs and results identities get orphaned.
    assert LongTermAgentConfig.AGENT_VERSION == LongTermAgentConfig.DETECTION_VERSION


def test_provenance_carries_versions_and_knobs():
    prov = LongTermAgentConfig.provenance()
    for key in ("detection_version", "pipeline_version", "knobs"):
        assert key in prov, f"provenance missing {key}"
    for knob in ("min_abs_delta", "attention_spike_ratio", "priced_in_move_pct",
                 "priced_in_penalty", "earnings_penalty", "min_analogs",
                 "shift_z", "conf_threshold"):
        assert knob in prov["knobs"], f"provenance knobs missing {knob}"


def test_wilson_interval():
    lo, hi = wilson_interval(4, 6)
    assert 0.28 <= lo <= 0.33 and 0.87 <= hi <= 0.92  # ~ (0.30, 0.90)
    assert wilson_interval(0, 0) is None
    lo, hi = wilson_interval(5, 5)  # 100% of 5 is NOT certainty
    assert hi == 1.0 and 0.5 < lo < 0.7
    lo, hi = wilson_interval(0, 5)
    assert lo == 0.0 and hi < 0.5


def test_empty_analogs_marked_insufficient():
    e = _empty("cold start: no graded analogs yet")
    assert e["insufficient"] is True
    assert e["n_analogs"] == 0
    assert e["share_positive_ci"] is None


def test_analog_desc_cold_start():
    out = _analog_desc({"n_analogs": 0, "fundamentals_matched_note": "cold start: no graded analogs yet"})
    assert "cold start" in out


def test_analog_desc_insufficient_suppresses_numbers():
    small = {"n_analogs": 3, "insufficient": True, "median_fwd_return_4w": 0.05,
             "fundamentals_matched_note": "insufficient analogs (n=3 < 5) — do not weigh"}
    out = _analog_desc(small)
    assert "DO NOT weigh" in out
    assert "0.05" not in out  # the number must not reach the prompt for the LLM to anchor on


def test_analog_desc_sufficient_carries_ci():
    ok = {"n_analogs": 6, "insufficient": False, "median_fwd_return_4w": 0.03,
          "median_excess_4w": 0.01, "share_positive": 0.67, "share_positive_ci": (0.30, 0.90)}
    out = _analog_desc(ok)
    assert "6 past analogs" in out
    assert "95% CI 30%–90%" in out


def test_attention_spike_ratio_is_configurable():
    # 4 weekly buckets, flat sentiment (no sentiment break); recent rate 3x baseline.
    as_of = datetime(2026, 7, 10)
    summaries = [{"sentiment": 0.0, "themes": [], "_week": f"w{i}"} for i in range(4)]
    buckets = [{"count": c} for c in (2, 2, 2, 10)]
    common = dict(bucket_summaries=summaries, period_buckets=buckets, as_of=as_of,
                  shift_window_days=14, bucket_days=7, ticker="TEST", z_threshold=1.0)
    m_default = compute_shift_metrics(**common, attention_spike_ratio=2.0)
    m_strict = compute_shift_metrics(**common, attention_spike_ratio=3.5)
    assert m_default["attention"]["spike_ratio"] == 3.0
    assert m_default["shift_flag"] is True   # 3.0 >= 2.0
    assert m_strict["shift_flag"] is False   # 3.0 < 3.5
