# Detection v2 — Validation Report (Phase 5)

**Date:** 2026-07-11 · **Backtest ID:** `wf_2026q2_v0.2` · **Detection:** 0.2.0 (metrics v2) · **git:** `2a69f07`

Validation of **detection v2** — per-article daily sentiment series with a median/MAD robust
break test — against the v1 weekly-bucket mean/std estimator. Companion to
[VALIDATION_REPORT.md](VALIDATION_REPORT.md) (v0.1, 2026-07-01). Same discipline: numbers on this
single-regime window are **harness checks, not verdicts** (WALK_FORWARD_PROTOCOL §7).

---

## TL;DR — Verdict

- ✅ **v2 is structurally sounder detection.** Decisions computed on hundreds of article-level
  points (AFFECTS coverage 88.2% on the universe) instead of ~8 weekly LLM scalars; robust
  median/MAD break test; sparse-series guards; direction abstains on pure attention spikes.
- ✅ **v1's over-triggering confirmed.** v1 flags 39% of all ticker-weeks as "shifts"; v2 flags 22%.
  Manual review of every wf-window disagreement sides with v2 (see §3).
- ❌ **No strategy edge demonstrated by either version** (expected: single bull regime, n≈13–19).
  v0.1's apparent 2w edge (+3.50%, FDR survivors AMD/MSFT) does not reproduce under v2 — it was
  carried by AMD trades whose underlying "shifts" were sector contagion (Broadcom miss),
  which v2 correctly rejects.
- 🟡 **Recommendation: flip the live default to v2** (`LT_METRICS_VERSION=2` in the server env)
  and accumulate forward evidence on the better-constructed detector. Cutover is an explicit
  ops action, not a code default change; v1 stays reproducible under its own version.

---

## 1. What changed (and what deliberately didn't)

| Component | v1 (0.1.0) | v2 (0.2.0) |
|---|---|---|
| Sentiment series | ~8 weekly bucket LLM scalars | per-article graph labels (±1/0), daily-aggregated; unlabeled → bucket LLM fallback (~12%) |
| Break test | count-weighted mean / pstdev z | recent-vs-baseline **median / scaled MAD** |
| Sparse guard | none | sentiment branch abstains below 3 recent / 5 baseline article-days |
| Attention branch | unchanged | unchanged |
| `min_abs_delta` guard | unchanged | unchanged |
| Corpus | graph + ilike | **identical** (embed source FAILED its precision audit — §4) |
| Bucket cache | keyed by detection version | shared via `BUCKET_CACHE_VERSION` (corpus/map identical) |

Because corpus and map summaries are byte-identical, the v1-vs-v2 comparison isolates the
estimator change — no map-LLM re-roll noise, no corpus drift.

## 2. Controlled comparison (full universe, 2026-04-30 → 2026-07-09, step 7d)

`python -m longterm_agent.compare_detections` · report:
`reports/detection_v1_v2_2026-04-30_2026-07-09.jsonl`

- Grid: 704 runs, **677 decided**, 0 errors.
- Shifts: v1 **265 (39%)**, v2 **152 (22%)**; both 126; v1-only 139; v2-only 26.
- Flag agreement **72.1%**; stored-v1 drift from graph growth: 1/17 (comparison is clean).
- v1-only shifts killed in v2 by: sparse series 46, |daily delta| < 0.2 → 57, robust |z| < 1 → 48.
- Direction flips 24 — **23 of them attention-triggered with sub-threshold deltas**: direction was
  sign-of-noise in both versions; v2 mostly reports `None` (honest) where v1 asserted a side.

**Graded detection base rates** (4w directional excess vs SPY, all graded library rows):

| | n | hit-rate | median | mean |
|---|---|---|---|---|
| v1 labels | 17 | 41% | **−1.6%** | −2.6% |
| v2 labels | 93 | 52% | +0.1% | −0.15% |

Single regime, small n — but v1's labels skew *contrarian*; v2's are neutral (no systematic error).

## 3. Manual review — every wf-window disagreement (8 tickers, ≤ 2026-06-19)

7 disagreements; review verdicts:

- **AMD 06-04, 06-11** (v1: SHIFT negative z=−2.3/−3.8; v2: no): the negative wave is the
  **Broadcom guidance miss** (06-04) dragging the whole chip sector + Nvidia superchip sympathy
  headlines, AFFECTS-fanned onto AMD. No AMD-specific shift. **v2 correct.** These very cohorts
  fed v0.1's "FDR-surviving" AMD trades.
- **MSFT / NVDA 05-07** (direction flip): both attention-triggered, |delta| ≤ 0.15 both scales —
  direction is noise either way; neither defensible. Tie (v2's abstention pattern preferable).
- **TSLA 05-07, 05-21 / JPM 05-21** (v1-only, weekly z 1.2–1.9 vs daily-median z 0.4–0.5):
  weekly buckets amplify on ≤6 baseline scalars (pstdev instability). Daily series shows drift,
  not a break. **v2 correct** (conservative).

## 4. Embed corpus source — precision audit FAILED (kept off)

Planned third retrieval source (pgvector cosine on `news.embedding`, HNSW index verified,
90.5% fill). Audit across 8 tickers, 60d window, with `gemini-embedding-001`:

- Query-vector: known-relevant articles median sim ≈ **0.50** — *below* generic sector chatter
  (0.55–0.60); target class (subsidiary phrasing, e.g. Waymo-without-Google) med 0.501 —
  indistinguishable from noise. No usable τ.
- Corpus-centroid variant: better (own med ≈ 0.72) but background p90 ≈ own median for
  GOOGL/META → still no usable τ.

Verdict: cosine in this embedding space encodes news *genre*, not company identity. The code
path ships **disabled** (`LT_CORPUS_EMBED=false` default in ALL versions); enabling it later is a
corpus change = new `DETECTION_VERSION` + `BUCKET_CACHE_VERSION` bump. Also fixed on the way:
`core/embeddings.py` retries the flaky OpenRouter embeddings endpoint and **raises** after
retries instead of silently returning an empty corpus contribution (labels must not depend on
API weather).

## 5. Walk-forward gates (B3) — v0.2 vs v0.1, same frozen window

8 tickers × 9 cohorts (2026-04-24 … 2026-06-19), horizons 14/28/84d, SPY,
`LT_LLM_CACHE=true LT_MAX_REFLECTION=0 LT_DEEP_ANALYSIS=false LT_METRICS_VERSION=2`.

| Gate / metric | v0.1 | **v0.2** |
|---|---|---|
| OOS (single provenance stamp) | ✓ | ✓ |
| traded (2w/4w) | 19 | 13 |
| 2w win rate | 61% CI [39, 83] | 54% CI [23, 85] |
| 2w mean excess | +3.50% CI [+0.18, +7.18] | +0.98% CI [−2.16, +4.46] |
| 4w mean excess | +4.02% CI [−1.37, +10.77] | +0.64% CI [−2.69, +4.40] |
| 4w median | −1.07% | −0.38% |
| conviction calibration | non-monotonic | non-monotonic |
| FDR survivors (BH 5%) | 2 @2w / 0 @4w | **0 / 0** |
| min-n gate | ESTIMATE (n<30) | ESTIMATE (n=13<30) |

Reading: v0.1's 2w result was concentration (AMD ×3, META short), not edge — §3 shows the AMD
shifts were sector noise. v2 removes those trades and the "edge" disappears; what remains is a
cleaner, smaller trade set with CIs straddling zero. Per protocol §7, **neither version is a
verdict**; forward accumulation decides. v2 rows accumulate under `agent_version='0.2.0'`
(152 backfilled shifts, 93 already graded); analog lookups under v2 match only v2 rows.

## 5b. Wide-universe check — 30 tickers × 9 cohorts (added 2026-07-11)

Re-ran both versions on a 30-name universe (`wf_30x_v0.1` / `wf_30x_v0.2`, same frozen
window/config, 20-worker parallel harness — `backtest.py --workers`). n≈44–75 per horizon,
~4× the 8-ticker sample:

| | v0.1 | **v0.2** |
|---|---|---|
| 4w traded / graded | 75 / 72 | 46 / 44 |
| 2w win / mean | 45% / −1.10% | 50% / −0.88% |
| 4w win / mean / median | 49% / −1.16% / −0.95% | **55% / −0.54% / +1.05%** |
| 4w Sharpe (per-cohort) | −0.29 | **+0.57** |
| calibration monotonic | no | no |

**Trade-set decomposition (4w):** overlap 37; the **35 v1-only trades sum to −42.8%**
(avg −1.2%) — v2's selectivity removed a net-losing book; the **7 v2-only trades sum to
+16.5%**. v0.1's 8-ticker "edge" confirmed dead on a wide universe (goes negative).

**Where v2's remaining losses live:** two counter-trend SHORTs shared by both versions —
INTC 04-24 (−40.7%; SHORT into a +317% rally) and QCOM 05-01 (−36.8%). Both were
*top-conviction* trades (0.75–0.78): the LLM is most confident exactly on dramatic
contrarian "narrative reckoning" theses, and no risk gate penalizes fighting a strong
trend (the priced-in guard only fires when the stock already moved WITH the signal).
Excluding those two, v2's 4w book is positive; its other 12 SHORTs sum ≈ +0.3%.
This cleanly separates the failure modes: **detection noise is fixed by v2; the residual
losses are a conviction/gate problem** — the highest-conviction bucket is the worst
(v2 4w: conv 0.75–0.85 mean −4.43%) — which is exactly Phase 7's scope. Any
momentum-guard/conviction change inspired by this window must be validated forward
(tuning on this window would break OOS discipline).

12w horizon not yet matured (first cohort matures 2026-07-17) — re-run grading later.

## 6. Cutover checklist (when the user green-lights)

1. Server env: add `LT_METRICS_VERSION=2` to the longterm_agent service env → provenance stamps
   `detection_version=0.2.0` automatically.
2. Nothing else changes: bucket cache shared; v1 library remains queryable under 0.1.0;
   graders grade both versions' rows.
3. Rollback = remove the env var (v1 default).
