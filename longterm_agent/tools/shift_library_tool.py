"""
Shift-library read/write (§4.1) — the case base for analogical reasoning.

write_shift_row: append the current shift's fingerprint at persistence time (outcomes
    are filled later by the grading job). Every run writes a row, so the library grows
    forward even though it starts empty.

find_similar_shifts: return graded past shifts that resemble the current one, as a base
    rate for synthesis. Leakage rule: only analogs whose outcome window completed before
    the current `as_of` (as_of_analog + horizon ≤ as_of). Version-filtered so fingerprints
    are comparable. Empty library → n_analogs=0 (cold start handled gracefully).

Similarity (v1, no embeddings yet): same direction, ranked by |Δ magnitude_z| + causal-type
    overlap. The pgvector embedding column is reserved for a later upgrade.
"""
import os
from statistics import median
from typing import Any, Dict
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import Json

HORIZON_DAYS = 28  # 4-week outcome window for the leakage cutoff / base rate


def _conn(db_manager):
    return psycopg2.connect(**db_manager.db_config)


def ensure_table(db_manager):
    sql_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "create_shift_library_table.sql")
    try:
        with open(sql_path) as f:
            ddl = f.read()
        conn = _conn(db_manager)
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"  ⚠ could not ensure shift_library table: {e}")
        return False


def write_shift_row(db_manager, ticker, as_of, agent_version, shift, fundamentals, metrics):
    """Append the current shift fingerprint (outcomes backfilled later by the grading job)."""
    if not ensure_table(db_manager):
        return False
    fund_ctx = {
        "verdict": (fundamentals.get("trend") or {}).get("verdict"),
        "revenue_yoy": fundamentals.get("revenue_yoy"),
        "valuation": {k: fundamentals.get("valuation", {}).get(k) for k in ("trailingPE", "forwardPE")},
    }
    row = {
        "agent_name": "longterm_agent", "agent_version": agent_version,
        "ticker": ticker.upper(), "as_of": as_of.date().isoformat(),
        "inflection_date": _date_or_none(shift.get("inflection_date")),
        "direction": shift.get("direction"),
        "magnitude_z": shift.get("magnitude_z"),
        "shift_window_days": metrics.get("recent_buckets", 0) * 7,
        "causal_event_types": [s.get("label") for s in metrics.get("new_stories", [])][:8],
        "novelty": None,
        "attention_spike_ratio": metrics.get("attention", {}).get("spike_ratio"),
        "fundamentals_context": fund_ctx,
        "features": {"shift_score": shift.get("shift_score"),
                     "narrative_turnover": metrics.get("narrative_turnover")},
    }
    conn = _conn(db_manager)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO shift_library
                    (agent_name, agent_version, ticker, as_of, inflection_date, direction,
                     magnitude_z, shift_window_days, causal_event_types, novelty,
                     attention_spike_ratio, fundamentals_context, features)
                VALUES (%(agent_name)s,%(agent_version)s,%(ticker)s,%(as_of)s,%(inflection_date)s,
                        %(direction)s,%(magnitude_z)s,%(shift_window_days)s,%(causal_event_types)s,
                        %(novelty)s,%(attention_spike_ratio)s,%(fundamentals_context)s,%(features)s)
                ON CONFLICT (agent_version, ticker, as_of) DO UPDATE SET
                    direction=EXCLUDED.direction, magnitude_z=EXCLUDED.magnitude_z,
                    fundamentals_context=EXCLUDED.fundamentals_context, features=EXCLUDED.features
            """, {**row,
                  "causal_event_types": row["causal_event_types"],
                  "fundamentals_context": Json(row["fundamentals_context"]),
                  "features": Json(row["features"])})
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"  ⚠ shift_library write failed: {e}")
        return False
    finally:
        conn.close()


def _date_or_none(s):
    if not s:
        return None
    # inflection may be a week range "2026-05-14..2026-05-21" — take the start
    s = str(s).split("..")[0].strip()
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except Exception:
        return None


def find_similar_shifts(db_manager, ticker, as_of, agent_version, direction,
                        target_z, causal_types, k=6) -> Dict[str, Any]:
    """Return base-rate stats over the k nearest graded analogs (leakage-safe)."""
    cutoff = (as_of - timedelta(days=HORIZON_DAYS)).date().isoformat()
    if not ensure_table(db_manager):
        return _empty("table unavailable")
    conn = _conn(db_manager)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ticker, as_of, direction, magnitude_z, causal_event_types,
                       fwd_return_4w, benchmark_return_4w
                FROM shift_library
                WHERE agent_version = %s
                  AND outcome_filled_at IS NOT NULL
                  AND fwd_return_4w IS NOT NULL
                  AND as_of <= %s
                  AND (%s IS NULL OR direction = %s)
            """, [agent_version, cutoff, direction, direction])
            rows = cur.fetchall()
    except Exception as e:
        print(f"  ⚠ shift_library read failed: {e}")
        return _empty("read failed")
    finally:
        conn.close()

    if not rows:
        return _empty("cold start: no graded analogs yet")

    target_types = set(causal_types or [])

    def dist(r):
        dz = abs((r[3] or 0.0) - (target_z or 0.0))
        rtypes = set(r[4] or [])
        overlap = len(target_types & rtypes) / (len(target_types | rtypes) or 1)
        return dz - overlap  # smaller is closer

    nearest = sorted(rows, key=dist)[:k]
    rets = [r[5] for r in nearest if r[5] is not None]
    excess = [(r[5] - (r[6] or 0.0)) for r in nearest if r[5] is not None]
    pos = sum(1 for x in rets if x > 0)
    return {
        "n_analogs": len(nearest),
        "median_fwd_return_4w": round(median(rets), 4) if rets else None,
        "median_excess_4w": round(median(excess), 4) if excess else None,
        "share_positive": round(pos / len(rets), 2) if rets else None,
        "fundamentals_matched_note": "",
        "nearest_cases": [f"{r[0]} {r[1]} {r[2]} ret4w={r[5]:+.2%}" for r in nearest if r[5] is not None][:5],
    }


def _empty(note):
    return {"n_analogs": 0, "median_fwd_return_4w": None, "median_excess_4w": None,
            "share_positive": None, "fundamentals_matched_note": note, "nearest_cases": []}
