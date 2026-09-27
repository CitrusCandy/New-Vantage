from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TopicBase(BaseModel):
    title: str = Field(..., description="Topic title")
    slug: str = Field(..., description="Unique URL slug")
    source_coverage: Optional[Dict[str, Any]] = Field(default=None, description="Coverage statistics per source")


class TopicCreate(BaseModel):
    title: str = Field(..., description="Topic title or search query")
    slug: Optional[str] = Field(default=None, description="Optional custom URL slug")
    source_coverage: Optional[Dict[str, Any]] = Field(default=None, description="Initial source coverage dictionary")


class TopicUpdate(BaseModel):
    title: Optional[str] = Field(default=None, description="Updated topic title")
    source_coverage: Optional[Dict[str, Any]] = Field(default=None, description="Updated source coverage dictionary")


class TopicResponse(TopicBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    search_count: int
    trending_score: float
    last_clustered_at: Optional[datetime] = None
    updated_at: datetime
    created_at: Optional[datetime] = None

    @model_validator(mode="after")
    def populate_created_at(self) -> "TopicResponse":
        if self.created_at is None:
            self.created_at = self.updated_at
        return self


class PerspectiveResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    per_id: int
    id: Optional[int] = None
    topic_id: int
    perspective_type: str
    title: Optional[str] = None
    heading: Optional[str] = None
    description: Optional[str] = None
    stance: Optional[str] = None
    estimated_share: Optional[float] = 0.0
    summary: Optional[str] = None
    key_arguments: List[str] = []
    summary_points: Optional[Any] = None
    sample_quotes: Optional[Any] = None
    confidence_note: Optional[str] = None
    generated_at: datetime
    created_at: Optional[datetime] = None

    @model_validator(mode="after")
    def populate_aliases(self) -> "PerspectiveResponse":
        if self.id is None:
            self.id = self.per_id
        if self.created_at is None:
            self.created_at = self.generated_at

        # Summary points resolution
        sp_dict = self.summary_points if isinstance(self.summary_points, dict) else {}

        # Populate title & heading
        if not self.title:
            self.title = sp_dict.get("title") or sp_dict.get("heading") or self.perspective_type
        if not self.heading:
            self.heading = self.title

        # Populate stance
        if not self.stance:
            self.stance = sp_dict.get("stance") or self.perspective_type

        # Populate summary
        if not self.summary:
            if isinstance(self.summary_points, dict):
                self.summary = self.summary_points.get("summary", "") or self.summary_points.get("description", "")
            elif isinstance(self.summary_points, str):
                self.summary = self.summary_points
            elif isinstance(self.summary_points, list) and self.summary_points:
                self.summary = str(self.summary_points[0])
            else:
                self.summary = ""

        # Populate description
        if not self.description:
            self.description = sp_dict.get("description") or self.summary or f"Synthesized public discourse perspective regarding {self.perspective_type}."

        # Populate key_arguments
        if not self.key_arguments:
            if isinstance(self.summary_points, dict):
                self.key_arguments = self.summary_points.get("key_arguments", [])
            elif isinstance(self.summary_points, list):
                self.key_arguments = [str(x) for x in self.summary_points]
            else:
                self.key_arguments = []

        # Populate sample_quotes
        if isinstance(self.sample_quotes, list):
            norm_quotes = []
            for q in self.sample_quotes:
                if isinstance(q, dict):
                    q_dict = dict(q)
                    text_val = q_dict.get("quote") or q_dict.get("text") or ""
                    q_dict["quote"] = text_val
                    q_dict["text"] = text_val
                    norm_quotes.append(q_dict)
                else:
                    norm_quotes.append({"quote": str(q), "text": str(q), "source": "news"})
            self.sample_quotes = norm_quotes
        return self


class RawDataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    raw_id: int
    topic_id: int
    source: str
    text_content: str
    url: Optional[str] = None
    author_handle: Optional[str] = None
    engagement_metrics: Optional[Dict[str, Any]] = None
    is_flagged_bot: bool
    created_at: datetime


class ClusterRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: int
    topic_id: int
    cluster_algorithm: str
    cluster_count: int
    sample_size: int
    run_at: datetime


class TopicDetailResponse(TopicResponse):
    perspectives: List[PerspectiveResponse] = []
