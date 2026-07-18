"""Consensus stage B (LT_CONSENSUS_IN_VERDICT): prompt-block rendering + node wiring.

Contract under test:
  - flag OFF → prompts byte-identical to before (no consensus text, no extra state key);
  - flag ON  → fundamentals verdict prompt carries the consensus block, the delta carries
    `street_consensus`, and synthesis renders one posture line from it;
  - Phase 8 delta contract holds either way (input state never mutated).
No DB, no network: read_fundamentals / read_consensus / technicals are monkeypatched.
"""
from datetime import date, datetime
from types import SimpleNamespace

from longterm_agent.tools.fundamentals_tool import (consensus_prompt_block,
                                                    consensus_posture_line)

CONS = {
    "available": True,
    "street_forward": {
        "eps_0q": {"avg": 1.89, "low": 1.83, "high": 1.99, "numberOfAnalysts": 31},
        "revenue_0q": {"avg": 108_900_000_000, "low": 107_000_000_000, "high": 112_000_000_000},
    },
    "eps_trend_0q": {"current": 1.894, "30daysAgo": 1.894, "90daysAgo": 1.729},
    "recent_surprises": [
        {"date": "2026-04-30", "eps_surprise_pct": 3.6, "revenue_surprise_pct": 2.5},
        {"date": "2026-01-29", "eps_surprise_pct": 6.4, "revenue_surprise_pct": 1.5},
        {"date": "2025-10-29", "eps_surprise_pct": -1.2, "revenue_surprise_pct": None},
    ],
    "next_report": {"date": "2026-07-30", "eps_estimated": 1.88,
                    "revenue_estimated": 108_847_000_000},
}


# --- pure renderers --------------------------------------------------------------------
def test_prompt_block_full():
    blk = consensus_prompt_block(CONS)
    assert blk.startswith("STREET CONSENSUS & SURPRISE HISTORY")
    assert "eps_0q avg=1.89 (n=31)" in blk
    assert "now=1.894" in blk and "90d ago=1.729" in blk
    assert "2026-04-30: EPS +3.6% / rev +2.5%" in blk
    assert "2025-10-29: EPS -1.2%" in blk          # None revenue → no rev segment
    assert "next report: 2026-07-30" in blk
    # Stage B.2 guard rides in EVERY rendered block (the #12 INTC failure class)
    assert "GUARD: surprise history modulates conviction ONLY" in blk
    assert blk.endswith("\n")


def test_prompt_block_degrades():
    assert consensus_prompt_block({}) == ""
    note_only = {"available": False, "note": "no consensus snapshots as-of"}
    blk = consensus_prompt_block(note_only)
    assert "no consensus snapshots as-of" in blk    # header + note, never a crash


def test_posture_line():
    line = consensus_posture_line(CONS)
    assert line.startswith("STREET CONSENSUS (advisory")
    assert "rising (+9.5% over 90d)" in line        # (1.894-1.729)/1.729
    assert "beat street 2/3" in line
    assert "next report 2026-07-30" in line
    assert consensus_posture_line({}) == ""
    assert consensus_posture_line({"available": False, "note": "x"}) == ""


# --- retro fallbacks (free FMP key covers ~40 symbols; history from later snapshots) ---
YF_HIST = [
    {"period": "2026-03-31T00:00:00", "epsActual": 0.26, "epsEstimate": 0.30278,
     "surprisePercent": -0.1413},
    {"period": "2025-12-31T00:00:00", "epsActual": 0.56, "epsEstimate": 0.6639,
     "surprisePercent": -0.1565},
    {"period": "2025-09-30T00:00:00", "epsActual": 2.21, "epsEstimate": 2.32057,
     "surprisePercent": -0.0476},
]


def test_non_pit_fmp_keeps_surprises_drops_next_report():
    from longterm_agent.tests.test_consensus import FMP
    from longterm_agent.tools.fundamentals_tool import format_consensus
    out = format_consensus(None, FMP, date(2026, 5, 1), fmp_is_pit=False)
    assert out["recent_surprises"][0]["date"] == "2026-04-30"   # report-date PIT filter holds
    assert "next_report" not in out          # estimate may embed post-as_of revisions


def test_yf_history_fallback_from_strict_snapshot():
    from longterm_agent.tools.fundamentals_tool import format_consensus
    est = {"earnings_history": YF_HIST}
    out = format_consensus(est, None, date(2026, 7, 15))
    s = out["recent_surprises"]
    assert s[0] == {"date": "2026-03-31", "eps_surprise_pct": -14.1,
                    "revenue_surprise_pct": None}
    assert [r["date"] for r in s] == ["2026-03-31", "2025-12-31", "2025-09-30"]


def test_yf_future_snapshot_fallback_applies_reporting_lag_buffer():
    from longterm_agent.tools.fundamentals_tool import format_consensus
    # as_of 2026-04-10: Q ending 03-31 has NOT been reported yet → buffered out;
    # 2025-12-31 (100 days back) survives.
    out = format_consensus(None, None, date(2026, 4, 10), yf_hist_future=YF_HIST)
    dates = [r["date"] for r in out["recent_surprises"]]
    assert "2026-03-31" not in dates and "2025-12-31" in dates
    # FMP surprises win over the future-snapshot fallback when both exist
    from longterm_agent.tests.test_consensus import FMP
    both = format_consensus(None, FMP, date(2026, 5, 1), fmp_is_pit=False,
                            yf_hist_future=YF_HIST)
    assert both["recent_surprises"][0]["revenue_surprise_pct"] is not None


# --- node wiring -----------------------------------------------------------------------
FUND = {"has_statements": True, "as_of": "2026-07-15", "revenue_yoy": 0.12,
        "revenue_qoq": 0.03, "margin_series": [0.31, 0.30], "valuation": {},
        "expectation_gap": None, "capital": {}, "street": None}


class _CaptureLLM:
    """with_structured_output(...).invoke(prompt) → records prompt, returns schema()."""
    def __init__(self):
        self.prompt = None

    def with_structured_output(self, schema):
        outer = self

        class _Runner:
            def invoke(self, prompt):
                outer.prompt = prompt
                return schema(verdict="ignores", reasoning="t")
        return _Runner()


def _run_fundamentals(monkeypatch, flag):
    from longterm_agent.agent import nodes
    monkeypatch.setattr(nodes, "read_fundamentals", lambda *a, **k: dict(FUND))
    monkeypatch.setattr(nodes, "compute_daily_technicals", lambda *a, **k: {"available": False})
    monkeypatch.setattr(nodes, "read_consensus", lambda *a, **k: dict(CONS))
    llm = _CaptureLLM()
    cfg = SimpleNamespace(CONSENSUS_IN_VERDICT=flag, LOOKBACK_DAYS_TECHNICAL_LT=180)
    state = {"ticker": "NVDA", "as_of": datetime(2026, 7, 15),
             "background_shift": {"has_shifted": False}}
    before = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in state.items()}
    out = nodes.fundamentals_analysis_node(state, llm, db_manager=None, config=cfg)
    assert state == before                          # delta contract: input untouched
    return out, llm.prompt


def test_fundamentals_flag_off_is_byte_identical(monkeypatch):
    out, prompt = _run_fundamentals(monkeypatch, flag=False)
    assert "STREET CONSENSUS" not in prompt
    assert "street_consensus" not in out            # key absent, not empty


def test_fundamentals_flag_on_injects_block(monkeypatch):
    out, prompt = _run_fundamentals(monkeypatch, flag=True)
    assert "STREET CONSENSUS & SURPRISE HISTORY" in prompt
    assert "2026-04-30: EPS +3.6%" in prompt
    assert out["street_consensus"]["available"] is True


def _run_synthesis(monkeypatch, flag, with_state_key):
    from longterm_agent.agent import nodes
    from longterm_agent.schemas import LongTermSignal
    captured = {}

    def fake_invoke(llm, schema, prompt):
        captured["prompt"] = prompt
        return LongTermSignal(direction="NEUTRAL", conviction=0.0, horizon="n/a", thesis="t")
    monkeypatch.setattr(nodes, "_invoke_structured", fake_invoke)
    cfg = SimpleNamespace(CONSENSUS_IN_VERDICT=flag, FEWSHOT_ANALOGS=False)
    state = {"ticker": "NVDA", "as_of": datetime(2026, 7, 15),
             "background_shift": {"has_shifted": False}, "fundamentals": {}}
    if with_state_key:
        state["street_consensus"] = dict(CONS)
    nodes.synthesis_node(state, llm=None, config=cfg)
    return captured["prompt"]


def test_synthesis_posture_line_on(monkeypatch):
    prompt = _run_synthesis(monkeypatch, flag=True, with_state_key=True)
    assert "STREET CONSENSUS (advisory" in prompt
    assert "beat street 2/3" in prompt


def test_synthesis_flag_off_or_no_data(monkeypatch):
    # flag off → byte-identical even if the key somehow exists
    assert "STREET CONSENSUS" not in _run_synthesis(monkeypatch, flag=False, with_state_key=True)
    # flag on but fundamentals never set the key (e.g. NO_DATA path) → no line, no crash
    assert "STREET CONSENSUS" not in _run_synthesis(monkeypatch, flag=True, with_state_key=False)
