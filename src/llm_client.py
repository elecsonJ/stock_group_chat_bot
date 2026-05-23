import os
import httpx
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from google import genai
from google.genai import types
from dotenv import load_dotenv
import re
from time import monotonic

load_dotenv()

class LLMClientManager:
    def __init__(self):
        # OpenAI (GPT)
        self.openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY") or "missing-openai-key")
        # Anthropic (Claude)
        self.anthropic_client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY") or "missing-anthropic-key")
        # Google (Gemini) - 최신 SDK 유지 (google.genai)
        self.gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY") or "missing-gemini-key")
        
        self.models = {
            "gpt": "gpt-5.2-2025-12-11", # 사용자가 제시한 작동 가능한 대화형 모델
            "claude": "claude-sonnet-4-6", # 2026 실제 지원 API Endpoint
            "gemini_primary": os.getenv("GEMINI_PRIMARY_MODEL", "gemini-3.1-pro-preview"),
            "gemini_fallback": os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3-flash-preview"),
            "local": os.getenv("LOCAL_MODEL_NAME", "gpt-oss:20b")
        }
        self.local_backend = os.getenv("LOCAL_MODEL_BACKEND", "ollama").strip().lower()
        self.local_timeout_sec = max(30, int(os.getenv("LOCAL_TIMEOUT_SEC", "600")))
        self.local_api_key = os.getenv("LOCAL_API_KEY", "lm-studio")
        self.local_openai_base_url = os.getenv("LOCAL_OPENAI_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
        self.local_ollama_url = os.getenv("LOCAL_OLLAMA_URL", "http://localhost:11434/api/chat")
        self.local_max_output_tokens = max(256, int(os.getenv("LOCAL_MAX_OUTPUT_TOKENS", "2048")))
        self.local_context_budgets = {
            "default": max(2000, int(os.getenv("LOCAL_CONTEXT_BUDGET_DEFAULT", "9000"))),
            "json": max(800, int(os.getenv("LOCAL_CONTEXT_BUDGET_JSON", "2000"))),
            "extract": max(800, int(os.getenv("LOCAL_CONTEXT_BUDGET_EXTRACT", "2500"))),
            "summary": max(2000, int(os.getenv("LOCAL_CONTEXT_BUDGET_SUMMARY", "12000"))),
            "rag_answer": max(4000, int(os.getenv("LOCAL_CONTEXT_BUDGET_RAG", "16000"))),
            "judge": max(4000, int(os.getenv("LOCAL_CONTEXT_BUDGET_JUDGE", "18000"))),
            "evidence": max(2000, int(os.getenv("LOCAL_CONTEXT_BUDGET_EVIDENCE", "10000"))),
            "claim_search": max(1200, int(os.getenv("LOCAL_CONTEXT_BUDGET_CLAIM_SEARCH", "6000"))),
            "evidence_verdict": max(2000, int(os.getenv("LOCAL_CONTEXT_BUDGET_EVIDENCE_VERDICT", "14000"))),
        }
        self.gemini_primary_retries = max(1, int(os.getenv("GEMINI_PRIMARY_RETRIES", "2")))
        self.gemini_fallback_retries = max(1, int(os.getenv("GEMINI_FALLBACK_RETRIES", "2")))
        self.gemini_timeout_sec = max(20, int(os.getenv("GEMINI_TIMEOUT_SEC", "60")))
        self.circuit_failure_threshold = max(1, int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "3")))
        self.circuit_cooldown_sec = max(10, int(os.getenv("CIRCUIT_COOLDOWN_SEC", "60")))
        self._circuit_state = {
            "gpt": {"failures": 0, "open_until": 0.0},
            "claude": {"failures": 0, "open_until": 0.0},
            "gemini": {"failures": 0, "open_until": 0.0},
            "local": {"failures": 0, "open_until": 0.0},
        }
        self.local_openai_client = AsyncOpenAI(
            api_key=self.local_api_key,
            base_url=self.local_openai_base_url,
        )

    def local_model_for_profile(self, profile: str = "default") -> str:
        safe_profile = re.sub(r"[^A-Z0-9]+", "_", str(profile or "default").upper()).strip("_")
        profile_model = os.getenv(f"LOCAL_MODEL_NAME_{safe_profile}", "").strip()
        return profile_model or self.models["local"]

    def _is_circuit_open(self, key: str) -> tuple[bool, int]:
        state = self._circuit_state.get(key, {"open_until": 0.0})
        remain = int(state.get("open_until", 0.0) - monotonic())
        return (remain > 0, max(0, remain))

    def _record_success(self, key: str):
        if key in self._circuit_state:
            self._circuit_state[key]["failures"] = 0
            self._circuit_state[key]["open_until"] = 0.0

    def _record_failure(self, key: str):
        if key not in self._circuit_state:
            return
        state = self._circuit_state[key]
        state["failures"] = int(state.get("failures", 0)) + 1
        if state["failures"] >= self.circuit_failure_threshold:
            state["open_until"] = monotonic() + float(self.circuit_cooldown_sec)
            state["failures"] = 0

    def fit_text_to_budget(self, text: str, max_chars: int) -> str:
        raw = str(text or "")
        if max_chars <= 0 or len(raw) <= max_chars:
            return raw
        marker = "\n...[middle truncated for local budget]...\n"
        if max_chars <= len(marker) + 20:
            return raw[:max_chars]
        head = int((max_chars - len(marker)) * 0.7)
        tail = max_chars - len(marker) - head
        return raw[:head] + marker + raw[-tail:]

    def prepare_local_payload(
        self,
        system_prompt: str,
        user_prompt: str,
        profile: str = "default",
        max_input_chars: int | None = None,
    ) -> tuple[str, str]:
        sys_prompt = str(system_prompt or "")
        usr_prompt = str(user_prompt or "")
        budget = max_input_chars or self.local_context_budgets.get(profile, self.local_context_budgets["default"])
        trimmed_user = self.fit_text_to_budget(usr_prompt, budget)
        if "json" not in sys_prompt.lower() and "출력기" not in sys_prompt:
            sys_prompt += "\n반드시 한국어(Korean)로 대답해."
        return sys_prompt, trimmed_user

    async def get_gpt_response(self, system_prompt: str, user_prompt: str) -> str:
        import asyncio
        is_open, remain = self._is_circuit_open("gpt")
        if is_open:
            return f"Error from GPT: CircuitOpen ({remain}s)"
        for attempt in range(3):
            try:
                response = await self.openai_client.chat.completions.create(
                    model=self.models["gpt"],
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    max_completion_tokens=4000,
                    timeout=180.0
                )
                self._record_success("gpt")
                return response.choices[0].message.content
            except Exception as e:
                if attempt == 2:
                    self._record_failure("gpt")
                    return f"Error from GPT: {str(e)}"
                await asyncio.sleep(2 ** attempt)

    async def get_claude_response(self, system_prompt: str, user_prompt: str) -> str:
        import asyncio
        is_open, remain = self._is_circuit_open("claude")
        if is_open:
            return f"Error from Claude: CircuitOpen ({remain}s)"
        for attempt in range(3):
            try:
                response = await self.anthropic_client.messages.create(
                    model=self.models["claude"],
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    max_tokens=4000,
                    temperature=0.7,
                    timeout=180.0
                )
                self._record_success("claude")
                return response.content[0].text
            except Exception as e:
                if attempt == 2:
                    self._record_failure("claude")
                    return f"Error from Claude: {str(e)}"
                await asyncio.sleep(2 ** attempt)

    async def get_gemini_response(self, system_prompt: str, user_prompt: str) -> str:
        import asyncio
        is_open, remain = self._is_circuit_open("gemini")
        if is_open:
            return f"Error from Gemini: CircuitOpen ({remain}s)"
        
        def _usable_text(text: str) -> bool:
            if not text:
                return False
            t = text.strip().lower()
            if not t:
                return False
            bad_prefixes = (
                "error from gemini",
                "internal error",
                "service unavailable",
            )
            return not any(t.startswith(p) for p in bad_prefixes)

        async def _try_model(model_name: str, retries: int) -> tuple[str | None, list[str]]:
            errors: list[str] = []

            def _fetch():
                return self.gemini_client.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                    )
                )

            for attempt in range(retries):
                try:
                    response = await asyncio.wait_for(
                        asyncio.to_thread(_fetch), timeout=float(self.gemini_timeout_sec)
                    )  # type: ignore
                    text = (getattr(response, "text", "") or "").strip()
                    if _usable_text(text):
                        return text, errors
                    errors.append(f"{model_name}: empty_or_invalid_text")
                except asyncio.TimeoutError:
                    errors.append(f"{model_name}: timeout({self.gemini_timeout_sec}s)")
                except Exception as e:
                    errors.append(f"{model_name}: {str(e)}")

                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
            return None, errors

        primary_model = self.models["gemini_primary"]
        fallback_model = self.models["gemini_fallback"]

        primary_text, primary_errors = await _try_model(primary_model, self.gemini_primary_retries)
        if primary_text:
            self._record_success("gemini")
            return primary_text

        fallback_text, fallback_errors = await _try_model(fallback_model, self.gemini_fallback_retries)
        if fallback_text:
            self._record_success("gemini")
            return fallback_text

        merged = primary_errors + fallback_errors
        self._record_failure("gemini")
        return f"Error from Gemini: primary/fallback 모두 실패 ({'; '.join(merged[:6])})"
            
    async def get_local_response(
        self,
        system_prompt: str,
        user_prompt: str,
        profile: str = "default",
        max_input_chars: int | None = None,
    ) -> str:
        """
        로컬 모델(Ollama 또는 OpenAI-compatible local server)을 호출합니다.
        """
        is_open, remain = self._is_circuit_open("local")
        if is_open:
            raise RuntimeError(f"Error connecting to local model: CircuitOpen ({remain}s)")
        system_prompt, user_prompt = self.prepare_local_payload(
            system_prompt,
            user_prompt,
            profile=profile,
            max_input_chars=max_input_chars,
        )

        try:
            if self.local_backend == "openai_compatible":
                response = await self.local_openai_client.chat.completions.create(
                    model=self.local_model_for_profile(profile),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=self.local_max_output_tokens,
                    timeout=float(self.local_timeout_sec),
                )
                ans = response.choices[0].message.content or ""
                ans = re.sub(r'<think>.*?</think>', '', ans, flags=re.DOTALL).strip()
                self._record_success("local")
                return ans

            local_url = self.local_ollama_url
            if "api/generate" in local_url:
                local_url = local_url.replace("api/generate", "api/chat")
            async with httpx.AsyncClient(timeout=float(self.local_timeout_sec)) as client:
                payload = {
                    "model": self.local_model_for_profile(profile),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "stream": False
                }
                response = await client.post(local_url, json=payload)
                if response.status_code == 200:
                    ans = response.json().get("message", {}).get("content", "")
                    ans = re.sub(r'<think>.*?</think>', '', ans, flags=re.DOTALL).strip()
                    self._record_success("local")
                    return ans
                raise RuntimeError(f"Local Model Error: HTTP {response.status_code}\n{response.text}")
        except httpx.TimeoutException:
            self._record_failure("local")
            raise RuntimeError(f"Error connecting to local model: Timeout ({self.local_timeout_sec} seconds exceeded)")
        except Exception as e:
            self._record_failure("local")
            raise RuntimeError(f"Error connecting to local model: {str(e)}")
