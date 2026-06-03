"""
servers/kavout_mcp/server.py
============================
KavoutMCP — Kavout AI 유니버스 기반 펀더멘털 스크리닝 MCP 서버

screener_mcp와 동일한 3단계 파이프라인이지만 유니버스 소스와
어닝 데이터 경로가 다릅니다:
  - 유니버스:    kavout_*.csv (최신 파일 자동 탐색)
  - 어닝 분석:   K어닝 분석.md / K어닝 분석_today.md
  - 어닝콜 폴더: K어닝콜_output/
  - Obsidian:   swing-procedure/screener/kavout/{date}.md

노출 Tools:
  1. run_kavout_screen     - 3단계 파이프라인 실행
  2. kavout_health_check   - 데이터 파일·연결 확인

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
from core.parsers import find_latest_kavout_csv, parse_finviz_detail, parse_kavout_universe
from core.slack import SlackClient
from shared.config import get_config
from shared.logger import get_logger, setup_logging
from shared.schemas import FundamentalScoreResult, KavoutRow, PipelinePaths, ScreenerResult

cfg = get_config()
log = setup_logging()

server = Server("kavout-mcp")
_obsidian = ObsidianClient()
_slack = SlackClient()

# ── Kavout 전용 경로 ────────────────────────────────────────
_EARNINGS_DIR = Path(r"Y:\내 드라이브\어닝")
_K_EARNINGS_ANALYSIS    = _EARNINGS_DIR / "K어닝 분석.md"
_K_EARNINGS_TODAY       = _EARNINGS_DIR / "K어닝 분석_today.md"
_K_EARNINGS_CALL_OUTPUT = _EARNINGS_DIR / "K어닝콜_output"
_DATA_DIR = Path(cfg.DATA_DIR)          # Y:\내 드라이브\Data
_FINVIZ_OUTPUT_DIR = _EARNINGS_DIR / "finviz_output"


# ─────────────────────────────────────────────────────────────
# 노트 포맷터
# ─────────────────────────────────────────────────────────────

def _format_obsidian_note(
    result: ScreenerResult,
    kavout_map: dict[str, KavoutRow],
) -> str:
    today = date.today().isoformat()
    lines = [
        f"# Kavout 펀더멘털 스크리닝 — {today}",
        f"실행ID: `{result.execution_id}`  |  "
        f"유니버스: {result.total_universe}개  |  "
        f"어닝콜 보유: {result.with_earnings}개  |  "
        f"소요: {result.duration_seconds:.1f}초",
        "",
        "## Top 10 후보",
        "",
        "| Rank | Ticker | K-Score | Momentum | Fundamental | Catalyst | Total | Price | RSI | 가이던스 | 톤 |",
        "|------|--------|---------|----------|-------------|----------|-------|-------|-----|----------|-----|",
    ]

    for r in result.top10:
        guidance_icon = {"up": "↑", "flat": "→", "down": "↓", "unknown": "?", "": "-"}.get(r.guidance_direction, "-")
        tone_icon = {"bullish": "🟢", "neutral": "🟡", "bearish": "🔴", "": "-"}.get(r.mgmt_tone, "-")
        price_str = f"${r.price:.2f}" if r.price else "-"
        rsi_str = f"{r.rsi14:.1f}" if r.rsi14 else "-"
        krow = kavout_map.get(r.ticker)
        k_score_str = f"{krow.k_score:.2f}" if krow and krow.k_score is not None else "-"

        lines.append(
            f"| {r.rank} | **{r.ticker}** | {k_score_str} | {r.momentum_score:.0f} | "
            f"{r.fundamental_score:.0f} | {r.catalyst_score:.0f} | **{r.total_score:.1f}** | "
            f"{price_str} | {rsi_str} | {guidance_icon} | {tone_icon} |"
        )

    lines += ["", "---", "", "## 전체 순위 (상위 30)"]
    for r in result.all_results[:30]:
        catalyst_tag = f" [{r.guidance_direction}/{r.mgmt_tone}]" if r.has_catalyst else ""
        krow = kavout_map.get(r.ticker)
        k_tag = f" K={krow.k_score:.1f}" if krow and krow.k_score is not None else ""
        lines.append(
            f"{r.rank}. **{r.ticker}** {r.total_score:.1f}점"
            f"  (M:{r.momentum_score:.0f} F:{r.fundamental_score:.0f} C:{r.catalyst_score:.0f})"
            f"{k_tag}{catalyst_tag}"
        )
        if r.key_risks:
            lines.append(f"   - 리스크: {', '.join(r.key_risks[:2])}")

    lines += ["", f"*생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"]
    return "\n".join(lines)


def _format_slack_summary(result: ScreenerResult, kavout_map: dict[str, KavoutRow]) -> str:
    today = date.today().isoformat()
    top3 = result.top10[:3]
    medals = ["🥇", "🥈", "🥉"]

    lines = [f"*📊 Kavout 스크리닝 완료 — {today}*"]
    lines.append(f"유니버스 {result.total_universe}개 | 어닝콜 {result.with_earnings}개 분석")
    lines.append("")
    lines.append("*Top 3 후보:*")
    for medal, r in zip(medals, top3):
        guidance = {"up": "↑가이던스 상향", "flat": "→유지", "down": "↓하향", "unknown": "", "": ""}.get(r.guidance_direction, "")
        krow = kavout_map.get(r.ticker)
        k_tag = f" | K={krow.k_score:.1f}" if krow and krow.k_score is not None else ""
        lines.append(f"{medal} `{r.ticker}` — {r.total_score:.1f}점  {guidance}{k_tag}")

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
            name="run_kavout_screen",
            description=(
                "Kavout AI 유니버스 대상 3단계 펀더멘털 스크리닝 파이프라인 실행. "
                "Step1: kavout_*.csv(최신) 파싱 + finviz_output/*.txt 상세 데이터 → "
                "Step2: K어닝 분석.md LLM 분석 → "
                "Step3: 점수화+랭킹(k_score 표시) → Top10 Obsidian 노트 + Slack 요약 전송."
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
            name="kavout_health_check",
            description="Kavout 데이터 파일 존재 여부, Obsidian·Slack 연결 상태 확인.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    if name == "run_kavout_screen":
        return await _run_kavout_screen(arguments)
    if name == "kavout_health_check":
        return await _kavout_health_check()
    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


# ─────────────────────────────────────────────────────────────
# run_kavout_screen 구현
# ─────────────────────────────────────────────────────────────

async def _run_kavout_screen(args: dict[str, Any]) -> list[types.TextContent]:
    execution_id = args.get("execution_id") or f"kavout_{uuid.uuid4().hex[:8]}"
    force_refresh = bool(args.get("force_refresh", False))
    top_n = int(args.get("top_n", 10))

    start = time.monotonic()
    log.info("kavout_screener_start", execution_id=execution_id, force_refresh=force_refresh)

    # ── Step 1: Kavout CSV 파싱 + finviz_output 필터링 ─────
    kavout_rows = parse_kavout_universe(_DATA_DIR)
    if not kavout_rows:
        return [types.TextContent(type="text", text="Step1 실패: kavout_*.csv 파일을 찾을 수 없습니다.")]

    kavout_tickers = {r.ticker for r in kavout_rows}
    kavout_map: dict[str, KavoutRow] = {r.ticker: r for r in kavout_rows}
    log.info("kavout_universe_loaded", count=len(kavout_tickers))

    # finviz_output/*.txt에서 kavout 티커만 추출
    all_finviz_details = parse_finviz_detail(_FINVIZ_OUTPUT_DIR)
    finviz_details = {t: d for t, d in all_finviz_details.items() if t in kavout_tickers}

    # finviz_output에 없는 Kavout 티커 → kavout CSV price로 최소 FinvizDetail 생성
    from shared.schemas import FinvizDetail
    for row in kavout_rows:
        if row.ticker not in finviz_details:
            finviz_details[row.ticker] = FinvizDetail(
                ticker=row.ticker,
                price=row.price,
            )

    log.info("step1_done",
             kavout=len(kavout_tickers),
             finviz_matched=sum(1 for t in kavout_tickers if t in all_finviz_details),
             total_detail=len(finviz_details))

    # 메타 (sector/company) — kavout CSV에서 우선, finviz_all_rows.txt 없어도 OK
    meta: dict[str, dict] = {
        r.ticker: {"sector": "", "company": r.company}
        for r in kavout_rows
    }

    # ── Step 2: K어닝콜 LLM 분석 ───────────────────────────
    earnings_analyses = {}
    if _K_EARNINGS_ANALYSIS.exists():
        try:
            today_path = _K_EARNINGS_TODAY if _K_EARNINGS_TODAY.exists() else None
            earnings_analyses = await analyze_earnings(
                earnings_analysis_path=_K_EARNINGS_ANALYSIS,
                earnings_today_path=today_path,
                force_refresh=force_refresh,
            )
            log.info("step2_done", analyzed=len(earnings_analyses))
        except Exception as exc:
            log.warning("step2_degraded", error=str(exc))
    else:
        log.warning("k_earnings_analysis_missing", path=str(_K_EARNINGS_ANALYSIS))

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

    # FundamentalScoreResult에 Kavout 고유 필드 채우기
    for r in ranked:
        krow = kavout_map.get(r.ticker)
        if krow:
            r.k_score = krow.k_score
            r.momentum_1m = krow.momentum_1m
            r.roe = krow.roe

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
    note_content = _format_obsidian_note(result, kavout_map)
    note_path = f"swing-procedure/screener/kavout/{date.today().isoformat()}.md"
    try:
        await _obsidian.write_note(note_path, note_content)
        result.obsidian_note_path = note_path
        log.info("obsidian_written", path=note_path)
    except Exception as exc:
        log.warning("obsidian_failed", error=str(exc))

    # ── Slack 요약 ──────────────────────────────────────────
    try:
        slack_msg = _format_slack_summary(result, kavout_map)
        await _slack._send(cfg.SLACK_CHANNEL_MAIN, slack_msg)
        result.slack_sent = True
        log.info("slack_sent")
    except Exception as exc:
        log.warning("slack_failed", error=str(exc))

    # ── MCP 응답 ────────────────────────────────────────────
    summary_lines = [
        f"✅ Kavout 스크리닝 완료 [{execution_id}]",
        f"유니버스: {result.total_universe}개 | 어닝콜 분석: {result.with_earnings}개 | 소요: {duration}초",
        "",
        "## Top 10",
    ]
    for r in result.top10:
        cat = f" | {r.guidance_direction}/{r.mgmt_tone}" if r.has_catalyst else ""
        k_tag = f" | K={r.k_score:.1f}" if r.k_score is not None else ""
        summary_lines.append(
            f"{r.rank}. {r.ticker}  {r.total_score:.1f}점"
            f"  (M:{r.momentum_score:.0f} F:{r.fundamental_score:.0f} C:{r.catalyst_score:.0f})"
            f"{k_tag}{cat}"
        )
    if result.obsidian_note_path:
        summary_lines.append(f"\nObsidian: {result.obsidian_note_path}")

    return [types.TextContent(type="text", text="\n".join(summary_lines))]


# ─────────────────────────────────────────────────────────────
# kavout_health_check 구현
# ─────────────────────────────────────────────────────────────

async def _kavout_health_check() -> list[types.TextContent]:
    checks: list[str] = []

    def _check(label: str, ok: bool, detail: str = "") -> None:
        icon = "✅" if ok else "❌"
        line = f"{icon} {label}"
        if detail:
            line += f": {detail}"
        checks.append(line)

    # Kavout CSV
    latest_csv = find_latest_kavout_csv(_DATA_DIR)
    _check("kavout_*.csv (최신)", latest_csv is not None,
           latest_csv.name if latest_csv else f"없음 ({_DATA_DIR})")

    # Kavout 유니버스 크기
    if latest_csv:
        rows = parse_kavout_universe(_DATA_DIR)
        _check("Kavout 유니버스", len(rows) > 0, f"{len(rows)}개 종목")

    # finviz_output 폴더
    txt_files = list(_FINVIZ_OUTPUT_DIR.glob("*.txt")) if _FINVIZ_OUTPUT_DIR.exists() else []
    _check("finviz_output 폴더", _FINVIZ_OUTPUT_DIR.exists(),
           f"{len(txt_files)}개 파일" if _FINVIZ_OUTPUT_DIR.exists() else "없음")

    # K어닝 파일
    _check("K어닝 분석.md", _K_EARNINGS_ANALYSIS.exists(),
           "(없으면 어닝콜 분석 스킵)" if not _K_EARNINGS_ANALYSIS.exists() else "")
    _check("K어닝 분석_today.md", _K_EARNINGS_TODAY.exists(),
           "(선택)" if not _K_EARNINGS_TODAY.exists() else "")
    _check("K어닝콜_output 폴더", _K_EARNINGS_CALL_OUTPUT.exists(),
           "(선택)" if not _K_EARNINGS_CALL_OUTPUT.exists() else "")

    # Obsidian 연결
    obsidian_ok = await _obsidian.ping()
    _check("Obsidian API", obsidian_ok)

    # 토큰·API 키
    _check("Slack 토큰", bool(cfg.SLACK_BOT_TOKEN))
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
