# 데이터 스키마 및 추적 가이드 (Current)

## 파일 경로
프로젝트 루트 기준:

```text
/Users/hanjaehoon/my_projects/stock_group_chat_bot/
└── data/
    └── investment_bot.db
```

## SQLite 테이블
현재 기본 테이블은 7개(+FTS 가상 테이블)입니다.

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

7. `news_ingest_checkpoints`
- 소스별 마지막 성공 시점/커서 저장
- 10분 폴링 overlap 윈도우 계산과 백필 보정에 사용

8. `debates_fts`, `summaries_fts` (FTS5 virtual table)
- `debates`, `summaries`의 전문 검색 인덱스
- 트리거 기반 동기화(`INSERT/UPDATE/DELETE`)

## 운영 설정
`DBManager`는 다음을 적용합니다.
1. `WAL` 모드
2. `busy_timeout=20000`
3. 조회 인덱스(`date`, `summary_type`, `topic` 등)
4. 보존 정책 purge(`daily_news`, `research_evidences`, `news_articles`, `news_events`, `news_ingest_checkpoints`) 기본 180일

## RAG 동작 메모
현재 RAG 조회 방식은 **FTS 우선 + LIKE fallback** 입니다.

1. 질문에서 키워드 추출
2. `debates_fts`/`summaries_fts` MATCH 검색
3. 결과가 부족하면 `LIKE`로 fallback
4. 최대 5개 맥락, 각 800자 절단
5. 로컬 모델이 컨텍스트 범위 내 응답

## 감사/재현 포인트
재현 가능한 판정을 위해 아래를 같이 보관합니다.

1. `debates.full_log`: 토론 맥락
2. `research_evidences.evidence_json`: 검색 근거 패키지
3. `debates.investment_json`: 최종 판결 JSON
