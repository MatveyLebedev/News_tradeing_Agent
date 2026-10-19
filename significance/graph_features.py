"""
Graph feature extractor (Neo4j) — pair-level and article-level features, no LLM.

Per (news_id, ticker)  [pair_df]:
  g_affects_conf      max AFFECTS.confidence linking this news -> this ticker
  g_sentiment         sentiment of the max-confidence AFFECTS edge (+1/0/-1)
  g_sent_net          sum of signed sentiments across linking events
  g_n_events          # distinct events linking this news -> this ticker
  g_from_single_evt   1 if any linking event affects exactly 1 company
  g_min_evt_companies min over linking events of that event's company fan-out
  g_is_primary        1 if argmax g_affects_conf within the news (tie -> single-evt)

Per news_id  [article_df]:
  ga_n_events, ga_n_companies, ga_max_conf
  ga_type_<etype>     # distinct events of each Event.type
  ga_has_macro        1 if any market_move/macro_economic/geopolitical/trade_policy/commodity event

Output: significance/data/graph_pair_features.parquet, graph_article_features.parquet
Read-only on Neo4j.
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "significance" / "data" / "labels.parquet"
OUT_PAIR = ROOT / "significance" / "data" / "graph_pair_features.parquet"
OUT_ART = ROOT / "significance" / "data" / "graph_article_features.parquet"

MACRO_TYPES = {"market_move", "macro_economic", "geopolitical_conflict",
               "trade_policy", "commodity_price"}
SENT_MAP = {"positive": 1, "bullish": 1, "negative": -1, "bearish": -1,
            "neutral": 0, None: 0, "": 0}

load_dotenv(ROOT / ".env")


def fetch_edges(news_ids):
    drv = GraphDatabase.driver(os.getenv("NEO4J_URI"),
                               auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
                               connection_timeout=15)
    drv.verify_connectivity()
    q = """
    MATCH (a:Article)<-[:SOURCED_FROM]-(e:Event)-[r:AFFECTS]->(c:Company)
    WHERE a.news_id IN $ids
    RETURN a.news_id AS nid, toUpper(c.ticker) AS ticker, e.id AS eid,
           e.type AS etype, r.confidence AS conf, r.sentiment AS sent
    """
    with drv.session() as s:
        rows = [dict(r) for r in s.run(q, ids=list(news_ids))]
    drv.close()
    df = pd.DataFrame(rows)
    df["conf"] = pd.to_numeric(df["conf"], errors="coerce").fillna(0.0)
    df["sent_val"] = df["sent"].str.lower().map(SENT_MAP).fillna(0).astype(int)
    return df


def build_pair_features(edges):
    # company fan-out per event (true, across all its companies in the result)
    evt_companies = edges.groupby("eid")["ticker"].nunique().rename("evt_n_companies")
    e = edges.join(evt_companies, on="eid")

    g = e.groupby(["nid", "ticker"])
    pair = pd.DataFrame({
        "g_affects_conf": g["conf"].max(),
        "g_n_events": g["eid"].nunique(),
        "g_sent_net": g["sent_val"].sum(),
        "g_from_single_evt": g["evt_n_companies"].min().le(1).astype(int),
        "g_min_evt_companies": g["evt_n_companies"].min(),
    }).reset_index()

    # sentiment of the max-confidence edge per (nid,ticker)
    idx = e.sort_values("conf").groupby(["nid", "ticker"]).tail(1)[["nid", "ticker", "sent_val"]]
    pair = pair.merge(idx.rename(columns={"sent_val": "g_sentiment"}), on=["nid", "ticker"], how="left")

    # is_primary: argmax confidence within the article (tie-break -> single-event member)
    pair["_rank"] = pair.groupby("nid")["g_affects_conf"].rank(method="max", ascending=False)
    pair["g_is_primary"] = ((pair["_rank"] == 1) | (pair["g_from_single_evt"] == 1)).astype(int)
    pair = pair.drop(columns="_rank").rename(columns={"nid": "news_id"})
    return pair


def build_article_features(edges):
    art = edges.groupby("nid").agg(
        ga_n_events=("eid", "nunique"),
        ga_n_companies=("ticker", "nunique"),
        ga_max_conf=("conf", "max"),
    )
    # event-type counts (distinct events per type)
    tcounts = (edges.drop_duplicates(["nid", "eid"])
               .groupby(["nid", "etype"])["eid"].nunique().unstack(fill_value=0))
    tcounts.columns = [f"ga_type_{c}" for c in tcounts.columns]
    art = art.join(tcounts, how="left").fillna(0)
    macro_cols = [f"ga_type_{t}" for t in MACRO_TYPES if f"ga_type_{t}" in art.columns]
    art["ga_has_macro"] = (art[macro_cols].sum(axis=1) > 0).astype(int) if macro_cols else 0
    return art.reset_index().rename(columns={"nid": "news_id"})


def main():
    labels = pd.read_parquet(LABELS, columns=["news_id"])
    news_ids = sorted(labels["news_id"].unique().tolist())
    print(f"[graph] fetching edges for {len(news_ids)} news ...")
    edges = fetch_edges(news_ids)
    print(f"[graph] edge rows (news,event,company): {len(edges)} | "
          f"news covered: {edges['nid'].nunique()} | events: {edges['eid'].nunique()}")

    pair = build_pair_features(edges)
    art = build_article_features(edges)
    pair.to_parquet(OUT_PAIR, index=False)
    art.to_parquet(OUT_ART, index=False)
    print(f"[write] {OUT_PAIR.name}: {pair.shape}  |  {OUT_ART.name}: {art.shape}")

    print("\n-- pair features sample --")
    print(pair.head(6).to_string(index=False))
    print("\n-- is_primary rate:", round(pair['g_is_primary'].mean(), 3),
          "| single-evt rate:", round(pair['g_from_single_evt'].mean(), 3))
    print("-- sentiment dist (max-conf edge):",
          pair['g_sentiment'].value_counts(normalize=True).round(3).to_dict())
    print("\n-- article event-type columns:",
          [c for c in art.columns if c.startswith("ga_type_")])
    print("-- ga_has_macro rate:", round(art['ga_has_macro'].mean(), 3))


if __name__ == "__main__":
    main()
