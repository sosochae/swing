"""
core/longterm_scorer.py
=======================
장기투자 6개 영역 스코어링 + 최종 투자 판단 (규칙 기반, LLM 없음)
"""

from __future__ import annotations

import logging
from typing import Optional

from shared.longterm_schemas import (
    BalanceSheetData,
    IndustryAnalysis,
    InvestmentVerdict,
    ManagementAnalysis,
    MarketSignals,
    MoatAnalysis,
    QualityMetrics,
    RiskItem,
    SectionScores,
    ValuationData,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 영역별 채점 (각 20점 만점)
# ─────────────────────────────────────────────────────────────

def _score_business_quality(quality: QualityMetrics) -> int:
    """FCF 마진, ROIC, 매출 성장성 기반 (20점)"""
    score = 0

    # FCF 마진 최신값 (8점)
    fcf_m = quality.fcf_margin[0] if quality.fcf_margin else None
    if fcf_m is not None:
        if fcf_m >= 25:   score += 8
        elif fcf_m >= 18: score += 6
        elif fcf_m >= 10: score += 4
        elif fcf_m >= 0:  score += 2

    # ROIC (7점)
    roic = quality.roic
    if roic is not None:
        if roic >= 40:    score += 7
        elif roic >= 25:  score += 5
        elif roic >= 15:  score += 3
        elif roic >= 8:   score += 1

    # 매출 3년 CAGR (5점)
    cagr = quality.revenue_cagr_3y
    if cagr is not None:
        if cagr >= 15:    score += 5
        elif cagr >= 8:   score += 4
        elif cagr >= 3:   score += 3
        elif cagr >= 0:   score += 1

    return min(20, score)


def _score_moat(moat: MoatAnalysis) -> int:
    """해자 총점 25점 → 20점으로 변환"""
    return round(moat.moat_total / 25 * 20)


def _score_financial_health(balance: BalanceSheetData, quality: QualityMetrics) -> int:
    """이자보상배율, 부채 수준, FCF 흑자 여부 (20점)"""
    score = 0

    # FCF 흑자 여부 (5점)
    fcf = quality.fcf_margin[0] if quality.fcf_margin else None
    if fcf is not None and fcf > 0:
        score += 5

    # 이자보상배율 (8점)
    ic = balance.interest_coverage
    if ic is not None:
        if ic >= 20:   score += 8
        elif ic >= 10: score += 6
        elif ic >= 5:  score += 4
        elif ic >= 2:  score += 1
    else:
        # 무부채 기업 (이자비용 없음) → 만점
        if balance.net_debt is not None and balance.net_debt <= 0:
            score += 8

    # 부채비율 (7점) — 낮을수록 좋음
    de = balance.debt_to_equity
    if de is not None:
        if de <= 30:    score += 7
        elif de <= 80:  score += 5
        elif de <= 150: score += 3
        elif de <= 250: score += 1
    else:
        score += 4  # 정보 없으면 중간값

    return min(20, score)


def _score_valuation(val: ValuationData) -> int:
    """PEG, DCF 괴리율, 역사적 PER 위치 (20점)"""
    score = 10  # 기본 중간값에서 시작

    # DCF 괴리율 (8점)
    if val.dcf_upside is not None:
        if val.dcf_upside >= 30:    score += 8
        elif val.dcf_upside >= 15:  score += 5
        elif val.dcf_upside >= 0:   score += 2
        elif val.dcf_upside >= -15: score -= 2
        else:                        score -= 5

    # 역사적 PER 위치 (6점) — 낮을수록 좋음 (0%=역대 최저, 100%=역대 최고)
    if val.hist_pe_pct is not None:
        if val.hist_pe_pct <= 20:    score += 4
        elif val.hist_pe_pct <= 40:  score += 2
        elif val.hist_pe_pct >= 80:  score -= 3
        elif val.hist_pe_pct >= 60:  score -= 1

    # PEG (6점)
    peg = val.peg
    if peg is not None and peg > 0:
        if peg <= 1.0:    score += 4
        elif peg <= 1.5:  score += 2
        elif peg >= 3.0:  score -= 3
        elif peg >= 2.0:  score -= 1

    return max(0, min(20, score))


def _score_management(management: ManagementAnalysis) -> int:
    """ManagementAnalysis.management_score (0~10) → 20점으로 변환"""
    return round(management.management_score / 10 * 20)


def _score_risk(risks: list[RiskItem]) -> int:
    """치명적 리스크 및 고위험 리스크 수에 따라 차감 방식 (20점 시작)"""
    score = 20

    for r in risks:
        if r.is_fatal:
            return 0  # 치명적 리스크 → 즉시 0점

        # severity 4~5: -3점, severity 3: -1점
        if r.severity >= 4:
            score -= 3
        elif r.severity == 3:
            score -= 1

    return max(0, score)


# ─────────────────────────────────────────────────────────────
# 투자 판단 매트릭스
# ─────────────────────────────────────────────────────────────

def _investment_opinion(total: int, val_score: int) -> str:
    if total >= 85:
        return "강력 매수"
    elif total >= 70:
        return "매수"
    elif total >= 55:
        return "장기 보유"
    elif total >= 40:
        return "관망"
    else:
        return "매도"


def _position_size(total: int) -> str:
    if total >= 85: return "8~10%"
    elif total >= 70: return "5~8%"
    elif total >= 55: return "3~5%"
    elif total >= 40: return "1~3%"
    else: return "0%"


def _annual_return_estimate(opinion: str) -> str:
    return {
        "강력 매수": "연 15%+",
        "매수":      "연 10~15%",
        "장기 보유": "연 7~10%",
        "관망":      "시장 수익률 미만",
        "매도":      "손실 위험",
    }.get(opinion, "")


def _build_review_triggers(
    quality: QualityMetrics,
    balance: BalanceSheetData,
    val: ValuationData,
) -> list[str]:
    triggers = []

    if quality.roic is not None:
        threshold = round(quality.roic * 0.7, 1)
        triggers.append(f"ROIC {threshold}% 이하로 하락 시 — 핵심 수익성 훼손")

    if val.pe_trailing is not None:
        threshold = round(val.pe_trailing * 1.3, 1)
        triggers.append(f"PER {threshold}x 이상 시 — 밸류에이션 부담 과도")

    if quality.revenue_cagr_3y is not None and quality.revenue_cagr_3y > 5:
        threshold = round(quality.revenue_cagr_3y * 0.5, 1)
        triggers.append(f"매출 성장률 {threshold}% 이하로 둔화 시 — 성장 동력 약화")

    if balance.interest_coverage is not None and balance.interest_coverage > 0:
        triggers.append(f"이자보상배율 3x 이하로 하락 시 — 재무 건전성 이상")

    triggers.append("치명적 리스크 신규 발생 시 (규제, M&A 실패, 경영진 교체)")

    return triggers


# ─────────────────────────────────────────────────────────────
# 통합 스코어링
# ─────────────────────────────────────────────────────────────

def score(
    quality: QualityMetrics,
    balance: BalanceSheetData,
    valuation: ValuationData,
    moat: MoatAnalysis,
    management: ManagementAnalysis,
    industry: IndustryAnalysis,
    risks: list[RiskItem],
    market_signals: MarketSignals,
) -> tuple[SectionScores, InvestmentVerdict]:

    bq  = _score_business_quality(quality)
    mt  = _score_moat(moat)
    fh  = _score_financial_health(balance, quality)
    vl  = _score_valuation(valuation)
    mg  = _score_management(management)
    rk  = _score_risk(risks)

    scores = SectionScores(
        business_quality=bq,
        moat=mt,
        financial_health=fh,
        valuation=vl,
        management=mg,
        risk=rk,
    )

    opinion = _investment_opinion(scores.total, vl)

    # 목표가: DCF 적정가 우선, 없으면 애널리스트 컨센서스
    target_price = valuation.dcf_fair_value or market_signals.target_price
    target_upside: Optional[float] = None
    if target_price and market_signals.price and market_signals.price > 0:
        target_upside = round((target_price / market_signals.price - 1) * 100, 1)

    verdict = InvestmentVerdict(
        opinion=opinion,
        target_price=target_price,
        target_upside_pct=target_upside,
        target_horizon="3~5년",
        target_return_pa=_annual_return_estimate(opinion),
        position_size_pct=_position_size(scores.total),
        review_triggers=_build_review_triggers(quality, balance, valuation),
    )

    log.info(
        "longterm_score total=%d opinion=%s bq=%d mt=%d fh=%d vl=%d mg=%d rk=%d",
        scores.total, opinion, bq, mt, fh, vl, mg, rk,
    )
    return scores, verdict
