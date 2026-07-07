"""
servers/longterm_mcp/server.py
==============================
LongtermMCP — 장기투자 보고서 생성 MCP 서버

노출 Tool (3개):
  1. run_longterm_pipeline  - 전체 파이프라인 실행 → 보고서 생성
  2. get_longterm_report    - 기존 보고서 Obsidian에서 읽어 반환
  3. health_check           - 연결 상태 확인

stdio 프로토콜로 Claude Desktop / Roo Code에 연결됩니다.
"""

from __future__ import annotations

# ── SSL CA bundle ASCII 경로 확보 (curl_cffi 로드 전에 반드시 실행) ─────────
import os as _os, shutil as _sh, certifi as _certifi_ssl
_ca_raw = _certifi_ssl.where()
try:
    _ca_raw.encode('ascii')
    _ca_ascii = _ca_raw
except UnicodeEncodeError:
    from pathlib import Path as _Path
    _cache_dir = _Path(__file__).resolve().parents[2] / 'cache'
    _cache_dir.mkdir(exist_ok=True)
    _ca_ascii = str(_cache_dir / 'cacert.pem')
    _sh.copy2(_ca_raw, _ca_ascii)
for _ev in ('SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'REQUESTS_CA_BUNDLE'):
    _os.environ[_ev] = _ca_ascii
del _ca_raw, _ca_ascii, _ev, _os, _sh, _certifi_ssl
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import logging as _logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

# 프로젝트 루트를 Python 경로에 추가
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

import mcp.server.stdio  # type: ignore
import mcp.types as types  # type: ignore
from mcp.server import Server  # type: ignore

from shared.config import get_config
from shared.logger import get_logger, setup_logging

# ── MCP stdio 보호: stdout은 JSON-RPC 전용 ──────────────────
_logging.root.handlers.clear()
_stderr_handler = _logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(_logging.WARNING)
_logging.root.addHandler(_stderr_handler)
_logging.root.setLevel(_logging.WARNING)
_logging.getLogger("mcp").setLevel(_logging.WARNING)
_logging.getLogger("asyncio").setLevel(_logging.WARNING)

cfg = get_config()
log = setup_logging()

server = Server("longterm-mcp")


# ─────────────────────────────────────────────────────────────
# Tool 정의
# ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="run_longterm_pipeline",
            description=(
                "장기투자 분석 파이프라인을 실행합니다. "
                "yfinance로 5년 재무 데이터를 수집하고, LLM으로 Moat·경영진·산업·리스크를 분석한 뒤 "
                "9개 섹션 보고서를 Obsidian에 저장합니다."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "종목 심볼 (예: AAPL, MSFT, NVDA)",
                    },
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_longterm_report",
            description=(
                "Obsidian에 저장된 장기투자 보고서를 읽어 반환합니다. "
                "보고서가 없으면 run_longterm_pipeline 실행을 안내합니다."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "종목 심볼",
                    },
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="health_check",
            description="yfinance 연결, Obsidian REST API, LLM API 키 유효성을 확인합니다.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


# ─────────────────────────────────────────────────────────────
# 파이프라인 오케스트레이터
# ─────────────────────────────────────────────────────────────

async def _run_pipeline(ticker: str) -> dict[str, Any]:
    """전체 파이프라인 실행 후 결과 요약 반환."""
    from shared.longterm_schemas import LongtermInput, LongtermReport
    from core.longterm_fetcher import fetch_all
    from core.longterm_analyzer import analyze_all
    from core.longterm_scorer import score
    from core.longterm_report import build_and_save

    ticker = ticker.upper().strip()
    log.info("longterm_pipeline_start ticker=%s", ticker)

    inp = LongtermInput(ticker=ticker)

    # Step 1: 정량 데이터 수집
    log.info("longterm_step1_fetch ticker=%s", ticker)
    data = await fetch_all(inp)

    company_info = data["company_info"]
    financial    = data["financial"]
    quality      = data["quality"]
    balance      = data["balance_sheet"]
    sr           = data["shareholder_return"]
    valuation    = data["valuation"]
    market_sig   = data["market_signals"]

    # Step 2: LLM 정성 분석 (Phase 1~4)
    log.info("longterm_step2_analyze ticker=%s", ticker)
    moat, management, industry, risks, qualitative = await analyze_all(
        inp, quality, valuation, market_sig, company_info
    )

    # Step 3: 스코어링
    log.info("longterm_step3_score ticker=%s", ticker)
    scores, verdict = score(quality, balance, valuation, moat, management, industry, risks, market_sig)

    # Step 4: 보고서 조립
    report = LongtermReport(
        input=inp,
        financial=financial,
        quality=quality,
        balance_sheet=balance,
        shareholder_return=sr,
        valuation=valuation,
        market_signals=market_sig,
        moat=moat,
        management=management,
        industry=industry,
        risks=risks,
        qualitative=qualitative,
        scores=scores,
        verdict=verdict,
        generated_at=date.today().isoformat(),
    )

    # Step 5: 렌더링 + 저장
    log.info("longterm_step5_save ticker=%s", ticker)
    path = await build_and_save(report)

    return {
        "ticker":        ticker,
        "company":       inp.company_name,
        "total_score":   scores.total,
        "opinion":       verdict.opinion,
        "target_price":  verdict.target_price,
        "target_upside": verdict.target_upside_pct,
        "obsidian_path": path,
    }


# ─────────────────────────────────────────────────────────────
# Tool 핸들러
# ─────────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:

    if name == "run_longterm_pipeline":
        ticker = str(arguments.get("ticker", "")).upper().strip()
        if not ticker:
            return [types.TextContent(type="text", text="오류: ticker를 입력하세요.")]

        try:
            result = await _run_pipeline(ticker)
            text = (
                f"✅ 장기투자 보고서 생성 완료\n\n"
                f"종목: {result['ticker']} ({result['company']})\n"
                f"종합 점수: {result['total_score']}/100\n"
                f"투자 의견: {result['opinion']}\n"
                f"목표 가격: ${result['target_price']:.2f}"
                f"  ({result['target_upside']:+.1f}%)\n"
                f"Obsidian: {result['obsidian_path']}"
            )
        except Exception as e:
            log.exception("pipeline_error ticker=%s", ticker)
            text = f"❌ 파이프라인 실행 오류: {e}"

        return [types.TextContent(type="text", text=text)]

    elif name == "get_longterm_report":
        ticker = str(arguments.get("ticker", "")).upper().strip()
        if not ticker:
            return [types.TextContent(type="text", text="오류: ticker를 입력하세요.")]

        # 가장 최근 보고서 검색 (날짜 미지정 → 오늘 우선, 없으면 안내)
        try:
            from core.obsidian import ObsidianClient
            obs = ObsidianClient()
            today = date.today().isoformat()
            path = cfg.LT_NOTE_PATH_TEMPLATE.format(ticker=ticker, date=today)
            content = await obs.read_note(path)
            if content:
                return [types.TextContent(type="text", text=content)]
        except Exception:
            pass

        return [types.TextContent(
            type="text",
            text=(
                f"{ticker} 보고서를 찾을 수 없습니다.\n"
                f"run_longterm_pipeline 도구로 먼저 보고서를 생성해주세요."
            ),
        )]

    elif name == "health_check":
        results: list[str] = []

        # yfinance
        try:
            import yfinance as yf
            info = yf.Ticker("AAPL").fast_info
            price = info.get("last_price")
            results.append(f"✅ yfinance: AAPL ${price:.2f}" if price else "⚠️ yfinance: 가격 없음")
        except Exception as e:
            results.append(f"❌ yfinance: {e}")

        # Obsidian
        try:
            from core.obsidian import ObsidianClient
            obs = ObsidianClient()
            await obs.ping()
            results.append("✅ Obsidian REST API: 연결됨")
        except Exception as e:
            results.append(f"❌ Obsidian: {e}")

        # LLM API 키
        if cfg.OPENROUTER_API_KEY:
            results.append("✅ OpenRouter API 키: 설정됨")
        else:
            results.append("⚠️ OpenRouter API 키: 미설정 (claude_cli 모드 확인 필요)")

        # Brave API 키
        if cfg.BRAVE_API_KEY:
            results.append("✅ Brave Search API 키: 설정됨")
        else:
            results.append("⚠️ Brave Search API 키: 미설정 (DDG 폴백 사용)")

        return [types.TextContent(type="text", text="\n".join(results))]

    return [types.TextContent(type="text", text=f"알 수 없는 도구: {name}")]


# ─────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────

async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
