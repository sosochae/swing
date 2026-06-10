"""
shared/schemas.py
=================
전체 Pydantic v2 스키마 정의 (SwingMCP 통합 스펙 v2.0.0 섹션 6 기반)

모든 입·출력 데이터 구조, 파이프라인 컨텍스트, Greeks, 레짐, 시나리오,
포지션, 재큐, 감사 로그 스키마를 포함합니다.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ─────────────────────────────────────────────────────────────
# 1. 시장 거시 데이터 (summary_*.json)
# ─────────────────────────────────────────────────────────────

class SummaryEvent(BaseModel):
    """향후 이벤트 항목"""
    date: datetime
    type: str             # "경제지표" | "실적" 등
    name: str
    importance: Literal["HIGH", "MED", "LOW"]
    days_until: int
    eps_estimate: float | None = None        # 예상 EPS (주당)
    revenue_estimate_b: float | None = None  # 예상 매출 (단위: 십억 달러)


class SummaryRiskParams(BaseModel):
    """리스크 파라미터"""
    total_capital: float = 3000.0
    max_per_position: float = 1000.0
    commission_per_contract: float = 5.0
    target_holding_days: str = "3~5일"
    capital_in_use: float = 0.0
    remaining_cash: float = 3000.0


class SummaryMacro(BaseModel):
    """거시 지표"""
    current_session: str | None = None
    sp500: float = 0.0
    sp500_change: float = 0.0
    sp500_ma20: float = 0.0
    nasdaq: float = 0.0
    nasdaq_change: float = 0.0
    nasdaq_ma20: float = 0.0
    spy: float = 0.0
    spy_ma20: float = 0.0
    qqq: float = 0.0
    qqq_ma20: float = 0.0
    vix: float = 20.0
    vix_ma20: float = 20.0
    dxy: float = 100.0
    dxy_ma20: float = 100.0
    yield_10y: float = 4.5
    gold: float = 3000.0
    oil_wti: float = 70.0
    soxx: float = 400.0
    soxx_ma20: float = 400.0
    fear_greed: int = Field(50, ge=0, le=100)
    fear_greed_label: str = "Neutral"
    fed_funds_rate: float = 4.5
    cpi_yoy: float = 3.0
    unemployment: float = 4.0
    pce: float = 3.0
    # ── SPY 4H 지표 (단기 방향 판정 정밀화) ───────────────────────────
    spy_di_plus_4h: float | None = None    # SPY 4H DI+ (단기 상승 모멘텀)
    spy_di_minus_4h: float | None = None   # SPY 4H DI- (단기 하락 모멘텀)
    spy_macd_hist_4h: float | None = None  # SPY 4H MACD 히스토그램


class TickerTechnical(BaseModel):
    """종목별 기술 지표"""
    price: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    avg_volume_ratio: float = 1.0   # RVOL
    high_52w: float = 0.0
    low_52w: float = 0.0
    position_52w: float = 0.0       # 52주 범위 내 위치 (%)
    ma5: float = 0.0
    ma20: float = 0.0
    ma50: float = 0.0
    ma60: float = 0.0
    ma200: float = 0.0
    ma_aligned: bool = False         # 완전 정배열 여부
    rsi14: float = 50.0
    bb_upper: float = 0.0
    bb_mid: float = 0.0
    bb_lower: float = 0.0
    bb_position: str = "mid"         # "upper_break" | "upper" | "mid" | "lower"
    adx14: float = 20.0
    di_plus: float = 0.0             # DMI+ (상승 방향성 지수)
    di_minus: float = 0.0            # DMI- (하락 방향성 지수)
    obv_direction: str = "neutral"   # "up" | "down" | "neutral"
    macd_line: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    macd_cross: str = "none"         # "golden" | "death" | "none"
    support1: float | None = None
    support2: float | None = None
    resistance1: float | None = None
    resistance2: float | None = None
    # 최근 3일 OHLC 히스토리 (오래된 순 정렬)
    # 예: [{"date": "2026-05-20", "open": 734.96, "high": 735.68,
    #        "low": 700.66, "close": 731.99, "volume": 48827400, "rvol": 0.97}]
    recent_ohlc: list[dict[str, Any]] = Field(default_factory=list)


class TickerOptions(BaseModel):
    """종목별 옵션 요약"""
    pc_ratio: float = 1.0
    total_call_oi: int = 0
    total_put_oi: int = 0
    implied_move_near: float = 0.0      # 가까운 만기 내재 움직임 %
    implied_move_far: float = 0.0       # 먼 만기 내재 움직임 %
    max_pain_near: float = 0.0
    max_pain_far: float = 0.0
    atm_straddle_price: float = 0.0
    call_wall: float | None = None      # 최대 콜 OI strike (단기 상단 저항)
    put_wall:  float | None = None      # 최대 풋 OI strike (단기 하단 지지)
    gex_flip:  float | None = None      # GEX 부호 전환 strike (딜러 헤지 방향 전환)
    chain: list[dict[str, Any]] = Field(default_factory=list)


class TickerValuation(BaseModel):
    """종목 밸류에이션 데이터"""
    pe_ttm: float | None = None
    forward_pe: float | None = None
    peg: float | None = None
    pb: float | None = None
    ps_ttm: float | None = None
    eps_ttm: float | None = None
    eps_growth_yoy: float | None = None
    revenue_growth_yoy: float | None = None
    roe: float | None = None
    roa: float | None = None
    net_margin: float | None = None
    op_margin: float | None = None
    debt_ratio: float | None = None
    beta: float | None = None
    competitors: list[str] = Field(default_factory=list)


class TickerSummary(BaseModel):
    """종목 통합 데이터"""
    ticker: str
    company: str = ""
    sector: str = ""
    industry: str = ""
    country: str = ""
    market_cap: float = 0.0          # USD
    pe_ratio: float | None = None
    technical: TickerTechnical = Field(default_factory=TickerTechnical)
    valuation: TickerValuation = Field(default_factory=TickerValuation)
    news: list[dict[str, str]] = Field(default_factory=list)
    insider: list[dict[str, Any]] = Field(default_factory=list)
    earnings: list[dict[str, Any]] = Field(default_factory=list)


class SummaryData(BaseModel):
    """summary_*.json 전체 파싱 결과"""
    snapshot_timestamp: datetime
    processed_tickers: list[str] = Field(default_factory=list)
    macro: SummaryMacro = Field(default_factory=SummaryMacro)
    events: list[SummaryEvent] = Field(default_factory=list)
    risk_params: SummaryRiskParams = Field(default_factory=SummaryRiskParams)
    tickers: dict[str, TickerSummary] = Field(default_factory=dict)
    options: dict[str, TickerOptions] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# 2. 종목 데이터 스키마
# ─────────────────────────────────────────────────────────────

class StockDetail(BaseModel):
    """yfinance API 실시간 주식 스냅샷 (가격·기술지표·밸류에이션·애널리스트)"""
    ticker: str
    forward_pe: float | None = None
    peg: float | None = None
    target_price: float | None = None      # 애널리스트 목표주가 (컨센서스 평균/중간)
    target_price_high: float | None = None  # 애널리스트 최고 목표주가 (Street-High)
    recom: float | None = None             # 1.0=Strong Buy ~ 5.0=Sell
    beta: float | None = None
    short_float_pct: float | None = None   # "2.20%" → 2.20
    insider_trans_pct: float | None = None # "-6.19%" → -6.19 (6개월 내부자 순매수 %)
    eps_surprise_pct: float | None = None  # 최근 분기 EPS 서프라이즈 %
    sales_surprise_pct: float | None = None
    gross_margin_pct: float | None = None
    op_margin_pct: float | None = None
    profit_margin_pct: float | None = None
    eps_next_5y_pct: float | None = None   # 향후 5년 EPS 성장률 %
    # 손익계산서 (TTM, 단위: M USD)
    revenue_ttm: float | None = None
    gross_profit_ttm: float | None = None
    net_income_ttm: float | None = None
    # ── 펀더멘털 스크리너 추가 필드 ─────────────────────────────
    price: float | None = None             # 현재가
    change_pct: float | None = None        # 당일 등락률 (%)
    rsi14: float | None = None             # RSI(14)
    rel_volume: float | None = None        # 상대 거래량 (평균 대비 배수)
    w52_high_pct: float | None = None      # 52주 고점 대비 % (음수: 고점 아래, e.g. -20.0)
    w52_low_pct: float | None = None       # 52주 저점 대비 % (양수: 저점 위,  e.g. +28.2)
    sma20_pct: float | None = None         # 현재가 vs SMA20 % (e.g. -12.66)
    sma50_pct: float | None = None         # 현재가 vs SMA50 %
    sma200_pct: float | None = None        # 현재가 vs SMA200 %
    # 성장률 (INCOME STATEMENT 두 기간 비교로 계산)
    revenue_growth_yoy: float | None = None   # 매출 YoY 성장률 %
    net_income_growth_yoy: float | None = None  # 순이익 YoY 성장률 %
    # ── 기술 지표 실제값 (api_fetcher 계산) ───────────────────────────
    sma5_val: float | None = None             # 5일 SMA 달러값
    sma10_val: float | None = None            # 10일 SMA 달러값
    sma20_val: float | None = None            # 20일 SMA 달러값
    sma50_val: float | None = None            # 50일 SMA 달러값
    sma60_val: float | None = None            # 60일 SMA 달러값 (ma60 브릿지용)
    sma200_val: float | None = None           # 200일 SMA 달러값
    bb_upper: float | None = None             # 볼린저밴드 상단
    bb_mid: float | None = None               # 볼린저밴드 중앙 (≈SMA20)
    bb_lower: float | None = None             # 볼린저밴드 하단
    macd_line: float | None = None            # MACD 라인
    macd_signal: float | None = None          # MACD 시그널
    macd_hist: float | None = None            # MACD 히스토그램
    adx: float | None = None                  # ADX 값 (방향성 강도)
    di_plus: float | None = None              # DI+ (상승 방향 지수)
    di_minus: float | None = None             # DI- (하락 방향 지수)
    atr: float | None = None                  # ATR(14) 달러값
    pivot: float | None = None                # 피벗 포인트 (전일 HLC/3)
    pivot_r1: float | None = None             # 저항1
    pivot_r2: float | None = None             # 저항2
    pivot_r3: float | None = None             # 저항3 = R2 + (R1 - S1)
    pivot_s1: float | None = None             # 지지1
    pivot_s2: float | None = None             # 지지2
    pivot_s3: float | None = None             # 지지3 = S2 - (R1 - S1)
    # ── 주봉 지표 ────────────────────────────────────────────────────
    weekly_sma5_val: float | None = None      # 주봉 5주 SMA
    weekly_pivot_p: float | None = None       # 주봉 피벗 중심 (진입 타이밍 기준)
    weekly_pivot_s1: float | None = None      # 주봉 피벗 S1
    weekly_pivot_s2: float | None = None      # 주봉 피벗 S2 (스윙 T3 기준)
    weekly_pivot_r1: float | None = None      # 주봉 피벗 R1 (스윙 T3 기준)
    weekly_pivot_r2: float | None = None      # 주봉 피벗 R2 (최대 목표)
    weekly_adx: float | None = None           # 주봉 ADX (장기 추세 강도)
    weekly_di_plus: float | None = None       # 주봉 DI+ (장기 상승 모멘텀)
    weekly_di_minus: float | None = None      # 주봉 DI- (장기 하락 모멘텀)
    weekly_rsi: float | None = None           # 주봉 RSI
    weekly_macd_hist: float | None = None     # 주봉 MACD 히스토그램
    # ── 4시간봉 지표 ─────────────────────────────────────────────────
    rsi_4h: float | None = None               # 4H RSI(14)
    macd_hist_4h: float | None = None         # 4H MACD 히스토그램 (모멘텀 방향 핵심)
    adx_4h: float | None = None               # 4H ADX
    di_plus_4h: float | None = None           # 4H DI+
    di_minus_4h: float | None = None          # 4H DI-
    vwap_4h: float | None = None              # 4H VWAP (진입 구간 상단 기준)
    pivot_p_4h: float | None = None           # 4H 피벗 중심값 (단기 진입/저항 기준)
    pivot_s3_4h: float | None = None          # 4H 피벗 S3 (진입 구간 하단 정밀화)
    pivot_r3_4h: float | None = None          # 4H 피벗 R3
    # ── 1시간봉 지표 ─────────────────────────────────────────────────
    rsi_1h: float | None = None               # 1H RSI(14) (극과매도 감지)
    bb_lower_1h: float | None = None          # 1H 볼린저밴드 하단 (단기 지지)
    sma5_1h: float | None = None              # 1H SMA5
    sma10_1h: float | None = None             # 1H SMA10 (진입 구간 계산 핵심)
    sma20_1h: float | None = None             # 1H SMA20 (진입 구간 계산 핵심)
    macd_hist_1h: float | None = None         # 1H MACD 히스토그램 (단기 반등 감지)
    macd_hist_1h_prev: float | None = None    # 1H MACD Hist 전봉 (④ 양전환 감지)
    bb_pct_b: float | None = None             # ⑧ 볼린저밴드 %B (0~1, <0.2=과매도)
    # ── 4H SMA (멀티TF 클러스터 ⑥) ──────────────────────────────────
    sma5_4h: float | None = None              # 4H SMA5
    sma10_4h: float | None = None             # 4H SMA10
    sma20_4h: float | None = None             # 4H SMA20
    # ── 전봉 ADX/DI (⑤ ADX 꺾임 + DI 교차) ──────────────────────────
    adx_prev: float | None = None             # 전봉 ADX (꺾임 감지용)
    di_plus_prev: float | None = None         # 전봉 DI+ (교차 감지용)
    di_minus_prev: float | None = None        # 전봉 DI-
    # ── 피보나치 레벨 (①② 되돌림 + 확장) ────────────────────────────
    swing_high_30d: float | None = None       # 최근 30일 스윙 고점 (Fib 기준)
    swing_low_30d: float | None = None        # 최근 30일 스윙 저점 (Fib 기준)
    fib_38_2: float | None = None             # 38.2% 되돌림 지지/저항
    fib_50_0: float | None = None             # 50.0% 되돌림 (진입 핵심)
    fib_61_8: float | None = None             # 61.8% 되돌림 (강한 지지/저항)
    fib_ext_100: float | None = None          # 100% 확장 목표 (T1 보조)
    fib_ext_162: float | None = None          # 161.8% 확장 목표 (T3 보조)
    # ── 앵커 VWAP (⑦ 스윙 저점 기준) ───────────────────────────────
    vwap_anchored: float | None = None        # 앵커 VWAP (최근 스윙 저점 기준)
    # ── 캔들 패턴 (⑩) ───────────────────────────────────────────────
    candle_signal: str | None = None          # "hammer"|"engulfing"|"morning_star"|"none"
    # ── Parabolic SAR (⑭) ───────────────────────────────────────────
    parabolic_sar: float | None = None        # Parabolic SAR 현재값
    sar_direction: str | None = None          # "up"|"down"
    # ── Camarilla Pivot (⑯) ─────────────────────────────────────────
    cam_h3: float | None = None               # Camarilla H3 (타이트 저항)
    cam_h4: float | None = None               # Camarilla H4 (추세 전환 저항)
    cam_l3: float | None = None               # Camarilla L3 (타이트 지지)
    cam_l4: float | None = None               # Camarilla L4 (추세 전환 지지)
    # ── 전일/전주 고점/저점 (E: Previous Period H/L) ───────────────────
    prev_day_high: float | None = None        # 전일 고점 (당일 저항)
    prev_day_low: float | None = None         # 전일 저점 (당일 지지)
    prev_week_high: float | None = None       # 전주 고점 (주간 저항)
    prev_week_low: float | None = None        # 전주 저점 (주간 지지)
    # ── VWAP 표준편차 밴드 (D: VWAP Std Dev Bands) ───────────────────
    vwap_std1_upper: float | None = None      # VWAP + 1σ (정상 거래 상한)
    vwap_std1_lower: float | None = None      # VWAP - 1σ (정상 거래 하한)
    vwap_std2_upper: float | None = None      # VWAP + 2σ (과매수 경계)
    vwap_std2_lower: float | None = None      # VWAP - 2σ (과매도 경계)
    # ── EMA 단기선 ────────────────────────────────────────────────────
    ema9:  float | None = None                # EMA 9일 (초단기 지지/저항)
    ema21: float | None = None                # EMA 21일 (단기 추세선)
    ema50:  float | None = None               # EMA 50일 (중기 추세선 / 기관 기준)
    ema100: float | None = None               # EMA 100일 (중장기 추세선)
    ema200: float | None = None               # EMA 200일 (장기 추세선 / 골든·데스크로스 기준)
    # ── 52주 고점/저점 ──────────────────────────────────────────────────
    w52_high: float | None = None             # 52주 고점 (연간 최대 저항)
    w52_low:  float | None = None             # 52주 저점 (연간 최대 지지)
    # ── Keltner Channel (EMA20 ± 2×ATR) ─────────────────────────────
    keltner_upper: float | None = None        # Keltner 상단 (과매수/채널 돌파 기준)
    keltner_lower: float | None = None        # Keltner 하단 (과매도/채널 하향 기준)
    # ── Donchian Channel 20일 ────────────────────────────────────────
    donchian_20_upper: float | None = None    # 20일 최고가 (단기 저항)
    donchian_20_lower: float | None = None    # 20일 최저가 (단기 지지)
    # ── HV 기반 기대이동폭 ────────────────────────────────────────────
    hv30: float | None = None                 # 30일 실현 변동성 % (연환산)
    hv_move_5d:  float | None = None          # HV 기반 5일 기대이동폭 ($)
    hv_move_15d: float | None = None          # HV 기반 15일 기대이동폭 ($)
    # ── Monthly Pivot (전월 OHLC) ────────────────────────────────────
    monthly_pivot:  float | None = None       # 월간 피벗 (P)
    monthly_pivot_r1: float | None = None     # 월간 R1
    monthly_pivot_r2: float | None = None     # 월간 R2
    monthly_pivot_s1: float | None = None     # 월간 S1
    monthly_pivot_s2: float | None = None     # 월간 S2
    # ── FVG (Fair Value Gap) ──────────────────────────────────────────
    fvg_bull_top:    float | None = None      # 상승 FVG 상단 (미채움 공정가치 구간)
    fvg_bull_bottom: float | None = None      # 상승 FVG 하단
    fvg_bear_top:    float | None = None      # 하락 FVG 상단
    fvg_bear_bottom: float | None = None      # 하락 FVG 하단
    # ── Gap Fill ─────────────────────────────────────────────────────
    gap_up_fill:   float | None = None        # 미채움 갭 업 레벨 (전날 종가)
    gap_down_fill: float | None = None        # 미채움 갭 다운 레벨 (전날 종가)
    # ── 애널리스트 의견 집계 ───────────────────────────────────────────
    analyst_buy: int | None = None            # 매수(Strong Buy + Buy) 수
    analyst_hold: int | None = None           # 보유(Hold) 수
    analyst_sell: int | None = None           # 매도(Underperform + Sell) 수
    # ── 추가 펀더멘털 ──────────────────────────────────────────────────
    trailing_pe: float | None = None          # P/E (TTM)
    eps_ttm: float | None = None              # EPS (TTM, USD)
    roe_pct: float | None = None              # ROE (%)
    debt_equity: float | None = None          # 부채/자기자본 비율
    fcf_ttm: float | None = None              # 잉여현금흐름 TTM (M USD)
    market_cap: float | None = None           # 시가총액 (USD, kavout_output 파싱)


class FinvizRow(BaseModel):
    """finviz_all_rows.txt 개별 행"""
    rank: int
    ticker: str = Field(pattern=r"^[A-Z]{1,5}$")
    company_name: str
    sector: str
    industry: str
    country: str
    market_cap: float           # 단위: USD (B/M 변환 완료)
    pe_ratio: float | None
    price: float
    change_pct: float           # '4.32%' → 4.32
    volume: int                 # '213,419,086' → 213419086

    @field_validator("ticker", mode="before")
    @classmethod
    def ticker_upper(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("change_pct", mode="before")
    @classmethod
    def parse_change(cls, v: Any) -> float:
        if isinstance(v, str):
            return float(v.replace("%", "").strip())
        return float(v)


# ─────────────────────────────────────────────────────────────
# 3. 어닝 분석 스키마
# ─────────────────────────────────────────────────────────────

class EarningsAnalysis(BaseModel):
    """어닝_분석.md frontmatter + 섹션"""
    ticker: str = Field(pattern=r"^[A-Z]{1,5}$")
    quarter: str
    date: date
    business_model: str = ""
    industry: str = ""
    strategy_changes: str = ""
    management_confidence: str = ""


# ─────────────────────────────────────────────────────────────
# 4. 포지션 스키마
# ─────────────────────────────────────────────────────────────

class PartialExit(BaseModel):
    """부분 청산 기록"""
    exit_date: date
    contracts_closed: int
    exit_premium: float
    realized_pnl: float
    reason: str = ""


class Position(BaseModel):
    """positions.md 개별 포지션"""
    ticker: str = Field(pattern=r"^[A-Z]{1,5}$")
    option_type: Literal["롱콜", "롱풋"]
    strike: float
    expiry: date
    entry_date: date
    entry_premium: float
    entry_stock_price: float
    original_contracts: int
    remaining_contracts: int
    partial_exits: list[PartialExit] = Field(default_factory=list)
    trailing_stop: float = 0.0
    entry_regime: str = ""             # 진입 시 레짐 상태 (favorable/borderline/unfavorable)
    entry_vix: float = 0.0             # 진입 시 VIX
    peak_premium: float = 0.0          # 프리미엄 고점 (트레일링 스탑 기준)
    entry_rationale: str = ""
    thesis: str = ""
    invalidation_conditions: list[str] = Field(default_factory=list)
    last_reviewed: date = Field(default_factory=date.today)
    conviction_score: float = Field(0.5, ge=0.0, le=1.0)

    @property
    def dte(self) -> int:
        return max(0, (self.expiry - date.today()).days)

    @property
    def total_cost(self) -> float:
        return self.entry_premium * 100 * self.original_contracts


# ─────────────────────────────────────────────────────────────
# 5. 시장 레짐 스키마
# ─────────────────────────────────────────────────────────────

class RegimeComponent(BaseModel):
    """레짐 개별 판정 항목"""
    value: float | str
    status: Literal["pass", "borderline", "fail"]
    reason: str = ""


class MarketRegime(BaseModel):
    """시장 레짐 판정 결과"""
    regime_status: Literal["favorable", "borderline", "unfavorable"]
    allowed_direction: Literal["long_call", "long_put", "both", "none"]
    trend_strength: RegimeComponent
    volatility: RegimeComponent
    index_trend: RegimeComponent
    risk_factors: list[str] = Field(default_factory=list)
    trend_confidence: float = Field(ge=0.0, le=1.0)
    regime_confidence: float = Field(ge=0.0, le=1.0)
    adx_source: Literal["direct", "ma_proxy"] = "direct"


# ─────────────────────────────────────────────────────────────
# 6. 기술 분석 스코어
# ─────────────────────────────────────────────────────────────

class TechnicalScore(BaseModel):
    """Step 4 기술 분석 결과"""
    ticker: str
    direction: Literal["long_call", "long_put"]
    ma_alignment: Literal["bullish", "bearish", "mixed"]
    adx_score: float        # 0~25
    rsi_score: float        # 0~25
    macd_score: float       # 0~25
    rvol_score: float       # 0~25
    raw_score: float        # 0~100
    final_score: float      # Devil's Advocate 차감 후
    trend_confirmed: bool
    capital_flow_confirmed: bool
    obv_ok: bool
    option_flow_ok: bool
    darkpool_ok: bool
    signal_count: int = Field(default=0, ge=0)  # 0~8 (추세4 + 자금유입4; kavout/finviz 보정 포함)


# ─────────────────────────────────────────────────────────────
# 7. Greeks 스키마
# ─────────────────────────────────────────────────────────────

class Greeks(BaseModel):
    """옵션 Greeks"""
    delta: float = Field(ge=0.0, le=1.0)
    gamma: float = 0.0
    theta: float = 0.0          # 일일 감소 (음수)
    vega: float = 0.0
    iv: float = 0.0             # Implied Volatility
    ivr: float = 0.0            # IV Rank 0~100

    @property
    def delta_dollar(self) -> float:
        """델타 1포인트 이동 시 달러 P&L"""
        return self.delta * 100


class OptionChainEntry(BaseModel):
    """개별 옵션 체인 항목"""
    expiry: date
    option_type: Literal["call", "put"]
    strike: float
    oi: int = 0
    volume: int = 0
    iv: float = 0.0
    ivr: float = 0.0
    mid_price: float = 0.0
    spread_pct: float = 0.0
    delta: float = 0.0
    theta: float = 0.0
    dte: int = 0
    is_anomaly: bool = False    # 이상 플로우


class OptionValidity(BaseModel):
    """Step 7 옵션 유효성 검증 결과"""
    ticker: str
    strike: float
    expiry: date
    direction: Literal["long_call", "long_put"]
    delta_ok: bool
    ivr_ok: bool
    ivr_warning: bool       # 50~70% 경고
    oi_ok: bool
    oi_warning: bool        # 500~999 경고
    spread_ok: bool
    dte_ok: bool
    is_valid: bool
    exclusion_reason: str = ""
    mid_price: float = 0.0       # 옵션 중간가 (프리미엄 추정용)
    oi: int = 0                  # 미결제약정 (선택 이유 표시용)
    greeks: Greeks = Field(default_factory=Greeks)


# ─────────────────────────────────────────────────────────────
# 8. 시나리오 스키마
# ─────────────────────────────────────────────────────────────

class ScenarioCase(BaseModel):
    """개별 시나리오 케이스"""
    name: Literal["bullish", "base", "bearish"]
    probability: float = Field(ge=0.0, le=1.0)
    stock_move_pct: float
    target_stock_price: float
    iv_change_assumption: str
    expected_option_value: float
    gross_profit: float
    net_profit: float           # 수수료 차감
    rationale: str = ""


class Scenario(BaseModel):
    """Step 8 시나리오 분석 전체"""
    ticker: str
    direction: Literal["long_call", "long_put"]
    strike: float
    expiry: date
    contracts: int
    total_investment: float
    commission_total: float
    implied_move_pct: float
    bullish: ScenarioCase
    base: ScenarioCase
    bearish: ScenarioCase
    expected_value: float
    stop_loss_premium: float
    target_premium_1st: float   # 1차 익절 (50% 수익)
    target_premium_2nd: float = 0.0    # 2차 익절 (100% 수익)
    target_premium_3rd: float = 0.0    # 3차 익절 (150% 수익)
    trailing_stop_pct: float = 20.0
    delta_gap_risk: str = ""    # Delta Gap 경고 문구

    @model_validator(mode="after")
    def validate_probability_sum(self) -> "Scenario":
        total = self.bullish.probability + self.base.probability + self.bearish.probability
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Probability sum must be 1.0, got {total:.4f}")
        return self


# ─────────────────────────────────────────────────────────────
# 9. 포트폴리오 노출 스키마
# ─────────────────────────────────────────────────────────────

class PortfolioExposure(BaseModel):
    """Step 9 포트폴리오 전체 노출"""
    total_delta: float = 0.0
    total_theta: float = 0.0
    total_vega: float = 0.0
    total_invested: float = 0.0
    remaining_cash: float = 3000.0
    sector_counts: dict[str, int] = Field(default_factory=dict)
    direction_bias: Literal["long", "short", "neutral"] = "neutral"
    concentration_warning: bool = False
    correlation_warning: bool = False
    warnings: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# 10. 최종 순위 스키마
# ─────────────────────────────────────────────────────────────

class ConfidenceScore(BaseModel):
    """확신도 점수 구성"""
    technical_signals: int = 0      # STEP 2 신호 수 (추세 신호)
    timing_conditions: int = 0      # STEP 3 타이밍 충족 수
    total_signals: int = 0          # 합계 (최대 8)
    rr_ratio: float = 0.0           # Risk/Reward 비율
    ivr: float = 0.0
    level: Literal["high", "medium", "low"] = "low"
    # 스펙 §6.2: 가중 합산 확신도 (0.0~1.0)
    # = trend(0.4) + news(0.2) + thesis(0.3) + execution(0.1)
    trend_confidence: float = Field(0.0, ge=0.0, le=1.0)
    news_confidence: float = Field(0.0, ge=0.0, le=1.0)
    thesis_confidence: float = Field(0.0, ge=0.0, le=1.0)
    execution_confidence: float = Field(0.0, ge=0.0, le=1.0)
    total_conviction: float = Field(0.0, ge=0.0, le=1.0)


class FinalRanking(BaseModel):
    """Step 10 최종 순위 항목"""
    rank: int
    ticker: str
    direction: Literal["long_call", "long_put"]
    action: Literal["진입", "관찰", "보류", "탈락"]
    final_score: float
    conviction: ConfidenceScore
    capital_allocation: float       # 권고 투자금
    contracts: int
    strike: float
    expiry: date
    rationale: str = ""
    risk_factors: list[str] = Field(default_factory=list)
    scenario: Scenario | None = None
    da_reasons: list[str] = Field(default_factory=list)  # DA 차감 이유 (Step 6)


# ─────────────────────────────────────────────────────────────
# 11. Requeue 스키마
# ─────────────────────────────────────────────────────────────

class RequeueThreshold(BaseModel):
    """Requeue 진입 조건"""
    ivr_max: float | None = None        # IVR < X 조건
    price_drop_pct: float | None = None # 가격 조정 조건
    rvol_min: float | None = None       # RVOL 조건
    custom: str = ""                    # 커스텀 조건 텍스트


class RequeueItem(BaseModel):
    """requeue.json 개별 항목"""
    ticker: str
    registered_at: datetime
    failed_filters: list[str]
    failure_reasons: list[str] = Field(default_factory=list)
    threshold: RequeueThreshold
    status: Literal["waiting", "ready", "processed"] = "waiting"
    last_checked: datetime | None = None
    ready_date: datetime | None = None


# ─────────────────────────────────────────────────────────────
# 12. 감사 로그 스키마
# ─────────────────────────────────────────────────────────────

class AuditRecord(BaseModel):
    """감사 로그 개별 레코드 (불변)"""
    timestamp: datetime = Field(default_factory=datetime.now)
    execution_id: str
    step: int
    status: Literal["started", "completed", "degraded", "failed", "skipped"]
    ticker: str | None = None
    duration_ms: int = 0
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# 13. Sell Pipeline 행동 스키마
# ─────────────────────────────────────────────────────────────

class SellDecision(BaseModel):
    """Sell Pipeline 최종 행동 결정"""
    ticker: str
    strike: float = 0.0          # pos_key 계산용 — obsidian 조회 키 일치
    expiry: date | None = None   # pos_key 계산용 — obsidian 조회 키 일치
    action: Literal["HOLD", "PARTIAL_EXIT", "FULL_EXIT", "ROLL"]
    contracts_to_close: int = 0
    target_premium: float | None = None
    roll_strike: float | None = None
    roll_expiry: date | None = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    rationale: str = ""
    risk_factors: list[str] = Field(default_factory=list)
    urgency: Literal["critical", "warning", "normal", "stable"] = "normal"


class SellResult(BaseModel):
    """Sell Pipeline 전체 결과"""
    execution_id: str
    decisions: list[SellDecision] = Field(default_factory=list)
    portfolio_after: PortfolioExposure = Field(default_factory=PortfolioExposure)
    iv_crush_warnings: list[str] = Field(default_factory=list)
    stop_loss_triggers: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# 14. 파이프라인 컨텍스트 (핵심 공유 객체)
# ─────────────────────────────────────────────────────────────

class PipelinePaths(BaseModel):
    """파이프라인에서 사용하는 파일 경로"""
    summary_dir: Path = Path(r"R:\내 드라이브\마켓 수치")
    finviz_file: Path = Path(r"Y:\내 드라이브\어닝\finviz_all_rows.txt")
    earnings_dir: Path = Path(r"Y:\내 드라이브\어닝")
    k_earnings_analysis: Path = Path(r"Y:\내 드라이브\어닝\K어닝 분析.md")
    k_earnings_analysis_today: Path = Path(r"Y:\내 드라이브\어닝\K어닝 분석_today.md")
    k_earnings_call_dir: Path = Path(r"Y:\내 드라이브\어닝\K어닝콜_output")
    positions_file: Path = Path(r"C:\lian\watchlist.md")
    watchlist_file: Path = Path(r"C:\lian\watchlist.md")
    data_dir: Path = Path(r"Y:\내 드라이브\Data")
    cache_dir: Path = Path("shared/cache")
    snapshots_dir: Path = Path("shared/state/snapshots")
    requeue_file: Path = Path("shared/state/requeue.json")
    logs_dir: Path = Path("shared/logs")

    model_config = {"arbitrary_types_allowed": True}


class PipelineContext(BaseModel):
    """파이프라인 실행 전체 컨텍스트"""
    execution_id: str
    pipeline_type: Literal["buy", "sell", "requeue"] = "buy"
    start_step: int = 0
    force_refresh: bool = False
    target_tickers: list[str] | None = None
    paths: PipelinePaths = Field(default_factory=PipelinePaths)

    # 단계별 누적 결과
    summary_data: SummaryData | None = None
    finviz_rows: list[FinvizRow] = Field(default_factory=list)
    earnings_list: list[EarningsAnalysis] = Field(default_factory=list)
    positions: list[Position] = Field(default_factory=list)
    watchlist: list[str] = Field(default_factory=list)
    kavout_data: dict[str, "KavoutRow"] = Field(default_factory=dict)
    stock_data: dict[str, "StockDetail"] = Field(default_factory=dict)
    # Step 6 DA 차감 이유 {ticker: ["이유1", "이유2"]}
    da_log: dict[str, list[str]] = Field(default_factory=dict)
    # Step 3 필터 탈락 수치 근거 {ticker: "RVOL 0.8 < 1.5 기준 | 가격 $3.2 < $5.0 기준"}
    filter_details: dict[str, str] = Field(default_factory=dict)
    regime: MarketRegime | None = None
    filtered_tickers: list[str] = Field(default_factory=list)
    filter_failures: dict[str, list[str]] = Field(default_factory=dict)
    technical_scores: dict[str, TechnicalScore] = Field(default_factory=dict)
    option_validity: dict[str, OptionValidity] = Field(default_factory=dict)
    # 기간별 옵션 추천 {ticker: {"단기": OptionValidity, "중기": ..., "장기": ...}}
    horizon_recommendations: dict[str, dict[str, "OptionValidity"]] = Field(default_factory=dict)
    # 투자 기간 분류 결과 {ticker: ["단기", "중기", "장기", "초장기"]}
    investment_horizons: dict[str, list[str]] = Field(default_factory=dict)
    # 초장기 기준 제시 {ticker: {"direction": ..., "dte_range": ..., ...}}
    # 체인 데이터가 없거나 LEAPS 미제공 종목에 대해 계약 대신 기준을 제시
    ultra_long_criteria: dict[str, dict] = Field(default_factory=dict)
    scenarios: dict[str, Scenario] = Field(default_factory=dict)
    portfolio_exposure: PortfolioExposure = Field(default_factory=PortfolioExposure)
    final_rankings: list[FinalRanking] = Field(default_factory=list)          # 안정성+수익 균형 순위
    final_rankings_aggressive: list[FinalRanking] = Field(default_factory=list)  # 수익성 최우선 순위
    high_downside_tickers: list[str] = Field(default_factory=list)            # 일변동 하락폭 큰 종목
    sell_decisions: list[SellDecision] = Field(default_factory=list)
    sentiment_results: dict[str, dict] = Field(default_factory=dict)

    # ── 매도 파이프라인 단계 간 전달용 전용 필드 ──────────────────────────
    # ctx.errors 음수 키 해킹 대신 명시적 필드 사용 (sell_steps.py 전용)
    sell_health: dict[str, Any] = Field(default_factory=dict)         # Step 1 → 4,7,8,11
    sell_regime_flags: dict[str, str] = Field(default_factory=dict)   # Step 2 → 7
    sell_regime_infer: dict[str, Any] = Field(default_factory=dict)   # Step 2 → 11 (LLM 레짐 추론 결과)
    sell_thesis: dict[str, Any] = Field(default_factory=dict)         # Step 4 → 7,10
    sell_devils: dict[str, Any] = Field(default_factory=dict)         # Step 5 → 7,10
    sell_iv_warnings: list[str] = Field(default_factory=list)         # Step 6 → 10,13
    sell_preliminary: list[Any] = Field(default_factory=list)         # Step 7 → 8,10

    # 단계 간 전달용 임시 메타
    obsidian_note_path: str = ""

    # 실행 메타
    started_at: datetime = Field(default_factory=datetime.now)
    completed_steps: list[int] = Field(default_factory=list)
    errors: dict[int, str] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


# ─────────────────────────────────────────────────────────────
# 15. 파이프라인 결과 스키마
# ─────────────────────────────────────────────────────────────

class PipelineResult(BaseModel):
    """파이프라인 실행 최종 결과"""
    execution_id: str
    pipeline_type: Literal["buy", "sell", "requeue"]
    status: Literal["completed", "partial", "failed"]
    completed_steps: list[int]
    failed_steps: dict[int, str] = Field(default_factory=dict)
    final_rankings: list[FinalRanking] = Field(default_factory=list)
    sell_decisions: list[SellDecision] = Field(default_factory=list)
    market_regime: str = ""
    snapshot_path: str = ""
    obsidian_note_path: str = ""
    slack_message_ts: str = ""
    duration_seconds: float = 0.0
    summary: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# 16. NL 쿼리 결과
# ─────────────────────────────────────────────────────────────

class NLQueryResult(BaseModel):
    """자연어 명령 파싱 결과"""
    intent: Literal[
        "BUY_PIPELINE", "SELL_PIPELINE", "POSITION_STATUS",
        "REQUEUE_ADD", "REQUEUE_LIST", "STEP_EXECUTE",
        "PARTIAL_EXIT", "EARNINGS_ANALYSIS", "MARKET_REGIME", "UNKNOWN"
    ]
    extracted_tickers: list[str] = Field(default_factory=list)
    routing_confidence: float = Field(ge=0.0, le=1.0)
    routed_tool: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    role_lock_applied: bool = True


# ─────────────────────────────────────────────────────────────
# 17. LLM 요청/응답
# ─────────────────────────────────────────────────────────────

class LLMRequest(BaseModel):
    """LLM 호출 요청"""
    messages: list[dict[str, str]]
    system_prompt: str = ""
    model: str = "anthropic/claude-sonnet-4-5"
    temperature: float = 0.0
    max_tokens: int = 4096
    response_format: str | None = None     # "json_object" | None
    cache_key: str | None = None


class LLMResponse(BaseModel):
    """LLM 응답"""
    content: str
    model_used: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False
    duration_ms: int = 0


# ─────────────────────────────────────────────────────────────
# 18. 펀더멘털 스크리너 스키마
# ─────────────────────────────────────────────────────────────

class EarningsCallAnalysis(BaseModel):
    """어닝_분석.md → LLM 재분류 결과"""
    ticker: str
    guidance_direction: Literal["up", "flat", "down", "unknown"] = "unknown"
    mgmt_tone: Literal["bullish", "neutral", "bearish"] = "neutral"
    key_risks: list[str] = Field(default_factory=list)
    catalyst_strength: int = Field(3, ge=1, le=5)  # 1=weak ~ 5=strong
    guidance_evidence: str = ""   # 가이던스 판단 근거 원문 인용 (1~2문장)
    tone_evidence: str = ""       # 경영진 톤 판단 근거 원문 인용 (1~2문장)


class FundamentalScoreResult(BaseModel):
    """종목별 펀더멘털 스크리닝 점수"""
    ticker: str
    company: str = ""
    sector: str = ""

    # 세부 점수 (0~100)
    momentum_score: float = 0.0      # RSI + Rel Volume + 52W 위치
    fundamental_score: float = 0.0   # 매출성장 + 순이익성장 + 마진
    catalyst_score: float = 0.0      # 가이던스 + 어닝콜 톤 (없으면 0)
    has_catalyst: bool = False        # 어닝콜 데이터 보유 여부

    total_score: float = 0.0
    rank: int = 0

    # 스냅샷 (Obsidian 노트 출력용)
    price: float | None = None
    rsi14: float | None = None
    rel_volume: float | None = None
    w52_high_pct: float | None = None
    revenue_growth_yoy: float | None = None
    net_income_growth_yoy: float | None = None
    op_margin_pct: float | None = None

    # 카탈리스트 상세
    guidance_direction: str = ""
    mgmt_tone: str = ""
    key_risks: list[str] = Field(default_factory=list)

    # Kavout 전용 (kavout_mcp에서만 채움, 나머지는 None)
    k_score: float | None = None            # Kavout QMP 순위 점수 (0~10, QMP 종목만)
    kavout_rank_score: float | None = None  # Kavout AI Stock Rank (0~100, 전 종목)
    momentum_1m: float | None = None        # 1개월 가격 모멘텀 %
    roe: float | None = None                # Return on Equity %


class ScreenerResult(BaseModel):
    """펀더멘털 스크리닝 전체 실행 결과"""
    execution_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    total_universe: int = 0       # 유니버스 종목 수
    with_earnings: int = 0        # 어닝콜 분석 보유 종목 수
    top10: list[FundamentalScoreResult] = Field(default_factory=list)
    all_results: list[FundamentalScoreResult] = Field(default_factory=list)
    obsidian_note_path: str = ""
    slack_sent: bool = False
    duration_seconds: float = 0.0


class ScreenerContext(BaseModel):
    """screener_mcp 파이프라인 컨텍스트"""
    execution_id: str
    paths: PipelinePaths = Field(default_factory=PipelinePaths)

    # Step 1 결과
    finviz_details: dict[str, StockDetail] = Field(default_factory=dict)

    # Step 2 결과
    earnings_analyses: dict[str, EarningsCallAnalysis] = Field(default_factory=dict)

    # Step 3 결과
    scores: list[FundamentalScoreResult] = Field(default_factory=list)

    started_at: datetime = Field(default_factory=datetime.now)
    errors: dict[str, str] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


# ─────────────────────────────────────────────────────────────
# 19. Kavout 유니버스 스키마
# ─────────────────────────────────────────────────────────────

class KavoutRow(BaseModel):
    """kavout_*.csv 한 행 (유니버스 + Kavout 고유 신호)"""
    ticker: str
    company: str = ""
    price: float | None = None
    market_cap_raw: float | None = None
    momentum_1m: float | None = None    # 1개월 가격 모멘텀 %
    roe: float | None = None            # Return on Equity %
    k_score: float | None = None        # Kavout AI 점수 (0~10)
    section: str = ""                   # "quantitative_momentum_plus" | "new_this_week"

    # ── Kavout AI 종합 점수 (stock-analysis radar) ────────────
    stock_rank_score:    float | None = None   # 0–100 종합 Stock Rank
    quality_score:       float | None = None
    growth_score:        float | None = None
    momentum_score:      float | None = None   # Kavout radar momentum (0–100)
    value_score:         float | None = None

    # ── Kavout 기술 분석 게이지 점수 (technical-analysis) ─────
    ma_score_num:           float | None = None
    oscillator_score_num:   float | None = None
    technical_rating_num:   float | None = None

    # ── MA / Oscillator 신호 (Bullish | Bearish | Neutral) ───
    ema10:      str | None = None
    sma20:      str | None = None
    sma50:      str | None = None
    sma200:     str | None = None
    rsi:        str | None = None
    stochastic: str | None = None
    macd:       str | None = None
    cci:        str | None = None

    # ── Kavout 펀더멘털 (stock-analysis) ─────────────────────
    roa:           float | None = None
    roic:          float | None = None
    debt_equity:   float | None = None
    current_ratio: float | None = None
    op_margin:     float | None = None
    pb_ratio:      float | None = None
    earnings_yield:float | None = None
    ev_ebitda:     float | None = None
    ps_ratio:      float | None = None
    div_yield:     float | None = None

    # ── 성장률 ────────────────────────────────────────────────
    asset_growth_1y:  float | None = None
    eps_growth_1y:    float | None = None
    rev_growth_3y:    float | None = None
    rev_growth_1y:    float | None = None
    ebitda_growth_3y: float | None = None

    # ── 수익률 ────────────────────────────────────────────────
    return_1w:  float | None = None
    return_1m:  float | None = None
    return_3m:  float | None = None
    return_6m:  float | None = None
    return_12m: float | None = None
