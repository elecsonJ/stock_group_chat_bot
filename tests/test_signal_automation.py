import asyncio
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from db_manager import DBManager
from signal_engine import SignalEngine


class FakeChecker:
    async def run_deep_research_package(self, query: str):
        return {
            "summary": f"verified package for {query}",
            "limitations": [],
            "evidences": [
                {
                    "domain": "sec.gov",
                    "source_tier": "regulatory",
                    "snippet": "recall investigation guidance cut",
                    "excerpt": "regulatory filing confirms issue",
                    "global_evidence_id": "EV1",
                },
                {
                    "domain": "reuters.com",
                    "source_tier": "tier1_media",
                    "snippet": "recall widens downside risk",
                    "excerpt": "major media confirmation",
                    "global_evidence_id": "EV2",
                },
            ],
        }


class BullishHiddenChecker:
    async def run_deep_research_package(self, query: str):
        return {
            "summary": f"bullish package for {query}",
            "limitations": [],
            "evidences": [
                {
                    "domain": "sec.gov",
                    "source_tier": "regulatory",
                    "snippet": "raised outlook demand surge",
                    "excerpt": "filing confirms stronger demand",
                    "global_evidence_id": "B1",
                },
                {
                    "domain": "reuters.com",
                    "source_tier": "tier1_media",
                    "snippet": "beneficiary of durable workwear demand",
                    "excerpt": "major media confirmation",
                    "global_evidence_id": "B2",
                },
            ],
        }


class SignalAutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.db = DBManager(db_path=self.db_path)

    def tearDown(self):
        self.db.conn.close()
        self.tmpdir.cleanup()

    def test_verified_bearish_portfolio_event_creates_queue_and_exit_review(self):
        self.db.save_news_events_bulk(
            [
                {
                    "event_key": "EVT-BEAR-1",
                    "date": "2026-04-05",
                    "title": "NVIDIA recall investigation triggers guidance cut",
                    "summary": "NVIDIA faces recall and investigation risk after AI server defects",
                    "source_count": 3,
                    "article_count": 5,
                    "confidence": 0.93,
                    "sample_urls": ["https://www.reuters.com/example"],
                }
            ]
        )
        engine = SignalEngine(self.db)
        engine._build_ontology_plan = lambda event: {"hidden_candidates": [], "web_queries": ["NVDA recall filing"]}  # type: ignore[method-assign]

        rows = asyncio.run(
            engine.generate_signals_from_news(
                portfolio_tickers=["NVDA"],
                max_events=10,
                threshold=0.0,
                checker=FakeChecker(),
                verify_new_only=False,
            )
        )

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["debate_queue"]["created"])
        queue_rows = self.db.list_debate_queue(limit=10, statuses=["pending"])
        self.assertEqual(len(queue_rows), 1)
        self.assertIn("high_quality_source", queue_rows[0]["reason"])

        review_rows = self.db.list_investment_review_triggers(limit=10, statuses=["open"])
        self.assertEqual(len(review_rows), 1)
        self.assertEqual(review_rows[0]["ticker"], "NVDA")
        self.assertEqual(review_rows[0]["trigger_type"], "exit_review")

    def test_strong_hidden_candidate_creates_add_review_for_portfolio_name(self):
        self.db.save_news_events_bulk(
            [
                {
                    "event_key": "EVT-HIDDEN-1",
                    "date": "2026-04-05",
                    "title": "Mining boom raised durable workwear outlook",
                    "summary": "Demand for rugged field clothing and workwear surged with the mining boom",
                    "source_count": 3,
                    "article_count": 4,
                    "confidence": 0.88,
                    "sample_urls": ["https://www.reuters.com/example2"],
                }
            ]
        )
        engine = SignalEngine(self.db)
        engine._build_ontology_plan = lambda event: {  # type: ignore[method-assign]
            "hidden_candidates": [
                {
                    "ticker": "LEVI",
                    "name": "Levi Strauss",
                    "validation_score": 0.91,
                }
            ],
            "web_queries": ["LEVI indirect beneficiary workwear demand"],
        }

        rows = asyncio.run(
            engine.generate_signals_from_news(
                portfolio_tickers=["LEVI"],
                max_events=10,
                threshold=0.0,
                checker=BullishHiddenChecker(),
                verify_new_only=False,
            )
        )

        self.assertEqual(len(rows), 1)
        queue_rows = self.db.list_debate_queue(limit=10, statuses=["pending"])
        self.assertEqual(len(queue_rows), 1)
        review_rows = self.db.list_investment_review_triggers(limit=10, statuses=["open"])
        self.assertEqual(len(review_rows), 1)
        self.assertEqual(review_rows[0]["ticker"], "LEVI")
        self.assertEqual(review_rows[0]["trigger_type"], "add_review")
        self.assertEqual(review_rows[0]["detail_json"].get("origin"), "hidden_candidate")

    def test_same_event_is_not_enqueued_twice_within_cooldown(self):
        self.db.save_news_events_bulk(
            [
                {
                    "event_key": "EVT-DUP-1",
                    "date": "2026-04-05",
                    "title": "NVIDIA contract award raised outlook",
                    "summary": "NVIDIA secured a major AI infrastructure contract award",
                    "source_count": 2,
                    "article_count": 3,
                    "confidence": 0.84,
                    "sample_urls": ["https://www.reuters.com/example3"],
                }
            ]
        )
        engine = SignalEngine(self.db)
        engine._build_ontology_plan = lambda event: {"hidden_candidates": [], "web_queries": []}  # type: ignore[method-assign]

        asyncio.run(
            engine.generate_signals_from_news(
                portfolio_tickers=["NVDA"],
                max_events=10,
                threshold=0.0,
                checker=FakeChecker(),
                verify_new_only=False,
            )
        )
        asyncio.run(
            engine.generate_signals_from_news(
                portfolio_tickers=["NVDA"],
                max_events=10,
                threshold=0.0,
                checker=FakeChecker(),
                verify_new_only=False,
            )
        )

        queue_rows = self.db.list_debate_queue(limit=10, statuses=["pending", "processing", "completed"])
        self.assertEqual(len(queue_rows), 1)


if __name__ == "__main__":
    unittest.main()
