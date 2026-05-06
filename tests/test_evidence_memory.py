import json
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
from evidence_memory import EvidenceMemory


class EvidenceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = DBManager(db_path=os.path.join(self.tmpdir.name, "test.db"))
        self.memory = EvidenceMemory(self.db)

    def tearDown(self):
        self.db.conn.close()
        self.tmpdir.cleanup()

    def test_collect_context_returns_recent_events_and_research(self):
        self.db.save_news_events_bulk(
            [
                {
                    "event_key": "EVT-NVDA-1",
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "title": "NVIDIA supply contract expansion",
                    "summary": "NVIDIA expanded AI server supply commitments",
                    "source_count": 3,
                    "article_count": 4,
                    "confidence": 0.88,
                    "sample_urls": ["https://www.reuters.com/example"],
                }
            ]
        )
        self.db.save_research_evidence(
            "NVIDIA contract",
            "NVDA contract expansion",
            {
                "status": "ok",
                "summary": "Reuters confirmed an expanded supply contract.",
                "evidences": [
                    {
                        "title": "NVIDIA signs expansion",
                        "domain": "reuters.com",
                        "url": "https://www.reuters.com/example",
                    }
                ],
            },
        )

        context = self.memory.collect_context(
            user_query="엔비디아 공급 계약 확대가 주가에 어떤 의미야?",
            tickers=["NVDA"],
            extra_terms=["엔비디아", "공급", "계약", "NVIDIA", "contract"],
        )

        self.assertGreaterEqual(len(context["events"]), 1)
        self.assertGreaterEqual(len(context["research"]), 1)
        self.assertGreater(context["events"][0]["ranking_score"], 0.0)
        self.assertGreater(context["research"][0]["ranking_score"], 0.0)
        self.assertIn("NVIDIA supply contract expansion", self.memory.render_debate_brief(context))
        rag_context = self.memory.render_rag_context(context)
        self.assertTrue(any("NVDA contract expansion" in item for item in rag_context))


if __name__ == "__main__":
    unittest.main()
