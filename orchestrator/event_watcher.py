"""
orchestrator/event_watcher.py
==============================
파일 감시 기반 자동 트리거 (watchfiles 라이브러리)

감시 대상 (스펙 FR-01):
  1. summary_*.json 신규 생성  → Buy Pipeline 자동 실행
  2. positions.md 변경         → Sell Pipeline 자동 실행
  3. 어닝콜_output/ 신규 파일  → 어닝 분석 자동 실행
  4. watchlist.md 변경         → 데이터 재수집 예약
  5. requeue.json status=ready → Requeue Pipeline 자동 실행

모든 이벤트에 debounce 30초 적용.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
from datetime import datetime
from pathlib import Path

from watchfiles import Change, awatch  # type: ignore

from orchestrator.engine import PipelineEngine
from shared.config import get_config
from shared.logger import get_logger

cfg = get_config()
log = get_logger()

# Debounce 상태 (파일 → 마지막 트리거 시각)
_last_triggered: dict[str, float] = {}
_DEBOUNCE_SECONDS = cfg.WATCHER_DEBOUNCE_SECONDS


class EventWatcher:
    """
    파일 시스템 이벤트 감시 및 파이프라인 자동 트리거.

    watchfiles.awatch()를 사용하여 비동기로 파일 변경을 감지합니다.
    """

    def __init__(self, engine: PipelineEngine) -> None:
        self._engine = engine
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # 감시 경로 설정
        self._watch_paths = [
            Path(cfg.SUMMARY_DIR),        # summary_*.json
            Path(cfg.POSITIONS_FILE).parent,  # positions.md, watchlist.md
            Path(cfg.EARNINGS_DIR),        # 어닝콜_output/
            Path(cfg.REQUEUE_FILE).parent, # requeue.json
        ]

    async def start(self) -> None:
        """이벤트 감시 시작 (무한 루프)"""
        self._running = True
        log.info("event_watcher_start", paths=[str(p) for p in self._watch_paths])

        # 존재하는 경로만 감시
        existing_paths = [str(p) for p in self._watch_paths if p.exists()]
        if not existing_paths:
            log.warning("no_watch_paths_exist", paths=[str(p) for p in self._watch_paths])
            # 로컬 테스트 폴백: 현재 디렉토리 감시
            existing_paths = ["."]

        try:
            async for changes in awatch(*existing_paths, stop_event=self._stop_event()):
                for change_type, changed_path in changes:
                    await self._dispatch(change_type, Path(changed_path))
        except Exception as exc:
            log.error("event_watcher_error", error=str(exc))
            raise

    def stop(self) -> None:
        """감시 종료"""
        self._running = False
        log.info("event_watcher_stopped")

    def _stop_event(self):
        """awatch 종료용 asyncio.Event"""
        stop = asyncio.Event()

        def _handler(signum, frame):
            log.info("signal_received", signal=signum)
            stop.set()

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
        return stop

    async def _dispatch(self, change_type: Change, path: Path) -> None:
        """
        파일 변경 이벤트 → 파이프라인 트리거

        Args:
            change_type: Change.added | Change.modified | Change.deleted
            path: 변경된 파일 경로
        """
        path_str = str(path)
        now = asyncio.get_running_loop().time()

        # Debounce 확인
        last = _last_triggered.get(path_str, 0)
        if now - last < _DEBOUNCE_SECONDS:
            log.info("event_debounced", path=path.name, remaining=_DEBOUNCE_SECONDS - (now - last))
            return

        _last_triggered[path_str] = now
        # 완료된 태스크 정리 (메모리 누수 방지)
        self._tasks = [t for t in self._tasks if not t.done()]
        log.info("event_detected", change=change_type.name, path=path.name)

        # ── 트리거 1: summary_*.json 신규 생성 → Buy Pipeline ──
        if (change_type == Change.added
                and re.match(r"summary_\d{8}_\d{6}\.json", path.name)):
            log.info("trigger_buy_pipeline", file=path.name)
            task = asyncio.create_task(
                self._run_buy_pipeline(path),
                name=f"buy_{path.name}",
            )
            self._tasks.append(task)
            return

        # ── 트리거 2: positions.md 변경 → Sell Pipeline ──────
        if (change_type in (Change.modified, Change.added)
                and path.name == "positions.md"):
            log.info("trigger_sell_pipeline", file=path.name)
            task = asyncio.create_task(
                self._run_sell_pipeline(),
                name="sell_positions",
            )
            self._tasks.append(task)
            return

        # ── 트리거 3: K어닝콜_output/ 신규 파일 → 어닝 분석 (Kavout 기반만)
        if (change_type == Change.added
                and "K어닝콜_output" in path_str):
            log.info("trigger_earnings_analysis", file=path.name)
            task = asyncio.create_task(
                self._run_earnings_analysis(path),
                name=f"earnings_{path.name}",
            )
            self._tasks.append(task)
            return

        # ── 트리거 4: watchlist.md 변경 → 데이터 재수집 예약 ─
        if (change_type in (Change.modified, Change.added)
                and path.name == "watchlist.md"):
            log.info("trigger_watchlist_refresh", file=path.name)
            # 단순 로깅 (실제 재수집은 다음 Buy Pipeline에서)
            return

        # ── 트리거 5: requeue.json 변경 → status=ready 항목 있을 때만 실행 ─
        if (change_type == Change.modified and path.name == "requeue.json"):
            log.info("trigger_requeue_check")
            # §P3: status=ready 항목이 있을 때만 파이프라인 실행
            try:
                import json as _json
                data = _json.loads(path.read_text(encoding="utf-8"))
                items = data if isinstance(data, list) else data.get("items", [])
                has_ready = any(item.get("status") == "ready" for item in items)
            except Exception:
                has_ready = True  # 파싱 실패 시 안전하게 실행
            if not has_ready:
                log.info("trigger_requeue_skip_no_ready")
                return
            task = asyncio.create_task(
                self._run_requeue_pipeline(),
                name="requeue_check",
            )
            self._tasks.append(task)
            return

    # ─────────────────────────────────────────────────────────
    # 파이프라인 실행 래퍼
    # ─────────────────────────────────────────────────────────

    async def _run_buy_pipeline(self, summary_file: Path) -> None:
        """Buy Pipeline 실행 (summary 파일 기반)"""
        import uuid
        eid = f"buy_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        try:
            result = await self._engine.run_buy(execution_id=eid)
            log.info("buy_pipeline_completed",
                     execution_id=eid, status=result.status,
                     rankings=len(result.final_rankings))
        except Exception as exc:
            log.error("buy_pipeline_failed", execution_id=eid, error=str(exc))

    async def _run_sell_pipeline(self) -> None:
        """Sell Pipeline 실행"""
        import uuid
        eid = f"sell_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        try:
            result = await self._engine.run_sell(execution_id=eid)
            log.info("sell_pipeline_completed",
                     execution_id=eid, status=result.status,
                     decisions=len(result.sell_decisions))
        except Exception as exc:
            log.error("sell_pipeline_failed", execution_id=eid, error=str(exc))

    async def _run_requeue_pipeline(self) -> None:
        """Requeue Pipeline 실행"""
        import uuid
        eid = f"requeue_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        try:
            result = await self._engine.run_requeue(execution_id=eid)
            log.info("requeue_pipeline_completed",
                     execution_id=eid, status=result.status)
        except Exception as exc:
            log.error("requeue_pipeline_failed", execution_id=eid, error=str(exc))

    async def _run_earnings_analysis(self, earnings_file: Path) -> None:
        """
        어닝콜_output/ 신규 파일 감지 → 해당 종목 Buy Pipeline 트리거

        파일명 패턴: {TICKER}_{YYYYMMDD}.txt  (예: AMD_20260511.txt)
        티커를 추출해 target_tickers 로 Buy Pipeline 재실행.
        """
        log.info("earnings_file_detected", file=earnings_file.name)

        # 파일명에서 티커 추출 (대문자 1~5자 + 언더스코어)
        m = re.match(r"^([A-Z]{1,5})_", earnings_file.name)
        if not m:
            log.warning("earnings_ticker_not_found", file=earnings_file.name)
            return

        ticker = m.group(1)
        eid = f"buy_{datetime.now().strftime('%Y%m%d_%H%M%S')}_earnings_{ticker}"
        log.info("trigger_earnings_buy_pipeline", ticker=ticker, execution_id=eid)

        try:
            result = await self._engine.run_buy(
                execution_id=eid,
                target_tickers=[ticker],
            )
            log.info(
                "earnings_buy_pipeline_completed",
                ticker=ticker,
                execution_id=eid,
                status=result.status,
            )
        except Exception as exc:
            log.error("earnings_buy_pipeline_failed",
                      ticker=ticker, execution_id=eid, error=str(exc))


async def main() -> None:
    """EventWatcher 단독 실행 진입점"""
    log.info("swing_mcp_event_watcher_starting")
    engine = PipelineEngine()
    watcher = EventWatcher(engine)

    try:
        await watcher.start()
    except KeyboardInterrupt:
        log.info("event_watcher_shutdown")
    finally:
        # 미완료 태스크 정리
        for task in watcher._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass


if __name__ == "__main__":
    asyncio.run(main())
