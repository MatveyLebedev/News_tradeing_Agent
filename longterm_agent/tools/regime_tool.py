"""
Regime-context stamp (Modification Plan Phase 14) — reads `macro_series` AS-OF and
computes the snapshot that rides on every signal (risk_checks) and graded backtest row.

STRICTLY DESCRIPTIVE: nothing gates or adjusts conviction on these values. All walk-forward
evidence so far comes from ONE regime (a bull window); the stamp exists so that once the
data spans ≥2 regimes, regime-dependence of the strategy becomes measurable — and only
then may any decision logic reference it.

Stamp fields (expert #28, #31-33, #37):
  vix, vix_pctile_5y, vix3m, vix_term_inverted (spot>3m = acute stress), vix_capitulation (>30)
  hy_oas, hy_oas_pctile_5y, hy_oas_roc_20d (level change over 20 observations)
  dgs10, dgs10_delta_15s_bp (Δ over 15 observations, bp), dgs10_shock (|Δ|≥40bp)
  brent, wti, oil_band (<$80 deflationary / >$100 hawkish)

`compute_regime_context` is pure (unit-testable); `regime_context` adds the DB read with a
per-(as_of-date) process cache so backtest grading doesn't re-read 5y of series per row.
Degrades to {"available": False} when the table is missing/empty — never fails a run.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import psycopg2

from .pit import daily_bar_cutoff

PCTILE_WINDOW_YEARS = 5
PCTILE_MIN_OBS = 60          # below this a percentile is noise → None
DGS10_SHOCK_BP = 40.0        # expert #28: Δ ≥ 40bp / 15 sessions = "hidden hike/cut"
DGS10_SESSIONS = 15
HY_ROC_OBS = 20
VIX_CAPITULATION = 30.0
OIL_DEFLATIONARY = 80.0
OIL_HAWKISH = 100.0

_STAMP_SERIES = ("vix", "vix3m", "hy_oas", "dgs10", "brent", "wti")

_cache: Dict[date, Dict[str, Any]] = {}


def _pctile(values: List[float], current: float) -> Optional[float]:
    if len(values) < PCTILE_MIN_OBS:
        return None
    return round(100.0 * sum(1 for v in values if v <= current) / len(values), 1)


def compute_regime_context(series: Dict[str, List[Tuple[date, float]]], as_of_date: date) -> Dict[str, Any]:
    """Pure snapshot from {series: [(date, value)] sorted asc, dates ≤ as_of enforced here}."""
    win_start = as_of_date - timedelta(days=365 * PCTILE_WINDOW_YEARS)
    s = {name: [(d, v) for d, v in pairs if d <= as_of_date]
         for name, pairs in (series or {}).items()}
    if not any(s.get(name) for name in _STAMP_SERIES):
        return {"available": False, "note": "no macro series as-of"}

    def last(name):
        return round(s[name][-1][1], 2) if s.get(name) else None

    def nth_back(name, n):
        pairs = s.get(name) or []
        return pairs[-1 - n][1] if len(pairs) > n else None

    ctx: Dict[str, Any] = {"available": True, "as_of": as_of_date.isoformat(),
                           "dates": {name: s[name][-1][0].isoformat()
                                     for name in _STAMP_SERIES if s.get(name)}}

    vix, vix3m = last("vix"), last("vix3m")
    ctx["vix"] = vix
    if vix is not None:
        window = [v for d, v in s["vix"] if d >= win_start]
        ctx["vix_pctile_5y"] = _pctile(window, vix)
        ctx["vix_capitulation"] = vix > VIX_CAPITULATION
    ctx["vix3m"] = vix3m
    ctx["vix_term_inverted"] = (vix > vix3m) if (vix is not None and vix3m is not None) else None

    hy = last("hy_oas")
    ctx["hy_oas"] = hy
    if hy is not None:
        window = [v for d, v in s["hy_oas"] if d >= win_start]
        ctx["hy_oas_pctile_5y"] = _pctile(window, hy)
        prev = nth_back("hy_oas", HY_ROC_OBS)
        ctx["hy_oas_roc_20d"] = round(hy - prev, 3) if prev is not None else None

    y10 = last("dgs10")
    ctx["dgs10"] = y10
    if y10 is not None:
        prev = nth_back("dgs10", DGS10_SESSIONS)
        delta_bp = round((y10 - prev) * 100.0, 1) if prev is not None else None
        ctx["dgs10_delta_15s_bp"] = delta_bp
        ctx["dgs10_shock"] = (abs(delta_bp) >= DGS10_SHOCK_BP) if delta_bp is not None else None

    brent, wti = last("brent"), last("wti")
    ctx["brent"], ctx["wti"] = brent, wti
    if brent is not None:
        ctx["oil_band"] = ("deflationary" if brent < OIL_DEFLATIONARY
                           else "hawkish" if brent > OIL_HAWKISH else "neutral")
    return ctx


def regime_context_from_conn(conn, as_of) -> Dict[str, Any]:
    """Snapshot via an existing psycopg2 connection (grading loops reuse theirs).

    Values are daily closes (VIX, HY-OAS, 10Y, oil), so they follow the same
    completed-session rule as price bars: the value dated D is not knowable until D
    closes. Keyed and cached by that CUTOFF, not by the as_of date — two runs on the same
    day either side of the close legitimately see different macro.
    """
    cutoff = daily_bar_cutoff(as_of)
    if cutoff in _cache:
        return _cache[cutoff]
    start = cutoff - timedelta(days=365 * PCTILE_WINDOW_YEARS + 30)
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT series, date, value FROM macro_series
                           WHERE date <= %s AND date >= %s ORDER BY series, date""",
                        [cutoff, start])
            rows = cur.fetchall()
    except Exception as e:
        conn.rollback()
        return {"available": False, "note": f"macro_series read failed: {e}"}
    series: Dict[str, List[Tuple[date, float]]] = {}
    for name, d, v in rows:
        series.setdefault(name, []).append((d, float(v)))
    ctx = compute_regime_context(series, cutoff)
    _cache[cutoff] = ctx
    if len(_cache) > 256:  # bound memory across long grading sweeps
        _cache.pop(next(iter(_cache)))
    return ctx


def regime_context(db_manager, as_of) -> Dict[str, Any]:
    """Snapshot for the live pipeline (risk_gate). Never raises."""
    try:
        conn = psycopg2.connect(**db_manager.db_config)
    except Exception as e:
        return {"available": False, "note": f"db connect failed: {e}"}
    try:
        return regime_context_from_conn(conn, as_of)
    finally:
        conn.close()
