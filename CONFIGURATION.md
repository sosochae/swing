# SwingMCP v2.0.0 — 설정 참조 가이드

---

## 목차

1. [환경 변수 (.env)](#1-환경-변수-env)
2. [전략 파라미터 (shared/strategy.py)](#2-전략-파라미터-sharedstrategypy)
3. [LLM 프롬프트 레지스트리](#3-llm-프롬프트-레지스트리)
4. [MCP 서버 설정](#4-mcp-서버-설정)
5. [로컬 파일 구조 요구사항](#5-로컬-파일-구조-요구사항)
6. [pyproject.toml 의존성](#6-pyprojecttoml-의존성)

---

## 1. 환경 변수 (.env)

프로젝트 루트 `C:\MCP\Swing\.env` 에 저장합니다. `python-dotenv`가 자동으로 로드합니다.

### 1.1 필수 API 키

| 변수명 | 예시값 | 설명 | 필수 여부 |
|--------|--------|------|-----------|
| `OPENROUTER_API_KEY` | `sk-or-v1-xxxx` | OpenRouter LLM API 키 | **필수** |
| `OBSIDIAN_API_KEY` | `abc123xyz` | Obsidian Local REST API 키 | **필수** |
| `SLACK_BOT_TOKEN` | `xoxb-xxxx` | Slack Bot 토큰 | 선택 (없으면 알림 비활성화) |
| `BRAVE_API_KEY` | `BSA-xxxx` | Brave Search API 키 | 선택 |
| `FINNHUB_API_KEY` | `xxxx` | Finnhub API 키 (목표주가 실시간 조회) | 선택 (없으면 yfinance 폴백) |

### 1.2 로컬 데이터 경로

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `SUMMARY_DIR` | `Y:\내 드라이브\Swing` | 매수 요약 JSON 디렉토리 |
| `FINVIZ_FILE` | `Y:\내 드라이브\Swing\finviz_all_rows.txt` | Finviz 전체 종목 파일 |
| `EARNINGS_DIR` | `Y:\내 드라이브\어닝` | 어닝 분석 마크다운 디렉토리 |
| `DATA_DIR` | `Y:\내 드라이브\Data` | Kavout CSV 등 데이터 디렉토리 |
| `POSITIONS_FILE` | `Y:\내 드라이브\Swing\positions.md` | 현재 포지션 파일 |
| `WATCHLIST_FILE` | `Y:\내 드라이브\Swing\watchlist.md` | 관찰 종목 목록 |

### 1.3 내부 상태 경로

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `CACHE_DIR` | `shared/cache` | LLM 응답 캐시 디렉토리 |
| `SNAPSHOTS_DIR` | `shared/state/snapshots` | 파이프라인 스냅샷 디렉토리 |
| `REQUEUE_FILE` | `shared/state/requeue.json` | Requeue 대기열 파일 |
| `LOGS_DIR` | `shared/logs` | 로그 + 감사 로그 디렉토리 |
| `SNAPSHOT_RETENTION_DAYS` | `30` | 스냅샷 보존 기간 (일) |

### 1.4 Obsidian 연결

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `OBSIDIAN_BASE_URL` | `http://localhost:27123` | Obsidian Local REST API 주소 |
| `BUY_NOTE_PATH_TEMPLATE` | `swing-procedure/buy/{date}.md` | 매수 노트 경로 템플릿 |
| `SELL_NOTE_PATH_TEMPLATE` | `swing-procedure/sell/{date}.md` | 매도 노트 경로 템플릿 |
| `TICKER_NOTE_PATH_TEMPLATE` | `swing-procedure/tickers/{ticker}.md` | 종목 노트 경로 |
| `REJECTED_NOTE_PATH_TEMPLATE` | `swing-procedure/rejected/{ticker}_{date}.md` | 탈락 종목 노트 경로 |

### 1.5 Slack 채널

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `SLACK_CHANNEL_MAIN` | `#swing-trading` | 정상 결과 채널 |
| `SLACK_CHANNEL_ALERT` | `#swing-alerts` | 리스크·오류 알림 채널 |

### 1.6 LLM 모델 설정

| 변수명 | 기본값 | 사용 위치 |
|--------|--------|-----------|
| `LLM_MODEL_PRIMARY` | `anthropic/claude-haiku-4-5` | 기본 모델 (모든 LLM 호출) |
| `LLM_MODEL_FALLBACK1` | `openai/gpt-4o-mini` | 폴백 1순위 |
| `LLM_MODEL_FALLBACK2` | `deepseek/deepseek-chat-v3-0324:free` | 폴백 2순위 |
| `LLM_MODEL_FALLBACK3` | `meta-llama/llama-4-maverick:free` | 폴백 3순위 |
| `LLM_MODEL_BUY_RESEARCH` | `anthropic/claude-haiku-4-5` | Buy Step 5 뉴스 분석 |
| `LLM_MODEL_SELL_HEALTH` | `openai/gpt-4o-mini` | Sell Step 4, 10, 12 |
| `LLM_MODEL_SELL_ENV` | `deepseek/deepseek-chat:free` | Sell Step 5 이벤트 |
| `LLM_MODEL_NL_ROUTING` | `deepseek/deepseek-chat:free` | NL 명령 라우팅 |

### 1.7 트레이딩 설정

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `TOTAL_CAPITAL` | `100000` | 총 투자 자본 ($) |
| `MAX_POSITION_SIZE` | `5000` | 포지션당 최대 투자금 ($) |
| `COMMISSION_PER_CONTRACT` | `0.65` | 계약당 수수료 ($) |
| `TRAILING_STOP_PCT` | `20` | 트레일링 스탑 % (고점 대비) |

### 1.8 완성된 .env 파일 예시

```env
# 필수 API 키
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OBSIDIAN_API_KEY=your-obsidian-key-here
SLACK_BOT_TOKEN=xoxb-your-slack-token
BRAVE_API_KEY=BSA-your-brave-key

# 로컬 데이터 경로
SUMMARY_DIR=Y:\내 드라이브\Swing
FINVIZ_FILE=Y:\내 드라이브\Swing\finviz_all_rows.txt
EARNINGS_DIR=Y:\내 드라이브\어닝
DATA_DIR=Y:\내 드라이브\Data
POSITIONS_FILE=Y:\내 드라이브\Swing\positions.md
WATCHLIST_FILE=Y:\내 드라이브\Swing\watchlist.md

# 내부 상태
CACHE_DIR=shared/cache
SNAPSHOTS_DIR=shared/state/snapshots
REQUEUE_FILE=shared/state/requeue.json
LOGS_DIR=shared/logs
SNAPSHOT_RETENTION_DAYS=30

# Obsidian
OBSIDIAN_BASE_URL=http://localhost:27123
BUY_NOTE_PATH_TEMPLATE=swing-procedure/buy/{date}.md
SELL_NOTE_PATH_TEMPLATE=swing-procedure/sell/{date}.md
TICKER_NOTE_PATH_TEMPLATE=swing-procedure/tickers/{ticker}.md
REJECTED_NOTE_PATH_TEMPLATE=swing-procedure/rejected/{ticker}_{date}.md

# Slack
SLACK_CHANNEL_MAIN=#swing-trading
SLACK_CHANNEL_ALERT=#swing-alerts

# LLM 모델
LLM_MODEL_PRIMARY=anthropic/claude-haiku-4-5
LLM_MODEL_FALLBACK1=openai/gpt-4o-mini
LLM_MODEL_FALLBACK2=deepseek/deepseek-chat-v3-0324:free
LLM_MODEL_FALLBACK3=meta-llama/llama-4-maverick:free
LLM_MODEL_BUY_RESEARCH=anthropic/claude-haiku-4-5
LLM_MODEL_SELL_HEALTH=openai/gpt-4o-mini
LLM_MODEL_SELL_ENV=deepseek/deepseek-chat:free
LLM_MODEL_NL_ROUTING=deepseek/deepseek-chat:free

# 트레이딩
TOTAL_CAPITAL=100000
MAX_POSITION_SIZE=5000
COMMISSION_PER_CONTRACT=0.65
TRAILING_STOP_PCT=20
```

---

## 2. 전략 파라미터 (shared/strategy.py)

`shared/strategy.py`는 모든 수치 임계값의 단일 진실 소스(Single Source of Truth)입니다.

### 2.1 옵션 선택 기준

| 파라미터 | 현재값 | 의미 | 조정 방향 |
|----------|--------|------|-----------|
| `DELTA_MIN` | `0.40` | 델타 최소값 (ITM 경계) | 낮추면 더 OTM 허용 → 레버리지 ↑, 성공률 ↓ |
| `DELTA_MAX` | `0.70` | 델타 최대값 (딥 ITM 경계) | 높이면 더 ITM → 안전하지만 레버리지 ↓ |
| `IVR_MAX` | `70.0` | IVR 최대값 | 낮추면 보수적 (고IVR 환경 제외) |
| `IVR_WARNING` | `60.0` | IVR 경고 임계값 | - |
| `OI_MIN` | `500` | 미결제약정 최소값 | 높이면 유동성 기준 엄격 |
| `OI_WARNING` | `1000` | OI 경고 임계값 | - |
| `SPREAD_MAX_PCT` | `5.0` | 스프레드 최대 % | 낮추면 유동성 좋은 종목만 |
| `DTE_MIN` | `21` | 최소 만기일 | 높이면 시간 여유 ↑, 기회 ↓ |

### 2.2 필터 기준

| 파라미터 | 현재값 | 의미 |
|----------|--------|------|
| `RVOL_MIN` | `1.5` | F1 상대거래량 최소 (평균 대비 1.5배) |
| `PRICE_TRADE_MIN` | `20.0` | F3 최소 주가 ($20) |
| `MARKET_CAP_MIN` | `10_000_000_000` | F3 최소 시가총액 ($10B) |
| `EARNINGS_IMMINENT_DAYS` | `7` | F5 어닝 임박 기준 (일) |
| `SECTOR_MAX_POSITIONS` | `3` | F6 섹터당 최대 포지션 수 |

### 2.3 기술 점수 배분

| 파라미터 | 현재값 | 의미 |
|----------|--------|------|
| `TECH_MA_MAX` | `25` | MA 정렬 최대 점수 |
| `TECH_ADX_MAX` | `25` | ADX 최대 점수 |
| `TECH_RSI_MAX` | `25` | RSI 최대 점수 |
| `TECH_RVOL_MAX` | `25` | RVOL 최대 점수 |

### 2.4 레짐 판단 기준

| 파라미터 | 현재값 | 의미 |
|----------|--------|------|
| `TREND_ADX_STRONG` | `25` | 추세 강함 기준 (ADX) |
| `TREND_ADX_BORDERLINE` | `20` | 경계선 기준 |
| `REGIME_VIX_FAVORABLE` | `20` | 우호적 VIX 상한 |
| `REGIME_VIX_BORDERLINE` | `30` | 경계선 VIX 상한 |

### 2.5 시나리오 확률 테이블

| 파라미터 | 현재값 | 적용 조건 |
|----------|--------|-----------|
| `SCENARIO_PROB_BULL_STRONG` | `(0.45, 0.35, 0.20)` | signal_count >= 6 |
| `SCENARIO_PROB_BULL_MEDIUM` | `(0.35, 0.40, 0.25)` | signal_count 4~5 |
| `SCENARIO_PROB_BULL_WEAK` | `(0.25, 0.40, 0.35)` | signal_count < 4 |

### 2.6 손절/익절 비율

| 파라미터 | 현재값 | 의미 |
|----------|--------|------|
| `SELL_STOP_LOSS_RATIO` | `0.50` | 손절: 진입 프리미엄의 50% (50% 손실 시) |
| `SELL_TARGET_1ST_RATIO` | `1.50` | 1차 익절: 50% 수익 시 |
| `SELL_TARGET_2ND_RATIO` | `2.00` | 2차 익절: 100% 수익 시 |
| `SELL_TARGET_3RD_RATIO` | `2.50` | 3차 익절: 150% 수익 시 |

### 2.7 DTE 긴급도 기준

| 파라미터 | 현재값 | 긴급도 |
|----------|--------|--------|
| `SELL_DTE_CRITICAL` | `7` | 위급 (즉시 청산 검토) |
| `SELL_DTE_WARNING` | `14` | 주의 |
| `SELL_DTE_NORMAL` | `21` | 보통 |
| `SELL_DTE_FORCE_EXIT` | `7` | 강제 청산 DTE |

### 2.8 IV Crush 관련

| 파라미터 | 현재값 | 의미 |
|----------|--------|------|
| `SELL_IVR_CRUSH_THRESHOLD` | `70` | IV Crush 경고 IVR 기준 |
| `SELL_IV_CRUSH_LOSS_RATIO` | `0.30` | 예상 손실 = 진입 프리미엄 x 0.30 |

### 2.9 부분 청산 비율

| 파라미터 | 현재값 | 상황 |
|----------|--------|------|
| `SELL_PARTIAL_REGIME_RATIO` | `0.75` | 레짐역전/DTE주의 |
| `SELL_PARTIAL_PROFIT_RATIO` | `0.50` | 수익 확정 |
| `SELL_PARTIAL_LOSS_RATIO` | `0.33` | 손실 헷지 |

### 2.10 매도 판단 Finviz 기준

| 파라미터 | 현재값 | 의미 |
|----------|--------|------|
| `SELL_ANALYST_SELL_THRESHOLD` | `3.5` | Recom >= 3.5 → 매도의견 |
| `SELL_EPS_MISS_PCT` | `-5.0` | EPS 서프라이즈 < -5% → EPS미스 경고 |
| `SELL_INSIDER_SELL_PCT` | `-20.0` | 내부자 거래 < -20% → 경고 |
| `SELL_TARGET_PRICE_PROXIMITY` | `0.95` | 현재가 >= 목표주가 x 95% → 근접 |

### 2.11 Devil's Advocate 차감

| 파라미터 | 현재값 | 차감 조건 |
|----------|--------|-----------|
| `DA_IV_CRUSH_DEDUCTION` | `-15` | 어닝 7일 이내 + IVR > 60 |
| `DA_THESIS_CONFLICT_DEDUCTION` | `-20` | 감성 NEGATIVE + 방향 반대 |
| `DA_INSIDER_SELL_DEDUCTION` | `-10` | insider_trans_pct < -20% |
| `DA_EPS_MISS_DEDUCTION` | `-5` | eps_surprise_pct < -5% |
| `MIN_SCORE_AFTER_DA` | `40` | DA 후 최소 점수 |

### 2.12 확신도 가중치

| 파라미터 | 현재값 | 성분 |
|----------|--------|------|
| `CONVICTION_WEIGHT_TREND` | `0.40` | 기술 분석 비중 |
| `CONVICTION_WEIGHT_NEWS` | `0.20` | 뉴스 감성 비중 |
| `CONVICTION_WEIGHT_THESIS` | `0.30` | 투자 논거 비중 |
| `CONVICTION_WEIGHT_EXECUTION` | `0.10` | 실행 조건 비중 |

### 2.13 펀더멘털 스크리너 점수 가중치

| 파라미터 | 현재값 | 의미 |
|----------|--------|------|
| `FSCORE_MOM_RSI_WEIGHT` | `0.35` | Momentum: RSI 비중 |
| `FSCORE_MOM_RVOL_WEIGHT` | `0.35` | Momentum: RelVol 비중 |
| `FSCORE_MOM_52W_WEIGHT` | `0.30` | Momentum: 52주위치 비중 |
| `FSCORE_FUND_REV_WEIGHT` | `0.35` | Fundamental: 매출성장 비중 |
| `FSCORE_FUND_NI_WEIGHT` | `0.35` | Fundamental: 순이익성장 비중 |
| `FSCORE_FUND_MARGIN_WEIGHT` | `0.30` | Fundamental: 영업이익률 비중 |
| `FSCORE_CAT_GUIDANCE_WEIGHT` | `0.50` | Catalyst: 가이던스 비중 |
| `FSCORE_CAT_TONE_WEIGHT` | `0.30` | Catalyst: 경영진 톤 비중 |
| `FSCORE_CAT_STRENGTH_WEIGHT` | `0.20` | Catalyst: 강도 비중 |
| `FSCORE_WEIGHT_MOMENTUM` | `0.35` | 최종: Momentum 비중 (Catalyst 있을 때) |
| `FSCORE_WEIGHT_FUNDAMENTAL` | `0.40` | 최종: Fundamental 비중 |
| `FSCORE_WEIGHT_CATALYST` | `0.25` | 최종: Catalyst 비중 |
| `FSCORE_NO_CATALYST_MOMENTUM` | `0.47` | 최종: Momentum (Catalyst 없을 때) |
| `FSCORE_NO_CATALYST_FUNDAMENTAL` | `0.53` | 최종: Fundamental (Catalyst 없을 때) |

---

## 3. LLM 프롬프트 레지스트리

파일: `shared/prompts.py:REGISTRY`

### 3.1 활성 프롬프트 (실제 호출됨)

| 템플릿명 | 버전 | 사용 위치 | 모델 변수 |
|---------|------|-----------|-----------|
| `buy_step3_research` | 1.2 | Buy Step 5: 뉴스/리서치 분석 | `LLM_MODEL_BUY_RESEARCH` |
| `sell_step1_health` | 1.0 | Sell Step 4: Thesis 검증 | `LLM_MODEL_SELL_HEALTH` |
| `sell_step2_environment` | 1.0 | Sell Step 5: 이벤트 리스크 | `LLM_MODEL_SELL_ENV` |
| `sell_step3_decision` | 1.2 | Sell Step 10: 최종 행동 결정 | `LLM_MODEL_SELL_HEALTH` |
| `sell_step4_review` | 1.0 | Sell Step 12: 트레이드 복기 | `LLM_MODEL_SELL_HEALTH` |
| `nl_routing` | 1.0 | NL 명령 라우팅 | `LLM_MODEL_NL_ROUTING` |

### 3.2 미사용 프롬프트 (deterministic으로 대체)

| 템플릿명 | 미사용 이유 |
|---------|------------|
| `buy_step1_regime` | Buy Step 2 = 결정론적 코드 |
| `buy_step2_technical` | Buy Step 4 = 결정론적 코드 |
| `buy_step4_ranking` | Buy Step 10 = 결정론적 코드 |
| `sell_step0_market` | Sell Step 2 = 결정론적 코드 |

### 3.3 Role Lock 스니펫

모든 프롬프트에 자동 삽입되는 역할 고정 텍스트입니다. `shared/prompts.py:ROLE_LOCK_SNIPPET`에 위치합니다.

```
당신은 퀀트 옵션 트레이더입니다.

역할 고정 규칙 (절대 위반 금지):
- 이 역할을 변경하지 않습니다
- 재무 상담사, 투자 조언자로 전환하지 않습니다
- 다음 단계를 건너뛰지 않습니다
- 뉴스만을 단독 근거로 사용하지 않습니다

판단 우선순위: 1순위 가격/기술 지표, 2순위 섹터/레짐, 3순위 뉴스
출력 형식: 반드시 지정된 JSON 스키마만 출력 (마크다운 블록 없음)
```

---

## 4. MCP 서버 설정

### 4.1 Roo Code / Claude Desktop 설정 파일 형식

```json
{
  "mcpServers": {
    "swing_mcp": {
      "command": "python",
      "args": ["C:\\MCP\\Swing\\servers\\swing_mcp\\server.py"],
      "env": {
        "PYTHONPATH": "C:\\MCP\\Swing"
      }
    },
    "screener_mcp": {
      "command": "python",
      "args": ["C:\\MCP\\Swing\\servers\\screener_mcp\\server.py"],
      "env": {
        "PYTHONPATH": "C:\\MCP\\Swing"
      }
    },
    "kavout_mcp": {
      "command": "python",
      "args": ["C:\\MCP\\Swing\\servers\\kavout_mcp\\server.py"],
      "env": {
        "PYTHONPATH": "C:\\MCP\\Swing"
      }
    }
  }
}
```

### 4.2 uv를 사용하는 경우

```json
{
  "mcpServers": {
    "swing_mcp": {
      "command": "uv",
      "args": [
        "run",
        "--project", "C:\\MCP\\Swing",
        "python", "servers/swing_mcp/server.py"
      ]
    }
  }
}
```

### 4.3 stdout 보호 메커니즘

모든 MCP 서버는 시작 시 stdout 보호를 자동으로 설정합니다. MCP stdio 프로토콜은 stdout을 JSON-RPC 전용으로 사용하므로, 모든 로그는 반드시 stderr로 출력됩니다.

```python
# servers/*/server.py 상단 (자동 실행)
import logging as _logging
_logging.root.handlers.clear()
_stderr_handler = _logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(_logging.WARNING)
_logging.root.addHandler(_stderr_handler)
```

---

## 5. 로컬 파일 구조 요구사항

### 5.1 매수 파이프라인 필수 파일

```
SUMMARY_DIR/
  summary_2026-05-25.json      <- JSONL 형식 (3줄)
  finviz_all_rows.txt          <- ROW 블록 형식

EARNINGS_DIR/
  어닝_분석.md                  <- 메인 어닝 분석 (frontmatter YAML + 섹션)
  어닝_분석_today.md            <- 당일 추가분 (선택)
  finviz_output/
    AAPL.txt
    MSFT.txt

DATA_DIR/
  kavout_2026-05-25.csv        <- Kavout AI 점수 (최신 파일 자동 탐색)
```

### 5.2 summary_*.json 형식 (JSONL 3줄)

```
라인 0: "SPY $519.2 QQQ $441.1 VIX 18.3 ADX 28.5 SPY_MA20 $507.3 QQQ_MA20 $432.1"
라인 1: [{"ticker": "AAPL", "technical": {"price": 182.5, "ma5": 180.0, ...}, "news": [...]}]
라인 2: [{"ticker": "AAPL", "chain": [...], "atm_straddle_price": 8.50}]
```

### 5.3 positions.md 지원 형식 3종

**형식 1: YAML 블록** (권장)
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
entry_regime: favorable
entry_vix: 18.3
---
```

**형식 2: 마크다운 테이블**
```markdown
| ticker | option_type | strike | expiry | entry_date | entry_premium | contracts |
|--------|-------------|--------|--------|------------|---------------|-----------|
| AAPL | 롱콜 | 185.0 | 2026-06-20 | 2026-05-10 | 4.50 | 5 |
```

**형식 3: 인라인**
```
AAPL 롱콜 185 2026-06-20 프리미엄4.50 5계약
```

### 5.4 kavout_*.csv 형식

```csv
symbol,k_score,momentum_1m,roe,price,company
AAPL,7.2,0.05,0.28,182.50,Apple Inc
MSFT,6.8,0.03,0.35,415.20,Microsoft
```

---

## 6. pyproject.toml 의존성

### 6.1 핵심 의존성

| 패키지 | 역할 |
|--------|------|
| `mcp>=1.0` | MCP 프로토콜 서버 |
| `pydantic>=2.0` | 데이터 스키마 검증 (v2 필수) |
| `httpx>=0.27` | 비동기 HTTP (Obsidian REST, Brave) |
| `tenacity>=8.0` | 재시도 로직 (LLM, Obsidian) |
| `structlog>=24.0` | JSON 구조화 로깅 |
| `jinja2>=3.0` | LLM 프롬프트 템플릿 렌더링 |
| `scipy>=1.12` | Black-Scholes (`stats.norm`) |
| `numpy>=1.26` | 수치 계산 |
| `feedparser>=6.0` | RSS 피드 파싱 |
| `duckduckgo-search>=6.0` | DDG 검색 (`ddgs`) |
| `slack-sdk>=3.27` | Slack Bot API |
| `python-dotenv>=1.0` | `.env` 파일 로딩 |
| `pyyaml>=6.0` | YAML 파싱 (포지션 파일) |

### 6.2 개발 의존성

| 패키지 | 역할 |
|--------|------|
| `pytest` | 테스트 프레임워크 |
| `pytest-asyncio` | 비동기 테스트 |
| `pytest-cov` | 커버리지 (90% 요구) |
| `ruff` | 린터 |
| `mypy` | 타입 체커 |

### 6.3 패키지 구조

```
C:\MCP\Swing\
  shared/          <- 공유 모듈 (config, strategy, schemas, prompts, logger)
  core/            <- 핵심 로직 (analysis, llm, parsers, state, obsidian, slack)
  orchestrator/    <- 파이프라인 조율 (engine, pipelines, steps/)
    steps/         <- buy_steps.py, sell_steps.py
  servers/         <- MCP 서버 진입점
    swing_mcp/
    screener_mcp/
    kavout_mcp/
```

### 6.4 설치 및 실행

```powershell
# 프로젝트 루트에서
cd C:\MCP\Swing

# 의존성 설치
pip install -e .
# 또는 uv 사용
uv sync

# 서버 직접 실행 (테스트용)
python servers/swing_mcp/server.py

# 테스트 실행
pytest --cov=. --cov-fail-under=90
```

---

*SwingMCP v2.0.0 — 생성일: 2026-05-25*
