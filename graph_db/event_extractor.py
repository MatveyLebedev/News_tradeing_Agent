import os
import sys
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Literal, List, Optional

# Единая точка конфигурации моделей — корень репозитория на sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from model_config import GRAPH_LLM_MODEL


EventType = Literal[
    "geopolitical_conflict", "macro_economic", "commodity_price",
    "corporate_announcement", "product_launch", "earnings",
    "merger_acquisition", "trade_policy", "regulatory",
    "market_move", "leadership_change", "legal", "other"
]


class IntraRelation(BaseModel):
    from_idx: int
    to_idx: int
    rel_type: Literal["CAUSES", "RELATED_TO"]
    evidence_text: str
    confidence: float = Field(ge=0, le=1)


class ExtractedEvent(BaseModel):
    name: str = Field(..., description="Short descriptive name (5-10 words)")
    event_type: EventType
    description: str = Field(..., description="1-2 sentence factual summary")
    date: Optional[str] = Field(None, description="ISO 8601 date if mentioned in article")
    tickers: List[str] = Field(default_factory=list, description="Affected US stock tickers")
    sentiment: Literal["positive", "negative", "neutral"]
    source_quote: Optional[str] = Field(None, description="Verbatim key sentence from article")
    confidence: float = Field(ge=0, le=1)
    canonical_entity: str = Field(..., description=(
        "Primary ticker (e.g. 'NVDA') if company event, "
        "else normalized lowercase topic ('iran', 'federal-reserve', 'oil')"
    ))
    canonical_period: str = Field(..., description=(
        "For company events: ISO week 'YYYY-WNN'. "
        "For macro events: month 'YYYY-MM'"
    ))


class ExtractionResult(BaseModel):
    events: List[ExtractedEvent]
    intra_relations: List[IntraRelation] = Field(default_factory=list)


def extract_events(title: str, summary: str) -> ExtractionResult:
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
        model=GRAPH_LLM_MODEL,
        temperature=0,
    ).with_structured_output(ExtractionResult)
    prompt = (
        "Extract all distinct financial/market events from this news article. "
        "For each event identify affected US stock tickers if any. "
        "If multiple events are present, describe causal relationships between them.\n\n"
        f"Title: {title}\nSummary: {summary}"
    )
    return llm.invoke(prompt)
