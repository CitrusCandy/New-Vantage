"""Regression tests verifying zero-source ingestion diagnosis and prevention of off-query perspectives."""

from datetime import datetime
import json
import unittest
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.models import (
    Base,
    CombinedRawData,
    Perspective,
    RawGoogleNews,
    RawReddit,
    RawX,
    Topic,
)
from app.ingestion.google_news import GoogleNewsIngestor
from app.ingestion.merge_pipeline import MergePipeline
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.reddit import RedditIngestor
from app.ingestion.x import XScraper
from app.llm.perspective import (
    MockPerspectiveSynthesizer,
    validate_and_sanitize_perspectives,
)
from app.llm.pipeline import PerspectivePipeline
from app.llm.schemas import PerspectiveItem, PerspectiveSynthesisOutput, SampleQuote
from app.processing.cluster_pipeline import ClusterPipeline
from app.processing.processor import DiscourseProcessor
from app.processing.relevance import (
    calculate_query_relevance,
    extract_content_tokens,
    filter_relevant_items,
)


class TestZeroSourceIngestionDiagnosis(unittest.TestCase):
    """Verify provider execution, error isolation, safe diagnostics, and persistence."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        self.topic = Topic(
            title="Electric Vehicles Market Growth",
            slug="electric-vehicles-market-growth",
            search_count=0,
            trending_score=0.0,
            source_coverage={"google_news": 0, "reddit": 0, "x": 0, "total_combined": 0},
            updated_at=datetime.utcnow(),
        )
        self.db.add(self.topic)
        self.db.commit()
        self.db.refresh(self.topic)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def test_pipeline_reports_provider_diagnostics_when_credentials_absent(self):
        """Pipeline must provide clear, safe diagnostic statuses when optional providers have no credentials."""
        mock_gn = MagicMock()
        mock_gn.fetch_and_stage.return_value = [
            RawGoogleNews(
                slug_id=self.topic.id,
                title="EV Sales Surge in 2026",
                link="https://news.google.com/ev-sales",
                source_name="Reuters",
                published_at=datetime.utcnow(),
            )
        ]
        self.db.add(mock_gn.fetch_and_stage.return_value[0])
        self.db.commit()

        mock_reddit = MagicMock()
        mock_reddit.fetch_and_stage.return_value = []

        mock_x = MagicMock()
        mock_x.fetch_and_stage.return_value = []

        pipeline = IngestionPipeline(
            google_news_ingestor=mock_gn,
            reddit_ingestor=mock_reddit,
            x_scraper=mock_x,
            merge_pipeline=MergePipeline(),
        )

        with patch("app.database.database.SessionLocal", return_value=self.db):
            result = pipeline.run(topic=self.topic, db=self.db, limit_per_source=10)

        self.assertIn("provider_diagnostics", result)
        diagnostics = result["provider_diagnostics"]

        # Google News succeeded
        self.assertEqual(diagnostics["google_news"]["status"], "success")
        self.assertEqual(diagnostics["google_news"]["records_staged"], 1)

        # X indicates unconfigured or empty without leaking secrets
        self.assertIn(diagnostics["x"]["status"], ["unconfigured", "no_results", "public_fallback_empty"])
        self.assertNotIn("Bearer", diagnostics["x"]["message"])
        self.assertNotIn("secret", diagnostics["x"]["message"].lower())

        # Merge produced combined items
        combined_count = self.db.query(CombinedRawData).filter(CombinedRawData.slug_id == self.topic.id).count()
        self.assertEqual(combined_count, 1)

    def test_successful_provider_persists_retrievable_sources(self):
        """A valid provider payload must persist through staging into combined_raw_data."""
        gn_record = RawGoogleNews(
            slug_id=self.topic.id,
            title="Global Electric Vehicle Adoption Reaches New Milestones",
            link="https://news.google.com/ev-milestones",
            snippet="Automakers report record EV deliveries across European markets.",
            source_name="Bloomberg",
            published_at=datetime.utcnow(),
        )
        self.db.add(gn_record)
        self.db.commit()

        merge_pipeline = MergePipeline()
        merge_res = merge_pipeline.merge_topic_staging_data(topic=self.topic, db=self.db)

        self.assertEqual(merge_res["status"], "success")
        self.assertEqual(merge_res["new_records_added"], 1)

        # Query back from combined_raw_data
        combined = self.db.query(CombinedRawData).filter(CombinedRawData.slug_id == self.topic.id).all()
        self.assertEqual(len(combined), 1)
        self.assertIn("Electric Vehicle", combined[0].text_content)
        self.assertEqual(combined[0].source, "google_news")


class TestOffQueryPerspectivePrevention(unittest.TestCase):
    """Verify that off-topic discourse is excluded and stale perspectives are never displayed."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        self.topic = Topic(
            title="EU AI Act Enforcement",
            slug="eu-ai-act-enforcement",
            search_count=1,
            trending_score=0.7,
            source_coverage={"google_news": 0, "reddit": 0, "x": 0, "total_combined": 0},
            updated_at=datetime.utcnow(),
        )
        self.db.add(self.topic)
        self.db.commit()
        self.db.refresh(self.topic)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def test_relevance_matches_paraphrases_and_acronyms(self):
        """Query relevance must match entity acronyms and morphological stems."""
        query = "EU AI Act Enforcement"

        # 1. Paraphrase with acronyms and stems
        text_rel = "European regulators begin enforcing compliance audits on machine learning foundation models under the AI Act."
        score_rel, is_rel = calculate_query_relevance(query=query, text=text_rel)
        self.assertTrue(is_rel)
        self.assertGreater(score_rel, 0.3)

        # 2. Irrelevant text that shares only generic words
        text_irrel = "Fresh vegetable soup recipe with carrots and celery for family dinner."
        score_irrel, is_irrel = calculate_query_relevance(query=query, text=text_irrel)
        self.assertFalse(is_irrel)
        self.assertEqual(score_irrel, 0.0)

    def test_stale_perspectives_purged_on_insufficient_evidence(self):
        """When a topic is re-analyzed and has insufficient evidence, prior stale perspectives must be purged."""
        # Insert a stale perspective from a previous run
        stale_p = Perspective(
            topic_id=self.topic.id,
            perspective_type="Old Stale Stance From Yesterday",
            estimated_share=1.0,
            summary_points={"summary": "Old stale summary", "key_arguments": []},
            sample_quotes=[],
            confidence_note="Stale",
            generated_at=datetime.utcnow(),
        )
        self.db.add(stale_p)
        self.db.commit()

        self.assertEqual(self.db.query(Perspective).filter(Perspective.topic_id == self.topic.id).count(), 1)

        # Run synthesis with 0 sources / insufficient volume
        pipeline = PerspectivePipeline(synthesizer=MockPerspectiveSynthesizer())
        result = pipeline.run_synthesis_for_topic(topic=self.topic, db=self.db, min_volume_threshold=5)

        self.assertEqual(result["perspectives_count"], 0)
        self.assertEqual(result["status"], "insufficient_volume")

        # Stale perspective must be deleted from DB
        remaining_p = self.db.query(Perspective).filter(Perspective.topic_id == self.topic.id).all()
        self.assertEqual(len(remaining_p), 0)

    def test_synthesizer_does_not_invent_perspectives_on_empty_input(self):
        """Synthesizer must return empty perspectives with a clear confidence note when evidence is empty."""
        synthesizer = MockPerspectiveSynthesizer()
        output = synthesizer.synthesize(
            topic_title="Nonexistent Niche Topic",
            cluster_payloads=[],
            total_sample_size=0,
        )

        self.assertEqual(len(output.perspectives), 0)
        self.assertIn("Limited evidence", output.confidence_note)
        self.assertIn("0 sources", output.confidence_note)

    def test_validate_and_sanitize_drops_off_query_perspectives(self):
        """validate_and_sanitize_perspectives must drop ungrounded or off-query perspectives."""
        raw_output = PerspectiveSynthesisOutput(
            core_topic="Bitcoin Halving",
            perspectives=[
                PerspectiveItem(
                    type="Miner Economics & BTC Supply Squeeze",
                    estimated_share=0.5,
                    summary="Halving reduces bitcoin block rewards, increasing marginal production cost for miners.",
                    key_arguments=["Reduces daily issuance", "Pushes inefficient rigs offline"],
                    sample_quotes=[SampleQuote(text="BTC halving cut miner rewards", source="news", url="https://news.google.com/btc")],
                ),
                PerspectiveItem(
                    type="Gourmet Pasta Cooking Tips",
                    estimated_share=0.5,
                    summary="How to boil handmade fettuccine al dente with garlic sauce.",
                    key_arguments=["Boil with sea salt"],
                    sample_quotes=[],
                ),
            ],
            confidence_note="Test note",
        )

        validated = validate_and_sanitize_perspectives(raw_output=raw_output, query="Bitcoin Halving")

        self.assertEqual(len(validated.perspectives), 1)
        self.assertEqual(validated.perspectives[0].type, "Miner Economics & BTC Supply Squeeze")
        self.assertEqual(validated.perspectives[0].estimated_share, 1.0)
