import asyncio
import json
import os
import sys
import types
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)


duckduckgo_stub = types.ModuleType("duckduckgo_search")


class _DummyDDGS:
    def text(self, *args, **kwargs):
        return []


duckduckgo_stub.DDGS = _DummyDDGS
sys.modules.setdefault("duckduckgo_search", duckduckgo_stub)

yfinance_stub = types.ModuleType("yfinance")


class _DummyTicker:
    def __init__(self, *args, **kwargs):
        self.info = {}


yfinance_stub.Ticker = _DummyTicker
sys.modules.setdefault("yfinance", yfinance_stub)

bs4_stub = types.ModuleType("bs4")


class _DummySoup:
    def __init__(self, *args, **kwargs):
        pass

    def find_all(self, *args, **kwargs):
        return []


bs4_stub.BeautifulSoup = _DummySoup
sys.modules.setdefault("bs4", bs4_stub)

httpx_module = sys.modules.get("httpx") or types.ModuleType("httpx")


class _DummyAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


if not hasattr(httpx_module, "AsyncClient"):
    httpx_module.AsyncClient = _DummyAsyncClient
sys.modules.setdefault("httpx", httpx_module)

from stable_web_search_agent import FactCheckAgent


class FakeLLM:
    async def get_local_response(self, system_prompt, user_prompt, profile="default"):
        if profile == "claim_search":
            return json.dumps(
                {
                    "claims": ["NVDA announced a new regional AI infrastructure contract"],
                    "search_queries": [
                        "NVDA investor relations regional AI infrastructure contract",
                        "NVDA Reuters regional AI infrastructure contract",
                    ],
                    "priority": "high",
                    "required_sources": ["company_ir", "tier1_media"],
                    "reason": "Need official and independent confirmation.",
                }
            )
        if profile == "evidence_verdict":
            return json.dumps(
                {
                    "status": "usable",
                    "ready_for_debate": True,
                    "ready_for_signal": True,
                    "verified_claims": ["Regional partnership was announced."],
                    "partially_supported_claims": [],
                    "unsupported_claims": [],
                    "contradictions": [],
                    "best_sources": [{"evidence_id": "E1", "domain": "investor.nvidia.com", "tier": "company_ir"}],
                    "missing_evidence": [],
                    "recommended_next_searches": [],
                    "allowed_use": "debate_and_signal",
                    "confidence": 0.78,
                }
            )
        return "- E1/E2 support the factual claim."


class FakeAgent(FactCheckAgent):
    def __init__(self):
        self.llm_manager = FakeLLM()
        self.max_results = 5
        self.fetch_concurrency = 1
        self.fetch_timeout_sec = 5
        self.search_query_budget = 3
        self.official_domains = {"sec.gov", "www.sec.gov"}
        self.company_ir_domains = {"investor.nvidia.com"}
        self.trusted_domains = {"reuters.com", "www.reuters.com"}

    async def _search_web_async(self, query: str):
        return [
            {
                "title": "NVIDIA regional partnership",
                "href": "https://investor.nvidia.com/news/example",
                "body": "NVIDIA disclosed a regional AI infrastructure partnership.",
                "domain": "investor.nvidia.com",
                "source_quality": 3,
                "source_tier": "company_ir",
            },
            {
                "title": "Reuters confirms partnership",
                "href": "https://www.reuters.com/technology/example",
                "body": "Reuters reported the partnership announcement.",
                "domain": "www.reuters.com",
                "source_quality": 2,
                "source_tier": "tier1_media",
            },
        ]

    async def _fetch_page_text(self, client, url: str) -> str:
        return "The page text describes the regional AI infrastructure partnership."


class StableWebSearchAgentTests(unittest.TestCase):
    def test_local_research_package_uses_plan_and_verdict(self):
        agent = FakeAgent()

        package = asyncio.run(agent.run_deep_research_package("NVDA regional AI infrastructure contract"))

        self.assertEqual(package["status"], "ok")
        self.assertEqual(len(package["search_plan"]["search_queries"]), 2)
        self.assertEqual(package["verdict"]["status"], "usable")
        self.assertTrue(package["verdict"]["ready_for_signal"])
        self.assertTrue(all(e.get("matched_query") for e in package["evidences"]))

    def test_fallback_verdict_requires_independent_high_quality_sources(self):
        agent = FakeAgent()
        verdict = agent._fallback_evidence_verdict(
            {
                "evidences": [
                    {"domain": "blog.example", "source_tier": "secondary", "evidence_id": "E1"},
                ]
            }
        )

        self.assertEqual(verdict["status"], "insufficient")
        self.assertFalse(verdict["ready_for_signal"])
        self.assertIn("official_or_tier1_source", verdict["missing_evidence"])


if __name__ == "__main__":
    unittest.main()
