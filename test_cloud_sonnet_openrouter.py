"""Smoke test: Claude Sonnet via OpenRouter (same pattern as test_gemini_simple.py)."""
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

# Override with OPENROUTER_SONNET_MODEL or LLM_MODEL if needed
DEFAULT_SONNET = "anthropic/claude-sonnet-4.6"


def main() -> None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("Set OPENROUTER_API_KEY (https://openrouter.ai/keys)")
        return

    model = (
        os.getenv("OPENROUTER_SONNET_MODEL")
        or os.getenv("LLM_MODEL")
        or DEFAULT_SONNET
    )
    temperature = float(os.getenv("LLM_TEMPERATURE", "0"))

    print(f"Model: {model} @ https://openrouter.ai/api/v1")

    llm = ChatOpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        model=model,
        temperature=temperature,
    )
    prompt = "Reply in one short sentence: confirm you are Claude Sonnet on OpenRouter."
    print(f"Prompt: {prompt}\n")
    out = llm.invoke(prompt)
    print(out.content)


if __name__ == "__main__":
    main()
