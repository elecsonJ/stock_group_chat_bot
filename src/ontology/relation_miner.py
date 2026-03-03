import re
from typing import Any

from ontology.store import OntologyStore


class EvidenceRelationMiner:
    """
    웹 리서치 evidence 텍스트에서 온톨로지 관계를 보수적으로 추출합니다.
    """

    def __init__(self, store: OntologyStore):
        self.store = store

    def _extract_query_terms(self, text: str) -> list[str]:
        if not text:
            return []
        terms = []
        seen = set()
        for token in re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b|[A-Za-z][A-Za-z0-9\-\&\.]{1,}|[가-힣]{2,}", text):
            t = token.strip()
            if t and t not in seen:
                terms.append(t)
                seen.add(t)
        return terms[:20]

    def _resolve_subject_entities(self, topic: str, query: str) -> list[dict[str, Any]]:
        candidates = self._extract_query_terms(f"{topic} {query}")
        subjects: list[dict[str, Any]] = []
        seen = set()
        for c in candidates:
            alias_hits = self.store.resolve_alias(c, limit=2)
            hits = alias_hits if alias_hits else self.store.search_entities(c, limit=2)
            for hit in hits:
                eid = hit.get("entity_id")
                if eid and eid not in seen:
                    subjects.append(hit)
                    seen.add(eid)
        return subjects[:4]

    def _detect_predicate(self, text: str) -> tuple[str | None, float]:
        t = (text or "").lower()
        if not t:
            return None, 0.0
        if any(k in t for k in ("공급", "납품", "supplier", "supplies", "supply chain", "vendor")):
            return "supplies_to", 0.78
        if any(k in t for k in ("고객", "customer", "주문", "order", "purchase")):
            return "customer_of", 0.72
        if any(k in t for k in ("경쟁", "compete", "rival")):
            return "competes_with", 0.68
        if any(k in t for k in ("협력", "제휴", "partner", "partnership")):
            return "partners_with", 0.74
        if any(k in t for k in ("투자", "지분", "invest", "stake")):
            return "invests_in", 0.7
        return None, 0.0

    def ingest_evidence_package(self, topic: str, query: str, package: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(package, dict):
            return {"added_relations": 0, "status": "invalid_package"}
        evidences = package.get("evidences", [])
        if not isinstance(evidences, list) or not evidences:
            return {"added_relations": 0, "status": "no_evidence"}

        subjects = self._resolve_subject_entities(topic, query)
        if not subjects:
            return {"added_relations": 0, "status": "no_subject"}

        added = 0
        for ev in evidences[:8]:
            if not isinstance(ev, dict):
                continue
            title = ev.get("title", "") or ""
            snippet = ev.get("snippet", "") or ""
            excerpt = ev.get("excerpt", "") or ""
            text_blob = f"{title}\n{snippet}\n{excerpt}"
            predicate, base_conf = self._detect_predicate(text_blob)
            if not predicate:
                continue

            linked_entities = self.store.match_entities_in_text(text_blob, limit=8, min_alias_len=3)
            if len(linked_entities) < 2:
                continue

            evidence_id = ev.get("global_evidence_id") or ev.get("evidence_id") or "E?"
            source = f"evidence_miner:{query}:{evidence_id}"
            # 노이즈 억제를 위해 query에서 가장 먼저 식별된 주체 1개만 anchor로 사용
            for subj in subjects[:1]:
                sid = subj.get("entity_id")
                if not sid:
                    continue
                for obj in linked_entities:
                    oid = obj.get("entity_id")
                    if not oid or oid == sid:
                        continue
                    obj_conf = float(obj.get("confidence", 1.0) or 1.0)
                    confidence = min(0.95, max(0.4, base_conf * obj_conf))
                    self.store.add_relation(
                        sid,
                        predicate,
                        oid,
                        source=source,
                        confidence=confidence,
                    )
                    added += 1

        return {
            "added_relations": added,
            "status": "ok",
            "subjects": [s.get("entity_id") for s in subjects if s.get("entity_id")],
        }
