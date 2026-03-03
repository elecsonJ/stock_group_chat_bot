import asyncio
import os
from duckduckgo_search import DDGS
from bs4 import BeautifulSoup
import httpx
import yfinance as yf
from typing import List, Dict, Any
from datetime import datetime
from urllib.parse import urlparse
import json

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
        self.fetch_concurrency = max(1, int(os.getenv("WEB_FETCH_CONCURRENCY", "4")))
        self.fetch_timeout_sec = max(5, int(os.getenv("WEB_FETCH_TIMEOUT_SEC", "10")))

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
        try:
            query = await self.llm_manager.get_local_response("너는 검색어 추출기야.", prompt)
        except Exception:
            return []
        
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

    async def _search_web_async(self, query: str) -> List[Dict]:
        try:
            return await asyncio.to_thread(self._search_web, query)
        except Exception:
            return []

    def _safe_domain(self, url: str) -> str:
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""

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

    async def _fetch_page_text(self, client: httpx.AsyncClient, url: str) -> str:
        """URL에 접속해 본문 텍스트(BeautifulSoup)를 긁어옴"""
        try:
            resp = await client.get(url, follow_redirects=True)
            soup = BeautifulSoup(resp.content, 'html.parser')
            text = ' '.join([p.text for p in soup.find_all('p')])
            return text[:1500] # 토큰 폭발 방지 (최대 1500자만 리딩)
        except:
            return ""

    async def run_deep_research_package(self, query: str) -> Dict[str, Any]:
        search_results = await self._search_web_async(query)
        generated_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        package: Dict[str, Any] = {
            "query": query,
            "generated_at_utc": generated_at,
            "status": "ok",
            "evidences": [],
            "limitations": [],
        }

        if not search_results:
            package["status"] = "no_results"
            package["limitations"].append("검색 결과가 없어 증거를 생성하지 못했습니다.")
            package["summary"] = f"'{query}'에 대한 최신 검색 결과가 없습니다."
            return package

        semaphore = asyncio.Semaphore(self.fetch_concurrency)

        async def build_evidence(idx: int, r: dict, client: httpx.AsyncClient) -> dict | None:
            url = r.get("href", "")
            if not url:
                return None
            async with semaphore:
                full_text = await self._fetch_page_text(client, url)
            title = (r.get("title", "") or "").strip()
            snippet = (r.get("body", "") or "").strip()
            excerpt = (full_text or "").strip()
            return {
                "evidence_id": f"E{idx}",
                "title": title,
                "url": url,
                "domain": self._safe_domain(url),
                "snippet": snippet[:300],
                "excerpt": excerpt[:450],
                "extraction_method": "ddgs.text + bs4(p tags)",
            }

        async with httpx.AsyncClient(timeout=float(self.fetch_timeout_sec)) as client:
            tasks = [
                build_evidence(idx, r, client)
                for idx, r in enumerate(search_results, 1)
            ]
            fetched = await asyncio.gather(*tasks, return_exceptions=True)

        evidences = []
        for item in fetched:
            if isinstance(item, dict):
                evidences.append(item)

        if not evidences:
            package["status"] = "no_extractable_evidence"
            package["limitations"].append("URL은 있었지만 본문 발췌를 확보하지 못했습니다.")
            package["summary"] = f"'{query}' 검색은 되었으나 본문 기반 증거 생성에 실패했습니다."
            return package

        package["evidences"] = evidences
        if len(evidences) < 3:
            package["limitations"].append("증거 개수가 적어 신뢰도가 낮을 수 있습니다.")

        summary_system_prompt = (
            "너는 수석 리서처야. 아래 JSON 증거 패키지만 근거로 5개 이내 불릿으로 요약해.\n"
            "규칙:\n"
            "1) 없는 사실 추가 금지.\n"
            "2) 각 불릿 말미에 (근거: E번호, 도메인) 표기.\n"
            "3) 마지막에 '검증 상태: 충분/부분/부족' 중 하나와 이유 1문장."
        )
        summary_user_prompt = json.dumps(package, ensure_ascii=False, indent=2)
        try:
            summary = await self.llm_manager.get_local_response(summary_system_prompt, summary_user_prompt)
            package["summary"] = summary
        except Exception:
            package["limitations"].append("로컬 모델 요약 생성 실패: raw evidence만 제공")
            package["summary"] = "로컬 요약 실패로 원시 증거만 제공합니다. 출처 목록을 직접 검토하세요."
        return package

    async def run_deep_research(self, query: str) -> str:
        """
        API 모델이 요청한 특정 검색어에 대해 원문 스크래핑 심층 리서치를 수행하고 요약해 반환.
        """
        package = await self.run_deep_research_package(query)
        if package.get("status") != "ok":
            return package.get("summary", f"'{query}'에 대한 리서치 결과가 부족합니다.")

        source_lines = []
        for ev in package.get("evidences", []):
            source_lines.append(f"- {ev.get('evidence_id')}: {ev.get('title')} ({ev.get('domain')})\n  {ev.get('url')}")

        sources = "\n".join(source_lines[:5])
        summary = package.get("summary", "")
        return (
            f"[Evidence Package]\n"
            f"- query: {package.get('query')}\n"
            f"- generated_at_utc: {package.get('generated_at_utc')}\n"
            f"- evidence_count: {len(package.get('evidences', []))}\n"
            f"- limitations: {package.get('limitations', [])}\n\n"
            f"[요약]\n{summary}\n\n"
            f"[출처 목록]\n{sources}"
        )

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

        try:
            verification_result = await self.llm_manager.get_local_response(system_prompt, user_prompt)
        except Exception as e:
            verification_result = f"로컬 Fact-Check 판독기 오류: {e}"
        
        # 최종 리턴 포맷: 모델의 주장 바로 아래에 이 첨언이 달리게 됩니다.
        links = "\n".join([f"({r['href']})" for r in search_results])
        return f"🔎 **[로컬 Fact-Check 결과 (검색어: {search_query})]**\n{verification_result}\n*참고 링크:\n{links}"
