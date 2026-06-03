"""
servers/screener_mcp/server.py
==============================
ScreenerMCP — 펀더멘털 스크리닝 전용 MCP 서버

노출 Tools:
  1. run_fundamental_screen  - 3단계 파이프라인 실행 (파싱→LLM분석→점수화)
  2. screener_health_check   - 데이터 파일·Obsidian·Slack 연결 확인

stdio 프로토콜로 Roo Code / Claude Desktop에 연결됩니다.
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

# 프로젝트 루트를 Python 경로에 추가
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

import mcp.server.stdio  # type: ignore
import mcp.types as types  # type: ignore
from mcp.server import Server  # type: ignore

# ── MCP stdio 보호: stdout은 JSON-RPC 전용 ──────────────────
import logging as _logging
_logging.root.handlers.clear()
_stderr_handler = _logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(_logging.WARNING)
_logging.root.addHandler(_stderr_handler)
_logging.root.setLevel(_logging.WARNING)
_logging.getLogger("mcp").setLevel(_logging.WARNING)
_logging.getLogger("asyncio").setLevel(_logging.WARNING)

from core.earnings_analyzer import analyze_earnings
from core.fundamental_screener import rank_universe
from core.obsidian import ObsidianClient
from core.parsers import parse_finviz, parse_finviz_detail
from core.slack import SlackClient
from shared.config import get_config
from shared.logger import get_logger, setup_logging
from shared.schemas import PipelinePaths, ScreenerResult

cfg = get_config()
log = setup_logging()

server = Server("screener-mcp")
_obsidian = ObsidianClient()
_slack = SlackClient()


# ─────────────────────────────────────────────────────────────
# 노트 포맷터
# ─────────────────────────────────────────────────────────────

def _format_obsidian_note(result: ScreenerResult) -> str:
    today = date.today().isoformat()
    lines = [
        f"# 펀더멘털 스크리닝 — {today}",
        f"실행ID: `{result.execution_id}`  |  "
        f"유니버스: {result.total_universe}개  |  "
        f"어닝콜 보유: {result.with_earnings}개  |  "
        f"소요: {result.duration_seconds:.1f}초",
        "",
        "## Top 10 후보",
        "",
        "| Rank | Ticker | Momentum | Fundamental | Catalyst | Total | Price | RSI | RelVol | 가이던스 | 톤 |",
        "|------|--------|----------|-------------|----------|-------|-------|-----|--------|----------|-----|",
    ]

    for r in result.top10:
        guidance_icon = {"up": "↑", "flat": "→", "down": "↓", "unknown": "?", "": "-"}.get(r.guidance_direction, "-")
        tone_icon = {"bullish": "🟢", "neutral": "🟡", "bearish": "🔴", "": "-"}.get(r.mgmt_tone, "-")
        price_str = f"${r.price:.2f}" if r.price else "-"
        rsi_str = f"{r.rsi14:.1f}" if r.rsi14 else "-"
        rvol_str = f"{r.rel_volume:.1f}x" if r.rel_volume else "-"

        lines.append(
            f"| {r.rank} | **{r.ticker}** | {r.momentum_score:.0f} | {r.fundamental_score:.0f} | "
            f"{r.catalyst_score:.0f} | **{r.total_score:.1f}** | {price_str} | {rsi_str} | "
            f"{rvol_str} | {guidance_icon} | {tone_icon} |"
        )

    lines += ["", "---", "", "## 전체 순위 (상위 30)"]
    for r in result.all_results[:30]:
        catalyst_tag = f" [{r.guidance_direction}/{r.mgmt_tone}]" if r.has_catalyst else ""
        lines.append(
            f"{r.rank}. **{r.ticker}** {r.total_score:.1f}점"
            f"  (M:{r.momentum_score:.0f} F:{r.fundamental_score:.0f} C:{r.catalyst_score:.0f})"
            f"{catalyst_tag}"
        )
        if r.key_risks:
            lines.append(f"   - 리스크: {', '.join(r.key_risks[:2])}")

    lines += ["", f"*생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"]
    return "\n".join(lines)


def _format_slack_summary(result: ScreenerResult) -> str:
    today = date.today().isoformat()
    top3 = result.top10[:3]
    medals = ["🥇", "🥈", "🥉"]

    lines = [f"*📊 펀더멘털 스크리닝 완료 — {today}*"]
    lines.append(f"유니버스 {result.total_universe}개 | 어닝콜 {result.with_earnings}개 분석")
    lines.append("")
    lines.append("*Top 3 후보:*")
    for medal, r in zip(medals, top3):
        guidance = {"up": "↑가이던스 상향", "flat": "→유지", "down": "↓하향", "unknown": "", "": ""}.get(r.guidance_direction, "")
        lines.append(f"{medal} `{r.ticker}` — {r.total_score:.1f}점  {guidance}")

    if len(result.top10) > 3:
        rest = [f"`{r.ticker}`({r.total_score:.0f})" for r in result.top10[3:]]
        lines.append(f"\nTop 4~10: {', '.join(rest)}")

    lines.append(f"\n_소요시간: {result.duration_seconds:.0f}초_")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Tool 정의
# ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="run_fundamental_screen",
            description=(
                "Finviz 전체 종목 대상 3단계 펀더멘털 스크리닝 파이프라인 실행. "
                "Step1: finviz_output/*.txt 파싱 → Step2: 어닝_분석.md LLM 분석 → "
                "Step3: 점수화+랭킹 → Top10 Obsidian 노트 + Slack 요약 전송."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "execution_id": {
                        "type": "string",
                        "description": "실행 ID (생략 시 자동 생성)",
                    },
                    "force_refresh": {
                        "type": "boolean",
                        "description": "LLM 캐시 무시 여부 (기본: false)",
                        "default": False,
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "최종 보고서 상위 N개 (기본: 10)",
                        "default": 10,
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="screener_health_check",
            description="데이터 파일 존재 여부, Obsidian·Slack 연결 상태 확인.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    if name == "run_fundamental_screen":
        # 백그라운드 실행 — 타임아웃 방지
        exec_id = arguments.get("execution_id") or f"screen_{uuid.uuid4().hex[:8]}"
        arguments["execution_id"] = exec_id
        asyncio.create_task(_run_fundamental_screen(arguments))
        return [types.TextContent(type="text", text=(
            f"✅ 펀더멘털 스크리닝 실행 완료 [{exec_id}]\n"
            "백그라운드에서 분석 중입니다. Slack으로 결과가 전송되고 Obsidian에 노트가 저장됩니다. 추가 작업 불필요."
        ))]
    if name == "screener_health_check":
        return await _screener_health_check()
    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


# ─────────────────────────────────────────────────────────────
# run_fundamental_screen 구현
# ─────────────────────────────────────────────────────────────

async def _run_fundamental_screen(args: dict[str, Any]) -> list[types.TextContent]:
    execution_id = args.get("execution_id") or f"screen_{uuid.uuid4().hex[:8]}"
    force_refresh = bool(args.get("force_refresh", False))
    top_n = int(args.get("top_n", 10))

    paths = PipelinePaths()
    start = time.monotonic()
    log.info("screener_start", execution_id=execution_id, force_refresh=force_refresh)

    # ── Step 1: Finviz 파싱 ─────────────────────────────────
    try:
        finviz_details = parse_finviz_detail(paths.finviz_output_dir)
        log.info("step1_done", tickers=len(finviz_details))
    except Exception as exc:
        msg = f"Step1 실패 (finviz 파싱): {exc}"
        log.error("step1_failed", error=str(exc))
        return [types.TextContent(type="text", text=msg)]

    # finviz_all_rows.txt에서 섹터/회사명 메타 추출
    meta: dict[str, dict] = {}
    try:
        rows = parse_finviz(paths.finviz_file)
        meta = {r.ticker: {"sector": r.sector, "company": r.company_name} for r in rows}
    except Exception:
        pass  # 메타 없어도 스코어링 가능

    # ── Step 2: 어닝콜 LLM 분석 ────────────────────────────
    earnings_analyses = {}
    if paths.earnings_analysis.exists():
        try:
            today_path = paths.earnings_analysis_today if paths.earnings_analysis_today.exists() else None
            earnings_analyses = await analyze_earnings(
                earnings_analysis_path=paths.earnings_analysis,
                earnings_today_path=today_path,
                force_refresh=force_refresh,
            )
            log.info("step2_done", analyzed=len(earnings_analyses))
        except Exception as exc:
            log.warning("step2_degraded", error=str(exc))
    else:
        log.warning("earnings_analysis_missing", path=str(paths.earnings_analysis))

    # ── Step 3: 점수화 + 랭킹 ──────────────────────────────
    try:
        ranked = rank_universe(
            finviz_details=finviz_details,
            earnings_analyses=earnings_analyses,
            finviz_rows_meta=meta,
        )
    except Exception as exc:
        msg = f"Step3 실패 (점수화): {exc}"
        log.error("step3_failed", error=str(exc))
        return [types.TextContent(type="text", text=msg)]

    duration = round(time.monotonic() - start, 1)

    result = ScreenerResult(
        execution_id=execution_id,
        total_universe=len(finviz_details),
        with_earnings=len(earnings_analyses),
        top10=ranked[:top_n],
        all_results=ranked,
        duration_seconds=duration,
    )

    # ── Obsidian 저장 ───────────────────────────────────────
    note_content = _format_obsidian_note(result)
    note_path = f"swing-procedure/screener/{date.today().isoformat()}.md"
    try:
        await _obsidian.write_note(note_path, note_content)
        result.obsidian_note_path = note_path
        log.info("obsidian_written", path=note_path)
    except Exception as exc:
        log.warning("obsidian_failed", error=str(exc))

    # ── Slack 요약 ──────────────────────────────────────────
    try:
        slack_msg = _format_slack_summary(result)
        await _slack._send(cfg.SLACK_CHANNEL_MAIN, slack_msg)
        result.slack_sent = True
        log.info("slack_sent")
    except Exception as exc:
        log.warning("slack_failed", error=str(exc))

    # ── MCP 응답 ────────────────────────────────────────────
    summary_lines = [
        f"✅ 펀더멘털 스크리닝 완료 [{execution_id}]",
        f"유니버스: {result.total_universe}개 | 어닝콜 분석: {result.with_earnings}개 | 소요: {duration}초",
        "",
        "## Top 10",
    ]
    for r in result.top10:
        cat = f" | {r.guidance_direction}/{r.mgmt_tone}" if r.has_catalyst else ""
        summary_lines.append(
            f"{r.rank}. {r.ticker}  {r.total_score:.1f}점"
            f"  (M:{r.momentum_score:.0f} F:{r.fundamental_score:.0f} C:{r.catalyst_score:.0f}){cat}"
        )
    if result.obsidian_note_path:
        summary_lines.append(f"\nObsidian: {result.obsidian_note_path}")

    return [types.TextContent(type="text", text="\n".join(summary_lines))]


# ─────────────────────────────────────────────────────────────
# screener_health_check 구현
# ─────────────────────────────────────────────────────────────

async def _screener_health_check() -> list[types.TextContent]:
    paths = PipelinePaths()
    checks: list[str] = []

    def _check(label: str, ok: bool, detail: str = "") -> None:
        icon = "✅" if ok else "❌"
        line = f"{icon} {label}"
        if detail:
            line += f": {detail}"
        checks.append(line)

    # 파일 경로 확인
    _check("finviz_output 폴더", paths.finviz_output_dir.exists(),
           f"{len(list(paths.finviz_output_dir.glob('*.txt')))}개 파일" if paths.finviz_output_dir.exists() else "없음")
    _check("어닝_분석.md", paths.earnings_analysis.exists())
    _check("finviz_all_rows.txt", paths.finviz_file.exists())

    # Obsidian 연결
    obsidian_ok = await _obsidian.ping()
    _check("Obsidian API", obsidian_ok)

    # Slack 토큰
    _check("Slack 토큰", bool(cfg.SLACK_BOT_TOKEN))

    # OpenRouter API 키
    _check("OpenRouter API 키", bool(cfg.OPENROUTER_API_KEY))

    return [types.TextContent(type="text", text="\n".join(checks))]


# ─────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────

async def main() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
