"""
Persist a long-term agent run: the result row (+ reasoning artifacts) and the
shift-library fingerprint (which accumulates forward for analogical reasoning).
"""
import os
from typing import Any, Dict

import psycopg2
from psycopg2.extras import Json

from .tools.shift_library_tool import write_shift_row


def _conn(db_manager):
    return psycopg2.connect(**db_manager.db_config)


def _ensure_results_table(db_manager):
    sql_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "create_results_table.sql")
    with open(sql_path) as f:
        ddl = f.read()
    conn = _conn(db_manager)
    with conn.cursor() as cur:
        cur.execute(ddl)
        # Phase 14 — risk_checks (incl. the regime_context stamp) persists with the run so
        # forward signals are regime-annotated from day one (splits become computable later).
        cur.execute("""ALTER TABLE longterm_agent_results
                       ADD COLUMN IF NOT EXISTS risk_checks JSONB""")
    conn.commit()
    conn.close()


def persist_run(db_manager, state: Dict[str, Any], config, execution_time: float = None) -> bool:
    """Write longterm_agent_results + shift_library row. Best-effort (never crashes a run)."""
    ticker = state["ticker"]
    as_of = state["as_of"]
    sig = state.get("signal") or {}
    filtered = state.get("filtered_signal")
    no_data = state.get("no_data", False)
    verdict = "NO_DATA" if no_data else (sig.get("direction") or "NEUTRAL")
    # A "trade" = a directional call that survived the risk gate (actionable). The bot/stats
    # split signals (all directional verdicts) from trades (traded=TRUE) on this flag.
    traded = bool(filtered) and (filtered.get("direction") in ("LONG", "SHORT"))

    try:
        _ensure_results_table(db_manager)
        conn = _conn(db_manager)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO longterm_agent_results
                    (agent_name, agent_version, ticker, as_of, lookback_days,
                     background_profile, background_shift, fundamentals_summary, shift_analogs,
                     enriched_analysis, signal, verdict, traded, risk_flags, conviction,
                     article_count, execution_time_seconds, risk_checks)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (agent_version, ticker, as_of) DO UPDATE SET
                    background_profile=EXCLUDED.background_profile,
                    background_shift=EXCLUDED.background_shift,
                    fundamentals_summary=EXCLUDED.fundamentals_summary,
                    shift_analogs=EXCLUDED.shift_analogs,
                    enriched_analysis=EXCLUDED.enriched_analysis,
                    signal=EXCLUDED.signal, verdict=EXCLUDED.verdict,
                    traded=EXCLUDED.traded, risk_flags=EXCLUDED.risk_flags,
                    conviction=EXCLUDED.conviction, article_count=EXCLUDED.article_count,
                    execution_time_seconds=EXCLUDED.execution_time_seconds,
                    risk_checks=EXCLUDED.risk_checks,
                    run_timestamp=NOW()
            """, [
                # Results identity = DETECTION_VERSION (see config.py migration note):
                # existing rows carry "0.1.0" and grading/stats filter on this column, so
                # pipeline bumps must NOT re-key rows. Pipeline version → provenance only.
                config.AGENT_NAME, config.DETECTION_VERSION, ticker.upper(), as_of.date(),
                state.get("lookback_days"),
                Json(state.get("background_profile") or {}),
                Json(state.get("background_shift") or {}),
                Json(state.get("fundamentals", {}).get("trend") or {}),
                Json(state.get("shift_analogs") or {}),
                Json(state.get("enriched_analysis") or {}),
                Json(filtered or sig or {}),
                verdict, traded, Json(state.get("risk_flags") or []),
                sig.get("conviction"), state.get("article_count"),
                round(execution_time, 1) if execution_time else None,
                Json(state.get("risk_checks") or {}),
            ])
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  ⚠ results persistence failed: {e}")
        return False

    # Append the shift fingerprint (forward-accumulating case base) when a shift fired.
    if not no_data and state.get("background_shift", {}).get("has_shifted"):
        write_shift_row(
            db_manager, ticker, as_of, config.DETECTION_VERSION,
            state["background_shift"], state.get("fundamentals", {}), state.get("background_metrics", {}),
        )
    return True
