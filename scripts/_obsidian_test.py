import asyncio, sys, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))

async def test():
    from datetime import date, timedelta
    from core.obsidian import ObsidianClient
    from shared.schemas import (
        FinalRanking, ConfidenceScore, OptionValidity, Scenario,
        ScenarioCase, Greeks
    )
    today = date.today()
    expiry = today + timedelta(days=60)

    conviction = ConfidenceScore(
        total_conviction=0.72, level="high",
        trend_confidence=0.70, news_confidence=0.65,
        thesis_confidence=0.80, execution_confidence=0.70,
        technical_signals=6, rr_ratio=1.5
    )
    greeks = Greeks(delta=0.53, theta=-0.08, vega=0.15, gamma=0.003, iv=0.45, ivr=42.0)
    ov = OptionValidity(
        ticker="TESTX", direction="long_call", strike=320.0, expiry=expiry,
        is_valid=True, delta_ok=True, ivr_ok=True, oi_ok=True,
        spread_ok=True, dte_ok=True, ivr_warning=False, oi_warning=False,
        greeks=greeks, mid_price=15.50, exclusion_reason=""
    )
    bull = ScenarioCase(name="bullish", probability=0.4, stock_move_pct=6.0,
        target_stock_price=340.0, iv_change_assumption="IV 유지",
        expected_option_value=25.0, gross_profit=970.0, net_profit=950.0)
    base = ScenarioCase(name="base", probability=0.4, stock_move_pct=1.5,
        target_stock_price=325.0, iv_change_assumption="IV 소폭 감소",
        expected_option_value=18.0, gross_profit=260.0, net_profit=250.0)
    bear = ScenarioCase(name="bearish", probability=0.2, stock_move_pct=-5.0,
        target_stock_price=290.0, iv_change_assumption="IV 급락",
        expected_option_value=3.0, gross_profit=-1245.0, net_profit=-1250.0)
    scenario = Scenario(
        ticker="TESTX", direction="long_call", strike=320.0, expiry=expiry,
        contracts=1, total_investment=1550.0, expected_value=320.0,
        bullish=bull, base=base, bearish=bear,
        stop_loss_premium=7.75, target_premium_1st=23.25,
        target_premium_2nd=31.0, target_premium_3rd=38.75
    )
    ranking = FinalRanking(
        rank=1, ticker="TESTX", direction="long_call", action="진입",
        final_score=72.0, conviction=conviction, capital_allocation=1000.0,
        contracts=1, strike=320.0, expiry=expiry,
        rationale="테스트 판단", risk_factors=[], scenario=scenario
    )

    opt_analytics = {
        "TESTX": {
            "implied_move_pct": 4.25,
            "max_pain": 315.0,
            "pc_ratio": 0.62,
            "oi_change_signal": True,
        }
    }

    client = ObsidianClient()
    note_path = await client.save_buy_note(
        execution_id="test_oi_verify",
        rankings=[ranking],
        regime_status="favorable",
        filter_failures={},
        options_analytics=opt_analytics,
    )

    from shared.config import get_config
    import httpx
    cfg = get_config()
    async with httpx.AsyncClient(verify=False) as c:
        r = await c.get(
            f"{cfg.OBSIDIAN_BASE_URL.rstrip('/')}/vault/{note_path}",
            headers={"Authorization": f"Bearer {cfg.OBSIDIAN_API_KEY}",
                     "accept": "application/vnd.olrapi.note+json"}
        )
        content = r.json().get("content", "")

    print(f"노트 경로: {note_path}, 길이: {len(content)} chars")
    checks = {
        "Implied Move (내재 이동폭)": "Implied Move",
        "Max Pain":                   "Max Pain",
        "P/C Ratio":                  "P/C Ratio",
        "OI 변화 신호":               "OI 변화 신호",
        "방향 일치 OI":               "방향 일치 OI",
    }
    all_pass = True
    for label, kw in checks.items():
        found = kw in content
        if not found: all_pass = False
        print(f"  {'OK' if found else 'FAIL'} {label}")

    if not all_pass:
        # 옵션 섹션 주변 출력
        idx = content.find("5-9")
        if idx > 0:
            print("\n--- 5-9 섹션 주변 ---")
            print(content[idx:idx+600])

    print("결과:", "PASS" if all_pass else "FAIL")

asyncio.run(test())
