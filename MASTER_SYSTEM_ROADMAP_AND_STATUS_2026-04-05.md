# Master System Roadmap And Status (2026-04-05)

## 이 문서의 목적
이 문서는 현재 프로젝트의 목표, 핵심 설계 원칙, 지금까지 구현된 내용, 아직 남은 과제, 다음 작업 우선순위를 한 번에 보기 위한 기준 문서입니다.

개별 구현 상세는 다른 문서에 남기되, 전체 방향성과 현재 상태 판정은 이 문서를 기준으로 정리합니다.

## 상태 업데이트 (2026-05-05)
최근 구현으로 뉴스 처리는 그룹챗 모델에서 분리된 독립 모듈이 되었습니다.

- `src/news_context_pack.py`: 뉴스 이벤트, 기사 원문 메타데이터, 누적 웹검증 evidence를 `News Context Pack`으로 구성
- `src/news_context_job.py`: 디스코드/그룹챗 없이 뉴스팩만 생성하는 독립 실행 진입점
- `run_news_context.bat`: Windows 수동/스케줄러 실행용 배치
- `news_context_packs`: 생성된 판단 패키지 저장 테이블

현재 기본 운영 모드는 `최종 투자 판단 100% 수동`입니다. 자동화는 수집, 정리, 검증, 토론 보조, paper/replay 성과 측정까지 담당하고, 실거래 브로커 연동은 아직 활성화하지 않습니다.

## 시스템의 최종 목표
이 프로젝트의 최종 목표는 단순 뉴스 요약이나 단순 종목 추천이 아닙니다.

핵심 목표는 다음 루프를 안정적으로 굴리는 것입니다.

1. 시장 이벤트를 빠르게 수집하고 구조화한다.
2. 직접 언급된 종목만이 아니라 온톨로지 기반으로 숨은 관련 후보까지 탐색한다.
3. 웹 evidence와 누적 근거 메모리로 최신성과 사실성을 검증한다.
4. 여러 LLM 오케스트레이션을 통해 찬반 근거와 반대 시나리오를 검토한다.
5. 승인형 시그널과 페이퍼 execution으로 실행 가능성을 검증한다.
6. replay와 성과 추적으로 실제 알파 가능성을 측정한다.
7. 검증이 충분히 쌓인 뒤에만 브로커 sandbox 또는 실거래 연동으로 확장한다.

즉 이 시스템은 본질적으로 다음을 목표로 합니다.

- `온톨로지 기반 탐색`
- `근거 중심 LLM 판단`
- `승인형 실행`
- `사후 성과 검증`

## 핵심 설계 원칙

### 1. 온톨로지는 결론 엔진이 아니라 탐색 엔진이다
- 온톨로지는 종목/이슈/연결고리를 넓힌다.
- 최종 투자 판단은 온톨로지가 아니라 evidence와 LLM 오케스트레이션이 한다.

### 2. 로컬 모델은 범용 초지능이 아니라 전문 작업자로 쓴다
- `extract`
- `json`
- `judge`
- `evidence summary`
- `rag answer`

이렇게 역할을 좁혀 16GB VRAM 환경에서도 안정적으로 돌리는 것이 원칙입니다.

### 3. 최신성은 누적 기억보다 우선한다
- 누적 데이터는 메모리로 사용한다.
- 최종 판단은 최근 이벤트와 웹 evidence를 우선한다.

### 4. 실행은 fail-open이 아니라 fail-safe여야 한다
- 가격 조회 실패 시 실행 중단
- 상태 불일치 시 실행 중단
- 리스크 한도 초과 시 실행 중단

### 5. 수익성은 구현이 아니라 검증으로 판단한다
- 코드가 완성됐다고 수익성이 입증되지는 않는다.
- replay, out-of-sample, paper 결과가 쌓여야만 다음 단계로 간다.

## 전체 로드맵

### Phase A. Research Core
목표:
- 뉴스, 근거 메모리, 웹 검증, 토론/RAG 기반을 안정화

핵심 항목:
- 구조화 뉴스 이벤트
- News Context Pack
- evidence memory 하위 호환
- RAG 확장
- 웹 source 품질 개선
- LLM 실패 격리

현재 상태:
- `완료`
- 뉴스 Context Pack, 자동 토론 큐(`debate_queue`), 투자 변화 트리거(`investment_review_triggers`)까지 연결됨

### Phase B. Ontology Discovery
목표:
- 직접 언급되지 않은 숨은 관련 후보를 보수적으로 탐색

핵심 항목:
- entity 정규화
- relation expansion
- hidden candidate discovery
- commonsense seed 운영

현재 상태:
- `기초 완료`
- 아직 relation validation과 attribution은 더 필요

### Phase C. Approval Signal Workflow
목표:
- 이벤트를 단기 시그널로 점수화하고 승인 상태를 일관되게 관리

핵심 항목:
- signal scoring
- approval lifecycle
- 상태 만료/회수
- fail-safe execution gating

현재 상태:
- `완료`

### Phase D. Paper Execution And Risk
목표:
- 내부 paper broker 기준으로 계좌/포지션/주문/체결을 일관되게 추적

핵심 항목:
- broker adapter
- paper account state
- positions/orders/fills
- sizing and exposure risk controls

현재 상태:
- `완료`

### Phase E. Replay And Performance Evaluation
목표:
- 실행 또는 시그널 기준으로 horizon별 성과와 alpha를 측정

핵심 항목:
- replay engine
- performance tracker
- expectancy / win rate / profit factor
- benchmark alpha
- out-of-sample split
- equity curve / MDD
- attribution

현재 상태:
- `상당 부분 완료`
- out-of-sample split, equity curve, MDD, attribution은 구현됨
- 아직 체결 현실성 고도화와 path attribution 심화는 남음

### Phase F. Production Research Quality
목표:
- 실제 수익 가능성을 판단할 정도로 연구 품질과 평가 품질을 끌어올림

핵심 항목:
- better source ranking
- evidence-specific FTS/ranking
- false positive taxonomy
- hidden candidate path attribution
- regime-aware filtering

현재 상태:
- `부분 완료`
- source tier / evidence ranking / hidden candidate validation은 구현됨
- false positive taxonomy, evidence-specific FTS, regime-aware filtering은 미완료

### Phase G. Broker Sandbox / Live Rollout
목표:
- 충분한 검증 후 broker sandbox, 이후 소액 실거래로 확장

핵심 항목:
- broker API mapping
- partial fill / replace / reject
- market hours validation
- live capital risk controls

현재 상태:
- `미착수`

## 지금까지 작업한 핵심 내용

### 1. 뉴스 Context Pack과 토론/RAG 정제
- `src/news_context_pack.py`
- `src/news_context_job.py`
- `src/evidence_memory.py` (하위 호환/이전 계층)
- `src/debate_manager.py`
- `src/rag_agent.py`
- `src/summarizer.py`

요약:
- 원문 뉴스 덩어리 대신 구조화 뉴스 이벤트, 관련 기사, recent evidence를 `News Context Pack`으로 정리
- 뉴스팩은 `strong/usable/weak/empty`, `web_required`, source tier, 부족 시 권장 웹검색 쿼리를 포함
- RAG가 과거 토론뿐 아니라 최신 뉴스팩 컨텍스트도 함께 참조
- 일간 요약에 시장 이벤트를 포함

### 2. DB 하드닝과 상태 정합성
- `src/db_manager.py`
- `src/signal_engine.py`

요약:
- 테스트 가능한 DB 경로 주입
- FTS 1회 부트스트랩
- approval / signal / recommendation 상태 동기화
- 근거/실행/성과 저장 계층 확대

### 3. 웹 검증과 로컬 모델 운영 개선
- `src/web_search_agent.py`
- `src/llm_client.py`
- `src/main.py`

요약:
- source quality 기반 정렬
- URL 정규화와 중복 제거
- LM Studio/OpenAI-compatible 로컬 모델 지원
- 역할별 컨텍스트 예산
- 모델 오류 문자열의 장기 기억 오염 차단

### 4. 온톨로지 + commonsense 탐색
- `src/ontology/store.py`
- `src/ontology/planner.py`
- `src/ontology_bootstrap.py`
- `commonsense_ontology.example.json`

요약:
- 2~3 hop hidden candidate 탐색
- commonsense seed 기반 간접 수혜/간접 피해 후보 발굴
- 웹 검증 쿼리 확장

### 5. paper execution / risk / replay
- `src/broker_adapter.py`
- `src/paper_broker.py`
- `src/risk_manager.py`
- `src/trading_executor.py`
- `src/performance_tracker.py`
- `src/replay_engine.py`
- `src/replay_job.py`

요약:
- 승인형 실행이 실제 계좌/포지션 상태를 갱신
- 종목/총 익스포저/현금 부족 리스크 차단
- replay와 alpha/expectancy 집계 가능
- out-of-sample split, equity curve, MDD, attribution 기록 가능
- slippage/spread/urgency 기반 execution realism 반영
- stop rule / TTL 기반 replay exit 반영

### 6. 자동 토론 큐와 변화 트리거
- `src/debate_job.py`
- `src/signal_job.py`
- `src/main.py`

요약:
- 시그널 엔진이 점수/긴급도/source tier/hidden candidate를 기준으로 자동 토론 큐를 생성
- 동일 이벤트/티커 기준 merge와 재등록 cooldown 적용
- 포트폴리오 직접 hit와 hidden candidate hit에 대해 `add_review`, `reduce_review`, `exit_review`, `hedge_review`를 생성
- Discord 명령 `!토론큐`, `!변화트리거`와 배치 `run_debates.bat`로 운영 상태 확인 가능

## 현재 상태 평가
실거래 브로커 연결을 제외한 기준에서의 보수적 평가는 다음과 같습니다.

- 리서치/탐색력: `78/100`
- 온톨로지 기반 숨은 후보 발굴력: `74/100`
- 근거 최신성/신뢰성 통제: `73/100`
- 승인형 paper execution 안정성: `76/100`
- 내부 계좌/포지션 상태 일관성: `73/100`
- replay/성과 검증 준비도: `67/100`
- 실거래 연결 제외 종합 완성도: `74/100`
- 인간 최종판단 보조 시스템 완성도: `80/100`

해석:
- 지금은 `수익 가능성을 검증할 수 있는 시스템`으로는 의미가 크다.
- 아직 `수익성이 입증된 시스템`은 아니다.

## 더 남아있는 과제

### 최우선
1. `relation path / hidden candidate attribution 심화`
2. `슬리피지/체결 현실성 고도화`
3. `정식 exit rule / take-profit / trailing discipline`
4. `regime-aware filtering`
5. `evidence-specific FTS/ranking`
6. `News Context Pack 품질 점수와 실제 replay 성과의 상관관계 추적`

### 다음 우선순위
1. `섹터/테마 상관관계 제한`
2. `false positive taxonomy`와 실패 패턴 저장
3. `event type별 holding policy`
4. `partial fill / cancel / replace`
5. `DB repository 분리`

### 이후 단계
1. broker sandbox adapter
2. partial fill / cancel / replace
3. 실시간 계좌 authoritative state sync
4. 소액 live rollout gate

## 지금 바로 가장 중요한 다음 작업
가장 가치가 큰 다음 3개는 이겁니다.

1. `path attribution + failure taxonomy`
2. `regime/sector-aware risk`
3. `execution realism 고도화`
4. `뉴스팩 품질 저하 시 자동 웹검증/공식소스 보강 루프`

이 세 가지가 먼저여야만, 나중에 broker 연결을 붙였을 때도 시스템이 단순 자동주문기가 아니라 검증 가능한 투자 엔진으로 유지됩니다.

## 관련 문서 맵
- 하드닝 기록:
  - `SYSTEM_HARDENING_NOTES_2026-04-05.md`
- 실거래 제외 구현 기록:
  - `PAPER_EXECUTION_AND_REPLAY_IMPLEMENTATION_2026-04-05.md`
- 투자 프로세스 감사:
  - `INVESTMENT_PROCESS_AUDIT_AND_BROKER_ROADMAP_2026-04-05.md`
- 로컬 모델 런타임:
  - `LOCAL_MODEL_RUNTIME_GUIDE_2026-04-05.md`
- 온톨로지 설계:
  - `PUBLIC_ONTOLOGY_AND_COMMONSENSE_PLAN.md`
- 2026-05-05 구현/문서 정리:
  - `IMPLEMENTATION_RECORD_2026-05-05.md`

## 검증 기준
현재 기준 코드 검증은 아래 명령으로 통과했습니다.

```bash
python3 -m compileall src tests
python3 -m unittest discover -s tests
```
