import sqlite3
import json
from datetime import datetime, timedelta
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'investment_bot.db')

class DBManager:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DB_PATH
        self._closed = False
        # data 폴더가 없으면 생성
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # timeout=20.0 및 check_same_thread=False 추가 (DB Lock 및 동시성 에러 방지)
        self.conn = sqlite3.connect(self.db_path, timeout=20.0, check_same_thread=False)
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

        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS news_context_packs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT,
                query TEXT,
                generated_at TEXT,
                pack_json TEXT
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

        # 8. 단기 이슈 시그널 이벤트
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS signal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE,
                event_key TEXT,
                date TEXT,
                detected_at TEXT,
                title TEXT,
                summary TEXT,
                score_total REAL,
                score_json TEXT,
                related_tickers TEXT,
                direction TEXT,
                urgency TEXT,
                confidence REAL,
                status TEXT,
                evidence_ids TEXT
            )
            '''
        )

        # 9. 이벤트 기반 주문 제안
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS signal_recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                ticker TEXT,
                side TEXT,
                size_rule TEXT,
                entry_rule TEXT,
                stop_rule TEXT,
                ttl_sec INTEGER,
                confidence REAL,
                rationale TEXT,
                status TEXT,
                created_at TEXT,
                expires_at TEXT
            )
            '''
        )

        # 10. 승인 요청 상태
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS approval_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE,
                requested_at TEXT,
                expires_at TEXT,
                approved_by TEXT,
                approved_at TEXT,
                rejected_by TEXT,
                rejected_at TEXT,
                state TEXT,
                note TEXT
            )
            '''
        )

        # 11. 페이퍼/실거래 체결 로그
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS order_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                ticker TEXT,
                side TEXT,
                qty REAL,
                order_type TEXT,
                submitted_at TEXT,
                filled_at TEXT,
                fill_price REAL,
                result TEXT,
                broker_order_id TEXT,
                detail_json TEXT
            )
            '''
        )

        # 12. 리스크 가드레일 전역 상태 (단일 row id=1)
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS risk_guardrail_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                kill_switch INTEGER,
                daily_order_limit INTEGER,
                hourly_order_limit INTEGER,
                daily_loss_limit REAL,
                updated_at TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS system_metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS paper_account_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                broker_name TEXT,
                mode TEXT,
                base_currency TEXT,
                cash_balance REAL,
                equity REAL,
                buying_power REAL,
                realized_pnl REAL,
                unrealized_pnl REAL,
                updated_at TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS paper_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT UNIQUE,
                qty REAL,
                avg_price REAL,
                market_price REAL,
                market_value REAL,
                realized_pnl REAL,
                unrealized_pnl REAL,
                updated_at TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS paper_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_order_id TEXT UNIQUE,
                broker_order_id TEXT,
                event_id TEXT,
                ticker TEXT,
                side TEXT,
                qty REAL,
                order_type TEXT,
                limit_price REAL,
                status TEXT,
                filled_qty REAL,
                filled_avg_price REAL,
                submitted_at TEXT,
                updated_at TEXT,
                notes TEXT,
                detail_json TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS paper_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_order_id TEXT,
                broker_order_id TEXT,
                event_id TEXT,
                ticker TEXT,
                side TEXT,
                qty REAL,
                fill_price REAL,
                filled_at TEXT,
                commission REAL,
                detail_json TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS signal_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                ticker TEXT,
                horizon TEXT,
                entry_price REAL,
                exit_price REAL,
                return_pct REAL,
                benchmark_ticker TEXT,
                benchmark_return_pct REAL,
                alpha_pct REAL,
                measured_at TEXT,
                source TEXT,
                detail_json TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS performance_run_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_name TEXT,
                split_label TEXT,
                horizon TEXT,
                window_start TEXT,
                window_end TEXT,
                signal_count INTEGER,
                measurement_count INTEGER,
                win_rate REAL,
                avg_return_pct REAL,
                avg_alpha_pct REAL,
                expectancy_pct REAL,
                profit_factor REAL,
                total_return_pct REAL,
                max_drawdown_pct REAL,
                created_at TEXT,
                detail_json TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS signal_attributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                ticker TEXT,
                horizon TEXT,
                category TEXT,
                label TEXT,
                weight REAL,
                return_pct REAL,
                alpha_pct REAL,
                measured_at TEXT,
                source TEXT,
                detail_json TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS debate_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE,
                event_key TEXT,
                ticker TEXT,
                direction TEXT,
                urgency TEXT,
                priority INTEGER,
                topic TEXT,
                reason TEXT,
                status TEXT,
                requested_at TEXT,
                claimed_at TEXT,
                completed_at TEXT,
                debate_id INTEGER,
                note TEXT,
                trigger_json TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS debate_quality_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                debate_id INTEGER UNIQUE,
                event_id TEXT,
                total_score REAL,
                status TEXT,
                scored_at TEXT,
                detail_json TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS investment_review_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                ticker TEXT,
                trigger_type TEXT,
                priority INTEGER,
                status TEXT,
                summary TEXT,
                detail_json TEXT,
                created_at TEXT,
                resolved_at TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS event_intake_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                event_key TEXT,
                audit_stage TEXT,
                route TEXT,
                reason TEXT,
                score_total REAL,
                quality_json TEXT,
                decision_json TEXT,
                created_at TEXT
            )
            '''
        )
        self.cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS context_selection_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                context_id TEXT,
                query TEXT,
                consumer TEXT,
                selected_json TEXT,
                excluded_json TEXT,
                quality_json TEXT,
                budget_json TEXT,
                created_at TEXT
            )
            '''
        )

        self._ensure_column("research_evidences", "query_norm", "TEXT")
        self._ensure_column("research_evidences", "created_at", "TEXT")
        self._ensure_column("news_articles", "fetched_at", "TEXT")
        self._ensure_column("news_articles", "ingest_delay_sec", "INTEGER")
        self._ensure_column("news_ingest_checkpoints", "last_attempt_at", "TEXT")
        self._ensure_column("news_ingest_checkpoints", "last_status", "TEXT")
        self._ensure_column("news_ingest_checkpoints", "last_error", "TEXT")
        self._ensure_column("news_ingest_checkpoints", "last_item_count", "INTEGER")
        self._ensure_column("signal_events", "verification_json", "TEXT")
        self._ensure_column("signal_events", "last_verified_at", "TEXT")
        self._ensure_column("debate_queue", "cost_gate_status", "TEXT")
        self._ensure_column("debate_queue", "cost_gate_json", "TEXT")

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
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_context_query_time ON news_context_packs(query_hash, generated_at)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_ckpt_source ON news_ingest_checkpoints(source)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_signal_events_date_score ON signal_events(date, score_total)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_signal_reco_event_status ON signal_recommendations(event_id, status)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_approval_event_state ON approval_requests(event_id, state)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_orders_event_submitted ON order_executions(event_id, submitted_at)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_paper_orders_event_status ON paper_orders(event_id, status)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_paper_fills_event_time ON paper_fills(event_id, filled_at)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_signal_perf_event_horizon ON signal_performance(event_id, horizon)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_perf_run_name_split ON performance_run_summaries(run_name, split_label, horizon)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_signal_attr_event_horizon ON signal_attributions(event_id, horizon, category)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_debate_queue_status_priority ON debate_queue(status, priority, requested_at)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_debate_queue_ticker_direction ON debate_queue(ticker, direction, status)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_debate_quality_event ON debate_quality_scores(event_id, total_score)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_review_triggers_status_priority ON investment_review_triggers(status, priority, created_at)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_review_triggers_event_type ON investment_review_triggers(event_id, ticker, trigger_type, status)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_intake_event_stage ON event_intake_audits(event_id, audit_stage, created_at)')
        self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_context_audit_context ON context_selection_audits(context_id, consumer, created_at)')
        self._upsert_default_guardrail_state()
        self._upsert_default_paper_account_state()
        self.conn.commit()

    def _get_metadata(self, key: str) -> str | None:
        self.cursor.execute(
            "SELECT value FROM system_metadata WHERE key = ? LIMIT 1",
            (key,),
        )
        row = self.cursor.fetchone()
        return str(row[0]) if row else None

    def _set_metadata(self, key: str, value: str):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            INSERT INTO system_metadata (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            ''',
            (key, value, now_iso),
        )
        self.conn.commit()

    def get_system_metadata(self, key: str) -> str | None:
        return self._get_metadata(key)

    def set_system_metadata(self, key: str, value: str):
        self._set_metadata(key, value)

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

    def _upsert_default_guardrail_state(self):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            INSERT INTO risk_guardrail_state (
                id, kill_switch, daily_order_limit, hourly_order_limit, daily_loss_limit, updated_at
            ) VALUES (1, 1, 5, 2, 200000, ?)
            ON CONFLICT(id) DO NOTHING
            ''',
            (now_iso,),
        )

    def _upsert_default_paper_account_state(self):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        starting_cash = float(os.getenv("PAPER_STARTING_CASH", "100000"))
        self.cursor.execute(
            '''
            INSERT INTO paper_account_state (
                id, broker_name, mode, base_currency, cash_balance, equity, buying_power,
                realized_pnl, unrealized_pnl, updated_at
            ) VALUES (1, 'paper', 'paper', 'USD', ?, ?, ?, 0, 0, ?)
            ON CONFLICT(id) DO NOTHING
            ''',
            (starting_cash, starting_cash, starting_cash, now_iso),
        )

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

            # 기존 데이터 역색인은 최초 1회만 수행
            if self._get_metadata("fts_bootstrapped_v1") != "1":
                self.cursor.execute("INSERT INTO debates_fts(debates_fts) VALUES ('rebuild')")
                self.cursor.execute("INSERT INTO summaries_fts(summaries_fts) VALUES ('rebuild')")
                self.conn.commit()
                self._set_metadata("fts_bootstrapped_v1", "1")
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

    def list_debates_by_date(self, target_date: str) -> list[tuple]:
        self.cursor.execute(
            """
            SELECT topic, consensus_status, investment_json
            FROM debates
            WHERE date = ?
            ORDER BY id ASC
            """,
            (target_date,),
        )
        return self.cursor.fetchall()

    def list_summaries_by_type_since(self, summary_type: str, since_target_date: str) -> list[tuple]:
        self.cursor.execute(
            """
            SELECT target_date, summary_text
            FROM summaries
            WHERE summary_type = ? AND target_date >= ?
            ORDER BY target_date ASC, id ASC
            """,
            (summary_type, since_target_date),
        )
        return self.cursor.fetchall()

    def search_debates_like(self, keyword: str, limit: int = 10) -> list[tuple]:
        kw_like = f"%{keyword}%"
        self.cursor.execute(
            """
            SELECT date, topic, investment_json
            FROM debates
            WHERE topic LIKE ? OR investment_json LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (kw_like, kw_like, int(limit)),
        )
        return self.cursor.fetchall()

    def search_summaries_like(self, keyword: str, limit: int = 10) -> list[tuple]:
        kw_like = f"%{keyword}%"
        self.cursor.execute(
            """
            SELECT target_date, summary_type, summary_text
            FROM summaries
            WHERE summary_text LIKE ? OR keywords LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (kw_like, kw_like, int(limit)),
        )
        return self.cursor.fetchall()

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

    def get_debate(self, debate_id: int) -> dict | None:
        self.cursor.execute(
            '''
            SELECT id, date, topic, full_log, consensus_status, investment_json
            FROM debates
            WHERE id = ?
            ''',
            (int(debate_id),),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "date": row[1],
            "topic": row[2] or "",
            "full_log": row[3] or "",
            "consensus_status": row[4] or "",
            "investment_json": json.loads(row[5]) if row[5] else {},
        }

    def list_recent_debates(self, limit: int = 10) -> list[dict]:
        self.cursor.execute(
            '''
            SELECT id, date, topic, consensus_status, investment_json
            FROM debates
            ORDER BY id DESC
            LIMIT ?
            ''',
            (int(limit),),
        )
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "id": int(row[0]),
                    "date": row[1],
                    "topic": row[2] or "",
                    "consensus_status": row[3] or "",
                    "investment_json": json.loads(row[4]) if row[4] else {},
                }
            )
        return out

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
            SELECT event_key, date, title, summary, source_count, article_count, confidence, sample_urls, updated_at
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
                    "updated_at": r[8],
                }
            )
        return out

    def list_news_events_since(self, since_date: str, limit: int = 50) -> list[dict]:
        self.cursor.execute(
            '''
            SELECT event_key, date, title, summary, source_count, article_count, confidence, sample_urls, updated_at
            FROM news_events
            WHERE date >= ?
            ORDER BY date DESC, confidence DESC, article_count DESC
            LIMIT ?
            ''',
            (since_date, int(limit)),
        )
        rows = self.cursor.fetchall()
        out = []
        for r in rows:
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
                    "updated_at": r[8],
                }
            )
        return out

    def list_news_articles_for_events(self, event_keys: list[str], limit_per_event: int = 8) -> dict[str, list[dict]]:
        keys = [str(k or "").strip() for k in event_keys if str(k or "").strip()]
        if not keys:
            return {}
        out: dict[str, list[dict]] = {k: [] for k in keys}
        placeholders = ",".join("?" for _ in keys)
        self.cursor.execute(
            f'''
            SELECT event_key, article_key, date, source, source_type, section, title, url,
                   canonical_url, published_at, summary, raw_json, fetched_at, ingest_delay_sec
            FROM news_articles
            WHERE event_key IN ({placeholders})
            ORDER BY event_key, published_at DESC, source ASC
            ''',
            keys,
        )
        rows = self.cursor.fetchall()
        for r in rows:
            event_key = r[0]
            if event_key not in out or len(out[event_key]) >= int(limit_per_event):
                continue
            try:
                raw_json = json.loads(r[11]) if r[11] else {}
            except Exception:
                raw_json = {}
            out[event_key].append(
                {
                    "event_key": event_key,
                    "article_key": r[1],
                    "date": r[2],
                    "source": r[3],
                    "source_type": r[4],
                    "section": r[5],
                    "title": r[6],
                    "url": r[7],
                    "canonical_url": r[8],
                    "published_at": r[9],
                    "summary": r[10],
                    "raw_json": raw_json,
                    "fetched_at": r[12],
                    "ingest_delay_sec": int(r[13] or 0),
                }
            )
        return out

    def save_news_context_pack(self, query_hash: str, query: str, pack: dict):
        generated_at = str(pack.get("generated_at") or datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
        self.cursor.execute(
            '''
            INSERT INTO news_context_packs (query_hash, query, generated_at, pack_json)
            VALUES (?, ?, ?, ?)
            ''',
            (
                str(query_hash or ""),
                str(query or ""),
                generated_at,
                json.dumps(pack or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def get_latest_news_context_pack(self, query_hash: str) -> dict | None:
        self.cursor.execute(
            '''
            SELECT pack_json FROM news_context_packs
            WHERE query_hash = ?
            ORDER BY generated_at DESC, id DESC
            LIMIT 1
            ''',
            (str(query_hash or ""),),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0]) if row[0] else None
        except Exception:
            return None

    def get_news_events_for_context(
        self,
        query_terms: list[str],
        tickers: list[str] | None = None,
        limit: int = 8,
        lookback_hours: int = 96,
    ) -> list[dict]:
        now = datetime.now()
        min_dt = now - timedelta(hours=max(1, int(lookback_hours)))
        min_date = min_dt.strftime('%Y-%m-%d')
        terms = [str(t).strip() for t in [*(query_terms or []), *(tickers or [])] if str(t).strip()]
        params: list[object] = [min_date]
        sql = (
            "SELECT event_key, date, title, summary, source_count, article_count, confidence, sample_urls, updated_at "
            "FROM news_events WHERE date >= ?"
        )
        if terms:
            term_clauses = []
            for term in terms[:12]:
                like = f"%{term}%"
                term_clauses.append("(title LIKE ? OR summary LIKE ?)")
                params.extend([like, like])
            sql += " AND (" + " OR ".join(term_clauses) + ")"
        sql += " ORDER BY date DESC, confidence DESC, article_count DESC LIMIT ?"
        params.append(int(limit))
        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()

        out = []
        lowered_terms = [t.lower() for t in terms]
        for r in rows:
            try:
                sample_urls = json.loads(r[7]) if r[7] else []
            except Exception:
                sample_urls = []
            body = f"{r[2] or ''}\n{r[3] or ''}".lower()
            matched = [t for t in terms if t.lower() in body][:6]
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
                    "updated_at": r[8],
                    "matched_terms": matched if lowered_terms else [],
                }
            )
        return out[:limit]

    def get_recent_research_context(
        self,
        query_terms: list[str],
        limit: int = 6,
        lookback_hours: int = 120,
    ) -> list[dict]:
        min_dt = datetime.now() - timedelta(hours=max(1, int(lookback_hours)))
        min_iso = min_dt.strftime('%Y-%m-%dT%H:%M:%S')
        min_date = min_dt.strftime('%Y-%m-%d')
        terms = [str(t).strip() for t in (query_terms or []) if str(t).strip()]
        clauses = ["COALESCE(created_at, date) >= ?", "date >= ?"]
        params: list[object] = [min_iso, min_date]
        for term in terms[:10]:
            like = f"%{term}%"
            clauses.append("(topic LIKE ? OR query LIKE ?)")
            params.extend([like, like])
        sql = (
            "SELECT topic, query, created_at, evidence_json "
            "FROM research_evidences "
            f"WHERE ({clauses[0]} OR {clauses[1]})"
        )
        if len(clauses) > 2:
            sql += " AND (" + " OR ".join(clauses[2:]) + ")"
        sql += " ORDER BY COALESCE(created_at, date) DESC LIMIT ?"
        params.append(int(limit))
        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        out = []
        for topic, query, created_at, evidence_json in rows:
            try:
                payload = json.loads(evidence_json) if evidence_json else {}
            except Exception:
                payload = {}
            evidences = payload.get("evidences", []) if isinstance(payload, dict) else []
            sources = []
            quality_values = []
            source_tiers = []
            if isinstance(evidences, list):
                for ev in evidences[:3]:
                    if isinstance(ev, dict):
                        title = str(ev.get("title", "")).strip()
                        domain = str(ev.get("domain", "")).strip()
                        url = str(ev.get("url", "")).strip()
                        source_quality = float(ev.get("source_quality", 0.0) or 0.0)
                        source_tier = str(ev.get("source_tier", "")).strip()
                        if source_quality:
                            quality_values.append(source_quality)
                        if source_tier:
                            source_tiers.append(source_tier)
                        sources.append(
                            {
                                "title": title,
                                "domain": domain,
                                "url": url,
                                "source_quality": source_quality,
                                "source_tier": source_tier,
                            }
                        )
            out.append(
                {
                    "topic": topic,
                    "query": query,
                    "created_at": created_at,
                    "summary": payload.get("summary", "") if isinstance(payload, dict) else "",
                    "status": payload.get("status", "") if isinstance(payload, dict) else "",
                    "limitations": payload.get("limitations", []) if isinstance(payload, dict) else [],
                    "evidence_count": len(evidences) if isinstance(evidences, list) else 0,
                    "sources": sources,
                    "source_quality_avg": round(sum(quality_values) / len(quality_values), 3) if quality_values else 0.0,
                    "source_tiers": list(dict.fromkeys(source_tiers)),
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
            SELECT source, last_success_at, cursor_json, updated_at,
                   last_attempt_at, last_status, last_error, last_item_count
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
            "last_attempt_at": row[4],
            "last_status": row[5] or "",
            "last_error": row[6] or "",
            "last_item_count": int(row[7] or 0),
        }

    def save_news_ingest_checkpoint(self, source: str, last_success_at: str, cursor: dict | None = None):
        key = (source or "").strip()
        if not key:
            return
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        cursor_json = json.dumps(cursor or {}, ensure_ascii=False)
        self.cursor.execute(
            '''
            INSERT INTO news_ingest_checkpoints (
                source, last_success_at, cursor_json, updated_at,
                last_attempt_at, last_status, last_error, last_item_count
            )
            VALUES (?, ?, ?, ?, ?, 'success', '', ?)
            ON CONFLICT(source) DO UPDATE SET
                last_success_at=excluded.last_success_at,
                cursor_json=excluded.cursor_json,
                updated_at=excluded.updated_at,
                last_attempt_at=excluded.last_attempt_at,
                last_status='success',
                last_error='',
                last_item_count=excluded.last_item_count
            ''',
            (key, last_success_at, cursor_json, now_iso, now_iso, int((cursor or {}).get("saved_articles", 0) or 0)),
        )
        self.conn.commit()

    def record_news_ingest_attempt(
        self,
        source: str,
        status: str,
        item_count: int = 0,
        error: str = "",
        cursor: dict | None = None,
        success_at: str | None = None,
        attempted_at: str | None = None,
    ):
        key = (source or "").strip()
        if not key:
            return
        attempt_iso = attempted_at or datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        cursor_json = json.dumps(cursor or {}, ensure_ascii=False)
        success_value = success_at
        self.cursor.execute(
            '''
            INSERT INTO news_ingest_checkpoints (
                source, last_success_at, cursor_json, updated_at,
                last_attempt_at, last_status, last_error, last_item_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                last_success_at=COALESCE(excluded.last_success_at, news_ingest_checkpoints.last_success_at),
                cursor_json=CASE
                    WHEN excluded.last_success_at IS NOT NULL THEN excluded.cursor_json
                    ELSE news_ingest_checkpoints.cursor_json
                END,
                updated_at=excluded.updated_at,
                last_attempt_at=excluded.last_attempt_at,
                last_status=excluded.last_status,
                last_error=excluded.last_error,
                last_item_count=excluded.last_item_count
            ''',
            (
                key,
                success_value,
                cursor_json,
                attempt_iso,
                attempt_iso,
                str(status or "").strip(),
                str(error or "")[:500],
                max(0, int(item_count or 0)),
            ),
        )
        self.conn.commit()

    def list_news_ingest_health(self, limit: int = 20) -> list[dict]:
        self.cursor.execute(
            '''
            SELECT source, last_success_at, cursor_json, updated_at,
                   last_attempt_at, last_status, last_error, last_item_count
            FROM news_ingest_checkpoints
            ORDER BY updated_at DESC, source ASC
            LIMIT ?
            ''',
            (int(limit),),
        )
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            try:
                cursor_payload = json.loads(row[2]) if row[2] else {}
            except Exception:
                cursor_payload = {}
            out.append(
                {
                    "source": row[0],
                    "last_success_at": row[1],
                    "cursor": cursor_payload,
                    "updated_at": row[3],
                    "last_attempt_at": row[4],
                    "last_status": row[5] or "",
                    "last_error": row[6] or "",
                    "last_item_count": int(row[7] or 0),
                }
            )
        return out

    # --- 단기 시그널/승인/체결 ---
    def get_guardrail_state(self) -> dict:
        self.cursor.execute(
            '''
            SELECT id, kill_switch, daily_order_limit, hourly_order_limit, daily_loss_limit, updated_at
            FROM risk_guardrail_state
            WHERE id = 1
            '''
        )
        row = self.cursor.fetchone()
        if not row:
            self._upsert_default_guardrail_state()
            self.conn.commit()
            return self.get_guardrail_state()
        return {
            "id": row[0],
            "kill_switch": bool(int(row[1] or 0)),
            "daily_order_limit": int(row[2] or 0),
            "hourly_order_limit": int(row[3] or 0),
            "daily_loss_limit": float(row[4] or 0.0),
            "updated_at": row[5],
        }

    def get_paper_account_state(self) -> dict:
        self.cursor.execute(
            '''
            SELECT id, broker_name, mode, base_currency, cash_balance, equity, buying_power,
                   realized_pnl, unrealized_pnl, updated_at
            FROM paper_account_state
            WHERE id = 1
            '''
        )
        row = self.cursor.fetchone()
        if not row:
            self._upsert_default_paper_account_state()
            self.conn.commit()
            return self.get_paper_account_state()
        return {
            "id": row[0],
            "broker_name": row[1],
            "mode": row[2],
            "base_currency": row[3],
            "cash_balance": float(row[4] or 0.0),
            "equity": float(row[5] or 0.0),
            "buying_power": float(row[6] or 0.0),
            "realized_pnl": float(row[7] or 0.0),
            "unrealized_pnl": float(row[8] or 0.0),
            "updated_at": row[9],
        }

    def update_paper_account_state(self, fields: dict):
        current = self.get_paper_account_state()
        merged = {**current, **fields}
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            UPDATE paper_account_state
            SET broker_name = ?, mode = ?, base_currency = ?, cash_balance = ?, equity = ?, buying_power = ?,
                realized_pnl = ?, unrealized_pnl = ?, updated_at = ?
            WHERE id = 1
            ''',
            (
                merged.get("broker_name", "paper"),
                merged.get("mode", "paper"),
                merged.get("base_currency", "USD"),
                float(merged.get("cash_balance", 0.0)),
                float(merged.get("equity", 0.0)),
                float(merged.get("buying_power", 0.0)),
                float(merged.get("realized_pnl", 0.0)),
                float(merged.get("unrealized_pnl", 0.0)),
                now_iso,
            ),
        )
        self.conn.commit()

    def reset_paper_account_state(self, starting_cash: float = 100000.0):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute("DELETE FROM paper_positions")
        self.cursor.execute("DELETE FROM paper_orders")
        self.cursor.execute("DELETE FROM paper_fills")
        self.cursor.execute(
            '''
            INSERT INTO paper_account_state (
                id, broker_name, mode, base_currency, cash_balance, equity, buying_power,
                realized_pnl, unrealized_pnl, updated_at
            ) VALUES (1, 'paper', 'paper', 'USD', ?, ?, ?, 0, 0, ?)
            ON CONFLICT(id) DO UPDATE SET
                cash_balance=excluded.cash_balance,
                equity=excluded.equity,
                buying_power=excluded.buying_power,
                realized_pnl=0,
                unrealized_pnl=0,
                updated_at=excluded.updated_at
            ''',
            (float(starting_cash), float(starting_cash), float(starting_cash), now_iso),
        )
        self.conn.commit()

    def get_paper_position(self, ticker: str) -> dict | None:
        self.cursor.execute(
            '''
            SELECT ticker, qty, avg_price, market_price, market_value, realized_pnl, unrealized_pnl, updated_at
            FROM paper_positions
            WHERE ticker = ?
            ''',
            (ticker,),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "ticker": row[0],
            "qty": float(row[1] or 0.0),
            "avg_price": float(row[2] or 0.0),
            "market_price": float(row[3] or 0.0),
            "market_value": float(row[4] or 0.0),
            "realized_pnl": float(row[5] or 0.0),
            "unrealized_pnl": float(row[6] or 0.0),
            "updated_at": row[7],
        }

    def list_paper_positions(self) -> list[dict]:
        self.cursor.execute(
            '''
            SELECT ticker, qty, avg_price, market_price, market_value, realized_pnl, unrealized_pnl, updated_at
            FROM paper_positions
            ORDER BY ticker ASC
            '''
        )
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "ticker": row[0],
                    "qty": float(row[1] or 0.0),
                    "avg_price": float(row[2] or 0.0),
                    "market_price": float(row[3] or 0.0),
                    "market_value": float(row[4] or 0.0),
                    "realized_pnl": float(row[5] or 0.0),
                    "unrealized_pnl": float(row[6] or 0.0),
                    "updated_at": row[7],
                }
            )
        return out

    def upsert_paper_position(self, row: dict):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            INSERT INTO paper_positions (
                ticker, qty, avg_price, market_price, market_value, realized_pnl, unrealized_pnl, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                qty=excluded.qty,
                avg_price=excluded.avg_price,
                market_price=excluded.market_price,
                market_value=excluded.market_value,
                realized_pnl=excluded.realized_pnl,
                unrealized_pnl=excluded.unrealized_pnl,
                updated_at=excluded.updated_at
            ''',
            (
                row.get("ticker"),
                float(row.get("qty", 0.0)),
                float(row.get("avg_price", 0.0)),
                float(row.get("market_price", 0.0)),
                float(row.get("market_value", 0.0)),
                float(row.get("realized_pnl", 0.0)),
                float(row.get("unrealized_pnl", 0.0)),
                now_iso,
            ),
        )
        self.conn.commit()

    def delete_paper_position(self, ticker: str):
        self.cursor.execute("DELETE FROM paper_positions WHERE ticker = ?", (ticker,))
        self.conn.commit()

    def save_paper_order(self, row: dict):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            INSERT INTO paper_orders (
                client_order_id, broker_order_id, event_id, ticker, side, qty, order_type, limit_price,
                status, filled_qty, filled_avg_price, submitted_at, updated_at, notes, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                broker_order_id=excluded.broker_order_id,
                status=excluded.status,
                filled_qty=excluded.filled_qty,
                filled_avg_price=excluded.filled_avg_price,
                updated_at=excluded.updated_at,
                notes=excluded.notes,
                detail_json=excluded.detail_json
            ''',
            (
                row.get("client_order_id"),
                row.get("broker_order_id"),
                row.get("event_id"),
                row.get("ticker"),
                row.get("side"),
                float(row.get("qty", 0.0)),
                row.get("order_type", "market"),
                float(row.get("limit_price", 0.0)) if row.get("limit_price") is not None else None,
                row.get("status", "submitted"),
                float(row.get("filled_qty", 0.0)),
                float(row.get("filled_avg_price", 0.0)),
                row.get("submitted_at") or now_iso,
                now_iso,
                row.get("notes", ""),
                json.dumps(row.get("detail_json", {}), ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def get_paper_order(self, client_order_id: str) -> dict | None:
        self.cursor.execute(
            '''
            SELECT client_order_id, broker_order_id, event_id, ticker, side, qty, order_type, limit_price,
                   status, filled_qty, filled_avg_price, submitted_at, updated_at, notes, detail_json
            FROM paper_orders
            WHERE client_order_id = ?
            ''',
            (client_order_id,),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "client_order_id": row[0],
            "broker_order_id": row[1],
            "event_id": row[2],
            "ticker": row[3],
            "side": row[4],
            "qty": float(row[5] or 0.0),
            "order_type": row[6],
            "limit_price": float(row[7] or 0.0) if row[7] is not None else None,
            "status": row[8],
            "filled_qty": float(row[9] or 0.0),
            "filled_avg_price": float(row[10] or 0.0),
            "submitted_at": row[11],
            "updated_at": row[12],
            "notes": row[13] or "",
            "detail_json": json.loads(row[14]) if row[14] else {},
        }

    def list_paper_orders(self, limit: int = 50, statuses: list[str] | None = None) -> list[dict]:
        params: list[object] = []
        sql = (
            "SELECT client_order_id, broker_order_id, event_id, ticker, side, qty, order_type, limit_price, "
            "status, filled_qty, filled_avg_price, submitted_at, updated_at, notes, detail_json "
            "FROM paper_orders"
        )
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" WHERE status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY submitted_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "client_order_id": row[0],
                    "broker_order_id": row[1],
                    "event_id": row[2],
                    "ticker": row[3],
                    "side": row[4],
                    "qty": float(row[5] or 0.0),
                    "order_type": row[6],
                    "limit_price": float(row[7] or 0.0) if row[7] is not None else None,
                    "status": row[8],
                    "filled_qty": float(row[9] or 0.0),
                    "filled_avg_price": float(row[10] or 0.0),
                    "submitted_at": row[11],
                    "updated_at": row[12],
                    "notes": row[13] or "",
                    "detail_json": json.loads(row[14]) if row[14] else {},
                }
            )
        return out

    def save_paper_fill(self, row: dict):
        self.cursor.execute(
            '''
            INSERT INTO paper_fills (
                client_order_id, broker_order_id, event_id, ticker, side, qty, fill_price, filled_at,
                commission, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                row.get("client_order_id"),
                row.get("broker_order_id"),
                row.get("event_id"),
                row.get("ticker"),
                row.get("side"),
                float(row.get("qty", 0.0)),
                float(row.get("fill_price", 0.0)),
                row.get("filled_at"),
                float(row.get("commission", 0.0)),
                json.dumps(row.get("detail_json", {}), ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def list_paper_fills(self, limit: int = 100) -> list[dict]:
        self.cursor.execute(
            '''
            SELECT client_order_id, broker_order_id, event_id, ticker, side, qty, fill_price, filled_at,
                   commission, detail_json
            FROM paper_fills
            ORDER BY filled_at DESC, id DESC
            LIMIT ?
            ''',
            (int(limit),),
        )
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "client_order_id": row[0],
                    "broker_order_id": row[1],
                    "event_id": row[2],
                    "ticker": row[3],
                    "side": row[4],
                    "qty": float(row[5] or 0.0),
                    "fill_price": float(row[6] or 0.0),
                    "filled_at": row[7],
                    "commission": float(row[8] or 0.0),
                    "detail_json": json.loads(row[9]) if row[9] else {},
                }
            )
        return out

    def save_signal_performance(self, row: dict):
        self.cursor.execute(
            '''
            INSERT INTO signal_performance (
                event_id, ticker, horizon, entry_price, exit_price, return_pct, benchmark_ticker,
                benchmark_return_pct, alpha_pct, measured_at, source, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                row.get("event_id"),
                row.get("ticker"),
                row.get("horizon"),
                float(row.get("entry_price", 0.0)),
                float(row.get("exit_price", 0.0)),
                float(row.get("return_pct", 0.0)),
                row.get("benchmark_ticker"),
                float(row.get("benchmark_return_pct", 0.0)),
                float(row.get("alpha_pct", 0.0)),
                row.get("measured_at"),
                row.get("source", "replay"),
                json.dumps(row.get("detail_json", {}), ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def list_signal_performance(self, event_id: str | None = None, limit: int = 100) -> list[dict]:
        params: list[object] = []
        sql = (
            "SELECT event_id, ticker, horizon, entry_price, exit_price, return_pct, benchmark_ticker, "
            "benchmark_return_pct, alpha_pct, measured_at, source, detail_json "
            "FROM signal_performance"
        )
        if event_id:
            sql += " WHERE event_id = ?"
            params.append(event_id)
        sql += " ORDER BY measured_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "event_id": row[0],
                    "ticker": row[1],
                    "horizon": row[2],
                    "entry_price": float(row[3] or 0.0),
                    "exit_price": float(row[4] or 0.0),
                    "return_pct": float(row[5] or 0.0),
                    "benchmark_ticker": row[6],
                    "benchmark_return_pct": float(row[7] or 0.0),
                    "alpha_pct": float(row[8] or 0.0),
                    "measured_at": row[9],
                    "source": row[10],
                    "detail_json": json.loads(row[11]) if row[11] else {},
                }
            )
        return out

    def save_performance_run_summary(self, row: dict):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            INSERT INTO performance_run_summaries (
                run_name, split_label, horizon, window_start, window_end, signal_count, measurement_count,
                win_rate, avg_return_pct, avg_alpha_pct, expectancy_pct, profit_factor,
                total_return_pct, max_drawdown_pct, created_at, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                row.get("run_name"),
                row.get("split_label"),
                row.get("horizon"),
                row.get("window_start"),
                row.get("window_end"),
                int(row.get("signal_count", 0) or 0),
                int(row.get("measurement_count", 0) or 0),
                float(row.get("win_rate", 0.0) or 0.0),
                float(row.get("avg_return_pct", 0.0) or 0.0),
                float(row.get("avg_alpha_pct", 0.0) or 0.0),
                float(row.get("expectancy_pct", 0.0) or 0.0),
                float(row.get("profit_factor", 0.0) or 0.0),
                float(row.get("total_return_pct", 0.0) or 0.0),
                float(row.get("max_drawdown_pct", 0.0) or 0.0),
                row.get("created_at") or now_iso,
                json.dumps(row.get("detail_json", {}), ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def list_performance_run_summaries(self, run_name: str | None = None, limit: int = 50) -> list[dict]:
        params: list[object] = []
        sql = (
            "SELECT run_name, split_label, horizon, window_start, window_end, signal_count, measurement_count, "
            "win_rate, avg_return_pct, avg_alpha_pct, expectancy_pct, profit_factor, total_return_pct, "
            "max_drawdown_pct, created_at, detail_json FROM performance_run_summaries"
        )
        if run_name:
            sql += " WHERE run_name = ?"
            params.append(run_name)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "run_name": row[0],
                    "split_label": row[1],
                    "horizon": row[2],
                    "window_start": row[3],
                    "window_end": row[4],
                    "signal_count": int(row[5] or 0),
                    "measurement_count": int(row[6] or 0),
                    "win_rate": float(row[7] or 0.0),
                    "avg_return_pct": float(row[8] or 0.0),
                    "avg_alpha_pct": float(row[9] or 0.0),
                    "expectancy_pct": float(row[10] or 0.0),
                    "profit_factor": float(row[11] or 0.0),
                    "total_return_pct": float(row[12] or 0.0),
                    "max_drawdown_pct": float(row[13] or 0.0),
                    "created_at": row[14],
                    "detail_json": json.loads(row[15]) if row[15] else {},
                }
            )
        return out

    def save_signal_attribution(self, row: dict):
        self.cursor.execute(
            '''
            INSERT INTO signal_attributions (
                event_id, ticker, horizon, category, label, weight, return_pct, alpha_pct,
                measured_at, source, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                row.get("event_id"),
                row.get("ticker"),
                row.get("horizon"),
                row.get("category"),
                row.get("label"),
                float(row.get("weight", 1.0) or 1.0),
                float(row.get("return_pct", 0.0) or 0.0),
                float(row.get("alpha_pct", 0.0) or 0.0),
                row.get("measured_at"),
                row.get("source", "replay"),
                json.dumps(row.get("detail_json", {}), ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def list_signal_attributions(
        self,
        category: str | None = None,
        horizon: str | None = None,
        event_id: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        params: list[object] = []
        sql = (
            "SELECT event_id, ticker, horizon, category, label, weight, return_pct, alpha_pct, "
            "measured_at, source, detail_json FROM signal_attributions WHERE 1=1"
        )
        if category:
            sql += " AND category = ?"
            params.append(category)
        if horizon:
            sql += " AND horizon = ?"
            params.append(horizon)
        if event_id:
            sql += " AND event_id = ?"
            params.append(event_id)
        sql += " ORDER BY measured_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "event_id": row[0],
                    "ticker": row[1],
                    "horizon": row[2],
                    "category": row[3],
                    "label": row[4],
                    "weight": float(row[5] or 1.0),
                    "return_pct": float(row[6] or 0.0),
                    "alpha_pct": float(row[7] or 0.0),
                    "measured_at": row[8],
                    "source": row[9],
                    "detail_json": json.loads(row[10]) if row[10] else {},
                }
            )
        return out

    def save_event_intake_audit(self, row: dict):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            INSERT INTO event_intake_audits (
                event_id, event_key, audit_stage, route, reason, score_total,
                quality_json, decision_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                row.get("event_id"),
                row.get("event_key"),
                row.get("audit_stage", "signal_intake"),
                row.get("route", ""),
                row.get("reason", ""),
                float(row.get("score_total", 0.0) or 0.0),
                json.dumps(row.get("quality_json", {}) or {}, ensure_ascii=False),
                json.dumps(row.get("decision_json", {}) or {}, ensure_ascii=False),
                row.get("created_at") or now_iso,
            ),
        )
        self.conn.commit()

    def list_event_intake_audits(
        self,
        event_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        params: list[object] = []
        sql = (
            "SELECT event_id, event_key, audit_stage, route, reason, score_total, "
            "quality_json, decision_json, created_at FROM event_intake_audits"
        )
        if event_id:
            sql += " WHERE event_id = ?"
            params.append(event_id)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        self.cursor.execute(sql, params)
        out = []
        for row in self.cursor.fetchall():
            out.append(
                {
                    "event_id": row[0] or "",
                    "event_key": row[1] or "",
                    "audit_stage": row[2] or "",
                    "route": row[3] or "",
                    "reason": row[4] or "",
                    "score_total": float(row[5] or 0.0),
                    "quality_json": json.loads(row[6]) if row[6] else {},
                    "decision_json": json.loads(row[7]) if row[7] else {},
                    "created_at": row[8],
                }
            )
        return out

    def save_context_selection_audit(self, row: dict):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            INSERT INTO context_selection_audits (
                context_id, query, consumer, selected_json, excluded_json,
                quality_json, budget_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                row.get("context_id"),
                row.get("query", ""),
                row.get("consumer", ""),
                json.dumps(row.get("selected_json", {}) or {}, ensure_ascii=False),
                json.dumps(row.get("excluded_json", {}) or {}, ensure_ascii=False),
                json.dumps(row.get("quality_json", {}) or {}, ensure_ascii=False),
                json.dumps(row.get("budget_json", {}) or {}, ensure_ascii=False),
                row.get("created_at") or now_iso,
            ),
        )
        self.conn.commit()

    def list_context_selection_audits(
        self,
        context_id: str | None = None,
        consumer: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        params: list[object] = []
        sql = (
            "SELECT context_id, query, consumer, selected_json, excluded_json, "
            "quality_json, budget_json, created_at FROM context_selection_audits WHERE 1=1"
        )
        if context_id:
            sql += " AND context_id = ?"
            params.append(context_id)
        if consumer:
            sql += " AND consumer = ?"
            params.append(consumer)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        self.cursor.execute(sql, params)
        out = []
        for row in self.cursor.fetchall():
            out.append(
                {
                    "context_id": row[0] or "",
                    "query": row[1] or "",
                    "consumer": row[2] or "",
                    "selected_json": json.loads(row[3]) if row[3] else {},
                    "excluded_json": json.loads(row[4]) if row[4] else {},
                    "quality_json": json.loads(row[5]) if row[5] else {},
                    "budget_json": json.loads(row[6]) if row[6] else {},
                    "created_at": row[7],
                }
            )
        return out

    def set_kill_switch(self, enabled: bool):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            UPDATE risk_guardrail_state
            SET kill_switch = ?, updated_at = ?
            WHERE id = 1
            ''',
            (1 if enabled else 0, now_iso),
        )
        self.conn.commit()

    def enqueue_debate_candidate(
        self,
        row: dict,
        cooldown_minutes: int = 30,
    ) -> dict[str, object]:
        now = datetime.now()
        now_iso = now.strftime('%Y-%m-%dT%H:%M:%S')
        event_id = str(row.get("event_id") or "").strip()
        if not event_id:
            return {"created": False, "merged": False, "queue_id": None, "reason": "missing_event_id"}

        topic = str(row.get("topic") or "").strip()
        event_key = str(row.get("event_key") or "").strip()
        ticker = str(row.get("ticker") or "").strip()
        direction = str(row.get("direction") or "").strip()
        priority = int(row.get("priority", 50) or 50)
        urgency = str(row.get("urgency") or "").strip()
        reason = str(row.get("reason") or "").strip()
        note = str(row.get("note") or "").strip()
        trigger_json = row.get("trigger_json", {}) or {}
        status = str(row.get("status") or "pending").strip() or "pending"
        cost_gate_status = str(row.get("cost_gate_status") or "").strip()
        cost_gate_json = row.get("cost_gate_json", {}) or {}
        cooldown_threshold = (now - timedelta(minutes=max(1, int(cooldown_minutes)))).strftime('%Y-%m-%dT%H:%M:%S')

        existing = self.get_debate_queue_item(event_id)
        if existing and existing.get("status") in {"pending", "processing"}:
            merged_payload = existing.get("trigger_json", {}) or {}
            merged_event_ids = list(dict.fromkeys((merged_payload.get("merged_event_ids") or []) + [event_id]))
            merged_payload.update(trigger_json if isinstance(trigger_json, dict) else {})
            merged_payload["merged_event_ids"] = merged_event_ids
            self.cursor.execute(
                '''
                UPDATE debate_queue
                SET priority = ?, urgency = ?, reason = ?, note = ?, trigger_json = ?,
                    cost_gate_status = ?, cost_gate_json = ?
                WHERE event_id = ?
                ''',
                (
                    max(priority, int(existing.get("priority", 0) or 0)),
                    urgency or existing.get("urgency"),
                    reason or existing.get("reason"),
                    note or existing.get("note"),
                    json.dumps(merged_payload, ensure_ascii=False),
                    cost_gate_status or existing.get("cost_gate_status", ""),
                    json.dumps(cost_gate_json if isinstance(cost_gate_json, dict) else {}, ensure_ascii=False),
                    event_id,
                ),
            )
            self.conn.commit()
            return {
                "created": False,
                "merged": True,
                "queue_id": existing.get("id"),
                "reason": "existing_open_event",
            }
        if existing:
            recent_marker = str(existing.get("requested_at") or existing.get("completed_at") or "")
            if recent_marker and recent_marker >= cooldown_threshold:
                return {
                    "created": False,
                    "merged": True,
                    "queue_id": existing.get("id"),
                    "reason": "recent_same_event",
                }
            self.cursor.execute(
                '''
                UPDATE debate_queue
                SET event_key = ?, ticker = ?, direction = ?, urgency = ?, priority = ?, topic = ?, reason = ?,
                    status = ?, requested_at = ?, claimed_at = NULL, completed_at = NULL,
                    debate_id = NULL, note = ?, trigger_json = ?, cost_gate_status = ?, cost_gate_json = ?
                WHERE event_id = ?
                ''',
                (
                    event_key or None,
                    ticker or None,
                    direction or None,
                    urgency or None,
                    priority,
                    topic,
                    reason,
                    status,
                    now_iso,
                    note,
                    json.dumps(trigger_json, ensure_ascii=False),
                    cost_gate_status,
                    json.dumps(cost_gate_json if isinstance(cost_gate_json, dict) else {}, ensure_ascii=False),
                    event_id,
                ),
            )
            self.conn.commit()
            return {"created": True, "merged": False, "queue_id": existing.get("id"), "reason": "reopened"}

        if event_key:
            self.cursor.execute(
                '''
                SELECT id, trigger_json
                FROM debate_queue
                WHERE event_key = ?
                  AND status IN ('pending', 'processing', 'completed')
                  AND requested_at >= ?
                ORDER BY requested_at DESC, id DESC
                LIMIT 1
                ''',
                (event_key, cooldown_threshold),
            )
            row_hit = self.cursor.fetchone()
            if row_hit:
                merged_payload = json.loads(row_hit[1]) if row_hit[1] else {}
                merged_event_ids = list(dict.fromkeys((merged_payload.get("merged_event_ids") or []) + [event_id]))
                merged_payload.update(trigger_json if isinstance(trigger_json, dict) else {})
                merged_payload["merged_event_ids"] = merged_event_ids
                self.cursor.execute(
                    '''
                    UPDATE debate_queue
                    SET priority = MAX(priority, ?), reason = COALESCE(NULLIF(reason, ''), ?), trigger_json = ?
                    WHERE id = ?
                    ''',
                    (
                        priority,
                        reason,
                        json.dumps(merged_payload, ensure_ascii=False),
                        int(row_hit[0]),
                    ),
                )
                self.conn.commit()
                return {"created": False, "merged": True, "queue_id": int(row_hit[0]), "reason": "recent_event_key"}

        if ticker and direction:
            self.cursor.execute(
                '''
                SELECT id, trigger_json
                FROM debate_queue
                WHERE ticker = ?
                  AND direction = ?
                  AND status IN ('pending', 'processing')
                  AND requested_at >= ?
                ORDER BY priority DESC, requested_at ASC, id ASC
                LIMIT 1
                ''',
                (ticker, direction, cooldown_threshold),
            )
            row_hit = self.cursor.fetchone()
            if row_hit:
                merged_payload = json.loads(row_hit[1]) if row_hit[1] else {}
                merged_event_ids = list(dict.fromkeys((merged_payload.get("merged_event_ids") or []) + [event_id]))
                merged_payload.update(trigger_json if isinstance(trigger_json, dict) else {})
                merged_payload["merged_event_ids"] = merged_event_ids
                self.cursor.execute(
                    '''
                    UPDATE debate_queue
                    SET priority = MAX(priority, ?), trigger_json = ?
                    WHERE id = ?
                    ''',
                    (
                        priority,
                        json.dumps(merged_payload, ensure_ascii=False),
                        int(row_hit[0]),
                    ),
                )
                self.conn.commit()
                return {"created": False, "merged": True, "queue_id": int(row_hit[0]), "reason": "ticker_direction_merge"}

        self.cursor.execute(
            '''
            INSERT INTO debate_queue (
                event_id, event_key, ticker, direction, urgency, priority, topic, reason, status,
                requested_at, claimed_at, completed_at, debate_id, note, trigger_json, cost_gate_status, cost_gate_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
            ''',
            (
                event_id,
                event_key or None,
                ticker or None,
                direction or None,
                urgency or None,
                priority,
                topic,
                reason,
                status,
                now_iso,
                note,
                json.dumps(trigger_json, ensure_ascii=False),
                cost_gate_status,
                json.dumps(cost_gate_json if isinstance(cost_gate_json, dict) else {}, ensure_ascii=False),
            ),
        )
        queue_id = int(self.cursor.lastrowid)
        self.conn.commit()
        return {"created": True, "merged": False, "queue_id": queue_id, "reason": "enqueued"}

    def get_debate_queue_item(self, event_id: str) -> dict | None:
        self.cursor.execute(
            '''
            SELECT id, event_id, event_key, ticker, direction, urgency, priority, topic, reason, status,
                   requested_at, claimed_at, completed_at, debate_id, note, trigger_json,
                   COALESCE(cost_gate_status, ''), COALESCE(cost_gate_json, '')
            FROM debate_queue
            WHERE event_id = ?
            LIMIT 1
            ''',
            (event_id,),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "event_id": row[1],
            "event_key": row[2],
            "ticker": row[3] or "",
            "direction": row[4] or "",
            "urgency": row[5] or "",
            "priority": int(row[6] or 0),
            "topic": row[7] or "",
            "reason": row[8] or "",
            "status": row[9] or "",
            "requested_at": row[10],
            "claimed_at": row[11],
            "completed_at": row[12],
            "debate_id": row[13],
            "note": row[14] or "",
            "trigger_json": json.loads(row[15]) if row[15] else {},
            "cost_gate_status": row[16] or "",
            "cost_gate_json": json.loads(row[17]) if row[17] else {},
        }

    def list_debate_queue(self, limit: int = 20, statuses: list[str] | None = None) -> list[dict]:
        sql = (
            "SELECT id, event_id, event_key, ticker, direction, urgency, priority, topic, reason, status, "
            "requested_at, claimed_at, completed_at, debate_id, note, trigger_json, "
            "COALESCE(cost_gate_status, ''), COALESCE(cost_gate_json, '') "
            "FROM debate_queue WHERE 1=1"
        )
        params: list[object] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY priority DESC, requested_at ASC, id ASC LIMIT ?"
        params.append(int(limit))
        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "id": row[0],
                    "event_id": row[1],
                    "event_key": row[2],
                    "ticker": row[3] or "",
                    "direction": row[4] or "",
                    "urgency": row[5] or "",
                    "priority": int(row[6] or 0),
                    "topic": row[7] or "",
                    "reason": row[8] or "",
                    "status": row[9] or "",
                    "requested_at": row[10],
                    "claimed_at": row[11],
                    "completed_at": row[12],
                    "debate_id": row[13],
                    "note": row[14] or "",
                    "trigger_json": json.loads(row[15]) if row[15] else {},
                    "cost_gate_status": row[16] or "",
                    "cost_gate_json": json.loads(row[17]) if row[17] else {},
                }
            )
        return out

    def claim_next_debate_queue_item(self) -> dict | None:
        self.cursor.execute(
            '''
            SELECT id, event_id, event_key, ticker, direction, urgency, priority, topic, reason, status,
                   requested_at, claimed_at, completed_at, debate_id, note, trigger_json
            FROM debate_queue
            WHERE status = 'pending'
              AND COALESCE(cost_gate_status, '') != 'review_required'
            ORDER BY priority DESC, requested_at ASC, id ASC
            LIMIT 1
            '''
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            "UPDATE debate_queue SET status='processing', claimed_at=? WHERE id=? AND status='pending'",
            (now_iso, int(row[0])),
        )
        if int(self.cursor.rowcount or 0) <= 0:
            self.conn.commit()
            return None
        self.conn.commit()
        return self.get_debate_queue_item(str(row[1]))

    def set_debate_queue_status(self, event_id: str, status: str, note: str = "") -> bool:
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        fields = "status = ?"
        params: list[object] = [status]
        if status == "pending":
            fields += ", requested_at = ?, claimed_at = NULL, completed_at = NULL, cost_gate_status = 'manual_approved'"
            params.append(now_iso)
        if note:
            fields += ", note = ?"
            params.append(note)
        params.append(str(event_id).strip())
        self.cursor.execute(
            f"UPDATE debate_queue SET {fields} WHERE event_id = ?",
            params,
        )
        self.conn.commit()
        return int(self.cursor.rowcount or 0) > 0

    def complete_debate_queue_item(
        self,
        item_id: int,
        status: str,
        debate_id: int | None = None,
        note: str = "",
    ):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            UPDATE debate_queue
            SET status = ?, completed_at = ?, debate_id = COALESCE(?, debate_id), note = ?
            WHERE id = ?
            ''',
            (status, now_iso, debate_id, note, int(item_id)),
        )
        self.conn.commit()

    def save_debate_quality_score(self, row: dict) -> dict:
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        detail = row.get("detail_json", {}) or {}
        payload = {
            "debate_id": int(row.get("debate_id")),
            "event_id": str(row.get("event_id") or "").strip(),
            "total_score": float(row.get("total_score", 0.0) or 0.0),
            "status": str(row.get("status") or "unknown").strip(),
            "scored_at": row.get("scored_at") or now_iso,
            "detail_json": detail if isinstance(detail, dict) else {},
        }
        self.cursor.execute(
            '''
            INSERT INTO debate_quality_scores (
                debate_id, event_id, total_score, status, scored_at, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(debate_id) DO UPDATE SET
                event_id=excluded.event_id,
                total_score=excluded.total_score,
                status=excluded.status,
                scored_at=excluded.scored_at,
                detail_json=excluded.detail_json
            ''',
            (
                payload["debate_id"],
                payload["event_id"] or None,
                payload["total_score"],
                payload["status"],
                payload["scored_at"],
                json.dumps(payload["detail_json"], ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return payload

    def get_debate_quality_score(self, debate_id: int) -> dict | None:
        self.cursor.execute(
            '''
            SELECT debate_id, event_id, total_score, status, scored_at, detail_json
            FROM debate_quality_scores
            WHERE debate_id = ?
            ''',
            (int(debate_id),),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "debate_id": int(row[0]),
            "event_id": row[1] or "",
            "total_score": float(row[2] or 0.0),
            "status": row[3] or "",
            "scored_at": row[4],
            "detail_json": json.loads(row[5]) if row[5] else {},
        }

    def get_latest_debate_quality_for_event(self, event_id: str) -> dict | None:
        self.cursor.execute(
            '''
            SELECT debate_id, event_id, total_score, status, scored_at, detail_json
            FROM debate_quality_scores
            WHERE event_id = ?
            ORDER BY scored_at DESC, debate_id DESC
            LIMIT 1
            ''',
            (str(event_id or "").strip(),),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "debate_id": int(row[0]),
            "event_id": row[1] or "",
            "total_score": float(row[2] or 0.0),
            "status": row[3] or "",
            "scored_at": row[4],
            "detail_json": json.loads(row[5]) if row[5] else {},
        }

    def upsert_investment_review_trigger(self, row: dict) -> dict[str, object]:
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        event_id = str(row.get("event_id") or "").strip()
        ticker = str(row.get("ticker") or "").strip()
        trigger_type = str(row.get("trigger_type") or "").strip()
        if not event_id or not trigger_type:
            return {"created": False, "trigger_id": None, "reason": "missing_keys"}

        self.cursor.execute(
            '''
            SELECT id, priority, detail_json
            FROM investment_review_triggers
            WHERE event_id = ?
              AND ticker = ?
              AND trigger_type = ?
              AND status = 'open'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            ''',
            (event_id, ticker, trigger_type),
        )
        existing = self.cursor.fetchone()
        detail_json = row.get("detail_json", {}) or {}
        priority = int(row.get("priority", 50) or 50)
        summary = str(row.get("summary") or "").strip()
        if existing:
            existing_detail = json.loads(existing[2]) if existing[2] else {}
            existing_detail.update(detail_json if isinstance(detail_json, dict) else {})
            self.cursor.execute(
                '''
                UPDATE investment_review_triggers
                SET priority = ?, summary = ?, detail_json = ?
                WHERE id = ?
                ''',
                (
                    max(priority, int(existing[1] or 0)),
                    summary,
                    json.dumps(existing_detail, ensure_ascii=False),
                    int(existing[0]),
                ),
            )
            self.conn.commit()
            return {"created": False, "trigger_id": int(existing[0]), "reason": "existing_open_trigger"}

        self.cursor.execute(
            '''
            INSERT INTO investment_review_triggers (
                event_id, ticker, trigger_type, priority, status, summary, detail_json, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, NULL)
            ''',
            (
                event_id,
                ticker or None,
                trigger_type,
                priority,
                summary,
                json.dumps(detail_json, ensure_ascii=False),
                now_iso,
            ),
        )
        trigger_id = int(self.cursor.lastrowid)
        self.conn.commit()
        return {"created": True, "trigger_id": trigger_id, "reason": "inserted"}

    def list_investment_review_triggers(
        self,
        limit: int = 20,
        statuses: list[str] | None = None,
    ) -> list[dict]:
        sql = (
            "SELECT id, event_id, ticker, trigger_type, priority, status, summary, detail_json, "
            "created_at, resolved_at FROM investment_review_triggers WHERE 1=1"
        )
        params: list[object] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY priority DESC, created_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "id": row[0],
                    "event_id": row[1],
                    "ticker": row[2] or "",
                    "trigger_type": row[3],
                    "priority": int(row[4] or 0),
                    "status": row[5],
                    "summary": row[6] or "",
                    "detail_json": json.loads(row[7]) if row[7] else {},
                    "created_at": row[8],
                    "resolved_at": row[9],
                }
            )
        return out

    def resolve_investment_review_trigger(self, trigger_id: int, status: str = "resolved"):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            UPDATE investment_review_triggers
            SET status = ?, resolved_at = ?
            WHERE id = ? AND status = 'open'
            ''',
            (status, now_iso, int(trigger_id)),
        )
        self.conn.commit()

    def upsert_signal_event(self, row: dict):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            INSERT INTO signal_events (
                event_id, event_key, date, detected_at, title, summary, score_total,
                score_json, related_tickers, direction, urgency, confidence, status, evidence_ids,
                verification_json, last_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                event_key=excluded.event_key,
                date=excluded.date,
                detected_at=excluded.detected_at,
                title=excluded.title,
                summary=excluded.summary,
                score_total=excluded.score_total,
                score_json=excluded.score_json,
                related_tickers=excluded.related_tickers,
                direction=excluded.direction,
                urgency=excluded.urgency,
                confidence=excluded.confidence,
                status=CASE
                    WHEN signal_events.status IN ('executed', 'rejected')
                         AND excluded.status NOT IN ('executed', 'rejected')
                    THEN signal_events.status
                    ELSE excluded.status
                END,
                evidence_ids=excluded.evidence_ids,
                verification_json=excluded.verification_json,
                last_verified_at=excluded.last_verified_at
            ''',
            (
                row.get("event_id"),
                row.get("event_key"),
                row.get("date"),
                row.get("detected_at") or now_iso,
                row.get("title"),
                row.get("summary"),
                float(row.get("score_total", 0.0)),
                json.dumps(row.get("score_json", {}), ensure_ascii=False),
                json.dumps(row.get("related_tickers", []), ensure_ascii=False),
                row.get("direction"),
                row.get("urgency"),
                float(row.get("confidence", 0.0)),
                row.get("status", "new"),
                json.dumps(row.get("evidence_ids", []), ensure_ascii=False),
                json.dumps(row.get("verification_json", {}), ensure_ascii=False),
                row.get("last_verified_at"),
            ),
        )
        self.conn.commit()

    def set_signal_event_status(self, event_id: str, status: str):
        self.cursor.execute(
            "UPDATE signal_events SET status = ? WHERE event_id = ?",
            (status, event_id),
        )
        self.conn.commit()

    def get_signal_event(self, event_id: str) -> dict | None:
        self.cursor.execute(
            '''
            SELECT event_id, event_key, date, detected_at, title, summary, score_total,
                   score_json, related_tickers, direction, urgency, confidence, status, evidence_ids,
                   verification_json, last_verified_at
            FROM signal_events
            WHERE event_id = ?
            ''',
            (event_id,),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "event_id": row[0],
            "event_key": row[1],
            "date": row[2],
            "detected_at": row[3],
            "title": row[4],
            "summary": row[5],
            "score_total": float(row[6] or 0.0),
            "score_json": json.loads(row[7]) if row[7] else {},
            "related_tickers": json.loads(row[8]) if row[8] else [],
            "direction": row[9],
            "urgency": row[10],
            "confidence": float(row[11] or 0.0),
            "status": row[12],
            "evidence_ids": json.loads(row[13]) if row[13] else [],
            "verification_json": json.loads(row[14]) if row[14] else {},
            "last_verified_at": row[15],
        }

    def list_recent_signal_events(self, limit: int = 20) -> list[dict]:
        self.cursor.execute(
            '''
            SELECT event_id, date, title, score_total, direction, urgency, status, related_tickers, last_verified_at
            FROM signal_events
            ORDER BY date DESC, score_total DESC, detected_at DESC
            LIMIT ?
            ''',
            (int(limit),),
        )
        rows = self.cursor.fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "event_id": r[0],
                    "date": r[1],
                    "title": r[2],
                    "score_total": float(r[3] or 0.0),
                    "direction": r[4],
                    "urgency": r[5],
                    "status": r[6],
                    "related_tickers": json.loads(r[7]) if r[7] else [],
                    "last_verified_at": r[8],
                }
            )
        return out

    def list_signal_events_between(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 500,
        statuses: list[str] | None = None,
    ) -> list[dict]:
        sql = (
            "SELECT event_id, event_key, date, detected_at, title, summary, score_total, score_json, "
            "related_tickers, direction, urgency, confidence, status, evidence_ids, verification_json, last_verified_at "
            "FROM signal_events WHERE 1=1"
        )
        params: list[object] = []
        if start_date:
            sql += " AND date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND date <= ?"
            params.append(end_date)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY date ASC, detected_at ASC, id ASC LIMIT ?"
        params.append(int(limit))
        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "event_id": row[0],
                    "event_key": row[1],
                    "date": row[2],
                    "detected_at": row[3],
                    "title": row[4],
                    "summary": row[5],
                    "score_total": float(row[6] or 0.0),
                    "score_json": json.loads(row[7]) if row[7] else {},
                    "related_tickers": json.loads(row[8]) if row[8] else [],
                    "direction": row[9],
                    "urgency": row[10],
                    "confidence": float(row[11] or 0.0),
                    "status": row[12],
                    "evidence_ids": json.loads(row[13]) if row[13] else [],
                    "verification_json": json.loads(row[14]) if row[14] else {},
                    "last_verified_at": row[15],
                }
            )
        return out

    def replace_recommendations(self, event_id: str, recs: list[dict]):
        self.cursor.execute(
            "DELETE FROM signal_recommendations WHERE event_id = ?",
            (event_id,),
        )
        now = datetime.now()
        now_iso = now.strftime('%Y-%m-%dT%H:%M:%S')
        for r in recs:
            ttl = int(r.get("ttl_sec", 0) or 0)
            expires_at = (now + timedelta(seconds=ttl)).strftime('%Y-%m-%dT%H:%M:%S') if ttl > 0 else None
            self.cursor.execute(
                '''
                INSERT INTO signal_recommendations (
                    event_id, ticker, side, size_rule, entry_rule, stop_rule, ttl_sec,
                    confidence, rationale, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    event_id,
                    r.get("ticker"),
                    r.get("side"),
                    r.get("size_rule"),
                    r.get("entry_rule"),
                    r.get("stop_rule"),
                    ttl,
                    float(r.get("confidence", 0.0)),
                    r.get("rationale", ""),
                    r.get("status", "pending_approval"),
                    now_iso,
                    expires_at,
                ),
            )
        self.conn.commit()

    def get_recommendations(self, event_id: str) -> list[dict]:
        self.cursor.execute(
            '''
            SELECT id, event_id, ticker, side, size_rule, entry_rule, stop_rule, ttl_sec,
                   confidence, rationale, status, created_at, expires_at
            FROM signal_recommendations
            WHERE event_id = ?
            ORDER BY id ASC
            ''',
            (event_id,),
        )
        rows = self.cursor.fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "id": r[0],
                    "event_id": r[1],
                    "ticker": r[2],
                    "side": r[3],
                    "size_rule": r[4],
                    "entry_rule": r[5],
                    "stop_rule": r[6],
                    "ttl_sec": int(r[7] or 0),
                    "confidence": float(r[8] or 0.0),
                    "rationale": r[9],
                    "status": r[10],
                    "created_at": r[11],
                    "expires_at": r[12],
                }
            )
        return out

    def set_recommendations_status(self, event_id: str, status: str):
        self.cursor.execute(
            "UPDATE signal_recommendations SET status = ? WHERE event_id = ?",
            (status, event_id),
        )
        self.conn.commit()

    def upsert_approval_request(self, event_id: str, ttl_sec: int = 900, allow_reopen_terminal: bool = False) -> bool:
        now = datetime.now()
        now_iso = now.strftime('%Y-%m-%dT%H:%M:%S')
        expires_at = (now + timedelta(seconds=max(60, int(ttl_sec)))).strftime('%Y-%m-%dT%H:%M:%S')
        existing = self.get_approval_request(event_id)
        if (
            existing
            and existing.get("state") in {"executed", "rejected"}
            and not allow_reopen_terminal
        ):
            # terminal 상태는 자동 루프에서 재오픈 금지
            return False
        if existing and existing.get("state") == "approved":
            self.cursor.execute(
                '''
                UPDATE approval_requests
                SET expires_at = ?, note = COALESCE(note, '')
                WHERE event_id = ? AND state = 'approved'
                ''',
                (expires_at, event_id),
            )
            self.conn.commit()
            return True
        self.cursor.execute(
            '''
            INSERT INTO approval_requests (
                event_id, requested_at, expires_at, state, note
            ) VALUES (?, ?, ?, 'pending', '')
            ON CONFLICT(event_id) DO UPDATE SET
                requested_at=excluded.requested_at,
                expires_at=excluded.expires_at,
                approved_by=NULL,
                approved_at=NULL,
                rejected_by=NULL,
                rejected_at=NULL,
                state='pending',
                note=''
            ''',
            (event_id, now_iso, expires_at),
        )
        self.conn.commit()
        return True

    def get_approval_request(self, event_id: str) -> dict | None:
        self.cursor.execute(
            '''
            SELECT event_id, requested_at, expires_at, approved_by, approved_at, rejected_by, rejected_at, state, note
            FROM approval_requests
            WHERE event_id = ?
            ''',
            (event_id,),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {
            "event_id": row[0],
            "requested_at": row[1],
            "expires_at": row[2],
            "approved_by": row[3],
            "approved_at": row[4],
            "rejected_by": row[5],
            "rejected_at": row[6],
            "state": row[7],
            "note": row[8] or "",
        }

    def list_pending_approvals(self, limit: int = 20) -> list[dict]:
        self.cursor.execute(
            '''
            SELECT a.event_id, a.requested_at, a.expires_at, a.state, e.title, e.score_total, e.direction, e.urgency
            FROM approval_requests a
            LEFT JOIN signal_events e ON e.event_id = a.event_id
            WHERE a.state = 'pending'
            ORDER BY a.requested_at DESC
            LIMIT ?
            ''',
            (int(limit),),
        )
        rows = self.cursor.fetchall()
        out = []
        now = datetime.now()
        for r in rows:
            expired = False
            if r[2]:
                try:
                    expired = datetime.fromisoformat(r[2]) < now
                except Exception:
                    expired = False
            out.append(
                {
                    "event_id": r[0],
                    "requested_at": r[1],
                    "expires_at": r[2],
                    "state": "expired" if expired else r[3],
                    "title": r[4] or "",
                    "score_total": float(r[5] or 0.0),
                    "direction": r[6] or "",
                    "urgency": r[7] or "",
                }
            )
        return out

    def approve_request(self, event_id: str, approved_by: str, note: str = "") -> bool:
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            UPDATE approval_requests
            SET state='approved', approved_by=?, approved_at=?, note=?
            WHERE event_id=? AND state='pending'
            ''',
            (approved_by, now_iso, note, event_id),
        )
        changed = self.cursor.rowcount > 0
        if changed:
            self.conn.commit()
        return changed

    def reject_request(self, event_id: str, rejected_by: str, note: str = "") -> bool:
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            UPDATE approval_requests
            SET state='rejected', rejected_by=?, rejected_at=?, note=?
            WHERE event_id=? AND state='pending'
            ''',
            (rejected_by, now_iso, note, event_id),
        )
        changed = self.cursor.rowcount > 0
        if changed:
            self.conn.commit()
        return changed

    def mark_approval_executed(self, event_id: str, note: str = "") -> bool:
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            UPDATE approval_requests
            SET state='executed', note=?, approved_at=COALESCE(approved_at, ?)
            WHERE event_id=? AND state IN ('approved', 'pending')
            ''',
            (note, now_iso, event_id),
        )
        changed = self.cursor.rowcount > 0
        if changed:
            self.conn.commit()
        return changed

    def mark_expired_approvals(self) -> int:
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            SELECT event_id
            FROM approval_requests
            WHERE state='pending' AND expires_at IS NOT NULL AND expires_at < ?
            ''',
            (now_iso,),
        )
        expired_ids = [str(row[0]) for row in self.cursor.fetchall() if row and row[0]]
        self.cursor.execute(
            '''
            UPDATE approval_requests
            SET state='expired'
            WHERE state='pending' AND expires_at IS NOT NULL AND expires_at < ?
            ''',
            (now_iso,),
        )
        count = int(self.cursor.rowcount or 0)
        if count and expired_ids:
            placeholders = ",".join("?" for _ in expired_ids)
            self.cursor.execute(
                f"""
                UPDATE signal_recommendations
                SET status='expired'
                WHERE event_id IN ({placeholders})
                  AND status IN ('pending_approval', 'approved')
                """,
                expired_ids,
            )
            self.cursor.execute(
                f"""
                UPDATE signal_events
                SET status='expired'
                WHERE event_id IN ({placeholders})
                  AND status NOT IN ('executed', 'rejected')
                """,
                expired_ids,
            )
            self.conn.commit()
        return count

    def supersede_signal_workflow(self, event_id: str, note: str = ""):
        now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        self.cursor.execute(
            '''
            UPDATE approval_requests
            SET state='superseded', note=?, rejected_at=COALESCE(rejected_at, ?)
            WHERE event_id=? AND state IN ('pending', 'approved')
            ''',
            (note, now_iso, event_id),
        )
        self.cursor.execute(
            '''
            UPDATE signal_recommendations
            SET status='superseded'
            WHERE event_id=? AND status IN ('pending_approval', 'approved')
            ''',
            (event_id,),
        )
        self.cursor.execute(
            '''
            UPDATE signal_events
            SET status='monitor_only'
            WHERE event_id=? AND status NOT IN ('executed', 'rejected', 'expired')
            ''',
            (event_id,),
        )
        self.conn.commit()

    def count_orders_in_window(self, since_iso: str) -> int:
        self.cursor.execute(
            '''
            SELECT COUNT(*)
            FROM order_executions
            WHERE submitted_at >= ?
            ''',
            (since_iso,),
        )
        row = self.cursor.fetchone()
        return int((row or [0])[0] or 0)

    def list_order_executions(
        self,
        event_id: str | None = None,
        limit: int = 100,
        ticker: str | None = None,
    ) -> list[dict]:
        params: list[object] = []
        sql = (
            "SELECT event_id, ticker, side, qty, order_type, submitted_at, filled_at, fill_price, "
            "result, broker_order_id, detail_json "
            "FROM order_executions"
        )
        clauses = []
        if event_id:
            clauses.append("event_id = ?")
            params.append(event_id)
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(filled_at, submitted_at) DESC, id DESC LIMIT ?"
        params.append(int(limit))
        self.cursor.execute(sql, params)
        rows = self.cursor.fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "event_id": row[0],
                    "ticker": row[1],
                    "side": row[2],
                    "qty": float(row[3] or 0.0),
                    "order_type": row[4],
                    "submitted_at": row[5],
                    "filled_at": row[6],
                    "fill_price": float(row[7] or 0.0),
                    "result": row[8],
                    "broker_order_id": row[9],
                    "detail_json": json.loads(row[10]) if row[10] else {},
                }
            )
        return out

    def save_order_execution(self, row: dict):
        self.cursor.execute(
            '''
            INSERT INTO order_executions (
                event_id, ticker, side, qty, order_type, submitted_at, filled_at, fill_price,
                result, broker_order_id, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                row.get("event_id"),
                row.get("ticker"),
                row.get("side"),
                float(row.get("qty", 0.0)),
                row.get("order_type", "paper_market"),
                row.get("submitted_at"),
                row.get("filled_at"),
                float(row.get("fill_price", 0.0)),
                row.get("result", "PAPER_FILLED"),
                row.get("broker_order_id", ""),
                json.dumps(row.get("detail_json", {}), ensure_ascii=False),
            ),
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
            "DELETE FROM news_context_packs WHERE generated_at < datetime('now', ?)",
            (f"-{retention_days} day",)
        )
        self.cursor.execute(
            "DELETE FROM news_ingest_checkpoints WHERE updated_at < datetime('now', ?)",
            (f"-{retention_days} day",)
        )
        self.cursor.execute(
            "DELETE FROM signal_events WHERE date < date('now', ?)",
            (f"-{retention_days} day",)
        )
        self.cursor.execute(
            "DELETE FROM signal_recommendations WHERE created_at < datetime('now', ?)",
            (f"-{retention_days} day",)
        )
        self.cursor.execute(
            "DELETE FROM approval_requests WHERE requested_at < datetime('now', ?)",
            (f"-{retention_days} day",)
        )
        self.cursor.execute(
            "DELETE FROM order_executions WHERE submitted_at < datetime('now', ?)",
            (f"-{retention_days} day",)
        )
        self.cursor.execute(
            "DELETE FROM debate_queue WHERE requested_at < datetime('now', ?)",
            (f"-{retention_days} day",)
        )
        self.cursor.execute(
            "DELETE FROM investment_review_triggers WHERE created_at < datetime('now', ?)",
            (f"-{retention_days} day",)
        )
        self.conn.commit()

    def close(self):
        if self._closed:
            return
        try:
            self.conn.commit()
        except Exception:
            pass
        try:
            self.cursor.close()
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

# 테스트용 실행
if __name__ == "__main__":
    with DBManager():
        print("DB 및 테이블 생성 완료!")
