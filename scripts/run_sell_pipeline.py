"""
scripts/run_sell_pipeline.py
============================
Sell Pipeline 실행 스크립트

실행:
    cd C:\\MCP\\Swing
    .venv\\Scripts\\python scripts\\run_sell_pipeline.py

ObsidianClient + SlackClient 실제 연결.
positions.md에 포지션이 없으면 분석 대상 없음으로 종료.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# ── 프로젝트 루트를 sys.path에 추가 ──────────────────────────
_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))


# ─────────────────────────────────────────────────────────────
# Step별 결과 출력
# ─────────────────────────────────────────────────────────────

def _print_step_result(ctx: Any, step_num: int) -> None:
    if step_num == 0:
        print(f"  summary  : {len(ctx.summary_data.tickers) if ctx.summary_data else 0}종목")
        print(f"  finviz   : {len(ctx.finviz_rows)}행")
        print(f"  earnings : {len(ctx.earnings_list)}건")
        print(f"  detail   : {len(ctx.finviz_detail)}종목")
        print(f"  kavout   : {len(ctx.kavout_data)}종목")
        print(f"  positions: {len(ctx.positions)}개 → {[p.ticker for p in ctx.positions]}")

    elif step_num == 1:
        for ticker, h in ctx.sell_health.items():
            pnl = (h.get("delta_pnl", 0) + h.get("theta_pnl", 0)
                   + h.get("vega_pnl", 0))
            print(f"  {ticker}: DTE긴급도={h.get('dte_urgency')}  "
                  f"P&L≈${pnl:+,.0f}  flags={h.get('flags', [])}")

    elif step_num == 2:
        if ctx.regime:
            print(f"  레짐: {ctx.regime.regime_status}  "
                  f"확신도: {ctx.regime.regime_confidence:.0%}")
        for ticker, flag in ctx.sell_regime_flags.items():
            print(f"  {ticker}: {flag}")

    elif step_num == 3:
        for ticker, score in ctx.technical_scores.items():
            print(f"  {ticker}: score={score.final_score:.1f}  "
                  f"signal={score.signal_count}  "
                  f"trend={'✓' if score.trend_confirmed else '✗'}  "
                  f"flow={'✓' if score.capital_flow_confirmed else '✗'}  "
                  f"ma={score.ma_alignment}  adx={score.adx_score:.0f}  "
                  f"rsi={score.rsi_score:.0f}  macd={score.macd_score:.0f}")

    elif step_num == 4:
        for ticker, thesis in ctx.sell_thesis.items():
            print(f"  {ticker}: flags={thesis.get('flags', [])}  "
                  f"urgency={thesis.get('dte_urgency')}")

    elif step_num == 5:
        for ticker, dv in ctx.sell_devils.items():
            da_reasons = dv.get("da_reasons", [])
            da_str = f"  DA차감={da_reasons}" if da_reasons else ""
            print(f"  {ticker}: event={dv.get('event_judgment')}  "
                  f"iv_crush={dv.get('iv_crush_risk')}{da_str}")

    elif step_num == 6:
        if ctx.sell_iv_warnings:
            for w in ctx.sell_iv_warnings:
                print(f"  ⚠ {w}")
        else:
            print("  IV Crush 경고 없음")

    elif step_num == 7:
        for d in ctx.sell_preliminary:
            key_flags = [f for f in d.get("flags", [])
                         if any(k in f for k in ["권고", "달성", "역전", "발동", "도달", "초과", "근접"])]
            print(f"  {d['ticker']}: {d['action']}  "
                  f"unrealized=${d.get('unrealized_pnl', 0):+,.0f}  "
                  f"flags={key_flags}")

    elif step_num == 8:
        partial = [d for d in ctx.sell_preliminary if d.get("action") == "PARTIAL_EXIT"]
        if partial:
            for d in partial:
                print(f"  {d['ticker']}: 부분청산 실행  "
                      f"realized=${d.get('realized_pnl', 0):+,.0f}")
        else:
            print("  PARTIAL_EXIT 없음 — 처리 스킵")

    elif step_num == 10:
        for d in ctx.sell_decisions:
            tech = ctx.technical_scores.get(
                next((f"{p.ticker}_{p.expiry}_{p.strike}"
                      for p in ctx.positions if p.ticker == d.ticker), ""), None
            )
            tech_str = f"  [기술점수 {tech.final_score:.1f}/signal={tech.signal_count}]" if tech else ""
            print(f"  {d.ticker}: {d.action}  urgency={d.urgency}{tech_str}")
            # 근거 전문 출력 (잘리지 않게)
            rationale = d.rationale or "(없음)"
            for i, line in enumerate(rationale.split(".")):
                line = line.strip()
                if line:
                    print(f"    {'└─' if i == 0 else '  '} {line}.")


# ─────────────────────────────────────────────────────────────
# 파이프라인 실행
# ─────────────────────────────────────────────────────────────

async def run(real: bool = False, use_cache: bool = False) -> None:
    from typing import Any
    from shared.config import get_config
    from shared.schemas import PipelineContext, PipelinePaths
    from orchestrator.pipelines import SellPipeline
    from core.obsidian import ObsidianClient
    from core.slack import SlackClient

    cfg = get_config()

    paths = PipelinePaths(
        summary_dir=Path(cfg.SUMMARY_DIR),
        finviz_file=Path(cfg.FINVIZ_FILE),
        earnings_dir=Path(cfg.EARNINGS_DIR),
        earnings_analysis=Path(cfg.EARNINGS_DIR) / "어닝 분석.md",
        earnings_analysis_today=Path(cfg.EARNINGS_DIR) / "어닝 분석_today.md",
        finviz_output_dir=Path(cfg.EARNINGS_DIR) / "finviz_output",
        earnings_call_dir=Path(cfg.EARNINGS_DIR) / "어닝콜_output",
        positions_file=Path(cfg.POSITIONS_FILE),
        watchlist_file=Path(cfg.WATCHLIST_FILE),
        data_dir=Path(cfg.DATA_DIR),
    )

    eid = f"sell_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    ctx = PipelineContext(
        execution_id=eid,
        pipeline_type="sell",
        start_step=0,
        force_refresh=not use_cache,
        paths=paths,
    )

    obsidian_client = ObsidianClient()
    slack_client = SlackClient()
    pipeline = SellPipeline(obsidian=obsidian_client, slack=slack_client)
    steps = pipeline._steps

    cache_mode = "캐시 사용" if use_cache else "캐시 무시 (새로 실행)"
    dry_run_label = "실제 저장/알림" if real else "DRY-RUN (저장 없음)"
    print(f"\n{'='*60}")
    print(f"  SwingMCP Sell Pipeline  [{dry_run_label}]")
    print(f"  실행ID : {eid}")
    print(f"  캐시 모드: {cache_mode}")
    print(f"{'='*60}")

    failed_steps: list[int] = []

    # ── Step 0: 환경 + 데이터 로딩 ───────────────────────────
    print(f"\n▶ Step 0: 환경 + 데이터 로딩...")
    try:
        await steps.step_0_env(ctx)
        _print_step_result(ctx, 0)
    except Exception as exc:
        import traceback
        print(f"  ✗ FATAL — Step 0 실패: {exc}")
        print(traceback.format_exc())
        return

    # ── 포지션 확인 ───────────────────────────────────────────
    if not ctx.positions:
        print("\n  → positions.md에 포지션 없음. 매도 분석 대상 없음.")
        return

    print(f"\n  ✓ 포지션 {len(ctx.positions)}개: {[p.ticker for p in ctx.positions]}")

    # ── Step 1~13 순서대로 실행 ──────────────────────────────
    step_defs = [
        (1,  "포지션 건전성",     steps.step_1_health),
        (2,  "시장 레짐 비교",    steps.step_2_regime),
        (3,  "기술 분석 + 뉴스",  steps.step_3_technical),
        (4,  "Thesis 검증 LLM",   steps.step_4_thesis),
        (5,  "Devil's Advocate",  steps.step_5_devils),
        (6,  "IV Crush 분석",     steps.step_6_options),
        (7,  "행동 시나리오",     steps.step_7_action),
        (8,  "부분 매도 처리",    steps.step_8_partial),
        (9,  "포트폴리오 재확인", steps.step_9_portfolio),
        (10, "최종 결정 LLM",     steps.step_10_decision),
        (11, "저장",              steps.step_11_storage),
        (12, "복기 LLM",          steps.step_12_review),
        (13, "Slack 알림",        steps.step_13_notify),
    ]

    for step_num, step_name, step_fn in step_defs:
        print(f"\n▶ Step {step_num}: {step_name}...")
        try:
            await step_fn(ctx)
            _print_step_result(ctx, step_num)
            print(f"  ✓ 완료")
        except Exception as exc:
            import traceback
            print(f"  ✗ 실패: {exc}")
            print(f"  {traceback.format_exc()}")
            failed_steps.append(step_num)

    # ── 최종 보고서 ──────────────────────────────────────────
    _print_final_report(ctx, failed_steps)


def _print_final_report(ctx: Any, failed_steps: list[int]) -> None:
    print(f"\n{'='*60}")
    print(f"  ★ 매도 파이프라인 최종 결과")
    print(f"{'='*60}")

    total = 14
    ok = total - len(failed_steps)
    print(f"\n  실행 결과: {ok}/{total} 성공  "
          f"{'✓ 전체통과' if not failed_steps else f'✗ 실패:{failed_steps}'}")

    if not ctx.sell_decisions:
        print("\n  → SellDecision 없음 (포지션 없거나 Step 10 미완)")
    else:
        print(f"\n  결정 ({len(ctx.sell_decisions)}건):")
        for d in ctx.sell_decisions:
            icon = {
                "HOLD":         "🟡",
                "PARTIAL_EXIT": "🟠",
                "FULL_EXIT":    "🔴",
                "ROLL":         "🔵",
            }.get(d.action, "⚪")
            print(f"\n  {icon} {d.ticker}: {d.action}  [{d.urgency}]")
            print(f"     실현P&L  : ${d.realized_pnl:+,.0f}")
            print(f"     미실현P&L: ${d.unrealized_pnl:+,.0f}")
            if d.risk_factors:
                print(f"     리스크   : {d.risk_factors[0][:100]}")
            # 근거 전문 출력 (잘리지 않게)
            rationale = d.rationale or "(없음)"
            lines = [l.strip() for l in rationale.split(".") if l.strip()]
            if lines:
                print(f"     근거     : {lines[0]}.")
                for extra in lines[1:]:
                    print(f"               {extra}.")

    print(f"\n{'='*60}\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SwingMCP Sell Pipeline")
    parser.add_argument(
        "--real", action="store_true",
        help="실제 저장/알림 (없으면 DRY-RUN)"
    )
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument(
        "--use-cache", action="store_true",
        help="LLM 캐시 있으면 재사용"
    )
    cache_group.add_argument(
        "--no-cache", action="store_true",
        help="LLM 캐시 무시하고 새로 실행 (기본값)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    args = _parse_args()
    use_cache = args.use_cache and not args.no_cache
    asyncio.run(run(real=args.real, use_cache=use_cache))
