"""Build the eval datasets from the live DB → evals/datasets/*.jsonl (versioned in git).

Rebuild deliberately (datasets are frozen test fixtures, not live views):
    python -m evals.build_datasets [--tickers ...] [--lookback 150]

bucket_map ground truth: the majority graph-AFFECTS sentiment of a week's articles,
kept only where it is unambiguous (≥ MIN_LABELED labeled articles, ≥ AGREEMENT
majority). The dataset then STRIPS the graph labels, so at eval time the map model
sees headlines only and cannot read the answer off its prompt seed.

synthesis_matrix: 8 synthetic canonical cell cases (full coverage from day one) +
frozen real states reconstructed from longterm_agent_results rows (stored artifacts +
PIT re-reads of fundamentals/technicals at the row's as_of — reproducible by design).

thesis_quality: real (inputs, thesis) pairs from directional results rows; inputs are
the stored reasoning artifacts the synthesis actually saw.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from core.settings import get_settings
from longterm_agent.company_names import COMPANY_NAMES
from longterm_agent.agent.nodes import _analog_desc
from longterm_agent.tools.news_corpus_tool import get_company_news_corpus, bucket_by_week
from longterm_agent.tools.fundamentals_tool import read_fundamentals
from longterm_agent.tools.technical_tool import compute_daily_technicals

DATASETS = Path(__file__).parent / "datasets"
SENT_SIGN = {"positive": 1, "bullish": 1, "negative": -1, "bearish": -1}
MIN_LABELED = 4      # labeled articles needed before a week is trusted as ground truth
MIN_ARTICLES = 5     # total articles so the map model has something to read
AGREEMENT = 0.75     # majority share among non-neutral labels
MAX_BUCKET_CASES = 50


def _db():
    return SimpleNamespace(db_config=get_settings().db_config())


def _write(name: str, rows: list):
    DATASETS.mkdir(exist_ok=True)
    path = DATASETS / f"{name}.jsonl"
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")
    print(f"  {name}: {len(rows)} cases → {path}")


# --- bucket_map ---------------------------------------------------------------
def build_bucket_map(tickers, lookback, neo4j_manager):
    db = _db()
    as_of = datetime.now()
    per_ticker = {}
    for tk in tickers:
        corpus = get_company_news_corpus(tk, as_of, lookback, db, neo4j_manager)
        arts = {a["id"]: a for a in corpus["articles"]}
        cases = []
        for b in bucket_by_week(corpus["articles"], as_of, lookback, 7):
            signs = [SENT_SIGN.get(s, 0) for s in b["affects_sentiments"]]
            signs = [s for s in signs if s]  # non-neutral only
            if b["count"] < MIN_ARTICLES or len(signs) < MIN_LABELED:
                continue
            pos = sum(1 for s in signs if s > 0)
            share = max(pos, len(signs) - pos) / len(signs)
            if share < AGREEMENT:
                continue
            cases.append({
                "id": f"{tk}_{b['start'].date()}",
                "ticker": tk,
                "week_start": b["start"].isoformat(),
                "week_end": b["end"].isoformat(),
                "count": b["count"],
                # graph labels intentionally NOT shipped (see module docstring)
                "articles": [{"id": aid, "title": arts[aid].get("title"),
                              "summary": arts[aid].get("summary")}
                             for aid in b["article_ids"] if aid in arts],
                "expected_sign": 1 if pos > len(signs) - pos else -1,
                "label_source": f"graph_affects_majority (n={len(signs)}, share={share:.2f})",
            })
        per_ticker[tk] = cases
    # round-robin across tickers so no single name dominates the metric
    out, i = [], 0
    while len(out) < MAX_BUCKET_CASES and any(per_ticker.values()):
        for tk in list(per_ticker):
            if per_ticker[tk]:
                out.append(per_ticker[tk].pop(0))
                if len(out) >= MAX_BUCKET_CASES:
                    break
        i += 1
        if i > 60:
            break
    _write("bucket_map", out)


# --- synthesis_matrix ----------------------------------------------------------
def _synth_state(ticker, direction, z, verdict, summary, rev_yoy, pe, has_shifted=True):
    """A minimal-but-complete frozen state for synthesis_node."""
    return {
        "ticker": ticker, "as_of": "2026-06-15",
        "background_profile": {"summary": summary},
        "background_shift": {
            "has_shifted": has_shifted, "direction": direction if has_shifted else None,
            "magnitude_z": z if has_shifted else 0.0, "shift_score": 0.8 if has_shifted else 0.0,
            "reasoning": summary},
        "fundamentals": {
            "trend": {"verdict": verdict, "reasoning": f"fundamentals {verdict} the shift"},
            "revenue_yoy": rev_yoy, "valuation": {"trailingPE": pe, "forwardPE": pe},
            "expectation_gap": None},
        "technical_longterm": {"trend": "mixed", "return_window_pct": 1.0},
        "shift_analogs": {"n_analogs": 0, "insufficient": True,
                          "fundamentals_matched_note": "cold start: no graded analogs yet"},
        "enriched_analysis": {},
    }


SYNTHETIC_CASES = [
    # full canonical cell coverage, independent of accumulated history
    ("syn_pos_confirms", _synth_state("AAA", "positive", 1.8, "confirms",
        "Sustained positive coverage: strong product demand, upbeat guidance.", 0.25, 22)),
    ("syn_neg_confirms", _synth_state("BBB", "negative", -1.7, "confirms",
        "Persistent negative coverage: demand deterioration, guidance cut.", -0.10, 35)),
    ("syn_neg_contradicts", _synth_state("CCC", "negative", -1.5, "contradicts",
        "Negative news wave: lawsuit headlines dominate coverage.", 0.20, 18)),
    ("syn_pos_contradicts", _synth_state("DDD", "positive", 1.6, "contradicts",
        "Hype wave: AI partnership rumors dominate coverage.", -0.05, 60)),
    ("syn_no_shift_good_fund", _synth_state("EEE", None, 0.0, "ignores",
        "Quiet, mildly positive routine coverage.", 0.15, 25, has_shifted=False)),
    ("syn_no_shift_flat", _synth_state("FFF", None, 0.0, "unknown",
        "Sparse mixed coverage, no dominant narrative.", 0.02, 20, has_shifted=False)),
    ("syn_pos_confirms_cheap", _synth_state("GGG", "positive", 1.4, "confirms",
        "Positive shift: order backlog growth, margin expansion.", 0.12, 11)),
    ("syn_neg_confirms_expensive", _synth_state("HHH", "negative", -1.9, "confirms",
        "Negative shift: churn spike, discounting pressure.", -0.02, 48)),
]


def build_synthesis_matrix():
    db = _db()
    rows = [{"id": cid, "source": "synthetic", "state": st,
             "cell": {"has_shifted": st["background_shift"]["has_shifted"],
                      "direction": st["background_shift"]["direction"],
                      "verdict": st["fundamentals"]["trend"]["verdict"]}}
            for cid, st in SYNTHETIC_CASES]

    import psycopg2
    conn = psycopg2.connect(**get_settings().db_config())
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, as_of, background_profile, background_shift,
                   fundamentals_summary, shift_analogs
            FROM longterm_agent_results
            WHERE verdict <> 'NO_DATA' ORDER BY as_of, ticker
        """)
        db_rows = cur.fetchall()
    conn.close()

    for ticker, as_of, profile, shift, trend, analogs in db_rows:
        shift = shift or {}
        trend = trend or {}
        verdict = trend.get("verdict")
        has_shifted = bool(shift.get("has_shifted"))
        if has_shifted and verdict not in ("confirms", "contradicts"):
            continue  # cell not rule-checkable
        as_of_dt = datetime(as_of.year, as_of.month, as_of.day)
        fund = dict(read_fundamentals(_db(), ticker, as_of_dt))  # PIT → reproducible
        fund["trend"] = trend
        tech = compute_daily_technicals(db, ticker, as_of_dt, 180)
        rows.append({
            "id": f"res_{ticker}_{as_of}", "source": "results",
            "state": {
                "ticker": ticker, "as_of": str(as_of),
                "background_profile": profile or {}, "background_shift": shift,
                "fundamentals": fund, "technical_longterm": tech,
                "shift_analogs": analogs or {}, "enriched_analysis": {},
            },
            "cell": {"has_shifted": has_shifted,
                     "direction": shift.get("direction"), "verdict": verdict},
        })
    _write("synthesis_matrix", rows)


# --- thesis_quality --------------------------------------------------------------
def build_thesis_quality():
    import psycopg2
    conn = psycopg2.connect(**get_settings().db_config())
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ticker, as_of, background_profile, background_shift,
                   fundamentals_summary, shift_analogs, signal
            FROM longterm_agent_results
            WHERE verdict IN ('LONG','SHORT') AND signal->>'thesis' IS NOT NULL
            ORDER BY as_of, ticker
        """)
        db_rows = cur.fetchall()
    conn.close()

    rows = []
    for ticker, as_of, profile, shift, trend, analogs, signal in db_rows:
        signal = signal or {}
        rows.append({
            "id": f"{ticker}_{as_of}",
            "inputs": {
                "profile_summary": (profile or {}).get("summary", ""),
                "shift": {k: (shift or {}).get(k) for k in
                          ("has_shifted", "direction", "magnitude_z", "reasoning",
                           "candidate_catalysts")},
                "fundamentals_verdict": (trend or {}).get("verdict"),
                "fundamentals_reasoning": (trend or {}).get("reasoning", ""),
                "analogs": _analog_desc(analogs or {}),
            },
            "direction": signal.get("direction"),
            "conviction": signal.get("conviction"),
            "thesis": signal.get("thesis", ""),
            "invalidation_conditions": signal.get("invalidation_conditions") or [],
        })
    _write("thesis_quality", rows)


def main():
    ap = argparse.ArgumentParser(description="Build eval datasets from the live DB")
    ap.add_argument("--tickers", nargs="+", default=sorted(COMPANY_NAMES.keys()))
    ap.add_argument("--lookback", type=int, default=150)
    args = ap.parse_args()

    neo = None
    try:
        from graph_db.neo4j_manager import Neo4jManager
        neo = Neo4jManager()
    except Exception as e:
        print(f"⚠ Neo4j unavailable ({e}) — bucket_map ground truth needs it")

    print("Building datasets…")
    build_bucket_map(args.tickers, args.lookback, neo)
    build_synthesis_matrix()
    build_thesis_quality()
    if neo:
        neo.close()


if __name__ == "__main__":
    main()
