"""
Load news text (title+summary) and pgvector embeddings for the labeled news set.

Output:
  significance/data/news_text.parquet   (news_id, title, summary)
  significance/data/embeddings.npz      (news_id: int[N], emb: float32[N, 1536])

Read-only on Postgres.
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
import psycopg2

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "significance" / "data" / "labels.parquet"
OUT_TEXT = ROOT / "significance" / "data" / "news_text.parquet"
OUT_EMB = ROOT / "significance" / "data" / "embeddings.npz"

load_dotenv(ROOT / ".env")


def parse_vec(s):
    if s is None:
        return None
    if isinstance(s, (list, tuple, np.ndarray)):
        return np.asarray(s, dtype="float32")
    # pgvector ::text form: "[0.1,0.2,...]"
    return np.fromstring(str(s).strip().lstrip("[").rstrip("]"), sep=",", dtype="float32")


def main():
    news_ids = sorted(pd.read_parquet(LABELS, columns=["news_id"])["news_id"].unique().tolist())
    print(f"[pg] loading text+embeddings for {len(news_ids)} news ...")

    conn = psycopg2.connect(host=os.getenv("DB_HOST"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
                            port=int(os.getenv("DB_PORT", 5432)), connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET statement_timeout='120s';")
    cur.execute("""SELECT id, title, summary, embedding::text
                   FROM news WHERE id = ANY(%s);""", (news_ids,))
    rows = cur.fetchall()
    cur.close(); conn.close()

    ids, titles, summaries, vecs = [], [], [], []
    bad = 0
    dim = None
    for nid, title, summary, emb in rows:
        v = parse_vec(emb)
        if v is None or v.size == 0:
            bad += 1
            continue
        dim = dim or v.size
        if v.size != dim:
            bad += 1
            continue
        ids.append(int(nid)); titles.append(title or ""); summaries.append(summary or ""); vecs.append(v)

    ids = np.array(ids, dtype="int64")
    emb = np.vstack(vecs).astype("float32")
    pd.DataFrame({"news_id": ids, "title": titles, "summary": summaries}).to_parquet(OUT_TEXT, index=False)
    np.savez_compressed(OUT_EMB, news_id=ids, emb=emb)

    norms = np.linalg.norm(emb, axis=1)
    print(f"[write] {OUT_TEXT.name}: {len(ids)} rows")
    print(f"[write] {OUT_EMB.name}: emb shape={emb.shape} dim={dim}  (skipped {bad})")
    print(f"  vec norm: mean={norms.mean():.3f} min={norms.min():.3f} max={norms.max():.3f}  "
          f"(≈1.0 => normalized)")
    print(f"  coverage: {len(ids)}/{len(news_ids)} labeled news have embeddings "
          f"({len(ids)*100//len(news_ids)}%)")


if __name__ == "__main__":
    main()
