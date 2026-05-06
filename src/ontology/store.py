import sqlite3
import re
from datetime import datetime, UTC
from typing import Optional, Any
import os

from db_manager import DB_PATH


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_alias(value: str) -> str:
    if not value:
        return ""
    text = value.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


class OntologyStore:
    """
    투자 도메인 온톨로지(엔티티/별칭/관계)를 SQLite에 저장/조회하는 경량 저장소.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=20.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self.predicate_weights = {
            "supplies_to": 1.0,
            "customer_of": 0.95,
            "belongs_to_supply_chain": 0.9,
            "produces": 0.88,
            "uses": 0.84,
            "requires": 0.84,
            "benefits_from": 0.8,
            "drives_demand_for": 0.8,
            "enables": 0.78,
            "competes_with": 0.7,
            "partners_with": 0.72,
            "invests_in": 0.68,
            "exposed_to": 0.62,
            "affected_by": 0.6,
            "sells": 0.74,
        }
        self._create_tables()

    def _create_tables(self):
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ontology_entities (
                entity_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                ticker TEXT,
                exchange TEXT,
                lei TEXT,
                figi TEXT,
                cik TEXT,
                country TEXT,
                sector TEXT,
                industry TEXT,
                source TEXT,
                updated_at TEXT
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ontology_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                alias_norm TEXT NOT NULL,
                source TEXT,
                confidence REAL DEFAULT 1.0
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ontology_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_id TEXT NOT NULL,
                source TEXT,
                confidence REAL DEFAULT 1.0,
                updated_at TEXT
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ontology_ingestion_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_name TEXT NOT NULL,
                source_path TEXT,
                records_count INTEGER,
                ingested_at TEXT NOT NULL
            )
            """
        )

        self.cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_ontology_entities_ticker ON ontology_entities(ticker)"
        )
        self.cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_ontology_aliases_norm ON ontology_aliases(alias_norm)"
        )
        self.cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_ontology_relations_subject ON ontology_relations(subject_id, predicate)"
        )
        self.conn.commit()

    def upsert_entity(self, entity: dict[str, Any]):
        entity_id = str(entity.get("entity_id", "")).strip()
        if not entity_id:
            return

        payload = (
            entity_id,
            str(entity.get("canonical_name", "")).strip(),
            str(entity.get("entity_type", "company")).strip() or "company",
            str(entity.get("ticker", "")).strip() or None,
            str(entity.get("exchange", "")).strip() or None,
            str(entity.get("lei", "")).strip() or None,
            str(entity.get("figi", "")).strip() or None,
            str(entity.get("cik", "")).strip() or None,
            str(entity.get("country", "")).strip() or None,
            str(entity.get("sector", "")).strip() or None,
            str(entity.get("industry", "")).strip() or None,
            str(entity.get("source", "")).strip() or None,
            _now_utc(),
        )
        self.cursor.execute(
            """
            INSERT INTO ontology_entities (
                entity_id, canonical_name, entity_type, ticker, exchange, lei, figi, cik,
                country, sector, industry, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                canonical_name=excluded.canonical_name,
                entity_type=excluded.entity_type,
                ticker=excluded.ticker,
                exchange=excluded.exchange,
                lei=excluded.lei,
                figi=excluded.figi,
                cik=excluded.cik,
                country=excluded.country,
                sector=excluded.sector,
                industry=excluded.industry,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            payload,
        )
        self.conn.commit()

    def add_alias(
        self,
        entity_id: str,
        alias: str,
        source: str = "manual",
        confidence: float = 1.0,
    ):
        alias_clean = alias.strip()
        norm = normalize_alias(alias_clean)
        if not entity_id or not alias_clean or not norm:
            return

        self.cursor.execute(
            """
            SELECT id FROM ontology_aliases
            WHERE entity_id = ? AND alias_norm = ?
            LIMIT 1
            """,
            (entity_id, norm),
        )
        exists = self.cursor.fetchone()
        if exists:
            return

        self.cursor.execute(
            """
            INSERT INTO ontology_aliases (entity_id, alias, alias_norm, source, confidence)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entity_id, alias_clean, norm, source, float(confidence)),
        )
        self.conn.commit()

    def add_relation(
        self,
        subject_id: str,
        predicate: str,
        object_id: str,
        source: str = "manual",
        confidence: float = 1.0,
    ):
        if not subject_id or not predicate or not object_id:
            return

        norm_pred = predicate.strip()
        now = _now_utc()
        self.cursor.execute(
            """
            SELECT id, confidence FROM ontology_relations
            WHERE subject_id = ? AND predicate = ? AND object_id = ?
            LIMIT 1
            """,
            (subject_id, norm_pred, object_id),
        )
        exists = self.cursor.fetchone()
        if exists:
            rel_id = exists["id"]
            prev_conf = float(exists["confidence"] or 0.0)
            merged_conf = max(prev_conf, float(confidence))
            self.cursor.execute(
                """
                UPDATE ontology_relations
                SET source = ?, confidence = ?, updated_at = ?
                WHERE id = ?
                """,
                (source, merged_conf, now, rel_id),
            )
            self.conn.commit()
            return

        self.cursor.execute(
            """
            INSERT INTO ontology_relations (subject_id, predicate, object_id, source, confidence, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (subject_id, norm_pred, object_id, source, float(confidence), now),
        )
        self.conn.commit()

    def get_entity(self, entity_id: str) -> Optional[dict[str, Any]]:
        self.cursor.execute(
            "SELECT * FROM ontology_entities WHERE entity_id = ? LIMIT 1", (entity_id,)
        )
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def resolve_alias(self, name: str, limit: int = 5) -> list[dict[str, Any]]:
        norm = normalize_alias(name)
        if not norm:
            return []

        self.cursor.execute(
            """
            SELECT e.*, a.alias, a.confidence
            FROM ontology_aliases a
            JOIN ontology_entities e ON e.entity_id = a.entity_id
            WHERE a.alias_norm = ?
            ORDER BY a.confidence DESC
            LIMIT ?
            """,
            (norm, limit),
        )
        rows = self.cursor.fetchall()
        return [dict(r) for r in rows]

    def search_alias_contains(self, name: str, limit: int = 5) -> list[dict[str, Any]]:
        norm = normalize_alias(name)
        if not norm:
            return []
        like = f"%{norm}%"
        self.cursor.execute(
            """
            SELECT e.*, a.alias, a.confidence
            FROM ontology_aliases a
            JOIN ontology_entities e ON e.entity_id = a.entity_id
            WHERE a.alias_norm LIKE ?
            ORDER BY a.confidence DESC
            LIMIT ?
            """,
            (like, limit),
        )
        rows = self.cursor.fetchall()
        return [dict(r) for r in rows]

    def search_entities(self, keyword: str, limit: int = 8) -> list[dict[str, Any]]:
        kw = keyword.strip()
        if not kw:
            return []
        like = f"%{kw}%"
        self.cursor.execute(
            """
            SELECT * FROM ontology_entities
            WHERE canonical_name LIKE ?
               OR ticker LIKE ?
               OR sector LIKE ?
               OR industry LIKE ?
            LIMIT ?
            """,
            (like, like, like, like, limit),
        )
        rows = self.cursor.fetchall()
        return [dict(r) for r in rows]

    def match_entities_in_text(self, text: str, limit: int = 10, min_alias_len: int = 3) -> list[dict[str, Any]]:
        norm = normalize_alias(text)
        if not norm:
            return []
        self.cursor.execute(
            """
            SELECT e.*, a.alias, a.alias_norm, a.confidence
            FROM ontology_aliases a
            JOIN ontology_entities e ON e.entity_id = a.entity_id
            WHERE LENGTH(a.alias_norm) >= ?
              AND INSTR(?, a.alias_norm) > 0
            ORDER BY LENGTH(a.alias_norm) DESC, a.confidence DESC
            LIMIT ?
            """,
            (int(min_alias_len), norm, int(limit)),
        )
        rows = self.cursor.fetchall()
        seen = set()
        out = []
        for row in rows:
            item = dict(row)
            eid = item.get("entity_id")
            if not eid or eid in seen:
                continue
            out.append(item)
            seen.add(eid)
        return out

    def get_neighbors(
        self, entity_id: str, predicates: Optional[list[str]] = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        if not entity_id:
            return []

        if predicates:
            placeholders = ",".join("?" for _ in predicates)
            sql = f"""
                SELECT r.*, e.canonical_name AS object_name, e.ticker AS object_ticker
                FROM ontology_relations r
                LEFT JOIN ontology_entities e ON e.entity_id = r.object_id
                WHERE r.subject_id = ?
                  AND r.predicate IN ({placeholders})
                ORDER BY r.confidence DESC
                LIMIT ?
            """
            params = [entity_id, *predicates, limit]
        else:
            sql = """
                SELECT r.*, e.canonical_name AS object_name, e.ticker AS object_ticker
                FROM ontology_relations r
                LEFT JOIN ontology_entities e ON e.entity_id = r.object_id
                WHERE r.subject_id = ?
                ORDER BY r.confidence DESC
                LIMIT ?
            """
            params = [entity_id, limit]

        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        return [dict(r) for r in rows]

    def get_reverse_neighbors(
        self, entity_id: str, predicates: Optional[list[str]] = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        if not entity_id:
            return []

        if predicates:
            placeholders = ",".join("?" for _ in predicates)
            sql = f"""
                SELECT r.*, e.canonical_name AS subject_name, e.ticker AS subject_ticker
                FROM ontology_relations r
                LEFT JOIN ontology_entities e ON e.entity_id = r.subject_id
                WHERE r.object_id = ?
                  AND r.predicate IN ({placeholders})
                ORDER BY r.confidence DESC
                LIMIT ?
            """
            params = [entity_id, *predicates, limit]
        else:
            sql = """
                SELECT r.*, e.canonical_name AS subject_name, e.ticker AS subject_ticker
                FROM ontology_relations r
                LEFT JOIN ontology_entities e ON e.entity_id = r.subject_id
                WHERE r.object_id = ?
                ORDER BY r.confidence DESC
                LIMIT ?
            """
            params = [entity_id, limit]

        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        return [dict(r) for r in rows]

    def discover_hidden_candidates(
        self,
        seed_entity_ids: list[str],
        predicates: Optional[list[str]] = None,
        max_depth: int = 2,
        per_hop_limit: int = 12,
        max_candidates: int = 10,
    ) -> list[dict[str, Any]]:
        """
        숨은 연결고리 탐색:
        - seed 엔티티에서 시작
        - relation을 양방향으로 최대 max_depth-hop까지 탐색
        - 회사/증권 엔티티를 hidden candidate로 수집
        """
        allowed_target_types = {"company", "security", "legal_entity"}
        seen_paths: set[tuple[str, str]] = set()
        frontier = []
        visited = set()

        for seed in seed_entity_ids:
            if seed:
                frontier.append((seed, [], 0))
                visited.add((seed, 0))

        candidates: dict[str, dict[str, Any]] = {}

        while frontier:
            current_id, path, depth = frontier.pop(0)
            if depth >= max_depth:
                continue

            outward = self.get_neighbors(current_id, predicates=predicates, limit=per_hop_limit)
            inward = self.get_reverse_neighbors(current_id, predicates=predicates, limit=per_hop_limit)

            next_steps = []
            for row in outward:
                next_steps.append(
                    {
                        "next_id": row.get("object_id"),
                        "next_name": row.get("object_name"),
                        "next_ticker": row.get("object_ticker"),
                        "predicate": row.get("predicate"),
                        "direction": "out",
                        "confidence": float(row.get("confidence") or 0.0),
                    }
                )
            for row in inward:
                next_steps.append(
                    {
                        "next_id": row.get("subject_id"),
                        "next_name": row.get("subject_name"),
                        "next_ticker": row.get("subject_ticker"),
                        "predicate": row.get("predicate"),
                        "direction": "in",
                        "confidence": float(row.get("confidence") or 0.0),
                    }
                )

            for step in next_steps:
                next_id = str(step.get("next_id") or "").strip()
                if not next_id:
                    continue
                new_path = [
                    *path,
                    {
                        "from_id": current_id,
                        "to_id": next_id,
                        "predicate": step.get("predicate"),
                        "direction": step.get("direction"),
                        "confidence": step.get("confidence"),
                    },
                ]

                entity = self.get_entity(next_id) or {}
                entity_type = str(entity.get("entity_type") or "").strip()
                if entity_type in allowed_target_types and next_id not in seed_entity_ids:
                    path_sig = (next_id, " > ".join(f"{p['direction']}:{p['predicate']}" for p in new_path))
                    if path_sig in seen_paths:
                        continue
                    seen_paths.add(path_sig)
                    path_score = self._score_path(new_path)
                    validation = self._validate_path(new_path)
                    existing = candidates.get(next_id)
                    path_payload = {
                        "entity_id": next_id,
                        "canonical_name": entity.get("canonical_name"),
                        "ticker": entity.get("ticker"),
                        "entity_type": entity_type,
                        "path": new_path,
                        "path_score": round(path_score, 3),
                        "validation_score": validation["score"],
                        "validation_flags": validation["flags"],
                    }
                    if validation["score"] >= 0.45 and (existing is None or path_payload["path_score"] > existing.get("path_score", 0.0)):
                        candidates[next_id] = path_payload

                next_depth = depth + 1
                visit_key = (next_id, next_depth)
                if visit_key in visited:
                    continue
                visited.add(visit_key)
                frontier.append((next_id, new_path, next_depth))

        ranked = sorted(
            candidates.values(),
            key=lambda x: (float(x.get("validation_score", 0.0)), float(x.get("path_score", 0.0))),
            reverse=True,
        )
        return ranked[:max_candidates]

    def _score_path(self, path: list[dict[str, Any]]) -> float:
        if not path:
            return 0.0
        score = 0.0
        for step in path:
            confidence = float(step.get("confidence") or 0.0)
            predicate = str(step.get("predicate") or "").strip()
            weight = float(self.predicate_weights.get(predicate, 0.55))
            score += confidence * weight
        avg_score = score / max(1, len(path))
        depth_penalty = max(0.0, 1.0 - ((len(path) - 1) * 0.12))
        return round(avg_score * depth_penalty, 4)

    def _validate_path(self, path: list[dict[str, Any]]) -> dict[str, Any]:
        flags = []
        if not path:
            return {"score": 0.0, "flags": ["empty_path"]}
        score = self._score_path(path)
        weak_steps = 0
        for step in path:
            confidence = float(step.get("confidence") or 0.0)
            predicate = str(step.get("predicate") or "").strip()
            if confidence < 0.55:
                weak_steps += 1
                flags.append(f"low_confidence:{predicate}")
            if self.predicate_weights.get(predicate, 0.0) < 0.65:
                flags.append(f"weak_predicate:{predicate}")
        if len(path) >= 3:
            flags.append("deep_path")
            score *= 0.92
        if weak_steps >= 2:
            score *= 0.8
        return {"score": round(score, 4), "flags": list(dict.fromkeys(flags))}

    def log_ingestion(self, dataset_name: str, source_path: str, records_count: int):
        self.cursor.execute(
            """
            INSERT INTO ontology_ingestion_log (dataset_name, source_path, records_count, ingested_at)
            VALUES (?, ?, ?, ?)
            """,
            (dataset_name, source_path, int(records_count), _now_utc()),
        )
        self.conn.commit()
