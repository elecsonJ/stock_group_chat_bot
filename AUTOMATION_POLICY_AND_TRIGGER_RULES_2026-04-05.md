# Automation Policy And Trigger Rules (2026-04-05)

## 목적
이 문서는 이 시스템을 자동화할 때 어떤 주기로 무엇을 실행하고, 어떤 뉴스/이벤트를 어떤 기준으로 토론과 투자 변화 트리거로 연결할지 정리한 운영 기준 문서입니다.

핵심 원칙은 단순합니다.

- `뉴스는 자주 로드한다`
- `시그널은 자주 평가한다`
- `토론은 드물고 엄격하게 호출한다`
- `투자 변화 트리거는 더 엄격하게 제한한다`
- `최종 투자 판단은 현재 100% 사용자가 한다`

따라서 이 문서의 자동화는 실거래 자동화를 뜻하지 않습니다. 현재 기본값은 수집/정리/검증/토론/paper-replay 자동화이며, 실제 투자 여부와 규모는 사용자가 보고서와 근거를 보고 결정합니다.

## 전체 자동화 흐름

1. 뉴스 수집
2. 기사 정규화/중복 제거
3. 이벤트 클러스터링
4. News Context Pack 생성/품질 판정
5. 시그널 점수화
6. 웹 evidence 검증
7. 온톨로지 hidden candidate 확장
8. 토론 필요 여부 판정
9. 사용자 최종 판단 보조 또는 paper execution
10. replay / 성과 측정

## 권장 주기

### 1. 뉴스 로드
- 주기: `10분`
- 목적:
  - 새 기사 수집
  - 중복 제거
  - `news_events` 갱신
- 관련 실행:
  - `run_news.bat`

### 2. 시그널 평가
- 주기: `10~15분`
- 목적:
  - 신규/최근 이벤트 점수화
  - 웹 evidence 검증
  - approval 후보 생성
- 관련 실행:
  - `run_signals.bat`

### 3. 뉴스 Context Pack 정리
- 주기: `30분`
- 목적:
  - 로컬 DB에 쌓인 뉴스 이벤트/기사/웹검증 evidence를 판단 패키지로 정리
  - 토론/RAG가 원자료 DB를 직접 해석하지 않게 함
  - `weak/empty` 또는 `web_required=true` 상태를 통해 보완 검색 필요성을 표시
- 관련 실행:
  - `run_news_context.bat`

### 4. 토론 호출
- 주기: `이벤트 기반`
- 목적:
  - 중요한 이벤트만 LLM 오케스트레이션 토론에 투입
- 원칙:
  - cron처럼 무조건 돌리지 않는다
  - 트리거 조건을 만족할 때만 호출한다
- 관련 실행:
  - `run_debates.bat`

### 5. replay / 성과 측정
- 주기:
  - 장중 `1시간`
  - 또는 장 종료 후 `1회`
- 목적:
  - horizon 성과 측정
  - MDD/equity curve 집계
  - attribution 저장
- 관련 실행:
  - `run_replay.bat`

### 6. 백필 / 보정
- 주기: `하루 1회`
- 목적:
  - 늦게 색인된 기사 반영
  - 누락 이벤트 보정
- 관련 실행:
  - `run_news_backfill.bat`

## 자동화 계층별 판단 기준

### A. 뉴스 저장만 할지
아래 중 하나라도 만족하면 저장합니다.

- 새 기사
- 기존 이벤트의 source/article count 증가
- title/summary가 기존 이벤트와 유사하지만 추가 출처가 생김
- 공시/IR/정부/규제 출처

### B. News Context Pack까지 만들지
아래 중 하나라도 만족하면 뉴스팩을 생성합니다.

- 최근 `news_events`가 존재
- 수동 질의가 `NEWS_CONTEXT_QUERIES`로 지정됨
- 토론/RAG가 최신 이슈로 판정됨
- 기존 뉴스팩 품질이 `weak/empty` 또는 `web_required=true`

### C. 시그널까지 올릴지
아래 기준을 만족하면 `signal_engine` 평가 대상으로 올립니다.

- `score_total >= threshold`
- 또는 `portfolio_hit == true`
- 또는 `urgency == immediate`
- 또는 `온톨로지 hidden candidate`가 검증 가치가 높음

### D. 토론까지 올릴지
아래 중 하나라도 만족하면 토론 큐로 올립니다.

1. `urgency == immediate`
2. `score_total >= 75`
3. `portfolio_hit == true`
4. `verification verdict == verified`
5. `source_tier`에 `regulatory`, `company_ir`, `tier1_media`가 포함
6. `hidden candidate validation_score >= 0.65`
7. 기존 포지션 방향과 충돌
8. 최근 24~72시간 기존 판단을 뒤집을 가능성

### E. 투자 변화 트리거를 줄지
현재 구현은 토론 결과를 기다리지 않고, 시그널 단계에서 아래 조건을 만족할 때 `preliminary review trigger`를 생성합니다.

1. verified evidence가 있음
2. 방향성이 `bullish` 또는 `bearish`로 명확함
3. 포트폴리오 또는 감시 유니버스에 직접 영향
4. hidden candidate인 경우 path validation이 충분히 높음
5. 기존 포지션과의 충돌 또는 신규 수혜/피해 논리가 분명함

## 긴급도 체계

### P0. 즉시 토론
조건:
- 규제/공시/실적/가이던스/리콜/파산/CEO/대형 계약
- `verified`
- 포트폴리오 직접 연관 또는 강한 hidden candidate

동작:
- 즉시 토론 큐 등록
- approval 후보 갱신
- 필요 시 포지션 리뷰 트리거

### P1. 15분 내 토론
조건:
- `score_total >= 75`
- 방향성 강함
- source tier 양호
- 포트폴리오 또는 관련 종목 영향 가능성 높음

동작:
- 다음 토론 슬롯에 등록
- approval 후보 유지

### P2. 시그널만 생성
조건:
- 점수는 높지만 evidence가 약함
- hidden candidate 가능성은 있으나 아직 애매함

동작:
- `pending_approval` 또는 `monitor_only`
- 토론 미호출 가능

### P3. 저장만
조건:
- 일반 시장 요약
- 반복 기사
- 낮은 품질 출처
- 방향성 없음

동작:
- memory 저장만
- 토론/시그널 미호출

## 토론 큐 등록 규칙

### 등록 조건
- `event_key` 기준으로 새롭거나 의미 있게 업데이트된 이벤트
- 토론 기준 충족

### 중복 방지
- 동일 `event_key`는 `30분` 내 재토론 금지
- 동일 ticker에 미해결 토론이 있으면 merge 우선
- 같은 테마/이슈가 반복되면 최신 증거만 추가하고 재토론 여부 재판정

### merge 기준
- 같은 ticker
- 같은 direction
- 또는 같은 `event_key`

## 투자 변화 트리거 유형

### 1. `add_review`
- 신규 bullish thesis 강화
- 기존 미보유 종목 또는 저비중 종목

### 2. `reduce_review`
- 기존 bullish 포지션에 bearish 이벤트
- 공급망/규제/실적 악화

### 3. `exit_review`
- P0 급 negative event
- verified
- 포지션 직접 연관

### 4. `hedge_review`
- 개별 종목보다 섹터/거시 리스크가 더 클 때

### 5. `monitor_only`
- 확신 부족
- 시그널은 있으나 행동까지는 이르지 않음

## 추천 운영 임계값

### 뉴스 -> 시그널
- 기본 threshold: 현재 `SIGNAL_MIN_SCORE`
- 추천 운영값:
  - 장중: `58~62`
  - 장마감 후 느슨 평가: `55~58`

### 시그널 -> 토론
- 추천값:
  - `score_total >= 75`
  - 또는 `urgency == immediate`
  - 또는 `portfolio_hit`

### hidden candidate -> 토론
- 추천값:
  - `validation_score >= 0.65`
  - `path_score >= 0.55`
  - evidence 2개 이상 또는 strong source tier 포함

### 투자 변화 트리거
- 추천값:
  - `verified`
  - `direction != neutral`
  - `portfolio/direct watchlist relation == true`

## 로컬 모델 역할 분담

### 로컬 모델이 해야 할 것
- 검색어 추출
- JSON 분류
- evidence summary
- News Context Pack의 짧은 limitations/요약 해석
- judge
- 토론 판독 보조

### 로컬 모델이 하면 안 되는 것
- 모든 뉴스 원문 장문 독해
- 아무 필터 없이 멀티홉 탐색
- 단독 최종 투자결정

즉:

- `탐색 범위 확장`: 온톨로지
- `최신 사실 수집`: 웹 evidence
- `판단`: LLM 오케스트레이션
- `실행`: signal/risk/paper broker

## 지금 코드 기준 추천 자동화 설정

### 장중 운영
- `run_news.bat`: `10분`
- `run_news_context.bat`: `30분`
- `run_signals.bat`: `10분`
- `run_debates.bat`: `10~15분` 또는 `event-driven`
- `run_replay.bat`: `60분`

### 장마감 후
- `run_replay.bat`: `1회`
- `run_daily.bat`: `1회`

### 백필
- `run_news_backfill.bat`: `1회`

## 현재 기준에서 남은 과제
자동화 프로세스 관점에서 아직 더 필요한 것은 아래입니다.

1. regime-aware filter
2. sector correlation risk
3. false positive taxonomy
4. 토론 큐 우선순위에 path attribution 성과를 반영하는 적응형 정책
5. broker sandbox 단계의 market-hours validation
6. 뉴스팩 품질 점수와 실제 replay 성과의 상관관계 검증

## 관련 문서
- 전체 로드맵/상태:
  - `MASTER_SYSTEM_ROADMAP_AND_STATUS_2026-04-05.md`
- 투자 프로세스 감사:
  - `INVESTMENT_PROCESS_AUDIT_AND_BROKER_ROADMAP_2026-04-05.md`
- 실거래 제외 구현 기록:
  - `PAPER_EXECUTION_AND_REPLAY_IMPLEMENTATION_2026-04-05.md`
