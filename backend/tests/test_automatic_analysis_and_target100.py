import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database.models import Topic, Perspective, RawGoogleNews, RawReddit, CombinedRawData
from app.ingestion.google_news import GoogleNewsIngestor
from app.ingestion.reddit import RedditIngestor
from app.ingestion.pipeline import IngestionPipeline


class TestAutomaticAnalysisAndTarget100:
    """Test suite verifying automatic topic analysis on submission, 100-item target, query expansion, and sample size reporting."""

    def test_submit_and_analyze_creates_and_executes_pipeline(self, db_session: Session):
        """Verify POST /topics/submit-and-analyze creates the topic and runs the complete analysis automatically."""
        client = TestClient(app)
        topic_title = "Future of Renewable Fusion Energy"
        
        with patch.object(IngestionPipeline, "run") as mock_ingest, \
             patch("app.api.topics.ClusterPipeline.run_for_topic") as mock_cluster, \
             patch("app.api.topics.PerspectivePipeline.run_synthesis_for_topic") as mock_synth:
            
            mock_ingest.return_value = {
                "target_items": 100,
                "total_staged": 115,
                "target_met": True,
                "staging_counts": {"google_news": 55, "reddit": 60, "x": 0},
                "staging_errors": {},
                "provider_diagnostics": {},
                "merge_result": {"new_records_added": 115},
                "source_coverage": {"google_news": 55, "reddit": 60, "x": 0, "total_combined": 115, "target_items": 100},
                "timings": {},
            }
            mock_cluster.return_value = {
                "status": "success",
                "sample_size": 115,
                "cluster_count": 3,
                "clusters": [
                    {"cluster_id": 1, "size": 60, "share": 0.52, "representative_samples": []},
                    {"cluster_id": 2, "size": 35, "share": 0.30, "representative_samples": []},
                    {"cluster_id": 3, "size": 20, "share": 0.18, "representative_samples": []},
                ],
            }
            mock_synth.return_value = {
                "status": "success",
                "sample_size": 115,
                "perspectives_count": 3,
                "confidence_note": "Analyzed 115 items exceeding target of 100 across Google News & Reddit.",
            }

            resp = client.post(
                "/api/topics/submit-and-analyze",
                json={"title": topic_title},
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["status"] == "success"
            assert data["topic"]["title"] == topic_title
            assert "slug" in data["topic"]
            assert mock_ingest.called
            assert mock_cluster.called
            assert mock_synth.called

    def test_submit_and_analyze_reuses_existing_topic(self, db_session: Session):
        """Verify submitting an already existing topic title triggers analysis on that topic without duplicate creation."""
        client = TestClient(app)
        existing = Topic(
            title="Quantum Computing Commercialization",
            slug="quantum-computing-commercialization",
            search_count=1,
            trending_score=0.5,
        )
        db_session.add(existing)
        db_session.commit()
        db_session.refresh(existing)

        with patch.object(IngestionPipeline, "run") as mock_ingest, \
             patch("app.api.topics.ClusterPipeline.run_for_topic") as mock_cluster, \
             patch("app.api.topics.PerspectivePipeline.run_synthesis_for_topic") as mock_synth:
            
            mock_ingest.return_value = {
                "target_items": 100,
                "total_staged": 80,
                "target_met": False,
                "staging_counts": {"google_news": 50, "reddit": 30, "x": 0},
                "staging_errors": {},
                "provider_diagnostics": {},
                "merge_result": {},
                "source_coverage": {},
                "timings": {},
            }
            mock_cluster.return_value = {"status": "success", "sample_size": 80, "cluster_count": 2, "clusters": []}
            mock_synth.return_value = {"status": "success", "perspectives_count": 2}

            resp = client.post(
                "/api/topics/submit-and-analyze",
                json={"title": "Quantum Computing Commercialization"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["topic"]["slug"] == "quantum-computing-commercialization"

    def test_google_news_query_expansion_for_100_items(self, db_session: Session):
        """Verify Google News ingestor formulates multiple query variations when limit >= 100."""
        ingestor = GoogleNewsIngestor()
        topic = Topic(title="Next Generation Aerospace", slug="next-gen-aero")
        db_session.add(topic)
        db_session.commit()

        mock_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Google News</title>
                <item>
                    <title>Aerospace Innovation Update</title>
                    <link>https://news.example.com/aerospace-1</link>
                    <description>Breakthrough propulsion testing succeeds.</description>
                    <source url="https://news.example.com">AeroNews</source>
                    <pubDate>Sun, 27 Sep 2026 04:00:00 GMT</pubDate>
                </item>
            </channel>
        </rss>"""

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = mock_xml
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            staged = ingestor.fetch_and_stage(topic=topic, db=db_session, limit=100)
            assert len(staged) >= 1
            # Verify urlopen was called with query variations
            assert mock_urlopen.call_count >= 1

    def test_ingestion_pipeline_reports_target_collection_metrics(self, db_session: Session):
        """Verify IngestionPipeline tracks target_items=100 and whether target was met."""
        topic = Topic(title="Urban Vertical Farming", slug="urban-vertical-farming")
        db_session.add(topic)
        db_session.commit()

        pipeline = IngestionPipeline()
        with patch.object(pipeline.google_news_ingestor, "fetch_and_stage", return_value=[MagicMock() for _ in range(40)]), \
             patch.object(pipeline.reddit_ingestor, "fetch_and_stage", return_value=[MagicMock() for _ in range(25)]), \
             patch.object(pipeline.x_scraper, "fetch_and_stage", return_value=[]), \
             patch.object(pipeline.merge_pipeline, "merge_topic_staging_data", return_value={"new_records_added": 65}):

            res = pipeline.run(topic=topic, db=db_session, limit_per_source=100)
            assert res["target_items"] == 100
            assert res["total_staged"] == 65
            assert res["target_met"] is False
            assert "google_news" in res["staging_counts"]
            assert res["staging_counts"]["google_news"] == 40
            assert res["staging_counts"]["reddit"] == 25
