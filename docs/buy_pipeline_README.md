# SwingMCP 매수 파이프라인 사용 설명서

> **분석 원칙**: 코드에 존재하는 것만 기술한다. 추측·미구현 내용은 작성하지 않는다.
> 실행 환경 표기: `[CLI]` 터미널 직접 실행 / `[MCP]` AI 에이전트 호출 / `[자동]` 파이프라인 내부 자동 처리

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [시작 전 준비](#2-시작-전-준비)
3. [전체 워크플로우](#3-전체-워크플로우)
4. [단계별 실행 방법](#4-단계별-실행-방법)
5. [명령어 레퍼런스](#5-명령어-레퍼런스)
6. [캐시 & 스냅샷 구조](#6-캐시--스냅샷-구조)
7. [주요 데이터 파일](#7-주요-데이터-파일)
8. [기능 상세](#8-기능-상세)
9. [MCP 연결 흐름](#9-mcp-연결-흐름)
10. [설정 레퍼런스](#10-설정-레퍼런스)
11. [오류 대응 가이드](#11-오류-대응-가이드)
12. [수정 가이드](#12-수정-가이드)
13. [보고서 읽는 법](#13-보고서-읽는-법)
14. [데이터 소스 트레이싱 맵](#14-데이터-소스-트레이싱-맵)
15. [전체 파일 트리](#15-전체-파일-트리)

---

## 1. 시스템 개요

### 1.1 목적

매일 장 마감 후 또는 장 중에 실행하여:
- **매수 신호가 있는 종목**을 watchlist에서 선별
- **옵션(롱콜/롱풋) 진입 판단**을 자동화
- 분석 결과를 **Obsidian 노트**에 저장하고 **Slack으로 알림**

### 1.2 파이프라인 구성

```
scripts/run_buy_pipeline.py  ← [CLI] 진입점
        │
        ▼
orchestrator/pipelines.py    ← BuyPipeline (Step 0~13 루프)
        │
        ▼
orchestrator/steps/buy_steps.py  ← BuySteps (각 Step 구현)
        │
        ├─ core/analysis.py       ← 기술 분석 / 필터 / 시나리오
        ├─ core/api_fetcher.py    ← yfinance / Finnhub / 옵션 체인
        ├─ core/llm.py            ← OpenRouter LLM / 뉴스 검색
        ├─ core/obsidian.py       ← Obsidian REST API
        ├─ core/slack.py          ← Slack Bot
        └─ core/state.py          ← 스냅샷 / 감사 로그 / Requeue
```

### 1.3 14단계 파이프라인

| Step | 이름 | 역할 | 치명적 오류 시 |
|------|------|------|---------------|
| 0 | 환경 검증 | Obsidian 연결·디렉토리 확인 | **중단** |
| 1 | 데이터 수집 | Summary·Kavout·Watchlist 로드 | **중단** |
| 2 | 시장 레짐 판정 | favorable/borderline/unfavorable 결정 | **중단** |
| 3 | 종목 필터링 | 7개 필터(F1~F7) 적용 | 계속 진행 |
| 4 | 기술 분석 | yfinance·Finnhub 실시간 데이터 + 점수 산출 | 계속 진행 |
| 5 | 뉴스/리서치 | RSS·DuckDuckGo·LLM 감성 분석 | 계속 진행 |
| 6 | Devil's Advocate | 차감 요인(IV Crush·내부자·EPS 미스) 적용 | 계속 진행 |
| 7 | 옵션 유효성 검증 | 체인 갱신·Greeks·기간별 최적 옵션 선택 | 계속 진행 |
| 8 | 시나리오 계산 | 강세/기본/약세 3케이스 EV·R/R | 계속 진행 |
| 9 | 포트폴리오 노출 | 섹터 집중·자본 한도 점검 | 계속 진행 |
| 10 | 최종 순위 | balanced + aggressive 정렬, action 결정 | 계속 진행 |
| 11 | Requeue 등록 | F1/F3 탈락 종목 대기열 등록 | 계속 진행 |
| 12 | Obsidian 저장 | buy note·watchlist.md 생성 | 계속 진행 |
| 13 | Slack 알림 | 매수 결과 전송 | 계속 진행 |

**Graceful Degradation**: Step 0/1/2 외의 오류는 기록 후 다음 Step을 계속 실행한다.

---

## 2. 시작 전 준비

### 2.1 필수 환경 변수 (`.env`)

```dotenv
# OpenRouter (LLM)
OPENROUTER_API_KEY=sk-or-...

# Obsidian REST API
OBSIDIAN_API_KEY=...
OBSIDIAN_BASE_URL=https://127.0.0.1:27124
OBSIDIAN_VAULT=C:\lian

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_MAIN=#swing-trading
SLACK_CHANNEL_ALERT=#swing-alerts

# 데이터 경로 (기본값)
SUMMARY_DIR=R:\내 드라이브\마켓 수치
EARNINGS_DIR=Y:\내 드라이브\어닝
DATA_DIR=Y:\내 드라이브\Data
POSITIONS_FILE=C:\lian\positions.md
WATCHLIST_FILE=C:\lian\Swing\watchlist.md

# 자본 설정
TOTAL_CAPITAL=22222
MAX_PER_POSITION=1000
```

### 2.2 선택 환경 변수

```dotenv
FINNHUB_API_KEY=...          # 목표주가·내부자거래 실시간 갱신 (없으면 summary 값 유지)
BRAVE_API_KEY=...             # Brave Search 보완 (없으면 DDG만 사용)
LLM_MODEL_BUY_RESEARCH=anthropic/claude-haiku-4-5   # 뉴스 감성 분석 모델
LLM_MODEL_BUY_TECH_NARRATIVE=deepseek/deepseek-v4-flash  # 기술 내러티브 모델
```

### 2.3 필수 입력 파일

| 파일 | 경로 | 생성 주체 |
|------|------|-----------|
| Summary JSON | `SUMMARY_DIR/summary_*.json` | Google Apps Script (AppScript) |
| Kavout CSV | `DATA_DIR/kavout_*.csv` | `scripts/fetch_kavout.py` |
| K어닝 분석.md | `EARNINGS_DIR/K어닝 분석.md` | 수동 작성 |
| K어닝 분석_today.md | `EARNINGS_DIR/K어닝 분석_today.md` | 수동 작성 (선택) |

---

## 3. 전체 워크플로우

```
[사전 준비]
  ① AppScript → Summary JSON 생성 (R:\내 드라이브\마켓 수치\summary_YYYYMMDD.json)
  ② fetch_kavout.py → Kavout CSV 생성 (Y:\내 드라이브\Data\kavout_YYYYMMDD.csv)
  ③ K어닝 분석.md 최신화 (어닝콜 텍스트 + frontmatter)
        │
        ▼
[파이프라인 실행] [CLI] or [MCP]
  python scripts/run_buy_pipeline.py           ← 기본 (캐시 무시)
  python scripts/run_buy_pipeline.py --use-cache  ← 캐시 재사용
        │
        ▼
[Step 0~13 자동 실행]
  Step 0 → 환경 검증
  Step 1 → Summary + Kavout 로드 → Watchlist 구성
  Step 2 → 시장 레짐 판정 (favorable/borderline/unfavorable)
  Step 3 → 7개 필터 적용 (F1~F7)
  Step 4 → yfinance·Finnhub 실시간 데이터 수집 + 기술 점수 산출
  Step 5 → RSS·DDG·LLM 뉴스 감성 분석
  Step 6 → Devil's Advocate (차감 요인 적용)
  Step 7 → 옵션 체인 갱신 + 기간별 최적 옵션 선택
  Step 8 → 3케이스 시나리오 계산 (EV·R/R·손절/익절)
  Step 9 → 포트폴리오 노출 점검 (섹터·자본)
  Step 10 → 최종 순위 결정 (balanced + aggressive)
  Step 11 → Requeue 등록 (F1/F3 탈락 종목)
  Step 12 → Obsidian 저장 (buy note + watchlist.md)
  Step 13 → Slack 알림
        │
        ▼
[출력]
  터미널: 실행 요약 카드 (레짐·필터·기술점수·옵션·시나리오·최종 순위)
  Obsidian: C:\lian\swing-procedure\notes\buy\YYYY-MM-DD.md
  Slack: #swing-trading 채널 (진입/관찰/보류/탈락 요약)
  watchlist.md: C:\lian\Swing\watchlist.md (YAML 블록 추가)
```

---

## 4. 단계별 실행 방법

### 4.1 전체 파이프라인 실행

```bash
# [CLI] 캐시 무시 (기본값 — 새로 실행)
python scripts/run_buy_pipeline.py

# [CLI] LLM 캐시 재사용 (빠른 재실행)
python scripts/run_buy_pipeline.py --use-cache
```

### 4.2 MCP를 통한 실행

```json
// [MCP] run_buy_pipeline 호출
{
  "execution_id": "buy_20260611_120000",  // 생략 시 자동 생성
  "force_refresh": false,
  "start_step": 0,
  "target_tickers": ["AAPL", "NVDA"]     // 생략 시 전체 watchlist
}
```

```json
// [MCP] 자연어 명령
{
  "query": "AMD 매수 분석해줘"
}
```

### 4.3 특정 Step만 재실행 (MCP)

```json
// [MCP] step_execute — 특정 단계 수동 실행 (디버깅용)
{
  "pipeline_type": "buy",
  "step": 5,
  "execution_id": "buy_20260611_120000"
}
```

### 4.4 Requeue 관리

```json
// [MCP] Requeue 목록 조회
{ "status": "waiting" }  // waiting / ready / processed

// [MCP] Requeue 수동 등록
{
  "ticker": "NVDA",
  "failed_filters": ["F1_RVOL_LOW"],
  "threshold": { "rvol_min": 1.5 }
}
```

---

## 5. 명령어 레퍼런스

### 5.1 CLI

```
python scripts/run_buy_pipeline.py [옵션]

옵션:
  --use-cache    LLM 캐시 재사용 (이미 분석된 뉴스·기술내러티브 재활용)
  --no-cache     캐시 무시하고 새로 실행 (기본값)

두 옵션은 상호 배타적 (mutually exclusive group)
```

### 5.2 MCP Tools 전체 목록

| Tool | 설명 | 필수 파라미터 |
|------|------|--------------|
| `run_buy_pipeline` | 매수 파이프라인 실행 | 없음 |
| `run_sell_pipeline` | 매도 파이프라인 실행 | 없음 |
| `nl_query` | 자연어 명령 라우팅 | `query` |
| `requeue_add` | Requeue 등록 | `ticker`, `failed_filters`, `threshold` |
| `requeue_list` | Requeue 목록 조회 | 없음 |
| `partial_exit_apply` | 부분 청산 처리 | `ticker`, `contracts_to_close`, `exit_premium` |
| `position_status` | 포지션 현황 조회 | 없음 |
| `step_execute` | 단일 Step 수동 실행 | `pipeline_type`, `step`, `execution_id` |
| `health_check` | 시스템 헬스 체크 | 없음 |
| `cache_clear` | LLM 캐시 삭제 | 없음 (ticker 지정 시 개별 삭제) |

### 5.3 MCP 서버 실행

```bash
# [CLI] MCP 서버 직접 시작 (stdio — Claude Desktop / Roo Code에서 자동 연결)
python servers/swing_mcp/server.py
```

---

## 6. 캐시 & 스냅샷 구조

### 6.1 LLM 캐시

```
shared/cache/
├── {ticker}_{date}_research.json       # Step 5 뉴스 감성 분석 결과
│   캐시키: "{TICKER}_{YYYY-MM-DD}_research"
│   TTL: CACHE_TTL_HOURS (기본 24시간)
│
├── {ticker}_{date}_tech_narrative.json # Step 12 기술 내러티브 LLM 생성 결과
│   캐시키: "{TICKER}_{YYYY-MM-DD}_tech_narrative"
│   TTL: CACHE_TTL_HOURS
│
└── cacert.pem                          # SSL CA bundle (한글 경로 우회용 복사본)
```

### 6.2 스냅샷 (idempotency)

```
shared/state/snapshots/
└── {execution_id}/
    ├── step_0.json   # {"warnings": [...]}
    ├── step_1.json   # {"watchlist": [...]}
    ├── step_2.json   # {"regime_status": "favorable", "allowed_direction": "long_call", ...}
    ├── step_3.json   # {"all_tickers": [...], "filter_pass": [...], ...}
    ├── step_4.json   # {ticker: final_score, ...}
    ├── step_5.json   # {"researched": [...]}
    ├── step_6.json   # {"surviving": [...], "da_deductions": {...}}
    ├── step_7.json   # {"valid_tickers": [...], "all_tickers": [...]}
    ├── step_8.json   # {ticker: expected_value, ...}
    ├── step_9.json   # {"remaining_cash": ..., "warnings": [...]}
    ├── step_10.json  # {"balanced": [...], "aggressive": [...], "high_downside": [...]}
    ├── step_11.json  # {"requeue_count": N}
    ├── step_12.json  # {"note_path": "...", "watchlist_written": N}
    └── step_13.json  # {"slack_ts": "..."}
```

**idempotency 동작**: `start_step` 파라미터로 재시작 시, 이미 완료된 Step은 자동 스킵.

### 6.3 감사 로그

```
shared/state/audit_{execution_id}.jsonl
```

각 줄: `{"execution_id": ..., "step": N, "status": "completed|failed|degraded", "duration_ms": ..., "data": {...}}`

---

## 7. 주요 데이터 파일

### 7.1 Summary JSON (`summary_YYYYMMDD.json`)

```
R:\내 드라이브\마켓 수치\summary_YYYYMMDD.json
```

AppScript가 생성하는 파이프라인의 핵심 입력 파일.

**주요 섹션**:

| 섹션 | 내용 |
|------|------|
| `tickers` | 각 종목별 technical(RSI·ADX·MA·BB·MACD), sector, insider, earnings |
| `macro` | SPY DI+/DI-/ADX, VIX, SPY/QQQ/IWM 추세 지표 |
| `events` | 향후 이벤트 (실적 발표·경제지표·OPEX, days_until 포함) |
| `options` | 종목별 옵션 체인 (strike·delta·OI·bid/ask·iv·oi_change) |
| `risk_params` | 섹터별 위험 한도 |

### 7.2 Kavout CSV (`kavout_YYYYMMDD.csv`)

```
Y:\내 드라이브\Data\kavout_YYYYMMDD.csv
```

`fetch_kavout.py`가 Playwright로 kavout.com을 스크래핑하여 생성.

Step 1에서 `parse_kavout_universe(DATA_DIR)` → `ctx.kavout_data: dict[str, KavoutRow]`

**Step 4에서 사용하는 필드**:

| 필드 | 용도 |
|------|------|
| `k_score` | Step 10 정렬 타이브레이커 (1~9점, NTW=None) |
| `stock_rank_score` | Kavout AI 종합 점수 → signal_count/final_score 보정 |
| `quality_score` | 품질 신호 보정 |
| `roic` | 자본효율 신호 보정 |
| `return_12m` | 12개월 수익률 신호 보정 |

### 7.3 K어닝 분석.md

```
Y:\내 드라이브\어닝\K어닝 분석.md
Y:\내 드라이브\어닝\K어닝 분석_today.md
```

Step 5에서 종목별 `earnings_summary` 텍스트로 추출하여 LLM 감성 분석 입력에 포함.

**frontmatter 형식**:
```
---
ticker: AAPL
quarter: Q1 2026
date: 2026-04-30
---
## 비즈니스 모델
...
## 인더스트리
...
## 변화/전략
...
## 자신감 표현
...
```

### 7.4 watchlist.md (`C:\lian\Swing\watchlist.md`)

Step 12에서 자동 생성/추가. YAML 블록 형식으로 분석 결과 저장.

```yaml
ticker: AAPL
action: 진입
option_type: 롱콜
strike: 210
expiry: 2026-08-15
entry_date: 2026-06-11
entry_premium: 5.20
entry_stock_price: 207.50
original_contracts: 2
remaining_contracts: 2
trailing_stop: 0.0
entry_regime: favorable
entry_vix: 18.5
entry_rationale: |
  확신도 0.75 (high) | 신호수 5/7, 시나리오R/R 2.3 ...
thesis: |
  🐂 강력한 iPhone 사이클 + AI 서비스 성장
```

### 7.5 positions.md (`C:\lian\positions.md`)

현재 보유 포지션. 매도 파이프라인의 입력 소스.

### 7.6 로그 파일

```
shared/logs/{execution_id}.log
```

실행마다 생성되는 구조화 로그. `llm_call_success`, `step_N_done`, 오류 등을 JSON Lines 형식으로 기록.

---

## 8. 기능 상세

### 8.1 시장 레짐 판정 (Step 2)

`core/analysis.py` → `analyze_market_regime(summary)`

**결정론적 판정** (LLM 없음):

| 레짐 | 의미 | allowed_direction |
|------|------|-------------------|
| `favorable` | 매수 우호적 | `long_call` |
| `borderline` | 혼조·모호 | `both` 또는 `long_call` |
| `unfavorable` | 매수 비우호적 | `long_put` 또는 `none` |

**실시간 보강**:
- `fetch_macro_realtime()`: 실시간 매크로 지표 갱신
- `_calc_intraday_indicators("SPY")`: SPY 4H DI+/DI-/MACD Hist → 단기 신호 강화

**레짐이 unfavorable이어도 분석은 계속 진행** — 확신도 계산에서 낮은 점수로 반영됨.

### 8.2 종목 필터링 (Step 3)

`core/analysis.py` → `apply_filters(summary, earnings_tickers, target_tickers)`

| 코드 | 조건 | Hard Stop? |
|------|------|-----------|
| F1_RVOL_LOW | RVOL < 1.5 | 아니오 (Requeue 대상) |
| F2_OI_LOW | 총 OI < 500 | 아니오 |
| F3_LIQUIDITY_LOW | 주가 < $20 또는 시총 < $10B | 아니오 (Requeue 대상) |
| F4_DELISTING_RISK | 주가 < $5 | **예 — 즉시 탈락** |
| F5_EARNINGS_PROXIMITY | 실적 발표 5거래일 이내 | 아니오 |
| F6_SECTOR_CONCENTRATION | 동일 섹터 5개 초과 | 아니오 |
| F7_DUPLICATE | 중복 티커 | 아니오 |

F4(상장폐지 위험)만 Hard Stop. 나머지 필터는 기록만 하고 분석 계속.

**어닝 예정일 실시간 갱신**: Finnhub `fetch_earnings_calendar_bulk()` → 실패 시 summary.events 폴백.

### 8.3 기술 분석 점수 (Step 4)

`core/analysis.py` → `calculate_technical_score(ticker, direction, summary)`

**7개 신호** (signal_count 0~7):

| 신호 | 기준 |
|------|------|
| ADX 추세 강도 | ADX ≥ 20 + DI 방향 일치 |
| RSI 모멘텀 | 50~70 (long_call) 또는 30~50 (long_put) |
| MACD 크로스 | golden cross (long_call) 또는 death cross (long_put) |
| MA 정렬 | 5>20>50>200 (long_call) 또는 역순 |
| RVOL 자금 유입 | RVOL ≥ 1.5 |
| 옵션 흐름 | P/C ratio 또는 OI 비율 방향 일치 |
| 자금 흐름 확인 | RVOL·OBV·옵션·다크풀 중 2개 이상 |

**Kavout 시그널 보정** (post-loop):

| 조건 | signal_count | final_score |
|------|-------------|------------|
| stock_rank_score ≥ 상위 임계값 | +보너스 | +보너스 |
| stock_rank_score ≤ 하위 임계값 | -패널티 | -패널티 |
| quality_score ≥ 임계값 | +보너스 | +보너스 |
| roic ≥ 임계값 | +보너스 | +보너스 |
| return_12m ≥ 임계값 | +보너스 | +보너스 |

**개별 종목 주봉 방향 오버라이드 (Category B)**:
- 주봉 DI+ >> DI- AND 주봉 ADX ≥ 30 → `long_call` (매크로 레짐과 무관)
- 주봉 DI- >> DI+ AND 주봉 ADX ≥ 30 → `long_put` (매크로 레짐과 무관)

**실시간 데이터 소스 우선순위**:
1. yfinance (`fetch_stock_data_bulk()`) — RSI·RVOL·MA·BB·MACD·ADX
2. Finnhub 밸류에이션 — forward_pe·PEG (summary에서 오버라이드)
3. Finnhub 목표주가 (`fetch_finnhub_price_targets_bulk()`)
4. Finnhub 내부자 거래 (`fetch_finnhub_insider_bulk()`)

### 8.4 뉴스/리서치 감성 분석 (Step 5)

`core/llm.py` → `analyze_with_llm(template_name, template_vars, cache_key, force_refresh)`

**뉴스 수집 파이프라인** (종목별):

```
① 종목별 RSS  (rss_feeds.json tickers 섹션, 없으면 Yahoo Finance 자동 생성)
② DuckDuckGo 3개 쿼리 병렬 실행
   - "{ticker} stock market news analysis"
   - "{ticker} options unusual activity IV analysis"
   - "{ticker} earnings guidance sector outlook"
③ Brave Search (API 키 있을 때만)
④ 시장 전체 RSS (rss_feeds.json market 섹션, max 50개/피드)
   → 상위 20개 샘플 추가
⑤ 중복 제거 후 최대 50개 → LLM
```

**LLM 출력 필드** (JSON):

| 필드 | 설명 |
|------|------|
| `overall_sentiment` | BULLISH / BEARISH / MIXED |
| `confidence` | High / Medium / Low |
| `key_drivers` | 핵심 동인 목록 |
| `bull_thesis` | 강세 근거 |
| `bear_thesis` | 약세 근거 |
| `debate_verdict` | Bullish / Bearish / Neutral |
| `next_catalyst_days` | 다음 촉매까지 예상 일수 |

모델: `LLM_MODEL_BUY_RESEARCH` (기본 `anthropic/claude-haiku-4-5`)

### 8.5 Devil's Advocate 차감 (Step 6)

`orchestrator/steps/buy_steps.py` → `step_6_devils()`

| 차감 사유 | 점수 차감 | 조건 |
|-----------|----------|------|
| IV Crush 위험 | -15점 | 실적 5일 내 + implied_move > 10% |
| Thesis 반박 | -20점 | LLM 판결이 포지션 방향과 반대 |
| 내부자 순매도 (summary) | -10점 | 내부자 순매도 > $10M |
| API 내부자 매도 | -10점 | insider_trans_pct < -10% (summary 차감 없을 때) |
| 최근 EPS 미스 (summary) | -5점 | 최근 분기 EPS 서프라이즈 < -5% |
| API EPS 미스 | -5점 | eps_surprise_pct < -5% (summary 차감 없을 때) |

40점 미만 종목은 경고 기록만, **탈락 없음** — 모든 종목이 다음 Step으로 진행.

### 8.6 옵션 유효성 검증 (Step 7)

**ATM 옵션 선택 우선순위** (복합 스코어 기반):
1. OI ≥ 500 + DTE 범위 내 + bid > 0
2. OI ≥ 500 + DTE 범위 내
3. OI ≥ OI_WARNING + DTE 범위 내
4. DTE 범위 내 (OI 무관)
5. 전체 후보 (DTE·OI 무관)

**복합 스코어** (낮을수록 선택됨):
```
score = |delta - delta_target| - log(OI+1)×0.015 - log(volume+1)×0.008 + spread_pct×0.25
```

**기간별 옵션 분류** (`classify_investment_horizon()`):

| 기간 | DTE 범위 | Delta 목표 |
|------|---------|-----------|
| 단기 | 25~40일 | 0.625 |
| 중기 | 45~90일 | 0.50 |
| 장기 | 90~180일 | 0.40 |
| 초장기 | 180~365일 | 기준 제시만 |

**검증 기준** (OptionValidity):

| 조건 | 기준값 |
|------|--------|
| Delta 범위 | 0.42~0.57 (중기 기준) |
| IVR | ≤ 70% (50% 이상 경고) |
| OI | ≥ 500 |
| Spread | ≤ 5% |
| DTE | ≥ 21일 |

DTE < 21일이면 35DTE 이후 첫 번째 금요일로 만기 투영.
체인 데이터 없으면 RSI 기반 IV 추정 합성 옵션 생성 (폴백).

**OI 변화 신호**: summary에서 백업한 oi_change로 ATM ±10% 대역의 방향성 포지션 구축 여부 확인 → signal_count/final_score 보정.

### 8.7 시나리오 계산 (Step 8)

`core/analysis.py` → `calculate_scenario()`

**3케이스 시나리오**:

| 케이스 | 주가 변동 가정 | 확률 |
|--------|-------------|------|
| 강세 (bullish) | 목표주가 기준 or ADX·레짐 연동 | 동적 |
| 기본 (base) | ADX·시그널 수 연동 | 동적 |
| 약세 (bearish) | DI 방향·레짐 연동 | 동적 |

**핵심 출력값**:
- `expected_value`: 가중 평균 기댓값 (`$`)
- `total_investment`: 총 투자금
- `contracts`: 계약 수 (`cfg.budget_1st` 기준)
- `stop_loss_premium`: 손절 프리미엄
- `target_premium_1st`: 1차 익절 프리미엄

`cfg.budget_1st = TOTAL_CAPITAL × (1 - NEXT_TRADE_RESERVE_PCT) × ENTRY_1ST_PCT`
= $22,222 × 0.7 × 0.5 ≈ **$7,778**

### 8.8 최종 순위 (Step 10)

**두 가지 정렬 동시 산출**:

**① balanced** (기본 보고서 — `ctx.final_rankings`):
```
정렬 기준: 확신도↓ → 기댓값↓ → 손익비↓ → IVR↑ → K-Score↓
```

**② aggressive** (`ctx.final_rankings_aggressive`):
```
정렬 기준: 기댓값↓ → 손익비↓ → 확신도↓
```

**Action 결정**:

| Action | 확신도 기준 | 추가 조건 |
|--------|-----------|---------|
| 진입 | ≥ 0.70 | validity.is_valid + contracts > 0 |
| 관찰 | ≥ 0.50 | validity.is_valid + contracts > 0 |
| 보류 | ≥ 0.30 | — |
| 탈락 | < 0.30 또는 contracts = 0 | — |

**확신도 계산** (`calculate_confidence()`):

| 구성 | 가중치 |
|------|--------|
| 추세 신뢰도 (technical signals) | 40% |
| 뉴스 확신도 (LLM sentiment) | 20% |
| Thesis 신뢰도 (R/R + 레짐) | 30% |
| 실행 신뢰도 (option validity) | 10% |

**고위험 플래그**: bear case 손실 > 투자금 50% → `[⚠️일변동하락위험]` 태그.

### 8.9 Requeue (Step 11)

F1_RVOL_LOW 또는 F3_LIQUIDITY_LOW 탈락 종목만 등록:

```
shared/state/requeue.json
{
  "ticker": "NVDA",
  "status": "waiting",      // waiting → ready → processed
  "failed_filters": ["F1_RVOL_LOW"],
  "threshold": {"rvol_min": 1.5},
  "registered_at": "..."
}
```

`RequeuePipeline`: waiting → ready 전환 감지 시 BuyPipeline 재투입.

---

## 9. MCP 연결 흐름

### 9.1 MCP 서버 구조

```
servers/swing_mcp/server.py
    │
    ├── orchestrator/engine.py   ← PipelineEngine (싱글톤)
    │       │
    │       └── orchestrator/pipelines.py  ← BuyPipeline/SellPipeline/RequeuePipeline
    │
    └── stdio JSON-RPC (Roo Code / Claude Desktop 연결)
```

### 9.2 run_buy_pipeline 호출 흐름

```
Claude/Roo Code
    │ (MCP Tool 호출)
    ▼
servers/swing_mcp/server.py :: call_tool("run_buy_pipeline", args)
    │
    ▼
orchestrator/engine.py :: PipelineEngine.run_buy(ctx)
    │
    ▼
orchestrator/pipelines.py :: BuyPipeline.run(ctx)
    │   ├── Step 0: step_0_env(ctx)
    │   ├── Step 1: step_1_data(ctx)
    │   ...
    │   └── Step 13: step_13_notify(ctx)
    │
    ▼
JSON 응답 반환 (execution_id, status, completed_steps, failed_steps, ...)
```

### 9.3 MCP 서버 Claude Desktop 설정

```json
// claude_desktop_config.json
{
  "mcpServers": {
    "swing-mcp": {
      "command": "python",
      "args": ["C:/MCP/Swing/servers/swing_mcp/server.py"],
      "env": {}
    }
  }
}
```

### 9.4 자연어 라우팅 (`nl_query`)

```
nl_query("AMD 매수 분석해줘")
    │
    ▼
LLM (LLM_MODEL_NL_ROUTING = deepseek/deepseek-v4-flash:free)
    │ → {"pipeline": "buy", "target_tickers": ["AMD"]}
    ▼
run_buy_pipeline(target_tickers=["AMD"])
```

---

## 10. 설정 레퍼런스

### 10.1 핵심값 (자주 조정)

| 설정 | 위치 | 기본값 | 설명 |
|------|------|--------|------|
| `TOTAL_CAPITAL` | `.env` | `22222` | 총 투자 가능 자본 ($) |
| `MAX_PER_POSITION` | `.env` | `1000` | 포지션당 최대 투자금 ($) |
| `NEXT_TRADE_RESERVE_PCT` | `.env` | `0.30` | 다음 투자 유보 비율 |
| `ENTRY_1ST_PCT` | `.env` | `0.50` | 1차 진입 비율 |
| `RVOL_MIN` | `shared/strategy.py` | `1.5` | F1 최소 상대거래량 |
| `OI_MIN` | `shared/strategy.py` | `500` | F2 최소 미결제약정 |
| `PRICE_TRADE_MIN` | `shared/strategy.py` | `$20` | F3 최소 주가 |
| `MARKET_CAP_MIN` | `shared/strategy.py` | `$10B` | F3 최소 시가총액 |
| `IVR_MAX` | `shared/strategy.py` | `70%` | 최대 IVR |
| `DTE_MIN` | `shared/strategy.py` | `21일` | 최소 잔존 만기 |
| `ENTRY_CONVICTION_MIN` | `shared/strategy.py` | `0.70` | "진입" 최소 확신도 |
| `WATCH_CONVICTION_MIN` | `shared/strategy.py` | `0.50` | "관찰" 최소 확신도 |
| `CACHE_TTL_HOURS` | `.env` | `24` | LLM 캐시 유효 시간 |

**자본 배분 계산**:
```
investable_capital = TOTAL_CAPITAL × (1 - NEXT_TRADE_RESERVE_PCT) = $22,222 × 0.7 = $15,556
budget_1st         = investable_capital × ENTRY_1ST_PCT           = $15,556 × 0.5 ≈ $7,778
budget_2nd         = investable_capital × ENTRY_2ND_PCT           = $15,556 × 0.3 ≈ $4,667
budget_reserve     = investable_capital × RESERVE_PCT             = $15,556 × 0.2 ≈ $3,111
```

### 10.2 커스터마이징 포인트

| 변경 목적 | 수정 파일 | 수정 변수 |
|-----------|----------|---------|
| 총 자본 변경 | `.env` | `TOTAL_CAPITAL` |
| 필터 기준 조정 | `shared/strategy.py` | `RVOL_MIN`, `OI_MIN`, `PRICE_TRADE_MIN`, `MARKET_CAP_MIN` |
| 옵션 Delta 범위 변경 | `shared/strategy.py` | `DELTA_MID_MIN/MAX/TARGET` (기간별 조정 가능) |
| 확신도 임계값 변경 | `shared/strategy.py` | `ENTRY_CONVICTION_MIN`, `WATCH_CONVICTION_MIN`, `HOLD_CONVICTION_MIN` |
| DA 차감 기준 완화 | `shared/strategy.py` | `DA_BUY_IV_CRUSH_PENALTY`, `DA_BUY_THESIS_CONTRA_PENALTY` 등 |
| LLM 모델 교체 | `.env` | `LLM_MODEL_BUY_RESEARCH`, `LLM_MODEL_BUY_TECH_NARRATIVE` |
| 뉴스 RSS 추가 | `shared/rss_feeds.json` | `market` 또는 `tickers` 섹션 |
| Obsidian 노트 경로 변경 | `shared/config.py` | `BUY_NOTE_PATH_TEMPLATE` |

---

## 11. 오류 대응 가이드

### 11.1 치명적 오류 (파이프라인 중단)

| 오류 코드 | 원인 | 해결책 |
|-----------|------|--------|
| `E101` | Obsidian REST API 응답 없음 (localhost:27124) | Obsidian 앱 실행 및 `Local REST API` 플러그인 활성화 확인 |
| `E101` | Obsidian API 키 오류 | `.env`의 `OBSIDIAN_API_KEY` 확인 |
| `FATAL Step 1` | Summary JSON 파일 없음 | `SUMMARY_DIR` 경로 확인, AppScript 실행 확인 |
| `FATAL Step 2` | summary_data 없음 (Step 1 실패 파생) | Step 1 오류 해결 후 재실행 |

### 11.2 비치명적 오류 (degraded — 계속 진행)

| 오류 코드 | 원인 | 영향 |
|-----------|------|------|
| `E200` | Summary 디렉토리 없음 (로컬 테스트 모드) | 로컬 `summary_*.json` 폴백 시도 |
| `E200` | 데이터 오래됨 (> 12시간) | 경고만 기록, 계속 진행 |
| `E300` | LLM 감성 분석 실패 | fallback 감성 결과 저장 (`MIXED/Low`) |
| `E400` | 옵션 유효성 실패 | 탈락 기록, 다음 Step 계속 |
| `E400` | 시나리오 계산 실패 | 해당 종목 순위에서 제외 |
| `E500` | Obsidian 저장 실패 | 경고 기록, Slack 알림은 계속 시도 |
| `E501` | Slack 전송 실패 | 경고 기록 |

### 11.3 자주 발생하는 문제

**SSL 오류 (UnicodeEncodeError on CA path)**:
```
# 자동 처리됨 — scripts/run_buy_pipeline.py 상단의 SSL 우회 코드
# _ca_ascii = cache/cacert.pem 으로 복사
```

**yfinance 데이터 없음**:
- 장 마감 전 실행 시 OI=0 → summary OI 자동 복원 처리
- 종목 티커 오류 → `ctx.stock_data` 빈 dict → summary 기반으로 대체

**LLM 할당량 초과**:
- 폴백 체인 자동 시도: primary → fallback → fallback_2 → `anthropic/claude-haiku-4-5`
- 모두 실패 시: 뉴스 감성 = 기본값(`MIXED/Low`), 기술 내러티브 생략

**옵션 체인 비어있음**:
- RSI 기반 IV 추정 합성 옵션 자동 생성 (폴백)
- 결과: `is_valid=False`, `exclusion_reason="OI 미확인"` → 탈락이지만 분석 계속

---

## 12. 수정 가이드

### 12.1 새 필터 추가

`core/analysis.py` → `apply_filters()` 함수에 F8 추가:
```python
# F8: 새 조건
if some_condition:
    codes.append("F8_NEW_FILTER")
    detail_parts.append("이유 설명")
```

Requeue 대상에 포함하려면 `orchestrator/steps/buy_steps.py` → `step_11_requeue()`:
```python
requeue_codes = [c for c in codes if c in ("F1_RVOL_LOW", "F3_LIQUIDITY_LOW", "F8_NEW_FILTER")]
```

### 12.2 새 DA 차감 항목 추가

`orchestrator/steps/buy_steps.py` → `step_6_devils()` 내부:
```python
# 새 차감 항목
if new_condition:
    deduction += abs(st.DA_BUY_NEW_PENALTY)
    reasons.append("새 차감 이유")
```

`shared/strategy.py`에 상수 추가:
```python
DA_BUY_NEW_PENALTY: float = -10.0
```

### 12.3 확신도 구성 변경

`core/analysis.py` → `calculate_confidence()`:
- `trend_confidence` 가중치 (현재 0.40)
- `news_confidence` 가중치 (현재 0.20)
- `thesis_confidence` 가중치 (현재 0.30)
- `execution_confidence` 가중치 (현재 0.10)

합계가 1.0을 유지해야 함.

### 12.4 새 MCP Tool 추가

`servers/swing_mcp/server.py`:
1. `list_tools()`의 반환 리스트에 `types.Tool(...)` 추가
2. `_dispatch()` 함수에 분기 추가

---

## 13. 보고서 읽는 법

### 13.1 터미널 출력 구조

```
============================================================
  SwingMCP Buy Pipeline  [buy_20260611_120000]
  캐시 모드: 캐시 무시 (새로 실행)
============================================================

============================================================
  ★ 실행 요약 카드
  실행ID : buy_20260611_120000
  생성일 : 2026-06-11 12:05:32
  상태   : completed
============================================================

▶ 시장 레짐
  상태     : FAVORABLE           ← favorable / borderline / unfavorable
  허용 방향 : long_call           ← long_call / long_put / both / none
  확신도   : 75%                  ← 레짐 신뢰도
  리스크   : VIX elevated, ...    ← 상위 3개 리스크 요인

▶ 종목 필터링
  분석 대상  : 15개               ← watchlist 전체
  DA 통과    : 12개               ← 필터 통과 수
  탈락 사유 :
    NVDA: F5_EARNINGS_PROXIMITY  ← 코드별 설명 참조

▶ 기술 분석 점수 (상위 5)
  티커   점수   방향          신호  추세  자금
  AAPL   78.5  long_call     6/7   ✓     ✓
  MSFT   71.2  long_call     5/7   ✓     ✓
  ...

▶ 옵션 유효성 검증
  AAPL: ✓ 유효  Strike=$210  Delta=0.52  IVR=35%  DTE=45일
  MSFT: ✓ 유효  Strike=$445  Delta=0.50  IVR=28%  DTE=52일

▶ 시나리오 분석 (3케이스)
  [AAPL] long_call  2계약 × $520
    강세(40%) → EV $+420
    기본(45%) → EV $+180
    약세(15%) → EV $-320
    기대값: $+189  손절: $2.30  1차익절: $7.50

============================================================
  ★ 최종 매매 판단
============================================================

🟢 [1위] AAPL — 진입          ← 🟢진입 / 🟡관찰 / 🟠보류 / 🔴탈락
  방향    : long_call
  행사가  : $210  만기: 2026-08-15
  투자금  : $520  계약: 2계약
  기술점수: 78.5/100
  확신도  : 0.78 (high)
    추세 0.82 × 0.4  뉴스 0.70 × 0.2
    thesis 0.75 × 0.3  실행 0.80 × 0.1
  신호수  : 6/7  R/R: 2.3
  판단    : 확신도 0.78 (high) | 신호수 6/7, ...
  리스크  : 섹터 집중 주의, ...
```

### 13.2 Action 아이콘 해석

| 아이콘 | Action | 의미 |
|--------|--------|------|
| 🟢 | 진입 | 확신도 ≥ 0.70 + 옵션 유효 → **즉시 진입 가능** |
| 🟡 | 관찰 | 확신도 ≥ 0.50 + 옵션 유효 → **추가 확인 후 진입** |
| 🟠 | 보류 | 확신도 ≥ 0.30 → **조건 개선 시 재분석** |
| 🔴 | 탈락 | 확신도 < 0.30 또는 contracts = 0 → **진입 불가** |

### 13.3 확신도 수준

| 수준 | 범위 | 해석 |
|------|------|------|
| high | ≥ 0.70 | 강한 신호 수렴 — 진입 고려 |
| medium | 0.50~0.69 | 보통 신호 — 관찰 유지 |
| low | 0.30~0.49 | 약한 신호 — 보류 |
| very_low | < 0.30 | 근거 불충분 — 탈락 |

### 13.4 고위험 플래그

| 태그 | 의미 |
|------|------|
| `[⚠️일변동하락위험]` | bear case 손실 > 투자금 50% |
| `[포지션한도초과]` | total_investment > MAX_PER_POSITION |
| `[⚠️총자본초과-확인필요]` | total_investment > TOTAL_CAPITAL |

### 13.5 balanced vs aggressive 차이

| | balanced (기본) | aggressive |
|--|----------------|-----------|
| 1순위 | 확신도 (안전마진) | 기댓값 (수익 극대화) |
| 2순위 | 기댓값 | 손익비 |
| 3순위 | 손익비 | 확신도 |
| 적합 | 리스크 관리 우선 | 수익성 우선 |

---

## 14. 데이터 소스 트레이싱 맵

```
입력 데이터 → 처리 함수 → 출력 필드

[AppScript Summary JSON]
  summary.macro.spy_di_plus/minus   → Step 2 → analyze_market_regime()  → ctx.regime
  summary.tickers[tk].technical.rsi14 → Step 4 → calculate_technical_score() → TechnicalScore
  summary.options[tk].chain          → Step 7 → validate_option()          → OptionValidity
  summary.tickers[tk].insider        → Step 6 → step_6_devils()            → DA 차감

[Kavout CSV]
  KavoutRow.k_score                 → Step 10 → _build_rankings()          → FinalRanking.rationale
  KavoutRow.stock_rank_score        → Step 4  → Kavout 시그널 보정          → TechnicalScore.signal_count

[yfinance (실시간)]
  StockDetail.rsi14                 → Step 4  → technical bridge           → summary.technical.rsi14
  StockDetail.adx/di_plus/di_minus  → Step 4  → _stock_direction()         → 방향 오버라이드 판단
  StockDetail.macd_line/signal/hist → Step 4  → technical bridge           → MACD 점수

[Finnhub (실시간)]
  target_price                      → Step 4  → ctx.stock_data[tk]         → Step 8 시나리오 bull_tp
  insider_trans_pct                 → Step 4  → ctx.stock_data[tk]         → Step 6 DA 차감
  forward_pe, peg                   → Step 4  → Finnhub 밸류에이션 오버라이드 → (표시용)
  옵션 체인                          → Step 7  → ctx.summary_data.options   → OptionValidity

[K어닝 분석.md]
  business_model/strategy_changes   → Step 5  → earnings_summary           → LLM 입력

[RSS/DuckDuckGo/Brave]
  뉴스 타이틀·설명                   → Step 5  → analyze_with_llm()         → ctx.sentiment_results

[LLM (뉴스 감성)]
  overall_sentiment/debate_verdict  → Step 6  → DA Thesis 반박 판단
  bull_thesis/bear_thesis           → Step 12 → watchlist.md thesis 필드

[LLM (기술 내러티브)]
  technical_narrative               → Step 12 → Obsidian buy note 텍스트 서술
```

---

## 15. 전체 파일 트리

```
C:\MCP\Swing\
│
├── scripts/
│   └── run_buy_pipeline.py        ← [CLI] 매수 파이프라인 실행 진입점
│
├── orchestrator/
│   ├── pipelines.py               ← BuyPipeline / SellPipeline / RequeuePipeline
│   ├── engine.py                  ← PipelineEngine (MCP 싱글톤)
│   └── steps/
│       ├── buy_steps.py           ← BuySteps Step 0~13 (2,722줄)
│       └── sell_steps.py          ← SellSteps Step 0~13
│
├── servers/
│   └── swing_mcp/
│       └── server.py              ← MCP stdio 서버 (10개 Tool 노출)
│
├── core/
│   ├── analysis.py                ← analyze_market_regime / apply_filters
│   │                                  calculate_technical_score / validate_option
│   │                                  calculate_scenario / calculate_confidence
│   │                                  check_portfolio_exposure / calculate_greeks
│   ├── api_fetcher.py             ← fetch_stock_data_bulk / fetch_option_chains_bulk
│   │                                  fetch_finnhub_price_targets_bulk
│   │                                  fetch_finnhub_insider_bulk
│   │                                  fetch_macro_realtime / _calc_intraday_indicators
│   ├── llm.py                     ← call_llm / analyze_with_llm
│   │                                  call_ddg_search / call_brave_search
│   │                                  _collect_rss_feeds
│   │                                  get_cache / set_cache / parse_llm_json
│   ├── obsidian.py                ← ObsidianClient (save_buy_note / ping / write_watchlist)
│   ├── slack.py                   ← SlackClient (send_buy_result / send_fatal_error)
│   ├── parsers.py                 ← load_latest_summary / parse_summary
│   │                                  parse_kavout_universe / parse_earnings
│   ├── state.py                   ← save_snapshot / load_snapshot / append_audit
│   │                                  requeue_add / requeue_list / requeue_mark_processed
│   └── earnings_analyzer.py       ← analyze_earnings (LLM K어닝 분석)
│
├── shared/
│   ├── config.py                  ← Config 싱글톤 (get_config)
│   ├── strategy.py                ← 전략 상수 단일 소스
│   ├── schemas.py                 ← PipelineContext / PipelineResult / TechnicalScore
│   │                                  OptionValidity / Scenario / FinalRanking
│   │                                  ConfidenceScore / SummaryData / KavoutRow
│   ├── logger.py                  ← setup_logging / get_logger (구조화 로그)
│   ├── rss_feeds.json             ← RSS 피드 URL 설정 (market / tickers 섹션)
│   ├── cache/                     ← LLM 응답 캐시 + cacert.pem
│   ├── state/
│   │   ├── snapshots/             ← Step별 스냅샷 (idempotency)
│   │   └── requeue.json           ← Requeue 대기열
│   └── logs/                      ← 실행별 구조화 로그 파일
│
├── docs/
│   ├── kavout_screener_README.md  ← Kavout 스크리너 사용 설명서 (별도 문서)
│   └── buy_pipeline_README.md     ← 이 문서
│
└── .env                           ← 환경 변수 (OPENROUTER_API_KEY 등)
```

---

*작성 기준: 2026-06-11 / 코드 기준 `scripts/run_buy_pipeline.py` · `orchestrator/steps/buy_steps.py` (2,722줄) · `orchestrator/pipelines.py` · `servers/swing_mcp/server.py` · `shared/config.py` · `shared/strategy.py`*
