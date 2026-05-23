import os
import sys
import types
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.append(SRC_DIR)

httpx_stub = types.ModuleType("httpx")
httpx_stub.TimeoutException = Exception
sys.modules.setdefault("httpx", httpx_stub)

openai_stub = types.ModuleType("openai")
class _DummyAsyncOpenAI:
    def __init__(self, *args, **kwargs):
        pass
openai_stub.AsyncOpenAI = _DummyAsyncOpenAI
sys.modules.setdefault("openai", openai_stub)

anthropic_stub = types.ModuleType("anthropic")
class _DummyAsyncAnthropic:
    def __init__(self, *args, **kwargs):
        pass
anthropic_stub.AsyncAnthropic = _DummyAsyncAnthropic
sys.modules.setdefault("anthropic", anthropic_stub)

google_stub = types.ModuleType("google")
genai_stub = types.ModuleType("google.genai")
class _DummyClient:
    def __init__(self, *args, **kwargs):
        pass
genai_stub.Client = _DummyClient
types_stub = types.ModuleType("google.genai.types")
class _DummyGenerateContentConfig:
    def __init__(self, *args, **kwargs):
        pass
types_stub.GenerateContentConfig = _DummyGenerateContentConfig
genai_stub.types = types_stub
google_stub.genai = genai_stub
sys.modules.setdefault("google", google_stub)
sys.modules.setdefault("google.genai", genai_stub)
sys.modules.setdefault("google.genai.types", types_stub)

dotenv_stub = types.ModuleType("dotenv")
def _dummy_load_dotenv(*args, **kwargs):
    return None
dotenv_stub.load_dotenv = _dummy_load_dotenv
sys.modules.setdefault("dotenv", dotenv_stub)

from llm_client import LLMClientManager


class LLMClientProfileTests(unittest.TestCase):
    def test_fit_text_to_budget_truncates_middle(self):
        manager = LLMClientManager()
        text = "A" * 500 + "B" * 500 + "C" * 500

        trimmed = manager.fit_text_to_budget(text, 300)

        self.assertLessEqual(len(trimmed), 300)
        self.assertIn("truncated", trimmed)
        self.assertTrue(trimmed.startswith("A"))
        self.assertTrue(trimmed.endswith("C" * 10))

    def test_prepare_local_payload_uses_profile_budget(self):
        manager = LLMClientManager()
        manager.local_context_budgets["extract"] = 120
        system_prompt, user_prompt = manager.prepare_local_payload(
            "너는 추출기야.",
            "Z" * 500,
            profile="extract",
        )

        self.assertIn("반드시 한국어", system_prompt)
        self.assertLessEqual(len(user_prompt), 120)

    def test_local_model_for_profile_uses_env_override(self):
        os.environ["LOCAL_MODEL_NAME_EVIDENCE_VERDICT"] = "google/gemma-4-31b"
        try:
            manager = LLMClientManager()

            self.assertEqual(manager.local_model_for_profile("evidence_verdict"), "google/gemma-4-31b")
            self.assertEqual(manager.local_model_for_profile("claim_search"), manager.models["local"])
        finally:
            os.environ.pop("LOCAL_MODEL_NAME_EVIDENCE_VERDICT", None)


if __name__ == "__main__":
    unittest.main()
