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
        self.db.conn.close()
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


if __name__ == "__main__":
    unittest.main()
