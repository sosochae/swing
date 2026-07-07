# 장기투자 서버 구현 계획서

> 작성일: 2026-07-06  
> 기반 프로젝트: C:\MCP\Swing (SwingMCP)

---

## 1. 목표

스윙 트레이딩 파이프라인과 독립적으로, **장기투자(3~5년 보유)** 판단을 위한 종목 분석 보고서를 자동 생성하는 MCP 서버를 추가한다.

---

## 2. 아키텍처 결정

### 위치: `servers/` 안에 추가 (동일 프로젝트)

```
C:\MCP\Swing\
├── core/               ← 공통 로직 (재사용)
├── shared/             ← 설정, 스키마, 유틸
├── servers/
│   ├── swing_mcp/      ← 기존 스윙 서버
│   └── longterm_mcp/   ← 신규 장기투자 서버
└── orchestrator/
```

**이유:** `core/llm.py`, `core/obsidian.py`, `core/slack.py`, `core/api_fetcher.py` 등 공통 인프라를 재사용. `.env`, `.venv`도 공유. `swing_mcp`와 `longterm_mcp`는 서로 import하지 않고, 둘 다 `core/`만 참조하므로 혼재 없음.

---

## 3. 장기투자 판단 기준 (전체 목록)

### 3-1. 비즈니스 펀더멘털

**수익성**
- 매출 성장률 (YoY, 3년 CAGR, 5년 CAGR)
- 영업이익률 / 순이익률 / EBITDA 마진 추세
- FCF 및 FCF 마진
- ROE / ROA / ROIC
- EPS 성장 일관성

**재무 건전성**
- 부채비율 / 순부채(Net Debt)
- 이자보상배율
- 유동비율
- 현금성 자산 규모
- 자사주매입 / 배당 이력 / FCF 환원율

**성장 잠재력**
- TAM (Total Addressable Market) 크기
- 시장 점유율 변화 추세
- R&D 투자 비율
- 해외 매출 비중 및 확장 가능성

### 3-2. 경쟁 우위 (Moat)
- 브랜드 파워, 전환 비용, 네트워크 효과, 원가 우위, 특허/라이선스
- Porter's 5 Forces (신규 진입, 대체재, 구매자/공급자 교섭력, 기존 경쟁 강도)
- 섹터 대비 상대 성장성
- Moat 지속 가능성 판단

### 3-3. 경영진
- CEO 이력 및 재임 기간
- 내부자 지분율
- 경영진 보상 구조 (스톡옵션 vs 현금 vs 장기 성과급)
- 자본 배분 결정 이력 (M&A 성공률)
- 가이던스 신뢰도 (과거 발표 vs 실제 달성)
- ESG 리스크 (거버넌스, 소송, 규제 위반)

### 3-4. 밸류에이션
- PER / Forward PER / PBR / PSR / EV/EBITDA / PEG / FCF Yield
- DCF 적정가 (FCF 기반 간이 모델)
- 역사적 밸류에이션 범위 대비 현재 위치

### 3-5. 산업/섹터 구조
- 산업 성장 단계 (도입기/성장기/성숙기/쇠퇴기)
- 기술 파괴 위험
- 사이클리컬 여부
- 규제 리스크

### 3-6. 거시경제 / 매크로
- 금리 환경 감응도
- 인플레이션 전가 능력
- 달러 강약 영향
- 지정학 리스크 노출
- 경기침체 시 실적 방어력

### 3-7. 주가/시장 신호 (장기투자 보조 참고용 — 헤더 요약만)
- 52주 범위 대비 현재 위치
- 200일선 대비 위치
- 기관 매수/매도 동향
- 공매도 비율
- 애널리스트 컨센서스 / 평균 목표가
- 어닝 서프라이즈 이력

### 3-8. 정성 판단 (LLM 합성)
- 최근 실적발표 컨퍼런스콜 핵심 발언
- 최신 뉴스 (긍정/부정)
- 경쟁사 동향 영향
- 10년 후 존재 이유 종합 판단

---

## 4. 보고서 형식 (확정판)

### 표지 / 요약 헤더
```
종목명 / 섹터 / 생성일 / 데이터 기준일
현재가 / 시총 / 52주 범위
종합 점수 (100점) / 투자 의견 / 목표 가격 / 투자 등급
시장 신호 요약 (200일선, 공매도, 기관 순매수, 어닝 서프라이즈, 애널리스트)
```

### Section 1. 비즈니스 개요
- 사업 모델 요약 / 핵심 수익원 구조 (바차트) / TAM / 10년 후 존재 이유

### Section 2. 경쟁 우위 (Moat)
- 해자 유형별 점수 (5개 × 5점) / Porter's 5 Forces / 섹터 대비 성장성 / Moat 지속 가능성

### Section 3. 재무 퀄리티
- 수익성 추세 테이블 (5년: 매출/영업이익률/EBITDA 마진/순이익률/FCF/FCF 마진)
- 자본 효율성 (ROE/ROA/ROIC)
- 성장률 (매출 3년/5년 CAGR, EPS CAGR, 핵심 부문 CAGR, R&D 비율)
- 재무 건전성 (부채비율, 순부채, 이자보상배율, 유동비율, 현금성 자산)
- 주주환원 (자사주매입, 배당수익률, FCF 환원율)

### Section 4. 밸류에이션
- 멀티플 비교 테이블 (현재/업계평균/5년평균/판단): PER, Forward PER, PBR, PSR, EV/EBITDA, PEG, FCF Yield
- DCF 간이 추정 (FCF 성장률 가정, 적정가 vs 현재가)
- 역사적 PER 범위 대비 위치

### Section 5. 경영진
- 프로필 (CEO명, 재임기간, CFO)
- 신뢰도 지표 (내부자지분율, 가이던스 적중률, M&A 성공률)
- 보상 구조 (기본급/현금성과급/장기주식 비율, 베스팅 조건)
- ESG 리스크 (거버넌스, 소송, 노동, 환경)
- 자본배분 평가 (+/-)

### Section 6. 산업/매크로 환경
- 산업 포지셔닝 (성장 단계, 시장 점유율)
- 경쟁 구도 (경쟁사별 위협 수준)
- 매크로 감응도 (금리, 인플레이션 전가력, 달러, 경기침체 방어력, 지정학)

### Section 7. 리스크 매트릭스
- 리스크 목록 (발생확률 × 임팩트 → 위험도 별점)
- 치명적 리스크 여부 / 주요 모니터링 항목

### Section 8. 정성 분석 (LLM 합성)
- 최근 실적발표 핵심 포인트 / 경영진 톤
- 주요 최신 뉴스 (+/-)
- 종합 정성 판단

### Section 9. 종합 스코어카드
- 6개 영역별 점수 (각 20점 만점): 비즈니스 퀄리티 / 경쟁 우위 / 재무 건전성 / 밸류에이션 / 경영진 신뢰도 / 리스크 관리
- 투자 판단 매트릭스 (퀄리티 × 밸류에이션)
- 최종 의견 박스 (투자 의견 / 목표 기간 / 목표 수익률 / 포지션 크기 / 재검토 트리거)

---

## 5. 신규 파일 목록 및 역할

| # | 파일 | 역할 |
|---|------|------|
| 1 | `shared/longterm_schemas.py` | 장기투자 전용 dataclass 모음 |
| 2 | `shared/config.py` *(수정)* | longterm 설정 블록 추가 |
| 3 | `core/longterm_fetcher.py` | yfinance 5년 재무 수집 + 정량 계산 |
| 4 | `core/longterm_analyzer.py` | LLM 정성 분석 4단계 (병렬) |
| 5 | `core/longterm_scorer.py` | 규칙 기반 스코어링 + 투자 판단 |
| 6 | `core/longterm_report.py` | 보고서 렌더링 + Obsidian 저장 + Slack |
| 7 | `servers/longterm_mcp/__init__.py` | 패키지 초기화 |
| 8 | `servers/longterm_mcp/server.py` | MCP 서버 진입점 (Tool 3개) |

---

## 6. 구현 의존 순서

```
[1] longterm_schemas.py
        ↓
[2] config.py (수정)
        ↓
[3] longterm_fetcher.py ──────────────────┐
        ↓                                 │
[4] longterm_analyzer.py (병렬 LLM 4개)  │
        ↓                                 │
[5] longterm_scorer.py ←──────────────────┘
        ↓
[6] longterm_report.py
        ↓
[7] longterm_mcp/server.py
```

---

## 7. 데이터 흐름

```
ticker 입력
  │
  ├─ [fetcher]
  │     yfinance: 5년 재무제표 (financials, balance_sheet, cashflow)
  │     계산: CAGR/ROA/ROIC/EBITDA마진/FCF마진/이자보상배율/DCF/역사적PER
  │     기존 fetch_stock_detail() 재사용: PE/마진/애널리스트/공매도/ROE 등
  │
  ├─ [analyzer] ← asyncio.gather로 병렬 실행
  │     Phase 1: Moat + Porter 5 Forces  (LLM_MODEL_LT_MOAT)
  │     Phase 2: 경영진 + ESG            (LLM_MODEL_LT_MANAGEMENT)
  │     Phase 3: 산업/매크로 + 리스크    (LLM_MODEL_LT_INDUSTRY)
  │     Phase 4: 뉴스/컨퍼런스콜 종합    (LLM_MODEL_LT_QUALITATIVE)
  │     웹 리서치: _search_and_fetch() 재사용
  │
  ├─ [scorer]
  │     6개 영역 규칙 기반 채점 (LLM 없음)
  │     투자 판단 매트릭스 → 의견/목표가/포지션 크기/재검토 트리거
  │
  └─ [report]
        9개 섹션 렌더링
        Obsidian 저장: longterm/reports/{ticker}_{date}.md
        Slack 알림: #longterm-investing
```

---

## 8. MCP Tool 목록 (3개)

| Tool | 설명 |
|------|------|
| `run_longterm_pipeline(ticker)` | 전체 파이프라인 실행 → 보고서 생성 |
| `get_longterm_report(ticker)` | 기존 보고서 Obsidian에서 읽어 반환 |
| `health_check()` | yfinance / Obsidian / LLM API 유효성 확인 |

---

## 9. 기존 스윙 코드 재사용 목록

### 그대로 재사용 (수정 없음)

| 모듈 | 재사용 항목 |
|------|-----------|
| `core/api_fetcher.py` | `fetch_stock_detail()` — PE/마진/ROE/FCF/애널리스트/공매도/52주/SMA200 등 이미 수집됨 |
| `core/api_fetcher.py` | `_f()`, `_pct()`, `_million()` 헬퍼 함수 |
| `core/research_agent.py` | `_fetch_earnings_data_sync()` — 어닝 서프라이즈 이력 4분기 |
| `core/research_agent.py` | `_search_and_fetch()` — DDG+Brave 웹 리서치 |
| `core/research_agent.py` | `_save_to_obsidian()`, `_unwrap_json()`, `_build_report_header()` |
| `core/llm.py` | `call_llm()`, `call_ddg_search()`, `call_brave_search()`, `rank_and_pick()`, `fetch_url_as_markdown()` |
| `core/obsidian.py` | `ObsidianClient` |
| `core/slack.py` | `SlackClient` |
| `shared/config.py` | API 키, LLM 모델, Obsidian/Slack 설정 |
| `servers/swing_mcp/server.py` | SSL CA 패치 + logging boilerplate (상단 ~40줄) |

### `longterm_fetcher.py`에서 새로 작성할 부분

`fetch_stock_detail()`에 없는 항목만 추가:

```
ticker.financials      → 5년 연간 매출/영업이익/순이익/EPS 시리즈
ticker.balance_sheet   → 자본/부채 상세 (ROIC, 이자보상배율, 유동비율 계산)
ticker.cashflow        → FCF 시리즈 (영업CF - CAPEX), FCF 마진 추세
ticker.info 추가 키    → PBR, EV/EBITDA, PSR, 기관지분율, R&D비용
CAGR 계산 (3년/5년)   → 연간 시리즈에서 직접 계산
ROA / ROIC             → 순이익/총자산, EBIT/투하자본
DCF 적정가             → FCF 5년 성장률 가정 기반 간이 모델
역사적 PER 범위        → 5년 주가 + EPS 데이터 기반 계산
```

### 가져오지 않을 것 (스윙 전용)

- RSI, MACD, ADX, 볼린저밴드 등 단기 기술지표 계산 함수
- 4H/1H 장중 지표, 주봉 지표
- Pivot, Fibonacci, VWAP 등 가격선 계산
- `StockDetail` 스키마 (스윙 전용 필드 가득)
- `PipelineEngine`, `BuyPipeline` 등 오케스트레이터

---

## 10. 설정 추가 항목 (shared/config.py)

```python
# ── 장기투자 LLM 모델 ────────────────────────────────────────
LLM_MODEL_LT_MOAT:        str  # Moat + Porter 5 Forces 분석
LLM_MODEL_LT_MANAGEMENT:  str  # 경영진 + ESG 분석
LLM_MODEL_LT_INDUSTRY:    str  # 산업/매크로 + 리스크 분석
LLM_MODEL_LT_QUALITATIVE: str  # 뉴스/컨퍼런스콜 종합 판단

# ── 장기투자 경로 및 채널 ────────────────────────────────────
LT_NOTE_PATH_TEMPLATE: str  # "longterm/reports/{ticker}_{date}.md"
LT_SLACK_CHANNEL:      str  # "#longterm-investing"

# ── DCF 파라미터 ─────────────────────────────────────────────
LT_HISTORY_YEARS:      int    # yfinance 수집 연수 (기본 5)
LT_DCF_GROWTH_RATE:    float  # FCF 5년 성장률 가정 (기본 0.08)
LT_DCF_TERMINAL_RATE:  float  # 터미널 성장률 (기본 0.03)
LT_DCF_DISCOUNT_RATE:  float  # 할인율 WACC (기본 0.09)
```

---

## 11. 스코어링 기준

### 6개 영역 채점 (각 20점 만점)

| 영역 | 핵심 지표 | 채점 방식 |
|------|----------|---------|
| 비즈니스 퀄리티 | FCF 마진, ROIC, 매출 성장성 | 정량 계산 |
| 경쟁 우위(Moat) | MoatAnalysis 해자 점수 합산 | 정량 변환 |
| 재무 건전성 | 이자보상배율, 부채비율, FCF 양수 | 정량 계산 |
| 밸류에이션 | PEG, DCF 괴리율, 역사적 PER 위치 | 정량 계산 |
| 경영진 신뢰도 | ManagementAnalysis.score | 변환 |
| 리스크 관리 | 치명적 리스크 수, 최고 위험도 | 차감 방식 |

### 투자 판단 매트릭스

| 총점 | 의견 |
|------|------|
| 85점 이상 | 강력 매수 |
| 70~84점 | 매수 |
| 55~69점 | 장기 보유 |
| 40~54점 | 관망 |
| 39점 이하 | 매도 |

---

*이 계획서는 `C:\MCP\Swing\docs\longterm_plan.md`에 저장됨*
