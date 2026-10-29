"""One-off regrade of rows whose exit was priced off a bar that was not there (2026-10-29).

Until `exit_rules.has_exit_bar` / `exit_close`, an exit was "the close on/before the
target" and the only guard asked whether a ticker's series reached PAST the target. Any
time the exit session's own bar was absent at grading, the trade was priced at the
previous session's close, and a graded row is never revisited. Three ways it happened:

  * a HOLE: the bar landed after a later one (2026-09-22 for 95 tickers, in on 09-25
    20:30 ET; LRCX 2026-06-17, in on 07-28);
  * NO GUARD on the leg: `backtest.grade()` had none at all, and the live sector-ETF leg
    exits on its own day, which the stock's guard never checked;
  * a MID-SESSION bar: a fetch run during the session wrote that day's bar at an intraday
    price, and a grade read it before a later fetch overwrote it (07-21 15:47 ET; the
    July fetcher ran weekly, so the intraday close stood until the Saturday top-up).

This recomputes every graded return column with the fixed helpers and lists each stored
value that differs, in:

    longterm_agent_results   hold 2w/4w/12w (both legs), stopped book, sector leg, MAE,
                             excess_beta (derived from the 4w legs and the stored beta)
    shift_library            fwd 2w/4w/12w, benchmark 4w
    backtest_runs            hold, stopped and t2 legs, with their excess columns

Each differing column carries its evidence from `stock_prices_daily.fetch_time` (first
landing, UTC): `hole` — a bar it reads landed after a later bar of the same ticker;
`after_grade` — a bar it reads first landed after the row's grade (outcome_filled_at,
taken as the 07:00 ET run; a hole is only counted while it was still open then);
`intraday_bar` — an exit bar it reads was first written during
its own session. One more cause is not about the exit at all and is tagged so it can be
told apart: `legacy_convention` — a row without fill_convention whose stored value is the
pre-2026-07-19 close→close return (graded by old code after the open_t1 regrade ran). A
column with no evidence is reported, not rewritten, unless --include-unexplained.

Default is a DRY RUN: prints the counts and writes the diff to longterm_agent/reports/
(the .md summary, and every differing row with its evidence in a gitignored _rows.jsonl).
--apply first dumps the touched columns' old values to a jsonl backup (gitignored), then
rewrites them. Idempotent: a second run finds nothing to change.

    python -m longterm_agent.regrade_exit_holes            # dry run + report
    python -m longterm_agent.regrade_exit_holes --apply    # backup + rewrite
"""
import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

import psycopg2
import pytz

from longterm_agent.backtest import _decision_moment as _cohort_moment
from longterm_agent.beta import beta_adjusted_excess
from longterm_agent.config import LongTermAgentConfig
from longterm_agent.exit_rules import (FILL_CONVENTION_D0, FILL_CONVENTION_MID, entry_bar,
                                       fetch_price_path, in_session, missing_sessions,
                                       resolve_entry, stopped_returns, t1_return)
from longterm_agent.grade_results import (BENCHMARK, EXTRA_HORIZONS, HORIZON_DAYS,
                                          _decision_moment)
from longterm_agent.market_calendar import last_session_on_or_before
from longterm_agent.stats import trade_excess
from longterm_agent.targets import worst_position_return

ET = pytz.timezone("America/New_York")
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
TABLES = ("longterm_agent_results", "shift_library", "backtest_runs")
# Stored columns are REAL rounded to 4 dp: anything under this is float32 noise.
TOL = 1.5e-4
GRADER_RUN = time(7, 0)   # the daily grader's ET start; outcome_filled_at is only a date
SESSION_CLOSE = time(16, 0)


class MemCursor:
    """Serves exit_rules' daily-bar queries from one in-memory load of stock_prices_daily
    (≈80k rows; per-row round trips over the remote link would take an hour). Minute-table
    queries (`mid_1m` fills) go to the real cursor."""

    def __init__(self, cur):
        self.cur = cur
        cur.execute("""SELECT ticker, date, open, close, fetch_time FROM stock_prices_daily
                       WHERE close IS NOT NULL ORDER BY ticker, date""")
        self.bars = defaultdict(dict)      # ticker -> {date: (open, close)}
        self.landed = {}                   # (ticker, date) -> first landing, ET (naive)
        self.hole = set()                  # (ticker, date) that landed after a later bar
        by_ticker = defaultdict(list)
        for t, d, o, c, ft in cur.fetchall():
            self.bars[t][d] = (float(o) if o is not None else float(c), float(c))
            if ft is not None:
                self.landed[(t, d)] = pytz.utc.localize(ft).astimezone(ET).replace(
                    tzinfo=None)
                by_ticker[t].append((d, self.landed[(t, d)]))
        for t, rows in by_ticker.items():
            later_min = None
            for d, ft in reversed(rows):
                if later_min is not None and ft > later_min:
                    self.hole.add((t, d))
                later_min = ft if later_min is None else min(later_min, ft)
        self._rows, self._real = [], False

    def execute(self, sql, args=None):
        s = " ".join(sql.split())
        if "stock_prices_daily" not in s:
            self._real = True
            self.cur.execute(sql, args)
            return
        self._real = False
        if "ticker='SPY'" in s:                                   # market_traded_on
            self._rows = [(1,)] if args[0] in self.bars["SPY"] else []
            return
        bars = self.bars.get(args[0], {})
        if "AND date=%s" in s:                                    # close_on
            b = bars.get(args[1])
            self._rows = [(b[1],)] if b else []
            return
        lo_ok = (lambda d: d >= args[1]) if "date >= %s" in s else (lambda d: d > args[1])
        if "ORDER BY date DESC" in s:                             # close_on_or_before
            hits = [d for d in bars if args[2] <= d <= args[1]]
            self._rows = [(bars[max(hits)][1],)] if hits else []
        elif "SELECT date, open, close" in s:                     # entry_bar
            ds = sorted(d for d in bars if lo_ok(d) and d <= args[2])[:args[3]]
            self._rows = [(d, *bars[d]) for d in ds]
        elif "SELECT date, close" in s:                           # fetch_price_path
            self._rows = [(d, bars[d][1]) for d in sorted(bars) if lo_ok(d) and d <= args[2]]
        else:
            raise ValueError(f"MemCursor: unhandled query {s[:120]}")

    def fetchone(self):
        if self._real:
            return self.cur.fetchone()
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self.cur.fetchall() if self._real else list(self._rows)

    def evidence(self, reads, graded_on):
        """Why a stored value could differ, from the first landings of the bars a column
        reads: `reads` = [(ticker, first date, last date)]; `graded_on` = the row's grade
        date, or None when the column has no grade date of its own (live 2w/12w)."""
        cutoff = datetime.combine(graded_on, GRADER_RUN) if graded_on else None
        out = []
        for t, lo, hi in reads:
            t = t.upper()
            for d in sorted(d for d in self.bars.get(t, {}) if lo <= d <= hi):
                ft = self.landed.get((t, d))
                late = ft is not None and (cutoff is None or ft > cutoff)
                if late and (t, d) in self.hole:     # a hole still open at the grade
                    out.append(f"hole {t} {d} @{ft:%m-%d %H:%M}")
                elif late and cutoff:
                    out.append(f"after_grade {t} {d} @{ft:%m-%d %H:%M}")
                elif lo == hi and ft and ft < datetime.combine(d, SESSION_CLOSE) \
                        and (graded_on is None or graded_on >= d):
                    out.append(f"intraday_bar {t} {d} @{ft:%m-%d %H:%M}")
        return out

    def legacy(self, ticker, as_of, days):
        """The pre-open_t1 close→close return (grade_shifts._ret), for the
        `legacy_convention` tag."""
        from longterm_agent.grade_shifts import _ret
        return _ret(self, ticker, as_of, days)


def _exit(tk, entry_date, days):
    x = last_session_on_or_before(entry_date + timedelta(days=days))
    return (tk, x, x)


def _r4(x):
    return None if x is None else round(x, 4)


def _differs(old, new):
    if isinstance(new, str):  # exit_reason
        return str(old).strip() != new
    if isinstance(new, int):  # exit_days
        return old != new
    return abs(float(old) - new) > TOL


class Diffs:
    """Per-row column diffs, each with the evidence for it."""

    def __init__(self):
        self.rows = []             # {table, id, key, cols: {col: (old, new, evidence)}}
        self.unpriceable = Counter()
        self.at_risk_same = Counter()   # exit bar late/mid-session, stored value still right

    def add(self, table, rid, key, stored, fresh, evidence):
        """fresh: {col: new value}; evidence: {col: [str]} (missing = no evidence)."""
        cols, risk = {}, None
        for c, new in fresh.items():
            old = stored.get(c)
            if old is None:
                continue                      # never graded: the daily grader fills it
            if new is None:
                self.unpriceable[(table, c)] += 1
            elif _differs(old, new):
                cols[c] = (old, new, evidence.get(c, []))
            elif evidence.get(c) and c not in PATH_COLUMNS:
                risk = evidence[c][0].split()[0]
        if cols:
            self.rows.append({"table": table, "id": rid, "key": key, "cols": cols})
        elif risk:
            self.at_risk_same[(table, risk)] += 1


def _hold(mc, ticker, as_of, days, entry=None, **kw):
    r = t1_return(mc, ticker, as_of, days, entry=entry, **kw)
    return None if r is None else r["ret"]


def _rows(cur, sql):
    cur.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def audit_results(mc, cfg, diffs):
    for row in _rows(mc.cur, """
            SELECT id, ticker, as_of, verdict, run_timestamp, outcome_filled_at, beta,
                   sector_benchmark, fwd_return_4w, benchmark_return_4w, fwd_return_2w,
                   benchmark_return_2w, fwd_return_12w, benchmark_return_12w,
                   fwd_return_stopped, benchmark_return_stopped, exit_reason, exit_days,
                   sector_benchmark_return_4w, worst_position_return_4w, excess_beta,
                   agent_version
            FROM longterm_agent_results
            WHERE verdict IN ('LONG','SHORT')
              AND (outcome_filled_at IS NOT NULL OR fwd_return_2w IS NOT NULL)
            ORDER BY as_of, id"""):
        tk, as_of, verdict, g = row["ticker"], row["as_of"], row["verdict"], \
            row["outcome_filled_at"]
        decision = _decision_moment(row["run_timestamp"])
        re_ = resolve_entry(mc, tk, as_of, decision)
        if re_ is None:
            diffs.unpriceable[("longterm_agent_results", "entry")] += 1
            continue
        conv, e_date, e_px = re_
        entry = bench_entry = None
        if conv == FILL_CONVENTION_MID:
            bre = resolve_entry(mc, BENCHMARK, as_of, decision)
            if bre is None or bre[0] != FILL_CONVENTION_MID:
                diffs.unpriceable[("longterm_agent_results", "bench_entry")] += 1
                continue
            entry, bench_entry = (e_date, e_px), (bre[1], bre[2])
        fresh, ev, raw = {}, {}, {}
        for tag, days in {"4w": HORIZON_DAYS, **EXTRA_HORIZONS}.items():
            raw[tag] = (_hold(mc, tk, as_of, days, entry),
                        _hold(mc, BENCHMARK, as_of, days, bench_entry))
            fresh[f"fwd_return_{tag}"], fresh[f"benchmark_return_{tag}"] = map(_r4, raw[tag])
            # 2w/12w fill on their own clock: no grade date, so only holes are evidence
            e = mc.evidence([_exit(tk, e_date, days), _exit(BENCHMARK, e_date, days)],
                            g if tag == "4w" else None)
            ev[f"fwd_return_{tag}"] = ev[f"benchmark_return_{tag}"] = e
        ex_b = beta_adjusted_excess(verdict, *raw["4w"], row["beta"])
        fresh["excess_beta"], ev["excess_beta"] = _r4(ex_b), ev["fwd_return_4w"]
        end4 = e_date + timedelta(days=HORIZON_DAYS)
        st = stopped_returns(mc, tk, as_of, verdict, HORIZON_DAYS, benchmark=BENCHMARK,
                             stop_loss=cfg.STOP_LOSS_PCT, take_profit=cfg.TAKE_PROFIT_PCT,
                             entry=entry, bench_entry=bench_entry)
        fresh.update({"fwd_return_stopped": _r4(st["fwd"]) if st else None,
                      "benchmark_return_stopped": _r4(st["bench"]) if st else None,
                      "exit_reason": st["reason"] if st else None,
                      "exit_days": st["days"] if st else None})
        reads = [(tk, e_date, end4)] + ([(BENCHMARK, st["exit_date"], st["exit_date"])]
                                        if st else [])
        e = mc.evidence(reads, g)
        for c in ("fwd_return_stopped", "benchmark_return_stopped", "exit_reason",
                  "exit_days"):
            ev[c] = e
        sb = row["sector_benchmark"]
        if sb and sb != "NONE":
            sr = t1_return(mc, sb, as_of, HORIZON_DAYS)
            fresh["sector_benchmark_return_4w"] = _r4(sr["ret"]) if sr else None
            seb = entry_bar(mc, sb, as_of)
            ev["sector_benchmark_return_4w"] = (
                mc.evidence([_exit(sb, seb[0], HORIZON_DAYS)], g) if seb else [])
        path = [(d, c) for d, c in fetch_price_path(mc, tk, as_of, HORIZON_DAYS) if d <= end4]
        fresh["worst_position_return_4w"] = (
            None if missing_sessions(path, as_of + timedelta(days=1), end4)
            else worst_position_return(path, verdict, e_px))
        ev["worst_position_return_4w"] = mc.evidence([(tk, as_of, end4)], g)
        diffs.add("longterm_agent_results", row["id"],
                  f"{row['agent_version']} {tk} {as_of} {verdict}", row, fresh, ev)


def audit_shift_library(mc, diffs):
    for row in _rows(mc.cur, """
            SELECT id, ticker, as_of, outcome_filled_at, fill_convention, fwd_return_2w,
                   fwd_return_4w, fwd_return_12w, benchmark_return_4w
            FROM shift_library WHERE outcome_filled_at IS NOT NULL ORDER BY id"""):
        tk, as_of, g = row["ticker"], row["as_of"], row["outcome_filled_at"]
        eb = entry_bar(mc, tk, as_of)
        if eb is None:
            diffs.unpriceable[("shift_library", "entry")] += 1
            continue
        fresh, ev = {}, {}
        for col, days in (("fwd_return_2w", 14), ("fwd_return_4w", 28),
                          ("fwd_return_12w", 84)):
            fresh[col] = _r4(_hold(mc, tk, as_of, days))
            ev[col] = mc.evidence([_exit(tk, eb[0], days)], g)
        fresh["benchmark_return_4w"] = _r4(_hold(mc, BENCHMARK, as_of, 28))
        sb = entry_bar(mc, BENCHMARK, as_of)
        ev["benchmark_return_4w"] = mc.evidence([_exit(BENCHMARK, sb[0], 28)], g) if sb else []
        if row["fill_convention"] is None:
            for col, (t, days) in {"fwd_return_2w": (tk, 14), "fwd_return_4w": (tk, 28),
                                   "fwd_return_12w": (tk, 84),
                                   "benchmark_return_4w": (BENCHMARK, 28)}.items():
                old, leg = row[col], mc.legacy(t, as_of, days)
                if old is not None and leg is not None and abs(old - leg) <= TOL:
                    ev[col] = [f"legacy_convention {t} close {as_of}→+{days}d"]
        diffs.add("shift_library", row["id"], f"{tk} {as_of}", row, fresh, ev)


def audit_backtests(mc, cfg, diffs):
    for row in _rows(mc.cur, """
            SELECT id, backtest_id, ticker, as_of, horizon_days, direction, fill_convention,
                   provenance->>'cohort_time_et' AS cohort_time_et, outcome_filled_at, beta,
                   fwd_return, benchmark_return, excess, fwd_return_stopped,
                   benchmark_return_stopped, excess_stopped, exit_reason, exit_days,
                   fwd_return_t2, benchmark_return_t2, excess_t2, excess_beta
            FROM backtest_runs WHERE outcome_filled_at IS NOT NULL ORDER BY id"""):
        tk, as_of, h, direction, g = (row["ticker"], row["as_of"], row["horizon_days"],
                                      row["direction"], row["outcome_filled_at"])
        d0 = row["fill_convention"] == FILL_CONVENTION_D0
        entry = bench_entry = None
        decision = _cohort_moment(as_of, row["cohort_time_et"])
        if decision is not None and in_session(decision):
            res = resolve_entry(mc, tk, as_of, decision)
            if res is None:
                diffs.unpriceable[("backtest_runs", "entry")] += 1
                continue
            if res[0] == FILL_CONVENTION_MID:
                bres = resolve_entry(mc, BENCHMARK, as_of, decision)
                if bres is None or bres[0] != FILL_CONVENTION_MID:
                    diffs.unpriceable[("backtest_runs", "bench_entry")] += 1
                    continue
                entry, bench_entry = (res[1], res[2]), (bres[1], bres[2])
        eb = entry or entry_bar(mc, tk, as_of, include_asof_day=d0)
        if eb is None:
            diffs.unpriceable[("backtest_runs", "entry")] += 1
            continue
        end = eb[0] + timedelta(days=h)
        fwd = _hold(mc, tk, as_of, h, entry, include_asof_day=d0)
        bench = _hold(mc, BENCHMARK, as_of, h, bench_entry, include_asof_day=d0)
        hold_ev = mc.evidence([_exit(tk, eb[0], h), _exit(BENCHMARK, eb[0], h)], g)
        fresh = {"fwd_return": _r4(fwd), "benchmark_return": _r4(bench),
                 "excess": _r4(trade_excess(direction, fwd, bench))}
        ev = dict.fromkeys(fresh, hold_ev)
        if direction in ("LONG", "SHORT"):
            fresh["excess_beta"] = _r4(beta_adjusted_excess(direction, fwd, bench,
                                                            row["beta"]))
            ev["excess_beta"] = hold_ev
            st = stopped_returns(mc, tk, as_of, direction, h, benchmark=BENCHMARK,
                                 stop_loss=cfg.STOP_LOSS_PCT,
                                 take_profit=cfg.TAKE_PROFIT_PCT, include_asof_day=d0,
                                 entry=entry, bench_entry=bench_entry)
            fresh.update({
                "fwd_return_stopped": _r4(st["fwd"]) if st else None,
                "benchmark_return_stopped": _r4(st["bench"]) if st else None,
                "excess_stopped": _r4(trade_excess(direction, st["fwd"], st["bench"]))
                if st else None,
                "exit_reason": st["reason"] if st else None,
                "exit_days": st["days"] if st else None})
            e = mc.evidence([(tk, eb[0], end)] + (
                [(BENCHMARK, st["exit_date"], st["exit_date"])] if st else []), g)
            for c in ("fwd_return_stopped", "benchmark_return_stopped", "excess_stopped",
                      "exit_reason", "exit_days"):
                ev[c] = e
        if row["fwd_return_t2"] is not None and entry is None:
            f2 = _hold(mc, tk, as_of, h, nth=2, include_asof_day=d0)
            b2 = _hold(mc, BENCHMARK, as_of, h, nth=2, include_asof_day=d0)
            fresh.update({"fwd_return_t2": _r4(f2), "benchmark_return_t2": _r4(b2),
                          "excess_t2": _r4(trade_excess(direction, f2, b2))})
            e2 = entry_bar(mc, tk, as_of, nth=2, include_asof_day=d0)
            e = mc.evidence([_exit(tk, e2[0], h), _exit(BENCHMARK, e2[0], h)], g) if e2 else []
            for c in ("fwd_return_t2", "benchmark_return_t2", "excess_t2"):
                ev[c] = e
        diffs.add("backtest_runs", row["id"],
                  f"{row['backtest_id']} {tk} {as_of} h{h} {direction}", row, fresh, ev)


def _explained(d):
    return {c: v for c, v in d["cols"].items() if v[2]}


CAUSES = ("legacy_convention", "hole", "intraday_bar", "after_grade")
# Columns read off a whole price path rather than one exit bar: their evidence includes bars
# after the stop day, which cannot change the value, so they say nothing about "at risk".
PATH_COLUMNS = {"fwd_return_stopped", "benchmark_return_stopped", "excess_stopped",
                "exit_reason", "exit_days", "worst_position_return_4w"}


def _cause(d):
    """The row's headline cause: the first of CAUSES any of its columns carries."""
    tags = {e.split()[0] for _, _, ev in d["cols"].values() for e in ev}
    return next((c for c in CAUSES if c in tags), "none")


def _report(diffs, todo, stamp):
    lines = [f"# Regrade: exits priced off a bar that was not there — {stamp}", "",
             "Recomputed with exit_rules.exit_close / has_exit_bar (fixed 2026-10-29). "
             "Evidence from first landings (`fetch_time`): `hole` = landed after a later bar "
             "of the same ticker, still missing at the grade; `after_grade` = landed after "
             "the row's grade (07:00 ET on outcome_filled_at); `intraday_bar` = an exit bar "
             "first written during its own session; `legacy_convention` = the stored value is "
             "the pre-open_t1 close→close return. Columns without evidence are listed, not "
             "rewritten. Every row with its evidence: the gitignored `_rows.jsonl` beside "
             "this file.", ""]
    for table in TABLES:
        rows = [d for d in diffs.rows if d["table"] == table]
        expl = [d for d in rows if _explained(d)]
        cols = Counter(c for d in rows for c in d["cols"])
        ecols = Counter(c for d in rows for c in _explained(d))
        by_cause = Counter(_cause(d) for d in rows)
        lines += [f"## {table}", "",
                  "- by cause: " + (", ".join(f"{c} {by_cause[c]}" for c in (*CAUSES, "none")
                                             if by_cause[c]) or "—"),
                  f"- rows with a differing stored value: **{len(rows)}**; with evidence "
                  f"for at least one column: **{len(expl)}**; rows to rewrite: "
                  f"{sum(1 for t, _, _ in todo if t == table)}",
                  "- differing columns (with evidence / all): "
                  + (", ".join(f"{c} {ecols[c]}/{n}" for c, n in cols.most_common()) or "—"),
                  ""]
        if table == "backtest_runs" and rows:
            per = Counter((d["key"].split()[0], _cause(d)) for d in rows)
            lines += ["| backtest_id | " + " | ".join(CAUSES) + " |",
                      "|---|" + "---|" * len(CAUSES)]
            for bid in sorted({b for b, _ in per}):
                lines.append(f"| {bid} | " + " | ".join(str(per[(bid, c)] or "")
                                                       for c in CAUSES) + " |")
            lines.append("")
        if rows and table != "backtest_runs":   # backtests: per row in the jsonl only
            lines += ["| id | row | column: stored → recomputed | evidence (first) |",
                      "|---|---|---|---|"]
            for d in rows:
                cs = "; ".join(f"{c}: {o} → {n}" for c, (o, n, _) in d["cols"].items())
                ev = next((e[0] for _, _, e in d["cols"].values() if e), "—")
                lines.append(f"| {d['id']} | {d['key']} | {cs} | {ev} |")
            lines.append("")
    if diffs.at_risk_same:
        lines += ["## Evidence but no difference", "",
                  "Rows whose EXIT bar landed after the 07:00 ET grade, in a hole or "
                  "mid-session, yet whose stored hold values match the recomputation (the "
                  "grade ran later that day, or the late write did not change the close): "
                  + ", ".join(f"{t} {c} {n}" for (t, c), n in sorted(diffs.at_risk_same.items())),
                  ""]
    if diffs.unpriceable:
        lines += ["## Not recomputable now (left as stored)", ""]
        lines += [f"- {t}.{c}: {n}" for (t, c), n in sorted(diffs.unpriceable.items())]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="back up, then rewrite")
    ap.add_argument("--include-unexplained", action="store_true",
                    help="also rewrite differing columns no first landing explains")
    ap.add_argument("--tables", default=",".join(TABLES))
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    conn = psycopg2.connect(**cfg.get_db_config())
    cur = conn.cursor()
    mc = MemCursor(cur)
    diffs = Diffs()
    tables = set(args.tables.split(","))
    if "longterm_agent_results" in tables:
        audit_results(mc, cfg, diffs)
    if "shift_library" in tables:
        audit_shift_library(mc, diffs)
    if "backtest_runs" in tables:
        audit_backtests(mc, cfg, diffs)

    todo = []   # (table, id, {col: (old, new)})
    for d in diffs.rows:
        cols = d["cols"] if args.include_unexplained else _explained(d)
        if cols:
            todo.append((d["table"], d["id"], {c: v[:2] for c, v in cols.items()}))
    stamp = datetime.now(ET).strftime("%Y-%m-%d")
    os.makedirs(REPORTS_DIR, exist_ok=True)
    rpath = os.path.join(REPORTS_DIR, f"regrade_exit_holes_{stamp}.md")
    with open(rpath, "w") as f:
        f.write(_report(diffs, todo, stamp))
    with open(rpath.replace(".md", "_rows.jsonl"), "w") as f:   # every row, gitignored
        for d in diffs.rows:
            f.write(json.dumps(d, default=str) + "\n")
    for table in TABLES:
        rows = [d for d in diffs.rows if d["table"] == table]
        causes = Counter(_cause(d) for d in rows)
        print(f"{table}: {len(rows)} rows differ ({', '.join(f'{c} {n}' for c, n in causes.most_common())}); "
              f"{sum(1 for t, _, _ in todo if t == table)} to rewrite")
    print(f"report → {rpath}" + ("" if args.apply else "  (dry run: nothing written)"))

    if args.apply and todo:
        bpath = os.path.join(REPORTS_DIR,
                             f"backup_pre_exit_holes_{datetime.now():%Y%m%d_%H%M%S}.jsonl")
        with open(bpath, "w") as f:
            for table, rid, cols in todo:
                f.write(json.dumps({"table": table, "id": rid,
                                    "old": {c: o for c, (o, _) in cols.items()}},
                                   default=str) + "\n")
        print(f"backup ({len(todo)} rows) → {bpath}")
        for table, rid, cols in todo:
            sets = ", ".join(f"{c}=%s" for c in cols)
            cur.execute(f"UPDATE {table} SET {sets} WHERE id=%s",  # noqa: S608
                        [n for _, n in cols.values()] + [rid])
        conn.commit()
        print(f"applied {len(todo)} row updates")
    conn.close()


if __name__ == "__main__":
    main()
