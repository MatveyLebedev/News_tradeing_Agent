"""
Full-state run snapshots (Modification Plan Phase 11 — learning-loop groundwork).

Every persisted run stores a REPLAYABLE snapshot: references for data that already lives
in other tables (corpus → news ids; fundamentals reproducible from (ticker, as_of) +
git_revision in provenance), LLM outputs IN FULL (bucket summaries, profile, shift
characterization, verdicts, findings, signal, node messages). This table IS the future
training set — prompt-tuning / fine-tuning needs (exact inputs, model outputs, realized
outcomes), and outcomes can only be joined later if inputs are captured NOW.

Size: ~30-80 KB per run → ~100-200 MB/year at 50 tickers/week. Keep forever.
Best-effort by contract: a snapshot failure must never fail a run (mirrors persist_run).
"""
from typing import Any, Dict, Optional

import psycopg2
from psycopg2.extras import Json

_DDL = """
CREATE TABLE IF NOT EXISTS longterm_run_snapshots (
    id                 SERIAL PRIMARY KEY,
    result_id          INTEGER,             -- soft ref → longterm_agent_results.id
    ticker             VARCHAR(10) NOT NULL,
    as_of              DATE        NOT NULL,
    detection_version  VARCHAR(12) NOT NULL,
    pipeline_version   VARCHAR(12),
    state              JSONB       NOT NULL,
    created_at         TIMESTAMP   DEFAULT NOW(),
    UNIQUE (detection_version, ticker, as_of)
);
"""


def build_snapshot_doc(state: Dict[str, Any], config) -> Dict[str, Any]:
    """Pure: state → snapshot document. References, not copies: the corpus goes in by
    news id (articles live in `news`); deterministic re-reads (fundamentals, technicals)
    are keyed by (ticker, as_of) + provenance.git_revision. LLM outputs go in FULL."""
    corpus = state.get("news_corpus") or []
    fund = state.get("fundamentals") or {}
    return {
        "identity": {
            "ticker": state.get("ticker"),
            "as_of": str(state.get("as_of")),
            "lookback_days": state.get("lookback_days"),
            "article_count": state.get("article_count"),
            "no_data": state.get("no_data", False),
        },
        "corpus_ref": {  # reference: bodies live in `news`
            "news_ids": [a.get("id") for a in corpus if a.get("id") is not None],
            "provenance": state.get("corpus_provenance"),
        },
        # LLM outputs — stored in full (these are the tuning targets/inputs)
        "bucket_summaries": state.get("bucket_summaries"),
        "background_profile": state.get("background_profile"),
        "background_shift": state.get("background_shift"),
        "fundamentals_trend": fund.get("trend"),
        "shift_analogs": state.get("shift_analogs"),
        "enriched_analysis": state.get("enriched_analysis"),
        "signal": state.get("signal"),
        "filtered_signal": state.get("filtered_signal"),
        "reflection_advice": state.get("reflection_advice"),
        "messages": state.get("messages"),
        # deterministic evidence (small, kept verbatim for exact replay)
        "background_metrics": state.get("background_metrics"),
        "technical_longterm": state.get("technical_longterm"),
        "risk_flags": state.get("risk_flags"),
        "risk_checks": state.get("risk_checks"),
        "provenance": config.provenance(),
    }


def write_snapshot(db_manager, state: Dict[str, Any], config,
                   result_id: Optional[int] = None) -> bool:
    """Upsert the run snapshot. Never raises (best-effort, like persist_run)."""
    try:
        doc = build_snapshot_doc(state, config)
        conn = psycopg2.connect(**db_manager.db_config)
        try:
            with conn.cursor() as cur:
                cur.execute(_DDL)
                cur.execute("""
                    INSERT INTO longterm_run_snapshots
                        (result_id, ticker, as_of, detection_version, pipeline_version, state)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (detection_version, ticker, as_of) DO UPDATE SET
                        result_id=EXCLUDED.result_id, pipeline_version=EXCLUDED.pipeline_version,
                        state=EXCLUDED.state, created_at=NOW()
                """, [result_id, state["ticker"].upper(), state["as_of"].date(),
                      config.DETECTION_VERSION, config.PIPELINE_VERSION, Json(doc)])
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:
        print(f"  ⚠ snapshot write failed: {e}")
        return False
