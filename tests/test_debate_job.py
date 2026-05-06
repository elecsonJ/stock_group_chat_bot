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
from debate_job import DebateQueueRunner


class FakePortfolioManager:
    def load_raw_portfolio(self):
        return ""


class FakeController:
    async def run_full_debate(self, ctx, user_query: str, portfolio_context: str = ""):
        await ctx.send(f"processing {user_query}")
        return "history", 42


class DebateJobTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.db = DBManager(db_path=self.db_path)

    def tearDown(self):
        self.db.conn.close()
        self.tmpdir.cleanup()

    def test_runner_claims_and_completes_queue_item(self):
        enqueue = self.db.enqueue_debate_candidate(
            {
                "event_id": "SGQUEUE1",
                "event_key": "EVQ1",
                "ticker": "NVDA",
                "direction": "bullish",
                "urgency": "same_day",
                "priority": 88,
                "topic": "NVIDIA contract award and outlook impact",
                "reason": "high_score,verified",
                "trigger_json": {"score_total": 88.0},
            }
        )
        self.assertTrue(enqueue["created"])

        runner = DebateQueueRunner(
            db=self.db,
            portfolio_manager=FakePortfolioManager(),
            controller_factory=lambda: FakeController(),
        )
        result = asyncio.run(runner.run_once())

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "completed")
        queue_rows = self.db.list_debate_queue(limit=10, statuses=["completed"])
        self.assertEqual(len(queue_rows), 1)
        self.assertEqual(queue_rows[0]["debate_id"], 42)


if __name__ == "__main__":
    unittest.main()
