"""`sel_null` — does the selection stage add value? (docs/AB_PLAN_selection_stage.md)

Both arms are derived from ONE stored sweep. The selection rule is post-synthesis and
deterministic, so re-running the pipeline with a flag flipped would be strictly worse:
a deep-enabled replay is not reproducible across days (measured 2026-07-20: 10 cache
misses and conviction 0.82 → 0.78 on an identical cohort), which would confound the
flag's effect with graph drift. Here the decisions are held fixed by construction and
only the selection rule varies — the flag's effect is the whole difference.

Arms:
  incumbent   — `conviction` ≥ threshold (reproduces the stored `traded`)
  vetoes_only — deterministic eligibility only; conviction stops selecting

Reads `gate_trace` (P2a). Rows graded before that column existed are reported as
uncovered rather than guessed at.

    python -m longterm_agent.ab_selection --backtest-id wf_30x_deep_pit_on
    python -m longterm_agent.ab_selection --backtest-id A --backtest-id B --horizon 28
"""
import argparse
from collections import defaultdict
from statistics import mean, median

import psycopg2

from longterm_agent import backtest_metrics as M
from longterm_agent import costs
from longterm_agent.agent.nodes import selection_passed
from longterm_agent.config import LongTermAgentConfig

ARMS = ("incumbent", "vetoes_only")


# row layout from _fetch; METRIC_IDX maps a metric name to its slot
METRIC_IDX = {"excess": 4, "excess_beta": 5}


def _fetch(cur, backtest_id, horizon):
    cur.execute("""
        SELECT ticker, as_of, direction, traded, excess, excess_beta, gate_trace
        FROM backtest_runs
        WHERE backtest_id=%s AND horizon_days=%s AND direction IN ('LONG','SHORT')
        ORDER BY as_of, ticker
    """, [backtest_id, horizon])
    return cur.fetchall()


def _arm_book(rows, arm, threshold, metric="excess_beta", net_days=None):
    """[(as_of, value)] selected by `arm` scored on `metric`, plus rows selected.

    `net_days` charges D2 costs over that holding period. Costs matter here specifically
    because the arms trade at different rates — a gross-only comparison flatters whichever
    arm is busier, which is exactly the challenger.
    """
    idx = METRIC_IDX[metric]
    book, selected = [], 0
    for row in rows:
        as_of, direction, gt = row[1], row[2], row[6]
        mode = "conviction" if arm == "incumbent" else "vetoes_only"
        if not selection_passed(mode, direction, (gt or {}).get("conviction_final"),
                                (gt or {}).get("vetoes") or [], threshold):
            continue
        selected += 1
        v = row[idx]
        if v is None:
            continue
        book.append((as_of, v if net_days is None
                     else costs.net_excess(v, direction, net_days)))
    return book, selected


def _stats(book):
    if not book:
        return None
    vals = [e for _, e in book]
    blocks = defaultdict(list)
    for as_of, e in book:
        blocks[as_of].append(e)
    lo, hi = M.bootstrap_ci(vals, lambda v: mean(v))
    blo, bhi = M.bootstrap_ci_blocks(list(blocks.values()), lambda v: mean(v))
    return {"n": len(vals), "cohorts": len(blocks), "mean": mean(vals),
            "median": median(vals), "win": sum(1 for v in vals if v > 0) / len(vals),
            "worst": min(vals), "ci": (lo, hi), "block_ci": (blo, bhi)}


def _fmt(s):
    if not s:
        return "  (no graded rows)"
    ci = f"[{s['ci'][0]:+.4f},{s['ci'][1]:+.4f}]" if s["ci"][0] is not None else "n/a"
    bci = (f"[{s['block_ci'][0]:+.4f},{s['block_ci'][1]:+.4f}]"
           if s["block_ci"][0] is not None else "n/a")
    return (f"  n={s['n']:3} ({s['cohorts']} cohorts)  mean {s['mean']:+.4f}  "
            f"median {s['median']:+.4f}  win {s['win']:.0%}  worst {s['worst']:+.4f}\n"
            f"       per-trade CI {ci}   cohort-block CI {bci}")


def evaluate(cfg, backtest_id, horizon=28):
    conn = psycopg2.connect(**cfg.get_db_config())
    cur = conn.cursor()
    rows = _fetch(cur, backtest_id, horizon)
    conn.close()
    if not rows:
        print(f"\n=== {backtest_id} ({horizon}d): no directional rows ===")
        return None

    covered = [r for r in rows if r[6]]
    print(f"\n{'='*74}\n{backtest_id}  ({horizon}d)  directional rows={len(rows)}  "
          f"with gate_trace={len(covered)}\n{'='*74}")
    if not covered:
        print("  NO gate_trace on this book — it predates P2a. Re-grade or re-run to\n"
              "  populate it; this A/B refuses to guess which penalty fired.")
        return None
    if len(covered) < len(rows):
        print(f"  ⚠ {len(rows)-len(covered)} rows lack gate_trace and are excluded "
              f"(partial coverage weakens the comparison)")

    th = cfg.LONGTERM_CONFIDENCE_THRESHOLD
    primary = getattr(cfg, "PRIMARY_METRIC", "excess_beta")
    if primary not in METRIC_IDX:
        raise SystemExit(f"unknown LT_PRIMARY_METRIC={primary!r}; "
                         f"expected one of {sorted(METRIC_IDX)}")
    secondary = "excess" if primary == "excess_beta" else "excess_beta"
    print(f"  primary metric: {primary}  (secondary shown for contrast: {secondary})")

    print(f"  {costs.describe()}")

    out = {}
    for arm in ARMS:
        book, selected = _arm_book(covered, arm, th, primary)
        alt, _ = _arm_book(covered, arm, th, secondary)
        net, _ = _arm_book(covered, arm, th, primary, net_days=horizon)
        s, s_alt, s_net = _stats(book), _stats(alt), _stats(net)
        out[arm] = {"stats": s, "alt": s_alt, "net": s_net, "selected": selected}
        print(f"\n{arm}:  selected {selected}/{len(covered)}")
        print(_fmt(s))
        if s_alt:
            print(f"       [{secondary}] mean {s_alt['mean']:+.4f}")
        if s_net:
            print(f"       [net of costs] mean {s_net['mean']:+.4f}")

    # sanity: the incumbent arm must reproduce the stored `traded` flag
    stored = sum(1 for r in covered if r[3])
    if stored != out["incumbent"]["selected"]:
        print(f"\n  ⚠ incumbent arm selected {out['incumbent']['selected']} but "
              f"{stored} rows are stored as traded — the replay does not reproduce the "
              f"run; treat the numbers below as unverified")
    else:
        print(f"\n  ✓ incumbent arm reproduces the stored traded set ({stored} rows)")

    a, b = out["incumbent"]["stats"], out["vetoes_only"]["stats"]
    if a and b:
        delta = b["mean"] - a["mean"]
        ratio = out["vetoes_only"]["selected"] / max(1, out["incumbent"]["selected"])
        print(f"\n  DELTA (vetoes_only − incumbent): {delta:+.4f} mean {primary}")
        aa, ab = out["incumbent"]["alt"], out["vetoes_only"]["alt"]
        if aa and ab:
            # printed always: a verdict that depends on the ruler is a fragile verdict
            print(f"  same delta on {secondary}: {ab['mean'] - aa['mean']:+.4f}")
        print(f"  trade-count ratio: {ratio:.2f}x   worst-trade change: "
              f"{b['worst'] - a['worst']:+.4f}")
        # frozen stage-1 criteria (reject-only on burned windows)
        na, nb = out["incumbent"]["net"], out["vetoes_only"]["net"]
        net_delta = (nb["mean"] - na["mean"]) if (na and nb) else None
        if net_delta is not None:
            print(f"  net of costs: {net_delta:+.4f}  "
                  f"(costs cost the challenger {net_delta - delta:+.4f} vs the incumbent)")

        print("\n  frozen stage-1 criteria (burned window ⇒ can only REJECT):")
        print(f"    N1 challenger not worse by >1pp : "
              f"{'PASS' if delta > -0.01 else 'REJECT'}  ({delta:+.2%})")
        if net_delta is not None:
            print(f"    N1 net of costs (§7.4)          : "
                  f"{'PASS' if net_delta > -0.01 else 'REJECT'}  ({net_delta:+.2%})")
        print(f"    N2 trade count ≤ 2x             : "
              f"{'PASS' if ratio <= 2.0 else 'REJECT'}  ({ratio:.2f}x)")
        print(f"    N3 worst trade not >5pp worse   : "
              f"{'PASS' if b['worst'] - a['worst'] > -0.05 else 'REJECT'}  "
              f"({b['worst'] - a['worst']:+.2%})")
    return out


def main():
    ap = argparse.ArgumentParser(description="sel_null: selection-stage A/B, offline")
    ap.add_argument("--backtest-id", action="append", required=True,
                    help="repeatable; each book is evaluated separately")
    ap.add_argument("--horizon", type=int, default=28)
    args = ap.parse_args()
    cfg = LongTermAgentConfig()
    for bid in args.backtest_id:
        evaluate(cfg, bid, args.horizon)


if __name__ == "__main__":
    main()
