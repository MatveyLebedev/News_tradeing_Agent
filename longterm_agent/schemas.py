"""
Pydantic structured-output schemas for the long-term agent (same discipline as
news_agent/schemas.py). Phase 1 uses PeriodBucketSummary and NewsBackgroundProfile;
the rest are defined here as the single source of truth for later phases.
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


# --- background_profiling: map step ---
class PeriodBucketSummary(BaseModel):
    """One weekly bucket, summarized by the cheap map model."""
    themes: List[str] = Field(default_factory=list, description="Dominant themes this week")
    key_events: List[str] = Field(default_factory=list, description="Concrete events (grounded in articles)")
    sentiment: float = Field(0.0, ge=-1.0, le=1.0, description="Net sentiment for the week, -1..1")
    attention: Literal["low", "normal", "elevated", "high"] = "normal"
    notable_article_ids: List[int] = Field(default_factory=list)


# --- background_profiling: reduce step (THE news background) ---
class NarrativeThread(BaseModel):
    label: str
    direction: Literal["positive", "negative", "mixed", "neutral"]
    weeks_active: int = 0
    evidence: List[str] = Field(default_factory=list)


class NewsBackgroundProfile(BaseModel):
    summary: str = Field(description="2-4 sentence plain-English background of the company's news")
    dominant_narratives: List[NarrativeThread] = Field(default_factory=list)
    sentiment_timeline: List[float] = Field(default_factory=list, description="Per-bucket sentiment, oldest→newest")
    attention_timeline: List[str] = Field(default_factory=list)
    recurring_risks: List[str] = Field(default_factory=list)
    recurring_catalysts: List[str] = Field(default_factory=list)


# --- shift_detection ---
class BackgroundShift(BaseModel):
    has_shifted: bool
    direction: Optional[Literal["positive", "negative"]] = None
    magnitude_z: Optional[float] = None
    shift_score: float = Field(0.0, ge=0.0, le=1.0, description="Continuous shift strength 0-1 (deterministic)")
    inflection_date: Optional[str] = None
    candidate_catalysts: List[str] = Field(default_factory=list)
    reasoning: str = ""


# --- fundamentals_analysis ---
class FundamentalsTrend(BaseModel):
    revenue_growth_trend: Optional[str] = None
    margin_trend: Optional[str] = None
    fcf_trend: Optional[str] = None
    debt_trend: Optional[str] = None
    verdict: Literal["confirms", "ignores", "contradicts", "unknown"] = "unknown"
    reasoning: str = ""


class ValuationContext(BaseModel):
    pe: Optional[float] = None
    ps: Optional[float] = None
    ev_ebitda: Optional[float] = None
    vs_sector: Optional[str] = None


# --- similar_shift_analysis ---
class ShiftAnalogs(BaseModel):
    n_analogs: int = 0
    median_fwd_return_4w: Optional[float] = None
    share_positive: Optional[float] = None
    fundamentals_matched_note: str = ""
    nearest_cases: List[str] = Field(default_factory=list)


# --- synthesis ---
class LongTermSignal(BaseModel):
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    conviction: float = Field(ge=0.0, le=1.0)
    horizon: str = Field(description="e.g. '4-12 weeks'")
    thesis: str
    supporting_factors: List[str] = Field(default_factory=list)
    key_risks: List[str] = Field(default_factory=list)
    invalidation_conditions: List[str] = Field(default_factory=list)


# --- deep_analysis (Phase 7A): advisory enrichment, never edits the deterministic shift ---
class DeepAnalysisOutput(BaseModel):
    findings: List[str] = Field(default_factory=list, description="Grounded, evidence-tagged findings")
    is_sector_wide: Optional[bool] = Field(None, description="True=sector-wide, False=idiosyncratic")
    competitor_read: str = ""
    catalyst_assessment: str = Field("", description="Is the catalyst new or already priced in?")
    analog_takeaway: str = Field("", description="What the closest past shifts imply")
    summary: str = Field("", description="Short written summary of the key insights found")
    conviction_adjustment: float = Field(
        0.0, ge=-0.2, le=0.2,
        description="ADVISORY nudge for synthesis; never changes the deterministic shift label")
    tools_used: List[str] = Field(default_factory=list)


class ReflectionOutput(BaseModel):
    needs_refinement: bool = False
    loop_to: Optional[Literal["shift_detection", "fundamentals_analysis"]] = None
    advice: str = ""
    confidence_in_analysis: float = Field(0.7, ge=0.0, le=1.0)


class LongTermAgentOutput(BaseModel):
    ticker: str
    as_of: str
    verdict: str
    signal: Optional[LongTermSignal] = None
