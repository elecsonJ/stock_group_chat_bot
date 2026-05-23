import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from db_manager import DBManager
from performance_tracker import PerformanceTracker


class PerformanceTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DBManager(db_path=os.path.join(self.tmpdir.name, "test.db"))
        self.tracker = PerformanceTracker(self.db)

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def test_equity_curve_and_run_summary(self):
        rows = []
        for idx, ret in enumerate([5.0, -2.0, 3.0], start=1):
            rows.append(
                self.tracker.record_measurement(
                    event_id=f"SG-PERF-{idx}",
                    ticker="NVDA",
                    horizon="1d",
                    entry_price=100.0,
                    exit_price=100.0 * (1 + ret / 100.0),
                    detail_json={"measured_at": f"2026-04-0{idx}T09:00:00"},
                )
            )

        curve = self.tracker.build_equity_curve(rows=rows, horizon="1d", starting_equity=1000.0)
        self.assertEqual(curve["count"], 3)
        self.assertLess(curve["max_drawdown_pct"], 0.0)
        summary = self.tracker.save_run_summary(
            run_name="perf_unit",
            split_label="test",
            horizon="1d",
            rows=rows,
            window_start="2026-04-01",
            window_end="2026-04-03",
            signal_count=3,
            starting_equity=1000.0,
        )
        self.assertEqual(summary["measurement_count"], 3)
        saved = self.db.list_performance_run_summaries(run_name="perf_unit")
        self.assertEqual(len(saved), 1)

    def test_feedback_report_uses_signal_and_debate_attributions(self):
        event_id = "SG-FEEDBACK-1"
        self.db.upsert_signal_event(
            {
                "event_id": event_id,
                "event_key": "EVT-FEEDBACK-1",
                "date": "2026-04-05",
                "detected_at": "2026-04-05T09:00:00",
                "title": "NVIDIA verified guidance raise",
                "summary": "NVIDIA guidance raise verified by SEC and Reuters",
                "score_total": 88.0,
                "score_json": {
                    "base_score": 62.0,
                    "impact_score": 18.0,
                    "portfolio_hit": True,
                    "impact_keywords": ["guidance"],
                },
                "related_tickers": ["NVDA"],
                "direction": "bullish",
                "urgency": "same_day",
                "confidence": 0.91,
                "status": "monitor_only",
                "evidence_ids": ["EV1", "EV2"],
                "verification_json": {
                    "verdict": "verified",
                    "evidence_count": 2,
                    "domains": ["sec.gov", "reuters.com"],
                    "source_tiers": ["regulatory", "tier1_media"],
                },
                "last_verified_at": "2026-04-05T09:01:00",
            }
        )
        self.db.save_debate_quality_score(
            {
                "debate_id": 42,
                "event_id": event_id,
                "total_score": 85.0,
                "status": "strong",
                "detail_json": {"missing": []},
            }
        )
        self.db.save_market_reaction_snapshot(
            {
                "event_id": event_id,
                "event_key": "EVT-FEEDBACK-1",
                "ticker": "NVDA",
                "event_time": "2026-04-05T09:00:00",
                "benchmark_ticker": "SPY",
                "event_price": 100.0,
                "post_60m_price": 103.0,
                "relative_post_60m_pct": 2.5,
                "pre_news_move_pct": 0.2,
                "already_priced_in": False,
                "market_data_quality": "reference",
            }
        )
        measurement = self.tracker.record_measurement(
            event_id=event_id,
            ticker="NVDA",
            horizon="1d",
            entry_price=100.0,
            exit_price=104.0,
            benchmark_entry_price=100.0,
            benchmark_exit_price=101.0,
            detail_json={"measured_at": "2026-04-06T09:00:00", "origin": "signal"},
        )

        attrs = self.tracker.record_attributions(
            signal_event=self.db.get_signal_event(event_id),
            measurement=measurement,
        )
        categories = {a["category"] for a in attrs}
        self.assertIn("signal_score_bucket", categories)
        self.assertIn("source_tier", categories)
        self.assertIn("debate_quality_status", categories)
        self.assertIn("relative_post_60m_bucket", categories)

        report = self.tracker.build_feedback_report(horizon="1d", min_samples=1)
        self.assertEqual(report["schema_version"], "performance_feedback.v1")
        self.assertTrue(report["policy_hints"]["promote"])
        self.tracker.save_feedback_profile(report)
        self.assertIsNotNone(self.db.get_system_metadata("performance_feedback_profile_v1"))


if __name__ == "__main__":
    unittest.main()
