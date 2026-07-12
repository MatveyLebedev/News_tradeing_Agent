"""Phase 14 tests: regime-context stamp (pure — synthetic series, no DB)."""
import sys
from datetime import date, timedelta
from pathlib import Path

# The fetcher package uses flat imports for the container layout (see
# test_edgar_normalize.py) — same path shim to import macro_fetcher from the repo.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bloomberg_news_fetcher"))
from macro_fetcher import parse_fred_csv  # noqa: E402

from longterm_agent.backtest import _hy_band, _vix_band  # noqa: E402
from longterm_agent.tools.regime_tool import compute_regime_context  # noqa: E402

AS_OF = date(2026, 7, 10)


def _daily(vals, end=AS_OF):
    """[(date, value)] ending at `end`, one obs per day, oldest first."""
    n = len(vals)
    return [(end - timedelta(days=n - 1 - i), v) for i, v in enumerate(vals)]


def test_empty_series_unavailable():
    ctx = compute_regime_context({}, AS_OF)
    assert ctx["available"] is False


def test_pit_windowing_excludes_future_values():
    # a value dated AFTER as_of must never leak into the stamp
    series = {"vix": _daily([15.0] * 10) + [(AS_OF + timedelta(days=1), 99.0)]}
    ctx = compute_regime_context(series, AS_OF)
    assert ctx["vix"] == 15.0


def test_vix_flags_capitulation_and_inversion():
    series = {"vix": _daily([18.0] * 200 + [35.0]),
              "vix3m": _daily([22.0] * 201)}
    ctx = compute_regime_context(series, AS_OF)
    assert ctx["vix"] == 35.0
    assert ctx["vix_capitulation"] is True          # >30
    assert ctx["vix_term_inverted"] is True         # spot 35 > 3m 22
    assert ctx["vix_pctile_5y"] == 100.0            # highest value in its own window
    calm = compute_regime_context({"vix": _daily([18.0] * 201),
                                   "vix3m": _daily([22.0] * 201)}, AS_OF)
    assert calm["vix_capitulation"] is False and calm["vix_term_inverted"] is False


def test_dgs10_shock_flag_both_directions():
    # +50bp over the last 15 observations → shock
    up = {"dgs10": _daily([4.0] * 30 + [4.5] * 15)}
    ctx = compute_regime_context(up, AS_OF)
    assert ctx["dgs10_delta_15s_bp"] == 50.0 and ctx["dgs10_shock"] is True
    down = {"dgs10": _daily([4.0] * 30 + [3.5] * 15)}
    ctx = compute_regime_context(down, AS_OF)
    assert ctx["dgs10_delta_15s_bp"] == -50.0 and ctx["dgs10_shock"] is True
    flat = {"dgs10": _daily([4.0] * 45)}
    assert compute_regime_context(flat, AS_OF)["dgs10_shock"] is False


def test_hy_oas_percentile_and_roc():
    series = {"hy_oas": _daily([3.0] * 200 + [5.0])}
    ctx = compute_regime_context(series, AS_OF)
    assert ctx["hy_oas"] == 5.0
    assert ctx["hy_oas_pctile_5y"] == 100.0
    assert ctx["hy_oas_roc_20d"] == 2.0             # 5.0 now vs 3.0 twenty obs back


def test_percentile_needs_min_observations():
    ctx = compute_regime_context({"vix": _daily([20.0] * 10)}, AS_OF)
    assert ctx["vix"] == 20.0 and ctx["vix_pctile_5y"] is None  # 10 obs < min → no pctile


def test_oil_bands():
    for px, band in ((70.0, "deflationary"), (90.0, "neutral"), (110.0, "hawkish")):
        ctx = compute_regime_context({"brent": _daily([px] * 5)}, AS_OF)
        assert ctx["oil_band"] == band, px


def test_report_bands():
    assert _vix_band({"vix": 15.0}) == "vix<20"
    assert _vix_band({"vix": 25.0}) == "vix20-30"
    assert _vix_band({"vix": 40.0}) == "vix>30"
    assert _vix_band(None) is None and _vix_band({}) is None
    assert _hy_band({"hy_oas_pctile_5y": 30.0}) == "hy<p50"
    assert _hy_band({"hy_oas_pctile_5y": 70.0}) == "hy_p50-80"
    assert _hy_band({"hy_oas_pctile_5y": 95.0}) == "hy>p80"


def test_parse_fred_csv_skips_header_and_missing():
    text = "DATE,DGS10\n2026-07-08,4.41\n2026-07-09,.\n2026-07-10,4.38\n"
    rows = parse_fred_csv(text)
    assert rows == [(date(2026, 7, 8), 4.41), (date(2026, 7, 10), 4.38)]
