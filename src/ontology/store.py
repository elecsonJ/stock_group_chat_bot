import sqlite3
import re
from datetime import datetime
from typing import Optional, Any
import os

from db_manager import DB_PATH


def _now_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


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

    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, timeout=20.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
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

    def log_ingestion(self, dataset_name: str, source_path: str, records_count: int):
        self.cursor.execute(
            """
            INSERT INTO ontology_ingestion_log (dataset_name, source_path, records_count, ingested_at)
            VALUES (?, ?, ?, ?)
            """,
            (dataset_name, source_path, int(records_count), _now_utc()),
        )
        self.conn.commit()
