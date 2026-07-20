# Data Expansion Plan

Two goals, two tracks:

1. **Depth** — more *historical* data so backtests can run over a longer window
   (today the news corpus caps them at ~2026-03+).
2. **Breadth** — more data *per decision* so the live agent gets better evidence
   (transcripts, full-text bodies, consensus, richer macro).

This plan consolidates and extends MODIFICATION_PLAN.md Phase 9 (data breadth) and
BACKTEST_PLAN.md B2 (data depth). Where those docs already carry a verdict (GDELT spike,
full-text-scraping non-viability) this plan builds on it rather than re-litigating.

Ground rules inherited from MODIFICATION_PLAN (apply to every item below):
- **PIT-first**: every row keyed by the date it *became knowable*; first-release-only
  where revisions exist.
- **Advisory-first**: new sources feed deep-analysis tools before anything touches the
  detection corpus; corpus-affecting changes ship as a detection release (new
  `DETECTION_VERSION`).
- **Freshness registration**: every new fetcher registers in `core/freshness.py`
  (advisory sources go in `_ADVISORY`).
- **Licensing column**: every source below carries an explicit "may we store it?" note.
  Free-but-unlicensed scraping (news bodies, transcripts) stays out.

---

## 0. Measured baseline (2026-07-19, live DB)

| Source (table) | Coverage | Depth limit | Binding? |
|---|---|---|---|
| `news` (RSS: Bloomberg/CNBC/MW) | dense **2026-02 →** (~7–8k/mo; 34.3k rows) | RSS is forward-only | **YES — caps backtest start at ~2026-03** |
| `news.embedding` | ~93% overall, **June 2026 gap: 4,905/7,158 (69%)** | backfillable | quality gap |
| `stock_prices_daily` | 2024-06-17 → now, 131 tickers | yfinance backfill choice (2y) | no (extendable free) |
| `fundamentals` (edgar) | **2019 →** now, 119 tickers | `EDGAR_HISTORY_YEARS=7` | no |
| `fundamentals` (yfinance snapshots) | 2026-06-15 → now (forward PIT) | snapshots can't be backfilled | for estimates/valuation history — partly mitigated by `valuation_pit` |
| `macro_series` | deep (VIX 1990→, most 2010→) | `hy_oas` ~3y (FRED licensing) | no |
| `filings` + `insider_trades` | 2025-07-15 → now | chosen 365d backfill; submissions "recent" ≈1000 filings | mildly (extendable free) |
| `options_iv` | **2026-07-15 →** (4 days) | chains are ephemeral — forward-only | for IV-percentile features |
| intraday graph (`AFFECTS.move_*`) | intraday bars 2026-02-23 →; `agent_results` **stalled 2026-06-06** | can't backfill intraday reactions | for the memory bridge |
| legacy `stock_prices` (1m) | empty (migrated to `historical_data_{t}`) | — | — |

Universe: `TOP_50_TICKERS` (actually ~120 names) + 12 benchmark ETFs (SPY + sector XL*).

**The single binding constraint for Goal 1 is news.** Everything else already reaches
2019+ or can get there for free. With news dense only from 2026-02 (and
`LOOKBACK_DAYS_BACKGROUND=60`), the earliest honest `as_of` is ~2026-04 → ~10 weekly
cohorts, one regime.

---

## Track D — Depth (longer backtests)

### D1. Free depth, no seam (do first — no decisions needed)

All of these extend history *of sources we already trust*, with the same source mix —
no corpus seam, no detection release needed (backtest-side data only).

1. **Daily bars → 2016-01** (`daily_price_fetcher.py --period max` variant, universe +
   benchmarks). yfinance daily is free and split-adjusted. Unlocks: 180d technical
   windows and analog grading at any archival `as_of`. Storage: ~120 tickers × 10y ×
   252d ≈ 300k rows — trivial.
2. **Filings + insider → 2019** (matches EDGAR fundamentals depth). The submissions
   API's `files` continuation chunks (beyond the ~1000-filing "recent" block) cover the
   full history; same parser, same PIT keys. Free, SEC-compliant pacing.
3. **Short-interest history**: FINRA/Nasdaq publish bi-monthly short-interest files as
   downloadable history — a true backfill for what the yfinance snapshot only collects
   forward. Small table, one-shot import.
4. **`EDGAR_HISTORY_YEARS` 7 → 10** on the next fundamentals run (cheap; bounds row
   count, not requests).
5. **Credit-spread series with full free history**: add FRED `BAA10YM` (Moody's BAA −
   10Y) alongside the licensing-capped `hy_oas` so regime percentiles have a deep
   sibling series.
6. **Embedding-gap backfill**: re-embed the ~2.3k June-2026 rows (and any other NULLs)
   with the *current* embedding model so pgvector retrieval doesn't silently skip a
   month.

Acceptance: freshness registry unchanged (same sources); coverage table above shows
2016+ prices, 2019+ filings/insider/short-interest; `news.embedding` NULL count ≈ 0.

### D2. News archive — the real unlock (decision gate)

Spike verdict (news_archive_spike.py, BACKTEST_PLAN B2): **GDELT = depth-yes,
parity-no** — it can fill the pre-2026-02 gap with on-topic articles (~140–250/quarter/
ticker) but with an inverted source mix (CNBC-heavy vs our Bloomberg-heavy live feed),
title-only text, and low URL overlap. A paid archive (NewsAPI.ai / Event Registry:
full text + metadata back to 2014, token-priced per year covered, ~$90/mo entry tier)
fixes parity and text depth.

**Recommended sequencing — hybrid, cheapest-evidence-first:**

- **D2a (free): GDELT import, narrow universe, tagged era.**
  - Importer (promote the spike into a fetch-once tool): `source='gdelt-<domain>'`,
    title-only embeddings, PIT by GDELT `seendate`.
  - Scope: the 8–16 backtest tickers only, 2025-01 → 2026-02 (≈ +56 weekly cohorts,
    3–4× today's sample). NOT the full 120-ticker universe — bucket-summary LLM cost
    scales as tickers × weeks (16×60 ≈ 1k bucket summaries ≈ tens of $ on flash-lite;
    120×270 ≈ 32k ≈ hundreds — don't pay that before the verdict).
  - Backfill processing: embed rows → weekly-bucket summaries → detection replay for
    the era. Graph analogs: pre-2026-02 events get daily-bar `move_1d` only (intraday
    windows tombstone as `move_unavailable` — mechanism already exists).
  - B1 report gets a **source-era flag**: cohorts before 2026-02 are marked
    `corpus_era='gdelt'` and reported both pooled and split. The era seam is a
    *confound to measure*, not to hide.
  - **Verdict question**: do pre-feed cohorts produce sane signals, and does the
    extended window change any B1 conclusion? If detection collapses on title-only
    corpus era, that alone justifies the paid archive.
- **D2b (paid, conditional on D2a): one-shot clean archive pull.**
  - NewsAPI.ai/Event Registry — buy the entry tier for 1–2 months, pull the universe's
    queries for **2021-01 → 2026-02** (spans the 2022 bear, 2023 recovery, 2024–25
    bull → 3+ regimes, the acceptance bar B2 actually needs), store full text +
    metadata, cancel. Archive tokens price per year covered — budget the pull, don't
    subscribe indefinitely.
  - **Before paying**: confirm TOS permits storing retrieved content for internal
    research (their token model implies yes for internal use; verify in writing), and
    verify current plan prices on newsapi.ai/plans (token math changed before).
  - Alternatives if TOS/pricing disappoint: Marketaux / StockNewsAPI (cheaper,
    shallower archives), Webz.io news archive (bulk), Dow Jones DNA / LSEG (real
    Bloomberg-grade parity, enterprise-priced — out of budget band by default).
- **Survivorship note (applies to any pre-2024 backtest)**: the universe is *today's*
  top-120. For 2021–2022 cohorts either freeze a PIT universe (e.g. S&P 100
  constituents as of the era — lists are freely reconstructable) or document the
  long-bias this injects. Cheap discipline: store `universe_version` on backtest rows.

### D3. History for forward-only sources (paid, only when the feature is prioritized)

| Need | Provider | Order of magnitude | Trigger |
|---|---|---|---|
| Options IV history (unlock IV-percentile `priced_in_check` now instead of ~2027 after forward accumulation) | CBOE DataShop one-shot EOD summaries; or ORATS API (history to 2007) | DataShop: per-dataset one-time $100–300 range; ORATS ~$99/mo | when priced_in IV upgrade is scheduled |
| PIT consensus-estimate history | FMP paid tier (see B2 below) or Sharadar | $29–79/mo band | Phase 12 v2 / expectation-gap history |
| 1-minute bars history (intraday agent + AFFECTS move backfill) | Polygon.io stocks (≈$29/mo tier, ~5y minute history) | one-shot month | only if the intraday program needs pre-2026 reactions |

None of these block Track D's main line; they piggyback on the same "buy a month, pull,
cancel" pattern as D2b.

---

## Track B — Breadth (better live agent)

### B1. Free breadth (start immediately, advisory-first)

1. **Press-release wires (Plan 9.6)** — PR Newswire / Business Wire / GlobeNewswire
   RSS: bodies are *meant* to be distributed, so full text is legally storable —
   exactly what the RSS trio can't give. New `source` labels, dedup vs EDGAR EX-99
   exhibits (often the same press release: normalize URL + fuzzy-title match before
   insert). Feeds guidance-extraction (#40-41) with genuinely fetchable text.
2. **Per-ticker headline feeds** — Yahoo Finance per-ticker RSS (free, dense) as a
   coverage-gap filler for universe names the topic feeds under-cover. Title+summary
   only; tag and measure incremental non-duplicate rate for a month before deciding
   it's corpus-worthy.
3. **Macro breadth (free FRED, same fetcher)**: `T10Y2Y` (curve), `DTWEXBGS` (dollar),
   `T5YIFR` (5y5y inflation), `NFCI` (financial conditions), `ICSA` (claims). All deep
   history, all PIT-clean. Advisory regime stamps only (Phase 14 discipline: nothing
   gates on single-regime data).
4. **Restart the intraday pipeline** — `agent_results` stalled 2026-06-06; the Phase 10
   bridge starves from that date. This is deployed capability losing data *now* (check
   the news_agent container on the server — deploy-checklist item already exists).
5. **Freshness registration** for every feed above; wires/headlines start `_ADVISORY`.

Explicitly NOT in B1: scraping news bodies or transcript sites (licensing verdict
already taken), 13F holdings (heavy parse, low marginal value for a weekly agent —
parked in the backlog).

### B2. Paid breadth (ranked by $/leverage)

1. **FMP paid key — the single highest-leverage subscription.** One key unblocks:
   - **Earnings-call transcripts (Plan 9.3)** — repo status: "one key → ~1h to wire"
     (free tier 402s the endpoint; fetcher plumbing + `FMP_API_KEY` config already
     exist).
   - **Analyst estimates / consensus** — turns the forward-only snapshot into a richer
     PIT archive going forward, plus historical surprises for analogs.
   - 30y fundamentals as an EDGAR cross-check (never a replacement — EDGAR stays the
     PIT source of truth).
   - Pricing: paid tiers sit in the ~$29–79/mo band (bandwidth-tiered; verify which
     tier carries transcripts + estimates before subscribing — their pricing page
     gates exact numbers).
2. **NewsAPI.ai one-shot archive** — D2b above (conditional on the D2a verdict).
3. **Options history (ORATS / CBOE DataShop)** — D3 row 1, when the IV feature is
   scheduled.
4. **Sharadar (~$50/mo band) — deferred.** EDGAR + `valuation_pit` already reconstruct
   PIT valuation; Sharadar only wins if we want *their* normalization or indicators we
   can't rebuild. Revisit after FMP.

---

## Sequencing & budget

| Step | Track | Cost | Depends on |
|---|---|---|---|
| 1. D1 free-depth backfills + embedding gap + intraday restart | D+B | $0 (+small LLM for embeddings) | — |
| 2. B1 wires/headlines/macro fetchers, advisory tools, freshness | B | $0 | — |
| 3. D2a GDELT import (narrow universe) + era-flagged extended backtest | D | $0 + ~tens of $ LLM replay | D1 |
| 4. FMP key → transcripts tool + estimates archive | B | ~$29–79/mo | — (parallel anytime) |
| 5. **Decision gate**: D2a verdict → D2b paid archive pull (2021→) | D | ~$90–200 one-shot | 3 |
| 6. D3 options/minute/consensus history pulls | D | per-need one-shots | feature scheduling |

Budget tiers this yields:
- **$0/mo**: steps 1–3 — extended (2025+) backtests, full-text press releases, richer
  macro, restored intraday flow.
- **~$30–80/mo**: + FMP — transcripts + consensus (the two oldest blocked backlog
  items).
- **one-shots on demand**: archive pull, options history, minute history — each bought
  for a month, landed as PIT tables, cancelled.

## Open decisions (user)

1. Budget ceiling: is a standing ~$30–80/mo subscription (FMP) acceptable, and a
   ~$100–200 one-shot for the news archive if D2a says depth pays?
2. Archive depth target: 2024 (cheap, 2 regimes-ish) vs 2021 (3+ regimes, the honest
   B2 acceptance bar)?
3. Does the intraday program need pre-2026 minute history (Polygon one-shot), or is
   forward accumulation enough?

## Hygiene (found while surveying — independent of the plan)

- `bloomberg_news_fetcher/DATABASE_SCHEMA.md` is a year stale (says 69 news rows) and
  embeds a plaintext DB credential — scrub the credential, mark the doc historical or
  regenerate from the live schema.
- Legacy `news.source` labels (`cnbc`, `reuters`, `feeds`, 85 rows total) predate the
  per-feed labels — harmless, but worth a one-line mapping note in SCHEMAS.md so
  source-split stats don't mysteriously drop them.
