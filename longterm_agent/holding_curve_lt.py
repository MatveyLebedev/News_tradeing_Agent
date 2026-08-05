#!/usr/bin/env python3
"""Profit vs holding time for LONG-TERM agent decisions entered midday, in session.

The hypothesis: a position the long-term agent opens on a **Monday, mid-session**, one
minute after the decision (`--agent-minutes`, its assumed working time), has a mean profit
that depends on how long it is held — and the curve says where, if anywhere, that profit
is reliably above zero.

The design point that makes the curve readable, inherited from
`profit_compute/holding_curve.py`: **every horizon is evaluated on the same entry**. Only
the exit moves along the x axis, so a bend is drift rather than a change of sample — as
long as the sample is held fixed, which `--truncation` governs and which is the one knob
that can manufacture a shape out of nothing.

What is different here, and why this is a separate module from the intraday one:

  * the entry is a **minute bar** but the thesis is **weeks** long, so the exit grid has
    two legs (minutes on the entry day, then trading-day closes) glued onto one x axis of
    *session minutes held* — see `intraday_entry.py`;
  * the entries are **long-term agent decisions** (`backtest_runs`), not news headlines:
    one decision per (ticker, Monday), direction LONG/SHORT, risk-gated;
  * the block for every CI is the **cohort Monday**, not the day of a story.

Not a decision path. Every price here is a realized outcome, so under `docs/PIT_RULES.md`
§4 this file belongs to grading and may see the future by construction. The PIT rule that
binds is the entry: the fill bar must start STRICTLY after `as_of + agent_minutes`
(`intraday_entry.entry_grid_point`).

⚠ Do NOT grade a midday sweep with `backtest.py --fill d0`: that fills at the as_of day's
09:30 open, hours BEFORE a midday decision exists. Run the sweep with `--no-grade` and use
this module. The loader refuses a decision whose fill would precede its own timestamp.

Usage (see docs/PREREG_MONDAY_MIDDAY_HOLDING_2026-07-29.md for the registered reading):
    python3 -m longterm_agent.holding_curve_lt --backtest-id monday_mid1200 --benchmark ew
    python3 -m longterm_agent.holding_curve_lt --backtest-id monday_mid1200 --truncation balanced
"""
import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.db import get_conn  # noqa: E402
from longterm_agent.beta import estimate_beta  # noqa: E402
from longterm_agent.intraday_entry import (  # noqa: E402
    DEFAULT_AGENT_MINUTES,
    DEFAULT_DECISION_TIME,
    DEFAULT_EOD_DAYS,
    DEFAULT_INTRADAY_MIN,
    FILL_CONVENTION_MID,
    GRID_POINTS,
    SESSION_BARS,
    SESSION_OPEN,
    chained_return,
    decision_moment,
    entry_grid_point,
    eod_price,
    first_real_bar,
    held_session_minutes,
    parse_grid,
    trading_day_closes,
)
# The minute-grid loader and the equal-weight intraday index are reused rather than
# re-implemented: they are the audited pieces of the intraday experiment, and a second
# copy of "how do bars become a grid" is how two harnesses start disagreeing.
from profit_compute.holding_curve import load_grids, load_market_grid  # noqa: E402


# ── entry universe: long-term agent decisions ────────────────────────────────────────


def resolve_decision_time(conn, backtest_id: str, override: str | None) -> tuple[time, str]:
    """The wall-clock ET moment the sweep's cohorts were stamped at.

    `backtest_runs.as_of` is a DATE, so the time of day survives only in `provenance`
    (`cohort_time_et`, stamped by `backtest.py --at`). Reading it back is what stops this
    harness from silently assuming noon for a sweep that ran pre-open — the difference is
    a fill 2.5 hours earlier and a completely different curve.
    """
    if override:
        hh, mm = map(int, override.split(":"))
        return time(hh, mm), "cli"

    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT provenance->>'cohort_time_et'
                       FROM backtest_runs WHERE backtest_id=%s""", [backtest_id])
        stamps = {r[0] for r in cur.fetchall()}
    stamps.discard(None)
    if len(stamps) == 1:
        hh, mm = map(int, stamps.pop().split(":"))
        return time(hh, mm), "provenance"
    if len(stamps) > 1:
        raise SystemExit(f"backtest_id {backtest_id!r} mixes cohort times {sorted(stamps)} — "
                         "that is two experiments in one id; re-run them under separate ids")
    raise SystemExit(
        f"backtest_id {backtest_id!r} carries no `cohort_time_et` provenance. It was run "
        "before backtest.py stamped the cohort clock, so its decision time is unknown. "
        "Pass --decision-time HH:MM explicitly, and only if you can prove the sweep used it."
    )


def load_universe(conn, backtest_id: str) -> set[str]:
    """Every ticker the sweep EVALUATED — the benchmark universe, not the traded subset.

    This distinction is not cosmetic. The equal-weight index is subtracted from the trades
    inside it, so building it from the traded names alone makes an all-long, equal-weighted
    cohort its own benchmark and drives the adjusted mean to exactly zero by construction
    (measured: 0.0 ± 5e-15 on a 3-name check). Off the full universe a traded name carries
    ~1/N of the index, so the attenuation is real but bounded — state it, don't hide it.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ticker FROM backtest_runs WHERE backtest_id=%s",
                    [backtest_id])
        return {r[0].upper() for r in cur.fetchall()}


def load_decisions(conn, backtest_id: str, traded_only: bool, min_conviction: float,
                   directions=("LONG", "SHORT")) -> list[dict]:
    """One decision per (ticker, as_of). `backtest_runs` holds a row per horizon; the
    decision is identical across them, so DISTINCT ON collapses them back to the trade.

    `traded_only` selects the book the agent would actually have opened (directional AND
    through the risk gate). The ungated directional book is the pre-registered secondary.
    """
    sql = """
        SELECT DISTINCT ON (ticker, as_of)
               ticker, as_of, direction, conviction, traded, shift_flag, shift_score,
               agent_version, signal
        FROM backtest_runs
        WHERE backtest_id = %s AND direction = ANY(%s)
    """
    params: list = [backtest_id, list(directions)]
    if traded_only:
        sql += " AND traded"
    if min_conviction > 0:
        sql += " AND conviction >= %s"
        params.append(min_conviction)
    sql += " ORDER BY ticker, as_of, horizon_days"

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [
        {
            "ticker": (tk or "").upper(),
            "cohort": as_of,
            "direction": direction,
            "side": 1 if direction == "LONG" else -1,
            "conviction": float(conv) if conv is not None else float("nan"),
            "traded": bool(traded),
            "shift_flag": bool(shift_flag) if shift_flag is not None else None,
            "shift_score": float(ss) if ss is not None else float("nan"),
            "agent_version": ver,
        }
        for tk, as_of, direction, conv, traded, shift_flag, ss, ver, _sig in rows
    ]


# ── benchmark: equal-weight index, continuous across the two legs ────────────────────


def load_ew_daily(conn, universe: set[str], start: date, end: date) -> dict[date, float]:
    """Cross-sectional mean daily return of `universe` — the multi-day leg of the index.

    Built from the SAME names as the intraday leg so the benchmark does not change
    identity at 16:00. We hold no SPY/QQQ MINUTE table (verified: `historical_data_spy`
    does not exist), so an ETF benchmark cannot cover the entry day at all — which is why
    the equal-weight index is the primary adjustment and SPY appears only as the
    close-anchored cross-reference column.
    """
    with conn.cursor() as cur:
        cur.execute("""
            WITH px AS (
                SELECT ticker, date, close::float8 AS c
                FROM stock_prices_daily
                WHERE ticker = ANY(%s) AND date >= %s AND date <= %s AND close IS NOT NULL
            ), r AS (
                SELECT date,
                       c / NULLIF(lag(c) OVER (PARTITION BY ticker ORDER BY date), 0) - 1 AS ret
                FROM px
            )
            SELECT date, avg(ret) FROM r WHERE ret IS NOT NULL GROUP BY 1 ORDER BY 1
        """, [sorted(universe), start - timedelta(days=7), end])
        return {d: float(v) for d, v in cur.fetchall()}


def load_spy_closes(conn, start: date, end: date) -> dict[date, float]:
    with conn.cursor() as cur:
        cur.execute("""SELECT date, close::float8 FROM stock_prices_daily
                       WHERE ticker='SPY' AND date >= %s AND date <= %s AND close IS NOT NULL
                       ORDER BY date""", [start, end])
        return {d: float(c) for d, c in cur.fetchall()}


# ── the panel ────────────────────────────────────────────────────────────────────────


def build_panel(conn, decisions: list[dict], grid, decision_time: time, agent_minutes: float,
                at_or_after: bool, entry_tolerance: int, truncation: str, cost_bps: float,
                benchmark: str, bench_universe: set[str] | None,
                beta_adjust: bool = True) -> tuple[pd.DataFrame, Counter]:
    """One row per (trade, exit spec). `ret_pct` is signed by the trade's direction.

    The hedge is BETA-MATCHED by default (`beta_adjust`), the standing convention of
    `docs/BENCHMARK_CONVENTION.md`: the benchmark leg is scaled by the stock's own beta to
    SPY, estimated on daily bars strictly before the entry. A flat 1:1 short of SPY prices
    a defensive name as if it moved with the market one-for-one, and this book's median
    beta is 0.48 — the un-scaled hedge leaves ~0.4 beta units of unintended SHORT market
    exposure per trade. `--no-beta-adjust` restores the old beta=1 arithmetic."""
    dropped = Counter()
    if not decisions:
        return pd.DataFrame(), dropped

    by_ticker: dict[str, set[date]] = defaultdict(set)
    for d in decisions:
        by_ticker[d["ticker"]].add(d["cohort"])

    grids: dict[str, dict] = {}
    for ticker, days in by_ticker.items():
        try:
            grids[ticker] = load_grids(conn, ticker, days)
        except Exception as exc:  # a table can exist and still be unreadable
            print(f"  ! {ticker}: {exc}")
            grids[ticker] = {}

    all_days = sorted({d for days in by_ticker.values() for d in days})
    max_eod = max([n for leg, n in grid if leg == "eod"], default=0)
    span_end = all_days[-1] + timedelta(days=int(max_eod * 7 / 5) + 21)

    universe = bench_universe or {d["ticker"] for d in decisions}
    if benchmark == "ew" and len(universe) < 10:
        print(f"  ⚠ benchmark universe is only {len(universe)} names — the traded book is a "
              "large share of its own benchmark, which pulls the adjusted mean toward zero.")
    mkt_minute: dict[date, np.ndarray] = {}
    ew_daily: dict[date, float] = {}
    spy = load_spy_closes(conn, all_days[0], span_end)
    if benchmark in ("ew", "spy"):
        # The entry-day stub (fill → 16:00) is equal-weight in BOTH modes: no ETF minute
        # table covers this window, so nothing else can price those 238 minutes. `spy`
        # differs only from the entry day's close onward, where a real ETF exists.
        print(f"Building equal-weight benchmark over {len(universe)} tickers "
              f"× {len(all_days)} cohort days…")
        mkt_minute = load_market_grid(conn, universe, set(all_days))
        if benchmark == "ew":
            ew_daily = load_ew_daily(conn, universe, all_days[0], span_end)
        elif not spy:
            raise SystemExit("--benchmark spy: no SPY rows in stock_prices_daily for this window")

    intraday_specs = [s for s in grid if s[0] == "intraday"]
    eod_specs = [s for s in grid if s[0] == "eod"]
    max_intraday = max([n for _, n in intraday_specs], default=0)

    rows, seams = [], []
    beta_cache: dict[tuple[str, date], float | None] = {}
    beta_fallbacks = 0
    cur = conn.cursor()
    for trade_id, dec in enumerate(decisions):
        day, ticker = dec["cohort"], dec["ticker"]
        grid_day = grids.get(ticker, {}).get(day)
        if grid_day is None:
            # No minute bars on a cohort Monday = market holiday (2026-05-25 in this
            # window) or a data hole. Either way there is no midday entry to make.
            dropped["no_session_that_day"] += 1
            continue
        px, real = grid_day

        decision = decision_moment(day, decision_time, agent_minutes)
        want = entry_grid_point(decision, at_or_after=at_or_after)
        entry_pt = first_real_bar(real, want, entry_tolerance)
        if entry_pt is None:
            dropped["no_entry_bar" if want < SESSION_BARS else "decision_after_close"] += 1
            continue
        entry_px = float(px[entry_pt])
        if not np.isfinite(entry_px) or entry_px <= 0:
            dropped["bad_entry_price"] += 1
            continue
        entry_time = datetime.combine(day, SESSION_OPEN) + timedelta(minutes=entry_pt)
        if entry_time < decision:  # belt-and-braces: the fill can never precede the decision
            dropped["fill_before_decision"] += 1
            continue

        # Beta is a property of the stock at decision time: `estimate_beta` reads daily
        # bars STRICTLY before the cohort date, so it never peeks. Cached per (ticker,
        # cohort) — it is identical for all 25 exit specs of one trade.
        beta = 1.0
        if beta_adjust:
            key = (ticker, day)
            if key not in beta_cache:
                beta_cache[key] = estimate_beta(cur, ticker, day, "SPY")
            est = beta_cache[key]
            if est is None:
                # Too short a price history to fit one. Falling back to 1.0 keeps the trade
                # in the sample under the OLD convention rather than silently changing the
                # denominator; the count is printed so the mix is never invisible.
                beta_fallbacks += 1
            else:
                beta = est

        closes = trading_day_closes(cur, ticker, day, max_eod) if eod_specs else []
        room_intraday = GRID_POINTS - 1 - entry_pt
        if truncation == "balanced" and (room_intraday < max_intraday
                                         or len(closes) < max_eod + 1):
            dropped["balanced_no_room"] += 1
            continue

        # Seam check: the entry day's 16:00 minute print vs its official daily close. They
        # measure the same instant from two sources, so a wide gap means one of the two
        # feeds is wrong and the curve's two legs are not on the same price scale.
        if closes and np.isfinite(px[SESSION_BARS]) and closes[0][0] == day and closes[0][1]:
            seams.append(abs(px[SESSION_BARS] / closes[0][1] - 1.0) * 100.0)

        mkt = mkt_minute.get(day)
        day0_bench_to_close = None
        if mkt is not None and mkt[entry_pt] > 0 and np.isfinite(mkt[SESSION_BARS]):
            day0_bench_to_close = mkt[SESSION_BARS] / mkt[entry_pt] - 1.0

        for spec in grid:
            leg, n = spec
            censored = False

            if leg == "intraday":
                exit_pt = entry_pt + n
                if exit_pt > GRID_POINTS - 1:
                    if truncation == "drop":
                        continue
                    exit_pt = GRID_POINTS - 1  # clamp / balanced: exit at the 16:00 print
                    censored = True
                exit_px = float(px[exit_pt])
                exit_when = datetime.combine(day, SESSION_OPEN) + timedelta(minutes=exit_pt)
                held = held_session_minutes(entry_pt, ("intraday", exit_pt - entry_pt))
                bench = None
                if mkt is not None and mkt[entry_pt] > 0 and np.isfinite(mkt[exit_pt]):
                    bench = mkt[exit_pt] / mkt[entry_pt] - 1.0
                spy_excess = np.nan
            else:
                hit = eod_price(closes, n)
                if hit is None:
                    if truncation == "drop" or not closes:
                        continue
                    hit = closes[-1]  # clamp: the last close the series actually reaches
                    censored = True
                exit_day, exit_px = hit
                exit_when = datetime.combine(exit_day, time(16, 0))
                held = held_session_minutes(entry_pt, ("eod", closes.index(hit)))
                bench = None
                if day0_bench_to_close is not None:
                    span = closes[:closes.index(hit) + 1]
                    if benchmark == "spy":
                        # Real ETF from the entry day's close onward — the same benchmark
                        # every daily book in the ledger is graded against.
                        legs, prev = [], spy.get(span[0][0])
                        for d, _ in span[1:]:
                            cur_px = spy.get(d)
                            legs.append((cur_px / prev - 1.0) if (cur_px and prev) else 0.0)
                            prev = cur_px or prev
                    else:
                        legs = [ew_daily.get(d, 0.0) for d, _ in span[1:]]
                    bench = chained_return([day0_bench_to_close] + legs)
                # Close-anchored SPY excess: the bridge to the ledger's daily books. It
                # DISCARDS the entry day's afternoon (both sides start at Monday's close),
                # so it answers a different question than the curve — kept as a column,
                # never as the primary reading.
                s0, s1 = spy.get(closes[0][0]), spy.get(exit_day)
                spy_excess = np.nan
                if s0 and s1 and closes[0][1]:
                    spy_excess = dec["side"] * (
                        (exit_px / closes[0][1] - 1.0) - (s1 / s0 - 1.0)) * 100.0

            if not np.isfinite(exit_px) or exit_px <= 0:
                continue

            raw = (exit_px - entry_px) / entry_px
            # Beta scales the SPY leg only. The intraday leg is hedged against the
            # equal-weight basket of the sweep's own universe (no ETF minute bars exist),
            # and a beta measured against SPY does not describe that hedge — so inside the
            # session the ratio stays 1:1, which is also the conservative choice there.
            beta_used = beta if leg == "eod" else 1.0
            adj = raw - beta_used * bench if bench is not None else raw
            ret = dec["side"] * adj * 100.0 - cost_bps / 100.0

            rows.append({
                "trade_id": trade_id,
                "cohort": day,
                "ticker": ticker,
                "direction": dec["direction"],
                "side": dec["side"],
                "conviction": dec["conviction"],
                "traded": dec["traded"],
                "shift_flag": dec["shift_flag"],
                "agent_version": dec["agent_version"],
                "entry_time": entry_time,
                "entry_price": entry_px,
                "leg": leg,
                "spec": n,
                "held_min": held,
                "held_days": round(held / SESSION_BARS, 3),
                "censored": censored,
                "exit_time": exit_when,
                "exit_price": exit_px,
                # Per row, because it genuinely differs: an intraday exit can only ever be
                # equal-weight adjusted, whatever mode the run asked for.
                "benchmark": ("none" if benchmark == "none"
                              else ("ew" if leg == "intraday" else benchmark)),
                "beta": beta,
                "beta_used": beta_used,
                "ret_pct": ret,
                # Direction-blind control, adjusted the same way the signal is: if the
                # signed curve does not clear it, the agent is selling you market drift.
                "always_long_pct": adj * 100.0 - cost_bps / 100.0,
                "raw_move_pct": raw * 100.0,
                "excess_spy_from_close": spy_excess,
            })
    cur.close()

    panel = pd.DataFrame(rows)
    if beta_adjust and not panel.empty:
        per_trade = panel.drop_duplicates("trade_id")["beta"]
        print(f"hedge: beta-matched vs SPY on the eod leg · median beta {per_trade.median():.2f} "
              f"(IQR {per_trade.quantile(.25):.2f}–{per_trade.quantile(.75):.2f})"
              + (f" · {beta_fallbacks} trade(s) fell back to 1.0" if beta_fallbacks else ""))
        if per_trade.median() < 0.8 or per_trade.median() > 1.25:
            print(f"  ⚠ the book is far from beta 1 — a 1:1 hedge would have carried "
                  f"{1 - per_trade.median():+.2f} beta units of unintended market exposure.")
    elif not beta_adjust:
        print("hedge: 1:1 vs benchmark (beta forced to 1.0) — NOT the standing convention")
    if seams:
        worst = max(seams)
        print(f"seam check (16:00 minute print vs official close): median "
              f"{np.median(seams):.3f}% · max {worst:.3f}%"
              + ("   ⚠ the two legs are not on the same price scale" if worst > 0.5 else ""))
    return panel, dropped


# ── statistics ───────────────────────────────────────────────────────────────────────


def _cohort_bootstrap(g: pd.DataFrame, col: str, n_boot: int,
                      rng: np.random.Generator) -> tuple[float, float]:
    """CI on the mean, resampling whole COHORT MONDAYS.

    Every trade in a cohort shares one market week and one news background; ledger F10
    measured cohort-block CIs at ~5× the per-trade width. With this book's concentration
    (`monday_t1_pit`: 29 of 41 trades on two Mondays) a per-trade CI would be fiction.
    """
    groups = [x[col].to_numpy() for _, x in g.groupby("cohort")]
    if len(groups) < 2:
        return float("nan"), float("nan")
    means = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        means[b] = np.concatenate([groups[i] for i in pick]).mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _sign_flip_p(g: pd.DataFrame, n_perm: int, rng: np.random.Generator) -> float:
    """Two-sided p from flipping direction, one coin per COHORT — the null keeps the
    data's clumpiness and tests the claim actually made: that the DIRECTION carries
    information, not that the tape moved."""
    groups = [x["ret_pct"].to_numpy() for _, x in g.groupby("cohort")]
    if len(groups) < 2:
        return float("nan")
    observed = abs(np.concatenate(groups).mean())
    hits = 0
    for _ in range(n_perm):
        signs = rng.choice([-1.0, 1.0], len(groups))
        if abs(np.concatenate([a * s for a, s in zip(groups, signs)]).mean()) >= observed:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def summarise(panel: pd.DataFrame, n_boot: int, n_perm: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    out = []
    for (leg, spec), g in panel.groupby(["leg", "spec"]):
        rets = g["ret_pct"].to_numpy()
        lo, hi = _cohort_bootstrap(g, "ret_pct", n_boot, rng)
        out.append({
            "leg": leg,
            "spec": spec,
            "label": f"{spec}m" if leg == "intraday" else f"{spec}d",
            "held_min": float(g["held_min"].median()),
            "held_days": round(float(g["held_min"].median()) / SESSION_BARS, 2),
            "n": len(g),
            "n_cohorts": g["cohort"].nunique(),
            "n_censored": int(g["censored"].sum()),
            "mean_pct": rets.mean(),
            "median_pct": float(np.median(rets)),
            "win_rate": float((rets > 0).mean()),
            "std_pct": rets.std(ddof=1) if len(rets) > 1 else float("nan"),
            "ci_lo": lo,
            "ci_hi": hi,
            "p_signflip": _sign_flip_p(g, n_perm, rng),
            "always_long_mean_pct": g["always_long_pct"].mean(),
            "spy_excess_from_close_pct": g["excess_spy_from_close"].mean(),
        })
    return pd.DataFrame(out).sort_values("held_min").reset_index(drop=True)


def variance_split(panel: pd.DataFrame, leg: str, spec: int) -> tuple[float, float]:
    """(between-cohort, within-cohort) variance of the signed return at one horizon.

    Printed because it explains the CI width instead of leaving it mysterious: when almost
    all the variance is BETWEEN cohorts, more trades per Monday buy very little and only
    more Mondays help. When it is mostly WITHIN, a wider ticker universe is the lever.
    """
    g = panel[(panel["leg"] == leg) & (panel["spec"] == spec)]
    if g["cohort"].nunique() < 2:
        return float("nan"), float("nan")
    means = g.groupby("cohort")["ret_pct"].mean()
    within = g.groupby("cohort")["ret_pct"].var(ddof=1).mean()
    return float(means.var(ddof=1)), float(within if pd.notna(within) else 0.0)


def paired_shape(panel: pd.DataFrame, ref: tuple[str, int], n_boot: int,
                 seed: int) -> pd.DataFrame:
    """Each horizon MINUS the reference horizon, **on the same trade**.

    This is the properly powered form of the question the experiment actually asks. The
    level question ("is mean profit above zero at 4w") is fought against the full
    cohort-level shock — one bad Monday moves it. The SHAPE question ("does holding longer
    pay more than holding shorter") is a within-trade difference, so the market move both
    exits share cancels before any averaging happens. Same data, much narrower interval;
    nothing is assumed that the level test does not already assume.

    Only trades present at BOTH horizons are compared, so the pairing is never broken by
    a trade that dropped out at the longer horizon.
    """
    rng = np.random.default_rng(seed)
    ref_rows = panel[(panel["leg"] == ref[0]) & (panel["spec"] == ref[1])]
    if ref_rows.empty:
        return pd.DataFrame()
    ref_ret = ref_rows.set_index("trade_id")["ret_pct"]

    out = []
    for (leg, spec), g in panel.groupby(["leg", "spec"]):
        g = g.set_index("trade_id")
        common = g.index.intersection(ref_ret.index)
        if len(common) < 2:
            continue
        diff = pd.DataFrame({
            "cohort": g.loc[common, "cohort"].to_numpy(),
            "ret_pct": g.loc[common, "ret_pct"].to_numpy() - ref_ret.loc[common].to_numpy(),
        })
        lo, hi = _cohort_bootstrap(diff, "ret_pct", n_boot, rng)
        d = diff["ret_pct"].to_numpy()
        out.append({
            "leg": leg,
            "spec": spec,
            "label": f"{spec}m" if leg == "intraday" else f"{spec}d",
            "held_min": float(g.loc[common, "held_min"].median()),
            "n_paired": len(common),
            "n_cohorts": diff["cohort"].nunique(),
            "mean_diff_pct": float(d.mean()),
            "ci_lo": lo,
            "ci_hi": hi,
            "beats_ref_rate": float((d > 0).mean()),
        })
    return pd.DataFrame(out).sort_values("held_min").reset_index(drop=True)


def parse_ref(text: str) -> tuple[str, int]:
    """`eod:20` / `intraday:30` → the reference horizon for the paired test."""
    leg, _, spec = text.partition(":")
    if leg not in ("intraday", "eod") or not spec.strip():
        raise ValueError(f"reference must be intraday:<min> or eod:<days>, got {text!r}")
    return leg, int(spec)


# ── output ───────────────────────────────────────────────────────────────────────────


def plot_curve(summary: pd.DataFrame, path: str, subtitle: str, xscale: str = "log") -> None:
    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(12, 9), sharex=True, gridspec_kw={"height_ratios": [3, 1.4]})
    x = summary["held_min"]

    ax.fill_between(x, summary["ci_lo"], summary["ci_hi"], alpha=0.20, color="#2E86AB",
                    label="95% CI (cohort-block bootstrap)")
    ax.plot(x, summary["mean_pct"], "o-", color="#2E86AB", lw=2, label="mean profit, signed")
    ax.plot(x, summary["median_pct"], "s--", color="#6C757D", lw=1.2, ms=4, label="median")
    ax.plot(x, summary["always_long_mean_pct"], ":", color="#C77DFF", lw=1.5,
            label="always-long control (direction ignored)")
    ax.axhline(0, color="black", lw=1)
    # Where the entry day ends: everything to the right of it is an overnight-carrying
    # hold, and the two legs of the grid are measured from different price feeds.
    seam = summary.loc[summary["leg"] == "eod", "held_min"].min()
    if pd.notna(seam):
        ax.axvline(seam, color="#B00020", ls="--", lw=1, alpha=0.6)
        ax.text(seam, ax.get_ylim()[1], " entry-day close ", color="#B00020",
                fontsize=8, va="top", ha="left")
    ax.set_ylabel("Profit %")
    ax.set_title(f"Long-term agent — profit vs holding time\n{subtitle}", fontsize=11)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    ax2.step(x, summary["n"], where="mid", color="#6C757D", lw=1.5, label="trades at horizon")
    ax2.fill_between(x, 0, summary["n"], step="mid", color="#ADB5BD", alpha=0.45)
    ax2.set_ylim(0, max(summary["n"].max() * 1.15, 1))
    ax2.set_ylabel("n trades")
    ax2.set_xlabel("Holding time — SESSION minutes held (overnight counts zero)")
    ax2.grid(alpha=0.3)
    axw = ax2.twinx()
    axw.plot(x, summary["win_rate"] * 100, "^-", color="#28A745", ms=4, lw=1.2, label="win rate")
    axw.axhline(50, color="#28A745", ls=":", lw=1)
    axw.set_ylabel("win rate %", color="#28A745")
    axw.set_ylim(0, 100)

    ax2.set_xscale(xscale)
    if xscale == "log":
        ticks = [1, 5, 15, 30, 60, 120, 240, 390, 1950, 3900, 7800, 15600, 23400]
        labels = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d",
                  "5d", "10d", "20d", "40d", "60d"]
    else:
        # Linear puts every intraday point inside the first ~1% of the axis; label in days
        # only, since a "15m" tick there would sit on top of the "1m" one.
        ticks = [0, 390, 1950, 3900, 7800, 11700, 15600, 19500, 23400]
        labels = ["0", "1d", "5d", "10d", "20d", "30d", "40d", "50d", "60d"]
    keep = [(t, s) for t, s in zip(ticks, labels) if x.min() * 0.8 <= t <= x.max() * 1.25]
    ax2.set_xticks([t for t, _ in keep])
    ax2.set_xticklabels([s for _, s in keep])
    ax2.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())

    lines = ax2.get_legend_handles_labels()[0] + axw.get_legend_handles_labels()[0]
    names = ax2.get_legend_handles_labels()[1] + axw.get_legend_handles_labels()[1]
    ax2.legend(lines, names, fontsize=8, loc="upper left")

    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches="tight")
    print(f"chart  → {path}")
    plt.close(fig)


def print_composition(panel: pd.DataFrame, dropped: Counter, n_decisions: int) -> None:
    """The part of the output that decides whether the curve means anything."""
    print("\n" + "=" * 72)
    print("SAMPLE COMPOSITION")
    print("=" * 72)
    print(f"decisions loaded: {n_decisions}")
    if dropped:
        print("dropped:")
        for reason, n in dropped.most_common():
            print(f"  {reason:26} {n}")
    if panel.empty:
        print("no trades survived")
        return

    trades = panel.drop_duplicates("trade_id")
    print(f"\ntrades {len(trades)} · cohorts {trades['cohort'].nunique()} "
          f"· tickers {trades['ticker'].nunique()}")
    print(f"window {trades['cohort'].min()} → {trades['cohort'].max()}")
    print(f"long/short {int((trades['side'] == 1).sum())}/{int((trades['side'] == -1).sum())}")
    print(f"entry times: {trades['entry_time'].min().time()} … {trades['entry_time'].max().time()}")

    per_cohort = trades.groupby("cohort").size().sort_values(ascending=False)
    top, top_n = per_cohort.index[0], per_cohort.iloc[0]
    share = top_n / len(trades)
    print(f"busiest cohort {top}: {top_n} trades ({share:.0%} of the sample)")
    if share > 0.25:
        print(f"  ⚠ one Monday carries {share:.0%} of the book — the curve is largely that week.")
    print("  trades per cohort: " + ", ".join(f"{d}×{n}" for d, n in per_cohort.items()))
    if trades["cohort"].nunique() < 20:
        print(f"  ⚠ {trades['cohort'].nunique()} cohorts — the cohort-block CI is the only "
              "honest one, and it will be wide.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--backtest-id", required=True, help="the sweep whose decisions to trade")
    ap.add_argument("--decision-time", default=None, metavar="HH:MM",
                    help="cohort wall-clock ET; default: read from the sweep's provenance")
    ap.add_argument("--agent-minutes", type=float, default=DEFAULT_AGENT_MINUTES,
                    help="assumed agent working time between as_of and the decision")
    ap.add_argument("--at-or-after", action="store_true",
                    help="allow the fill bar to start AT the decision instant (looser "
                         "convention; default is strictly after)")
    ap.add_argument("--intraday-horizons", default=",".join(map(str, DEFAULT_INTRADAY_MIN)),
                    help="comma-separated minutes, exits on the entry day")
    ap.add_argument("--eod-horizons", default=",".join(map(str, DEFAULT_EOD_DAYS)),
                    help="comma-separated TRADING days, exits at the close (0 = entry day)")
    ap.add_argument("--truncation", choices=("drop", "clamp", "balanced"), default="drop",
                    help="drop: the trade leaves the sample when its exit does not exist "
                         "(n falls with h); clamp: exit at the last price available "
                         "(constant n, tail flattened by construction); balanced: only "
                         "trades with room for the longest horizon (n collapses)")
    ap.add_argument("--entry-tolerance", type=int, default=5,
                    help="minutes to search FORWARD for the first real bar at entry")
    ap.add_argument("--benchmark", choices=("none", "ew", "spy"), default="spy",
                    help="spy (default): equal-weight for the entry-day stub, then SPY "
                         "daily closes — the ledger's own benchmark for the part that "
                         "carries the variance. ew: equal-weight throughout. none: raw "
                         "signed profit. No ETF minute table covers this window, so the "
                         "fill→16:00 stub is equal-weight in either adjusted mode.")
    ap.add_argument("--bench-universe", default="",
                    help="comma-separated benchmark tickers; default = every ticker the "
                         "sweep evaluated (never just the traded ones — see load_universe)")
    ap.add_argument("--cost-bps", type=float, default=0.0, help="round-trip cost in bps")
    ap.add_argument("--all-directional", action="store_true",
                    help="include signals the risk gate rejected (secondary reading)")
    ap.add_argument("--min-conviction", type=float, default=0.0)
    ap.add_argument("--exclude-cohorts", default="",
                    help="comma-separated YYYY-MM-DD Mondays to drop (robustness)")
    ap.add_argument("--paired-reference", default="eod:20", metavar="LEG:N",
                    help="reference horizon for the paired shape test (intraday:<min> or "
                         "eod:<trading days>); default is the pre-registered eod:20")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--permutations", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--no-beta-adjust", dest="beta_adjust", action="store_false",
                    help="hedge 1:1 instead of beta-matched. Reproduces results produced "
                         "before docs/BENCHMARK_CONVENTION.md; not the standing convention")
    ap.set_defaults(beta_adjust=True)
    ap.add_argument("--xscale", choices=("log", "linear"), default="log",
                    help="x-axis (session minutes held). log spreads the intraday leg out; "
                         "linear shows true elapsed proportion, which squashes all of the "
                         "intraday points against the origin")
    ap.add_argument("--tag", default="", help="suffix for the output filenames")
    args = ap.parse_args()

    grid = parse_grid(args.intraday_horizons, args.eod_horizons)
    out_dir = os.path.dirname(os.path.abspath(__file__))
    stem = f"holding_curve_lt_{args.backtest_id}{('_' + args.tag) if args.tag else ''}"

    with get_conn() as conn:
        decision_time, src = resolve_decision_time(conn, args.backtest_id, args.decision_time)
        decision = decision_moment(date(2026, 1, 5), decision_time, args.agent_minutes)
        fill_pt = entry_grid_point(decision, at_or_after=args.at_or_after)
        print(f"backtest_id={args.backtest_id} · decision {decision_time.strftime('%H:%M')} ET "
              f"({src}) + {args.agent_minutes}m agent → fill at bar "
              f"{(datetime.combine(date(2026, 1, 5), SESSION_OPEN) + timedelta(minutes=fill_pt)).time()}"
              f" ({FILL_CONVENTION_MID}) · truncation={args.truncation} "
              f"· benchmark={args.benchmark}"
              f"{' (beta-matched)' if args.beta_adjust else ' (1:1, beta=1)'}"
              f" · cost={args.cost_bps}bps")

        decisions = load_decisions(conn, args.backtest_id, not args.all_directional,
                                   args.min_conviction)
        print(f"decisions: {len(decisions)}")
        if not decisions:
            print("nothing to trade — has the sweep been run?")
            return 1

        # Default to every ticker the sweep evaluated, NOT the traded ones: a book that is
        # its own benchmark has no excess by construction (see `load_universe`).
        bench_universe = ({t.strip().upper() for t in args.bench_universe.split(",") if t.strip()}
                          or load_universe(conn, args.backtest_id))
        panel, dropped = build_panel(
            conn, decisions, grid, decision_time, args.agent_minutes, args.at_or_after,
            args.entry_tolerance, args.truncation, args.cost_bps, args.benchmark,
            bench_universe, args.beta_adjust)

    if args.exclude_cohorts and not panel.empty:
        drop = {datetime.strptime(d.strip(), "%Y-%m-%d").date()
                for d in args.exclude_cohorts.split(",") if d.strip()}
        before = panel["trade_id"].nunique()
        panel = panel[~panel["cohort"].isin(drop)]
        print(f"excluded {sorted(drop)}: {before} → {panel['trade_id'].nunique()} trades")

    print_composition(panel, dropped, len(decisions))
    if panel.empty:
        return 1

    summary = summarise(panel, args.bootstrap, args.permutations, args.seed)
    print("\n" + "=" * 72)
    print("PROFIT vs HOLDING TIME")
    print("=" * 72)
    print(summary.to_string(index=False, float_format=lambda v: f"{v:8.3f}"))

    best = summary.loc[summary["mean_pct"].idxmax()]
    print(f"\nbest mean at {best['label']} ({best['held_min']:.0f} session minutes): "
          f"{best['mean_pct']:+.3f}% (n={int(best['n'])}, "
          f"CI [{best['ci_lo']:+.2f}, {best['ci_hi']:+.2f}], p_signflip={best['p_signflip']:.3f})")
    print(f"  ⚠ best-of-{len(summary)} over the horizon grid is a SELECTED maximum, not a "
          "discovery — read the pre-registered horizon and treat this one as a hypothesis "
          "for a fresh window.")

    ref = parse_ref(args.paired_reference)
    paired = paired_shape(panel, ref, args.bootstrap, args.seed)
    if not paired.empty:
        print("\n" + "=" * 72)
        print(f"PAIRED SHAPE — each horizon minus {ref[0]}:{ref[1]}, same trade")
        print("=" * 72)
        print("the level test fights the whole cohort shock; this one cancels it before "
              "averaging,\nso it is the better-powered form of 'is there a best holding time'.")
        print(paired.to_string(index=False, float_format=lambda v: f"{v:8.3f}"))

    between, within = variance_split(panel, *ref)
    if np.isfinite(between):
        share = between / (between + within) if (between + within) > 0 else float("nan")
        print(f"\nvariance at {ref[0]}:{ref[1]} — between cohorts {between:.3f} · "
              f"within {within:.3f} · between share {share:.0%}")
        print("  " + ("most variance is BETWEEN Mondays: only more cohorts narrow the CI, "
                      "a wider ticker universe barely helps."
                      if share > 0.5 else
                      "most variance is WITHIN a Monday: a wider ticker universe narrows "
                      "the CI without needing more weeks."))

    panel_path = os.path.join(out_dir, f"{stem}_trades.csv")
    summary_path = os.path.join(out_dir, f"{stem}_summary.csv")
    panel.to_csv(panel_path, index=False)
    summary.to_csv(summary_path, index=False)
    print(f"\npanel  → {panel_path}\nsummary→ {summary_path}")
    if not paired.empty:
        paired_path = os.path.join(out_dir, f"{stem}_paired.csv")
        paired.to_csv(paired_path, index=False)
        print(f"paired → {paired_path}")

    trades = panel.drop_duplicates("trade_id")
    subtitle = (f"{args.backtest_id} · entry {decision_time.strftime('%H:%M')}+"
                f"{args.agent_minutes:g}m ET ({FILL_CONVENTION_MID}) · "
                f"n={len(trades)} trades / {trades['cohort'].nunique()} Monday cohorts · "
                f"truncation={args.truncation}"
                + {"ew": " · EW-adjusted", "none": " · raw, unhedged",
                   "spy": (" · vs SPY, beta-matched" if args.beta_adjust
                           else " · vs SPY, 1:1 hedge")}[args.benchmark]
                + (f" · {args.cost_bps}bps round-trip" if args.cost_bps else ""))
    plot_curve(summary, os.path.join(out_dir, f"{stem}.png"), subtitle, args.xscale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
