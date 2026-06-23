"""개선사항 5종 반영 검증 스크립트
Task #1: 진입 품질 과열 페널티
Task #2: R/R 레이블 명확화
Task #3: PEG Forward 병기
Task #4: 레짐 충돌 경고
Task #5: 역산 성장률 내재분석
"""
import sys, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import date, timedelta
from shared.schemas import (
    FinalRanking, ConfidenceScore, OptionValidity, Scenario,
    ScenarioCase, Greeks, TechnicalScore, MarketRegime, RegimeComponent,
    StockDetail,
)
from core.obsidian import _format_integrated_buy_block

# ── 공통 날짜 ──────────────────────────────────────────────────
today  = date.today()
expiry = today + timedelta(days=45)

# ── MarketRegime: unfavorable → macro_score ≈ 25 (< 50) ───────
regime = MarketRegime(
    regime_status="unfavorable",
    allowed_direction="long_call",
    trend_strength=RegimeComponent(value="weak", status="fail", reason="SPY 200일선 하회"),
    volatility=RegimeComponent(value="high", status="fail", reason="VIX 28"),
    index_trend=RegimeComponent(value="bearish", status="fail", reason="QQQ 하락 추세"),
    risk_factors=["VIX 상승", "SPY 200일선 하회"],
    trend_confidence=0.5,
    regime_confidence=0.5,
)
# _regime_to_score: unfavorable base=25, confidence_adj=0 → 25
macro_score = 25
macro_label = "불리 (Unfavorable)"

# ── TechnicalScore: signal_count=6 (원래 Good) ────────────────
ts = TechnicalScore(
    ticker="LRCX",
    direction="long_call",
    ma_alignment="bullish",
    adx_score=18.0,
    rsi_score=16.0,
    macd_score=15.0,
    rvol_score=14.0,
    raw_score=63.0,
    final_score=63.0,
    trend_confirmed=True,
    capital_flow_confirmed=True,
    obv_ok=True,
    option_flow_ok=False,
    darkpool_ok=False,
    signal_count=6,
)

# ── StockDetail (fv): 과열 조건 + 새 필드 ─────────────────────
fv = StockDetail(
    ticker="LRCX",
    price=830.0,
    rsi14=71.5,        # ≥70 → overheat
    sma20_pct=13.2,    # ≥12 → overheat (둘 다 해당 → Poor)
    forward_pe=22.5,
    trailing_pe=28.0,
    peg=2.43,
    peg_forward=1.43,
    implied_eps_growth_pct=37.1,
    eps_ttm=29.6,
    eps_next_5y_pct=20.0,
    fcf_ttm=4200.0,
    roe_pct=72.0,
    debt_equity=120.0,
    revenue_growth_yoy=14.5,
    net_income_growth_yoy=9.2,
    eps_surprise_pct=4.3,
    target_price=950.0,
    target_price_high=1100.0,
    recom=1.8,
    beta=1.35,
    short_float_pct=1.5,
    insider_trans_pct=-3.2,
    rel_volume=1.1,
    w52_high_pct=-8.0,
    w52_low_pct=22.0,
    sma20_val=734.0,
    sma50_val=720.0,
    sma200_val=710.0,
    change_pct=0.8,
)

# ── Greeks / OptionValidity ────────────────────────────────────
greeks = Greeks(delta=0.45, theta=-0.12, vega=0.22, gamma=0.002, iv=0.35, ivr=38.0)
ov = OptionValidity(
    ticker="LRCX", direction="long_call", strike=840.0, expiry=expiry,
    is_valid=True, delta_ok=True, ivr_ok=True, oi_ok=True,
    spread_ok=True, dte_ok=True, ivr_warning=False, oi_warning=False,
    greeks=greeks, mid_price=18.0, exclusion_reason="",
)

# ── Scenario ──────────────────────────────────────────────────
bull = ScenarioCase(name="bullish", probability=0.35, stock_move_pct=7.0,
    target_stock_price=890.0, iv_change_assumption="IV 유지",
    expected_option_value=32.0, gross_profit=1400.0, net_profit=1382.0)
base = ScenarioCase(name="base", probability=0.40, stock_move_pct=1.5,
    target_stock_price=843.0, iv_change_assumption="IV 소폭 감소",
    expected_option_value=19.5, gross_profit=150.0, net_profit=132.0)
bear = ScenarioCase(name="bearish", probability=0.25, stock_move_pct=-5.0,
    target_stock_price=789.0, iv_change_assumption="IV 급락",
    expected_option_value=4.0, gross_profit=-1400.0, net_profit=-1418.0)
sc = Scenario(
    ticker="LRCX", direction="long_call", strike=840.0, expiry=expiry,
    contracts=1, total_investment=1800.0, expected_value=180.0,
    commission_total=2.0, implied_move_pct=5.1,
    bullish=bull, base=base, bearish=bear,
    stop_loss_premium=9.0, target_premium_1st=27.0,
    target_premium_2nd=36.0, target_premium_3rd=45.0,
)

# ── ConfidenceScore / FinalRanking ────────────────────────────
conviction = ConfidenceScore(
    total_conviction=0.68, level="high",
    trend_confidence=0.70, news_confidence=0.60,
    thesis_confidence=0.75, execution_confidence=0.65,
    technical_signals=6, rr_ratio=0.6,
)
r = FinalRanking(
    rank=1, ticker="LRCX", direction="long_call", action="진입",
    final_score=63.0, conviction=conviction, capital_allocation=3000.0,
    contracts=1, strike=840.0, expiry=expiry,
    rationale="반도체 장비 업황 회복 기대, 기술적 상승 모멘텀",
    risk_factors=["고평가 우려", "반도체 경기 변동"],
    scenario=sc,
)

# ── 센티멘트 mock ──────────────────────────────────────────────
sent = {
    "overall_sentiment": "POSITIVE",
    "confidence": "Medium",
    "debate_verdict": "Slight Bull",
    "bull_thesis": "장비 수주 증가, AI 투자 지속",
    "bear_thesis": "메모리 반도체 수요 둔화",
    "technical_narrative": {
        "trend_outlook": "BULLISH",
        "entry_quality": "Good",   # LLM이 Good 반환 → 과열 페널티로 Poor로 강등되어야 함
        "summary": "RSI 71 과매수, SMA20 13% 이격으로 단기 과열 상태.",
    },
}

# ── opt_analytics mock ─────────────────────────────────────────
opt_analytics = {
    "LRCX": {
        "implied_move_pct": 5.1,
        "max_pain": 820.0,
        "pc_ratio": 0.75,
        "oi_change_signal": True,
    }
}

# ── 실행 ──────────────────────────────────────────────────────
print("=" * 65)
print("  SwingMCP 개선사항 5종 검증")
print("=" * 65)

lines = _format_integrated_buy_block(
    r=r, ts=ts, ov=ov, sc=sc,
    macro_score=macro_score, macro_label=macro_label,
    sent=sent, fv=fv, regime=regime,
    opt_analytics=opt_analytics,
)
content = "\n".join(lines)

# ── 전체 보고서 덤프 ──────────────────────────────────────────
with open("scripts/_report_dump.md", "w", encoding="utf-8") as f:
    f.write(content)

# ── 검증 항목 ─────────────────────────────────────────────────
checks = [
    # Task #1: 과열 페널티 (RSI 71.5 + SMA20 13.2% → Good→Poor)
    ("#1 진입품질 Poor 강등",         "Poor"),
    ("#1 Good이 아님",               "Good",    True),  # True = 없어야 함

    # Task #2: R/R 레이블
    # '시나리오R/R'은 save_buy_note 요약 테이블(obsidian.py:362)에 있어 여기선 체크 불가
    ("#2 R/R 프리미엄 기준",          "프리미엄 기준"),
    ("#2 R/R 비율 (프리미엄 기준)",   "R/R 비율 (프리미엄 기준)"),

    # Task #3: PEG Forward
    ("#3 PEG (Trailing)",            "PEG (Trailing)"),
    ("#3 PEG (Forward)",             "PEG (Forward)"),
    ("#3 peg_forward 값 1.43",       "1.43"),

    # Task #4: 레짐 충돌 경고
    ("#4 레짐 충돌 경고 블록",        "레짐 충돌 경고"),
    ("#4 무효화 트리거",              "무효화 트리거"),

    # Task #5: 역산 성장률
    ("#5 역산 성장률 내재분석",       "역산 성장률 내재분석"),
    ("#5 EPS CAGR 수치 37.1%",       "37.1%"),
    ("#5 컨센서스 비교",             "컨센서스"),
]

print()
all_pass = True
for item in checks:
    label, keyword = item[0], item[1]
    must_absent = len(item) > 2 and item[2] is True
    found = keyword in content
    if must_absent:
        ok = not found
        status = "✅" if ok else "❌"
        print(f"  {status} {label}  ('{keyword}' 없어야 함)")
    else:
        ok = found
        status = "✅" if ok else "❌"
        print(f"  {status} {label}  ('{keyword}')")
    if not ok:
        all_pass = False

print()
print("=" * 65)
print(f"  최종: {'PASS ✅' if all_pass else 'FAIL ❌'}")
print("=" * 65)

# 실패 항목 주변 컨텍스트 출력
if not all_pass:
    print()
    print("[디버그] 실패 항목 컨텍스트:")
    for item in checks:
        label, keyword = item[0], item[1]
        must_absent = len(item) > 2 and item[2] is True
        found = keyword in content
        ok = (not found) if must_absent else found
        if not ok:
            idx = content.find(keyword) if not must_absent else 0
            if idx >= 0:
                snippet = content[max(0, idx-100):idx+200]
            else:
                # 관련 섹션 찾기
                for kw in ["진입 품질", "TYPE 3", "entry_quality", "R/R", "PEG", "레짐"]:
                    idx2 = content.find(kw)
                    if idx2 >= 0:
                        snippet = content[idx2:idx2+300]
                        break
                else:
                    snippet = content[:400]
            print(f"\n  [{label}]")
            print("  " + snippet.replace("\n", "\n  "))
