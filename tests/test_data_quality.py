import os
import sys
import tempfile
import unittest
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from data_quality import DataQualityEvaluator
from db_manager import DBManager
from news_context_pack import NewsContextPackService
from performance_tracker import PerformanceTracker


class DataQualityEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DBManager(db_path=os.path.join(self.tmpdir.name, "test.db"))

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_assess_scores_collection_quality_and_persists_report(self):
        today = datetime.now().strftime("%Y-%m-%d")
        now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self.db.save_news_events_bulk(
            [
                {
                    "event_key": "EVT-DQ-1",
                    "date": today,
                    "title": "NVIDIA guidance raise confirmed",
                    "summary": "NVIDIA guidance raise confirmed by official filing and Reuters",
                    "source_count": 2,
                    "article_count": 2,
                    "confidence": 0.9,
                    "sample_urls": ["https://www.sec.gov/example", "https://www.reuters.com/example"],
                }
            ]
        )
        self.db.save_news_articles_bulk(
            [
                {
                    "article_key": "dq-sec-1",
                    "date": today,
                    "source": "SEC",
                    "source_type": "regulatory",
                    "section": "filings",
                    "title": "NVIDIA filing",
                    "url": "https://www.sec.gov/example",
                    "canonical_url": "https://www.sec.gov/example",
                    "published_at": now_iso,
                    "summary": "Official filing",
                    "content_hash": "dq-sec-1",
                    "raw_json": {},
                    "event_key": "EVT-DQ-1",
                    "fetched_at": now_iso,
                    "ingest_delay_sec": 60,
                },
                {
                    "article_key": "dq-reuters-1",
                    "date": today,
                    "source": "Reuters",
                    "source_type": "rss",
                    "section": "markets",
                    "title": "NVIDIA guidance",
                    "url": "https://www.reuters.com/example",
                    "canonical_url": "https://www.reuters.com/example",
                    "published_at": now_iso,
                    "summary": "Reuters report",
                    "content_hash": "dq-reuters-1",
                    "raw_json": {},
                    "event_key": "EVT-DQ-1",
                    "fetched_at": now_iso,
                    "ingest_delay_sec": 90,
                },
            ]
        )
        self.db.save_research_evidence(
            "NVIDIA guidance",
            "NVDA guidance official filing Reuters",
            {
                "summary": "SEC and Reuters support the event.",
                "evidences": [
                    {"domain": "sec.gov", "source_tier": "regulatory", "source_quality": 4.0},
                    {"domain": "reuters.com", "source_tier": "tier1_media", "source_quality": 3.5},
                ],
            },
        )
        event_id = "SG-DQ-1"
        self.db.upsert_signal_event(
            {
                "event_id": event_id,
                "event_key": "EVT-DQ-1",
                "date": today,
                "detected_at": now_iso,
                "title": "NVIDIA guidance raise confirmed",
                "summary": "verified event",
                "score_total": 86.0,
                "score_json": {"base_score": 60.0, "impact_score": 20.0},
                "related_tickers": ["NVDA"],
                "direction": "bullish",
                "urgency": "same_day",
                "confidence": 0.9,
                "status": "monitor_only",
                "evidence_ids": ["EV1", "EV2"],
                "verification_json": {"verdict": "verified", "evidence_count": 2},
                "last_verified_at": now_iso,
            }
        )
        pack = NewsContextPackService(self.db).build_for_query(
            query="NVDA guidance official filing",
            tickers=["NVDA"],
        )
        self.assertIn(pack["quality"]["state"], {"strong", "usable", "usable_needs_refresh"})
        PerformanceTracker(self.db).record_measurement(
            event_id=event_id,
            ticker="NVDA",
            horizon="1d",
            entry_price=100.0,
            exit_price=102.0,
            detail_json={"measured_at": now_iso},
        )

        evaluator = DataQualityEvaluator(self.db)
        report = evaluator.assess(lookback_hours=168)

        self.assertEqual(report["schema_version"], DataQualityEvaluator.SCHEMA_VERSION)
        self.assertGreater(report["overall_score"], 0)
        self.assertEqual(report["collection"]["event_count"], 1)
        self.assertGreaterEqual(report["quality"]["high_quality_article_ratio"], 0.5)
        self.assertGreaterEqual(report["quality"]["verification_coverage"], 1.0)
        self.assertIn("[데이터 품질 평가]", evaluator.render(report))
        evaluator.save_report(report)
        self.assertIsNotNone(self.db.get_system_metadata("data_quality_report_v1"))


if __name__ == "__main__":
    unittest.main()
