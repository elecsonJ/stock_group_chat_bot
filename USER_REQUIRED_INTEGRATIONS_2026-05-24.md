# 사용자가 직접 해야 하는 외부 연동과 결정 사항 - 2026-05-24

이 문서는 코드만으로는 대신 처리할 수 없는 일을 정리한 것입니다. 계정 생성, API 키 발급, 실거래 범위 결정, 위험 한도 설정처럼 사용자의 권한과 책임이 필요한 항목들입니다.

## 1. 실전용 주가 데이터 제공자 선택

현재 코드는 `yfinance` 기반 참조용 시세와, 나중에 실전용 시세 provider를 붙일 수 있는 adapter 구조까지 준비되어 있습니다. 하지만 실제 실전용 시세 feed는 아직 선택되어 있지 않습니다.

다음 중 하나를 선택해야 합니다.

- Alpaca market data
- Interactive Brokers
- 한국투자증권 API
- 다른 국내/해외 브로커 또는 유료 시세 vendor

실전용으로 최소한 필요한 데이터는 다음과 같습니다.

- 마지막 체결가
- bid/ask 호가
- 시세 timestamp
- 거래소/장 상태
- 가능하다면 거래정지, 상한가/하한가, limit state
- 실시간/지연 시세 여부

provider를 정하면 `.env`에 대략 이런 식으로 설정해야 합니다.

```env
MARKET_DATA_ADAPTER=<선택한_provider_이름>
REQUIRE_EXECUTION_GRADE_MARKET_DATA=true
```

## 2. 브로커와 첫 거래 범위 결정

처음부터 미국/한국/글로벌 전체를 섞어 실거래로 가는 것은 위험합니다. 첫 단계는 하나로 좁히는 것이 좋습니다.

선택지:

- 미국 주식/ETF만
- 한국 주식/ETF만
- 당분간 실거래 없이 paper-only 글로벌 watchlist

함께 결정해야 할 것:

- sandbox부터 할지, paper-only를 더 유지할지
- 첫 실전 단계에서 사용할 최대 자본
- 허용 주문 유형: market, limit, stop, bracket 등
- 정규장만 거래할지, 프리마켓/애프터마켓도 허용할지
- 공매도/숏 포지션을 허용할지

## 3. API 키와 계정 정보 입력

API 키와 계정 정보는 GitHub에 올리면 안 됩니다. 반드시 로컬 `.env` 또는 브로커가 제공하는 안전한 설정 방식에만 넣어야 합니다.

provider에 따라 이름은 달라질 수 있지만, 보통 이런 정보가 필요합니다.

```env
BROKER_API_KEY=
BROKER_API_SECRET=
BROKER_ACCOUNT_ID=
BROKER_BASE_URL=
MARKET_DATA_API_KEY=
MARKET_DATA_API_SECRET=
```

정확한 변수명은 선택한 브로커/API 문서에 맞춰 정해야 합니다.

## 4. 실전 위험 한도 결정

sandbox나 live 주문으로 넘어가기 전에 아래 값들을 직접 정해야 합니다.

```env
RISK_DEFAULT_POSITION_PCT=
RISK_MAX_TICKER_EXPOSURE_PCT=
RISK_MAX_GROSS_EXPOSURE_PCT=
RISK_MAX_OPEN_POSITIONS=
RISK_TICKER_COOLDOWN_MIN=
```

추가로 아직 사용자가 결정해야 하는 실전 운영 규칙:

- 하루 최대 손실 한도
- 최대 낙폭 발생 시 자동 중단 기준
- live 주문 unlock 확인 절차
- 거래 허용 종목 universe
- earnings, FOMC, CPI 같은 이벤트 전후 거래 제한 여부

## 5. Discord 운영자 ID 설정

승인, 거절, kill-switch 같은 위험 명령을 아무나 실행하지 못하게 해야 합니다.

`.env`에 본인의 Discord user ID를 넣으세요.

```env
DISCORD_OPERATOR_USER_IDS=123456789012345678,234567890123456789
```

여러 명이면 쉼표로 구분합니다.

## 6. 현재 코드로 이미 준비된 것

아래 항목은 코드 쪽에서 이미 처리되어 있습니다.

- 시장별 ticker 정규화
- `yfinance` 참조용 market data adapter
- 미래 실전용 execution-grade adapter 계약
- 거래소 캘린더 연동과 fallback
- market data connectivity 점검 job
- 실전용 시세가 필요할 때 reference feed를 경고/차단하는 readiness check
- 거래량 z-score, 시장/섹터 벤치마크 기반 market reaction scoring
- paper mode에서 execution-grade 시세를 요구하도록 잠글 수 있는 품질 gate

## 7. 다음으로 결정할 것

가장 먼저 아래 셋 중 하나를 정하면 됩니다.

1. 미국 주식만 대상으로 Alpaca 또는 IBKR부터 붙인다.
2. 한국 주식만 대상으로 국내 브로커 API부터 붙인다.
3. 아직 실거래는 하지 않고 paper/replay 데이터를 더 쌓는다.

현재 상태에서 가장 안전한 선택은 3번입니다. paper/replay 결과가 충분히 안정적으로 쌓인 뒤, 1번 또는 2번을 아주 작은 규모의 sandbox/live로 시작하는 것이 좋습니다.
