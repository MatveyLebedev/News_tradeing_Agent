"""
Phase 1 nodes: corpus_collection and background_profiling (map-reduce).

corpus_collection (no LLM): fetch the company corpus, bucket by week, and apply the
NO_DATA gate (too few articles → early exit, like news_agent's significance pre-check).

background_profiling (LLM map-reduce): summarize each weekly bucket with the cheap map
model, then merge the summaries into a NewsBackgroundProfile with the main model. This
profile IS the "news background".
"""
import json
from collections import Counter
from typing import Any, Dict, List

from ..schemas import (
    PeriodBucketSummary, NewsBackgroundProfile, BackgroundShift, FundamentalsTrend,
    ShiftAnalogs, LongTermSignal, ReflectionOutput, DeepAnalysisOutput, DeepAnalysisFinding,
)
from ..tools.news_corpus_tool import get_company_news_corpus, bucket_by_week
from ..tools.background_metrics import compute_shift_metrics
from ..tools.fundamentals_tool import read_fundamentals
from ..tools.technical_tool import compute_daily_technicals
from ..tools.shift_library_tool import find_similar_shifts
from ..tools.risk_tool import priced_in_check, earnings_event_risk
from ..tools import bucket_cache
from datetime import timedelta

# Cap articles fed to the map model per bucket (keeps token cost bounded).
MAX_ARTICLES_PER_BUCKET = 12
SUMMARY_TRUNC = 240


def _msg(state: Dict[str, Any], name: str, content: str, data: Dict = None):
    state.setdefault("messages", []).append(
        {"role": "assistant", "name": name, "content": content, "data": data or {}}
    )


# Moved to shared infra (Phase 1); alias keeps existing call sites and tests unchanged.
from core.llm import invoke_structured as _invoke_structured


def _fallback_profile(summaries, sent_timeline):
    """Build a usable NewsBackgroundProfile from the (deterministic) bucket summaries when the
    reduce LLM still fails after retries — far better than an empty '(profile unavailable)'."""
    themes = Counter(t for s in summaries for t in (s.get("themes") or []))
    top = [t for t, _ in themes.most_common(5)]
    avg = (sum(sent_timeline) / len(sent_timeline)) if sent_timeline else 0.0
    mood = "positive" if avg > 0.1 else "negative" if avg < -0.1 else "mixed"
    return {
        "summary": f"Background mood {mood} (avg weekly sentiment {avg:+.2f}). "
                   f"Recurring themes: {', '.join(top) or 'n/a'}. (auto-summary; LLM reduce unavailable)",
        "dominant_narratives": [{"label": t, "direction": "mixed", "weeks_active": 0, "evidence": []} for t in top[:3]],
        "sentiment_timeline": sent_timeline,
        "attention_timeline": [s.get("attention", "normal") for s in summaries],
        "recurring_risks": [],
        "recurring_catalysts": [],
    }


def _analog_desc(analogs: Dict[str, Any]) -> str:
    """Render the analog base rate HONESTLY for prompts (Phase 3): below MIN_ANALOGS the
    rate is suppressed with an explicit do-not-weigh instruction (an LLM anchors on any
    number it sees), and above it `share_positive` carries its Wilson 95% CI so small-n
    uncertainty is visible instead of a false point estimate."""
    analogs = analogs or {}
    n = analogs.get("n_analogs") or 0
    if not n:
        return f"no graded analogs yet ({analogs.get('fundamentals_matched_note') or 'cold start'})"
    if analogs.get("insufficient"):
        note = analogs.get("fundamentals_matched_note") or f"insufficient analogs (n={n})"
        return f"{note}. DO NOT weigh analog base rates in this decision."
    ci = analogs.get("share_positive_ci")
    ci_txt = f" (95% CI {ci[0]:.0%}–{ci[1]:.0%})" if ci else ""
    return (f"{n} past analogs, median 4w return {analogs.get('median_fwd_return_4w')}, "
            f"median excess vs SPY {analogs.get('median_excess_4w')}, "
            f"share positive {analogs.get('share_positive')}{ci_txt}")


def _has_usable_fundamentals(fund: Dict[str, Any]) -> bool:
    """True only if income-side fundamentals are actually present (not just balance-sheet rows).

    Banks/financials often yield has_statements=True (Assets/Liabilities/Equity) but revenue
    and margins are None because their income is filed under non-standard us-gaap tags.
    """
    if not fund.get("has_statements"):
        return False
    if fund.get("revenue_yoy") is not None:
        return True
    for m in fund.get("margin_series", []) or []:
        if any(m.get(k) is not None for k in ("gross_margin", "operating_margin", "net_margin")):
            return True
    return False


# ---------------------------------------------------------------------------
# 1. corpus_collection
# ---------------------------------------------------------------------------
def corpus_collection_node(state, db_manager, neo4j_manager, config) -> Dict[str, Any]:
    ticker = state["ticker"]
    as_of = state["as_of"]
    lookback = state.get("lookback_days", config.LOOKBACK_DAYS_BACKGROUND)
    bucket_days = config.BUCKET_DAYS

    print(f"\n=== Corpus Collection: {ticker} (as_of={as_of.date()}, lookback={lookback}d) ===")
    corpus = get_company_news_corpus(
        ticker, as_of, lookback, db_manager, neo4j_manager,
        min_confidence=config.AFFECTS_MIN_CONFIDENCE,
        embed_enabled=config.CORPUS_EMBED_ENABLED,
        embed_tau=config.EMBED_TAU,
        embed_top_k=config.EMBED_TOP_K,
    )
    articles = corpus["articles"]
    buckets = bucket_by_week(articles, as_of, lookback, bucket_days)

    state["news_corpus"] = articles
    state["corpus_provenance"] = corpus["provenance"]
    state["period_buckets"] = buckets
    state["article_count"] = len(articles)

    print(f"  provenance: {corpus['provenance']}")
    print(f"  weekly buckets: {[b['count'] for b in buckets]}")

    # Phase 13 (detection v3): sector baseline for the excess-series break test.
    # Computed here because this node holds both db and neo; cached per (sector, as_of).
    if getattr(config, "METRICS_VERSION", 1) >= 3:
        from ..tools.sector_sentiment import sector_baseline
        try:
            base = sector_baseline(ticker, as_of, lookback, db_manager, neo4j_manager,
                                   min_confidence=config.AFFECTS_MIN_CONFIDENCE)
        except Exception as e:
            print(f"  ⚠ sector baseline failed ({e}) — v3 degrades to v2 for this run")
            base = None
        state["sector_baseline"] = base
        print(f"  sector baseline: {'%d days' % len(base) if base else 'unavailable'}")

    if len(articles) < config.MIN_ARTICLES_FOR_BACKGROUND:
        state["no_data"] = True
        _msg(state, "corpus_collection",
             f"NO_DATA: only {len(articles)} articles (< {config.MIN_ARTICLES_FOR_BACKGROUND})",
             {"article_count": len(articles)})
        print(f"  → NO_DATA (below MIN_ARTICLES_FOR_BACKGROUND={config.MIN_ARTICLES_FOR_BACKGROUND})")
    else:
        state["no_data"] = False
        _msg(state, "corpus_collection",
             f"Collected {len(articles)} articles across {len(buckets)} weekly buckets",
             {"article_count": len(articles), "provenance": corpus["provenance"]})
    return state


# ---------------------------------------------------------------------------
# 2. background_profiling (map-reduce)
# ---------------------------------------------------------------------------
def _bucket_text(bucket: Dict[str, Any], articles_by_id: Dict[int, Dict]) -> str:
    lines = []
    for aid in bucket["article_ids"][:MAX_ARTICLES_PER_BUCKET]:
        a = articles_by_id.get(aid)
        if not a:
            continue
        title = (a.get("title") or "").strip()
        summ = (a.get("summary") or "").strip()[:SUMMARY_TRUNC]
        lines.append(f"- {title}" + (f" — {summ}" if summ else ""))
    return "\n".join(lines)


def _map_bucket(llm_map, bucket, articles_by_id) -> Dict[str, Any]:
    """Summarize one weekly bucket → PeriodBucketSummary (with graph-sentiment seed)."""
    if bucket["count"] == 0:
        return PeriodBucketSummary(sentiment=0.0, attention="low").model_dump()

    sent_counts = Counter(bucket.get("affects_sentiments", []))
    seed = (f"Graph sentiment labels this week: {dict(sent_counts)}." if sent_counts else "")
    text = _bucket_text(bucket, articles_by_id)
    prompt = (
        f"You summarize one week of news about a single company. "
        f"Week {bucket['start'].date()}..{bucket['end'].date()}, {bucket['count']} articles.\n"
        f"{seed}\n\nHeadlines:\n{text}\n\n"
        f"Extract the dominant themes, concrete key events, a net sentiment (-1..1), and an "
        f"attention level. Ground events in the headlines; do not invent."
    )
    try:
        out: PeriodBucketSummary = llm_map.with_structured_output(PeriodBucketSummary).invoke(prompt)
        return out.model_dump()
    except Exception as e:
        print(f"    ⚠ map failed for bucket {bucket['index']}: {e}")
        # fall back to graph-sentiment-derived value
        pos = sent_counts.get("positive", 0) + sent_counts.get("bullish", 0)
        neg = sent_counts.get("negative", 0) + sent_counts.get("bearish", 0)
        tot = pos + neg
        s = (pos - neg) / tot if tot else 0.0
        return PeriodBucketSummary(sentiment=s, attention="normal", themes=["(map unavailable)"]).model_dump()


def background_profiling_node(state, llm_map, llm_reduce, config, db_manager=None, skip_reduce=False) -> Dict[str, Any]:
    print("\n=== Background Profiling (map" + ("" if skip_reduce else "-reduce") + ") ===")
    buckets = state["period_buckets"]
    articles_by_id = {a["id"]: a for a in state["news_corpus"]}
    as_of = state["as_of"]
    ticker = state["ticker"]
    bucket_days = config.BUCKET_DAYS
    # Cache key ≠ detection version: summaries depend on corpus+map prompt+map model only,
    # which are identical across metrics v1/v2 (see config.BUCKET_CACHE_VERSION).
    version = config.BUCKET_CACHE_VERSION

    # --- MAP (with per-week cache for COMPLETE buckets only) ---
    cache_hits = 0
    summaries = []
    for b in buckets:
        complete = (b["start"] + timedelta(days=bucket_days)) <= as_of  # week fully elapsed
        week_start = b["start"].date()
        s = None
        if db_manager and complete:
            s = bucket_cache.get_cached(db_manager, ticker, week_start, version)
            if s is not None:
                cache_hits += 1
        if s is None:
            s = _map_bucket(llm_map, b, articles_by_id)
            if db_manager and complete and b["count"] > 0:
                bucket_cache.put_cached(db_manager, ticker, week_start, version, s)
        s["_bucket_index"] = b["index"]
        s["_week"] = f"{b['start'].date()}..{b['end'].date()}"
        s["_count"] = b["count"]
        summaries.append(s)
    state["bucket_summaries"] = summaries
    if db_manager:
        print(f"  map: {cache_hits}/{len(buckets)} buckets from cache")
    sent_timeline = [round(s.get("sentiment", 0.0), 2) for s in summaries]
    print(f"  bucket sentiments (old→new): {sent_timeline}")

    # Detection-only fast path (replay/backtest): the shift DECISION is computed from the
    # map output (bucket sentiments) in compute_shift_metrics, not from the reduce. So we skip
    # the expensive reduce LLM call entirely and store a minimal profile.
    if skip_reduce:
        state["background_profile"] = {
            "summary": "(detection-only: reduce skipped)",
            "sentiment_timeline": sent_timeline,
        }
        return state

    # --- REDUCE ---
    digest_lines = []
    for s in summaries:
        digest_lines.append(
            f"week {s['_week']} (n={s['_count']}): sentiment={s.get('sentiment',0):+.2f}, "
            f"attention={s.get('attention')}, themes={s.get('themes', [])[:4]}, "
            f"events={s.get('key_events', [])[:3]}"
        )
    digest = "\n".join(digest_lines)
    prompt = (
        f"You are building the news-background profile for {state['ticker']} over the lookback "
        f"window (weekly summaries below, oldest first).\n\n{digest}\n\n"
        f"Produce: a 2-4 sentence plain-English background; the dominant narratives with their "
        f"direction and how many weeks each ran; the per-week sentiment timeline (reuse the values "
        f"above); the attention timeline; recurring risks; recurring catalysts. Ground everything "
        f"in the weekly summaries."
    )
    try:
        profile: NewsBackgroundProfile = _invoke_structured(llm_reduce, NewsBackgroundProfile, prompt)
        profile_d = profile.model_dump()
        if not profile_d.get("sentiment_timeline"):
            profile_d["sentiment_timeline"] = sent_timeline
    except Exception as e:
        print(f"  ⚠ reduce failed after retries: {e}; using bucket-derived fallback profile")
        profile_d = _fallback_profile(summaries, sent_timeline)

    state["background_profile"] = profile_d
    _msg(state, "background_profiling", profile_d.get("summary", ""),
         {"sentiment_timeline": profile_d.get("sentiment_timeline")})
    print(f"  → {profile_d.get('summary', '')[:200]}")
    return state


# ---------------------------------------------------------------------------
# 3. shift_detection (quantitative metrics → LLM interpretation)
# ---------------------------------------------------------------------------
def shift_detection_node(state, llm, config, neo4j_manager=None, characterize=True) -> Dict[str, Any]:
    print("\n=== Shift Detection ===")
    metrics = compute_shift_metrics(
        bucket_summaries=state["bucket_summaries"],
        period_buckets=state["period_buckets"],
        as_of=state["as_of"],
        shift_window_days=state.get("shift_window_days", config.SHIFT_WINDOW_DAYS),
        bucket_days=config.BUCKET_DAYS,
        ticker=state["ticker"],
        z_threshold=config.SENTIMENT_SHIFT_Z_THRESHOLD,
        min_abs_delta=config.MIN_ABS_DELTA,
        attention_spike_ratio=config.ATTENTION_SPIKE_RATIO,
        neo4j_manager=neo4j_manager,
        metrics_version=config.METRICS_VERSION,
        articles=state.get("news_corpus"),
        sector_baseline=state.get("sector_baseline"),
    )
    state["background_metrics"] = metrics
    sm = metrics["sentiment"]
    print(f"  sentiment: recent={sm['recent_mean']} baseline={sm['baseline_mean']} "
          f"z={sm['z']} | attention spike={metrics['attention']['spike_ratio']}x | "
          f"turnover={metrics['narrative_turnover']} | flag={metrics['shift_flag']} "
          f"score={metrics['shift_score']}")
    if metrics.get("sector"):
        sec = metrics["sector"]
        print(f"  sector: break={sec.get('sector_break')} dir={sec.get('direction')} "
              f"z={sec.get('z')} (n_days={sec.get('n_days')}, available={sec.get('available')})")

    # DETERMINISTIC decision: existence/direction/magnitude come from the metrics, not the
    # LLM — so the shift_library labels are reproducible. The LLM ONLY characterizes
    # (catalysts, inflection refinement, reasoning); it cannot flip has_shifted.
    has_shifted = metrics["shift_flag"]
    direction = metrics["direction"]
    profile = state.get("background_profile", {})

    catalysts, reasoning, inflection = [], "", metrics["inflection_week"]
    if has_shifted and characterize:
        # Consume reviewer critique on a reflection loop-back, then clear it (Phase 3).
        critique = state.pop("reflection_advice", "") if state.get("loop_to") == "shift_detection" else ""
        critique_line = f"\nREVIEWER CRITIQUE TO ADDRESS: {critique}\n" if critique else ""
        prompt = (
            f"The news background for {state['ticker']} HAS shifted {direction} "
            f"(z={sm['z']}, score={metrics['shift_score']}). Do NOT re-decide whether it shifted — "
            f"that is settled. Your job is to CHARACTERIZE it.\n\n"
            f"Background summary: {profile.get('summary','')}\n"
            f"Per-week sentiment (old→new): {profile.get('sentiment_timeline', [])}\n"
            f"Candidate inflection week: {metrics['inflection_week']}\n"
            f"New stories in recent window: {[s.get('label') for s in metrics['new_stories']][:5]}\n"
            f"{critique_line}\n"
            f"Set has_shifted=true, direction={direction}. Give the inflection_date, list candidate "
            f"catalysts grounded in the summary/new-stories, and explain the shift concisely."
        )
        try:
            shift: BackgroundShift = llm.with_structured_output(BackgroundShift).invoke(prompt)
            sd = shift.model_dump()
            catalysts = sd.get("candidate_catalysts", []) or []
            reasoning = sd.get("reasoning", "") or ""
            inflection = sd.get("inflection_date") or metrics["inflection_week"]
        except Exception as e:
            print(f"  ⚠ shift characterization failed: {e}")
            reasoning = "(characterization unavailable)"

    # Build the authoritative record from deterministic values + LLM characterization.
    shift_d = BackgroundShift(
        has_shifted=has_shifted,
        direction=direction,
        magnitude_z=sm["z"],
        shift_score=metrics["shift_score"],
        inflection_date=inflection if has_shifted else None,
        candidate_catalysts=catalysts,
        reasoning=reasoning,
    ).model_dump()

    state["background_shift"] = shift_d
    verdict = ("SHIFT " + (shift_d.get("direction") or "")) if shift_d.get("has_shifted") else "no shift"
    _msg(state, "shift_detection", f"{verdict}: {shift_d.get('reasoning','')[:160]}",
         {"metrics": metrics, "shift": shift_d})
    print(f"  → {verdict} (z={shift_d.get('magnitude_z')}, inflection={shift_d.get('inflection_date')})")
    return state


# ---------------------------------------------------------------------------
# 4. fundamentals_analysis (read fundamentals/technicals → confirm/contradict the shift)
# ---------------------------------------------------------------------------
def fundamentals_analysis_node(state, llm, db_manager, config) -> Dict[str, Any]:
    print("\n=== Fundamentals Analysis ===")
    ticker, as_of = state["ticker"], state["as_of"]
    fund = read_fundamentals(db_manager, ticker, as_of)
    tech = compute_daily_technicals(db_manager, ticker, as_of, config.LOOKBACK_DAYS_TECHNICAL_LT)
    state["fundamentals"] = fund
    state["technical_longterm"] = tech

    print(f"  revenue_yoy={fund['revenue_yoy']} | margins(newest)="
          f"{fund['margin_series'][0] if fund['margin_series'] else None}")
    print(f"  valuation={ {k: fund['valuation'].get(k) for k in ['trailingPE','forwardPE']} } "
          f"| expectation_gap={fund['expectation_gap']}")
    print(f"  technicals: trend={tech.get('trend')} return={tech.get('return_window_pct')}% "
          f"(available={tech.get('available')})")

    if not fund["has_statements"]:
        state["fundamentals"]["verdict"] = "unknown"
        _msg(state, "fundamentals_analysis", "No statements available — verdict unknown", {})
        print("  → no statements; verdict=unknown")
        return state

    shift = state.get("background_shift", {})
    shift_desc = (f"{shift.get('direction')} shift (z={shift.get('magnitude_z')})"
                  if shift.get("has_shifted") else "no significant news shift")
    margins = fund["margin_series"]
    # Consume reviewer critique on a reflection loop-back to this node, then clear it (Phase 3).
    critique = state.pop("reflection_advice", "") if state.get("loop_to") == "fundamentals_analysis" else ""
    critique_line = f"REVIEWER CRITIQUE TO ADDRESS: {critique}\n\n" if critique else ""
    # Phase 12 — structured evidence blocks (all point-in-time, wired from stored filings;
    # blocks render only when data exists so thin tickers don't get walls of None).
    def _blk(label, d):
        return f"  {label}: {d}\n" if d else ""
    prompt = (
        f"You judge whether {ticker}'s FUNDAMENTALS confirm, ignore, or contradict the news-background shift.\n\n"
        f"{critique_line}"
        f"News-background shift: {shift_desc}. {shift.get('reasoning','')}\n\n"
        f"Fundamentals (as-of {fund['as_of']}, point-in-time):\n"
        f"  GROWTH: revenue YoY={fund['revenue_yoy']}, QoQ={fund['revenue_qoq']}\n"
        f"  PROFITABILITY: margin series (newest→oldest): {margins[:4]}\n"
        + _blk("EPS trajectory", fund.get("eps_trajectory"))
        + _blk("OPEX intensity (R&D/SG&A share of revenue, Δ vs yr ago)", fund.get("opex"))
        + f"  VALUATION: {fund['valuation']}\n"
        + _blk("PEER MULTIPLE (fwd P/E vs sector median — 'expensive vs peers' ≠ 'expensive')",
               fund.get("peer_multiples"))
        + f"  CAPITAL (as-filed, window={fund.get('capital', {}).get('window')}): "
          f"FCF={fund.get('capital', {}).get('fcf')}, "
          f"capex/OCF={fund.get('capital', {}).get('capex_intensity')}, "
          f"capital return vs FCF={fund.get('capital', {}).get('capital_return_vs_fcf')}\n"
        + _blk("BALANCE SHEET", fund.get("balance"))
        + _blk("STREET (target gap, recommendation, beta, 52w position)", fund.get("street"))
        + f"  analyst estimate revisions (expectation gap): {fund['expectation_gap']}\n"
        f"  long-term technicals: {tech}\n\n"
        f"Decide the verdict:\n"
        f"  'confirms'    — fundamentals move the SAME way as the news shift\n"
        f"  'contradicts' — fundamentals move OPPOSITE (e.g. negative news shift but accelerating "
        f"revenue/margins → overreaction candidate)\n"
        f"  'ignores'     — fundamentals are flat / unrelated to the shift\n"
        f"Fill revenue_growth_trend, margin_trend, fcf_trend, debt_trend with short phrases grounded "
        f"in the numbers above. Also fill the five SUB-VERDICTS (growth_sub, profitability_sub, "
        f"balance_sheet_sub, capital_allocation_sub, street_sub) as improving/stable/deteriorating/"
        f"unknown — each judged ONLY on its block above. Be concrete."
    )
    try:
        ft: FundamentalsTrend = llm.with_structured_output(FundamentalsTrend).invoke(prompt)
        ft_d = ft.model_dump()
    except Exception as e:
        print(f"  ⚠ fundamentals verdict failed: {e}")
        ft_d = FundamentalsTrend(verdict="unknown", reasoning="(LLM unavailable)").model_dump()

    state["fundamentals"]["trend"] = ft_d
    _msg(state, "fundamentals_analysis",
         f"verdict={ft_d.get('verdict')}: {ft_d.get('reasoning','')[:160]}", {"trend": ft_d})
    print(f"  → verdict={ft_d.get('verdict')} (vs the news shift)")
    return state


# ---------------------------------------------------------------------------
# 4b. similar_shift_analysis (analog base rates from the shift library)
# ---------------------------------------------------------------------------
def similar_shift_analysis_node(state, db_manager, config) -> Dict[str, Any]:
    print("\n=== Similar-Shift Analysis ===")
    shift = state.get("background_shift", {})
    metrics = state.get("background_metrics", {})
    if not shift.get("has_shifted"):
        analogs = ShiftAnalogs(n_analogs=0, fundamentals_matched_note="no shift → no analog lookup").model_dump()
        state["shift_analogs"] = analogs
        print("  → no shift; skipping analog lookup")
        return state

    causal = [s.get("label") for s in metrics.get("new_stories", [])][:8]
    analogs = find_similar_shifts(
        db_manager, state["ticker"], state["as_of"], config.DETECTION_VERSION,
        direction=shift.get("direction"), target_z=shift.get("magnitude_z"),
        causal_types=causal, k=6, min_analogs=config.MIN_ANALOGS,
    )
    state["shift_analogs"] = analogs
    if analogs["n_analogs"]:
        ci = analogs.get("share_positive_ci")
        suffix = " [INSUFFICIENT — not weighed]" if analogs.get("insufficient") else (
            f" (95% CI {ci[0]:.0%}–{ci[1]:.0%})" if ci else "")
        print(f"  → {analogs['n_analogs']} analogs | median 4w return={analogs['median_fwd_return_4w']} "
              f"| share positive={analogs['share_positive']}{suffix}")
    else:
        print(f"  → {analogs['fundamentals_matched_note']}")
    _msg(state, "similar_shift_analysis",
         f"{analogs['n_analogs']} analogs, median 4w={analogs.get('median_fwd_return_4w')}", {"analogs": analogs})
    return state


# ---------------------------------------------------------------------------
# 4c. deep_analysis (Phase 7A — map-reduce cross-company research; advisory only)
# ---------------------------------------------------------------------------
# Isolation, not truncation: each research thread runs in its OWN small context (a focused
# bounded tool loop) and returns a short finding. No single context accumulates every tool
# result, so the reduce can't be starved/truncated (the Mode-3 problem) — mirrors the
# background_profiling map-reduce. Hard boundary unchanged: writes only `enriched_analysis`,
# never `background_shift`.
_MAP_MAX_ITERS = 2      # tool ROUNDS per isolated thread (finalize is a separate call)
_MAP_TRUNC = 1200       # per-result cap inside a map thread


def _exec_tool(tools, name, args):
    for t in tools:
        if t.name == name:
            try:
                return str(t.invoke(args))
            except Exception as e:  # graceful: the model sees the error and can adapt
                return json.dumps({"error": str(e)})
    return json.dumps({"error": f"tool {name} not found"})


def _run_map_thread(llm_map, tools, seed, max_iter=_MAP_MAX_ITERS, trunc=_MAP_TRUNC):
    """Run ONE focused bounded tool loop in its own context; return (finding_dict, tools_used).
    The transcript never exceeds ~max_iter rounds, so the map finalize can't truncate."""
    from langchain_core.messages import SystemMessage, ToolMessage
    messages = [SystemMessage(content=seed)]
    llm_tools = llm_map.bind_tools(tools)
    used: List[str] = []
    # Literal semantics (Phase 3 fix): exactly ≤ max_iter tool rounds, then the caller's
    # separate structured finalize. The old loop broke one round early, so "3" delivered 2.
    for _ in range(max_iter):
        resp = llm_tools.invoke(messages)
        messages.append(resp)
        calls = getattr(resp, "tool_calls", None)
        if not calls:
            break
        for call in calls:
            name, args, cid = call.get("name", ""), call.get("args", {}), call.get("id", "")
            used.append(name)
            messages.append(ToolMessage(content=_exec_tool(tools, name, args)[:trunc], tool_call_id=cid))
    try:
        finding = _invoke_structured(llm_map, DeepAnalysisFinding, messages).model_dump()
    except Exception:
        finding = DeepAnalysisFinding().model_dump()
    return finding, used


def deep_analysis_node(state, llm_map, llm_reduce, db_manager, neo4j_manager, config) -> Dict[str, Any]:
    """Map-reduce cross-company research. MAP: a focused tool-using call per research thread,
    each in an isolated small context → a short finding. REDUCE: the main model folds the
    findings into DeepAnalysisOutput (summary + advisory conviction_adjustment). Advisory only:
    never edits `background_shift`. Runs only on a detected shift."""
    print("\n=== Deep Analysis (Phase 7A, map-reduce) ===")
    if not getattr(config, "DEEP_ANALYSIS_ENABLED", False):
        print("  → disabled (LT_DEEP_ANALYSIS=false); skipping")
        return state
    shift = state.get("background_shift", {})
    if state.get("no_data") or not shift.get("has_shifted"):
        print("  → no shift / no data; skipping enrichment")
        return state

    from ..tools.analysis_tools import build_deep_analysis_tools

    ticker, as_of = state["ticker"], state["as_of"]
    trend = (state.get("fundamentals", {}).get("trend") or {})
    analogs = state.get("shift_analogs", {})
    fund = state.get("fundamentals", {})
    profile = state.get("background_profile", {})
    news_summary = (profile.get("summary") or "").strip()

    tools = build_deep_analysis_tools(
        ticker, as_of, db_manager, neo4j_manager, config,
        shift=shift, metrics=state.get("background_metrics", {}))
    if not tools:
        print("  → no tools available; skipping")
        return state

    # Resolve peers deterministically so every thread has concrete comparison targets.
    peers = []
    try:
        from news_agent.tools.sector_competitor_tool import get_industry_competitors
        peers = get_industry_competitors(fund.get("sector"), fund.get("industry"), ticker.upper())[:4]
    except Exception:
        peers = []
    peer_line = ", ".join(peers) if peers else "(use the sector tool to find them)"
    analog_line = _analog_desc(analogs)

    # Shared context every map thread sees — just the small deterministic labels.
    context = (
        f"{ticker} (as-of {as_of.date()}) — detected news-background SHIFT.\n"
        f"  shift: {shift.get('direction')} (z={shift.get('magnitude_z')}, score={shift.get('shift_score')}); "
        f"catalysts {shift.get('candidate_catalysts')}\n"
        f"  news background: {news_summary or '(unavailable)'}\n"
        f"  fundamentals verdict vs shift: {trend.get('verdict','unknown')}\n"
        f"  sector/industry: {fund.get('sector')} / {fund.get('industry')}\n"
        f"  PEERS: {peer_line}\n"
        f"  analog base rate: {analog_line}\n"
        f"Do NOT recompute or contradict these deterministic labels.\n"
    )

    # The research threads — each runs in isolation (its own context) as one MAP unit.
    threads = [
        ("sector_breadth",
         f"FOCUSED TASK: decide whether this shift is SECTOR-WIDE or IDIOSYNCRATIC. Research the "
         f"peers ({peer_line}) — pass each peer's ticker to get_fundamentals_detail / "
         f"get_long_term_technicals — and use cross-ticker correlation + the sector tool. Are peers "
         f"moving/deteriorating with {ticker} or not?"),
        ("cause_novelty",
         "FOCUSED TASK: trace the CAUSAL CHAIN behind the shift and judge whether the catalyst is "
         "genuinely NEW or already PRICED IN (use the causal-chain, events and novelty tools)."),
        ("fundamentals",
         f"FOCUSED TASK: drill {ticker}'s quarter-level FUNDAMENTALS and contrast them with a key "
         f"peer's — do the numbers CONFIRM or CONTRADICT the shift?"),
        ("analogs",
         "FOCUSED TASK: check whether the closest historical ANALOGS support the directional read; "
         "report their base rate (find_historical_analogs)."),
    ]

    findings, all_used = [], []
    for name, task in threads:
        seed = (
            f"You are researching ONE focused question for a multi-week trade thesis.\n\n{context}\n"
            f"{task}\nYou MUST call at least one tool — do not answer from prior knowledge. Then "
            f"return a 1-3 sentence evidence-tagged `finding` and a short `signal` label "
            f"(e.g. sector-wide / idiosyncratic / confirms / contradicts / novel / priced-in)."
        )
        finding, used = _run_map_thread(llm_map, tools, seed)
        finding["thread"] = name
        findings.append(finding)
        all_used += used
        print(f"  → map[{name}]: {len(used)} tool call(s) | {str(finding.get('finding',''))[:80]}")

    # REDUCE — fold the short findings (a few hundred tokens total) into the structured output.
    digest = "\n".join(
        f"- [{f.get('thread')}] {f.get('finding','')} (signal: {f.get('signal','')})" for f in findings)
    reduce_prompt = (
        f"{context}\nRESEARCH FINDINGS (from focused tool-using passes):\n{digest}\n\n"
        f"Synthesize a DeepAnalysisOutput: `findings` (the concrete points, noting the company), a "
        f"short `summary` of the cross-company insights, `is_sector_wide`, `catalyst_assessment` "
        f"(new vs priced-in), `analog_takeaway`, and an ADVISORY `conviction_adjustment` "
        f"(-0.2..+0.2) that nudges synthesis but NEVER changes the shift label."
    )
    try:
        data = _invoke_structured(llm_reduce, DeepAnalysisOutput, reduce_prompt).model_dump()
    except Exception as e:
        print(f"  ⚠ deep_analysis reduce failed: {e}")
        data = DeepAnalysisOutput().model_dump()

    # Honest tools_used = what ACTUALLY ran across threads. If nothing ran, the enrichment is
    # evidence-free → zero the advisory nudge so a no-investigation guess can't sway a trade.
    data["tools_used"] = sorted(set(all_used))
    if not all_used:
        data["conviction_adjustment"] = 0.0
        data["note"] = "no tools were called across map threads — enrichment skipped"
        print("  ⚠ no tools ran across threads — advisory nudge zeroed (enrichment skipped)")

    state["enriched_analysis"] = data
    _msg(state, "deep_analysis",
         f"adj={data.get('conviction_adjustment')} tools={data.get('tools_used')}",
         {"enriched": data})
    print(f"  → conviction_adjustment={data.get('conviction_adjustment')} (advisory) | "
          f"sector_wide={data.get('is_sector_wide')} | findings={len(data.get('findings') or [])}")
    print(f"    summary: {str(data.get('summary',''))[:140]}")
    return state


# ---------------------------------------------------------------------------
# 5. synthesis (profile + shift + fundamentals + analogs → LongTermSignal)
# ---------------------------------------------------------------------------
# Module-level so the eval harness can (a) reference the exact production rules and
# (b) monkeypatch an inverted matrix to prove the synthesis_matrix eval catches it.
# Byte-identical to the previous inline f-string — the LLM cache key is unchanged.
SYNTHESIS_MATRIX_RULES = (
    "Apply the news×fundamentals matrix:\n"
    "  shift POSITIVE + fundamentals improving → high-conviction LONG\n"
    "  shift NEGATIVE + fundamentals deteriorating → high-conviction SHORT\n"
    "  shift NEGATIVE + fundamentals improving (contradicts) → overreaction: contrarian LONG or NEUTRAL\n"
    "  shift POSITIVE + fundamentals deteriorating → hype risk: low conviction / avoid\n"
    "  NO shift → no actionable edge for this agent: default NEUTRAL with LOW conviction "
    "(strong standing fundamentals alone are NOT a trade trigger here — this agent trades shifts).\n"
)


def synthesis_node(state, llm, config) -> Dict[str, Any]:
    print("\n=== Synthesis ===")
    profile = state.get("background_profile", {})
    shift = state.get("background_shift", {})
    fund = state.get("fundamentals", {})
    trend = (fund.get("trend") or {})
    analogs = state.get("shift_analogs", {})
    tech = state.get("technical_longterm", {})

    shift_desc = (f"{shift.get('direction')} (score={shift.get('shift_score')}, z={shift.get('magnitude_z')})"
                  if shift.get("has_shifted") else "no significant shift")
    analog_desc = _analog_desc(analogs)

    # Phase 7A: fold the deep-analysis enrichment in as ADVISORY context (it never overrides
    # the matrix or the deterministic shift — see DEEP_ANALYSIS_PLAN.md §1).
    enriched = state.get("enriched_analysis") or {}
    enriched_desc = ""
    if enriched:
        enriched_desc = (
            f"DEEP ANALYSIS (advisory): sector_wide={enriched.get('is_sector_wide')}; "
            f"insight summary={str(enriched.get('summary',''))[:300]}; "
            f"catalyst={str(enriched.get('catalyst_assessment',''))[:120]}; "
            f"analog_takeaway={str(enriched.get('analog_takeaway',''))[:120]}; "
            f"findings={enriched.get('findings', [])[:3]}; advisory conviction_adjustment="
            f"{enriched.get('conviction_adjustment', 0.0)} (you MAY weigh this ±0.2; it does NOT "
            f"change the deterministic shift label).\n"
        )

    prompt = (
        f"Form a multi-week trade thesis for {state['ticker']} (as-of {state['as_of'].date()}).\n\n"
        f"NEWS BACKGROUND: {profile.get('summary','')}\n"
        f"NEWS SHIFT: {shift_desc}. {shift.get('reasoning','')}\n"
        f"FUNDAMENTALS verdict vs shift: {trend.get('verdict','unknown')} — {trend.get('reasoning','')}\n"
        f"  revenue YoY={fund.get('revenue_yoy')}, valuation={ {k: fund.get('valuation',{}).get(k) for k in ('trailingPE','forwardPE')} }, "
        f"expectation_gap={fund.get('expectation_gap')}\n"
        f"  long-term technicals: trend={tech.get('trend')}, window return={tech.get('return_window_pct')}%\n"
        f"ANALOG BASE RATE: {analog_desc}\n"
        f"{enriched_desc}\n"
        f"{SYNTHESIS_MATRIX_RULES}"
        f"Valuation scales conviction (a positive shift at 40x sales ≠ at 12x earnings). "
        f"Give direction LONG/SHORT/NEUTRAL, conviction 0-1, horizon (e.g. '4-12 weeks'), a concise thesis, "
        f"supporting factors, key risks, and explicit invalidation conditions (what observation kills the thesis)."
    )
    try:
        sig: LongTermSignal = _invoke_structured(llm, LongTermSignal, prompt)
        sig_d = sig.model_dump()
    except Exception as e:
        print(f"  ⚠ synthesis failed after retries: {e}")
        sig_d = LongTermSignal(direction="NEUTRAL", conviction=0.0, horizon="n/a",
                               thesis="(synthesis unavailable)").model_dump()

    state["signal"] = sig_d
    _msg(state, "synthesis", f"{sig_d['direction']} conviction={sig_d['conviction']}: {sig_d['thesis'][:160]}",
         {"signal": sig_d})
    print(f"  → {sig_d['direction']} (conviction={sig_d['conviction']}, horizon={sig_d.get('horizon')})")
    return state


# ---------------------------------------------------------------------------
# 6. reflection (1-iteration quality check; may loop back)
# ---------------------------------------------------------------------------
def reflection_node(state, llm, config) -> Dict[str, Any]:
    print("\n=== Reflection ===")
    count = state.get("reflection_count", 0)
    state["reflection_count"] = count + 1
    if count >= config.MAX_REFLECTION_ITERATIONS:
        state["needs_refinement"] = False
        print("  → max iterations reached; proceeding")
        return state

    sig = state.get("signal", {})
    shift = state.get("background_shift", {})
    trend = (state.get("fundamentals", {}).get("trend") or {})
    prompt = (
        f"Review this {state['ticker']} analysis for gaps. "
        f"Shift: {shift.get('direction')} score={shift.get('shift_score')}. "
        f"Fundamentals verdict: {trend.get('verdict')}. "
        f"Signal: {sig.get('direction')} conviction={sig.get('conviction')}, thesis: {sig.get('thesis','')[:200]}.\n"
        f"If the analysis is sound, set needs_refinement=false. Only request a loop if there is a CONCRETE "
        f"gap: loop_to='shift_detection' if the shift call looks wrong, 'fundamentals_analysis' if the "
        f"fundamentals verdict is unsupported. Give brief advice."
    )
    try:
        ref: ReflectionOutput = llm.with_structured_output(ReflectionOutput).invoke(prompt)
        ref_d = ref.model_dump()
    except Exception as e:
        print(f"  ⚠ reflection failed: {e}; proceeding")
        ref_d = ReflectionOutput(needs_refinement=False).model_dump()

    state["needs_refinement"] = bool(ref_d.get("needs_refinement"))
    state["loop_to"] = ref_d.get("loop_to") or "synthesis"
    # Feed the critique into the re-run (Phase 3): without this the loop re-executes on
    # IDENTICAL inputs — a wasted LLM call, since the deterministic shift label can't move.
    # The targeted node injects this into its prompt and clears it. (Label stays fixed;
    # only the LLM CHARACTERIZATION / fundamentals verdict can be refined.)
    state["reflection_advice"] = ref_d.get("advice", "") if state["needs_refinement"] else ""
    _msg(state, "reflection", f"needs_refinement={state['needs_refinement']} loop_to={state.get('loop_to')}: "
         f"{ref_d.get('advice','')[:140]}", {"reflection": ref_d})
    print(f"  → needs_refinement={state['needs_refinement']} "
          f"(conf={ref_d.get('confidence_in_analysis')})")
    return state


# ---------------------------------------------------------------------------
# 7. risk_gate (priced-in, earnings-event, data sufficiency, conviction threshold)
# ---------------------------------------------------------------------------
def _table_conviction(table, shift_score, verdict):
    """Deterministic base conviction (gate=table): CONVICTION_TABLE[verdict][score bucket].
    Buckets on shift_score: [0, .4) / [.4, .7) / [.7, 1]."""
    row = table.get(verdict or "unknown") or table["unknown"]
    s = shift_score or 0.0
    return row[0] if s < 0.4 else row[1] if s < 0.7 else row[2]


def risk_gate_node(state, db_manager, config) -> Dict[str, Any]:
    print("\n=== Risk Gate ===")
    sig = dict(state.get("signal", {}))
    shift = state.get("background_shift", {})
    flags = []

    # Phase 7 — what the threshold gates on. "llm": the model's self-reported conviction
    # (current behavior; wf_30x showed it is anti-correlated with outcomes on the tails).
    # "table": deterministic base from evidence (shift_score × fundamentals verdict) plus
    # the advisory deep-analysis nudge; the LLM number is recorded but stops gating.
    raw_conviction = sig.get("conviction", 0.0)
    gate_mode = getattr(config, "CONVICTION_GATE", "llm")
    if gate_mode == "table":
        verdict = (state.get("fundamentals", {}).get("trend") or {}).get("verdict")
        base = _table_conviction(config.CONVICTION_TABLE, shift.get("shift_score"), verdict)
        nudge = float((state.get("enriched_analysis") or {}).get("conviction_adjustment") or 0.0)
        sig["conviction"] = round(min(1.0, max(0.0, base + nudge)), 2)
        print(f"  gate=table: base={base} (score={shift.get('shift_score')}, verdict={verdict}) "
              f"nudge={nudge:+.2f} | llm raw={raw_conviction}")

    priced = priced_in_check(db_manager, state["ticker"], state["as_of"],
                             state.get("shift_window_days", config.SHIFT_WINDOW_DAYS))
    earn = earnings_event_risk(db_manager, state["ticker"], state["as_of"], config.EARNINGS_RISK_DAYS)

    direction = sig.get("direction")

    # DATA-SUFFICIENCY GATE: the agent's premise is news × fundamentals. If there are no
    # USABLE income fundamentals, the cross-check never really happened — so a confident
    # directional call rests on news alone. This catches both the empty case AND the
    # banks/financials case where balance-sheet rows exist (has_statements=True) but
    # revenue/margins are None (their income is filed under tags we don't map). Hard-cap
    # conviction below the trade threshold so such signals degrade to FILTERED, not LONG/SHORT.
    fund = state.get("fundamentals", {})
    if not _has_usable_fundamentals(fund):
        capped = min(sig.get("conviction", 0.0), config.LONGTERM_CONFIDENCE_THRESHOLD - 0.05)
        if capped < sig.get("conviction", 0.0):
            flags.append("no usable income fundamentals (news-only) — conviction capped below threshold")
        sig["conviction"] = round(max(0.0, capped), 2)

    # STALE-DATA GATE (Phase 2): entry points stamp `data_freshness` for live runs only
    # (backtests replay historical data by construction and are never stamped). A signal
    # computed on frozen sources looks normal and is silently wrong — cap it below the
    # trade threshold so it degrades to FILTERED, with the reason visible in the flags.
    fresh = state.get("data_freshness")
    if fresh and fresh.get("any_stale"):
        capped = min(sig.get("conviction", 0.0), config.LONGTERM_CONFIDENCE_THRESHOLD - 0.05)
        if capped < sig.get("conviction", 0.0):
            flags.append(f"stale data sources {fresh.get('stale')} — conviction capped below threshold")
        sig["conviction"] = round(max(0.0, capped), 2)

    # NO-SHIFT GATE: this agent trades background SHIFTS. With no detected shift the matrix has
    # no cell and synthesis conviction is unstable run-to-run, so a "trade" here is not
    # reproducible. Cap conviction below the threshold so no-shift signals degrade to FILTERED
    # DETERMINISTICALLY (never traded), regardless of LLM jitter. Lean + thesis are preserved.
    if config.REQUIRE_SHIFT_TO_TRADE and not shift.get("has_shifted"):
        capped = min(sig.get("conviction", 0.0), config.LONGTERM_CONFIDENCE_THRESHOLD - 0.05)
        if capped < sig.get("conviction", 0.0):
            flags.append("no background shift — conviction capped below threshold (not actionable)")
        sig["conviction"] = round(max(0.0, capped), 2)

    # priced-in: if the stock already moved hard in the signal's direction, cut conviction
    if priced.get("available") and direction in ("LONG", "SHORT"):
        mv = priced["window_move_pct"]
        same_way = (direction == "LONG" and mv > 0) or (direction == "SHORT" and mv < 0)
        if same_way and abs(mv) >= config.PRICED_IN_MOVE_PCT:
            flags.append(f"priced-in: {mv:+.1f}% already moved {direction} over the window")
            sig["conviction"] = round(max(0.0, sig.get("conviction", 0.0) - config.PRICED_IN_PENALTY), 2)

    if earn.get("flag"):
        flags.append(f"earnings within {config.EARNINGS_RISK_DAYS}d ({earn['upcoming']}) — binary-event risk")
        sig["conviction"] = round(max(0.0, sig.get("conviction", 0.0) - config.EARNINGS_PENALTY), 2)

    # COUNTER-TREND GUARD (Phase 7): a signal fighting a strong trailing trend (SHORT into
    # a big rally / LONG into a collapse) has negative skew, and the priced-in guard cannot
    # see it — it only fires when price already moved WITH the signal. Structural prior
    # (not fitted to any window); LT_COUNTER_TREND_PENALTY=0 disables.
    tech = state.get("technical_longterm") or {}
    ret = tech.get("return_window_pct")
    ct_penalty = getattr(config, "COUNTER_TREND_PENALTY", 0.0)
    if direction in ("LONG", "SHORT") and ret is not None and ct_penalty > 0:
        against = ((direction == "SHORT" and ret >= config.COUNTER_TREND_PCT)
                   or (direction == "LONG" and ret <= -config.COUNTER_TREND_PCT))
        if against:
            flags.append(f"counter-trend: {direction} against a {ret:+.1f}% trailing move")
            sig["conviction"] = round(max(0.0, sig.get("conviction", 0.0) - ct_penalty), 2)

    if state.get("article_count", 0) < config.MIN_ARTICLES_FOR_BACKGROUND * 2:
        flags.append(f"thin coverage ({state.get('article_count')} articles)")

    # final gate: conviction threshold. Phase 7: both conviction values persist — the raw
    # LLM self-report (for the calibration study) and the value actually gated on
    # (sig.conviction stays the gating value for back-compat with grading/stats readers).
    sig["conviction_raw"] = round(raw_conviction, 2)
    sig["conviction_gate"] = sig.get("conviction", 0.0)
    sig["gate"] = gate_mode
    passed = sig.get("conviction", 0.0) >= config.LONGTERM_CONFIDENCE_THRESHOLD and direction in ("LONG", "SHORT")
    state["risk_flags"] = flags
    state["risk_checks"] = {"priced_in": priced, "earnings": earn}
    state["signal"] = sig
    state["filtered_signal"] = sig if passed else None

    print(f"  flags: {flags or 'none'}")
    print(f"  → {'PASS' if passed else 'FILTERED'} "
          f"(conviction={sig.get('conviction')} [{gate_mode}] vs threshold {config.LONGTERM_CONFIDENCE_THRESHOLD})")
    _msg(state, "risk_gate", f"{'PASS' if passed else 'FILTERED'}; flags={flags}",
         {"flags": flags, "passed": passed})
    return state
