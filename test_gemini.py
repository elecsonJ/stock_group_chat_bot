import os
import asyncio
import google.generativeai as genai
from dotenv import load_dotenv

# .env 파일에서 환경 변수 로드
load_dotenv()

async def test_gemini_api():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ 오류: .env 파일에 GEMINI_API_KEY가 설정되어 있지 않습니다.")
        return

    print("🔑 GEMINI_API_KEY가 감지되었습니다. 연결을 시도합니다...")
    
    # 1. API 키 설정 (2026년 기준 gemini sdk 호출 방식 검증)
    genai.configure(api_key=api_key)
    
    # 2. 모델 선택 (gemini-3-flash-preview)
    model_name = "gemini-3-flash-preview"
    # 만약 구버전을 테스트하고 싶다면 아래 주석을 해제하세요.
    # model_name = "gemini-3.1-pro-preview" 

    try:
        print(f"🔄 '{model_name}' 모델을 초기화 중입니다...")
        model = genai.GenerativeModel(model_name)
        
        # 3. 테스트 프롬프트 전송
        test_prompt = "안녕 제미나이! 네가 정상적으로 응답 가능한지 테스트 중이야. 짧게 1~2문장으로 한국어로 답변해줘."
        print(f"📤 보내는 프롬프트: {test_prompt}")
        
        # 비동기 생성 호출 (10초 타임아웃)
        response = await asyncio.wait_for(
            model.generate_content_async(test_prompt), 
            timeout=10.0
        )
        
        print("\n✅ [테스트 성공] Gemini 응답이 도착했습니다!")
        print("-" * 50)
        print(response.text)
        print("-" * 50)
        
    except asyncio.TimeoutError:
        print("\n❌ [오류] 응답 지연 (Timeout): 10초 이내에 답변을 받지 못했습니다.")
        print("API 키 한도 초과(Rate Limit) 문제이거나 해외 모델 접속 오류일 가능성이 높습니다.")
    except Exception as e:
        print(f"\n❌ [오류] 예외 발생: {str(e)}")
        print("API 키가 올바르지 않거나 결제/인증 문제가 있을 수 있습니다.")

if __name__ == "__main__":
    # Windows/Python 비동기 루프 강제 실행
    asyncio.run(test_gemini_api())
