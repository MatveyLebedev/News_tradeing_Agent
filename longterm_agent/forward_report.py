"""
Forward paper-testing report (Backtest Plan B4).

Given thin history, the statistically honest validation is *forward* accumulation: Phase 6
already writes every weekly signal to `longterm_agent_results` and `grade_results` fills the 4w
outcome as horizons mature. This formalizes that into a report over the LIVE graded trades —
reusing the B1 metrics (`backtest_metrics`) so the forward and backtest views speak the same
language — and adds the two pieces forward-testing needs:

  * an EQUITY CURVE / cumulative excess (is the edge compounding, and through what drawdown?);
  * a COHORT-MATURITY tracker (how many trades have matured, how many are pending, and when the
    next outcomes land — so "we don't have the numbers yet" is explicit, not hidden).

Reads only `longterm_agent_results` (the live table). Run:  python -m longterm_agent.forward_report
"""
from datetime import date, timedelta

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.stats import _conn, _version, trade_excess
from longterm_agent import backtest_metrics as M

HORIZON_DAYS = 28  # matches grade_results (4w vs SPY)
MIN_N = 30         # below this the report is an estimate, not a verdict


def _graded_trades(days: int = 3650):
    """Actionable trades whose 4w horizon has matured, with per-trade strategy excess vs SPY."""
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, as_of, verdict, conviction,
                   (background_shift->>'has_shifted')::boolean,
                   fwd_return_4w, benchmark_return_4w
            FROM longterm_agent_results
            WHERE agent_version=%s AND traded=TRUE AND verdict IN ('LONG','SHORT')
              AND outcome_filled_at IS NOT NULL AND fwd_return_4w IS NOT NULL
              AND as_of >= (CURRENT_DATE - %s::int)
            ORDER BY as_of, ticker
        """, [_version(), days])
        out = []
        for tk, ao, v, conv, shift, f4, b4 in cur.fetchall():
            ex = trade_excess(v, f4, b4)
            if ex is not None:
                out.append({"ticker": tk, "as_of": ao, "verdict": v, "conviction": conv,
                            "shift_flag": shift, "excess": ex})
    conn.close()
    return out


def forward_metrics(days: int = 3650) -> dict:
    """Equity curve + B1 metrics over the live graded trades."""
    trades = _graded_trades(days)
    ex = [t["excess"] for t in trades]
    cohorts = sorted({t["as_of"] for t in trades})
    series = [M.distribution([t["excess"] for t in trades if t["as_of"] == c])["mean"]
              for c in cohorts]  # per-cohort mean excess
    equity = M.cumulative(series)
    return {
        "n_graded": len(trades),
        "n_cohorts": len(cohorts),
        "win_rate": M.win_rate(ex),
        "win_ci": M.bootstrap_ci(ex, M.win_rate),
        "mean_excess": M.distribution(ex)["mean"],
        "mean_ci": M.bootstrap_ci(ex, lambda s: M.distribution(s)["mean"]),
        "distribution": M.distribution(ex),
        "sharpe": M.sharpe(series),
        "max_drawdown": M.max_drawdown(series),
        "cumulative_excess": (equity[-1] if equity else None),
        "equity_curve": list(zip([str(c) for c in cohorts], equity)),
        "calibration": M.calibration_curve(trades),
        "by_direction": {d: M.distribution([t["excess"] for t in trades if t["verdict"] == d])
                         for d in ("LONG", "SHORT")},
        "by_shift": {k: M.distribution([t["excess"] for t in trades if t["shift_flag"] == b])
                     for k, b in (("shift", True), ("no_shift", False))},
    }


def cohort_maturity(days: int = 3650, horizon_days: int = HORIZON_DAYS, today: date = None) -> dict:
    """How many actionable trades have matured vs are still pending, and when the next ones land."""
    today = today or date.today()
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT as_of, outcome_filled_at FROM longterm_agent_results
            WHERE agent_version=%s AND traded=TRUE AND verdict IN ('LONG','SHORT')
              AND as_of >= (CURRENT_DATE - %s::int)
        """, [_version(), days])
        rows = cur.fetchall()
    conn.close()
    matured = [a for a, filled in rows if filled is not None]
    pending = [a for a, filled in rows if filled is None]
    pend_dates = sorted(a + timedelta(days=horizon_days) for a in pending)
    future = [d for d in pend_dates if d > today]
    return {
        "total": len(rows), "matured": len(matured), "pending": len(pending),
        "next_maturity": (future[0] if future else None),
        "ungraded_due": len([d for d in pend_dates if d <= today]),  # matured but not yet graded
    }


# --------------------------------------------------------------------------- rendering
def _pct(x):
    return "  n/a" if x is None else f"{x:+.2%}"


def _ci(lo, hi):
    return "[ n/a ]" if lo is None or hi is None else f"[{lo:+.2%}, {hi:+.2%}]"


def bot_summary(days: int = 3650) -> str:
    """Compact forward block for the Telegram /trades view (HTML)."""
    m = forward_metrics(days)
    mat = cohort_maturity(days)
    if m["n_graded"] == 0:
        nxt = f"next outcomes ~{mat['next_maturity']}" if mat["next_maturity"] else "none scheduled"
        return (f"📈 <b>Forward</b>: 0 matured · {mat['pending']} pending\n"
                f"<i>cumulative excess starts once trades mature — {nxt}.</i>")
    cum = m["cumulative_excess"]
    sh = m["sharpe"]
    return (f"📈 <b>Forward equity</b> (n={m['n_graded']}, {m['n_cohorts']} cohorts)\n"
            f"cumulative excess <b>{_pct(cum)}</b> · "
            f"Sharpe {('n/a' if sh is None else f'{sh:.2f}')} · "
            f"max DD {_pct(m['max_drawdown'])}\n"
            f"pending {mat['pending']}"
            + (f" · next ~{mat['next_maturity']}" if mat["next_maturity"] else ""))


def main():
    LongTermAgentConfig()  # load env
    m = forward_metrics()
    mat = cohort_maturity()
    print("=" * 70)
    print(f"FORWARD PAPER-TESTING REPORT  (live, version {_version()}, 4w vs SPY)")
    print("=" * 70)
    print(f"cohort maturity: {mat['matured']} matured · {mat['pending']} pending "
          f"(of {mat['total']} actionable trades)")
    if mat["ungraded_due"]:
        print(f"  ⚠ {mat['ungraded_due']} matured but ungraded — run `python -m longterm_agent.grade_results`")
    if mat["next_maturity"]:
        print(f"  next outcomes mature ~{mat['next_maturity']}")
    if m["n_graded"] == 0:
        print("\nno matured trades yet — the equity curve begins once the first 4w horizon lands.")
        print("=" * 70)
        return
    gate = "" if m["n_graded"] >= MIN_N else f"  ⚠ ESTIMATE (n={m['n_graded']} < {MIN_N}; not yet a verdict)"
    d = m["distribution"]
    print(f"\ngraded trades : {m['n_graded']} across {m['n_cohorts']} cohorts{gate}")
    print(f"win rate      : {m['win_rate']:.0%}   CI {_ci(*m['win_ci'])}")
    print(f"mean excess   : {_pct(m['mean_excess'])}   CI {_ci(*m['mean_ci'])}")
    print(f"median / IQR  : {_pct(d['median'])}   [{_pct(d['p25'])}, {_pct(d['p75'])}]")
    sh = "n/a" if m["sharpe"] is None else f"{m['sharpe']:.2f}"
    print(f"cumulative ex : {_pct(m['cumulative_excess'])}   Sharpe {sh}   "
          f"max drawdown {_pct(m['max_drawdown'])}")
    print("\nequity curve (cumulative excess by cohort):")
    for c, cum in m["equity_curve"]:
        print(f"  {c}  {_pct(cum)}")
    print("\nconviction calibration:")
    for b in m["calibration"]:
        if b["n"]:
            print(f"  conv {b['lo']:.2f}-{b['hi']:.2f}: n={b['n']:2d} win {b['win_rate']:.0%} mean {_pct(b['mean_excess'])}")
    for label, dist in (("LONG", m["by_direction"]["LONG"]), ("SHORT", m["by_direction"]["SHORT"]),
                        ("shift", m["by_shift"]["shift"]), ("no_shift", m["by_shift"]["no_shift"])):
        if dist["n"]:
            print(f"  {label:9s}: n={dist['n']:2d} mean {_pct(dist['mean'])}")
    print("=" * 70)


if __name__ == "__main__":
    main()
