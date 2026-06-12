"""
Phase 0 — Significance labeling (NO LLM / NO agent re-run).

Builds a labeled dataset for the significance pre-filter classifier by joining
existing data only:
  - article -> ticker mapping  : Neo4j graph  (Event)-[:SOURCED_FROM]->(Article)
                                                (Event)-[:AFFECTS]->(Company)
  - 1-minute prices (label)    : Postgres  historical_data_<ticker>
  - news text / source / t0    : Postgres  news

Only news published DURING regular trading hours is labeled; after-hours /
pre-market / weekend news is discarded (no live price at t0 — its reaction is the
open-auction gap, which conflates with open volatility). See GAP_MAX_MIN.

Label = price move over W *trading* minutes after publication (window must close
within the same session — overnight spill is treated as invalid).
Primary label  : volatility-normalized |move| >= Z_K * sigma_ticker(W)   (default Z_K=2)
Sanity labels  : absolute |return| >= 0.5% / 1% / 2%
Windows        : W in {15, 30, 60} trading minutes (computed in parallel)

Output: significance/data/labels.parquet  (one row per (news_id, ticker))
Plus an EDA summary printed to stdout (base rate / move distribution / split / tz sanity).

Read-only on both DBs; writes only the local parquet.
"""
import os
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
from dotenv import load_dotenv
import psycopg2
from neo4j import GraphDatabase

# ----------------------------- config -----------------------------
ROOT = Path("/Users/lebedevmatvey/test")
OUT = ROOT / "significance" / "data" / "labels.parquet"
WINDOWS = [15, 30, 60]            # trading minutes (= bars)
Z_K = 2.0                         # primary threshold: |move| >= Z_K * sigma
ABS_THRESHOLDS = {"abs05": 0.005, "abs1": 0.01, "abs2": 0.02}
FOCUS_MAX_TICKERS = 5             # "focused" article = maps to <= this many priced tickers
GAP_MAX_MIN = 3                   # keep ONLY news published during trading hours
                                  # (first price bar within this many minutes of t0);
                                  # after-hours / pre-market / weekend news is discarded.
PRIMARY_W = 30                    # window used for the headline `label` column
NS_PER_MIN = 60_000_000_000
NS_PER_DAY = 86_400_000_000_000

load_dotenv(ROOT / ".env")


def pg_connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), port=int(os.getenv("DB_PORT", 5432)), connect_timeout=10,
    )


def table_for(ticker: str) -> str:
    return "historical_data_" + ticker.lower().replace(".", "_").replace("-", "_")


# ----------------------------- 1. graph mapping -----------------------------
def load_graph_mapping():
    drv = GraphDatabase.driver(
        os.getenv("NEO4J_URI"), auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
        connection_timeout=10)
    drv.verify_connectivity()
    with drv.session() as s:
        rows = list(s.run("""
            MATCH (a:Article)<-[:SOURCED_FROM]-(:Event)-[:AFFECTS]->(c:Company)
            RETURN a.news_id AS nid, collect(DISTINCT c.ticker) AS tickers"""))
    drv.close()
    mapping = {}
    for r in rows:
        try:
            nid = int(r["nid"])
        except (TypeError, ValueError):
            continue
        tks = sorted({str(t).upper().strip() for t in r["tickers"] if t})
        if tks:
            mapping[nid] = tks
    print(f"[graph] articles with >=1 company: {len(mapping)}")
    return mapping


# ----------------------------- 2. priced universe + news meta -----------------------------
def load_priced_tables(cur):
    cur.execute("""SELECT table_name FROM information_schema.tables
                   WHERE table_schema='public' AND table_name LIKE 'historical_data_%';""")
    return {t[0] for t in cur.fetchall()}


def load_news_meta(cur, news_ids):
    cur.execute("""SELECT id, published, source, (embedding IS NOT NULL) AS has_emb, title
                   FROM news WHERE id = ANY(%s);""", (list(news_ids),))
    meta = {}
    for nid, published, source, has_emb, title in cur.fetchall():
        if published is None:
            continue
        meta[int(nid)] = dict(t0=pd.Timestamp(published), source=source,
                              has_emb=bool(has_emb), title=title or "")
    return meta


# ----------------------------- 3. price loader + sigma + market index -----------------------------
def load_prices(cur, table):
    cur.execute(f'SELECT "timestamp", close FROM "{table}" ORDER BY "timestamp";')
    data = cur.fetchall()
    if not data:
        return None
    ts = np.array([d[0] for d in data], dtype="datetime64[ns]").astype("int64")
    close = np.array([float(d[1]) for d in data], dtype="float64")
    order = np.argsort(ts, kind="stable")
    return ts[order], close[order]


def sigma_per_window(ts, close):
    """std of W-bar simple returns, excluding steps that cross a calendar day (overnight)."""
    day = ts // NS_PER_DAY
    out = {}
    for W in WINDOWS:
        if len(close) <= W:
            out[W] = np.nan
            continue
        ret = close[W:] / close[:-W] - 1.0
        same_day = day[W:] == day[:-W]
        vals = ret[same_day & np.isfinite(ret)]
        out[W] = float(np.std(vals)) if vals.size > 50 else np.nan
    return out


def build_market_index(price_cache):
    """Equal-weight index from all loaded priced tickers (clean market proxy for focused news)."""
    cols = {}
    for tkr, (ts, close) in price_cache.items():
        s = pd.Series(close, index=pd.to_datetime(ts))
        s = s[~s.index.duplicated(keep="last")]
        if len(s) and s.iloc[0] > 0:
            cols[tkr] = s / s.iloc[0]
    if not cols:
        return None, None
    df = pd.DataFrame(cols).sort_index().ffill()
    lvl = df.mean(axis=1, skipna=True).values.astype("float64")
    mts = df.index.values.astype("int64")
    print(f"[market] equal-weight index built on {len(mts)} minute-bars from {len(cols)} tickers")
    return mts, lvl


# ----------------------------- 4. label computation -----------------------------
def compute_labels(mapping, meta, priced_tables, cur):
    # candidate (news_id, ticker) restricted to priced universe
    cand = defaultdict(list)          # ticker -> list of news_id
    n_priced = {}                     # news_id -> #priced tickers
    for nid, tks in mapping.items():
        if nid not in meta:
            continue
        priced_hits = [t for t in tks if table_for(t) in priced_tables]
        if not priced_hits:
            continue
        n_priced[nid] = len(priced_hits)
        for t in priced_hits:
            cand[t].append(nid)
    needed = sorted(cand.keys())
    print(f"[cand] priced tickers needed: {len(needed)}  | candidate articles: {len(n_priced)}")

    # load prices once per needed ticker
    price_cache, sigma_cache = {}, {}
    for i, tkr in enumerate(needed, 1):
        pr = load_prices(cur, table_for(tkr))
        if pr is None:
            continue
        price_cache[tkr] = pr
        sigma_cache[tkr] = sigma_per_window(*pr)
        if i % 25 == 0:
            print(f"  loaded prices {i}/{len(needed)} ...")
    print(f"[prices] loaded {len(price_cache)} ticker series")

    mts, mlvl = build_market_index(price_cache)

    rows = []
    for tkr, nids in cand.items():
        if tkr not in price_cache:
            continue
        ts, close = price_cache[tkr]
        n = len(ts)
        day = ts // NS_PER_DAY
        sig = sigma_cache[tkr]
        nids = [nid for nid in nids if nid in meta]
        t0s = np.array([meta[nid]["t0"].value for nid in nids], dtype="int64")

        idx_e = np.searchsorted(ts, t0s, side="left")
        entry_valid = idx_e < n
        idx_e_c = np.clip(idx_e, 0, n - 1)
        entry_ts = ts[idx_e_c]
        entry_px = close[idx_e_c]
        entry_gap_min = (entry_ts - t0s) / NS_PER_MIN

        # market entry level
        if mts is not None:
            mie = np.clip(np.searchsorted(mts, entry_ts, side="left"), 0, len(mts) - 1)
            m_entry = mlvl[mie]

        per_w = {}
        for W in WINDOWS:
            idx_x = idx_e + W
            valid = entry_valid & (idx_x < n)
            idx_x_c = np.clip(idx_x, 0, n - 1)
            exit_ts = ts[idx_x_c]
            exit_px = close[idx_x_c]
            ret = np.where(valid, exit_px / entry_px - 1.0, np.nan)
            overnight = valid & (day[idx_x_c] != day[idx_e_c])
            sW = sig.get(W, np.nan)
            movez = ret / sW if (sW and np.isfinite(sW) and sW > 0) else np.full_like(ret, np.nan)
            if mts is not None:
                mix = np.clip(np.searchsorted(mts, exit_ts, side="left"), 0, len(mts) - 1)
                mret = np.where(valid, mlvl[mix] / m_entry - 1.0, np.nan)
            else:
                mret = np.full_like(ret, np.nan)
            per_w[W] = dict(ret=ret, movez=movez, mret=mret, exc=ret - mret,
                            overnight=overnight, valid=valid)

        for j, nid in enumerate(nids):
            # DROP news published outside trading hours (no live price at t0 within GAP_MAX_MIN)
            if not (entry_valid[j] and entry_gap_min[j] <= GAP_MAX_MIN):
                continue
            row = dict(news_id=nid, ticker=tkr, t0=meta[nid]["t0"], source=meta[nid]["source"],
                       has_emb=meta[nid]["has_emb"], title=meta[nid]["title"][:200],
                       n_priced_tickers=n_priced[nid], is_focus=n_priced[nid] <= FOCUS_MAX_TICKERS,
                       entry_gap_min=float(entry_gap_min[j]))
            for W in WINDOWS:
                pw = per_w[W]
                # valid only if the W-window completes within the same session (no overnight gap)
                vW = bool(pw["valid"][j] and not pw["overnight"][j])
                row[f"ret_{W}"] = pw["ret"][j]
                row[f"abs_ret_{W}"] = abs(pw["ret"][j]) if np.isfinite(pw["ret"][j]) else np.nan
                row[f"mret_{W}"] = pw["mret"][j]
                row[f"exc_{W}"] = pw["exc"][j]
                row[f"movez_{W}"] = pw["movez"][j]
                row[f"overnight_{W}"] = bool(pw["overnight"][j])
                row[f"valid_{W}"] = vW
                row[f"label_z_{W}"] = bool(vW and np.isfinite(pw["movez"][j])
                                          and abs(pw["movez"][j]) >= Z_K)
                for name, thr in ABS_THRESHOLDS.items():
                    row[f"label_{name}_{W}"] = bool(vW and np.isfinite(pw["ret"][j])
                                                    and abs(pw["ret"][j]) >= thr)
            rows.append(row)

    return pd.DataFrame(rows)


# ----------------------------- 5. temporal split -----------------------------
def add_temporal_split(df):
    per_news = df[["news_id", "t0"]].drop_duplicates().sort_values("t0")
    q70, q85 = per_news["t0"].quantile([0.70, 0.85])
    def lab(t):
        return "train" if t <= q70 else ("val" if t <= q85 else "test")
    split_map = {nid: lab(t) for nid, t in zip(per_news["news_id"], per_news["t0"])}
    df["split"] = df["news_id"].map(split_map)
    df["label"] = df[f"label_z_{PRIMARY_W}"]
    return df, (q70, q85)


# ----------------------------- 6. EDA -----------------------------
def eda(df, split_bounds):
    P = print
    foc = df[df.is_focus]
    P("\n" + "=" * 64)
    P("EDA SUMMARY")
    P("=" * 64)
    P(f"rows (news,ticker)        : {len(df)}")
    P(f"unique news               : {df.news_id.nunique()}")
    P(f"unique tickers            : {df.ticker.nunique()}")
    P(f"focus rows (<= {FOCUS_MAX_TICKERS} tickers): {len(foc)}  ({len(foc)*100//max(len(df),1)}%)")
    P(f"with embedding            : {int(df.has_emb.sum())}")

    P("\n-- tz / alignment sanity (t0 hour-of-day, ET expected ~9-16) --")
    hod = df.drop_duplicates('news_id').t0.dt.hour.value_counts().sort_index()
    P("  " + ", ".join(f"{h:02d}h:{c}" for h, c in hod.items()))
    gap = df["entry_gap_min"].dropna()
    P(f"  entry_gap_min: median={gap.median():.1f}  <=2min={ (gap<=2).mean()*100:.0f}%  "
      f"<=30min={(gap<=30).mean()*100:.0f}%   (small gap => intraday news, aligned tz)")

    P("\n-- base rate by label definition (FOCUS subset) --")
    P(f"  {'W':>4} | {'valid':>6} | {'label_z(2σ)':>11} | {'abs>=0.5%':>9} | {'abs>=1%':>8} | {'abs>=2%':>8} | overnight")
    for W in WINDOWS:
        v = foc[foc[f"valid_{W}"]]
        if len(v) == 0:
            continue
        P(f"  {W:>4} | {len(v):>6} | {v[f'label_z_{W}'].mean()*100:>10.1f}% | "
          f"{v[f'label_abs05_{W}'].mean()*100:>8.1f}% | {v[f'label_abs1_{W}'].mean()*100:>7.1f}% | "
          f"{v[f'label_abs2_{W}'].mean()*100:>7.1f}% | {v[f'overnight_{W}'].mean()*100:>4.0f}%")

    P("\n-- base rate ALL vs FOCUS (primary label_z @ W=%d) --" % PRIMARY_W)
    for name, sub in [("ALL", df), ("FOCUS", foc)]:
        v = sub[sub[f"valid_{PRIMARY_W}"]]
        if len(v):
            P(f"  {name:<6} pair-level : {v['label'].mean()*100:5.1f}%  (n={len(v)})")
    art = foc[foc[f"valid_{PRIMARY_W}"]].groupby("news_id")["label"].max()
    if len(art):
        P(f"  FOCUS article-level (any ticker moved): {art.mean()*100:.1f}%  (n={len(art)})")

    P(f"\n-- abs_ret_{PRIMARY_W} distribution, FOCUS valid (percentiles) --")
    a = foc[foc[f"valid_{PRIMARY_W}"]][f"abs_ret_{PRIMARY_W}"].dropna()
    if len(a):
        for p in [50, 75, 90, 95, 99]:
            P(f"  p{p:<2}: {np.percentile(a, p)*100:.2f}%")

    P("\n-- split counts (FOCUS) + base rate --")
    P(f"  bounds: train<= {split_bounds[0]:%Y-%m-%d}  val<= {split_bounds[1]:%Y-%m-%d}")
    for sp in ["train", "val", "test"]:
        v = foc[(foc.split == sp) & (foc[f"valid_{PRIMARY_W}"])]
        if len(v):
            P(f"  {sp:<6}: n={len(v):>6}  base_rate={v['label'].mean()*100:.1f}%")

    P("\n-- base rate by source (FOCUS, primary label, top 8) --")
    v = foc[foc[f"valid_{PRIMARY_W}"]]
    g = v.groupby("source")["label"].agg(["mean", "count"]).sort_values("count", ascending=False).head(8)
    for src, r in g.iterrows():
        P(f"  {str(src):<22} base_rate={r['mean']*100:5.1f}%  n={int(r['count'])}")


def main():
    pg = pg_connect()
    pg.autocommit = True
    cur = pg.cursor()
    cur.execute("SET statement_timeout='180s';")

    mapping = load_graph_mapping()
    priced_tables = load_priced_tables(cur)
    print(f"[pg] priced tables: {len(priced_tables)}")
    meta = load_news_meta(cur, set(mapping.keys()))
    print(f"[pg] news meta loaded: {len(meta)}")

    df = compute_labels(mapping, meta, priced_tables, cur)
    cur.close(); pg.close()
    if df.empty:
        print("NO ROWS — aborting"); sys.exit(1)

    df, bounds = add_temporal_split(df)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"\n[write] {OUT}  ({len(df)} rows, {len(df.columns)} cols)")
    eda(df, bounds)


if __name__ == "__main__":
    main()
