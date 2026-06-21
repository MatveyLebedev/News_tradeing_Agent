"""
Phase 1 nodes: corpus_collection and background_profiling (map-reduce).

corpus_collection (no LLM): fetch the company corpus, bucket by week, and apply the
NO_DATA gate (too few articles → early exit, like news_agent's significance pre-check).

background_profiling (LLM map-reduce): summarize each weekly bucket with the cheap map
model, then merge the summaries into a NewsBackgroundProfile with the main model. This
profile IS the "news background".
"""
import time
from collections import Counter
from typing import Any, Dict, List

from ..schemas import (
    PeriodBucketSummary, NewsBackgroundProfile, BackgroundShift, FundamentalsTrend,
    ShiftAnalogs, LongTermSignal, ReflectionOutput,
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


def _invoke_structured(llm, schema, prompt, retries=2):
    """Structured-output call with retry. Reasoning models intermittently truncate the
    structured output (reasoning tokens eat the budget) → transient parse errors; a retry
    usually succeeds. Re-raises the last error so callers keep their fallback."""
    last = None
    for attempt in range(retries + 1):
        try:
            return llm.with_structured_output(schema).invoke(prompt)
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    raise last


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
    )
    articles = corpus["articles"]
    buckets = bucket_by_week(articles, as_of, lookback, bucket_days)

    state["news_corpus"] = articles
    state["corpus_provenance"] = corpus["provenance"]
    state["period_buckets"] = buckets
    state["article_count"] = len(articles)

    print(f"  provenance: {corpus['provenance']}")
    print(f"  weekly buckets: {[b['count'] for b in buckets]}")

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
    version = config.AGENT_VERSION

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
        neo4j_manager=neo4j_manager,
    )
    state["background_metrics"] = metrics
    sm = metrics["sentiment"]
    print(f"  sentiment: recent={sm['recent_mean']} baseline={sm['baseline_mean']} "
          f"z={sm['z']} | attention spike={metrics['attention']['spike_ratio']}x | "
          f"turnover={metrics['narrative_turnover']} | flag={metrics['shift_flag']} "
          f"score={metrics['shift_score']}")

    # DETERMINISTIC decision: existence/direction/magnitude come from the metrics, not the
    # LLM — so the shift_library labels are reproducible. The LLM ONLY characterizes
    # (catalysts, inflection refinement, reasoning); it cannot flip has_shifted.
    has_shifted = metrics["shift_flag"]
    direction = metrics["direction"]
    profile = state.get("background_profile", {})

    catalysts, reasoning, inflection = [], "", metrics["inflection_week"]
    if has_shifted and characterize:
        prompt = (
            f"The news background for {state['ticker']} HAS shifted {direction} "
            f"(z={sm['z']}, score={metrics['shift_score']}). Do NOT re-decide whether it shifted — "
            f"that is settled. Your job is to CHARACTERIZE it.\n\n"
            f"Background summary: {profile.get('summary','')}\n"
            f"Per-week sentiment (old→new): {profile.get('sentiment_timeline', [])}\n"
            f"Candidate inflection week: {metrics['inflection_week']}\n"
            f"New stories in recent window: {[s.get('label') for s in metrics['new_stories']][:5]}\n\n"
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
    prompt = (
        f"You judge whether {ticker}'s FUNDAMENTALS confirm, ignore, or contradict the news-background shift.\n\n"
        f"News-background shift: {shift_desc}. {shift.get('reasoning','')}\n\n"
        f"Fundamentals (as-of {fund['as_of']}, point-in-time):\n"
        f"  revenue YoY={fund['revenue_yoy']}, QoQ={fund['revenue_qoq']}\n"
        f"  margin series (newest→oldest): {margins[:4]}\n"
        f"  valuation: {fund['valuation']}\n"
        f"  free cash flow: {fund['fcf']}, debt: {fund['debt']}\n"
        f"  analyst estimate revisions (expectation gap): {fund['expectation_gap']}\n"
        f"  long-term technicals: {tech}\n\n"
        f"Decide the verdict:\n"
        f"  'confirms'    — fundamentals move the SAME way as the news shift\n"
        f"  'contradicts' — fundamentals move OPPOSITE (e.g. negative news shift but accelerating "
        f"revenue/margins → overreaction candidate)\n"
        f"  'ignores'     — fundamentals are flat / unrelated to the shift\n"
        f"Fill revenue_growth_trend, margin_trend, fcf_trend, debt_trend with short phrases grounded "
        f"in the numbers above. Be concrete."
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
        db_manager, state["ticker"], state["as_of"], config.AGENT_VERSION,
        direction=shift.get("direction"), target_z=shift.get("magnitude_z"),
        causal_types=causal, k=6,
    )
    state["shift_analogs"] = analogs
    if analogs["n_analogs"]:
        print(f"  → {analogs['n_analogs']} analogs | median 4w return={analogs['median_fwd_return_4w']} "
              f"| share positive={analogs['share_positive']}")
    else:
        print(f"  → {analogs['fundamentals_matched_note']}")
    _msg(state, "similar_shift_analysis",
         f"{analogs['n_analogs']} analogs, median 4w={analogs.get('median_fwd_return_4w')}", {"analogs": analogs})
    return state


# ---------------------------------------------------------------------------
# 5. synthesis (profile + shift + fundamentals + analogs → LongTermSignal)
# ---------------------------------------------------------------------------
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
    analog_desc = (f"{analogs.get('n_analogs')} past analogs, median 4w return "
                   f"{analogs.get('median_fwd_return_4w')}, share positive {analogs.get('share_positive')}"
                   if analogs.get("n_analogs") else "no graded analogs yet (cold start)")

    prompt = (
        f"Form a multi-week trade thesis for {state['ticker']} (as-of {state['as_of'].date()}).\n\n"
        f"NEWS BACKGROUND: {profile.get('summary','')}\n"
        f"NEWS SHIFT: {shift_desc}. {shift.get('reasoning','')}\n"
        f"FUNDAMENTALS verdict vs shift: {trend.get('verdict','unknown')} — {trend.get('reasoning','')}\n"
        f"  revenue YoY={fund.get('revenue_yoy')}, valuation={ {k: fund.get('valuation',{}).get(k) for k in ('trailingPE','forwardPE')} }, "
        f"expectation_gap={fund.get('expectation_gap')}\n"
        f"  long-term technicals: trend={tech.get('trend')}, window return={tech.get('return_window_pct')}%\n"
        f"ANALOG BASE RATE: {analog_desc}\n\n"
        f"Apply the news×fundamentals matrix:\n"
        f"  shift POSITIVE + fundamentals improving → high-conviction LONG\n"
        f"  shift NEGATIVE + fundamentals deteriorating → high-conviction SHORT\n"
        f"  shift NEGATIVE + fundamentals improving (contradicts) → overreaction: contrarian LONG or NEUTRAL\n"
        f"  shift POSITIVE + fundamentals deteriorating → hype risk: low conviction / avoid\n"
        f"  NO shift → no actionable edge for this agent: default NEUTRAL with LOW conviction "
        f"(strong standing fundamentals alone are NOT a trade trigger here — this agent trades shifts).\n"
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
        print(f"  → max iterations reached; proceeding")
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
    _msg(state, "reflection", f"needs_refinement={state['needs_refinement']} loop_to={state.get('loop_to')}: "
         f"{ref_d.get('advice','')[:140]}", {"reflection": ref_d})
    print(f"  → needs_refinement={state['needs_refinement']} "
          f"(conf={ref_d.get('confidence_in_analysis')})")
    return state


# ---------------------------------------------------------------------------
# 7. risk_gate (priced-in, earnings-event, data sufficiency, conviction threshold)
# ---------------------------------------------------------------------------
def risk_gate_node(state, db_manager, config) -> Dict[str, Any]:
    print("\n=== Risk Gate ===")
    sig = dict(state.get("signal", {}))
    shift = state.get("background_shift", {})
    flags = []

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
        if same_way and abs(mv) >= 8.0:
            flags.append(f"priced-in: {mv:+.1f}% already moved {direction} over the window")
            sig["conviction"] = round(max(0.0, sig.get("conviction", 0.0) - 0.2), 2)

    if earn.get("flag"):
        flags.append(f"earnings within {config.EARNINGS_RISK_DAYS}d ({earn['upcoming']}) — binary-event risk")
        sig["conviction"] = round(max(0.0, sig.get("conviction", 0.0) - 0.1), 2)

    if state.get("article_count", 0) < config.MIN_ARTICLES_FOR_BACKGROUND * 2:
        flags.append(f"thin coverage ({state.get('article_count')} articles)")

    # final gate: conviction threshold
    passed = sig.get("conviction", 0.0) >= config.LONGTERM_CONFIDENCE_THRESHOLD and direction in ("LONG", "SHORT")
    state["risk_flags"] = flags
    state["risk_checks"] = {"priced_in": priced, "earnings": earn}
    state["signal"] = sig
    state["filtered_signal"] = sig if passed else None

    print(f"  flags: {flags or 'none'}")
    print(f"  → {'PASS' if passed else 'FILTERED'} "
          f"(conviction={sig.get('conviction')} vs threshold {config.LONGTERM_CONFIDENCE_THRESHOLD})")
    _msg(state, "risk_gate", f"{'PASS' if passed else 'FILTERED'}; flags={flags}",
         {"flags": flags, "passed": passed})
    return state
