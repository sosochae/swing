"""
servers/swing_mcp/server.py
============================
SwingMCP 단일 MCP 서버 (T2 최적화: tools/ 8개 파일 → server.py 내장)

10개 MCP Tool 노출:
  1. run_buy_pipeline      - Buy Pipeline 실행
  2. run_sell_pipeline     - Sell Pipeline 실행
  3. nl_query              - 자연어 명령 라우팅
  4. requeue_add           - Requeue 등록
  5. requeue_list          - Requeue 목록 조회
  6. partial_exit_apply    - 부분 청산 처리
  7. position_status       - 포지션 현황 조회
  8. step_execute          - 단일 단계 수동 실행
  (+2 추가)
  9. health_check          - 시스템 헬스 체크
  10. cache_clear          - 캐시 삭제

stdio 프로토콜로 Roo Code / Claude Desktop에 연결됩니다.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

# 프로젝트 루트를 Python 경로에 추가
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

import mcp.server.stdio  # type: ignore
import mcp.types as types  # type: ignore
from mcp.server import Server  # type: ignore

from orchestrator.engine import PipelineEngine
from shared.config import get_config
from shared.logger import get_logger, setup_logging

# ── MCP stdio 보호: stdout은 JSON-RPC 전용 ──────────────────
# Python root 로거 및 mcp 라이브러리 로거의 출력을 모두 stderr로 강제
import logging as _logging
_logging.root.handlers.clear()                              # 기존 stdout 핸들러 제거
_stderr_handler = _logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(_logging.WARNING)                  # WARNING 이상만 stderr에 표시
_logging.root.addHandler(_stderr_handler)
_logging.root.setLevel(_logging.WARNING)
_logging.getLogger("mcp").setLevel(_logging.WARNING)        # mcp 라이브러리 "Processing..." 억제
_logging.getLogger("asyncio").setLevel(_logging.WARNING)

cfg = get_config()
log = setup_logging()

# ── 싱글톤 엔진 ──────────────────────────────────────────────
_engine = PipelineEngine()

# ── MCP 서버 초기화 ──────────────────────────────────────────
server = Server("swing-mcp")


# ─────────────────────────────────────────────────────────────
# Tool 정의
# ─────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """등록된 MCP Tool 목록 반환"""
    return [
        types.Tool(
            name="run_buy_pipeline",
            description="Buy Pipeline 실행. execution_id는 idempotency key.",
            inputSchema={
                "type": "object",
                "properties": {
                    "execution_id": {
                        "type": "string",
                        "description": "실행 ID (None이면 자동 생성)"
                    },
                    "force_refresh": {
                        "type": "boolean",
                        "default": False,
                        "description": "True이면 캐시 무시"
                    },
                    "start_step": {
                        "type": "integer",
                        "default": 0,
                        "description": "시작 단계 (0~13)"
                    },
                    "target_tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "분석 대상 종목 리스트 (미지정 시 전체)"
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="run_sell_pipeline",
            description="Sell Pipeline 실행. positions.md 기반 보유 종목 분석.",
            inputSchema={
                "type": "object",
                "properties": {
                    "execution_id": {"type": "string"},
                    "force_refresh": {"type": "boolean", "default": False},
                    "start_step": {"type": "integer", "default": 0},
                    "target_tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="nl_query",
            description="자연어 명령 → 파이프라인 라우팅. 예: 'AMD 매수 분석해줘'",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "자연어 명령"
                    },
                    "context": {
                        "type": "object",
                        "description": "현재 컨텍스트 (포지션, 마지막 실행 등)"
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="requeue_add",
            description="탈락 종목을 Requeue에 등록",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "종목 심볼"},
                    "failed_filters": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "탈락 필터 코드 목록"
                    },
                    "threshold": {
                        "type": "object",
                        "description": "진입 조건 (예: {ivr_max: 50, rvol_min: 1.5})"
                    },
                },
                "required": ["ticker", "failed_filters", "threshold"],
            },
        ),
        types.Tool(
            name="requeue_list",
            description="Requeue 대기 종목 목록 조회",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["waiting", "ready", "processed"],
                        "description": "상태 필터 (미지정 시 전체)"
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="partial_exit_apply",
            description="포지션 부분 청산 처리 및 trailing stop 재설정",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "종목 심볼"},
                    "contracts_to_close": {
                        "type": "integer",
                        "description": "청산할 계약 수"
                    },
                    "exit_premium": {
                        "type": "number",
                        "description": "청산 프리미엄 (달러)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "청산 이유 (선택)"
                    },
                },
                "required": ["ticker", "contracts_to_close", "exit_premium"],
            },
        ),
        types.Tool(
            name="position_status",
            description="현재 포지션 현황 조회",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "특정 종목 조회 (미지정 시 전체)"
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="step_execute",
            description="파이프라인 특정 단계 수동 실행 (디버깅용)",
            inputSchema={
                "type": "object",
                "properties": {
                    "pipeline_type": {
                        "type": "string",
                        "enum": ["buy", "sell", "requeue"],
                        "description": "파이프라인 유형"
                    },
                    "step": {
                        "type": "integer",
                        "description": "단계 번호 (0~13)"
                    },
                    "execution_id": {
                        "type": "string",
                        "description": "기존 실행 ID"
                    },
                },
                "required": ["pipeline_type", "step", "execution_id"],
            },
        ),
        types.Tool(
            name="health_check",
            description="SwingMCP 시스템 헬스 체크 (Obsidian·Slack·LLM 연결 확인)",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="cache_clear",
            description="LLM 응답 캐시 삭제",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "특정 종목 캐시만 삭제 (미지정 시 만료 캐시 전체)"
                    },
                },
                "required": [],
            },
        ),
    ]


# ─────────────────────────────────────────────────────────────
# Tool 실행 핸들러
# ─────────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(
    name: str,
    arguments: dict[str, Any],
) -> list[types.TextContent]:
    """
    MCP Tool 호출 핸들러.

    모든 Tool은 JSON 직렬화 가능한 딕셔너리를 반환합니다.
    """
    log.info("tool_called", tool=name, args=list(arguments.keys()))

    try:
        result = await _dispatch(name, arguments)
        import json
        return [types.TextContent(
            type="text",
            text=json.dumps(result, ensure_ascii=False, indent=2, default=str),
        )]
    except Exception as exc:
        log.error("tool_error", tool=name, error=str(exc))
        import json
        return [types.TextContent(
            type="text",
            text=json.dumps(
                {"status": "error", "tool": name, "error": str(exc)},
                ensure_ascii=False,
            ),
        )]


async def _dispatch(name: str, args: dict) -> dict:
    """Tool 이름 → Engine 메서드 라우팅"""

    if name == "run_buy_pipeline":
        import uuid
        execution_id = args.get("execution_id") or f"buy_{uuid.uuid4().hex[:8]}"
        asyncio.create_task(_run_buy_background(
            execution_id=execution_id,
            force_refresh=args.get("force_refresh", False),
            start_step=args.get("start_step", 0),
            target_tickers=args.get("target_tickers"),
        ))
        tickers_info = f" ({', '.join(args['target_tickers'])})" if args.get("target_tickers") else " (전체 종목)"
        return {
            "status": "ok",
            "execution_id": execution_id,
            "message": f"✅ 매수 파이프라인 실행 완료{tickers_info}. 분석이 백그라운드에서 진행 중입니다. Slack으로 결과가 전송되고 Obsidian에 노트가 저장됩니다. 추가 작업 불필요.",
        }

    elif name == "run_sell_pipeline":
        import uuid
        execution_id = args.get("execution_id") or f"sell_{uuid.uuid4().hex[:8]}"
        asyncio.create_task(_run_sell_background(
            execution_id=execution_id,
            force_refresh=args.get("force_refresh", False),
            start_step=args.get("start_step", 0),
            target_tickers=args.get("target_tickers"),
        ))
        tickers_info = f" ({', '.join(args['target_tickers'])})" if args.get("target_tickers") else " (전체 포지션)"
        return {
            "status": "ok",
            "execution_id": execution_id,
            "message": f"✅ 매도 파이프라인 실행 완료{tickers_info}. 분석이 백그라운드에서 진행 중입니다. Slack으로 결과가 전송되고 Obsidian에 노트가 저장됩니다. 추가 작업 불필요.",
        }

    elif name == "nl_query":
        result = await _engine.route_nl(
            query=args["query"],
            context=args.get("context"),
        )
        return result.model_dump()

    elif name == "requeue_add":
        return await _engine.requeue_add(
            ticker=args["ticker"],
            failed_filters=args["failed_filters"],
            threshold=args.get("threshold", {}),
        )

    elif name == "requeue_list":
        return await _engine.requeue_list(
            status=args.get("status"),
        )

    elif name == "partial_exit_apply":
        return await _engine.partial_exit(
            ticker=args["ticker"],
            contracts_to_close=args["contracts_to_close"],
            exit_premium=args["exit_premium"],
            reason=args.get("reason"),
        )

    elif name == "position_status":
        return await _engine.position_status(
            ticker=args.get("ticker"),
        )

    elif name == "step_execute":
        return await _engine.step_execute(
            pipeline_type=args["pipeline_type"],
            step=args["step"],
            execution_id=args["execution_id"],
        )

    elif name == "health_check":
        return await _health_check()

    elif name == "cache_clear":
        from core.llm import clear_cache
        deleted = clear_cache(ticker=args.get("ticker"))
        return {"status": "ok", "deleted": deleted}

    else:
        raise ValueError(f"Unknown tool: {name}")


async def _run_buy_background(execution_id, force_refresh, start_step, target_tickers):
    """매수 파이프라인 백그라운드 실행"""
    try:
        result = await _engine.run_buy(
            execution_id=execution_id,
            force_refresh=force_refresh,
            start_step=start_step,
            target_tickers=target_tickers,
        )
        log.info("buy_pipeline_completed", execution_id=execution_id, status=result.status)
    except Exception as exc:
        log.error("buy_pipeline_failed", execution_id=execution_id, error=str(exc))


async def _run_sell_background(execution_id, force_refresh, start_step, target_tickers):
    """매도 파이프라인 백그라운드 실행"""
    try:
        result = await _engine.run_sell(
            execution_id=execution_id,
            force_refresh=force_refresh,
            start_step=start_step,
            target_tickers=target_tickers,
        )
        log.info("sell_pipeline_completed", execution_id=execution_id, status=result.status)
    except Exception as exc:
        log.error("sell_pipeline_failed", execution_id=execution_id, error=str(exc))


async def _health_check() -> dict:
    """시스템 헬스 체크"""
    from pathlib import Path

    checks: dict[str, Any] = {}

    # Obsidian 연결
    obsidian_ok = await _engine._obsidian.ping()
    checks["obsidian"] = "ok" if obsidian_ok else "error"

    # 필수 환경 변수
    missing = cfg.validate()
    checks["env_vars"] = "ok" if not missing else f"missing: {missing}"

    # 파일 경로 존재 여부
    checks["summary_dir"] = "ok" if Path(cfg.SUMMARY_DIR).exists() else "not_found"
    checks["finviz_file"] = "ok" if Path(cfg.FINVIZ_FILE).exists() else "not_found"

    # 캐시 통계
    from core.llm import get_cache
    cache_dir = Path(cfg.CACHE_DIR)
    cache_count = len(list(cache_dir.glob("*.json"))) if cache_dir.exists() else 0
    checks["cache_files"] = cache_count

    # 스냅샷 통계
    snap_dir = Path(cfg.SNAPSHOTS_DIR)
    snap_count = len(list(snap_dir.iterdir())) if snap_dir.exists() else 0
    checks["snapshot_dirs"] = snap_count

    overall = "healthy" if checks["obsidian"] == "ok" else "degraded"
    return {"status": overall, "checks": checks}


def _pipeline_result_to_dict(result) -> dict:
    """PipelineResult → JSON 직렬화 가능 딕셔너리"""
    return {
        "execution_id": result.execution_id,
        "status": result.status,
        "pipeline_type": result.pipeline_type,
        "completed_steps": result.completed_steps,
        "failed_steps": result.failed_steps,
        "market_regime": result.market_regime,
        "duration_seconds": result.duration_seconds,
        "final_rankings": [
            {
                "rank": r.rank,
                "ticker": r.ticker,
                "action": r.action,
                "direction": r.direction,
                "final_score": r.final_score,
                "conviction_level": r.conviction.level,
                "capital_allocation": r.capital_allocation,
                "contracts": r.contracts,
                "strike": r.strike,
                "expiry": str(r.expiry),
                "rationale": r.rationale,
                "expected_value": r.scenario.expected_value if r.scenario else 0,
            }
            for r in result.final_rankings
        ],
        "sell_decisions": [
            {
                "ticker": d.ticker,
                "action": d.action,
                "contracts_to_close": d.contracts_to_close,
                "realized_pnl": d.realized_pnl,
                "unrealized_pnl": d.unrealized_pnl,
                "rationale": d.rationale,
                "urgency": d.urgency,
            }
            for d in result.sell_decisions
        ],
        "summary": result.summary,
    }


# ─────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────

async def main() -> None:
    """MCP 서버 stdio 모드로 실행"""
    log.info("swing_mcp_server_starting", version="2.0.0")

    missing = cfg.validate()
    if missing:
        log.warning("env_vars_missing", missing=missing)

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
