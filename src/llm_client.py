import os
import httpx
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from google import genai
from google.genai import types
from dotenv import load_dotenv
import re

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
            "gemini": "gemini-3-flash-preview", # 안정성 및 속도를 위해 flash 모델로 내림
            "local": "gpt-oss:20b" # 16GB VRAM 환경의 최고 속도/지능 토론 판사
        }

    async def get_gpt_response(self, system_prompt: str, user_prompt: str) -> str:
        import asyncio
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
                return response.choices[0].message.content
            except Exception as e:
                if attempt == 2:
                    return f"Error from GPT: {str(e)}"
                await asyncio.sleep(2 ** attempt)

    async def get_claude_response(self, system_prompt: str, user_prompt: str) -> str:
        import asyncio
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
                return response.content[0].text
            except Exception as e:
                if attempt == 2:
                    return f"Error from Claude: {str(e)}"
                await asyncio.sleep(2 ** attempt)

    async def get_gemini_response(self, system_prompt: str, user_prompt: str) -> str:
        import asyncio
        from google.genai import types
        
        def _fetch():
            return self.gemini_client.models.generate_content(
                model=self.models["gemini"],
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                )
            )
            
        for attempt in range(3):
            try:
                response = await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=60.0) # type: ignore
                return response.text
            except asyncio.TimeoutError:
                if attempt == 2:
                    return "Error from Gemini: Timeout (60 seconds exceeded)"
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                if attempt == 2:
                    return f"Error from Gemini: {str(e)}"
                await asyncio.sleep(2 ** attempt)
            
    async def get_local_response(self, system_prompt: str, user_prompt: str) -> str:
        """
        Ollama 등에 띄워진 로컬 모델을 호출하는 간단한 REST API 인터페이스
        """
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
                    return ans
                else:
                    return f"Local Model Error: HTTP {response.status_code}\n{response.text}"
        except httpx.TimeoutException:
            return f"Error connecting to local model: Timeout (600 seconds exceeded)"
        except Exception as e:
            return f"Error connecting to local model: {str(e)}"
