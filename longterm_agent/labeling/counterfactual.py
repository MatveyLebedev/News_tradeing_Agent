"""
Labeler `counterfactual`: what the stock did after EVERY run, whatever the verdict.

The grader prices only LONG/SHORT rows, so 800+ NEUTRAL and NO_DATA runs had no outcome and
the training set saw only the runs where the agent acted. This labeler prices every run the
way the grader would have priced a trade: same entry convention (resolve_entry from the
decision moment, minute fill for an in-session decision with the SPY leg on the same
convention), same exits (t1_return), same data-through guard. On a graded LONG/SHORT row
`stock_excess_4w` × the position sign therefore equals the grader's excess_4w.

`stock_excess_*` is unsigned (stock − SPY): a fact about the stock, not about the verdict.
`excess_z_4w` scales it by the ticker's own 4-week excess volatility over the year before
as_of, and `opportunity_4w` says whether that move was a trade worth taking (|z| ≥ 1).
"""
from datetime import datetime, time, timedelta
from statistics import pstdev
from typing import Dict, Iterable, List, Optional

from core import tracing
from longterm_agent.exit_rules import FILL_CONVENTION_MID, resolve_entry, t1_return
from longterm_agent.grade_results import (BENCHMARK, EXTRA_HORIZONS, HORIZON_DAYS,
                                          _decision_moment, _finite_ret)
from longterm_agent.grade_shifts import _has_data_through

from .base import RUN_NODE, Label, ScoreSpec
from .store import existing_keys

NAME = "counterfactual"
VERSION = "1"
RUN_KIND = "live"
HORIZONS = {"2w": EXTRA_HORIZONS["2w"], "4w": HORIZON_DAYS, "12w": EXTRA_HORIZONS["12w"]}
SIGMA_LOOKBACK_DAYS = 365   # the year before as_of
SIGMA_WINDOW = 20           # trading days ≈ the 4-week horizon
SIGMA_MIN_WINDOWS = 60
OPPORTUNITY_Z = 1.0
GIVE_UP_DAYS = 21           # a horizon still unpriceable this long after it matured → unavailable

_STOCK = "stock − SPY over {h} from the entry fill the run would have had, unsigned: what " \
         "the stock did whatever the verdict"
SCORES: Dict[str, ScoreSpec] = {
    "stock_excess_2w": ScoreSpec("NUMERIC", _STOCK.format(h="2 weeks")),
    "stock_excess_4w": ScoreSpec("NUMERIC", _STOCK.format(h="4 weeks")),
    "stock_excess_12w": ScoreSpec("NUMERIC", _STOCK.format(h="12 weeks")),
    "excess_z_4w": ScoreSpec("NUMERIC", "stock_excess_4w in units of the ticker's own 4-week "
                                        "excess volatility over the year before as_of"),
    "opportunity_4w": ScoreSpec("CATEGORICAL", f"|excess_z_4w| ≥ {OPPORTUNITY_Z}: there was a "
                                               "trade to take, and on which side",
                                categories=(("long", 1.0), ("none", 0.0), ("short", -1.0))),
}

_SQL = """SELECT agent_version, ticker, as_of, verdict, traded, pit_excluded, run_timestamp
          FROM longterm_agent_results ORDER BY as_of, ticker"""


def _entry(cur, ticker, as_of, decision):
    """(convention, stock_entry, bench_entry) exactly as grade_results resolves them; the two
    entries are None for the daily convention (t1_return derives them). None = pending."""
    re_ = resolve_entry(cur, ticker, as_of, decision)
    if re_ is None:
        return None
    conv, e_date, e_px = re_
    if conv != FILL_CONVENTION_MID:
        return conv, e_date, None, None
    bre = resolve_entry(cur, BENCHMARK, as_of, decision)
    if bre is None or bre[0] != FILL_CONVENTION_MID:
        return None  # both legs must share a convention (grade_results: mid_pending_bench)
    return conv, e_date, (e_date, e_px), (bre[1], bre[2])


def sigma_4w(series: List, bench: List, as_of) -> Optional[float]:
    """Std of overlapping 20-session stock − SPY returns over the year before as_of."""
    start = as_of - timedelta(days=SIGMA_LOOKBACK_DAYS)
    b = {d: c for d, c in bench if start <= d < as_of}
    pts = [(d, c, b[d]) for d, c in series if d in b]
    ex = [(c / c0 - 1) - (s / s0 - 1)
          for (_, c0, s0), (_, c, s) in zip(pts, pts[SIGMA_WINDOW:])]
    if len(ex) < SIGMA_MIN_WINDOWS:
        return None
    sd = pstdev(ex)
    return sd if sd > 0 else None


def opportunity(z: float) -> str:
    return "long" if z >= OPPORTUNITY_Z else "short" if z <= -OPPORTUNITY_Z else "none"


def collect(cur, ctx) -> Iterable[Label]:
    done = existing_keys(cur, NAME, VERSION)
    cur.execute(_SQL)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        key = tracing.run_trace_key(RUN_KIND, r["agent_version"], r["ticker"], r["as_of"])
        todo = [h for h in HORIZONS if (key, RUN_NODE, "", f"stock_excess_{h}") not in done]
        if not todo or r["as_of"] + timedelta(days=HORIZONS[todo[0]]) >= ctx.today:
            continue  # all written, or not even the shortest missing horizon has matured
        yield from _run_labels(ctx.read_cur, ctx, r, key, todo)


def _run_labels(cur, ctx, r, key, todo) -> List[Label]:
    """`cur` is ctx.read_cur: the grader's price helpers may swallow a failed query."""
    ticker, as_of = r["ticker"].upper(), r["as_of"]
    decision = _decision_moment(r.get("run_timestamp"))
    trace_time = decision or datetime.combine(as_of, time())
    ctx_json = {"verdict": r["verdict"], "traded": bool(r.get("traded")),
                "pit_excluded": r.get("pit_excluded")}
    entry = _entry(cur, ticker, as_of, decision)
    out: List[Label] = []

    def add(label_key, cutoff, horizon, num=None, cat=None, status="final", reason=None,
            raw=None):
        out.append(Label(trace_key=key, ticker=ticker, as_of=as_of, node=RUN_NODE,
                         label_key=label_key, labeler=NAME, labeler_version=VERSION,
                         trace_time=trace_time, data_cutoff=cutoff, horizon_days=horizon,
                         value_num=num, value_cat=cat, status=status, reason=reason,
                         value_json={**ctx_json, **(raw or {})}))

    for h in todo:
        days = HORIZONS[h]
        priced = None
        if entry is not None:
            conv, e_date, stock_entry, bench_entry = entry
            target = e_date + timedelta(days=days)
            if target <= ctx.today and \
                    _has_data_through(cur, ticker, target, ctx.max_dates) and \
                    _has_data_through(cur, BENCHMARK, target, ctx.max_dates):
                fwd = _finite_ret(t1_return(cur, ticker, as_of, days, entry=stock_entry))
                bench = _finite_ret(t1_return(cur, BENCHMARK, as_of, days, entry=bench_entry))
                if fwd is not None and bench is not None:
                    priced = (target, fwd, bench, conv, e_date)
        if priced is None:
            if as_of + timedelta(days=days + GIVE_UP_DAYS) < ctx.today:
                add(f"stock_excess_{h}", as_of + timedelta(days=days), days,
                    status="unavailable", reason="no entry fill or prices through the horizon")
            continue
        target, fwd, bench, conv, e_date = priced
        excess = round(fwd - bench, 4)
        raw = {"fwd": round(fwd, 4), "bench": round(bench, 4), "fill_convention": conv,
               "entry_date": str(e_date)}
        add(f"stock_excess_{h}", target, days, num=excess, raw=raw)
        if h != "4w":
            continue
        sd = sigma_4w(ctx.closes(ticker), ctx.closes(BENCHMARK), as_of)
        if sd is None:
            continue
        z = round(excess / sd, 3)
        raw = {**raw, "sigma_4w": round(sd, 4)}
        add("excess_z_4w", target, days, num=z, raw=raw)
        add("opportunity_4w", target, days, cat=opportunity(z), raw=raw)
    return out
