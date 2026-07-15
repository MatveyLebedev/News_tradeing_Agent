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
- ✅ Wide-universe check (2026-07-11, post-release): 30 tickers × 9 cohorts, 20-worker
  parallel harness (`backtest.py --workers`). v2 beats v1 on every headline (4w: win 55%
  vs 49%, median +1.05% vs −0.95%, Sharpe +0.57 vs −0.29) while trading 40% less; the 35
  v1-only trades sum to −42.8%. Residual v2 losses = two top-conviction counter-trend
  SHORTs (INTC/QCOM) shared with v1 → conviction/gate problem, Phase 7's scope
  (VALIDATION_REPORT_v0.2.md §5b).
- 🟡 **Live cutover awaits user go**: set `LT_METRICS_VERSION=2` in the server env
  (checklist in VALIDATION_REPORT_v0.2.md §6). v1 remains the code default until then.

---

## Phase 5.5 — Exit discipline (added 2026-07-11 from wf_30x evidence)

**Goal:** cap the unbounded per-trade left tail. The wf_30x decomposition showed the
strategy earns small and loses catastrophically: two counter-trend SHORTs (INTC −41%,
QCOM −37%) erased more than the other 42 trades earned (+53.7%). The pipeline generates
invalidation conditions but nothing enforces them; trades ride to the fixed horizon.

Changes:
- `exit_rules.py`: structural stop-loss on the RAW direction-adjusted position return,
  simulated on daily closes (exit at breach-day close; gaps exit beyond the level).
  Knobs `LT_STOP_LOSS_PCT` (default 0.10) / `LT_TAKE_PROFIT_PCT` (default 0 = off) in
  provenance. In-sample sweep was positive across 6–12% → 10% is a round prior, not a
  fitted optimum; TP measured useless at 4w (1 trade in 44 reached +20%).
- Graders fill **BOTH books** on every matured trade — hold-to-horizon (unchanged
  columns) + stopped (`fwd_return_stopped`, `benchmark_return_stopped`, `exit_reason`,
  `exit_days`; backtest_runs also `excess_stopped`) — in `backtest.py grade()` (with
  idempotent backfill of already-graded rows) and `grade_results.py` (live daily job).
- Backtest report prints the stopped book per horizon next to the hold book.
- Live daily scan (`grade_results.scan_open_breaches`): open traded rows whose position
  return breached the stop → one-time Telegram alert (`exit_alert_at`).
- `PIPELINE_VERSION` → 0.4.0 (trade semantics; detection untouched).

Validation: **deliberately no separate campaign** (user decision 2026-07-11). The
in-sample A/B ran in-session (wf_30x v2 4w: hold −23.9% sum / worst −40.8% → stopped
+11.9% / −15.1%; whipsaw cost 2 of 7 stops ≈ −12.5% vs +49.6% saved; v1's book stays
negative under stops — stops don't fix selection). Forward validation is AUTOMATIC:
every matured live trade grades both books, so the hold-vs-stopped comparison accrues
without extra runs. CI on the stopped book still includes zero (+0.27% [−2.3%, +2.8%]) —
the stop caps tails; it does not prove edge.

**Status 2026-07-11 — done:** all of the above shipped; 9 pure simulator tests
(breach-day close, gap-through-stop, TP off by default, entry conventions); stopped book
backfilled onto wf_30x_v0.1/v0.2/g2ct; report verified against the ad-hoc analysis.

---

## Phase 6-pre — MEASURE the current deep analysis first (added 2026-07-12)

Deep analysis has NEVER been measured: every backtest ran `LT_DEEP_ANALYSIS=false`
(config: "OFF in backtest, ON live") — so its ±0.2 advisory nudge affects live trades
while being absent from every validated number (a live/backtest asymmetry). Before
investing in Phase 6, run the A/B: `wf_30x_v0.2_deep` (same 30-ticker grid,
`LT_METRICS_VERSION=2 LT_DEEP_ANALYSIS=true`) vs the `wf_30x_v0.2` baseline. Decide:
(a) nudge helps → Phase 6 justified; (b) noise/harm → turn it OFF live for symmetry and
skip/deprioritize Phase 6. **First attempt 2026-07-12 was destroyed by the OpenRouter
weekly key limit (403 mid-sweep, fallback-NEUTRAL contamination) — rows deleted, must
re-run on a healthy key. Run it from a pre-Phase-12 checkout (commit `1162a76`'s parent
`4d148aa`) OR accept the fundamentals-v2 confound and compare against wf_30x_f12 instead.**

**VERDICT 2026-07-15 — (a): the nudge HELPS (docs/BACKTESTS_2026Q2_PH6_12_13.md §2).**
Re-run clean (zero 403s), baseline = wf_30x_f12 (same tree, confound resolved). 4w:
win 57%→62%, mean +0.53%→+1.78%, Sharpe 0.91→1.30, stopped book +0.71%→+2.04%,
calibration turns monotonic; 2w same direction. The ±0.2 nudge dropped 7 trades and
improved every aggregate — it filters bad trades. Live deep ON validated; Phase 6
deep-v2 A/B (queued #4) justified. CIs include zero (one regime) — directional, not proof.

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

**Status 2026-07-12 — CODE DONE behind `LT_DEEP_V2` (default OFF; cutover waits on the
6-pre A/B verdict + a deep-v2 backtest):**
- ✅ Planner (map model, structured `DeepAnalysisPlan`): picks 2-5 threads from the
  7-thread menu (v1's four + legal_regulatory_exposure / supply_chain /
  management_change) with a one-line `focus` each; ANY planner failure falls back to the
  v1 default four (planner can redirect effort, never lose it); plan + rationale visible
  in `messages` (`deep_analysis_planner`) and persisted in `enriched_analysis.planner`.
- ✅ Parallel threads: `ThreadPoolExecutor(max_workers=4)` per run + a PROCESS-WIDE
  semaphore (`LT_DEEP_LLM_CONCURRENCY`, default 8) around every deep LLM call so backtest
  workers × thread fan-out can't multiply into an OpenRouter stampede.
- ✅ Citations: map finalize is `DeepAnalysisFindingV2` (`evidence[{news_id, event_id,
  quote}]`); uncited findings DROPPED; tools-ran-but-zero-cited → advisory nudge zeroed
  (extends the v1 no-tools guard); cited findings persist in
  `enriched_analysis.map_findings` (results JSONB, no migration).
- ✅ Corpus reuse: `build_deep_analysis_tools(news_corpus=…)` — company-under-analysis
  searches serve from the state's corpus (peers still hit the DB) and headlines carry
  `news_id` only in v2 (v1 tool outputs stay byte-identical → v1 LLM-cache keys intact).
- ✅ Per-thread oplog (`deep_thread`: tools, duration, n_evidence). PIPELINE_VERSION
  0.7.0; `deep_analysis`/`deep_v2` stamped into provenance knobs. 5 new tests
  (test_deep_v2.py) + v1 suite untouched and green.
- ❌ **A/B VERDICT 2026-07-15: deep v2 REJECTED** (`wf_30x_f12_deepv2`, ledger #11):
  4w mean −0.91% / Sharpe 0.39 vs deep-v1's +1.78% / 1.30 and f12's +0.53% / 0.91 —
  loses to BOTH baselines (2w −0.96%, Sharpe −0.04; 4w SHORT book −4.75%).
  `LT_DEEP_V2` stays false; live keeps deep v1 (the 6-pre champion). Post-mortem
  hypotheses (only if ever revisited): the planner skips core angles v1 always covers
  (force-include sector_breadth?), and the citation guard zeroes nudges v1 would have
  used to filter bad trades. Minor confound noted: v2 ran with the (empty-as-of)
  consensus tool in the toolset; irrelevant to the verdict since v2 < no-deep f12.
  The v2 machinery stays in the codebase behind the flag — measured, documented, off.

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

**Status 2026-07-11 — plumbing done; A/B verdict: DO NOT switch (docs/GATE_AB_2026Q2.md):**
- ✅ Gate machinery shipped behind `LT_CONVICTION_GATE=llm|table` (default `llm` — live
  behavior unchanged): deterministic `CONVICTION_TABLE[verdict][shift_score bucket]` +
  advisory nudge; LLM conviction recorded as `signal.conviction_raw`, gated value as
  `signal.conviction_gate` + `signal.gate`; scalar `conviction` column = gating value
  (rides in signal JSONB, no schema migration). `PIPELINE_VERSION` → 0.3.0.
- ✅ NEW counter-trend guard (−0.2 when fighting a ≥50% trailing 180d move; structural
  prior, values not fitted): stays ON — removed zero trades under the llm gate on wf_30x
  while protecting the mid-conviction band. Knife-edge limitation documented: LLM 0.75+
  eats a flat −0.2 and still clears 0.55 (INTC did exactly this).
- ✅ A/B 2×2 on the wf_30x grid (cache replay, identical signals): **table gate REJECTED**
  — it kept 22 trades summing −63.6% and dropped 24 summing +39.7% (kept confirms-cell
  SHORTs into a bull market, dropped the LLM's profitable contradicts-cell contrarian
  LONGs). table+guard was best-behaved (2w mean +1.14%, worst −11.3%, n=20) but 4w still
  negative and single-regime → burden of proof for switching not met. QCOM-class losses
  (by-the-book SHORT squeezed by a new catalyst) are irreducible by gates.
- ✅ 140 tests green (11 new in test_phase7.py), ruff clean.
- ▶ "Later" step unchanged and now unblocked by data: isotonic calibration
  (`calibrate.py`) once ~80+ graded v2 signals accumulate live — calibrate the number,
  don't replace the selector. Re-examine the table after a second regime.

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

**Status 2026-07-15 — DONE:**
- ✅ Delta refactor: all 9 nodes return only their changed keys and never mutate input
  state; `_msg` → `_msg_entry` (messages ride in deltas); `messages` carries an
  `operator.add` reducer; `sector_baseline`/`reflection_advice` declared in the schema;
  reflection-advice "consume and clear" reimplemented as delta overwrite.
- ✅ Parallel branches: shift_detection fans out to fundamentals_analysis ∥
  similar_shift_analysis with a BARRIER join at deep_analysis
  (`add_edge([both], deep)`). Reflection loop_to=fundamentals routes through a
  `refine_fanout` passthrough that re-fans BOTH branches — re-running one alone would
  deadlock the barrier (the analogs branch is LLM-free, so the re-run is cheap).
- ✅ Idempotent batch resume: `batch_run_longterm` skips tickers already persisted for
  (DETECTION_VERSION, as_of); `--force` overrides. Demonstrated live: a repeat batch
  over already-persisted tickers = "skipping 2 … Batch complete in 5s, runs: 0".
- ✅ Backtests untouched (their own done-set skip + cache replay already cover this).
- ✅ 6 new tests (test_phase8.py: delta contracts, input immutability, graph fan-out/
  barrier/refan edges, messages reducer, resume filter); deep tests updated to the
  delta contract (assert input unmutated instead of echoed state); live cached smoke
  through the new graph (NVDA 06-19: both branches → synthesis → risk_gate).
  Suite 200 green, ruff clean.

---

## Phase 9 — Data breadth (rolling backlog; one source at a time)

Priority order, each PIT-first, each registered in the Phase 2 freshness checker,
each starting **advisory-only** (deep-analysis tool) before any promotion into the
detection corpus (promotion = Phase 5-style detection release):

1. **8-K / press releases** — EDGAR full-text search, keyed by `filed_date` →
   `filings` table + `get_recent_filings` tool. Free, true PIT, immediate value.
   **✅ SHIPPED 2026-07-15** (`filings_fetcher.py`): 8-K/8-K/A + dilution forms
   (S-1/S-3/424B5), PRIMARY doc text + first EX-99 exhibit (usually the press release —
   our only legal full text), first-release-only, daily 19:30 ET job, 365d backfill;
   advisory deep tool `get_recent_filings`. Known bound: submissions API "recent" block
   (~1000 filings) may not span a full year for the heaviest filers.
2. **Insider transactions (Form 4)** — EDGAR, PIT by filing → `insider_trades` table +
   tool; later a risk-gate feature (cluster selling against a LONG).
   **✅ SHIPPED 2026-07-15**: Form 4 ownership-XML parse (code/shares/price/role,
   P/S vs awards), same daily job; advisory tool `get_insider_activity` with
   open-market buy/sell aggregates + cluster-selling flag (≥3 distinct sellers).
   Risk-gate promotion stays future (advisory-first discipline).
3. **Earnings call transcripts** — richest narrative source; **decision needed** on
   provider (free scrapes have licensing risk; FMP/API-Ninjas are cheap paid options).
   Tool first; candidate for weekly-bucket enrichment later. **Still BLOCKED on a paid
   key (FMP free tier 402s transcripts — probed 2026-07-15); one key → ~1h to wire.**
4. **Options-implied vol** — snapshot yfinance option chains forward-PIT (same pattern as
   `fundamentals_fetcher`); upgrade `priced_in_check` from realized-move-only to
   IV-percentile ("is the event already in the options?").
   **✅ SHIPPED 2026-07-15** (`options_fetcher.py`): daily 17:30 ET ATM call/put IV per
   3 nearest expiries + put skew + P/C open interest → `options_iv` (first-release,
   forward-only by nature); advisory tool `get_options_positioning` (+30d IV change).
   The priced_in_check IV-percentile upgrade WAITS for forward history to accumulate.
5. **Short interest** (FINRA bi-monthly) — small table, slow-moving context feature.
   **✅ SHIPPED 2026-07-15**: FINRA figures ride the weekly yfinance info snapshot
   (sharesShort/priorMonth, shortRatio, shortPercentOfFloat, dateShortInterest in
   INFO_KEYS); advisory tool `get_short_interest`. No separate FINRA-file parser needed.
- ~~Also: turn `FETCH_FULL_TEXT=true` for the corpus universe~~ **NOT VIABLE (user,
  2026-07-15): full-text scraping doesn't work with the current parser — the RSS sources
  (Bloomberg/CNBC/MarketWatch) block or paywall article bodies, so the flag would fetch
  little beyond what snippets already give.** Map summaries stay headline+snippet.
  Consequences: (a) LLM guidance-extraction (#40-41 text side) does NOT ride on news
  full-text — it rides on 9.1 EDGAR 8-K/press releases, which are true full text, free
  and legal; (b) new backlog item 9.6 below.
6. **New full-text-friendly news sources** (9.6, only if corpus depth becomes the
   bottleneck): evaluate sources whose BODY is legally fetchable — company IR/PR feeds
   (PR Newswire / Business Wire / GlobeNewswire RSS), EDGAR 8-K exhibits (covered by
   9.1), free news APIs with licensing that permits storage. Each candidate goes through
   the usual gauntlet: PIT timestamps, dedup vs existing feeds, freshness registration,
   advisory-first; corpus-affecting ⇒ bundle with a detection release.

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

**Status 2026-07-15 — DONE, plus a production bug found and fixed on the way:**
- ✅ `tools/intraday_tool.py`: `read_intraday_reactions` joins `agent_results` calls
  (12.9k rows, 2026-03..06) with realized `AFFECTS.move_1d` from the graph → per-event
  predicted-vs-realized + hit rate; as-of-windowed, advisory deep tool
  `get_intraday_reactions`. Reduce-prompt line SKIPPED on purpose (deep v1 prompts are
  LLM-cache keys; the LLM can call the tool). 3 new tests.
- 🐞 **Found: `AFFECTS.move_*` was NEVER populated in production (0 of 23,166 edges)**
  despite healthy inputs (96% SOURCED_FROM.published_at, 120 intraday tables, fresh
  bars) — Phase 11's per-article ground-truth assumption was false. Two causes in
  `update_price_movements`: (a) unbounded 23k-row aggregation selector dies on the
  server's Neo4j before writing anything (silent ⚠ every 5-min cycle); (b) selector
  keyed on `move_15m IS NULL` dropped fresh events from the queue once the 15m window
  filled, so `move_1d` could never fill for live events.
- ✅ Fixed: batched selectors (`EVENT_MOVES_BATCH`, default 300/cycle), keyed on
  `move_1d IS NULL` with a `move_checked_at` 4h retry cooldown + a `move_unavailable`
  TOMBSTONE for edges that can never fill (junk e.date — the graph carries 1978..2027;
  tickers without historical tables; pubs older than intraday coverage, which starts
  2026-02-23 and only accumulates forward). Verified live: real moves fill, junk
  tombstones once. **Local backlog drain ABANDONED (remote link ≈ 4.6s/edge → 30h+);
  the SERVER drains its own backlog in ~6h of normal 5-min cycles after the fetcher
  deploy — add "verify AFFECTS move coverage" to the deploy checklist.**
- Note: intraday agent's last `agent_results` row is 2026-06-06 — check the news_agent
  container on deploy day (bridge data stops there until it runs again).

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

**Status 2026-07-15 — GROUNDWORK DONE (tuning itself stays future work):**
- ✅ `snapshots.py` + `longterm_run_snapshots` (UNIQUE detection_version+ticker+as_of,
  upsert): references-not-copies (corpus → news ids; deterministic re-reads keyed by
  (ticker, as_of) + provenance.git_revision), LLM outputs IN FULL (bucket summaries,
  profile, shift, verdicts, findings, signal, node messages). Wired into `persist_run`
  (best-effort, result_id soft ref). Registered KEEP-FOREVER in db_cleanup_report.py
  (with macro_series). pg_dump job: still an ops TODO on the server.
- ✅ Multi-target grading (`grade_results.py` + pure classifiers in `targets.py`):
  2w/12w return pairs alongside the 4w book; `sector_benchmark`(+`_return_4w`) vs the
  sector ETF (SECTOR_ETF map; ETFs added to the daily fetcher benchmarks + 2y local
  backfill); `worst_position_return_4w` (max adverse excursion); `aux_targets` JSONB =
  `shift_outcome` (post-window AFFECTS daily means → persisted/reverted/mixed — same
  estimator family as detection v2) + `fundamentals_realized` (next filed quarter ≥120d
  later vs the verdict, ±2pp revenue / ±1pp margin thresholds). Invalidation-trigger
  flag = the Phase 5.5 stopped book (`exit_reason`), already graded per trade.
- ✅ `export_training_data.py`: snapshots ⋈ graded results → per-node JSONL
  (synthesis / fundamentals / shift), versioned filenames, hindsight guard (inputs from
  snapshot only, targets from grader columns only). `longterm_agent/exports/` gitignored.
- ✅ First consumer: dynamic few-shot behind `LT_FEWSHOT` (default OFF — prompt is an
  LLM-cache key): graded `nearest_cases` rendered into the synthesis prompt; insufficient
  analogs never shown. PIPELINE_VERSION → 0.8.0; flag in provenance knobs.
- ▶ Data accumulates from the NEXT live/batch runs (backtests don't snapshot by design —
  they don't call persist_run). Few-shot A/B + any prompt optimization stay gated on
  WALK_FORWARD_PROTOCOL once ≥2 nodes have non-empty exports.

---

## Phase 12 — Fundamentals interpretation v2 (wire STORED fields; zero new data)

**Origin:** expert indicator checklist (`docs/EXPERT_INDICATORS.md`) × wf_30x evidence.
The INTC/QCOM blowups were "confirms" cells judged on revenue+margins+valuation alone —
the verdict is too coarse, while the richer filed picture is ALREADY in the DB (Table B
fields fetched by the EDGAR/yfinance pipelines but never surfaced to the agent).

Changes (expert items #43–47, #49–50, #53–54 — all STORED or computed-from-stored):
- `read_fundamentals` / state gains: true PIT FCF (OCF − capex), capex intensity
  (capex/OCF), capital return (buybacks+dividends vs FCF), PIT leverage (debt split,
  current ratio), R&D intensity + SG&A trend, EPS trajectory slope (sequential
  `eps_basic` path), analyst target gap + `recommendationMean`, beta / 52w distance.
- **Relative multiple gap** (#49): forward P/E vs peer-group median + percentile
  (peer groups from the existing sector map) — "expensive vs its peers" ≠ "expensive".
- Fundamentals prompt: structured blocks (growth / profitability / balance sheet /
  capital allocation / street positioning); schema records per-block sub-verdicts for
  the future calibration study; the headline verdict stays confirms/contradicts/ignores
  (matrix unchanged).
- Validation: wf_30x A/B under a fresh backtest id vs v0.2 baseline (prompts change →
  new LLM calls, moderate cost); `synthesis_matrix` eval must not regress; new pure
  tests for each derived metric (PIT: filed/fetched dates only).
- `PIPELINE_VERSION` bump; detection untouched (verdict feeds synthesis, not the shift
  label).

Acceptance: fundamentals prompt shows all blocks with PIT values; sub-verdicts persisted;
A/B report vs v0.2 checked into docs/; no synthesis_matrix eval regression.

**Status 2026-07-12 — code shipped (commit `1162a76`), A/B pending key-limit recovery:**
- ✅ All blocks wired (capital/balance/opex/eps_trajectory/street/peer_multiples), signs
  verified normalized; sub-verdicts in schema; prompt structured; 7 pure tests; NVDA live
  smoke (FCF $74.8B, fwd P/E at 28th pct of 25 sector peers). `PIPELINE_VERSION` 0.5.0.
- ⏳ wf_30x_f12 backtest queued (see "Queued backtests" below).

---

## Phase 13 — Sector-relative shift detection (detection v3 candidate) ⚠ label-changing

**Origin:** the AMD/Broadcom failure — the dominant observed news-interpretation error is
labeling a SECTOR wave as a company shift (graph AFFECTS fan-out tags peer headlines onto
the company; the weekly map summaries amplify sector gloom). Detection v2 dampened this
statistically; this fixes it structurally. Not in the expert checklist — derived from our
own test evidence; complements (not replaces) the advisory `is_sector_wide` in deep
analysis.

Changes:
- Sector baseline series: median daily AFFECTS-label sentiment across the ticker's
  sector peers (same data as v2, computed across tickers; sector map exists).
- Company **excess series** = company daily mean − sector baseline; the median/MAD break
  test runs on the excess series. Report both `company_break` and `sector_break` flags —
  a sector-wide break is CONTEXT (risk gate / synthesis), not a company shift.
- Full Phase-5 playbook (this is why it's cheap now): `LT_METRICS_VERSION=3` →
  `DETECTION_VERSION 0.3.0`; bucket cache shared (corpus/map unchanged);
  `compare_detections` v2-vs-v3 on the full grid; manual review of disagreements;
  wf gates under a fresh backtest id; cutover only after review.
- Prediction to verify: the AMD 06-04/06-11 cohorts must flag `sector_break`, not
  `company_break`.

Acceptance: v2-vs-v3 comparison report; wf_30x_v0.3 gates alongside v0.2; the
sector-contagion cases from VALIDATION_REPORT_v0.2.md §3 re-classified correctly.

**Status 2026-07-12 — code COMPLETE but UNCOMMITTED (tests deferred by user):**
- ✅ Written: `sector_map.py` (static groups, **industry granularity** — the live AMD
  check proved a sector-level median too coarse: the Broadcom wave hit Semiconductors
  while the 26-name Technology median stayed flat, so baseline groups are industries
  with ≥4 members, semis-equipment merged into the semis complex, <4-member leftovers
  unmapped → honest v2 degradation; GOOGL/GOOG twins never each other's peers),
  `tools/sector_sentiment.py` (median-of-peers daily baseline, graph labels only —
  LLM-free by design; process cache per (group, as_of)), metrics v3 in
  `background_metrics.py` (excess series + `sector_break` context flag),
  nodes wiring (baseline computed in corpus_collection when `LT_METRICS_VERSION>=3`),
  `compare_detections --versions OLD NEW` (writes 0.3.0 rows), `config` mapping
  3→"0.3.0", tests in `test_phase13.py` (AMD-shape suppression, idiosyncratic
  survival, degradation, map integrity).
- ✅ RESUMED & DONE 2026-07-15: committed `82f1ff8` (suite green); live AMD re-check
  resolved the prediction — the 06-04/06-11 "wave" was AVGO-idiosyncratic (fresh v2
  doesn't flag it; the peer MEDIAN is robust to one-name events by design), while the
  REAL July semis wave (07-02 sector z=−6.88) is where v3 suppressed v2's false AMD
  company-shift and stamped `sector_break` — the structural fix works where it matters.
- ✅ Full-grid compare + wf_30x_f12_v3 run (see "Queued backtests" status / docs report).
  **VERDICT: no cutover yet — live stays `LT_METRICS_VERSION=2`.** Decision-level ≈ wash
  (label changes concentrate in non-traded cells; 2w book degrades), and v3's showcase
  window (July) is outside the graded grid. Re-examine ~2026-08-06 when the 07-02/07-09
  cohorts mature (0.3.0 shift_library rows are in and grading forward).

---

## Queued backtests (waiting on user "go" after key fix; 2026-07-12)

OpenRouter weekly key limit killed the first deep A/B mid-sweep (403 → fallback-NEUTRAL
contamination; wf_30x_v0.2_deep rows DELETED). Key has since been fixed per user.

**Status 2026-07-15 — ALL THREE COMPLETE (zero 403s; report:
docs/BACKTESTS_2026Q2_PH6_12_13.md):** #1 wf_30x_f12 ✅ (+1.07pp 4w mean vs v0.2 —
Phase 12 validated) · #2 wf_30x_v0.2_deep ✅ (deep nudge HELPS — 6-pre verdict (a)) ·
#3 compare v2-vs-v3 ✅ (100% baseline coverage; July AMD semis wave correctly
reclassified as sector_break) + wf_30x_f12_v3 ✅ (decision-level ≈ wash → **NO cutover;
stay LT_METRICS_VERSION=2**; re-examine ~2026-08-06 when the July sector-wave cohorts
mature) · #4 deep-v2 justified by #2, still queued. Original queue for reference:

```bash
# 1. deep-analysis A/B (Phase 6-pre) — NOTE the pre-Phase-12 confound choice, see 6-pre
LT_METRICS_VERSION=2 LT_DEEP_ANALYSIS=true LT_LLM_CACHE=true LT_MAX_REFLECTION=0 \
  python -u -m longterm_agent.backtest --backtest-id wf_30x_v0.2_deep \
  --tickers <30 names as in wf_30x_v0.2> --start 2026-04-24 --end 2026-06-19 --step 7 \
  --horizons 14 28 84 --benchmark SPY --workers 20 --asof-today 2026-07-10

# 2. Phase 12 A/B (fundamentals v2; diff vs wf_30x_v0.2 isolates the wiring)
LT_METRICS_VERSION=2 LT_DEEP_ANALYSIS=false ... --backtest-id wf_30x_f12 ...

# 3. Phase 13 (detection v3 on top of f12; diff vs wf_30x_f12 isolates sector-relative)
#    FIRST: python -m longterm_agent.compare_detections --versions 2 3 \
#             --start 2026-04-30 --end 2026-07-09   (v2-vs-v3 report + 0.3.0 backfill)
#           python -m longterm_agent.grade_shifts   (grade the 0.3.0 rows for analogs)
LT_METRICS_VERSION=3 LT_DEEP_ANALYSIS=false ... --backtest-id wf_30x_f12_v3 ...

# 4. Phase 6 deep-v2 A/B (queued 2026-07-12; run AFTER 1-3 settle and only if the 6-pre
#    verdict doesn't kill deep analysis outright). Same grid; diff vs wf_30x_f12 isolates
#    deep-v2; diff vs wf_30x_v0.2_deep isolates v2-over-v1.
LT_METRICS_VERSION=2 LT_DEEP_ANALYSIS=true LT_DEEP_V2=true LT_LLM_CACHE=true LT_MAX_REFLECTION=0 \
  python -u -m longterm_agent.backtest --backtest-id wf_30x_f12_deepv2 \
  --tickers <same 30> --start 2026-04-24 --end 2026-06-19 --step 7 \
  --horizons 14 28 84 --benchmark SPY --workers 20 --asof-today 2026-07-10
```

Analysis after: deep vs v0.2; f12 vs v0.2; f12_v3 vs f12; AMD 06-04/06-11 must show
`sector_break` under the semis baseline; write the 3-way report into docs/.

---

## Phase 14 — Regime context lite (free PIT series only; collect NOW, gate LATER)

**Origin:** expert Layers 3–4, trimmed to what is PIT-clean and free. All wf shorts died
in one bull regime; regime detectors are how the system will eventually know the
difference — but gating on them TODAY would be fitting to a single regime, so this phase
only collects and stamps.

Changes (expert #28, #31–33, #37):
- Daily fetcher job (bloomberg_news_fetcher container, same cadence as prices): VIX +
  VIX3M (term-structure inversion flag), HY OAS (FRED `BAMLH0A0HYM2`), 10Y (`DGS10`,
  Δ≥40bp/15-session shock flag), Brent/WTI → new `macro_series` table (series, date,
  value; first-release only, PIT by construction).
- Stamp a `regime_context` snapshot (VIX level/percentile, HY OAS percentile, 10Y Δ,
  oil) into `risk_checks` on every signal and into backtest rows; report splits by these
  (like the existing bull/flat/bear SPY split).
- Explicitly NO decision logic until data spans ≥2 regimes. The consensus-dependent
  Macro Surprise Engine (expert Layers 1–2, #19–21) and guidance-vs-consensus (#40–41)
  stay DEFERRED until a consensus source is chosen (ties into the Phase 9.3 transcript
  decision).

**Consensus decision 2026-07-15 (probe-driven, see chat): per-ticker side is FREE.**
- yfinance covers forward EPS/revenue consensus, estimate revisions (7/30/60/90d trend)
  and EPS-surprise history — and the weekly fundamentals job has been snapshotting most
  of it since day one (`analyst_estimates` kind; now + `growth_estimates`,
  `earnings_history`). FMP FREE tier (`/stable/earnings`, key in .env as FMP_API_KEY)
  adds the missing piece: per-report **revenue**Actual-vs-Estimated history + the next
  report's street consensus → new weekly snapshot `source='fmp', kind='earnings'`
  (250 calls/day limit vs ~50/week used — ×35 headroom).
- Consumer shipped ADVISORY-ONLY: `read_consensus` + deep-analysis tool
  `get_street_consensus` (forward consensus, revision trend, last-4 surprises, next
  report). NOT added to the fundamentals verdict prompt — that's a prompt change and
  needs its own A/B per release discipline.
- PIT: forward snapshots are first-release by construction; FMP's in-snapshot HISTORY
  has no first-release guarantee → historical fields stay advisory until forward data
  covers the window (same stance as hy_oas percentile).
- Still PAID-blocked (unchanged): FMP `economic-calendar` (macro consensus → surprise
  engine) and `earning-call-transcript` (Phase 9.3) — both 402 on free; decide Starter
  vs API-Ninjas only when we actually schedule those phases. yfinance v3 legacy API
  note: new FMP keys work ONLY against /stable/ endpoints.

Acceptance: `macro_series` populated daily with backfill from FRED/yfinance history;
every new signal carries `regime_context`; forward report gains regime-split tables.

**Status 2026-07-12 — DONE (collect-and-stamp only, as designed):**
- ✅ `bloomberg_news_fetcher/macro_fetcher.py`: 9 series (vix/vix3m/brent/wti via
  yfinance; hy_oas/dgs10 + fed_bs/tga/rrp liquidity extras via fredgraph.csv, no key),
  first-release-only inserts (ON CONFLICT DO NOTHING — PIT by construction), incremental
  runs re-scan a 21d window to heal FRED lag. Scheduled daily 18:30 ET in the news
  container (`MACRO_FETCH_ENABLED/MACRO_FETCH_TIME`). Backfilled locally: VIX since 1990,
  DGS10 2010+, **hy_oas only 2023+ — FRED caps ICE BofA series to trailing ~3y
  (licensing), so its "5y" percentile grows into a true 5y window as PIT rows accumulate**.
- ✅ `longterm_agent/tools/regime_tool.py`: pure `compute_regime_context` (unit-tested) +
  cached AS-OF reader; stamp = vix (+p5y, >30 capitulation, vix3m inversion), hy_oas
  (+p5y, 20-obs RoC), dgs10 (+Δ15s bp, ≥40bp shock flag), brent/wti (+oil band).
- ✅ Stamped: `risk_checks.regime_context` in risk_gate (live runs; new `risk_checks`
  JSONB column in longterm_agent_results), `regime_context` JSONB on backtest_runs filled
  at grade time (idempotent backfill for rows graded pre-Phase-14; left NULL if
  macro_series empty so a later pass can fill it). Report: `by vix band` / `by hy band`
  splits with an explicit "single band — one-regime sample" marker.
- ✅ Freshness: `macro` registered as an ADVISORY source (visible in /freshness, never in
  `any_stale` — a missing/stale advisory stamp must not abort a batch). `PIPELINE_VERSION`
  → 0.6.0. 9 new tests (test_phase14.py). Live check 2026-07-10: vix 15.03 (p21),
  hy_oas 2.70 (p8), dgs10 4.54 (Δ+11bp), brent 76 deflationary — one calm regime, QED.
- ▶ Server side on next deploy: new image picks up the daily job automatically
  (macro_series auto-created; run `python macro_fetcher.py --backfill` once in the
  container for history).

---

## Deployment status (2026-07-11, session notes — resume point)

Server ops executed over SSH (root@DB_HOST, expect + hidden password) after user approval.

**Done:**
- ✅ **news-fetcher redeployed with the SPY fix.** The running container had been started
  from a stale image (`…news-fetcher:server`, no `BENCHMARK_TICKERS`); replaced with the
  user-pushed `matveylebedev891/stock-fetcher:latest` (fix verified inside the pulled
  image on the server before the swap). Recreated via
  `docker run … --env-file /root/trade_agent/.env -e FETCH_INTERVAL_MIN=5 -e VERBOSE=true
  -e FETCH_PRICES=true -e FETCH_FULL_TEXT=false … python -u auto_news_fetcher.py`
  (default bridge network; DB via public IP — matches the old container). First fetch
  cycle healthy. **Rollback container kept**: `bloomberg_news_fetcher_old` (stopped) —
  delete after the Saturday 03:00 ET run proves SPY updates
  (`SELECT max(date) FROM stock_prices_daily WHERE ticker='SPY'`).
- ✅ `.dockerignore` now excludes `.env`/`*.bak`/`.claude/` — the longterm Dockerfile does
  `COPY . .` and would have baked secrets. (Audited the already-public stock-fetcher
  image: clean — selective COPYs only.)
- ✅ `docker-compose.yml` (repo) carries `LT_METRICS_VERSION=2` for longterm-agent.
- ✅ Local image **`longterm-agent:0.4.0` built (linux/amd64, 1.01 GB)**, verified: no
  `.env`, code = pipeline 0.4.0. Archive was mid-transfer when the user paused the deploy.

**Discovered server state (important):**
- ❗ **longterm-agent has NEVER been deployed** — no container, so no Sunday batch, no
  daily grading, no forward accumulation is currently running anywhere.
- `/root/trade_agent/` holds only `.env` (no repo checkout); deploy model = prebuilt
  images + `docker run`. Infra present: postgres (pgvector), neo4j, telegram-bot,
  3x-ui (VPN — part of §9 already covered), openclaw.
- Server `.env` gaps: no `TELEGRAM_CHAT_ID` (agent alerts will be silently disabled),
  no `LT_TELEGRAM_BOT_TOKEN` (stats bot would conflict with the news telegram-bot on the
  shared token — do NOT deploy stats bot until a second token exists).
- Disk: 19 GB free (fine); 6.4 GB reclaimable in old docker images if needed.

**⏸ PAUSED — to resume the agent deploy (user said "skip for now"):**
1. `docker save longterm-agent:0.4.0 | gzip > lta-0.4.0.tar.gz` (or reuse the archive if
   the session scratchpad still has it) → scp to `/root/` (a partial
   `/root/lta-0.4.0.tar.gz` from the interrupted transfer may need deleting first).
2. On server: `gunzip -c /root/lta-0.4.0.tar.gz | docker load && rm /root/lta-0.4.0.tar.gz`
3. `docker run -d --name longterm_agent --restart unless-stopped
   --env-file /root/trade_agent/.env -e LT_METRICS_VERSION=2 longterm-agent:0.4.0`
   (CMD defaults to the weekly scheduler; TZ baked into the image.)
4. Verify: `docker exec longterm_agent python -c "from longterm_agent.config import
   LongTermAgentConfig as C; print(C.DETECTION_VERSION, C.METRICS_VERSION)"` → `0.2.0 2`;
   logs show schedule (batch Sun 06:00 ET, grade daily 07:00 ET).
5. Add `TELEGRAM_CHAT_ID` to `/root/trade_agent/.env` first if alerts are wanted from
   day one (agent runs fine without — alerts are just skipped).

**Still open (user, §9 hardening — interactive session offered):** firewall for
5544/7688, non-root SSH; VPN already present.

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
