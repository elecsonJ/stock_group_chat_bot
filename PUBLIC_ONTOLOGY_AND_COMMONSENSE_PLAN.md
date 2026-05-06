# Public Ontology And Commonsense Plan

## 목표
이 프로젝트의 온톨로지는 정답을 직접 내리는 엔진이 아닙니다.

역할은 다음 3가지입니다.

1. 사용자가 직접 언급한 종목/이슈를 정규화한다.
2. 공급망/경쟁/소유관계/상식적 수요 전이를 통해 숨은 후보를 넓힌다.
3. 넓혀진 후보를 웹 evidence 검증 큐로 넘긴다.

최종 판단은 여전히 LLM 오케스트레이션 + 최신 evidence가 담당합니다.

## 공개 데이터 조합안

### 1. 구조 스키마
- FIBO
  - 금융 개념 체계의 상위 스키마로 사용
  - 엔터티 종류, 증권, 조직, 이벤트, 계약, 공시 등의 개념 정리용

### 2. 법인/지배구조
- GLEIF LEI L1/L2
  - who is who / who owns whom
  - 법인 정규화 및 부모-자회사 관계에 활용

### 3. 상장사/식별자
- SEC company_tickers
  - 미국 상장사 ticker/CIK bootstrap
- DART + KRX
  - 한국 상장사 bootstrap
- OpenFIGI
  - 식별자 매핑 보강

### 4. 개방형 지식 그래프
- Wikidata
  - 거래소, 티커, 제품, 인물, 자회사, 국가 연결 힌트
- OpenCorporates
  - 글로벌 법인 탐색 힌트

## Commonsense Ontology가 필요한 이유
공개 금융 온톨로지만으로는 이런 추론이 약합니다.

- 광산붐 -> 채굴 활동 증가 -> 작업복 수요 증가 -> 청바지 회사 수혜
- AI 데이터센터 붐 -> 전력/냉각 설비 수요 증가 -> 인프라 장비 회사 수혜
- 방위산업 확대 -> 특수소재/부품/정비 체인 수혜

즉 시장이 바로 가격에 반영하는 1차 종목 외에,
간접 수혜/간접 피해를 탐색하려면 `개념 노드`가 필요합니다.

## 권장 엔티티 타입
- `company`
- `security`
- `legal_entity`
- `sector`
- `industry`
- `product`
- `material`
- `activity`
- `macro_event`
- `theme`
- `regulation`

## 권장 relation 타입

### 기업/법인 관계
- `supplies_to`
- `customer_of`
- `competes_with`
- `partners_with`
- `invests_in`
- `owned_by`

### 기업-개념 관계
- `produces`
- `sells`
- `uses`
- `requires`
- `benefits_from`
- `exposed_to`
- `affected_by`

### 개념-개념 관계
- `drives_demand_for`
- `enables`
- `substitutes_for`
- `constrains`

## 추론 원칙

### 허용
- 1-hop: 직접 연결 기업 탐색
- 2-hop: 일반 공급망/경쟁 확장
- 선택적 3-hop: curated commonsense seed에 한해 상식적 수요 전이 탐색
- 양방향 탐색:
  - `company -> sells -> product`
  - `macro_event -> drives_demand_for -> product`
  - product 노드를 통해 두 방향 연결

### 금지
- 4-hop 이상 자동 확장
- 확신 없는 관계를 대량 자동 축적
- 온톨로지 경로만으로 투자 결론 확정

## 현재 구현
- `src/ontology/store.py`
  - 양방향 neighbor 조회
  - `discover_hidden_candidates()` 추가
- `src/ontology/planner.py`
  - 2-hop hidden candidate 탐색
  - hidden candidate 기반 web query 생성
- `src/ontology_bootstrap.py`
  - `--commonsense-json` 지원
- `commonsense_ontology.example.json`
  - 광산붐/리바이스, 데이터센터 붐/Vertiv 예시

## 운영 방식

### Stage 1. 정규화
- 공개 데이터(FIBO/GLEIF/SEC/FIGI/DART)로 기업과 식별자 정리

### Stage 2. Commonsense Seed
- 직접 curated JSON으로 개념 노드와 핵심 relation 추가
- 예: 금/구리/전력/냉각/작업복/반도체 장비/정비 등

### Stage 3. 탐색
- 사용자 질의 -> linked entities
- linked entities -> 2-hop hidden candidates
- hidden candidates -> web verification queue

### Stage 4. 판단
- 검증 통과 후보만 Fact-Sheet / Debate 입력으로 승격
- LLM 오케스트레이션이 직접 수혜/간접 수혜/과대해석을 구분

## 품질 관리 규칙
- hidden candidate는 반드시 path 설명을 남긴다.
- hidden candidate는 evidence 검증 전까지 추천 종목으로 승격하지 않는다.
- relation confidence가 낮으면 web query는 생성하되 ticker priority는 낮춘다.
- 동일 경로가 반복되면 path_score가 높은 하나만 유지한다.

## 예시

### Gold Rush
- `Gold Rush`
- `drives_demand_for`
- `Mining Activity`
- `requires`
- `Durable Workwear`
- `Levi Strauss`
- `sells`
- `Durable Workwear`

가능한 hidden candidate:
- `LEVI`

### AI Data Center Boom
- `Data Center Boom`
- `drives_demand_for`
- `Power and Cooling Infrastructure`
- `Vertiv`
- `sells`
- `Power and Cooling Infrastructure`

가능한 hidden candidate:
- `VRT`

## 다음 확장
1. `owned_by`/`parent_of`를 GLEIF L2와 연결
2. `ticker -> product -> macro_event` 역추론 강화
3. hidden candidate 평가 스코어에 웹 evidence 품질 반영
4. 잘 맞았던 경로/틀렸던 경로를 사후 성과로 학습
