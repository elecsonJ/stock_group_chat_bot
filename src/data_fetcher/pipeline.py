import asyncio
from data_fetcher.fundamental import AdvancedDataFetcher
from data_fetcher.news_scraper import NewsSentimentFetcher
from data_fetcher.macro import MacroFetcher

class MasterDataPipeline:
    def __init__(self, llm_manager):
        self.fundamental_fetcher = AdvancedDataFetcher()
        self.news_fetcher = NewsSentimentFetcher(llm_manager)
        self.macro_fetcher = MacroFetcher()

    async def build_ultimate_fact_sheet(self, tickers: list[str], search_queries: list[str]) -> str:
        """
        주어진 티커 목록과 다중 검색어들에 대해, 
        거시지표 + 주식 펀더멘털 + 기술적 차트 + 거대 뉴스 요약을 
        비동기 병렬로 가져와서 하나의 거대한(하지만 최적화된) Fact-Sheet를 완성합니다.
        """
        
        tasks_fund = [self.fundamental_fetcher.get_comprehensive_stock_data(t) for t in tickers]
        tasks_news = [self.news_fetcher.get_bulk_news_and_summarize(q) for q in search_queries if q.strip()]
        task_macro = self.macro_fetcher.get_macro_environment()
        
        # 병렬 대기
        all_tasks = tasks_fund + [task_macro] + tasks_news
        results = await asyncio.gather(*all_tasks, return_exceptions=True)
        
        fund_results = results[:len(tickers)]
        macro_result = results[len(tickers)]
        news_results = results[len(tickers)+1:]
        
        fact_sheet = "========================================\n"
        fact_sheet += "🌐 **[토론을 위한 공통 Fact-Sheet (Master Data)]**\n"
        fact_sheet += "========================================\n\n"
        
        # 거시 지표
        if macro_result and not isinstance(macro_result, Exception):
            fact_sheet += f"{macro_result}\n"
            
        # 개별 종목 펀더멘털 및 기술적 분석
        if fund_results:
            fact_sheet += "📈 **[개별 주식 정밀 분석 (Fundamental & Technical)]**\n"
            for fr in fund_results:
                if not isinstance(fr, Exception):
                    fact_sheet += f"{fr}\n"
                else:
                    fact_sheet += f"해당 티커 수집 중 오류: {fr}\n"
                    
        # 뉴스 및 센티멘탈 요약 (로컬 모델이 요약한 결과)
        if news_results:
            fact_sheet += "📰 **[심층 웹 리서치 요약 (Local AI Summarized)]**\n"
            for idx, nr in enumerate(news_results):
                if not isinstance(nr, Exception):
                    fact_sheet += f"[검색 {idx+1} 결과]\n{nr}\n"
                else:
                    fact_sheet += f"[검색 {idx+1} 에러]: {nr}\n"
                    
        return fact_sheet
