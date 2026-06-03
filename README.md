# SwingMCP v2.0.0 — 완전 참조 매뉴얼

> **면책 고지**: 이 시스템은 자동화 분석 참고 도구입니다. 투자 결정은 반드시 본인의 판단과 책임하에 이루어져야 합니다. 옵션 거래는 원금 전액 손실 위험이 있습니다.

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [아키텍처 다이어그램](#2-아키텍처-다이어그램)
3. [CLI 직접 실행 가이드 (주 운영 방식)](#3-cli-직접-실행-가이드-주-운영-방식)
4. [로컬 데이터 파일 및 경로](#4-로컬-데이터-파일-및-경로)
5. [외부 API 및 데이터 소스](#5-외부-api-및-데이터-소스)
6. [MCP 서버 목록 및 도구 등록 (선택적 연동)](#6-mcp-서버-목록-및-도구-등록-선택적-연동)
7. [매수 파이프라인 (BuyPipeline) — Step 0~13](#7-매수-파이프라인-buypipeline--step-013)
8. [매도 파이프라인 (SellPipeline) — Step 0~13](#8-매도-파이프라인-sellpipeline--step-013)
9. [Requeue 파이프라인 — Step 0~4](#9-requeue-파이프라인--step-04)
10. [펀더멘털 스크리너 (screener_mcp)](#10-펀더멘털-스크리너-screener_mcp)
11. [Kavout AI 스크리너 (kavout_mcp)](#11-kavout-ai-스크리너-kavout_mcp)
12. [핵심 분석 엔진 (core/analysis.py)](#12-핵심-분석-엔진-coreanalysispy)
13. [LLM 호출 및 캐시 (core/llm.py)](#13-llm-호출-및-캐시-corellmpy)
14. [투자 로직 평가 요약](#14-투자-로직-평가-요약)
15. [기능-모듈 매핑표](#15-기능-모듈-매핑표)
16. [사용자 프롬프트 → 내부 함수 매핑표](#16-사용자-프롬프트--내부-함수-매핑표)
17. [수정 가이드](#17-수정-가이드)
18. [매수 노트 품질 목표 및 달성 현황](#18-매수-노트-품질-목표-및-달성-현황)

---

## 1. 시스템 개요

SwingMCP는 **옵션 스윙 트레이딩 자동화** 시스템입니다.

| 항목 | 내용 |
|------|------|
| 버전 | 2.0.0 |
| 언어 | Python 3.12 |
| 주 실행 방식 | **CLI 직접 실행** (`scripts/` 스크립트) |
| 선택적 연동 | MCP stdio (JSON-RPC) — Roo Code / Claude Desktop |
| 투자 대상 | 미국 주식 롱콜/롱풋 옵션 |
| 전략 | 추세 추종 스윙 (3~15일 보유) |

### 스크립트 구성 (CLI 직접 실행)

| 스크립트 | 역할 | 유니버스 소스 | 실제 저장/알림 |
|----------|------|-------------|:---:|
| `scripts/run_screener.py` | Finviz 기반 펀더멘털 스크리닝 + 어닝콜 LLM 분석 | `finviz_output/*.txt` (로컬 파일) | ✅ |
| `scripts/run_kavout_screener.py` | **Kavout AI 유니버스 스크리닝** (Yahoo Finance API 실시간 수집) | `kavout_*.csv` + **yfinance API** | ✅ |
| `scripts/run_buy_pipeline.py` | 매수 분석 파이프라인 (14단계) | summary JSON | ✅ |
| `scripts/run_sell_pipeline.py --real` | 매도 분석 파이프라인 (14단계) | positions.md | ✅ |
| `scripts/run_sell_pipeline.py` | 매도 파이프라인 DRY-RUN (테스트용) | — | ❌ Mock |
| `scripts/run_requeue.py` | Requeue 파이프라인 (ready 종목 재실행) | requeue.json | ✅ |
| `scripts/run_ticker.py TSLA` | **특정 종목 단일/복수 매수 분석** | summary JSON | ✅ |

### MCP 서버 3종 (선택적 연동)

| 서버 | 실행 파일 | 도구 수 | 역할 |
|------|-----------|---------|------|
| `swing_mcp` | `servers/swing_mcp/server.py` | 10개 | 메인 매수/매도/포지션 관리 |
| `screener_mcp` | `servers/screener_mcp/server.py` | 2개 | Finviz 기반 펀더멘털 스크리닝 |
| `kavout_mcp` | `servers/kavout_mcp/server.py` | 2개 | Kavout AI 신호 기반 스크리닝 |

---

## 2. 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────────────┐
│                  CLI 직접 실행 (주 운영)                  │
│                                                         │
│  scripts/run_screener.py        → Finviz 펀더멘털 스크리닝 │
│  scripts/run_kavout_screener.py → Kavout AI 스크리닝      │
│  scripts/run_buy_pipeline.py    → 매수 분석 (14단계)       │
│  scripts/run_sell_pipeline.py --real → 매도 (14단계)      │
│  scripts/run_requeue.py         → Requeue 파이프라인       │
└───────────────────────┬─────────────────────────────────┘
                        │
┌─────────────────────────────────────────────────────────┐
│            MCP 서버 (선택적 — Roo Code / Claude Desktop)  │
│  swing_mcp / screener_mcp / kavout_mcp                  │
│  servers/*/server.py  ← MCP stdio (JSON-RPC)            │
└───────────────────────┬─────────────────────────────────┘
                        │
        orchestrator/engine.py (PipelineEngine)
          ├─ BuyPipeline   (Step 0~13)
          ├─ SellPipeline  (Step 0~13)
          └─ RequeuePipeline (Step 0~4)
                        │
        ├── core/analysis.py         (Black-Scholes, Greeks, 기술점수)
        ├── core/api_fetcher.py      (Yahoo Finance API → FinvizDetail)
        ├── core/llm.py              (OpenRouter, DDG, Brave, 캐시)
        ├── core/parsers.py          (Finviz, Summary, Earnings, Positions)
        ├── core/state.py            (Snapshot, Audit, Requeue, Positions)
        ├── core/obsidian.py         (Obsidian REST API)
        ├── core/slack.py            (Slack Bot API)
        ├── core/fundamental_screener.py   (펀더멘털 점수화)
        └── core/earnings_analyzer.py      (어닝콜 LLM 분석)

        외부 연동
        ├── Yahoo Finance API   (yfinance, 무료 — Kavout 스크리너용)
        ├── Obsidian REST API   (https://127.0.0.1:27124)
        ├── Slack Bot API       (api.slack.com)
        ├── OpenRouter API      (openrouter.ai)
        ├── DuckDuckGo Search   (ddgs, 무료)
        └── Brave Search API    (선택, 유료)
```

---

## 3. CLI 직접 실행 가이드 (주 운영 방식)

### 전제 조건

```powershell
# 작업 디렉토리
cd C:\MCP\Swing

# .venv 활성화 (또는 .venv\Scripts\python 직접 사용)
.venv\Scripts\activate
```

`.env` 파일에 다음 키가 설정되어 있어야 합니다:

```env
OPENROUTER_API_KEY=...
OBSIDIAN_API_KEY=...
OBSIDIAN_BASE_URL=https://127.0.0.1:27124
SLACK_BOT_TOKEN=...
```

---

### 권장 일일 워크플로우

```
Step A  →  Step B  →  Step C
스크리닝      매수 분석     매도 분석
```

**Step A: Kavout AI 스크리닝** (매수 전 실행 — Yahoo Finance API 실시간 수집)

```powershell
python scripts/run_kavout_screener.py
```

**Step A (대안): Finviz 기반 펀더멘털 스크리닝** (finviz_output 파일 보유 시)

```powershell
python scripts/run_screener.py
```

**Step B: 매수 파이프라인** (A 결과 기반)

```powershell
python scripts/run_buy_pipeline.py
```

**Step C: 매도 파이프라인** (보유 포지션 있을 때 — 실제 저장/알림)

```powershell
python scripts/run_sell_pipeline.py --real
```

---

### 스크립트별 전체 옵션

#### `run_kavout_screener.py` — Kavout AI 유니버스 + Yahoo Finance API 스크리닝

```powershell
# 기본 실행 (Top 10 출력, Obsidian 저장, Slack 전송)
python scripts/run_kavout_screener.py

# LLM 캐시 무시하고 K어닝콜 재분석
python scripts/run_kavout_screener.py --force-refresh

# 상위 N개 출력
python scripts/run_kavout_screener.py --top 20
python scripts/run_kavout_screener.py --force-refresh --top 15
```

**처리 순서:**
1. `kavout_*.csv` 파싱 → 유니버스 티커 목록 + K-Score 확보
2. 전 티커 **Yahoo Finance API 실시간 수집** (`core/api_fetcher.py`)
   - 가격·등락률, RSI(14), 상대거래량(RVOL), SMA20·50·200 위치
   - Forward PE, PEG, Beta, 애널리스트 목표가·추천등급
   - 영업이익률·순이익률·매출성장률, EPS 서프라이즈, **시가총액**
   - API 실패 시 `kavout_output/*.txt` → kavout CSV 순으로 fallback
3. `kavout_output/*.txt` 파싱 → API가 채우지 못한 펀더멘털 필드 보완
4. `K어닝 분석.md` LLM 분석 (K어닝콜 가이던스 + 경영진 톤)
5. 시가총액 티어 분류 → 티어 내 모멘텀/펀더멘털/카탈리스트 점수화 + 랭킹
6. Obsidian: `swing-procedure/screener/kavout/YYYY-MM-DD.md`
7. Slack: 티어별 Top 3 요약 + K-Score 표시

> **API 키 불필요**: Yahoo Finance는 무료 공개 API (`yfinance` 라이브러리). 별도 키 등록 없이 실행됩니다.

---

#### `run_screener.py` — Finviz 기반 펀더멘털 스크리닝 + 어닝콜 LLM 분석

```powershell
# 기본 실행 (Top 10 출력, Obsidian 저장, Slack 전송)
python scripts/run_screener.py

# LLM 캐시 무시하고 재분석
python scripts/run_screener.py --force-refresh

# 상위 N개 출력
python scripts/run_screener.py --top 20
python scripts/run_screener.py --force-refresh --top 15
```

**처리 순서:**
1. `finviz_output/*.txt` 파싱 → `dict[str, FinvizDetail]` 생성
2. `어닝 분석.md` LLM 분석 (어닝콜 가이던스 + 경영진 톤)
3. 모멘텀/펀더멘털/카탈리스트 점수화 + 랭킹
4. Obsidian: `swing-procedure/screener/YYYY-MM-DD.md`
5. Slack: Top 3 요약

> **전제**: `EARNINGS_DIR/finviz_output/` 폴더에 `{TICKER}.txt` 파일이 사전 준비되어 있어야 합니다.

---

#### `run_buy_pipeline.py` — 매수 분석 (14단계 전체)

```powershell
# 기본 실행 (summary의 전체 종목 대상)
python scripts/run_buy_pipeline.py
```

**처리 순서:** Step 0~13 (데이터 로딩 → 레짐 → 필터 → 기술분석 → 뉴스LLM → DA → 옵션검증 → 시나리오 → 포트폴리오 → 랭킹 → Requeue → 저장 → 알림)

#### `run_ticker.py` — 특정 종목 단일/복수 매수 분석

```powershell
# 단일 종목
python scripts/run_ticker.py TSLA

# 복수 종목
python scripts/run_ticker.py AAPL MSFT NVDA
```

> 인자 없이 실행하면 사용법을 출력합니다. `force_refresh=True` 고정 (캐시 무시).

**출력:**
- Obsidian: `swing-procedure/buy/YYYY-MM-DD.md`
- Slack: 최종 매수 판단 + 종목별 확신도

---

#### `run_sell_pipeline.py` — 매도 분석 (14단계 전체)

```powershell
# 실제 실행 (positions.md 포지션 → Obsidian 저장 + Slack 알림)
python scripts/run_sell_pipeline.py --real

# DRY-RUN (기본 — 실제 저장 없음, AMD/MU 테스트 포지션 자동 주입)
python scripts/run_sell_pipeline.py
```

> **주의**: `--real` 없이 실행하면 실제 Obsidian/Slack에 아무것도 저장/전송되지 않습니다.  
> `--real` 모드에서 `positions.md`에 포지션이 없으면 자동 종료됩니다.

**처리 순서:** Step 0~13 (포지션 로딩 → 건전성 → 레짐비교 → 기술분석 → thesis검증 → DA → IV Crush → 시나리오 → 부분청산 → 포트폴리오 → 최종결정LLM → 저장 → 복기 → 알림)

---

#### `run_requeue.py` — Requeue 파이프라인

```powershell
# requeue.json에서 ready 종목 → Buy Pipeline 재실행
python scripts/run_requeue.py

# 항목 추가 (waiting 상태로 등록)
python scripts/run_requeue.py add NVDA --reason "눌림목 회복 대기"
python scripts/run_requeue.py add TSLA  # reason 생략 시 "수동 등록"

# 목록 조회 (waiting/ready/completed/failed)
python scripts/run_requeue.py list
```

**처리 순서:** `requeue.json` 조회 → ready 전환 확인 → ready 종목 Buy Pipeline 재실행 → Slack 알림

---

### 로그 모니터링

파이프라인 실행 중 실시간 로그 확인:

```powershell
# 실시간 감사 로그 (단계 진행 상황)
Get-Content "C:\MCP\Swing\shared\logs\audit_$(Get-Date -Format 'yyyy-MM-dd').json" -Wait -Tail 10

# 실행 스냅샷 확인 (마지막 완료 단계)
Get-ChildItem "C:\MCP\Swing\shared\state\snapshots\" | Sort-Object LastWriteTime -Desc | Select-Object -First 5
```

---

## 4. 로컬 데이터 파일 및 경로

모든 경로는 `.env`에서 설정합니다. 기본값은 구글 드라이브 마운트 기준입니다.

| 변수명 | 기본 경로 | 내용 | 데이터 타입 |
|--------|-----------|------|-------------|
| `SUMMARY_DIR` | `R:\내 드라이브\마켓 수치` | 매수 요약 JSON 파일 디렉토리 | JSONL (`summary_*.json`) |
| `FINVIZ_FILE` | `Y:\내 드라이브\어닝\finviz_all_rows.txt` | Finviz 전체 종목 ROW 블록 | 텍스트, ROW 블록 형식 |
| `EARNINGS_DIR` | `Y:\내 드라이브\어닝` | 어닝 분석 마크다운 + finviz_output + kavout_output | `.md` + `finviz_output/` + `kavout_output/` |
| `DATA_DIR` | `Y:\내 드라이브\Data` | Kavout CSV | CSV (`kavout_*.csv`) |
| `POSITIONS_FILE` | `C:\lian\positions.md` | 현재 포지션 | 마크다운 (YAML 블록) |
| `WATCHLIST_FILE` | `C:\lian\Swing\watchlist.md` | 관찰 종목 목록 | 마크다운 테이블 |
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

> Kavout 스크리너는 이 CSV에서 **티커 목록과 K-Score만** 사용합니다.  
> 가격·기술지표·펀더멘털 데이터는 **Yahoo Finance API에서 실시간 수집**합니다.

### kavout_output/*.txt 파일 형식 (per-ticker 파이낸셜 데이터)

```
경로: Y:\내 드라이브\어닝\kavout_output\{TICKER}.txt
```

Finviz 스타일의 줄별 key-value 형식. 섹션: SNAPSHOT TABLE / INCOME STATEMENT / BALANCE SHEET / CASH FLOW.

```
Market Cap
85.2B
EPS/Sales Surpr.
12.5%/3.2%
```

> **역할**: Yahoo Finance API가 채우지 못한 필드의 fallback 소스.  
> 실시간 시세는 API 우선, 파이낸셜 구조 데이터(ROE, FCF 등)는 kavout_output으로 보완합니다.

---

## 5. 외부 API 및 데이터 소스

| 서비스 | 엔드포인트 / 라이브러리 | 인증 | 역할 | 필수 여부 |
|--------|----------------------|------|------|-----------|
| **Yahoo Finance** | `yfinance` 라이브러리 | **없음 (무료)** | Kavout 스크리너 — 가격·기술·펀더멘털 실시간 수집 | Kavout 스크리너 필수 |
| **Obsidian REST API** | `https://127.0.0.1:27124` | `OBSIDIAN_API_KEY` Bearer | 분석 노트 저장/읽기 | 필수 (FATAL) |
| **Slack Bot API** | `https://slack.com/api/` | `SLACK_BOT_TOKEN` Bearer | 알림 전송 | 선택 (없으면 비활성화) |
| **OpenRouter API** | `https://openrouter.ai/api/v1/chat/completions` | `OPENROUTER_API_KEY` | LLM 호출 | 필수 (뉴스 분석, 매도 결정) |
| **DuckDuckGo Search** | 내부 (`ddgs` 라이브러리) | 없음 | 실시간 뉴스 검색 | 선택 (없으면 스킵) |
| **Brave Search API** | `https://api.search.brave.com/` | `BRAVE_API_KEY` | 보완 뉴스 검색 | 선택 |
| **RSS Feeds** | 설정 파일 `shared/rss_feeds.json` | 없음 | 시장/종목 뉴스 | 선택 |

### Yahoo Finance (yfinance) — 수집 필드 상세

`core/api_fetcher.py`가 `yfinance.Ticker` 를 통해 수집하는 데이터:

| 분류 | 필드 | yfinance 소스 |
|------|------|--------------|
| **가격** | `price`, `change_pct` | `Ticker.info["currentPrice"]` |
| **기술지표** | `rsi14`, `rel_volume`, `sma20_pct`, `sma50_pct`, `sma200_pct` | `Ticker.history(period="1y")` → 직접 계산 |
| **52주 위치** | `w52_high_pct`, `w52_low_pct` | `Ticker.info["fiftyTwoWeekHigh/Low"]` |
| **밸류에이션** | `forward_pe`, `peg`, `beta`, `target_price`, `recom` | `Ticker.info` |
| **마진** | `gross_margin_pct`, `op_margin_pct`, `profit_margin_pct` | `Ticker.info["grossMargins" 등]` (decimal → %) |
| **성장률** | `revenue_growth_yoy`, `net_income_growth_yoy` | `Ticker.info["revenueGrowth" 등]` (decimal → %) |
| **손익** | `revenue_ttm`, `gross_profit_ttm`, `net_income_ttm` | `Ticker.info["totalRevenue" 등]` (raw → M USD) |
| **서프라이즈** | `eps_surprise_pct` | `Ticker.earnings_history` |
| **공매도** | `short_float_pct` | `Ticker.info["shortPercentOfFloat"]` |
| **시가총액** | `market_cap` | `Ticker.info["marketCap"]` (티어 분류용) |

### OpenRouter LLM 폴백 체인 (우선순위 순)

태스크별 지정 모델 실패 시 아래 순서로 자동 폴백됩니다. 비워두면 전체 체인을 사용합니다.

```
1. LLM_PRIMARY_MODEL    현재 설정: nvidia/nemotron-3-super-120b-a12b:free
2. LLM_FALLBACK_MODEL   현재 설정: meta-llama/llama-3.3-70b-instruct:free
3. LLM_FALLBACK_2       현재 설정: qwen/qwen3-coder:free
4. LLM_FALLBACK_3       현재 설정: openai/gpt-oss-120b:free
```

> **비용 정책**: `buy_step3_research` 실패 시에만 폴백 체인이 순서대로 시도됩니다.  
> 매도/라우팅 태스크는 폴백 체인이 모두 무료이므로 유료 청구가 발생하지 않습니다.

### 태스크별 LLM 모델 매핑

| 태스크 | `.env` 변수 | 현재 설정값 | 비고 |
|--------|-------------|------------|------|
| 뉴스·리서치 합성 (Buy Step 5) | `LLM_MODEL_BUY_RESEARCH` | `deepseek/deepseek-v4-pro` | 50개 뉴스 합성, 8192 토큰 출력 |
| 기술 내러티브 생성 (Buy Step 5) | `LLM_MODEL_BUY_TECH_NARRATIVE` | `deepseek/deepseek-v4-flash` | 6단락 기술분석 내러티브 |
| 리서치 증분 업데이트 | `LLM_MODEL_BUY_RESEARCH_UPDATE` | `deepseek/deepseek-v4-flash` | 신규 기사만 합성 |
| 어닝콜 분석 (Screener) | `LLM_MODEL_KAVOUT_EARNINGS` | `deepseek/deepseek-v4-flash` | 4값 분류, ~70 토큰 |
| 포지션 건전성 (Sell Step 4) | `LLM_MODEL_SELL_HEALTH` | `nvidia/nemotron-3-super-120b-a12b:free` | |
| 환경 이벤트 리스크 (Sell Step 5) | `LLM_MODEL_SELL_ENV` | `nvidia/nemotron-3-super-120b-a12b:free` | |
| NL 라우팅 | `LLM_MODEL_NL_ROUTING` | `nvidia/nemotron-3-super-120b-a12b:free` | |

---

## 6. MCP 서버 목록 및 도구 등록 (선택적 연동)

> MCP 서버는 Roo Code / Claude Desktop 연동 시에만 사용합니다.  
> **일반 운영은 §3 CLI 직접 실행**을 사용하세요.

### 6.1 swing_mcp — 10개 도구

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

### 6.2 screener_mcp — 2개 도구

| 도구명 | 설명 | 주요 파라미터 |
|--------|------|--------------|
| `run_fundamental_screen` | Finviz 기반 3단계 펀더멘털 스크리닝 실행 | `execution_id?`, `force_refresh?`, `top_n?` |
| `screener_health_check` | 데이터 파일·연결 상태 확인 | (없음) |

### 6.3 kavout_mcp — 2개 도구

| 도구명 | 설명 | 주요 파라미터 |
|--------|------|--------------|
| `run_kavout_screen` | Kavout AI + Yahoo Finance API 스크리닝 실행 | `execution_id?`, `force_refresh?`, `top_n?` |
| `kavout_health_check` | Kavout 데이터·연결 상태 확인 | (없음) |

### 6.4 MCP CLI 연동 (Roo Code 사용 시 참고)

MCP 서버는 백그라운드 실행 방식으로 동작합니다. 도구 호출 즉시 `{"status": "ok", "message": "... 추가 작업 불필요"}` 를 반환하고, 실제 파이프라인은 백그라운드(`asyncio.create_task`)에서 실행됩니다. 결과는 Obsidian + Slack으로 전달됩니다.

---

## 7. 매수 파이프라인 (BuyPipeline) — Step 0~13

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
| **외부 호출** | `ObsidianClient.ping()` → `GET https://127.0.0.1:27124/` |
| **출력** | 성공 시 다음 단계 진행; 실패 시 Slack E101 오류 전송 후 `RuntimeError` |

---

### Step 1 — 데이터 로딩 (load)

**파일**: `buy_steps.py:step_1_load()` (line ~150)

| 요소 | 내용 |
|------|------|
| **입력** | `SUMMARY_DIR`, `FINVIZ_FILE`, `EARNINGS_DIR`, `DATA_DIR`, `POSITIONS_FILE` |
| **처리 로직** | 5가지 데이터를 순차 로딩: summary JSON → finviz txt → earnings md → finviz_output → kavout CSV → positions.md → watchlist |
| **조건 분기** | 각 파일 로딩 실패 시 개별 degraded; summary 없으면 FATAL |
| **데이터 변환** | `parse_summary()` → `SummaryData`; `parse_finviz()` → `list[FinvizRow]`; `parse_earnings()` → `list[EarningsAnalysis]`; `parse_kavout()` → `dict[str, KavoutData]`; `parse_positions()` → `list[Position]`; `parse_finviz_detail()` → `dict[str, FinvizDetail]` |
| **출력** | `ctx.summary_data`, `ctx.finviz_rows`, `ctx.earnings_list`, `ctx.kavout_data`, `ctx.positions`, `ctx.finviz_detail`, `ctx.watchlist` |

**투자 로직**: 동일 날짜 summary가 여러 개일 경우 `load_latest_summary()`로 가장 최근 파일 자동 선택. 어닝 분석은 메인 파일 + today 파일 병합하여 당일 발표분 누락 방지.

---

### Step 2 — 시장 레짐 분석 (regime)

**파일**: `buy_steps.py:step_2_regime()` → `core/analysis.py:analyze_market_regime()`

| 요소 | 내용 |
|------|------|
| **입력** | `ctx.summary_data` (SPY/QQQ/VIX/ADX 지표) |
| **처리 로직** | 결정론적 규칙 기반: ADX ≥ 25 → trend_strength.pass; VIX ≤ 20 → volatility.pass; SPY + QQQ 모두 20MA 위 → long_call 허용 |
| **조건 분기** | `regime_status == "unfavorable"` → 경고 로그; `allowed_direction`에 따라 Step 3 필터 적용 방식 결정 |
| **데이터 변환** | `SummaryData` → `MarketRegime` |
| **출력** | `ctx.regime: MarketRegime` (regime_status, allowed_direction, risk_factors, regime_confidence) |

**수정 포인트**: ADX 기준 → `shared/strategy.py:REGIME_ADX_STRONG` (현재 25) / VIX 기준 → `REGIME_VIX_FAVORABLE` (현재 20)

---

### Step 3 — 필터링 (filters)

**파일**: `buy_steps.py:step_3_filter()` → `core/analysis.py:apply_filters()`

| 필터 | 코드 | 조건 | 하드 스탑? |
|------|------|------|:---:|
| F1 | RVOL_LOW | `avg_volume_ratio < RVOL_MIN (1.5)` | No |
| F2 | DIRECTION_MISMATCH | 방향 불일치 (long_call에 bearish 종목 등) | No |
| F3 | LIQUIDITY_LOW | `price < PRICE_TRADE_MIN ($20)` or `market_cap < 10B` | No |
| F4 | DELISTING_RISK | 상장폐지 위험 키워드 감지 | **Yes** |
| F5 | EARNINGS_IMMINENT | 어닝 7일 이내 + IV 급등 위험 | No |
| F6 | SECTOR_OVERWEIGHT | 동일 섹터 3개 초과 | No |
| F7 | DUPLICATE | 동일 종목 중복 | No |

**출력**: `ctx.passed_tickers`, `ctx.filter_failures: dict[str, list[str]]`

**투자 로직**: F1 RVOL 탈락 종목은 Step 11에서 Requeue 등록 → 나중에 조건 충족 시 자동 재분석. 기회 손실 최소화.

---

### Step 4 — 기술 분석 (technical)

**파일**: `buy_steps.py:step_4_technical()` → `core/analysis.py:calculate_technical_score()`

```
raw_score = MA(25) + ADX(25) + RSI(25) + RVOL(25)  →  최대 100
final_score = raw_score - devil's_advocate_deductions
signal_count = 각 지표 통과 수 (최대 8)
```

**Devil's Advocate 자동 차감**:
- RSI > 80 + 52주 고점 98%+ : -10점
- 거래량 미동반 (OBV 약화) : -5점
- 볼린저밴드 상단 돌파 : -5점
- 52주 고점 5% 이내 : -5점
- 당일 이상 급등 : -5점

**출력**: `ctx.technical_scores: dict[str, TechnicalScore]`

---

### Step 5 — 뉴스/리서치 분석 (research)

**파일**: `buy_steps.py:step_5_research()`

| 요소 | 내용 |
|------|------|
| **처리 로직** | RSS 수집 → DDG 3개 쿼리 병렬 → Brave Search → **LLM 호출 ①** 뉴스 감성 합성 → **LLM 호출 ②** 기술 내러티브 생성 |
| **외부 호출** | `call_ddg_search()`, `call_brave_search()`, `analyze_with_llm()` × 2 → OpenRouter |
| **출력 ①** | `ctx.sentiment_results: dict[str, dict]` — overall_sentiment, bull_thesis, bear_thesis, conviction_delta 등 (TYPE 1 소스) |
| **출력 ②** | `ctx.tech_narratives: dict[str, dict]` — 6단락 기술분석 내러티브 (TYPE 3 소스) |

**LLM 호출 구분:**

| 호출 | 템플릿 | 모델 | 출력 토큰 | 역할 |
|------|--------|------|-----------|------|
| ① research | `buy_step3_research` | `deepseek/deepseek-v4-pro` | 최대 8,192 | 50개 뉴스 → 7섹션 감성 분석 JSON |
| ② tech_narrative | `buy_step3b_technical_narrative` | `deepseek/deepseek-v4-flash` | 최대 4,096 | 기술지표 → 6단락 자연어 내러티브 |

> **max_tokens 설정 이유**: `buy_step3_research`는 7개 JSON 섹션(bull_thesis, bear_thesis 등)을 생성하므로 기본 4,096으로는 JSON이 잘려 파싱 실패합니다. `analyze_with_llm()` 내부의 `_TEMPLATE_MAX_TOKENS` 딕셔너리로 템플릿별 한도를 분리 관리합니다.

---

### Step 6 — Devil's Advocate 검토 (devils)

**파일**: `buy_steps.py:step_6_devils()`

| 조건 | 차감 |
|------|------|
| IV Crush 위험 (어닝 7일 이내 + IVR > 60) | -15점 |
| Thesis 충돌 (sentiment NEGATIVE + direction 반대) | -20점 |
| 내부자 대량 매도 (`insider_trans_pct < -20%`) | -10점 |
| 최근 EPS 미스 (`eps_surprise_pct < -5%`) | -5점 |

40점 미만 종목은 자동 탈락.

---

### Step 7 — 옵션 진입 선택 (options)

**파일**: `buy_steps.py:step_7_options()` → `core/analysis.py:calculate_greeks()`, `validate_option()`

**옵션 유효성 기준**:

| 조건 | 기준값 | 변경 위치 |
|------|--------|-----------|
| Delta 범위 | 0.40 ~ 0.70 | `strategy.py:DELTA_MIN`, `DELTA_MAX` |
| IVR 최대 | 70 | `strategy.py:IVR_MAX` |
| OI 최소 | 500 | `strategy.py:OI_MIN` |
| 스프레드 최대 | 5% | `strategy.py:SPREAD_MAX_PCT` |
| DTE 최소 | 21일 | `strategy.py:DTE_MIN` |

**출력**: `ctx.option_validity: dict[str, OptionValidity]`, `ctx.selected_options`

---

### Step 8 — 시나리오 계획 (scenarios)

**파일**: `buy_steps.py:step_8_scenarios()` → `core/analysis.py:calculate_scenario()`

**시나리오 확률 테이블**:

| signal_count | 강세 | 기본 | 약세 |
|-------------|:----:|:----:|:----:|
| ≥ 6 | 45% | 35% | 20% |
| 4~5 | 35% | 40% | 25% |
| < 4 | 25% | 40% | 35% |

**손절/익절 기준**: 손절 ×0.5 / 1차 ×1.5 / 2차 ×2.0 / 3차 ×2.5 / 트레일링 스탑 고점 대비 -20%

**출력**: `ctx.scenarios: dict[str, Scenario]`

---

### Step 9 — 포트폴리오 리스크 확인 (portfolio)

**파일**: `buy_steps.py:step_9_portfolio()` → `core/analysis.py:check_portfolio_exposure()`

섹터 집중도 / 방향 편향 / 전체 Greeks 집계 / 개별 리스크 경고 생성 → Slack `send_risk_alert()` (경고 시)

**출력**: `ctx.portfolio_exposure: PortfolioExposure`

---

### Step 10 — 최종 순위 결정 (ranking)

**파일**: `buy_steps.py:step_10_ranking()` → `core/analysis.py:calculate_confidence()`

**확신도 점수 계산**:
```
conviction = 0.4×trend + 0.2×news + 0.3×thesis + 0.1×execution

trend     = TechnicalScore.final_score / 100
news      = sentiment_score + conviction_delta
thesis    = 1.0 if signal_count >= 6 else 0.7 if >= 4 else 0.3
execution = option_validity 점수 (delta/IVR/OI/spread/DTE)
```

**판단 기준**: conviction ≥ 0.70 → "진입" / 0.50~0.70 → "관찰" / < 0.50 → "보류"

**출력**: `ctx.rankings: list[FinalRanking]` (rank, ticker, action, conviction, rationale)

---

### Step 11 — Requeue 등록 (requeue)

**파일**: `buy_steps.py:step_11_requeue()`

F1(RVOL_LOW), F3(LIQUIDITY_LOW) 탈락 종목을 `requeue.json`에 등록. threshold(RVOL, price_min) 함께 저장하여 조건 충족 시 자동 재분석.

**출력**: `shared/state/requeue.json` 업데이트

---

### Step 12 — 저장 (storage)

**파일**: `buy_steps.py:step_12_storage()`, `core/obsidian.py`

- Obsidian 매수 노트 저장: `swing-procedure/notes/buy/YYYY-MM-DD.md`
- 탈락 종목 별도 노트: `swing-procedure/rejected/{ticker}_{date}.md`
- `watchlist.md` 갱신
- "진입" 종목 → `positions.md`에 추가

**매수 노트 구조 (TYPE 1~5):**

각 종목별로 다음 5개 섹션이 순서대로 생성됩니다.

| 섹션 | 제목 | 소스 | 주요 내용 |
|------|------|------|-----------|
| **TYPE 1** | 📰 News Sentiment | `ctx.sentiment_results` (LLM ①) | 전반 심리(POSITIVE/MIXED/NEGATIVE), 신뢰도, Key Drivers 표, Critical Events, Bull/Bear 분석, Verdict |
| **TYPE 2** | 🏢 Fundamental | `ctx.finviz_detail`, `ctx.kavout_data` | Kavout K-Score, 재무지표(Forward PE, EPS 서프라이즈, 매출성장률), 어닝콜 분석(guidance_direction, mgmt_tone) |
| **TYPE 3** | 📈 Technical Analysis | `ctx.technical_scores` + `ctx.tech_narratives` (LLM ②) | 실제 지표값 테이블(RSI/ADX/SMA/BB), **6단락 기술 내러티브** (추세·이동평균 / 모멘텀 / 추세강도·변동성 / 지지저항 / 진입타이밍 / 리스크 시나리오), Multi-Timeframe 표 |
| **TYPE 4** | 📊 Swing Analysis | `ctx.scenarios` | 듀얼 호라이즌 표(Near-Term 1~5일 / Swing 5~15일), 손절/T1/T2/T3 프리미엄, R/R 비율, 추천 호라이즌 |
| **TYPE 5** | 🎯 Buy & Sell | 종합 계산 | **3D 복합 스코어카드**, 실행 계획(Strike/Expiry/Greeks), 거래 조건, **행동 계획**, 시나리오 확률표 |

**TYPE 5 — 3D 복합 스코어카드 (Weighted Composite):**

```
기술적 (Technical) × 40% + 거시 (Macro/Regime) × 30% + 심리 (Sentiment) × 30%
= 복합 점수 (0~100)

판정 기준:
  ≥ 70  → 🟢 진입 가능
  ≥ 55  → 🟡 관찰 대기
  ≥ 40  → 🟠 보류
  < 40  → 🔴 탈락
```

**TYPE 5 — 행동 계획 (Action Plan):**

- **보유자**: Stop 유지 라인 / T1 도달 시 50% 부분 익절 / 잔여 T2 향해 트레일링 스탑 / Time Stop
- **미보유자**: 3단계 판단 (컨빅션+신호 모두 충족 → 진입 가능 / 부분 충족 → 조건부 대기 / 미충족 → 보류)
- **무효화 조건**: risk_factors 상위 3개 자동 표시

---

### Step 13 — 알림 (notify)

**파일**: `buy_steps.py:step_13_notify()`

Slack 매수 결과 전송 (Block Kit): 균형순위 + 공격순위 + 하락 위험 종목 + Obsidian 링크

---

## 8. 매도 파이프라인 (SellPipeline) — Step 0~13

파일: `orchestrator/steps/sell_steps.py` (SellSteps 클래스)

> **매도 파이프라인에 없는 기능**: 옵션 유효성 검증, 필터링, 확신도 점수, Requeue 등록  
> 이유: 이미 보유 중인 포지션 대상이므로 매수 판단 기준 불필요. 매도 판단은 urgency/P&L/thesis로 처리.

---

### Step 0 — 환경 + 포지션 로딩 (env)

**파일**: `sell_steps.py:step_0_env()`

Obsidian ping → `parse_positions()` → 5가지 데이터 로딩 (summary/finviz/earnings/finviz_detail/kavout)

`target_tickers` 지정 시 해당 포지션만 필터링. Obsidian 실패 → FATAL.

---

### Step 1 — 포지션 건전성 점검 (health)

**파일**: `sell_steps.py:step_1_health()`

현재 프리미엄 → 트레일링 스탑 고점 갱신 → Greeks 계산 → P&L 귀인(Delta/Theta/Vega) → DTE 긴급도 → 무효화 조건 점검

**DTE 긴급도**: ≤ 7일 → 위급 / 8~14 → 주의 / 15~21 → 보통 / 21+ → 안정

**P&L 귀인**:
- `delta_pnl = delta × (current_price - entry_price) × 100 × contracts`
- `theta_pnl = theta × days_held × 100 × contracts`
- `vega_pnl = total_pnl - delta_pnl - theta_pnl` (잔차법)

**출력**: `ctx.sell_health: dict[str, dict]`

---

### Step 2 — 시장 레짐 비교 (regime)

**파일**: `sell_steps.py:step_2_regime()`

현재 레짐 vs 진입 시 레짐 비교 → 역전 감지.

- 롱콜 + bearish 레짐 → `REGIME_REVERSED` → Step 7에서 PARTIAL_EXIT 자동 권고
- 롱풋 + bullish 레짐 → `REGIME_REVERSED`

**출력**: `ctx.sell_regime_flags: dict[str, str]`

---

### Step 3 — 기술 분석 + 뉴스 감성 (technical)

**파일**: `sell_steps.py:step_3_technical()`

보유 포지션 기술 점수 계산 → DDG 뉴스 검색 (2쿼리) → LLM 감성 분석

**출력**: `ctx.technical_scores`, `ctx.sentiment_results`

---

### Step 4 — Thesis 검증 (thesis)

**파일**: `sell_steps.py:step_4_thesis()`

`sell_step1_health` LLM 템플릿으로 무효화 조건 점검. LLM 실패 시 Step 1 결정론적 결과 유지 (Graceful Degradation).

**출력**: `ctx.sell_thesis: dict[str, dict]`

---

### Step 5 — Devil's Advocate (devils)

**파일**: `sell_steps.py:step_5_devils()`

`sell_step2_environment` LLM 템플릿으로 이벤트 리스크 + IV Crush 리스크 분석. LLM 실패 → IVR 임계값 기반 결정론적 폴백.

**출력**: `ctx.sell_devils: dict[str, dict]` (event_judgment, iv_crush_risk, recommendation)

---

### Step 6 — IV Crush 분석 (options)

**파일**: `sell_steps.py:step_6_options()`

IVR > `SELL_IVR_CRUSH_THRESHOLD` + 어닝 DTE 이내 → Slack IV Crush 경고 전송.

**투자 로직**: 고IVR + 어닝 없음 = Vega 수혜 기회. 어닝 타이밍 확인이 핵심.

**출력**: `ctx.sell_iv_warnings: list[str]`

---

### Step 7 — 행동 시나리오 결정 (action)

**파일**: `sell_steps.py:step_7_action()`

**7개 우선순위 규칙**:
1. 트레일링 스탑 발동 → **FULL_EXIT**
2. 청산 권고 신호 / 스탑로스 도달 → **FULL_EXIT**
3. 150% 수익 달성 → **FULL_EXIT**
4. 100% 수익 or 레짐 역전 → **PARTIAL_EXIT** (75%)
5. 50% 수익 or 이벤트 청산 유리 → **PARTIAL_EXIT** (50%)
6. 추세 미확인 → **PARTIAL_EXIT** (33%)
7. 그 외 → **HOLD**

**Finviz 추가 플래그**: 애널리스트 매도 의견 / EPS 미스 주의 / 내부자 매도 주의 / 목표주가 근접

**출력**: `ctx.sell_preliminary: list[dict]`, `ctx.scenarios` 갱신

---

### Step 8 — 부분 청산 처리 (partial)

**파일**: `sell_steps.py:step_8_partial()`

PARTIAL_EXIT 포지션 처리 → trailing stop 재설정.

**청산 비율** (`shared/strategy.py`):
- 레짐 역전 / DTE 주의 → 75% (`SELL_PARTIAL_REGIME_RATIO`)
- 수익 확정 → 50% (`SELL_PARTIAL_PROFIT_RATIO`)
- 손실 헷지 → 33%

---

### Step 9 — 포트폴리오 재확인 (portfolio)

잔여 포지션 집계 + 총 투자금 재계산 → 스냅샷 저장.

---

### Step 10 — 최종 행동 결정 (decision)

**파일**: `sell_steps.py:step_10_decision()`

ROLL 조건 확인 (FULL_EXIT + DTE ≤ 7 + 추세 확인 → 만기 35일 연장) → `sell_step3_decision` LLM 최종 결정 → `SellDecision` 생성.

LLM 실패 시 규칙 기반 예비 결정 유지.

**출력**: `ctx.sell_decisions: list[SellDecision]` (action, contracts_to_close, realized_pnl, unrealized_pnl, rationale, urgency)

---

### Step 11 — 저장 (storage)

Obsidian 매도 노트 저장 (`swing-procedure/sell/YYYY-MM-DD.md`) + 포지션 상태 캐시 저장.

---

### Step 12 — FULL_EXIT 복기 (review)

FULL_EXIT 종목 대상 `sell_step4_review` LLM 분석 → 교훈, thesis 정확도, 개선점 스냅샷 저장.

---

### Step 13 — 알림 (notify)

매도 결과 Slack 전송. FULL_EXIT + 손실 포지션 → Slack `send_risk_alert()` (STOP_LOSS_TRIGGERED).

---

## 9. Requeue 파이프라인 — Step 0~4

파일: `orchestrator/pipelines.py` (RequeuePipeline 클래스)

| Step | 역할 | 핵심 로직 |
|------|------|-----------|
| Step 0 | 환경 확인 | Obsidian ping |
| Step 1 | Summary 로드 | `load_latest_summary()` |
| Step 2 | ready 조건 확인 | `requeue_check_ready(summary_data)` → IVR/가격/RVOL 조건 체크 → ready 전환 |
| Step 3 | ready 종목 BuyPipeline 실행 | `BuyPipeline(target_tickers=ready_tickers)` |
| Step 4 | Slack 알림 | `send_requeue_alert(ready_items)` |

**Requeue 조건 확인**: IVR이 임계값 이하 / 주가가 최소가 이상 / RVOL이 최소 기준 이상 → 모두 충족 시 `status = "ready"`.

**CLI 사용법**: [§3 run_requeue.py 참조](#run_requeuepy--requeue-파이프라인)

---

## 10. 펀더멘털 스크리너 (screener_mcp)

파일: `servers/screener_mcp/server.py`, `core/fundamental_screener.py`, `core/earnings_analyzer.py`

**CLI**: `python scripts/run_screener.py`

### 3단계 파이프라인

| 단계 | 역할 | 입력 → 출력 |
|------|------|------------|
| Step 1 | Finviz 파싱 | `finviz_output/*.txt` (로컬 파일) → `dict[str, FinvizDetail]` |
| Step 2 | 어닝콜 LLM 분석 | `어닝 분석.md` → `dict[str, EarningsCallAnalysis]` (가이던스 방향, 경영진 톤, catalyst_strength) |
| Step 3 | 점수화 + 랭킹 | FinvizDetail + EarningsCallAnalysis → `list[FundamentalScoreResult]` |

### 점수 계산 구조

```
Momentum Score (0~100):
  = RSI(25%) + RelVolume(25%) + 52주위치(25%) + SMA추세(25%)

Fundamental Score (0~100):
  = 매출성장(40%) + EPS서프라이즈(25%) + 영업이익률(35%)

Catalyst Score (0~100):  [어닝콜 있을 때만]
  = 가이던스(60%) + 경영진톤(40%)

Final Score:
  - Catalyst 있음: 0.35×M + 0.40×F + 0.25×C
  - Catalyst 없음: 0.45×M + 0.55×F
```

> **EPS 서프라이즈 채택 이유**: GAAP 순이익성장률은 M&A·일회성 비용으로 왜곡 가능 (예: MRVL GAAP -81% vs Non-GAAP +33%). EPS 서프라이즈는 Non-GAAP 컨센서스 대비 측정이므로 실제 어닝 품질을 더 정확하게 반영합니다.

> **SMA 추세 추가**: SMA200(40%) + SMA50(35%) + SMA20(25%) 가중 평균. 장기 추세에 더 높은 가중치 부여.

### RSI 점수 기준

| RSI 범위 | 점수 | 해석 |
|----------|:----:|------|
| 50~70 | 100 | 이상적 (모멘텀 지속) |
| 40~50 | 65 | 적정 |
| 70~80 | 55 | 과매수 근접 |
| < 40 | 30 | 과매도 (약세) |
| > 80 | 20 | 극단적 과매수 |

### 출력

- Obsidian: `swing-procedure/screener/YYYY-MM-DD.md`
- Slack: Top 3 요약 + 나머지 Top 10

---

## 11. Kavout AI 스크리너 (kavout_mcp)

파일: `servers/kavout_mcp/server.py`, `scripts/run_kavout_screener.py`, `core/api_fetcher.py`, `core/parsers.py`

**CLI**: `python scripts/run_kavout_screener.py`

### screener_mcp vs kavout_mcp 비교

| 항목 | screener_mcp | kavout_mcp |
|------|-------------|------------|
| **유니버스 소스** | `finviz_output/*.txt` (로컬 파일) | `kavout_*.csv` (최신 자동 탐색) |
| **시세·지표 수집** | 사전 준비된 파일 파싱 | **Yahoo Finance API 실시간 수집** |
| **보조 파이낸셜** | — | `kavout_output/*.txt` (pre-market 파이낸셜 보완) |
| **API 키 필요** | 없음 | 없음 (yfinance 무료) |
| 어닝 파일 | `어닝_분석.md` | `K어닝 분석.md` |
| Obsidian 경로 | `screener/{date}.md` | `screener/kavout/{date}.md` |
| Kavout K-Score | 없음 | 노트 + Slack에 K-Score 표시 |
| 순위 방식 | 전체 통합 | **시가총액 티어별 독립 랭킹** |

### 4단계 파이프라인

| 단계 | 역할 | 입력 → 출력 |
|------|------|------------|
| Step 1 | Kavout CSV + Yahoo Finance API 수집 | `kavout_*.csv` → 티커 목록 → `fetch_finviz_details_bulk()` → `dict[str, FinvizDetail]` |
| Step 1b | kavout_output 병합 | `kavout_output/*.txt` → `parse_kavout_output()` → API 누락 필드 보완 |
| Step 2 | K어닝 분석.md LLM 분석 | `K어닝 분석.md` → `dict[str, EarningsCallAnalysis]` + raw `EarningsAnalysis` 객체 보존 |
| Step 3 | 점수화 + 티어 분류 + 랭킹 | FinvizDetail + EarningsCallAnalysis → `ScreenerResult` + 티어별 `RankedStock` 리스트 |
| Step 4 | 출력 | Obsidian 노트 저장 + Slack 티어별 Top 3 전송 |

### Step 1 상세 — Yahoo Finance API 수집 (`core/api_fetcher.py`)

```
kavout_*.csv
  └→ 티커 목록 추출 (약 41개)
       └→ fetch_finviz_details_bulk(tickers)
            ├─ asyncio.Semaphore(5) — 최대 5개 동시 실행
            └─ 각 티커: asyncio.to_thread(fetch_finviz_detail)
                 ├─ yf.Ticker(ticker).info       → 펀더멘털 필드 + market_cap
                 └─ yf.Ticker(ticker).history("1y") → OHLC → 기술지표 계산
```

**수집 필드 전체** (`FinvizDetail`):

| 분류 | 필드 | 계산/소스 |
|------|------|----------|
| 가격 | `price`, `change_pct` | `info["currentPrice"]`, 전일 대비 계산 |
| 기술 | `rsi14` | OHLC 1년치 → RSI(14) 직접 계산 |
| 기술 | `rel_volume` | 최근 거래량 / 직전 20일 평균 |
| 기술 | `sma20_pct`, `sma50_pct`, `sma200_pct` | (현재가 - SMAn) / SMAn × 100 |
| 52주 | `w52_high_pct`, `w52_low_pct` | (현재가 - 52W고/저) / 52W고/저 × 100 |
| 밸류 | `forward_pe`, `peg`, `beta` | `info["forwardPE/trailingPegRatio/beta"]` |
| 애널 | `target_price`, `recom` | `info["targetMeanPrice/recommendationMean"]` (1=강매수, 5=매도) |
| 마진 | `op_margin_pct`, `profit_margin_pct` | `info["operatingMargins/profitMargins"]` × 100 |
| 성장 | `revenue_growth_yoy`, `net_income_growth_yoy` | `info["revenueGrowth/earningsGrowth"]` × 100 |
| 손익 | `revenue_ttm`, `net_income_ttm` | `info["totalRevenue/netIncomeToCommon"]` ÷ 1M |
| 서프 | `eps_surprise_pct` | `Ticker.earnings_history` 최근 분기 계산 |
| **시가총액** | **`market_cap`** | `info["marketCap"]` (티어 분류 1순위 소스) |

**병렬 처리**: `asyncio.Semaphore(5)` + `asyncio.to_thread` → 41개 종목 약 15~20초 완료

### Step 1b 상세 — kavout_output 병합

```
kavout_output/*.txt (per-ticker)
  └→ parse_kavout_output(kavout_output_dir) → dict[str, FinvizDetail]
       └→ _parse_kavout_output_file(ticker, content)
            ├─ SNAPSHOT TABLE → kv dict 파싱
            │    ├─ "Market Cap" → market_cap (시가총액 fallback)
            │    └─ "EPS/Sales Surpr." → eps_surprise_pct, revenue_growth_yoy
            ├─ INCOME STATEMENT → YoY 성장률 계산
            ├─ BALANCE SHEET → ROE
            └─ CASH FLOW → FCF
```

**API vs kavout_output 역할 분담**:

| 필드 유형 | 우선 소스 | 이유 |
|-----------|-----------|------|
| 가격, RSI, RVOL, SMA | Yahoo Finance API | 실시간성 중요 |
| 시가총액 | API → kavout_output → kavout CSV | 티어 분류 필수, 3단계 fallback |
| EPS 서프라이즈, FCF, ROE | kavout_output 보완 | API 누락 시 구조 데이터 활용 |

### Step 3 상세 — 시가총액 티어별 랭킹

**시가총액 티어 분류** (`shared/strategy.py`):

| 티어 | 기준 | 상수 |
|------|------|------|
| 대형주 (Large Cap) | 시가총액 ≥ $50B | `MCAP_LARGE_CAP = 50_000_000_000` |
| 중형주 (Mid Cap) | $5B ≤ 시가총액 < $50B | `MCAP_MID_CAP = 5_000_000_000` |
| 소형주 (Small Cap) | 시가총액 < $5B | — |

**랭킹 방식**:

1. **전체 유니버스 점수화**: `rank_universe()` → 모든 종목 점수 계산
2. **티어 분류**: 시가총액 기준으로 대형/중형/소형 그룹화
3. **티어 내 독립 랭킹**: 각 티어 내에서 점수 순 정렬 → 1위부터 재부여
4. 전체 통합 순위 없음 — 티어 내 비교만 의미 있음

> **설계 의도**: 대형주($50B+)와 소형주(<$5B)는 성장·리스크 프로파일이 달라 직접 비교가 부적절합니다. 같은 섹터·규모 내 상대 강도를 측정하는 것이 실제 투자 판단에 유용합니다.

### 점수 계산 구조 (v2 — 업데이트됨)

`core/fundamental_screener.py:rank_universe()` 사용:

```
Momentum Score (0~100):
  = RSI(25%) + RelVolume(25%) + 52주위치(25%) + SMA추세(25%)
    ↳ SMA추세: SMA200(40%) + SMA50(35%) + SMA20(25%) 가중 평균

Fundamental Score (0~100):
  = 매출성장(40%) + EPS서프라이즈(25%) + 영업이익률(35%)
    ↳ EPS서프라이즈 없으면 50점 (neutral, 페널티 없음)

Catalyst Score (0~100):  [K어닝 분석.md 기반, 어닝콜 있을 때만]
  = 가이던스방향(60%) + 경영진톤(40%)

Final Score:
  - Catalyst 있음: 0.35×M + 0.40×F + 0.25×C
  - Catalyst 없음: 0.45×M + 0.55×F
```

**변경 이력 (이전 v1 대비)**:

| 항목 | v1 (이전) | v2 (현재) |
|------|-----------|-----------|
| 모멘텀 구성 | RSI(35%) + RVOL(35%) + 52W(30%) | RSI(25%) + RVOL(25%) + 52W(25%) + SMA(25%) |
| 펀더멘털 구성 | 매출(35%) + **순이익성장**(35%) + 마진(30%) | 매출(40%) + **EPS서프라이즈**(25%) + 마진(35%) |
| 카탈리스트 구성 | 가이던스(50%) + 톤(30%) + **catalyst_strength**(20%) | 가이던스(60%) + 톤(40%) |
| No-catalyst 가중치 | 0.47×M + 0.53×F | 0.45×M + 0.55×F |
| 순위 방식 | 전체 통합 | **티어별 독립 랭킹** |

### Kavout K-Score 활용

- K-Score 1~9 스케일 (Kavout AI 자체 신호)
- 매수 파이프라인 Step 4에서 K-Score ≥ 7 → `signal_count += 1`
- 스크리닝 결과 노트·Slack에 K-Score 함께 표시

### 출력 — Obsidian 노트 구조

경로: `swing-procedure/screener/kavout/YYYY-MM-DD.md`

**티어별 섹션 구성**:

```markdown
## 🏦 대형주 ($50B+)  — N종목

| 티어순위 | 티커 | 시가총액 | 점수 | K-Score | ...지표... |
|----------|------|----------|------|---------|------------|

### 1. TICKER — 회사명

#### 📋 투자 근거 (K어닝 분석.md 원문 발췌, LLM 토큰 0)
- 비즈니스 모델: ...
- 전략 변화: ...
- 경영진 신뢰도: ...

#### 📊 기술 스냅샷
| RSI | RVOL | SMA20 | SMA50 | SMA200 | 52W위치 |
| ... | .... | ..... | ..... | ...... | ....... |

#### 💰 밸류에이션
| Forward PE | PEG | Beta | 목표가 | 추천등급 |
| .......... | ... | .... | ...... | ........ |

#### 📈 펀더멘털
| 매출성장 | EPS서프라이즈 | 영업이익률 | 순이익률 |
| ........ | ............. | .......... | ........ |
```

> **투자 근거 섹션**: `parse_earnings()` 반환값의 `business_model`, `strategy_changes`, `management_confidence` 필드를 **직접 출력** (LLM 재호출 없음 → 토큰 비용 0).

### 출력 — Slack 메시지

티어별 Top 3 + 핵심 지표 표시:

```
📊 Kavout 스크리닝 결과 — 2026-05-30

🏦 대형주 ($50B+)
1. NVDA  시총 $3.2T  점수 82.4  K:8.1  RSI:62  EPS서프:+15%
2. MSFT  시총 $2.8T  점수 79.1  K:7.8  RSI:58  EPS서프:+8%
3. AAPL  시총 $2.6T  점수 75.3  K:7.2  RSI:55  EPS서프:+5%

🏢 중형주 ($5B~$50B)
1. CRDO  시총 $8.1B  점수 88.6  K:8.4  RSI:71  EPS서프:+22%
...
```

---

## 12. 핵심 분석 엔진 (core/analysis.py)

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

## 13. LLM 호출 및 캐시 (core/llm.py)

### call_llm()

```
입력: messages, system_prompt, model, max_tokens, temperature
처리:
  1. 지정 모델로 OpenRouter POST 시도
  2. 실패 시 MODEL_PRIORITY 폴백 체인 (최대 4개 모델 순차 시도)
  3. tenacity retry (최대 3회, 지수 백오프 2~10초)
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
  - 기본: 24시간 (CACHE_TTL_HOURS)
```

### analyze_with_llm()

```
입력: template_name, template_vars, cache_key(선택), force_refresh(선택)
처리:
  1. _TEMPLATE_MAX_TOKENS.get(template_name, 4096) → 템플릿별 출력 한도 결정
  2. cache_key + not force_refresh → get_cache() 확인 → 히트 시 즉시 반환
  3. prompts.render(template_name, **template_vars) → 프롬프트 생성
  4. get_model_for(template_name) → 태스크별 모델 결정
  5. call_llm(max_tokens=max_tokens, ...) → OpenRouter 호출
  6. parse_llm_json() → 마크다운 코드 블록 제거 후 JSON 파싱
  7. set_cache(cache_key, result) → 캐시 저장
출력: dict (LLM 응답 JSON)
```

**템플릿별 max_tokens 설정** (`_TEMPLATE_MAX_TOKENS`):

| 템플릿 | max_tokens | 이유 |
|--------|:----------:|------|
| `buy_step3_research` | 8,192 | 7개 JSON 섹션 (bull/bear thesis 각 1,000자 이상) → 기본 4,096 잘림 방지 |
| `buy_step3b_technical_narrative` | 4,096 | 6단락 기술 내러티브 (~1,500 토큰 출력) |
| 그 외 (기본값) | 4,096 | — |

**모델 라우팅** (`shared/prompts.py:get_model_for()`):

```python
_TEMPLATE_TO_CFG = {
    "buy_step3_research":             cfg.LLM_MODEL_BUY_RESEARCH,
    "buy_step3b_technical_narrative": cfg.LLM_MODEL_BUY_TECH_NARRATIVE,
    "sell_step1_health":              cfg.LLM_MODEL_SELL_HEALTH,
    "sell_step2_environment":         cfg.LLM_MODEL_SELL_ENV,
    "sell_step3_decision":            cfg.LLM_MODEL_SELL_HEALTH,
    "sell_step4_review":              cfg.LLM_MODEL_SELL_HEALTH,
    "nl_routing":                     cfg.LLM_MODEL_NL_ROUTING,
}
```

---

## 14. 투자 로직 평가 요약

| 기능 | 평가 | 비고 |
|------|:----:|------|
| 레짐 필터링 | ★★★★★ | ADX+VIX+SPY+QQQ 4중 확인, 결정론적 |
| 기술 점수 (100점) | ★★★★★ | 4지표 균형 배분 + Kavout AI + 애널리스트 3중 검증 |
| Devil's Advocate 차감 | ★★★★☆ | 자동 과열 방지, 차감 수치는 조정 가능 |
| 옵션 선택 기준 | ★★★★★ | Delta 0.4~0.7 ITM 범위, DTE≥21 시간 여유, OI/스프레드 유동성 |
| 시나리오 EV 계산 | ★★★★☆ | signal_count 기반 확률 테이블은 단순화. 실제 내재 확률(IV 기반) 도입 고려 |
| 뉴스 LLM 분석 | ★★★★☆ | Role Lock으로 편향 방지, LLM 환각 가능성 → `conviction_delta` 제한 |
| IV Crush 보호 | ★★★★★ | 어닝 타이밍까지 확인하는 정밀 분류 |
| 트레일링 스탑 | ★★★★☆ | 고점 자동 추적, 20% 기본값 조정 가능 |
| Requeue 시스템 | ★★★★☆ | 탈락 종목 재분석으로 기회 손실 최소화 |
| P&L 귀인 분석 | ★★★★☆ | Delta/Theta/Vega 분리 → Vega는 잔차법으로 근사 |
| Kavout 스크리닝 (API) | ★★★★★ | Yahoo Finance 실시간 + kavout_output 보완 → 시가총액 티어별 독립 랭킹, 투자 근거 자동 생성 |
| Finviz 스크리닝 (파일) | ★★★★☆ | 사전 파일 준비 필요, 실시간성 낮음 |

---

## 15. 기능-모듈 매핑표

| 기능 | 파일 | 핵심 함수/클래스 |
|------|------|----------------|
| 매수 파이프라인 실행 | `orchestrator/pipelines.py` | `BuyPipeline.run()` |
| 매도 파이프라인 실행 | `orchestrator/pipelines.py` | `SellPipeline.run()` |
| Requeue 파이프라인 | `orchestrator/pipelines.py` | `RequeuePipeline.run()` |
| 엔진 (NL 라우팅 포함) | `orchestrator/engine.py` | `PipelineEngine` |
| CLI — 매수 실행 | `scripts/run_buy_pipeline.py` | `run()` |
| CLI — 매도 실행 (실제/DRY-RUN) | `scripts/run_sell_pipeline.py` | `run(real_mode)` |
| CLI — Finviz 스크리닝 | `scripts/run_screener.py` | `run()` |
| CLI — Kavout 스크리닝 (API) | `scripts/run_kavout_screener.py` | `run()` |
| CLI — Requeue | `scripts/run_requeue.py` | `run_pipeline()` |
| CLI — 특정 종목 매수 | `scripts/run_ticker.py` | `run(tickers)` |
| Yahoo Finance API 수집 | `core/api_fetcher.py` | `fetch_finviz_detail()`, `fetch_finviz_details_bulk()` |
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
| kavout_output 파싱 | `core/parsers.py` | `parse_kavout_output()`, `_parse_kavout_output_file()` |
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

## 16. 사용자 프롬프트 → 내부 함수 매핑표

### 16.1 자연어 명령 → 인텐트 → 도구

`orchestrator/engine.py:route_nl()` 에서 처리. 키워드 매핑 우선, 실패 시 LLM 폴백.

**⚠️ nl_query 라우팅 가능 인텐트**: BUY_PIPELINE, SELL_PIPELINE, POSITION_STATUS, REQUEUE_ADD, REQUEUE_LIST, STEP_EXECUTE — 이 6개만. 나머지는 직접 도구 호출 필요.

| 사용자 입력 예시 | 인텐트 | 호출 함수 | nl_query 가능? |
|----------------|--------|-----------|:---:|
| "매수 분석 실행해줘" | BUY_PIPELINE | `engine.run_buy()` | ✅ |
| "AAPL MSFT만 분석해줘" | BUY_PIPELINE | `engine.run_buy(target_tickers=["AAPL","MSFT"])` | ✅ |
| "매도 분석 해줘" | SELL_PIPELINE | `engine.run_sell()` | ✅ |
| "포지션 청산 검토해줘" | SELL_PIPELINE | `engine.run_sell()` | ✅ |
| "AAPL 포지션 어떻게 됐어?" | POSITION_STATUS | `engine.position_status("AAPL")` | ✅ |
| "TSLA 대기열에 넣어줘" | REQUEUE_ADD | `engine.requeue_add()` | ✅ |
| "대기 종목 목록 보여줘" | REQUEUE_LIST | `engine.requeue_list()` | ✅ |
| "Step 5 다시 실행해줘" | STEP_EXECUTE | `engine.step_execute()` | ✅ |
| "MSFT 2계약 부분 청산해줘" | — | `partial_exit_apply` 직접 호출 | ❌ |
| "헬스 체크해줘" | — | `health_check` 직접 호출 | ❌ |
| "펀더멘털 스크리닝 해줘" | — | `run_fundamental_screen` (screener_mcp) | ❌ |

### 16.2 MCP 도구 직접 호출

| 도구 | 파라미터 예시 | 내부 경로 |
|------|-------------|-----------|
| `run_buy_pipeline` | `{}` | `engine.run_buy()` → `BuyPipeline.run()` |
| `run_buy_pipeline` | `{"target_tickers": ["AAPL", "MSFT"]}` | 특정 종목만 필터 |
| `run_sell_pipeline` | `{"target_tickers": ["TSLA"]}` | `engine.run_sell()` → `SellPipeline.run()` |
| `health_check` | `{}` | Obsidian ping + 파일 존재 확인 |
| `cache_clear` | `{"ticker": "AAPL"}` | `engine.clear_cache("AAPL")` |
| `partial_exit_apply` | `{"ticker": "AAPL", "contracts_to_close": 2, "exit_premium": 5.50}` | `engine.partial_exit()` |
| `position_status` | `{"ticker": "AAPL"}` | `engine.position_status()` |
| `requeue_add` | `{"ticker": "NVDA", "failed_filters": ["F1_RVOL_LOW"]}` | `engine.requeue_add()` |
| `run_fundamental_screen` | `{"top_n": 10}` | `_run_fundamental_screen()` → `rank_universe()` |
| `run_kavout_screen` | `{"top_n": 20}` | `_run_kavout_screen()` → `fetch_finviz_details_bulk()` → `rank_universe()` |

### 16.3 LLM 프롬프트 → 실행 경로

| 상황 | 템플릿 | 모델 변수 | 출력 JSON 핵심 필드 |
|------|--------|----------|-------------------|
| 뉴스 감성 합성 (TYPE 1) | `buy_step3_research` | `LLM_MODEL_BUY_RESEARCH` | overall_sentiment, bull_thesis, bear_thesis, conviction_delta, key_drivers, critical_events |
| 기술 내러티브 생성 (TYPE 3) | `buy_step3b_technical_narrative` | `LLM_MODEL_BUY_TECH_NARRATIVE` | trend_ma, momentum, trend_strength, support_resistance, entry_timing, risk_scenario |
| 포지션 무효화 점검 | `sell_step1_health` | `LLM_MODEL_SELL_HEALTH` | flags, dte_urgency, condition_checks |
| 이벤트 리스크 분석 | `sell_step2_environment` | `LLM_MODEL_SELL_ENV` | event_judgment, iv_crush_risk |
| 최종 매도 결정 | `sell_step3_decision` | `LLM_MODEL_SELL_HEALTH` | action (HOLD/PARTIAL_EXIT/FULL_EXIT/ROLL) |
| 트레이드 복기 | `sell_step4_review` | `LLM_MODEL_SELL_HEALTH` | thesis_accuracy, lesson, improvement |
| NL 명령 라우팅 | `nl_routing` | `LLM_MODEL_NL_ROUTING` | intent, extracted_tickers, routed_tool |

---

## 17. 수정 가이드

### 17.1 전략 파라미터 수정

모든 수치 임계값은 `shared/strategy.py` 한 곳에서 관리합니다.

```python
# 옵션 선택 기준
DELTA_MIN = 0.40        # 델타 하한
DELTA_MAX = 0.70        # 델타 상한
IVR_MAX = 70.0          # IVR 최대
DTE_MIN = 21            # 최소 DTE
OI_MIN = 500            # 최소 미결제약정

# 필터 기준
RVOL_MIN = 1.5          # 최소 상대거래량
PRICE_TRADE_MIN = 20.0  # 최소 주가
MARKET_CAP_MIN = 10_000_000_000  # 최소 시가총액 ($10B)

# 매도 기준
SELL_DTE_CRITICAL = 7
SELL_DTE_WARNING = 14
SCENARIO_STOP_LOSS_RATIO = 0.5    # 손절
SCENARIO_TARGET_1ST_RATIO = 1.5   # 1차 익절
SCENARIO_TARGET_2ND_RATIO = 2.0   # 2차 익절
SCENARIO_TARGET_3RD_RATIO = 2.5   # 3차 익절
TRAILING_STOP_PCT = 20.0          # 트레일링 스탑 %

# DA 차감
DA_BUY_IV_CRUSH_PENALTY = -15.0
DA_BUY_THESIS_CONTRA_PENALTY = -20.0
DA_BUY_INSIDER_SELL_PENALTY = -10.0
DA_BUY_SCORE_THRESHOLD = 40.0     # DA 후 최소 점수
```

### 17.2 Kavout 스크리너 API 동시 처리 수 조정

파일: `core/api_fetcher.py`

```python
# fetch_finviz_details_bulk() 호출 시 조정
finviz_details = await fetch_finviz_details_bulk(
    sorted(kavout_tickers),
    sleep_sec=0.5,       # 티커당 대기 시간 (Yahoo Finance 차단 방지)
    max_concurrency=5,   # 동시 실행 스레드 수 (높이면 빠르지만 차단 위험)
)
```

또는 `scripts/run_kavout_screener.py` 안의 호출부를 직접 수정합니다.

### 17.3 LLM 모델 변경

`.env` 파일에서 변수만 수정하면 즉시 반영됩니다 (코드 변경 불필요).

```env
# ── 태스크별 지정 모델 (비워두면 폴백 체인 전체 사용) ────────────────
# Buy Step 5 — 뉴스 합성: 7섹션 JSON 생성, 고품질 필요 → 유료
LLM_MODEL_BUY_RESEARCH=deepseek/deepseek-v4-pro

# Buy Step 5 — 기술 내러티브: 6단락 생성, flash 충분
LLM_MODEL_BUY_TECH_NARRATIVE=deepseek/deepseek-v4-flash

# Buy Step 5 — 증분 리서치 업데이트 (신규 기사만 합성)
LLM_MODEL_BUY_RESEARCH_UPDATE=deepseek/deepseek-v4-flash

# Screener — 어닝콜 4값 분류, flash 충분
LLM_MODEL_KAVOUT_EARNINGS=deepseek/deepseek-v4-flash

# Sell, Routing — 단순 구조화 판단, 무료로 충분
LLM_MODEL_SELL_HEALTH=nvidia/nemotron-3-super-120b-a12b:free
LLM_MODEL_SELL_ENV=nvidia/nemotron-3-super-120b-a12b:free
LLM_MODEL_NL_ROUTING=nvidia/nemotron-3-super-120b-a12b:free

# ── 무료 폴백 체인 (지정 모델 실패 시 순서대로) ─────────────────────
LLM_PRIMARY_MODEL=nvidia/nemotron-3-super-120b-a12b:free
LLM_FALLBACK_MODEL=meta-llama/llama-3.3-70b-instruct:free
LLM_FALLBACK_2=qwen/qwen3-coder:free
LLM_FALLBACK_3=openai/gpt-oss-120b:free
```

### 17.4 실제 측정 토큰 사용량 및 CLI 비용 추정

#### 실제 측정값 — TSLA 단일 실행 (2026-05-29, `run_tsla_only.py`)

> 두 번 실행한 결과. `force_refresh=True`이므로 캐시 없이 매번 LLM 호출.

| 호출 순서 | 템플릿 | 모델 | 입력 토큰 | 출력 토큰 | 합계 | 소요 시간 |
|-----------|--------|------|-----------|-----------|------|-----------|
| 1회차 — ① | `buy_step3_research` | `deepseek/deepseek-v4-pro` | 5,563 | **8,689** | 14,252 | 257s |
| 1회차 — ② | `buy_step3b_technical_narrative` | `deepseek/deepseek-v4-flash` | 1,679 | 1,484 | 3,163 | 44s |
| 2회차 — ① | `buy_step3_research` | `deepseek/deepseek-v4-pro` | 5,563 | **5,823** | 11,386 | 231s |
| 2회차 — ② | `buy_step3b_technical_narrative` | `deepseek/deepseek-v4-flash` | 1,700 | 1,544 | 3,244 | 15s |

> **출력 토큰 변동**: research 출력이 1회차 8,689 vs 2회차 5,823으로 차이나는 이유는 LLM 확률적 생성 특성상 매번 다른 길이의 응답이 나오기 때문. 비용 추정은 **평균값 (7,256)**을 사용.

**입력 토큰 구성 (research 기준):**

```
system_prompt   : ~500 토큰  (역할 고정 + 전략 철학)
시장 뉴스 (20개): ~1,500 토큰 (RSS 마켓 샘플)
종목 뉴스 (44개): ~3,300 토큰 (RSS + DDG + Brave)
기술 지표 컨텍스트: ~263 토큰
─────────────────────────────────────
합계             : ~5,563 토큰  ← 측정값과 일치
```

---

#### 단가 기준 — 실측 역산 (2026-05-29 실사용 데이터)

> **실측 데이터**: 3시간 반복 실행 → deepseek/deepseek-v4-pro **$0.22 / 109K tokens**  
> 추정 실행 횟수: 109K ÷ 평균 12,819토큰/회 ≈ **8.5회**, 1회 평균 **$0.026**
>
> 역산 단가 (input 5,563 / output 7,256, output = 4× input 가정 검증):  
> `8.5 × (5,563 × $0.75 + 7,256 × $3.00) / 1M = $0.221 ≈ $0.22 ✓`

| 모델 | 입력 ($/1M) | 출력 ($/1M) | 근거 |
|------|:-----------:|:-----------:|------|
| `deepseek/deepseek-v4-pro` | **$0.75** | **$3.00** | 실측 역산 ($0.22/109K) |
| `deepseek/deepseek-v4-flash` | **~$0.19** | **~$0.75** | v4-pro 대비 ÷4 추정 (실측 없음) |
| `:free` 모델 전체 | $0 | $0 | 무료 |

> ⚠️ **v4-pro 단가는 실측 기반**, v4-flash는 추정치. 정확한 단가는 openrouter.ai/activity 확인.

---

#### 호출별 실제 비용 (역산 단가 적용)

| 호출 | 모델 | 입력 비용 | 출력 비용 | 1회 비용 |
|------|------|-----------|-----------|:--------:|
| research (평균 — 출력 7,256) | v4-pro | 5,563 × $0.75/M = $0.00417 | 7,256 × $3.00/M = $0.02177 | **~$0.026** |
| research (최대 — 출력 8,689) | v4-pro | $0.00417 | 8,689 × $3.00/M = $0.02607 | **~$0.030** |
| research (최소 — 출력 5,823) | v4-pro | $0.00417 | 5,823 × $3.00/M = $0.01747 | **~$0.022** |
| tech_narrative (평균) | v4-flash | 1,690 × $0.19/M = $0.000321 | 1,514 × $0.75/M = $0.001136 | **~$0.0015** |
| earnings/ticker | v4-flash | 500 × $0.19/M = $0.000095 | 120 × $0.75/M = $0.000090 | **~$0.0002** |
| sell 전체 | nemotron-free | — | — | **$0** |

**1티커 전체 (research + tech_narrative):**  
$0.026 + $0.0015 = **~$0.028/ticker**

---

#### CLI 기능별 1회 실행 비용 추정 (역산 단가 기준)

> **전제**: 캐시 없음 (당일 첫 실행). 동일 날짜 재실행은 캐시 히트 → **$0**.  
> `run_tsla_only.py`는 `force_refresh=True` 고정 → 캐시 없이 매번 청구됨 (테스트 전용).

| CLI 스크립트 | LLM 호출 내역 | 추정 비용 | 캐시 재실행 |
|-------------|--------------|:---------:|:-----------:|
| **`run_screener.py`** (Finviz, ~50종목) | earnings × 50 ($0.0002/개, flash) | **~$0.010** | $0 |
| **`run_kavout_screener.py`** (Kavout, ~41종목) | earnings × 41 ($0.0002/개, flash) | **~$0.008** | $0 |
| **`run_buy_pipeline.py`** (평균 5종목 통과) | research × 5 ($0.026) + tech_narrative × 5 ($0.0015) | **~$0.138** | $0 |
| **`run_buy_pipeline.py`** (최악 — 10종목 통과) | research × 10 + tech_narrative × 10 | **~$0.275** | $0 |
| **`run_sell_pipeline.py --real`** (3포지션) | 전부 `:free` 모델 | **$0** | $0 |
| **`run_ticker.py TSLA`** (단일) | research × 1 + tech_narrative × 1 | **~$0.028** | $0 |
| **`run_ticker.py AAPL MSFT NVDA`** (3종목) | research × 3 + tech_narrative × 3 | **~$0.083** | $0 |
| **`run_requeue.py`** (준비 종목 2개) | research × 2 + tech_narrative × 2 | **~$0.055** | $0 |

---

#### 일일 워크플로우 비용 추정 (Step A → B → C)

```
Step A: run_kavout_screener.py  →  ~$0.008  (earnings LLM, flash)
Step B: run_buy_pipeline.py     →  ~$0.138  (research+narrative, pro+flash, 5종목 기준)
Step C: run_sell_pipeline.py    →  $0.000   (모두 무료 모델)
────────────────────────────────────────────────────────────────
1일 합계                         →  ~$0.146  (~170원)
20거래일 기준 월 합계             →  ~$2.92   (~3,400원)
```

**이전 추정값 대비 수정:**

| 항목 | 이전 추정 | 실측 역산 | 차이 |
|------|:---------:|:---------:|:----:|
| research 1회 비용 | $0.0095 | $0.026 | **×2.7** |
| 5종목 buy pipeline | $0.048 | $0.138 | **×2.9** |
| 1일 워크플로우 | $0.052 | $0.146 | **×2.8** |
| 월 합계 (20일) | $1.04 | $2.92 | **×2.8** |

> **핵심**: deepseek-v4-pro 출력 단가($3.00/M)가 예상보다 약 3배 높음.  
> research 호출은 입력(5,563토큰)보다 **출력(평균 7,256토큰)이 더 길어** 출력 단가 영향이 큼.

### 17.5 새로운 필터 추가

파일: `core/analysis.py:apply_filters()` (line ~300)

```python
# 예: F8 — RSI 과열 필터 추가
for ticker in passed_list[:]:
    ticker_data = summary_data.tickers.get(ticker)
    if ticker_data and ticker_data.technical.rsi > 85:
        failures[ticker] = failures.get(ticker, []) + ["F8_RSI_OVERBOUGHT"]
        passed_list.remove(ticker)
```

### 17.6 Obsidian 노트 경로 변경

`shared/config.py` 또는 `.env`:

```env
BUY_NOTE_PATH_TEMPLATE=swing-procedure/buy/{date}.md
SELL_NOTE_PATH_TEMPLATE=swing-procedure/sell/{date}.md
TICKER_NOTE_PATH_TEMPLATE=swing-procedure/tickers/{ticker}.md
REJECTED_NOTE_PATH_TEMPLATE=swing-procedure/rejected/{ticker}_{date}.md
```

### 17.7 Slack 채널 변경

```env
SLACK_CHANNEL_MAIN=#swing-trading
SLACK_CHANNEL_ALERT=#swing-alerts
```

### 17.8 확신도 가중치 변경

`shared/strategy.py`:

```python
ENTRY_CONVICTION_MIN = 0.70   # 진입 최소 확신도
WATCH_CONVICTION_MIN = 0.50   # 관찰 최소 확신도
```

가중치 변경: `core/analysis.py:calculate_confidence()` 내 `0.4×trend + 0.2×news + 0.3×thesis + 0.1×execution` 직접 수정.

### 17.9 Obsidian retry 설정

파일: `core/obsidian.py` — `@retry` 데코레이터

```python
# 현재 설정 (빠른 실패 우선)
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=10)
)
# 최대 대기: 약 22초 (2 + 4 + 10 + 6초 처리)
```

---

## 18. 매수 노트 품질 목표 및 달성 현황

### 18.1 섹션별 데이터 소스 흐름

```
Step 1 (데이터 로딩)
  ├─ FinvizDetail (가격·기술지표)  ──────────────────────────→ TYPE 2, TYPE 3 (지표 테이블)
  ├─ KavoutData (K-Score)         ──────────────────────────→ TYPE 2 (K-Score 표시)
  └─ EarningsAnalysis             ──────────────────────────→ TYPE 2 (어닝콜 분석)

Step 4 (기술분석)
  └─ TechnicalScore (final_score, signal_count) ─────────────→ TYPE 3 (Dashboard), TYPE 5 (3D 기술점수)

Step 5 (뉴스+내러티브)
  ├─ LLM① buy_step3_research      ──────────────────────────→ TYPE 1 (감성·thesis·drivers)
  └─ LLM② buy_step3b_technical_narrative ───────────────────→ TYPE 3 (6단락 내러티브)

Step 2 (레짐)
  └─ MarketRegime                  ─────────────────────────→ TYPE 5 (3D 거시점수)

Step 7 (옵션)
  └─ OptionValidity (Greeks)       ─────────────────────────→ TYPE 4, TYPE 5 (실행 계획)

Step 8 (시나리오)
  └─ Scenario (EV·손절·목표)        ─────────────────────────→ TYPE 4, TYPE 5 (시나리오 표)

Step 10 (랭킹)
  └─ FinalRanking (conviction)     ─────────────────────────→ TYPE 5 (3D 심리점수, 행동 계획)
```

### 18.2 3D 복합 스코어카드 계산 공식

```
기술 점수 (Technical):
  TechnicalScore.final_score (0~100)

거시 점수 (Macro):
  "favorable"   → 90점
  "neutral"     → 60점
  "unfavorable" → 25점

심리 점수 (Sentiment):
  base    = {"POSITIVE": 75, "MIXED": 45, "NEGATIVE": 20}[overall_sentiment]
  adj     = {"High": +15, "Medium": 0, "Low": -15}[confidence]
  sent_score = clamp(base + adj, 0, 100)

복합 점수 = round(tech × 0.40 + macro × 0.30 + sent × 0.30, 1)

판정:
  ≥ 70  → 🟢 진입 가능
  ≥ 55  → 🟡 관찰 대기
  ≥ 40  → 🟠 보류
  < 40  → 🔴 탈락
```

### 18.3 행동 계획 (Action Plan) 생성 로직

보유자/미보유자 판단은 다음 3가지 조건으로 결정됩니다:

| 조건 | 변수 | 기준 |
|------|------|------|
| 확신도 충족 | `r.conviction.total_conviction` | ≥ 6.0 |
| 신호 충족 | `signal_count` | ≥ 5 |
| 행동 방향 | `r.action` | BUY 또는 STRONG_BUY |

3가지 모두 충족 → "신규 진입 가능"  
2가지 충족 → "조건부 진입 대기"  
미충족 → "진입 보류 — 조건 충족 시까지 대기"

---

*SwingMCP v2.0.0 — 업데이트: 2026-05-30*  
*본 문서는 코드 분석 및 실제 운영 경험을 바탕으로 작성된 참조 자료입니다.*
