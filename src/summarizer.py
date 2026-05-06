import asyncio
import os
from datetime import datetime, timedelta
from db_manager import DBManager
from llm_client import LLMClientManager

class RAGSummarizer:
    def __init__(self):
        self.db = DBManager()
        self.llm = LLMClientManager()

    async def summarize_daily(self, target_date: str | None = None):
        """특정 날짜(기본값: 오늘)의 시장 이벤트 + 토론 결론을 합쳐 일간 요약본 생성"""
        if target_date is None:
            target_date = datetime.now().strftime('%Y-%m-%d')

        rows = self.db.list_debates_by_date(target_date)
        news_rows = [e for e in self.db.list_news_events_since(target_date, limit=40) if e.get("date") == target_date]

        if not rows and not news_rows:
            print(f"[{target_date}] 요약할 일간 이벤트/토론 기록이 없습니다.")
            return

        combined_logs = ""
        if news_rows:
            combined_logs += "[오늘의 핵심 뉴스 이벤트]\n"
            for i, event in enumerate(news_rows[:12], 1):
                combined_logs += (
                    f"=== 뉴스 이벤트 {i} ===\n"
                    f"제목: {event.get('title', '')}\n"
                    f"요약: {event.get('summary', '')}\n"
                    f"신뢰도: conf={event.get('confidence', 0)}, "
                    f"sources={event.get('source_count', 0)}, articles={event.get('article_count', 0)}\n\n"
                )

        for i, row in enumerate(rows):
            topic, status, inv_json = row
            combined_logs += f"=== 토론 {i+1} (주제: {topic} / 상황: {status}) ===\n[판결내용]: {inv_json}\n\n"

        sys_prompt = (
            "너는 주식 토론 회의록 정리의 달인(수석 투자 전략가)이야. 사용자가 건네주는 '오늘의 뉴스 이벤트'와 "
            "'오늘 진행된 AI 토론 결론들'을 함께 읽고,\n"
            "1) 시장을 움직인 핵심 이벤트,\n"
            "2) 토론이 도달한 주요 판단,\n"
            "3) 내일 점검할 리스크/확인 포인트\n"
            "를 7문장 이내로 전문적으로 압축해줘.\n"
            "마지막 줄에는 핵심 키워드를 해시태그 5개로 적어줘."
        )

        user_prompt = f"[오늘의 토론 내역]\n{combined_logs}"

        print(f"[{target_date}] 일간 요약을 gpt-oss-20b 로컬 모델에 요청합니다...")
        summary = await self.llm.get_local_response(sys_prompt, user_prompt, profile="summary")
        
        # 키워드 대충 추출 (해시태그 기반)
        keywords = ", ".join([word for word in summary.split() if word.startswith('#')])
        
        self.db.save_summary('daily', target_date, summary, keywords)
        print(f"✅ [{target_date}] 일간 요약 완료 및 DB 저장됨.")

    async def summarize_weekly(self):
        """최근 7일간의 '일간 요약본'을 모아 주간 요약본 생성"""
        today = datetime.now()
        week_ago = today - timedelta(days=7)

        rows = self.db.list_summaries_by_type_since('daily', week_ago.strftime('%Y-%m-%d'))
        
        if not rows:
            print("요약할 주간 데이터(일간 요약본)가 부족합니다.")
            return

        week_logs = "\n".join([f"[{r[0]} 요약]: {r[1]}" for r in rows])
        target_range = f"{week_ago.strftime('%Y-%m-%d')}~{today.strftime('%Y-%m-%d')}"

        sys_prompt = (
            "너는 월스트리트의 매크로 투자 수석 위원장이야.\n"
            "사용자가 건네주는 '최근 1주일간의 주식 토론 일별 브리핑 모음'을 읽고,\n"
            "이번 주 시장을 관통했던 가장 중요한 메가 트렌드, 자산 시장의 변화, 그리고 다음 주를 대비한 전략적 뷰를 3문단으로 압축해서 주간 보고서로 작성해줘."
        )
        user_prompt = f"[일간 요약 모음]\n{week_logs}"
        
        print(f"[{target_range}] 주간 요약 중...")
        summary = await self.llm.get_local_response(sys_prompt, user_prompt, profile="summary")
        
        self.db.save_summary('weekly', target_range, summary, "주간전망, 트렌드")
        print(f"✅ [{target_range}] 주간 요약 완료 및 DB 저장됨.")

    async def summarize_monthly(self):
        """최근 30일간의 '일간/주간 요약본'을 모아 월간 요약본 생성"""
        today = datetime.now()
        month_ago = today - timedelta(days=30)

        rows = self.db.list_summaries_by_type_since('weekly', month_ago.strftime('%Y-%m-%d'))
        
        if not rows:
            print("요약할 월간 데이터(주간 요약본)가 부족합니다.")
            return

        month_logs = "\n".join([f"[{r[0]} 요약]: {r[1]}" for r in rows])
        target_range = f"월간보고({month_ago.strftime('%Y-%m')}~{today.strftime('%Y-%m')})"

        sys_prompt = (
            "너는 월스트리트의 권위 있는 거시경제 전문가야.\n"
            "사용자가 건네주는 '지난 달의 주식 시장 주간 브리핑 모음'을 읽고,\n"
            "이번 한 달을 관통했던 거시경제적 패러다임 변화, 호재/악재의 발생 및 시장의 반응, 그리고 다음 달(Next)의 글로벌 투자 전략 방향성을 4문단 이내로 아주 전문적으로 요약해 줘."
        )
        user_prompt = f"[주간 요약 모음]\n{month_logs}"
        
        print(f"[{target_range}] 월간 요약 중...")
        summary = await self.llm.get_local_response(sys_prompt, user_prompt, profile="summary")
        
        self.db.save_summary('monthly', target_range, summary, "월간전망, 거시경제, 거시트렌드")
        print(f"✅ [{target_range}] 월간 요약 완료 및 DB 저장됨.")

async def main():
    import sys
    print("=== AI 회의록 자동 RAG 요약기 (Windows 스케줄러 연동용) ===")
    
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
    else:
        command = input("수행할 압축 종류를 입력하세요 (daily, weekly, monthly): ").strip().lower()
        
    summarizer = RAGSummarizer()
    
    if command == 'daily':
        await summarizer.summarize_daily()
    elif command == 'weekly':
        await summarizer.summarize_weekly()
    elif command == 'monthly':
        await summarizer.summarize_monthly()
    else:
        print("알 수 없는 명령어입니다. (daily, weekly, monthly 중 선택)")

if __name__ == "__main__":
    asyncio.run(main())
