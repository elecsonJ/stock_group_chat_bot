from duckduckgo_search import DDGS
import warnings

# duckduckgo_search 패키지 이름 변경 경고 숨기기
warnings.filterwarnings('ignore', category=RuntimeWarning, module='duckduckgo_search')

class NewsSentimentFetcher:
    def __init__(self, llm_manager):
        # llm_manager는 로컬 모델(1차 필터링)로 활용하기 위해 주입받습니다.
        self.llm = llm_manager

    async def get_bulk_news_and_summarize(self, keyword: str) -> str:
        """
        1. 티커(키워드) 관련 최신 뉴스 15~20개를 긁어모읍니다.
        2. 로컬 모델을 통해 '핵심 호재 3개, 악재 3개'로 요약하여 리턴합니다.
        """
        def fetch_sync():
            texts = []
            try:
                # 동기 방식의 DDGS를 스레드 풀에서 실행 (버전 호환성 확보)
                with DDGS() as ddgs:
                    results = list(ddgs.news(keyword, max_results=20))
                for res in results:
                    title = res.get('title', '')
                    body = res.get('body', '')
                    texts.append(f"- 제목: {title}\n  내용: {body}")
            except Exception as e:
                texts.append(f"ERROR: {e}")
            return texts

        # 비동기 블로킹 방지
        import asyncio
        news_texts = await asyncio.to_thread(fetch_sync) # type: ignore
        
        if not news_texts or (len(news_texts) == 1 and news_texts[0].startswith("ERROR:")):
            err_msg = news_texts[0] if news_texts else "결과 없음"
            return f"[{keyword}] 관련된 최근 뉴스가 없습니다. ({err_msg})\n"

        # 수집된 원본 뉴스들
        raw_news_corpus = "\n".join(news_texts)
        
        # 로컬 모델(gpt-oss-20b)에게 요약 및 감성 분석 지시
        # 이 과정이 컨텍스트 비용을 획기적으로 줄여줍니다.
        sys_prompt = (
            "너는 월스트리트의 정보 분석가야. 아래 제공된 20여 개의 최신 뉴스 헤드라인과 요약을 읽고, "
            "이 종목의 현재 가장 큰 방향성을 보여주는 리포트를 작성해. "
            "반드시 아래 포맷을 지켜서 작성할 것:\n"
            "1. 🟢 핵심 호재 (최대 3개, 요약)\n"
            "2. 🔴 핵심 악재 (최대 3개, 요약)\n"
            "3. 🌐 전반적인 시장 심리 (1문장: 강한 매수/매수/중립/매도/강한 매도 중 택1 및 이유)"
        )
        
        try:
            summary_report = await self.llm.get_local_response(sys_prompt, raw_news_corpus)
            final_text = (
                f"**[📰 종목: {keyword} 대규모 뉴스 분석 결과]**\n"
                f"{summary_report}\n"
            )
            return final_text
        except Exception as e:
            return f"[{keyword}] 로컬 모델 뉴스 요약 실패: {e}\n"
