"""
scripts/run_screener.py
=======================
펀더멘털 스크리닝 + 어닝콜 LLM 분석 독립 실행 스크립트

Step 1: Finviz 상세 데이터 파싱 (finviz_output/*.txt)
Step 2: 어닝콜 LLM 분석 (어닝 분석.md → LLM → 점수)
Step 3: 종목 점수화 + 랭킹

결과 → Obsidian 노트 저장 + Slack 요약 전송

사용법:
    cd C:\\MCP\\Swing
    .venv\\Scripts\\python scripts\\run_screener.py
    .venv\\Scripts\\python scripts\\run_screener.py --force-refresh --top 20
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path

# ── 프로젝트 루트를 sys.path에 추가 ──────────────────────────
_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))


async def run(force_refresh: bool = False, top_n: int = 10, earnings_cache_only: bool = False) -> None:
    from core.earnings_analyzer import analyze_earnings
    from core.fundamental_screener import rank_universe
    from core.obsidian import ObsidianClient
    from core.parsers import parse_finviz, parse_finviz_detail
    from core.slack import SlackClient
    from shared.config import get_config
    from shared.logger import setup_logging
    from shared.schemas import PipelinePaths, ScreenerResult

    cfg = get_config()
    execution_id = f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log = setup_logging(execution_id)

    obsidian = ObsidianClient()
    slack = SlackClient()
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

    print(f"\n{'='*60}")
    print(f"  SwingMCP 펀더멘털 스크리닝  [{execution_id}]")
    print(f"  force_refresh={force_refresh}  earnings_cache_only={earnings_cache_only}  top_n={top_n}")
    print(f"{'='*60}")

    start = time.monotonic()

    # ── Step 1: Finviz 상세 파싱 ─────────────────────────────
    print("\n▶ Step 1: Finviz 상세 데이터 파싱...")
    try:
        finviz_details = parse_finviz_detail(paths.finviz_output_dir)
        print(f"  ✓ {len(finviz_details)}개 종목 파싱 완료")
    except Exception as exc:
        print(f"  ✗ FATAL — Finviz 파싱 실패: {exc}")
        return

    # finviz_all_rows.txt → 섹터/회사명 메타
    meta: dict[str, dict] = {}
    try:
        rows = parse_finviz(paths.finviz_file)
        meta = {r.ticker: {"sector": r.sector, "company": r.company_name} for r in rows}
        print(f"  ✓ 메타(섹터/회사명) {len(meta)}개 로드")
    except Exception as exc:
        print(f"  △ 메타 로드 실패 (스코어링은 계속): {exc}")

    # ── Step 2: 어닝콜 LLM 분석 ─────────────────────────────
    print("\n▶ Step 2: 어닝콜 LLM 분석...")
    # earnings_cache_only: 어닝 캐시는 항상 재사용 (force_refresh 무시)
    earnings_force_refresh = False if earnings_cache_only else force_refresh
    earnings_analyses: dict = {}
    if paths.earnings_analysis.exists():
        try:
            today_path = (
                paths.earnings_analysis_today
                if paths.earnings_analysis_today.exists()
                else None
            )
            earnings_analyses = await analyze_earnings(
                earnings_analysis_path=paths.earnings_analysis,
                earnings_today_path=today_path,
                force_refresh=earnings_force_refresh,
            )
            print(f"  ✓ {len(earnings_analyses)}개 어닝콜 분석 완료")
        except Exception as exc:
            print(f"  △ 어닝콜 분석 실패 (스코어링은 계속): {exc}")
    else:
        print(f"  △ 어닝 분석.md 없음 — 어닝콜 점수 제외")
        print(f"     경로: {paths.earnings_analysis}")

    # ── Step 3: 점수화 + 랭킹 ───────────────────────────────
    print("\n▶ Step 3: 종목 점수화 + 랭킹...")
    try:
        ranked = rank_universe(
            finviz_details=finviz_details,
            earnings_analyses=earnings_analyses,
            finviz_rows_meta=meta,
        )
        print(f"  ✓ {len(ranked)}개 랭킹 완료")
    except Exception as exc:
        print(f"  ✗ FATAL — 점수화 실패: {exc}")
        return

    duration = round(time.monotonic() - start, 1)

    result = ScreenerResult(
        execution_id=execution_id,
        total_universe=len(finviz_details),
        with_earnings=len(earnings_analyses),
        top10=ranked[:top_n],
        all_results=ranked,
        duration_seconds=duration,
    )

    # ── 터미널 보고서 ────────────────────────────────────────
    _print_report(result, top_n)

    # ── Obsidian 저장 ────────────────────────────────────────
    print("\n▶ Obsidian 저장...")
    note_content = _format_obsidian_note(result, earnings_analyses)
    note_path = f"swing-procedure/screener/{date.today().isoformat()}.md"
    try:
        await obsidian.write_note(note_path, note_content)
        print(f"  ✓ {note_path}")
    except Exception as exc:
        print(f"  △ Obsidian 저장 실패: {exc}")

    # ── Slack 알림 ───────────────────────────────────────────
    print("\n▶ Slack 전송...")
    try:
        slack_msg = _format_slack_summary(result)
        await slack._send(cfg.SLACK_CHANNEL_MAIN, slack_msg)
        print(f"  ✓ {cfg.SLACK_CHANNEL_MAIN} 전송 완료")
    except Exception as exc:
        print(f"  △ Slack 전송 실패: {exc}")

    print(f"\n{'='*60}")
    print(f"  스크리닝 완료  [{execution_id}]  소요: {duration}초")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────
# 터미널 보고서 포맷터
# ─────────────────────────────────────────────────────────────

def _print_report(result: "ScreenerResult", top_n: int) -> None:
    print(f"\n{'='*60}")
    print(f"  ★ 펀더멘털 스크리닝 결과")
    print(f"  유니버스: {result.total_universe}개  어닝콜: {result.with_earnings}개  소요: {result.duration_seconds:.1f}초")
    print(f"{'='*60}")

    print(f"\n{'티커':<7} {'총점':>6} {'모멘텀':>6} {'펀더':>6} {'카탈리스트':>10} {'가격':>8} {'RSI':>6} {'RelVol':>7} 가이던스 톤")
    print(f"  {'-'*75}")

    guidance_map = {"up": "↑상향", "flat": "→유지", "down": "↓하향", "unknown": "?", "": "-"}
    tone_map = {"bullish": "🟢강세", "neutral": "🟡중립", "bearish": "🔴약세", "": "-"}

    for r in result.top10:
        price_str = f"${r.price:.2f}" if r.price else "  -   "
        rsi_str = f"{r.rsi14:.1f}" if r.rsi14 else "  -  "
        rvol_str = f"{r.rel_volume:.1f}x" if r.rel_volume else "  -  "
        guidance = guidance_map.get(r.guidance_direction or "", "-")
        tone = tone_map.get(r.mgmt_tone or "", "-")
        print(f"  {r.rank:>2}. {r.ticker:<6} {r.total_score:>5.1f} {r.momentum_score:>6.0f} {r.fundamental_score:>6.0f} {r.catalyst_score:>10.0f} {price_str:>8} {rsi_str:>6} {rvol_str:>7} {guidance:<6} {tone}")

    if result.all_results and len(result.all_results) > top_n:
        remaining = result.all_results[top_n:top_n+20]
        print(f"\n  다음 {len(remaining)}개:")
        line_parts = []
        for r in remaining:
            line_parts.append(f"{r.ticker}({r.total_score:.0f})")
        print(f"  {', '.join(line_parts)}")


def _format_obsidian_note(result: "ScreenerResult", earnings_analyses: dict | None = None) -> str:
    today = date.today().isoformat()
    earnings_analyses = earnings_analyses or {}

    lines = [
        f"# 펀더멘털 스크리닝 — {today}",
        f"실행ID: `{result.execution_id}`  |  "
        f"유니버스: {result.total_universe}개  |  "
        f"어닝콜 보유: {result.with_earnings}개  |  "
        f"소요: {result.duration_seconds:.1f}초",
        "",
        "## Top 10 후보",
        "",
        "| Rank | Ticker | Momentum | Catalyst | Total | Price | RSI | RelVol | 가이던스 | 톤 |",
        "|------|--------|----------|----------|-------|-------|-----|--------|----------|-----|",
    ]

    _guidance_icon = {"up": "↑", "flat": "→", "down": "↓", "unknown": "?", "": "-"}
    _tone_icon     = {"bullish": "🟢", "neutral": "🟡", "bearish": "🔴", "": "-"}

    for r in result.top10:
        guidance_icon = _guidance_icon.get(r.guidance_direction or "", "-")
        tone_icon = _tone_icon.get(r.mgmt_tone or "", "-")
        price_str = f"${r.price:.2f}" if r.price else "-"
        rsi_str = f"{r.rsi14:.1f}" if r.rsi14 else "-"
        rvol_str = f"{r.rel_volume:.1f}x" if r.rel_volume else "-"

        lines.append(
            f"| {r.rank} | **{r.ticker}** | {r.momentum_score:.0f} | "
            f"{r.catalyst_score:.0f} | **{r.total_score:.1f}** | {price_str} | {rsi_str} | "
            f"{rvol_str} | {guidance_icon} | {tone_icon} |"
        )

    lines += ["", "---", "", "## 전체 순위 (상위 30)"]
    for r in result.all_results[:30]:
        catalyst_tag = f" [{r.guidance_direction}/{r.mgmt_tone}]" if r.has_catalyst else ""
        lines.append(
            f"{r.rank}. **{r.ticker}** {r.total_score:.1f}점"
            f"  (M:{r.momentum_score:.0f} C:{r.catalyst_score:.0f})"
            f"{catalyst_tag}"
        )
        if r.key_risks:
            lines.append(f"   - 리스크: {', '.join(r.key_risks[:2])}")
        # LLM 판단 근거 인용
        ea = earnings_analyses.get(r.ticker)
        if ea:
            if ea.guidance_evidence:
                g_icon = _guidance_icon.get(r.guidance_direction or "", "-")
                lines.append(f"   - 📈 가이던스 [{g_icon}]: _{ea.guidance_evidence}_")
            if ea.tone_evidence:
                t_icon = _tone_icon.get(r.mgmt_tone or "", "-")
                lines.append(f"   - 🗣️ 톤 [{t_icon}]: _{ea.tone_evidence}_")

    lines += ["", f"*생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"]
    return "\n".join(lines)


def _format_slack_summary(result: "ScreenerResult") -> str:
    today = date.today().isoformat()
    top3 = result.top10[:3]
    medals = ["🥇", "🥈", "🥉"]

    lines = [f"*📊 펀더멘털 스크리닝 완료 — {today}*"]
    lines.append(f"유니버스 {result.total_universe}개 | 어닝콜 {result.with_earnings}개 분석")
    lines.append("")
    lines.append("*Top 3 후보:*")
    for medal, r in zip(medals, top3):
        guidance = {
            "up": "↑가이던스 상향", "flat": "→유지", "down": "↓하향",
            "unknown": "", "": ""
        }.get(r.guidance_direction or "", "")
        lines.append(f"{medal} `{r.ticker}` — {r.total_score:.1f}점  {guidance}")

    if len(result.top10) > 3:
        rest = [f"`{r.ticker}`({r.total_score:.0f})" for r in result.top10[3:]]
        lines.append(f"\nTop 4~{len(result.top10)}: {', '.join(rest)}")

    lines.append(f"\n_소요시간: {result.duration_seconds:.0f}초_")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# CLI 진입점
# ─────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SwingMCP 펀더멘털 스크리닝 + 어닝콜 LLM 분석"
    )
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument(
        "--no-cache", "--force-refresh", dest="no_cache", action="store_true",
        help="모든 LLM 캐시 무시하고 새로 분석 (어닝 포함)"
    )
    cache_group.add_argument(
        "--earnings-cache-only", action="store_true",
        help="어닝 LLM 캐시만 재사용, 나머지는 새로 실행"
    )
    cache_group.add_argument(
        "--use-cache", dest="use_cache", action="store_true", default=True,
        help="캐시 있으면 재사용 (기본값)"
    )
    parser.add_argument(
        "--top", type=int, default=10, metavar="N",
        help="보고서 상위 N개 출력 (기본: 10)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    args = _parse_args()
    asyncio.run(run(
        force_refresh=args.no_cache,
        top_n=args.top,
        earnings_cache_only=args.earnings_cache_only,
    ))
