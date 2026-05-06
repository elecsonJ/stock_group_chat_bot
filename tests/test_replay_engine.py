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
from performance_tracker import PerformanceTracker
from replay_engine import ReplayEngine


class ReplayEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DBManager(db_path=os.path.join(self.tmpdir.name, "test.db"))
        self.performance = PerformanceTracker(self.db)
        self.price_map = {
            ("NVDA", "2026-04-04T09:00:00"): 99.0,
            ("NVDA", "2026-04-04T09:15:00"): 100.0,
            ("NVDA", "2026-04-05T09:00:00"): 100.0,
            ("NVDA", "2026-04-05T09:15:00"): 98.0,
            ("NVDA", "2026-04-05T10:00:00"): 103.0,
            ("NVDA", "2026-04-06T09:00:00"): 105.0,
            ("NVDA", "2026-04-06T09:15:00"): 106.0,
            ("NVDA", "2026-04-08T09:00:00"): 107.0,
            ("NVDA", "2026-04-07T09:00:00"): 106.0,
            ("SPY", "2026-04-05T09:00:00"): 500.0,
            ("SPY", "2026-04-05T09:15:00"): 500.5,
            ("SPY", "2026-04-05T10:00:00"): 501.0,
            ("SPY", "2026-04-06T09:00:00"): 505.0,
            ("SPY", "2026-04-06T09:15:00"): 505.5,
            ("SPY", "2026-04-08T09:00:00"): 506.0,
            ("SPY", "2026-04-04T09:00:00"): 498.0,
            ("SPY", "2026-04-04T09:15:00"): 498.5,
            ("SPY", "2026-04-07T09:00:00"): 504.0,
        }

        def provider(ticker: str, when: datetime) -> float | None:
            return self.price_map.get((ticker, when.strftime('%Y-%m-%dT%H:%M:%S')))

        self.engine = ReplayEngine(
            db=self.db,
            performance=self.performance,
            price_provider=provider,
            benchmark_ticker="SPY",
        )

    def tearDown(self):
        self.db.conn.close()
        self.tmpdir.cleanup()

    def test_replay_event_records_horizon_performance(self):
        self.db.save_order_execution(
            {
                "event_id": "SG-REPLAY-1",
                "ticker": "NVDA",
                "side": "BUY",
                "qty": 1.0,
                "order_type": "paper_market",
                "submitted_at": "2026-04-05T09:00:00",
                "filled_at": "2026-04-05T09:00:00",
                "fill_price": 100.0,
                "result": "PAPER_FILLED",
                "broker_order_id": "PAPER-1",
                "detail_json": {"stop_rule": "손절 -1.5%", "ttl_sec": 86400},
            }
        )
        self.db.upsert_signal_event(
            {
                "event_id": "SG-REPLAY-1",
                "event_key": "EVT-REPLAY-1",
                "date": "2026-04-05",
                "detected_at": "2026-04-05T09:00:00",
                "title": "NVDA move",
                "summary": "NVDA contract move",
                "score_total": 90.0,
                "score_json": {"impact_keywords": ["contract"]},
                "related_tickers": ["NVDA"],
                "direction": "bullish",
                "urgency": "immediate",
                "confidence": 0.9,
                "status": "executed",
                "evidence_ids": [],
                "verification_json": {"verdict": "verified", "domains": ["reuters.com"], "source_tiers": ["tier1_media"], "evidence_count": 3},
                "last_verified_at": "2026-04-05T09:00:00",
            }
        )

        rows = self.engine.replay_event("SG-REPLAY-1")

        self.assertEqual(len(rows), 4)
        first_15m = next(row for row in rows if row["horizon"] == "15m")
        self.assertLess(first_15m["return_pct"], 0.0)
        attrs = self.db.list_signal_attributions(event_id="SG-REPLAY-1")
        self.assertTrue(any(item["category"] == "source_tier" for item in attrs))
        summary = self.performance.summarize(event_id="SG-REPLAY-1")
        self.assertEqual(summary["count"], 4)
        curve = self.performance.build_equity_curve(event_id="SG-REPLAY-1")
        self.assertLessEqual(curve["max_drawdown_pct"], 0.0)

    def test_replay_split_saves_train_and_test_summaries(self):
        for idx, event_id in enumerate(["SG-SPLIT-1", "SG-SPLIT-2"], start=1):
            date = "2026-04-04" if idx == 1 else "2026-04-06"
            self.db.upsert_signal_event(
                {
                    "event_id": event_id,
                    "event_key": f"EVT-{event_id}",
                    "date": date,
                    "detected_at": f"{date}T09:00:00",
                    "title": "NVDA replay split",
                    "summary": "Split test",
                    "score_total": 80.0,
                    "score_json": {"impact_keywords": ["earnings"]},
                    "related_tickers": ["NVDA"],
                    "direction": "bullish",
                    "urgency": "same_day",
                    "confidence": 0.8,
                    "status": "monitor_only",
                    "evidence_ids": [],
                    "verification_json": {"verdict": "verified", "domains": ["reuters.com"], "source_tiers": ["tier1_media"], "evidence_count": 2},
                    "last_verified_at": f"{date}T09:00:00",
                }
            )
            self.db.replace_recommendations(
                event_id,
                [
                    {
                        "ticker": "NVDA",
                        "side": "BUY",
                        "size_rule": "1 unit",
                        "entry_rule": "market",
                        "stop_rule": "손절 -1.5%",
                        "ttl_sec": 900,
                        "confidence": 0.8,
                        "rationale": "split test",
                        "status": "pending_approval",
                    }
                ],
            )

        result = self.engine.replay_split(split_date="2026-04-05", run_name="unit_split", horizon="1d", limit=10)

        self.assertEqual(result["run_name"], "unit_split")
        summaries = self.db.list_performance_run_summaries(run_name="unit_split")
        self.assertEqual(len(summaries), 2)


if __name__ == "__main__":
    unittest.main()
