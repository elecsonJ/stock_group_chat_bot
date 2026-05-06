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


class NewsContextPackServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DBManager(db_path=os.path.join(self.tmpdir.name, "test.db"))
        self.service = NewsContextPackService(self.db)

    def tearDown(self):
        self.db.conn.close()
        self.tmpdir.cleanup()

    def test_build_for_query_returns_quality_pack_and_persists_it(self):
        today = datetime.now().strftime("%Y-%m-%d")
        now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self.db.save_news_events_bulk(
            [
                {
                    "event_key": "EVT-NVDA-1",
                    "date": today,
                    "title": "NVIDIA supply contract expansion",
                    "summary": "NVIDIA expanded AI server supply commitments with official filing support",
                    "source_count": 3,
                    "article_count": 3,
                    "confidence": 0.9,
                    "sample_urls": ["https://www.sec.gov/example", "https://www.reuters.com/example"],
                }
            ]
        )
        self.db.save_news_articles_bulk(
            [
                {
                    "article_key": "sec-nvda-1",
                    "date": today,
                    "source": "SEC-PressRelease",
                    "source_type": "regulatory",
                    "section": "filings",
                    "title": "NVIDIA filing references supply commitments",
                    "url": "https://www.sec.gov/example",
                    "canonical_url": "https://www.sec.gov/example",
                    "published_at": now_iso,
                    "summary": "Official filing source for supply commitments.",
                    "content_hash": "hash-sec-nvda-1",
                    "raw_json": {"source": "sec"},
                    "event_key": "EVT-NVDA-1",
                    "fetched_at": now_iso,
                },
                {
                    "article_key": "reuters-nvda-1",
                    "date": today,
                    "source": "Reuters-Markets",
                    "source_type": "rss",
                    "section": "markets",
                    "title": "NVIDIA expands AI server supply contract",
                    "url": "https://www.reuters.com/example",
                    "canonical_url": "https://www.reuters.com/example",
                    "published_at": now_iso,
                    "summary": "Reuters reports expanded AI server supply commitments.",
                    "content_hash": "hash-reuters-nvda-1",
                    "raw_json": {"source": "reuters"},
                    "event_key": "EVT-NVDA-1",
                    "fetched_at": now_iso,
                },
            ]
        )
        self.db.save_research_evidence(
            "NVDA supply contract",
            "NVDA supply contract official filing",
            {
                "status": "ok",
                "summary": "Official filings and Reuters support the supply contract context.",
                "evidences": [
                    {
                        "title": "NVIDIA supply filing",
                        "domain": "sec.gov",
                        "url": "https://www.sec.gov/example",
                        "source_quality": 4.0,
                        "source_tier": "regulatory",
                    },
                    {
                        "title": "Reuters supply report",
                        "domain": "reuters.com",
                        "url": "https://www.reuters.com/example",
                        "source_quality": 3.5,
                        "source_tier": "tier1_media",
                    },
                ],
            },
        )

        pack = self.service.build_for_query(
            query="NVDA AI server supply contract",
            tickers=["NVDA"],
            extra_terms=["supply", "contract"],
        )

        self.assertEqual(pack["schema_version"], NewsContextPackService.SCHEMA_VERSION)
        self.assertEqual(pack["quality"]["state"], "strong")
        self.assertFalse(pack["quality"]["web_required"])
        self.assertEqual(pack["quality"]["official_event_count"], 1)
        self.assertGreaterEqual(pack["quality"]["high_quality_research_count"], 1)
        self.assertEqual(pack["events"][0]["memory_status"], "officially_supported")
        self.assertIn("regulatory", pack["events"][0]["tier_mix"])
        self.assertIn("[NEWS_CONTEXT_PACK]", self.service.render_for_model(pack))
        self.assertTrue(any("NVIDIA supply contract expansion" in ctx for ctx in self.service.render_rag_contexts(pack)))

        saved = self.db.get_latest_news_context_pack(pack["query_hash"])
        self.assertIsNotNone(saved)
        self.assertEqual(saved["query_hash"], pack["query_hash"])

    def test_empty_pack_requires_web_refresh_queries(self):
        pack = self.service.build_for_query(
            query="XYZ unknown catalyst risk",
            tickers=["XYZ"],
            persist=False,
        )

        self.assertEqual(pack["quality"]["state"], "empty")
        self.assertTrue(pack["quality"]["web_required"])
        self.assertIn("no_local_event_memory", pack["quality"]["limitations"])
        self.assertIn("no_web_verification_memory", pack["quality"]["limitations"])
        self.assertTrue(any("XYZ" in q for q in pack["recommended_web_queries"]))

    def test_unmatched_query_does_not_fallback_to_unrelated_latest_events(self):
        today = datetime.now().strftime("%Y-%m-%d")
        self.db.save_news_events_bulk(
            [
                {
                    "event_key": "EVT-NVDA-UNRELATED",
                    "date": today,
                    "title": "NVIDIA raises data center guidance",
                    "summary": "NVIDIA issued a stronger data center outlook.",
                    "source_count": 3,
                    "article_count": 4,
                    "confidence": 0.92,
                    "sample_urls": ["https://www.reuters.com/example"],
                }
            ]
        )

        pack = self.service.build_for_query(
            query="XYZ biotech trial halt",
            tickers=["XYZ"],
            persist=False,
        )

        self.assertEqual(pack["events"], [])
        self.assertEqual(pack["quality"]["state"], "empty")
        self.assertTrue(pack["quality"]["web_required"])


if __name__ == "__main__":
    unittest.main()
