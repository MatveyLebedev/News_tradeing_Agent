# Decision matrix — what turns a detected shift into a position

The agent trades **changes in a company's news background**, not the level of it. This
document is the authoritative statement of how a detected change becomes a LONG, a SHORT or
nothing, and of where the prompt and the gate disagree.

Two surfaces implement it and they are not the same thing:

| | where | what it is |
|---|---|---|
| **the rules** | `agent/nodes.py::SYNTHESIS_MATRIX_RULES` | text handed to the synthesis model; it *proposes* a direction |
| **the cells** | `agent/nodes.py::MATRIX_CELLS` | table the risk gate enforces; it *permits* a direction |

`tests/test_matrix_cell.py::test_table_matches_the_prompt_text` asserts every licensed cell
is a line of the rules text, so editing one without the other fails a test rather than
quietly changing a book.

---

## 1. The verdict is relative, not absolute

`fundamentals_analysis` does not ask "are the fundamentals good". It asks how they move
**relative to the news shift** (`agent/nodes.py::fundamentals_analysis_node`):

| verdict | meaning |
|---|---|
| `confirms` | fundamentals move the SAME way as the shift |
| `contradicts` | fundamentals move the OPPOSITE way |
| `ignores` | fundamentals are flat / unrelated to the shift |
| `unknown` | no verdict was formed (LLM failure, or no usable statements) |

So the same fundamental picture reads differently depending on which way the news moved.
That is the point of the cross-check, and it is why the verdict alone cannot place a
decision: **every cell depends on the shift's sign.**

## 2. The rules (what synthesis is told)

|  | fundamentals improving | fundamentals deteriorating |
|---|---|---|
| **shift positive** | high-conviction **LONG** | hype risk → low conviction / **avoid** |
| **shift negative** | overreaction → contrarian **LONG** or NEUTRAL | high-conviction **SHORT** |
| **no shift** | NEUTRAL, low conviction | NEUTRAL, low conviction |

Plus one line that is easy to miss and does real work: *strong standing fundamentals alone
are NOT a trade trigger here — this agent trades shifts.*

## 3. The cells (what the gate permits)

`MATRIX_CELLS` — a cell is `(shift sign, verdict)` and licenses exactly one side:

| shift | verdict | licenses |
|---|---|---|
| `positive` | `confirms` | **LONG** |
| `negative` | `confirms` | **SHORT** |
| `negative` | `contradicts` | **LONG** — the overreaction cell |

Anything absent licenses nothing. Enumerated, the gate vetoes (`no_matrix_cell`):

| case | why |
|---|---|
| `positive` + `contradicts` | hype risk — the rules say avoid |
| any + `ignores` | fundamentals unrelated to the shift; no cell at any sign |
| any + `unknown` or missing | the cross-check never landed |
| shift direction is NULL | the decision places nowhere in the matrix |
| no shift at all | caught earlier by `no_shift` |
| right cell, wrong side | `positive`+`confirms` does not license a SHORT, and vice versa |

There is no tunable number anywhere in this table. It is three entries transcribed from the
rules text.

## 4. The full path from decision to trade

```
shift detected?              no ──────────────────────► NEUTRAL    [no_shift]
   │ yes
   ▼
cell licenses this side?     no ──────────────────────► FILTERED   [no_matrix_cell]
   │ yes
   ▼
data vetoes                  no usable income fundamentals ──────► FILTERED
                             stale sources (live runs only) ─────► FILTERED
   │ passed
   ▼
conviction penalties         priced-in        −0.20
                             earnings ≤10d    −0.10
                             counter-trend    −0.20
   │
   ▼
conviction ≥ threshold?      no ──────────────────────► FILTERED
   │ yes
   ▼
TRADE
```

Detection itself is deterministic: `has_shifted`, `direction` and `shift_score` come from
`tools/background_metrics.py::compute_shift_metrics`, and the LLM only *characterises* the
shift (catalysts, inflection, wording) — it cannot flip the label. The metrics consume the
weekly bucket sentiments, which are produced by the map model; the label is reproducible
given those summaries, not independently of them.

Flags in force (`config.py`):

| flag | default | effect |
|---|---|---|
| `LT_REQUIRE_SHIFT` | `true` | no shift ⇒ never traded |
| `LT_REQUIRE_MATRIX_CELL` | `true` | side must be licensed by its cell |
| `LT_CONF_THRESHOLD` | `0.55` | final conviction bar |

## 5. Where the two surfaces disagree

**The gate is stricter in one place.** The rules call the hype-risk corner "low conviction /
avoid", which still admits a small position. The gate reads it as zero licensed sides and
removes it outright.

**The gate is looser in another.** The rules offer "contrarian LONG **or** NEUTRAL" for the
overreaction corner and leave the choice to the model. The gate does not force an entry; it
only refuses what the matrix does not name.

Neither surface decides *size*: conviction, its penalties and the threshold do, and they sit
downstream of everything above.

## 6. The live book is narrower than the matrix

Since `0.11.0` the live path runs with `LT_LONG_CONFIRMS_ONLY=true`, which keeps **one** cell:

| shift | verdict | licenses |
|---|---|---|
| `positive` | `confirms` | **LONG** |

No short leg, and the overreaction cell closed. Decided 2026-08-24 on expert advice, against
the measurements in `../docs/EVENT_STUDY_2026-08-20.md` rather than because they were
resolved: the arm has the highest mean on sweeps and the highest median on the least-mined
dates, and it keeps 26% of trades, halves the name count, drops the hedge (book beta
0.84 → 1.23) and concentrates the tail (top-5 share of P&L 36% → 58%).

This is a **book decision layered on the matrix, not a reading of it** — `MATRIX_CELLS` still
licenses all three cells, `LONG_CONFIRMS_CELLS` is separate, and the flag defaults false so
backtests and research keep the full matrix. `provenance.knobs.long_confirms_only` says which
book any stored row belongs to.

## 7. The cell that was under question

`negative + contradicts → LONG` was registered for possible closure as `no_overreaction`
(`../docs/AB_PLAN_overreaction_cell.md`). The §6 book closes the cell outright, so it can no
longer be enabled live on acceptance and live corroboration stops accruing — but the
registration **stays open and runnable**: its stage 2 is a sweep, and the book flag defaults
false, so backtests still produce both arms.

The motivation is mechanism-level: the cell exists to buy an overreaction *after a name has
been beaten down*, and split by the 21-day pre-move it returns +13.83% (median +3.94) when
the name came in rising and +2.74% (median −1.88, CI covering zero) when it came in falling.
It works in the momentum half and fails in the contrarian half that is its justification.

The evidence is two-sided and the registration says so: closing the cell measures
+2.03/+3.07/+7.01pp on sweeps at 14/28/84d but −2.43pp on the live open book — evidence that
flips sign between snapshots, which is why it was registered rather than adopted. The live
book has since closed the cell by decision instead.

## 8. Where to look in code

| thing | file |
|---|---|
| rules text | `agent/nodes.py::SYNTHESIS_MATRIX_RULES` |
| cell table + helper | `agent/nodes.py::MATRIX_CELLS`, `matrix_licenses()` |
| the gate | `agent/nodes.py::risk_gate_node` |
| what becomes a trade | `agent/nodes.py::selection_passed` |
| verdict production | `agent/nodes.py::fundamentals_analysis_node` |
| shift detection | `tools/background_metrics.py::compute_shift_metrics` |
| tests pinning rules ↔ cells | `tests/test_matrix_cell.py` |
