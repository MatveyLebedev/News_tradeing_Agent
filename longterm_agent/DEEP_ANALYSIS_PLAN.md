# Deep Analysis Node — Dedicated Plan (Phase 7A)

A bounded, LLM-driven tool-calling step that lets the long-term agent *choose* what to
investigate (instead of every ticker getting the identical fixed analysis), **modeled
directly on the short-term agent's proven `main_analysis` loop** and current LangChain
best practices.

---

## 1. Goal & the one hard constraint

**Goal:** give the agent adaptive depth — it can drill into fundamentals, check whether a
shift is company-specific or sector-wide, traverse causal chains, and weigh historical
analogs, deciding *which* of these to do per company.

**Hard constraint — the determinism boundary:** the deterministic spine
(`shift_score` / `has_shifted`, risk gate, grading) produces the **labels** the
`shift_library` and backtest depend on. `deep_analysis` must **enrich the thesis only**;
it must never change the deterministic shift label or the graded outcome. (Reproducibility
is the thing we'd lose if tool-calling leaked into the labels — see §8.)

Placement in the graph:
```
… shift_detection → fundamentals_analysis → similar_shift_analysis
        → deep_analysis (NEW, bounded ReAct) → synthesis → reflection → risk_gate
```
`deep_analysis` consumes the deterministic outputs and produces an `enriched_analysis`
dict that `synthesis` folds into the thesis. It cannot write back to `background_shift`.

---

## 2. Lock to the short-term agent (reuse what already works)

The short-term agent already runs an LLM tool-calling loop in `main_analysis`
([news_agent/agent/nodes.py:944](news_agent/agent/nodes.py:944)). We reuse that exact
pattern rather than inventing one:

| Short-term agent (proven) | Deep-analysis node (reuse) |
|---|---|
| `tools = [...]; tools.extend(create_graph_tools(neo4j, db, timestamp))` | Same — **reuse `create_graph_tools`** verbatim ([graph_tools.py:19](news_agent/tools/graph_tools.py:19)) |
| `llm_with_tools = llm.bind_tools(tools)` | Same |
| `while iteration < MAX_TOOL_CALL_ITERATIONS:` invoke → if `response.tool_calls` execute by name → append `ToolMessage` → continue | Same loop, copied |
| Force `with_structured_output(...)` on the last iteration | Same — finalize to `DeepAnalysisOutput` |
| Tool error → `ToolMessage(content=json.dumps({"error": ...}))` | Same graceful handling |
| `MAX_TOOL_CALL_ITERATIONS = 7` | Same config knob |

This keeps one tool-calling idiom across both agents, reuses the already-built+time-aware
graph tools (the `timestamp` closure = our `as_of`, so leakage discipline carries over for
free), and means the loop is already battle-tested.

> Modern alternative considered: LangChain `create_agent()` / prebuilt ReAct. Rejected for
> v1 — the manual loop gives tighter control over the determinism boundary and matches the
> short-term agent, so behavior is consistent and debuggable. `create_agent` is the path if
> we later want middleware (HITL, `@wrap_tool_call`).

---

## 3. Tools to expose (all already exist — just `@tool`-wrap / reuse)

The LLM gets a curated, **read-only** toolset (no tool can mutate state or place trades):

**Graph (reuse `create_graph_tools`, as-of aware):**
- `get_causal_chain_with_prices` — why did the shift happen; trace cause→effect
- `get_cross_ticker_correlation` — is this shift idiosyncratic or sector-wide?
- `get_story_context`, `get_story_momentum`, `get_events_for_ticker`
- `get_novelty_score` — is the catalyst new or already priced in?

**Fundamentals / market (wrap existing fns):**
- `get_fundamentals_detail(ticker, quarters)` — drill into specific quarters
- `analyze_sector_and_competitors` — peer comparison ([sector_competitor_tool.py](news_agent/tools/sector_competitor_tool.py))
- `compute_daily_technicals` — on-demand technicals

**News / analogs:**
- `search_company_news(query)` — re-query the corpus with a refined search
- `find_similar_shifts` — pull the closest historical analogs + their outcomes

Each tool: clear name + **specific docstring with Args** (LangChain best practice — the
description is how the model decides when to call it), returns compact JSON.

---

## 4. The bounded ReAct loop (best practices baked in)

```
build read-only tools (incl. create_graph_tools(neo4j, db, as_of))
llm_with_tools = reduce_llm.bind_tools(tools)
seed = open questions from the deterministic pass (shift dir/score, fundamentals verdict, analog gaps)
loop up to MAX_TOOL_CALL_ITERATIONS (=7):
    resp = llm_with_tools.invoke(messages)
    if resp.tool_calls:
        for call: execute by name; on error → ToolMessage(error json); TRUNCATE result to N chars
        append ToolMessages; continue
    else: break
finalize: reduce_llm.with_structured_output(DeepAnalysisOutput).invoke(messages)
force structured output on the final iteration (no infinite loop)
```

**Best practices applied** (sources below):
1. **Bounded iterations + forced finalization** — hard cap (7) and force structured output on
   the last turn so the loop always terminates (LangChain "infinite loop" guidance; mirrors
   short-term agent).
2. **Graceful tool-failure recovery** — every tool error becomes a `ToolMessage` the model
   can react to, never a crash (2026 benchmark: "recover gracefully when tools fail").
3. **Tool-result truncation** — cap each tool result (e.g. 2–3k chars) before appending; the
   graph/corpus tools can return large blobs and the real 2026 bottleneck is "reliable tool
   calling over an expanding context window," not raw calls. Keeps context lean.
4. **Seed the loop with concrete open questions** (not "analyze X") so calls are purposeful
   and few.
5. **Read-only tools only** — no mutation/trading; safety by construction.
6. **Reuse the retry/fallback** helper (`_invoke_structured`) for the finalization call.
7. **Observability** — because these are real `bind_tools` calls, tool invocations + their
   I/O now appear in LangSmith (fixes the earlier "no tool calls in traces" gap).

---

## 5. Output schema (`DeepAnalysisOutput`, Pydantic)

```python
class DeepAnalysisOutput(BaseModel):
    findings: List[str]                 # grounded, evidence-tagged
    is_sector_wide: Optional[bool]      # idiosyncratic vs sector
    competitor_read: str
    catalyst_assessment: str            # new vs priced-in (novelty)
    analog_takeaway: str                # what the closest past shifts imply
    conviction_adjustment: float = 0.0  # -0.2..+0.2 advisory nudge for synthesis
    tools_used: List[str]
```
`conviction_adjustment` is **advisory** — synthesis may weigh it, but it never edits the
deterministic shift label.

---

## 6. Config (add to `LongTermAgentConfig`)

```python
DEEP_ANALYSIS_ENABLED = env("LT_DEEP_ANALYSIS", "true")  # off in backtest, on live
MAX_TOOL_CALL_ITERATIONS = 7        # same as short-term agent
TOOL_RESULT_MAX_CHARS = 3000
DEEP_ANALYSIS_MODEL = LLM_MODEL     # main reasoning model
```

---

## 7. Build steps

1. `longterm_agent/tools/analysis_tools.py` — `@tool`-wrap the fundamentals/sector/news/analog
   functions; import `create_graph_tools` for the graph set.
2. `deep_analysis_node(state, llm, db, neo4j, config)` in `nodes.py` — the loop from §4,
   copied from `main_analysis` and adapted (truncation + seed questions).
3. Wire into `graph.py` between `similar_shift_analysis` and `synthesis`; gate on
   `DEEP_ANALYSIS_ENABLED` (skip node when off).
4. `synthesis` prompt: add the `enriched_analysis` block as additional context.
5. Tests: tool-wrap smoke tests; a loop test with a **fake LLM** that emits one tool call then
   finalizes (deterministic, no network) — verify execution, truncation, and forced
   finalization; verify the node never writes `background_shift`.

---

## 8. Reproducibility & evaluation (non-negotiable)

- **Backtest / `backfill_shift_library` run with `LT_DEEP_ANALYSIS=false`** → labels stay
  deterministic and reproducible (the spine is untouched).
- **Live / forward runs use `LT_DEEP_ANALYSIS=true`** → richer theses.
- **A/B**: the full-signal backtest with the node ON vs OFF measures whether the tools
  actually improve excess return **net of the extra token cost**. Do not declare it valuable
  on vibes — gate it on that A/B (and recall the current sample is still noise).

---

## 9. Why this is the right scope now

- **Incremental & low-risk** — one new node, reuses proven code, determinism preserved.
- **Fixes the tracing gap** you flagged (tool calls become visible in LangSmith).
- **Prerequisite for Deep Agents (Phase 7B)** — the subagents there need exactly these tools
  to call. Build tools first, harness later, gated on a real signal.

---

## Sources (best practices)
- LangChain `langchain-fundamentals` skill: `create_agent`, `@tool` descriptions, structured
  output, iteration limits (`recursion_limit`), tool error handling.
- Short-term agent reference loop: [news_agent/agent/nodes.py:944](news_agent/agent/nodes.py:944), [graph_tools.py:19](news_agent/tools/graph_tools.py:19).
- [LangChain — On Agent Frameworks and Observability](https://www.langchain.com/blog/on-agent-frameworks-and-agent-observability)
- [Building Tool-Calling Agents with LangGraph (2026)](https://sangeethasaravanan.medium.com/building-tool-calling-agents-with-langgraph-a-complete-guide-ebdcdea8f475)
- [LangGraph overview — LangChain docs](https://docs.langchain.com/oss/python/langgraph/overview)
