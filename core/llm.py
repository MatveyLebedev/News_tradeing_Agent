"""OpenRouter LLM factory + structured-output retry, shared by all agents.

Moved from LongTermAgentConfig.make_llm and longterm_agent.agent.nodes._invoke_structured
so news_agent / graph_db / longterm_agent build clients the same way.
"""
import time

from core.settings import get_settings


def make_llm(model: str = None, max_tokens: int = None, temperature: float = 0.0,
             max_tokens_cap: int = 8192):
    """Build a ChatOpenAI client on OpenRouter.

    max_tokens must leave room for reasoning models (Gemini/o-series burn
    'reasoning_tokens' before any output) — reduce/synthesis steps need several
    thousand or structured output gets truncated mid-reasoning.

    Structured output is forced to method="function_calling": the native json_schema
    path stashes a non-serializable parsed object in additional_kwargs, which breaks
    the LLM response cache (Backtest Plan B0). Tool calls serialize cleanly, so the
    cache round-trips and backtests are reproducible. Live and backtest use the same
    method, so cached replays match live decisions.
    """
    from langchain_openai import ChatOpenAI  # heavy import, keep lazy

    class _StructuredFnChatOpenAI(ChatOpenAI):
        def with_structured_output(self, schema, **kwargs):
            kwargs.setdefault("method", "function_calling")
            return super().with_structured_output(schema, **kwargs)

    s = get_settings()
    kwargs = {}
    routing = provider_routing(s, model=model or s.llm_model)
    if routing:
        # OpenRouter reads this; without it a request may land on any of the backends
        # serving the model, at differing quantisation — see settings.llm_provider.
        kwargs["extra_body"] = {"provider": routing}
    return _StructuredFnChatOpenAI(
        api_key=s.openrouter_api_key,
        base_url=s.openrouter_base_url,
        model=model or s.llm_model,
        temperature=temperature,
        max_tokens=min(max_tokens or 2048, max_tokens_cap),
        **kwargs,
    )


def provider_routing(s=None, model=None):
    """OpenRouter `provider` block pinning which backend serves the request, or None.

    Emits `only`, NOT `order`: `order` is merely a preference and silently falls through
    to another backend when the first is unavailable — measured 2026-07-20, asking for
    DeepSeek returned StreamLake without complaint. Silent substitution is the exact drift
    this pin exists to remove, so the restriction has to be a hard one.

    Applies to `llm_model` ONLY. A provider list names backends that serve one specific
    model, so applying it globally 404s every other role: pinning novita (which serves
    deepseek-v4-pro) killed every map call to gemini-3.1-flash-lite, whose only backend is
    google-ai-studio. That is not a hypothetical — it degraded a live run on 2026-07-20
    before it was caught. Roles with a single backend need no pin anyway.

    None means route freely. Kept separate from client construction so provenance can
    record what a run was pinned to.
    """
    s = s or get_settings()
    only = [p.strip() for p in (s.llm_provider or "").split(",") if p.strip()]
    if not only:
        return None
    if model is not None and model != s.llm_model:
        return None
    return {"only": only, "allow_fallbacks": bool(s.llm_provider_allow_fallbacks)}


def invoke_structured(llm, schema, prompt, retries: int = 2):
    """Structured-output call with retry. Reasoning models intermittently truncate the
    structured output (reasoning tokens eat the budget) → transient parse errors; a
    retry usually succeeds. Re-raises the last error so callers keep their fallback."""
    last = None
    for attempt in range(retries + 1):
        try:
            return llm.with_structured_output(schema).invoke(prompt)
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    raise last
