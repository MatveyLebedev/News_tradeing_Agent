# Fine-tuning pre-checks — pipeline sensitivity (E1) and zero-shot baselines (E2)

**Status: PLAN, not started** · drafted 2026-10-20 · nothing here has been run.
Thresholds in §4 must be frozen in `BACKTEST_LEDGER.md` before E1 step 2 runs.

**2026-10-22 — gold set T2 sampled, not yet labeled.** `python -m evals.build_gold_sample`
(seed 20261022): 300 articles (graph_only 120 / random 100 / multi_ticker 80), 1,827
article × candidate items incl. 268 sector-peer distractors; guide `docs/GOLD_LABELING_GUIDE.md`,
files in `longterm_agent/exports/gold/` (gitignored). Pool facts measured on the way, inputs to
E1: over 2026-03-01 → 09-21 the live 64-ticker corpus holds 49,436 (article, ticker) pairs, of
which **78% (38,737) are graph-only**, and 31,433 of those sit on articles linked to ≥ 5 universe
tickers. Also found: the NOW regex path matches the English word "now" (1,558 case-insensitive
hits vs 39 case-sensitive) — logged as a separate fix, not part of this plan.

**2026-10-22 — T2 labeled by an LLM, not a human.** At the user's request all 1,827 items were
labeled by Claude (Opus 5.5) under the frozen guide, blind to `gold_key.csv`
(`gold_labels_claude.csv`; 19 marked unsure). Two consequences: the E2 teacher must not be an
Anthropic model when scored against T2 (same-family agreement would flatter it), and a human
should blind-label a random ~150-item subset to measure labeler agreement before T2 is trusted
for anything finer than the results below. Results, joined to the key after labeling:

| | not in text | subject / involved |
|---|---|---|
| random corpus pairs (≈ uniform over the corpus) | 83/100 | 11/100 |
| graph-only corpus pairs | 119/120 (fan-out bins 1 / 2-4 / ≥5: 39/40, 40/40, 40/40) | 1/120 |
| multi-ticker articles, sampled pair | 73/80 | 7/80 |
| all universe candidates by source: graph-only / graph+regex / regex-only | 1131/1135 · 0/87 · 19/40 | |
| sector-peer distractors | 268/268 | |

- **The graph-only path is noise, and the cause is structural, not the extractor.** The AFFECTS
  edge that puts a graph-only article into a ticker's corpus belongs to a merged canonical event:
  of the 122 linking events behind the graph-only anchors, 114 are sourced from more than one
  article (median 21 articles, p90 300) and affect a median 6 companies (p90 168). An article
  inherits every ticker its event accumulated from other articles. A fine-tuned extractor cannot
  fix that; attributing AFFECTS per article (or requiring the article to name the company) can.
- **Graph sentiment on real mentions:** on the 127 subject/involved pairs that carry a graph label,
  exact agreement is 64/127 (50%) with 16 sign flips — but that label is also a majority over the
  merged event's edges, so this measures the merge as much as the extractor.
- **Thin for task S:** only 161 of 1,827 items are mentions (110 subject, 25 involved, 26 context).
  T2 is a strong set for task R; sentiment evaluation needs T1/T3/T4.

Implication for E1: the "R-full" proxy (drop graph-only articles) is no longer a crude upper
bound — on this sample it is what correct attribution does. E1 should run it first.

Context: the working hypothesis (2026-10-20) is that reaction headlines ("X Jumps on Y",
`docs/REACTION_NEWS_DATASET_2026-07-27.md`) plus public entity-sentiment datasets can train
an open-weights model (Qwen3.5-9B class) to attribute news to companies and score
per-company sentiment better than the current `GRAPH_LLM_MODEL`. Before any training,
two questions decide whether it is worth doing:

- **E1 — does it matter?** How sensitive are detection and the live book to the per-article
  labels the model would change?
- **E2 — where do we start?** How do the current models, untuned Qwen and a strong teacher
  score on the data we would train and test on?

Neither experiment trains anything.

---

## 1. E1 — pipeline sensitivity

Detection v2 is a pure function of the article labels: `compute_shift_metrics(metrics_version=2,
articles=…)` reads `affects_sentiment` per article plus cached weekly map summaries
(fallback for the ~12% unlabeled). Perturbing labels in memory and recomputing costs no
LLM calls — same pattern as `compare_detections.py`.

**Grid.** Live universe (64 tickers) × weekly as_of 2026-04-30 → 2026-09-21 (~21 cohorts,
~1,350 points; 4w outcomes matured). Map summaries from `bucket_summary_cache` namespace
`0.1.0` (71 tickers, weeks 2026-01-22 → 2026-10-08) — cache misses fall back to
flash-lite map calls.

**Steps.**
1. **Snapshot** every grid point's corpus (article ids, sources, `affects_sentiment`,
   AFFECTS fan-out degree) + bucket summaries to local JSONL, read-only. The remote link
   drops large result sets (a full `news` scan failed twice on 2026-10-20) — batch with
   server-side cursors.
2. **Noise sweep** (elasticity): at p ∈ {5, 10, 20, 30}% × 10 seeds, independently
   (a) flip pos↔neg, (b) set to neutral, (c) drop the label (→ bucket fallback),
   (d) drop the article (hits the attention branch). Recompute; record flag / direction /
   positive-shift changes.
3. **Targeted proxies of the attribution model** (deterministic):
   - R-full: remove every graph-only article (regex did not match the company) — upper bound
     of "the model rejects all fan-out". Graph-only is ~¾ of the corpus (regex-only mean 74.7
     vs actual 302.8 articles, `DATA_UNUSED_LT_TESTS_2026-08-09.md` §4.1).
   - R-fanout: remove graph-only articles whose AFFECTS fan-out ≥ 5 tickers.
   - R-audit: remove graph-only articles at the fan-out share measured in step 4.
4. **Audit** 100–150 graph-only articles by hand (stratified by ticker and fan-out degree):
   own news / involved / sector context / not about the company. Gives the real fan-out
   share and whether regex misses (e.g. AMAT: 0 by regex, 58 actual) are common.
5. **Outcomes of changed decisions.** Removed shifts: graded `shift_library` rows
   (0.2.0: 332 rows, 254 graded) — removed vs kept hit-rate and median 4w excess. Live book:
   `longterm_agent_results` traded rows in the `(positive, confirms)` cell whose shift
   disappears (live book trades ~5/week since 2026-07-13) and their `excess_beta`.
   Shifts that APPEAR are counted only; grading them needs a synthesis re-run (optional, §3).
6. **Teacher upper bound** (conditional on G1 and G2): teacher re-labels attribution +
   sentiment for every corpus article on a subset of cohorts; repeat steps 3 and 5 with
   teacher labels instead of proxies.

**Primary readout:** changed live-book decisions per week, and whether the removed shifts
graded worse than the kept ones. Differences between two pipeline versions come only from
changed decisions (the Sonnet arm: 100% of its delta from 5 flips).

---

## 2. E2 — zero-shot baselines on the planned data

**Test sets** (none of these is ever trained on):

| id | set | size (est.) | labels |
|---|---|---|---|
| T1 | our 2026 STRICT reaction headlines, single-name stock subjects | ~800 (503 at the 2026-07-26 census; corpus grew 35.6k → 56.4k) | S: direction (reaction clause stripped); R: mover = subject, + injected sector-peer distractors = `not_in_text` |
| T2 | hand-labelled gold set | 300 (100 random corpus, 100 graph-only, 100 multi-ticker/sector) | S + R, labelled **before** seeing any model output |
| T3 | FinEntity | ~1k paragraphs / ~2.1k entities | S |
| T4 | SEntFiN test split | ~2k headlines (dataset is released on request) | S |
| T5 | FNSPID 2022–2023 reaction-derived sample | 2,000 | S + R from the scanner — measures **synthetic-label noise** (teacher vs label agreement) |

**Models:** `gemini-3.1-flash-lite-preview` (current) and `gemini-3.1-flash-lite` (planned
stable swap), `deepseek-v4-pro` (main pipeline model), Qwen3.5-9B and Qwen3.5-27B zero-shot,
teacher `anthropic/claude-sonnet-5.5`. **Candidates narrowed 2026-10-20 to two:**
Qwen3.5-9B (LoRA) and DeBERTa-v3-large (encoder classifier for tasks S and R; runs on CPU).
DeBERTa has no zero-shot mode — it enters at the training stage as its own arm.
Considered and dropped: Gemma 4 12B / E4B, Ministral 3 8B, Granite 4.2 8B, GPT-OSS-20B.

**Protocol.** One frozen prompt for all models, developed on a 200-item dev slice that is
not part of T1–T5. Reasoning disabled, `max_tokens` capped. Stability: 500 items × 3 repeats.

**Metrics.** S: pos-vs-neg accuracy (sign errors are the costly ones), macro-F1 secondary
(neutral is fuzzy — SEntFiN neutral/positive agreement 80%). R: subject precision on
sector/multi-ticker items, distractor rejection rate. Plus JSON validity and repeat
agreement. Report T1 by cause category (39% of 2026 reactions are the Iran war).

**Leak check for T1/T5:** bag-of-words logistic regression on the stripped inputs; move
verbs, "%" or "shares" among the top features means the stripping leaks.

---

## 3. Budget

Prices: OpenRouter `/api/v1/models`, read 2026-10-20 ($ per 1M tokens, in/out).

**E2** — ~8,900 calls (6.1k test items + 1k stability repeats + 1.8k prompt-dev), assumed
600 in / 120 out tokens per call (conservative; reasoning off):

| model | price in/out | cost |
|---|---|---|
| gemini-3.1-flash-lite-preview | 0.25 / 1.50 | $2.9 |
| gemini-3.1-flash-lite (stable) | 0.25 / 1.50 | $2.9 |
| deepseek-v4-pro | 0.42 / 0.85 | $3.2 |
| qwen3.5-9b | 0.10 / 0.15 | $0.7 |
| qwen3.5-27b | 0.27 / 2.16 | $3.8 |
| claude-sonnet-5.5 (teacher) | 2.00 / 10.00 | $21.4 (≈ $10.7 via `:batch`) |
| **subtotal** | | **≈ $35**; with 1.5× margin **≈ $55** |

**E1:**

| step | cost |
|---|---|
| 1–3 snapshot, noise sweep, proxies | ≈ $0 LLM (map cache misses: ~$0.001/bucket, < $2) |
| 4 audit | human time only |
| 5 outcomes of removed decisions | $0 |
| 5b (optional) synthesis re-run for appearing shifts, ≤ 200 decisions | ≈ $10–20 |
| 6 (conditional) teacher re-label, 20–35k articles × ~600 in / 120 out | ≈ $45–90 (≈ half via `:batch`) |

**Totals.** Core (E1 1–5 + E2): **≈ $55–60**. Everything incl. conditional steps:
**≈ $130–170**. No GPU spend — nothing is trained.

**Human time.** Gold set ~5–6 h; graph-only audit ~2–3 h.
**Dev time.** E1 snapshot + recompute harness ~1–2 days; E2 sets (scanner batching, FNSPID
adapter, stripping + leak check, distractors) + eval harness ~2–3 days.

---

## 4. Order of work and gates

0. **Preconditions.** Set a spend limit on the OpenRouter key and check `GET /v1/key`
   `limit_remaining` before the teacher runs (the Sonnet arm died on a key limit). Register E1 and E2 in `BACKTEST_LEDGER.md` with the thresholds below. Request
   SEntFiN from its authors. Settle FNSPID (card: CC BY-NC-4.0) and SEntFiN terms for
   evaluation use. Downloads need explicit approval: FinEntity (small, HF), FNSPID
   headline columns streamed rather than the 29.6 GB repo.
1. E1 steps 1–3 (snapshot, sweep, proxies) in parallel with the E2 gold-set labelling
   (it must be labelled blind, so it starts first).
2. E1 steps 4–5 → **gate G1.**
3. E2 test sets → prompt on the dev slice → model runs → report → **gate G2.**
4. E1 step 6 (teacher upper bound) only if G1 and G2 both pass.

**G1 — does it matter** (proposed, freeze before step 2):
- pass if R-full or R-audit changes ≥ 1 live-book `(positive, confirms)` decision per week
  on average, or ≥ 10% of positive-shift flags, **and** removed shifts graded worse than
  kept ones (direction of the mechanism, not significance);
- kill the *performance* track if < 0.5 changed live decisions per week and no grading
  difference. Fine-tuning can then be justified only by pinned weights and cost.

**G2 — is there room to learn** (proposed):
- teacher beats current flash-lite by ≥ 5 pp on S pos-vs-neg accuracy, or by ≥ 10 pp on R
  subject precision for sector/multi-ticker items (T1 + T2), **and** Qwen3.5-9B zero-shot
  sits below the teacher;
- if Qwen3.5-9B zero-shot already matches flash-lite, the pinned-weights goal is reachable
  without training, regardless of G1.

Burned-grid rule: E1 runs on burned windows, so it can **reject** the performance
hypothesis, never accept it. Acceptance stays with new cohorts after any trained model ships.
