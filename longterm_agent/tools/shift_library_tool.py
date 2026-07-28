"""
Shift-library read/write (§4.1) — the case base for analogical reasoning.

write_shift_row: append the current shift's fingerprint at persistence time (outcomes
    are filled later by the grading job). Every run writes a row, so the library grows
    forward even though it starts empty.

find_similar_shifts: return graded past shifts that resemble the current one, as a base
    rate for synthesis. Leakage rule: only analogs whose outcome window completed before
    the current `as_of`. The window is anchored where the trade actually is — at the
    ENTRY bar (`entry_date + horizon ≤ as_of`), since under `open_t1` an analog's outcome
    runs a few days past `as_of_analog + horizon`; rows graded before entry_date existed
    fall back to the old as_of rule. Version-filtered so fingerprints are comparable.
    Empty library → n_analogs=0 (cold start handled gracefully).

Similarity (v1, no embeddings yet): same direction, ranked by |Δ magnitude_z| + causal-type
    overlap. The pgvector embedding column is reserved for a later upgrade.
"""
import os
from math import isfinite, sqrt
from statistics import median
from typing import Any, Dict
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import Json

HORIZON_DAYS = 28  # 4-week outcome window for the leakage cutoff / base rate
DEFAULT_MIN_ANALOGS = 5  # below this, a base rate is noise dressed as evidence


def wilson_interval(successes: int, n: int, z: float = 1.96):
    """Wilson score 95% CI for a binomial proportion — honest for the tiny n here
    (k≤6), where the naive p±... interval is nonsense. Returns (low, high) or None."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round(max(0.0, center - margin), 2), round(min(1.0, center + margin), 2))


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
            # Grading columns (added by grade_shifts on existing DBs). Ensured here too so
            # the leakage filter in find_similar_shifts can rely on entry_date existing
            # even if the grader has never run against this database.
            cur.execute("""ALTER TABLE shift_library
                           ADD COLUMN IF NOT EXISTS entry_date DATE,
                           ADD COLUMN IF NOT EXISTS entry_px REAL,
                           ADD COLUMN IF NOT EXISTS fill_convention VARCHAR(12)""")
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
                        target_z, causal_types, k=6, min_analogs=DEFAULT_MIN_ANALOGS) -> Dict[str, Any]:
    """Return base-rate stats over the k nearest graded analogs (leakage-safe).

    Honesty (Phase 3): below `min_analogs` graded neighbours the base rate is set
    `insufficient=True` (synthesis is told not to weigh it); at/above, `share_positive`
    ships with a Wilson 95% CI so its uncertainty is explicit rather than a false point."""
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
                  AND COALESCE(entry_date, as_of) <= %s
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
    # `IS NOT NULL` in SQL and `is not None` here both PASS a Postgres numeric NaN, and one
    # NaN in the list makes median() return NaN — which then kills the JSONB write of the
    # whole decision (measured: NOW @ 2026-07-26, a traded SHORT lost this way). A graded
    # outcome that isn't a real number is not an analog; drop it from the pool.
    _ok = lambda x: x is not None and isfinite(float(x))
    nearest = [r for r in nearest if _ok(r[5])]
    rets = [float(r[5]) for r in nearest]
    excess = [float(r[5]) - (float(r[6]) if _ok(r[6]) else 0.0) for r in nearest]
    n = len(rets)
    pos = sum(1 for x in rets if x > 0)
    insufficient = n < min_analogs
    return {
        "n_analogs": len(nearest),
        "median_fwd_return_4w": round(median(rets), 4) if rets else None,
        "median_excess_4w": round(median(excess), 4) if excess else None,
        "share_positive": round(pos / n, 2) if n else None,
        "share_positive_ci": wilson_interval(pos, n) if n else None,
        "insufficient": insufficient,
        "fundamentals_matched_note": (f"insufficient analogs (n={n} < {min_analogs}) — do not weigh"
                                      if insufficient else ""),
        "nearest_cases": [f"{r[0]} {r[1]} {r[2]} ret4w={r[5]:+.2%}" for r in nearest if r[5] is not None][:5],
    }


def _empty(note):
    return {"n_analogs": 0, "median_fwd_return_4w": None, "median_excess_4w": None,
            "share_positive": None, "share_positive_ci": None, "insufficient": True,
            "fundamentals_matched_note": note, "nearest_cases": []}
