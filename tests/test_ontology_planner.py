import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

from ontology.store import OntologyStore
from ontology.planner import HybridResearchPlanner
from ontology_bootstrap import ingest_commonsense_json


class OntologyPlannerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "ontology_test.db")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_commonsense_seed_surfaces_hidden_candidate(self):
        seed_path = os.path.join(self.tmpdir.name, "commonsense.json")
        with open(seed_path, "w", encoding="utf-8") as f:
            with open(os.path.join(PROJECT_ROOT, "commonsense_ontology.example.json"), "r", encoding="utf-8") as src:
                f.write(src.read())

        store = OntologyStore(db_path=self.db_path)
        ingest_commonsense_json(seed_path, store)
        planner = HybridResearchPlanner(store)

        plan = planner.build_plan("광산붐 때 숨은 수혜주가 뭘까?")

        hidden = plan.get("hidden_candidates", [])
        self.assertTrue(any(item.get("ticker") == "LEVI" for item in hidden))
        self.assertTrue(any(float(item.get("validation_score", 0.0)) >= 0.45 for item in hidden))
        self.assertTrue(any("LEVI" in q for q in plan.get("web_queries", [])))


if __name__ == "__main__":
    unittest.main()
