import asyncio
import os
import sys
from dotenv import load_dotenv

# 스크립트 실행 경로를 src 디렉토리로 맞추기 위한 경로 설정
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

# .env 파일 로드 (로컬 모델 및 API 등 환경 변수)
load_dotenv()

# 우리가 만든 모듈들 가져오기
from data_fetcher.pipeline import MasterDataPipeline
from llm_client import LLMClientManager

async def test_data_pipeline():
    print("==================================================")
    print("1. [테스트 초기화] 파이프라인 엔진을 조립합니다...")
    # 실제 로컬/API 모델 클라이언트 매니저 생성 (뉴스 요약에 로컬 모델 필요)
    llm_manager = LLMClientManager()
    
    # 마스터 파이프라인 생성
    pipeline = MasterDataPipeline(llm_manager)
    print("✅ 데이터 파이프라인 조립 완료.")
    
    # 테스트할 종목 티커 및 뉴스 검색어 설정
    tickers_to_test = ["TSLA", "NVDA"]
    search_query = "Tesla Nvidia earnings and market outlook"
    
    print("\n2. [데이터 수집 시작] 거시경제, 펀더멘털, 딥 뉴스 스크래핑 및 요약을 병렬로 수행합니다...")
    print(f"👉 대상 티커: {tickers_to_test}")
    print(f"👉 뉴스 검색어: '{search_query}'")
    
    try:
        # 이 함수 하나로 3가지 작업(매크로, 개별주식, 뉴스 대량수집+요약)이 동시에 굴러갑니다.
        final_fact_sheet = await pipeline.build_ultimate_fact_sheet(
            tickers=tickers_to_test, 
            search_query=search_query
        )
        
        print("\n==================================================")
        print("🎉 [테스트 성공] 완성된 마스터 Fact-Sheet 출력:")
        print("==================================================")
        print(final_fact_sheet)
        print("==================================================")
        
    except Exception as e:
        print(f"\n❌ [오류 발생] 데이터 수집 중 문제가 생겼습니다:\n{e}")

if __name__ == "__main__":
    # 윈도우 환경에서 asyncio Event Loop Policy 오류 방지
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    asyncio.run(test_data_pipeline())
