"""
shared/logger.py
================
structlog 기반 구조화 로깅

- JSON 형식 출력 (audit 로그 포함)
- execution_id, step, ticker 컨텍스트 자동 바인딩
- 파일 + 콘솔 동시 출력
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from shared.config import get_config


def _json_serializer(obj: Any, **kw: Any) -> str:
    """datetime 등 JSON 직렬화 처리"""
    def default(o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, Path):
            return str(o)
        raise TypeError(f"Object of type {type(o)} is not JSON serializable")
    # structlog may pass its own ``default`` kwarg; ensure we don't supply it twice
    kw.pop("default", None)
    return json.dumps(obj, default=default, ensure_ascii=False, **kw)


def _add_timestamp(logger: Any, method: str, event_dict: dict) -> dict:
    """타임스탬프 추가 프로세서"""
    event_dict["timestamp"] = datetime.now(timezone.utc).isoformat()
    event_dict["server"] = "swing-mcp"
    return event_dict


def setup_logging(execution_id: str | None = None) -> structlog.BoundLogger:
    """
    structlog 초기화 및 파일 핸들러 설정

    Args:
        execution_id: 실행 ID (로그 파일명에 사용)

    Returns:
        바운드 로거 인스턴스
    """
    cfg = get_config()
    log_dir = Path(cfg.LOGS_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    # 표준 라이브러리 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 기존 stdout 핸들러 제거 (mcp 라이브러리 등이 미리 등록했을 경우 대비)
    root_logger.handlers = [
        h for h in root_logger.handlers
        if not (isinstance(h, logging.StreamHandler) and
                getattr(h, 'stream', None) is sys.stdout)
    ]

    # 콘솔 핸들러 — MCP stdio 모드에서 stdout은 JSON-RPC 전용이므로 반드시 stderr 사용
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)

    # 실행별 파일 핸들러
    if execution_id:
        log_file = log_dir / f"{execution_id}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(file_handler)

    root_logger.addHandler(console_handler)

    # structlog 프로세서 체인
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            _add_timestamp,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(serializer=_json_serializer),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logger = structlog.get_logger()

    if execution_id:
        logger = logger.bind(execution_id=execution_id)

    return logger


def get_logger(
    execution_id: str = "",
    step: int | None = None,
    ticker: str | None = None,
) -> structlog.BoundLogger:
    """
    컨텍스트 바운드 로거 반환

    Args:
        execution_id: 실행 ID
        step: 현재 파이프라인 단계
        ticker: 현재 처리 중인 종목

    Returns:
        컨텍스트가 바인딩된 structlog 로거
    """
    logger = structlog.get_logger()
    ctx: dict[str, Any] = {}
    if execution_id:
        ctx["execution_id"] = execution_id
    if step is not None:
        ctx["step"] = step
    if ticker:
        ctx["ticker"] = ticker
    return logger.bind(**ctx)


def write_audit_record(
    execution_id: str,
    step: int,
    status: str,
    duration_ms: int = 0,
    ticker: str | None = None,
    error: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """
    감사 로그 파일에 불변 레코드 추가 (append-only)

    Args:
        execution_id: 실행 ID
        step: 파이프라인 단계
        status: started | completed | degraded | failed | skipped
        duration_ms: 소요 시간 (밀리초)
        ticker: 관련 종목
        error: 에러 메시지
        data: 추가 데이터
    """
    cfg = get_config()
    audit_file = Path(cfg.LOGS_DIR) / f"audit_{datetime.now().strftime('%Y-%m-%d')}.json"

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "execution_id": execution_id,
        "step": step,
        "status": status,
        "duration_ms": duration_ms,
    }
    if ticker:
        record["ticker"] = ticker
    if error:
        record["error"] = error
    if data:
        record["data"] = data

    # append-only: 파일에 한 줄씩 추가 (JSON Lines 형식)
    with open(audit_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
