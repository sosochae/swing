"""
orchestrator/engine.py
=======================
PipelineEngine — MCP 서버와 파이프라인 사이의 핵심 오케스트레이터

역할:
- run_buy()  : Buy Pipeline 실행
- run_sell() : Sell Pipeline 실행
- run_requeue(): Requeue Pipeline 실행
- route_nl() : 자연어 명령 라우팅
- step_execute(): 단일 단계 수동 실행
- position_status(): 포지션 현황 조회
- partial_exit(): 부분 청산 처리
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.obsidian import ObsidianClient
from core.slack import SlackClient
from core.state import (
    apply_partial_exit,
    load_pipeline_result,
    load_positions_state,
    save_positions_state,
)
from orchestrator.pipelines import BuyPipeline, RequeuePipeline, SellPipeline
from shared.config import get_config
from shared.logger import get_logger, setup_logging
from shared.schemas import (
    NLQueryResult,
    PipelineContext,
    PipelineResult,
    PipelinePaths,
)

cfg = get_config()
log = get_logger()

# NL 라우팅 키워드 매핑 (LLM 폴백 전 빠른 판단)
_NL_KEYWORDS: dict[str, list[str]] = {
    "BUY_PIPELINE": ["매수", "buy", "진입", "분석해", "스크리닝"],
    "SELL_PIPELINE": ["매도", "sell", "청산", "exit", "포지션 정리", "포지션 청산", "팔아"],
    "POSITION_STATUS": ["포지션 현황", "포지션 상태", "보유 현황", "현재 포지션", "status", "잔고", "보유"],
    "REQUEUE_ADD": ["requeue 등록", "대기 등록", "나중에 다시"],
    "REQUEUE_LIST": ["requeue 목록", "대기 목록", "waiting"],
    "STEP_EXECUTE": ["step", "단계", "스텝"],
    "PARTIAL_EXIT": ["부분 청산", "partial exit", "일부 청산", "반 청산", "일부만 팔아"],
    "EARNINGS_ANALYSIS": ["어닝", "earnings", "실적 분석", "어닝콜"],
    "MARKET_REGIME": ["레짐", "regime", "시장 상태", "시장 레짐", "시장 환경"],
}


class PipelineEngine:
    """
    SwingMCP 핵심 오케스트레이터.

    MCP Tool → PipelineEngine → Pipeline → Steps 순으로 호출됩니다.
    모든 외부 의존성(Obsidian, Slack)을 여기서 초기화합니다.
    """

    def __init__(self) -> None:
        self._obsidian = ObsidianClient()
        self._slack = SlackClient()
        self._paths = PipelinePaths(
            summary_dir=Path(cfg.SUMMARY_DIR),
            finviz_file=Path(cfg.FINVIZ_FILE),
            earnings_dir=Path(cfg.EARNINGS_DIR),
            earnings_analysis=Path(cfg.EARNINGS_DIR) / "어닝 분석.md",
            positions_file=Path(cfg.POSITIONS_FILE),
            watchlist_file=Path(cfg.WATCHLIST_FILE),
            data_dir=Path(cfg.DATA_DIR),
        )

    # ─────────────────────────────────────────────────────────
    # Buy Pipeline
    # ─────────────────────────────────────────────────────────

    async def run_buy(
        self,
        execution_id: str | None = None,
        force_refresh: bool = False,
        start_step: int = 0,
        target_tickers: list[str] | None = None,
    ) -> PipelineResult:
        """
        Buy Pipeline 실행

        Args:
            execution_id: 실행 ID (None이면 자동 생성)
            force_refresh: True이면 캐시 무시
            start_step: 시작 단계 (0~13)
            target_tickers: 분석 대상 종목 (None이면 전체)

        Returns:
            PipelineResult
        """
        eid = execution_id or f"buy_{datetime.now().strftime('%Y-%m-%d')}_{uuid.uuid4()}"
        setup_logging(eid)

        # 이전 결과가 이미 completed이면 반환
        if not force_refresh:
            prev = load_pipeline_result(eid)
            if prev and prev.get("status") == "completed":
                log.info("pipeline_already_completed", execution_id=eid)
                return PipelineResult(**prev)

        ctx = PipelineContext(
            execution_id=eid,
            pipeline_type="buy",
            start_step=start_step,
            force_refresh=force_refresh,
            target_tickers=target_tickers,
            paths=self._paths,
        )

        pipeline = BuyPipeline(obsidian=self._obsidian, slack=self._slack)
        return await pipeline.run(ctx)

    # ─────────────────────────────────────────────────────────
    # Sell Pipeline
    # ─────────────────────────────────────────────────────────

    async def run_sell(
        self,
        execution_id: str | None = None,
        force_refresh: bool = False,
        start_step: int = 0,
        target_tickers: list[str] | None = None,
    ) -> PipelineResult:
        """
        Sell Pipeline 실행

        Args:
            execution_id: 실행 ID
            force_refresh: 캐시 무시 여부
            start_step: 시작 단계
            target_tickers: 분석 대상 종목

        Returns:
            PipelineResult
        """
        eid = execution_id or f"sell_{datetime.now().strftime('%Y-%m-%d')}_{uuid.uuid4()}"
        setup_logging(eid)

        # §FR-03 idempotency: 이미 completed이면 캐시 반환 (run_buy와 동일)
        if not force_refresh:
            prev = load_pipeline_result(eid)
            if prev and prev.get("status") == "completed":
                log.info("pipeline_already_completed", execution_id=eid)
                return PipelineResult(**prev)

        ctx = PipelineContext(
            execution_id=eid,
            pipeline_type="sell",
            start_step=start_step,
            force_refresh=force_refresh,
            target_tickers=target_tickers,
            paths=self._paths,
        )

        pipeline = SellPipeline(obsidian=self._obsidian, slack=self._slack)
        return await pipeline.run(ctx)

    # ─────────────────────────────────────────────────────────
    # Requeue Pipeline
    # ─────────────────────────────────────────────────────────

    async def run_requeue(
        self,
        execution_id: str | None = None,
        force_refresh: bool = True,
    ) -> PipelineResult:
        """
        Requeue Pipeline 실행 (waiting → ready 전환 후 Buy 재실행)

        Returns:
            PipelineResult
        """
        eid = execution_id or f"requeue_{datetime.now().strftime('%Y-%m-%d')}_{uuid.uuid4()}"
        setup_logging(eid)

        ctx = PipelineContext(
            execution_id=eid,
            pipeline_type="requeue",
            paths=self._paths,
            force_refresh=force_refresh,
        )

        pipeline = RequeuePipeline(obsidian=self._obsidian, slack=self._slack)
        return await pipeline.run(ctx)

    # ─────────────────────────────────────────────────────────
    # 자연어 라우팅
    # ─────────────────────────────────────────────────────────

    async def route_nl(
        self,
        query: str,
        context: dict | None = None,
    ) -> NLQueryResult:
        """
        자연어 명령 → 파이프라인 라우팅

        키워드 매칭 우선, 실패 시 LLM 폴백.

        Args:
            query: 자연어 명령 (예: "AMD 매수 분석해줘")
            context: 현재 컨텍스트 (포지션, 마지막 실행 등)

        Returns:
            NLQueryResult
        """
        query_lower = query.lower()

        # ── 1단계: 키워드 매칭 ─────────────────────────────
        matched_intent = None
        best_score = 0

        for intent, keywords in _NL_KEYWORDS.items():
            # 긴 키워드(구체적)에 가중치 — 길이 기준으로 정렬 후 점수 계산
            matched_kws = [kw for kw in keywords if kw in query_lower]
            if not matched_kws:
                continue
            # 매칭된 키워드의 총 길이 합산 (구체적인 구절 우선)
            score = sum(len(kw) for kw in matched_kws)
            if score > best_score:
                best_score = score
                matched_intent = intent

        # 티커 추출 (대문자 2~5자리 패턴, 일반 영단어 제외)
        import re
        _TICKER_STOPWORDS: frozenset[str] = frozenset({
            "ETF", "USA", "CEO", "AI", "IV", "DTE", "OI", "RSI", "MA",
            "BUY", "SELL", "AND", "THE", "FOR", "NLP", "API",
        })
        tickers = re.findall(r'\b([A-Z]{2,5})\b', query)
        extracted_tickers = [t for t in tickers if t not in _TICKER_STOPWORDS]

        if matched_intent and best_score >= 1:
            confidence = min(0.95, 0.6 + best_score * 0.1)
        else:
            # ── 2단계: LLM 폴백 ────────────────────────────
            try:
                from core.llm import analyze_with_llm
                result = await analyze_with_llm(
                    template_name="nl_routing",
                    template_vars={
                        "query": query,
                        "context": str(context or {}),
                    },
                )
                _valid_intents = frozenset({
                    "BUY_PIPELINE", "SELL_PIPELINE", "POSITION_STATUS",
                    "REQUEUE_ADD", "REQUEUE_LIST", "STEP_EXECUTE",
                    "PARTIAL_EXIT", "EARNINGS_ANALYSIS", "MARKET_REGIME", "UNKNOWN",
                })
                raw_intent = result.get("intent", "UNKNOWN")
                matched_intent = raw_intent if raw_intent in _valid_intents else "UNKNOWN"
                extracted_tickers = result.get("extracted_tickers", extracted_tickers)
                confidence = float(result.get("routing_confidence", 0.5))
            except Exception as exc:
                log.warning("nl_llm_fallback_failed", error=str(exc))
                matched_intent = "UNKNOWN"
                confidence = 0.3

        # 라우팅 파라미터 생성
        parameters: dict[str, Any] = {}
        tool_map = {
            "BUY_PIPELINE": "run_buy_pipeline",
            "SELL_PIPELINE": "run_sell_pipeline",
            "POSITION_STATUS": "position_status",
            "REQUEUE_ADD": "requeue_add",
            "REQUEUE_LIST": "requeue_list",
            "STEP_EXECUTE": "step_execute",
            "PARTIAL_EXIT": "partial_exit_apply",
            "EARNINGS_ANALYSIS": "run_buy_pipeline",
            "MARKET_REGIME": "run_buy_pipeline",
        }

        if matched_intent == "BUY_PIPELINE":
            parameters = {
                "execution_id": f"buy_{datetime.now().strftime('%Y-%m-%d')}_{uuid.uuid4()}",
                "target_tickers": extracted_tickers or None,
                "force_refresh": False,
            }
        elif matched_intent == "SELL_PIPELINE":
            parameters = {
                "execution_id": f"sell_{datetime.now().strftime('%Y-%m-%d')}_{uuid.uuid4()}",
                "target_tickers": extracted_tickers or None,
            }
        elif matched_intent == "POSITION_STATUS":
            parameters = {"ticker": extracted_tickers[0] if extracted_tickers else None}
        elif matched_intent == "PARTIAL_EXIT":
            parameters = {"ticker": extracted_tickers[0] if extracted_tickers else None}
        elif matched_intent == "MARKET_REGIME":
            parameters = {
                "execution_id": f"buy_{datetime.now().strftime('%Y-%m-%d')}_{uuid.uuid4()}",
                "start_step": 2,
            }

        result_obj = NLQueryResult(
            intent=matched_intent,  # type: ignore
            extracted_tickers=extracted_tickers,
            routing_confidence=confidence,
            routed_tool=tool_map.get(matched_intent, ""),
            parameters=parameters,
            role_lock_applied=True,
        )

        log.info(
            "nl_routed",
            query=query[:60],
            intent=matched_intent,
            tickers=extracted_tickers,
            confidence=confidence,
        )
        return result_obj

    # ─────────────────────────────────────────────────────────
    # 단일 단계 수동 실행
    # ─────────────────────────────────────────────────────────

    async def step_execute(
        self,
        pipeline_type: str,
        step: int,
        execution_id: str,
    ) -> dict:
        """
        특정 단계 단독 실행 (디버깅·재실행 용도)

        Args:
            pipeline_type: buy | sell | requeue
            step: 단계 번호 (0~13)
            execution_id: 기존 실행 ID

        Returns:
            실행 결과 딕셔너리
        """
        log.info("step_execute_manual", pipeline=pipeline_type, step=step, eid=execution_id)

        ctx = PipelineContext(
            execution_id=execution_id,
            pipeline_type=pipeline_type,  # type: ignore
            start_step=step,
            paths=self._paths,
        )

        if pipeline_type == "buy":
            pipeline = BuyPipeline(obsidian=self._obsidian, slack=self._slack)
        elif pipeline_type == "sell":
            pipeline = SellPipeline(obsidian=self._obsidian, slack=self._slack)
        else:
            pipeline = RequeuePipeline(obsidian=self._obsidian, slack=self._slack)

        try:
            await pipeline._run_step(step, ctx)
            return {
                "status": "success",
                "step": step,
                "execution_id": execution_id,
                "completed_steps": ctx.completed_steps,
            }
        except Exception as exc:
            return {
                "status": "error",
                "step": step,
                "error": str(exc),
                "execution_id": execution_id,
            }

    # ─────────────────────────────────────────────────────────
    # 포지션 현황 조회
    # ─────────────────────────────────────────────────────────

    async def position_status(self, ticker: str | None = None) -> dict:
        """
        현재 포지션 현황 조회

        Args:
            ticker: 특정 종목 (None이면 전체)

        Returns:
            포지션 현황 딕셔너리
        """
        from core.parsers import parse_positions
        from pathlib import Path

        # positions.md에서 최신 데이터 읽기
        try:
            positions = parse_positions(Path(cfg.POSITIONS_FILE))
        except Exception:
            # 캐시된 상태 폴백
            state = load_positions_state()
            positions_raw = state.get("positions", [])
            from shared.schemas import Position
            positions = []
            for p in positions_raw:
                try:
                    from datetime import date as _date
                    pos = Position(
                        ticker=p["ticker"],
                        option_type=p["option_type"],
                        strike=p["strike"],
                        expiry=_date.fromisoformat(p["expiry"]),
                        entry_date=_date.fromisoformat(p["entry_date"]),
                        entry_premium=p["entry_premium"],
                        entry_stock_price=p["entry_stock_price"],
                        original_contracts=p["original_contracts"],
                        remaining_contracts=p["remaining_contracts"],
                    )
                    positions.append(pos)
                except Exception:
                    pass

        if ticker:
            positions = [p for p in positions if p.ticker == ticker.upper()]

        result = []
        for pos in positions:
            result.append({
                "ticker": pos.ticker,
                "option_type": pos.option_type,
                "strike": pos.strike,
                "expiry": pos.expiry.isoformat(),
                "dte": pos.dte,
                "entry_premium": pos.entry_premium,
                "remaining_contracts": pos.remaining_contracts,
                "total_cost": pos.total_cost,
                "conviction_score": pos.conviction_score,
            })

        return {
            "positions": result,
            "total_count": len(result),
            "total_invested": sum(p["total_cost"] for p in result),
            "queried_at": datetime.now().isoformat(),
        }

    # ─────────────────────────────────────────────────────────
    # 부분 청산
    # ─────────────────────────────────────────────────────────

    async def partial_exit(
        self,
        ticker: str,
        contracts_to_close: int,
        exit_premium: float,
        reason: str | None = None,
    ) -> dict:
        """
        부분 청산 처리

        Args:
            ticker: 종목
            contracts_to_close: 청산할 계약 수
            exit_premium: 청산 프리미엄
            reason: 청산 이유

        Returns:
            청산 결과 딕셔너리
        """
        from core.parsers import parse_positions
        from pathlib import Path

        try:
            positions = parse_positions(Path(cfg.POSITIONS_FILE))
        except Exception:
            return {"status": "error", "error": "positions.md 읽기 실패"}

        updated_positions, realized_pnl = apply_partial_exit(
            positions=positions,
            ticker=ticker.upper(),
            contracts_to_close=contracts_to_close,
            exit_premium=exit_premium,
            reason=reason or "수동 청산",
        )

        save_positions_state(updated_positions)

        pos = next((p for p in updated_positions if p.ticker == ticker.upper()), None)
        new_trailing_stop = exit_premium * (1 - cfg.TRAILING_STOP_PCT / 100) if exit_premium else 0

        log.info(
            "partial_exit_done",
            ticker=ticker,
            contracts=contracts_to_close,
            realized_pnl=round(realized_pnl, 2),
        )

        return {
            "status": "success",
            "ticker": ticker.upper(),
            "contracts_closed": contracts_to_close,
            "exit_premium": exit_premium,
            "realized_pnl": round(realized_pnl, 2),
            "remaining_contracts": pos.remaining_contracts if pos else 0,
            "new_trailing_stop": round(new_trailing_stop, 2),
            "positions_updated": True,
            "audit_id": f"exit_{ticker}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        }

    # ─────────────────────────────────────────────────────────
    # Requeue 관리
    # ─────────────────────────────────────────────────────────

    async def requeue_add(
        self,
        ticker: str,
        failed_filters: list[str],
        threshold: dict,
    ) -> dict:
        """Requeue 등록"""
        from core.state import requeue_add as _add
        item = _add(
            ticker=ticker.upper(),
            failed_filters=failed_filters,
            threshold=threshold,
        )
        return {
            "status": "added",
            "ticker": item.ticker,
            "threshold": threshold,
            "registered_at": item.registered_at.isoformat(),
        }

    async def requeue_list(self, status: str | None = None) -> dict:
        """Requeue 목록 조회"""
        from core.state import requeue_list as _list
        items = _list(status=status)
        return {
            "items": [
                {
                    "ticker": i.ticker,
                    "status": i.status,
                    "registered_at": i.registered_at.isoformat(),
                    "failed_filters": i.failed_filters,
                    "threshold": i.threshold.model_dump(),
                }
                for i in items
            ],
            "total": len(items),
            "status_filter": status,
        }
