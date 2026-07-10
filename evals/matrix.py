"""Pure matrix-consistency logic for the synthesis_matrix eval (unit-tested, no LLM).

Mirrors SYNTHESIS_MATRIX_RULES (longterm_agent.agent.nodes): the eval checks that the
synthesis LLM's direction lands in the cell the deterministic inputs dictate. Cells the
matrix leaves open (verdict ignores/unknown WITH a shift) are not rule-checkable and
are skipped rather than guessed at.
"""
TRADE_THRESHOLD = 0.55  # mirrors LT_CONF_THRESHOLD's default


def expected_directions(has_shifted: bool, shift_direction, verdict):
    """Allowed directions for a matrix cell, or None when the cell is not rule-checkable."""
    if not has_shifted:
        return {"NEUTRAL"}
    if verdict == "confirms":
        return {"LONG"} if shift_direction == "positive" else {"SHORT"}
    if verdict == "contradicts":
        # negative news + improving fundamentals → overreaction: contrarian LONG or NEUTRAL
        # positive news + deteriorating fundamentals → hype risk: avoid
        return {"LONG", "NEUTRAL"} if shift_direction == "negative" else {"NEUTRAL"}
    return None  # ignores / unknown with a shift — matrix defines no cell


def score_matrix_case(has_shifted, shift_direction, verdict, direction, conviction,
                      threshold: float = TRADE_THRESHOLD):
    """True/False = consistent/violation; None = cell not rule-checkable.

    Low-conviction escape hatches, mirroring the prompt's own wording:
      - no shift: any direction passes IF conviction stays below the trade threshold
        (the prompt demands "NEUTRAL with LOW conviction" — a 0.3 LONG lean is a lean,
        not a matrix violation; the risk gate filters it deterministically anyway);
      - hype-risk cell (positive shift + contradicts): a LONG passes only below the
        threshold ("low conviction / avoid").
    """
    exp = expected_directions(has_shifted, shift_direction, verdict)
    if exp is None:
        return None
    if direction in exp:
        return True
    conviction = conviction or 0.0
    if not has_shifted:
        return conviction < threshold
    if verdict == "contradicts" and shift_direction == "positive" and direction == "LONG":
        return conviction < threshold
    return False
