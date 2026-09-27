import os
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database.models import Topic, Perspective, RawGoogleNews, CombinedRawData
from app.database.schemas import PerspectiveResponse, TopicDetailResponse, TopicResponse
from app.ingestion.pipeline import IngestionPipeline
from app.llm.perspective import get_perspective_synthesizer, MockPerspectiveSynthesizer, OpenAIPerspectiveSynthesizer


class TestCorsAndFetchRegression:
    """Regression test suite for frontend fetch connectivity, CORS, schema normalization, and analysis."""

    def test_cors_headers_and_cross_origin_resource_policy(self):
        """Verify CORS allows frontend origin and Cross-Origin-Resource-Policy is cross-origin."""
        client = TestClient(app)

        # 1. Test standard GET request with Origin header
        resp = client.get("/api/topics", headers={"Origin": "http://localhost:3000"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
        assert resp.headers.get("cross-origin-resource-policy") == "cross-origin"

        # 2. Test preflight OPTIONS request
        resp_opt = client.options(
            "/api/topics/submit-and-analyze",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type,Authorization",
            },
        )
        assert resp_opt.status_code == 200
        assert resp_opt.headers.get("access-control-allow-origin") == "http://localhost:3000"
        assert "POST" in resp_opt.headers.get("access-control-allow-methods", "")

    def test_perspective_response_schema_compatibility(self, db_session: Session):
        """Verify PerspectiveResponse serializes both backend column names and frontend expected properties."""
        topic = Topic(title="Next Generation Batteries", slug="next-gen-batteries")
        db_session.add(topic)
        db_session.commit()
        db_session.refresh(topic)

        perspective = Perspective(
            topic_id=topic.id,
            perspective_type="Solid-State Energy Proponents",
            estimated_share=0.45,
            summary_points={
                "summary": "Solid-state batteries promise higher energy density and improved thermal safety.",
                "key_arguments": [
                    "Eliminates flammable liquid electrolytes.",
                    "Enables 500+ Wh/kg cell level energy density.",
                ],
            },
            sample_quotes=[
                {
                    "text": "Automotive OEMs are accelerating pilot line installations for solid state cells.",
                    "source": "google_news",
                    "url": "https://example.com/battery-news-1",
                }
            ],
            confidence_note="Synthesized from verified discourse evidence.",
        )
        db_session.add(perspective)
        db_session.commit()
        db_session.refresh(perspective)

        # Validate with PerspectiveResponse schema
        resp_schema = PerspectiveResponse.model_validate(perspective)
        data = resp_schema.model_dump()

        # Both per_id and id should be present and equal
        assert data["per_id"] == perspective.per_id
        assert data["id"] == perspective.per_id

        # Both summary and summary_points should be populated
        assert data["summary"] == "Solid-state batteries promise higher energy density and improved thermal safety."
        assert len(data["key_arguments"]) == 2

        # sample_quotes should provide both quote and text
        assert len(data["sample_quotes"]) == 1
        assert data["sample_quotes"][0]["quote"] == "Automotive OEMs are accelerating pilot line installations for solid state cells."
        assert data["sample_quotes"][0]["text"] == "Automotive OEMs are accelerating pilot line installations for solid state cells."
        assert data["sample_quotes"][0]["source"] == "google_news"
        assert data["sample_quotes"][0]["url"] == "https://example.com/battery-news-1"

        # created_at and generated_at should be present
        assert data["created_at"] is not None
        assert data["generated_at"] is not None

    def test_get_topic_by_slug_returns_compatible_perspectives_shape(self, db_session: Session):
        """Verify GET /api/topics/{slug} returns complete structure expected by frontend."""
        client = TestClient(app)
        topic = Topic(
            title="Carbon Capture Infrastructure",
            slug="carbon-capture-infra",
            search_count=1,
            trending_score=0.75,
            source_coverage={"google_news": 50, "reddit": 50, "x": 0, "total_combined": 100},
        )
        db_session.add(topic)
        db_session.commit()
        db_session.refresh(topic)

        p1 = Perspective(
            topic_id=topic.id,
            perspective_type="Direct Air Capture Scaling",
            estimated_share=0.60,
            summary_points={
                "summary": "Scaling DAC facilities requires substantial clean power inputs.",
                "key_arguments": ["Point source vs atmospheric capture economics differ significantly."],
            },
            sample_quotes=[
                {"quote": "Megaton facility commissioning begins in 2027.", "source": "reddit", "url": "https://reddit.com/r/energy/1"}
            ],
        )
        db_session.add(p1)
        db_session.commit()

        resp = client.get("/api/topics/carbon-capture-infra")
        assert resp.status_code == 200, resp.text
        json_data = resp.json()

        assert json_data["slug"] == "carbon-capture-infra"
        assert len(json_data["perspectives"]) == 1
        per = json_data["perspectives"][0]
        assert per["id"] == p1.per_id
        assert per["summary"] == "Scaling DAC facilities requires substantial clean power inputs."
        assert per["sample_quotes"][0]["quote"] == "Megaton facility commissioning begins in 2027."

    def test_missing_openai_key_falls_back_cleanly(self, monkeypatch):
        """Verify missing or invalid OpenAI keys gracefully select MockPerspectiveSynthesizer without error."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        synth = get_perspective_synthesizer()
        assert isinstance(synth, MockPerspectiveSynthesizer)

        monkeypatch.setenv("OPENAI_API_KEY", "your_openai_api_key_here")
        synth_placeholder = get_perspective_synthesizer()
        assert isinstance(synth_placeholder, MockPerspectiveSynthesizer)

    def test_end_to_end_submit_and_retrieve_perspectives_flow(self, db_session: Session):
        """Test full topic-to-perspectives flow with grounded mock synthesis."""
        client = TestClient(app)
        topic_title = "Global SMR Nuclear Deployment"

        resp = client.post(
            "/api/topics/submit-and-analyze",
            json={"title": topic_title},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "success"
        slug = data["topic"]["slug"]

        # Fetch detail by slug
        resp_detail = client.get(f"/api/topics/{slug}")
        assert resp_detail.status_code == 200
        topic_data = resp_detail.json()
        assert topic_data["title"] == topic_title
        assert "perspectives" in topic_data
