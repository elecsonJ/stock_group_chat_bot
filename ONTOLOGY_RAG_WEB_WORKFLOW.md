# Ontology + RAG + Web Workflow

## 추천 구성 (Best-Of-Breed)
다음 조합을 기본으로 권장합니다.

1. 구조 지식(온톨로지)
- FIBO (금융 개념 체계)
- GLEIF LEI (법인 식별/지배구조)
- FIGI 매핑 (증권 식별자 연결)
- SEC company_tickers / XBRL taxonomy (미국 상장사 정규화)
- DART corpCode + KRX 상장목록 (한국 상장사 정규화)
- Commonsense seed graph (간접 수혜/수요 전이/제품-활동 연결)

2. 시계열/가격 데이터
- yfinance (기본 가격/기술지표)
- 추후 보강: 거래소/발행사 1차 데이터

3. 뉴스/웹 근거
- NYT API(이벤트 레이더)
- Reuters RSS, SEC, Fed RSS(구조화 이벤트/공식소스 보강)
- DDG 검색 + 원문 발췌(근거 확보)
- 규제/공시는 1차 소스 URL 우선
- 로컬 누적 뉴스는 `News Context Pack`으로 품질 판정 후 토론/RAG에 전달

## 오케스트레이션 규칙
### 단계 1: Ontology First
1. 사용자 질의에서 엔티티 후보 추출
2. 별칭 링크(티커/법인명/한글명)
3. 관계 확장(공급망/경쟁/섹터)
4. 2~3-hop hidden candidate 탐색(제품/활동/매크로 이벤트 경유, curated commonsense seed 우선)
5. coverage 점수 계산

### 단계 2: RAG
1. 링크된 엔티티 기반으로 과거 토론/RAG 키워드 생성
2. 과거 논점 회수
3. 단, 최신 이슈는 RAG보다 최신 근거 우선

### 단계 3: News Context Pack
1. `news_events`에서 질의/티커/온톨로지 키워드와 맞는 최근 이벤트 회수
2. `news_articles`에서 이벤트별 기사와 source tier 확인
3. `research_evidences`에서 누적 웹검증 근거 회수
4. `strong/usable/weak/empty`, `web_required`, `limitations` 산출
5. 품질이 약하면 공식소스/Reuters/IR 중심 보완 검색어 생성

### 단계 4: Web Search
1. 온톨로지 확장 노드 기반으로 쿼리 생성
2. URL/도메인/원문 발췌 확보
3. Evidence 패키지 저장

### 단계 5: Debate/Judge
1. Fact-Sheet에 온톨로지 플랜 + 웹 근거 결합
2. News Context Pack의 `limitations`와 `web_required`를 토론 입력에 명시
3. 모델 토론
4. 판정은 근거/라벨 규칙 + 로컬 판사 보조

## 품질 게이트
1. 결론 전 최소 1회 SEARCH 근거 강제
2. 비정상 수치 sanity-check
3. 출처 없는 단정 금지
4. conflicting/unknown 라벨 허용
5. 뉴스팩 상태가 `weak/empty`이거나 `web_required=true`이면 최신 웹검증 비중을 높임

## 업데이트 주기
1. 온톨로지(식별자/법인): 주 1회
2. 가격/매크로: 일 1회 이상
3. 뉴스 수집: 10분 폴링 + 일 1회 백필
4. News Context Pack: 30분 주기 또는 토론/RAG 시 즉시 생성
5. 뉴스/검색 근거: 토론 시 실시간

## 부트스트랩 실행 예시
```bash
python src/ontology_bootstrap.py \
  --sec-json data_sources/sec/company_tickers.json \
  --dart-krx-csv data_sources/kr/dart_krx.csv \
  --lei-csv data_sources/lei/lei.csv \
  --figi-csv data_sources/figi/figi.csv
```

## 현재 구현 포인트
1. 온톨로지 저장소: `src/ontology/store.py`
2. 하이브리드 플래너: `src/ontology/planner.py`
3. 부트스트랩 로더: `src/ontology_bootstrap.py`
4. 관계 자동추출기: `src/ontology/relation_miner.py`
5. 토론 연동: `src/debate_manager.py`
6. 뉴스 Context Pack: `src/news_context_pack.py`, `src/news_context_job.py`
7. commonsense seed 예시: `commonsense_ontology.example.json`
8. 상세 설계: `PUBLIC_ONTOLOGY_AND_COMMONSENSE_PLAN.md`
