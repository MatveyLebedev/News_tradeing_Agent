#!/usr/bin/env python3
"""
News-based price movement forecaster.

Pipeline:
  1. Embed input news → find top-10 similar historical news (pgvector)
  2. Extract tickers from each similar news via LLM
  3. Compute % price change 40 min after each news publication (stock_prices table)
  4. LLM forecast: predicted tickers + direction + confidence based on historical evidence
"""
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import psycopg2
from openai import OpenAI

from config import DB_CONFIG, OPENROUTER_API_KEY, LLM_MODEL, EMBEDDING_MODEL, EMBEDDING_DIMS

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
PRICE_WINDOW_MIN = 40


@dataclass
class EvidenceItem:
    news_id: int
    title: str
    summary: str
    published: Optional[datetime]
    similarity: float
    tickers: list[str]
    price_changes: dict[str, Optional[float]]  # ticker -> pct_change or None


@dataclass
class ForecastResult:
    input_news: str
    similar_news_count: int
    predicted_tickers: list[str]
    price_direction: str        # "up" | "down" | "neutral"
    confidence: float           # 0.0 – 1.0
    reasoning: str
    evidence: list[dict] = field(default_factory=list)


class NewsForecastPredictor:
    def __init__(self):
        if not OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY is not set")
        self._llm = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
        self._conn = psycopg2.connect(**DB_CONFIG)

    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()

    # ------------------------------------------------------------------
    # Step 1: similar news
    # ------------------------------------------------------------------
    def _embed(self, text: str) -> list[float]:
        resp = self._llm.embeddings.create(
            input=[text[:20000]],
            model=EMBEDDING_MODEL,
            dimensions=EMBEDDING_DIMS,
        )
        return resp.data[0].embedding

    def _find_similar_news(self, text: str, top_k: int = 10) -> list[dict]:
        vec = self._embed(text)
        vec_str = "[" + ",".join(map(str, vec)) + "]"
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, title, summary, published,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM news
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (vec_str, vec_str, top_k),
            )
            rows = cur.fetchall()
        return [
            {"id": r[0], "title": r[1], "summary": r[2], "published": r[3], "similarity": float(r[4])}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Step 2: extract tickers via LLM (batched, one call for all news)
    # ------------------------------------------------------------------
    def _extract_tickers_batch(self, news_list: list[dict]) -> list[list[str]]:
        items = "\n".join(
            f"{i+1}. TITLE: {n['title']}\nSUMMARY: {(n['summary'] or '')[:400]}"
            for i, n in enumerate(news_list)
        )
        prompt = (
            "For each news item below, extract all US stock ticker symbols that are "
            "explicitly mentioned OR clearly implied (e.g. company name → ticker). "
            "Return a JSON array of arrays, one inner array per news item. "
            "Use only real NYSE/NASDAQ tickers. Empty array if none found.\n\n"
            f"{items}\n\n"
            "Return ONLY valid JSON, no markdown, no explanation. Example: "
            '[[\"AAPL\",\"MSFT\"],[],[\"TSLA\"]]'
        )
        resp = self._llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        # strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            result = json.loads(raw)
            if isinstance(result, list) and len(result) == len(news_list):
                return [r if isinstance(r, list) else [] for r in result]
        except Exception:
            pass
        return [[] for _ in news_list]

    # ------------------------------------------------------------------
    # Step 3: price change 40 min after publication
    # ------------------------------------------------------------------
    def _price_change(self, ticker: str, published: Optional[datetime]) -> Optional[float]:
        if not published:
            return None
        t_start = published
        t_end = published + timedelta(minutes=PRICE_WINDOW_MIN + 5)
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT timestamp, close FROM stock_prices
                WHERE ticker = %s AND timestamp BETWEEN %s AND %s
                ORDER BY timestamp ASC
                """,
                (ticker, t_start, t_end),
            )
            rows = cur.fetchall()
        if len(rows) < 2:
            return None
        price_at_pub = float(rows[0][1])
        # find closest candle at published + 40 min
        target = published + timedelta(minutes=PRICE_WINDOW_MIN)
        closest = min(rows, key=lambda r: abs((r[0] - target).total_seconds()))
        price_40m = float(closest[1])
        if price_at_pub == 0:
            return None
        return round((price_40m - price_at_pub) / price_at_pub * 100, 4)

    # ------------------------------------------------------------------
    # Step 4: LLM forecast
    # ------------------------------------------------------------------
    def _forecast(self, input_news: str, evidence: list[EvidenceItem]) -> dict:
        evidence_text = ""
        for i, ev in enumerate(evidence, 1):
            if not ev.tickers:
                continue
            changes = ", ".join(
                f"{t}: {v:+.2f}%" if v is not None else f"{t}: no data"
                for t, v in ev.price_changes.items()
            )
            evidence_text += (
                f"{i}. [{ev.similarity:.2f}] {ev.title}\n"
                f"   Tickers: {', '.join(ev.tickers)} | 40-min price changes: {changes}\n"
            )

        if not evidence_text:
            evidence_text = "No historical price data found for similar news."

        prompt = (
            "You are a quantitative analyst. Based on historical similar news and their "
            "observed 40-minute stock price impacts, forecast the likely price movement "
            "for the new news article.\n\n"
            f"NEW NEWS:\n{input_news}\n\n"
            f"HISTORICAL EVIDENCE (similarity score, title, tickers, 40-min % price change):\n"
            f"{evidence_text}\n\n"
            "Return a JSON object with these fields:\n"
            '  "predicted_tickers": [list of ticker strings likely affected],\n'
            '  "price_direction": "up" or "down" or "neutral",\n'
            '  "confidence": float 0.0–1.0,\n'
            '  "reasoning": "brief explanation"\n\n'
            "Return ONLY valid JSON, no markdown."
        )
        resp = self._llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            return json.loads(raw)
        except Exception:
            return {
                "predicted_tickers": [],
                "price_direction": "neutral",
                "confidence": 0.0,
                "reasoning": raw,
            }

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def predict(self, news_text: str) -> ForecastResult:
        print("Step 1: Finding similar news...")
        similar = self._find_similar_news(news_text)
        print(f"  Found {len(similar)} similar articles")

        print("Step 2: Extracting tickers...")
        tickers_per_news = self._extract_tickers_batch(similar)

        print("Step 3: Computing price changes...")
        evidence_items: list[EvidenceItem] = []
        for news, tickers in zip(similar, tickers_per_news):
            changes: dict[str, Optional[float]] = {}
            for ticker in tickers:
                changes[ticker] = self._price_change(ticker, news["published"])
            evidence_items.append(
                EvidenceItem(
                    news_id=news["id"],
                    title=news["title"] or "",
                    summary=news["summary"] or "",
                    published=news["published"],
                    similarity=news["similarity"],
                    tickers=tickers,
                    price_changes=changes,
                )
            )

        print("Step 4: Generating forecast...")
        forecast_raw = self._forecast(news_text, evidence_items)

        evidence_dicts = [
            {
                "title": ev.title,
                "similarity": ev.similarity,
                "tickers": ev.tickers,
                "price_changes": ev.price_changes,
            }
            for ev in evidence_items
        ]

        return ForecastResult(
            input_news=news_text,
            similar_news_count=len(similar),
            predicted_tickers=forecast_raw.get("predicted_tickers", []),
            price_direction=forecast_raw.get("price_direction", "neutral"),
            confidence=float(forecast_raw.get("confidence", 0.0)),
            reasoning=forecast_raw.get("reasoning", ""),
            evidence=evidence_dicts,
        )


def main():
    if len(sys.argv) < 2:
        print("Usage: python predictor.py \"<news text>\"")
        sys.exit(1)

    news_text = sys.argv[1]
    predictor = NewsForecastPredictor()
    try:
        result = predictor.predict(news_text)
    finally:
        predictor.close()

    print("\n" + "=" * 60)
    print("FORECAST RESULT")
    print("=" * 60)
    print(f"Direction  : {result.price_direction.upper()}")
    print(f"Confidence : {result.confidence:.0%}")
    print(f"Tickers    : {', '.join(result.predicted_tickers) or 'none'}")
    print(f"Reasoning  : {result.reasoning}")
    print(f"\nBased on {result.similar_news_count} similar historical news articles.")
    if result.evidence:
        print("\nEvidence:")
        for ev in result.evidence:
            if ev["tickers"]:
                changes = ", ".join(
                    f"{t}: {v:+.2f}%" if v is not None else f"{t}: no data"
                    for t, v in ev["price_changes"].items()
                )
                print(f"  [{ev['similarity']:.2f}] {ev['title'][:70]} | {changes}")


if __name__ == "__main__":
    main()
