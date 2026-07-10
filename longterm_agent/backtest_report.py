"""
Backtest report over graded shift_library rows (detection-only hypothesis test).

Core question: do detected shifts predict subsequent excess return? For a NEGATIVE shift we
expect negative 4w excess (stock underperforms SPY); for POSITIVE, positive excess. "Hit" =
the realized 4w excess return had the same sign as the shift direction.

Sample sizes are printed prominently — with only a few weeks of history this is a SMOKE TEST
of the harness, not a verdict on the strategy. Statistical power comes from forward accumulation.

    python -m longterm_agent.backtest_report
"""
from statistics import mean, median

import psycopg2

from longterm_agent.config import LongTermAgentConfig


def _hit(direction, excess):
    if excess is None:
        return None
    return (direction == "positive" and excess > 0) or (direction == "negative" and excess < 0)


def main():
    cfg = LongTermAgentConfig()
    conn = psycopg2.connect(**cfg.get_db_config())
    cur = conn.cursor()
    cur.execute("""SELECT direction, fwd_return_4w, benchmark_return_4w, magnitude_z, shift_window_days
                   FROM shift_library
                   WHERE agent_version=%s AND outcome_filled_at IS NOT NULL AND fwd_return_4w IS NOT NULL""",
                [cfg.DETECTION_VERSION])
    rows = cur.fetchall()
    cur.execute("SELECT count(*) FROM shift_library WHERE agent_version=%s", [cfg.DETECTION_VERSION])
    total = cur.fetchone()[0]
    conn.close()

    print("=" * 70)
    print(f"SHIFT BACKTEST (detection-only)  agent_version={cfg.DETECTION_VERSION}")
    print("=" * 70)
    print(f"shift_library rows: {total} total, {len(rows)} graded (4w matured)")
    if not rows:
        print("\nNo graded rows yet — run backfill + grade, or wait for horizons to mature.")
        print("(Expected at cold start: outcomes need as_of + 4w ≤ today.)")
        return

    def summarize(label, subset):
        if not subset:
            print(f"\n{label}: n=0")
            return
        rets = [r[1] for r in subset]
        excess = [r[1] - (r[2] or 0.0) for r in subset]
        hits = [_hit(r[0], r[1] - (r[2] or 0.0)) for r in subset]
        hits = [h for h in hits if h is not None]
        hr = (sum(hits) / len(hits)) if hits else None
        print(f"\n{label}: n={len(subset)}")
        print(f"  mean 4w return : {mean(rets):+.2%}   median {median(rets):+.2%}")
        print(f"  mean 4w excess : {mean(excess):+.2%}   median {median(excess):+.2%}")
        print(f"  directional hit rate: {hr:.0%}" if hr is not None else "  hit rate: n/a")

    summarize("ALL shifts", rows)
    summarize("NEGATIVE shifts", [r for r in rows if r[0] == "negative"])
    summarize("POSITIVE shifts", [r for r in rows if r[0] == "positive"])

    print("\n" + "-" * 70)
    print("⚠ Interpret with care: tiny sample (see n). This validates the harness;")
    print("  meaningful hit-rate/return stats need a quarter+ of forward accumulation.")


if __name__ == "__main__":
    main()
