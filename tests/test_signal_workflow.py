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
from signal_engine import SignalEngine


class FailingChecker:
    async def run_deep_research_package(self, _query):
        raise RuntimeError("network down")


class SignalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.db = DBManager(db_path=self.db_path)

    def tearDown(self):
        self.db.conn.close()
        self.tmpdir.cleanup()

    def _upsert_base_signal(self, event_id: str, status: str = "pending_approval"):
        self.db.upsert_signal_event(
            {
                "event_id": event_id,
                "event_key": "EVT-BASE",
                "date": "2026-04-05",
                "detected_at": "2026-04-05T09:00:00",
                "title": "NVIDIA contract award",
                "summary": "NVIDIA contract award raised guidance",
                "score_total": 88.0,
                "score_json": {"base_score": 80.0},
                "related_tickers": ["NVDA"],
                "direction": "bullish",
                "urgency": "same_day",
                "confidence": 0.92,
                "status": status,
                "evidence_ids": [],
                "verification_json": {},
                "last_verified_at": None,
            }
        )

    def test_mark_expired_approvals_syncs_related_state(self):
        event_id = "SG-EXPIRE-1"
        self._upsert_base_signal(event_id)
        self.db.replace_recommendations(
            event_id,
            [
                {
                    "ticker": "NVDA",
                    "side": "BUY",
                    "size_rule": "1 unit",
                    "entry_rule": "market",
                    "stop_rule": "-1%",
                    "ttl_sec": 900,
                    "confidence": 0.8,
                    "rationale": "test",
                    "status": "pending_approval",
                }
            ],
        )
        self.db.upsert_approval_request(event_id, ttl_sec=900)
        self.db.cursor.execute(
            "UPDATE approval_requests SET expires_at = ? WHERE event_id = ?",
            ("2000-01-01T00:00:00", event_id),
        )
        self.db.conn.commit()

        changed = self.db.mark_expired_approvals()

        self.assertEqual(changed, 1)
        self.assertEqual(self.db.get_approval_request(event_id)["state"], "expired")
        self.assertEqual(self.db.get_signal_event(event_id)["status"], "expired")
        self.assertEqual(self.db.get_recommendations(event_id)[0]["status"], "expired")

    def test_upsert_approval_request_preserves_approved_state(self):
        event_id = "SG-APPROVED-1"
        self.db.upsert_approval_request(event_id, ttl_sec=300)
        self.db.approve_request(event_id, "tester", note="manual approve")

        reopened = self.db.upsert_approval_request(event_id, ttl_sec=600)
        approval = self.db.get_approval_request(event_id)

        self.assertTrue(reopened)
        self.assertEqual(approval["state"], "approved")
        self.assertEqual(approval["approved_by"], "tester")

    def test_non_actionable_refresh_supersedes_pending_workflow(self):
        engine = SignalEngine(self.db)
        event_key = "EVENT-001"
        self.db.save_news_events_bulk(
            [
                {
                    "event_key": event_key,
                    "date": "2026-04-05",
                    "title": "NVIDIA contract award raised guidance",
                    "summary": "NVIDIA contract award raised guidance for AI servers",
                    "source_count": 3,
                    "article_count": 4,
                    "confidence": 0.91,
                    "sample_urls": ["https://www.reuters.com/example"],
                }
            ]
        )
        asyncio.run(
            engine.generate_signals_from_news(
                portfolio_tickers=[],
                max_events=10,
                threshold=0.0,
                checker=None,
                verify_new_only=True,
            )
        )

        event_id = engine._build_event_id({"event_key": event_key, "date": "2026-04-05"})
        self.assertEqual(self.db.get_approval_request(event_id)["state"], "pending")

        self.db.save_news_events_bulk(
            [
                {
                    "event_key": event_key,
                    "date": "2026-04-05",
                    "title": "Broad market update",
                    "summary": "General market review without a specific tradable company event",
                    "source_count": 3,
                    "article_count": 4,
                    "confidence": 0.91,
                    "sample_urls": ["https://www.reuters.com/example2"],
                }
            ]
        )
        asyncio.run(
            engine.generate_signals_from_news(
                portfolio_tickers=[],
                max_events=10,
                threshold=0.0,
                checker=None,
                verify_new_only=True,
            )
        )

        self.assertEqual(self.db.get_approval_request(event_id)["state"], "superseded")
        self.assertEqual(self.db.get_signal_event(event_id)["status"], "monitor_only")
        self.assertTrue(
            all(r["status"] == "superseded" for r in self.db.get_recommendations(event_id))
        )

    def test_common_acronyms_are_not_treated_as_tickers(self):
        engine = SignalEngine(self.db)

        tickers, portfolio_hit = engine._extract_related_tickers(
            "AI server demand lifts GDP-sensitive semiconductor outlook before CPI",
            portfolio_tickers=[],
        )

        self.assertFalse(portfolio_hit)
        self.assertNotIn("AI", tickers)
        self.assertNotIn("GDP", tickers)
        self.assertNotIn("CPI", tickers)

    def test_portfolio_ticker_is_preserved_even_if_it_is_common_acronym(self):
        engine = SignalEngine(self.db)

        tickers, portfolio_hit = engine._extract_related_tickers(
            "AI reports contract award",
            portfolio_tickers=["AI"],
        )

        self.assertTrue(portfolio_hit)
        self.assertIn("AI", tickers)

    def test_web_verification_failure_does_not_abort_signal_batch(self):
        engine = SignalEngine(self.db)
        self.db.save_news_events_bulk(
            [
                {
                    "event_key": "EVENT-WEB-FAIL",
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "title": "NVIDIA contract award raised guidance",
                    "summary": "NVIDIA contract award raised guidance for AI servers",
                    "source_count": 3,
                    "article_count": 4,
                    "confidence": 0.91,
                    "sample_urls": ["https://www.reuters.com/example"],
                }
            ]
        )

        created = asyncio.run(
            engine.generate_signals_from_news(
                portfolio_tickers=[],
                max_events=10,
                threshold=0.0,
                checker=FailingChecker(),
                verify_new_only=True,
            )
        )

        self.assertEqual(len(created), 1)
        event = self.db.get_signal_event(created[0]["event_id"])
        self.assertEqual(event["verification_json"]["verdict"], "insufficient")
        self.assertTrue(event["verification_json"]["limitations"])

    def test_market_reaction_adjustment_changes_signal_score(self):
        engine = SignalEngine(self.db)
        event_id = "SG-MR-ADJ"
        self.db.save_market_reaction_snapshot(
            {
                "event_id": event_id,
                "event_key": "EVT-MR-ADJ",
                "ticker": "NVDA",
                "event_time": "2026-05-24T10:00:00",
                "captured_at": "2026-05-24T11:00:00",
                "benchmark_ticker": "SPY",
                "sector_ticker": "SOXX",
                "pre_60m_price": 100.0,
                "pre_30m_price": 100.0,
                "pre_5m_price": 100.5,
                "event_price": 101.0,
                "post_5m_price": 102.0,
                "post_30m_price": 103.0,
                "post_60m_price": 104.5,
                "post_1d_price": 105.0,
                "pre_news_move_pct": 0.5,
                "post_60m_move_pct": 3.5,
                "post_1d_move_pct": 4.0,
                "benchmark_post_60m_pct": 0.2,
                "relative_post_60m_pct": 3.3,
                "volume_zscore": 2.5,
                "already_priced_in": False,
                "market_data_quality": "reference",
                "detail_json": {},
            }
        )

        adjusted = engine._apply_market_reaction_adjustment(
            event_id,
            {
                "score_total": 70.0,
                "direction": "bullish",
                "score_json": {},
            },
        )

        self.assertGreater(adjusted["score_total"], 70.0)
        self.assertIn("market_reaction_adjustments", adjusted["score_json"])


if __name__ == "__main__":
    unittest.main()
