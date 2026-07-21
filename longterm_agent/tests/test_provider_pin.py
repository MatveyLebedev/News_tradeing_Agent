"""Provider pinning — a model id alone does not make a replay reproducible.

OpenRouter routes each request across the backends serving a model; for
deepseek/deepseek-v4-pro that is 17 providers at fp8 / fp4 / undeclared quantisation.
Same prompt on different numerics returns different text, which is the leading explanation
for the measured replay drift. These tests pin the plumbing, not the choice of provider.
"""
from types import SimpleNamespace

from core.llm import provider_routing


MAIN = "deepseek/deepseek-v4-pro"
MAP = "google/gemini-3.1-flash-lite-preview"


def _s(provider="", fallbacks=False):
    return SimpleNamespace(llm_provider=provider, llm_provider_allow_fallbacks=fallbacks,
                           llm_model=MAIN)


def test_pin_applies_to_the_main_model_only():
    """A provider list names backends for ONE model. Applied globally it 404s the rest:
    novita serves deepseek-v4-pro but not gemini, whose only backend is google-ai-studio.
    That mistake degraded a live run on 2026-07-20 before it was caught."""
    s = _s("novita")
    assert provider_routing(s, model=MAIN) == {"only": ["novita"], "allow_fallbacks": False}
    assert provider_routing(s, model=MAP) is None


def test_unset_means_free_routing_ie_todays_behaviour():
    """Default must not change live behaviour (standing rule 4)."""
    assert provider_routing(_s("")) is None
    assert provider_routing(_s("   ")) is None


def test_single_provider_pins_with_fallbacks_off():
    """Fallbacks off is deliberate: for an experiment, failing beats silently switching."""
    assert provider_routing(_s("novita")) == {
        "only": ["novita"], "allow_fallbacks": False}


def test_uses_only_not_order_because_order_falls_through_silently():
    """Measured: asking for DeepSeek via `order` was served by StreamLake without error."""
    r = provider_routing(_s("novita"))
    assert "only" in r and "order" not in r


def test_allowed_set_is_preserved_and_whitespace_tolerated():
    assert provider_routing(_s(" novita , baidu ,, siliconflow "))["only"] == [
        "novita", "baidu", "siliconflow"]


def test_fallbacks_can_be_re_enabled_for_availability():
    assert provider_routing(_s("DeepSeek", True))["allow_fallbacks"] is True


def test_provenance_records_the_pin_so_drift_is_attributable():
    from longterm_agent.config import LongTermAgentConfig
    p = LongTermAgentConfig.provenance()
    assert "llm_provider" in p, "a run must record which backend served it"
