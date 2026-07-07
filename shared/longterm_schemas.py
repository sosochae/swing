"""
shared/longterm_schemas.py
==========================
장기투자 파이프라인 전용 데이터 모델
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────
# 1. 입력
# ─────────────────────────────────────────────────────────────

@dataclass
class LongtermInput:
    ticker: str
    company_name: str = ""
    sector: str = ""
    description: str = ""  # longBusinessSummary (Section 1 렌더링용)


# ─────────────────────────────────────────────────────────────
# 2. 재무 데이터
# ─────────────────────────────────────────────────────────────

@dataclass
class FinancialData:
    """5년 연간 재무 시리즈 (최신순)"""
    years: list[int] = field(default_factory=list)           # [2025, 2024, 2023, 2022, 2021]
    revenue: list[Optional[float]] = field(default_factory=list)          # 억달러
    operating_income: list[Optional[float]] = field(default_factory=list) # 억달러
    net_income: list[Optional[float]] = field(default_factory=list)       # 억달러
    eps: list[Optional[float]] = field(default_factory=list)              # 주당
    fcf: list[Optional[float]] = field(default_factory=list)              # 억달러
    ebitda: list[Optional[float]] = field(default_factory=list)           # 억달러


@dataclass
class QualityMetrics:
    """수익성·효율성 지표"""
    # 마진 시리즈 (최신순, %)
    op_margin: list[Optional[float]] = field(default_factory=list)
    net_margin: list[Optional[float]] = field(default_factory=list)
    ebitda_margin: list[Optional[float]] = field(default_factory=list)
    fcf_margin: list[Optional[float]] = field(default_factory=list)
    # 자본 효율성 (최신 단일값, %)
    roe: Optional[float] = None
    roa: Optional[float] = None
    roic: Optional[float] = None
    # 성장률 (%)
    revenue_cagr_3y: Optional[float] = None
    revenue_cagr_5y: Optional[float] = None
    eps_cagr_3y: Optional[float] = None
    fcf_cagr_3y: Optional[float] = None
    # 핵심 고성장 부문 성장률 (서비스, 클라우드 등) — LLM이 채움
    segment_growth_label: str = ""       # 예: "Services"
    segment_growth_rate: Optional[float] = None  # %
    # R&D
    rd_ratio: Optional[float] = None     # R&D / 매출 (%)


@dataclass
class BalanceSheetData:
    """재무 건전성"""
    debt_to_equity: Optional[float] = None       # 부채비율 (%)
    net_debt: Optional[float] = None             # 순부채 (억달러, 음수 = 순현금)
    interest_coverage: Optional[float] = None    # 이자보상배율
    current_ratio: Optional[float] = None        # 유동비율
    cash: Optional[float] = None                 # 현금성 자산 (억달러)


@dataclass
class ShareholderReturnData:
    """주주환원"""
    buyback_5y: Optional[float] = None     # 최근 5년 자사주매입 누적 (억달러)
    dividend_yield: Optional[float] = None # 배당수익률 (%)
    fcf_payout_ratio: Optional[float] = None  # FCF 환원율 (자사주+배당 / FCF, %)


# ─────────────────────────────────────────────────────────────
# 3. 밸류에이션
# ─────────────────────────────────────────────────────────────

@dataclass
class ValuationData:
    # 현재 멀티플
    pe_trailing: Optional[float] = None
    pe_forward: Optional[float] = None
    pbr: Optional[float] = None
    psr: Optional[float] = None
    ev_ebitda: Optional[float] = None
    peg: Optional[float] = None
    fcf_yield: Optional[float] = None       # %
    # 업계 평균 / 5년 평균
    sector_pe: Optional[float] = None
    hist_pe_avg: Optional[float] = None     # 5년 평균 PER
    hist_pe_high: Optional[float] = None    # 5년 최고 PER
    hist_pe_low: Optional[float] = None     # 5년 최저 PER
    hist_pe_pct: Optional[float] = None     # 현재 PER의 5년 범위 내 위치 (0~100%)
    # DCF
    dcf_fair_value: Optional[float] = None  # DCF 적정가 ($)
    dcf_upside: Optional[float] = None      # 현재가 대비 괴리율 (%)
    # 판단
    valuation_verdict: str = ""             # "저평가" | "적정" | "소폭 고평가" | "고평가"


# ─────────────────────────────────────────────────────────────
# 4. 시장 신호 (헤더 요약용)
# ─────────────────────────────────────────────────────────────

@dataclass
class MarketSignals:
    price: Optional[float] = None
    market_cap: Optional[float] = None      # 억달러
    w52_high: Optional[float] = None
    w52_low: Optional[float] = None
    w52_position_pct: Optional[float] = None  # 52주 범위 내 위치 (0~100%)
    sma200_pct: Optional[float] = None      # 200일선 대비 % (+위 / -아래)
    short_float_pct: Optional[float] = None # 공매도 비율 (%)
    inst_ownership_pct: Optional[float] = None  # 기관 지분율 (%)
    analyst_buy: Optional[int] = None
    analyst_hold: Optional[int] = None
    analyst_sell: Optional[int] = None
    target_price: Optional[float] = None    # 애널리스트 평균 목표가
    target_upside: Optional[float] = None   # 목표가 대비 업사이드 (%)
    # 어닝 서프라이즈 이력 (최신순, 최대 4분기)
    earnings_surprise_history: list[dict] = field(default_factory=list)
    # [{"date": "2026-04-30", "surprise_pct": +8.2, "price_move_pct": +4.1}, ...]


# ─────────────────────────────────────────────────────────────
# 5. LLM 정성 분석 결과
# ─────────────────────────────────────────────────────────────

@dataclass
class MoatAnalysis:
    # 해자 유형별 점수 (0~5)
    brand_power: int = 0
    switching_cost: int = 0
    network_effect: int = 0
    cost_advantage: int = 0
    patent_license: int = 0
    moat_total: int = 0          # 합산 (25점 만점)
    moat_label: str = ""         # "강한 해자" | "보통 해자" | "약한 해자"
    # Porter's 5 Forces
    threat_new_entrants: str = ""    # "낮음" | "중간" | "높음"
    threat_substitutes: str = ""
    buyer_power: str = ""
    supplier_power: str = ""
    rivalry: str = ""
    # 섹터 대비 성장성
    sector_growth_comparison: str = ""  # 예: "섹터 8.4% 대비 Apple 2.1% — 낮음"
    # 지속 가능성
    moat_durability: str = ""    # 5년 지속 가능성 판단 텍스트
    moat_threats: str = ""
    moat_defenses: str = ""


@dataclass
class ManagementAnalysis:
    ceo_name: str = ""
    ceo_tenure_years: Optional[float] = None
    insider_ownership_pct: Optional[float] = None
    guidance_accuracy_pct: Optional[float] = None  # 가이던스 적중률 (%)
    # 보상 구조
    comp_base_pct: Optional[float] = None    # 기본급 비율
    comp_cash_pct: Optional[float] = None    # 현금성과급 비율
    comp_equity_pct: Optional[float] = None  # 장기주식(RSU) 비율
    comp_vesting_note: str = ""              # 베스팅 조건 설명
    # ESG
    esg_governance: str = ""   # 거버넌스 리스크 평가
    esg_legal: str = ""        # 소송/규제 위반 이력
    esg_labor: str = ""        # 노동 리스크
    esg_environment: str = ""  # 환경 이슈
    esg_summary: str = ""      # ESG 종합 한 줄
    # 추가 프로필
    cfo_name: str = ""
    ma_track_record: str = ""        # M&A 이력 요약 (성공/실패)
    inst_ownership_change: str = ""  # "증가" | "감소" | "유지"
    # 자본배분
    capital_allocation_pros: list[str] = field(default_factory=list)
    capital_allocation_cons: list[str] = field(default_factory=list)
    # 점수
    management_score: int = 0  # /10


@dataclass
class IndustryAnalysis:
    industry_stage: str = ""          # "성장기" | "성숙기" | "쇠퇴기" 등
    market_share_trend: str = ""      # "상승" | "유지" | "하락"
    # 경쟁 구도 (경쟁사별)
    competitors: list[dict] = field(default_factory=list)
    # [{"name": "Samsung", "threat": "제한적", "detail": "..."}, ...]
    tech_disruption_risk: str = ""    # "낮음" | "중간" | "높음"
    cyclical_sensitivity: str = ""    # "방어적" | "중간" | "경기민감"
    # 매크로 감응도
    rate_sensitivity: str = ""        # "낮음" | "중간" | "높음"
    inflation_passthrough: str = ""   # "높음" — 가격 인상 시 고객 이탈 낮음
    fx_sensitivity: str = ""          # "달러 강세 부정적" 등
    geopolitical_risk: str = ""       # 주요 지정학 노출 설명
    recession_defense: str = ""        # "중간" 등
    regulatory_risk: str = ""          # 규제 리스크 설명
    international_revenue_pct: Optional[float] = None  # 해외 매출 비중 (%)


@dataclass
class RiskItem:
    name: str
    probability: str    # "낮음" | "중간" | "높음"
    impact: str         # "낮음" | "중간" | "높음"
    severity: int       # 1~5 (별점)
    is_fatal: bool = False  # 즉시 제외 수준


@dataclass
class QualitativeAnalysis:
    earnings_call_summary: str = ""   # 최근 실적발표 핵심 포인트
    mgmt_tone: str = ""               # "낙관적" | "중립" | "신중"
    news_positive: list[str] = field(default_factory=list)  # 긍정 뉴스 목록
    news_negative: list[str] = field(default_factory=list)  # 부정 뉴스 목록
    qualitative_verdict: str = ""     # 종합 정성 판단 (2~3문장)
    ten_year_thesis: str = ""         # 10년 후 존재 이유
    tam_estimate: str = ""            # TAM 규모 추정 (LLM)


# ─────────────────────────────────────────────────────────────
# 6. 스코어카드
# ─────────────────────────────────────────────────────────────

@dataclass
class SectionScores:
    business_quality: int = 0    # /20
    moat: int = 0                # /20
    financial_health: int = 0    # /20
    valuation: int = 0           # /20
    management: int = 0          # /20
    risk: int = 0                # /20

    @property
    def total(self) -> int:
        return (self.business_quality + self.moat + self.financial_health
                + self.valuation + self.management + self.risk)


# ─────────────────────────────────────────────────────────────
# 7. 최종 투자 판단
# ─────────────────────────────────────────────────────────────

@dataclass
class InvestmentVerdict:
    opinion: str = ""            # "강력 매수" | "매수" | "장기 보유" | "관망" | "매도"
    target_price: Optional[float] = None
    target_upside_pct: Optional[float] = None
    target_horizon: str = "3~5년"
    target_return_pa: str = ""   # 예: "연 8~12%"
    position_size_pct: str = ""  # 포트폴리오 비중 권고 (예: "5~8%")
    review_triggers: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# 8. 통합 보고서 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class LongtermReport:
    # 입력
    input: LongtermInput = field(default_factory=LongtermInput)
    # 재무
    financial: FinancialData = field(default_factory=FinancialData)
    quality: QualityMetrics = field(default_factory=QualityMetrics)
    balance_sheet: BalanceSheetData = field(default_factory=BalanceSheetData)
    shareholder_return: ShareholderReturnData = field(default_factory=ShareholderReturnData)
    valuation: ValuationData = field(default_factory=ValuationData)
    market_signals: MarketSignals = field(default_factory=MarketSignals)
    # 정성 분석
    moat: MoatAnalysis = field(default_factory=MoatAnalysis)
    management: ManagementAnalysis = field(default_factory=ManagementAnalysis)
    industry: IndustryAnalysis = field(default_factory=IndustryAnalysis)
    risks: list[RiskItem] = field(default_factory=list)
    qualitative: QualitativeAnalysis = field(default_factory=QualitativeAnalysis)
    # 결론
    scores: SectionScores = field(default_factory=SectionScores)
    verdict: InvestmentVerdict = field(default_factory=InvestmentVerdict)
    # 메타
    generated_at: str = ""
    obsidian_path: str = ""
