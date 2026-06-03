# SwingMCP v2.0.0 — 완전 참조 매뉴얼

> **면책 고지**: 이 시스템은 자동화 분석 참고 도구입니다. 투자 결정은 반드시 본인의 판단과 책임하에 이루어져야 합니다. 옵션 거래는 원금 전액 손실 위험이 있습니다.

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [아키텍처 다이어그램](#2-아키텍처-다이어그램)
3. [로컬 데이터 파일 및 경로](#3-로컬-데이터-파일-및-경로)
4. [외부 API 및 데이터 소스](#4-외부-api-및-데이터-소스)
5. [MCP 서버 목록 및 도구 등록](#5-mcp-서버-목록-및-도구-등록)
6. [매수 파이프라인 (BuyPipeline) — Step 0~13](#6-매수-파이프라인-buypipeline--step-013)
7. [매도 파이프라인 (SellPipeline) — Step 0~13](#7-매도-파이프라인-sellpipeline--step-013)
8. [Requeue 파이프라인 — Step 0~4](#8-requeue-파이프라인--step-04)
9. [펀더멘털 스크리너 (screener_mcp)](#9-펀더멘털-스크리너-screener_mcp)
10. [Kavout AI 스크리너 (kavout_mcp)](#10-kavout-ai-스크리너-kavout_mcp)
11. [핵심 분석 엔진 (core/analysis.py)](#11-핵심-분석-엔진-coreanalysispy)
12. [LLM 호출 및 캐시 (core/llm.py)](#12-llm-호출-및-캐시-corellmpy)
13. [투자 로직 평가 요약](#13-투자-로직-평가-요약)
14. [기능-모듈 매핑표](#14-기능-모듈-매핑표)
15. [사용자 프롬프트 → 내부 함수 매핑표](#15-사용자-프롬프트--내부-함수-매핑표)
16. [수정 가이드](#16-수정-가이드)

---

## 1. 시스템 개요

SwingMCP는 **옵션 스윙 트레이딩 자동화** MCP(Model Context Protocol) 서버 시스템입니다.

| 항목 | 내용 |
|------|------|
| 버전 | 2.0.0 |
| 언어 | Python 3.12 |
| 프로토콜 | MCP stdio (JSON-RPC) |
| 연동 클라이언트 | Roo Code, Claude Desktop |
| 투자 대상 | 미국 주식 롱콜/롱풋 옵션 |
| 전략 | 추세 추종 스윙 (3~15일 보유) |

### MCP 서버 3종

| 서버 | 실행 파일 | 도구 수 | 역할 |
|------|-----------|---------|------|
| `swing_mcp` | `servers/swing_mcp/server.py` | 10개 | 메인 매수/매도/포지션 관리 |
| `screener_mcp` | `servers/screener_mcp/server.py` | 2개 | Finviz 기반 펀더멘털 스크리닝 |
| `kavout_mcp` | `servers/kavout_mcp/server.py` | 2개 | Kavout AI 신호 기반 스크리닝 |

---

## 2. 아키텍처 다이어그램

```
Roo Code / Claude Desktop
        │ MCP stdio (JSON-RPC)
        ▼
┌─────────────────────────────────┐
│         swing_mcp               │  ← 10 tools
│  PipelineEngine (engine.py)     │
│    ├─ BuyPipeline  (Step 0-13)  │
│    ├─ SellPipeline (Step 0-13)  │
│    └─ RequeuePipeline (Step 0-4)│
└───────┬─────────────────────────┘
        │
        ├── core/analysis.py  (Black-Scholes, Greeks, 기술점수)
        ├── core/llm.py        (OpenRouter, DDG, Brave, 캐시)
        ├── core/parsers.py    (Finviz, Summary, Earnings, Positions)
        ├── core/state.py      (Snapshot, Audit, Requeue, Positions)
        ├── core/obsidian.py   (Obsidian REST API)
        └── core/slack.py      (Slack Bot API)

┌─────────────────────────────────┐
│       screener_mcp              │  ← 2 tools
│  3-stage fundamental pipeline   │
│  (Finviz → LLM → Score & Rank) │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│       kavout_mcp                │  ← 2 tools
│  Kavout CSV + Finviz → Screen   │
└─────────────────────────────────┘

        외부 연동
        ├── Obsidian REST API (localhost:27123)
        ├── Slack Bot API (api.slack.com)
        ├── OpenRouter API (openrouter.ai)
        ├── DuckDuckGo Search (ddgs, 무료)
        └── Brave Search API (선택, 유료)
```

---

## 3. 로컬 데이터 파일 및 경로

모든 경로는 `.env`에서 설정합니다. 기본값은 구글 드라이브 마운트 기준입니다.

| 변수명 | 기본 경로 | 내용 | 데이터 타입 |
|--------|-----------|------|-------------|
| `SUMMARY_DIR` | `Y:\내 드라이브\Swing` | 매수 요약 JSON 파일 디렉토리 | JSONL (`summary_*.json`) |
| `FINVIZ_FILE` | `Y:\내 드라이브\Swing\finviz_all_rows.txt` | Finviz 전체 종목 ROW 블록 | 텍스트, ROW 블록 형식 |
| `EARNINGS_DIR` | `Y:\내 드라이브\어닝` | 어닝 분석 마크다운 | `.md` (frontmatter YAML + 섹션) |
| `DATA_DIR` | `Y:\내 드라이브\Data` | Kavout CSV, 기타 데이터 | CSV (`kavout_*.csv`) |
| `POSITIONS_FILE` | `Y:\내 드라이브\Swing\positions.md` | 현재 포지션 | 마크다운 (YAML 블록 or 테이블) |
| `WATCHLIST_FILE` | `Y:\내 드라이브\Swing\watchlist.md` | 관찰 종목 목록 | 마크다운 테이블 |
| `CACHE_DIR` | `shared/cache/` | LLM 응답 캐시 | JSON (`{key}.json`) |
| `SNAPSHOTS_DIR` | `shared/state/snapshots/` | 파이프라인 단계별 스냅샷 | JSON (`step_{N}.json`) |
| `REQUEUE_FILE` | `shared/state/requeue.json` | Requeue 대기 종목 | JSON 배열 |
| `LOGS_DIR` | `shared/logs/` | 구조화 로그 + 감사 로그 | JSON Lines |

### summary_*.json 파일 형식 (JSONL, 3줄)

```
라인 0: "SPY $519.2 QQQ $441.1 VIX 18.3 ADX 28.5..."  ← 거시 지표 문자열
라인 1: [{"ticker": "AAPL", "technical": {...}, "news": [...]}, ...]  ← 종목 데이터
라인 2: [{"ticker": "AAPL", "chain": [...], "ivr": 45.2}, ...]  ← 옵션 체인
```

### finviz_all_rows.txt 파일 형식

```
ROW | AAPL | Apple Inc | Technology | 182.50 | ...
ROW | MSFT | Microsoft | Technology | 415.20 | ...
```

### positions.md 파일 형식 (YAML 블록)

```yaml
---
ticker: AAPL
option_type: 롱콜
strike: 185.0
expiry: 2026-06-20
entry_date: 2026-05-10
entry_premium: 4.50
entry_stock_price: 182.50
original_contracts: 5
remaining_contracts: 5
thesis: "AI 수요 증가로 상승 모멘텀"
invalidation_conditions:
  - "20MA 아래로 이탈"
  - "VIX 30 초과"
---
```

### kavout_*.csv 파일 형식

```csv
symbol,k_score,momentum_1m,roe
AAPL,7.2,0.05,0.28
MSFT,6.8,0.03,0.35
```

---

## 4. 외부 API 및 데이터 소스

| 서비스 | 엔드포인트 | 인증 | 역할 | 필수 여부 |
|--------|-----------|------|------|-----------|
| **Obsidian REST API** | `http://localhost:27123` | `OBSIDIAN_API_KEY` Bearer | 분석 노트 저장/읽기 | 필수 (FATAL) |
| **Slack Bot API** | `https://slack.com/api/` | `SLACK_BOT_TOKEN` Bearer | 알림 전송 | 선택 (없으면 비활성화) |
| **OpenRouter API** | `https://openrouter.ai/api/v1/chat/completions` | `OPENROUTER_API_KEY` | LLM 호출 | 필수 (뉴스 분석, 매도 결정) |
| **DuckDuckGo Search** | 내부 (`ddgs` 라이브러리) | 없음 | 실시간 뉴스 검색 | 선택 (없으면 스킵) |
| **Brave Search API** | `https://api.search.brave.com/` | `BRAVE_API_KEY` | 보완 뉴스 검색 | 선택 |
| **RSS Feeds** | 설정 파일 `shared/rss_feeds.json` | 없음 | 시장/종목 뉴스 | 선택 |

### OpenRouter LLM 폴백 체인 (우선순위 순)

```
1. LLM_MODEL_PRIMARY    (예: anthropic/claude-haiku-4-5)
2. LLM_MODEL_FALLBACK1  (예: openai/gpt-4o-mini)
3. LLM_MODEL_FALLBACK2  (예: deepseek/deepseek-chat-v3-0324:free)
4. LLM_MODEL_FALLBACK3  (예: meta-llama/llama-4-maverick:free)
```

### 태스크별 LLM 모델 매핑

| 태스크 | .env 변수 | 기본값 |
|--------|----------|--------|
| 뉴스/리서치 분석 (Buy Step 5) | `LLM_MODEL_BUY_RESEARCH` | `anthropic/claude-haiku-4-5` |
| 포지션 건전성 (Sell Step 4) | `LLM_MODEL_SELL_HEALTH` | `openai/gpt-4o-mini` |
| 환경 이벤트 리스크 (Sell Step 5) | `LLM_MODEL_SELL_ENV` | `deepseek/deepseek-chat:free` |
| NL 라우팅 | `LLM_MODEL_NL_ROUTING` | `deepseek/deepseek-chat:free` |

---

## 5. MCP 서버 목록 및 도구 등록

### 5.1 swing_mcp — 10개 도구

| 도구명 | 설명 | 주요 파라미터 |
|--------|------|--------------|
| `run_buy_pipeline` | 매수 분석 파이프라인 실행 (14단계) | `execution_id?`, `target_tickers?[]` |
| `run_sell_pipeline` | 매도/청산 분석 파이프라인 실행 (14단계) | `execution_id?`, `target_tickers?[]` |
| `nl_query` | 자연어 명령 처리 및 라우팅 | `query` (문자열) |
| `requeue_add` | 종목을 Requeue 대기열에 등록 | `ticker`, `failed_filters[]`, `threshold{}` |
| `requeue_list` | Requeue 대기열 조회 | `status?` (waiting/ready/processed) |
| `partial_exit_apply` | 포지션 부분 청산 처리 | `ticker`, `contracts_to_close`, `exit_premium`, `reason?` |
| `position_status` | 포지션 현황 조회 | `ticker` |
| `step_execute` | 특정 파이프라인 단계 수동 실행 | `pipeline_type`, `step`, `execution_id` |
| `health_check` | 시스템 연결 상태 확인 | (없음) |
| `cache_clear` | LLM 캐시 삭제 | `ticker?` |

### 5.2 screener_mcp — 2개 도구

| 도구명 | 설명 | 주요 파라미터 |
|--------|------|--------------|
| `run_fundamental_screen` | 3단계 펀더멘털 스크리닝 실행 | `execution_id?`, `force_refresh?`, `top_n?` |
| `screener_health_check` | 데이터 파일·연결 상태 확인 | (없음) |

### 5.3 kavout_mcp — 2개 도구

| 도구명 | 설명 | 주요 파라미터 |
|--------|------|--------------|
| `run_kavout_screen` | Kavout AI 유니버스 스크리닝 실행 | `execution_id?`, `force_refresh?`, `top_n?` |
| `kavout_health_check` | Kavout 데이터·연결 상태 확인 | (없음) |

---

## 6. 매수 파이프라인 (BuyPipeline) — Step 0~13

파일: `orchestrator/steps/buy_steps.py` (BuySteps 클래스)  
파일: `orchestrator/pipelines.py` (BuyPipeline 클래스)

### 공통 메커니즘

- **Idempotency**: `core/state.py:load_snapshot()` → 이미 완료된 step은 자동 건너뜀
- **Graceful Degradation**: FATAL 아닌 step에서 예외 발생 시 `append_audit(..., "degraded")` 기록 후 계속 진행
- **FATAL Steps**: Step 0, 1, 2 — 실패 시 파이프라인 전체 중단 + Slack FATAL 알림
- **감사 로그**: 모든 step이 `append_audit()` 호출 → `shared/logs/audit_YYYY-MM-DD.json`

---

### Step 0 — 환경 검증 (env)

**파일**: `buy_steps.py:step_0_env()` (line ~87)

| 요소 | 내용 |
|------|------|
| **입력** | `PipelineContext.paths` (경로 설정), Obsidian 연결 |
| **처리 로직** | Obsidian `ping()` → summary_dir 존재 확인 → finviz_file 존재 확인 |
| **조건 분기** | Obsidian 실패 → FATAL 예외 발생; 파일 없으면 WARN 로그 |
| **외부 호출** | `ObsidianClient.ping()` → `GET http://localhost:27123/` |
| **데이터 변환** | 없음 (검증만) |
| **출력** | 성공 시 다음 단계 진행; 실패 시 Slack E101 오류 전송 후 `RuntimeError` |

**투자 로직 평가**: 필수 인프라 확인 단계. Obsidian 없이는 분석 결과를 저장할 수 없으므로 FATAL 처리가 올바름.

---

### Step 1 — 데이터 로딩 (load)

**파일**: `buy_steps.py:step_1_load()` (line ~150)

| 요소 | 내용 |
|------|------|
| **입력** | `SUMMARY_DIR`, `FINVIZ_FILE`, `EARNINGS_DIR`, `DATA_DIR`, `POSITIONS_FILE` |
| **처리 로직** | 5가지 데이터를 순차 로딩: summary JSON → finviz txt → earnings md → finviz_output → kavout CSV → positions.md → watchlist |
| **조건 분기** | 각 파일 로딩 실패 시 개별 degraded; summary 없으면 FATAL |
| **외부 호출** | 없음 (로컬 파일만) |
| **데이터 변환** | `parse_summary()` → `SummaryData`; `parse_finviz()` → `list[FinvizRow]`; `parse_earnings()` → `list[EarningsAnalysis]`; `parse_kavout()` → `dict[str, KavoutData]`; `parse_positions()` → `list[Position]`; `parse_finviz_detail()` → `dict[str, FinvizDetail]` |
| **출력** | `ctx.summary_data`, `ctx.finviz_rows`, `ctx.earnings_list`, `ctx.kavout_data`, `ctx.positions`, `ctx.finviz_detail`, `ctx.watchlist` |

**투자 로직 평가**: 동일 날짜 summary가 여러 개일 경우 가장 최근 파일을 `load_latest_summary()`로 자동 선택. 어닝 분석은 메인 파일 + today 파일 병합하여 당일 발표분 누락 방지.

---

### Step 2 — 시장 레짐 분석 (regime)

**파일**: `buy_steps.py:step_2_regime()` → `core/analysis.py:analyze_market_regime()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.summary_data` (SPY/QQQ/VIX/ADX 지표) |
| **처리 로직** | 결정론적 규칙 기반: ADX ≥ 25 → trend_strength.pass; VIX ≤ 20 → volatility.pass; SPY + QQQ 모두 20MA 위 → long_call 허용 |
| **조건 분기** | `regime_status == "unfavorable"` → 경고 로그; `allowed_direction`에 따라 Step 3 필터 적용 방식 결정 |
| **외부 호출** | 없음 (deterministic) |
| **데이터 변환** | `SummaryData` → `MarketRegime` (Pydantic 모델) |
| **출력** | `ctx.regime: MarketRegime` (regime_status, allowed_direction, risk_factors, trend_confidence, regime_confidence) |

**투자 로직 평가**: VIX/ADX/SPY/QQQ 4개 지표를 조합한 레짐 판단은 추세 추종 전략에 적합. `borderline` 레짐에서도 거래를 허용하되 위험 요인을 기록하는 방식은 유연성 유지.

**수정 포인트**:
- ADX 기준 변경: `shared/strategy.py:TREND_ADX_STRONG` (현재 25)
- VIX 기준 변경: `shared/strategy.py:REGIME_VIX_FAVORABLE` (현재 20)

---

### Step 3 — 필터링 (filters)

**파일**: `buy_steps.py:step_3_filter()` → `core/analysis.py:apply_filters()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.summary_data.tickers`, `ctx.finviz_rows`, `ctx.regime.regime_status` |
| **처리 로직** | F1~F7 필터 순차 적용; 레짐 unfavorable이면 전체 스킵 |
| **조건 분기** | F4(상장폐지) → 해당 종목 하드 스탑; 나머지는 탈락 리스트에 추가 |
| **외부 호출** | 없음 |
| **데이터 변환** | 전체 종목 → (통과 리스트, 탈락 딕셔너리) |
| **출력** | `ctx.passed_tickers`, `ctx.filter_failures: dict[str, list[str]]` |

**필터 상세**:

| 필터 | 코드 | 조건 | 하드 스탑? |
|------|------|------|-----------|
| F1 | RVOL_LOW | `avg_volume_ratio < RVOL_MIN (1.5)` | No |
| F2 | DIRECTION_MISMATCH | 방향 불일치 (long_call에 bearish 종목 등) | No |
| F3 | LIQUIDITY_LOW | `price < PRICE_TRADE_MIN ($20)` or `market_cap < 10B` | No |
| F4 | DELISTING_RISK | 상장폐지 위험 키워드 감지 | **Yes** |
| F5 | EARNINGS_IMMINENT | 어닝 7일 이내 + IV 급등 위험 | No |
| F6 | SECTOR_OVERWEIGHT | 동일 섹터 3개 초과 | No |
| F7 | DUPLICATE | 동일 종목 중복 | No |

**투자 로직 평가**: RVOL 필터(F1)가 Requeue 대상이 되어 나중에 재분석되므로 완전한 손실 없음. 어닝 임박 필터(F5)는 IV 폭등으로 인한 프리미엄 왜곡을 방지.

---

### Step 4 — 기술 분석 (technical)

**파일**: `buy_steps.py:step_4_technical()` → `core/analysis.py:calculate_technical_score()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.passed_tickers`, `ctx.summary_data`, `ctx.finviz_rows`, `ctx.kavout_data`, `ctx.finviz_detail` |
| **처리 로직** | `asyncio.gather()`로 모든 통과 종목 병렬 기술 분석; Kavout K-Score 후처리; Finviz 애널리스트 신호 |
| **조건 분기** | K-Score ≥ 7 → `signal_count += 1`; 애널리스트 매수 consensus → `final_score += 5`; 개별 종목 분석 실패 시 해당 종목만 스킵 |
| **외부 호출** | 없음 (모두 로컬 데이터 기반) |
| **데이터 변환** | 각 종목 지표 → `TechnicalScore` (ma_alignment, adx_score, rsi_score, macd_score, rvol_score, final_score 0~100, signal_count 0~8) |
| **출력** | `ctx.technical_scores: dict[str, TechnicalScore]` |

**점수 계산 방식**:

```
raw_score = MA(25) + ADX(25) + RSI(25) + RVOL(25)  →  최대 100
final_score = raw_score - devil's_advocate_deductions
signal_count = 각 지표 통과 수 (최대 8)
```

**Devil's Advocate 차감** (`_apply_devils_advocate()`):
- RSI > 80 + 52주 고점 98%+ : -10점
- 거래량 미동반 (OBV 약화) : -5점
- 볼린저밴드 상단 돌파 : -5점
- 52주 고점 5% 이내 : -5점
- 당일 이상 급등 : -5점

**투자 로직 평가**: 기술 분석 4가지 지표 + Kavout AI + Finviz 애널리스트 의견의 3중 확인 구조. Devil's Advocate로 과열 신호 자동 차감은 과매수 진입 방지에 효과적.

**수정 포인트**:
- MA 점수 기준: `shared/strategy.py:TECH_MA_*`
- RSI 차감 임계값: `core/analysis.py:_apply_devils_advocate()` line ~180

---

### Step 5 — 뉴스/리서치 분석 (research)

**파일**: `buy_steps.py:step_5_research()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.passed_tickers`, `ctx.summary_data`, `ctx.earnings_list`, RSS 피드 설정 |
| **처리 로직** | RSS 수집 → DDG 3개 쿼리 병렬 → Brave Search → LLM 감성 분석 (`buy_step3_research` 템플릿) |
| **조건 분기** | LLM 실패 시 default 감성 딕셔너리 사용; 뉴스 없으면 LLM 건너뜀 |
| **외부 호출** | `call_ddg_search()` (DuckDuckGo), `call_brave_search()` (선택), `analyze_with_llm()` → OpenRouter API |
| **데이터 변환** | 뉴스 텍스트 + 어닝 요약 → LLM JSON 응답 → `sentiment_results[ticker]` 딕셔너리 |
| **출력** | `ctx.sentiment_results: dict[str, dict]` (overall_sentiment, bull_thesis, bear_thesis, debate_verdict, key_drivers 등) |

**DDG 검색 쿼리 3종**:
1. `"{ticker} stock news analysis"`
2. `"{ticker} options earnings catalyst"`
3. `"{ticker} technical momentum breakout"`

**LLM 출력 필드**:
- `overall_sentiment`: POSITIVE / MIXED / NEGATIVE
- `confidence`: High / Medium / Low
- `bull_thesis` / `bear_thesis`: 강세/약세 논거 (실제 뉴스 기반)
- `debate_verdict`: Slight Bull / Neutral / Slight Bear
- `conviction_delta`: -0.2 ~ 0.2 (확신도 조정값)
- `invalidation_conditions`: 무효화 조건 목록

**투자 로직 평가**: 뉴스를 단독 판단 근거로 사용하지 않도록 Role Lock 강제. `conviction_delta`로 기술 점수와 가중 합산하여 뉴스의 영향을 정량화.

---

### Step 6 — Devil's Advocate 검토 (devils)

**파일**: `buy_steps.py:step_6_devils()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.technical_scores`, `ctx.sentiment_results`, `ctx.finviz_detail`, `ctx.earnings_list` |
| **처리 로직** | 4가지 차감 요인 적용; 최종 점수 40점 미만 종목 탈락 처리 |
| **조건 분기** | IV Crush 위험 → -15점; Thesis 충돌 → -20점; 내부자 매도 → -10점; EPS 미스 → -5점 |
| **외부 호출** | 없음 |
| **데이터 변환** | `TechnicalScore.final_score` 값 인플레이스 수정; 40점 미만 → `ctx.filter_failures`에 추가 |
| **출력** | 갱신된 `ctx.technical_scores`; 추가 `ctx.filter_failures` |

**DA 차감 상세**:

| 조건 | 차감 | 소스 |
|------|------|------|
| IV Crush 위험 (어닝 7일 이내 + IVR > 60) | -15점 | `earnings_list` + 옵션 체인 |
| Thesis 충돌 (sentiment NEGATIVE + direction 반대) | -20점 | `sentiment_results` |
| 내부자 대량 매도 (`insider_trans_pct < -20%`) | -10점 | `finviz_detail.insider_trans_pct` |
| 최근 EPS 미스 (`eps_surprise_pct < -5%`) | -5점 | `finviz_detail.eps_surprise_pct` |

**투자 로직 평가**: 기술 점수 이후 추가 질적 요인 검토는 과매수/과신 편향 방지에 핵심적. 40점 컷오프는 낮은 확신도 종목 자동 제거.

**수정 포인트**:
- DA 차감값 변경: `shared/strategy.py:DA_IV_CRUSH_DEDUCTION` (현재 -15)
- 최소 점수 컷오프: `shared/strategy.py:MIN_SCORE_AFTER_DA` (현재 40)

---

### Step 7 — 옵션 진입 선택 (options)

**파일**: `buy_steps.py:step_7_options()` → `core/analysis.py:calculate_greeks()`, `validate_option()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.passed_tickers`, `ctx.summary_data.options` (옵션 체인), `ctx.technical_scores` |
| **처리 로직** | 옵션 체인에서 delta 0.4~0.7, DTE≥21 조건 만족하는 최적 계약 선택; Black-Scholes Greeks 계산; 유효성 검증 |
| **조건 분기** | 유효 계약 없으면 해당 종목 스킵; IVR 경고 구간 (60~70) → warning 플래그; OI 경고 구간 (500~1000) → warning 플래그 |
| **외부 호출** | 없음 (`scipy.stats.norm` 내부 계산) |
| **데이터 변환** | 옵션 체인 딕셔너리 → `Greeks` 모델 → `OptionValidity` 모델 |
| **출력** | `ctx.option_validity: dict[str, OptionValidity]`, `ctx.selected_options: dict[str, dict]` |

**Greeks 계산 (Black-Scholes)**:
- `delta`: 옵션 가격 민감도 (주가 $1 변화당 옵션 가격 변화)
- `gamma`: delta 변화율
- `theta`: 시간 가치 일일 감소 ($)
- `vega`: IV 1% 변화당 옵션 가격 변화 ($)
- `delta_dollar`: `delta × spot × contracts × 100` (달러 노출)

**옵션 유효성 기준** (`validate_option()`):

| 조건 | 기준값 | 변경 위치 |
|------|--------|-----------|
| Delta 범위 | 0.40 ~ 0.70 | `strategy.py:DELTA_MIN`, `DELTA_MAX` |
| IVR 최대 | 70 | `strategy.py:IVR_MAX` |
| OI 최소 | 500 | `strategy.py:OI_MIN` |
| 스프레드 최대 | 5% | `strategy.py:SPREAD_MAX_PCT` |
| DTE 최소 | 21일 | `strategy.py:DTE_MIN` |

---

### Step 8 — 시나리오 계획 (scenarios)

**파일**: `buy_steps.py:step_8_scenarios()` → `core/analysis.py:calculate_scenario()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.passed_tickers`, `ctx.summary_data`, `ctx.technical_scores`, `ctx.selected_options`, `ctx.finviz_detail` (애널리스트 목표주가) |
| **처리 로직** | 3개 시나리오 (강세/기본/약세) 확률 계산; EV 산출; Finviz 목표주가를 강세 시나리오 override로 사용 |
| **조건 분기** | `signal_count ≥ 6` → 강세 확률 45%; `signal_count 4~5` → 35%; `signal_count < 4` → 25%; Finviz 목표주가 있으면 강세 이동폭 재계산 |
| **외부 호출** | 없음 |
| **데이터 변환** | 기술 지표 + 옵션 데이터 → `Scenario` 모델 (ScenarioCase 3개 + EV + 손절/목표 프리미엄) |
| **출력** | `ctx.scenarios: dict[str, Scenario]` |

**시나리오 확률 테이블**:

| signal_count | 강세 | 기본 | 약세 |
|-------------|------|------|------|
| ≥ 6 | 45% | 35% | 20% |
| 4~5 | 35% | 40% | 25% |
| < 4 | 25% | 40% | 35% |

**손절/익절 기준**:
- 손절 (Stop Loss): 진입 프리미엄 × 0.5 (50% 손실)
- 1차 익절 (T1): 진입 프리미엄 × 1.5 (50% 수익)
- 2차 익절 (T2): 진입 프리미엄 × 2.0 (100% 수익)
- 3차 익절 (T3): 진입 프리미엄 × 2.5 (150% 수익)
- 트레일링 스탑: 고점 대비 -20%

**투자 로직 평가**: EV = Σ(확률 × 순손익)으로 기대값 계산. Finviz 목표주가를 강세 시나리오에 반영하는 방식은 애널리스트 컨센서스를 자동 통합하는 영리한 설계.

---

### Step 9 — 포트폴리오 리스크 확인 (portfolio)

**파일**: `buy_steps.py:step_9_portfolio()` → `core/analysis.py:check_portfolio_exposure()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.positions`, `ctx.passed_tickers`, `ctx.selected_options`, `ctx.technical_scores` |
| **처리 로직** | 섹터 집중도 계산; 방향 편향 (콜/풋 비율); 전체 Greeks 집계; 개별 리스크 경고 생성 |
| **조건 분기** | 섹터 집중 경고 → Slack `send_risk_alert()`; 방향 편향 → Slack 경고; delta 노출 과대 → 경고 |
| **외부 호출** | `SlackClient.send_risk_alert()` (경고 시) |
| **데이터 변환** | `list[Position]` + 신규 후보 → `PortfolioExposure` (sector_counts, direction_bias, total_delta, total_theta) |
| **출력** | `ctx.portfolio_exposure: PortfolioExposure`, Slack 경고 메시지 |

---

### Step 10 — 최종 순위 결정 (ranking)

**파일**: `buy_steps.py:step_10_ranking()` → `core/analysis.py:calculate_confidence()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.passed_tickers`, `ctx.technical_scores`, `ctx.sentiment_results`, `ctx.scenarios`, `ctx.option_validity` |
| **처리 로직** | 확신도 점수 4성분 가중 계산; 균형순위(balanced)와 공격순위(aggressive) 2가지 정렬 생성 |
| **조건 분기** | `bear_loss > 50%` → `high_downside_tickers`에 추가; conviction ≥ 0.7 → "진입", 0.5~0.7 → "관찰", < 0.5 → "보류" |
| **외부 호출** | 없음 |
| **데이터 변환** | 전체 분석 데이터 → `list[FinalRanking]` (rank, ticker, action, conviction, rationale) |
| **출력** | `ctx.rankings: list[FinalRanking]`, `ctx.rankings_aggressive: list[FinalRanking]`, `ctx.high_downside_tickers: list[str]` |

**확신도 점수 (ConfidenceScore) 계산**:

```
conviction = 0.4×trend + 0.2×news + 0.3×thesis + 0.1×execution

trend     = TechnicalScore.final_score / 100
news      = (sentiment_positive ? 1.0 : sentiment_mixed ? 0.5 : 0.1) + conviction_delta
thesis    = 1.0 if signal_count >= 6 else 0.7 if >= 4 else 0.3
execution = option_validity 점수 (delta/IVR/OI/spread/DTE 기준)
```

**정렬 기준**:
- Balanced (균형): 확신도 → EV → R/R → IVR 낮은 순
- Aggressive (공격): EV → R/R → 확신도

**투자 로직 평가**: 4성분 확신도는 기술(40%) + 뉴스(20%) + 논거(30%) + 실행(10%)의 균형잡힌 가중치. 공격적 순위를 별도 제공하여 투자자 성향에 따른 선택 가능.

---

### Step 11 — Requeue 등록 (requeue)

**파일**: `buy_steps.py:step_11_requeue()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.filter_failures` (F1 RVOL_LOW, F3 LIQUIDITY_LOW 탈락 종목만) |
| **처리 로직** | F1/F3 탈락 종목을 `core/state.py:requeue_add()`로 등록; threshold(RVOL, price_min) 함께 저장 |
| **조건 분기** | F1 탈락 → `threshold = {rvol_min: RVOL_MIN}`; F3 탈락 → `threshold = {price_min: PRICE_TRADE_MIN}` |
| **외부 호출** | 없음 (로컬 파일 쓰기) |
| **데이터 변환** | 탈락 딕셔너리 → `RequeueItem` → `requeue.json` 추가 |
| **출력** | `shared/state/requeue.json` 업데이트; `ctx.requeue_count` 카운터 |

---

### Step 12 — 저장 (storage)

**파일**: `buy_steps.py:step_12_storage()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.rankings`, `ctx.filter_failures`, `ctx.technical_scores`, `ctx.scenarios`, `ctx.option_validity`, `ctx.sentiment_results`, `ctx.regime` |
| **처리 로직** | Obsidian 매수 노트 저장; 탈락 종목 별도 노트; `watchlist.md` 갱신; "진입" 종목은 `positions.md`에 추가 |
| **조건 분기** | Obsidian 실패 → degraded (계속 진행); "진입" 액션 → `_append_positions_md()` 호출 |
| **외부 호출** | `ObsidianClient.save_buy_note()`, `save_rejected_note()`, `write_watchlist()` → Obsidian REST API |
| **데이터 변환** | `list[FinalRanking]` → 마크다운 종합 보고서 (TYPE 1~5 통합 형식) |
| **출력** | Obsidian vault 경로 (`swing-procedure/buy/YYYY-MM-DD.md`); `ctx.obsidian_note_path` |

**Obsidian 노트 경로 템플릿** (`BUY_NOTE_PATH_TEMPLATE`):
- 기본값: `swing-procedure/buy/{date}.md`
- `{date}` = `YYYY-MM-DD`

---

### Step 13 — 알림 (notify)

**파일**: `buy_steps.py:step_13_notify()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.rankings`, `ctx.rankings_aggressive`, `ctx.high_downside_tickers`, `ctx.regime`, `ctx.obsidian_note_path` |
| **처리 로직** | Slack 매수 결과 전송 (Block Kit 형식); 균형순위 + 공격순위 + 하락 위험 종목 함께 전송 |
| **조건 분기** | Slack 실패 → degraded (파이프라인 종료하지 않음) |
| **외부 호출** | `SlackClient.send_buy_result()` → `POST https://slack.com/api/chat.postMessage` |
| **데이터 변환** | `list[FinalRanking]` → Slack Block Kit JSON |
| **출력** | Slack 메시지 타임스탬프 (ts) |

---

## 7. 매도 파이프라인 (SellPipeline) — Step 0~13

파일: `orchestrator/steps/sell_steps.py` (SellSteps 클래스)

---

### Step 0 — 환경 + 포지션 로딩 (env)

**파일**: `sell_steps.py:step_0_env()`

| 요소 | 내용 |
|------|------|
| **입력** | `POSITIONS_FILE`, `SUMMARY_DIR`, `FINVIZ_FILE`, `EARNINGS_DIR`, `DATA_DIR` |
| **처리 로직** | Obsidian ping → `parse_positions()` → 5가지 데이터 로딩 (summary/finviz/earnings/finviz_detail/kavout) |
| **조건 분기** | Obsidian 실패 → FATAL; `target_tickers` 지정 시 해당 포지션만 필터링; 각 데이터 로딩 실패 → degraded |
| **외부 호출** | `ObsidianClient.ping()` |
| **데이터 변환** | 매수 Step 1과 동일 패턴의 5종 데이터 파싱 |
| **출력** | `ctx.positions`, `ctx.summary_data`, `ctx.finviz_rows`, `ctx.earnings_list`, `ctx.finviz_detail`, `ctx.kavout_data` |

---

### Step 1 — 포지션 건전성 점검 (health)

**파일**: `sell_steps.py:step_1_health()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.positions`, `ctx.summary_data` (옵션 체인, 현재 주가) |
| **처리 로직** | 각 포지션의 현재 프리미엄 조회 → 트레일링 스탑 고점 갱신 → IV 실제값 조회 → Greeks 계산 → P&L 귀인(Delta/Theta/Vega) → DTE 긴급도 → 무효화 조건 점검 |
| **조건 분기** | `DTE ≤ SELL_DTE_CRITICAL (7)` → "위급"; DTE 8~14 → "주의"; DTE 15~21 → "보통"; 21+ → "안정"; `strike=0` → Greeks 폴백값 사용 |
| **외부 호출** | 없음 |
| **데이터 변환** | `Position` → `health_results[ticker]: dict` (delta_pnl, theta_pnl, vega_pnl, dte_urgency, flags, greeks) |
| **출력** | `ctx.sell_health: dict[str, dict]` |

**P&L 귀인 계산**:
- `delta_pnl = delta × (current_price - entry_price) × 100 × contracts`
- `theta_pnl = theta × days_held × 100 × contracts`
- `vega_pnl = total_pnl - delta_pnl - theta_pnl` (잔차법)

---

### Step 2 — 시장 레짐 비교 (regime)

**파일**: `sell_steps.py:step_2_regime()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.summary_data`, `ctx.positions` |
| **처리 로직** | 현재 레짐 분석 → 진입 시 레짐과 비교 → 역전 감지 |
| **조건 분기** | 롱콜 + bearish 레짐 → "REGIME_REVERSED"; 롱풋 + bullish 레짐 → "REGIME_REVERSED"; 그 외 → "REGIME_OK" |
| **외부 호출** | 없음 |
| **데이터 변환** | `MarketRegime` + `Position.entry_regime` → `regime_flags: dict[str, str]` |
| **출력** | `ctx.sell_regime_flags: dict[str, str]` |

**투자 로직 평가**: 진입 시와 현재 레짐을 비교하는 것은 방향성 전환에 대한 자동 경보 시스템. "REGIME_REVERSED" 시 Step 7에서 PARTIAL_EXIT 권고로 자동 연결.

---

### Step 3 — 기술 분석 + 뉴스 감성 (technical)

**파일**: `sell_steps.py:step_3_technical()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.positions`, `ctx.summary_data`, `ctx.finviz_rows`, `ctx.kavout_data` |
| **처리 로직** | 각 포지션 기술 점수 계산 → DDG 2쿼리 병렬 뉴스 검색 → LLM 감성 분석 (`buy_step3_research` 템플릿 재사용) |
| **조건 분기** | 뉴스 없으면 LLM 스킵 → `_default_sentiment()` 사용; LLM 실패 → 기본값 |
| **외부 호출** | `call_ddg_search()` (2회), `analyze_with_llm()` → OpenRouter |
| **데이터 변환** | 보유 포지션 뉴스 → `ctx.sentiment_results[ticker]` |
| **출력** | `ctx.technical_scores`, `ctx.sentiment_results` |

---

### Step 4 — Thesis 검증 (thesis)

**파일**: `sell_steps.py:step_4_thesis()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.positions`, `ctx.sell_health`, `ctx.summary_data` |
| **처리 로직** | `sell_step1_health` LLM 템플릿으로 무효화 조건 점검 (결정론적 Step 1 → LLM 갱신) |
| **조건 분기** | LLM 성공 시 flags/urgency LLM 결과로 갱신; LLM 실패 → Step 1 결과 유지 (Graceful Degradation) |
| **외부 호출** | `analyze_with_llm("sell_step1_health")` → OpenRouter |
| **데이터 변환** | `Position.invalidation_conditions` + 현재 시장 데이터 → `thesis_results[ticker]` (flags, condition_checks, pnl_attribution) |
| **출력** | `ctx.sell_thesis: dict[str, dict]` |

---

### Step 5 — Devil's Advocate (devils)

**파일**: `sell_steps.py:step_5_devils()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.positions`, `ctx.summary_data.events`, 옵션 체인 IVR |
| **처리 로직** | `sell_step2_environment` LLM 템플릿으로 이벤트 리스크 + IV Crush 리스크 분석 |
| **조건 분기** | LLM 실패 → `iv_crush_risk = IVR > SELL_IVR_CRUSH_THRESHOLD` 결정론적 판단으로 폴백 |
| **외부 호출** | `analyze_with_llm("sell_step2_environment")` → OpenRouter |
| **데이터 변환** | 이벤트 목록 + IVR → `devils_results[ticker]` (event_judgment, iv_crush_risk, iv_crush_estimated_loss, recommendation) |
| **출력** | `ctx.sell_devils: dict[str, dict]` |

---

### Step 6 — IV Crush 분석 (options)

**파일**: `sell_steps.py:step_6_options()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.positions`, `ctx.summary_data.options`, `ctx.earnings_list` |
| **처리 로직** | IVR > `SELL_IVR_CRUSH_THRESHOLD` 확인 → 어닝 발표가 DTE 이내인지 확인 → IV Crush 위험 분류 |
| **조건 분기** | 고IVR + 어닝 DTE 이내 → Slack IV Crush 경고 전송; 고IVR + 어닝 없음 → "Vega 수혜 중" 정보 메모만 |
| **외부 호출** | `SlackClient.send_iv_crush_warning()` (위험 시) |
| **데이터 변환** | 옵션 체인 IVR + 어닝 날짜 → `iv_crush_warnings: list[str]` |
| **출력** | `ctx.sell_iv_warnings: list[str]` |

**투자 로직 평가**: 어닝 발표 타이밍을 반드시 확인하는 것이 핵심. IV 높음 = 항상 위험이 아님 — 어닝 없는 고IVR은 Vega 수혜 기회이므로 정확히 구분.

---

### Step 7 — 행동 시나리오 결정 (action)

**파일**: `sell_steps.py:step_7_action()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.positions`, `ctx.sell_health`, `ctx.sell_thesis`, `ctx.sell_devils`, `ctx.sell_regime_flags`, `ctx.finviz_detail`, `ctx.technical_scores` |
| **처리 로직** | 7개 우선순위 규칙 순차 적용 → HOLD/PARTIAL_EXIT/FULL_EXIT/ROLL 예비 결정 → HOLD vs EXIT 시나리오 계산 |
| **조건 분기** | 트레일링 스탑 발동 / 청산권고신호 / 스탑로스 도달 → FULL_EXIT; 150%수익 달성 → FULL_EXIT; 100%수익 or 레짐역전 → PARTIAL_EXIT; 50%수익 or 이벤트 청산유리 → PARTIAL_EXIT; 추세 미확인 → PARTIAL_EXIT; 그 외 → HOLD |
| **외부 호출** | 없음 |
| **데이터 변환** | 건전성/논거/DA 결과 → `preliminary_decisions: list[dict]`, `ctx.scenarios[ticker]` (Scenario 모델) |
| **출력** | `ctx.sell_preliminary: list[dict]`, `ctx.scenarios` 갱신 |

**Finviz 추가 플래그** (finviz_detail 기반):
- `애널리스트_매도의견`: Recom ≥ `SELL_ANALYST_SELL_THRESHOLD` (기본 3.5)
- `EPS미스_주의`: eps_surprise_pct < `SELL_EPS_MISS_PCT` (기본 -5%)
- `내부자매도_주의`: insider_trans_pct < `SELL_INSIDER_SELL_PCT` (기본 -20%)
- `목표주가_근접`: 현재가 ≥ target_price × `SELL_TARGET_PRICE_PROXIMITY` (기본 95%)

---

### Step 8 — 부분 청산 처리 (partial)

**파일**: `sell_steps.py:step_8_partial()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.sell_preliminary` (PARTIAL_EXIT인 것만), `ctx.sell_health`, `ctx.summary_data.options` |
| **처리 로직** | 상황별 청산 비율 결정 → `apply_partial_exit()` 호출 → trailing stop 재설정 |
| **조건 분기** | 레짐역전 or DTE 주의 → 75% 청산; 수익 중 확정 → 50%; 손실 헷지 → 33% |
| **외부 호출** | 없음 |
| **데이터 변환** | `Position` → 잔여 계약 수 감소 + `PartialExit` 기록 추가 + trailing_stop 재설정 |
| **출력** | 갱신된 `ctx.positions`, `d["realized_pnl"]` (각 포지션별 실현 손익) |

**청산 비율** (`shared/strategy.py`):
- `SELL_PARTIAL_REGIME_RATIO`: 0.75 (레짐역전/DTE주의)
- `SELL_PARTIAL_PROFIT_RATIO`: 0.50 (수익 확정)
- `SELL_PARTIAL_LOSS_RATIO`: 0.33 (손실 헷지)

---

### Step 9 — 포트폴리오 재확인 (portfolio)

**파일**: `sell_steps.py:step_9_portfolio()`

| 요소 | 내용 |
|------|------|
| **입력** | 부분 청산 후 `ctx.positions` |
| **처리 로직** | 잔여 포지션 집계; 총 투자금 재계산 |
| **외부 호출** | 없음 |
| **출력** | 스냅샷 (remaining_positions, total_invested) |

---

### Step 10 — 최종 행동 결정 (decision)

**파일**: `sell_steps.py:step_10_decision()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.sell_preliminary`, `ctx.sell_iv_warnings`, `ctx.sell_devils`, `ctx.sentiment_results`, `ctx.technical_scores` |
| **처리 로직** | ROLL 조건 확인 (FULL_EXIT + DTE≤7 + 추세 확인) → `sell_step3_decision` LLM 최종 결정 → `SellDecision` 생성 |
| **조건 분기** | FULL_EXIT + DTE≤7 + trend_confirmed → ROLL (만기 35일 연장); LLM 유효 액션 반환 시 LLM 결정 우선; LLM 실패 → 규칙 기반 유지 |
| **외부 호출** | `analyze_with_llm("sell_step3_decision")` → OpenRouter |
| **데이터 변환** | 예비 결정 + LLM 판정 → `SellDecision` (action, contracts_to_close, realized_pnl, unrealized_pnl, rationale, urgency) |
| **출력** | `ctx.sell_decisions: list[SellDecision]` |

---

### Step 11 — 저장 (storage)

**파일**: `sell_steps.py:step_11_storage()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.sell_decisions`, `ctx.positions`, `ctx.technical_scores`, `ctx.scenarios`, `ctx.regime`, `ctx.sell_health`, `ctx.sentiment_results` |
| **처리 로직** | Obsidian 매도 노트 저장 → 포지션 상태 캐시 저장 |
| **외부 호출** | `ObsidianClient.save_sell_note()`, `save_positions_state()` |
| **출력** | `ctx.obsidian_note_path` (vault 경로) |

---

### Step 12 — FULL_EXIT 복기 (review)

**파일**: `sell_steps.py:step_12_review()`

| 요소 | 내용 |
|------|------|
| **입력** | FULL_EXIT 결정 포지션, 실현 손익 |
| **처리 로직** | `sell_step4_review` LLM 트레이드 복기 분석 |
| **외부 호출** | `analyze_with_llm("sell_step4_review")` → OpenRouter |
| **출력** | 스냅샷 (review_notes: 교훈, thesis_accuracy, improvement) |

---

### Step 13 — 알림 (notify)

**파일**: `sell_steps.py:step_13_notify()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.sell_decisions`, `ctx.obsidian_note_path` |
| **처리 로직** | 매도 결과 Slack 전송; FULL_EXIT + 손실 포지션 → Slack `send_risk_alert()` (STOP_LOSS_TRIGGERED) |
| **외부 호출** | `SlackClient.send_sell_result()`, `send_risk_alert()` |
| **출력** | Slack 메시지 타임스탬프 |

---

## 8. Requeue 파이프라인 — Step 0~4

파일: `orchestrator/pipelines.py` (RequeuePipeline 클래스)

| Step | 역할 | 핵심 로직 |
|------|------|-----------|
| Step 0 | 환경 확인 | Obsidian ping → 데이터 로딩 (매수 Step 0~1 동일) |
| Step 1 | Requeue 목록 로드 | `requeue_list(status="waiting")` → waiting 항목만 |
| Step 2 | 조건 충족 여부 확인 | `requeue_check_ready(summary_data)` → IVR/가격/RVOL 조건 체크 → ready 전환 |
| Step 3 | ready 종목 BuyPipeline 실행 | `BuyPipeline(target_tickers=ready_tickers)` 신규 실행 |
| Step 4 | 처리 완료 표시 | `requeue_mark_processed(ticker)` → status = "processed" |

**Requeue 조건 확인 로직** (`requeue_check_ready()`):
- `ivr_max`: IVR이 등록 시 기준 이하로 떨어졌는지 확인
- `price_min`: 주가가 최소 거래가 이상인지 확인
- `rvol_min`: RVOL이 최소 기준 이상인지 확인
- 모든 조건 충족 시 → `status = "ready"`

---

## 9. 펀더멘털 스크리너 (screener_mcp)

파일: `servers/screener_mcp/server.py`, `core/fundamental_screener.py`

### 3단계 파이프라인

| 단계 | 역할 | 입력 → 출력 |
|------|------|------------|
| Step 1 | Finviz 파싱 | `finviz_output/*.txt` → `dict[str, FinvizDetail]` |
| Step 2 | 어닝콜 LLM 분석 | `어닝_분석.md` → `dict[str, EarningsCallAnalysis]` |
| Step 3 | 점수화 + 랭킹 | FinvizDetail + EarningsCallAnalysis → `list[FundamentalScoreResult]` |

### 점수 계산 구조

```
Momentum Score (0~100):
  = RSI(35%) + RelVolume(35%) + 52주위치(30%)

Fundamental Score (0~100):
  = 매출성장(35%) + 순이익성장(35%) + 영업이익률(30%)

Catalyst Score (0~100):  [어닝콜 있을 때만]
  = 가이던스(50%) + 경영진톤(30%) + catalyst_strength(20%)

Final Score:
  - Catalyst 있음: 0.35×M + 0.40×F + 0.25×C
  - Catalyst 없음: 0.47×M + 0.53×F
```

### RSI 점수 기준

| RSI 범위 | 점수 | 해석 |
|----------|------|------|
| 50~70 | 100 | 이상적 (모멘텀 지속) |
| 40~50 | 65 | 적정 |
| 70~80 | 55 | 과매수 근접 |
| < 40 | 30 | 과매도 (약세) |
| > 80 | 20 | 극단적 과매수 |

### 출력

- Obsidian 노트: `swing-procedure/screener/YYYY-MM-DD.md`
- Slack 요약: Top 3 + 나머지 Top 10

---

## 10. Kavout AI 스크리너 (kavout_mcp)

파일: `servers/kavout_mcp/server.py`

screener_mcp와 동일한 3단계 파이프라인이지만:

| 항목 | screener_mcp | kavout_mcp |
|------|-------------|------------|
| 유니버스 소스 | `finviz_output/*.txt` | `kavout_*.csv` (최신 자동 탐색) |
| 어닝 파일 | `어닝_분석.md` | `K어닝 분석.md` |
| 어닝콜 폴더 | `어닝콜_output/` | `K어닝콜_output/` |
| Obsidian 경로 | `screener/{date}.md` | `screener/kavout/{date}.md` |
| Kavout K-Score | 없음 | 노트 + Slack에 K-Score 표시 |

### Kavout K-Score 활용

- K-Score 1~9 스케일 (Kavout AI 자체 신호)
- `kavout_*.csv`에서 파싱
- `swing_mcp` 매수 파이프라인 Step 4에서도 활용: K-Score ≥ 7 → `signal_count += 1`

---

## 11. 핵심 분석 엔진 (core/analysis.py)

### analyze_market_regime()

```
입력: SummaryData
처리: ADX ≥ 25 → pass; VIX ≤ 20 → pass; SPY+QQQ 20MA → 방향 결정
출력: MarketRegime
```

### calculate_technical_score()

```
입력: ticker, direction, summary, finviz_rows, kavout_score
처리: MA(25) + ADX(25) + RSI(25) + RVOL(25) → raw_score → DA 차감 → final_score
출력: TechnicalScore (final_score 0~100, signal_count 0~8)
```

### calculate_greeks() — Black-Scholes

```
입력: spot, strike, expiry_days, iv, option_type
처리: scipy.stats.norm.cdf() 기반 d1, d2 계산
출력: Greeks (delta, gamma, theta, vega, rho, ivr)

공식:
  d1 = (ln(S/K) + (r + σ²/2)×T) / (σ√T)
  d2 = d1 - σ√T
  delta_call = N(d1)
  delta_put  = N(d1) - 1
```

### validate_option()

```
입력: greeks, option chain entry
출력: OptionValidity (delta_ok, ivr_ok, oi_ok, spread_ok, dte_ok + 경고 플래그)
```

### calculate_scenario()

```
입력: ticker, direction, spot, strike, iv, contracts, signal_count, ...
처리: 확률 테이블 → 3케이스 × (target_price → option_value → net_profit)
출력: Scenario (bullish/base/bearish ScenarioCase + EV + 손절/목표 프리미엄)
```

### apply_filters()

```
입력: tickers, summary_data, finviz_rows, regime
처리: F1~F7 필터 순차 적용
출력: (passed_list, failures_dict)
```

---

## 12. LLM 호출 및 캐시 (core/llm.py)

### call_llm()

```
입력: messages, system_prompt, model, max_tokens, temperature
처리:
  1. 지정 모델로 OpenRouter POST 시도
  2. 실패 시 MODEL_PRIORITY 폴백 체인 (최대 4개 모델 순차 시도)
  3. tenacity retry (최대 3회, 지수 백오프)
출력: LLMResponse (content, model_used, usage)
```

### 캐시 시스템

```
경로: shared/cache/{key}.json
형식: {"expires_at": "ISO datetime", "data": {...}}

get_cached_or_fetch(cache_key, fetch_fn, *args):
  1. 캐시 히트 → 반환
  2. 캐시 미스 → fetch_fn 실행 → 캐시 저장 → 반환

TTL:
  - expires_today=True → 오늘 23:59 만료
  - ttl_hours=N → N시간 후 만료
```

### analyze_with_llm()

```
입력: template_name, template_vars, cache_key(선택)
처리:
  1. prompts.render(template_name, **template_vars)
  2. get_model_for(template_name) → 모델 결정
  3. call_llm() → 응답
  4. parse_llm_json() → 마크다운 코드 블록 제거 후 JSON 파싱
출력: dict (LLM 응답 JSON)
```

---

## 13. 투자 로직 평가 요약

| 기능 | 평가 | 비고 |
|------|------|------|
| 레짐 필터링 | ★★★★★ | ADX+VIX+SPY+QQQ 4중 확인, 결정론적 |
| 기술 점수 (100점) | ★★★★★ | 4지표 균형 배분 + Kavout AI + 애널리스트 3중 검증 |
| Devil's Advocate 차감 | ★★★★☆ | 자동 과열 방지, 차감 수치는 조정 가능 |
| 옵션 선택 기준 | ★★★★★ | Delta 0.4~0.7 ITM 범위, DTE≥21 시간 여유, OI/스프레드 유동성 |
| 시나리오 EV 계산 | ★★★★☆ | signal_count 기반 확률 테이블은 단순화. 실제 내재 확률(IV 기반) 도입 고려 |
| 뉴스 LLM 분석 | ★★★★☆ | Role Lock으로 편향 방지, but LLM 환각 가능성 → `conviction_delta` 제한 |
| IV Crush 보호 | ★★★★★ | 어닝 타이밍까지 확인하는 정밀 분류 |
| 트레일링 스탑 | ★★★★☆ | 고점 자동 추적, 20% 기본값은 조정 가능 |
| Requeue 시스템 | ★★★★☆ | 탈락 종목 재분석으로 기회 손실 최소화 |
| P&L 귀인 분석 | ★★★★☆ | Delta/Theta/Vega 분리 → Vega는 잔차법으로 근사 |

---

## 14. 기능-모듈 매핑표

| 기능 | 파일 | 핵심 함수/클래스 |
|------|------|----------------|
| 매수 파이프라인 실행 | `orchestrator/pipelines.py` | `BuyPipeline.run()` |
| 매도 파이프라인 실행 | `orchestrator/pipelines.py` | `SellPipeline.run()` |
| Requeue 파이프라인 | `orchestrator/pipelines.py` | `RequeuePipeline.run()` |
| 엔진 (NL 라우팅 포함) | `orchestrator/engine.py` | `PipelineEngine` |
| 시장 레짐 분석 | `core/analysis.py` | `analyze_market_regime()` |
| 기술 점수 계산 | `core/analysis.py` | `calculate_technical_score()` |
| Black-Scholes Greeks | `core/analysis.py` | `calculate_greeks()` |
| 옵션 유효성 검증 | `core/analysis.py` | `validate_option()` |
| 시나리오 계획 | `core/analysis.py` | `calculate_scenario()` |
| 포트폴리오 리스크 | `core/analysis.py` | `check_portfolio_exposure()` |
| 확신도 점수 | `core/analysis.py` | `calculate_confidence()` |
| 필터링 | `core/analysis.py` | `apply_filters()` |
| LLM 호출 | `core/llm.py` | `call_llm()`, `analyze_with_llm()` |
| LLM 캐시 | `core/llm.py` | `get_cache()`, `set_cache()` |
| DDG 검색 | `core/llm.py` | `call_ddg_search()` |
| Brave 검색 | `core/llm.py` | `call_brave_search()` |
| Finviz 파싱 | `core/parsers.py` | `parse_finviz()`, `parse_finviz_detail()` |
| Summary 파싱 | `core/parsers.py` | `parse_summary()`, `load_latest_summary()` |
| 어닝 파싱 | `core/parsers.py` | `parse_earnings()` |
| 포지션 파싱 | `core/parsers.py` | `parse_positions()` |
| Kavout 파싱 | `core/parsers.py` | `parse_kavout()`, `parse_kavout_universe()` |
| 스냅샷 저장/로드 | `core/state.py` | `save_snapshot()`, `load_snapshot()` |
| 감사 로그 | `core/state.py` | `append_audit()` |
| Requeue 관리 | `core/state.py` | `requeue_add()`, `requeue_check_ready()` |
| 부분 청산 처리 | `core/state.py` | `apply_partial_exit()` |
| Obsidian 저장 | `core/obsidian.py` | `ObsidianClient.save_buy_note()` |
| Slack 알림 | `core/slack.py` | `SlackClient.send_buy_result()` |
| 펀더멘털 점수 | `core/fundamental_screener.py` | `rank_universe()`, `score_ticker()` |
| 어닝콜 LLM 분석 | `core/earnings_analyzer.py` | `analyze_earnings()` |
| LLM 프롬프트 | `shared/prompts.py` | `render()`, `get_model_for()` |
| 설정 관리 | `shared/config.py` | `get_config()` |
| 전략 파라미터 | `shared/strategy.py` | 25개 섹션, 모든 숫자 임계값 |
| 스키마 정의 | `shared/schemas.py` | 18+ Pydantic v2 모델 |
| 구조화 로깅 | `shared/logger.py` | `setup_logging()`, `get_logger()` |

---

## 15. 사용자 프롬프트 → 내부 함수 매핑표

### 15.1 자연어 명령 → 인텐트 → 도구

`orchestrator/engine.py:route_nl()` 에서 처리. 키워드 매핑 우선, 실패 시 LLM 폴백.

| 사용자 입력 예시 | 감지 키워드 | 인텐트 | 호출 도구/함수 |
|----------------|------------|--------|---------------|
| "매수 분석 실행해줘" | 매수, 진입, 분석 | BUY_PIPELINE | `engine.run_buy()` |
| "오늘 매수 파이프라인 돌려줘" | 매수, 파이프라인 | BUY_PIPELINE | `engine.run_buy()` |
| "매도 분석 해줘" | 매도, 청산, 매각 | SELL_PIPELINE | `engine.run_sell()` |
| "포지션 청산 검토해줘" | 청산 | SELL_PIPELINE | `engine.run_sell()` |
| "AAPL 포지션 어떻게 됐어?" | 포지션, 현황 | POSITION_STATUS | `engine.position_status("AAPL")` |
| "TSLA 대기열에 넣어줘" | 대기, 리큐 | REQUEUE_ADD | `engine.requeue_add()` |
| "대기 종목 목록 보여줘" | 목록, 리스트, 대기 | REQUEUE_LIST | `engine.requeue_list()` |
| "Step 5 다시 실행해줘" | 단계, 스텝 | STEP_EXECUTE | `engine.step_execute()` |
| "MSFT 계약 3개 청산" | 계약, 부분 | PARTIAL_EXIT | `engine.partial_exit()` |

### 15.2 MCP 도구 직접 호출

| 도구 | 파라미터 예시 | 내부 경로 |
|------|-------------|-----------|
| `run_buy_pipeline` | `{}` | `engine.run_buy()` → `BuyPipeline.run()` → `BuySteps.step_0~13` |
| `run_buy_pipeline` | `{"target_tickers": ["AAPL", "MSFT"]}` | 위와 동일, 단 특정 종목만 필터 |
| `run_sell_pipeline` | `{"target_tickers": ["TSLA"]}` | `engine.run_sell()` → `SellPipeline.run()` → `SellSteps.step_0~13` |
| `nl_query` | `{"query": "매수 분석 실행"}` | `engine.route_nl()` → 키워드/LLM 라우팅 |
| `health_check` | `{}` | `_health_check()` → Obsidian ping + 파일 존재 확인 |
| `cache_clear` | `{"ticker": "AAPL"}` | `engine.clear_cache("AAPL")` |
| `cache_clear` | `{}` | 전체 캐시 삭제 |
| `partial_exit_apply` | `{"ticker": "AAPL", "contracts_to_close": 2, "exit_premium": 5.50}` | `engine.partial_exit()` → `apply_partial_exit()` |
| `position_status` | `{"ticker": "AAPL"}` | `engine.position_status()` → `parse_positions()` |
| `requeue_add` | `{"ticker": "NVDA", "failed_filters": ["F1_RVOL_LOW"]}` | `engine.requeue_add()` → `state.requeue_add()` |
| `requeue_list` | `{"status": "waiting"}` | `engine.requeue_list()` → `state.requeue_list()` |
| `step_execute` | `{"pipeline_type": "buy", "step": 5, "execution_id": "abc123"}` | `engine.step_execute()` → 개별 스텝 실행 |
| `run_fundamental_screen` | `{"top_n": 10}` | `_run_fundamental_screen()` → `rank_universe()` |
| `run_kavout_screen` | `{"force_refresh": true}` | `_run_kavout_screen()` → `parse_kavout_universe()` + `rank_universe()` |

### 15.3 LLM 프롬프트 → 실행 경로

| 상황 | 템플릿 | 모델 변수 | 출력 JSON 핵심 필드 |
|------|--------|----------|-------------------|
| 뉴스 감성 분석 | `buy_step3_research` | `LLM_MODEL_BUY_RESEARCH` | overall_sentiment, bull_thesis, bear_thesis, conviction_delta |
| 포지션 무효화 점검 | `sell_step1_health` | `LLM_MODEL_SELL_HEALTH` | flags, dte_urgency, condition_checks |
| 이벤트 리스크 분석 | `sell_step2_environment` | `LLM_MODEL_SELL_ENV` | event_judgment, iv_crush_risk |
| 최종 매도 결정 | `sell_step3_decision` | `LLM_MODEL_SELL_HEALTH` | action (HOLD/PARTIAL_EXIT/FULL_EXIT/ROLL) |
| 트레이드 복기 | `sell_step4_review` | `LLM_MODEL_SELL_HEALTH` | thesis_accuracy, lesson, improvement |
| NL 명령 라우팅 | `nl_routing` | `LLM_MODEL_NL_ROUTING` | intent, extracted_tickers, routed_tool |

---

## 16. 수정 가이드

### 16.1 전략 파라미터 수정

모든 수치 임계값은 `shared/strategy.py` 한 곳에서 관리합니다.

```python
# shared/strategy.py

# 옵션 선택 기준
DELTA_MIN = 0.40        # 델타 하한 (더 OTM 허용 시 낮춤)
DELTA_MAX = 0.70        # 델타 상한 (더 ITM 허용 시 높임)
IVR_MAX = 70.0          # IVR 최대 (낮출수록 보수적)
DTE_MIN = 21            # 최소 DTE (높일수록 시간 여유)
OI_MIN = 500            # 최소 미결제약정

# 필터 기준
RVOL_MIN = 1.5          # 최소 상대거래량 (높일수록 엄격)
PRICE_TRADE_MIN = 20.0  # 최소 주가
MARKET_CAP_MIN = 10_000_000_000  # 최소 시가총액 ($10B)

# 매도 기준
SELL_DTE_CRITICAL = 7   # 위급 DTE 기준
SELL_DTE_WARNING = 14   # 주의 DTE 기준
SELL_STOP_LOSS_RATIO = 0.5    # 손절: 진입 프리미엄 × 0.5
SELL_TARGET_1ST_RATIO = 1.5   # 1차 익절
SELL_TARGET_2ND_RATIO = 2.0   # 2차 익절
SELL_TARGET_3RD_RATIO = 2.5   # 3차 익절

# DA 차감
DA_IV_CRUSH_DEDUCTION = -15    # IV Crush 차감점
DA_THESIS_CONFLICT_DEDUCTION = -20  # Thesis 충돌 차감점
DA_INSIDER_SELL_DEDUCTION = -10     # 내부자 매도 차감점
DA_EPS_MISS_DEDUCTION = -5          # EPS 미스 차감점
MIN_SCORE_AFTER_DA = 40         # DA 후 최소 점수
```

### 16.2 LLM 모델 변경

`.env` 파일에서 변수만 수정하면 즉시 반영됩니다.

```env
# .env
LLM_MODEL_BUY_RESEARCH=anthropic/claude-haiku-4-5
LLM_MODEL_SELL_HEALTH=openai/gpt-4o-mini
LLM_MODEL_SELL_ENV=deepseek/deepseek-chat:free
LLM_MODEL_NL_ROUTING=deepseek/deepseek-chat:free

# 폴백 체인
LLM_MODEL_PRIMARY=anthropic/claude-haiku-4-5
LLM_MODEL_FALLBACK1=openai/gpt-4o-mini
LLM_MODEL_FALLBACK2=deepseek/deepseek-chat-v3-0324:free
LLM_MODEL_FALLBACK3=meta-llama/llama-4-maverick:free
```

### 16.3 새로운 필터 추가

파일: `core/analysis.py:apply_filters()` (line ~300)

```python
# 예: F8 — RSI 과열 필터 추가
def apply_filters(tickers, summary_data, finviz_rows, regime):
    ...
    # 기존 F1~F7 처리 후:
    for ticker in passed_list[:]:
        ticker_data = summary_data.tickers.get(ticker)
        if ticker_data and ticker_data.technical.rsi > 85:
            failures[ticker] = failures.get(ticker, []) + ["F8_RSI_OVERBOUGHT"]
            passed_list.remove(ticker)
```

### 16.4 Obsidian 노트 경로 변경

파일: `shared/config.py` 또는 `.env`

```env
BUY_NOTE_PATH_TEMPLATE=swing-procedure/buy/{date}.md
SELL_NOTE_PATH_TEMPLATE=swing-procedure/sell/{date}.md
TICKER_NOTE_PATH_TEMPLATE=swing-procedure/tickers/{ticker}.md
REJECTED_NOTE_PATH_TEMPLATE=swing-procedure/rejected/{ticker}_{date}.md
```

### 16.5 Slack 채널 변경

```env
SLACK_CHANNEL_MAIN=#swing-trading       # 정상 결과 채널
SLACK_CHANNEL_ALERT=#swing-alerts       # 리스크·오류 채널
```

### 16.6 RSS 피드 추가

파일: `shared/rss_feeds.json`

```json
{
  "market": [
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html"
  ],
  "tickers": {}
}
```

### 16.7 확신도 가중치 변경

파일: `shared/strategy.py`

```python
# 현재 기본값
CONVICTION_WEIGHT_TREND = 0.4      # 기술 분석 비중
CONVICTION_WEIGHT_NEWS = 0.2       # 뉴스 감성 비중
CONVICTION_WEIGHT_THESIS = 0.3     # 투자 논거 비중
CONVICTION_WEIGHT_EXECUTION = 0.1  # 실행 조건 비중
```

---

*SwingMCP v2.0.0 — 생성일: 2026-05-25*  
*본 문서는 코드 자동 분석으로 작성된 참고 자료입니다.*
