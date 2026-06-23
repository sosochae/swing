# Kavout Screener — 사용자 매뉴얼

> **대상 파일/폴더**: `scripts/fetch_kavout.py`, `scripts/run_kavout_screener.py`, `servers/kavout_mcp/server.py` 및 관련 core/shared 모듈

---

## 목차

1. [환경설정](#1-환경설정)
2. [실행방법 및 작업흐름](#2-실행방법-및-작업흐름)
3. [기능목록](#3-기능목록)
4. [프롬프트-기능 매핑](#4-프롬프트-기능-매핑)
5. [고급사용법 및 트러블슈팅](#5-고급사용법-및-트러블슈팅)

---

## 1. 환경설정

### 시스템 요구사항

- OS: Windows 10/11 (경로 하드코딩 포함)
- Python 3.12 이상
- RAM 최소 8GB 권장
- 실제 Chrome 설치 필요 (`channel="chrome"` Playwright 사용)
- 네트워크: kavout.com 및 OpenRouter API 접속 가능

### 패키지·의존성 설치

```powershell
cd C:\MCP\Swing
.venv\Scripts\pip install -e .
```

주요 패키지: `playwright`(Kavout 스크래핑), `playwright-stealth`(봇 감지 우회), `yfinance`(기술지표·펀더멘털), `numpy`(RSI·SMA 계산), `pydantic`(스키마 검증), `python-dotenv`(환경변수), `openai`(OpenRouter LLM), `certifi`(SSL), `mcp`(MCP 서버)

```powershell
.venv\Scripts\playwright install chromium
# 실제 Chrome 바이너리 필요 (channel="chrome") — 이미 설치되어 있어야 함
```

### 디렉터리 구조

```
C:\MCP\Swing\
├── scripts\
│   ├── run_kavout_screener.py     ← 메인 CLI
│   ├── fetch_kavout.py            ← Kavout CSV 수집 CLI
│   └── kavout_chrome_profile\    ← Chrome 로그인 세션 (자동 생성)
├── shared\
│   ├── cache\                     ← LLM 응답 캐시 (자동 생성)
│   ├── state\snapshots\           ← 스냅샷 (자동 생성)
│   └── logs\                      ← 로그 (자동 생성)
└── servers\kavout_mcp\
    └── server.py                  ← MCP 서버

Y:\내 드라이브\
├── 어닝\
│   ├── K어닝 分析.md              ← 어닝콜 분析 파일 (수동 관리)
│   └── K어닝 分析_today.md        ← 오늘 추가분 (선택, 수동 작성)
└── Data\
    └── kavout_YYYYMMDD.csv       ← fetch_kavout.py가 생성
```

### .env 파일 설정

`C:\MCP\Swing\.env`에 생성:

```dotenv
# ── LLM (OpenRouter) ──────────────────────────────────────────────────
OPENROUTER_API_KEY=여기에_입력

# ── 어닝 分析 LLM 모델 ───────────────────────────────────────────────
LLM_MODEL_KAVOUT_EARNINGS=deepseek/deepseek-v4-flash

# ── 데이터 경로 ───────────────────────────────────────────────────────
EARNINGS_DIR=Y:\내 드라이브\어닝
DATA_DIR=Y:\내 드라이브\Data

# ── Obsidian ─────────────────────────────────────────────────────────
OBSIDIAN_API_KEY=여기에_입력
OBSIDIAN_BASE_URL=https://127.0.0.1:27124
OBSIDIAN_VAULT=C:\lian

# ── Slack ─────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN=xoxb-여기에_입력
SLACK_CHANNEL_MAIN=#swing-trading

# ── 캐시 ─────────────────────────────────────────────────────────────
CACHE_TTL_HOURS=24
```

| 항목 | 설명 | 필수 여부 |
|------|------|---------|
| `OPENROUTER_API_KEY` | LLM 어닝 分析용 API 키 | 필수 |
| `EARNINGS_DIR` | `K어닝 分析.md` 폴더 경로 | 필수 |
| `DATA_DIR` | `kavout_*.csv` 저장/탐색 폴더 | 필수 |
| `OBSIDIAN_API_KEY` | Obsidian Local REST API 키 | 선택 (없으면 노트 저장 실패만) |
| `SLACK_BOT_TOKEN` | Slack Bot Token | 선택 (없으면 알림만 실패) |
| `LLM_MODEL_KAVOUT_EARNINGS` | 어닝 分析 LLM 모델 | 선택 (기본: `deepseek/deepseek-v4-flash`) |
| `CACHE_TTL_HOURS` | LLM 캐시 유효 시간(시간 단위) | 선택 (기본: 24) |

### API 키 발급

**OpenRouter**: [openrouter.ai](https://openrouter.ai) → Dashboard → Keys → Create Key

**Obsidian Local REST API**: Obsidian → 설정 → Community Plugins → Local REST API 설치/활성화 → 플러그인 설정에서 Key 복사

**Slack Bot Token**: [api.slack.com/apps](https://api.slack.com/apps) → Create App → OAuth & Permissions → `chat:write` 스코프 추가 → Install to Workspace → Bot User OAuth Token 복사

---

## 2. 실행방법 및 작업흐름

전체 실행 순서: **CSV 수집 → 어닝 파일 준비 → 스크리닝 실행**

```
[수동] Kavout.com 로그인 계정 준비
        │
        ▼
[CLI] fetch_kavout.py
  ├─ Chrome 브라우저 실행
  ├─ [수동] 첫 실행 시 Kavout 로그인 (이후 자동 세션 재사용)
  ├─ QMP 테이블 (최대 30행) + NTW 테이블 (최대 5행) 스크래핑
  ├─ 종목별 상세 페이지 (stock-analysis, technical) 수집
  └─ DATA_DIR/kavout_YYYYMMDD.csv 저장
        │
        ▼
[수동] K어닝 分析.md 작성/업데이트 (어닝콜 원문 요약 기록)
        │
        ▼
[CLI] run_kavout_screener.py  (또는 MCP: run_kavout_screen)
  │
  ├─ Step 1: kavout_*.csv 파싱 → KavoutRow 유니버스 구성
  │           Yahoo Finance API → StockDetail (RSI·RVOL·SMA·펀더멘털) 수집
  │
  ├─ Step 2: K어닝 分析.md → LLM 분류
  │           가이던스 방향 (up/flat/down), 경영진 톤 (bullish/neutral/bearish)
  │           캐시 재사용 (--refresh-earnings 로 강제 갱신)
  │
  └─ Step 3: 점수화 + 랭킹 → 터미널 보고서 + Obsidian 노트 + Slack 알림
```

### 2.1 초기설정: Kavout CSV 수집 (`fetch_kavout.py`)

```powershell
cd C:\MCP\Swing

# 기본 실행 (all-caps 유니버스)
.venv\Scripts\python scripts\fetch_kavout.py

# 유니버스 지정
.venv\Scripts\python scripts\fetch_kavout.py --universe sp500
.venv\Scripts\python scripts\fetch_kavout.py --universe large-cap
```

`--universe` 선택지: `all-caps`(기본), `sp500`, `large-cap`, `mid-cap`, `small-cap`, `russell1000`

> `fetch_kavout.py`는 캐시 없음. 실행마다 전체 스크래핑 수행. Chrome 세션(`kavout_chrome_profile/`)만 재사용.

생성 파일:
```
Y:\내 드라이브\Data\kavout_20260611.csv
C:\MCP\Swing\scripts\kavout_before.png
C:\MCP\Swing\scripts\kavout_after.png
```

### 2.2 기본사용: 스크리닝 실행 (`run_kavout_screener.py`)

```powershell
cd C:\MCP\Swing

# 기본 실행 (어닝 LLM 캐시 재사용)
.venv\Scripts\python scripts\run_kavout_screener.py

# 어닝 LLM 새로 分析 (캐시 무시)
.venv\Scripts\python scripts\run_kavout_screener.py --refresh-earnings

# 상위 N개 출력 변경
.venv\Scripts\python scripts\run_kavout_screener.py --top 20

# 조합
.venv\Scripts\python scripts\run_kavout_screener.py --refresh-earnings --top 15
```

생성 파일:
```
C:\lian\swing-procedure\screener\kavout\2026-06-11.md   ← Obsidian 노트
C:\MCP\Swing\shared\cache\*.json                        ← LLM 캐시
C:\MCP\Swing\shared\logs\audit_YYYY-MM-DD.json          ← 감사 로그
```

### 2.3 고급옵션: MCP 서버 (`run_kavout_screen`)

Claude Code(Claude Desktop / Roo Code)에서 도구 호출로 실행.

| 파라미터 | 타입/기본값 | 설명 |
|---------|-----------|------|
| `execution_id` | str / 자동생성 | 실행 ID (로그·노트 추적용) |
| `force_refresh` | bool / `false` | LLM 캐시 무시 (`--refresh-earnings`와 동일) |
| `top_n` | int / `10` | 보고서 상위 N개 |

> **주의**: MCP 버전은 상위 60개 티커만 Yahoo Finance API에 요청 (`ticker_list[:60]` 하드코딩). CLI 버전은 전체 티커 모두 요청.

MCP 서버 등록 (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "kavout-mcp": {
      "command": "C:\\MCP\\Swing\\.venv\\Scripts\\python",
      "args": ["C:\\MCP\\Swing\\servers\\kavout_mcp\\server.py"]
    }
  }
}
```

### 2.4 캐시 동작 요약

| 실행 방식 | 재사용되는 캐시 | 재생성되는 것 |
|----------|--------------|-------------|
| 기본 실행 (캐시 있음) | `shared/cache/screener_earnings_{ticker}.json` | Yahoo Finance API 호출 |
| `--refresh-earnings` | 없음 | 전 종목 LLM 재분析 + 캐시 갱신 |
| `--top N` | LLM 캐시 재사용 | 출력 행 수만 변경 (점수 계산 영향 없음) |
| `force_refresh=true` (MCP) | 없음 | `--refresh-earnings`와 동일 |

캐시 키: `screener_earnings_{TICKER}` (날짜 구분 없음, TTL=`CACHE_TTL_HOURS`)

### 2.5 로컬 데이터 파일 경로

**읽기 전용 (사용자 준비)**

| 경로 | 용도 |
|------|------|
| `{DATA_DIR}/kavout_*.csv` | Kavout 유니버스 (fetch_kavout.py 생성) |
| `{EARNINGS_DIR}/K어닝 分析.md` | 어닝콜 分析 원문 (수동 관리) |
| `{EARNINGS_DIR}/K어닝 分析_today.md` | 오늘 추가분 (선택) |

**자동 생성**

| 경로 | 생성 시점 |
|------|---------|
| `{DATA_DIR}/kavout_YYYYMMDD.csv` | `fetch_kavout.py` 실행 시 |
| `shared/cache/screener_earnings_{ticker}.json` | LLM 分析 완료 시 |
| `shared/logs/audit_YYYY-MM-DD.json` | 실행마다 |
| Obsidian: `swing-procedure/screener/kavout/YYYY-MM-DD.md` | Step 3 완료 시 |

---

## 3. 기능목록

### 3.1 Kavout CSV 수집 (`fetch_kavout.py`)

Playwright(Chrome stealth)로 kavout.com을 스크래핑해 QMP(최대 30종목) + NTW(최대 5종목) 유니버스를 수집하고 CSV로 저장. 소요시간: 10~30분.

**수집 흐름**:
```
Chrome 프로파일 로드 → kavout.com 접속 → 로그인 확인
→ NTW Show More 클릭 → QMP Show More 클릭 (30행 미만 시)
→ QMP·NTW 테이블 DOM 추출
→ 종목별 상세 페이지 수집
    ├─ /stock-analysis → 펀더멘털 지표 + 레이더 점수
    └─ /technical-analysis → MA/오실레이터 신호 + 게이지 점수
→ QMP: 시가총액 내림차순 정렬 + k_score 계산
→ DATA_DIR/kavout_YYYYMMDD.csv 저장
```

**k_score 계산**:
```python
k_score = 9.0 - (rank_1based - 1) * 8.0 / (total - 1)
# 1위 → 9.0, 최하위 → 1.0, NTW 종목 → 0.0
```

조건 분기: 로그인 타임아웃 5분 → RuntimeError. QMP 데이터 없음 → RuntimeError. 종목 상세: nasdaq → nyse → nysearca 순으로 시도, 3개소 모두 실패 시 해당 필드 공백.

---

### 3.2 Yahoo Finance 수집 (`core/api_fetcher.py`)

각 종목의 기술지표·펀더멘털을 실시간 수집. 소요시간: 1~3분.

| 추출 필드 | 소스 | 설명 |
|---------|------|------|
| `rsi14` | 1년 일봉 Close 시리즈 | Wilder RSI 직접 계산 |
| `rel_volume` | 오늘 거래량 / 직전 20일 평균 | 상대 거래량 |
| `sma20/50/200_pct` | (price - SMA) / SMA × 100 | SMA 대비 % |
| `w52_high/low_pct` | (price - 52주고/저) / 기준 × 100 | 52주 위치 |
| `forward_pe`, `peg`, `beta` | `info["forwardPE/pegRatio/beta"]` | 밸류에이션 |
| `target_price`, `recom` | `info["targetMeanPrice/recommendationMean"]` | 애널리스트 |
| `op_margin_pct`, `profit_margin_pct` | `info["operatingMargins/profitMargins"] × 100` | 마진 |
| `revenue_growth_yoy` | `info["revenueGrowth"] × 100` | 매출 YoY |
| `eps_surprise_pct` | `info["earningsSurprisePercent"]` | EPS 서프라이즈 |
| `market_cap`, `price` | `info["marketCap"]`, `history["Close"].iloc[-1]` | 시총·현재가 |

폴백: API 응답 없음/타임아웃 → 해당 필드 `None`. 가격은 Kavout CSV `price`로 보완.

---

### 3.3 LLM 어닝 分析 (`core/earnings_analyzer.py`)

`K어닝 分析.md`를 파싱해 OpenRouter LLM으로 가이던스 방향·경영진 톤을 분류. 소요시간: 종목당 30~90초, 캐시 재사용 시 무시.

기본 모델: `deepseek/deepseek-v4-flash`. 폴백 체인: PRIMARY → FALLBACK → FALLBACK_2 → `anthropic/claude-haiku-4-5`.

LLM 응답 필드:

| 필드 | 타입 | 의미 |
|------|------|------|
| `guidance_direction` | `"up"/"flat"/"down"/"unknown"` | 가이던스 방향 |
| `guidance_evidence` | str (≤120자) | 판단 근거 원문 |
| `mgmt_tone` | `"bullish"/"neutral"/"bearish"` | 경영진 톤 |
| `tone_evidence` | str (≤120자) | 톤 판단 근거 원문 |
| `key_risks` | list[str] | 주요 리스크 |
| `catalyst_strength` | int (1~5) | 카탈리스트 강도 |

폴백: LLM 호출 실패 시 키워드 기반 간이 분류 (`_fallback_from_text()`).

---

### 3.4 점수화 알고리즘 (`core/fundamental_screener.py`)

소요시간: < 5초.

```
Momentum Score (0~100)
├─ RSI(14) 구간 (20%)     → 50~70: 100점 | 40~50: 65점 | >70~80: 55점 | <40: 30점 | >80: 20점
├─ Relative Volume (20%) → ≥2.0: 100점 | ≥1.5: 70점 | ≥1.2: 45점 | <1.2: 20점
├─ 52주 위치 (20%)        → 고점 5% 이내: 100점 | 저점 100%+ 상승: 100점 (평균)
├─ SMA20/50/200 (20%)    → SMA200:40% + SMA50:35% + SMA20:25% 가중평균
└─ 멀티 기간 수익률 (20%) → 12M×40% + 6M×35% + 3M×25%
   ※ krow 없으면: RSI·RVOL·52W·SMA 각 25%

Fundamental Score (0~100) — 노트 표시용, 순위 계산 제외
├─ 매출 YoY 성장률 (40%)  → ≥50%: 100점 | ≥25%: 80점 | ≥10%: 60점
├─ EPS 서프라이즈 (25%)   → ≥15%: 100점 | ≥5%: 80점 | 없음: 50점(중립)
└─ 영업이익률 (35%)        → ≥25%: 100점 | ≥15%: 80점 | ≥8%: 60점

Catalyst Score (0~100) — 어닝콜 데이터 있는 경우만
├─ 가이던스 방향 (60%)    → up: 100점 | flat: 50점 | down: 10점
└─ 경영진 톤 (40%)         → bullish: 100점 | neutral: 55점 | bearish: 15점

Kavout AI Score — stock_rank_score (0~100) 그대로 사용

Total Score
├─ [Catalyst 있음] = Momentum×0.50 + Catalyst×0.35 + Kavout×0.15
└─ [Catalyst 없음] = Momentum×0.85 + Kavout×0.15
```

티어 분류 (시가총액 우선순위: Yahoo Finance → Kavout CSV):
- 대형주: $50B 이상
- 중형주: $5B ~ $50B
- 소형주: $5B 미만 또는 시총 미확인

---

### 3.5 Obsidian 저장 (`core/obsidian.py`)

Step 3 완료 후 스크리닝 결과를 Obsidian 노트로 자동 저장. 소요시간: < 5초.

저장 경로: `swing-procedure/screener/kavout/YYYY-MM-DD.md`

---

### 3.6 Slack 알림 (`core/slack.py`)

Step 3 완료 후 티어별 Top 3 요약 메시지를 전송. 소요시간: < 5초.

---

### 3.7 보고서 독해 가이드

#### 터미널 보고서 컬럼

| 컬럼명 | 내용 | 해석 |
|--------|------|------|
| `K` | Kavout QMP 점수 | 0~9 (9=시총 1위). `None`=NTW 전용 |
| `SR` | Kavout AI Stock Rank | 0~100 AI 종합 평가 |
| `시총` | 시가총액 | T=조, B=십억, M=백만 달러 |
| `M` | 모멘텀 점수 | 0~100 (RSI·RVOL·52W·SMA·수익률) |
| `F` | 펀더멘털 점수 | 0~100 (노트 표시용, 순위 영향 없음) |
| `C` | 카탈리스트 점수 | 0~100 (어닝콜 없으면 0) |
| `RSI` | RSI(14) | 50~70: 이상 | >80: 과매수 |
| `가이던스` | 가이던스 방향 | `↑상향` / `→유지` / `↓하향` |
| `톤` | 경영진 톤 | `🟢강세` / `🟡중립` / `🔴약세` |

#### Obsidian 노트 섹션

각 종목의 `### N. TICKER — 점수` 블록 내:

| 섹션 | 내용 |
|------|------|
| **📌 비즈니스 모델** / **🏭 인더스트리** / **🔀 전략·변화** / **💬 경영진 톤 근거** | K어닝 分析.md 원문 |
| **🔍 LLM 판단 근거** | `📈 가이던스 [방향]: 인용문` / `🗣️ 경영진 톤 [톤]: 인용문` |
| **⚠️ 주요 리스크** | LLM 추출 리스크 목록 |
| **📊 기술적 스냅샷** | 가격·등락·RSI·RVOL·SMA 테이블 |
| **💰 밸류에이션 & 애널리스트** | Fwd PE·PEG·Beta·목표가·추천 테이블 |
| **📈 펀더멘털** | 영업이익률·순이익률·매출성장·EPS서프 테이블 |
| **🤖 Kavout AI 점수** | Stock Rank·Quality·Growth·Momentum·Value 테이블 |
| **📡 Kavout 기술 分析** | MA Score·Oscillator Score·Technical Rating + 신호 테이블 |
| **📋 Kavout 펀더멘털 상세** | ROA·ROIC·D/E·EV/EBITDA 등 테이블 |
| **🏆 점수** | 모멘텀 N \| 펀더멘털 N \| 카탈리스트 N \| 합계 N |

수치 해석 기준:

| 지표 | 정상 범위 |
|------|---------|
| RSI(14) | 50~70: 이상적, >80: 과매수 경고 |
| RVOL | ≥1.5: 양호, ≥2.0: 강함 |
| SMA20/50/200 % | 양수: SMA 위 (추세 강함) |
| k_score | ≥7: 상위, <3: 하위 |
| Stock Rank | ≥70: 우수, <30: 약세 |
| MA/Oscillator/Technical Score | ≥70: 강한 신호 |

---

### 3.8 보고서 데이터 소스 역추적 맵

| 보고서 항목 | 소스 종류 | 소스 위치 / 필드 |
|------------|---------|----------------|
| 종목 목록, k_score, Stock Rank | 로컬 CSV | `kavout_*.csv` — `symbol`, `k_score`, `stock_rank_score` |
| 시가총액, 현재가 | API 우선 → CSV 폴백 | Yahoo Finance `info["marketCap"]` / `history["Close"]` |
| RSI(14), RVOL, SMA%, 52주 위치 | API 계산 | Yahoo Finance 1년 일봉 |
| 등락, Forward PE, PEG, Beta, 목표주가, Recom | API | Yahoo Finance `info[*]` |
| 영업/순이익률, 매출성장, EPS서프 | API | Yahoo Finance `info[*]` |
| 비즈니스 모델, 인더스트리, 전략·변화, 경영진 톤 원문 | 로컬 MD | `K어닝 分析.md` 섹션 1~4 |
| 가이던스 방향·근거, 경영진 톤·근거, 리스크 | LLM | OpenRouter → `guidance_direction`, `mgmt_tone`, `key_risks` 등 |
| Kavout AI 점수 (Quality/Growth/Momentum/Value) | 로컬 CSV | `kavout_*.csv` — `quality_score`, `growth_score` 등 |
| MA/Oscillator/Technical 점수, 이평선·오실레이터 신호 | 로컬 CSV | `kavout_*.csv` — `ma_score_num`, `ema10`, `rsi` 등 |
| ROA, ROIC, D/E, Current Ratio 등 | 로컬 CSV | `kavout_*.csv` — `roa`, `roic`, `debt_equity` 등 |
| 수익률 (1W~12M) | 로컬 CSV | `kavout_*.csv` — `return_1w`, `return_1m` 등 |
| 모멘텀/펀더멘털/카탈리스트/합계 점수 | 계산 | `core/fundamental_screener.py` |

> ⚠️ 애널리스트 B/H/S 집계: 소스 불명확 — 코드 직접 확인 필요.

---

## 4. 프롬프트-기능 매핑

Claude Code에서 `run_kavout_screen` / `kavout_health_check` 도구를 호출했을 때의 내부 흐름:

```
Claude Code (stdio JSON-RPC)
        │
        ▼
servers/kavout_mcp/server.py
        │
        ├─ run_kavout_screen
        │   ├─ parse_kavout_universe(DATA_DIR)     → list[KavoutRow]
        │   ├─ fetch_stock_data_bulk(tickers[:60]) → dict[str, StockDetail]
        │   ├─ analyze_earnings(K어닝 分析.md)      → dict[str, EarningsCallAnalysis]
        │   ├─ rank_universe(...)                   → list[FundamentalScoreResult]
        │   ├─ obsidian.write_note(...)
        │   └─ slack._send(...)
        │
        └─ kavout_health_check
            ├─ find_latest_kavout_csv(DATA_DIR)
            ├─ obsidian.ping()
            └─ 환경변수 확인
```

데이터 흐름 단계별 형태:

| 단계 | 데이터 형태 |
|------|----------|
| 유니버스 파싱 | `list[KavoutRow]` |
| API 수집 | `dict[str, StockDetail]` ← Yahoo Finance |
| LLM 分析 | `dict[str, EarningsCallAnalysis]` ← OpenRouter |
| 점수화 결과 | `list[FundamentalScoreResult]` |
| Obsidian 저장 | HTTP POST → Local REST API |
| Slack 알림 | HTTP POST → Slack API |
| MCP → Claude | `list[TextContent]` 텍스트 요약 |

---

## 5. 고급사용법 및 트러블슈팅

### 5.1 커스터마이징

**LLM 모델 변경** — `.env`:
```dotenv
LLM_MODEL_KAVOUT_EARNINGS=anthropic/claude-haiku-4-5
```

**점수 가중치 조정** — `shared/strategy.py`:
```python
FSCORE_WEIGHT_MOMENTUM = 0.45
FSCORE_WEIGHT_CATALYST = 0.30
FSCORE_WEIGHT_KAVOUT   = 0.25   # 합: 1.00
```
> ⚠️ Catalyst 있음/없음 각각 세 값의 합이 반드시 1.00이어야 함.

**대형주/중형주 경계 변경** — `shared/strategy.py` (약 552행):
```python
MCAP_LARGE_CAP = 20_000_000_000   # 기본: $50B
MCAP_MID_CAP   =  2_000_000_000   # 기본: $5B
```

**RSI 이상 구간 변경** — `shared/strategy.py` (약 541행):
```python
FSCORE_RSI_IDEAL_MIN = 60.0   # 기본: 50.0
FSCORE_RSI_IDEAL_MAX = 75.0   # 기본: 70.0
```

**가이던스 점수 기준 변경** — `core/fundamental_screener.py` (약 314행):
```python
_GUIDANCE_SCORE = {"up": 100.0, "flat": 30.0, "down": 10.0, "unknown": 40.0}
```

**MCP 서버 티커 수 제한 변경** — `servers/kavout_mcp/server.py` (224행):
```python
ticker_list = sorted(kavout_tickers)   # 기본: [:60]
# ⚠️ API 과부하 및 속도 저하 위험
```

커스터마이징 가능 전체 변수 목록은 [환경설정 섹션](#1-환경설정)의 `.env` 항목 및 `shared/strategy.py`를 참조.

---

### 5.2 캐시 관리

```powershell
# 특정 종목 캐시 삭제
Remove-Item "C:\MCP\Swing\shared\cache\screener_earnings_AAPL.json"

# 전체 캐시 초기화
Remove-Item "C:\MCP\Swing\shared\cache\screener_earnings_*.json"

# Kavout 로그인 세션 초기화 (재로그인 필요)
Remove-Item -Recurse -Force "C:\MCP\Swing\scripts\kavout_chrome_profile"
.venv\Scripts\python scripts\fetch_kavout.py
# 브라우저가 열리면 Kavout 로그인 후 대기
```

---

### 5.3 오류 해결

| 오류 메시지 | 원인 | 해결 방법 |
|------------|------|---------|
| `FATAL — kavout_*.csv 파일 없음` | `DATA_DIR`에 CSV 없음 | `fetch_kavout.py` 먼저 실행 |
| `로그인 타임아웃 (5분). 재실행하세요.` | 로그인 대기 초과 | 실행 후 5분 내 브라우저에서 로그인 |
| `Kavout QMP 데이터 없음 — 로그인 필요 또는 페이지 구조 변경` | 로그인 안 됨 또는 DOM 변경 | Chrome 세션 삭제 후 재로그인 |
| `Step3 실패 (점수화)` | 유니버스 데이터 불량 | CSV 파일 내용 확인 |
| `K어닝 分析.md 없음 — 카탈리스트 점수 제외` | `EARNINGS_DIR` 경로 오류 | `.env`의 `EARNINGS_DIR` 확인 |
| `Obsidian 저장 실패` | Obsidian 실행 안 됨 또는 API 키 오류 | Obsidian 앱 실행 + API 키 확인 |
| `Slack 전송 실패` | 토큰 만료 또는 채널 없음 | `SLACK_BOT_TOKEN` 재확인 |
| `UnicodeEncodeError` (SSL CA) | 경로에 한글 포함 | 코드 첫 블록이 자동 처리 (cacert.pem 복사) |

**디버깅 체크리스트**:
```
□ DATA_DIR에 오늘 날짜 kavout_*.csv 존재 확인
□ CSV 내 symbol 컬럼에 실제 티커 데이터 있는지 확인
□ EARNINGS_DIR\K어닝 分析.md 파일 존재 확인
□ .env 파일 존재 및 OPENROUTER_API_KEY 입력 확인
□ 캐시 파일 (shared/cache/) 오래된 경우 삭제 후 재실행
□ Obsidian 앱이 실행 중인지 확인 (Local REST API 플러그인 활성화)
□ scripts/kavout_after.png 확인 → 실제 데이터가 보이는지 시각 확인
```

---

### 5.4 전체 파일·폴더 구조

```
C:\MCP\Swing\
│
├── scripts\
│   ├── run_kavout_screener.py       # 메인 스크리닝 CLI
│   ├── fetch_kavout.py              # Kavout CSV 수집 CLI
│   └── kavout_chrome_profile\       # Chrome 세션 (자동 관리)
│
├── servers\
│   └── kavout_mcp\
│       └── server.py                # MCP 서버 (run_kavout_screen, kavout_health_check)
│
├── core\
│   ├── api_fetcher.py               # Yahoo Finance API + 기술지표 계산
│   ├── earnings_analyzer.py         # 어닝 MD → LLM → EarningsCallAnalysis
│   ├── fundamental_screener.py      # 점수화 + 랭킹
│   ├── obsidian.py                  # Obsidian Local REST API 클라이언트
│   ├── slack.py                     # Slack API 클라이언트
│   ├── llm.py                       # OpenRouter LLM + 캐시
│   ├── parsers.py                   # CSV·MD·JSON 파서
│   ├── analysis.py                  # 분析 유틸리티
│   └── state.py                     # 상태 관리
│
├── shared\
│   ├── config.py                    # 환경변수 기반 설정 (get_config 싱글톤)
│   ├── schemas.py                   # Pydantic v2 스키마 (KavoutRow, StockDetail 등)
│   ├── strategy.py                  # 전략 파라미터 (점수 가중치, 임계값)
│   ├── logger.py                    # structlog 기반 로거
│   ├── prompts.py                   # LLM 시스템 프롬프트
│   ├── cache\                       # LLM 응답 캐시
│   ├── state\snapshots\             # 실행 스냅샷
│   └── logs\                        # 감사 로그
│
├── .env                             # API 키·경로 (gitignore 대상)
└── pyproject.toml                   # 패키지 의존성
```

---

*최종 생성: 2026-06-11*
