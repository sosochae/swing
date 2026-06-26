"""
core/analysis.py
================
분석 엔진 통합 모듈 (T3 최적화: regime·technical·greeks·scenario·portfolio·devils·confidence → 1개)

담당:
- analyze_market_regime(): 시장 레짐 판정 (결정론적, LLM 없음)
- calculate_technical_score(): 기술 분석 점수 산출
- calculate_greeks(): Black-Scholes Greeks 계산
- calculate_scenario(): 시나리오 3-케이스 분석
- check_portfolio_exposure(): 포트폴리오 노출 점검
- apply_devils_advocate(): Devil's Advocate 차감
- calculate_confidence(): 확신도 점수 산출
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from scipy.stats import norm  # type: ignore

from shared.config import get_config
from shared.logger import get_logger
from shared import strategy as st
from shared.schemas import (
    ConfidenceScore,
    FinalRanking,
    Greeks,
    MarketRegime,
    OptionValidity,
    PortfolioExposure,
    RegimeComponent,
    Scenario,
    ScenarioCase,
    SummaryData,
    TechnicalScore,
)

log = get_logger()
cfg = get_config()


# ─────────────────────────────────────────────────────────────
# 1. 시장 레짐 판정 (Step 2 — 결정론적)
# ─────────────────────────────────────────────────────────────

def analyze_market_regime(summary: SummaryData) -> MarketRegime:
    """
    다중 신호 합산 기반 시장 레짐 및 방향 판정.
    모든 판정은 결정론적이며 LLM을 사용하지 않습니다.

    방향 결정 로직:
      - 7개 신호의 가중 합산 스코어로 방향 결정 (단순 MA 비교 대신)
      - score ≥ +3 → long_call / score ≤ -3 → long_put 후보
      - long_put 후보는 VIX·ADX 추가 검증 후 확정

    레짐 결정 로직:
      - VIX > 30 또는 추세 없음(ADX < 18 + 혼조 MA) → unfavorable
      - 나머지는 방향 스코어 강도에 따라 favorable/borderline

    Returns:
        MarketRegime
    """
    macro = summary.macro
    vix = macro.vix
    spy = macro.spy
    spy_ma20 = macro.spy_ma20
    qqq = macro.qqq
    qqq_ma20 = macro.qqq_ma20

    # ── SPY 기술 데이터 (ADX, DI+/DI-, 4H DI) ──────────────────────
    adx_value: float | None = None
    spy_di_plus: float | None = None
    spy_di_minus: float | None = None
    spy_di_plus_4h: float | None = None   # 4H DI+ (단기 방향 가중치)
    spy_di_minus_4h: float | None = None
    adx_source = "ma_proxy"
    spy_data = summary.tickers.get("SPY")
    if spy_data and spy_data.technical.adx14 > 0:
        adx_value = spy_data.technical.adx14
        adx_source = "direct"
        _di_p = getattr(spy_data.technical, "di_plus",  0.0) or 0.0
        _di_n = getattr(spy_data.technical, "di_minus", 0.0) or 0.0
        if _di_p > 0 and _di_n > 0:
            spy_di_plus  = _di_p
            spy_di_minus = _di_n
    # SPY의 4H DI: StockDetail이 있으면 가져오기 (summary.tickers["SPY"]는 TickerData)
    # stock_data(StockDetail)는 여기서 접근 불가 → 방향 스코어에서는 일봉 DI만 사용
    # (4H DI는 obsidian의 3-layer 신호에서만 활용)

    # ── A. 추세 강도 컴포넌트 ─────────────────────────────────────
    # "추세 강도"는 방향이 아니라 추세의 존재 여부만 판단.
    # SPY+QQQ 둘 다 MA 아래여도 하락 추세는 엄연히 존재 → fail 아님 (버그 수정)
    spy_above = spy > spy_ma20
    qqq_above = qqq > qqq_ma20

    if adx_value is not None:
        if adx_value >= st.REGIME_ADX_STRONG:
            trend_status = "pass"
            trend_reason = f"ADX {adx_value:.1f} ≥ {st.REGIME_ADX_STRONG} (추세 강함)"
        elif adx_value >= st.REGIME_ADX_WEAK:
            trend_status = "borderline"
            trend_reason = f"ADX {adx_value:.1f} 경계선 ({st.REGIME_ADX_WEAK}~{st.REGIME_ADX_STRONG})"
        else:
            trend_status = "fail"
            trend_reason = f"ADX {adx_value:.1f} < {st.REGIME_ADX_WEAK} (추세 없음 — 횡보)"
        trend_val: float | str = adx_value
    else:
        # ADX 없음 → MA 구조로 추세 존재 여부만 판단 (방향 판단 아님)
        # 둘 다 위 OR 둘 다 아래 = 추세 존재(pass) / 혼조 = 추세 불명확(borderline)
        if spy_above == qqq_above:   # 둘 다 같은 방향 → 추세 있음
            trend_status = "pass"
            trend_reason = (
                "MA 구조: SPY·QQQ 모두 20MA 위 (상승 추세)" if spy_above
                else "MA 구조: SPY·QQQ 모두 20MA 아래 (하락 추세)"
            )
        else:
            trend_status = "borderline"
            trend_reason = f"MA 구조 혼조: SPY {'위' if spy_above else '아래'}, QQQ {'위' if qqq_above else '아래'}"
        trend_val = "MA 구조 대체"

    trend_component = RegimeComponent(
        value=trend_val, status=trend_status, reason=trend_reason  # type: ignore
    )

    # ── B. 변동성 컴포넌트 ────────────────────────────────────────
    if vix <= st.REGIME_VIX_FAVORABLE:
        vol_status = "pass"
        vol_reason = f"VIX {vix:.2f} ≤ {st.REGIME_VIX_FAVORABLE} (옵션 매수 유리)"
    elif vix <= st.REGIME_VIX_BORDERLINE:
        vol_status = "borderline"
        vol_reason = f"VIX {vix:.2f} {st.REGIME_VIX_FAVORABLE}~{st.REGIME_VIX_BORDERLINE} (개별 IVR 확인 필요)"
    else:
        vol_status = "fail"
        vol_reason = f"VIX {vix:.2f} ≥ {st.REGIME_VIX_BORDERLINE} (옵션 매수 비효율)"

    vol_component = RegimeComponent(value=vix, status=vol_status, reason=vol_reason)

    # ── C. 다중 신호 방향 스코어 ──────────────────────────────────
    # 각 신호는 상승 방향 + / 하락 방향 - 가중치
    # 최대 ±5.5 (전 신호 일치 시)
    dir_score: float = 0.0
    dir_signals: list[str] = []

    # 신호 1·2: SPY/QQQ vs SMA20 (각 ±1.0)
    if spy_ma20 > 0:
        if spy_above:
            dir_score += 1.0
            dir_signals.append(f"SPY > MA20 (+1)")
        else:
            dir_score -= 1.0
            dir_signals.append(f"SPY < MA20 (-1)")

    if qqq_ma20 > 0:
        if qqq_above:
            dir_score += 1.0
            dir_signals.append(f"QQQ > MA20 (+1)")
        else:
            dir_score -= 1.0
            dir_signals.append(f"QQQ < MA20 (-1)")

    # 신호 3: SPY 일봉 DI+/DI- (±1.0, 데이터 있을 때만)
    if spy_di_plus is not None and spy_di_minus is not None:
        if spy_di_plus > spy_di_minus * 1.05:
            dir_score += 1.0
            dir_signals.append(f"SPY DI+{spy_di_plus:.1f} > DI-{spy_di_minus:.1f} (+1)")
        elif spy_di_minus > spy_di_plus * 1.05:
            dir_score -= 1.0
            dir_signals.append(f"SPY DI-{spy_di_minus:.1f} > DI+{spy_di_plus:.1f} (-1)")

    # 신호 3-B: SPY 4H DI+/DI- (±0.5 — 단기 방향 보완, 일봉 DI보다 낮은 가중치)
    # 일봉·4H 같은 방향 → 신호 강화 / 반대 방향 → 전환 감지
    _spy_dip_4h = getattr(macro, "spy_di_plus_4h",  None)
    _spy_din_4h = getattr(macro, "spy_di_minus_4h", None)
    if _spy_dip_4h is not None and _spy_din_4h is not None:
        if _spy_dip_4h > _spy_din_4h * 1.05:
            dir_score += 0.5
            dir_signals.append(f"SPY 4H DI+{_spy_dip_4h:.0f}>DI-{_spy_din_4h:.0f} (+0.5)")
        elif _spy_din_4h > _spy_dip_4h * 1.05:
            dir_score -= 0.5
            dir_signals.append(f"SPY 4H DI-{_spy_din_4h:.0f}>DI+{_spy_dip_4h:.0f} (-0.5)")

    # 신호 3-C: SPY 4H MACD 히스토그램 (±0.5 — 단기 모멘텀 전환 조기 감지)
    _spy_mh_4h = getattr(macro, "spy_macd_hist_4h", None)
    if _spy_mh_4h is not None:
        if _spy_mh_4h > 0:
            dir_score += 0.5
            dir_signals.append(f"SPY 4H MACD Hist+{_spy_mh_4h:.2f} (+0.5)")
        elif _spy_mh_4h < 0:
            dir_score -= 0.5
            dir_signals.append(f"SPY 4H MACD Hist{_spy_mh_4h:.2f} (-0.5)")

    # 신호 4: VIX 추세 — VIX가 MA20 대비 상승 중이면 약세 신호 (±1.0)
    if macro.vix_ma20 > 0:
        if vix < macro.vix_ma20:
            dir_score += 1.0
            dir_signals.append(f"VIX 하락 추세 ({vix:.1f} < MA20 {macro.vix_ma20:.1f}) (+1)")
        elif vix > macro.vix_ma20 * st.REGIME_VIX_UPTREND_RATIO:
            dir_score -= 1.0
            dir_signals.append(f"VIX 상승 추세 ({vix:.1f} > MA20×{st.REGIME_VIX_UPTREND_RATIO}) (-1)")

    # 신호 5: Fear & Greed (±0.5)
    if macro.fear_greed > 50:
        dir_score += 0.5
        dir_signals.append(f"F&G {macro.fear_greed} (낙관 +0.5)")
    elif macro.fear_greed < st.REGIME_FEAR_GREED_EXTREME_FEAR:
        dir_score -= 0.5
        dir_signals.append(f"F&G {macro.fear_greed} (극공포 -0.5)")

    # 신호 6: DXY 달러 강세 → 증시 압박 (±0.5)
    if macro.dxy > 0 and macro.dxy_ma20 > 0:
        if macro.dxy > macro.dxy_ma20 * st.REGIME_DXY_STRENGTH_RATIO:
            dir_score -= 0.5
            dir_signals.append(f"DXY 강세 ({macro.dxy:.1f} > MA20) (-0.5)")
        elif macro.dxy < macro.dxy_ma20:
            dir_score += 0.5
            dir_signals.append(f"DXY 약세 ({macro.dxy:.1f} < MA20) (+0.5)")

    # 신호 7: SOXX 반도체 지수 (±0.5)
    if macro.soxx > 0 and macro.soxx_ma20 > 0:
        if macro.soxx > macro.soxx_ma20:
            dir_score += 0.5
            dir_signals.append(f"SOXX 강세 (+0.5)")
        elif macro.soxx < macro.soxx_ma20 * st.REGIME_SOXX_WEAK_RATIO:
            dir_score -= 0.5
            dir_signals.append(f"SOXX 약세 (-0.5)")

    dir_score = round(dir_score, 1)

    # ── D. 방향 확정 ─────────────────────────────────────────────
    # long_call: score ≥ +3
    # long_put : score ≤ -3 AND 추가 검증 통과
    # both     : 혼조 (-3 < score < +3)
    if dir_score >= st.DIRECTION_SCORE_CALL_MIN:
        index_trend_dir = "long_call"
        index_reason = (
            f"방향 스코어 {dir_score:+.1f} ≥ +{st.DIRECTION_SCORE_CALL_MIN} → Long Call"
            f" | {', '.join(dir_signals[:3])}"
        )
        index_status = "pass"
    elif dir_score <= st.DIRECTION_SCORE_PUT_MAX:
        # long_put 추가 검증: VIX ≤ 30 AND (ADX ≥ 18 OR MA 구조 일치)
        _put_vix_ok  = vix <= st.DIRECTION_PUT_VIX_MAX
        _put_adx_ok  = (adx_value is not None and adx_value >= st.DIRECTION_PUT_ADX_MIN)
        _put_ma_ok   = (not spy_above and not qqq_above)  # MA 구조로 하락 확인
        _put_viable  = _put_vix_ok and (_put_adx_ok or _put_ma_ok)

        if _put_viable:
            index_trend_dir = "long_put"
            index_reason = (
                f"방향 스코어 {dir_score:+.1f} ≤ {st.DIRECTION_SCORE_PUT_MAX} + "
                f"풋 검증 통과 (VIX {vix:.1f}, ADX {'있음' if _put_adx_ok else 'MA확인'}) → Long Put"
                f" | {', '.join(dir_signals[:3])}"
            )
            index_status = "pass"
        else:
            # 점수는 하락인데 VIX 너무 높거나 추세 확인 불가 → 방향 없음
            index_trend_dir = "none"
            _reason_why = ("VIX 과고" if not _put_vix_ok else "추세 미확인")
            index_reason = (
                f"방향 스코어 {dir_score:+.1f} (하락) but {_reason_why} → 보류"
                f" | {', '.join(dir_signals[:3])}"
            )
            index_status = "fail"
    else:
        # 혼조 구간
        index_trend_dir = "both"
        index_reason = (
            f"방향 스코어 {dir_score:+.1f} (혼조 구간 {st.DIRECTION_SCORE_PUT_MAX}~{st.DIRECTION_SCORE_CALL_MIN})"
            f" | {', '.join(dir_signals[:3])}"
        )
        index_status = "borderline"

    index_component = RegimeComponent(
        value=dir_score, status=index_status, reason=index_reason  # type: ignore
    )

    # ── E. 리스크 요인 수집 ──────────────────────────────────────
    risk_factors: list[str] = []
    if vix > 20:
        risk_factors.append(f"VIX 상승 ({vix:.1f}) — 옵션 프리미엄 과대")
    if not spy_above:
        risk_factors.append("SPY 20MA 아래 — 하락 추세")
    if not qqq_above:
        risk_factors.append("QQQ 20MA 아래 — 기술주 약세")
    if macro.fear_greed < st.REGIME_FEAR_GREED_EXTREME_FEAR:
        risk_factors.append(f"Fear & Greed {macro.fear_greed} ({macro.fear_greed_label}) — 극단적 공포")
    if macro.fear_greed > st.REGIME_FEAR_GREED_EXTREME_GREED:
        risk_factors.append(f"Fear & Greed {macro.fear_greed} ({macro.fear_greed_label}) — 극단적 탐욕")
    if macro.vix_ma20 > 0 and vix > macro.vix_ma20 * st.REGIME_VIX_UPTREND_RATIO:
        risk_factors.append(
            f"VIX 상승 추세 ({vix:.1f} > MA20 {macro.vix_ma20:.1f}) — 공포 확산 중"
        )
    if macro.dxy > 0 and macro.dxy_ma20 > 0 and macro.dxy > macro.dxy_ma20 * st.REGIME_DXY_STRENGTH_RATIO:
        risk_factors.append(
            f"DXY 강세 ({macro.dxy:.1f} > MA20 {macro.dxy_ma20:.1f}) — 달러 강세, 수익 압박"
        )
    if macro.yield_10y >= st.REGIME_YIELD_CRITICAL:
        risk_factors.append(
            f"10년물 금리 {macro.yield_10y:.2f}% ≥ {st.REGIME_YIELD_CRITICAL}% — 고금리 밸류에이션 압박"
        )
    elif macro.yield_10y >= st.REGIME_YIELD_WARNING:
        risk_factors.append(
            f"10년물 금리 {macro.yield_10y:.2f}% — 금리 주의 구간 ({st.REGIME_YIELD_WARNING}~{st.REGIME_YIELD_CRITICAL}%)"
        )
    if macro.soxx > 0 and macro.soxx_ma20 > 0 and macro.soxx < macro.soxx_ma20 * st.REGIME_SOXX_WEAK_RATIO:
        risk_factors.append(
            f"SOXX 약세 ({macro.soxx:.0f} < MA20 {macro.soxx_ma20:.0f}) — 기술주 선행 약세"
        )

    # ── E-2. RRE 매크로/섹터 보강 리스크 (docs §8) ───────────────
    # macro_warn: '심각' 등급 경고 수 → 2건 이상 동시 발생 시 favorable 강등
    macro_warn = 0
    # ① 섹터 대장주 피어 약세
    _peer_ratio = getattr(macro, "sector_peer_weak_ratio", None)
    if _peer_ratio is not None and _peer_ratio >= st.RRE_MACRO_PEER_WEAK_RATIO:
        macro_warn += 1
        _peer_detail = getattr(macro, "sector_peer_detail", "") or ""
        risk_factors.append(
            f"반도체 피어 약세 {_peer_ratio*100:.0f}% ({_peer_detail}) — 섹터 끌림 위험"
        )
    # ② SMH vs SPY 상대강도 이탈
    _rs = getattr(macro, "smh_rs_20d", None)
    if _rs is not None:
        if _rs < st.RRE_MACRO_SECTOR_RS_CRITICAL:
            macro_warn += 1
            risk_factors.append(f"SMH 상대강도 {_rs:+.1f}%p (강한 이탈) — 반도체 자금 유출")
        elif _rs < st.RRE_MACRO_SECTOR_RS_WARNING:
            risk_factors.append(f"SMH 상대강도 {_rs:+.1f}%p — 반도체 약세 경고")
    # ③ HYG 크레딧 스프레드 확대
    _hyg_chg = getattr(macro, "hyg_ief_chg_20d", None)
    if _hyg_chg is not None and _hyg_chg < st.RRE_MACRO_HYG_DROP_PCT:
        macro_warn += 1
        risk_factors.append(
            f"HYG/IEF 비율 20일 {_hyg_chg:+.1f}% — 신용 리스크 확대 (주식 선행 하락 신호)"
        )
    # ④ VIX 백워데이션
    _vix_bw = getattr(macro, "vix_backwardation", None)
    if _vix_bw is not None and _vix_bw > st.RRE_MACRO_VIX_BACKWARDATION:
        macro_warn += 1
        risk_factors.append(f"VIX 백워데이션 (VIX9D/VIX {_vix_bw:.2f}) — 즉각적 위험 신호")
    # ⑤ 시장 폭 약화 (RSP 동일가중이 SPY 시총가중보다 뒤짐 = 소수 종목 견인)
    _breadth = getattr(macro, "breadth_rsp_spy_20d", None)
    if _breadth is not None and _breadth < st.RRE_MACRO_BREADTH_WEAK:
        macro_warn += 1
        risk_factors.append(
            f"시장 폭 약화 (RSP−SPY 20일 {_breadth:+.1f}%p) — 소수 종목 의존 상승"
        )
    # ⑥ 금리 방향성 (상승 중)
    _yld_chg = getattr(macro, "yield_10y_chg_20d", None)
    if _yld_chg is not None:
        if _yld_chg > st.RRE_MACRO_YIELD_RISE_CRITICAL:
            macro_warn += 1
            risk_factors.append(f"10년물 20일 +{_yld_chg:.2f}%p 급등 — 고PER 성장주 강한 압박")
        elif _yld_chg > st.RRE_MACRO_YIELD_RISE_WARNING:
            risk_factors.append(f"10년물 20일 +{_yld_chg:.2f}%p 상승 중 — 밸류에이션 압박")
    # ⑦ SKEW (기관 하락 헤지)
    _skew = getattr(macro, "skew", None)
    if _skew is not None and _skew > 140:
        risk_factors.append(f"SKEW {_skew:.0f} > 140 — 기관 OTM 풋 대량 매입 (하락 헤지 급증)")
    # ⑧ 섹터 로테이션 (방어주 > 성장주 outperform = 리스크오프 전환)
    _rot = getattr(macro, "sector_rotation_10d", None)
    if _rot is not None and _rot > st.RRE_MACRO_ROTATION_GAP:
        macro_warn += 1
        risk_factors.append(
            f"섹터 로테이션: 방어주가 성장주 +{_rot:.1f}%p outperform — 리스크오프 전환"
        )

    # ── F. 레짐 최종 판정 ────────────────────────────────────────
    # unfavorable 조건:
    #   - VIX > 30 (옵션 매수 비효율) — vol_status fail
    #   - 추세 없음 (ADX < 18 + 혼조 MA) — trend_status fail
    #   - 방향 스코어 혼조 + 추세 약함 — index_status fail/borderline
    # 핵심 변경: long_put이 확정된 경우 unfavorable이어도 방향은 long_put 유지
    fail_count       = sum(1 for s in [trend_status, vol_status, index_status] if s == "fail")
    borderline_count = sum(1 for s in [trend_status, vol_status, index_status] if s == "borderline")

    if vol_status == "fail":
        # VIX > 30: 어느 방향이든 옵션 비효율 → 진짜 unfavorable
        regime_status     = "unfavorable"
        allowed_direction = "none"
    elif index_trend_dir == "none":
        # 방향 스코어 하락인데 풋 검증 실패 → 불명확 횡보
        regime_status     = "unfavorable"
        allowed_direction = "none"
    elif index_trend_dir == "both":
        # 혼조: 추세 강도에 따라 borderline or unfavorable
        if trend_status == "fail":
            regime_status     = "unfavorable"
            allowed_direction = "none"
        else:
            regime_status     = "borderline"
            allowed_direction = "both"
    else:
        # long_call 또는 long_put 방향 확정
        if fail_count >= 1:
            # 추세 fail이지만 방향은 명확 → borderline (약한 추세에서의 거래)
            regime_status = "borderline"
        elif borderline_count >= 2:
            regime_status = "borderline"
        elif borderline_count == 1:
            regime_status = "borderline"
        else:
            regime_status = "favorable"
        allowed_direction = index_trend_dir

    # 매크로/섹터 심각 경고 2건+ 동시 발생 → favorable 강등 (long_put 방향은 유지)
    if regime_status == "favorable" and macro_warn >= 2:
        regime_status = "borderline"
        risk_factors.append(f"매크로/섹터 경고 {macro_warn}건 동시 → 레짐 borderline 강등")

    # 확신도 계산
    pass_count       = sum(1 for s in [trend_status, vol_status, index_status] if s == "pass")
    regime_confidence = pass_count / 3.0
    trend_confidence  = 1.0 if trend_status == "pass" else (0.5 if trend_status == "borderline" else 0.0)

    result = MarketRegime(
        regime_status=regime_status,
        allowed_direction=allowed_direction,  # type: ignore
        trend_strength=trend_component,
        volatility=vol_component,
        index_trend=index_component,
        risk_factors=risk_factors,
        trend_confidence=trend_confidence,
        regime_confidence=regime_confidence,
        adx_source=adx_source,  # type: ignore
    )

    log.info(
        "regime_analyzed",
        status=regime_status,
        direction=allowed_direction,
        dir_score=dir_score,
        confidence=regime_confidence,
    )
    return result


# ─────────────────────────────────────────────────────────────
# 2. 기술 분석 점수 (Step 4)
# ─────────────────────────────────────────────────────────────

def calculate_technical_score(
    ticker: str,
    direction: str,
    summary: SummaryData,
) -> TechnicalScore:
    """
    종목 기술 분석 점수 산출 (0~100점).
    Devil's Advocate 차감을 포함합니다.

    Args:
        ticker: 종목 심볼
        direction: long_call | long_put
        summary: SummaryData
    Returns:
        TechnicalScore
    """
    ticker_data = summary.tickers.get(ticker)

    if not ticker_data:
        log.warning("technical_no_ticker_data", ticker=ticker)
        return TechnicalScore(
            ticker=ticker, direction=direction,  # type: ignore
            ma_alignment="mixed", adx_score=0, rsi_score=0,
            macd_score=0, rvol_score=0, raw_score=0, final_score=0,
            trend_confirmed=False, capital_flow_confirmed=False,
            obv_ok=False, option_flow_ok=False, darkpool_ok=False,
            signal_count=0,
        )

    tech = ticker_data.technical
    is_long = direction == "long_call"

    # ── MA 정렬 점수 (0~25) — MA200 + 주가 위치 포함 ──────────
    # 기존: MA 순서만 체크 → 급락 당일 SMA5>SMA20>SMA60 이어도 가격은 SMA20 아래
    # 수정: 주가가 SMA20 위에 있어야 full/partial 점수 인정 (방향성 확인)
    if is_long:
        full_align = tech.ma5 > tech.ma20 > tech.ma60 and tech.ma60 > 0
        price_above_ma20 = (tech.price > tech.ma20) if tech.ma20 > 0 else True
        if full_align and price_above_ma20:
            ma_above_200 = tech.ma200 > 0 and tech.price > tech.ma200
            ma_score = st.SCORE_MA_FULL_WITH_MA200 if ma_above_200 else st.SCORE_MA_FULL_NO_MA200
            ma_align = "bullish"
        elif full_align and not price_above_ma20:
            # 장기 MA 정배열이지만 주가가 SMA20 이탈 → mixed (급락 당일 패턴)
            ma_score = st.SCORE_MA_NONE
            ma_align = "mixed"
        elif tech.ma5 > tech.ma20 and tech.ma20 > 0 and price_above_ma20:
            ma_score = st.SCORE_MA_PARTIAL
            ma_align = "bullish"
        else:
            ma_score = st.SCORE_MA_NONE
            ma_align = "mixed"
    else:
        full_align = tech.ma5 < tech.ma20 < tech.ma60 and tech.ma60 > 0
        price_below_ma20 = (tech.price < tech.ma20) if tech.ma20 > 0 else True
        if full_align and price_below_ma20:
            ma_below_200 = tech.ma200 > 0 and tech.price < tech.ma200
            ma_score = st.SCORE_MA_FULL_WITH_MA200 if ma_below_200 else st.SCORE_MA_FULL_NO_MA200
            ma_align = "bearish"
        elif full_align and not price_below_ma20:
            # 장기 역배열이지만 주가가 SMA20 위 → mixed
            ma_score = st.SCORE_MA_NONE
            ma_align = "mixed"
        elif tech.ma5 < tech.ma20 and tech.ma20 > 0 and price_below_ma20:
            ma_score = st.SCORE_MA_PARTIAL
            ma_align = "bearish"
        else:
            ma_score = st.SCORE_MA_NONE
            ma_align = "mixed"

    # ── ADX 점수 (0~25) — DI+/DI- 방향성 인식 ────────────────
    # 기존: ADX 강도만 측정 → 강한 하락추세도 만점
    # 수정: DI-가 DI+를 초과하면 롱콜에서 0점 (방향 역행 추세)
    adx = tech.adx14
    if adx >= st.ADX_STRONG:
        adx_base = st.SCORE_ADX_STRONG
    elif adx >= st.ADX_MEDIUM:
        adx_base = st.SCORE_ADX_MEDIUM
    elif adx >= st.ADX_WEAK:
        adx_base = st.SCORE_ADX_WEAK
    else:
        adx_base = st.SCORE_ADX_NONE

    di_p = getattr(tech, "di_plus", 0.0) or 0.0
    di_n = getattr(tech, "di_minus", 0.0) or 0.0
    if di_p > 0 and di_n > 0:
        if is_long and di_n > di_p:
            # 강한 하락추세 (DI- > DI+) → 롱콜 역방향, 점수 0
            adx_score = 0
        elif not is_long and di_p > di_n:
            # 강한 상승추세 (DI+ > DI-) → 롱풋 역방향, 점수 0
            adx_score = 0
        else:
            adx_score = adx_base
    else:
        # DI 데이터 없으면 기존 강도 기준만 적용
        adx_score = adx_base

    # ── RSI 점수 (0~25) ────────────────────────────────────
    rsi = tech.rsi14
    if is_long:
        if st.RSI_LONG_CALL_IDEAL_MIN <= rsi <= st.RSI_LONG_CALL_IDEAL_MAX:
            rsi_score = st.SCORE_RSI_IDEAL
        elif st.RSI_LONG_CALL_OK_MIN <= rsi < st.RSI_LONG_CALL_IDEAL_MIN:
            rsi_score = st.SCORE_RSI_OK
        elif rsi > st.RSI_LONG_CALL_IDEAL_MAX:
            rsi_score = st.SCORE_RSI_EXTREME  # 과매수 감점
        else:
            rsi_score = st.SCORE_RSI_NONE
    else:
        if st.RSI_LONG_PUT_IDEAL_MIN <= rsi <= st.RSI_LONG_PUT_IDEAL_MAX:
            rsi_score = st.SCORE_RSI_IDEAL
        elif st.RSI_LONG_PUT_IDEAL_MAX < rsi <= st.RSI_LONG_PUT_OK_MAX:
            rsi_score = st.SCORE_RSI_OK
        elif rsi < st.RSI_LONG_PUT_IDEAL_MIN:
            rsi_score = st.SCORE_RSI_EXTREME  # 과매도 감점
        else:
            rsi_score = st.SCORE_RSI_NONE

    # ── MACD 점수 (0~25) ───────────────────────────────────
    if is_long:
        if tech.macd_cross == "golden" and tech.macd_histogram > 0:
            macd_score = st.SCORE_MACD_CROSS
        elif tech.macd_line > tech.macd_signal:
            macd_score = st.SCORE_MACD_TREND
        else:
            macd_score = st.SCORE_MACD_NONE
    else:
        if tech.macd_cross == "death" and tech.macd_histogram < 0:
            macd_score = st.SCORE_MACD_CROSS
        elif tech.macd_line < tech.macd_signal:
            macd_score = st.SCORE_MACD_TREND
        else:
            macd_score = st.SCORE_MACD_NONE

    # ── RVOL 점수 (0~25) ───────────────────────────────────
    rvol = tech.avg_volume_ratio
    if rvol >= st.RVOL_HIGH:
        rvol_score = st.SCORE_RVOL_HIGH
    elif rvol >= st.RVOL_MED:
        rvol_score = st.SCORE_RVOL_MED
    elif rvol >= st.RVOL_LOW:
        rvol_score = st.SCORE_RVOL_LOW
    else:
        rvol_score = st.SCORE_RVOL_NONE

    raw_score = ma_score + adx_score + rsi_score + macd_score + rvol_score  # 최대 125점 (st.SCORE_RAW_MAX)

    # ── Devil's Advocate 차감 ─────────────────────────────
    deduction = _apply_devils_advocate(tech, direction, is_long)
    # 0~100 정규화: 5개 컴포넌트 각 25점 최대 = st.SCORE_RAW_MAX점 스케일 → 100점 스케일
    final_score = max(0.0, (raw_score - deduction) / st.SCORE_RAW_MAX * 100.0)

    # ── 추세/자금 유입 확인 ────────────────────────────────
    trend_confirmed = adx_score > 0 and ma_score > 0
    obv_ok = (tech.obv_direction == "up") == is_long

    # 옵션 플로우 이상 감지: P/C ratio + 총 OI 비교
    opt_data = summary.options.get(ticker) if summary else None
    if opt_data:
        pc = opt_data.pc_ratio
        call_oi = opt_data.total_call_oi
        put_oi = opt_data.total_put_oi
        # 이상 콜 플로우: P/C < 0.7 또는 콜 OI가 풋 OI의 1.5배 이상
        # 이상 풋 플로우: P/C > 1.5 또는 풋 OI가 콜 OI의 1.5배 이상
        if is_long:
            option_flow_ok = (pc < st.PC_RATIO_CALL_BULL
                              or (call_oi > 0 and put_oi > 0 and call_oi >= put_oi * st.OI_RATIO_DOMINANCE))
        else:
            option_flow_ok = (pc > st.PC_RATIO_PUT_BULL
                              or (call_oi > 0 and put_oi > 0 and put_oi >= call_oi * st.OI_RATIO_DOMINANCE))
        anomaly_count = sum(1 for e in opt_data.chain if e.get("is_anomaly", False))
        if anomaly_count >= st.ANOMALY_COUNT_OVERRIDE:
            option_flow_ok = True
    else:
        option_flow_ok = False

    # 지지/저항선 기반 진입 신호 (다크풀 데이터 대체)
    # 롱콜: 지지선 위 5% 이내 = 이탈 리스크 낮은 좋은 진입 구간
    # 롱풋: 저항선 아래 5% 이내 = 돌파 실패 후 하락 기대 구간
    support_ok = False
    if tech.price > 0:
        if is_long and tech.support1 and tech.support1 > 0:
            gap_pct = (tech.price - tech.support1) / tech.price * 100
            support_ok = 0 < gap_pct <= st.SUPPORT_GAP_MAX_PCT
        elif not is_long and tech.resistance1 and tech.resistance1 > 0:
            gap_pct = (tech.resistance1 - tech.price) / tech.price * 100
            support_ok = 0 < gap_pct <= st.SUPPORT_GAP_MAX_PCT

    capital_flow_confirmed = sum([rvol >= st.RVOL_MED, obv_ok, option_flow_ok, support_ok]) >= st.CAPITAL_FLOW_MIN_SIGNALS

    # 신호 수 (최대 8: 추세 4 + 자금유입 4)
    trend_signals = sum([
        ma_score >= st.SIGNAL_MA_SCORE_MIN,
        adx_score >= st.SIGNAL_ADX_SCORE_MIN,
        macd_score >= st.SIGNAL_MACD_SCORE_MIN,
        rsi_score >= st.SIGNAL_RSI_SCORE_MIN,
    ])
    flow_signals = sum([rvol >= st.RVOL_MED, obv_ok, option_flow_ok, support_ok])
    signal_count = trend_signals + flow_signals

    # Kavout K-Score final_score 보정은 buy_steps.py Step 4 post-loop에서 단일 처리.
    # (signal_count까지 함께 조정하며, 여기서 중복 적용하면 최대 +12pt 과다 반영됨)
    # → 이 함수에서는 signal_count 계산까지만 담당. final_score 조정은 하지 않음.

    # 밸류에이션 보정: EPS YoY 성장률 기반 ±2pt (데이터 있을 때만)
    val = ticker_data.valuation if ticker_data else None
    if val and val.eps_growth_yoy is not None:
        if is_long and val.eps_growth_yoy > st.EPS_GROWTH_BULL_THRESHOLD:
            final_score = min(100.0, final_score + st.SCORE_VALUATION_BOOST)
        elif is_long and val.eps_growth_yoy < st.EPS_GROWTH_BEAR_THRESHOLD:
            final_score = max(0.0, final_score + st.SCORE_VALUATION_PENALTY)
        elif not is_long and val.eps_growth_yoy < st.EPS_GROWTH_BEAR_THRESHOLD:
            final_score = min(100.0, final_score + st.SCORE_VALUATION_BOOST)

    result = TechnicalScore(
        ticker=ticker,
        direction=direction,  # type: ignore
        ma_alignment=ma_align,  # type: ignore
        adx_score=adx_score,
        rsi_score=rsi_score,
        macd_score=macd_score,
        rvol_score=rvol_score,
        raw_score=raw_score,
        final_score=final_score,
        trend_confirmed=trend_confirmed,
        capital_flow_confirmed=capital_flow_confirmed,
        obv_ok=obv_ok,
        option_flow_ok=option_flow_ok,
        darkpool_ok=support_ok,   # 지지/저항 신호로 대체 (필드명 유지)
        signal_count=signal_count,
    )

    log.info(
        "technical_scored",
        ticker=ticker,
        raw=raw_score,
        final=final_score,
        signals=signal_count,
    )
    return result


def _apply_devils_advocate(tech: Any, direction: str, is_long: bool) -> float:
    """
    Devil's Advocate 차감 계산 (Step 6)

    Returns:
        차감 포인트 (0 이상)
    """
    deduction = 0.0

    # 과열 감점 (롱콜): RSI > DA_RSI_EXTREME_THRESHOLD + 52주 고점 98% 이상
    if is_long and tech.rsi14 > st.DA_RSI_EXTREME_THRESHOLD and tech.position_52w > st.DA_52W_HIGH_THRESHOLD_STRONG:
        deduction += abs(st.DA_RSI_EXTREME_PENALTY)

    # 거래량 미동반 감점
    if tech.avg_volume_ratio < st.DA_AVG_VOLUME_THRESHOLD:
        deduction += abs(st.DA_LOW_VOLUME_PENALTY)

    # 볼린저밴드 상단 돌파 (롱콜 시 과매수)
    if is_long and tech.bb_position == "upper_break":
        deduction += abs(st.DA_BOLLINGER_BREAK_PENALTY)

    # 볼린저밴드 하단 돌파 (롱풋 시 과매도)
    if not is_long and tech.bb_position == "lower_break":
        deduction += abs(st.DA_BOLLINGER_BREAK_PENALTY)

    # 52주 고점 근처에서 롱콜 — 추가 리스크
    if is_long and tech.position_52w > st.DA_52W_HIGH_THRESHOLD_WEAK:
        deduction += abs(st.DA_NEAR_52W_HIGH_PENALTY)

    # 당일 급등/급락 — 늦은 진입 페널티
    change = getattr(tech, "change_pct", 0.0) or 0.0
    if is_long and change >= st.DA_DAILY_MOVE_LARGE:
        deduction += abs(st.DA_LARGE_DAILY_MOVE)
    elif is_long and change >= st.DA_DAILY_MOVE_MEDIUM:
        deduction += abs(st.DA_MEDIUM_DAILY_MOVE)
    elif not is_long and change <= -st.DA_DAILY_MOVE_LARGE:
        deduction += abs(st.DA_LARGE_DAILY_MOVE)
    elif not is_long and change <= -st.DA_DAILY_MOVE_MEDIUM:
        deduction += abs(st.DA_MEDIUM_DAILY_MOVE)

    # SMA20 이탈 + 급락 복합 패널티 — 추세 붕괴 신호
    # 조건: 롱콜에서 주가가 SMA20 아래 + 당일 -5% 이상 하락
    ma20_val = getattr(tech, "ma20", 0.0) or 0.0
    price_val = getattr(tech, "price", 0.0) or 0.0
    if (is_long and ma20_val > 0 and price_val > 0
            and price_val < ma20_val and change <= -5.0):
        deduction += abs(st.DA_MA20_BREAK_PENALTY)

    return deduction


# ─────────────────────────────────────────────────────────────
# 3. Black-Scholes Greeks 계산
# ─────────────────────────────────────────────────────────────

def calculate_greeks(
    spot: float,
    strike: float,
    expiry_days: int,
    iv: float,
    option_type: str = "call",
    risk_free_rate: float | None = None,
) -> Greeks:
    """
    Black-Scholes 모델 기반 Greeks 계산

    Args:
        spot: 현재 주가
        strike: 행사가
        expiry_days: 만기까지 남은 일수
        iv: Implied Volatility (소수점: 0.85 = 85%)
        option_type: "call" | "put"
        risk_free_rate: 무위험 금리 (None이면 config 사용)

    Returns:
        Greeks 인스턴스

    Raises:
        ValueError: 음수 주가, 행사가, IV
    """
    if spot <= 0 or strike <= 0 or iv <= 0:
        raise ValueError(f"Invalid inputs: spot={spot}, strike={strike}, iv={iv}")

    if expiry_days <= 0:
        # 만기 당일: 내재가치만
        intrinsic = max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
        delta = 1.0 if (option_type == "call" and spot > strike) else 0.0
        return Greeks(delta=delta, gamma=0.0, theta=0.0, vega=0.0, iv=iv, ivr=0.0)

    r = risk_free_rate or cfg.RISK_FREE_RATE
    T = expiry_days / 365.0

    # d1, d2 계산
    try:
        d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
        d2 = d1 - iv * math.sqrt(T)
    except (ValueError, ZeroDivisionError):
        return Greeks(delta=0.5, gamma=0.0, theta=0.0, vega=0.0, iv=iv)

    # N(d1), N(d2)
    Nd1 = norm.cdf(d1)
    Nd2 = norm.cdf(d2)
    nd1 = norm.pdf(d1)   # 표준 정규 PDF

    if option_type == "call":
        delta = Nd1
    else:
        delta = Nd1 - 1.0   # 음수 (put), 스키마는 ge=0이므로 abs 적용

    gamma = nd1 / (spot * iv * math.sqrt(T))
    vega = spot * nd1 * math.sqrt(T) / 100   # 1% IV 변화당 달러
    theta_annual = (
        -(spot * nd1 * iv) / (2 * math.sqrt(T))
        - r * strike * math.exp(-r * T) * (Nd2 if option_type == "call" else (1 - Nd2))
    )
    theta = theta_annual / 365.0  # 일일 세타

    return Greeks(
        delta=abs(delta),
        gamma=gamma,
        theta=theta,
        vega=vega,
        iv=iv,
    )


# ─────────────────────────────────────────────────────────────
# 4. 옵션 유효성 검증 (Step 7)
# ─────────────────────────────────────────────────────────────

def validate_option(
    ticker: str,
    strike: float,
    expiry: date,
    direction: str,
    delta: float,
    ivr: float,
    oi: int,
    spread_pct: float,
    mid_price: float,
    iv: float = 0.0,
    theta: float = 0.0,
    gamma: float = 0.0,
    vega: float = 0.0,
    delta_min: float | None = None,
    delta_max: float | None = None,
    dte_min: int | None = None,
) -> OptionValidity:
    """
    옵션 유효성 검증 (섹션 9.3 기준)

    Args:
        ticker: 종목
        strike: 행사가
        expiry: 만기
        direction: long_call | long_put
        delta: 옵션 델타
        ivr: IV Rank (0~100)
        oi: 미결제약정
        spread_pct: 매수-매도 스프레드 %
        mid_price: 옵션 중간가
        iv: Implied Volatility
        theta: 일일 세타
        gamma: 감마 (calculate_greeks 결과)
        vega: 베가 (calculate_greeks 결과)

    Returns:
        OptionValidity
    """
    dte = max(0, (expiry - date.today()).days)

    _delta_min = delta_min if delta_min is not None else cfg.DELTA_MIN
    _delta_max = delta_max if delta_max is not None else cfg.DELTA_MAX
    _dte_min   = dte_min   if dte_min   is not None else cfg.DTE_MIN

    delta_ok = _delta_min <= delta <= _delta_max
    ivr_ok = ivr <= cfg.IVR_MAX
    ivr_warning = cfg.IVR_WARNING <= ivr <= cfg.IVR_MAX
    oi_ok = oi >= cfg.OI_MIN
    oi_warning = cfg.OI_MIN <= oi <= cfg.OI_WARNING
    spread_ok = spread_pct <= cfg.SPREAD_MAX_PCT
    dte_ok = dte >= _dte_min

    # ── 자본 가용성 체크 (경고만, 진입 차단 안 함) ──────────────────
    # 1계약 비용 = 프리미엄 × 100 + 수수료
    # TOTAL_CAPITAL 초과해도 is_valid에 영향 없음 — 실제 자본이 더 많을 수 있음
    cost_1_contract = mid_price * 100 + cfg.COMMISSION_PER_CONTRACT
    capital_ok = (mid_price <= 0) or (cost_1_contract <= cfg.TOTAL_CAPITAL)

    is_valid = delta_ok and ivr_ok and oi_ok and spread_ok and dte_ok

    reasons = []
    if not delta_ok:
        reasons.append(f"Delta {delta:.2f} 범위 이탈 ({_delta_min}~{_delta_max})")
    if not ivr_ok:
        reasons.append(f"IVR {ivr:.0f}% > {cfg.IVR_MAX}% 제한")
    if not oi_ok:
        reasons.append(f"OI {oi} < {cfg.OI_MIN} 최소 기준")
    if not spread_ok:
        reasons.append(f"Spread {spread_pct:.1f}% > {cfg.SPREAD_MAX_PCT}% 제한")
    if not dte_ok:
        reasons.append(f"DTE {dte}일 < {_dte_min}일 최소 기준")
    if not capital_ok:
        # 경고만 — 진입 차단 안 함 (실제 자본이 TOTAL_CAPITAL보다 클 수 있음)
        reasons.append(
            f"[경고] 1계약 ${cost_1_contract:,.0f}"
            f" > 설정 총자본 ${cfg.TOTAL_CAPITAL:,.0f} (초과 진입 허용)"
        )

    greeks = Greeks(delta=delta, gamma=gamma, theta=theta, vega=vega, iv=iv, ivr=ivr)

    result = OptionValidity(
        ticker=ticker,
        strike=strike,
        expiry=expiry,
        direction=direction,  # type: ignore
        delta_ok=delta_ok,
        ivr_ok=ivr_ok,
        ivr_warning=ivr_warning,
        oi_ok=oi_ok,
        oi_warning=oi_warning,
        spread_ok=spread_ok,
        dte_ok=dte_ok,
        is_valid=is_valid,
        exclusion_reason="; ".join(reasons),
        mid_price=mid_price,
        oi=oi,
        greeks=greeks,
    )

    log.info(
        "option_validated",
        ticker=ticker, strike=strike, valid=is_valid,
        delta=delta, ivr=ivr, dte=dte,
    )
    return result


# ─────────────────────────────────────────────────────────────
# 4-B. 투자 기간 분류  (Step 7 전 호출)
# ─────────────────────────────────────────────────────────────

def classify_investment_horizon(
    ticker: str,
    *,
    rsi14: float | None = None,
    adx14: float | None = None,
    avg_volume_ratio: float | None = None,   # RVOL
    change_pct: float | None = None,         # 당일 등락률 (%)
    ma_alignment: str | None = None,         # "bullish" / "bearish" / "mixed"
    analyst_buy_pct: float | None = None,    # Buy 비율 (0~1)
    peg_ratio: float | None = None,
    revenue_growth_yoy: float | None = None, # %
    days_since_earnings: int | None = None,  # 어닝 발표 후 경과일
    days_until_earnings: int | None = None, # 다음 어닝까지 남은 일수 (미래)
    forward_pe: float | None = None,
    ivr: float | None = None,               # IVR (옵션 가격 수준, 높을수록 비쌈)
) -> tuple[list[str], str]:
    """
    종목의 투자 기간 적합성 분류.

    단기는 항상 기본 포함. 우선 추천(primary) 기간을 tuple 두 번째 원소로 반환.

    분류 기준:
      단기 (DTE 25-40): 기본 항상 포함. 아래 조건 충족 시 우선 추천.
        - RSI 55~72 AND ADX ≥ 22 AND RVOL ≥ 1.3 AND IVR < 50
        - AND (당일 등락 ≥ +5% OR 어닝 후 14일 이내)
      중기 (DTE 45-90): 표준 스윙 — ADX/MA 조건 미달이어도 기본 포함
        - IVR < 65 권장
      장기 (DTE 90-180): 구조적 성장 + 옵션 저렴할 때
        - PEG ≤ 2.0 OR 매출 성장 ≥ 20%, AND IVR < 35

    Returns:
        (horizons, primary) — horizons: 해당 기간 목록, primary: 우선 추천 기간
    """
    horizons: list[str] = []

    # ── 단기 판정 (항상 기본 포함, 조건 충족 시 우선 추천) ────────
    _rsi_ok   = rsi14 is not None and st.HORIZON_SHORT_RSI_MIN <= rsi14 <= st.HORIZON_SHORT_RSI_MAX
    _adx_ok   = adx14 is not None and adx14 >= st.HORIZON_SHORT_ADX_MIN
    _rvol_ok  = avg_volume_ratio is not None and avg_volume_ratio >= st.HORIZON_SHORT_RVOL_MIN
    _ivr_short_ok = ivr is None or ivr < st.HORIZON_SHORT_IVR_MAX
    _move_ok  = change_pct is not None and abs(change_pct) >= st.HORIZON_SHORT_MOVE_MIN
    _earn_ok  = days_since_earnings is not None and days_since_earnings <= st.HORIZON_SHORT_EARN_DAYS
    _short_priority = _rsi_ok and _adx_ok and _rvol_ok and _ivr_short_ok and (_move_ok or _earn_ok)
    horizons.append("단기")  # 항상 기본 포함

    # ── 중기 판정 ──────────────────────────────────────────────
    _mid_adx  = adx14 is not None and adx14 >= st.HORIZON_MID_ADX_MIN
    _mid_ma   = ma_alignment == "bullish"
    _ivr_mid_ok = ivr is None or ivr < st.HORIZON_MID_IVR_MAX
    if ((_mid_adx and _mid_ma) or _mid_ma or _mid_adx) and _ivr_mid_ok:
        horizons.append("중기")
    elif _ivr_mid_ok:
        horizons.append("중기")  # 기본 추천 (IVR 허용 범위 내)
    else:
        horizons.append("중기")  # IVR 높아도 중기는 항상 포함 (기본값)

    # ── 장기 판정 (펀더멘털 + 저IVR 조합 필요) ───────────────────
    _peg_ok  = peg_ratio is not None and 0 < peg_ratio <= st.HORIZON_LONG_PEG_MAX
    _rev_ok  = revenue_growth_yoy is not None and revenue_growth_yoy >= st.HORIZON_LONG_REV_MIN
    _pe_ok   = forward_pe is not None and 0 < forward_pe <= 80
    _ivr_long_ok = ivr is None or ivr < st.HORIZON_LONG_IVR_MAX
    if (_peg_ok or (_rev_ok and _pe_ok)) and _ivr_long_ok:
        horizons.append("장기")

    # ── 초장기 판정 (DTE 180~365, LEAPS) ───────────────────────
    _ultra_rev = revenue_growth_yoy is not None and revenue_growth_yoy >= st.HORIZON_ULTRA_REV_MIN
    _ultra_peg = peg_ratio is not None and 0 < peg_ratio <= st.HORIZON_ULTRA_PEG_MAX
    if _ultra_peg or (_ultra_rev and _pe_ok):
        horizons.append("초장기")

    # ── 어닝 촉매 창 판정 ──────────────────────────────────────
    # 7~21일 전: 어닝 기대 모멘텀 → 단기 우선 권고
    _pre_earn_window = (
        days_until_earnings is not None and 7 < days_until_earnings <= 21
    )
    # 7일 이내: 어닝 직전 급등락 불확실 → 단기 priority 제외
    _pre_earn_danger = (
        days_until_earnings is not None and days_until_earnings <= 7
    )

    # ── primary horizon 결정 ────────────────────────────────────
    if _short_priority and not _pre_earn_danger:
        primary = "단기"
    elif _pre_earn_window and not _short_priority and not _pre_earn_danger:
        # 모멘텀 미충족이어도 어닝 창 내이면 단기 권고
        primary = "단기"
    elif "중기" in horizons and (_mid_adx or _mid_ma):
        primary = "중기"
    elif "장기" in horizons:
        primary = "장기"
    else:
        primary = "중기"

    log.debug("horizon_classified", ticker=ticker, horizons=horizons, primary=primary,
              rsi=rsi14, adx=adx14, rvol=avg_volume_ratio, ivr=ivr,
              change_pct=change_pct, peg=peg_ratio, short_priority=_short_priority,
              days_until_earnings=days_until_earnings, pre_earn_window=_pre_earn_window)
    return horizons, primary


# ─────────────────────────────────────────────────────────────
# 5. 시나리오 계산 (Step 8)
# ─────────────────────────────────────────────────────────────

def calculate_scenario(
    ticker: str,
    direction: str,
    strike: float,
    expiry: date,
    current_stock_price: float,
    current_premium: float,
    delta: float,
    theta: float,
    iv: float,
    atm_straddle_price: float,
    adx: float = 25.0,
    signal_count: int = 4,
    total_capital: float | None = None,
    max_per_position: float | None = None,
    commission_per_contract: float | None = None,
    bull_target_price: float | None = None,  # 애널리스트 목표주가 (롱콜 bull case 오버라이드)
    di_plus: float = 0.0,    # DI+ (방향성 DMI)
    di_minus: float = 0.0,   # DI- (방향성 DMI)
    macro_score: int = 50,   # 레짐 매크로 점수 (0-100)
) -> Scenario:
    """
    3-케이스 시나리오 분석 (Bullish / Base / Bearish)

    확률 배정 근거:
    1. ATM 스트래들 기반 내재 움직임
    2. ADX 강도 조정
    3. 신호 수 조정

    Args:
        ticker: 종목
        direction: long_call | long_put
        strike: 행사가
        expiry: 만기
        current_stock_price: 현재 주가
        current_premium: 현재 옵션 프리미엄
        delta: 옵션 델타
        theta: 일일 세타 (음수)
        iv: IV
        atm_straddle_price: ATM 스트래들 가격
        adx: ADX 수치
        signal_count: 추세+자금유입 신호 합계 (0~8)
        total_capital: 총 자본 오버라이드 (None이면 cfg.TOTAL_CAPITAL)
        max_per_position: 포지션당 최대 투자 오버라이드 (None이면 cfg.MAX_PER_POSITION)
        commission_per_contract: 계약당 수수료 오버라이드 (None이면 cfg.COMMISSION_PER_CONTRACT)

    Returns:
        Scenario (확률 합 = 1.0 보장)
    """
    dte = max(1, (expiry - date.today()).days)
    is_long = direction == "long_call"

    # ── 내재 움직임 계산 ───────────────────────────────────
    implied_move_pct = (atm_straddle_price / current_stock_price * 100
                        if current_stock_price > 0 and atm_straddle_price > 0
                        else iv * math.sqrt(dte / 365) * 100)

    # ── 기본 확률 배정 ─────────────────────────────────────
    # 기본값: 강세 30% / 기본 40% / 약세 30%
    base_bull = st.SCENARIO_BASE_BULL_PROB
    base_base = st.SCENARIO_BASE_BASE_PROB
    base_bear = st.SCENARIO_BASE_BEAR_PROB

    # ADX 조정: 강할수록 추세 방향 확률 +5~10%
    # ※ ADX는 강도만 측정, 방향은 DI+/DI-로 별도 판단
    if adx >= st.SCENARIO_ADX_STRONG_THRESHOLD:
        adx_adj = st.SCENARIO_ADX_STRONG_ADJ
    elif adx >= st.SCENARIO_ADX_MED_THRESHOLD:
        adx_adj = st.SCENARIO_ADX_MED_ADJ
    else:
        adx_adj = st.SCENARIO_ADX_WEAK_ADJ

    # 신호 수 조정: 8개 만점 기준
    signal_adj = (signal_count - st.SCENARIO_SIGNAL_CENTER) * st.SCENARIO_SIGNAL_ADJ_PER_SIGNAL

    if is_long:
        bull_prob = max(st.SCENARIO_BULL_PROB_MIN, min(st.SCENARIO_BULL_PROB_MAX, base_bull + adx_adj + signal_adj))
        bear_prob = max(st.SCENARIO_BEAR_PROB_MIN, min(st.SCENARIO_BEAR_PROB_MAX, base_bear - adx_adj - signal_adj))
    else:
        bull_prob = max(st.SCENARIO_BULL_PROB_MIN, min(st.SCENARIO_BEAR_PROB_MAX, base_bull - adx_adj - signal_adj))
        bear_prob = max(st.SCENARIO_BEAR_PROB_MIN, min(st.SCENARIO_BULL_PROB_MAX, base_bear + adx_adj + signal_adj))

    # ── DI 방향 조정 ─────────────────────────────────────────
    # ADX 강도 부스트는 방향 무관 — DI-/DI+로 실제 추세 방향 반영
    # DI bearish (DI- > DI+ × 1.05): long_call이면 bull -10%, bear +10%
    # DI bullish (DI+ > DI- × 1.05): long_put이면 bear -10%, bull +10%
    if di_plus > 0 and di_minus > 0:
        _di_bearish = di_minus > di_plus * 1.05
        _di_bullish = di_plus > di_minus * 1.05
        if is_long and _di_bearish:
            bull_prob -= 0.10
            bear_prob += 0.10
        elif not is_long and _di_bullish:
            bear_prob -= 0.10
            bull_prob += 0.10

    # ── 레짐(Macro) 조정 ─────────────────────────────────────
    # unfavorable (macro_score < 30): long_call bull -8%, bear +8%
    # favorable (macro_score > 70): long_call bull +5%, bear -5%
    if macro_score < 30:
        if is_long:
            bull_prob -= 0.08
            bear_prob += 0.08
        else:
            bear_prob -= 0.08
            bull_prob += 0.08
    elif macro_score > 70:
        if is_long:
            bull_prob += 0.05
            bear_prob -= 0.05
        else:
            bear_prob += 0.05
            bull_prob -= 0.05

    # 범위 클램프 (각 확률 최소 PROB_MIN 보장)
    bull_prob = max(st.SCENARIO_BULL_PROB_MIN, min(st.SCENARIO_BULL_PROB_MAX, bull_prob))
    bear_prob = max(st.SCENARIO_BEAR_PROB_MIN, min(st.SCENARIO_BEAR_PROB_MAX, bear_prob))

    base_prob = max(0.05, 1.0 - bull_prob - bear_prob)

    # 정규화 (합 = 1.0 보장)
    total = bull_prob + base_prob + bear_prob
    bull_prob /= total
    base_prob /= total
    bear_prob /= total

    # ── 시나리오별 주가 계산 ───────────────────────────────
    move_pct = implied_move_pct / 100.0
    bull_price = current_stock_price * (1 + move_pct * st.SCENARIO_BULL_PRICE_MULT)
    # Finviz 목표주가가 제공된 경우 long_call bull case를 오버라이드 (더 높을 때만)
    if bull_target_price is not None and is_long and bull_target_price > bull_price:
        bull_price = bull_target_price
    base_price = current_stock_price * (1 + move_pct * st.SCENARIO_BASE_PRICE_MULT if is_long else 1 - move_pct * st.SCENARIO_BASE_PRICE_MULT)
    bear_price = current_stock_price * (1 - move_pct * st.SCENARIO_BEAR_PRICE_MULT)

    # ── 옵션 가치 추정 (델타 기반) ────────────────────────
    def estimate_option_value(target_price: float, iv_change: float = 0.0) -> float:
        """델타 기반 옵션 가치 추정 + IV 변화 반영"""
        stock_move = target_price - current_stock_price
        delta_pnl = delta * stock_move
        # 세타 비용 (5일 기준)
        theta_cost = theta * 5
        # 베가 효과 (IV 변화 × 베가 추정)
        vega_est = current_premium * 0.1  # 간단 추정
        vega_pnl = vega_est * iv_change
        return max(0.0, current_premium + delta_pnl + theta_cost + vega_pnl)

    # ── 계약 수 계산 ────────────────────────────────────────
    # 원칙: MAX_PER_POSITION($1,000) 내에서 최대 계약 수
    # 예외: 1계약 비용이 MAX_PER_POSITION 초과 시에도 TOTAL_CAPITAL($3,000) 이하면
    #       자금이 있으므로 1계약 허용 (고가 종목 AMD/NVDA 등 대응)
    #       단, TOTAL_CAPITAL 초과 시에는 0계약 (진입 불가)
    _total_cap  = total_capital          if total_capital          is not None else cfg.TOTAL_CAPITAL
    _max_pos    = max_per_position       if max_per_position       is not None else cfg.MAX_PER_POSITION
    _commission = commission_per_contract if commission_per_contract is not None else cfg.COMMISSION_PER_CONTRACT

    commission = _commission
    cost_per_contract = current_premium * 100  # 1계약당 프리미엄 비용
    min_cost = cost_per_contract + commission  # 최소 1계약 총비용

    if current_premium <= 0:
        contracts = 0
    elif min_cost > _total_cap:
        # TOTAL_CAPITAL 설정값 초과 → 최소 1계약 허용 (실제 자본이 더 많을 수 있음)
        contracts = 1
    else:
        # MAX_PER_POSITION 내 최대 계약 수 (최소 1계약 보장)
        contracts = max(1, int((_max_pos - commission) / cost_per_contract))
        # 계약 수 × 비용이 TOTAL_CAPITAL 초과 방지
        while contracts > 1 and (cost_per_contract * contracts + commission * contracts) > _total_cap:
            contracts -= 1

    commission_total = contracts * commission
    actual_investment = cost_per_contract * contracts + commission_total

    def make_case(
        name: str,
        probability: float,
        target_price: float,
        iv_assumption: str,
        iv_change: float,
    ) -> ScenarioCase:
        opt_val = estimate_option_value(target_price, iv_change)
        gross = (opt_val - current_premium) * 100 * contracts
        net = gross - commission_total
        return ScenarioCase(
            name=name,  # type: ignore
            probability=probability,
            stock_move_pct=(target_price - current_stock_price) / current_stock_price * 100,
            target_stock_price=target_price,
            iv_change_assumption=iv_assumption,
            expected_option_value=opt_val,
            gross_profit=gross,
            net_profit=net,
        )

    bull_case = make_case("bullish", bull_prob, bull_price, "IV 유지", st.SCENARIO_BULL_IV_CHANGE)
    base_case = make_case("base", base_prob, base_price, "IV 소폭 압축", st.SCENARIO_BASE_IV_CHANGE)
    bear_case = make_case("bearish", bear_prob, bear_price, "IV 붕괴", st.SCENARIO_BEAR_IV_CHANGE)

    # 기대값
    ev = (
        bull_case.net_profit * bull_prob
        + base_case.net_profit * base_prob
        + bear_case.net_profit * bear_prob
    )

    stop_loss_premium = current_premium * st.SCENARIO_STOP_LOSS_RATIO    # -50% → 손절
    target_1st = current_premium * st.SCENARIO_TARGET_1ST_RATIO          # +50% → 1차 익절
    target_2nd = current_premium * st.SCENARIO_TARGET_2ND_RATIO          # +100% → 2차 익절
    target_3rd = current_premium * st.SCENARIO_TARGET_3RD_RATIO          # +150% → 3차 익절

    # Delta Gap 리스크 경고
    delta_gap = []
    if delta < st.SCENARIO_DELTA_WARN_LOW:
        delta_gap.append(f"낮은 델타({delta:.2f}) — 주가 상승이 프리미엄에 비례하지 않을 수 있음")
    if abs(theta) > current_premium * st.SCENARIO_THETA_WARN_RATIO:
        delta_gap.append(f"세타 비용 과다 ({theta:.3f}/일) — 방향 없이 보유 시 빠른 가치 소멸")
    if iv > st.SCENARIO_IV_WARN_HIGH:
        delta_gap.append(f"고IV({iv:.0%}) — IV 붕괴 시 베가 손실 주의")

    result = Scenario(
        ticker=ticker,
        direction=direction,  # type: ignore
        strike=strike,
        expiry=expiry,
        contracts=contracts,
        total_investment=actual_investment,
        commission_total=commission_total,
        implied_move_pct=implied_move_pct,
        bullish=bull_case,
        base=base_case,
        bearish=bear_case,
        expected_value=ev,
        stop_loss_premium=stop_loss_premium,
        target_premium_1st=target_1st,
        target_premium_2nd=target_2nd,
        target_premium_3rd=target_3rd,
        trailing_stop_pct=cfg.TRAILING_STOP_PCT,
        delta_gap_risk="; ".join(delta_gap) if delta_gap else "해당 없음",
    )

    log.info(
        "scenario_calculated",
        ticker=ticker, ev=round(ev, 2),
        bull=bull_prob, base=base_prob, bear=bear_prob,
    )
    return result


# ─────────────────────────────────────────────────────────────
# 6. 포트폴리오 노출 점검 (Step 9)
# ─────────────────────────────────────────────────────────────

def check_portfolio_exposure(
    scenarios: dict[str, Scenario],
    summary: "SummaryData | None" = None,
    existing_positions_investment: float = 0.0,
    option_validity: dict[str, OptionValidity] | None = None,
) -> PortfolioExposure:
    """
    포트폴리오 전체 델타/세타/베가 합산 및 집중 리스크 점검

    Args:
        scenarios: {ticker: Scenario}
        summary: SummaryData (섹터 정보용, None이면 섹터 집중도 체크 생략)
        existing_positions_investment: 기존 포지션 투자금
        option_validity: {ticker: OptionValidity} — Greeks 집계용 (없으면 0으로 남음)

    Returns:
        PortfolioExposure
    """
    total_delta = 0.0
    total_theta = 0.0
    total_vega = 0.0
    total_invested = existing_positions_investment
    sector_counts: dict[str, int] = {}
    direction_counts = {"long": 0, "short": 0}
    warnings: list[str] = []

    for ticker, scenario in scenarios.items():
        total_invested += scenario.total_investment

        # Greeks 집계 (option_validity 제공 시 실제 합산)
        if option_validity:
            validity = option_validity.get(ticker)
            if validity and scenario.contracts > 0:
                # 롱콜 = 양의 델타, 롱풋 = 음의 델타
                sign = 1.0 if scenario.direction == "long_call" else -1.0
                total_delta += validity.greeks.delta * 100 * scenario.contracts * sign
                total_theta += validity.greeks.theta * 100 * scenario.contracts
                total_vega  += validity.greeks.vega  * 100 * scenario.contracts

        # 섹터 집중도 (summary_data 기반)
        if summary and ticker in summary.tickers:
            sector = summary.tickers[ticker].sector or ""
            if sector:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1

        # 방향 편향
        if scenario.direction == "long_call":
            direction_counts["long"] += 1
        else:
            direction_counts["short"] += 1

    # 경고 생성 — 섹터·방향 편향
    for sector, count in sector_counts.items():
        if count > cfg.SECTOR_MAX_COUNT:
            warnings.append(f"섹터 집중 과다: {sector} {count}개 — RVOL 하위 제거 권고")

    total = sum(direction_counts.values())
    if total > 0:
        long_ratio = direction_counts["long"] / total
        if long_ratio > st.PORTFOLIO_LONG_RATIO_MAX:
            direction_bias = "long"
            warnings.append(f"포트폴리오 롱 편향 ({st.PORTFOLIO_LONG_RATIO_MAX:.0%} 이상) — 하락 리스크 무방비")
        elif long_ratio < st.PORTFOLIO_LONG_RATIO_MIN:
            direction_bias = "short"
            warnings.append(f"포트폴리오 숏 편향 ({st.PORTFOLIO_LONG_RATIO_MIN:.0%} 이하) — 상승 리스크 무방비")
        else:
            direction_bias = "neutral"
    else:
        direction_bias = "neutral"

    remaining_cash = max(0.0, cfg.TOTAL_CAPITAL - total_invested)

    # Greeks 기반 경고 (집계된 경우만)
    if option_validity:
        if abs(total_delta) > cfg.TOTAL_CAPITAL * st.PORTFOLIO_DELTA_CAPITAL_PCT:
            warnings.append(
                f"포트 델타 과다 (Δ{total_delta:+.1f}) — 방향성 리스크 집중"
            )
        if abs(total_theta) > abs(st.PORTFOLIO_THETA_WARN):
            warnings.append(
                f"일일 세타 비용 ${abs(total_theta):.1f} — 무방향 보유 시 빠른 손실"
            )

    result = PortfolioExposure(
        total_delta=total_delta,
        total_theta=total_theta,
        total_vega=total_vega,
        total_invested=total_invested,
        remaining_cash=remaining_cash,
        sector_counts=sector_counts,
        direction_bias=direction_bias,  # type: ignore
        concentration_warning=any("집중" in w for w in warnings),
        correlation_warning=False,
        warnings=warnings,
    )

    log.info("portfolio_checked", invested=total_invested, cash=remaining_cash, warnings=len(warnings))
    return result


# ─────────────────────────────────────────────────────────────
# 7. 확신도 점수 산출
# ─────────────────────────────────────────────────────────────

def calculate_confidence(
    technical: TechnicalScore,
    scenario: Scenario,
    option_valid: OptionValidity,
    timing_conditions_met: int = 2,
    regime_confidence: float = 0.67,  # MarketRegime.regime_confidence (0~1)
    sentiment: dict | None = None,    # Step 5 LLM 감성 분석 결과 (있으면 news_confidence에 반영)
    sentiment_reversal: bool = False,  # 포물선 극단 구간 → 긍정 감성을 역발상(약세)으로 재해석
) -> ConfidenceScore:
    """
    최종 확신도 점수 계산

    Args:
        technical: TechnicalScore
        scenario: Scenario
        option_valid: OptionValidity
        timing_conditions_met: 타이밍 충족 조건 수 (0~4)
        regime_confidence: MarketRegime.regime_confidence (0~1). 레짐 판정 확신도.
        sentiment: Step 5 LLM 감성 결과 dict (없으면 final_score 기반 근사).

    Returns:
        ConfidenceScore
    """
    total_signals = technical.signal_count + timing_conditions_met

    # R/R 비율: 기본 케이스 순수익 / 약세 케이스 손실
    bear_loss = abs(scenario.bearish.net_profit)
    rr_ratio = scenario.base.net_profit / bear_loss if bear_loss > 0 else 0.0

    ivr = option_valid.greeks.ivr

    # ── 스펙 §6.2: 4개 확신도 구성 요소 (각 0.0~1.0) ──────
    # trend_confidence: 신호수 기반 + regime_confidence 조정
    # 실질 최대 신호수 = 7 (추세4 + 자금유입3: rvol·obv·option_flow)
    # darkpool_ok는 데이터 소스 없어 상시 False → 분모 7 사용
    signal_ratio = min(1.0, technical.signal_count / st.CONVICTION_MAX_SIGNALS)
    trend_confidence = signal_ratio * (st.CONVICTION_TREND_BASE + regime_confidence * st.CONVICTION_TREND_REGIME_MULT)

    # news_confidence: LLM 감성(sentiment) + 기술점수 2-way 혼합
    # K-Score는 QMP 시총 순위이므로 뉴스 품질 판단에서 제외
    tech_news_base = min(1.0, technical.final_score / 100.0) * 0.5 + 0.25

    if sentiment:
        overall   = sentiment.get("overall_sentiment", "MIXED")
        conf_str  = sentiment.get("confidence", "Low")
        strength  = sentiment.get("sentiment_strength", "Moderate")
        is_long_dir = technical.direction == "long_call"
        bullish_set = {"BULLISH", "VERY_BULLISH"}
        bearish_set = {"BEARISH", "VERY_BEARISH"}
        if is_long_dir:
            base_sent = (st.CONVICTION_NEWS_BULLISH_BASE if overall in bullish_set
                         else st.CONVICTION_NEWS_BEARISH_BASE if overall in bearish_set
                         else st.CONVICTION_NEWS_MIXED_BASE)
        else:
            base_sent = (st.CONVICTION_NEWS_BULLISH_BASE if overall in bearish_set
                         else st.CONVICTION_NEWS_BEARISH_BASE if overall in bullish_set
                         else st.CONVICTION_NEWS_MIXED_BASE)
        # 포물선 극단 구간: 극단 긍정 감성을 역발상(약세) 신호로 재해석.
        # "살 사람이 다 샀다" → 나쁜 뉴스 하나에 팔 사람만 남은 상태.
        if sentiment_reversal and is_long_dir and overall in bullish_set:
            base_sent = st.CONVICTION_NEWS_BEARISH_BASE
            if conf_str == "High" or strength in ("Strong", "Very Strong"):
                base_sent = max(0.0, base_sent + st.CONVICTION_NEWS_CONFIDENCE_PENALTY)
        elif conf_str == "High" or strength in ("Strong", "Very Strong"):
            base_sent = min(1.0, base_sent + st.CONVICTION_NEWS_CONFIDENCE_BONUS)
        elif conf_str == "Low" or strength in ("Weak", "Very Weak"):
            base_sent = max(0.0, base_sent + st.CONVICTION_NEWS_CONFIDENCE_PENALTY)
        sentiment_weight = st.CONVICTION_SENTIMENT_WEIGHT
        tech_weight      = 1.0 - sentiment_weight
        news_confidence  = base_sent * sentiment_weight + tech_news_base * tech_weight
    else:
        news_confidence = tech_news_base

    thesis_confidence = min(1.0, max(0.0, rr_ratio / st.CONVICTION_RR_NORMALIZATION))

    if option_valid.is_valid and ivr <= st.CONVICTION_EXECUTION_LOW_IVR:
        execution_confidence = st.CONVICTION_EXECUTION_HIGH_SCORE
    elif option_valid.is_valid and ivr <= st.CONVICTION_EXECUTION_MED_IVR:
        execution_confidence = st.CONVICTION_EXECUTION_MED_SCORE
    elif option_valid.is_valid:
        execution_confidence = st.CONVICTION_EXECUTION_LOW_SCORE
    else:
        execution_confidence = 0.0

    total_conviction = (
        trend_confidence * st.CONVICTION_WEIGHT_TREND
        + news_confidence * st.CONVICTION_WEIGHT_NEWS
        + thesis_confidence * st.CONVICTION_WEIGHT_THESIS
        + execution_confidence * st.CONVICTION_WEIGHT_EXECUTION
    )

    if total_conviction >= st.CONVICTION_HIGH_THRESHOLD:
        level = "high"
    elif total_conviction >= st.CONVICTION_MEDIUM_THRESHOLD:
        level = "medium"
    else:
        level = "low"

    return ConfidenceScore(
        technical_signals=technical.signal_count,
        timing_conditions=timing_conditions_met,
        total_signals=total_signals,
        rr_ratio=round(rr_ratio, 2),
        ivr=ivr,
        level=level,  # type: ignore
        trend_confidence=round(trend_confidence, 3),
        news_confidence=round(news_confidence, 3),
        thesis_confidence=round(thesis_confidence, 3),
        execution_confidence=round(execution_confidence, 3),
        total_conviction=round(total_conviction, 3),
    )


# ─────────────────────────────────────────────────────────────
# 7.5 Reversal Risk Engine (RRE) — 고점 회피 엔진
#   매수 파이프라인과 독립적으로 "이 종목이 얼마나 위험한 고점인가?"를 평가.
#   설계: docs/reversal_risk_engine_design.md / 검증: scripts/validate_rre_thresholds.py
# ─────────────────────────────────────────────────────────────

def is_parabolic_top(weekly_rsi: float | None, weekly_adx: float | None) -> bool:
    """F8 포물선 경고 — 주봉 RSI/ADX 동시 극단 과열(블로우오프 탑) 여부.

    Hard stop 아님 — 호출부에서 F8_PARABOLIC_TOP 코드만 기록(종목 유지)하고
    DA에서 차감한다.
    """
    if weekly_rsi is None or weekly_adx is None:
        return False
    return (weekly_rsi > st.RRE_F8_WEEKLY_RSI_MIN
            and weekly_adx > st.RRE_F8_WEEKLY_ADX_MIN)


def is_max_pain_plausible(price: float | None, max_pain: float | None) -> bool:
    """Max Pain이 주가의 1/2~2배 범위 안인지 (밖이면 데이터 불량으로 신호 무시).

    summary의 만기별 Max Pain이 깨진 값(예: $281 종목에 $40)일 때 오신호 방지.
    """
    if not price or not max_pain or price <= 0 or max_pain <= 0:
        return False
    r = st.RRE_MAX_PAIN_PLAUSIBLE_RATIO
    return (1.0 / r) <= (price / max_pain) <= r


def calculate_reversal_risk_score(
    fv: Any,                                # StockDetail (없으면 None)
    opt: Any,                               # TickerOptions (없으면 None)
    insider_net_sell_usd: float | None = None,  # 내부자 순매도 달러 (양수=순매도)
    return_1m_pct: float | None = None,         # kavout 1개월 수익률 (퍼센트)
    next_catalyst_days: int | None = None,      # 다음 실적까지 일수 (없으면 None=공백)
) -> tuple[int, list[str]]:
    """Reversal Risk Engine — 7개 차원 독립 평가.

    차원 1/6/7은 validate_rre_thresholds.py에서 튜닝된 로직 그대로(FP 0% 보존),
    차원 2/3/4/5는 설계 문서 기반(옵션·펀더 데이터 한계로 별도 미검증).

    Returns:
        (적색 차원 수, 적색 차원 이름 리스트)
    """
    red: list[str] = []

    def _g(name: str) -> Any:
        return getattr(fv, name, None) if fv is not None else None

    weekly_rsi   = _g("weekly_rsi")
    weekly_adx   = _g("weekly_adx")
    weekly_di_p  = _g("weekly_di_plus")
    weekly_di_n  = _g("weekly_di_minus")
    sma20_pct    = _g("sma20_pct")
    macd_hist_d  = _g("macd_hist")            # 일봉 MACD 히스토그램 (검증 로직)
    rel_volume   = _g("rel_volume")
    gaap_growth  = _g("net_income_growth_yoy")
    dcf_cagr     = _g("implied_eps_growth_pct")
    current_px   = _g("price")

    pc_ratio = getattr(opt, "pc_ratio", None) if opt is not None else None
    max_pain = getattr(opt, "max_pain_near", None) if opt is not None else None

    # ── 차원 1: 기술적 소진 (필수 RSI + 보조 2개 이상) ──────────────
    cond_rsi = weekly_rsi is not None and weekly_rsi > st.RRE_F8_WEEKLY_RSI_MIN
    aux = 0
    if weekly_adx is not None and weekly_adx > st.RRE_F8_WEEKLY_ADX_MIN:
        aux += 1
    if sma20_pct is not None and sma20_pct > st.RRE_D1_SMA20_GAP_MIN:
        aux += 1
    if return_1m_pct is not None and return_1m_pct > st.RRE_D1_RETURN_1M_MIN:
        aux += 1
    if cond_rsi and aux >= 2:
        red.append("기술적소진")

    # ── 차원 2: 옵션 시장 구조 (Max Pain 괴리 AND P/C 극단) ─────────
    # Max Pain이 주가의 1/2~2배 밖이면 데이터 불량 → 신호 무시 (오신호 방지)
    gap_red = (is_max_pain_plausible(current_px, max_pain)
               and (current_px - max_pain) / max_pain > st.RRE_D2_MAXPAIN_GAP_MIN)
    pc_red = pc_ratio is not None and 0 < pc_ratio < st.RRE_D2_PC_MAX
    if gap_red and pc_red:
        red.append("옵션구조")

    # ── 차원 3: 스마트머니 이탈 (내부자 대규모 순매도) ──────────────
    if insider_net_sell_usd is not None and insider_net_sell_usd > st.RRE_D3_INSIDER_SELL_MIN:
        red.append("스마트머니이탈")

    # ── 차원 4: 촉매 소진 (다음 촉매 공백 AND 최근 급등) ────────────
    catalyst_gap = next_catalyst_days is None or next_catalyst_days > st.RRE_D4_CATALYST_GAP_DAYS
    if catalyst_gap and return_1m_pct is not None and return_1m_pct > st.RRE_D4_RETURN_1M_MIN:
        red.append("촉매소진")

    # ── 차원 5: 펀더멘털 괴리 (GAAP 악화 AND DCF 거품) ──────────────
    gaap_bad = gaap_growth is not None and gaap_growth < st.RRE_D5_GAAP_DECLINE_MAX
    dcf_bad = dcf_cagr is not None and dcf_cagr > st.RRE_D5_DCF_CAGR_BUBBLE
    if gaap_bad and dcf_bad:
        red.append("펀더멘털괴리")

    # ── 차원 6: 수급 군집 (검증됨: RVOL<1.2 AND 1M>+30%) ────────────
    rvol_red = rel_volume is not None and rel_volume < st.RRE_D6_RVOL_MAX
    ret_red = return_1m_pct is not None and return_1m_pct > st.RRE_D6_RETURN_1M_MIN
    if rvol_red and ret_red:
        red.append("수급군집")

    # ── 차원 7: 멀티TF 역배열 (검증됨: 주봉상승 AND 일봉 MACD<0) ─────
    weekly_bullish = (weekly_di_p is not None and weekly_di_n is not None
                      and weekly_adx is not None
                      and weekly_di_p > weekly_di_n
                      and weekly_adx > st.RRE_D7_WEEKLY_ADX_MIN)
    if weekly_bullish and macd_hist_d is not None and macd_hist_d < 0:
        red.append("멀티TF역배열")

    return len(red), red


# ─────────────────────────────────────────────────────────────
# 8. 종목 필터링 (Step 3)
# ─────────────────────────────────────────────────────────────

def apply_filters(
    summary: SummaryData,
    earnings_tickers: list[str],
    target_tickers: list[str] | None = None,
    skip_f1_f3: bool = False,
) -> tuple[list[str], dict[str, list[str]], dict[str, str]]:
    """
    필터 적용 (섹션 9.2 기준)
    F1: RVOL / F2: OI / F3: 가격·시총 / F5: 실적 근접 / F6: 섹터 집중도 / F7: 중복
    F3/F4는 summary technical 데이터 기반 (finviz 제거)

    Args:
        summary: SummaryData (RVOL, 가격, 섹터 등)
        earnings_tickers: 향후 5일 내 실적 발표 종목
        target_tickers: 명시적 분석 대상 (None이면 전체)

    Returns:
        (통과_종목_리스트, {탈락_종목: [탈락_코드_리스트]}, {탈락_종목: "수치 근거 문자열"})
    """
    passed: list[str] = []
    failures: dict[str, list[str]] = {}
    filter_details: dict[str, str] = {}   # 수치 근거 상세
    sector_counts: dict[str, int] = {}

    # 기준 종목 목록: target_tickers 지정 시 해당 목록,
    # 미지정 시 summary.tickers (step_1에서 watchlist로 설정됨)
    tickers_to_check: list[str] = (
        list(target_tickers) if target_tickers
        else list(summary.tickers.keys())
    )

    for ticker in tickers_to_check:
        codes: list[str] = []
        detail_parts: list[str] = []
        ticker_data = summary.tickers.get(ticker)
        opt_data = summary.options.get(ticker)

        # F1: RVOL (skip_f1_f3=True 시 Step 4 실시간으로 처리)
        if not skip_f1_f3 and ticker_data is not None:
            rvol = ticker_data.technical.avg_volume_ratio
            if rvol < cfg.RVOL_MIN:
                codes.append("F1_RVOL_LOW")
                detail_parts.append(f"RVOL {rvol:.2f} < {cfg.RVOL_MIN} 기준")

        # F2: OI — Summary OI는 생성 시각 데이터 소스 문제로 신뢰 불가.
        #          Step 7 option_validity(yfinance 실시간 체인)가 OI 유효성을 담당.

        # F3: 가격 (skip_f1_f3=True 시 Step 4 실시간으로 처리)
        if ticker_data is not None:
            price = ticker_data.technical.price or 0.0
            if not skip_f1_f3 and price > 0 and price < cfg.PRICE_TRADE_MIN:
                codes.append("F3_LIQUIDITY_LOW")
                detail_parts.append(f"주가 ${price:.2f} < ${cfg.PRICE_TRADE_MIN} 기준")
            # F4: 주가 $5 미만 (상장폐지 위험) — 항상 적용
            if price > 0 and price < cfg.PRICE_MIN:
                codes.append("F4_DELISTING_RISK")
                detail_parts.append(f"주가 ${price:.2f} < ${cfg.PRICE_MIN} (상장폐지 위험)")

        # F6: 섹터 집중도 (summary sector 기반)
        if ticker_data is not None:
            sector = ticker_data.sector or ""
            if sector:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
                if sector_counts[sector] > cfg.SECTOR_MAX_COUNT:
                    codes.append("F6_SECTOR_CONCENTRATION")
                    detail_parts.append(
                        f"{sector} 섹터 {sector_counts[sector]}개 > {cfg.SECTOR_MAX_COUNT}개 한도"
                    )

        # F5: 향후 5거래일 내 실적
        if ticker in earnings_tickers:
            codes.append("F5_EARNINGS_PROXIMITY")
            detail_parts.append("실적 발표 5거래일 이내")

        # F7: 중복
        if ticker in passed:
            codes.append("F7_DUPLICATE")
            detail_parts.append("중복 티커")

        if codes:
            failures[ticker] = codes
            if detail_parts:
                filter_details[ticker] = " | ".join(detail_parts)
        else:
            passed.append(ticker)

    log.info(
        "filters_applied",
        total=len(tickers_to_check),
        passed=len(passed),
        failed=len(failures),
    )
    return passed, failures, filter_details
