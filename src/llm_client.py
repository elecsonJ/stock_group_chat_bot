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
        self.openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        # Anthropic (Claude)
        self.anthropic_client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        # Google (Gemini) - 최신 SDK 유지 (google.genai)
        self.gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        
        self.models = {
            "gpt": "gpt-5.2-2025-12-11", # 사용자가 제시한 작동 가능한 대화형 모델
            "claude": "claude-sonnet-4-6", # 2026 실제 지원 API Endpoint
            "gemini_primary": os.getenv("GEMINI_PRIMARY_MODEL", "gemini-3.1-pro-preview"),
            "gemini_fallback": os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3-flash-preview"),
            "local": "gpt-oss:20b" # 16GB VRAM 환경의 최고 속도/지능 토론 판사
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
            
    async def get_local_response(self, system_prompt: str, user_prompt: str) -> str:
        """
        Ollama 등에 띄워진 로컬 모델을 호출하는 간단한 REST API 인터페이스
        """
        is_open, remain = self._is_circuit_open("local")
        if is_open:
            raise RuntimeError(f"Error connecting to local model: CircuitOpen ({remain}s)")
        # 만약 .env에 과거 generate url이 있다면 강제로 chat url로 변환합니다.
        local_url = os.getenv("LOCAL_OLLAMA_URL", "http://localhost:11434/api/chat")
        if "api/generate" in local_url:
            local_url = local_url.replace("api/generate", "api/chat")
            
        if "json" not in system_prompt.lower() and "출력기" not in system_prompt:
            system_prompt += "\n반드시 한국어(Korean)로 대답해."
            
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                payload = {
                    "model": self.models["local"],
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
                else:
                    raise RuntimeError(f"Local Model Error: HTTP {response.status_code}\n{response.text}")
        except httpx.TimeoutException:
            self._record_failure("local")
            raise RuntimeError("Error connecting to local model: Timeout (600 seconds exceeded)")
        except Exception as e:
            self._record_failure("local")
            raise RuntimeError(f"Error connecting to local model: {str(e)}")
