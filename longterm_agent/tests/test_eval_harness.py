"""Pure tests for the Phase 4 eval harness: matrix logic + dataset integrity (no LLM)."""
import json
from pathlib import Path

from evals.matrix import expected_directions, score_matrix_case

DATASETS = Path(__file__).resolve().parents[2] / "evals" / "datasets"


# --- matrix cells --------------------------------------------------------------
def test_expected_directions_cells():
    assert expected_directions(True, "positive", "confirms") == {"LONG"}
    assert expected_directions(True, "negative", "confirms") == {"SHORT"}
    assert expected_directions(True, "negative", "contradicts") == {"LONG", "NEUTRAL"}
    assert expected_directions(True, "positive", "contradicts") == {"NEUTRAL"}
    assert expected_directions(False, None, "confirms") == {"NEUTRAL"}
    assert expected_directions(True, "positive", "ignores") is None
    assert expected_directions(True, "positive", "unknown") is None


def test_score_direct_hits():
    assert score_matrix_case(True, "positive", "confirms", "LONG", 0.8) is True
    assert score_matrix_case(True, "negative", "confirms", "SHORT", 0.7) is True
    assert score_matrix_case(True, "negative", "contradicts", "NEUTRAL", 0.4) is True
    assert score_matrix_case(True, "positive", "confirms", "SHORT", 0.8) is False


def test_score_low_conviction_escape_hatches():
    # no shift: a low-conviction lean is a lean, not a violation…
    assert score_matrix_case(False, None, "ignores", "LONG", 0.3) is True
    # …but a confident trade without a shift IS one
    assert score_matrix_case(False, None, "ignores", "LONG", 0.7) is False
    # hype-risk cell: LONG passes only below the trade threshold
    assert score_matrix_case(True, "positive", "contradicts", "LONG", 0.4) is True
    assert score_matrix_case(True, "positive", "contradicts", "LONG", 0.7) is False


def test_score_unscoreable_cell_returns_none():
    assert score_matrix_case(True, "positive", "ignores", "LONG", 0.9) is None


# --- dataset integrity -----------------------------------------------------------
def _rows(name):
    path = DATASETS / f"{name}.jsonl"
    assert path.exists(), f"{name} dataset missing — run `python -m evals.build_datasets`"
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_bucket_map_dataset_shape_and_no_leak():
    rows = _rows("bucket_map")
    assert len(rows) >= 20
    for r in rows:
        assert r["expected_sign"] in (1, -1)
        assert len(r["articles"]) >= 1
        # the ground-truth labels must NOT ship with the inputs (prompt-leak guard)
        assert "affects_sentiments" not in r
        for a in r["articles"]:
            assert "sentiment" not in a


def test_synthesis_matrix_dataset_covers_all_cells():
    rows = _rows("synthesis_matrix")
    cells = {(r["cell"]["has_shifted"], r["cell"]["direction"], r["cell"]["verdict"])
             for r in rows if r["source"] == "synthetic"}
    assert (True, "positive", "confirms") in cells
    assert (True, "negative", "confirms") in cells
    assert (True, "negative", "contradicts") in cells
    assert (True, "positive", "contradicts") in cells
    assert any(not c[0] for c in cells)  # at least one no-shift case
    # every case must be scorable or explicitly synthetic-unknown
    for r in rows:
        c = r["cell"]
        if c["has_shifted"]:
            assert c["verdict"] in ("confirms", "contradicts", "ignores", "unknown")


def test_thesis_quality_dataset_shape():
    rows = _rows("thesis_quality")
    for r in rows:
        assert r["direction"] in ("LONG", "SHORT")
        assert r["thesis"]
        assert "profile_summary" in r["inputs"]
