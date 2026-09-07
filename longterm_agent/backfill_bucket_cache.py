#!/usr/bin/env python3
"""Recompute weekly bucket summaries into a PRIVATE cache namespace, for offline replay.

Purpose: `compute_shift_metrics` is deterministic given the weekly bucket summaries, so the
shift DIRECTION — which `gate_trace.table_inputs` does not store — can be recovered for past
sweeps without re-running the agent. That needs the summaries the run used, and the cache
does not hold all of them.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
An earlier design copied summaries between existing cache namespaces to close an apparent
88% gap. That is wrong, and `config.BUCKET_CACHE_VERSION` says why in full: the cache key is
`(ticker, week_start, version)` where `week_start` is a DATE, but bucket boundaries are
anchored on `as_of` as a DATETIME. Two sweeps of the same Mondays at 06:30 and 12:00 produce
weeks whose article membership differs by hours of news while colliding on the same key. The
namespace exists precisely to keep them apart; copying across it is the silent corruption it
was built to prevent, and every future detection experiment reads that cache.

So this tool NEVER writes into a namespace anything else reads. `--namespace` is mandatory
and is refused if it already exists in `bucket_summary_cache` or matches the configured
`BUCKET_CACHE_VERSION`. A wrong assumption here damages only this tool's own rows.

WHY --clock IS MANDATORY
------------------------
The week grid is `as_of - lookback + i*bucket_days`, carrying the decision's time of day, so
the grid cannot be built without knowing it. `provenance.cohort_time_et` records it for only
**5 of 32 books** (640 rows at 06:30, 490 at 12:00, 19982 rows with no clock at all) — the
field was added late. There is no safe default: guessing noon for a 06:30 book produces
weeks that were never summarised and silently mismatched metrics. Pass the clock you know to
be right for the books you select, and the tool refuses any row whose recorded clock
disagrees.

INVARIANTS, both matching `background_profiling_node`
-----------------------------------------------------
  1. **Never write an incomplete bucket.** Production gates both the cache read and write on
     `complete = b.start + bucket_days <= as_of`, because a part-elapsed week is not final.
     The danger survives even in a private namespace: two pairs in the same week with
     different `as_of` collide on `week_start`. Reproducing a run exactly therefore still
     costs one map call for its most recent bucket, which this tool does not cache.
  2. **Never write an empty bucket.** `_map_bucket` returns a deterministic
     `sentiment=0.0, attention=low` for `count == 0` with no model call, and production does
     not cache it. Free to recompute, pointless to store.

Idempotent: `put_cached` upserts and present keys are skipped, so a killed run resumes by
re-running it.

Usage:
    python3 -m longterm_agent.backfill_bucket_cache --clock 12:00 \
        --books intraday_curve34 monday_mid1200                       # dry run
    python3 -m longterm_agent.backfill_bucket_cache --clock 12:00 \
        --books intraday_curve34 --namespace backfill-2026-08-21 --apply --limit 50
"""
import argparse
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.db import connect  # noqa: E402

BUCKET_DAYS = 7
# A private namespace must carry this prefix: production keys are versions like "0.1.0",
# so the rule makes them unreachable while letting an interrupted backfill resume its own.
PRIVATE_PREFIX = "backfill-"


def protected_namespaces(cur):
    """Namespaces the agent reads. Writing into any of them is refused."""
    cur.execute("SELECT DISTINCT agent_version FROM bucket_summary_cache "
                "WHERE agent_version IS NOT NULL")
    used = {r[0] for r in cur.fetchall()}
    try:
        from longterm_agent.config import LongTermAgentConfig as cfg
        if getattr(cfg, "BUCKET_CACHE_VERSION", None):
            used.add(cfg.BUCKET_CACHE_VERSION)
    except Exception:
        pass
    return used


def select_pairs(cur, clock, books):
    """(ticker, as_of, lookback) for the selected books, refusing clock disagreements."""
    sql = ["""SELECT DISTINCT backtest_id, ticker, as_of,
                     COALESCE((provenance->>'lookback_days')::int, 60) lb,
                     provenance->>'cohort_time_et' rec_clock
              FROM backtest_runs WHERE provenance IS NOT NULL"""]
    params = []
    if books:
        sql.append("AND backtest_id = ANY(%s)")
        params.append(list(books))
    cur.execute(" ".join(sql), params)
    pairs, refused, unknown_clock = [], defaultdict(int), defaultdict(int)
    for bid, ticker, as_of, lb, rec in cur.fetchall():
        if rec and rec != clock:
            refused[bid] += 1
            continue
        if not rec:
            unknown_clock[bid] += 1
        pairs.append((bid, ticker, as_of, lb))
    return pairs, dict(refused), dict(unknown_clock)


def needed_slots(pairs, clock):
    """Every COMPLETE (ticker, week_start) those runs used, on the given decision clock."""
    hh, mm = (int(x) for x in clock.split(":"))
    slots = set()
    for _bid, ticker, as_of, lb in pairs:
        as_of_dt = datetime(as_of.year, as_of.month, as_of.day, hh, mm)
        start = as_of_dt - timedelta(days=lb)
        for i in range(max(1, (lb + BUCKET_DAYS - 1) // BUCKET_DAYS)):
            b_start = start + timedelta(days=i * BUCKET_DAYS)
            if b_start + timedelta(days=BUCKET_DAYS) > as_of_dt:
                break  # invariant 1
            slots.add((ticker, b_start))
    return slots


def existing(cur, namespace):
    cur.execute("SELECT ticker, week_start FROM bucket_summary_cache WHERE agent_version=%s",
                [namespace])
    return {(t, w) for t, w in cur.fetchall()}


def generate(slots, namespace, limit, workers):
    """One corpus collection per TICKER (not per pair), then map the missing weeks.

    Corpus collection stays sequential — `Neo4jManager` is shared and its thread safety is
    not established here, and it is one call per ticker rather than per week anyway. The map
    calls inside a ticker are pooled: they are independent, and `bucket_cache._conn` opens a
    fresh psycopg2 connection per write instead of drawing on the shared pool, so concurrent
    writes cannot starve it.
    """
    from news_agent.database.db_manager import DBManager
    from graph_db.neo4j_manager import Neo4jManager
    from longterm_agent.config import LongTermAgentConfig as cfg
    from longterm_agent.tools.news_corpus_tool import get_company_news_corpus
    from longterm_agent.tools import bucket_cache
    from longterm_agent.agent.nodes import _map_bucket

    by_ticker = defaultdict(list)
    for ticker, w0 in sorted(slots):
        by_ticker[ticker].append(w0)
    if limit:
        trimmed, left = {}, limit
        for t, ws in by_ticker.items():
            if left <= 0:
                break
            trimmed[t] = ws[:left]
            left -= len(trimmed[t])
        by_ticker = trimmed

    db = DBManager(db_config=cfg.get_db_config(), min_conn=1, max_conn=3)
    neo = Neo4jManager()
    llm_map = cfg.make_llm(model=cfg.MAP_MODEL, max_tokens=2048)
    written = empty = failed = 0
    corpus_s = map_s = 0.0
    t0 = time.time()
    total = sum(len(v) for v in by_ticker.values())
    for n, (ticker, weeks) in enumerate(sorted(by_ticker.items()), 1):
        lo, hi = min(weeks), max(weeks)
        as_of = hi + timedelta(days=BUCKET_DAYS)
        lookback = (as_of - lo).days + 1
        tc = time.time()
        try:
            corpus = get_company_news_corpus(ticker, as_of, lookback, db, neo,
                                             min_confidence=cfg.AFFECTS_MIN_CONFIDENCE,
                                             embed_enabled=False)
        except Exception as e:
            print(f"  ⚠ {ticker}: corpus failed ({e}) — {len(weeks)} weeks skipped", flush=True)
            failed += len(weeks)
            continue
        corpus_s += time.time() - tc
        arts = corpus["articles"]
        by_id = {a["id"]: a for a in arts}
        jobs = []
        for w0 in weeks:
            w1 = w0 + timedelta(days=BUCKET_DAYS)
            in_week = [a for a in arts if w0 <= a["published"] < w1]
            if not in_week:  # invariant 2
                empty += 1
                continue
            jobs.append((w0, {"index": 0, "start": w0, "end": w1,
                              "article_ids": [a["id"] for a in in_week], "count": len(in_week),
                              "affects_sentiments": [a["affects_sentiment"] for a in in_week
                                                     if a.get("affects_sentiment")]}))

        def _one(job):
            w0, bucket = job
            bucket_cache.put_cached(db, ticker, w0.date(), namespace,
                                    _map_bucket(llm_map, bucket, by_id))

        tm = time.time()
        if jobs:
            if workers > 1:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    list(pool.map(_one, jobs))
            else:
                for job in jobs:
                    _one(job)
        map_s += time.time() - tm
        written += len(jobs)
        print(f"  [{n}/{len(by_ticker)}] {ticker}: {len(weeks)} weeks "
              f"| written {written}/{total}, empty {empty} | {time.time() - t0:.0f}s", flush=True)
    el = time.time() - t0
    print(f"\n  written {written} | empty (correctly not cached) {empty} | failed {failed}")
    print(f"  wall clock {el:.0f}s  (corpus {corpus_s:.0f}s, map {map_s:.0f}s, workers={workers})")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clock", required=True, metavar="HH:MM",
                    help="decision time of day the selected books ran on — no default, see the "
                         "module docstring; rows whose recorded cohort_time_et disagrees are refused")
    ap.add_argument("--books", nargs="+", help="restrict to these backtest_ids")
    ap.add_argument("--namespace",
                    help=f"private cache namespace to write into; must start with {PRIVATE_PREFIX!r} "
                         "(required with --apply)")
    ap.add_argument("--apply", action="store_true", help="write; omit for a dry run")
    ap.add_argument("--limit", type=int, default=0, help="cap weeks generated (timed sample)")
    ap.add_argument("--workers", type=int, default=6,
                    help="parallel map calls within a ticker (corpus stays sequential)")
    args = ap.parse_args()
    try:
        datetime.strptime(args.clock, "%H:%M")
    except ValueError:
        raise SystemExit(f"--clock must be HH:MM, got {args.clock!r}")

    conn = connect()
    cur = conn.cursor()
    protected = protected_namespaces(cur)
    if args.apply:
        if not args.namespace:
            raise SystemExit("--apply requires --namespace (a private one)")
        if not args.namespace.startswith(PRIVATE_PREFIX):
            raise SystemExit(
                f"REFUSING {args.namespace!r}: a private namespace must start with "
                f"{PRIVATE_PREFIX!r}.\n"
                f"  namespaces the agent reads: {sorted(protected)}\n"
                "  Bucket boundaries carry the decision clock, so a summary written under the "
                "wrong one is silently mismatched news. The prefix rule makes a production "
                "namespace unreachable by construction, and lets this tool resume its own.")

    pairs, refused, unknown = select_pairs(cur, args.clock, args.books)
    slots = needed_slots(pairs, args.clock)
    have = existing(cur, args.namespace) if args.namespace else set()
    todo = sorted(k for k in slots if (k[0], k[1].date()) not in have)

    print(f"clock {args.clock} | books {', '.join(args.books) if args.books else 'ALL'}")
    print(f"pairs selected: {len(pairs)}")
    if refused:
        print(f"  refused (recorded clock disagrees): {refused}")
    if unknown:
        print(f"  ⚠ no recorded clock, taking --clock on trust: "
              f"{sum(unknown.values())} pairs across {len(unknown)} books")
    print(f"complete bucket-slots on this clock: {len(slots)} distinct (ticker, week)")
    print(f"namespace {args.namespace or '(none given)'} — protected: {sorted(protected)}")
    print(f"already present: {len(slots) - len(todo)} | to generate: {len(todo)}")
    if todo:
        print(f"  ≈{len(todo) * 1200 / 1e6:.2f}M input tokens at the 12-article / 240-char cap")
    conn.close()
    if not args.apply:
        print("\n-- dry run; add --namespace <private> --apply to write --")
        return
    generate(set(todo), args.namespace, args.limit, max(1, args.workers))


if __name__ == "__main__":
    main()
