# System Hardening Notes (2026-04-05)

## 목적 재정의
이 시스템의 목표는 단순 뉴스 요약이 아니라, 다음 루프를 안정적으로 굴리는 것입니다.

1. 최신 시장 이벤트를 구조화해서 누적한다.
2. 누적된 근거와 최근 이벤트를 바탕으로 토론을 시작한다.
3. 토론 과정에서 필요한 지점만 웹검증을 추가한다.
4. 단기 시그널은 상태 정합성을 유지한 채 승인/실행으로 이어진다.
5. 결과와 근거가 다음 질의와 다음 토론의 메모리로 다시 재사용된다.

## 이번 작업의 핵심 보강

### 1. Evidence Memory 계층 추가
- 신규 파일: `src/evidence_memory.py`
- 역할:
  - 최근 `news_events`를 주제 키워드/티커 기준으로 수집
  - 최근 `research_evidences`를 함께 묶어 토론용 브리프 생성
  - RAG에서 재사용할 수 있는 최근 근거 컨텍스트 생성
- 효과:
  - 과거 토론 결론만 검색하던 구조에서 최근 구조화 뉴스/웹검증까지 기억 범위를 확장
  - 최근 뉴스 원문 덩어리 대신 “핵심 이벤트 + 누적 evidence” 브리프를 투입

### 1-1. News Context Pack 계층 추가 (2026-05-05)
- 신규 파일:
  - `src/news_context_pack.py`
  - `src/news_context_job.py`
  - `run_news_context.bat`
- 수정 파일:
  - `src/db_manager.py`
  - `src/debate_manager.py`
  - `src/rag_agent.py`
  - `scripts/windows/run_task.ps1`
  - `scripts/windows/install_scheduled_tasks.ps1`
- 역할:
  - 뉴스 처리를 그룹챗/토론 모델과 분리
  - `news_events`, 관련 `news_articles`, `research_evidences`를 하나의 `News Context Pack`으로 구성
  - 품질 상태(`strong/usable/weak/empty`), `web_required`, source tier, 권장 웹검증 쿼리 제공
  - 생성된 패키지를 `news_context_packs` 테이블에 저장
- 효과:
  - 토론/RAG는 DB 원자료를 직접 해석하지 않고, 독립 뉴스 모듈이 만든 판단 패키지만 사용
  - 로컬 뉴스 메모리가 비거나 약할 때 웹 재검증 필요성을 명시적으로 드러냄
  - Windows 작업 스케줄러에서 `NewsContextPack` 작업을 그룹챗과 별개로 돌릴 수 있음

### 2. DB 계층 강화
- 수정 파일: `src/db_manager.py`
- 변경점:
  - `DBManager(db_path=...)` 지원으로 테스트 가능성 확보
  - `system_metadata` 테이블 추가
  - FTS rebuild를 매 프로세스 초기화마다 반복하지 않고 1회 부트스트랩으로 제한
  - 직접 `cursor` 접근을 줄이기 위한 조회 메서드 추가:
    - `list_debates_by_date`
    - `list_summaries_by_type_since`
    - `search_debates_like`
    - `search_summaries_like`
    - `get_news_events_for_context`
    - `get_recent_research_context`
    - `list_news_articles_for_events`
    - `save_news_context_pack`
    - `get_latest_news_context_pack`
    - `list_news_events_since`
- 효과:
  - 누적 기억 검색 범위를 근거 데이터까지 확장
  - 테스트에서 별도 DB 파일 사용 가능
  - 시작 시 불필요한 전체 FTS 재색인 비용 감소

### 2-1. DB connection lifecycle 하드닝 (2026-05-23)
- 수정 파일:
  - `src/db_manager.py`
  - `src/ontology/store.py`
  - `src/signal_engine.py`
- 변경점:
  - `DBManager`와 `OntologyStore`에 명시적 `close()`와 context manager를 추가
  - 객체 해제 시 남은 SQLite connection을 안전하게 닫는 fallback을 추가
  - `SignalEngine`이 내부 온톨로지 저장소 connection을 정리하도록 보강
  - 외부에서 주입된 DB connection은 호출자가 소유하도록 ownership을 분리
- 효과:
  - Windows에서 임시 SQLite 파일 삭제가 실패하던 테스트 불안정성 제거
  - 배치/테스트/장기 실행 프로세스에서 connection 정리 경로가 명확해짐

### 3. 토론 입력 정제
- 수정 파일: `src/debate_manager.py`
- 변경점:
  - 최근 며칠치 뉴스 텍스트 파일을 크게 읽어 넣는 방식 제거
  - 온톨로지 플랜 이후 `NewsContextPackService`를 통해 최근 이벤트/기사/누적 evidence를 품질 표시된 패키지로 주입
  - 모델 장애 문자열을 실제 주장처럼 저장하지 않도록 실패 응답 격리
- 효과:
  - 토론 입력이 “원문 덩어리”에서 “품질 표시된 뉴스 Context Pack”으로 변경
  - LLM API 장애가 RAG/판정 데이터 오염으로 이어지는 문제 완화

### 4. RAG 검색 범위 확장
- 수정 파일: `src/rag_agent.py`
- 변경점:
  - debate/summaries 검색 외에 `NewsContextPackService` 기반 최근 뉴스/웹검증 컨텍스트 병합
  - 로컬 모델 키워드 추출 실패 시 휴리스틱 fallback 유지
- 효과:
  - `!질문`이 과거 토론 기록만 뒤지는 구조에서 최근 근거 기억까지 참고하는 구조로 확장

### 5. 요약기 개선
- 수정 파일: `src/summarizer.py`
- 변경점:
  - 일간 요약이 토론 결론만이 아니라 당일 구조화 뉴스 이벤트까지 함께 요약
  - DB 직접 cursor 접근 대신 메서드 사용
- 효과:
  - 일/주/월 요약이 “의견 요약”에서 “시장 이벤트 + 판단 요약”에 더 가까워짐

### 6. 웹 검증 품질 보강
- 수정 파일: `src/web_search_agent.py`
- 변경점:
  - URL 정규화
  - 결과 중복 제거
  - 공식/주요 언론 도메인 우선 정렬
  - evidence에 `source_quality` 포함
- 효과:
  - DDG 검색 결과가 그대로 들어가던 구조에서 최소한의 출처 우선순위 부여

### 7. 시그널 상태 정합성 보강
- 수정 파일: `src/db_manager.py`, `src/signal_engine.py`
- 변경점:
  - 승인 만료 시 `approval_requests`만이 아니라 `signal_events`, `signal_recommendations`도 함께 `expired`로 동기화
  - 더 이상 actionable 하지 않은 이벤트는 `superseded`/`monitor_only`로 회수
  - 이미 `approved` 상태인 요청을 주기 배치가 다시 `pending`으로 덮어쓰지 않도록 보정
- 효과:
  - 배치 재실행 중 승인 상태가 흔들리는 문제 완화
  - 보드/승인/추천 상태가 서로 어긋나는 현상 완화

### 8. 실행 fail-safe 강화
- 수정 파일: `src/trading_executor.py`
- 변경점:
  - 가격 조회 실패 시 `$1.0` 대체 체결 제거
  - 추천 종목 가격이 하나라도 조회되지 않으면 전체 실행 중단
  - 가격 검증 이후 승인/체결 순서로 변경
- 효과:
  - 페이퍼 로그 오염 방지
  - 사후 성과 측정 왜곡 감소

### 9. 일반 채팅 이어가기 안전성 보강
- 수정 파일: `src/main.py`
- 변경점:
  - GPT 실패 문자열을 대화 메모리에 저장하지 않고 사용자에게 오류 메시지 반환
- 효과:
  - 장애 문자열이 장기 기억에 남는 문제 완화

## 추가 테스트
신규 테스트 디렉토리: `tests/`

### 추가된 테스트
- `tests/test_signal_workflow.py`
  - 승인 만료 시 관련 상태 동기화 확인
  - 승인된 요청이 배치 재실행으로 `pending`으로 되돌아가지 않는지 확인
  - 더 이상 actionable 하지 않은 이벤트가 `superseded` 처리되는지 확인
- `tests/test_trading_executor.py`
  - 가격 조회 실패 시 fail-closed 동작 확인
  - 정상 가격이 있을 때만 주문 로그가 남는지 확인
- `tests/test_evidence_memory.py`
  - 최근 뉴스 이벤트와 research evidence가 하나의 근거 메모리로 수집되는지 확인
- `tests/test_news_context_pack.py`
  - 공식/주요 출처가 있는 뉴스팩이 `strong` 상태로 생성/저장되는지 확인
  - 로컬 뉴스/웹검증 메모리가 없으면 `web_required=true`와 보완 검색어를 반환하는지 확인

### 검증 명령
```bash
python3 -m compileall src tests
python3 -m unittest discover -s tests
```

## 운영 관점에서의 의미

### 이전 구조
- 최근 뉴스 텍스트 뭉치를 로컬 모델에 요약시킨 뒤 토론에 투입
- RAG는 거의 과거 토론 결론/요약만 재사용
- 승인/추천/시그널 상태가 배치 실행 중 흔들릴 여지 존재
- 시세 조회 실패 시도 로그가 오염될 수 있음

### 현재 구조
- 최근 구조화 뉴스 이벤트 + 기사 출처 tier + 최근 웹검증 evidence를 독립 News Context Pack으로 만든 뒤 토론 시작 메모리로 사용
- RAG가 최근 뉴스팩 메모리까지 참고
- 상태 만료/회수 경로가 명시적으로 생김
- 실행은 fail-safe 방향으로 강화

## 아직 남아있는 한계
- `DBManager`는 명시적 종료 경로가 생겼지만 여전히 단일 connection/cursor 패턴이 많아, 장기적으로는 connection-per-operation 또는 repository 분리가 필요함
- 웹 검증은 아직 DDG 기반이며, 공식 공시/IR/언론을 소스 타입별로 더 엄격히 분류하지는 못함
- 토론 품질과 시그널 성능을 정량 평가하는 백테스트/리플레이 프레임워크는 아직 초기 단계
- 온톨로지 관계 추출은 보수적 키워드 기반이므로, 정교한 relation validation은 추가 필요

## 다음 우선순위 제안
1. `news_events` / `research_evidences` 전용 FTS 또는 ranking layer 추가
2. 토론 결론의 사후 성과 추적 테이블 도입
3. 시그널 리플레이 평가 스크립트 추가
4. DB 계층에서 cursor 직접 노출 제거

## 후속 확장 (온톨로지)
- 공개 온톨로지 + commonsense 설계 문서:
  - `PUBLIC_ONTOLOGY_AND_COMMONSENSE_PLAN.md`
- `commonsense_ontology.example.json` 예시 추가:
  - 광산붐 -> 채굴 활동 -> 작업복 -> 리바이스
  - 데이터센터 붐 -> 전력/냉각 설비 -> Vertiv

## 후속 확장 (로컬 모델 런타임)
- `src/llm_client.py`
  - `LOCAL_MODEL_BACKEND=ollama|openai_compatible`
  - LM Studio OpenAI-compatible endpoint 지원
  - 역할별 컨텍스트 예산(`json`, `extract`, `summary`, `rag_answer`, `judge`, `evidence`)
  - 긴 입력 자동 축약
- 문서:
  - `LOCAL_MODEL_RUNTIME_GUIDE_2026-04-05.md`

## 후속 설계 문서
- 투자 프로세스 감사 + 브로커 연동 로드맵:
  - `INVESTMENT_PROCESS_AUDIT_AND_BROKER_ROADMAP_2026-04-05.md`

## 후속 구현 (실거래 제외)
- 신규 파일:
  - `src/broker_adapter.py`
  - `src/paper_broker.py`
  - `src/risk_manager.py`
  - `src/performance_tracker.py`
  - `src/replay_engine.py`
  - `src/replay_job.py`
- 추가 실행 파일:
  - `run_replay.bat`
- 핵심 효과:
  - 승인형 페이퍼 체결이 계좌/포지션 상태까지 반영
  - 종목/총익스포저 리스크 제한 도입
  - 체결 및 시그널 기준 replay 성과 측정 가능
  - split replay / equity curve / MDD / attribution 저장 가능
  - slippage/spread/urgency 기반 execution realism 반영
  - stop rule / TTL 기반 replay exit 반영
  - evidence source tier와 hidden candidate validation score 도입
  - 기대수익률/승률/알파를 저장하는 `signal_performance` 계층 추가
- 상세 기록:
  - `PAPER_EXECUTION_AND_REPLAY_IMPLEMENTATION_2026-04-05.md`
  - `MASTER_SYSTEM_ROADMAP_AND_STATUS_2026-04-05.md`

## 후속 구현 (자동 토론 큐 / 변화 트리거)
- 신규 파일:
  - `src/debate_job.py`
- 추가 실행 파일:
  - `run_debates.bat`
- 핵심 효과:
  - 시그널 엔진이 `debate_queue`를 자동 생성
  - 동일 이벤트/티커 기준 merge와 cooldown 적용
  - 포트폴리오 hit와 hidden candidate hit에 대해 `investment_review_triggers` 생성
  - Discord 명령 `!토론큐`, `!변화트리거`로 운영 상태 확인 가능
  - `run_debates.bat`로 이벤트 기반 자동 토론 실행 가능

## 후속 구현 (정보 수집 하드닝)
- 핵심 파일:
  - `src/data_fetcher/premium_crawler.py`
  - `src/db_manager.py`
- 핵심 효과:
  - 소스별 `last_attempt_at`, `last_status`, `last_error`, `last_item_count` 기록
  - `0건 저장` 실행에서는 `news_pipeline` success checkpoint 비전진
  - run별 timestamp 아카이브와 latest 아카이브 동시 저장
  - `content_hash` 기반 2차 dedup 추가
  - 최근 유사 이벤트에 대해 기존 `event_key` 재사용
  - 자정 경계 사건을 더 잘 묶기 위한 시간창 기반 클러스터링 보강

## 후속 구현 (News Context Pack 독립화)
- 핵심 파일:
  - `src/news_context_pack.py`
  - `src/news_context_job.py`
  - `run_news_context.bat`
- 핵심 효과:
  - 뉴스 모듈과 그룹챗 모델의 결합도 감소
  - 토론/RAG 입력에 품질 상태와 한계(`limitations`)를 명시
  - 로컬 뉴스 저장소가 좁거나 오래됐을 때 공식소스/Reuters/IR 중심 재검색 쿼리 생성
  - Windows 자동화에서 30분 주기 `StockBot\NewsContextPack` 작업으로 운영 가능
