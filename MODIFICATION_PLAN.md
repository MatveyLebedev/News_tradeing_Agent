# Modification Plan

Consolidated plan from the 2026-07-06 architecture + agentic-capabilities reviews.
Scope: repo engineering, agent correctness, detection quality, data breadth.
Out of scope: live trading/execution, merging the two agents, the Deep Agents harness (7B stays deferred).

How to read: phases are independently shippable, ordered by (impact ÷ effort) and dependency.
Each phase lists concrete file-level changes, acceptance criteria, and whether it changes
**detection labels** (which forces re-validation — see Ground Rules).

Revision 2 (2026-07-08):
- **Phase 0 is now "the data layer must work today"** — verified against the live DB
  (see findings inside); everything else is pointless on stale data.
- **Docker consolidation dropped** (per decision): existing per-service Dockerfiles and
  compose layout stay as they are.

---

## Ground rules (invariants every phase must preserve)

1. **PIT everywhere.** Any new data source is keyed by its original availability date
   (`filed_date`, publication time, snapshot `fetch_date`) and read `≤ as_of`. No exceptions.
2. **Deterministic decision spine.** Nothing LLM-produced may flip `has_shifted`, `direction`,
   or the risk-gate outcome. LLM output stays characterization + thesis only.
3. **Version-gated labels.** Any change that can alter `shift_flag` / `shift_score` / corpus
   membership is a **detection release**: bump `DETECTION_VERSION` (see Phase 3), re-run
   `backfill_shift_library` over history, and pass the WALK_FORWARD_PROTOCOL gates before
   trusting results. Advisory-only changes (deep analysis, thesis wording) bump
   `PIPELINE_VERSION` and ship without re-validation.
4. **Reproducibility.** Prompts stay byte-stable given identical inputs (sorted iteration,
   rounded numbers) so the content-addressed LLM cache keeps working.
5. **Honest reporting.** Small-n statistics carry intervals or are suppressed; anything the
   pipeline could not verify is labeled as such in output and persistence.

---

## Phase overview

| # | Phase | Effort | Labels change? | Depends on |
|---|-------|--------|----------------|------------|
| 0 | Data layer works **now** (DB health first) | S–M (1–2d, mostly ops) | no | — |
| 1 | Packaging & shared core | M (2–3d) | no | — |
| 2 | Automated freshness gate + security | M (1–3d) | no | 0, 1 |
| 3 | Agent quick wins (reflection, analogs, versioning) | S–M (1–2d) | no | — |
| 4 | Offline eval harness | M (2–3d) | no | 1 |
| 5 | Detection v2 (sentiment stats + semantic retrieval) | L (1–2w) | **yes** | 2, 3, 4 |
| 6 | Deep analysis v2 (planner, parallel, citations) | M (2–4d) | no (advisory) | 1, 3 |
| 7 | Conviction calibration | M plumbing now, fit later | gate only | 5 |
| 8 | LangGraph mechanics (deltas, batch resume, parallel branches) | M (2–3d) | no | 1 |
| 9 | Data breadth (filings, transcripts, insider, IV) | L (per source M) | advisory first | 2 |
| 10 | Intraday ↔ long-term memory bridge | S (0.5–1d) | no (advisory) | 1 |
| 11 | Learning-loop groundwork (targets, snapshots, dataset export) | M (2–4d now, tuning later) | no | 0, 3, 4 |

Suggested order: **0 immediately** (nothing downstream is trustworthy until the data layer
is healthy), then **1 → 2 → 3 → 4**, then 5 as the big validated release, with 6/8/10 in
parallel as they don't touch labels. 7 activates once 5 has accumulated graded outcomes.
9 is a rolling backlog, one source at a time.

---

## Phase 0 — Data layer works NOW (do first)

**Goal:** the databases and fetchers are verifiably healthy today; grading is unblocked.
Everything else in this plan builds on this.

**Live check, 2026-07-08 (read-only, via `.env`):**

| Check | Result | Verdict |
|---|---|---|
| Postgres (`trade_agent`, ext. port) | connected | ✅ |
| `news` max(published) | 2026-07-07, 31 941 rows | ✅ parser live |
| `fundamentals` max(fetch_date / filed_date) | 2026-07-04 / 2026-06-25 | ✅ weekly job alive again |
| `stock_prices_daily` max(date) | 2026-07-02 | 🟡 ~3 trading days behind |
| `stock_prices_daily` **SPY** max(date) | **2026-06-15** | ❌ benchmark frozen → grading vs SPY blocked |
| `stock_prices` (intraday) max(timestamp) | NULL (empty) | ⚠ dead table or broken fetcher — confirm intent |
| Neo4j | connected, 23 428 Articles | ✅ (note: Event dates include future values, max 2029-01-01 — known `e.date` unreliability; corpus is windowed on `news.published`, so detection is unaffected) |

Actions:
- **Fix SPY in `daily_price_fetcher`** — the universe resumed after 06-15 but SPY did not
  (likely dropped from the ticker list / separate code path). Backfill SPY 2026-06-15 → today.
- **Close the daily-price lag**: fetcher should land T+0 at 22:00 ET; check the container /
  schedule on the server, backfill the missing days for the whole universe.
- **Confirm the fundamentals job stays enabled** (`FETCH_FUNDAMENTALS=true`,
  `SEC_EDGAR_USER_AGENT` set) — data says it ran on 07-04; verify oplog/fetcher logs agree.
- **Decide `stock_prices` (intraday)**: if intentionally emptied by DB cleanup, document it
  and stop shipping the fetcher; if not, fix the price-fetcher service.
- **Re-grade stalled cohorts** once SPY is backfilled: run `grade_shifts` + `grade_results`
  for everything matured since 06-15; update the VALIDATION_REPORT caveat.

Acceptance:
- SPY and the full universe are ≤ 1 trading day behind, holding for 7 consecutive days.
- `grade_results` grades all matured cohorts (none stuck on missing benchmark).
- A one-page "data layer status" note (what runs where, on what schedule) added to DEPLOY.md.

**Status 2026-07-10 — done except redeploy:**
- ✅ Root cause: weekly job passed bare `TOP_50_TICKERS` (no SPY). Fixed:
  `DEFAULT_TICKERS = TOP_50 + BENCHMARK (SPY)` in `daily_price_fetcher.py`; weekly top-up
  `5d → 1mo`; connection retry added (flaky external link killed multi-ticker runs).
- ✅ Backfilled: SPY + 120 tickers fresh through 2026-07-09.
- ✅ Graders ran; audit: 0 live-table rows graded against frozen prices (contamination
  was confined to the wf_2026q2 backtest report). 5+5 rows pending maturity — normal.
- ✅ Intraday mystery solved: fetcher alive (5-min cadence), writes `historical_data_<ticker>`
  (120 tables); `stock_prices` is a dead legacy table → documented in DEPLOY.md §8.
- ✅ Fundamentals weekly job confirmed on schedule (Sat 03:00 ET, last run 07-04).
- ✅ DEPLOY.md §8 (data-layer map) + VALIDATION_REPORT addendum written.
- ⚠ **Open: rebuild/redeploy the `news-fetcher` image before Saturday 03:00 ET** — the SPY
  fix is local-only until then (server still runs the old code and will re-freeze SPY).
- Cosmetic: `SQ` delisted (Block → XYZ) — fails every fetch; fix when the universe list is
  next touched (label-affecting).

---

## Phase 1 — Packaging & shared core

**Goal:** a real Python package; delete all `sys.path.insert` hacks; one home for the
7 duplicated `DB_CONFIG`s, the 174 raw `psycopg2.connect` sites, and the 6 drifting
`requirements.txt`. **Docker layout stays as-is** (per decision: no single-image
consolidation) — Dockerfiles are only touched to install the package properly.

Changes:
- Add `pyproject.toml` (project name `trade-agents`, Python 3.12). Declare packages:
  `core`, `longterm_agent`, `news_agent`, `bloomberg_news_fetcher`, `graph_db`,
  `telegram_bot`. Optional-dependency groups: `[fetcher]`, `[agents]`, `[bots]`, `[dev]`.
  Adopt `uv` with a committed `uv.lock` (unpinned `langchain>=0.1.0` currently undermines
  the reproducibility the LLM cache was built for).
- New `core/` package:
  - `core/settings.py` — one pydantic-settings object (DB, Neo4j, OpenRouter, models,
    Telegram, LangSmith). Absorbs root `config.py` + `model_config.py`.
    **Decision needed:** canonical `DB_NAME` default (root config says `news`,
    longterm says `trade_agent` — today a silent wrong-DB footgun; live DB is `trade_agent`).
  - `core/db.py` — `get_conn()` context manager with TCP keepalives
    (`keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3`).
    This solves the stale-pooled-connection problem that motivated fresh-connection-per-call
    in `news_corpus_tool` / `fundamentals_tool` / `bucket_cache`, properly.
    Plus `fetch_all(sql, params)` / `execute(sql, params)` helpers.
  - `core/llm.py` — `make_llm` + `_StructuredFnChatOpenAI` (from `LongTermAgentConfig`)
    + `_invoke_structured` retry helper (from `longterm_agent/agent/nodes.py`).
  - `core/notify.py` — Telegram push (from `longterm_agent/notifier.py`).
- Delete every `sys.path.insert` (16+ files: all `longterm_agent` entry points,
  `telegram_bot/bot.py`, tests). `pip install -e .` in dev and in the existing Dockerfiles.
- Test config → `pyproject.toml`: `testpaths = ["news_agent/tests", "longterm_agent/tests"]`,
  drop `--disable-warnings`. Delete `pytest.ini`.
- CI: `.github/workflows/ci.yml` — `uv sync`, `ruff check` (new), `pytest` (DB-integration
  tests already self-skip). Runs on every push/PR.
- Hygiene sweep (same PR or follow-up): move concluded experiments (`profit_compute/`,
  `significance/`, `batch_analysis/`, `simple_agent/`, `forecast_model/`) to `experiments/`;
  root point-in-time MDs (SUCCESS.md, REFACTORING_SUMMARY.md, …) to `docs/archive/`;
  delete `env.example` (keep `.env.example`); delete stale `.claude/worktrees/` (59 MB)
  and root `batch_run_*.log` (~28 MB); rewrite root README as the system map.

Acceptance:
- `pytest` green from repo root with no DB and no `sys.path` mutation anywhere
  (`git grep "sys.path.insert"` → only `experiments/`).
- `python -m longterm_agent.run_longterm_agent --help` works from a clean venv after
  `uv sync && pip install -e .`.
- All existing docker services build and start exactly as before.

**Status 2026-07-10 — core done; hygiene sweep deferred:**
- ✅ `pyproject.toml` (packages, extras `[fetcher|agents|bots|dev]`, pytest + ruff config)
  + committed `uv.lock` (79 packages). `pytest.ini` deleted; one `pytest` runs both suites
  (95 passed), warnings no longer suppressed.
- ✅ `core/` shipped: settings (pydantic-settings; canonical DB_NAME=`trade_agent`),
  db (keepalives + retry), llm (`make_llm` + `invoke_structured`), notify (Telegram).
  Root `config.py` / `model_config.py` and `longterm_agent/notifier.py` are now shims.
- ✅ 17 `sys.path.insert` hacks removed; ruff `--fix` cleaned 112 orphaned imports.
  One deliberate hack remains (test_edgar_normalize — flat fetcher imports, commented).
  Regression caught & fixed: ruff dropped the side-effect `psycopg2.pool` import in
  news_agent's DBManager (restored as explicit `import psycopg2.pool`).
- ✅ CI workflow (`.github/workflows/ci.yml`) — dormant until the repo gets a remote.
- ✅ Container compat: `Dockerfile.news` now COPYs `core/`; pydantic-settings added to
  fetcher/telegram/longterm requirements (root shims import core).
- ⏳ Deferred to a follow-up commit: hygiene sweep (experiments/ move, docs/archive,
  README rewrite, stale worktrees + logs deletion) and migrating the 174 raw
  `psycopg2.connect` call sites onto `core.db` (incremental).

---

## Phase 2 — Automated freshness gate + security hardening

**Goal:** the pipeline can never again *silently* consume stale data (Phase 0 fixed today's
staleness by hand; this phase automates the guarantee); the DBs stop being open to the internet.

Changes:
- `core/freshness.py` — `check_freshness(as_of) -> {source: {max_date, age, stale}}` over
  `news.published`, `stock_prices_daily.date` (incl. SPY specifically), `fundamentals`
  (`filed_date` / `fetch_date`). Thresholds in settings (news ≤ 2d, prices ≤ 3 trading
  days, fundamentals ≤ 14d).
- Wire it in three places:
  1. `batch_run_longterm.py` preflight — stale source ⇒ abort batch + Telegram alert
     (via `core/notify`), oplog row.
  2. `corpus_collection_node` / `fundamentals_analysis_node` stamp per-source staleness
     into state; `risk_gate_node` adds a `stale-data` flag and caps conviction below the
     trade threshold (same mechanism as `_has_usable_fundamentals`).
  3. `grade_shifts.py` / `grade_results.py` — refuse to grade a cohort unless
     `max(price date) ≥ maturity date` (SPY included); mark `pending_data` instead.
     This makes the Phase-0 SPY incident structurally impossible to repeat unnoticed.
- Daily freshness ping in the stats bot (`/freshness` command).
- Security (ops runbook additions to `deploy/`, not code):
  - Put Postgres/Neo4j behind WireGuard/Tailscale, or at minimum firewall the external
    ports to known IPs. Moving ports off defaults is not access control.
  - Non-root SSH user; disable root login.
  - Scrub the server IP / `ssh root@…` / credential examples from tracked files
    (README.md, SIGNIFICANCE_EXPERIMENT.md, `scripts/*.sh`); rotate anything that ever
    appeared in git history.

Acceptance:
- Batch run against a DB with a frozen source aborts with an alert (test with a threshold
  override).
- Grader on an immature/priceless cohort produces `pending_data`, not a grade.
- `git grep` for the server IP → empty; DEPLOY.md documents the VPN/firewall setup.

**Status 2026-07-10 — code done; server-side hardening is the user's ops task:**
- ✅ `core/freshness.py`: pure `evaluate_freshness` + DB `check_freshness` (fail-closed on
  query errors), weekday-based price age, partial threshold overrides. Defaults in
  `core.settings` (news ≤2d, prices ≤5 weekdays — weekly cadence, no midweek false
  alarms; fundamentals ≤14d).
- ✅ Wired in all three places: batch preflight aborts + alerts + oplogs on stale (tight
  prices≤2 override — "the Saturday fetch must have happened"; `--skip-freshness` escape
  hatch; only enforced for live as_of); `risk_gate_node` caps conviction on a stale stamp
  (entry points stamp live runs only — backtests never gated); graders now require BOTH
  the stock and SPY series to REACH the exit date (closes the 7-day-tolerance hole that
  let the 06-15 freeze grade against stale closes) and report `pending_data` counts;
  `grade_results` now requires the benchmark (excess-vs-SPY IS the score).
- ✅ Stats bot: `/freshness` command.
- ✅ Server IP scrubbed from all tracked files (`git grep` → 0); DEPLOY.md §9 = hardening
  runbook (firewall/VPN, non-root SSH, rotation checklist).
- ✅ 103 tests green (8 new: freshness logic + graders' pending guard); live check: all
  sources currently fresh under both default and preflight thresholds.
- ⚠ Open (ops, not code): actually apply §9 on the server — firewall/VPN, deploy user,
  credential rotation.

---

## Phase 3 — Agent quick wins (no detection-label changes)

**Goal:** stop paying for a reflection loop that can't work, stop presenting small-n noise
as base rates, and make future label-changing releases survivable.

Changes:
- **Reflection advice actually consumed.** `reflection_node` writes
  `state["reflection_advice"]`; `shift_detection_node` (characterization prompt) and
  `fundamentals_analysis_node` append a `REVIEWER CRITIQUE TO ADDRESS: …` paragraph when
  present, and clear it after use. The deterministic shift label stays untouched by design —
  document in the node docstring that a loop can only refresh characterization/verdict.
  (Fallback option if we'd rather not pay for the loop at all: default `LT_MAX_REFLECTION=0`
  and delete the conditional edge. Decide at implementation; injection is the default plan.)
- **Analog honesty.** `find_similar_shifts` gains `min_analogs` (default 5): below it,
  return `{"n_analogs": n, "insufficient": true, note}` and the synthesis prompt renders
  "insufficient analogs (n=3) — do not weigh". At/above it, report `share_positive` with a
  Wilson 95% interval, rendered in the prompt (`4/6 positive, 95% CI 30–86%`).
- **Split versioning.** `AGENT_VERSION` → `DETECTION_VERSION` (gates `shift_library`
  comparability + `bucket_cache` keys) and `PIPELINE_VERSION` (provenance only). Today one
  version does both, so any pipeline tweak orphans the entire analog library. Migration:
  existing rows keep their version string as `DETECTION_VERSION`.
- **Magic numbers → config + provenance.** `PRICED_IN_MOVE_PCT` (8.0), `PRICED_IN_PENALTY`
  (0.2), `EARNINGS_PENALTY` (0.1), `ATTENTION_SPIKE_RATIO` (2.0), `MIN_ABS_DELTA` (0.2)
  move to `LongTermAgentConfig` (env-overridable) and into `provenance()`.
- **Pin stable model IDs.** Replace `-preview` defaults in `model_config.py`; document that
  a model change invalidates LLM-cache comparability (it's part of the cache key/provenance).
- **Deep-analysis loop off-by-one.** `_run_map_thread` currently breaks at
  `i == max_iter - 1` before invoking, so `_MAP_MAX_ITERS=3` yields 2 rounds. Make the
  semantics literal (N tool rounds + separate finalize) and keep the effective behavior
  explicit in the constant's comment.

Tests: Wilson CI math; min-n path renders "insufficient"; reflection advice appears in the
re-run prompt and is cleared after; provenance includes the new constants; version-split
migration keeps old analogs queryable.

Acceptance: full-pipeline run on one ticker shows (a) loop-back prompts containing the
critique, (b) analog line with CI or suppression. `PIPELINE_VERSION` bumped to 0.1.1;
`DETECTION_VERSION` unchanged at 0.1.0 — old shift library remains valid.

**Status 2026-07-10 — done:**
- ✅ Version split with a **no-migration alias**: `AGENT_VERSION == DETECTION_VERSION`
  (existing DB rows carry "0.1.0" = the detection identity), `PIPELINE_VERSION=0.1.1`
  lives in provenance. Shift-library-facing code (persistence, backfill, backtest_report,
  analog lookups) now names `DETECTION_VERSION` explicitly.
- ✅ Reflection critique consumed: `reflection_node` → `state["reflection_advice"]`;
  shift characterization + fundamentals prompts inject "REVIEWER CRITIQUE TO ADDRESS"
  targeted by `loop_to` and clear it after use.
- ✅ Analog honesty: `wilson_interval`, `min_analogs=5` (`LT_MIN_ANALOGS`), `insufficient`
  flag; `_analog_desc` renders CI when sufficient and SUPPRESSES the numbers with an
  explicit do-not-weigh instruction when not (LLMs anchor on any number shown).
- ✅ Magic numbers → config + provenance `knobs{}` (priced-in %, penalties, spike ratio,
  min_abs_delta, min_analogs; label-affecting ones flagged in README table).
- ✅ Model pinning: map/graph role → stable `gemini-3.1-flash-lite`; main model has no
  same-gen stable alias on OpenRouter (checked live 2026-07-10) — documented; generation
  jumps are eval-gated changes, not pins.
- ✅ Deep-analysis loop off-by-one fixed (literal `max_iter` tool rounds, `_MAP_MAX_ITERS=2`
  preserves effective behavior).
- ✅ 111 tests green (8 new in test_phase3.py), ruff clean.

---

## Phase 4 — Offline eval harness (before touching detection)

**Goal:** prompt/model changes measurable without a full backtest.

Changes:
- `evals/` package with three LangSmith datasets built from existing traces + cached runs:
  - `bucket_map`: bucket text → expected sentiment sign + theme keywords
    (~50 human-verified examples sampled across tickers/weeks).
  - `synthesis_matrix`: frozen synthesis inputs → expected direction, judged by
    **matrix consistency** (direction must match the news×fundamentals cell), not by
    realized outcomes (that would be fitting to market noise).
  - `thesis_quality`: LLM-as-judge rubric — grounded only in supplied evidence, concrete
    invalidation condition, no invented facts, cites at least one specific catalyst.
- `evals/run_evals.py` — runs all three against current prompts/models, prints deltas vs
  the recorded baseline; CI job runs it when a PR touches `agent/`, `tools/`, or
  `model_config` (gated on `LANGCHAIN_API_KEY` secret presence).

Acceptance: baseline scores committed (`evals/BASELINE.md`); a deliberately broken prompt
(e.g. inverted matrix) fails the `synthesis_matrix` eval.

**Status 2026-07-11 — done (one deliberate deviation: frozen JSONL in-repo instead of
LangSmith-hosted datasets — reproducible, no cloud dependency; `--push-langsmith` mirrors
them when wanted):**
- ✅ `evals/` package: `matrix.py` (pure news×fundamentals cell logic + scorer with
  low-conviction escape hatches), `build_datasets.py` (live DB → frozen
  `evals/datasets/*.jsonl`, rebuilt deliberately, versioned in git),
  `run_evals.py` (runs REAL node code — `_map_bucket`, `synthesis_node` — on the frozen
  cases; no DB needed at eval time).
- ✅ Datasets built: `bucket_map` 50 cases (ground truth = unambiguous graph-AFFECTS
  majority ≥0.75 with ≥4 labels; labels STRIPPED from inputs — prompt-leak guard;
  round-robin across tickers), `synthesis_matrix` 24 (8 synthetic canonical cells for
  full coverage + 16 real states with PIT re-reads), `thesis_quality` 5 (real directional
  theses; 4-criteria LLM-judge rubric).
- ✅ Baseline recorded (`evals/BASELINE.md`): bucket_map 0.5, synthesis_matrix 0.958,
  thesis_quality 0.7. Finding: bucket_map misses are one-sided — the map model reads
  positive weeks correctly (+0.5…+0.8) but hedges negative weeks to ~0.0 instead of
  going negative. First concrete prompt-tuning target for the Phase 11 loop.
- ✅ Harness sanity check (`--check-harness`): inverted matrix tanks the synthesis eval
  (normal 1.0 vs inverted 0.625) — the metric can tell a correct matrix from a broken one.
- ✅ `SYNTHESIS_MATRIX_RULES` extracted as a module constant in `nodes.py`
  (byte-identical text → LLM cache keys unchanged) so evals can reference/invert it.
- ✅ CI: `.github/workflows/evals.yml` on PRs touching agent/tools/llm/settings/evals;
  regression gate `--tolerance 0.05` vs baseline; skips gracefully without the key.
- ✅ 118 tests green (7 new in test_eval_harness.py: matrix cells, escape hatches,
  dataset integrity incl. no-leak guard), ruff clean.

---

## Phase 5 — Detection v2: robust sentiment stats + semantic retrieval  ⚠ label-changing

**Goal:** shift decisions computed on hundreds of article-level points instead of ~8 weekly
LLM scalars, over a corpus that doesn't miss product/subsidiary/supply-chain phrasing.
One detection release, one re-backfill, one walk-forward — do both halves together.

### 5a. Sentiment statistics v2
- New `background_metrics.py` v2 path (behind `LT_METRICS_VERSION=1|2`):
  - Primary series: **per-article** sentiment from `AFFECTS` edges (the `SENTIMENT_NUM`
    mapping already exists), daily-aggregated; articles without a graph label fall back to
    the bucket-level LLM sentiment.
  - Robust break test: recent-vs-baseline on **median/MAD** (or EWMA baseline), keeping the
    `min_abs_delta` magnitude guard and the attention-spike branch as-is.
  - This also decouples the *decision* from map-LLM variance — today a map-model change
    silently changes labels despite the deterministic framing.
- Prerequisite measurement: extend corpus provenance with **AFFECTS-sentiment coverage**
  (share of articles carrying a graph label). If coverage < ~60% on the target universe,
  keep hybrid weighting rather than pure article-level.

### 5b. Semantic retrieval path
- `news_corpus_tool` gains a third source `embed`: pgvector cosine search
  (query = embedding of "{company names} {ticker} {sector blurb}", threshold τ,
  `published ≤ as_of`), merged and deduped by `news.id`; provenance gains an `embed`
  counter. Verify an HNSW/IVFFlat index exists on `news.embedding` (1536-dim) before
  enabling per-ticker scans.
- Precision guard: manually audit ~100 embed-only hits across 8 tickers to set τ; embed-only
  articles are tagged in provenance so a bad τ is visible per run.

### Validation & migration (the actual work)
- Bump `DETECTION_VERSION` → 0.2.0.
- Re-run `backfill_shift_library` over the full history for the universe → v2 labels
  coexist with v1 rows (version column).
- Compare v1 vs v2: flag agreement rate, shift counts per ticker, manual review of every
  disagreement in the wf_2026q2 window.
- Run the full WALK_FORWARD_PROTOCOL gates (OOS, FDR, calibration) on v2 before it becomes
  the live default. Live cutover = flipping `LT_METRICS_VERSION` + noting it in provenance.

Acceptance: v2 walk-forward report exists alongside VALIDATION_REPORT.md; analog lookups
under v2 only match v2 rows; corpus provenance shows all three sources with the embed
share and coverage numbers.

**Status 2026-07-11 — done (one honest deviation: the embed source FAILED its precision
audit and ships disabled — full analysis in `longterm_agent/VALIDATION_REPORT_v0.2.md`):**
- ✅ Prerequisite measured: AFFECTS coverage **88.2%** on the universe (min GOOG 74.4%)
  → per-article series is primary; hybrid fallback carries ~12%.
- ✅ 5a shipped: `compute_shift_metrics(metrics_version=2, articles=…)` — per-article
  daily series (graph label ±1/0, unlabeled → bucket LLM sentiment), **median/scaled-MAD**
  break test, sparse guards (3 recent / 5 baseline article-days → sentiment branch
  abstains), zero-MAD coarse fallback (delta/0.3, same scale as v1). Attention branch,
  `min_abs_delta`, turnover, inflection identical to v1. Same output keys (+`estimator`,
  `n_*_days`, `label_coverage`, `sparse`, top-level `metrics_version`).
- ✅ 5b audit FAILED → embed source off in ALL versions (`LT_CORPUS_EMBED` experiment
  flag only): gemini-embedding-001 cosine encodes news genre, not company identity —
  known-relevant med sim ≈0.50 *below* sector chatter (0.55–0.60); Waymo-without-Google
  class inseparable; corpus-centroid variant also fails (bg p90 ≈ own median). Code path
  + HNSW verified (90.5% fill; June gap 68.5% noted). `core/embeddings.py` retries the
  flaky endpoint and raises after 4 attempts (labels must not depend on API weather).
- ✅ Version wiring: `LT_METRICS_VERSION` (default 1) → `DETECTION_VERSION` 0.1.0/0.2.0;
  `PIPELINE_VERSION=0.2.0`; bucket cache decoupled via `BUCKET_CACHE_VERSION="0.1.0"`
  (corpus+map prompt+map model identical across v1/v2 → cache shared, comparison isolates
  the estimator).
- ✅ Backfill + controlled comparison (`compare_detections.py`, one pass, same corpus &
  summaries): 677 decided grid points; v1 flags **39%** of ticker-weeks, v2 **22%**;
  agreement 72.1%; 23/24 direction flips were attention-triggered sign-noise; stored-v1
  graph-drift 1/17. v2 library: 152 rows under 0.2.0 (v1 rows untouched), 93 graded:
  directional hit-rate v1 41% (median −1.6%) vs v2 52% (median +0.1%).
- ✅ Manual review of all 7 wf-window disagreements → v2 correct or tie in every case;
  the v0.1 AMD "edge" shifts were Broadcom sector contagion, correctly rejected by v2.
- ✅ Walk-forward gates on v2 (`wf_2026q2_v0.2`, frozen config, OOS ✓): 2w +0.98%
  CI [−2.2,+4.5], 4w +0.64% CI [−2.7,+4.4], calibration non-monotonic, FDR 0/0,
  ESTIMATE n=13 — v0.1's apparent 2w edge does not reproduce (it was AMD concentration).
  Per protocol §7 neither version is a verdict; forward accumulation decides.
- ✅ 129 tests green (11 new in test_phase5.py), ruff clean.
- 🟡 **Live cutover awaits user go**: set `LT_METRICS_VERSION=2` in the server env
  (checklist in VALIDATION_REPORT_v0.2.md §6). v1 remains the code default until then.

---

## Phase 6 — Deep analysis v2 (advisory only; no label impact)

**Goal:** the "adaptive depth" the docstring promises, at lower latency, with verifiable
evidence.

Changes:
- **Planner step** (cheap model): given the shift context (direction, catalysts, sector,
  fundamentals verdict), select 2–5 threads from a menu and parameterize them (peer list,
  search focus). Menu = existing four + `legal_regulatory_exposure`, `supply_chain`,
  `management_change`. Hard cap on total threads + total tool calls per run.
- **Parallel threads** via `ThreadPoolExecutor(max_workers=4)` — threads are independent,
  each already opens fresh DB connections; add a shared OpenRouter rate-limit semaphore.
- **Citations.** `DeepAnalysisFinding` gains `evidence: list[{news_id | event_id, quote}]`;
  seed prompt requires citing tool outputs; findings with zero citations are dropped from
  the reduce and, if none survive, the advisory nudge is zeroed (extends the existing
  no-tools-ran guard). Citations persist with results.
- **Stop re-fetching the corpus.** `build_deep_analysis_tools` takes the state's
  `news_corpus`; `search_company_news` serves the company-under-analysis from it (peer
  queries still hit the DB).
- Log per-thread tool calls + duration to the oplog for cost visibility.

Acceptance: wall-clock for deep_analysis ≤ sequential baseline despite more threads;
every persisted finding carries ≥1 citation; planner output visible in `messages` and
LangSmith traces. `PIPELINE_VERSION` bump.

---

## Phase 7 — Conviction calibration (gate change only)

**Goal:** the 0.55 gate operates on a number that means something.

Changes now (plumbing):
- Deterministic interim conviction: `base_conviction = table[shift_score bucket ×
  fundamentals verdict]` (config matrix), adjusted by the existing advisory nudge and risk
  penalties. LLM conviction is still recorded (for later calibration study) but stops
  gating. A/B both gates in `full_signal_backtest` before switching.
- Persistence stores `conviction_raw` (LLM), `conviction_gate` (what gated), and the gate
  version.

Changes later (data-dependent, after Phase 5 accumulates ~80+ graded v2 signals):
- `longterm_agent/calibrate.py` — isotonic regression per `DETECTION_VERSION` on
  (conviction_gate, realized excess > 0); calibration artifact stored in DB with a
  fitted-on range; risk gate applies it; monthly re-fit in the scheduler; calibration curve
  in the stats bot.

Acceptance: backtest A/B report (deterministic vs LLM gate) checked into `docs/`; grading
tables carry both conviction columns.

---

## Phase 8 — LangGraph mechanics: deltas, batch resume, parallel branches

**Goal:** a crashed weekly batch loses nothing; independent nodes run in parallel.

Changes:
- Nodes return **deltas** (dict of changed keys) instead of mutating and returning the
  whole state; `messages` switches to an `Annotated[list, operator.add]` reducer.
- **Idempotent batch resume instead of a checkpointer** (decision 2026-07-08): before
  running a ticker, `batch_run_longterm.py` checks `longterm_results` for an existing
  (ticker, as_of, versions) row and skips it. A crash mid-batch (OOM, deploy, docker
  restart — the 18 GB disk incident has killed containers before) costs only the one
  half-finished ticker, re-run from scratch. No checkpoint tables, nothing to size-manage.
  Full `PostgresSaver` checkpointing stays a deferred option if intra-ticker resume ever
  becomes worth it (e.g. much longer deep-analysis runs).
- Fan out `fundamentals_analysis` ∥ `similar_shift_analysis` after `shift_detection`,
  join at `deep_analysis` (both are independent; requires the delta refactor first).
- Backtests unchanged: no resume machinery (determinism + LLM cache already cover replay).

Acceptance: `kill -9` mid-batch, rerun → batch completes, already-done tickers skipped,
no duplicate rows; graph-structure test asserts the parallel branch; per-node delta unit
tests.

---

## Phase 9 — Data breadth (rolling backlog; one source at a time)

Priority order, each PIT-first, each registered in the Phase 2 freshness checker,
each starting **advisory-only** (deep-analysis tool) before any promotion into the
detection corpus (promotion = Phase 5-style detection release):

1. **8-K / press releases** — EDGAR full-text search, keyed by `filed_date` →
   `filings` table + `get_recent_filings` tool. Free, true PIT, immediate value.
2. **Insider transactions (Form 4)** — EDGAR, PIT by filing → `insider_trades` table +
   tool; later a risk-gate feature (cluster selling against a LONG).
3. **Earnings call transcripts** — richest narrative source; **decision needed** on
   provider (free scrapes have licensing risk; FMP/API-Ninjas are cheap paid options).
   Tool first; candidate for weekly-bucket enrichment later.
4. **Options-implied vol** — snapshot yfinance option chains forward-PIT (same pattern as
   `fundamentals_fetcher`); upgrade `priced_in_check` from realized-move-only to
   IV-percentile ("is the event already in the options?").
5. **Short interest** (FINRA bi-monthly) — small table, slow-moving context feature.
- Also: turn `FETCH_FULL_TEXT=true` for the corpus universe (map summaries currently see
  headline+snippet only) — storage cost is modest, benefit flows to every bucket summary.
  Corpus-affecting ⇒ bundle with a detection release.

---

## Phase 10 — Intraday ↔ long-term memory bridge

**Goal:** the long-term agent can see how the market *actually reacted* to each significant
event in its window, from data the intraday agent already grades.

Changes:
- Deep-analysis tool `get_intraday_reactions(ticker)` — reads the intraday results table
  for significant events in `[as_of − lookback, as_of]`: headline, predicted direction,
  realized short-horizon move. Read-only, as-of-windowed, advisory.
- Reduce prompt gains an optional "realized-reaction check" line.

Acceptance: tool returns graded events for a ticker with intraday history; cited in
findings like any other evidence.

---

## Phase 11 — Learning-loop groundwork (prompt-tuning / fine-tuning later; collect NOW)

**Goal:** in 6–12 months, be able to optimize prompts (and possibly fine-tune per-node
models) against realized outcomes. The tuning itself is future work; what must start now
is the data discipline, because training data can only be collected forward.

Groundwork (do now, cheap):
- **Full state snapshot per run.** New Postgres table `longterm_run_snapshots`
  (result_id FK → results row, ticker, as_of, detection/pipeline versions, state JSONB,
  created_at) in the same `trade_agent` DB. Store **references, not copies**: data that
  already lives in other tables goes in by ID (corpus = news ids, fundamentals = row keys);
  LLM outputs (bucket summaries, profile, shift characterization, verdicts, findings,
  signal) are stored in full. ≈30–80 KB per run → ~100–200 MB/year at 50 tickers/week;
  keep forever (it IS the future training set); register in `db_cleanup_report.py`;
  add a pg_dump backup job for the DB if none exists.
- **Multi-target grading.** Extend grading beyond 4w-vs-SPY to a per-node target set:
  - price: excess vs SPY *and* vs sector ETF at 2/4/12w, max drawdown, invalidation-triggered flag;
  - fundamentals: next-quarter realized revenue/margins vs the `confirms/contradicts`
    verdict (grades the fundamentals node directly);
  - news background: did the detected shift persist or mean-revert over the horizon
    (grades shift characterization);
  - per-article: realized short-horizon reaction already sits on `AFFECTS.move_1d` —
    usable as ground truth for map-sentiment.
- **`export_training_data.py`** — continuous job joining state snapshots + outcome targets
  into versioned JSONL datasets (per node, per DETECTION_VERSION).
- **First consumer — dynamic few-shot (no training).** Retrieve k graded analogs with
  their outcomes into the synthesis prompt. Advisory, PIPELINE_VERSION bump, measurable
  via Phase 4 evals + backtest A/B.

Future stages (explicitly deferred, gated by WALK_FORWARD_PROTOCOL):
- Automated prompt optimization (DSPy/GEPA-style search) scored on Phase 4 evals +
  outcome datasets; time-based OOS split mandatory.
- Per-node LoRA fine-tuning (map summarizer, fundamentals verdict) on open-weights models
  via a tunable provider — OpenRouter itself doesn't fine-tune; `make_llm`'s base_url
  swap keeps this a config change. DPO pairs over SFT where labels are outcome-derived;
  any hindsight-generated "correct answer" must be constructible from ≤ as_of inputs only.

Acceptance (groundwork): every new run stores a replayable snapshot; grading tables carry
the multi-target columns; export produces a non-empty dataset for ≥2 nodes.

---

## Open decisions (flagged, non-blocking)

| Decision | Options | Default if not decided |
|---|---|---|
| Canonical `DB_NAME` | `news` vs `trade_agent` | `trade_agent` (what the live DB uses) |
| Reflection loop | inject advice vs remove | inject (Phase 3) |
| Lock tooling | uv vs pip-tools | uv |
| Transcript provider | FMP / API Ninjas / defer | defer to Phase 9.3 |
| Embed threshold τ | tune on audit sample | **RESOLVED 2026-07-11: audit failed — no usable τ exists in this embedding space; source ships disabled** (VALIDATION_REPORT_v0.2.md §4) |
| Intraday `stock_prices` table | document-as-dead vs fix fetcher | decide in Phase 0 |

## Explicitly not in this plan

- Live order execution / brokerage integration.
- Merging `news_agent` and `longterm_agent` into one framework (shared `core/` only).
- Single-Docker-image consolidation (decided against — per-service images stay).
- Deep Agents harness (7B) — unchanged, deferred.
- Full-universe replay and regime-diverse validation — governed by
  WALK_FORWARD_PROTOCOL.md / BACKTEST_PLAN.md, not re-planned here; Phase 5's gates feed it.
