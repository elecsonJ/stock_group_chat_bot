import asyncio
from duckduckgo_search import DDGS
from bs4 import BeautifulSoup
import httpx
import yfinance as yf
from typing import List, Dict

class FactCheckAgent:
    def __init__(self, llm_manager):
        """
        AI(GPT, Claude 등)가 생성한 답변(주장)을 입력받아,
        그 주장이 사실인지 웹 검색을 통해 '교차 검토(Fact Check)'하고,
        거짓이거나 근거가 부족하면 반박/보완을 지시하는 에이전트.
        """
        self.ddgs = DDGS()
        self.llm_manager = llm_manager
        self.max_results = 5 

    async def _extract_search_queries(self, ai_statement: str) -> List[str]:
        """
        1단계: AI의 긴 주장에서 '팩트체크가 필요한 핵심 키워드/문장'을 구조화된 검색어 형태로 추출
        (로컬 LLM에게 이 작업을 맡기면 가장 안전하고 똑똑함)
        """
        prompt = (
            "다음 [문장]에서 웹 검색에 사용할 가장 핵심적인 명사 키워드 딱 1~2개만 추출해줘.\n"
            "예시1) '최근 1주일간 미국 연준 금리 인하 관련 기사 분석해' -> '연준 금리인하'\n"
            "예시2) '테슬라와 BYD의 시장 점유율 비교' -> '테슬라 BYD 점유율'\n"
            "절대 문장형태로 대답하지 말고, 특수문자나 안내 멘트 없이 검색창에 입력할 수 있는 단답형 키워드만 달랑 하나 출력해.\n"
            f"[문장]: {ai_statement}"
        )
        # 로컬 모델(gpt-oss-20b)에게 추출을 시킴
        query = await self.llm_manager.get_local_response("너는 검색어 추출기야.", prompt)
        
        # 모델 연결 실패 에러가 검색어로 들어가는 것을 방지
        if not query or "Error connecting" in query or "Local Model Error" in query:
            return []
            
        # 만약 모델이 말을 길게 하면 첫 줄만 쓴다든지 하는 정제 과정
        clean_query = query.split('\n')[0].strip(' "\'')
        return [clean_query]

    def _search_web(self, query: str) -> List[Dict]:
        """2단계: 추출된 검색어로 실제 최신 웹 검색"""
        results = []
        try:
            search_results = self.ddgs.text(query, max_results=self.max_results)
            for r in search_results:
                results.append({"title": r.get("title", ""), "href": r.get("href", ""), "body": r.get("body", "")})
        except Exception as e:
            pass
        return results

    async def get_stock_data(self, ticker: str) -> str:
        """yfinance를 통해 실시간 주가 및 재무 데이터를 가져옴"""
        try:
            def fetch_data():
                stock = yf.Ticker(ticker)
                return stock.info
            info = await asyncio.to_thread(fetch_data)
            
            c_price = info.get("currentPrice") or info.get("regularMarketPrice", "N/A")
            f_pe = info.get("forwardPE", "N/A")
            t_pe = info.get("trailingPE", "N/A")
            m_cap = info.get("marketCap", "N/A")
            high52 = info.get("fiftyTwoWeekHigh", "N/A")
            low52 = info.get("fiftyTwoWeekLow", "N/A")
            
            return (f"[{ticker} 최신 주식 데이터]\n"
                    f"현재가: {c_price}\n시가총액: {m_cap}\n"
                    f"52주 최고/최저: {high52} / {low52}\n"
                    f"Trailing PER: {t_pe} / Forward PER: {f_pe}")
        except Exception as e:
            return f"{ticker} 주식 데이터 조회 실패: {str(e)}"

    async def _fetch_page_text(self, url: str) -> str:
        """URL에 접속해 본문 텍스트(BeautifulSoup)를 긁어옴"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, follow_redirects=True)
                soup = BeautifulSoup(resp.content, 'html.parser')
                text = ' '.join([p.text for p in soup.find_all('p')])
                return text[:1500] # 토큰 폭발 방지 (최대 1500자만 리딩)
        except:
            return ""

    async def run_deep_research(self, query: str) -> str:
        """
        API 모델이 요청한 특정 검색어에 대해 원문 스크래핑 심층 리서치를 수행하고 요약해 반환.
        """
        search_results = self._search_web(query)
        if not search_results:
            return f"'{query}'에 대한 최신 검색 결과가 없습니다. 기존 지식 기반으로 판단하세요."
            
        context_blocks = []
        for r in search_results:
            url = r.get('href', '')
            if url:
                full_text = await self._fetch_page_text(url)
                block = f"- 제목: {r.get('title', '')}\n  요약: {r.get('body', '')}\n  원문 발췌: {full_text}"
                context_blocks.append(block)
            
        context_str = "\n\n".join(context_blocks)
        
        system_prompt = (
            "너는 수석 리서처야. 주어진 [웹 검색 결과](제목, 요약, 원문 발췌)를 분석하여 토론자들에게 제공할 '심층 리서치 레포트'를 작성해.\n"
            "단순 요약을 넘어서, 주식 투자에 영향을 미칠 수 있는 구체적인 수치, 핵심 악재/호재 트렌드, 애널리스트의 논리를 구조화된 글(불릿 포인트 활용)로 명확하게 제시해. 길이는 600자 내외로 작성해."
        )
        user_prompt = f"[검색 키워드: {query}]\n\n[웹 검색 결과]\n{context_str}\n\n위 내용을 분석하여 심층 리서치 레포트를 작성해줘:"
        
        summary = await self.llm_manager.get_local_response(system_prompt, user_prompt)
        return summary

    async def verify_statement(self, ai_name: str, ai_statement: str) -> str:
        """
        3단계: 
        API가 말한 주장(`ai_statement`)을 로컬 모델이 웹과 비교 후 '승인'할지 '반박'할지 결정
        """
        # 1. 주장에서 검색어 추출
        queries = await self._extract_search_queries(ai_statement)
        if not queries or not queries[0]:
            return "검색어 추출 실패로 팩트체크를 건너뜁니다."
            
        search_query = queries[0]
        
        # 2. 웹 검색
        search_results = self._search_web(search_query)
        if not search_results:
            return f"[{ai_name}의 주장]에 대한 최신 교차 검증 데이터를 웹에서 찾을 수 없습니다."

        context_blocks = "\n".join([f"- {r['title']}: {r['body']}" for r in search_results])
        
        # 3. 로컬 판독기 (gpt-oss-20b)
        system_prompt = (
            "너는 수석 팩트체커(Fact Checker)야. [AI의 주장]에 등장하는 통계, 날짜, 고유명사 등 '객관적 사실'이 [웹 검색 결과]와 정면으로 모순될 때만 '거짓(False)'이라고 지적해.\n"
            "AI가 단순한 '전망'이나 '투자 분석 의견'을 말한 것이라면, 검색 결과와 단어가 일치하지 않더라도 '거짓'이라고 판단하지 말고 '의견/분석(Opinion)'으로 분류해.\n"
            "즉, 웹 검색에 안 나온다고 거짓이 아니야. 명백한 사실 오류가 있을 때만 지적해. 3문장 이내로 답변해."
        )
        
        user_prompt = (
            f"[AI의 주장]\n{ai_statement}\n\n"
            f"[최신 웹 검색 결과]\n{context_blocks}\n\n"
            f"검증 결과 및 이유를 서술해:"
        )

        verification_result = await self.llm_manager.get_local_response(system_prompt, user_prompt)
        
        # 최종 리턴 포맷: 모델의 주장 바로 아래에 이 첨언이 달리게 됩니다.
        links = "\n".join([f"({r['href']})" for r in search_results])
        return f"🔎 **[로컬 Fact-Check 결과 (검색어: {search_query})]**\n{verification_result}\n*참고 링크:\n{links}"
