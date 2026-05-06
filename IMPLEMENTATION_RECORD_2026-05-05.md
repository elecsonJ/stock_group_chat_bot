# Implementation Record (2026-05-05)

## 목적
이 문서는 최근 작업으로 실제 코드와 운영 문서에 반영된 내용을 한곳에 정리하기 위한 기록입니다.

현재 기준의 핵심 운영 원칙은 다음입니다.

- 최종 투자 판단은 사용자가 100% 수행한다.
- 자동화는 뉴스 수집, 근거 정리, 토론 보조, 시그널 생성, paper/replay 검증까지만 담당한다.
- 실거래 브로커 연동과 자동 주문은 아직 기본 운영 범위가 아니다.

## 구현된 핵심 변경

### 1. 뉴스 처리 독립 모듈화
- 신규 파일:
  - `src/news_context_pack.py`
  - `src/news_context_job.py`
  - `run_news_context.bat`
- DB 추가:
  - `news_context_packs`
- Windows 작업 추가:
  - `StockBot\NewsContextPack`
- 목적:
  - 그룹챗/토론 모델이 뉴스 DB 원자료를 직접 해석하지 않게 함
  - 독립 뉴스 모듈이 `News Context Pack`을 만들고, 토론/RAG는 그 결과만 사용

### 2. News Context Pack 구조
뉴스팩은 다음 데이터를 묶습니다.

- `news_events`: 클러스터링된 최근 이벤트
- `news_articles`: 이벤트별 기사와 출처 정보
- `research_evidences`: 누적 웹검증 evidence

뉴스팩은 다음 품질 정보를 함께 제공합니다.

- `state`: `strong`, `usable`, `usable_needs_refresh`, `weak`, `empty`
- `score`: 이벤트/근거/출처/최신성 기반 점수
- `web_required`: 웹 재검증 필요 여부
- `limitations`: 로컬 뉴스 메모리 부족, 웹검증 부족, 공식소스 부족 등
- `recommended_web_queries`: 보완 검색어

### 3. 토론/RAG 연동 변경
- `src/debate_manager.py`
  - 최신 이슈 판정 시 `NewsContextPackService`로 뉴스팩 생성
  - 뉴스팩 품질 상태와 한계를 토론 히스토리에 주입
- `src/rag_agent.py`
  - 과거 토론/요약 검색 결과에 뉴스팩 컨텍스트를 병합
  - RAG가 과거 기록뿐 아니라 최근 뉴스/웹검증 메모리도 참고

### 4. Windows 자동화 보강
- `scripts/windows/run_task.ps1`
  - `news_context` job 추가
- `scripts/windows/install_scheduled_tasks.ps1`
  - `StockBot\NewsContextPack` 30분 주기 등록
- `WINDOWS_DESKTOP_AUTOMATION_GUIDE.md`
  - `run_news_context.bat` 수동 검증 절차 추가
  - `NEWS_CONTEXT_QUERIES` 기반 수동 쿼리 점검 예시 추가

### 5. 테스트 추가
- 신규 테스트:
  - `tests/test_news_context_pack.py`
- 검증 내용:
  - 공식/주요 출처가 있는 뉴스팩은 `strong` 상태로 생성/저장됨
  - 로컬 뉴스/웹검증 메모리가 없으면 `web_required=true`와 보완 검색어가 생성됨

### 6. 공식 기업 데이터 우선 계층 추가
- 신규 파일:
  - `src/data_fetcher/sec_official.py`
  - `src/data_fetcher/dart_official.py`
- 수정 파일:
  - `src/data_fetcher/fundamental.py`
  - `src/web_search_agent.py`
  - `tests/test_sec_official.py`
  - `tests/test_dart_official.py`
- 목적:
  - 미국 상장사의 기업 재무/공시 데이터는 SEC EDGAR submissions/companyfacts API를 우선 사용
  - 한국 6자리 종목코드(`005930`, `005930.KS`, `005930.KQ`)는 OpenDART 회사개황/공시목록/단일회사 주요계정 API를 우선 사용
  - yfinance는 현재가/차트/비공식 밸류에이션 보조 데이터로 라벨을 낮춤
  - Fact-Sheet에 SEC/OpenDART 출처 URL, 최근 공시, 주요 재무계정, 데이터 한계를 명시
- 운영 설정:
  - `SEC_USER_AGENT`
  - `SEC_REQUEST_TIMEOUT_SEC`
  - `DART_API_KEY`
  - `DART_REQUEST_TIMEOUT_SEC`

### 7. 자동토론 비용 게이트/품질 검증/시장 데이터 추상화
- 신규/수정 파일:
  - `src/market_data_provider.py`
  - `src/data_fetcher/krx_kind_official.py`
  - `src/signal_engine.py`
  - `src/debate_job.py`
  - `src/main.py`
  - `src/db_manager.py`
- 핵심 동작:
  - `DEBATE_FRONTIER_MODE=gated/manual/auto/off`로 프론티어 풀토론 비용을 제어
  - 비용 게이트에서 `review_required`가 된 큐 항목은 Discord에서 `!토론승인` 전까지 자동 소비하지 않음
  - 자동/수동 토론 완료 후 공식근거, 근거ID, 반대논리, 웹리서치, 최종판결, 불확실성 표기 기준으로 품질 점수 저장
  - `!토론품질`, `!성과` 명령으로 운영자가 품질/성과를 확인
  - yfinance 직접 의존을 `MarketDataProvider`로 감싸 향후 broker/live provider 교체 지점 마련
  - 한국 종목 Fact-Sheet에 KRX/KIND 시장조치 확인 필요성을 명시
- 운영 설정:
  - `DEBATE_FRONTIER_MODE`
  - `DEBATE_AUTO_MIN_SCORE`
  - `DEBATE_REVIEW_MIN_SCORE`
  - `DEBATE_REQUIRE_VERIFIED`
  - `DEBATE_AUTO_COOLDOWN_MIN`
  - `DEBATE_JOB_MAX_ITEMS`
  - `MARKET_DATA_PROVIDER`

## 문서 정리 내용
다음 문서를 현재 구현 기준으로 수정했습니다.

- `README.md`
  - `Evidence Memory Layer` 중심 설명을 `News Context Pack Layer`로 갱신
  - 최종 투자 판단 100% 수동 운영 원칙 추가
  - `run_news_context.bat`, `NEWS_CONTEXT_QUERIES` 추가
- `MASTER_SYSTEM_ROADMAP_AND_STATUS_2026-04-05.md`
  - 2026-05-05 상태 업데이트 추가
  - Research Core에 News Context Pack 반영
  - 현재 완성도 평가 갱신
- `data_schema_and_tracking_guide.md`
  - 실제 DB 테이블 수와 `news_context_packs`, paper/replay/queue/review 테이블 반영
  - RAG 동작을 FTS + LIKE + News Context Pack 병합으로 갱신
- `SYSTEM_HARDENING_NOTES_2026-04-05.md`
  - News Context Pack 계층 추가 기록
  - 테스트와 운영 의미 갱신
- `ONTOLOGY_RAG_WEB_WORKFLOW.md`
  - Ontology -> RAG -> News Context Pack -> Web Search -> Debate/Judge 흐름으로 갱신
- `AUTOMATION_POLICY_AND_TRIGGER_RULES_2026-04-05.md`
  - 뉴스팩 생성 단계를 자동화 흐름에 추가
  - 최종 투자 판단 수동 원칙 명시
- `WINDOWS_DESKTOP_AUTOMATION_GUIDE.md`
  - Windows에서 뉴스팩 작업을 수동/스케줄러로 실행하는 절차 보강
- `System Restart Guide`
  - Ollama/LM Studio 런타임 구분, 뉴스팩 배치, healthcheck 절차 반영
- `FEATURES_AND_PORTFOLIO_GUIDE.md`
  - 독립 News Context Pack 기능 추가
- `LOCAL_MODEL_RUNTIME_GUIDE_2026-04-05.md`
  - 로컬 모델이 뉴스 원문 전체를 단독 판단하지 않고 뉴스팩의 한계/보완 필요성을 해석하는 역할로 제한됨을 명시
- `INVESTMENT_PROCESS_AUDIT_AND_BROKER_ROADMAP_2026-04-05.md`
  - 뉴스/근거 독립 모듈화 상태와 수동 최종판단 정책 반영
- `docs_debate_process.md`, `8step_debate_process.md`
  - 토론 시작 단계에 News Context Pack 주입 흐름 추가
- `SYSTEM_UPDATE_NOTES_2026-03-03.md`
  - 2026-05-05 추가 업데이트 섹션 추가
- `README.md`, `WINDOWS_DESKTOP_AUTOMATION_GUIDE.md`
  - SEC/OpenDART 공식 데이터 우선 계층과 `SEC_USER_AGENT`, `DART_API_KEY` 설정 추가

## 현재 시스템 상태
실거래 브로커 연결을 제외하면 현재 시스템은 다음 작업을 수행할 수 있습니다.

- 다중 소스 뉴스 수집/정규화/이벤트 클러스터링
- 누적 뉴스/웹검증 근거 기반 Context Pack 생성
- 미국 상장사 SEC EDGAR 공식 filings/XBRL 기반 기업 데이터 주입
- 한국 상장사 OpenDART 공식 공시/재무 기반 기업 데이터 주입
- 온톨로지 기반 직접/간접 관련 후보 탐색
- 프론티어 모델 + 로컬 모델을 활용한 근거 중심 토론
- 승인형 paper execution
- replay/performance/attribution 저장
- Windows Task Scheduler 기반 장기 실행

아직 부족한 부분은 다음입니다.

- News Context Pack 품질 점수와 실제 성과의 상관관계 검증
- evidence-specific FTS/ranking
- false positive taxonomy
- regime/sector-aware risk
- 한국 외 비미국 상장사와 KRX/KIND/기업 IR/브로커 원장 provider 추가
- 실시간/체결 가능 가격용 broker 또는 유료 market data provider 추가
- broker sandbox/live 연동 전 market-hours, partial fill, cancel/replace 처리

## 검증 기록
현재 문서 정리 직전 기준으로 아래 검증이 통과했습니다.

```bash
python3 -m py_compile src/news_context_job.py src/news_context_pack.py src/db_manager.py src/debate_manager.py src/rag_agent.py
python3 -m unittest discover -s tests
```

테스트 결과:

```text
Ran 27 tests
OK
```
