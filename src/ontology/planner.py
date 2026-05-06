import os
import re
from typing import Any

from ontology.store import OntologyStore


class HybridResearchPlanner:
    """
    온톨로지 -> RAG -> 웹검색을 오가는 리서치 플래너.
    - query에서 엔티티 후보 추출
    - 온톨로지 링크/관계 확장
    - 커버리지 기반으로 조사 모드 선택
    - tickers/search_queries/rag_keywords 생성
    """

    def __init__(self, store: OntologyStore):
        self.store = store
        self.hidden_candidate_min_score = max(0.0, float(os.getenv("ONTOLOGY_HIDDEN_MIN_SCORE", "0.45")))

    def _extract_candidates(self, user_query: str) -> list[str]:
        tokens = set()

        # 대문자 티커 후보
        for t in re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b", user_query):
            tokens.add(t.strip())

        # 한글/영문 단어 묶음
        for k in re.findall(r"[A-Za-z][A-Za-z0-9\-\&\.]{1,}|[가-힣]{2,}", user_query):
            base = k.strip()
            if base:
                tokens.add(base)
                # 한국어 조사 제거형도 같이 후보로 추가
                for josa in ("와", "과", "를", "을", "은", "는", "이", "가", "의", "도", "로", "에", "에서"):
                    if base.endswith(josa) and len(base) > len(josa) + 1:
                        tokens.add(base[: -len(josa)])

        stop_words = {
            "주식",
            "투자",
            "토론",
            "토론하고",
            "토론해줘",
            "중심으로",
            "중심으",
            "수혜인지",
            "관련",
            "현재",
            "최근",
            "어디",
            "집중",
            "비교",
            "분석",
            "리스크",
            "공급망",
            "싶어",
            "그리고",
            "또는",
            "ETF",
            "AI",
        }

        def _strip_josa(text: str) -> str:
            for josa in ("와", "과", "를", "을", "은", "는", "이", "가", "의", "도", "로", "에", "에서"):
                if text.endswith(josa) and len(text) > len(josa) + 1:
                    return text[: -len(josa)]
            return text

        filtered = []
        for t in tokens:
            if not t or len(t) < 2:
                continue
            base = _strip_josa(t)
            if t in stop_words or base in stop_words:
                continue
            filtered.append(t)

        return filtered[:20]

    def _link_entities(self, candidates: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
        linked = []
        unresolved = []
        seen_ids = set()

        def _variants(token: str) -> list[str]:
            vars_ = [token]
            for josa in ("와", "과", "를", "을", "은", "는", "이", "가", "의", "도", "로", "에", "에서"):
                if token.endswith(josa) and len(token) > len(josa) + 1:
                    vars_.append(token[: -len(josa)])
                    break
            return list(dict.fromkeys([v for v in vars_ if v]))

        for cand in candidates:
            matched = False
            for cand_v in _variants(cand):
                alias_hits = self.store.resolve_alias(cand_v, limit=3)
                if alias_hits:
                    for hit in alias_hits:
                        eid = hit.get("entity_id")
                        if eid and eid not in seen_ids:
                            linked.append(hit)
                            seen_ids.add(eid)
                    matched = True
                    break

                search_hits = self.store.search_entities(cand_v, limit=2)
                if search_hits:
                    for hit in search_hits:
                        eid = hit.get("entity_id")
                        if eid and eid not in seen_ids:
                            linked.append(hit)
                            seen_ids.add(eid)
                    matched = True
                    break

                # contains 기반 fuzzy는 오매칭 위험이 커서 길이가 충분한 영문/숫자 토큰에만 제한
                if len(cand_v) >= 4 and re.search(r"[A-Za-z0-9]", cand_v):
                    fuzzy_hits = self.store.search_alias_contains(cand_v, limit=2)
                    if fuzzy_hits:
                        for hit in fuzzy_hits:
                            eid = hit.get("entity_id")
                            if eid and eid not in seen_ids:
                                linked.append(hit)
                                seen_ids.add(eid)
                        matched = True
                        break

            if not matched:
                unresolved.append(cand)

        return linked, unresolved

    def build_plan(self, user_query: str) -> dict[str, Any]:
        candidates = self._extract_candidates(user_query)
        linked, unresolved = self._link_entities(candidates)

        coverage = 0.0
        if candidates:
            coverage = len(linked) / max(1, len(candidates))

        # 모드 선택
        if coverage >= 0.55:
            mode = "ontology_first"
        elif coverage >= 0.25:
            mode = "hybrid"
        else:
            mode = "web_first"

        tickers = []
        rag_keywords = []
        web_queries = []
        expansion_nodes = []
        linked_ids = []

        for ent in linked[:6]:
            name = (ent.get("canonical_name") or "").strip()
            ticker = (ent.get("ticker") or "").strip()
            sector = (ent.get("sector") or "").strip()
            industry = (ent.get("industry") or "").strip()
            entity_id = ent.get("entity_id")
            entity_type = (ent.get("entity_type") or "").strip()

            if ticker and ticker not in tickers:
                tickers.append(ticker)
            if name and name not in rag_keywords:
                rag_keywords.append(name)
            if sector and sector not in rag_keywords:
                rag_keywords.append(sector)
            if industry and industry not in rag_keywords:
                rag_keywords.append(industry)

            if entity_id:
                linked_ids.append(entity_id)
                if entity_type == "company" and ticker:
                    rag_keywords.append(ticker)
                neighbors = self.store.get_neighbors(
                    entity_id,
                    predicates=[
                        "supplies_to", "customer_of", "competes_with", "belongs_to_supply_chain",
                        "produces", "sells", "uses", "requires", "benefits_from",
                        "drives_demand_for", "enables", "exposed_to",
                    ],
                    limit=5,
                )
                for n in neighbors:
                    obj_name = (n.get("object_name") or "").strip()
                    pred = (n.get("predicate") or "").strip()
                    if obj_name:
                        expansion_nodes.append({"from": name, "predicate": pred, "to": obj_name})

        hidden_candidates = self.store.discover_hidden_candidates(
            linked_ids,
            predicates=[
                "supplies_to", "customer_of", "competes_with", "belongs_to_supply_chain",
                "partners_with", "invests_in", "produces", "sells", "uses", "requires",
                "benefits_from", "drives_demand_for", "enables", "exposed_to", "affected_by",
            ],
            max_depth=3,
            per_hop_limit=10,
            max_candidates=8,
        )
        hidden_candidates = [
            c for c in hidden_candidates
            if (c.get("ticker") or c.get("canonical_name"))
            and (c.get("ticker") not in tickers)
            and float(c.get("validation_score", 0.0) or 0.0) >= self.hidden_candidate_min_score
        ]

        # 웹검색 쿼리 생성
        if linked:
            for ent in linked[:3]:
                cname = (ent.get("canonical_name") or ent.get("ticker") or "").strip()
                if cname:
                    web_queries.append(f"{cname} latest filing guidance supply chain")
                    web_queries.append(f"{cname} recent earnings outlook risk")
        for x in expansion_nodes[:3]:
            web_queries.append(f"{x['from']} {x['predicate']} {x['to']} evidence")
        for hc in hidden_candidates[:3]:
            cname = (hc.get("canonical_name") or hc.get("ticker") or "").strip()
            ticker = (hc.get("ticker") or "").strip()
            if cname:
                subject = f"{ticker} {cname}".strip() if ticker else cname
                web_queries.append(f"{subject} why demand exposure catalyst risk")
                web_queries.append(f"{subject} indirect beneficiary evidence")

        # unresolved 토큰은 잡음이 많아 일반화된 ticker profile 쿼리는 생성하지 않음

        # dedup + 제한
        dedup_web = []
        seen_q = set()
        for q in web_queries:
            q_norm = re.sub(r"\s+", " ", q.strip().lower())
            if q_norm and q_norm not in seen_q:
                dedup_web.append(q.strip())
                seen_q.add(q_norm)
        web_queries = dedup_web[:4]

        if not web_queries:
            web_queries = [user_query.strip()[:180]]

        if not rag_keywords:
            rag_keywords = candidates[:5]

        confidence = "high" if coverage >= 0.55 else ("medium" if coverage >= 0.25 else "low")

        planner_tickers = []
        for t in tickers:
            if t and t not in planner_tickers:
                planner_tickers.append(t)
        for c in hidden_candidates:
            t = str(c.get("ticker") or "").strip()
            if t and t not in planner_tickers:
                planner_tickers.append(t)

        return {
            "mode": mode,
            "coverage": round(coverage, 3),
            "confidence": confidence,
            "candidate_count": len(candidates),
            "linked_entities": [
                {
                    "entity_id": e.get("entity_id"),
                    "name": e.get("canonical_name"),
                    "ticker": e.get("ticker"),
                    "sector": e.get("sector"),
                    "industry": e.get("industry"),
                }
                for e in linked[:8]
            ],
            "unresolved_terms": unresolved[:8],
            "expansion_nodes": expansion_nodes[:8],
            "hidden_candidates": [
                {
                    "entity_id": c.get("entity_id"),
                    "name": c.get("canonical_name"),
                    "ticker": c.get("ticker"),
                    "path_score": c.get("path_score"),
                    "validation_score": c.get("validation_score"),
                    "validation_flags": c.get("validation_flags", []),
                    "path": [
                        {
                            "predicate": p.get("predicate"),
                            "direction": p.get("direction"),
                        }
                        for p in (c.get("path") or [])[:3]
                    ],
                }
                for c in hidden_candidates[:6]
            ],
            "tickers": planner_tickers[:3],
            "rag_keywords": rag_keywords[:8],
            "web_queries": web_queries,
        }
