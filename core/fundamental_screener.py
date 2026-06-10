"""
core/fundamental_screener.py
============================
Finviz 상세 데이터 + 어닝콜 분석 + Kavout KavoutRow → 종목 점수화 + 랭킹

점수 구조 (각 0~100):
  Momentum Score    = RSI(20%) + Rel Volume(20%) + 52W 위치(20%) + SMA 추세(20%)
                      + 멀티 기간 수익률(20%)  ← kavout_row 있을 때
  Fundamental Score = 매출성장YoY(40%) + EPS서프라이즈(25%) + 영업이익률(35%)
  Catalyst Score    = 가이던스(60%) + 경영진 톤(40%)
  Kavout AI Score   = stock_rank_score (0~100) 직접 사용

변경 이력:
  - net_income_growth_yoy 제거: GAAP 기준으로 M&A·스톡옵션 일회성 비용에 왜곡됨
  - eps_surprise_pct 도입: Non-GAAP 컨센서스 대비 서프라이즈 → 실질 이익 품질 반영
  - SMA 추세 추가: SMA20/50/200 위치로 중장기 추세 확인
  - catalyst_strength 제거: 가이던스+톤에서 이미 반영된 내용 중복 집계 방지
  - 멀티 기간 수익률 추가: Kavout 3M/6M/12M 수익률로 모멘텀 지속성 측정
  - Kavout AI Score 추가: stock_rank_score를 독립 요소로 반영

최종 점수:
  - Catalyst 있음: 0.50×M + 0.35×C + 0.15×K
  - Catalyst 없음: 0.85×M + 0.15×K
"""

from __future__ import annotations

from shared.logger import get_logger
from shared.schemas import EarningsCallAnalysis, StockDetail, FundamentalScoreResult, KavoutRow
from shared.strategy import (
    FSCORE_CAT_GUIDANCE_WEIGHT,
    FSCORE_CAT_TONE_WEIGHT,
    FSCORE_FUND_EPS_SURPR_WEIGHT,
    FSCORE_FUND_MARGIN_WEIGHT,
    FSCORE_FUND_REV_WEIGHT,
    FSCORE_MOM_52W_WEIGHT,
    FSCORE_MOM_RSI_WEIGHT,
    FSCORE_MOM_RVOL_WEIGHT,
    FSCORE_MOM_SMA_WEIGHT,
    FSCORE_MOM_RETURN_WEIGHT,
    FSCORE_NO_CATALYST_FUNDAMENTAL,
    FSCORE_NO_CATALYST_MOMENTUM,
    FSCORE_NO_CATALYST_KAVOUT,
    FSCORE_RSI_IDEAL_MAX,
    FSCORE_RSI_IDEAL_MIN,
    FSCORE_RSI_OK_MAX,
    FSCORE_RSI_OK_MIN,
    FSCORE_RVOL_HIGH,
    FSCORE_RVOL_LOW,
    FSCORE_RVOL_MED,
    FSCORE_WEIGHT_CATALYST,
    FSCORE_WEIGHT_FUNDAMENTAL,
    FSCORE_WEIGHT_MOMENTUM,
    FSCORE_WEIGHT_KAVOUT,
)

log = get_logger()


# ─────────────────────────────────────────────────────────────
# 1. Momentum Score 계산
# ─────────────────────────────────────────────────────────────

def _rsi_score(rsi: float | None) -> float:
    """RSI(14) → 0~100 점수 (롱 관점: 50~70 이상적)"""
    if rsi is None:
        return 50.0  # 데이터 없으면 중립
    if FSCORE_RSI_IDEAL_MIN <= rsi <= FSCORE_RSI_IDEAL_MAX:
        return 100.0
    if FSCORE_RSI_OK_MIN <= rsi < FSCORE_RSI_IDEAL_MIN:
        return 65.0
    if FSCORE_RSI_IDEAL_MAX < rsi <= FSCORE_RSI_OK_MAX:
        return 55.0  # 과매수 구간: 약간 낮게
    if rsi < FSCORE_RSI_OK_MIN:
        return 30.0  # 과매도: 약세 신호
    return 20.0  # RSI > 80: 극단적 과매수


def _rvol_score(rel_volume: float | None) -> float:
    """Relative Volume → 0~100 점수"""
    if rel_volume is None:
        return 40.0
    if rel_volume >= FSCORE_RVOL_HIGH:
        return 100.0
    if rel_volume >= FSCORE_RVOL_MED:
        return 70.0
    if rel_volume >= FSCORE_RVOL_LOW:
        return 45.0
    return 20.0


def _w52_score(w52_high_pct: float | None, w52_low_pct: float | None) -> float:
    """
    52주 위치 점수 (0~100).
    고점 근접도와 저점 이탈도를 각각 독립 점수화 후 평균.

    w52_high_pct: 고점 대비 % (e.g. -5.0 = 고점 5% 아래)
    w52_low_pct:  저점 대비 % (e.g. +80.0 = 저점 80% 위)
    """
    scores: list[float] = []

    # 고점 근접도: 고점에 가까울수록 강한 모멘텀
    if w52_high_pct is not None:
        dist = abs(w52_high_pct)
        if dist <= 5:
            scores.append(100.0)
        elif dist <= 15:
            scores.append(75.0)
        elif dist <= 30:
            scores.append(50.0)
        elif dist <= 50:
            scores.append(30.0)
        else:
            scores.append(10.0)

    # 저점 이탈도: 저점에서 많이 올라올수록 추세 강함
    if w52_low_pct is not None:
        if w52_low_pct >= 100:
            scores.append(100.0)
        elif w52_low_pct >= 50:
            scores.append(80.0)
        elif w52_low_pct >= 25:
            scores.append(60.0)
        elif w52_low_pct >= 10:
            scores.append(40.0)
        else:
            scores.append(20.0)

    if not scores:
        return 50.0
    return round(sum(scores) / len(scores), 2)


def _sma_score(sma20_pct: float | None, sma50_pct: float | None, sma200_pct: float | None) -> float:
    """
    SMA20/50/200 위치 점수 (0~100).
    가격이 각 SMA 위에 있을수록, 얼마나 위에 있는지에 따라 점수 부여.
    SMA200 > SMA50 > SMA20 순으로 중장기 추세 가중치 적용.

    sma_pct: (price - sma) / sma * 100 — 양수=SMA 위, 음수=SMA 아래
    """
    def _single(pct: float) -> float:
        if pct >= 10:
            return 100.0
        if pct >= 3:
            return 80.0
        if pct >= 0:
            return 60.0
        if pct >= -10:
            return 35.0
        return 10.0

    # 가중치: SMA200(중장기) > SMA50(중기) > SMA20(단기)
    weighted = [
        (sma20_pct,  0.25),
        (sma50_pct,  0.35),
        (sma200_pct, 0.40),
    ]
    available = [(p, w) for p, w in weighted if p is not None]
    if not available:
        return 50.0

    total_w = sum(w for _, w in available)
    score = sum(_single(p) * (w / total_w) for p, w in available)
    return round(score, 2)


def _multi_return_score(krow: "KavoutRow") -> float:
    """
    Kavout 멀티 기간 수익률 → 0~100 점수.
    12M(40%) + 6M(35%) + 3M(25%) 가중 평균 — 장기 추세에 더 가중.
    각 기간 수익률은 구간별로 0~100으로 변환.
    """
    def _single(ret: float | None) -> float:
        if ret is None:
            return 50.0  # 중립
        if ret >= 50:   return 100.0
        if ret >= 25:   return 85.0
        if ret >= 10:   return 70.0
        if ret >= 0:    return 55.0
        if ret >= -10:  return 35.0
        if ret >= -25:  return 20.0
        return 5.0

    r3m  = _single(krow.return_3m)
    r6m  = _single(krow.return_6m)
    r12m = _single(krow.return_12m)

    return round(r12m * 0.40 + r6m * 0.35 + r3m * 0.25, 2)


def _kavout_ai_score(krow: "KavoutRow | None") -> float:
    """
    Kavout stock_rank_score (0~100) → 그대로 반환.
    없으면 50 (중립).
    """
    if krow is None:
        return 50.0
    v = krow.stock_rank_score
    if v is None:
        return 50.0
    return float(v)


def calc_momentum_score(detail: StockDetail, krow: "KavoutRow | None" = None) -> float:
    """Momentum Score (0~100)

    krow 있음: RSI(20%) + RVOL(20%) + 52W(20%) + SMA(20%) + 멀티수익률(20%)
    krow 없음: RSI(25%) + RVOL(25%) + 52W(25%) + SMA(25%)  ← 기존 동작 유지
    """
    rsi  = _rsi_score(detail.rsi14)
    rvol = _rvol_score(detail.rel_volume)
    w52  = _w52_score(detail.w52_high_pct, detail.w52_low_pct)
    sma  = _sma_score(detail.sma20_pct, detail.sma50_pct, detail.sma200_pct)

    if krow is not None:
        ret = _multi_return_score(krow)
        score = (
            rsi  * FSCORE_MOM_RSI_WEIGHT
            + rvol * FSCORE_MOM_RVOL_WEIGHT
            + w52  * FSCORE_MOM_52W_WEIGHT
            + sma  * FSCORE_MOM_SMA_WEIGHT
            + ret  * FSCORE_MOM_RETURN_WEIGHT
        )
    else:
        # krow 없으면 4요소를 균등 25%씩 (기존 동작)
        score = (
            rsi  * 0.25
            + rvol * 0.25
            + w52  * 0.25
            + sma  * 0.25
        )
    return round(score, 2)


# ─────────────────────────────────────────────────────────────
# 2. Fundamental Score 계산
# ─────────────────────────────────────────────────────────────

def _growth_score(growth_pct: float | None) -> float:
    """매출 YoY 성장률 % → 0~100 점수"""
    if growth_pct is None:
        return 40.0  # 데이터 없음 → 중립 이하
    if growth_pct >= 50:
        return 100.0
    if growth_pct >= 25:
        return 80.0
    if growth_pct >= 10:
        return 60.0
    if growth_pct >= 0:
        return 40.0
    if growth_pct >= -10:
        return 20.0
    return 5.0  # 심각한 역성장


def _eps_surprise_score(eps_surprise_pct: float | None) -> float:
    """
    EPS 서프라이즈 % → 0~100 점수.
    어닝 서프라이즈는 보통 Non-GAAP 컨센서스 대비 측정 → GAAP 왜곡 없음.
    데이터 없으면 중립(50) — 판단 불가이므로 패널티 없음.
    """
    if eps_surprise_pct is None:
        return 50.0  # 중립 (GAAP 순이익 성장률과 달리 없으면 패널티 안 줌)
    if eps_surprise_pct >= 15:
        return 100.0
    if eps_surprise_pct >= 5:
        return 80.0
    if eps_surprise_pct >= 0:
        return 60.0
    if eps_surprise_pct >= -5:
        return 35.0
    return 15.0  # 큰 어닝 미스


def _margin_score(op_margin: float | None) -> float:
    """영업이익률 % → 0~100 점수 (업종 무관 효율성 지표)"""
    if op_margin is None:
        return 40.0
    if op_margin >= 25:
        return 100.0
    if op_margin >= 15:
        return 80.0
    if op_margin >= 8:
        return 60.0
    if op_margin >= 0:
        return 35.0
    return 10.0  # 적자


def calc_fundamental_score(detail: StockDetail) -> float:
    """Fundamental Score (0~100)
    매출성장YoY(40%) + EPS서프라이즈(25%) + 영업이익률(35%)

    GAAP 순이익 성장률은 제외: M&A·스톡옵션·구조조정 일회성 비용에 크게 왜곡됨.
    EPS 서프라이즈는 Non-GAAP 컨센서스 대비이므로 실질 이익 품질을 더 잘 반영.
    """
    rev_s    = _growth_score(detail.revenue_growth_yoy)
    surpr_s  = _eps_surprise_score(detail.eps_surprise_pct)
    margin_s = _margin_score(detail.op_margin_pct)

    score = (
        rev_s    * FSCORE_FUND_REV_WEIGHT
        + surpr_s  * FSCORE_FUND_EPS_SURPR_WEIGHT
        + margin_s * FSCORE_FUND_MARGIN_WEIGHT
    )
    return round(score, 2)


# ─────────────────────────────────────────────────────────────
# 3. Catalyst Score 계산
# ─────────────────────────────────────────────────────────────

_GUIDANCE_SCORE = {"up": 100.0, "flat": 50.0, "down": 10.0, "unknown": 40.0}
_TONE_SCORE     = {"bullish": 100.0, "neutral": 55.0, "bearish": 15.0}


def calc_catalyst_score(analysis: EarningsCallAnalysis) -> float:
    """Catalyst Score (0~100)
    가이던스(60%) + 경영진 톤(40%)

    catalyst_strength 제거: 가이던스+톤에서 이미 반영된 내용을 중복 집계하던 문제 해소.
    """
    g_score = _GUIDANCE_SCORE.get(analysis.guidance_direction, 40.0)
    t_score = _TONE_SCORE.get(analysis.mgmt_tone, 55.0)

    score = (
        g_score * FSCORE_CAT_GUIDANCE_WEIGHT
        + t_score * FSCORE_CAT_TONE_WEIGHT
    )
    return round(score, 2)


# ─────────────────────────────────────────────────────────────
# 4. 종목별 최종 점수 산출
# ─────────────────────────────────────────────────────────────

def score_ticker(
    detail: StockDetail,
    analysis: EarningsCallAnalysis | None,
    sector: str = "",
    company: str = "",
    krow: "KavoutRow | None" = None,
) -> FundamentalScoreResult:
    """단일 종목 FundamentalScoreResult 생성"""
    m_score = calc_momentum_score(detail, krow)
    f_score = calc_fundamental_score(detail)

    has_catalyst = analysis is not None
    c_score = calc_catalyst_score(analysis) if has_catalyst else 0.0
    k_ai    = _kavout_ai_score(krow)

    if has_catalyst:
        total = (
            m_score * FSCORE_WEIGHT_MOMENTUM
            + c_score * FSCORE_WEIGHT_CATALYST
            + k_ai    * FSCORE_WEIGHT_KAVOUT
        )
    else:
        total = (
            m_score * FSCORE_NO_CATALYST_MOMENTUM
            + k_ai  * FSCORE_NO_CATALYST_KAVOUT
        )

    return FundamentalScoreResult(
        ticker=detail.ticker,
        company=company,
        sector=sector,
        momentum_score=m_score,
        fundamental_score=f_score,
        catalyst_score=c_score,
        has_catalyst=has_catalyst,
        total_score=round(total, 2),
        price=detail.price,
        rsi14=detail.rsi14,
        rel_volume=detail.rel_volume,
        w52_high_pct=detail.w52_high_pct,
        revenue_growth_yoy=detail.revenue_growth_yoy,
        net_income_growth_yoy=detail.net_income_growth_yoy,
        op_margin_pct=detail.op_margin_pct,
        guidance_direction=analysis.guidance_direction if analysis else "",
        mgmt_tone=analysis.mgmt_tone if analysis else "",
        key_risks=analysis.key_risks if analysis else [],
    )


# ─────────────────────────────────────────────────────────────
# 5. 전체 유니버스 랭킹
# ─────────────────────────────────────────────────────────────

def rank_universe(
    finviz_details: dict[str, StockDetail],
    earnings_analyses: dict[str, EarningsCallAnalysis],
    finviz_rows_meta: dict[str, dict],  # {ticker: {"sector": ..., "company": ...}}
    kavout_map: "dict[str, KavoutRow] | None" = None,
) -> list[FundamentalScoreResult]:
    """
    전체 종목 점수화 → 내림차순 정렬 → rank 번호 부여

    Args:
        finviz_details:    parse_finviz_detail() 결과
        earnings_analyses: analyze_earnings() 결과
        finviz_rows_meta:  finviz_all_rows 또는 파일명에서 추출한 섹터/회사 정보
        kavout_map:        {ticker: KavoutRow} — Kavout AI 점수·멀티수익률 반영용

    Returns:
        FundamentalScoreResult 리스트 (rank 1이 최상위)
    """
    results: list[FundamentalScoreResult] = []
    km = kavout_map or {}

    for ticker, detail in finviz_details.items():
        meta = finviz_rows_meta.get(ticker, {})
        analysis = earnings_analyses.get(ticker)
        krow = km.get(ticker)

        result = score_ticker(
            detail=detail,
            analysis=analysis,
            sector=meta.get("sector", ""),
            company=meta.get("company", ""),
            krow=krow,
        )
        results.append(result)

    # 내림차순 정렬
    results.sort(key=lambda r: r.total_score, reverse=True)

    # rank 부여
    for i, r in enumerate(results, start=1):
        r.rank = i

    log.info(
        "screener_ranked",
        total=len(results),
        with_catalyst=sum(1 for r in results if r.has_catalyst),
        top1=results[0].ticker if results else "N/A",
        top1_score=results[0].total_score if results else 0,
    )
    return results
