"""Bot charts and the HTML digest (pure; no DB, no Telegram).

The behaviour worth pinning is mostly about REFUSING to draw: a chart implies a
population, and this project has already been misled once by reading a handful of rows
as a result. Silence below the minimum is the feature, not a gap.
"""
import pytest

from longterm_agent import charts


def _pos(ticker, ret, day=5, verdict="LONG"):
    return {"ticker": ticker, "verdict": verdict, "position_return": ret, "days_open": day}


def test_png_is_produced_for_a_real_set():
    png = charts.open_positions_chart([_pos("AMD", 0.09), _pos("JNJ", -0.01)])
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_one_position_draws_nothing():
    """A one-bar bar chart is a stat tile pretending to be a chart (anti-patterns)."""
    assert charts.open_positions_chart([_pos("AMD", 0.09)]) is None
    assert charts.open_positions_chart([]) is None


def test_two_closed_trades_draw_nothing():
    """Two outcomes are not a distribution; the text line says it better."""
    rows = [{"ticker": "A", "verdict": "LONG", "excess": 0.01},
            {"ticker": "B", "verdict": "SHORT", "excess": -0.02}]
    assert charts.closed_trades_chart(rows) is None
    assert charts.closed_trades_chart(rows + [{"ticker": "C", "verdict": "LONG",
                                               "excess": 0.03}]) is not None


def test_positions_without_a_price_are_excluded_not_zeroed():
    """An unpriced position must drop out, never render as a flat 0% bar."""
    rows = [_pos("AMD", 0.09), _pos("JNJ", None), _pos("NKE", None)]
    assert charts.open_positions_chart(rows) is None      # only one priced row left


def test_identical_row_notes_are_hoisted_out_of_the_rows():
    """Six bars each stamped 'day 2 / 28' says one thing six times and hits the zero line."""
    notes, subtitle = charts._per_row_note(["day 2 / 28"] * 6)
    assert notes == [""] * 6 and subtitle == "day 2 / 28"


def test_differing_row_notes_stay_on_their_rows():
    notes, subtitle = charts._per_row_note(["day 2 / 28", "day 9 / 28"])
    assert notes == ["day 2 / 28", "day 9 / 28"] and subtitle is None


def test_partial_notes_stay_on_their_rows():
    """Only a note present on EVERY row and identical can move to the subtitle."""
    notes, subtitle = charts._per_row_note(["stop", "", ""])
    assert notes == ["stop", "", ""] and subtitle is None


def test_palette_is_the_documented_diverging_pair():
    """Reference palette, dark surface — not hand-picked hues, and never red/green."""
    assert (charts.POSITIVE, charts.NEGATIVE) == ("#3987e5", "#e66767")
    assert charts.SURFACE == "#1a1a19"


# --- the HTML digest --------------------------------------------------------------------
def test_digest_is_not_wrapped_in_a_code_block():
    """<pre> is what renders as Telegram's monospace 'copy' card — the thing being fixed."""
    from longterm_agent.stats import format_weekly_report_html
    rep = {"days": 7, "new": [], "open": [], "closed": [],
           "closed_wins": 0, "closed_graded": 0, "closed_mean_excess": None}
    out = format_weekly_report_html(rep)
    assert "<pre>" not in out and "<b>" in out


def test_digest_warns_while_the_sample_is_tiny():
    from longterm_agent.stats import format_weekly_report_html
    rep = {"days": 7, "new": [], "open": [],
           "closed": [{"ticker": "GOOGL", "verdict": "SHORT", "excess": -0.004,
                       "excess_stopped": None, "exit_reason": "horizon", "exit_days": None}],
           "closed_wins": 0, "closed_graded": 1, "closed_mean_excess": -0.004}
    assert "not a sample size" in format_weekly_report_html(rep)


def test_digest_escapes_html_in_data():
    from longterm_agent.stats import format_weekly_report_html
    rep = {"days": 7, "new": [{"ticker": "A<b>", "as_of": "2026-07-19",
                               "verdict": "LONG", "conviction": 0.7}],
           "open": [], "closed": [], "closed_wins": 0, "closed_graded": 0,
           "closed_mean_excess": None}
    assert "A&lt;b&gt;" in format_weekly_report_html(rep)


# --- /gate and /health formatting -------------------------------------------------------
def test_gate_names_the_real_reason_when_the_column_is_absent():
    """'No signals' and 'no traces stored' are different problems; saying the wrong one
    sends someone hunting a bug that isn't there."""
    from longterm_agent.stats import format_gate_breakdown
    out = format_gate_breakdown({"days": 90, "covered": 0, "total": 0, "vetoes": {},
                                 "penalties": {}, "filtered": [], "traded": [],
                                 "column_missing": True})
    assert "predate" in out and "No directional signals" not in out


def test_gate_distinguishes_an_empty_window():
    from longterm_agent.stats import format_gate_breakdown
    out = format_gate_breakdown({"days": 90, "covered": 0, "total": 0, "vetoes": {},
                                 "penalties": {}, "filtered": [], "traded": []})
    assert "No directional signals" in out


def test_gate_reports_untraced_rows_rather_than_guessing():
    from longterm_agent.stats import format_gate_breakdown
    out = format_gate_breakdown({"days": 90, "covered": 0, "total": 7, "vetoes": {},
                                 "penalties": {}, "filtered": [], "traded": []})
    assert "7 directional signals" in out and "predate" in out


def test_gate_states_the_direction_of_the_effect():
    from longterm_agent.stats import format_gate_breakdown
    g = {"days": 90, "covered": 40, "total": 40, "vetoes": {"no_shift": 3},
         "penalties": {"priced_in": 2}, "filtered": [0.05] * 20, "traded": [0.01] * 20}
    out = format_gate_breakdown(g)
    assert "gate costing" in out and "no_shift" in out


def test_gate_warns_while_the_sample_is_tiny():
    from longterm_agent.stats import format_gate_breakdown
    g = {"days": 90, "covered": 5, "total": 5, "vetoes": {}, "penalties": {},
         "filtered": [0.05], "traded": [0.01]}
    assert "not a verdict" in format_gate_breakdown(g)


def test_health_flags_each_condition_that_has_bitten_before():
    from longterm_agent.stats import format_health
    bad = format_health({"days": 30, "runs": 100, "failure_rate": 0.14,
                         "price_max": "2026-07-17", "price_lag_days": 4,
                         "overdue": 2, "pending": 6, "next_mature": "2026-08-16"})
    assert "⚠️" in bad and "deflated" in bad
    good = format_health({"days": 30, "runs": 100, "failure_rate": 0.0,
                          "price_max": "2026-07-21", "price_lag_days": 0,
                          "overdue": 0, "pending": 6, "next_mature": "2026-08-16"})
    assert "⚠️" not in good and "deflated" not in good


# --- /open formatting -------------------------------------------------------------------
def test_unrealized_excess_applies_the_short_sign_exactly_once():
    """position_return arrives direction-signed; flipping it twice is how a profitable
    SHORT quietly reports a negative excess."""
    from longterm_agent.stats import unrealized_excess
    # stock -5% (so the SHORT's position_return is +5%), SPY +1% → excess = +6%
    assert abs(unrealized_excess("SHORT", 0.05, 0.01) - 0.06) < 1e-12
    assert abs(unrealized_excess("LONG", 0.05, 0.01) - 0.04) < 1e-12
    assert unrealized_excess("SHORT", None, 0.01) is None
    assert unrealized_excess("SHORT", 0.05, None) is None


def test_open_positions_render_book_mean_and_warning():
    from longterm_agent.stats import format_open_positions_html
    rows = [{"ticker": "AMD", "verdict": "LONG", "as_of": "2026-07-19", "days_open": 2,
             "position_return": 0.012, "excess_now": 0.008, "entry_px_used": 155.2,
             "last_px": 157.1, "matures": "2026-08-17"},
            {"ticker": "MRK", "verdict": "SHORT", "as_of": "2026-07-19", "days_open": 2,
             "position_return": -0.004, "excess_now": None, "entry_px_used": None,
             "last_px": None, "matures": "2026-08-17"}]
    out = format_open_positions_html(rows)
    assert "AMD" in out and "155.20" in out and "157.10" in out
    assert "book mean" in out and "unrealized" in out
    assert "vs SPY <code>—</code>" in out  # missing excess is a dash, not a crash


def test_open_positions_empty_book_says_flat():
    from longterm_agent.stats import format_open_positions_html
    out = format_open_positions_html([])
    assert "(0)" in out and "flat" in out


def test_open_positions_show_the_agents_thesis_verbatim():
    from longterm_agent.stats import format_open_positions_html
    rows = [{"ticker": "NKE", "verdict": "SHORT", "as_of": "2026-07-19", "days_open": 3,
             "position_return": 0.015, "excess_now": 0.017, "entry_px_used": 43.76,
             "last_px": 43.09, "matures": "2026-08-16",
             "thesis": "Inventory glut & <weak> China demand"}]
    out = format_open_positions_html(rows)
    assert "Inventory glut" in out and "&lt;weak&gt;" in out  # present, and escaped


def test_ticker_status_renders_full_reasoning_sections():
    from longterm_agent.stats import format_ticker_status_html
    st = {"ticker": "MRK", "verdict": "SHORT", "traded": True, "as_of": "2026-07-19",
          "conviction": 0.8, "fwd_return_4w": None, "benchmark_return_4w": None,
          "horizon": "4-12 weeks", "thesis": "Patent cliff pressure",
          "supporting_factors": ["Keytruda LOE 2028", "pipeline gaps"],
          "key_risks": ["M&A surprise"],
          "invalidation_conditions": ["successful subcutaneous launch"]}
    out = format_ticker_status_html(st)
    assert "Patent cliff pressure" in out
    assert "Why" in out and "Keytruda LOE 2028" in out
    assert "Risks" in out and "M&amp;A surprise" in out  # escaped
    assert "Invalidated if" in out and "subcutaneous" in out


def test_ticker_status_without_reasoning_lists_stays_compact():
    from longterm_agent.stats import format_ticker_status_html
    st = {"ticker": "AMD", "verdict": "NEUTRAL", "traded": False, "as_of": "2026-07-19",
          "conviction": 0.2, "fwd_return_4w": None, "benchmark_return_4w": None,
          "thesis": None, "horizon": None, "supporting_factors": [],
          "key_risks": [], "invalidation_conditions": []}
    out = format_ticker_status_html(st)
    assert "Why" not in out and "Risks" not in out and "Invalidated" not in out


# --- the Monday forward cohort job ------------------------------------------------------
def test_monday_fwd_command_matches_the_registration():
    """The argv IS the experiment: a wrong flag silently produces a book under a
    different convention. Pinned to the monday_t1_fwd registration in the ledger."""
    from datetime import date
    from longterm_agent.auto_longterm_agent import monday_fwd_command, MONDAY_FWD_TICKERS
    cmd = monday_fwd_command(date(2026, 7, 27))
    assert "--backtest-id" in cmd and "monday_t1_fwd" in cmd
    assert cmd[cmd.index("--fill") + 1] == "d0"          # same-Monday entry
    assert cmd[cmd.index("--at") + 1] == "06:30"
    assert cmd[cmd.index("--start") + 1] == "2026-07-27"
    i = cmd.index("--horizons")
    assert cmd[i + 1:i + 6] == ["14", "28", "42", "56", "84"]  # the full curve
    assert len(MONDAY_FWD_TICKERS) == 30 and "SQ" not in MONDAY_FWD_TICKERS
    # The Monday line was closed by the operator before this ever ran on a schedule;
    # the default must stay OFF so a later deploy cannot silently re-open it.
    from longterm_agent.config import LongTermAgentConfig
    assert LongTermAgentConfig.MONDAY_FWD is False


def test_monday_fwd_env_pins_the_provider_without_touching_the_parent():
    """A subprocess on purpose: pinning novita in-process would 404 every gemini map
    call of the live batch (measured 2026-07-20, aborted a run)."""
    from longterm_agent.auto_longterm_agent import monday_fwd_env
    base = {"PATH": "/usr/bin", "LT_METRICS_VERSION": "2"}
    env = monday_fwd_env(base)
    assert env["LLM_PROVIDER"] == "novita" and env["LT_LLM_CACHE"] == "true"
    assert env["LT_METRICS_VERSION"] == "2"      # inherits the container's detection pick
    assert "LLM_PROVIDER" not in base            # parent env untouched
