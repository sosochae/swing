"""
scripts/run_buy_pipeline.py
===========================
Buy Pipeline 독립 실행 스크립트

실제 ObsidianClient / SlackClient를 사용하여 분석 결과를
Obsidian에 저장하고 Slack으로 알림을 전송합니다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

# ── 프로젝트 루트를 sys.path에 추가 ──────────────────────────
_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))


# ── 파이프라인 실행 ────────────────────────────────────────────

async def run(use_cache: bool = False) -> None:
    from shared.config import get_config
    from shared.logger import setup_logging
    from shared.schemas import PipelineContext, PipelinePaths
    from orchestrator.pipelines import BuyPipeline
    from core.obsidian import ObsidianClient
    from core.slack import SlackClient

    cfg = get_config()

    eid = f"buy_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    setup_logging(eid)  # ← llm_call_success 포함 전체 로그를 파일에 저장

    paths = PipelinePaths(
        summary_dir=Path(cfg.SUMMARY_DIR),
        finviz_file=Path(cfg.FINVIZ_FILE),
        earnings_dir=Path(cfg.EARNINGS_DIR),
        earnings_analysis=Path(cfg.EARNINGS_DIR) / "어닝 분석.md",
        positions_file=Path(cfg.POSITIONS_FILE),
        watchlist_file=Path(cfg.WATCHLIST_FILE),
        data_dir=Path(cfg.DATA_DIR),
    )

    ctx = PipelineContext(
        execution_id=eid,
        pipeline_type="buy",
        start_step=0,
        force_refresh=not use_cache,
        target_tickers=None,   # summary의 모든 종목
        paths=paths,
    )

    pipeline = BuyPipeline(obsidian=ObsidianClient(), slack=SlackClient())

    cache_mode = "캐시 사용" if use_cache else "캐시 무시 (새로 실행)"
    print(f"{'='*60}")
    print(f"  SwingMCP Buy Pipeline  [{eid}]")
    print(f"  캐시 모드: {cache_mode}")
    print(f"{'='*60}")

    result = await pipeline.run(ctx)

    # ── 최종 보고서 출력 ──────────────────────────────────────
    print_report(ctx, result)


def print_report(ctx: Any, result: Any) -> None:
    from shared.config import get_config
    cfg = get_config()

    print(f"\n{'='*60}")
    print(f"  ★ 실행 요약 카드")
    print(f"  실행ID : {ctx.execution_id}")
    print(f"  생성일 : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  상태   : {result.status}")
    print(f"{'='*60}")

    # 시장 레짐
    if ctx.regime:
        r = ctx.regime
        print(f"\n▶ 시장 레짐")
        print(f"  상태     : {r.regime_status.upper()}")
        print(f"  허용 방향 : {r.allowed_direction}")
        print(f"  확신도   : {r.regime_confidence:.0%}")
        if r.risk_factors:
            print(f"  리스크   : {', '.join(r.risk_factors[:3])}")

    # 필터링 결과
    print(f"\n▶ 종목 필터링")
    print(f"  분석 대상  : {len(ctx.watchlist)}개")
    print(f"  DA 통과    : {len(ctx.filtered_tickers)}개")
    if ctx.filter_failures:
        print(f"  탈락 사유 :")
        for t, reasons in list(ctx.filter_failures.items())[:5]:
            print(f"    {t}: {reasons[0] if reasons else '사유 없음'}")

    # 기술 분석 점수
    if ctx.technical_scores:
        print(f"\n▶ 기술 분석 점수 (상위 5)")
        sorted_scores = sorted(ctx.technical_scores.items(),
                               key=lambda x: x[1].final_score, reverse=True)
        print(f"  {'티커':<6} {'점수':>6} {'방향':<12} {'신호':>4} {'추세':>6} {'자금':>6}")
        print(f"  {'-'*50}")
        for t, s in sorted_scores[:5]:
            print(f"  {t:<6} {s.final_score:>5.1f}  {s.direction:<12} {s.signal_count:>3}/7 "
                  f"  {'✓' if s.trend_confirmed else '✗':<5}  {'✓' if s.capital_flow_confirmed else '✗'}")

    # 옵션 유효성
    if ctx.option_validity:
        valid_cnt = sum(1 for v in ctx.option_validity.values() if v.is_valid)
        print(f"\n▶ 옵션 유효성 검증")
        print(f"  검증 완료 : {len(ctx.option_validity)}개")
        print(f"  유효      : {valid_cnt}개")
        for t, v in ctx.option_validity.items():
            status = "✓ 유효" if v.is_valid else "✗ 제외"
            print(f"  {t}: {status}  Strike=${v.strike:.0f}  Delta={v.greeks.delta:.2f}  "
                  f"IVR={v.greeks.ivr:.0f}%  DTE={(v.expiry - date.today()).days}일")
            if not v.is_valid and v.exclusion_reason:
                print(f"    └─ {v.exclusion_reason}")

    # 시나리오 분석
    if ctx.scenarios:
        print(f"\n▶ 시나리오 분석 (3케이스)")
        for t, s in ctx.scenarios.items():
            print(f"  [{t}] {s.direction}  {s.contracts}계약 × ${s.total_investment:,.0f}")
            print(f"    강세({s.bullish.probability:.0%}) → EV ${s.bullish.net_profit:+,.0f}")
            print(f"    기본({s.base.probability:.0%}) → EV ${s.base.net_profit:+,.0f}")
            print(f"    약세({s.bearish.probability:.0%}) → EV ${s.bearish.net_profit:+,.0f}")
            print(f"    기대값: ${s.expected_value:+,.0f}  "
                  f"손절: ${s.stop_loss_premium:.2f}  1차익절: ${s.target_premium_1st:.2f}")

    # 최종 순위 (핵심 보고서)
    print(f"\n{'='*60}")
    print(f"  ★ 최종 매매 판단")
    print(f"{'='*60}")

    if not ctx.final_rankings:
        print("  → 진입 가능 종목 없음 (레짐 불리하거나 전종목 필터 탈락)")
    else:
        for r in ctx.final_rankings:
            action_icon = {"진입": "🟢", "관찰": "🟡", "보류": "🟠", "탈락": "🔴"}.get(r.action, "⚪")
            print(f"\n  {action_icon} [{r.rank}위] {r.ticker} — {r.action}")
            print(f"  방향    : {r.direction}")
            print(f"  행사가  : ${r.strike:.0f}  만기: {r.expiry}")
            print(f"  투자금  : ${r.capital_allocation:,.0f}  계약: {r.contracts}계약")
            print(f"  기술점수: {r.final_score:.1f}/100")
            c = r.conviction
            print(f"  확신도  : {c.total_conviction:.2f} ({c.level})")
            print(f"    추세 {c.trend_confidence:.2f} × 0.4  뉴스 {c.news_confidence:.2f} × 0.2")
            print(f"    thesis {c.thesis_confidence:.2f} × 0.3  실행 {c.execution_confidence:.2f} × 0.1")
            print(f"  신호수  : {c.technical_signals}/7  R/R: {c.rr_ratio:.1f}")
            print(f"  판단    : {r.rationale}")
            if r.risk_factors:
                print(f"  리스크  : {'; '.join(r.risk_factors[:2])}")

    # 포트폴리오 노출
    if ctx.portfolio_exposure:
        pe = ctx.portfolio_exposure
        print(f"\n▶ 포트폴리오 현황")
        print(f"  총자본   : ${cfg.TOTAL_CAPITAL:,.0f}")
        print(f"  투자중   : ${pe.total_invested:,.0f}")
        print(f"  잔여현금 : ${pe.remaining_cash:,.0f}")
        if pe.warnings:
            print(f"  경고     : {'; '.join(pe.warnings[:2])}")

    # 완료 단계
    completed = result.completed_steps if hasattr(result, 'completed_steps') else []
    failed = result.failed_steps if hasattr(result, 'failed_steps') else []
    print(f"\n▶ 실행 요약")
    print(f"  완료 단계: {completed}")
    print(f"  실패 단계: {failed}")
    print(f"  총 소요  : {result.duration_seconds:.1f}초" if hasattr(result, 'duration_seconds') else "")

    print(f"\n{'='*60}")
    print("  분석 완료")
    print(f"{'='*60}\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SwingMCP Buy Pipeline")
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
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    args = _parse_args()
    use_cache = args.use_cache and not args.no_cache
    asyncio.run(run(use_cache=use_cache))
