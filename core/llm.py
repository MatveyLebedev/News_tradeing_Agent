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
    return _StructuredFnChatOpenAI(
        api_key=s.openrouter_api_key,
        base_url=s.openrouter_base_url,
        model=model or s.llm_model,
        temperature=temperature,
        max_tokens=min(max_tokens or 2048, max_tokens_cap),
    )


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
