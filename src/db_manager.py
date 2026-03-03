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
        self._create_tables()

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
        self.conn.commit()

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

# 테스트용 실행
if __name__ == "__main__":
    db = DBManager()
    print("DB 및 테이블 생성 완료!")
