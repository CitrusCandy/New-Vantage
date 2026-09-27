"""Tests for query relevance, perspective count (5-10 target), and expanded source coverage in VantageNews."""

from datetime import datetime
import json
import unittest
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, CombinedRawData, RawGoogleNews, RawReddit, RawX, Topic
from app.ingestion.google_news import GoogleNewsIngestor
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.reddit import RedditIngestor
from app.ingestion.x import XScraper
from app.llm.perspective import (
    MockPerspectiveSynthesizer,
    OpenAIPerspectiveSynthesizer,
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


class TestQueryRelevance(unittest.TestCase):
    """Test query relevance calculations, token extraction, and source filtering."""

    def test_extract_content_tokens(self):
        tokens = extract_content_tokens("The Commercialization of Autonomous AI Agents in 2026")
        self.assertIn("commercialization", tokens)
        self.assertIn("autonomous", tokens)
        self.assertIn("ai", tokens)
        self.assertIn("agents", tokens)
        self.assertNotIn("the", tokens)
        self.assertNotIn("of", tokens)
        self.assertNotIn("in", tokens)

    def test_calculate_query_relevance_exact_and_partial(self):
        query = "Solid-State Battery EV"
        # Highly relevant text
        score_high, is_rel_high = calculate_query_relevance(
            query=query,
            text="Breakthroughs in solid-state battery technology for electric vehicles and EV fast charging.",
        )
        self.assertTrue(is_rel_high)
        self.assertGreaterEqual(score_high, 0.5)

        # Off-topic text
        score_low, is_rel_low = calculate_query_relevance(
            query=query,
            text="Local football league standings and weekend match highlights.",
        )
        self.assertFalse(is_rel_low)
        self.assertLess(score_low, 0.35)

    def test_filter_relevant_items(self):
        query = "EU AI Act"
        items = [
            {"text_content": "European Parliament votes on the EU AI Act enforcement framework."},
            {"text_content": "Fresh strawberry pancake recipe with whipped cream."},
            {"text_content": "Compliance guidelines for high-risk artificial intelligence models under EU AI Act."},
        ]

        relevant, filtered_count = filter_relevant_items(
            query=query,
            items=items,
            text_extractor=lambda x: x["text_content"],
        )
        self.assertEqual(len(relevant), 2)
        self.assertEqual(filtered_count, 1)


class TestDiscourseProcessorRelevanceAndGating(unittest.TestCase):
    """Test DiscourseProcessor filters off-topic items and handles adaptive volume gating."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        self.topic = Topic(
            title="Quantum Computing Commercialization",
            slug="quantum-computing-commercialization",
            search_count=1,
            trending_score=0.5,
            source_coverage={"google_news": 0, "reddit": 0, "x": 0, "total_combined": 0},
            updated_at=datetime.utcnow(),
        )
        self.db.add(self.topic)
        self.db.commit()
        self.db.refresh(self.topic)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)

    def test_processor_filters_irrelevant_discourse(self):
        # 3 relevant items + 2 completely off-topic items
        items = [
            CombinedRawData(
                slug_id=self.topic.id,
                source="google_news",
                text_content="Tech giants announce $1B investment in quantum computing hardware and commercialization.",
                url="https://example.com/1",
                is_flagged_bot=False,
                created_at=datetime.utcnow(),
            ),
            CombinedRawData(
                slug_id=self.topic.id,
                source="reddit",
                text_content="Superconducting qubits achieve quantum advantage in optimization benchmarks.",
                url="https://example.com/2",
                is_flagged_bot=False,
                created_at=datetime.utcnow(),
            ),
            CombinedRawData(
                slug_id=self.topic.id,
                source="google_news",
                text_content="Quantum computing market capitalization projected to quadruple by 2030.",
                url="https://example.com/3",
                is_flagged_bot=False,
                created_at=datetime.utcnow(),
            ),
            CombinedRawData(
                slug_id=self.topic.id,
                source="reddit",
                text_content="Best recipes for sourdough bread during winter mornings.",
                url="https://example.com/4",
                is_flagged_bot=False,
                created_at=datetime.utcnow(),
            ),
            CombinedRawData(
                slug_id=self.topic.id,
                source="reddit",
                text_content="Movie review of the latest Hollywood summer blockbuster.",
                url="https://example.com/5",
                is_flagged_bot=False,
                created_at=datetime.utcnow(),
            ),
        ]
        self.db.add_all(items)
        self.db.commit()

        processor = DiscourseProcessor(min_volume_threshold=3, min_relevance_score=0.2)
        res = processor.process_topic_items(topic=self.topic, db=self.db)

        self.assertEqual(res.statistics.total_raw_items, 5)
        self.assertEqual(res.statistics.irrelevant_items_removed, 2)
        self.assertEqual(len(res.usable_items), 3)
        self.assertTrue(res.statistics.volume_gate_passed)


class TestPerspectiveSynthesisValidationAndTargets(unittest.TestCase):
    """Test 5-10 perspective targets, validation, duplicate rejection, and limited evidence handling."""

    def test_post_generation_validation_removes_duplicates_and_off_query(self):
        query = "Autonomous AI Agents"
        raw_output = PerspectiveSynthesisOutput(
            core_topic="Autonomous AI Agents",
            perspectives=[
                PerspectiveItem(
                    type="Economic Productivity Proponents",
                    estimated_share=0.4,
                    summary="AI agents accelerate enterprise productivity and automate knowledge work.",
                    key_arguments=["Reduces friction", "Accelerates delivery"],
                    sample_quotes=[SampleQuote(text="Quote 1", source="google_news", url="javascript:alert(1)")],
                ),
                PerspectiveItem(
                    type="economic productivity proponents",  # Duplicate type
                    estimated_share=0.3,
                    summary="Paraphrase of the exact same economic viewpoint.",
                    key_arguments=["Duplicate argument"],
                    sample_quotes=[],
                ),
                PerspectiveItem(
                    type="Gardening in Suburbs",  # Off-topic
                    estimated_share=0.3,
                    summary="Tips on growing organic tomatoes in raised backyard beds.",
                    key_arguments=["Watering schedules"],
                    sample_quotes=[],
                ),
            ],
            confidence_note="Initial test generation note",
        )

        validated = validate_and_sanitize_perspectives(raw_output=raw_output, query=query)

        # Duplicate and off-topic perspectives should be dropped
        self.assertEqual(len(validated.perspectives), 1)
        self.assertEqual(validated.perspectives[0].type, "Economic Productivity Proponents")
        # URL sanitized
        self.assertIsNone(validated.perspectives[0].sample_quotes[0].url)
        # Share normalized
        self.assertAlmostEqual(validated.perspectives[0].estimated_share, 1.0)

    def test_mock_synthesizer_generates_5_to_10_grounded_perspectives(self):
        query = "Commercial Fusion Energy Deployments"
        cluster_payloads = [
            {
                "cluster_id": i + 1,
                "size": 6,
                "share": 0.2,
                "representative_samples": [
                    {
                        "source": "google_news" if i % 2 == 0 else "reddit",
                        "text_content": f"Perspective evidence analysis chunk number {i+1} for fusion energy deployment.",
                        "url": f"https://news.example.com/fusion-{i+1}",
                    },
                ],
            }
            for i in range(5)
        ]

        synthesizer = MockPerspectiveSynthesizer()
        output = synthesizer.synthesize(
            topic_title=query,
            cluster_payloads=cluster_payloads,
            total_sample_size=30,
        )

        self.assertEqual(len(output.perspectives), 5)
        self.assertEqual(output.core_topic, query)

        # Check evidence grounding, specific headings, descriptions, and stance
        for p in output.perspectives:
            self.assertTrue(len(p.title) >= 10)
            self.assertTrue(len(p.description) >= 25)
            self.assertTrue(p.stance is not None and len(p.stance) >= 3)
            self.assertTrue(len(p.key_arguments) >= 1)
            self.assertTrue(len(p.sample_quotes) >= 1)

    def test_limited_evidence_returns_clear_outcome(self):
        query = "Ultra Rare Event 12345"
        synthesizer = MockPerspectiveSynthesizer()
        output = synthesizer.synthesize(
            topic_title=query,
            cluster_payloads=[],
            total_sample_size=0,
        )

        self.assertEqual(len(output.perspectives), 0)
        self.assertIn("Limited evidence", output.confidence_note)


class TestSourceCoverageAndPagination(unittest.TestCase):
    """Test Reddit pagination, Google News query expansion, and X ingestion."""

    def test_reddit_pagination_fetches_multiple_pages_and_deduplicates(self):
        ingestor = RedditIngestor()

        # Mock 2 pages of search results with an after continuation token
        page1_json = json.dumps({
            "data": {
                "after": "t3_token_page2",
                "children": [
                    {"data": {"id": "post_1", "title": "AI agents post 1", "selftext": "body 1", "score": 10, "created_utc": 1700000000}},
                    {"data": {"id": "post_2", "title": "AI agents post 2", "selftext": "body 2", "score": 20, "created_utc": 1700000000}},
                ],
            }
        })
        page2_json = json.dumps({
            "data": {
                "after": None,
                "children": [
                    {"data": {"id": "post_2", "title": "Duplicate post 2", "selftext": "body 2", "score": 20, "created_utc": 1700000000}},
                    {"data": {"id": "post_3", "title": "AI agents post 3", "selftext": "body 3", "score": 30, "created_utc": 1700000000}},
                ],
            }
        })

        seen_ids = set()
        mock_db = MagicMock()

        staged_p1, after_p1 = ingestor.parse_json_and_persist_page(
            raw_json=page1_json,
            topic_id=1,
            db=mock_db,
            limit=10,
            seen_post_ids=seen_ids,
        )
        self.assertEqual(len(staged_p1), 2)
        self.assertEqual(after_p1, "t3_token_page2")

        staged_p2, after_p2 = ingestor.parse_json_and_persist_page(
            raw_json=page2_json,
            topic_id=1,
            db=mock_db,
            limit=10,
            seen_post_ids=seen_ids,
        )
        # post_2 was a duplicate, so only post_3 was added
        self.assertEqual(len(staged_p2), 1)
        self.assertIsNone(after_p2)
        self.assertEqual(len(seen_ids), 3)

    def test_google_news_rss_deduplication_across_queries(self):
        ingestor = GoogleNewsIngestor()

        # Mock RSS XML feed
        xml_feed = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <item>
                    <title>AI Agent Deployment Surges Across Enterprises</title>
                    <link>https://news.google.com/articles/ai-agent-1</link>
                    <description>Enterprise tech news snippet.</description>
                </item>
                <item>
                    <title>AI Agent Deployment Surges Across Enterprises</title>
                    <link>https://news.google.com/articles/ai-agent-1</link>
                    <description>Duplicate snippet.</description>
                </item>
                <item>
                    <title>New Benchmark Ranks Autonomous LLM Tool Use</title>
                    <link>https://news.google.com/articles/ai-agent-2</link>
                    <description>Benchmark evaluation.</description>
                </item>
            </channel>
        </rss>"""

        mock_db = MagicMock()
        seen_links = set()

        staged = ingestor.parse_and_persist(
            xml_content=xml_feed,
            topic_id=1,
            db=mock_db,
            limit=50,
            seen_links=seen_links,
        )

        self.assertEqual(len(staged), 2)
        self.assertEqual(len(seen_links), 2)

    def test_x_ingestion_without_credentials_returns_empty_gracefully(self):
        with patch.dict("os.environ", {"XQUIK_API_KEY": ""}, clear=False):
            scraper = XScraper()
            topic = Topic(id=1, title="Test Topic", slug="test-topic")
            mock_db = MagicMock()
            result = scraper.fetch_and_stage(topic=topic, db=mock_db, limit=50)
            self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
