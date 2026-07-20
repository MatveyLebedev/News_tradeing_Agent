"""One-off re-grade of every measured book under the open_t1 fill convention.

The pre-2026-07-19 convention filled a trade at the last close ≤ as_of — for weekend
live cohorts a price printed BEFORE up to ~38h of news the decision had read, and in
every book it credited the untradable decision→entry gap to the trade. This script
recomputes all already-measured return columns with exit_rules (entry = open of the
first bar strictly after as_of, horizon anchored at the entry) across:

    longterm_agent_results   (live + validation-era rows)
    backtest_runs            (all backtest_ids, per-row horizon)
    shift_library            (analog base — same convention as trades)

Decisions/labels/prompts are untouched; this is measurement only.

Default is a DRY-RUN: prints + writes the side-by-side report (old vs new, weekend-gap
decomposition) to longterm_agent/reports/. With --apply it first dumps every touched
row's old values to a jsonl backup, then rewrites the columns and stamps
entry_date/entry_px/fill_convention. Rows whose NEW exit window hasn't matured yet
(entry+h beyond the price series) are REOPENED (returns NULLed, outcome_filled_at
cleared) so the daily graders re-fill them honestly. Idempotent: re-running recomputes
the same values.

    python -m longterm_agent.regrade_fill_convention           # dry-run report
    python -m longterm_agent.regrade_fill_convention --apply   # backup + rewrite
"""
import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import mean, median

import psycopg2

from longterm_agent.config import LongTermAgentConfig
from longterm_agent.exit_rules import (FILL_CONVENTION, close_on_or_before, entry_bar,
                                       fetch_price_path, simulate_exit)
from longterm_agent.grade_shifts import _has_data_through
from longterm_agent.stats import trade_excess

BENCH = "SPY"
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class PriceCache:
    """Point lookups memoized per (ticker, date) — cohorts share entry bars."""

    def __init__(self, cur):
        self.cur = cur
        self._eb, self._cb, self._max = {}, {}, {}

    def entry(self, tk, as_of):
        k = (tk, as_of)
        if k not in self._eb:
            self._eb[k] = entry_bar(self.cur, tk, as_of)
        return self._eb[k]

    def close_leq(self, tk, target):
        k = (tk, target)
        if k not in self._cb:
            self._cb[k] = close_on_or_before(self.cur, tk, target)
        return self._cb[k]

    def matured(self, tk, target):
        return _has_data_through(self.cur, tk, target, self._max)

    def t1(self, tk, as_of, days):
        """t1_return via the caches; None when entry/exit is not measurable."""
        eb = self.entry(tk, as_of)
        if eb is None or not eb[1]:
            return None
        if not self.matured(tk, eb[0] + timedelta(days=days)):
            return None
        x = self.close_leq(tk, eb[0] + timedelta(days=days))
        if x is None:
            return None
        return {"entry_date": eb[0], "entry_px": eb[1], "ret": (x - eb[1]) / eb[1]}


def _stopped(cur, pc, tk, as_of, direction, horizon, cfg):
    eb = pc.entry(tk, as_of)
    if eb is None:
        return None
    path = fetch_price_path(cur, tk, as_of, horizon)
    sim = simulate_exit(path, eb[0], eb[1], direction, horizon,
                        stop_loss=cfg.STOP_LOSS_PCT, take_profit=cfg.TAKE_PROFIT_PCT)
    if sim is None:
        return None
    bb = pc.entry(BENCH, as_of)
    b_exit = pc.close_leq(BENCH, sim["exit_date"]) if bb else None
    if bb is None or b_exit is None:
        return None
    return {"fwd": (sim["exit_px"] - sim["entry_px"]) / sim["entry_px"],
            "bench": (b_exit - bb[1]) / bb[1],
            "reason": sim["reason"], "days": sim["days"]}


def _gap(pc, tk, as_of, entry_px):
    """Untradable decision→entry gap: last close ≤ as_of (the OLD entry) → open_t1."""
    prior = pc.close_leq(tk, as_of)
    if not prior or not entry_px:
        return None
    return (entry_px - prior) / prior


class Agg:
    """old/new stats for one report bucket."""

    def __init__(self):
        self.rows = 0
        self.reopened = 0
        self.old_f, self.new_f = [], []          # all-row fwd returns (primary horizon)
        self.old_ex, self.new_ex = [], []        # traded-only excess
        self.gaps = []                           # traded-only sign-adjusted gap

    def line(self, label):
        def m(v):
            return f"{mean(v):+.4f}" if v else "—"

        def wr(v):
            return f"{100 * sum(1 for x in v if x > 0) / len(v):.0f}%" if v else "—"

        return (f"| {label} | {self.rows} | {self.reopened} | {m(self.old_f)} | {m(self.new_f)} "
                f"| {len(self.old_ex)} | {m(self.old_ex)} | {m(self.new_ex)} "
                f"| {wr(self.old_ex)} | {wr(self.new_ex)} | {m(self.gaps)} |")


HEADER = ("| bucket | rows | reopened | mean fwd old | new | trades | mean excess old | new "
          "| win% old | new | mean gap→old book |\n"
          "|---|---|---|---|---|---|---|---|---|---|---|")


def _regrade_results(cur, pc, cfg, updates, backup):
    cur.execute("""
        SELECT id, agent_version, ticker, as_of, verdict, traded,
               fwd_return_2w, fwd_return_4w, fwd_return_12w,
               benchmark_return_2w, benchmark_return_4w, benchmark_return_12w,
               sector_benchmark, sector_benchmark_return_4w, worst_position_return_4w,
               fwd_return_stopped, benchmark_return_stopped, exit_reason, exit_days
        FROM longterm_agent_results
        WHERE fwd_return_4w IS NOT NULL OR fwd_return_2w IS NOT NULL
           OR fwd_return_12w IS NOT NULL
        ORDER BY as_of, ticker""")
    aggs, wd = defaultdict(Agg), defaultdict(Agg)
    for (rid, ver, tk, as_of, verdict, traded, f2, f4, f12, b2, b4, b12,
         sec_bm, sec_r, worst, fst, bst, exr, exd) in cur.fetchall():
        a = aggs[f"results/{ver}"]
        a.rows += 1
        new, old_cols = {}, {}
        eb = pc.entry(tk, as_of)
        t4 = pc.t1(tk, as_of, 28)
        if eb is None or (f4 is not None and t4 is None):
            # entry/exit not measurable under the new anchor → reopen the row
            a.reopened += 1
            for col, v in (("fwd_return_2w", f2), ("fwd_return_4w", f4),
                           ("fwd_return_12w", f12), ("benchmark_return_2w", b2),
                           ("benchmark_return_4w", b4), ("benchmark_return_12w", b12),
                           ("sector_benchmark_return_4w", sec_r),
                           ("worst_position_return_4w", worst),
                           ("fwd_return_stopped", fst), ("benchmark_return_stopped", bst),
                           ("exit_reason", exr), ("exit_days", exd)):
                if v is not None:
                    old_cols[col] = v
                    new[col] = None
            new["outcome_filled_at"] = None
            backup.append({"table": "longterm_agent_results", "id": rid, "old": old_cols})
            updates.append(("longterm_agent_results", rid, new))
            continue
        e_date, e_px = eb
        for tag, days, oldv in (("2w", 14, f2), ("4w", 28, f4), ("12w", 84, f12)):
            if oldv is None:
                continue
            r = pc.t1(tk, as_of, days)
            b = pc.t1(BENCH, as_of, days)
            old_cols[f"fwd_return_{tag}"] = oldv
            new[f"fwd_return_{tag}"] = round(r["ret"], 4) if r else None
            new[f"benchmark_return_{tag}"] = round(b["ret"], 4) if b else None
        if sec_r is not None and sec_bm and sec_bm != "NONE":
            sr = pc.t1(sec_bm, as_of, 28)
            old_cols["sector_benchmark_return_4w"] = sec_r
            new["sector_benchmark_return_4w"] = round(sr["ret"], 4) if sr else None
        if worst is not None:
            from longterm_agent.targets import worst_position_return
            h_path = [(d, c) for d, c in fetch_price_path(cur, tk, as_of, 28)
                      if d <= e_date + timedelta(days=28)]
            old_cols["worst_position_return_4w"] = worst
            new["worst_position_return_4w"] = worst_position_return(h_path, verdict, e_px)
        if exr is not None and verdict in ("LONG", "SHORT"):
            st = _stopped(cur, pc, tk, as_of, verdict, 28, cfg)
            old_cols.update({"fwd_return_stopped": fst, "exit_reason": exr})
            if st:
                new.update({"fwd_return_stopped": round(st["fwd"], 4),
                            "benchmark_return_stopped": round(st["bench"], 4),
                            "exit_reason": st["reason"], "exit_days": st["days"]})
        new.update({"entry_date": e_date, "entry_px": e_px,
                    "fill_convention": FILL_CONVENTION})
        backup.append({"table": "longterm_agent_results", "id": rid, "old": old_cols})
        updates.append(("longterm_agent_results", rid, new))
        # report stats (primary horizon)
        if f4 is not None and t4 is not None:
            a.old_f.append(f4)
            a.new_f.append(t4["ret"])
            w = wd[f"results as_of={WEEKDAYS[as_of.weekday()]}"]
            w.rows += 1
            w.old_f.append(f4)
            w.new_f.append(t4["ret"])
            if traded and verdict in ("LONG", "SHORT") and b4 is not None:
                b_new = pc.t1(BENCH, as_of, 28)
                ex_o = trade_excess(verdict, f4, b4)
                ex_n = trade_excess(verdict, t4["ret"], b_new["ret"] if b_new else None)
                g = _gap(pc, tk, as_of, e_px)
                for bucket in (a, w):
                    if ex_o is not None:
                        bucket.old_ex.append(ex_o)
                    if ex_n is not None:
                        bucket.new_ex.append(ex_n)
                    if g is not None:
                        bucket.gaps.append((1 if verdict == "LONG" else -1) * g)
    return aggs, wd


def _regrade_backtests(cur, pc, cfg, updates, backup):
    cur.execute("""
        SELECT id, backtest_id, ticker, as_of, horizon_days, direction, traded,
               fwd_return, benchmark_return, excess, fwd_return_stopped,
               benchmark_return_stopped, excess_stopped, exit_reason, exit_days
        FROM backtest_runs
        WHERE fwd_return IS NOT NULL
          AND (fill_convention IS NULL OR fill_convention = 'open_t1')
        ORDER BY backtest_id, as_of, ticker""")  # open_d0 grids grade by their own rule
    aggs, wd = defaultdict(Agg), defaultdict(Agg)
    for (rid, bid, tk, as_of, horizon, direction, traded, f, b, ex, fst, bst,
         exst, exr, exd) in cur.fetchall():
        a = aggs[f"backtest/{bid}/h{horizon}"]
        a.rows += 1
        t = pc.t1(tk, as_of, horizon)
        if t is None:
            a.reopened += 1
            backup.append({"table": "backtest_runs", "id": rid,
                           "old": {"fwd_return": f, "benchmark_return": b, "excess": ex,
                                   "exit_reason": exr}})
            updates.append(("backtest_runs", rid,
                            {"fwd_return": None, "benchmark_return": None, "excess": None,
                             "fwd_return_stopped": None, "benchmark_return_stopped": None,
                             "excess_stopped": None, "exit_reason": None, "exit_days": None,
                             "outcome_filled_at": None}))
            continue
        bn = pc.t1(BENCH, as_of, horizon)
        new_f = round(t["ret"], 4)
        new_b = round(bn["ret"], 4) if bn else None
        new_ex = trade_excess(direction, t["ret"], bn["ret"] if bn else None)
        new = {"fwd_return": new_f, "benchmark_return": new_b,
               "excess": round(new_ex, 4) if new_ex is not None else None,
               "entry_date": t["entry_date"], "entry_px": t["entry_px"],
               "fill_convention": FILL_CONVENTION}
        old_cols = {"fwd_return": f, "benchmark_return": b, "excess": ex}
        if exr is not None and direction in ("LONG", "SHORT"):
            st = _stopped(cur, pc, tk, as_of, direction, horizon, cfg)
            old_cols.update({"fwd_return_stopped": fst, "excess_stopped": exst,
                             "exit_reason": exr})
            if st:
                ex_st = trade_excess(direction, st["fwd"], st["bench"])
                new.update({"fwd_return_stopped": round(st["fwd"], 4),
                            "benchmark_return_stopped": round(st["bench"], 4),
                            "excess_stopped": round(ex_st, 4) if ex_st is not None else None,
                            "exit_reason": st["reason"], "exit_days": st["days"]})
        backup.append({"table": "backtest_runs", "id": rid, "old": old_cols})
        updates.append(("backtest_runs", rid, new))
        a.old_f.append(f)
        a.new_f.append(t["ret"])
        w = wd[f"backtests as_of={WEEKDAYS[as_of.weekday()]}"]
        w.rows += 1
        w.old_f.append(f)
        w.new_f.append(t["ret"])
        if traded and direction in ("LONG", "SHORT"):
            g = _gap(pc, tk, as_of, t["entry_px"])
            for bucket in (a, w):
                if ex is not None:
                    bucket.old_ex.append(ex)
                if new_ex is not None:
                    bucket.new_ex.append(new_ex)
                if g is not None:
                    bucket.gaps.append((1 if direction == "LONG" else -1) * g)
    return aggs, wd


def _regrade_shift_library(cur, pc, updates, backup):
    cur.execute("""ALTER TABLE shift_library
                   ADD COLUMN IF NOT EXISTS entry_date DATE,
                   ADD COLUMN IF NOT EXISTS entry_px REAL,
                   ADD COLUMN IF NOT EXISTS fill_convention VARCHAR(12)""")
    cur.execute("""
        SELECT id, ticker, as_of, fwd_return_2w, fwd_return_4w, fwd_return_12w,
               benchmark_return_4w
        FROM shift_library WHERE outcome_filled_at IS NOT NULL
        ORDER BY as_of, ticker""")
    aggs = defaultdict(Agg)
    a = aggs["shift_library"]
    for rid, tk, as_of, f2, f4, f12, b4 in cur.fetchall():
        a.rows += 1
        t4 = pc.t1(tk, as_of, 28)
        eb = pc.entry(tk, as_of)
        if eb is None or (f4 is not None and t4 is None):
            a.reopened += 1
            backup.append({"table": "shift_library", "id": rid,
                           "old": {"fwd_return_4w": f4}})
            updates.append(("shift_library", rid,
                            {"fwd_return_2w": None, "fwd_return_4w": None,
                             "fwd_return_12w": None, "benchmark_return_4w": None,
                             "outcome_filled_at": None}))
            continue
        new, old_cols = {}, {}
        for col, days, oldv in (("fwd_return_2w", 14, f2), ("fwd_return_4w", 28, f4),
                                ("fwd_return_12w", 84, f12)):
            if oldv is None:
                continue
            r = pc.t1(tk, as_of, days)
            old_cols[col] = oldv
            new[col] = round(r["ret"], 4) if r else None
        if b4 is not None:
            bn = pc.t1(BENCH, as_of, 28)
            old_cols["benchmark_return_4w"] = b4
            new["benchmark_return_4w"] = round(bn["ret"], 4) if bn else None
        new.update({"entry_date": eb[0], "entry_px": eb[1],
                    "fill_convention": FILL_CONVENTION})
        backup.append({"table": "shift_library", "id": rid, "old": old_cols})
        updates.append(("shift_library", rid, new))
        if f4 is not None and t4 is not None:
            a.old_f.append(f4)
            a.new_f.append(t4["ret"])
    return aggs


def main():
    ap = argparse.ArgumentParser(description="Re-grade all measured books to open_t1")
    ap.add_argument("--apply", action="store_true",
                    help="write the recomputed values (default: dry-run report only)")
    args = ap.parse_args()

    cfg = LongTermAgentConfig()
    conn = psycopg2.connect(**cfg.get_db_config())
    cur = conn.cursor()
    # entry stamps live on every book (the graders also ensure these on their own runs)
    for table in ("longterm_agent_results", "backtest_runs", "shift_library"):
        cur.execute(f"""ALTER TABLE {table}
                        ADD COLUMN IF NOT EXISTS entry_date DATE,
                        ADD COLUMN IF NOT EXISTS entry_px REAL,
                        ADD COLUMN IF NOT EXISTS fill_convention VARCHAR(12)""")
    conn.commit()
    pc = PriceCache(cur)
    updates, backup = [], []

    res_aggs, res_wd = _regrade_results(cur, pc, cfg, updates, backup)
    bt_aggs, bt_wd = _regrade_backtests(cur, pc, cfg, updates, backup)
    sl_aggs = _regrade_shift_library(cur, pc, updates, backup)

    stamp = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# Re-grade to `open_t1` fill convention — {stamp}", "",
             "Entry = open of the first trading bar strictly after as_of; horizon anchored",
             "at the entry; benchmark measured with the same convention. Decisions, labels",
             "and prompts untouched — measurement only. `mean gap→old book` is the",
             "sign-adjusted untradable gap (last close ≤ as_of → entry open) the old",
             "convention credited to traded rows.", "", HEADER]
    for aggs in (res_aggs, bt_aggs, sl_aggs):
        for label in sorted(aggs):
            lines.append(aggs[label].line(label))
    lines += ["", "## By as_of weekday (primary horizon rows)", "", HEADER]
    for wd in (res_wd, bt_wd):
        for label in sorted(wd):
            lines.append(wd[label].line(label))
    lines += ["", f"Rows touched: {len(updates)}; mode: "
              + ("APPLY" if args.apply else "dry-run"), ""]
    report = "\n".join(lines)
    print(report)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    rpath = os.path.join(REPORTS_DIR, f"regrade_open_t1_{stamp}.md")
    with open(rpath, "w") as f:
        f.write(report)
    print(f"report → {rpath}")

    if args.apply:
        bpath = os.path.join(REPORTS_DIR,
                             f"backup_pre_open_t1_{datetime.now():%Y%m%d_%H%M%S}.jsonl")
        with open(bpath, "w") as f:
            for row in backup:
                f.write(json.dumps(row, default=str) + "\n")
        print(f"backup ({len(backup)} rows) → {bpath}")
        for table, rid, cols in updates:
            sets = ", ".join(f"{c}=%s" for c in cols)
            cur.execute(f"UPDATE {table} SET {sets} WHERE id=%s",
                        list(cols.values()) + [rid])
        conn.commit()
        print(f"applied {len(updates)} row updates")
    conn.close()


if __name__ == "__main__":
    main()
