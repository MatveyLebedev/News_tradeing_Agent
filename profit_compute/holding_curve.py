#!/usr/bin/env python3
"""Profit as a function of holding time, for trades entered inside the regular session.

The hypothesis this measures: a position opened **one minute after** the news that
triggered it (`--latency-min`, the agent's working time), while the session is open, has a
return profile that depends on how long it is held — and that profile has a shape worth
knowing before an exit rule is written.

The design point that makes the curve readable: **every horizon is evaluated on the same
entry**. Only the exit moves along the x axis, so a bend in the curve is drift, not a
change of sample — as long as the sample itself is held fixed, which is what `--truncation`
governs (see below, it is the one knob that can manufacture a shape out of nothing).

Not a decision path. Every price here is a realized outcome, so under `docs/PIT_RULES.md`
§4 this file belongs to grading and may see the future by construction. The PIT rule that
*does* bind is the entry: the entry bar must start **strictly after** `published + latency`.
`trade_simulator.py` takes the bar nearest the entry time within ±5 min, which can fill at a
price printed *before* the news — this module only ever searches forward.

Entry universes (`--source`):
  agent     — the agent's own signals (`agent_results` ⋈ `news`), direction = buy/sell.
  reaction  — single-name market-reaction headlines, direction read off the headline verb
              (`scripts/scan_reaction_news.classify`). Cross-check universe: it is bigger
              and far less day-concentrated than `agent`, and per
              `docs/REACTION_NEWS_DATASET_2026-07-27.md` §4 its expected curve is flat —
              which makes it the null this harness should reproduce if it is honest.

Usage:
    python3 -m profit_compute.holding_curve --source agent
    python3 -m profit_compute.holding_curve --source reaction --market-adjust
    python3 -m profit_compute.holding_curve --source agent --truncation balanced
"""
import argparse
import json
import math
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

# `historical_data_*` bars are tz-naive US/Eastern wall clock, regular session only:
# 390 one-minute bars, 09:30 through 15:59. `news.published` is naive ET too, so the whole
# module stays in naive ET and never localizes — mixing the two clocks is §6 of PIT_RULES.
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
SESSION_BARS = 390

# A price *point* grid of SESSION_BARS + 1 entries: point k is the open of bar k, and the
# last point is the 16:00 close of the final bar. Holding h minutes from point e is then
# exactly point e + h, with no special-casing at either end.
GRID_POINTS = SESSION_BARS + 1

DEFAULT_HORIZONS = [1, 2, 3, 5, 7, 10, 15, 20, 30, 45, 60, 90, 120, 150, 180, 240, 300, 360]


# ── entry universes ──────────────────────────────────────────────────────────────────


def _table(ticker: str) -> str:
    return "historical_data_" + ticker.lower().replace("-", "_").replace(".", "_")


def _priced_universe(conn) -> set[str]:
    """Tickers we hold minute bars for. ETFs are NOT among them — see the census note."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            r"WHERE table_name LIKE 'historical\_data\_%'"
        )
        return {r[0].replace("historical_data_", "").upper().replace("_", "-") for r in cur.fetchall()}


def load_agent_entries(conn, min_confidence: float, agent_version: str | None) -> list[dict]:
    """Signals from `agent_results`, joined to `news` for the authoritative publication time.

    `agent_results.news_published` is tz-aware and, per PIT_RULES §6, mis-tagged on some
    rows; `news.published` is the naive-ET original. Always join.
    """
    sql = """
        SELECT ar.id, ar.agent_name, ar.agent_version, ar.news_id, n.published,
               n.fetch_time, ar.execution_time_seconds, ar.news_title, ar.signals_json
        FROM agent_results ar
        JOIN news n ON n.id = ar.news_id
        WHERE ar.signals_generated > 0
    """
    params: list = []
    if agent_version:
        sql += " AND ar.agent_version = %s"
        params.append(agent_version)
    sql += " ORDER BY n.published, ar.id"

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    entries = []
    for _rid, agent, version, news_id, published, fetch_time, exec_s, title, signals_json in rows:
        parsed = json.loads(signals_json) if isinstance(signals_json, str) else (signals_json or [])
        for sig in parsed:
            direction = (sig.get("signal") or "").strip().lower()
            if direction not in ("buy", "sell"):
                continue
            conf = float(sig.get("confidence") or 0.0)
            if conf < min_confidence:
                continue
            entries.append(
                {
                    "news_id": news_id,
                    "published": published,
                    "fetch_time": fetch_time,
                    "exec_seconds": float(exec_s) if exec_s is not None else None,
                    "ticker": (sig.get("ticker") or "").strip().upper(),
                    "side": 1 if direction == "buy" else -1,
                    "confidence": conf,
                    "label": f"{agent}/{version}",
                    "title": title,
                }
            )
    return entries


def load_reaction_entries(conn, min_confidence: float, agent_version: str | None) -> list[dict]:
    """Single-name reaction headlines; direction is the headline's own move verb."""
    del min_confidence, agent_version  # not applicable to this universe
    from scripts.scan_reaction_news import classify

    with conn.cursor() as cur:
        cur.execute("SELECT id, published, fetch_time, title FROM news ORDER BY published")
        rows = cur.fetchall()

    entries = []
    for news_id, published, fetch_time, title in rows:
        c = classify(title)
        # STRICT (asset subject + causal connector), one unambiguous mover, and the mover
        # must be a price rather than a fundamental ("Profit Jumps 68%" is not a reaction).
        if not c or not (c["asset"] and c["cause"]):
            continue
        if c["cls"] != "single_name" or len(c["tickers"]) != 1 or c["fundamental_subject"]:
            continue
        entries.append(
            {
                "news_id": news_id,
                "published": published,
                "fetch_time": fetch_time,
                "exec_seconds": None,  # no agent ran; only `flat` and `ingest` latency apply
                "ticker": c["tickers"][0],
                "side": 1 if c["direction"] == "up" else -1,
                "confidence": float("nan"),
                "label": "reaction_headline",
                "title": title,
            }
        )
    return entries


SOURCES = {"agent": load_agent_entries, "reaction": load_reaction_entries}


# ── price grids ──────────────────────────────────────────────────────────────────────


def _session_point(published: datetime, latency_min: float) -> int:
    """Grid point of the first price we could actually have traded at.

    Decision lands at `published + latency`; we fill at the next bar to *start* at or after
    it, so a 10:00:30 story with 1-minute latency fills at the 10:02 open, never 10:01.
    Rounding down here is the difference between a backtest and a wish.
    """
    target = published + timedelta(minutes=latency_min)
    open_dt = datetime.combine(published.date(), SESSION_OPEN)
    return math.ceil((target - open_dt).total_seconds() / 60.0)


def resolve_latency(entry: dict, mode: str, flat_min: float) -> tuple[float, str]:
    """Minutes from publication to the decision, and which clock produced them.

    `flat` is an assumption; the other two are measured, and they are what a live run would
    have paid:

      ingest   — `news.fetch_time - published`: the RSS poll only sees the story on its next
                 pass. This is the dominant term and it is invisible in `agent_results`.
      runtime  — `agent_results.execution_time_seconds`: the agent's own thinking time, which
                 is the thing "1 minute" was meant to stand for.
      realistic = ingest + runtime. Anything faster requires a faster feed, not a faster agent.

    Falls back to `flat` whenever a component is missing, and says so in the returned reason
    so the caller can count how much of the sample is assumption rather than measurement.
    """
    if mode == "flat":
        return flat_min, "flat"

    ingest = None
    if entry.get("fetch_time") is not None:
        ingest = (entry["fetch_time"] - entry["published"]).total_seconds() / 60.0
        if ingest < 0:
            ingest = None  # clock artefact; do not invent a negative delay

    runtime = entry["exec_seconds"] / 60.0 if entry.get("exec_seconds") is not None else None

    if mode == "ingest":
        return (ingest, "measured") if ingest is not None else (flat_min, "fallback_flat")
    if mode == "runtime":
        return (runtime, "measured") if runtime is not None else (flat_min, "fallback_flat")
    if mode == "realistic":
        if ingest is None or runtime is None:
            return flat_min, "fallback_flat"
        return ingest + runtime, "measured"
    raise ValueError(f"unknown latency mode: {mode}")


def load_grids(conn, ticker: str, days: set[date]) -> dict[date, tuple[np.ndarray, np.ndarray]]:
    """Per-day (prices, is_real) arrays of GRID_POINTS entries for one ticker.

    Gaps are forward-filled from the last print — that is what an exit would actually get —
    but `is_real` records which points were genuine bars, so an *entry* can insist on one.
    """
    lo, hi = min(days), max(days) + timedelta(days=1)
    with conn.cursor() as cur:
        # Range predicate first (it uses the ticker/timestamp index), then the exact-day
        # filter so a ticker with events months apart does not drag the whole span over.
        cur.execute(
            f"SELECT timestamp, open, close FROM {_table(ticker)} "  # noqa: S608 - name from a fixed table list
            "WHERE timestamp >= %s AND timestamp < %s AND timestamp::date = ANY(%s) "
            "ORDER BY timestamp",
            (datetime.combine(lo, SESSION_OPEN), datetime.combine(hi, SESSION_OPEN), sorted(days)),
        )
        rows = cur.fetchall()

    by_day: dict[date, list] = defaultdict(list)
    for ts, o, c in rows:
        if ts.date() in days:
            by_day[ts.date()].append((ts, o, c))

    grids = {}
    for day, bars in by_day.items():
        px = np.full(GRID_POINTS, np.nan)
        real = np.zeros(GRID_POINTS, dtype=bool)
        open_dt = datetime.combine(day, SESSION_OPEN)
        last_close = np.nan
        for ts, o, c in bars:
            k = int((ts - open_dt).total_seconds() // 60)
            if not 0 <= k < SESSION_BARS:
                continue  # extended-hours row; the tables should not have any, but do not trust it
            o = float(o) if o is not None else np.nan
            c = float(c) if c is not None else np.nan
            if not np.isfinite(o):
                o = c  # a bar with no open still tells us where it traded
            px[k] = o
            real[k] = np.isfinite(o)
            if np.isfinite(c):
                last_close = c
        # The final point is the 16:00 print: the close of the last real bar of the day.
        px[SESSION_BARS] = last_close
        real[SESSION_BARS] = np.isfinite(last_close)

        # forward-fill, then back-fill the leading gap so an early entry is not NaN-poisoned
        if not np.isfinite(px).any():
            continue
        px = pd.Series(px).ffill().bfill().to_numpy()
        grids[day] = (px, real)
    return grids


def load_market_grid(conn, universe: set[str], days: set[date]) -> dict[date, np.ndarray]:
    """Equal-weight intraday index built from the priced universe.

    We hold no ETF minute bars (no SPY/QQQ in `historical_data_*`), so the market factor has
    to be synthesised. Cross-sectional mean of per-minute returns, cumulated — good enough
    to strip the day's common move, which matters a lot when the sample is a few days wide.

    Aggregated server-side: 120 tickers × a multi-month span is ~5M bars over the wire, and
    all we need out of it is 390 numbers per day.
    """
    parts = " UNION ALL ".join(
        f"SELECT '{t}' AS tk, timestamp, open, close FROM {_table(t)} WHERE timestamp::date = ANY(%s)"
        for t in sorted(universe)
    )
    sql = f"""
        WITH bars AS ({parts}),
        px AS (
            SELECT tk, timestamp::date AS d, timestamp,
                   COALESCE(open, close)::float8 AS p
            FROM bars
        ),
        rets AS (
            SELECT d, timestamp,
                   p / NULLIF(lag(p) OVER (PARTITION BY tk, d ORDER BY timestamp), 0) - 1 AS r
            FROM px
        )
        SELECT d, timestamp, avg(r) FROM rets WHERE r IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2
    """  # noqa: S608 - table names come from information_schema, never user input
    day_list = sorted(days)
    with conn.cursor() as cur:
        cur.execute(sql, [day_list] * len(universe))
        rows = cur.fetchall()

    # A gap makes lag() span two minutes and land the return in one slot; with 390/390
    # coverage on these names that is rare, and it cannot bias a direction.
    per_day: dict[date, np.ndarray] = {d: np.zeros(GRID_POINTS) for d in day_list}
    for d, ts, r in rows:
        k = int((ts - datetime.combine(d, SESSION_OPEN)).total_seconds() // 60)
        if 0 <= k < GRID_POINTS and r is not None:
            per_day[d][k] = float(r)

    return {d: np.cumprod(1.0 + r) for d, r in per_day.items()}  # level, base 1.0 at the open


# ── the panel ────────────────────────────────────────────────────────────────────────


def build_panel(
    conn,
    entries: list[dict],
    horizons: list[int],
    latency_min: float,
    latency_mode: str,
    entry_tolerance: int,
    truncation: str,
    cost_bps: float,
    market_adjust: bool,
) -> tuple[pd.DataFrame, Counter]:
    """One row per (trade, horizon). `ret_pct` is signed by the trade's direction."""
    universe = _priced_universe(conn)
    dropped = Counter()

    tradable = []
    for e in entries:
        pub = e["published"]
        if not e["ticker"] or e["ticker"] not in universe:
            dropped["ticker_not_priced"] += 1
            continue
        if not (SESSION_OPEN <= pub.time() < SESSION_CLOSE):
            dropped["published_outside_session"] += 1
            continue
        tradable.append(e)

    # De-duplicate: the same story re-analysed by two agent versions is one trade, not two.
    seen, deduped = set(), []
    for e in sorted(tradable, key=lambda x: (x["published"], x["ticker"])):
        key = (e["news_id"], e["ticker"], e["side"])
        if key in seen:
            dropped["duplicate_news_ticker"] += 1
            continue
        seen.add(key)
        deduped.append(e)

    by_ticker: dict[str, set[date]] = defaultdict(set)
    for e in deduped:
        by_ticker[e["ticker"]].add(e["published"].date())

    grids: dict[str, dict] = {}
    for ticker, days in by_ticker.items():
        try:
            grids[ticker] = load_grids(conn, ticker, days)
        except Exception as exc:  # a table can exist and still be unreadable
            print(f"  ! {ticker}: {exc}")
            grids[ticker] = {}

    market = {}
    if market_adjust:
        all_days = {d for days in by_ticker.values() for d in days}
        print(f"Building equal-weight market grid over {len(universe)} tickers × {len(all_days)} days…")
        market = load_market_grid(conn, universe, all_days)

    max_h = max(horizons)
    rows = []
    for trade_id, e in enumerate(deduped):
        day = e["published"].date()
        grid = grids.get(e["ticker"], {}).get(day)
        if grid is None:
            dropped["no_bars_that_day"] += 1
            continue
        px, real = grid

        latency, latency_kind = resolve_latency(e, latency_mode, latency_min)
        if latency_kind == "fallback_flat":
            dropped["latency_fell_back_to_flat"] += 1

        want = _session_point(e["published"], latency)
        if want >= SESSION_BARS:
            # The decision landed after the last bar — with a realistic feed delay this is a
            # real outcome, not a data problem: the story was untradable that day.
            dropped["entry_after_close"] += 1
            continue
        # Search forward only — never fill at a price printed before the news.
        cand = [k for k in range(want, min(want + entry_tolerance + 1, SESSION_BARS)) if real[k]]
        if not cand:
            dropped["no_entry_bar"] += 1
            continue
        entry_pt = cand[0]
        entry_px = px[entry_pt]
        if not np.isfinite(entry_px) or entry_px <= 0:
            dropped["bad_entry_price"] += 1
            continue

        room = GRID_POINTS - 1 - entry_pt
        if truncation == "balanced" and room < max_h:
            dropped["balanced_no_room"] += 1
            continue

        mkt = market.get(day)
        entry_time = datetime.combine(day, SESSION_OPEN) + timedelta(minutes=entry_pt)

        for h in horizons:
            exit_pt = entry_pt + h
            if exit_pt > GRID_POINTS - 1:
                if truncation == "drop":
                    continue
                exit_pt = GRID_POINTS - 1  # clamp / balanced: exit at the 16:00 print
            exit_px = px[exit_pt]
            if not np.isfinite(exit_px):
                continue

            raw = (exit_px - entry_px) / entry_px
            if mkt is not None and np.isfinite(mkt[exit_pt]) and mkt[entry_pt] > 0:
                raw = raw - (mkt[exit_pt] / mkt[entry_pt] - 1.0)
            ret = e["side"] * raw * 100.0 - cost_bps / 100.0

            rows.append(
                {
                    "trade_id": trade_id,
                    "news_id": e["news_id"],
                    "day": day,
                    "ticker": e["ticker"],
                    "side": e["side"],
                    "confidence": e["confidence"],
                    "label": e["label"],
                    "published": e["published"],
                    "latency_min": round(latency, 2),
                    "latency_kind": latency_kind,
                    "entry_time": entry_time,
                    "entry_price": entry_px,
                    "horizon": h,
                    "exit_time": datetime.combine(day, SESSION_OPEN) + timedelta(minutes=exit_pt),
                    "exit_price": exit_px,
                    "held_min": exit_pt - entry_pt,
                    "censored": exit_pt - entry_pt < h,
                    "ret_pct": ret,
                    "raw_move_pct": (exit_px - entry_px) / entry_px * 100.0,
                    "title": (e["title"] or "")[:90],
                }
            )

    return pd.DataFrame(rows), dropped


# ── statistics ───────────────────────────────────────────────────────────────────────


def _block_bootstrap(df_h: pd.DataFrame, n_boot: int, rng: np.random.Generator) -> tuple[float, float]:
    """CI on the mean, resampling whole trading DAYS.

    Ledger item F10: trades inside one day are one bet, not many. A per-trade CI on a
    sample this day-concentrated is several times too narrow and will call noise a finding.
    """
    groups = [g["ret_pct"].to_numpy() for _, g in df_h.groupby("day")]
    if len(groups) < 2:
        return float("nan"), float("nan")
    means = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        means[b] = np.concatenate([groups[i] for i in pick]).mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _sign_flip_p(df_h: pd.DataFrame, n_perm: int, rng: np.random.Generator) -> float:
    """Two-sided p from flipping every trade's direction, one coin per DAY.

    Flipping per day rather than per trade keeps the within-day correlation intact, so the
    null has the same clumpiness as the data. Tests exactly the claim being made: that the
    *direction* carries information, not that the tape moved.
    """
    groups = [g["ret_pct"].to_numpy() for _, g in df_h.groupby("day")]
    if len(groups) < 2:
        return float("nan")
    observed = abs(np.concatenate(groups).mean())
    hits = 0
    for _ in range(n_perm):
        signs = rng.choice([-1.0, 1.0], len(groups))
        if abs(np.concatenate([g * s for g, s in zip(groups, signs)]).mean()) >= observed:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def summarise(panel: pd.DataFrame, n_boot: int, n_perm: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    out = []
    for h, g in panel.groupby("horizon"):
        rets = g["ret_pct"].to_numpy()
        lo, hi = _block_bootstrap(g, n_boot, rng)
        out.append(
            {
                "horizon_min": h,
                "n": len(g),
                "n_days": g["day"].nunique(),
                "n_censored": int(g["censored"].sum()),
                "mean_pct": rets.mean(),
                "median_pct": float(np.median(rets)),
                "win_rate": float((rets > 0).mean()),
                "std_pct": rets.std(ddof=1) if len(rets) > 1 else float("nan"),
                "ci_lo": lo,
                "ci_hi": hi,
                "p_signflip": _sign_flip_p(g, n_perm, rng),
                # Direction-blind control: what a coin-flip long on every signal would earn.
                # If the curve does not clear this, the agent is selling you market drift.
                "always_buy_mean_pct": g["raw_move_pct"].mean(),
            }
        )
    return pd.DataFrame(out).sort_values("horizon_min").reset_index(drop=True)


# ── output ───────────────────────────────────────────────────────────────────────────


def plot_curve(summary: pd.DataFrame, path: str, subtitle: str, xscale: str = "log") -> None:
    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(11, 9), sharex=True, gridspec_kw={"height_ratios": [3, 1.4]}
    )
    x = summary["horizon_min"]

    ax.fill_between(x, summary["ci_lo"], summary["ci_hi"], alpha=0.20, color="#2E86AB",
                    label="95% CI (day-block bootstrap)")
    ax.plot(x, summary["mean_pct"], "o-", color="#2E86AB", lw=2, label="mean profit, signed")
    ax.plot(x, summary["median_pct"], "s--", color="#6C757D", lw=1.2, ms=4, label="median")
    ax.plot(x, summary["always_buy_mean_pct"], ":", color="#C77DFF", lw=1.5,
            label="always-buy control (direction ignored)")
    ax.axhline(0, color="black", lw=1)
    ax.set_ylabel("Profit %")
    ax.set_title(f"Profit vs holding time\n{subtitle}", fontsize=11)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # n(h) as a step, not bars: on the log axis the short horizons sit on top of each other.
    ax2.step(x, summary["n"], where="mid", color="#6C757D", lw=1.5, label="trades at horizon")
    ax2.fill_between(x, 0, summary["n"], step="mid", color="#ADB5BD", alpha=0.45)
    ax2.set_ylim(0, summary["n"].max() * 1.15)
    ax2.set_ylabel("n trades")
    ax2.set_xlabel("Holding time, minutes (entry = news + latency)")
    ax2.grid(alpha=0.3)
    axw = ax2.twinx()
    axw.plot(x, summary["win_rate"] * 100, "^-", color="#28A745", ms=4, lw=1.2, label="win rate")
    axw.axhline(50, color="#28A745", ls=":", lw=1)
    axw.set_ylabel("win rate %", color="#28A745")
    axw.set_ylim(0, 100)

    lines = ax2.get_legend_handles_labels()[0] + axw.get_legend_handles_labels()[0]
    labels = ax2.get_legend_handles_labels()[1] + axw.get_legend_handles_labels()[1]
    ax2.legend(lines, labels, fontsize=8, loc="upper left")

    if xscale == "log":
        ax2.set_xscale("log")
        ticks = [h for h in (1, 2, 5, 10, 15, 30, 60, 120, 240, 390) if h <= x.max()]
        ax2.set_xticks(ticks)
        ax2.set_xticklabels([str(t) for t in ticks])
        ax2.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())

    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches="tight")
    print(f"chart  → {path}")
    plt.close(fig)


def print_composition(panel: pd.DataFrame, dropped: Counter) -> None:
    """The part of the output that decides whether the curve means anything."""
    print("\n" + "=" * 72)
    print("SAMPLE COMPOSITION")
    print("=" * 72)
    if dropped:
        print("dropped entries:")
        for reason, n in dropped.most_common():
            print(f"  {reason:26} {n}")
    if panel.empty:
        print("no trades survived")
        return

    trades = panel.drop_duplicates("trade_id")
    per_day = trades.groupby("day").size().sort_values(ascending=False)
    print(f"\ntrades {len(trades)} · days {trades['day'].nunique()} · tickers {trades['ticker'].nunique()}")
    print(f"window {trades['day'].min()} → {trades['day'].max()}")
    print(f"long/short {int((trades['side'] == 1).sum())}/{int((trades['side'] == -1).sum())}")
    top_day, top_n = per_day.index[0], per_day.iloc[0]
    share = top_n / len(trades)
    print(f"busiest day {top_day}: {top_n} trades ({share:.0%} of the sample)")
    if share > 0.25:
        print(f"  ⚠ one day carries {share:.0%} of the sample — the curve is largely that day's tape.")
    top_tk = trades.groupby("ticker").size().sort_values(ascending=False)
    print(f"top tickers: {', '.join(f'{t}×{n}' for t, n in top_tk.head(6).items())}")

    lat = trades["latency_min"]
    print(f"latency actually applied: p50 {lat.median():.1f}m · p90 {lat.quantile(0.9):.1f}m "
          f"· max {lat.max():.1f}m")
    assumed = int((trades["latency_kind"] != "measured").sum())
    if assumed:
        print(f"  {assumed}/{len(trades)} trades used the flat assumption, not a measured delay")
    if trades["day"].nunique() < 20:
        print(f"  ⚠ {trades['day'].nunique()} distinct days — the day-block CI is the only honest one.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=sorted(SOURCES), default="agent")
    ap.add_argument("--latency-mode", choices=("flat", "runtime", "ingest", "realistic"),
                    default="flat",
                    help="flat: --latency-min for every trade (an assumption); "
                         "runtime: the agent's measured execution_time_seconds; "
                         "ingest: the measured fetch_time - published feed delay; "
                         "realistic: ingest + runtime, what a live run would have paid")
    ap.add_argument("--latency-min", type=float, default=1.0,
                    help="flat latency, and the fallback when a measured component is missing")
    ap.add_argument("--horizons", default=",".join(map(str, DEFAULT_HORIZONS)),
                    help="comma-separated holding times in minutes")
    ap.add_argument("--truncation", choices=("drop", "clamp", "balanced"), default="drop",
                    help="drop: trade leaves the sample past 16:00 (n falls with h); "
                         "clamp: exit at the close (constant n, flattened tail); "
                         "balanced: only trades with room for the longest horizon")
    ap.add_argument("--entry-tolerance", type=int, default=5,
                    help="minutes to search FORWARD for the first real bar at entry")
    ap.add_argument("--cost-bps", type=float, default=0.0, help="round-trip cost in bps")
    ap.add_argument("--market-adjust", action="store_true",
                    help="subtract an equal-weight intraday index built from the priced universe")
    ap.add_argument("--min-confidence", type=float, default=0.0, help="agent source only")
    ap.add_argument("--agent-version", default=None, help="agent source only, e.g. 2.0.0")
    ap.add_argument("--exclude-days", default="", help="comma-separated YYYY-MM-DD to drop (robustness)")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--permutations", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--xscale", choices=("log", "linear"), default="log",
                    help="log spreads the short horizons out; linear shows the real spacing")
    ap.add_argument("--tag", default="", help="suffix for the output filenames")
    args = ap.parse_args()

    horizons = sorted({int(h) for h in args.horizons.split(",") if h.strip()})
    out_dir = os.path.dirname(os.path.abspath(__file__))
    stem = f"holding_curve_{args.source}{('_' + args.tag) if args.tag else ''}"

    lat_desc = f"{args.latency_min}m flat" if args.latency_mode == "flat" else args.latency_mode
    print(f"source={args.source} latency={lat_desc} truncation={args.truncation} "
          f"cost={args.cost_bps}bps market_adjust={args.market_adjust}")

    with get_conn() as conn:
        entries = SOURCES[args.source](conn, args.min_confidence, args.agent_version)
        print(f"entries from source: {len(entries)}")
        panel, dropped = build_panel(
            conn, entries, horizons, args.latency_min, args.latency_mode, args.entry_tolerance,
            args.truncation, args.cost_bps, args.market_adjust,
        )

    if args.exclude_days and not panel.empty:
        drop_days = {datetime.strptime(d.strip(), "%Y-%m-%d").date()
                     for d in args.exclude_days.split(",") if d.strip()}
        before = panel["trade_id"].nunique()
        panel = panel[~panel["day"].isin(drop_days)]
        print(f"excluded {sorted(drop_days)}: {before} → {panel['trade_id'].nunique()} trades")

    print_composition(panel, dropped)
    if panel.empty:
        return 1

    summary = summarise(panel, args.bootstrap, args.permutations, args.seed)

    print("\n" + "=" * 72)
    print("PROFIT vs HOLDING TIME")
    print("=" * 72)
    print(summary.to_string(index=False, float_format=lambda v: f"{v:8.3f}"))

    best = summary.loc[summary["mean_pct"].idxmax()]
    print(f"\nbest mean at {int(best['horizon_min'])}m: {best['mean_pct']:+.3f}% "
          f"(n={int(best['n'])}, CI [{best['ci_lo']:+.2f}, {best['ci_hi']:+.2f}], "
          f"p_signflip={best['p_signflip']:.3f})")
    print("  ⚠ best-of-N over the horizon grid is a selected maximum, not a discovery — "
          "read the pre-registered horizon, and treat this one as a hypothesis for a fresh window.")

    panel_path = os.path.join(out_dir, f"{stem}_trades.csv")
    summary_path = os.path.join(out_dir, f"{stem}_summary.csv")
    panel.to_csv(panel_path, index=False)
    summary.to_csv(summary_path, index=False)
    print(f"\npanel  → {panel_path}\nsummary→ {summary_path}")

    trades = panel.drop_duplicates("trade_id")
    subtitle = (f"source={args.source} · latency={lat_desc} "
                f"(p50 {trades['latency_min'].median():.1f}m) · "
                f"n={len(trades)} trades / {trades['day'].nunique()} days · "
                f"truncation={args.truncation}"
                + (" · market-adjusted" if args.market_adjust else "")
                + (f" · {args.cost_bps}bps round-trip" if args.cost_bps else ""))
    plot_curve(summary, os.path.join(out_dir, f"{stem}.png"), subtitle, xscale=args.xscale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
