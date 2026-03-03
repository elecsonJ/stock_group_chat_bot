import os
import asyncio
from dotenv import load_dotenv

# .env 파일에서 환경 변수 로드
load_dotenv()

async def test_gemini_api():
    try:
        from google import genai
        from google.genai import types
    except Exception as e:
        print(f"❌ google-genai 패키지 로드 실패: {e}")
        print("   먼저 `pip install -r requirements.txt` 를 실행하세요.")
        return

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ 오류: .env 파일에 GEMINI_API_KEY가 설정되어 있지 않습니다.")
        return

    print("🔑 GEMINI_API_KEY가 감지되었습니다. 연결을 시도합니다...")
    
    # 1. API 키 설정 (2026년 기준 google.genai SDK 호출 방식)
    client = genai.Client(api_key=api_key)
    
    # 2. 모델 선택 (gemini-3-flash-preview)
    model_name = "gemini-3-flash-preview"
    # 만약 구버전을 테스트하고 싶다면 아래 주석을 해제하세요.
    # model_name = "gemini-3.1-pro-preview" 

    try:
        print(f"🔄 '{model_name}' 모델 응답을 요청합니다...")
        # 3. 테스트 프롬프트 전송
        test_prompt = "안녕 제미나이! 네가 정상적으로 응답 가능한지 테스트 중이야. 짧게 1~2문장으로 한국어로 답변해줘."
        print(f"📤 보내는 프롬프트: {test_prompt}")
        
        def _fetch():
            return client.models.generate_content(
                model=model_name,
                contents=test_prompt,
                config=types.GenerateContentConfig(
                    system_instruction="반드시 한국어로만 1~2문장 답변해."
                ),
            )

        # 스레드로 감싸 비동기 타임아웃 제어
        response = await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=20.0)
        
        print("\n✅ [테스트 성공] Gemini 응답이 도착했습니다!")
        print("-" * 50)
        print(response.text)
        print("-" * 50)
        
    except asyncio.TimeoutError:
        print("\n❌ [오류] 응답 지연 (Timeout): 20초 이내에 답변을 받지 못했습니다.")
        print("API 키 한도 초과(Rate Limit) 문제이거나 해외 모델 접속 오류일 가능성이 높습니다.")
    except Exception as e:
        print(f"\n❌ [오류] 예외 발생: {str(e)}")
        print("API 키가 올바르지 않거나 결제/인증 문제가 있을 수 있습니다.")

if __name__ == "__main__":
    # Windows/Python 비동기 루프 강제 실행
    asyncio.run(test_gemini_api())
