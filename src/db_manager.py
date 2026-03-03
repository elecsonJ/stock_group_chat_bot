import sqlite3
import json
from datetime import datetime
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'investment_bot.db')

class DBManager:
    def __init__(self):
        # data 폴더가 없으면 생성
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        # timeout=20.0 및 check_same_thread=False 추가 (DB Lock 및 동시성 에러 방지)
        self.conn = sqlite3.connect(DB_PATH, timeout=20.0, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.cursor.execute("PRAGMA journal_mode=WAL;")
        self.cursor.execute("PRAGMA synchronous=NORMAL;")
        self.cursor.execute("PRAGMA busy_timeout=20000;")
        self.fts_enabled = False
        self._create_tables()
        self._create_fts_indexes()

    def _create_tables(self):
        # 1. 일일 뉴스 저장 테이블 (당일 맥락 일관성 유지)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                keyword TEXT,
                news_data TEXT
            )
        ''')
        
        # 2. 토론 및 회의록 원본 저장 테이블
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS debates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                topic TEXT,
                full_log TEXT,
                consensus_status TEXT,
                investment_json TEXT
            )
        ''')
        
        # 3. 요약 저장 테이블 (일/주/월간 RAG 용도)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary_type TEXT, -- 'daily', 'weekly', 'monthly'
                target_date TEXT,  -- 요약 대상 날짜 또는 기간
                summary_text TEXT,
                keywords TEXT
            )
        ''')

        # 4. 리서치 증거 패키지 저장 테이블 (검증 재현성 확보)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS research_evidences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                topic TEXT,
                query TEXT,
                query_norm TEXT,
                created_at TEXT,
                evidence_json TEXT
            )
        ''')

        # 5. 고품질 뉴스 아티클 저장 테이블 (정규화/중복제거)
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS news_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_key TEXT UNIQUE,
                date TEXT,
                source TEXT,
                source_type TEXT,
                section TEXT,
                title TEXT,
                url TEXT,
                canonical_url TEXT,
                published_at TEXT,
                summary TEXT,
                content_hash TEXT,
                raw_json TEXT,
                event_key TEXT,
                fetched_at TEXT,
                ingest_delay_sec INTEGER,
                ingested_at TEXT
            )
            '''
        )

        # 6. 뉴스 이벤트 클러스터 저장 테이블
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS news_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT UNIQUE,
                date TEXT,
                title TEXT,
                summary TEXT,
                source_count INTEGER,
                article_count INTEGER,
                confidence REAL,
                sample_urls TEXT,
                updated_at TEXT
            )
            '''
        )

        # 7. 소스별 수집 체크포인트(인덱싱 지연 보정용)
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS news_ingest_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT UNIQUE,
                last_success_at TEXT,
                cursor_json TEXT,
                updated_at TEXT
            )
            '''
        )

        self._ensure_column("research_evidences", "query_norm", "TEXT")
        self._ensure_column("research_evidences", "created_at", "TEXT")
        self._ensure_column("news_articles", "fetched_at", "TEXT")
        self._ensure_column("news_articles", "ingest_delay_sec", "INTEGER")

        # 조회 성능 및 24시간 운영 안정성 향상을 위한 인덱스
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_daily_news_date_keyword ON daily_news(date, keyword)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_debates_date ON debates(date)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_summaries_type_date ON summaries(summary_type, target_date)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_research_date_topic ON research_evidences(date, topic)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_research_querynorm_created ON research_evidences(query_norm, created_at)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_articles_date_source ON news_articles(date, source)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_articles_event ON news_articles(event_key)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_articles_source_pub ON news_articles(source, published_at)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_events_date_conf ON news_events(date, confidence)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_ckpt_source ON news_ingest_checkpoints(source)')
        self.conn.commit()

    def _ensure_column(self, table_name: str, column_name: str, col_type: str):
        self.cursor.execute(f"PRAGMA table_info({table_name})")
        columns = {row[1] for row in self.cursor.fetchall()}
        if column_name not in columns:
            self.cursor.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {col_type}"
            )
            self.conn.commit()

    def _normalize_query(self, query: str) -> str:
        return " ".join((query or "").strip().lower().split())

    def _create_fts_indexes(self):
        """
        FTS5 기반 RAG 검색 성능 향상.
        환경에 FTS5가 비활성인 경우 자동으로 LIKE fallback 경로를 유지합니다.
        """
        try:
            self.cursor.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS debates_fts USING fts5(
                    topic,
                    full_log,
                    investment_json,
                    content='debates',
                    content_rowid='id'
                )
                """
            )
            self.cursor.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS summaries_fts USING fts5(
                    summary_text,
                    keywords,
                    content='summaries',
                    content_rowid='id'
                )
                """
            )
            self.cursor.execute(
                """
                CREATE TRIGGER IF NOT EXISTS debates_ai AFTER INSERT ON debates BEGIN
                    INSERT INTO debates_fts(rowid, topic, full_log, investment_json)
                    VALUES (new.id, new.topic, new.full_log, new.investment_json);
                END;
                """
            )
            self.cursor.execute(
                """
                CREATE TRIGGER IF NOT EXISTS debates_ad AFTER DELETE ON debates BEGIN
                    INSERT INTO debates_fts(debates_fts, rowid, topic, full_log, investment_json)
                    VALUES ('delete', old.id, old.topic, old.full_log, old.investment_json);
                END;
                """
            )
            self.cursor.execute(
                """
                CREATE TRIGGER IF NOT EXISTS debates_au AFTER UPDATE ON debates BEGIN
                    INSERT INTO debates_fts(debates_fts, rowid, topic, full_log, investment_json)
                    VALUES ('delete', old.id, old.topic, old.full_log, old.investment_json);
                    INSERT INTO debates_fts(rowid, topic, full_log, investment_json)
                    VALUES (new.id, new.topic, new.full_log, new.investment_json);
                END;
                """
            )
            self.cursor.execute(
                """
                CREATE TRIGGER IF NOT EXISTS summaries_ai AFTER INSERT ON summaries BEGIN
                    INSERT INTO summaries_fts(rowid, summary_text, keywords)
                    VALUES (new.id, new.summary_text, new.keywords);
                END;
                """
            )
            self.cursor.execute(
                """
                CREATE TRIGGER IF NOT EXISTS summaries_ad AFTER DELETE ON summaries BEGIN
                    INSERT INTO summaries_fts(summaries_fts, rowid, summary_text, keywords)
                    VALUES ('delete', old.id, old.summary_text, old.keywords);
                END;
                """
            )
            self.cursor.execute(
                """
                CREATE TRIGGER IF NOT EXISTS summaries_au AFTER UPDATE ON summaries BEGIN
                    INSERT INTO summaries_fts(summaries_fts, rowid, summary_text, keywords)
                    VALUES ('delete', old.id, old.summary_text, old.keywords);
                    INSERT INTO summaries_fts(rowid, summary_text, keywords)
                    VALUES (new.id, new.summary_text, new.keywords);
                END;
                """
            )
            self.conn.commit()

            # 기존 데이터 역색인
            self.cursor.execute("INSERT INTO debates_fts(debates_fts) VALUES ('rebuild')")
            self.cursor.execute("INSERT INTO summaries_fts(summaries_fts) VALUES ('rebuild')")
            self.conn.commit()
            self.fts_enabled = True
        except sqlite3.Error:
            self.fts_enabled = False

    def search_debates_fts(self, query: str, limit: int = 10) -> list[tuple]:
        if not self.fts_enabled or not query.strip():
            return []
        try:
            self.cursor.execute(
                """
                SELECT d.date, d.topic, d.investment_json, d.full_log
                FROM debates_fts f
                JOIN debates d ON d.id = f.rowid
                WHERE debates_fts MATCH ?
                ORDER BY bm25(debates_fts)
                LIMIT ?
                """,
                (query, limit),
            )
            return self.cursor.fetchall()
        except sqlite3.Error:
            return []

    def search_summaries_fts(self, query: str, limit: int = 10) -> list[tuple]:
        if not self.fts_enabled or not query.strip():
            return []
        try:
            self.cursor.execute(
                """
                SELECT s.target_date, s.summary_type, s.summary_text
                FROM summaries_fts f
                JOIN summaries s ON s.id = f.rowid
                WHERE summaries_fts MATCH ?
                ORDER BY bm25(summaries_fts)
                LIMIT ?
                """,
                (query, limit),
            )
            return self.cursor.fetchall()
        except sqlite3.Error:
            return []

    # --- 뉴스 관련 ---
    def save_daily_news(self, keyword: str, news_list: list):
        """특정 날짜의 크롤링된 뉴스를 저장 (당일 대화에서 계속 꺼내씀)"""
        today_str = datetime.now().strftime('%Y-%m-%d')
        # 이미 오늘 해당 키워드의 뉴스가 있는지 확인
        self.cursor.execute('SELECT id FROM daily_news WHERE date = ? AND keyword = ?', (today_str, keyword))
        if self.cursor.fetchone() is None:
            self.cursor.execute(
                'INSERT INTO daily_news (date, keyword, news_data) VALUES (?, ?, ?)',
                (today_str, keyword, json.dumps(news_list, ensure_ascii=False))
            )
            self.conn.commit()

    def get_daily_news(self, keyword: str) -> list:
        """오늘치 뉴스가 DB에 있으면 그것을 반환, 없으면 None 반환"""
        today_str = datetime.now().strftime('%Y-%m-%d')
        self.cursor.execute('SELECT news_data FROM daily_news WHERE date = ? AND keyword = ?', (today_str, keyword))
        row = self.cursor.fetchone()
        if row:
            return json.loads(row[0])
        return None

    # --- 토론 및 요약 관련 ---
    def save_debate(self, topic: str, full_log: str, consensus_status: str, investment_json: dict) -> int:
        today_str = datetime.now().strftime('%Y-%m-%d')
        self.cursor.execute(
            'INSERT INTO debates (date, topic, full_log, consensus_status, investment_json) VALUES (?, ?, ?, ?, ?)',
            (today_str, topic, full_log, consensus_status, json.dumps(investment_json, ensure_ascii=False))
        )
        self.conn.commit()
        return self.cursor.lastrowid

    def update_debate_log(self, debate_id: int, new_log: str):
        """기존 토론 테이블의 full_log(회의록)에 사용자의 일반 채팅 내역을 이어 붙임"""
        self.cursor.execute('SELECT full_log FROM debates WHERE id = ?', (debate_id,))
        row = self.cursor.fetchone()
        if row:
            updated_log = row[0] + "\n" + new_log
            self.cursor.execute('UPDATE debates SET full_log = ? WHERE id = ?', (updated_log, debate_id))
            self.conn.commit()

    def save_summary(self, summary_type: str, target_date: str, summary_text: str, keywords: str):
        self.cursor.execute(
            'INSERT INTO summaries (summary_type, target_date, summary_text, keywords) VALUES (?, ?, ?, ?)',
            (summary_type, target_date, summary_text, keywords)
        )
        self.conn.commit()

    def save_research_evidence(self, topic: str, query: str, evidence_payload: dict):
        today_str = datetime.now().strftime('%Y-%m-%d')
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        query_norm = self._normalize_query(query)
        self.cursor.execute(
            'INSERT INTO research_evidences (date, topic, query, query_norm, created_at, evidence_json) VALUES (?, ?, ?, ?, ?, ?)',
            (today_str, topic, query, query_norm, now_iso, json.dumps(evidence_payload, ensure_ascii=False))
        )
        self.conn.commit()

    def save_news_articles_bulk(self, articles: list[dict]):
        if not articles:
            return
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        for a in articles:
            self.cursor.execute(
                '''
                INSERT INTO news_articles (
                    article_key, date, source, source_type, section, title, url, canonical_url,
                    published_at, summary, content_hash, raw_json, event_key, fetched_at,
                    ingest_delay_sec, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_key) DO UPDATE SET
                    date=excluded.date,
                    source=excluded.source,
                    source_type=excluded.source_type,
                    section=excluded.section,
                    title=excluded.title,
                    url=excluded.url,
                    canonical_url=excluded.canonical_url,
                    published_at=excluded.published_at,
                    summary=excluded.summary,
                    content_hash=excluded.content_hash,
                    raw_json=excluded.raw_json,
                    event_key=excluded.event_key,
                    fetched_at=excluded.fetched_at,
                    ingest_delay_sec=excluded.ingest_delay_sec,
                    ingested_at=excluded.ingested_at
                ''',
                (
                    a.get("article_key"),
                    a.get("date"),
                    a.get("source"),
                    a.get("source_type"),
                    a.get("section"),
                    a.get("title"),
                    a.get("url"),
                    a.get("canonical_url"),
                    a.get("published_at"),
                    a.get("summary"),
                    a.get("content_hash"),
                    json.dumps(a.get("raw_json", {}), ensure_ascii=False),
                    a.get("event_key"),
                    a.get("fetched_at"),
                    int(a.get("ingest_delay_sec", 0) or 0),
                    now_iso,
                ),
            )
        self.conn.commit()

    def save_news_events_bulk(self, events: list[dict]):
        if not events:
            return
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        for e in events:
            self.cursor.execute(
                '''
                INSERT INTO news_events (
                    event_key, date, title, summary, source_count, article_count,
                    confidence, sample_urls, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_key) DO UPDATE SET
                    date=excluded.date,
                    title=excluded.title,
                    summary=excluded.summary,
                    source_count=excluded.source_count,
                    article_count=excluded.article_count,
                    confidence=excluded.confidence,
                    sample_urls=excluded.sample_urls,
                    updated_at=excluded.updated_at
                ''',
                (
                    e.get("event_key"),
                    e.get("date"),
                    e.get("title"),
                    e.get("summary"),
                    int(e.get("source_count", 0)),
                    int(e.get("article_count", 0)),
                    float(e.get("confidence", 0.0)),
                    json.dumps(e.get("sample_urls", []), ensure_ascii=False),
                    now_iso,
                ),
            )
        self.conn.commit()

    def get_latest_news_events(self, limit: int = 15) -> list[dict]:
        self.cursor.execute(
            '''
            SELECT event_key, date, title, summary, source_count, article_count, confidence, sample_urls
            FROM news_events
            ORDER BY date DESC, confidence DESC, article_count DESC
            LIMIT ?
            ''',
            (int(limit),),
        )
        rows = self.cursor.fetchall()
        out = []
        for r in rows:
            sample_urls = []
            try:
                sample_urls = json.loads(r[7]) if r[7] else []
            except Exception:
                sample_urls = []
            out.append(
                {
                    "event_key": r[0],
                    "date": r[1],
                    "title": r[2],
                    "summary": r[3],
                    "source_count": r[4],
                    "article_count": r[5],
                    "confidence": r[6],
                    "sample_urls": sample_urls,
                }
            )
        return out

    def get_cached_research_evidence(self, query: str, max_age_hours: int = 12) -> dict | None:
        if max_age_hours <= 0:
            return None
        qn = self._normalize_query(query)
        if not qn:
            return None
        self.cursor.execute(
            """
            SELECT evidence_json, created_at, date
            FROM research_evidences
            WHERE query_norm = ?
            ORDER BY COALESCE(created_at, date) DESC
            LIMIT 1
            """,
            (qn,),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        evidence_json, created_at, date_str = row
        try:
            payload = json.loads(evidence_json)
        except Exception:
            return None

        ts_source = created_at or date_str
        if not ts_source:
            return None

        ts = None
        try:
            ts = datetime.fromisoformat(str(ts_source).replace("Z", ""))
        except Exception:
            try:
                ts = datetime.strptime(str(ts_source), "%Y-%m-%d")
            except Exception:
                ts = None
        if not ts:
            return None
        age_hours = (datetime.now() - ts).total_seconds() / 3600.0
        if age_hours > max_age_hours:
            return None
        return payload

    def get_news_ingest_checkpoint(self, source: str) -> dict | None:
        key = (source or "").strip()
        if not key:
            return None
        self.cursor.execute(
            '''
            SELECT source, last_success_at, cursor_json, updated_at
            FROM news_ingest_checkpoints
            WHERE source = ?
            ''',
            (key,),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        cursor_payload = {}
        try:
            cursor_payload = json.loads(row[2]) if row[2] else {}
        except Exception:
            cursor_payload = {}
        return {
            "source": row[0],
            "last_success_at": row[1],
            "cursor": cursor_payload,
            "updated_at": row[3],
        }

    def save_news_ingest_checkpoint(self, source: str, last_success_at: str, cursor: dict | None = None):
        key = (source or "").strip()
        if not key:
            return
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        cursor_json = json.dumps(cursor or {}, ensure_ascii=False)
        self.cursor.execute(
            '''
            INSERT INTO news_ingest_checkpoints (source, last_success_at, cursor_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                last_success_at=excluded.last_success_at,
                cursor_json=excluded.cursor_json,
                updated_at=excluded.updated_at
            ''',
            (key, last_success_at, cursor_json, now_iso),
        )
        self.conn.commit()

    def purge_old_data(self, retention_days: int = 180):
        """
        운영 기간이 길어질 때 DB 비대화를 막기 위한 보존 정책.
        debates/summaries는 장기 기억을 위해 유지하고, 단기 캐시/증거 로그를 정리합니다.
        """
        if retention_days <= 0:
            return
        self.cursor.execute(
            "DELETE FROM daily_news WHERE date < date('now', ?)",
            (f"-{retention_days} day",)
        )
        self.cursor.execute(
            "DELETE FROM research_evidences WHERE date < date('now', ?)",
            (f"-{retention_days} day",)
        )
        self.cursor.execute(
            "DELETE FROM news_articles WHERE date < date('now', ?)",
            (f"-{retention_days} day",)
        )
        self.cursor.execute(
            "DELETE FROM news_events WHERE date < date('now', ?)",
            (f"-{retention_days} day",)
        )
        self.cursor.execute(
            "DELETE FROM news_ingest_checkpoints WHERE updated_at < datetime('now', ?)",
            (f"-{retention_days} day",)
        )
        self.conn.commit()

# 테스트용 실행
if __name__ == "__main__":
    db = DBManager()
    print("DB 및 테이블 생성 완료!")
