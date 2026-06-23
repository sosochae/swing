"""실제 Obsidian 노트 생성 테스트 — 개선사항 5종 전체 반영 확인
save_buy_note 직접 호출로 파이프라인 필터와 무관하게 전체 보고서 생성
"""
import asyncio, sys, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import date, timedelta
from shared.schemas import (
    FinalRanking, ConfidenceScore, OptionValidity, Scenario, ScenarioCase,
    Greeks, TechnicalScore, MarketRegime, RegimeComponent, StockDetail,
)

today  = date.today()
expiry = today + timedelta(days=45)

# ── 레짐: borderline, confidence=0.4 → macro_score = 50+(0.4-0.5)*30 = 47 < 50 ──
regime = MarketRegime(
    regime_status="borderline",
    allowed_direction="long_call",
    trend_strength=RegimeComponent(value="weak",    status="borderline", reason="SPY 20MA 근접"),
    volatility=RegimeComponent(   value="moderate", status="pass",       reason="VIX 19"),
    index_trend=RegimeComponent(  value="mixed",    status="borderline", reason="방향 혼조"),
    risk_factors=["SPY 20MA 아래"],
    trend_confidence=0.5,
    regime_confidence=0.4,   # → macro_score = 47
)

# ── TechnicalScore: signal_count=6 (원래 Good) ──
ts = TechnicalScore(
    ticker="LRCX", direction="long_call",
    ma_alignment="bullish",
    adx_score=18.0, rsi_score=16.0, macd_score=15.0, rvol_score=14.0,
    raw_score=63.0, final_score=63.0,
    trend_confirmed=True, capital_flow_confirmed=True,
    obv_ok=True, option_flow_ok=False, darkpool_ok=False,
    signal_count=6,
)

# ── StockDetail: 과열 조건 + 신규 필드 포함 ──
fv = StockDetail(
    ticker="LRCX", price=364.0,
    rsi14=71.5,       # ≥70 → overheat
    sma20_pct=13.2,   # ≥12 → overheat → Good→Poor
    forward_pe=22.5, trailing_pe=28.0,
    peg=2.43, peg_forward=1.43,
    implied_eps_growth_pct=37.1,
    eps_ttm=13.0, eps_next_5y_pct=20.0,
    fcf_ttm=4200.0, roe_pct=72.0, debt_equity=120.0,
    revenue_growth_yoy=14.5, net_income_growth_yoy=9.2,
    eps_surprise_pct=4.3,
    target_price=420.0, target_price_high=480.0,
    recom=1.8, beta=1.35,
    short_float_pct=1.5, insider_trans_pct=-3.2,
    rel_volume=1.55, change_pct=0.8,
    w52_high_pct=-8.0, w52_low_pct=22.0,
    sma20_val=322.0, sma50_val=310.0, sma200_val=280.0,
)

# ── Greeks / OptionValidity ──
greeks = Greeks(delta=0.45, theta=-0.12, vega=0.22, gamma=0.002, iv=0.35, ivr=38.0)
ov = OptionValidity(
    ticker="LRCX", direction="long_call", strike=370.0, expiry=expiry,
    is_valid=True, delta_ok=True, ivr_ok=True, oi_ok=True,
    spread_ok=True, dte_ok=True, ivr_warning=False, oi_warning=False,
    greeks=greeks, mid_price=18.0, exclusion_reason="",
)

# ── Scenario ──
bull = ScenarioCase(name="bullish", probability=0.35, stock_move_pct=7.0,
    target_stock_price=390.0, iv_change_assumption="IV 유지",
    expected_option_value=32.0, gross_profit=1400.0, net_profit=1382.0)
base = ScenarioCase(name="base", probability=0.40, stock_move_pct=1.5,
    target_stock_price=370.0, iv_change_assumption="IV 소폭 감소",
    expected_option_value=19.5, gross_profit=150.0, net_profit=132.0)
bear = ScenarioCase(name="bearish", probability=0.25, stock_move_pct=-5.0,
    target_stock_price=346.0, iv_change_assumption="IV 급락",
    expected_option_value=4.0, gross_profit=-1400.0, net_profit=-1418.0)
sc = Scenario(
    ticker="LRCX", direction="long_call", strike=370.0, expiry=expiry,
    contracts=1, total_investment=1800.0, expected_value=180.0,
    commission_total=2.0, implied_move_pct=5.1,
    bullish=bull, base=base, bearish=bear,
    stop_loss_premium=9.0, target_premium_1st=27.0,
    target_premium_2nd=36.0, target_premium_3rd=45.0,
)

# ── FinalRanking ──
conviction = ConfidenceScore(
    total_conviction=0.68, level="high",
    trend_confidence=0.70, news_confidence=0.60,
    thesis_confidence=0.75, execution_confidence=0.65,
    technical_signals=6, rr_ratio=0.6,
)
r = FinalRanking(
    rank=1, ticker="LRCX", direction="long_call", action="진입",
    final_score=63.0, conviction=conviction, capital_allocation=3000.0,
    contracts=1, strike=370.0, expiry=expiry,
    rationale="반도체 장비 업황 회복 기대, 기술적 상승 모멘텀",
    risk_factors=["고평가 우려", "반도체 경기 변동"],
    scenario=sc,
)

sent = {
    "overall_sentiment": "POSITIVE", "confidence": "Medium",
    "debate_verdict": "Slight Bull",
    "bull_thesis": "장비 수주 증가, AI 투자 지속",
    "bear_thesis": "메모리 반도체 수요 둔화",
    "technical_narrative": {
        "trend_outlook": "BULLISH",
        "entry_quality": "Good",  # LLM Good → 과열 페널티로 Poor 강등
        "summary": "RSI 71 과매수, SMA20 13% 이격.",
    },
}

opt_analytics = {
    "LRCX": {"implied_move_pct": 5.1, "max_pain": 360.0, "pc_ratio": 0.75, "oi_change_signal": True}
}

async def run():
    from core.obsidian import ObsidianClient
    import httpx

    client = ObsidianClient()
    note_path = await client.save_buy_note(
        execution_id="test_improvements_verify",
        rankings=[r],
        regime_status="borderline",
        filter_failures={},
        technical_scores={"LRCX": ts},
        option_validity={"LRCX": ov},
        scenarios={"LRCX": sc},
        regime=regime,
        sentiment_results={"LRCX": sent},
        finviz_details={"LRCX": fv},
        options_analytics=opt_analytics,
    )
    print(f"노트 저장: {note_path}")

    from shared.config import get_config
    cfg = get_config()
    async with httpx.AsyncClient(verify=False) as c:
        resp = await c.get(
            f"{cfg.OBSIDIAN_BASE_URL.rstrip('/')}/vault/{note_path}",
            headers={"Authorization": f"Bearer {cfg.OBSIDIAN_API_KEY}",
                     "accept": "application/vnd.olrapi.note+json"},
        )
        content = resp.json().get("content", "")

    print(f"노트 길이: {len(content)} chars\n")

    checks = [
        ("#1 진입품질 Poor",           "Poor",                 False),
        ("#1 Good 없음",               "진입 품질: Good",      True),
        ("#2 R/R 프리미엄 기준",       "프리미엄 기준",        False),
        ("#2 시나리오R/R",             "시나리오R/R",          False),
        ("#3 PEG (Trailing)",          "PEG (Trailing)",       False),
        ("#3 PEG (Forward)",           "PEG (Forward)",        False),
        ("#3 peg_forward=1.43",        "1.43",                 False),
        ("#4 레짐 충돌 경고",           "레짐 충돌 경고",       False),
        ("#4 무효화 트리거",            "무효화 트리거",        False),
        ("#5 역산 성장률 내재분석",     "역산 성장률 내재분석", False),
        ("#5 EPS CAGR 37.1%",          "37.1%",                False),
        ("#5 컨센서스 비교",            "컨센서스",             False),
    ]

    all_pass = True
    for label, kw, must_absent in checks:
        found = kw in content
        ok = (not found) if must_absent else found
        icon = "✅" if ok else "❌"
        hint = f"(없어야 함)" if must_absent else ""
        print(f"  {icon} {label}  '{kw}' {hint}")
        if not ok:
            all_pass = False

    print(f"\n{'='*50}")
    print(f"  최종: {'PASS ✅' if all_pass else 'FAIL ❌'}")
    print(f"{'='*50}")

    if not all_pass:
        for label, kw, must_absent in checks:
            found = kw in content
            ok = (not found) if must_absent else found
            if not ok and not must_absent:
                idx = content.find("5-4")
                if idx > 0:
                    print(f"\n[5-4 펀더멘털 섹션 발췌]")
                    print(content[idx:idx+800])
                break

asyncio.run(run())
