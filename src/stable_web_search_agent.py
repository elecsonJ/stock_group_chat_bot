import asyncio
import json
import re
from datetime import datetime
from typing import Any, Dict, List

import httpx

from json_utils import parse_json_object
from web_search_agent import FactCheckAgent as BaseFactCheckAgent


class FactCheckAgent(BaseFactCheckAgent):
    """Stable local-LLM research layer over the legacy web search collector."""

    async def _plan_fact_check_searches(self, ai_statement: str, max_queries: int | None = None) -> Dict[str, Any]:
        budget = max(1, int(max_queries or self.search_query_budget))
        system_prompt = (
            "You are a claim-to-search planner for investment research. "
            "Do not make investment conclusions. Split the input into checkable factual claims "
            "and return only strict JSON."
        )
        user_prompt = (
            "[Input claim]\n"
            f"{ai_statement}\n\n"
            "[Required JSON]\n"
            "{\n"
            '  "claims": ["checkable factual claim"],\n'
            '  "search_queries": ["query optimized for official or tier-1 sources"],\n'
            '  "priority": "high|medium|low",\n'
            '  "required_sources": ["regulatory", "company_ir", "tier1_media"],\n'
            '  "reason": "why these searches are needed"\n'
            "}\n"
            "Rules:\n"
            "- Prefer English search queries; add Korean terms only if the claim is Korea-specific.\n"
            "- Use source hints such as site:sec.gov, investor relations, Reuters, company press release, or exchange filing when useful.\n"
            "- Separate facts from forecasts/opinions. Search only for facts that need verification.\n"
            f"- Return at most {budget} search_queries.\n"
            "- Avoid placeholders such as 'search query' or 'keyword'."
        )
        raw = ""
        parsed: dict[str, Any] = {}
        try:
            raw = await self.llm_manager.get_local_response(
                system_prompt,
                user_prompt,
                profile="claim_search",
            )
            candidate = parse_json_object(raw) or {}
            if isinstance(candidate, dict):
                parsed = candidate
        except Exception:
            parsed = {}

        queries: list[str] = []
        for q in parsed.get("search_queries", []):
            qs = self._clean_search_query(str(q))
            if qs and qs not in queries:
                queries.append(qs)
        if not queries:
            queries = self._fallback_search_queries(ai_statement)

        def _text_list(value: Any) -> list[str]:
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            if isinstance(value, str) and value.strip():
                return [value.strip()]
            return []

        claims = _text_list(parsed.get("claims", []))
        sources = _text_list(parsed.get("required_sources", []))
        return {
            "claims": claims[:5],
            "search_queries": queries[:budget],
            "priority": str(parsed.get("priority", "medium") or "medium"),
            "required_sources": sources[:5],
            "reason": str(parsed.get("reason", "") or ""),
            "raw_model_output": raw,
        }

    def _clean_search_query(self, query: str) -> str:
        qs = " ".join(str(query or "").replace("\n", " ").split()).strip(" `\"'")
        ql = qs.lower()
        banned = {"search query", "query", "keywords", "keyword", "n/a", "none", "..."}
        if not qs or ql in banned or len(qs) < 4:
            return ""
        if "placeholder" in ql or "specific keyword" in ql:
            return ""
        return qs[:220]

    def _fallback_search_queries(self, ai_statement: str) -> List[str]:
        text = " ".join(str(ai_statement or "").split())
        ticker_match = re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b", text)
        ticker = ticker_match[0] if ticker_match else ""
        base = text[:160]
        queries: list[str] = []
        if ticker:
            queries.extend(
                [
                    f"{ticker} investor relations latest filing guidance",
                    f"{ticker} Reuters latest official confirmation",
                    f"{ticker} SEC filing latest material event",
                ]
            )
        if base:
            queries.append(base)
        return list(dict.fromkeys(q for q in queries if q))[: self.search_query_budget]

    async def _extract_search_queries(self, ai_statement: str) -> List[str]:
        plan = await self._plan_fact_check_searches(ai_statement, max_queries=self.search_query_budget)
        return plan.get("search_queries", [])[: self.search_query_budget]

    async def _search_multiple_queries(self, queries: list[str]) -> list[dict]:
        clean_queries = [self._clean_search_query(q) for q in queries]
        clean_queries = list(dict.fromkeys(q for q in clean_queries if q))
        tasks = [self._search_web_async(q) for q in clean_queries]
        if not tasks:
            return []
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        dedup: dict[str, dict] = {}
        for query, batch in zip(clean_queries, batches):
            if not isinstance(batch, list):
                continue
            for item in batch:
                if not isinstance(item, dict):
                    continue
                href = item.get("href", "")
                if not href:
                    continue
                enriched = dict(item)
                enriched["matched_query"] = query
                prev = dedup.get(href)
                if prev is None or int(enriched.get("source_quality", 0) or 0) > int(prev.get("source_quality", 0) or 0):
                    dedup[href] = enriched
        ranked = sorted(
            dedup.values(),
            key=lambda x: (int(x.get("source_quality", 0)), len(str(x.get("body", "")))),
            reverse=True,
        )
        return ranked[: self.max_results]

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
            package["limitations"].append("No web search results were found for the planned queries.")
            package["summary"] = f"No recent evidence found for: {query}"
            package["verdict"] = self._fallback_evidence_verdict(package)
            package["recommended_next_searches"] = search_queries
            return package

        semaphore = asyncio.Semaphore(self.fetch_concurrency)

        async def build_evidence(idx: int, result: dict, client: httpx.AsyncClient) -> dict | None:
            url = result.get("href", "")
            if not url:
                return None
            async with semaphore:
                full_text = await self._fetch_page_text(client, url)
            return {
                "evidence_id": f"E{idx}",
                "title": (result.get("title", "") or "").strip(),
                "url": url,
                "domain": self._safe_domain(url),
                "source_quality": result.get("source_quality", 0),
                "source_tier": result.get("source_tier", "unknown"),
                "matched_query": result.get("matched_query", ""),
                "snippet": (result.get("body", "") or "").strip()[:300],
                "excerpt": (full_text or "").strip()[:450],
                "extraction_method": "ddgs.text + bs4(p tags)",
            }

        async with httpx.AsyncClient(timeout=float(self.fetch_timeout_sec)) as client:
            fetched = await asyncio.gather(
                *[build_evidence(idx, r, client) for idx, r in enumerate(search_results, 1)],
                return_exceptions=True,
            )

        evidences = [item for item in fetched if isinstance(item, dict)]
        package["evidences"] = evidences
        if not evidences:
            package["status"] = "no_extractable_evidence"
            package["limitations"].append("Search returned URLs, but no extractable page text was captured.")
            package["summary"] = f"Search ran for '{query}', but evidence extraction failed."
            package["verdict"] = self._fallback_evidence_verdict(package)
            return package
        if len(evidences) < 3:
            package["limitations"].append("Evidence count is small, so confidence may be limited.")

        summary_system_prompt = (
            "You are an evidence summarizer for investment research. Use only the provided JSON evidence. "
            "Summarize in Korean in at most 5 bullets. Each bullet must cite evidence IDs like E1/E2. "
            "Do not add facts that are not present in the evidence."
        )
        try:
            package["summary"] = await self.llm_manager.get_local_response(
                summary_system_prompt,
                json.dumps(package, ensure_ascii=False, indent=2),
                profile="evidence",
            )
        except Exception as exc:
            package["limitations"].append(f"Local evidence summary failed: {str(exc)[:120]}")
            package["summary"] = "Local summary failed; raw evidence is provided."

        package["verdict"] = await self._build_evidence_verdict(package)
        verdict = package.get("verdict", {})
        if isinstance(verdict, dict):
            package["missing_evidence"] = verdict.get("missing_evidence", [])
            package["recommended_next_searches"] = verdict.get("recommended_next_searches", [])
            if not verdict.get("ready_for_signal", False):
                package["limitations"].append("Local evidence verdict is not ready_for_signal.")
        return package

    def _fallback_evidence_verdict(self, package: dict[str, Any]) -> dict[str, Any]:
        evidences = package.get("evidences", []) if isinstance(package, dict) else []
        source_tiers = [str(e.get("source_tier", "")) for e in evidences if isinstance(e, dict)]
        high_quality = any(t in {"regulatory", "company_ir", "tier1_media"} for t in source_tiers)
        independent_domains = {str(e.get("domain", "")) for e in evidences if isinstance(e, dict) and e.get("domain")}
        usable = len(evidences) >= 2 and len(independent_domains) >= 2 and high_quality
        missing = []
        if not high_quality:
            missing.append("official_or_tier1_source")
        if len(independent_domains) < 2:
            missing.append("independent_confirmation")
        if len(evidences) < 2:
            missing.append("more_evidence")
        status = "usable" if usable else "insufficient"
        return {
            "status": status,
            "ready_for_debate": bool(evidences),
            "ready_for_signal": usable,
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
            "recommended_next_searches": [],
            "allowed_use": "debate_and_signal" if usable else "discussion_only",
            "confidence": 0.55 if usable else 0.2,
        }

    async def _build_evidence_verdict(self, package: dict[str, Any]) -> dict[str, Any]:
        system_prompt = (
            "You are an evidence verdict worker for investment research. "
            "Use only the provided evidence JSON. Do not give buy/sell advice. "
            "Return only strict JSON."
        )
        user_prompt = (
            "[Evidence Package]\n"
            f"{json.dumps(package, ensure_ascii=False, indent=2)}\n\n"
            "[Required JSON]\n"
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
            "Rules:\n"
            "- ready_for_signal may be true only when official/IR/tier-1 evidence directly supports the claim.\n"
            "- If evidence is weak, list missing_evidence and recommended_next_searches instead of forcing a conclusion.\n"
            "- Contradictions must be explicit evidence conflicts, not uncertainty."
        )
        parsed: dict[str, Any] = {}
        try:
            raw = await self.llm_manager.get_local_response(
                system_prompt,
                user_prompt,
                profile="evidence_verdict",
            )
            candidate = parse_json_object(raw) or {}
            if isinstance(candidate, dict):
                parsed = candidate
        except Exception:
            parsed = {}
        fallback = self._fallback_evidence_verdict(package)
        if not parsed:
            return fallback

        def _list(key: str, default: list | None = None) -> list:
            value = parsed.get(key, default or [])
            return value if isinstance(value, list) else (default or [])

        status = str(parsed.get("status") or fallback["status"]).strip().lower()
        if status not in {"strong", "usable", "insufficient", "contradictory"}:
            status = fallback["status"]
        confidence = parsed.get("confidence", fallback["confidence"])
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except Exception:
            confidence = fallback["confidence"]
        ready_for_signal = bool(parsed.get("ready_for_signal", fallback["ready_for_signal"]))
        if status in {"insufficient", "contradictory"}:
            ready_for_signal = False
        return {
            "status": status,
            "ready_for_debate": bool(parsed.get("ready_for_debate", fallback["ready_for_debate"])),
            "ready_for_signal": ready_for_signal,
            "verified_claims": _list("verified_claims"),
            "partially_supported_claims": _list("partially_supported_claims"),
            "unsupported_claims": _list("unsupported_claims"),
            "contradictions": _list("contradictions"),
            "best_sources": _list("best_sources", fallback["best_sources"]),
            "missing_evidence": _list("missing_evidence", fallback["missing_evidence"]),
            "recommended_next_searches": _list("recommended_next_searches"),
            "allowed_use": str(parsed.get("allowed_use") or fallback["allowed_use"]),
            "confidence": confidence,
        }

    async def run_deep_research(self, query: str) -> str:
        package = await self.run_deep_research_package(query)
        verdict = package.get("verdict", {}) if isinstance(package, dict) else {}
        if package.get("status") != "ok" and not package.get("evidences"):
            return (
                f"[Evidence Package]\n"
                f"- query: {query}\n"
                f"- status: {package.get('status')}\n"
                f"- search_queries: {(package.get('search_plan') or {}).get('search_queries', [])}\n"
                f"- limitations: {package.get('limitations', [])}\n"
                f"- recommended_next_searches: {package.get('recommended_next_searches', [])}"
            )

        source_lines = [
            f"- {ev.get('evidence_id')}: {ev.get('title')} ({ev.get('domain')})\n  {ev.get('url')}"
            for ev in package.get("evidences", [])[:5]
        ]
        return (
            f"[Evidence Package]\n"
            f"- query: {package.get('query')}\n"
            f"- search_queries: {(package.get('search_plan') or {}).get('search_queries', [])}\n"
            f"- generated_at_utc: {package.get('generated_at_utc')}\n"
            f"- evidence_count: {len(package.get('evidences', []))}\n"
            f"- limitations: {package.get('limitations', [])}\n\n"
            f"[Local Evidence Verdict]\n{json.dumps(verdict, ensure_ascii=False, indent=2)}\n\n"
            f"[Summary]\n{package.get('summary', '')}\n\n"
            f"[Sources]\n{chr(10).join(source_lines)}"
        )

    async def verify_statement(self, ai_name: str, ai_statement: str) -> str:
        package = await self.run_deep_research_package(ai_statement)
        verdict = package.get("verdict", {}) if isinstance(package, dict) else {}
        links = "\n".join(
            f"- {e.get('evidence_id')}: {e.get('url')}"
            for e in package.get("evidences", [])[:5]
            if isinstance(e, dict) and e.get("url")
        )
        return (
            f"**[Local Fact-Check: {ai_name}]**\n"
            f"- status: {package.get('status')}\n"
            f"- search_queries: {(package.get('search_plan') or {}).get('search_queries', [])}\n"
            f"- verdict: {json.dumps(verdict, ensure_ascii=False)}\n\n"
            f"{package.get('summary', '')}\n\n"
            f"*Links:*\n{links}"
        )
