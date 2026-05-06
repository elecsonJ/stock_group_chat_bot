import os
import sys
import unittest
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from exit_manager import ExitManager


class ExitManagerTests(unittest.TestCase):
    def test_stop_loss_exits_early(self):
        prices = {
            ("NVDA", "2026-04-05T09:15:00"): 98.0,
            ("NVDA", "2026-04-05T09:30:00"): 97.5,
            ("NVDA", "2026-04-05T10:00:00"): 101.0,
        }

        def getter(ticker: str, when: datetime):
            return prices.get((ticker, when.strftime("%Y-%m-%dT%H:%M:%S")))

        manager = ExitManager(getter)
        result = manager.resolve_exit(
            ticker="NVDA",
            side="BUY",
            entry_dt=datetime.fromisoformat("2026-04-05T09:00:00"),
            entry_price=100.0,
            horizon_end_dt=datetime.fromisoformat("2026-04-05T10:00:00"),
            stop_rule="손절 -1.5%",
        )

        self.assertEqual(result["exit_reason"], "stop_loss")
        self.assertEqual(result["exit_price"], 98.0)


if __name__ == "__main__":
    unittest.main()
