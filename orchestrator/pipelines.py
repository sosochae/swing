"""
orchestrator/pipelines.py
==========================
파이프라인 통합 모듈 (T6: buy_pipeline·sell_pipeline·requeue_pipeline → 1개)

BasePipeline: idempotency·스냅샷·감사 로그·Graceful Degradation 공통 처리
BuyPipeline : Step 0~13 매수 파이프라인
SellPipeline: Step 0~13 매도 파이프라인
RequeuePipeline: Step 0~4 Requeue 파이프라인 (간소화)
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from core.state import (
    append_audit,
    load_snapshot,
    requeue_check_ready,
    save_pipeline_result,
    save_snapshot,
)
from shared.logger import get_logger
from shared.schemas import PipelineContext, PipelineResult

if TYPE_CHECKING:
    from core.obsidian import ObsidianClient
    from core.slack import SlackClient

log = get_logger()

# 치명적 오류로 파이프라인을 즉시 중단해야 하는 Step 집합
_BUY_FATAL_STEPS: frozenset[int] = frozenset({0, 1, 2})
_SELL_FATAL_STEPS: frozenset[int] = frozenset({0})


# ─────────────────────────────────────────────────────────────
# BasePipeline
# ─────────────────────────────────────────────────────────────

class BasePipeline(ABC):
    """
    파이프라인 공통 인프라.

    - idempotency: 완료된 단계는 자동 스킵
    - Graceful Degradation: 비치명적 오류는 기록 후 계속 진행
    - 감사 로그: append_audit()으로 불변 기록
    - 스냅샷: save_snapshot()으로 단계별 결과 저장
    """

    TOTAL_STEPS: int = 14  # Step 0~13

    def __init__(self, obsidian: "ObsidianClient", slack: "SlackClient") -> None:
        self.obsidian = obsidian
        self.slack = slack

    async def run(self, ctx: PipelineContext) -> PipelineResult:
        """
        파이프라인 실행 메인 루프

        Args:
            ctx: PipelineContext (execution_id, start_step 등 포함)

        Returns:
            PipelineResult (completed / partial / failed)
        """
        wall_start = time.monotonic()
        execution_id = ctx.execution_id

        log.info(
            "pipeline_start",
            execution_id=execution_id,
            pipeline=ctx.pipeline_type,
            start_step=ctx.start_step,
        )

        # 이미 완료된 단계 조회 (idempotency)
        completed_steps = load_snapshot(execution_id)

        # 슬랙 시작 알림
        try:
            await self.slack.send_pipeline_start(execution_id, ctx.pipeline_type)
        except Exception:
            pass  # 알림 실패는 비치명적

        pipeline_status = "completed"

        for step_num in range(ctx.start_step, self.TOTAL_STEPS):
            # 이미 완료된 단계 스킵
            if step_num in completed_steps:
                log.info("step_skipped_idempotent", step=step_num)
                ctx.completed_steps.append(step_num)
                continue

            append_audit(execution_id, step_num, "started")
            step_start = time.monotonic()

            try:
                await self._run_step(step_num, ctx)
                ctx.completed_steps.append(step_num)
                completed_steps.add(step_num)

            except RuntimeError as exc:
                # FATAL: 파이프라인 즉시 중단
                duration_ms = int((time.monotonic() - step_start) * 1000)
                err_msg = str(exc)
                ctx.errors[step_num] = err_msg
                append_audit(execution_id, step_num, "failed",
                             error=err_msg, duration_ms=duration_ms)
                log.error("pipeline_fatal", step=step_num, error=err_msg)

                try:
                    await self.slack.send_fatal_error(
                        execution_id, "E100", err_msg, step=step_num
                    )
                except Exception:
                    pass

                pipeline_status = "failed"
                break

            except Exception as exc:
                # DEGRADED: 비치명적 오류 — 기록 후 계속
                duration_ms = int((time.monotonic() - step_start) * 1000)
                err_msg = str(exc)

                if self._is_fatal(step_num, exc):
                    ctx.errors[step_num] = err_msg
                    append_audit(execution_id, step_num, "failed",
                                 error=err_msg, duration_ms=duration_ms)
                    try:
                        await self.slack.send_fatal_error(
                            execution_id, "E100", err_msg, step=step_num
                        )
                    except Exception:
                        pass
                    pipeline_status = "failed"
                    break

                ctx.errors[step_num] = err_msg
                append_audit(execution_id, step_num, "degraded",
                             error=err_msg, duration_ms=duration_ms)
                log.warning("step_degraded", step=step_num, error=err_msg)

                try:
                    await self.slack.send_step_degraded(execution_id, step_num, err_msg)
                except Exception:
                    pass

                # 스냅샷에 degraded 표시
                save_snapshot(execution_id, step_num,
                              {"status": "degraded", "error": err_msg},
                              duration_ms)
                pipeline_status = "partial"
                ctx.completed_steps.append(step_num)

        # 결과 생성
        total_duration = time.monotonic() - wall_start
        result = self._build_result(ctx, pipeline_status, total_duration)
        save_pipeline_result(result)

        log.info(
            "pipeline_done",
            execution_id=execution_id,
            status=pipeline_status,
            duration_s=round(total_duration, 1),
            steps_done=len(ctx.completed_steps),
        )
        return result

    @abstractmethod
    async def _run_step(self, step: int, ctx: PipelineContext) -> None:
        """하위 클래스에서 단계별 메서드 호출"""
        ...

    def _is_fatal(self, step: int, error: Exception) -> bool:
        """치명적 오류 여부 판단 (하위 클래스에서 오버라이드 가능)"""
        return step in _BUY_FATAL_STEPS

    def _build_result(
        self,
        ctx: PipelineContext,
        status: str,
        duration_s: float,
    ) -> PipelineResult:
        """PipelineResult 생성"""
        return PipelineResult(
            execution_id=ctx.execution_id,
            pipeline_type=ctx.pipeline_type,
            status=status,  # type: ignore
            completed_steps=list(ctx.completed_steps),
            failed_steps={k: v for k, v in ctx.errors.items()
                          if isinstance(k, int) and k >= 0},
            final_rankings=ctx.final_rankings,
            sell_decisions=ctx.sell_decisions,
            market_regime=ctx.regime.regime_status if ctx.regime else "",
            duration_seconds=round(duration_s, 1),
            summary={
                "filtered_tickers": ctx.filtered_tickers,
                "regime": ctx.regime.regime_status if ctx.regime else "",
            },
        )


# ─────────────────────────────────────────────────────────────
# BuyPipeline
# ─────────────────────────────────────────────────────────────

class BuyPipeline(BasePipeline):
    """
    매수 분석 파이프라인 (Step 0~13)

    BuySteps 클래스의 메서드를 순서대로 호출합니다.
    """

    STEP_MAP: dict[int, str] = {
        0:  "step_0_env",
        1:  "step_1_data",
        2:  "step_2_regime",
        3:  "step_3_filter",
        4:  "step_4_technical",
        5:  "step_5_research",
        6:  "step_6_devils",
        7:  "step_7_options",
        8:  "step_8_scenario",
        9:  "step_9_portfolio",
        10: "step_10_ranking",
        11: "step_11_requeue",
        12: "step_12_storage",
        13: "step_13_notify",
    }

    def __init__(self, obsidian: "ObsidianClient", slack: "SlackClient") -> None:
        super().__init__(obsidian, slack)
        from orchestrator.steps.buy_steps import BuySteps
        self._steps = BuySteps(obsidian=obsidian, slack=slack)

    async def _run_step(self, step: int, ctx: PipelineContext) -> None:
        method_name = self.STEP_MAP.get(step)
        if not method_name:
            raise ValueError(f"Unknown step: {step}")
        method = getattr(self._steps, method_name)
        await method(ctx)

    def _is_fatal(self, step: int, error: Exception) -> bool:
        return step in _BUY_FATAL_STEPS


# ─────────────────────────────────────────────────────────────
# SellPipeline
# ─────────────────────────────────────────────────────────────

class SellPipeline(BasePipeline):
    """
    매도 분석 파이프라인 (Step 0~13)

    SellSteps 클래스의 메서드를 순서대로 호출합니다.
    """

    STEP_MAP: dict[int, str] = {
        0:  "step_0_env",
        1:  "step_1_health",
        2:  "step_2_regime",
        3:  "step_3_technical",
        4:  "step_4_thesis",
        5:  "step_5_devils",
        6:  "step_6_options",
        7:  "step_7_action",
        8:  "step_8_partial",
        9:  "step_9_portfolio",
        10: "step_10_decision",
        11: "step_11_storage",
        12: "step_12_review",
        13: "step_13_notify",
    }

    def __init__(self, obsidian: "ObsidianClient", slack: "SlackClient") -> None:
        super().__init__(obsidian, slack)
        from orchestrator.steps.sell_steps import SellSteps
        self._steps = SellSteps(obsidian=obsidian, slack=slack)

    async def _run_step(self, step: int, ctx: PipelineContext) -> None:
        method_name = self.STEP_MAP.get(step)
        if not method_name:
            raise ValueError(f"Unknown step: {step}")
        method = getattr(self._steps, method_name)
        await method(ctx)

    def _is_fatal(self, step: int, error: Exception) -> bool:
        return step in _SELL_FATAL_STEPS


# ─────────────────────────────────────────────────────────────
# RequeuePipeline (간소화 — 5단계)
# ─────────────────────────────────────────────────────────────

class RequeuePipeline(BasePipeline):
    """
    Requeue 처리 파이프라인 (Step 0~4)

    waiting → ready 전환된 종목을 Buy Pipeline에 재투입합니다.
    """

    TOTAL_STEPS = 5

    STEP_MAP: dict[int, str] = {
        0: "_step_env",
        1: "_step_load_summary",
        2: "_step_check_ready",
        3: "_step_run_buy",
        4: "_step_notify",
    }

    def __init__(self, obsidian: "ObsidianClient", slack: "SlackClient") -> None:
        super().__init__(obsidian, slack)
        self._ready_tickers: list[str] = []

    async def _run_step(self, step: int, ctx: PipelineContext) -> None:
        method_name = self.STEP_MAP.get(step)
        if not method_name:
            raise ValueError(f"Unknown requeue step: {step}")
        method = getattr(self, method_name)
        await method(ctx)

    def _is_fatal(self, step: int, error: Exception) -> bool:
        return step == 0

    # ── Requeue 단계 메서드 ────────────────────────────────

    async def _step_env(self, ctx: PipelineContext) -> None:
        obsidian_ok = await self.obsidian.ping()
        if not obsidian_ok:
            raise RuntimeError("FATAL: Obsidian 연결 실패")
        save_snapshot(ctx.execution_id, 0, {"status": "ok"})
        append_audit(ctx.execution_id, 0, "completed")

    async def _step_load_summary(self, ctx: PipelineContext) -> None:
        from core.parsers import load_latest_summary
        try:
            ctx.summary_data = load_latest_summary(ctx.paths.summary_dir)
        except Exception as exc:
            append_audit(ctx.execution_id, 1, "degraded", error=str(exc))
            save_snapshot(ctx.execution_id, 1, {"summary_loaded": False})
            return
        save_snapshot(ctx.execution_id, 1, {"summary_loaded": True})
        append_audit(ctx.execution_id, 1, "completed")

    async def _step_check_ready(self, ctx: PipelineContext) -> None:
        self._ready_tickers = requeue_check_ready(ctx.summary_data)
        save_snapshot(ctx.execution_id, 2, {"ready": self._ready_tickers})
        append_audit(ctx.execution_id, 2, "completed",
                     data={"ready_count": len(self._ready_tickers)})
        log.info("requeue_ready_tickers", tickers=self._ready_tickers)

    async def _step_run_buy(self, ctx: PipelineContext) -> None:
        """ready 종목을 Buy Pipeline으로 재실행"""
        if not self._ready_tickers:
            save_snapshot(ctx.execution_id, 3, {"status": "no_ready_tickers"})
            append_audit(ctx.execution_id, 3, "skipped")
            return

        import uuid
        buy_ctx = PipelineContext(
            execution_id=f"buy_{ctx.execution_id}_requeue_{uuid.uuid4().hex[:8]}",
            pipeline_type="buy",
            target_tickers=self._ready_tickers,
            paths=ctx.paths,
        )
        buy_pipeline = BuyPipeline(self.obsidian, self.slack)
        await buy_pipeline.run(buy_ctx)

        save_snapshot(ctx.execution_id, 3, {"triggered": self._ready_tickers})
        append_audit(ctx.execution_id, 3, "completed",
                     data={"triggered": self._ready_tickers})

    async def _step_notify(self, ctx: PipelineContext) -> None:
        from core.state import requeue_list
        ready_items = requeue_list(status="ready")
        if ready_items:
            await self.slack.send_requeue_alert(ready_items)
        save_snapshot(ctx.execution_id, 4, {"notified": len(ready_items)})
        append_audit(ctx.execution_id, 4, "completed")
