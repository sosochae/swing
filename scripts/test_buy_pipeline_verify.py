"""
test_buy_pipeline_verify.py
============================
수정 사항 검증 스크립트 — LLM 호출 없이 Steps 0-4 + 7 실행 후 핵심 데이터 출력.

검증 항목:
  1. RSI / ADX 파싱 (50.0/20.0 기본값 탈출 여부)
  2. 목표주가 — Finnhub 실시간 vs yfinance
  3. VIX / SPY / QQQ / Fear&Greed — 매크로 실시간 갱신
  4. Forward P/E / PEG — Finnhub summary 우선
  5. 옵션 체인 — yfinance 실시간 갱신
"""

import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("core.api_fetcher").setLevel(logging.INFO)

from shared.schemas import PipelinePaths, PipelineContext
from shared.config import get_config
from orchestrator.steps.buy_steps import BuySteps
from core.obsidian import ObsidianClient
from core.slack import SlackClient

cfg = get_config()


async def main():
    # ── Context 초기화 ────────────────────────────────────────────
    eid = f"verify_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    _earnings_dir = Path(cfg.EARNINGS_DIR)
    paths = PipelinePaths(
        summary_dir=Path(cfg.SUMMARY_DIR),
        finviz_file=Path(cfg.FINVIZ_FILE),
        earnings_dir=_earnings_dir,
        k_earnings_analysis=_earnings_dir / "K어닝 분석.md",
        k_earnings_analysis_today=_earnings_dir / "K어닝 분석_today.md",
        k_earnings_call_dir=_earnings_dir / "K어닝콜_output",
        positions_file=Path(cfg.POSITIONS_FILE),
        watchlist_file=Path(cfg.WATCHLIST_FILE),
        data_dir=Path(cfg.DATA_DIR),
    )
    ctx = PipelineContext(
        execution_id=eid,
        pipeline_type="buy",
        start_step=0,
        force_refresh=False,
        target_tickers=None,
        paths=paths,
    )
    steps = BuySteps(
        obsidian=ObsidianClient(),
        slack=SlackClient(),
    )

    # ── Step 0 ───────────────────────────────────────────────────
    sep("STEP 0  환경 초기화")
    await steps.step_0_env(ctx)

    # ── Step 1 ───────────────────────────────────────────────────
    sep("STEP 1  데이터 로드")
    await steps.step_1_data(ctx)

    if not ctx.summary_data:
        print("❌ SUMMARY 파일 로드 실패 — 종료")
        return

    # ── [검증 1] RSI / ADX (장전 파싱) ───────────────────────────
    print("\n[검증 1] SUMMARY 파싱 — RSI / ADX (장전 스냅샷)")
    for tk, ts in list(ctx.summary_data.tickers.items())[:8]:
        rsi = ts.technical.rsi14
        adx = ts.technical.adx14
        flag_rsi = "⚠️ 기본값" if rsi == 50.0 else "✅"
        flag_adx = "⚠️ 기본값" if adx == 20.0 else "✅"
        print(f"  {tk:6s}  RSI={rsi:6.2f} {flag_rsi}  ADX={adx:6.2f} {flag_adx}")

    # ── [검증 2] 매크로 장전 스냅샷 기록 ─────────────────────────
    m = ctx.summary_data.macro
    macro_before = dict(
        vix=m.vix, spy=m.spy, qqq=m.qqq, dxy=m.dxy,
        gold=m.gold, oil_wti=m.oil_wti, yield_10y=m.yield_10y,
        soxx=m.soxx, fear_greed=m.fear_greed,
        fear_greed_label=m.fear_greed_label,
    )
    print(f"\n[검증 2] 매크로 장전 스냅샷")
    print(f"  VIX={m.vix}  SPY={m.spy}  QQQ={m.qqq}  DXY={m.dxy}")
    print(f"  Gold={m.gold}  Oil={m.oil_wti}  10Y={m.yield_10y}")
    print(f"  SOXX={m.soxx}  Fear&Greed={m.fear_greed} ({m.fear_greed_label})")

    # ── [검증 3] 목표주가 (yfinance 기준, Step 4 전) ──────────────
    print("\n[검증 3] 목표주가 Step 4 전 (yfinance / summary 기준)")
    for tk in list(ctx.summary_data.tickers.keys())[:5]:
        val = ctx.summary_data.tickers[tk].valuation
        print(f"  {tk:6s}  fwd_pe={val.forward_pe}  peg={val.peg}")

    # ── Step 2 ───────────────────────────────────────────────────
    sep("STEP 2  레짐")
    await steps.step_2_regime(ctx)
    print(f"  레짐: {ctx.regime.regime_status}  방향: {ctx.regime.allowed_direction}")

    # ── Step 3 ───────────────────────────────────────────────────
    sep("STEP 3  필터")
    await steps.step_3_filter(ctx)
    print(f"  통과: {ctx.filtered_tickers}")

    if not ctx.filtered_tickers:
        # 필터 통과 종목 없으면 상위 3개 강제 설정 (테스트 목적)
        ctx.filtered_tickers = list(ctx.summary_data.tickers.keys())[:3]
        print(f"  ⚠️ 필터 통과 없음 → 테스트용 강제 설정: {ctx.filtered_tickers}")

    # ── Step 4 ───────────────────────────────────────────────────
    sep("STEP 4  기술분석 (yfinance + Finnhub 오버라이드 + 매크로 갱신)")
    await steps.step_4_technical(ctx)

    # ── [검증 4] RSI / ADX after yfinance ─────────────────────────
    print("\n[검증 4] Step 4 후 RSI / ADX / 목표주가 / P/E / PEG")
    print(f"  {'티커':6s}  {'RSI':>7}  {'ADX':>7}  {'목표주가':>10}  {'FwdPE':>7}  {'PEG':>7}")
    print(f"  {'-'*6}  {'-'*7}  {'-'*7}  {'-'*10}  {'-'*7}  {'-'*7}")
    for tk in ctx.filtered_tickers:
        fv = ctx.stock_data.get(tk)
        if not fv:
            print(f"  {tk:6s}  FinvizDetail 없음")
            continue
        rsi_flag = "⚠️" if fv.rsi14 == 50.0 else "  "
        adx_flag = "⚠️" if (fv.adx or 0) < 1.0 else "  "
        print(f"  {rsi_flag}{tk:6s}  {fv.rsi14 or 'N/A':>7}  "
              f"{fv.adx or 'N/A':>7}  "
              f"${fv.target_price or 'N/A':>9}  "
              f"{fv.forward_pe or 'N/A':>7}  "
              f"{fv.peg or 'N/A':>7}")

    # ── [검증 5] 매크로 실시간 갱신 비교 ─────────────────────────
    m2 = ctx.summary_data.macro
    print(f"\n[검증 5] 매크로 실시간 갱신 결과")
    fields = [
        ("vix", "VIX"), ("spy", "SPY"), ("qqq", "QQQ"),
        ("dxy", "DXY"), ("gold", "Gold"), ("oil_wti", "Oil WTI"),
        ("yield_10y", "10Y채권"), ("soxx", "SOXX"),
    ]
    for attr, label in fields:
        before = macro_before.get(attr, 0)
        after = getattr(m2, attr, 0)
        changed = "✅ 갱신" if after != before else "⚠️ 동일"
        print(f"  {label:10s}  {before:>8.2f} → {after:>8.2f}  {changed}")
    # Fear&Greed
    fg_b, fg_a = macro_before['fear_greed'], m2.fear_greed
    fg_lb, fg_la = macro_before['fear_greed_label'], m2.fear_greed_label
    changed = "✅ 갱신" if fg_a != fg_b else "⚠️ 동일"
    print(f"  {'Fear&Greed':10s}  {fg_b:>8} ({fg_lb}) → {fg_a:>8} ({fg_la})  {changed}")

    # ── 옵션 체인 before 기록 ─────────────────────────────────────
    chain_before = {}
    for tk in ctx.filtered_tickers:
        opt = ctx.summary_data.options.get(tk)
        chain_before[tk] = len(opt.chain) if opt else 0

    # ── Step 7 ───────────────────────────────────────────────────
    sep("STEP 7  옵션 체인 실시간 갱신")
    await steps.step_7_options(ctx)

    # ── [검증 6] 옵션 체인 ───────────────────────────────────────
    print("\n[검증 6] 옵션 체인 갱신 결과")
    for tk in ctx.filtered_tickers:
        opt = ctx.summary_data.options.get(tk)
        after = len(opt.chain) if opt else 0
        before = chain_before.get(tk, 0)
        flag = "✅ 갱신됨" if after > 0 else "⚠️ 갱신 실패"
        print(f"  {tk:6s}  {before} → {after}개  {flag}", end="")
        if opt and opt.chain:
            c = opt.chain[0]
            print(f"  [{c.get('option_type')} K={c.get('strike')} "
                  f"DTE={c.get('dte')} IV={c.get('iv')}% OI={c.get('oi')}]")
        else:
            print()

    # ── [검증 7] option_validity ───────────────────────────────────
    print("\n[검증 7] 옵션 유효성 결과")
    for tk, v in ctx.option_validity.items():
        status = "✅ 유효" if v.is_valid else "❌ 탈락"
        print(f"  {tk:6s}  {status}  {v.exclusion_reason or ''}")

    # ── [검증 8] 기술 점수 ────────────────────────────────────────
    print("\n[검증 8] 기술 점수")
    for tk, sc in ctx.technical_scores.items():
        print(f"  {tk:6s}  최종={sc.final_score:.1f}  원점수={sc.raw_score:.1f}"
              f"  ADX={sc.adx_score:.0f}  RSI={sc.rsi_score:.0f}"
              f"  MACD={sc.macd_score:.0f}  RVOL={sc.rvol_score:.0f}"
              f"  추세확인={sc.trend_confirmed}")

    sep("검증 완료")


def sep(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
