"""
core/state.py
=============
상태 관리 통합 모듈 (T3: snapshot·cache·requeue·positions → 1개)

담당:
- save_snapshot() / load_snapshot(): 파이프라인 단계별 스냅샷 (idempotency)
- append_audit(): 감사 로그 불변 기록
- requeue_add() / requeue_list() / requeue_check_ready(): Requeue 관리
- load_positions_state() / save_positions_state(): 포지션 상태 캐시
- cleanup_old_snapshots(): 보존 기간 초과 스냅샷 삭제
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from shared.config import get_config
from shared.logger import get_logger, write_audit_record
from shared.schemas import (
    AuditRecord,
    PipelineResult,
    Position,
    RequeueItem,
    RequeueThreshold,
)

log = get_logger()
cfg = get_config()


# ─────────────────────────────────────────────────────────────
# 1. 스냅샷 관리 (Idempotency 핵심)
# ─────────────────────────────────────────────────────────────

def _snapshot_dir(execution_id: str) -> Path:
    """실행별 스냅샷 디렉토리 경로"""
    d = Path(cfg.SNAPSHOTS_DIR) / execution_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_snapshot(
    execution_id: str,
    step: int,
    data: Any,
    duration_ms: int = 0,
) -> None:
    """
    단계 완료 스냅샷 저장

    파일명: shared/state/snapshots/{execution_id}/step_{step}.json

    Args:
        execution_id: 실행 ID
        step: 파이프라인 단계 번호
        data: 저장할 데이터 (JSON 직렬화 가능)
        duration_ms: 단계 소요 시간
    """
    snap_dir = _snapshot_dir(execution_id)
    snap_file = snap_dir / f"step_{step}.json"

    payload = {
        "execution_id": execution_id,
        "step": step,
        "completed_at": datetime.now().isoformat(),
        "duration_ms": duration_ms,
        "data": _serialize(data),
    }

    snap_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("snapshot_saved", execution_id=execution_id, step=step)


def load_snapshot(execution_id: str) -> set[int]:
    """
    완료된 단계 번호 집합 반환 (idempotency 체크)

    Args:
        execution_id: 실행 ID

    Returns:
        완료된 step 번호 집합 (예: {0, 1, 2, 5})
    """
    snap_dir = Path(cfg.SNAPSHOTS_DIR) / execution_id
    if not snap_dir.exists():
        return set()

    completed: set[int] = set()
    for f in snap_dir.glob("step_*.json"):
        try:
            step_num = int(f.stem.split("_")[1])
            completed.add(step_num)
        except (IndexError, ValueError):
            pass

    log.info("snapshot_loaded", execution_id=execution_id, completed=sorted(completed))
    return completed


def load_step_data(execution_id: str, step: int) -> dict | None:
    """
    특정 단계의 스냅샷 데이터 로드

    Args:
        execution_id: 실행 ID
        step: 단계 번호

    Returns:
        저장된 데이터 딕셔너리 또는 None
    """
    snap_file = Path(cfg.SNAPSHOTS_DIR) / execution_id / f"step_{step}.json"
    if not snap_file.exists():
        return None
    try:
        payload = json.loads(snap_file.read_text(encoding="utf-8"))
        return payload.get("data")
    except Exception as exc:
        log.warning("snapshot_load_error", step=step, error=str(exc))
        return None


def cleanup_old_snapshots(retention_days: int | None = None) -> int:
    """
    보존 기간 초과 스냅샷 삭제

    Args:
        retention_days: 보존 일수 (None이면 config 사용)

    Returns:
        삭제된 디렉토리 수
    """
    days = retention_days or cfg.SNAPSHOT_RETENTION_DAYS
    cutoff = datetime.now() - timedelta(days=days)
    snap_root = Path(cfg.SNAPSHOTS_DIR)
    deleted = 0

    for exec_dir in snap_root.iterdir():
        if not exec_dir.is_dir():
            continue
        # 가장 오래된 파일의 수정 시간 확인
        files = list(exec_dir.glob("step_*.json"))
        if not files:
            exec_dir.rmdir()
            deleted += 1
            continue
        oldest_mtime = min(f.stat().st_mtime for f in files)
        oldest_dt = datetime.fromtimestamp(oldest_mtime)
        if oldest_dt < cutoff:
            for f in files:
                f.unlink(missing_ok=True)
            exec_dir.rmdir()
            deleted += 1

    log.info("snapshots_cleaned", deleted=deleted, retention_days=days)
    return deleted


# ─────────────────────────────────────────────────────────────
# 2. 감사 로그 (불변 Append-only)
# ─────────────────────────────────────────────────────────────

def append_audit(
    execution_id: str,
    step: int,
    status: str,
    error: str | None = None,
    ticker: str | None = None,
    duration_ms: int = 0,
    data: dict[str, Any] | None = None,
) -> None:
    """
    감사 로그 파일에 불변 레코드 추가 (E400 이상 에러 코드 포함)

    Args:
        execution_id: 실행 ID
        step: 파이프라인 단계
        status: started | completed | degraded | failed | skipped
        error: 에러 메시지 (있는 경우)
        ticker: 관련 종목
        duration_ms: 소요 시간
        data: 추가 데이터
    """
    write_audit_record(
        execution_id=execution_id,
        step=step,
        status=status,
        duration_ms=duration_ms,
        ticker=ticker,
        error=error,
        data=data or {},
    )


# ─────────────────────────────────────────────────────────────
# 3. Requeue 관리
# ─────────────────────────────────────────────────────────────

def _load_requeue() -> list[dict]:
    """requeue.json 로드 (없으면 빈 리스트)"""
    requeue_file = Path(cfg.REQUEUE_FILE)
    if not requeue_file.exists():
        return []
    try:
        return json.loads(requeue_file.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_requeue(items: list[dict]) -> None:
    """requeue.json 저장"""
    requeue_file = Path(cfg.REQUEUE_FILE)
    requeue_file.parent.mkdir(parents=True, exist_ok=True)
    requeue_file.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def requeue_add(
    ticker: str,
    failed_filters: list[str],
    threshold: dict,
    failure_reasons: list[str] | None = None,
) -> RequeueItem:
    """
    탈락 종목을 Requeue에 등록

    Args:
        ticker: 종목 심볼
        failed_filters: 탈락 필터 코드 목록
        threshold: 진입 조건 딕셔너리
        failure_reasons: 탈락 상세 이유

    Returns:
        등록된 RequeueItem
    """
    items = _load_requeue()

    # 중복 확인 (waiting 상태인 동일 종목은 업데이트)
    existing_idx = next(
        (i for i, item in enumerate(items)
         if item["ticker"] == ticker and item["status"] == "waiting"),
        None,
    )

    threshold_obj = RequeueThreshold(**threshold)
    new_item = RequeueItem(
        ticker=ticker,
        registered_at=datetime.now(),
        failed_filters=failed_filters,
        failure_reasons=failure_reasons or [],
        threshold=threshold_obj,
        status="waiting",
    )

    if existing_idx is not None:
        items[existing_idx] = _serialize(new_item)
        log.info("requeue_updated", ticker=ticker, filters=failed_filters)
    else:
        items.append(_serialize(new_item))
        log.info("requeue_added", ticker=ticker, filters=failed_filters)

    _save_requeue(items)
    return new_item


def requeue_list(status: str | None = None) -> list[RequeueItem]:
    """
    Requeue 목록 조회

    Args:
        status: 필터링할 상태 (None이면 전체)

    Returns:
        RequeueItem 리스트
    """
    items = _load_requeue()
    result: list[RequeueItem] = []

    for raw in items:
        try:
            # added_date는 이전 버전 호환 키 (registered_at으로 통합)
            registered = raw.get("registered_at") or raw.get("added_date", datetime.now().isoformat())
            item = RequeueItem(
                ticker=raw["ticker"],
                registered_at=datetime.fromisoformat(registered),
                failed_filters=raw.get("failed_filters", []),
                failure_reasons=raw.get("failure_reasons", []),
                threshold=RequeueThreshold(**raw.get("threshold", {})),
                status=raw.get("status", "waiting"),  # type: ignore
            )
            if status is None or item.status == status:
                result.append(item)
        except Exception as exc:
            log.warning("requeue_parse_error", error=str(exc))

    return result


def requeue_check_ready(summary_data: Any) -> list[str]:
    """
    Requeue 항목 중 threshold 조건 충족 여부 확인 → ready 전환

    Args:
        summary_data: 현재 SummaryData

    Returns:
        ready 상태로 전환된 종목 리스트
    """
    items = _load_requeue()
    ready_tickers: list[str] = []
    updated = False

    for item in items:
        if item.get("status") != "waiting":
            continue
        ticker = item["ticker"]
        threshold = item.get("threshold", {})

        ticker_data = summary_data.tickers.get(ticker) if summary_data else None
        if not ticker_data:
            continue

        is_ready = True

        # IVR 조건 (F1_RVOL_LOW 등 구형 requeue 항목 하위호환)
        ivr_max = threshold.get("ivr_max")
        opt_data = summary_data.options.get(ticker) if summary_data else None
        if ivr_max and opt_data:
            chain = opt_data.chain
            current_ivr = chain[0].get("ivr", 100) if chain else 100
            if current_ivr > ivr_max:
                is_ready = False

        # 주가 회복 조건 (F3_LIQUIDITY_LOW: price < PRICE_TRADE_MIN 탈락)
        # 주가가 기준선을 회복해야 재진입 가능
        price_min = threshold.get("price_min")
        if price_min and ticker_data:
            current_price = ticker_data.technical.price
            if current_price < price_min:
                is_ready = False

        # 시총 회복 조건 — summary_data에 market_cap 없으면 주가로 대리 판단
        # (market_cap ∝ price, 가격 회복으로 커버)
        # market_cap_min은 등록 시 기록용으로만 사용, 실시간 체크는 price_min으로 대체

        # RVOL 조건 (F1_RVOL_LOW)
        rvol_min = threshold.get("rvol_min")
        if rvol_min and ticker_data:
            rvol = ticker_data.technical.avg_volume_ratio
            if rvol < rvol_min:
                is_ready = False

        if is_ready:
            item["status"] = "ready"
            item["ready_date"] = datetime.now().isoformat()
            ready_tickers.append(ticker)
            updated = True
            log.info("requeue_ready", ticker=ticker)

    if updated:
        _save_requeue(items)

    return ready_tickers


def requeue_mark_processed(ticker: str) -> None:
    """Requeue 항목 처리 완료 표시"""
    items = _load_requeue()
    for item in items:
        if item["ticker"] == ticker and item["status"] in ("ready", "waiting"):
            item["status"] = "processed"
    _save_requeue(items)


# ─────────────────────────────────────────────────────────────
# 4. 포지션 상태 캐시
# ─────────────────────────────────────────────────────────────

def _positions_state_path() -> Path:
    return Path(cfg.SNAPSHOTS_DIR).parent / "positions_state.json"


def load_positions_state() -> dict[str, Any]:
    """
    포지션 상태 캐시 로드

    Returns:
        포지션 상태 딕셔너리
    """
    path = _positions_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_positions_state(positions: list[Position]) -> None:
    """
    포지션 상태 캐시 저장

    Args:
        positions: 현재 포지션 리스트
    """
    path = _positions_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    state = {
        "updated_at": datetime.now().isoformat(),
        "positions": [_serialize(p) for p in positions],
    }
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("positions_state_saved", count=len(positions))


def apply_partial_exit(
    positions: list[Position],
    ticker: str,
    contracts_to_close: int,
    exit_premium: float,
    reason: str = "",
) -> tuple[list[Position], float]:
    """
    부분 청산 처리 후 포지션 리스트 반환

    Args:
        positions: 현재 포지션 리스트
        ticker: 청산할 종목
        contracts_to_close: 청산 계약 수
        exit_premium: 청산 프리미엄
        reason: 청산 이유

    Returns:
        (업데이트된 포지션 리스트, 실현 손익)
    """
    from shared.schemas import PartialExit

    realized_pnl = 0.0
    updated: list[Position] = []

    for pos in positions:
        if pos.ticker == ticker and pos.remaining_contracts > 0:
            close_cnt = min(contracts_to_close, pos.remaining_contracts)
            pnl = (exit_premium - pos.entry_premium) * 100 * close_cnt - \
                  close_cnt * cfg.COMMISSION_PER_CONTRACT
            realized_pnl += pnl

            exit_record = PartialExit(
                exit_date=date.today(),
                contracts_closed=close_cnt,
                exit_premium=exit_premium,
                realized_pnl=pnl,
                reason=reason,
            )
            # trailing_stop 재설정 (고점 대비 20% 하락)
            new_stop = exit_premium * (1 - cfg.TRAILING_STOP_PCT / 100)

            updated_pos = pos.model_copy(update={
                "remaining_contracts": pos.remaining_contracts - close_cnt,
                "partial_exits": [*pos.partial_exits, exit_record],
                "trailing_stop": new_stop,
                "last_reviewed": date.today(),
            })
            updated.append(updated_pos)
            log.info("partial_exit_applied", ticker=ticker, closed=close_cnt, pnl=round(pnl, 2))
        else:
            updated.append(pos)

    return updated, realized_pnl


# ─────────────────────────────────────────────────────────────
# 5. 파이프라인 결과 저장
# ─────────────────────────────────────────────────────────────

def save_pipeline_result(result: PipelineResult) -> None:
    """파이프라인 최종 결과를 스냅샷 디렉토리에 저장"""
    snap_dir = _snapshot_dir(result.execution_id)
    result_file = snap_dir / "result.json"
    result_file.write_text(
        json.dumps(_serialize(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("pipeline_result_saved", execution_id=result.execution_id, status=result.status)


def load_pipeline_result(execution_id: str) -> dict | None:
    """저장된 파이프라인 결과 로드"""
    result_file = Path(cfg.SNAPSHOTS_DIR) / execution_id / "result.json"
    if not result_file.exists():
        return None
    return json.loads(result_file.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────
# 6. JSON 직렬화 헬퍼
# ─────────────────────────────────────────────────────────────

def _serialize(obj: Any) -> Any:
    """Pydantic 모델 및 datetime을 JSON 직렬화 가능한 형태로 변환"""
    from pydantic import BaseModel

    if isinstance(obj, BaseModel):
        return json.loads(obj.model_dump_json())
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(i) for i in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj
