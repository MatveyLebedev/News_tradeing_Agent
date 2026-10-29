"""Configuration for the long-term agent. Reuses model_config.py (single point for models)."""
import os
from pathlib import Path

import pytz

_REPO_ROOT = Path(__file__).resolve().parents[1]  # for provenance() git lookup

# model_config is a shim over core.settings, whose import also load_dotenv()s the
# repo-root .env — so the os.getenv(LT_*) reads below see the file values.
from model_config import (
    LLM_MODEL as _LLM_MODEL,
    GRAPH_LLM_MODEL as _GRAPH_LLM_MODEL,
    STRONG_LLM_MODEL as _STRONG_LLM_MODEL,
    EMBEDDING_MODEL as _EMBEDDING_MODEL,
    EMBEDDING_DIMENSIONS as _EMBEDDING_DIMENSIONS,
    OPENROUTER_BASE_URL as _OPENROUTER_BASE_URL,
)

# --- Versioning (Phase 3): split what used to be one AGENT_VERSION ---
# DETECTION_VERSION gates DETECTION ARTIFACTS — the shift_library case base and the
#   bucket_summary_cache. Bump ONLY on a change that can alter shift membership / labels
#   (Phase 5); a bump requires re-running backfill_shift_library. Analog lookups filter on
#   it, so a stable DETECTION_VERSION is what keeps the library from being orphaned.
# PIPELINE_VERSION identifies the overall build for provenance/display. Advisory-only
#   changes (reflection wiring, analog CI, deep-analysis) bump THIS and ship freely.
# Migration: existing DB rows carry "0.1.0" in their `agent_version` column → that value
#   IS the detection version, so AGENT_VERSION stays an alias of DETECTION_VERSION for
#   backward compat (results identity + grading queries unchanged, no schema migration).
#
# Phase 5: DETECTION_VERSION derives from LT_METRICS_VERSION, so one env flag selects the
# whole detection release coherently — statistics path, embed corpus source, AND the
# version stamped on rows / used by analog lookups and the bucket cache:
#   1 → "0.1.0"  weekly bucket LLM sentiments, mean/std z-score, graph+ilike corpus
#   2 → "0.2.0"  per-article AFFECTS daily series, median/MAD robust break
#   3 → "0.3.0"  Phase 13: break test on the company-MINUS-SECTOR excess series
#                (sector baseline = median of peers' graph-label daily means), so a
#                sector-wide wave (the AMD←Broadcom failure) is context, not a shift
# Rows of every version coexist in shift_library (agent_version column); live cutover =
# changing LT_METRICS_VERSION in the server env AFTER the walk-forward gates pass.
METRICS_VERSION = int(os.getenv("LT_METRICS_VERSION", "1"))
DETECTION_VERSION = {1: "0.1.0", 2: "0.2.0", 3: "0.3.0"}[METRICS_VERSION]
# 0.9.0 (2026-07-19 PIT audit): document/ingest cutoffs on every as-of reader, code-enforced
# no-lookahead in raw Cypher, knowledge-date gating + as-of story sizes in the graph tools,
# maturity-masked intraday moves. Detection labels are untouched (DETECTION_VERSION stands),
# but tool outputs and the fundamentals/characterization prompts change — so runs before and
# after this bump are NOT like-for-like and need a fresh backtest_id.
# 0.9.3 (2026-07-22): advisory-only since 0.9.0 — synthesis retry budget 4× exponential
# (fewer failed→NEUTRAL rows, decision logic untouched), /open /gate /health bot commands,
# HTML digest + charts, Monday job default OFF. Bumped so the version stamped on rows
# matches the shipped image tag (0.9.1/0.9.2 images still stamped 0.9.0 — a provenance
# gap, not a code difference).
# 0.9.4 (2026-07-22): notifier fans out to comma-separated LT_TELEGRAM_CHAT_ID list.
# 0.9.5 (2026-07-23): /open overlays delayed live quotes — display only, grading
#   still reads official closes from stock_prices_daily.
# 0.9.6 (2026-07-28): NaN-price hardening after the 2026-07-24 incident (BACKTEST_LEDGER
#   `live_0726`). Decision logic untouched and a NO-OP on finite data — the reader-side
#   guards only change behaviour on a bar that can no longer exist (CHECK constraint on
#   stock_prices_daily). What DOES change operationally: a failed persist is now an
#   oplog.error + `not_persisted` in the batch stats instead of a bare print, so a batch
#   can no longer report trades it did not write. Bumped per the 0.9.3 rule — the stamp
#   must match the shipped image.
#   Provenance note: the `live_0726` backfill ran on this code but started before the
#   bump, so its snapshots carry 0.9.5. Same class of gap 0.9.3 recorded, stated here.
# 0.9.7 (2026-08-03): stats-bot only, no decision or detection path touched. /open died
#   silently once the live book passed Telegram's 4096-char cap (17 positions → 6673
#   chars): the BadRequest killed the handler before the chart sent, and with no error
#   handler registered the command answered with nothing for two days. Replies now split
#   on line boundaries (_reply_long), and an error handler names any future failure
#   instead of swallowing it. Bumped per the 0.9.3 rule — the stamp must match the tag.
# 0.10.0 (2026-08-10): a MINOR bump, not a patch, because the decision path moves. `as_of`
#   is now stamped per ticker at the moment its run begins instead of once for the batch
#   (docs/PIT_RULES.md §1.1): the batch is serial and runs ~1.6h, so the last ticker used to
#   decide on data 1.6h older than its own fill. The cutoff tightens, tool outputs shift with
#   it, and runs before and after are therefore NOT like-for-like — same rule as 0.9.0.
#   Also: `in_session` no longer calls a weekend a session (a Sunday overrun produced rows
#   that could never fill), holidays resolve through `market_traded_on`, a batch that would
#   overrun the close stops rather than splitting its cohort across two fill conventions,
#   and a cohort decided on a day other than its own `as_of` now alerts — that leak was
#   found by hand on 2026-07-26 and its six traded rows carry `pit_excluded`.
#   The paper-broker package ships INERT: LT_BROKER_ENABLED defaults false, and when on it
#   is dry-run by default. Nothing in it can write to longterm_agent_results.
# 0.10.1 (2026-08-17): a PATCH — nothing about the decision path moves, so runs before and
#   after ARE like-for-like. Three fixes, all found in production:
#   * The scheduler called `self.db.get_cursor()`, which `DBManager` does not have, so every
#     broker job raised AttributeError on its first line — measured 2026-08-17 13:26 ET on
#     all three (`broker_entries`, `broker_exits`, `broker_reconcile`). Replaced with a
#     `_cursor()` helper opening a fresh `core.db` connection: the pool is wrong here because
#     this process idles for days between jobs, and psycopg2 rolls back anything handed back
#     to the pool mid-transaction, which would have lost broker writes silently.
#   * `get_novelty_score` (exposed to the deep-analysis node) asked for embeddings without
#     `encoding_format`, and openai>=2.53 defaults that to base64, which Google AI Studio
#     rejects. It has no retry and returns a neutral 0.5 on failure, so ~half of all calls
#     fed the agent a fabricated novelty reading. Measured: 5 failures in 10 calls.
#   * `broker_exits` now ensures the ledger schema; it can be the first broker step to touch
#     the DB, and `paper_orders` would not exist yet.
#   Also adds `ALPACA_HTTPS_PROXY`, read ONLY by the broker client — inert while unset.
# 0.11.0 — the live book narrows to `(positive shift, confirms) -> LONG` (decision
#   2026-08-24, on expert advice). Behaviour change on the live path only: the flag
#   `LT_LONG_CONFIRMS_ONLY` defaults false, so backtests and research keep the incumbent
#   book and the arm this supersedes stays measurable offline.
# 0.11.1 (2026-10-24): a PATCH, display and alerting only. /open lists the ungraded
#   `pit_excluded` trades (the weeks replayed after the 2026-10 OpenRouter outage) below
#   the book, faded on the chart and outside every total — hidden, they read as weeks that
#   were never filled. `scan_open_breaches` skips those rows, so a replay cannot send a
#   STOP BREACH for a position nobody holds. Grading and the decision path are untouched.
# 0.11.2 (2026-10-28): a PATCH, observability only. Tracing moves from the LangSmith cloud to
#   self-hosted Langfuse (core/tracing.py, deploy/langfuse): one trace per run under a
#   deterministic id, incl. the Monday forward cohort. Callbacks ride in the invoke config,
#   so prompts and LLM-cache keys are unchanged (test_tracing.py); decisions are untouched.
# 0.11.3 (2026-10-29): a PATCH, observability only. The daily grading step now restates the
#   grader's columns as trace labels (`trace_labels`, longterm_agent/labeling) and mirrors
#   them into Langfuse as scores, rebuilding a missing trace from its snapshot. Grading and
#   decisions are untouched; a labeling failure never holds up a grade.
# 0.11.4 (2026-10-29): a PATCH, observability only. Trace labeling stage 2: every run gets an
#   outcome (counterfactual), every shifted run a persistence check, risk_gate/reflection a
#   value, map buckets a weekly excess, each batch a session score. Decisions untouched.
PIPELINE_VERSION = "0.11.4"
AGENT_VERSION = DETECTION_VERSION  # back-compat alias (DB `agent_version` column value)

# BUCKET_CACHE_VERSION keys bucket_summary_cache SEPARATELY from the detection version.
# A cached weekly map summary depends on exactly: corpus definition + map prompt + map
# model — all UNCHANGED between metrics v1 and v2 (the Phase 5b embed source failed its
# precision audit and stays off, so the corpus is byte-identical). Sharing the cache
# across versions (a) saves ~1k map calls per backfill and (b) makes the v1-vs-v2 label
# comparison isolate the estimator change — identical summaries feed both. Bump this
# whenever the corpus definition, map prompt, or map model changes.
#
# Env-overridable because the key is (ticker, week_start, version) and week_start is a
# DATE: two sweeps of the same Mondays at DIFFERENT times of day (06:30 vs 12:00) produce
# buckets whose boundaries differ by hours, so their article membership differs — but they
# collide on the key and would silently read each other's summaries. A sweep on a new
# decision clock must namespace its cache (e.g. LT_BUCKET_CACHE_VERSION=0.1.0-mid1200).
BUCKET_CACHE_VERSION = os.getenv("LT_BUCKET_CACHE_VERSION", "0.1.0")


class LongTermAgentConfig:
    """Config for the long-term news-background + fundamentals agent."""

    AGENT_NAME = "longterm_agent"
    METRICS_VERSION = METRICS_VERSION
    DETECTION_VERSION = DETECTION_VERSION
    PIPELINE_VERSION = PIPELINE_VERSION
    AGENT_VERSION = AGENT_VERSION  # == DETECTION_VERSION
    BUCKET_CACHE_VERSION = BUCKET_CACHE_VERSION

    # --- Windows (see plan §8: start at 60d, raise to 90d as news history accrues) ---
    LOOKBACK_DAYS_BACKGROUND = int(os.getenv("LT_LOOKBACK_DAYS", "60"))
    SHIFT_WINDOW_DAYS = int(os.getenv("LT_SHIFT_WINDOW_DAYS", "14"))
    BUCKET_DAYS = int(os.getenv("LT_BUCKET_DAYS", "7"))
    MIN_ARTICLES_FOR_BACKGROUND = int(os.getenv("LT_MIN_ARTICLES", "8"))

    # --- Shift / signal thresholds ---
    SENTIMENT_SHIFT_Z_THRESHOLD = float(os.getenv("LT_SHIFT_Z", "1.0"))
    LOOKBACK_DAYS_TECHNICAL_LT = int(os.getenv("LT_TECH_DAYS", "180"))
    EARNINGS_RISK_DAYS = int(os.getenv("LT_EARNINGS_RISK_DAYS", "10"))
    LONGTERM_CONFIDENCE_THRESHOLD = float(os.getenv("LT_CONF_THRESHOLD", "0.55"))
    MAX_REFLECTION_ITERATIONS = int(os.getenv("LT_MAX_REFLECTION", "1"))

    # --- Risk-gate / detection knobs (Phase 3: were magic numbers in nodes/metrics).
    # Env-overridable + stamped into provenance() so a backtest records the knobs it used.
    # DETECTION knobs (ATTENTION_SPIKE_RATIO, MIN_ABS_DELTA) feed compute_shift_metrics —
    # changing them alters labels, so treat a change as a detection release (bump above).
    ATTENTION_SPIKE_RATIO = float(os.getenv("LT_ATTENTION_SPIKE_RATIO", "2.0"))
    MIN_ABS_DELTA = float(os.getenv("LT_MIN_ABS_DELTA", "0.2"))
    PRICED_IN_MOVE_PCT = float(os.getenv("LT_PRICED_IN_MOVE_PCT", "8.0"))
    PRICED_IN_PENALTY = float(os.getenv("LT_PRICED_IN_PENALTY", "0.2"))
    EARNINGS_PENALTY = float(os.getenv("LT_EARNINGS_PENALTY", "0.1"))
    # Analog honesty (Phase 3): min graded neighbours before a base rate is trustworthy.
    MIN_ANALOGS = int(os.getenv("LT_MIN_ANALOGS", "5"))
    # This agent's edge is detecting background SHIFTS. With no shift the matrix has no cell
    # and synthesis conviction is unstable run-to-run, so a "trade" there is not reproducible.
    # When true, no-shift signals are capped below the trade threshold (deterministic
    # not-traded) — the directional lean + thesis stay for information.
    REQUIRE_SHIFT_TO_TRADE = os.getenv("LT_REQUIRE_SHIFT", "true").lower() == "true"

    # --- Matrix-cell eligibility (docs/AB_PLAN_matrix_cell.md) ---
    # A LONG/SHORT requires the (shift sign, verdict) pair to license THAT side. The three
    # licensed cells are transcribed from SYNTHESIS_MATRIX_RULES and live in
    # agent/nodes.py::MATRIX_CELLS — there is no threshold here to tune.
    #
    # Defaults TRUE, unlike a feature flag, because this is conformance rather than a bet:
    # the gate was trading cells the agent's own synthesis prompt tells it to avoid
    # (`ignores` has no cell at any shift sign; `contradicts` licenses contrarian LONG or
    # NEUTRAL, never SHORT; a missing verdict or missing shift direction places the decision
    # nowhere). Measured impact is inside the noise on every surface available — sweeps 4w
    # +0.71pp, live 2w -0.44pp, live 4w 0.00pp, live open book +0.04pp — which is what a
    # spec fix should look like. `REQUIRE_SHIFT_TO_TRADE` above defaults true for the same
    # reason and is the closest precedent. Set false to restore the pre-fix behaviour.
    REQUIRE_MATRIX_CELL = os.getenv("LT_REQUIRE_MATRIX_CELL", "true").lower() == "true"

    # --- Long-confirms-only book (live strategy decision, 2026-08-24) ---
    # Restricts MATRIX_CELLS to `(positive, confirms) -> LONG`: no short leg, and the
    # overreaction cell `(negative, contradicts) -> LONG` closed too.
    #
    # Defaults FALSE and is enabled per-environment, NOT in code, because unlike
    # REQUIRE_MATRIX_CELL above this is a strategy bet rather than conformance — it deletes
    # cells SYNTHESIS_MATRIX_RULES explicitly licenses. Keeping the default off means
    # backtests and research keep the incumbent book unless they opt in, so the arm this
    # change supersedes stays measurable offline. Which mode a run used is stamped in
    # provenance, so a stored row always says which book it belongs to.
    #
    # Measured before adoption (docs/EVENT_STUDY_2026-08-20.md, exploratory): on sweeps it
    # is the highest-mean arm and on the least-mined dates the highest-median one, but it
    # keeps 26% of trades, halves the name count, drops the hedge (book beta 0.84 -> 1.23)
    # and concentrates the tail (top-5 share of P&L 36% -> 58%). Adopted on expert advice
    # against those measurements, not because they were resolved.
    LONG_CONFIRMS_ONLY = os.getenv("LT_LONG_CONFIRMS_ONLY", "false").lower() == "true"

    # --- Conviction gate (Phase 7) ---
    # What number the 0.55 threshold operates on:
    #   "llm"   — the synthesis model's self-reported conviction (current behavior).
    #             wf_30x showed it is ANTI-correlated with outcomes on the tails: the
    #             worst calibration bucket is the most confident one (0.75-0.85 @4w:
    #             −4.43%) — the LLM is surest on dramatic contrarian theses.
    #   "table" — deterministic base from CONVICTION_TABLE[verdict][shift_score bucket]
    #             (+ the advisory deep-analysis nudge), so the gate input is reproducible
    #             and monotone in evidence. LLM conviction is still recorded
    #             (signal.conviction_raw) for the later calibration study (calibrate.py).
    # Default stays "llm" until the A/B (docs/GATE_AB_2026Q2.md) is reviewed — switching
    # is an explicit env change, like the detection cutover.
    CONVICTION_GATE = os.getenv("LT_CONVICTION_GATE", "llm").lower()
    # --- Selection stage (`sel_null`, docs/AB_PLAN_selection_stage.md) ---
    # WHAT decides a trade, as opposed to what number the threshold reads:
    #   "conviction"  — incumbent: eligibility caps + risk penalties are folded into one
    #                   scalar, compared to LONGTERM_CONFIDENCE_THRESHOLD.
    #   "vetoes_only" — the NULL: trade every directional signal that passes the
    #                   deterministic eligibility vetoes (shift / usable fundamentals /
    #                   fresh data); conviction and the risk penalties stop selecting and
    #                   are recorded only. Motivation: across three books the gate added
    #                   value in NONE and subtracted in two, and the signals the machinery
    #                   cut returned +1.5…+3.6% vs the ones it kept.
    # Defaults to the incumbent (rule 4). Pre-registered; acceptance needs unburned cohorts.
    SELECTION_MODE = os.getenv("LT_SELECTION", "conviction").lower()
    # --- Primary metric (methodology change 2026-07-20) ---
    # Which column a pre-registered criterion is read against.
    #   "excess"      — stock vs SPY at an implicit beta of 1
    #   "excess_beta" — stock vs a beta-matched benchmark; beta is estimated from bars
    #                   STRICTLY BEFORE entry (longterm_agent/beta.py), so it is known at
    #                   decision time and adds no lookahead.
    # Switched to excess_beta because scoring at beta 1 mis-attributes factor exposure to
    # skill in BOTH directions: it flattered high-beta longs in a rising market and read a
    # short of a beta-1.26 name that LAGGED its own beta as a loss.
    # Applies to experiments whose results do not yet exist. Verdicts already frozen under
    # "excess" are NOT re-read — see the ledger's methodology entry: the deep ON/OFF gap is
    # +0.97pp under excess and +1.09pp under excess_beta, i.e. re-reading it would reverse
    # a frozen verdict by changing the ruler after seeing the result.
    PRIMARY_METRIC = os.getenv("LT_PRIMARY_METRIC", "excess_beta").lower()
    # --- What synthesis is told about fundamentals (longterm_agent/synth_fundamentals.py) ---
    #   "thin" — incumbent: verdict label + revenue_yoy + trailing/forward P/E + gap
    #   "rich" — also the latest margins vs the same quarter a year earlier, the five
    #            sub-verdicts and four trend phrases the FundamentalsTrend call already
    #            produces (and nothing currently reads), and P/B / P/S when P/E is None.
    # Measured motivation: P/E is None on 20% of traded decisions, margin_series reaches
    # synthesis on 0%, and deep analysis re-reads margin_series directly — so deep may be
    # buying back what this compression discards. Changing this INVALIDATES the LLM cache.
    SYNTH_FUNDAMENTALS = os.getenv("LT_SYNTH_FUNDAMENTALS", "thin").lower()
    # --- Forward Monday cohort (`monday_t1_fwd`, docs/BACKTEST_LEDGER.md) ---
    # Runs one pre-registered forward cohort every Monday 06:35 ET from the scheduler.
    # Default OFF by operator decision 2026-07-22 (same day it was written): the operator
    # closed the Monday-morning line — "monday is the wrong way, we test intraday later
    # instead" — before this ever shipped. The job stays for the record and for the
    # measured lesson it encodes (a cohort must run ON its as_of day, in a pinned
    # subprocess); flipping this on is re-opening the Monday line, a registered decision,
    # not a config tweak. The existing monday_t1_pit cohorts mature regardless — the free
    # ~2026-08-10 read happens with or without new cohorts.
    MONDAY_FWD = os.getenv("LT_MONDAY_FWD", "false").lower() == "true"

    # --- Paper broker (longterm_agent/broker) ---
    # OFF by default, and DRY RUN by default when on. Two flags rather than one because the
    # interesting failure is not "the broker was off" but "the broker was live before anyone
    # meant it to be": enabling the layer to watch what it *would* do must not be the same
    # keystroke as letting it place orders. The keys in use are paper-only (the live endpoint
    # 401s them), so the worst case is a paper position, but the book it produces is meant to
    # be a forward record and a stray cohort would sit in it forever.
    BROKER_ENABLED = os.getenv("LT_BROKER_ENABLED", "false").lower() == "true"
    BROKER_DRY_RUN = os.getenv("LT_BROKER_DRY_RUN", "true").lower() == "true"
    # Only cohorts from this date onward reach the broker; everything older keeps showing
    # the prices the grader derived. Without a cutoff, switching the layer on would try to
    # open positions for trades that are already weeks into their horizon.
    BROKER_START_DATE = os.getenv("LT_BROKER_START_DATE", "")
    # Rows: fundamentals-trend verdict; columns: shift_score buckets
    # [0, .4) / [.4, .7) / [.7, 1]. Priors mirroring the synthesis matrix semantics:
    # confirms trades (mid+ score clears the 0.55 gate), contradicts = overreaction/hype
    # cell (never clears alone; a strong live advisory nudge +0.2 can lift strong cases),
    # ignores/unknown = news-only basis (never clears alone). NOT fitted to any window.
    CONVICTION_TABLE = {
        "confirms":    (0.45, 0.60, 0.70),
        "contradicts": (0.25, 0.35, 0.45),
        "ignores":     (0.20, 0.30, 0.40),
        "unknown":     (0.20, 0.30, 0.40),
    }
    # Counter-trend guard: penalize a signal that FIGHTS a strong trailing trend (SHORT
    # into a big rally / LONG into a collapse, measured on the 180d technical window).
    # Structural prior (same class as the earnings penalty), NOT tuned on wf_30x — that
    # window merely exposed the gap (INTC −41% / QCOM −37% were counter-trend SHORTs the
    # priced-in guard can't catch: it only fires when price moved WITH the signal).
    # Set LT_COUNTER_TREND_PENALTY=0 to disable.
    COUNTER_TREND_PCT = float(os.getenv("LT_COUNTER_TREND_PCT", "50.0"))
    COUNTER_TREND_PENALTY = float(os.getenv("LT_COUNTER_TREND_PENALTY", "0.2"))

    # --- Exit discipline (Phase 5.5) ---
    # Structural stop on the RAW position return (direction-adjusted), simulated on daily
    # closes by the graders (both books recorded: hold-to-horizon AND stopped) and watched
    # live by the daily open-position breach scan. Round prior — the in-sample sweep was
    # positive across 6–12%, so 10% is not a fitted optimum. take_profit=0 disables TP
    # (measured useless at the 4w horizon: 1 trade in 44 reached +20%).
    STOP_LOSS_PCT = float(os.getenv("LT_STOP_LOSS_PCT", "0.10"))
    TAKE_PROFIT_PCT = float(os.getenv("LT_TAKE_PROFIT_PCT", "0"))

    # --- Corpus retrieval ---
    AFFECTS_MIN_CONFIDENCE = float(os.getenv("LT_AFFECTS_MIN_CONF", "0.5"))
    # Semantic retrieval (Phase 5b): third corpus source via pgvector cosine search.
    # AUDIT VERDICT (2026-07-11): FAILED the precision guard — with gemini-embedding-001,
    # known-relevant articles (median sim ≈0.50 to a query vector; ≈0.72 to the corpus
    # centroid) are inseparable from market noise (bg p90 ≈ own median), and the target
    # class (subsidiary phrasing, e.g. Waymo-without-Google) scores BELOW sector chatter.
    # No usable τ exists → OFF by default in ALL versions. The code path stays for future
    # embedding upgrades; enabling it is a corpus change = new DETECTION_VERSION +
    # BUCKET_CACHE_VERSION bump. Experiment-only override: LT_CORPUS_EMBED=true.
    CORPUS_EMBED_ENABLED = os.getenv("LT_CORPUS_EMBED", "false").lower() == "true"
    EMBED_TAU = float(os.getenv("LT_EMBED_TAU", "0.62"))       # no usable value found (audit)
    EMBED_TOP_K = int(os.getenv("LT_EMBED_TOP_K", "400"))      # candidates per HNSW scan

    # --- Models (OpenRouter) ---
    LLM_MODEL = _LLM_MODEL              # main reasoning model (reduce, shift, reflection)
    MAP_MODEL = _GRAPH_LLM_MODEL        # cheap model for per-bucket map summaries
    # Strong-model split (STRONG_LLM_MODEL, default empty → LLM_MODEL, i.e. OFF): upgrades
    # ONLY the three judgment-heavy calls — synthesis (LongTermSignal), fundamentals verdict
    # (FundamentalsTrend), deep-analysis reduce (DeepAnalysisOutput). All three are advisory
    # path: detection labels come from MAP_MODEL sentiments + deterministic metrics, so this
    # split can never alter shift membership. A different model = different LLM-cache keys
    # for those calls only; runs stop being like-for-like (stamped in provenance). Budget
    # guard from the 2026-07-18 cost analysis: with claude-sonnet-5 this is +106% per run
    # at measured token volumes and breaches the +200% cap only if reasoning output doubles.
    STRONG_LLM_MODEL = _STRONG_LLM_MODEL or _LLM_MODEL
    EMBEDDING_MODEL = _EMBEDDING_MODEL
    EMBEDDING_DIMENSIONS = _EMBEDDING_DIMENSIONS
    OPENROUTER_BASE_URL = _OPENROUTER_BASE_URL
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    LLM_TEMPERATURE = 0
    LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LT_MAX_OUTPUT_TOKENS", "2048"))

    # --- Deep analysis (Phase 7A: bounded ReAct tool-calling enrichment) ---
    # Adaptive depth: the LLM chooses what to investigate per company via read-only tools.
    # OFF in backtest (labels stay deterministic), ON live (richer theses). It enriches the
    # thesis only — it can never change the deterministic shift label or graded outcome.
    DEEP_ANALYSIS_ENABLED = os.getenv("LT_DEEP_ANALYSIS", "true").lower() == "true"
    MAX_TOOL_CALL_ITERATIONS = int(os.getenv("LT_MAX_TOOL_ITERS", "7"))  # same cap as news_agent
    TOOL_RESULT_MAX_CHARS = int(os.getenv("LT_TOOL_RESULT_MAX_CHARS", "3000"))  # truncate tool blobs
    # Phase 6 — deep analysis v2 (planner + parallel threads + citations). DEFAULT OFF:
    # the v1 path stays byte-identical (prompts are LLM-cache keys) until the queued
    # wf_30x deep-v2 A/B earns the cutover. Both flags must be true for v2 to run.
    DEEP_V2 = os.getenv("LT_DEEP_V2", "false").lower() == "true"
    DEEP_LLM_CONCURRENCY = int(os.getenv("LT_DEEP_LLM_CONCURRENCY", "8"))  # global LLM-call cap
    # Phase 11 — dynamic few-shot: graded analog outcomes as worked examples in the
    # synthesis prompt. DEFAULT OFF (prompt = LLM-cache key); enable via backtest A/B.
    FEWSHOT_ANALOGS = os.getenv("LT_FEWSHOT", "false").lower() == "true"
    # Consensus stage B — street consensus + realized-surprise history injected into the
    # fundamentals verdict prompt (and a posture line in synthesis). Targets the INTC/QCOM
    # error class: direction is set by the delta vs expectations, not absolute quality.
    # DEFAULT OFF (prompt = LLM-cache key); enable via the wf_30x consensus A/B.
    # Historical as-of dates degrade to FMP surprise history only (yfinance forward
    # estimates have no history — snapshots collect from 2026-07-15 forward).
    CONSENSUS_IN_VERDICT = os.getenv("LT_CONSENSUS_IN_VERDICT", "false").lower() == "true"

    # --- Database ---
    DB_CONFIG = {
        "host": os.getenv("DB_HOST"),
        "database": os.getenv("DB_NAME", "trade_agent"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "port": os.getenv("DB_PORT", "5432"),
    }

    TIMEZONE = pytz.timezone("America/New_York")

    # --- Tracing: self-hosted Langfuse (core.tracing). Prompts stay on our own server. ---
    # Scheduler tracing (auto_longterm_agent): LT_TRACE=false to opt out; no-op without LANGFUSE_*.
    TRACE = os.getenv("LT_TRACE", "true").lower() == "true"

    # --- Ops / Phase 6 (weekly scheduler, Telegram alerts, stats bot) ---
    # Weekly batch cadence (ET). Run after the weekly fundamentals fetch so data is fresh.
    BATCH_WEEKDAY = os.getenv("LT_BATCH_WEEKDAY", "sunday").lower()
    BATCH_TIME = os.getenv("LT_BATCH_TIME", "06:00")          # ET, off-market
    GRADE_TIME = os.getenv("LT_GRADE_TIME", "07:00")          # daily grading of matured cohorts
    RUN_ON_START = os.getenv("LT_RUN_ON_START", "false").lower() == "true"
    # Telegram alerts (batch job → push) and the stats bot. Falls back to the shared bot token.
    TELEGRAM_BOT_TOKEN = os.getenv("LT_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("LT_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
    TELEGRAM_ALLOWED_USER_IDS = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")  # bot auth (comma-sep)
    ALERT_ON_ERROR = os.getenv("LT_ALERT_ON_ERROR", "true").lower() == "true"

    # --- Backtest reproducibility (Backtest Plan B0) ---
    LLM_CACHE = os.getenv("LT_LLM_CACHE", "false").lower() == "true"

    @classmethod
    def provenance(cls):
        """Run profile stamp for backtests — what produced a result, so runs are comparable."""
        rev = "unknown"
        try:
            import subprocess
            rev = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(_REPO_ROOT), stderr=subprocess.DEVNULL).decode().strip()
        except Exception:
            pass
        return {
            "detection_version": cls.DETECTION_VERSION,
            "metrics_version": cls.METRICS_VERSION,
            "pipeline_version": cls.PIPELINE_VERSION,
            "agent_version": cls.AGENT_VERSION,  # == detection_version (back-compat)
            "git_revision": rev,
            # A model change (or these knobs) shifts prompts/labels and invalidates
            # LLM-cache comparability — stamped so two runs are only compared like-for-like.
            # Which backend served the run, not just which model was asked for: an
            # unpinned request can land on any of 17 providers at differing quantisation.
            "llm_provider": cls.llm_provider_routing(),
            "model": cls.LLM_MODEL,
            "map_model": cls.MAP_MODEL,
            "strong_model": cls.STRONG_LLM_MODEL,
            "temperature": cls.LLM_TEMPERATURE,
            "reflection_iters": cls.MAX_REFLECTION_ITERATIONS,
            "llm_cache": cls.LLM_CACHE,
            "lookback_days": cls.LOOKBACK_DAYS_BACKGROUND,
            "knobs": {
                "attention_spike_ratio": cls.ATTENTION_SPIKE_RATIO,
                "min_abs_delta": cls.MIN_ABS_DELTA,
                "priced_in_move_pct": cls.PRICED_IN_MOVE_PCT,
                "priced_in_penalty": cls.PRICED_IN_PENALTY,
                "earnings_penalty": cls.EARNINGS_PENALTY,
                "min_analogs": cls.MIN_ANALOGS,
                "shift_z": cls.SENTIMENT_SHIFT_Z_THRESHOLD,
                "conf_threshold": cls.LONGTERM_CONFIDENCE_THRESHOLD,
                "corpus_embed": cls.CORPUS_EMBED_ENABLED,
                "embed_tau": cls.EMBED_TAU,
                "embed_top_k": cls.EMBED_TOP_K,
                "conviction_gate": cls.CONVICTION_GATE,
                "selection_mode": cls.SELECTION_MODE,
                "primary_metric": cls.PRIMARY_METRIC,
                "synth_fundamentals": cls.SYNTH_FUNDAMENTALS,
                "conviction_table": {k: list(v) for k, v in cls.CONVICTION_TABLE.items()},
                "counter_trend_pct": cls.COUNTER_TREND_PCT,
                "counter_trend_penalty": cls.COUNTER_TREND_PENALTY,
                "stop_loss_pct": cls.STOP_LOSS_PCT,
                "take_profit_pct": cls.TAKE_PROFIT_PCT,
                "deep_analysis": cls.DEEP_ANALYSIS_ENABLED,
                "deep_v2": cls.DEEP_V2,
                "fewshot_analogs": cls.FEWSHOT_ANALOGS,
                "require_matrix_cell": cls.REQUIRE_MATRIX_CELL,
                "long_confirms_only": cls.LONG_CONFIRMS_ONLY,
                # Keys bucket_summary_cache. Was never stamped, which is why 27 of 32 stored
                # books cannot be told apart on which summaries they read.
                "bucket_cache_version": cls.BUCKET_CACHE_VERSION,
            },
        }

    @classmethod
    def get_universe(cls):
        """Default ticker universe for the weekly batch (the curated, news-named set)."""
        env = os.getenv("LT_UNIVERSE", "").strip()
        if env:
            return [t.strip().upper() for t in env.split(",") if t.strip()]
        try:
            from bloomberg_news_fetcher.price_fetcher import TOP_50_TICKERS
            return list(TOP_50_TICKERS)
        except Exception:
            from longterm_agent.company_names import COMPANY_NAMES
            return list(COMPANY_NAMES.keys())

    @classmethod
    def get_db_config(cls):
        return dict(cls.DB_CONFIG)

    @classmethod
    def enable_tracing(cls, environment: str = "dev"):
        """Turn on Langfuse tracing for this process (self-hosted, see core.tracing).
        Runs are traced only where the caller wraps graph.invoke in tracing.agent_run.
        Returns True if enabled."""
        from core import tracing
        return tracing.init(default_environment=environment)

    @classmethod
    def llm_provider_routing(cls):
        """The OpenRouter provider pin in force, or None when routing is free."""
        from core.llm import provider_routing
        return provider_routing()

    @classmethod
    def make_llm(cls, model: str = None, max_tokens: int = None):
        """Build a ChatOpenAI client on OpenRouter — shared factory in core.llm
        (function_calling-forced structured output; see rationale there)."""
        from core.llm import make_llm
        return make_llm(
            model=model or cls.LLM_MODEL,
            max_tokens=max_tokens or cls.LLM_MAX_OUTPUT_TOKENS,
            temperature=cls.LLM_TEMPERATURE,
        )
