"""
Assemble the final training table for the significance pre-filter (variant A).

Combines (all from existing data, no LLM):
  labels.parquet                 base pairs + per-pair z-label + split + t0
  news_text.parquet              title+summary  -> regex/lexical features
  graph_pair_features.parquet    per (news,ticker): affects conf/sentiment/fan-out
  graph_article_features.parquet per news: event-type taxonomy, diffuseness
  ticker price aggregates        per ticker: liquidity / price level / dispersion
  derived time-of-day/structural features

Output:
  significance/data/features_tabular.parquet   (pairs x [meta + tabular features])
  embeddings.npz is reused as-is (per-news, mapped by news_id at train time)
  prints a feature manifest grouped by family.
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
import psycopg2

from regex_featurizer import RegexFeaturizer

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "significance" / "data"
OUT = D / "features_tabular.parquet"
TICKER_CACHE = D / "ticker_features.parquet"
PRIMARY_LABEL = "label_z_30"
load_dotenv(ROOT / ".env")

OPEN_MIN = 9 * 60 + 30  # 09:30 ET


def ticker_price_features(tickers):
    if TICKER_CACHE.exists():
        cached = pd.read_parquet(TICKER_CACHE)
        if set(tickers).issubset(set(cached.ticker)):
            return cached
    conn = psycopg2.connect(host=os.getenv("DB_HOST"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
                            port=int(os.getenv("DB_PORT", 5432)), connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()
    rows = []
    for t in tickers:
        tbl = "historical_data_" + t.lower().replace(".", "_").replace("-", "_")
        try:
            cur.execute(f'SELECT avg(close), stddev_samp(close), avg(volume), count(*) FROM "{tbl}";')
            ac, sc, av, n = cur.fetchone()
            rows.append(dict(ticker=t, tk_price=float(ac or 0),
                             tk_disp=float((sc or 0) / ac) if ac else 0.0,   # coef. of variation ~ vol proxy
                             tk_log_vol=float(np.log1p(av or 0)), tk_nbars=int(n or 0)))
        except Exception as e:
            rows.append(dict(ticker=t, tk_price=0.0, tk_disp=0.0, tk_log_vol=0.0, tk_nbars=0))
    cur.close(); conn.close()
    out = pd.DataFrame(rows)
    out.to_parquet(TICKER_CACHE, index=False)
    return out


def main():
    labels = pd.read_parquet(D / "labels.parquet")
    labels["ticker"] = labels["ticker"].str.upper()
    df = labels[labels["valid_30"]].copy()
    print(f"[base] trainable pairs (valid@30): {len(df)}  | news: {df.news_id.nunique()}  | pos rate: {df[PRIMARY_LABEL].mean():.3f}")

    # --- regex / lexical on title+summary ---
    text = pd.read_parquet(D / "news_text.parquet")
    text["doc"] = (text["title"].fillna("") + ". " + text["summary"].fillna("")).str.slice(0, 1000)
    rf = RegexFeaturizer()
    reg = rf.transform_frame(text["doc"])
    reg.insert(0, "news_id", text["news_id"].values)
    print(f"[regex] features: {len(rf.feature_names)} (lexicon v{rf.version})")

    # --- graph features ---
    gp = pd.read_parquet(D / "graph_pair_features.parquet").drop(columns=["g_is_primary"], errors="ignore")
    gp["ticker"] = gp["ticker"].str.upper()
    ga = pd.read_parquet(D / "graph_article_features.parquet")

    # --- ticker price features ---
    tk = ticker_price_features(sorted(df.ticker.unique().tolist()))
    print(f"[ticker] price features for {len(tk)} tickers")

    # --- merges ---
    df = (df
          .merge(reg, on="news_id", how="left")
          .merge(gp, on=["news_id", "ticker"], how="left")
          .merge(ga, on="news_id", how="left")
          .merge(tk, on="ticker", how="left"))

    # has embedding?
    emb_ids = set(np.load(D / "embeddings.npz")["news_id"].tolist())
    df["has_emb"] = df["news_id"].isin(emb_ids).astype(int)

    # --- time-of-day / structural ---
    t0 = pd.to_datetime(df["t0"])
    mins = t0.dt.hour * 60 + t0.dt.minute - OPEN_MIN
    df["tod_mins_since_open"] = mins.clip(0, 390)
    df["tod_mins_to_close"] = (390 - mins).clip(0, 390)
    df["tod_first_hour"] = (mins < 60).astype(int)
    df["tod_last_hour"] = (mins > 330).astype(int)
    df["dow"] = t0.dt.dayofweek
    df["source_code"] = df["source"].astype("category").cat.codes

    # diffuseness as a feature (already have ga_n_companies, g_min_evt_companies, n_priced_tickers)
    df["log_n_companies"] = np.log1p(df["ga_n_companies"].fillna(df["n_priced_tickers"]))

    # --- feature manifest ---
    groups = {
        "regex": rf.feature_names,
        "graph_pair": ["g_affects_conf", "g_sentiment", "g_sent_net", "g_n_events",
                       "g_from_single_evt", "g_min_evt_companies"],
        "graph_article": [c for c in df.columns if c.startswith("ga_")],
        "ticker": ["tk_price", "tk_disp", "tk_log_vol"],
        "structural": ["tod_mins_since_open", "tod_mins_to_close", "tod_first_hour",
                       "tod_last_hour", "dow", "source_code", "n_priced_tickers", "log_n_companies"],
    }
    feat_cols = [c for g in groups.values() for c in g if c in df.columns]
    meta_cols = ["news_id", "ticker", "t0", "source", "split", "has_emb",
                 PRIMARY_LABEL, "label_abs1_30", "ret_30", "movez_30", "is_focus"]

    out = df[meta_cols + feat_cols].copy()
    # fill NA in features only
    out[feat_cols] = out[feat_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    out = out.rename(columns={PRIMARY_LABEL: "label"})
    out.to_parquet(OUT, index=False)

    print(f"\n[write] {OUT.name}: {out.shape}  ({len(feat_cols)} tabular features + {len(meta_cols)} meta)")
    print("\n=== feature manifest ===")
    for g, cols in groups.items():
        present = [c for c in cols if c in df.columns]
        print(f"  {g:<14} ({len(present)}): {present}")
    print(f"  embeddings    (1): emb[1536]  (per-news, in embeddings.npz)")

    print("\n=== label balance by split (valid@30, has_emb) ===")
    e = out[out.has_emb == 1]
    for sp in ["train", "val", "test"]:
        s = e[e.split == sp]
        print(f"  {sp:<6} pairs={len(s):>6}  pos={s.label.mean()*100:4.1f}%  news={s.news_id.nunique():>5}")
    print(f"  TOTAL (has_emb) pairs={len(e)} pos={e.label.mean()*100:.1f}%  | dropped no-emb pairs: {(out.has_emb==0).sum()}")


if __name__ == "__main__":
    main()
