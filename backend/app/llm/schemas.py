from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class SampleQuote(BaseModel):
    """Verbatim quote attributed to a source and URL."""

    model_config = ConfigDict(from_attributes=True)

    text: str = Field(..., description="Verbatim or near-verbatim quote from discourse")
    source: str = Field(..., description="Source origin, e.g., 'google_news', 'reddit', 'x'")
    url: Optional[str] = Field(default=None, description="Direct URL to original content")


class PerspectiveItem(BaseModel):
    """Individual perspective stance identified within public discourse."""

    model_config = ConfigDict(from_attributes=True)

    title: Optional[str] = Field(default=None, description="Clear, specific, reader-friendly heading summarizing the central viewpoint")
    heading: Optional[str] = Field(default=None, description="Alternative alias for title")
    type: str = Field(default="Neutral Perspective", description="Perspective stance/category label, e.g. 'Industry Proponents', 'Regulatory Skeptics'")
    stance: Optional[str] = Field(default=None, description="Secondary stance category label")
    description: Optional[str] = Field(default=None, description="Meaningful narrative explaining what proponents believe, key reasons, and contrast with other viewpoints")
    summary: str = Field(..., description="Narrative synthesis of this viewpoint")
    estimated_share: float = Field(..., description="Estimated percentage or proportion of discourse share")
    key_arguments: List[str] = Field(default_factory=list, description="Bullet points of key arguments")
    sample_quotes: List[SampleQuote] = Field(
        default_factory=list,
        description="Attributed quotes with source and URL for traceability",
    )


class PerspectiveSynthesisOutput(BaseModel):
    """Structured LLM output for multi-perspective synthesis."""

    model_config = ConfigDict(from_attributes=True)

    core_topic: str = Field(..., description="The main topic analyzed")
    perspectives: List[PerspectiveItem] = Field(
        default_factory=list,
        description="Collection of distinct synthesized viewpoints",
    )
    confidence_note: str = Field(
        ...,
        description="Confidence assessment detailing sample representation and data quality",
    )
