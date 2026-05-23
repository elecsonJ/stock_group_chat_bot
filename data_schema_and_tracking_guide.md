# 데이터 스키마 및 추적 가이드 (Current)

## 파일 경로
프로젝트 루트 기준:

```text
/Users/hanjaehoon/my_projects/stock_group_chat_bot/
└── data/
    └── investment_bot.db
```

## SQLite 테이블
현재 기본 테이블은 26개(+FTS 가상 테이블)입니다.

1. `daily_news`
- 날짜/키워드 기반 단기 뉴스 캐시
- 현재 핵심 명령 경로에서는 파일 아카이브(`news_archive`)를 주로 사용하고, 이 테이블은 보조 캐시 역할

2. `debates`
- 토론 주제, 전체 로그, 합치 여부, 최종 판결 JSON 저장
- 일반 채팅 후속 로그도 append됨

3. `summaries`
- 일/주/월 요약 저장
- RAG에서 장문 로그를 압축해 재사용할 때 사용

4. `research_evidences`
- `[SEARCH: ...]` 실행 결과의 Evidence 패키지 저장
- URL/도메인/발췌/제약/요약이 JSON으로 보관됨
- 세션 전역 근거ID(`EV0001` 등)가 evidence 항목에 포함됨

5. `news_articles`
- 다중 소스에서 수집된 정규화 기사 저장
- `article_key` 기준 중복 제거, `event_key`로 이벤트 연결
- `fetched_at`, `ingest_delay_sec`로 신선도 추적

6. `news_events`
- 기사 클러스터링 결과(이벤트 단위) 저장
- `confidence`, `source_count`, `article_count`, `sample_urls` 포함

7. `news_context_packs`
- 독립 뉴스 판단 패키지 저장
- `query_hash`, `query`, `generated_at`, `pack_json` 포함
- 토론/RAG는 원문 DB를 직접 해석하지 않고 이 패키지를 통해 최근 뉴스 맥락을 받음

8. `news_ingest_checkpoints`
- 소스별 마지막 성공 시점/커서 저장
- 10분 폴링 overlap 윈도우 계산과 백필 보정에 사용

9. `signal_events`
- 뉴스 이벤트 기반 단기 시그널 점수/상태 저장
- `event_id`, `score_total`, `direction`, `urgency`, `related_tickers` 포함
- `verification_json`, `last_verified_at`로 이벤트 웹검증 결과 저장

10. `signal_recommendations`
- 이벤트별 제안 주문(티커/방향/진입룰/손절룰/TTL) 저장

11. `approval_requests`
- 사용자 승인/거부/만료 상태 저장

12. `order_executions`
- 승인 후 실행된 페이퍼/실거래 주문 로그

13. `risk_guardrail_state`
- kill switch, 일/시간 주문 한도, 일손실 한도 저장(단일 row)

14. `system_metadata`
- FTS 부트스트랩 여부 등 시스템 내부 상태 저장

15. `paper_account_state`
- paper broker의 현금, equity, buying power, realized/unrealized PnL 저장

16. `paper_positions`
- ticker별 수량, 평균단가, 시장가, 평가금액, 손익 저장

17. `paper_orders`
- paper broker 주문 상태와 체결 수량/평균가 저장

18. `paper_fills`
- paper broker 체결 단위 로그 저장

19. `signal_performance`
- 이벤트/티커/horizon별 entry 대비 성과, benchmark alpha 저장

20. `performance_run_summaries`
- replay run별 win rate, expectancy, profit factor, equity curve/MDD 요약 저장

21. `signal_attributions`
- 이벤트 성과를 source/relation/path/category별로 귀속하기 위한 attribution 저장

22. `debate_queue`
- 시그널 기반 자동 토론 큐 상태 저장

23. `investment_review_triggers`
- add/reduce/exit/hedge/monitor review 트리거 저장

24. `event_intake_audits`
- 뉴스 이벤트가 시그널 엔진에서 왜 무시/모니터/토론/승인 경로로 갔는지 저장
- source count, article count, confidence, verification verdict, route, reason, decision payload 포함

25. `context_selection_audits`
- News Context Pack, RAG, 토론 매니저가 AI에게 어떤 컨텍스트를 골랐는지 저장
- 선택된 이벤트/근거, 제외 사유, 품질 상태, 컨텍스트 budget/문자 수 포함

26. `debates_fts`, `summaries_fts` (FTS5 virtual table)
- `debates`, `summaries`의 전문 검색 인덱스
- 트리거 기반 동기화(`INSERT/UPDATE/DELETE`)

## 운영 설정
`DBManager`는 다음을 적용합니다.
1. `WAL` 모드
2. `busy_timeout=20000`
3. 조회 인덱스(`date`, `summary_type`, `topic` 등)
4. 보존 정책 purge는 단기 캐시/뉴스/근거/시그널 계층 중심으로 적용하며, 장기 성과 추적 테이블은 별도 정책이 필요합니다.

## RAG 동작 메모
현재 RAG 조회 방식은 **FTS 우선 + LIKE fallback + News Context Pack 병합** 입니다.

1. 질문에서 키워드 추출
2. `debates_fts`/`summaries_fts` MATCH 검색
3. 결과가 부족하면 `LIKE`로 fallback
4. `NewsContextPackService`가 최근 `news_events/news_articles/research_evidences`를 묶어 RAG 컨텍스트로 추가
5. 최대 5개 맥락, 각 800자 절단
6. 로컬 모델이 컨텍스트 범위 내 응답

## 감사/재현 포인트
재현 가능한 판정을 위해 아래를 같이 보관합니다.

1. `debates.full_log`: 토론 맥락
2. `research_evidences.evidence_json`: 검색 근거 패키지
3. `debates.investment_json`: 최종 판결 JSON
4. `signal_events.verification_json`: 신규 이벤트 웹검증 근거 요약(판정 근거)
5. `news_context_packs.pack_json`: 토론/RAG에 투입된 뉴스 판단 패키지
6. `signal_performance`, `performance_run_summaries`, `signal_attributions`: 사후 성과와 실패 패턴 분석 근거
7. `event_intake_audits`, `context_selection_audits`: 데이터 반응과 AI 입력 컨텍스트의 사후 복기 근거

## 단기 시그널 운영 원칙
1. DB의 과거 결론을 그대로 재사용하지 않고, 신규 이벤트마다 웹검증을 수행한 결과를 우선 사용합니다.
2. DB는 판정 엔진이 아니라 근거 추적/재현 저장소로 사용합니다.
