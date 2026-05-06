# Investment Process Audit And Broker Roadmap (2026-04-05)

## 결론
현재 시스템은 `아이디어 생성 + 이벤트 트리아지 + 승인형 페이퍼 실행` 단계로는 의미가 있습니다.
하지만 아직 `지속적으로 이익을 기대할 만큼 탄탄한 투자 실행 시스템`이라고 보기는 어렵습니다.

판정은 다음과 같습니다.

- 리서치/탐색 엔진: `중상`
- 근거 품질 통제: `중`
- 시그널 실행 안전성: `중하`
- 실거래 준비도: `하`
- 수익성 검증 가능성: `하`

즉 지금은 `잘 설계된 research/trade-idea engine`에 가깝고,
`production trading bot`에는 아직 이르지 않았습니다.

## 상태 업데이트 (2026-04-05 후속 구현 반영)
이 문서 초안 작성 이후, 다음 항목은 실제 구현으로 반영되었습니다.

- `BrokerAdapter`
- `PaperBroker`
- paper account / position / order / fill 상태 모델
- `RiskManager`
- `ReplayEngine`
- `PerformanceTracker`
- `NewsContextPackService`
- 독립 뉴스팩 실행 진입점(`run_news_context.bat`, `src/news_context_job.py`)

즉 아래 로드맵 중 다음은 이제 `완료 또는 초기 완료` 상태입니다.

- Phase 0. 연구 계좌 추상화: `완료`
- Phase 1. Paper Brokerage State Sync: `완료`
- Phase 2. Replay / Evaluation Engine: `초기 완료`
- 뉴스/근거 독립 모듈화: `초기 완료`

현재 운영 정책은 `최종 투자 판단 100% 수동`입니다. 자동화는 시그널/토론/paper/replay까지 보조하되, 실거래 브로커 연동과 자동 주문은 이 문서의 후속 단계로만 남겨둡니다.

현재 기준 전체 상태는 아래 문서를 함께 보세요.

- `MASTER_SYSTEM_ROADMAP_AND_STATUS_2026-04-05.md`
- `PAPER_EXECUTION_AND_REPLAY_IMPLEMENTATION_2026-04-05.md`

## 현재 강점

### 1. 최신 근거 우선 구조
- 구조화 뉴스 이벤트(`news_events`)
- 웹검증 evidence(`research_evidences`)
- 독립 뉴스 판단 패키지(`news_context_packs`, `NewsContextPackService`)

이 구조 덕분에 시스템이 과거 결론만 되풀이하지 않고, 최신 근거를 재사용할 수 있습니다.

### 2. 승인형 실행 구조
- 자동 주문이 아니라 승인 후 실행
- kill switch
- 일/시간 주문 한도
- approval TTL

이건 실거래 이전 단계에서 매우 중요한 안전 설계입니다.

### 3. 온톨로지 기반 hidden candidate 탐색
- 직접 언급된 종목만 보는 것이 아니라
- 공급망/경쟁/commonsense demand transfer를 통해
- 숨은 후보를 검증 큐로 올릴 수 있습니다.

### 4. 로컬 모델을 좁은 역할로 분리할 수 있는 구조
- 검색어 추출
- JSON 분류
- evidence 요약
- judge 보조

이건 16GB VRAM 환경에서 매우 현실적인 방향입니다.

## 아직 이익을 장담할 수 없는 이유

### 1. 시그널의 예측력 검증이 없다
현재는 이벤트를 점수화하고 승인대기로 올릴 수는 있지만,
그 시그널이 실제로 유의미한 수익을 냈는지 측정하는 프레임이 없습니다.

부족한 것:
- event entry/exit replay
- 기준 수익률(baseline) 비교
- precision/recall
- expectancy
- holding-period별 성과
- false positive 분석

### 2. 포지션 사이징이 너무 단순하다
현재는 사실상 `1단위 페이퍼 체결` 수준입니다.
실전에서는 최소한 다음이 필요합니다.

- 계좌 규모 기반 sizing
- 포지션별 risk budget
- 변동성 기반 size 조절
- 테마/섹터/종목 집중도 한도

### 3. 체결 현실성이 없다
실거래 이익은 signal quality만이 아니라 execution quality에 크게 좌우됩니다.
현재 부족한 것:

- bid/ask spread
- 슬리피지
- 장중 유동성
- 프리마켓/애프터마켓 제한
- partial fill
- order rejection / replace

### 4. 포트폴리오 상태 동기화가 없다
현재는 로컬 포트폴리오 파일을 읽는 구조이지,
실제 브로커 계좌 상태와 포지션/현금/미체결 주문을 동기화하지 않습니다.

즉:
- 계좌 실제 포지션
- 주문 대기 상태
- 실현손익 / 미실현손익
- buying power

를 시스템이 아직 authoritative source로 갖고 있지 않습니다.

### 5. 리스크 관리가 이벤트 단위에 머무른다
현재 가드레일은 의미 있지만 아직 얕습니다.

부족한 것:
- 총 익스포저 한도
- 순롱/순숏 한도
- 섹터 상관관계 한도
- 실시간 max drawdown 제어
- 변동성 급등 시 자동 강등
- 시장 체제 변화(regime shift) 감지

### 6. 모델 출력이 수익으로 이어지는지 피드백 루프가 없다
이익을 내려면 시스템은 결국 이런 질문에 답해야 합니다.

- 어떤 relation path가 유효했는가
- 어떤 hidden candidate가 실제 알파로 이어졌는가
- 어떤 뉴스 source 조합이 더 좋은가
- 어떤 prompt/pipeline이 false positive를 줄였는가

지금은 이 피드백 루프가 거의 없습니다.

## 로컬 모델 관점 평가

### 당신 환경
- VRAM 16GB
- RAM 48GB
- 후보:
  - `gpt-oss:20b`
  - `qwen 3.5 9b` 계열
  - LM Studio에서 가능한 GGUF/QAT 계열

### 판단
이 투자 프로세스에서 로컬 모델은 `주연`이 아니라 `전문화된 보조 모델`이어야 합니다.

#### 적합한 용도
- 티커/엔티티 추출
- 간단한 JSON 판정
- 짧은 evidence 묶음 요약
- 토론 결과 판독

#### 부적합한 용도
- 장문 원문 뉴스 일괄 독해
- 자유형 멀티홉 탐색 + 결론 생성
- 최종 투자 의사결정 단독 수행

### 추천
- `Qwen 9B급`: extract/json/router
- `20B~27B급`: judge/rag_answer/evidence summary
- 프론티어 모델: 최종 장문 논증과 difficult arbitration

즉 작은 로컬 모델도 충분히 쓸 수 있지만,
그 전제는 `탐색/검증/판단을 분리`하고 각 단계 입력을 강하게 축약하는 것입니다.

## 실거래 계좌 연동 전 필수 체크리스트

### 절대 먼저 해야 하는 것
1. event replay 백테스트
2. paper 계좌 상태 동기화
3. order state machine
4. 포지션 사이징 규칙
5. 체결/슬리피지 기록
6. 손익 및 MDD 추적
7. 장애 시 fail-safe 정지

### 아직 하면 안 되는 것
- 신호 검증 없이 바로 실계좌 연동
- 브로커 주문을 Discord 명령에 바로 연결
- 포지션 관리 없는 event-driven 진입

## 계좌 연동 로드맵

### Phase 0. 연구 계좌 추상화
목표:
- 브로커보다 먼저 내부 account abstraction을 만든다.

필요 컴포넌트:
- `BrokerAdapter` 인터페이스
- `AccountSnapshot`
- `PositionSnapshot`
- `OpenOrderSnapshot`
- `ExecutionReport`

핵심 메서드:
- `get_account()`
- `get_positions()`
- `get_open_orders()`
- `submit_order()`
- `cancel_order()`
- `replace_order()`
- `get_fills()`

### Phase 1. Paper Brokerage State Sync
목표:
- 내부 paper trading을 브로커처럼 동작시키기

추가 테이블 제안:
- `account_snapshots`
- `positions`
- `open_orders`
- `fills`
- `execution_events`

이 단계에서 해야 할 것:
- entry/exit rule을 구조화
- 체결 후 포지션 평균단가 계산
- realized/unrealized pnl 계산
- 동일 ticker 중복 신호 병합

### Phase 2. Replay / Evaluation Engine
목표:
- 실제 알파가 있는지 측정

필요 기능:
- 과거 `news_events`와 시그널 생성 시점 replay
- 신호 생성 시점 이후 15m / 1h / 1d / 3d 성과 측정
- benchmark 비교(SPY/QQQ/섹터 ETF)
- hidden candidate path별 승률 집계

핵심 지표:
- win rate
- average win / average loss
- expectancy
- max drawdown
- profit factor
- hit rate by source / relation / topic

### Phase 3. Broker Sandbox Integration
목표:
- 실거래가 아니라 sandbox/paper API와 연결

후보:
- 미국주식: Alpaca paper, IBKR paper
- 한국주식: KIS mock/sandbox 가능 여부 검토

이 단계에서 필요한 기능:
- order id 매핑
- partial fill 대응
- cancel/replace
- market hours validation
- order rejection 사유 로깅

### Phase 4. Live Small-Capital Rollout
목표:
- 아주 작은 자본으로 제한된 종목/세션/전략만 운영

필수 조건:
- 1일 최대 주문 수 제한
- 1일 최대 손실 한도
- 단일 이벤트 최대 노출 한도
- 전략별 enable/disable 스위치
- 수동 승인 유지

### Phase 5. Semi-Automated Trade Bot
목표:
- 완전자동이 아니라 감독형 반자동

추천 구조:
- 자동 탐색
- 자동 evidence 패키지
- 자동 proposal
- 사용자 승인 또는 사전 승인 정책
- 주문 후 자동 모니터링/청산 제안

## 추천 파일 구조

### 신규/확장 파일
- `src/broker_adapter.py`
- `src/brokers/base.py`
- `src/brokers/paper_broker.py`
- `src/brokers/alpaca_adapter.py`
- `src/brokers/ibkr_adapter.py`
- `src/account_manager.py`
- `src/order_manager.py`
- `src/replay_engine.py`
- `src/performance_tracker.py`
- `src/risk_manager.py`

### 기존 파일 확장
- `src/trading_executor.py`
  - paper executor -> broker orchestration layer로 승격
- `src/signal_engine.py`
  - signal lifecycle + entry/exit 제안
- `src/db_manager.py`
  - account / order / fill / pnl 스키마 추가

## 추천 운영 정책

### 전략 분리
- `research mode`
- `paper signal mode`
- `sandbox execution mode`
- `live approved mode`

각 모드는 명시적으로 분리해야 합니다.

### 실거래 전 최소 기준
- replay 3개월 이상
- signal count 충분
- out-of-sample 기간 확보
- strategy expectancy > 0
- max drawdown 통제 가능

## 지금 당장 가장 가치 있는 다음 작업
1. `replay_engine` 설계
2. `BrokerAdapter` 추상화
3. account/position/order/fill 스키마 추가
4. paper broker를 실제 broker처럼 상태 동기화
5. hidden candidate path 성과 기록

## 최종 판단
현재 시스템은 `좋은 투자 리서치/아이디어 발굴 엔진`으로 발전 중입니다.
하지만 `실거래 수익 엔진`이 되려면, 앞으로의 중심축은 LLM이 아니라 아래 4가지여야 합니다.

1. 검증 가능한 replay
2. 브로커 상태 동기화
3. 포지션/리스크 관리
4. execution reality modeling
