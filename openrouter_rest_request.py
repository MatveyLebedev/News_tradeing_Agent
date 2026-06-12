"""
OpenRouter REST: POST /v1/chat/completions (OpenAI-compatible).
https://openrouter.ai/docs/api/reference/overview

CURL (replace sk-or-v1-... or export OPENROUTER_API_KEY first):

curl -sS -X POST "https://openrouter.ai/api/v1/chat/completions" \\
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"anthropic/claude-sonnet-4.6","temperature":0,"messages":[{"role":"user","content":"Say hi in five words."}]}'
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.6"


def chat_completion(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.0,
) -> dict:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENROUTER_API_KEY")

    model = model or os.getenv("OPENROUTER_SONNET_MODEL") or os.getenv("LLM_MODEL") or DEFAULT_MODEL

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    data = chat_completion(
        [{"role": "user", "content": "Say hi in five words."}],
    )
    text = data["choices"][0]["message"]["content"]
    print(text)
