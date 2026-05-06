# Windows Desktop Automation Guide

## 목적
이 문서는 맥북에서 개발한 코드를 GitHub로 옮긴 뒤, 윈도우 데스크탑에서 자동 수집/시그널/토론/리플레이를 안정적으로 돌리기 위한 운영 절차입니다.

이 프로젝트의 윈도우 자동화는 Windows Task Scheduler를 기준으로 합니다. 봇의 디스코드 프로세스는 선택적으로 로그온 시 실행하고, 뉴스/시그널/토론/요약/헬스체크는 주기 작업으로 실행합니다.

## 윈도우 데스크탑 준비물
- Git for Windows
- Python 3.11 이상
- PowerShell 5 이상 또는 PowerShell 7
- 로컬 모델 런타임: Ollama 또는 LM Studio
- GitHub 저장소 접근 권한
- Discord/OpenAI/Anthropic/Gemini/NYT API 키

## GitHub로 옮길 때 주의
절대 커밋하지 말아야 할 파일은 이미 `.gitignore`에 들어가 있습니다.

- `.env`
- `data/`
- `news_archive/`
- `logs/`
- `src/data_fetcher/cookies.local.json`
- `.venv/`

윈도우 데스크탑에서 새로 생성해야 할 로컬 상태는 `.env`, `data/`, `news_archive/`, `logs/`입니다. 기존 맥북 DB를 이어 쓰고 싶을 때만 `data/investment_bot.db`를 별도로 복사하세요.

## 최초 설치 절차
윈도우 PowerShell에서 실행합니다.

```powershell
cd C:\projects
git clone https://github.com/<YOUR_ACCOUNT>/<YOUR_REPO>.git stock_group_chat_bot
cd stock_group_chat_bot
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\windows\bootstrap.ps1 -RunTests
```

Playwright 기반 브라우저 자동화까지 쓸 계획이면 다음처럼 실행합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\windows\bootstrap.ps1 -InstallPlaywright -RunTests
```

`bootstrap.ps1`은 `.venv`를 만들고, `requirements.txt`를 설치하고, `.env.example`에서 `.env`를 생성하고, `logs/data/news_archive` 디렉터리를 만듭니다.

## `.env` 설정
`bootstrap.ps1` 실행 후 `.env`를 직접 수정합니다.

필수에 가까운 값:

```env
DISCORD_TOKEN=
DISCORD_DEBATE_WEBHOOK_URL=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
NYT_API_KEY=
PORTFOLIO_FILE_PATH=data/my_portfolio.md
SEC_USER_AGENT=stock_group_chat_bot/0.1 your_email@example.com
DART_API_KEY=
```

`SEC_USER_AGENT`는 SEC EDGAR 공식 API 접근용 식별자입니다. 실제 이메일/연락 가능한 문자열로 바꾸세요.
한국 종목(`005930.KS`, `005930` 등)의 공식 공시/재무 수집을 쓰려면 OpenDART에서 발급받은 `DART_API_KEY`를 설정하세요.
`DISCORD_DEBATE_WEBHOOK_URL`을 설정하면 Windows 자동 토론 배치(`run_debates.bat`)가 진행 단계와 모델 발언을 지정 채널에 실시간으로 전송합니다. 비워두면 로그 파일과 DB에만 남습니다.

Ollama를 쓸 경우:

```env
LOCAL_MODEL_BACKEND=ollama
LOCAL_MODEL_NAME=<ollama_model_name>
LOCAL_OLLAMA_URL=http://localhost:11434/api/chat
LOCAL_TIMEOUT_SEC=600
```

LM Studio를 쓸 경우:

```env
LOCAL_MODEL_BACKEND=openai_compatible
LOCAL_MODEL_NAME=<lm_studio_loaded_model>
LOCAL_OPENAI_BASE_URL=http://127.0.0.1:1234/v1
LOCAL_API_KEY=lm-studio
LOCAL_TIMEOUT_SEC=600
```

초기 자동 실행은 보수적으로 시작하세요.

```env
SIGNAL_MIN_SCORE=65
DEBATE_FRONTIER_MODE=gated
DEBATE_AUTO_MIN_SCORE=85
DEBATE_REVIEW_MIN_SCORE=70
DEBATE_REQUIRE_VERIFIED=true
DEBATE_AUTO_COOLDOWN_MIN=120
DEBATE_JOB_MAX_ITEMS=1
MARKET_DATA_PROVIDER=yfinance
REVIEW_TRIGGER_MIN_SCORE=75
RISK_DEFAULT_POSITION_PCT=0.01
RISK_MAX_TICKER_EXPOSURE_PCT=0.05
RISK_MAX_GROSS_EXPOSURE_PCT=0.10
RISK_MAX_OPEN_POSITIONS=4
```

`gated` 모드에서는 조건이 약한 이벤트도 큐에는 보이지만 `cost_gate_status=review_required`이면 `run_debates.bat`가 자동 소비하지 않습니다. Discord에서 `!토론승인 EVENT_ID`를 실행하면 다음 배치에서 처리됩니다.

## 수동 검증 순서
작업 스케줄러에 등록하기 전에 각각 한 번씩 직접 실행합니다.

```powershell
.\run_local_healthcheck.bat
.\run_news.bat
.\run_news_context.bat
.\run_signals.bat
.\run_debates.bat
.\run_replay.bat
.\run_maintenance.bat
```

로그는 `logs/` 아래에 `job_yyyyMMdd_HHmmss.log` 형식으로 남습니다. 실패 원인은 먼저 최신 로그 파일에서 확인합니다.

`run_news_context.bat`은 디스코드/그룹챗을 시작하지 않고 DB에 쌓인 뉴스와 웹검증 근거만 독립 `News Context Pack`으로 정리합니다. 특정 쿼리를 강제로 점검하려면 PowerShell에서 다음처럼 실행합니다.

```powershell
$env:NEWS_CONTEXT_QUERIES="NVDA AI 서버 공급계약;TSLA 인도량 둔화 리스크"
.\run_news_context.bat
```

## 작업 스케줄러 등록
기본 작업만 등록합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\windows\install_scheduled_tasks.ps1
```

디스코드 봇까지 로그온 시 자동 실행하려면 다음처럼 실행합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\windows\install_scheduled_tasks.ps1 -IncludeBotOnLogon
```

리플레이를 매시간 자동으로 돌리려면 다음처럼 실행합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\windows\install_scheduled_tasks.ps1 -IncludeReplayHourly
```

실제 등록 전에 어떤 명령이 생성되는지만 보려면 `-WhatIfOnly`를 붙입니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\windows\install_scheduled_tasks.ps1 -WhatIfOnly
```

## 등록되는 작업
- `StockBot\NewsPoll`: 10분마다 뉴스 수집
- `StockBot\NewsContextPack`: 30분마다 최근 뉴스 이벤트를 독립 판단 패키지로 정리
- `StockBot\Signals`: 15분마다 시그널 생성
- `StockBot\Debates`: 15분마다 자동 토론 큐 소비
- `StockBot\NewsBackfill`: 매일 07:00 백필
- `StockBot\DailySummary`: 매일 23:30 일간 요약
- `StockBot\WeeklySummary`: 매주 일요일 23:40 주간 요약
- `StockBot\MonthlySummary`: 매월 1일 23:50 월간 요약
- `StockBot\LocalHealthcheck`: 매일 08:30 로컬 모델 점검
- `StockBot\Maintenance`: 매일 03:30 DB 단기 캐시/뉴스팩/시그널 로그 정리
- `StockBot\ReplayHourly`: 선택, 매시간 리플레이
- `StockBot\DiscordBotOnLogon`: 선택, 윈도우 로그온 시 디스코드 봇 실행

## 작업 제거
스케줄러 작업을 제거하려면 다음을 실행합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\windows\uninstall_scheduled_tasks.ps1
```

## 운영 원칙
초기에는 실거래를 연결하지 말고 paper/replay 중심으로 성능을 봅니다. 현재 기본 정책은 최종 투자 판단 100% 수동입니다. Windows 자동화는 뉴스 수집, 뉴스 Context Pack 정리, 시그널 생성, 토론 큐 소비, 요약, 리플레이 성과 측정까지만 맡기고, 실제 투자 여부와 규모는 디스코드 보고서와 근거를 보고 직접 결정합니다.

나중에 실거래를 검토하더라도 먼저 paper/replay에서 충분한 표본과 out-of-sample 성과가 쌓여야 합니다. 브로커 sandbox/live 연동은 별도 단계이며 이 가이드의 기본 자동화에는 포함하지 않습니다.

윈도우 데스크탑은 장시간 켜져 있는 실행 노드로 쓰고, 맥북은 코드 수정과 리뷰용으로 유지하세요. 변경은 GitHub를 통해 이동시키고, 윈도우에서는 `git pull` 후 `bootstrap.ps1 -RunTests`로 의존성/테스트를 다시 확인하는 흐름이 안정적입니다.

## 장애 점검 체크리스트
- 최신 로그: `logs/`
- 작업 상태: Windows Task Scheduler의 `StockBot` 폴더
- 로컬 모델 서버 상태: Ollama 또는 LM Studio
- `.env` 키 누락 여부
- `data/investment_bot.db` 잠금 또는 손상 여부
- `news_archive/`와 `data/` 쓰기 권한
- GitHub에서 받은 최신 코드 여부
