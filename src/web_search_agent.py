import asyncio
import os
import re
from duckduckgo_search import DDGS
from bs4 import BeautifulSoup
import httpx
import yfinance as yf
from typing import List, Dict, Any
from datetime import datetime
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
import json
from data_fetcher.dart_official import DARTOfficialFetcher
from data_fetcher.krx_kind_official import KRXKindOfficialChecker
from data_fetcher.sec_official import SECOfficialFetcher
from json_utils import parse_json_object
from market_data_provider import MarketDataProvider

class FactCheckAgent:
    def __init__(self, llm_manager):
        """
        AI(GPT, Claude 등)가 생성한 답변(주장)을 입력받아,
        그 주장이 사실인지 웹 검색을 통해 '교차 검토(Fact Check)'하고,
        거짓이거나 근거가 부족하면 반박/보완을 지시하는 에이전트.
        """
        self.ddgs = DDGS()
        self.llm_manager = llm_manager
        self.dart_fetcher = DARTOfficialFetcher()
        self.krx_kind_checker = KRXKindOfficialChecker()
        self.sec_fetcher = SECOfficialFetcher()
        self.market_data = MarketDataProvider()
        self.max_results = 5
        self.fetch_concurrency = max(1, int(os.getenv("WEB_FETCH_CONCURRENCY", "4")))
        self.fetch_timeout_sec = max(5, int(os.getenv("WEB_FETCH_TIMEOUT_SEC", "10")))
        self.search_query_budget = max(1, int(os.getenv("WEB_SEARCH_QUERY_BUDGET", "3")))
        self.official_domains = {
            "sec.gov", "www.sec.gov", "federalreserve.gov", "www.federalreserve.gov",
            "investor.nvidia.com", "ir.tesla.com", "investor.apple.com", "www.apple.com",
        }
        self.company_ir_domains = {
            "investor.nvidia.com", "ir.tesla.com", "investor.apple.com", "investor.microsoft.com",
            "investor.amd.com", "investor.broadcom.com",
        }
        self.trusted_domains = {
            "reuters.com", "www.reuters.com", "nytimes.com", "www.nytimes.com",
            "bloomberg.com", "www.bloomberg.com", "wsj.com", "www.wsj.com",
            "ft.com", "www.ft.com", "marketwatch.com", "www.marketwatch.com",
            "cnbc.com", "www.cnbc.com", "finance.yahoo.com",
        }

    async def _plan_fact_check_searches(self, ai_statement: str, max_queries: int | None = None) -> Dict[str, Any]:
        budget = max(1, int(max_queries or self.search_query_budget))
        system_prompt = (
            "너는 투자 리서치용 claim-to-search 변환기다. 투자 의견을 내지 말고, "
            "웹검증이 필요한 객관 주장만 분해해 검색어로 바꿔라. 반드시 JSON만 출력한다."
        )
        user_prompt = (
            "[입력 주장]\n"
            f"{ai_statement}\n\n"
            "[출력 JSON 형식]\n"
            "{\n"
            '  "claims": ["검증할 객관 주장"],\n'
            '  "search_queries": ["공식/주요 출처 확인에 적합한 검색어"],\n'
            '  "priority": "high|medium|low",\n'
            '  "required_sources": ["regulatory", "company_ir", "tier1_media"],\n'
            '  "reason": "검색어를 이렇게 만든 이유"\n'
            "}\n"
            "규칙:\n"
            "- 검색어는 영어 우선, 필요하면 한국어를 섞어도 된다.\n"
            "- site:sec.gov, investor relations, Reuters 같은 출처 힌트를 적극 사용한다.\n"
            "- 전망/의견은 주장으로 확정하지 말고, 확인 필요한 사실만 분리한다.\n"
            f"- search_queries는 최대 {budget}개."
        )
        try:
            raw = await self.llm_manager.get_local_response(
                system_prompt,
                user_prompt,
                profile="claim_search",
            )
            parsed = parse_json_object(raw) or {}
        except Exception:
            parsed = {}

        queries = []
        for q in parsed.get("search_queries", []) if isinstance(parsed, dict) else []:
            qs = self._clean_search_query(str(q))
            if qs and qs not in queries:
                queries.append(qs)
        if not queries:
            queries = self._fallback_search_queries(ai_statement)

        claims = parsed.get("claims", []) if isinstance(parsed, dict) else []
        return {
            "claims": [str(c).strip() for c in claims if str(c).strip()][:5],
            "search_queries": queries[:budget],
            "priority": str(parsed.get("priority", "medium") if isinstance(parsed, dict) else "medium"),
            "required_sources": parsed.get("required_sources", []) if isinstance(parsed, dict) else [],
            "reason": str(parsed.get("reason", "") if isinstance(parsed, dict) else ""),
            "raw_model_output": raw if "raw" in locals() else "",
        }

    def _clean_search_query(self, query: str) -> str:
        qs = " ".join(str(query or "").replace("\n", " ").split()).strip(" `\"'")
        ql = qs.lower()
        banned = {
            "검색할 구체적인 키워드",
            "반대 관점 키워드",
            "search query",
            "query",
            "keywords",
            "...",
        }
        if not qs or ql in banned or len(qs) < 4:
            return ""
        if "검색할 구체적인 키워드" in ql or "반대 관점 키워드" in ql:
            return ""
        return qs[:220]

    def _fallback_search_queries(self, ai_statement: str) -> List[str]:
        text = " ".join(str(ai_statement or "").split())
        ticker_match = re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b", text)
        ticker = ticker_match[0] if ticker_match else ""
        base = text[:160]
        queries = []
        if ticker:
            queries.extend(
                [
                    f"{ticker} investor relations latest filing guidance",
                    f"{ticker} Reuters latest official confirmation",
                ]
            )
        if base:
            queries.append(base)
        return list(dict.fromkeys(q for q in queries if q))[: self.search_query_budget]

    async def _extract_search_queries(self, ai_statement: str) -> List[str]:
        """
        1단계: AI의 긴 주장에서 '팩트체크가 필요한 핵심 키워드/문장'을 구조화된 검색어 형태로 추출
        (로컬 LLM에게 이 작업을 맡기면 가장 안전하고 똑똑함)
        """
        plan = await self._plan_fact_check_searches(ai_statement, max_queries=2)
        return plan.get("search_queries", [])[:2]

    def _search_web(self, query: str) -> List[Dict]:
        """2단계: 추출된 검색어로 실제 최신 웹 검색"""
        results = []
        try:
            search_results = self.ddgs.text(query, max_results=self.max_results)
            for r in search_results:
                href = self._normalize_url(r.get("href", ""))
                title = (r.get("title", "") or "").strip()
                body = (r.get("body", "") or "").strip()
                if not href or not title:
                    continue
                results.append(
                    {
                        "title": title,
                        "href": href,
                        "body": body,
                        "domain": self._safe_domain(href),
                        "source_quality": self._domain_quality(href),
                        "source_tier": self._domain_tier(href),
                    }
                )
        except Exception:
            pass
        dedup = {}
        for item in results:
            key = item.get("href", "")
            if not key:
                continue
            prev = dedup.get(key)
            if prev is None or item.get("source_quality", 0) > prev.get("source_quality", 0):
                dedup[key] = item
        ranked = sorted(
            dedup.values(),
            key=lambda x: (int(x.get("source_quality", 0)), len(str(x.get("body", "")))),
            reverse=True,
        )
        return ranked[: self.max_results]

    async def _search_web_async(self, query: str) -> List[Dict]:
        try:
            return await asyncio.to_thread(self._search_web, query)
        except Exception:
            return []

    async def _search_multiple_queries(self, queries: list[str]) -> list[dict]:
        tasks = [self._search_web_async(q) for q in queries if q]
        if not tasks:
            return []
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        dedup: dict[str, dict] = {}
        for batch in batches:
            if not isinstance(batch, list):
                continue
            for item in batch:
                if not isinstance(item, dict):
                    continue
                href = item.get("href", "")
                if not href:
                    continue
                prev = dedup.get(href)
                if prev is None or int(item.get("source_quality", 0) or 0) > int(prev.get("source_quality", 0) or 0):
                    dedup[href] = item
        ranked = sorted(
            dedup.values(),
            key=lambda x: (int(x.get("source_quality", 0)), len(str(x.get("body", "")))),
            reverse=True,
        )
        return ranked[: self.max_results]

    def _safe_domain(self, url: str) -> str:
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""

    def _normalize_url(self, url: str) -> str:
        try:
            parsed = urlparse(str(url or "").strip())
            query = []
            for key, value in parse_qsl(parsed.query, keep_blank_values=True):
                if key.lower().startswith("utm_"):
                    continue
                query.append((key, value))
            parsed = parsed._replace(query=urlencode(query), fragment="")
            return urlunparse(parsed)
        except Exception:
            return str(url or "").strip()

    def _domain_quality(self, url: str) -> int:
        domain = self._safe_domain(url)
        if domain in self.official_domains or domain.endswith(".gov"):
            return 4
        if domain in self.company_ir_domains:
            return 3
        if domain in self.trusted_domains:
            return 2
        if domain:
            return 1
        return 0

    def _domain_tier(self, url: str) -> str:
        domain = self._safe_domain(url)
        if domain in self.official_domains or domain.endswith(".gov"):
            return "regulatory"
        if domain in self.company_ir_domains:
            return "company_ir"
        if domain in self.trusted_domains:
            return "tier1_media"
        if domain:
            return "secondary"
        return "unknown"

    async def get_stock_data(self, ticker: str) -> str:
        """공식 공시 데이터 우선 + yfinance 보조 시장 데이터를 가져옴."""
        official_text = await self._get_official_company_text(ticker)
        try:
            instrument = self.market_data.resolve_instrument(ticker)
            provider_ticker = instrument.provider_ticker if instrument else str(ticker or "").upper().strip()
            def fetch_data():
                stock = yf.Ticker(provider_ticker)
                return stock.info
            info = await asyncio.to_thread(fetch_data)
            
            c_price = info.get("currentPrice") or info.get("regularMarketPrice", "N/A")
            currency = info.get("currency") or info.get("financialCurrency") or "N/A"
            f_pe = info.get("forwardPE", "N/A")
            t_pe = info.get("trailingPE", "N/A")
            m_cap = info.get("marketCap", "N/A")
            high52 = info.get("fiftyTwoWeekHigh", "N/A")
            low52 = info.get("fiftyTwoWeekLow", "N/A")
            
            market_text = (
                f"[{ticker} 비공식 시장 데이터: yfinance/Yahoo Finance]\n"
                f"주의: 공식 공시/브로커 호가가 아니므로 실제 투자 전 별도 확인 필요\n"
                f"현재가: {c_price} {currency}\n시가총액: {m_cap} {currency}\n"
                f"52주 최고/최저: {high52} / {low52}\n"
                f"Trailing PER: {t_pe} / Forward PER: {f_pe}"
            )
            return f"{official_text}\n{market_text}"
        except Exception as e:
            return f"{official_text}\n[{ticker} yfinance 보조 데이터 조회 실패: {str(e)}]"

    async def _get_official_company_text(self, ticker: str) -> str:
        sections = []
        if self.sec_fetcher.is_supported_ticker(ticker):
            sections.append(await asyncio.to_thread(self.sec_fetcher.render_official_fact_sheet, ticker))
        if self.dart_fetcher.is_supported_ticker(ticker):
            sections.append(await asyncio.to_thread(self.dart_fetcher.render_official_fact_sheet, ticker))
        if self.krx_kind_checker.is_supported_ticker(ticker):
            sections.append(self.krx_kind_checker.render_market_integrity_note(ticker))
        if sections:
            return "\n".join(sections)
        label = str(ticker or "").strip().upper()
        return (
            f"**[공식 기업 데이터: {label}]**\n"
            "- 현재 내장 공식 provider(SEC EDGAR/OpenDART)로 지원되지 않는 티커입니다.\n"
            "- 실제 투자 전 해당 거래소, 감독기관 공시, 기업 IR, 브로커 원장을 별도 확인해야 합니다.\n"
        )

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
        search_plan = await self._plan_fact_check_searches(query)
        search_queries = search_plan.get("search_queries", []) or [query]
        search_results = await self._search_multiple_queries(search_queries)
        generated_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        package: Dict[str, Any] = {
            "query": query,
            "search_plan": {
                "claims": search_plan.get("claims", []),
                "search_queries": search_queries,
                "priority": search_plan.get("priority", "medium"),
                "required_sources": search_plan.get("required_sources", []),
                "reason": search_plan.get("reason", ""),
            },
            "generated_at_utc": generated_at,
            "status": "ok",
            "evidences": [],
            "limitations": [],
        }

        if not search_results:
            package["status"] = "no_results"
            package["limitations"].append("검색 결과가 없어 증거를 생성하지 못했습니다.")
            package["summary"] = f"'{query}'에 대한 최신 검색 결과가 없습니다."
            package["verdict"] = self._fallback_evidence_verdict(package)
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
                "source_quality": r.get("source_quality", 0),
                "source_tier": r.get("source_tier", "unknown"),
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
            package["verdict"] = self._fallback_evidence_verdict(package)
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
            summary = await self.llm_manager.get_local_response(summary_system_prompt, summary_user_prompt, profile="evidence")
            package["summary"] = summary
        except Exception:
            package["limitations"].append("로컬 모델 요약 생성 실패: raw evidence만 제공")
            package["summary"] = "로컬 요약 실패로 원시 증거만 제공합니다. 출처 목록을 직접 검토하세요."
        package["verdict"] = await self._build_evidence_verdict(package)
        return package

    def _fallback_evidence_verdict(self, package: dict[str, Any]) -> dict[str, Any]:
        evidences = package.get("evidences", []) if isinstance(package, dict) else []
        source_tiers = [str(e.get("source_tier", "")) for e in evidences if isinstance(e, dict)]
        high_quality = any(t in {"regulatory", "company_ir", "tier1_media"} for t in source_tiers)
        status = "usable" if len(evidences) >= 2 and high_quality else "insufficient"
        missing = []
        if not high_quality:
            missing.append("official_or_tier1_source")
        if len(evidences) < 2:
            missing.append("independent_confirmation")
        return {
            "status": status,
            "ready_for_debate": bool(evidences),
            "ready_for_signal": status == "usable",
            "verified_claims": [],
            "partially_supported_claims": [],
            "unsupported_claims": [],
            "contradictions": [],
            "best_sources": [
                {"domain": e.get("domain"), "tier": e.get("source_tier"), "evidence_id": e.get("evidence_id")}
                for e in evidences[:5]
                if isinstance(e, dict)
            ],
            "missing_evidence": missing,
            "allowed_use": "signal_candidate" if status == "usable" else "discussion_only",
            "confidence": 0.45 if status == "usable" else 0.2,
        }

    async def _build_evidence_verdict(self, package: dict[str, Any]) -> dict[str, Any]:
        system_prompt = (
            "너는 투자 리서치용 evidence 판정기다. 투자 결론/매수/매도 의견은 금지한다. "
            "아래 evidence JSON만 근거로 무엇이 확인/부분확인/미확인/충돌인지 JSON으로 판정하라."
        )
        user_prompt = (
            "[Evidence Package]\n"
            f"{json.dumps(package, ensure_ascii=False, indent=2)}\n\n"
            "[출력 JSON 형식]\n"
            "{\n"
            '  "status": "strong|usable|insufficient|contradictory",\n'
            '  "ready_for_debate": true,\n'
            '  "ready_for_signal": false,\n'
            '  "verified_claims": [],\n'
            '  "partially_supported_claims": [],\n'
            '  "unsupported_claims": [],\n'
            '  "contradictions": [],\n'
            '  "best_sources": [{"evidence_id": "E1", "domain": "...", "tier": "..."}],\n'
            '  "missing_evidence": [],\n'
            '  "recommended_next_searches": [],\n'
            '  "allowed_use": "debate_and_signal|discussion_only|needs_refresh",\n'
            '  "confidence": 0.0\n'
            "}\n"
            "규칙:\n"
            "- 공식/IR/주요언론 근거가 직접 claim을 받칠 때만 ready_for_signal=true.\n"
            "- 검색 실패나 근거 부족은 실패가 아니라 missing_evidence로 명시.\n"
            "- evidence에 없는 사실을 추가하지 말 것."
        )
        try:
            raw = await self.llm_manager.get_local_response(
                system_prompt,
                user_prompt,
                profile="evidence_verdict",
            )
            parsed = parse_json_object(raw) or {}
        except Exception:
            parsed = {}
        fallback = self._fallback_evidence_verdict(package)
        if not isinstance(parsed, dict) or not parsed:
            return fallback
        return {
            "status": str(parsed.get("status") or fallback["status"]),
            "ready_for_debate": bool(parsed.get("ready_for_debate", fallback["ready_for_debate"])),
            "ready_for_signal": bool(parsed.get("ready_for_signal", fallback["ready_for_signal"])),
            "verified_claims": parsed.get("verified_claims", []) if isinstance(parsed.get("verified_claims", []), list) else [],
            "partially_supported_claims": parsed.get("partially_supported_claims", []) if isinstance(parsed.get("partially_supported_claims", []), list) else [],
            "unsupported_claims": parsed.get("unsupported_claims", []) if isinstance(parsed.get("unsupported_claims", []), list) else [],
            "contradictions": parsed.get("contradictions", []) if isinstance(parsed.get("contradictions", []), list) else [],
            "best_sources": parsed.get("best_sources", []) if isinstance(parsed.get("best_sources", []), list) else fallback["best_sources"],
            "missing_evidence": parsed.get("missing_evidence", []) if isinstance(parsed.get("missing_evidence", []), list) else fallback["missing_evidence"],
            "recommended_next_searches": parsed.get("recommended_next_searches", []) if isinstance(parsed.get("recommended_next_searches", []), list) else [],
            "allowed_use": str(parsed.get("allowed_use") or fallback["allowed_use"]),
            "confidence": float(parsed.get("confidence", fallback["confidence"]) or 0.0),
        }

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
            f"- search_queries: {(package.get('search_plan') or {}).get('search_queries', [])}\n"
            f"- generated_at_utc: {package.get('generated_at_utc')}\n"
            f"- evidence_count: {len(package.get('evidences', []))}\n"
            f"- limitations: {package.get('limitations', [])}\n\n"
            f"[로컬 Evidence Verdict]\n{json.dumps(package.get('verdict', {}), ensure_ascii=False, indent=2)}\n\n"
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
            verification_result = await self.llm_manager.get_local_response(system_prompt, user_prompt, profile="judge")
        except Exception as e:
            verification_result = f"로컬 Fact-Check 판독기 오류: {e}"
        
        # 최종 리턴 포맷: 모델의 주장 바로 아래에 이 첨언이 달리게 됩니다.
        links = "\n".join([f"({r['href']})" for r in search_results])
        return f"🔎 **[로컬 Fact-Check 결과 (검색어: {search_query})]**\n{verification_result}\n*참고 링크:\n{links}"
