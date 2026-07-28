"""
Persist a long-term agent run: the result row (+ reasoning artifacts) and the
shift-library fingerprint (which accumulates forward for analogical reasoning).
"""
import math
import os
import traceback
from typing import Any, Dict

import psycopg2
from psycopg2.extras import Json

from .tools.shift_library_tool import write_shift_row


def _conn(db_manager):
    return psycopg2.connect(**db_manager.db_config)


def json_safe(obj, _path="", _scrubbed=None):
    """Replace every non-finite float with None, returning (clean_obj, scrubbed_paths).

    Postgres JSONB rejects NaN and Infinity outright, so ONE bad float anywhere in a
    diagnostic blob destroys the whole INSERT — and with it the decision the run just
    spent minutes producing. That is not a trade the risk gate declined; it is a trade
    that silently never existed. Two separate NaN sources have already done this:
    a poisoned price (2026-07-24) and a median over analogs carrying NaN outcomes.

    So the rule is: a decision is never lost to a formatting artifact in a field that
    only explains it. The scrubbed paths are returned rather than swallowed — the caller
    logs them, because a NaN reaching here still means something upstream is wrong.
    """
    scrubbed = [] if _scrubbed is None else _scrubbed
    if isinstance(obj, float):
        if not math.isfinite(obj):
            scrubbed.append(_path or "<root>")
            return None, scrubbed
        return obj, scrubbed
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k], _ = json_safe(v, f"{_path}.{k}" if _path else str(k), scrubbed)
        return out, scrubbed
    if isinstance(obj, (list, tuple)):
        out = []
        for i, v in enumerate(obj):
            cleaned, _ = json_safe(v, f"{_path}[{i}]", scrubbed)
            out.append(cleaned)
        return out, scrubbed
    return obj, scrubbed


def _J(obj, scrubbed):
    """Json() wrapper that scrubs non-finite floats, accumulating the paths it touched."""
    clean, _ = json_safe(obj, "", scrubbed)
    return Json(clean)


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
                       ADD COLUMN IF NOT EXISTS risk_checks JSONB,
                       ADD COLUMN IF NOT EXISTS gate_trace JSONB""")
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
    scrubbed: list = []          # non-finite floats replaced with None, reported below

    try:
        _ensure_results_table(db_manager)
        conn = _conn(db_manager)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO longterm_agent_results
                    (agent_name, agent_version, ticker, as_of, lookback_days,
                     background_profile, background_shift, fundamentals_summary, shift_analogs,
                     enriched_analysis, signal, verdict, traded, risk_flags, conviction,
                     article_count, execution_time_seconds, risk_checks, gate_trace)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                    gate_trace=EXCLUDED.gate_trace,
                    run_timestamp=NOW()
            """, [
                # Results identity = DETECTION_VERSION (see config.py migration note):
                # existing rows carry "0.1.0" and grading/stats filter on this column, so
                # pipeline bumps must NOT re-key rows. Pipeline version → provenance only.
                config.AGENT_NAME, config.DETECTION_VERSION, ticker.upper(), as_of.date(),
                state.get("lookback_days"),
                _J(state.get("background_profile") or {}, scrubbed),
                _J(state.get("background_shift") or {}, scrubbed),
                _J(state.get("fundamentals", {}).get("trend") or {}, scrubbed),
                _J(state.get("shift_analogs") or {}, scrubbed),
                _J(state.get("enriched_analysis") or {}, scrubbed),
                _J(filtered or sig or {}, scrubbed),
                verdict, traded, _J(state.get("risk_flags") or [], scrubbed),
                sig.get("conviction"), state.get("article_count"),
                round(execution_time, 1) if execution_time else None,
                _J(state.get("risk_checks") or {}, scrubbed),
                _J(state.get("gate_trace") or {}, scrubbed),
            ])
            cur.execute("""SELECT id FROM longterm_agent_results
                           WHERE agent_version=%s AND ticker=%s AND as_of=%s""",
                        [config.DETECTION_VERSION, ticker.upper(), as_of.date()])
            row = cur.fetchone()
            result_id = row[0] if row else None
        conn.commit()
        conn.close()
        if scrubbed:
            # The row IS written — but a non-finite number upstream is still a defect, and
            # the field names are the whole lead (`shift_analogs.median_fwd_return_4w`
            # pointed straight at NaN outcomes sitting in shift_library).
            paths = ", ".join(sorted(set(scrubbed))[:8])
            print(f"  ⚠ {ticker}: scrubbed {len(scrubbed)} non-finite value(s) → NULL: {paths}")
            try:
                from . import oplog
                oplog.warning("NONFINITE_SCRUBBED", f"{ticker}: {paths}", ticker=ticker,
                              as_of=as_of,
                              details={"paths": sorted(set(scrubbed)), "count": len(scrubbed)})
            except Exception:
                pass
    except Exception as e:
        # LOUD on purpose. This used to be a bare print to stdout, and on 2026-07-26 that
        # swallowed a whole week: NaN prices reached the JSONB payloads, Postgres refused
        # every insert ("invalid input syntax for type json"), and the batch still reported
        # "1 trade" while the results table got nothing. A run that decided something and
        # failed to record it is an incident, not a warning — the caller escalates further.
        detail = f"{type(e).__name__}: {e}"
        print(f"  ⚠ results persistence failed for {ticker} @ {as_of.date()}: {detail}")
        try:
            from . import oplog
            oplog.error("PERSIST_FAILED", f"{ticker}: {detail}", ticker=ticker, as_of=as_of,
                        details={"traceback": traceback.format_exc()[-2000:],
                                 "verdict": verdict, "traded": traded})
        except Exception as log_err:      # oplog is best-effort; never mask the real failure
            print(f"  ⚠ could not log the persistence failure: {log_err}")
        return False

    # Phase 11 — replayable full-state snapshot (references + LLM outputs). Best-effort:
    # the training set grows silently alongside results; a failure never fails the run.
    from .snapshots import write_snapshot
    write_snapshot(db_manager, state, config, result_id=result_id)

    # Append the shift fingerprint (forward-accumulating case base) when a shift fired.
    if not no_data and state.get("background_shift", {}).get("has_shifted"):
        write_shift_row(
            db_manager, ticker, as_of, config.DETECTION_VERSION,
            state["background_shift"], state.get("fundamentals", {}), state.get("background_metrics", {}),
        )
    return True
