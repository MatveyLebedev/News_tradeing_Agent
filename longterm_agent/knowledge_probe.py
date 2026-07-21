"""C2 — does the LLM already KNOW the outcome? (a holdout is worthless if it does)

The data-side canaries (test_leakage_canary, test_deep_tools_canary) prove no READER
returns post-decision data. They cannot prove anything about the model's own memory: if
the checkpoint was trained through April 2026, then asking it about March 2026 is asking
it to recall, not to predict — and a "holdout" inside the training window measures
memorisation, not edge.

Design: difference-in-difference, never absolute accuracy. A model scores above 50% on
direction alone because equities drift up, so an absolute number proves nothing. Instead
the SAME tickers are asked on two sets of dates:

  treatment — inside the claimed cutoff (memorisable)
  control   — after it (unknowable by construction)

Both arms are answered with NO news, NO fundamentals, NO prices: the prompt carries a
ticker and a date and nothing else. Whatever the model scores in the control arm IS its
drift/prior baseline. Only the GAP is evidence.

  H0 (clean): treatment ≈ control ⇒ the window is usable as a holdout.
  H1 (leak) : treatment >> control ⇒ that window measures recall; do not spend it.

Reads outcomes from the price tables only — the probe never sees the agent pipeline, so a
bug there cannot manufacture a pass.

    python -m longterm_agent.knowledge_probe --dry-run     # prompts + cost, no LLM calls
    python -m longterm_agent.knowledge_probe
"""
import argparse
import random
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import psycopg2

from longterm_agent import backtest_metrics as M
from longterm_agent.config import LongTermAgentConfig
from longterm_agent.exit_rules import t1_return

# Dates are Mondays. Treatment sits inside the April-2026 cutoff claimed for the GA
# checkpoint; control sits after it. Both are 4w-matured against daily bars to 2026-07-17.
TREATMENT = ["2026-03-16", "2026-03-23", "2026-03-30",
             "2026-04-06", "2026-04-13", "2026-04-20"]
CONTROL = ["2026-05-18", "2026-05-25", "2026-06-01",
           "2026-06-08", "2026-06-15", "2026-06-19"]

# Liquid, heavily-covered names: the most likely to be memorised, i.e. the arm most
# favourable to detecting a leak. A null result here is therefore meaningful.
TICKERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
           "TSLA", "JPM", "XOM", "PFE"]

HORIZON = 28
BENCHMARK = "SPY"

PROMPT = (
    "You are given only a ticker and a date. No news, no filings, no prices.\n\n"
    "Ticker: {ticker}\nDate: {date}\n\n"
    "Over the {h} calendar days following that date, did {ticker} OUTPERFORM or "
    "UNDERPERFORM the S&P 500 (SPY)?\n"
    "Answer with exactly one word: OUTPERFORM or UNDERPERFORM."
)


def _outcome(cur, ticker, date_str):
    """True if the ticker beat SPY over the horizon, else False; None if ungraded."""
    d = date.fromisoformat(date_str)
    s = t1_return(cur, ticker, d, HORIZON)
    b = t1_return(cur, BENCHMARK, d, HORIZON)
    if s is None or b is None:
        return None
    return s["ret"] > b["ret"]


def _ask(llm, ticker, date_str):
    txt = llm.invoke(PROMPT.format(ticker=ticker, date=date_str, h=HORIZON)).content
    m = re.search(r"\b(OUTPERFORM|UNDERPERFORM)\b", (txt or "").upper())
    return None if not m else m.group(1) == "OUTPERFORM"


def _binom_p(hits, n):
    """One-sided p(X >= hits) under a fair coin, by simulation — no scipy dependency."""
    if not n:
        return None
    rng = random.Random(20260720)
    trials = 20000
    at_least = sum(1 for _ in range(trials)
                   if sum(rng.random() < 0.5 for _ in range(n)) >= hits)
    return at_least / trials


def run(dry_run=False):
    cfg = LongTermAgentConfig()
    conn = psycopg2.connect(**cfg.get_db_config())
    cur = conn.cursor()

    cases = []
    for arm, dates in (("treatment", TREATMENT), ("control", CONTROL)):
        for d in dates:
            for tk in TICKERS:
                truth = _outcome(cur, tk, d)
                if truth is not None:
                    cases.append((arm, d, tk, truth))
    conn.close()

    n_by_arm = defaultdict(int)
    for arm, *_ in cases:
        n_by_arm[arm] += 1
    print(f"model: {cfg.LLM_MODEL}   graded cases: "
          f"{dict(n_by_arm)}   total {len(cases)}")
    if dry_run:
        print("\n--- sample prompt ---")
        print(PROMPT.format(ticker="NVDA", date="2026-03-16", h=HORIZON))
        print(f"\n{len(cases)} calls, ~60 in + ~5 out tokens each — well under $0.10.")
        return None

    # NOT a tight budget: deepseek-v4-pro is a reasoning model, and a small max_tokens is
    # spent on hidden reasoning, returning empty content. A first run at 16 lost 73% of
    # cases that way — and the survivors were exactly the ones the model answered without
    # deliberating, i.e. selected for confident recall. That selection can manufacture the
    # very gap this probe looks for, so the budget must be large enough that nothing drops.
    llm = cfg.make_llm(max_tokens=2048)
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(lambda c: _ask(llm, c[2], c[1]), cases))

    res = defaultdict(lambda: {"hits": 0, "n": 0, "abstain": 0})
    for (arm, _d, _tk, truth), ans in zip(cases, answers):
        if ans is None:
            res[arm]["abstain"] += 1
            continue
        res[arm]["n"] += 1
        res[arm]["hits"] += int(ans == truth)

    print(f"\n{'arm':10} {'n':>4} {'hits':>5} {'accuracy':>9} {'p(>=hits|coin)':>15}")
    for arm in ("treatment", "control"):
        r = res[arm]
        if not r["n"]:
            print(f"{arm:10} — no parseable answers")
            continue
        acc = r["hits"] / r["n"]
        print(f"{arm:10} {r['n']:4} {r['hits']:5} {acc:8.1%} "
              f"{_binom_p(r['hits'], r['n']):15.3f}"
              + (f"   ({r['abstain']} unparsed)" if r["abstain"] else ""))

    t, c = res["treatment"], res["control"]
    # Dropped cases are not missing at random — they select for whatever the model answers
    # readily. Past a few percent the arms are no longer comparable and the gap is unusable.
    drop = sum(r["abstain"] for r in res.values()) / max(1, len(cases))
    if drop > 0.05:
        print(f"\n  ⚠ {drop:.0%} of cases returned no parseable answer. The surviving "
              f"subset is\n    self-selected, so the gap below is NOT interpretable. "
              f"Raise max_tokens and re-run.")
        return res
    if t["n"] and c["n"]:
        gap = t["hits"] / t["n"] - c["hits"] / c["n"]
        lo, hi = M.bootstrap_ci(
            [1] * t["hits"] + [0] * (t["n"] - t["hits"]), lambda v: sum(v) / len(v))
        print(f"\n  GAP (treatment − control): {gap:+.1%}")
        print(f"  treatment accuracy CI: [{lo:.1%}, {hi:.1%}]")
        print("\n  reading (frozen before the run):")
        print(f"    gap <= +10pp  ⇒ no detectable recall; March window usable as a holdout")
        print(f"    gap >  +10pp  ⇒ the model recalls that window; do NOT spend it")
        print(f"    VERDICT: {'USABLE' if gap <= 0.10 else 'CONTAMINATED'}  ({gap:+.1%})")
    return res


def main():
    ap = argparse.ArgumentParser(description="C2: does the model recall the holdout window?")
    ap.add_argument("--dry-run", action="store_true", help="prompts and cost, no LLM calls")
    run(**vars(ap.parse_args()))


if __name__ == "__main__":
    main()
