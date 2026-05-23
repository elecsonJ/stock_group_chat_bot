import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from db_manager import DBManager
from news_context_pack import NewsContextPackService
from signal_engine import SignalEngine


class AuditTrailTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DBManager(db_path=os.path.join(self.tmpdir.name, "test.db"))

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_signal_engine_records_intake_routes(self):
        today = datetime.now().strftime("%Y-%m-%d")
        self.db.save_news_events_bulk(
            [
                {
                    "event_key": "EVT-AUDIT-LOW",
                    "date": today,
                    "title": "Small vendor mentions quarterly update",
                    "summary": "No actionable ticker or major catalyst",
                    "source_count": 1,
                    "article_count": 1,
                    "confidence": 0.2,
                    "sample_urls": [],
                },
                {
                    "event_key": "EVT-AUDIT-NVDA",
                    "date": today,
                    "title": "NVIDIA guidance raised after AI server contract",
                    "summary": "NVIDIA raised guidance after record contract award",
                    "source_count": 3,
                    "article_count": 4,
                    "confidence": 0.9,
                    "sample_urls": ["https://www.reuters.com/example"],
                },
            ]
        )
        engine = SignalEngine(self.db)
        try:
            rows = asyncio.run(
                engine.generate_signals_from_news(
                    portfolio_tickers=[],
                    threshold=58.0,
                    checker=None,
                    verify_new_only=False,
                )
            )
        finally:
            engine.close()

        self.assertTrue(any(row["event_id"] for row in rows))
        audits = self.db.list_event_intake_audits(limit=10)
        routes = {row["route"] for row in audits}
        self.assertIn("ignored_below_threshold", routes)
        self.assertIn("approval_request", routes)
        actionable = next(row for row in audits if row["route"] == "approval_request")
        self.assertGreater(actionable["score_total"], 0)
        self.assertIn("source_count", actionable["quality_json"])

    def test_news_context_pack_records_context_selection_audit(self):
        today = datetime.now().strftime("%Y-%m-%d")
        now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self.db.save_news_events_bulk(
            [
                {
                    "event_key": "EVT-CONTEXT-1",
                    "date": today,
                    "title": "NVIDIA AI server supply contract",
                    "summary": "NVIDIA contract reported by Reuters",
                    "source_count": 2,
                    "article_count": 1,
                    "confidence": 0.8,
                    "sample_urls": ["https://www.reuters.com/example"],
                }
            ]
        )
        self.db.save_news_articles_bulk(
            [
                {
                    "article_key": "context-reuters-1",
                    "date": today,
                    "source": "Reuters",
                    "source_type": "rss",
                    "section": "markets",
                    "title": "NVIDIA contract",
                    "url": "https://www.reuters.com/example",
                    "canonical_url": "https://www.reuters.com/example",
                    "published_at": now_iso,
                    "summary": "Reuters reports contract.",
                    "content_hash": "context-reuters-1",
                    "raw_json": {},
                    "event_key": "EVT-CONTEXT-1",
                    "fetched_at": now_iso,
                }
            ]
        )
        service = NewsContextPackService(self.db)
        pack = service.build_for_query(query="NVDA supply contract", tickers=["NVDA"])
        contexts = service.render_rag_contexts(pack)
        service.audit_rendered_contexts(
            pack=pack,
            consumer="unit_test",
            rendered_contexts=contexts,
            limit=5,
            truncated_chars=800,
        )

        audits = self.db.list_context_selection_audits(context_id=pack["query_hash"], limit=10)
        consumers = {row["consumer"] for row in audits}
        self.assertIn("news_context_pack", consumers)
        self.assertIn("unit_test", consumers)
        latest = audits[0]
        self.assertIn("state", latest["quality_json"])
        self.assertTrue(latest["selected_json"])


if __name__ == "__main__":
    unittest.main()
