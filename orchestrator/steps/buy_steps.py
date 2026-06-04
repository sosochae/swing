"""
orchestrator/steps/buy_steps.py
================================
Buy Pipeline Step 0~13 — 클래스 메서드 방식 (T1 최적화)

14개 step_N_xxx.py 파일을 단일 클래스 메서드로 통합.
각 메서드는 PipelineContext를 받아 해당 단계 결과를 context에 기록합니다.

Step 번호 → 역할 매핑:
  0: 환경 검증
  1: 데이터 수집 + Watchlist
  2: 시장 레짐 판정
  3: 종목 필터링
  4: 기술 분석
  5: 뉴스/리서치
  6: Devil's Advocate (analysis에 통합)
  7: 옵션 유효성 검증
  8: 시나리오 계산
  9: 포트폴리오 노출 점검
  10: 최종 순위
  11: Requeue 등록
  12: Obsidian 저장
  13: Slack 알림
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from core.analysis import (
    analyze_market_regime,
    apply_filters,
    calculate_confidence,
    calculate_scenario,
    calculate_technical_score,
    check_portfolio_exposure,
    validate_option,
)
from core.api_fetcher import fetch_finviz_details_bulk
from core.llm import analyze_with_llm, call_ddg_search
from core.parsers import load_latest_summary, parse_earnings, parse_finviz_detail
from core.state import (
    append_audit,
    requeue_add,
    requeue_mark_processed,
    save_pipeline_result,
    save_snapshot,
)
from shared.config import get_config
from shared.logger import get_logger
from shared import strategy as st
from shared.schemas import (
    ConfidenceScore,
    FinalRanking,
    OptionValidity,
    PipelineContext,
    PipelineResult,
    Scenario,
    TechnicalScore,
)

if TYPE_CHECKING:
    from core.obsidian import ObsidianClient
    from core.slack import SlackClient

cfg = get_config()
log = get_logger()

# 향후 5일 내 실적 발표 종목 판단 기준 (Summary events에서 추출)
_EARNINGS_PROXIMITY_DAYS = cfg.EARNINGS_PROXIMITY_DAYS


class BuySteps:
    """
    Buy Pipeline Step 0~13.

    각 메서드 시그니처: async def step_N_xxx(self, ctx: PipelineContext) -> None
    결과는 ctx 필드를 직접 수정하여 누적합니다.
    """

    def __init__(self, obsidian: "ObsidianClient", slack: "SlackClient") -> None:
        self.obsidian = obsidian
        self.slack = slack

    # ─────────────────────────────────────────────────────────
    # Step 0: 환경 검증
    # ─────────────────────────────────────────────────────────

    async def step_0_env(self, ctx: PipelineContext) -> None:
        """
        
        환경 검증 (FATAL / WARN 분류)

        FATAL: Obsidian 연결 실패, 필수 디렉토리 없음
        WARN : 데이터 최신성 초과, 선택 경로 없음
        

        스펙: §9.1 Step 0
        """
        log.info("step_0_start", execution_id=ctx.execution_id)
        start = time.monotonic()

        errors: list[str] = []
        warnings: list[str] = []

        # Obsidian 연결 확인 (FATAL)
        obsidian_ok = await self.obsidian.ping()
        if not obsidian_ok:
            errors.append("E101: Obsidian REST API 응답 없음 (localhost:27123)")

        # 필수 경로 확인
        import os
        summary_dir = ctx.paths.summary_dir
        finviz_file = ctx.paths.finviz_file

        # Windows 경로가 존재하지 않을 경우 로컬 폴백
        if not summary_dir.exists():
            warnings.append(f"E200: Summary 디렉토리 없음: {summary_dir}. 로컬 테스트 모드.")

        if not finviz_file.exists():
            warnings.append(f"E200: Finviz 파일 없음: {finviz_file}")

        # 데이터 최신성 확인 (WARN)
        try:
            files = list(summary_dir.glob("summary_*.json")) if summary_dir.exists() else []
            if files:
                latest = max(files, key=lambda f: f.stat().st_mtime)
                mtime = datetime.fromtimestamp(latest.stat().st_mtime)
                age_hours = (datetime.now() - mtime).total_seconds() / 3600
                if age_hours > 12:
                    warnings.append(f"E200: 데이터 오래됨 ({age_hours:.1f}시간 전, 마지막 업데이트: {mtime.strftime('%m/%d %H:%M')})")
        except Exception:
            pass

        duration_ms = int((time.monotonic() - start) * 1000)

        if errors:
            append_audit(ctx.execution_id, 0, "failed", error="; ".join(errors))
            try:
                await self.slack.send_fatal_error(
                    ctx.execution_id, "E101", errors[0], step=0
                )
            except Exception:
                pass
            raise RuntimeError(f"FATAL Step 0: {'; '.join(errors)}")

        for w in warnings:
            log.warning("step_0_warn", msg=w)

        append_audit(ctx.execution_id, 0, "completed", duration_ms=duration_ms,
                     data={"warnings": warnings})
        save_snapshot(ctx.execution_id, 0, {"warnings": warnings}, duration_ms)
        log.info("step_0_done", duration_ms=duration_ms, warnings=len(warnings))

    # ─────────────────────────────────────────────────────────
    # Step 1: 데이터 수집 + Watchlist
    # ─────────────────────────────────────────────────────────

    async def step_1_data(self, ctx: PipelineContext) -> None:
        """
        
        Finviz + Summary + 어닝 데이터 수집, Watchlist 통합 생성
        

        스펙: §9.1 Step 1
        """
        log.info("step_1_start", execution_id=ctx.execution_id)
        start = time.monotonic()

        # Summary 로드 (가장 최근 파일)
        try:
            ctx.summary_data = load_latest_summary(ctx.paths.summary_dir)
            log.info("summary_loaded", tickers=len(ctx.summary_data.tickers))
        except FileNotFoundError:
            # 로컬 테스트 폴백: 프로젝트 내 파일 탐색
            import json as _json
            from pathlib import Path
            test_files = list(Path(".").glob("**/summary_*.json"))
            if test_files:
                from core.parsers import parse_summary
                ctx.summary_data = parse_summary(test_files[0])
            else:
                append_audit(ctx.execution_id, 1, "degraded", error="E201: Summary 없음")
                from shared.schemas import SummaryData
                ctx.summary_data = SummaryData(snapshot_timestamp=datetime.now())

        # 어닝 분석 파싱 (어닝_분석_today.md 병합)
        try:
            ctx.earnings_list = parse_earnings(
                ctx.paths.earnings_analysis,
                today_file=ctx.paths.earnings_analysis_today,
            )
        except Exception as exc:
            log.warning("earnings_parse_warn", error=str(exc))
            ctx.earnings_list = []

        # finviz_output/*.txt 상세 파싱
        try:
            ctx.finviz_detail = parse_finviz_detail(ctx.paths.finviz_output_dir)
            log.info("finviz_detail_loaded", tickers=len(ctx.finviz_detail))
        except Exception as exc:
            log.warning("finviz_detail_parse_warn", error=str(exc))
            ctx.finviz_detail = {}

        # Kavout AI 점수 파싱 (DATA_DIR 내 kavout_*.csv)
        try:
            from core.parsers import parse_kavout
            from pathlib import Path as _Path
            data_dir = _Path(cfg.DATA_DIR)
            kavout_files = sorted(data_dir.glob("kavout_*.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
            if kavout_files:
                ctx.kavout_data = parse_kavout(kavout_files[0])
                log.info("kavout_loaded", file=kavout_files[0].name, tickers=len(ctx.kavout_data))
            else:
                ctx.kavout_data = {}
        except Exception as exc:
            log.warning("kavout_parse_warn", error=str(exc))
            ctx.kavout_data = {}

        # Watchlist 구성
        # target_tickers 지정 → 해당 종목만
        # 미지정 → summary에 있는 종목만 (finviz 전체가 아님)
        if ctx.target_tickers:
            ctx.watchlist = [t.upper() for t in ctx.target_tickers]
        elif ctx.summary_data and ctx.summary_data.tickers:
            ctx.watchlist = list(ctx.summary_data.tickers.keys())
        else:
            ctx.watchlist = []

        # Obsidian watchlist.md 갱신 (실패해도 파이프라인 계속)
        try:
            await asyncio.wait_for(
                self.obsidian.write_watchlist(ctx.watchlist[:50]),
                timeout=8.0
            )
        except Exception as exc:
            log.warning("watchlist_write_warn", error=str(exc))

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 1, "completed", duration_ms=duration_ms,
                     data={"watchlist_count": len(ctx.watchlist)})
        save_snapshot(ctx.execution_id, 1,
                      {"watchlist": ctx.watchlist[:20]},
                      duration_ms)
        log.info("step_1_done", watchlist=len(ctx.watchlist))

    # ─────────────────────────────────────────────────────────
    # Step 2: 시장 레짐 판정 (결정론적)
    # ─────────────────────────────────────────────────────────

    async def step_2_regime(self, ctx: PipelineContext) -> None:
        """
        결정론적 레짐 판정 (LLM 없음)

        스펙: §9.1 Step 2
        """
        log.info("step_2_start")
        start = time.monotonic()

        if not ctx.summary_data:
            raise RuntimeError("FATAL: summary_data 없음 (Step 1 실패)")

        # ── 매크로 지표 실시간 갱신 (레짐 판단 전에 수행) ────────────────
        # Step 4보다 먼저 갱신해야 analyze_market_regime()가 실시간 데이터 사용
        try:
            from core.api_fetcher import fetch_macro_realtime
            macro_updates = await asyncio.to_thread(fetch_macro_realtime)
            if macro_updates:
                ctx.summary_data.macro = ctx.summary_data.macro.model_copy(
                    update=macro_updates
                )
                append_audit(ctx.execution_id, 2, "info",
                             data={"macro_realtime": "ok",
                                   "fields": list(macro_updates.keys())})
                log.info("macro_realtime_refreshed", fields=len(macro_updates))
        except Exception as _exc:
            log.warning("macro_realtime_failed", error=str(_exc))

        ctx.regime = analyze_market_regime(ctx.summary_data)

        if ctx.regime.regime_status == "unfavorable":
            log.warning("regime_unfavorable", direction="none")

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 2, "completed", duration_ms=duration_ms,
                     data={"regime": ctx.regime.regime_status,
                           "direction": ctx.regime.allowed_direction,
                           "regime_confidence": round(ctx.regime.regime_confidence, 2),
                           "trend_confidence": round(ctx.regime.trend_confidence, 2),
                           "risk_factor_count": len(ctx.regime.risk_factors)})
        save_snapshot(ctx.execution_id, 2,
                      {"regime_status": ctx.regime.regime_status,
                       "allowed_direction": ctx.regime.allowed_direction,
                       "regime_confidence": round(ctx.regime.regime_confidence, 2),
                       "trend_confidence": round(ctx.regime.trend_confidence, 2),
                       "risk_factors": ctx.regime.risk_factors[:6],
                       "trend_strength": ctx.regime.trend_strength.reason,
                       "volatility": ctx.regime.volatility.reason,
                       "index_trend": ctx.regime.index_trend.reason},
                      duration_ms)
        log.info("step_2_done", regime=ctx.regime.regime_status,
                 direction=ctx.regime.allowed_direction,
                 regime_confidence=round(ctx.regime.regime_confidence, 2),
                 risk_factors=len(ctx.regime.risk_factors))

    # ─────────────────────────────────────────────────────────
    # Step 3: 종목 필터링 (7개 필터)
    # ─────────────────────────────────────────────────────────

    async def step_3_filter(self, ctx: PipelineContext) -> None:
        """
        7개 필터 (F1~F7) 적용

        스펙: §9.1 Step 3 / §9.2 F1~F7
        """
        log.info("step_3_start")
        start = time.monotonic()

        if ctx.regime and ctx.regime.regime_status == "unfavorable":
            log.warning("step_3_skip_unfavorable_regime")
            ctx.filtered_tickers = []
            ctx.filter_failures = {}
            save_snapshot(ctx.execution_id, 3, {"reason": "regime_unfavorable"})
            return

        # ── 어닝 예정일 실시간 갱신 (Finnhub, 실패 시 summary.events 폴백) ────────
        # FINNHUB_API_KEY 없으면 fetch_earnings_calendar_bulk()가 빈 리스트 반환
        if ctx.summary_data and ctx.watchlist:
            try:
                from core.api_fetcher import fetch_earnings_calendar_bulk as _fecb
                _fresh_earn = await _fecb(ctx.watchlist[:20])
                if _fresh_earn:
                    # 기존 events에서 "실적" 이벤트만 교체, 경제지표/OPEX 유지
                    _non_earn = [e for e in ctx.summary_data.events if "실적" not in e.type]
                    ctx.summary_data.events = _non_earn + _fresh_earn
                    append_audit(ctx.execution_id, 3, "info",
                                 data={"earnings_calendar_realtime": "ok",
                                       "count": len(_fresh_earn)})
                    log.info("earnings_calendar_realtime", count=len(_fresh_earn))
            except Exception as _ec_exc:
                log.warning("earnings_calendar_realtime_failed", error=str(_ec_exc))
                # summary.events 그대로 유지 — 폴백

        # 향후 5일 내 실적 발표 종목 추출
        earnings_tickers: list[str] = []
        if ctx.summary_data:
            for ev in ctx.summary_data.events:
                if ev.days_until <= _EARNINGS_PROXIMITY_DAYS and "실적" in ev.type:
                    # 이벤트 이름에서 티커 추출 (예: "MRVL 실적 발표")
                    for ticker_key in ctx.summary_data.tickers:
                        if ticker_key in ev.name.upper():
                            earnings_tickers.append(ticker_key)
        # 어닝 분석 파일에서도 추출 (§9.2 F5: ea.date 기준 ±5거래일 이내만)
        today = date.today()
        for ea in ctx.earnings_list:
            if ea.ticker in earnings_tickers:
                continue
            days_diff = abs((ea.date - today).days)
            if days_diff <= _EARNINGS_PROXIMITY_DAYS:
                earnings_tickers.append(ea.ticker)

        _passed, ctx.filter_failures, ctx.filter_details = apply_filters(
            summary=ctx.summary_data,
            earnings_tickers=earnings_tickers,
            target_tickers=ctx.watchlist if ctx.watchlist else None,
        )

        # 나머지 필터는 참고 정보로 유지하되, F4(상장폐지 위험)만 hard stop 적용
        # → 옵션 결제 리스크·거래 정지 가능성이 있어 점수 기반 판단 체계 밖의 문제
        _HARD_STOP = frozenset({"F4_DELISTING_RISK"})

        base_list = list(ctx.watchlist) if ctx.watchlist else (
            _passed + [t for t in ctx.filter_failures if t not in _passed]
        )
        hard_stopped = [
            t for t in base_list
            if any(c in _HARD_STOP for c in ctx.filter_failures.get(t, []))
        ]
        ctx.filtered_tickers = [t for t in base_list if t not in hard_stopped]

        if hard_stopped:
            log.warning("step_3_hard_stop", tickers=hard_stopped,
                        reason="F4_DELISTING_RISK")

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 3, "completed", duration_ms=duration_ms,
                     data={"total": len(ctx.filtered_tickers),
                           "filter_pass": len(_passed),
                           "filter_fail": len(ctx.filter_failures),
                           "hard_stopped": hard_stopped})
        save_snapshot(ctx.execution_id, 3,
                      {"all_tickers": ctx.filtered_tickers,
                       "filter_pass": _passed,
                       "filter_fail_count": len(ctx.filter_failures),
                       "hard_stopped": hard_stopped},
                      duration_ms)
        log.info("step_3_done", total=len(ctx.filtered_tickers),
                 filter_pass=len(_passed), filter_fail=len(ctx.filter_failures),
                 hard_stopped=len(hard_stopped))

    # ─────────────────────────────────────────────────────────
    # Step 4: 기술 분석 (병렬)
    # ─────────────────────────────────────────────────────────

    async def step_4_technical(self, ctx: PipelineContext) -> None:
        """
        종목 기술 분석 점수 산출 (병렬 처리)

        스펙: §9.1 Step 4
        """
        log.info("step_4_start", tickers=len(ctx.filtered_tickers))
        start = time.monotonic()

        if not ctx.filtered_tickers or not ctx.regime:
            duration_ms = int((time.monotonic() - start) * 1000)
            append_audit(ctx.execution_id, 4, "completed", duration_ms=duration_ms,
                         data={"skipped": "no_tickers_or_regime"})
            save_snapshot(ctx.execution_id, 4, {}, duration_ms)
            return

        # ── yfinance 신선 데이터 수집 (filtered_tickers 대상) ────────────
        # finviz_output/*.txt의 오래된 펀더멘털 데이터를 실시간 값으로 교체
        # 교체 대상: price, target_price, recom, peg, forward_pe, beta,
        #            eps_surprise_pct, revenue_growth_yoy, op_margin_pct 등
        # insider_trans_pct: yfinance 미지원 → 기존 파일 값 보존
        try:
            fresh_fv_map = await fetch_finviz_details_bulk(ctx.filtered_tickers)
            refreshed, failed = 0, 0
            for ticker, fresh_fv in fresh_fv_map.items():
                old_fv = ctx.finviz_detail.get(ticker)
                # insider_trans_pct 보존 (yfinance에서 직접 계산 불가)
                if old_fv and old_fv.insider_trans_pct is not None:
                    fresh_fv = fresh_fv.model_copy(
                        update={"insider_trans_pct": old_fv.insider_trans_pct}
                    )
                ctx.finviz_detail[ticker] = fresh_fv
                refreshed += 1
            append_audit(ctx.execution_id, 4, "info",
                         data={"yfinance_refresh": "ok", "refreshed": refreshed})
        except Exception as exc:
            failed = len(ctx.filtered_tickers)
            append_audit(ctx.execution_id, 4, "degraded",
                         data={"yfinance_refresh_failed": str(exc), "tickers": failed})
            # 실패해도 기존 finviz_detail 유지 → 파이프라인 계속 진행

        # ── Finnhub 밸류에이션으로 yfinance 구식 값 보정 ────────────────
        # forward_pe, peg는 yfinance가 구식 데이터를 반환하는 경우가 있음
        # summary_data의 [VALUATION] 섹션(Finnhub 기반)으로 우선 교체
        if ctx.summary_data:
            for ticker in ctx.filtered_tickers:
                fv = ctx.finviz_detail.get(ticker)
                val = ctx.summary_data.tickers.get(ticker)
                if not fv or not val:
                    continue
                overrides: dict = {}
                if val.valuation.forward_pe is not None:
                    overrides["forward_pe"] = val.valuation.forward_pe
                if val.valuation.peg is not None:
                    overrides["peg"] = val.valuation.peg
                if overrides:
                    ctx.finviz_detail[ticker] = fv.model_copy(update=overrides)

        # ── Finnhub 목표주가 실시간 오버라이드 ──────────────────────────
        # yfinance targetMeanPrice는 구식 — Finnhub /stock/price-target 으로 교체
        try:
            from core.api_fetcher import fetch_finnhub_price_targets_bulk
            pt_map = await fetch_finnhub_price_targets_bulk(ctx.filtered_tickers)
            for ticker, pt in pt_map.items():
                fv = ctx.finviz_detail.get(ticker)
                if fv and pt > 0:
                    ctx.finviz_detail[ticker] = fv.model_copy(update={"target_price": pt})
            if pt_map:
                append_audit(ctx.execution_id, 4, "info",
                             data={"finnhub_price_target": "ok", "updated": len(pt_map)})
        except Exception as _exc:
            log.warning("finnhub_price_target_failed", error=str(_exc))

        # ── Finnhub 내부자 거래 실시간 오버라이드 ────────────────────────
        # insider_trans_pct: yfinance 미지원 → Finnhub으로 교체
        try:
            from core.api_fetcher import fetch_finnhub_insider_bulk
            insider_map = await fetch_finnhub_insider_bulk(ctx.filtered_tickers)
            for ticker, pct in insider_map.items():
                fv = ctx.finviz_detail.get(ticker)
                if fv:
                    ctx.finviz_detail[ticker] = fv.model_copy(
                        update={"insider_trans_pct": pct}
                    )
            if insider_map:
                append_audit(ctx.execution_id, 4, "info",
                             data={"finnhub_insider": "ok", "updated": len(insider_map)})
            log.info("finnhub_insider_refreshed", tickers=len(insider_map))
        except Exception as _exc:
            log.warning("finnhub_insider_failed", error=str(_exc))

        # ── 실시간 데이터 → summary.technical 브릿지 ────────────────────────────
        # yfinance/Finnhub으로 가져온 finviz_detail을 summary_data.technical에 반영.
        # calculate_technical_score()가 실시간 기술지표를 사용하게 됨.
        # 각 필드는 None이면 스킵 → 실패 시 자동으로 summary 값 유지 (폴백).
        if ctx.summary_data:
            _bridge_count = 0
            for _tk in ctx.filtered_tickers:
                _fv = ctx.finviz_detail.get(_tk)
                if not _fv or _tk not in ctx.summary_data.tickers:
                    continue
                _td = ctx.summary_data.tickers[_tk]
                _tech = _td.technical
                _upd: dict = {}

                # 가격 / 당일 등락 (DA _apply_devils_advocate에서 change_pct 사용)
                if _fv.price       is not None: _upd["price"]            = _fv.price
                if _fv.change_pct  is not None: _upd["change_pct"]       = _fv.change_pct
                # RSI / RVOL
                if _fv.rsi14       is not None: _upd["rsi14"]            = _fv.rsi14
                if _fv.rel_volume  is not None: _upd["avg_volume_ratio"] = _fv.rel_volume
                # ADX
                if _fv.adx         is not None: _upd["adx14"]            = _fv.adx
                # MA 달러값 (MA 정렬 점수 전체에 영향)
                if _fv.sma5_val    is not None: _upd["ma5"]              = _fv.sma5_val
                if _fv.sma20_val   is not None: _upd["ma20"]             = _fv.sma20_val
                if _fv.sma50_val   is not None: _upd["ma50"]             = _fv.sma50_val
                if _fv.sma60_val   is not None: _upd["ma60"]             = _fv.sma60_val
                if _fv.sma200_val  is not None: _upd["ma200"]            = _fv.sma200_val
                # 볼린저밴드
                if _fv.bb_upper    is not None: _upd["bb_upper"]         = _fv.bb_upper
                if _fv.bb_mid      is not None: _upd["bb_mid"]           = _fv.bb_mid
                if _fv.bb_lower    is not None: _upd["bb_lower"]         = _fv.bb_lower
                # bb_position 재계산 (DA Bollinger Break 차감 조건)
                if _fv.price and _fv.bb_upper and _fv.bb_lower:
                    if   _fv.price >= _fv.bb_upper: _upd["bb_position"] = "upper_break"
                    elif _fv.price <= _fv.bb_lower: _upd["bb_position"] = "lower_break"
                    else:                            _upd["bb_position"] = "mid"
                # MACD
                if _fv.macd_line   is not None: _upd["macd_line"]        = _fv.macd_line
                if _fv.macd_signal is not None: _upd["macd_signal"]      = _fv.macd_signal
                if _fv.macd_hist   is not None: _upd["macd_histogram"]   = _fv.macd_hist
                # macd_cross 재계산 (MACD 점수에 영향)
                if _fv.macd_line is not None and _fv.macd_signal is not None:
                    _upd["macd_cross"] = (
                        "golden" if _fv.macd_line > _fv.macd_signal else "death"
                    )
                # 지지/저항 (pivot 기반 — darkpool_ok / support_ok 판단)
                if _fv.pivot_s1    is not None: _upd["support1"]         = _fv.pivot_s1
                if _fv.pivot_s2    is not None: _upd["support2"]         = _fv.pivot_s2
                if _fv.pivot_r1    is not None: _upd["resistance1"]      = _fv.pivot_r1
                if _fv.pivot_r2    is not None: _upd["resistance2"]      = _fv.pivot_r2

                if _upd:
                    ctx.summary_data.tickers[_tk] = _td.model_copy(
                        update={"technical": _tech.model_copy(update=_upd)}
                    )
                    _bridge_count += 1

            append_audit(ctx.execution_id, 4, "info",
                         data={"technical_bridge": "ok", "bridged": _bridge_count})
            log.info("technical_bridge_done", bridged=_bridge_count,
                     total=len(ctx.filtered_tickers))

        direction = ctx.regime.allowed_direction
        # both이면 long_call 기본
        if direction == "both":
            direction = "long_call"
        elif direction == "none":
            duration_ms = int((time.monotonic() - start) * 1000)
            append_audit(ctx.execution_id, 4, "completed", duration_ms=duration_ms,
                         data={"skipped": "regime_none"})
            save_snapshot(ctx.execution_id, 4, {}, duration_ms)
            return

        async def analyze_one(ticker: str) -> tuple[str, TechnicalScore]:
            score = calculate_technical_score(
                ticker=ticker,
                direction=direction,
                summary=ctx.summary_data,
                kavout_score=ctx.kavout_data.get(ticker, {}).get("k_score", 5.0),
            )
            return ticker, score

        tasks = [analyze_one(t) for t in ctx.filtered_tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                log.warning("technical_error", error=str(r))
                continue
            ticker, score = r
            ctx.technical_scores[ticker] = score

        # ── Kavout AI 시그널 보정 (post-loop) ────────────────────────────
        # calculate_technical_score()에서 final_score만 ±4pt 반영됨;
        # 여기서는 signal_count까지 조정하고 momentum_1m 확인까지 수행
        for ticker in list(ctx.technical_scores.keys()):
            score = ctx.technical_scores[ticker]
            kavout_entry = ctx.kavout_data.get(ticker, {})
            k_score = float(kavout_entry.get("k_score", 5.0))
            momentum_1m = float(kavout_entry.get("momentum_1m", 0.0))

            extra_signals = 0
            extra_score = 0.0

            if k_score >= st.KAVOUT_HIGH_SCORE:
                extra_signals += st.KAVOUT_HIGH_SIGNAL_BONUS
                extra_score += st.KAVOUT_HIGH_SCORE_BONUS
            elif k_score <= st.KAVOUT_LOW_SCORE:
                extra_signals += st.KAVOUT_LOW_SIGNAL_PENALTY
                extra_score += st.KAVOUT_LOW_SCORE_PENALTY

            # 1개월 모멘텀 강세 + K-Score 지지 = 추가 모멘텀 확인
            if momentum_1m >= st.KAVOUT_MOMENTUM_THRESHOLD and k_score >= st.KAVOUT_COMBO_SCORE:
                extra_signals += st.KAVOUT_COMBO_SIGNAL_BONUS
                extra_score += st.KAVOUT_COMBO_SCORE_BONUS

            if extra_signals != 0 or extra_score != 0:
                ctx.technical_scores[ticker] = score.model_copy(update={
                    "signal_count": max(0, score.signal_count + extra_signals),
                    "final_score": max(0.0, min(100.0, score.final_score + extra_score)),
                })

        # ── Finviz 애널리스트 추천 시그널 보정 ──────────────────────────
        for ticker in list(ctx.technical_scores.keys()):
            fvd = ctx.finviz_detail.get(ticker)
            if not fvd:
                continue
            score = ctx.technical_scores[ticker]
            extra_signals = 0
            extra_score = 0.0

            # 애널리스트 추천 (Recom: 1.0=Strong Buy ~ 5.0=Sell)
            if fvd.recom is not None:
                if fvd.recom <= st.ANALYST_BUY_THRESHOLD:     # Strong Buy / Buy
                    extra_signals += 1
                elif fvd.recom >= st.ANALYST_SELL_THRESHOLD:   # Underperform / Sell
                    extra_signals -= 1
                    extra_score += st.ANALYST_SELL_SCORE_PENALTY

            # 숏 스퀴즈 가능성: Short Float ≥ 임계값 + long_call
            if (direction == "long_call"
                    and fvd.short_float_pct is not None
                    and fvd.short_float_pct >= st.SHORT_FLOAT_SQUEEZE_THRESHOLD):
                extra_signals += 1  # 숏 커버링 상방 압력

            if extra_signals != 0 or extra_score != 0:
                ctx.technical_scores[ticker] = score.model_copy(update={
                    "signal_count": max(0, score.signal_count + extra_signals),
                    "final_score": max(0.0, min(100.0, score.final_score + extra_score)),
                })

        # 추세 확인 여부는 기록하되, 탈락해도 다음 step으로 계속 진행
        trend_confirmed_count = sum(
            1 for t in ctx.filtered_tickers
            if ctx.technical_scores.get(t) and ctx.technical_scores[t].trend_confirmed
        )

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 4, "completed", duration_ms=duration_ms,
                     data={"scored": len(ctx.technical_scores),
                           "trend_confirmed": trend_confirmed_count,
                           "continuing": len(ctx.filtered_tickers)})
        save_snapshot(ctx.execution_id, 4,
                      {t: s.final_score for t, s in ctx.technical_scores.items()},
                      duration_ms)
        log.info("step_4_done", scored=len(ctx.technical_scores),
                 trend_confirmed=trend_confirmed_count)

    # ─────────────────────────────────────────────────────────
    # Step 5: 뉴스/리서치 (Graceful Degradation)
    # ─────────────────────────────────────────────────────────

    async def step_5_research(self, ctx: PipelineContext) -> None:
        """
        뉴스/리서치 수집: RSS(Telegram+금융) + DuckDuckGo 다중쿼리 + LLM 감성 분석

        수집 전략:
        - 시장 전체 RSS: shared/rss_feeds.json market 섹션 (Telegram 집계 포함), 최대 50개/피드
        - 종목별 RSS: tickers 섹션 + watchlist 종목 Yahoo Finance 자동 생성, 최대 20개/피드
        - DuckDuckGo 검색: 목적별 3개 쿼리 병렬 실행 (뉴스·옵션흐름·어닝+섹터)
        - Brave Search: API 키 있을 때만 보완 (선택)
        - LLM 입력: 중복 제거 후 최대 30개 기사 (title + description)
        - 모델: anthropic/claude-haiku-4.5 (유료, 고품질)
        """
        log.info("step_5_start", tickers=len(ctx.filtered_tickers))
        start = time.monotonic()

        direction = ctx.regime.allowed_direction if ctx.regime else "long_call"

        # ── RSS 피드 로드 (설정 파일) ───────────────────────────
        market_rss_news: list[dict] = []
        rss_config: dict = {}
        try:
            import json as _json
            from pathlib import Path as _Path
            rss_file = _Path(cfg.RSS_FEEDS_FILE)
            if rss_file.exists():
                rss_config = _json.loads(rss_file.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("rss_config_load_fail", error=str(exc))

        # 시장 전체 RSS 수집 (Telegram 피드 포함 — max_per_feed=50으로 전체 수집)
        market_feeds: list[str] = [
            u for u in rss_config.get("market", [])
            if isinstance(u, str) and not u.startswith("_")
        ]
        if market_feeds:
            market_rss_news = await _collect_rss_feeds(
                market_feeds, label="market", max_per_feed=50
            )

        # ── 종목별 처리 ─────────────────────────────────────────
        for ticker in ctx.filtered_tickers:
            ticker_news: list[dict] = []

            # ① 종목별 RSS 수집 (rss_feeds.json 설정값 우선, 없으면 Yahoo Finance 자동 생성)
            ticker_feeds: list[str] = [
                u for u in rss_config.get("tickers", {}).get(ticker, [])
                if isinstance(u, str)
            ]
            if not ticker_feeds:
                # watchlist에 없는 종목도 Yahoo Finance RSS 자동 생성
                ticker_feeds = [
                    f"https://feeds.finance.yahoo.com/rss/2.0/headline"
                    f"?s={ticker}&region=US&lang=en-US"
                ]
            rss_items = await _collect_rss_feeds(
                ticker_feeds, label=ticker, max_per_feed=20
            )
            ticker_news.extend(rss_items)

            # ② DuckDuckGo 실시간 웹 검색 — 목적별 3개 쿼리 병렬 실행
            #    (TrendRadar 제거: 로컬 DB라 US 주식 실시간 데이터 없음)
            ddg_queries = [
                f"{ticker} stock market news analysis",           # 일반 뉴스
                f"{ticker} options unusual activity IV analysis", # 옵션 흐름
                f"{ticker} earnings guidance sector outlook",     # 어닝·섹터
            ]
            try:
                ddg_results = await asyncio.gather(
                    *[call_ddg_search(q, num_results=8) for q in ddg_queries],
                    return_exceptions=True,
                )
                for r in ddg_results:
                    if isinstance(r, list):
                        ticker_news.extend(r)
            except Exception as exc:
                log.warning("ddg_search_error", ticker=ticker, error=str(exc))

            # ③ Brave Search 보완 (API 키 있을 때만)
            brave_news: list[dict] = []
            try:
                from core.llm import call_brave_search
                brave_news = await call_brave_search(
                    f"{ticker} stock options news catalyst", count=5
                )
            except Exception:
                pass

            # ④ 소스별 최대 항목 수 제한 후 합산
            #    순서: 종목RSS → DDG → Brave → 시장RSS(Telegram 포함)
            #    각 소스에서 골고루 포함되도록 cap 적용
            def _dedup(items: list[dict]) -> list[dict]:
                seen_: set[str] = set()
                out: list[dict] = []
                for item in items:
                    k = item.get("url") or item.get("title", "")
                    if k and k not in seen_:
                        seen_.add(k)
                        out.append(item)
                return out

            ticker_specific = _dedup(ticker_news)           # RSS+DDG+Brave (이미 누적)
            market_sample   = _dedup(market_rss_news)[:20]  # 시장RSS 상위 20개 (Telegram 포함)

            # 전체 풀 구성: 종목 특화 먼저, 시장 RSS 보완
            combined = _dedup(ticker_specific + market_sample)

            log.info("news_collected",
                     ticker=ticker,
                     ticker_specific=len(ticker_specific),
                     market_sampled=len(market_sample),
                     combined=len(combined))

            # 어닝 분석 요약 (EarningsAnalysis 전 섹션 — 최대 2000자)
            ea = next((e for e in ctx.earnings_list if e.ticker == ticker), None)
            earnings_summary = ""
            if ea:
                parts: list[str] = []
                if ea.quarter:
                    parts.append(f"최근분기: {ea.quarter}")
                if ea.industry:
                    parts.append(f"인더스트리: {ea.industry[:200]}")
                if ea.business_model:
                    parts.append(f"사업모델: {ea.business_model[:600]}")
                if ea.strategy_changes:
                    parts.append(f"전략변화: {ea.strategy_changes[:500]}")
                if ea.management_confidence:
                    parts.append(f"경영진확신도: {ea.management_confidence[:400]}")
                earnings_summary = (" | ".join(parts))[:2000]

            # ⑥ LLM 감성 분석 — 최대 50개 기사 (title + description 포함)
            #    claude-haiku-4.5 200k context → 50개 × 평균 300token ≒ 15k tokens, 충분
            try:
                ticker_data = ctx.summary_data.tickers.get(ticker) if ctx.summary_data else None
                cache_key = f"{ticker}_{date.today()}_research"
                result = await analyze_with_llm(
                    template_name="buy_step3_research",
                    template_vars={
                        "ticker": ticker,
                        "direction": direction,
                        "price": ticker_data.technical.price if ticker_data else 0,
                        "news": combined[:50],
                        "earnings_summary": earnings_summary,
                    },
                    cache_key=cache_key,
                    force_refresh=ctx.force_refresh,
                )
                ctx.sentiment_results[ticker] = {
                    "overall_sentiment": result.get("overall_sentiment", "MIXED"),
                    "confidence": result.get("confidence", "Low"),
                    "key_drivers": result.get("key_drivers", []),
                    "critical_events": result.get("critical_events", []),
                    "major_positives": result.get("major_positives", []),
                    "significant_negatives": result.get("significant_negatives", []),
                    "sentiment_strength": result.get("sentiment_strength", "Moderate"),
                    "information_consensus": result.get("information_consensus", "Conflicting"),
                    "lasting_impacts": result.get("lasting_impacts", ""),
                    "fading_impacts": result.get("fading_impacts", ""),
                    "next_catalyst_days": result.get("next_catalyst_days", 0),
                    "bull_thesis": result.get("bull_thesis", ""),
                    "bear_thesis": result.get("bear_thesis", ""),
                    "debate_verdict": result.get("debate_verdict", "Neutral"),
                    "thesis": result.get("thesis", ""),
                }
                if ctx.summary_data and ticker in ctx.summary_data.tickers:
                    ctx.summary_data.tickers[ticker].news.extend(
                        [{"source": "LLM", "title": result.get("thesis", "")}]
                    )
            except Exception as exc:
                # LLM 실패해도 sentiment_results에 fallback 저장 (보고서 N/A 방지)
                if ticker not in ctx.sentiment_results:
                    ctx.sentiment_results[ticker] = {
                        "overall_sentiment": "MIXED",
                        "confidence": "Low",
                        "key_drivers": [],
                        "critical_events": [],
                        "major_positives": [],
                        "significant_negatives": [],
                        "sentiment_strength": "Moderate",
                        "information_consensus": "Conflicting",
                        "lasting_impacts": "",
                        "fading_impacts": "",
                        "next_catalyst_days": 0,
                        "bull_thesis": "",
                        "bear_thesis": "",
                        "debate_verdict": "Neutral",
                        "thesis": "",
                    }
                append_audit(ctx.execution_id, 5, "degraded",
                             ticker=ticker, error=f"E300: LLM 실패: {exc}")

            # ⑦ 기술 분석 내러티브 LLM 생성 (buy_step3b_technical_narrative)
            try:
                td = ctx.summary_data.tickers.get(ticker) if ctx.summary_data else None
                ts = ctx.technical_scores.get(ticker)
                fv = td.technical if td else None
                if fv and ts:
                    conf_pct = round(ts.signal_count / 8 * 100)
                    # fv는 TickerTechnical(summary) 또는 FinvizDetail 둘 다 올 수 있으므로
                    # 두 클래스의 필드명을 모두 시도하는 getattr 방식 사용
                    def _g(obj, *keys, default="N/A"):
                        for k in keys:
                            v = getattr(obj, k, None)
                            if v is not None and v != 0.0:
                                return v
                        return default
                    tech_vars = {
                        "ticker": ticker,
                        "direction": direction,
                        "price":       _g(fv, 'price', default=0),
                        "rsi":         _g(fv, 'rsi14'),
                        "adx":         _g(fv, 'adx14', 'adx'),          # TT=adx14, FD=adx
                        "rvol":        _g(fv, 'avg_volume_ratio', 'rel_volume'),  # TT=avg_volume_ratio, FD=rel_volume
                        "sma5_val":    _g(fv, 'ma5', 'sma5_val'),       # TT=ma5, FD=sma5_val
                        "sma20_val":   _g(fv, 'ma20', 'sma20_val'),
                        "sma50_val":   _g(fv, 'ma50', 'sma50_val'),
                        "sma200_val":  _g(fv, 'ma200', 'sma200_val'),
                        "bb_upper":    _g(fv, 'bb_upper'),
                        "bb_mid":      _g(fv, 'bb_mid'),
                        "bb_lower":    _g(fv, 'bb_lower'),
                        "macd_line":   _g(fv, 'macd_line'),
                        "macd_signal": _g(fv, 'macd_signal'),
                        "macd_hist":   _g(fv, 'macd_histogram', 'macd_hist'),  # TT=macd_histogram, FD=macd_hist
                        "atr":         _g(fv, 'atr'),
                        "di_plus":     _g(fv, 'di_plus'),
                        "di_minus":    _g(fv, 'di_minus'),
                        "pivot":       _g(fv, 'pivot'),
                        "pivot_r1":    _g(fv, 'resistance1', 'pivot_r1'),  # TT=resistance1, FD=pivot_r1
                        "pivot_r2":    _g(fv, 'resistance2', 'pivot_r2'),
                        "pivot_s1":    _g(fv, 'support1', 'pivot_s1'),    # TT=support1, FD=pivot_s1
                        "pivot_s2":    _g(fv, 'support2', 'pivot_s2'),
                        "w52_high_pct": _g(fv, 'w52_high_pct'),
                        "w52_low_pct":  _g(fv, 'w52_low_pct'),
                        "ma_alignment":    ts.ma_alignment,
                        "adx_score":       ts.adx_score,
                        "rsi_score":       ts.rsi_score,
                        "macd_score":      ts.macd_score,
                        "rvol_score":      ts.rvol_score,
                        "signal_count":    ts.signal_count,
                        "confidence_pct":  conf_pct,
                    }
                    nar_key = f"{ticker}_{date.today()}_tech_narrative"
                    nar_result = await analyze_with_llm(
                        template_name="buy_step3b_technical_narrative",
                        template_vars=tech_vars,
                        cache_key=nar_key,
                        force_refresh=ctx.force_refresh,
                    )
                    ctx.sentiment_results[ticker]["technical_narrative"] = nar_result
            except Exception as exc:
                log.debug("tech_narrative_skip", ticker=ticker, error=str(exc))

            await asyncio.sleep(0.5)  # Rate limit 방지

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 5, "completed", duration_ms=duration_ms)
        save_snapshot(ctx.execution_id, 5, {"researched": ctx.filtered_tickers}, duration_ms)
        log.info("step_5_done", duration_ms=duration_ms)

    # ─────────────────────────────────────────────────────────
    # Step 6: Devil's Advocate (analysis.py에 통합, 재점수 확인)
    # ─────────────────────────────────────────────────────────

    async def step_6_devils(self, ctx: PipelineContext) -> None:
        """
        Devil's Advocate: IV Crush / Thesis반박 추가 차감 + 경고 기록

        스펙: §9.1 Step 6
        - IV Crush (-15점): 5일 내 실적 + 높은 IV (implied_move > 10%)
        - Thesis반박 (-20점): LLM 감성 판결이 포지션 방향과 반대
        - final_score < DA_BUY_SCORE_THRESHOLD 종목은 degraded 경고만, 탈락 없음
        """
        log.info("step_6_start")
        start = time.monotonic()

        today = date.today()
        direction = ctx.regime.allowed_direction if ctx.regime else "long_call"
        if direction == "both":
            direction = "long_call"
        is_long = direction == "long_call"

        # 5일 내 실적 발표 종목 집합
        earnings_near: set[str] = set()
        for ea in ctx.earnings_list:
            if abs((ea.date - today).days) <= _EARNINGS_PROXIMITY_DAYS:
                earnings_near.add(ea.ticker)

        da_log: dict[str, list[str]] = {}
        LOW_SCORE_THRESHOLD = st.DA_BUY_SCORE_THRESHOLD

        for ticker in list(ctx.filtered_tickers):
            score = ctx.technical_scores.get(ticker)
            if not score:
                continue

            deduction = 0.0
            reasons: list[str] = []

            # ── IV Crush 차감 (-15점) ───────────────────────
            # 조건: 5일 내 실적 발표 + implied_move > 10%
            if ticker in earnings_near:
                opt_data = ctx.summary_data.options.get(ticker) if ctx.summary_data else None
                implied_move = opt_data.implied_move_near if opt_data else 0.0
                if implied_move > st.DA_BUY_IV_CRUSH_IMPLIED_MOVE:
                    deduction += abs(st.DA_BUY_IV_CRUSH_PENALTY)
                    _ea_match = next((e for e in ctx.earnings_list if e.ticker == ticker), None)
                    _days_str = str(abs((_ea_match.date - today).days)) if _ea_match else "?"
                    reasons.append(
                        f"IV Crush 위험 (실적 {_days_str}일, implied_move {implied_move:.1f}%)"
                    )

            # ── Thesis 반박 차감 (-20점) ────────────────────
            # 조건: 감성 분석 판결이 포지션 방향과 반대
            sentiment = ctx.sentiment_results.get(ticker, {})
            verdict = sentiment.get("debate_verdict", "Neutral")
            overall = sentiment.get("overall_sentiment", "MIXED")
            bearish_verdicts = {"Bearish", "Bear", "Negative", "Strongly Bearish"}
            bullish_verdicts = {"Bullish", "Bull", "Positive", "Strongly Bullish"}
            if is_long and (verdict in bearish_verdicts or overall in {"BEARISH", "VERY_BEARISH"}):
                deduction += abs(st.DA_BUY_THESIS_CONTRA_PENALTY)
                reasons.append(f"Thesis 반박 — LLM 판결: {verdict} / 전체 감성: {overall}")
            elif not is_long and (verdict in bullish_verdicts or overall in {"BULLISH", "VERY_BULLISH"}):
                deduction += abs(st.DA_BUY_THESIS_CONTRA_PENALTY)
                reasons.append(f"Thesis 반박 — LLM 판결: {verdict} / 전체 감성: {overall}")

            # ── 내부자 순매도 차감 (-10점) ─────────────────────────────
            # 소스 우선순위: summary INSIDER 섹션 (달러 금액) → Finviz insider_trans_pct
            # 두 소스가 모두 해당돼도 최대 -10pt (동일 사건 이중 차감 방지)
            ticker_sd = ctx.summary_data.tickers.get(ticker) if ctx.summary_data else None
            insider_deducted = False

            if ticker_sd and ticker_sd.insider:
                net_sell = sum(
                    (tx.get("total") or 0.0)
                    for tx in ticker_sd.insider
                    if tx.get("type") == "매도"
                ) - sum(
                    (tx.get("total") or 0.0)
                    for tx in ticker_sd.insider
                    if tx.get("type") == "매수"
                )
                if net_sell > st.DA_BUY_INSIDER_SELL_AMOUNT:
                    deduction += abs(st.DA_BUY_INSIDER_SELL_PENALTY)
                    insider_deducted = True
                    reasons.append(
                        f"내부자 순매도 ${net_sell / 1e6:.1f}M (최근 거래 집계)"
                    )

            # ── 최근 EPS 미스 차감 (-5점, summary EARNINGS 섹션) ────────
            eps_deducted = False
            if ticker_sd and ticker_sd.earnings:
                # 가장 최근 분기 (리스트 마지막 항목 — 날짜 오름차순)
                latest_q = ticker_sd.earnings[-1]
                surprise_raw = latest_q.get("surprise_pct")
                if surprise_raw is not None:
                    # 데이터가 비율(0.27=27%) 또는 퍼센트(27.0) 양쪽 모두 가능
                    surprise = float(surprise_raw)
                    if surprise > 1.0:          # 퍼센트 형식 (27.0)
                        surprise /= 100.0
                    if surprise < st.DA_BUY_EPS_MISS_FRACTION:
                        deduction += abs(st.DA_BUY_EPS_MISS_PENALTY)
                        eps_deducted = True
                        reasons.append(
                            f"최근 EPS 미스 {surprise * 100:.1f}% "
                            f"(분기: {latest_q.get('quarter', '?')})"
                        )

            # ── Finviz 내부자 거래 / EPS 서프라이즈 차감 (보완 소스) ────
            # summary에서 이미 차감한 경우에는 Finviz 소스로 중복 차감하지 않음
            fvd = ctx.finviz_detail.get(ticker)
            if fvd:
                if (not insider_deducted
                        and fvd.insider_trans_pct is not None
                        and fvd.insider_trans_pct < st.DA_BUY_FINVIZ_INSIDER_PCT):
                    deduction += abs(st.DA_BUY_FINVIZ_INSIDER_PENALTY)
                    reasons.append(
                        f"Finviz 내부자 거래 {fvd.insider_trans_pct:.1f}% "
                        f"(대규모 내부자 매도)"
                    )
                if (not eps_deducted
                        and fvd.eps_surprise_pct is not None
                        and fvd.eps_surprise_pct < st.DA_BUY_EPS_MISS_PCT):
                    deduction += abs(st.DA_BUY_FINVIZ_EPS_PENALTY)
                    reasons.append(
                        f"Finviz EPS 서프라이즈 {fvd.eps_surprise_pct:.1f}% (미스)"
                    )

            # ── 점수 조정 적용 ──────────────────────────────
            if deduction > 0:
                new_score = max(0.0, score.final_score - deduction)
                ctx.technical_scores[ticker] = score.model_copy(
                    update={"final_score": new_score}
                )
                da_log[ticker] = reasons
                log.info("da_deduction", ticker=ticker, deduction=deduction,
                         old=score.final_score, new=new_score, reasons=reasons)
            elif reasons:
                # 차감 없이 경고만 기록된 이유도 보존
                da_log[ticker] = reasons

        # ── 40점 미만 경고 (탈락 없음, 모든 종목 계속 진행) ──
        before_count = len(ctx.filtered_tickers)
        warned: list[str] = []
        surviving: list[str] = list(ctx.filtered_tickers)
        for ticker in ctx.filtered_tickers:
            score = ctx.technical_scores.get(ticker)
            final = score.final_score if score else 0.0
            if final < LOW_SCORE_THRESHOLD:
                warned.append(ticker)
                append_audit(ctx.execution_id, 6, "degraded", ticker=ticker,
                             error=f"DA경고: 최종점수 {final:.1f} < {LOW_SCORE_THRESHOLD} (계속 진행)")

        # ctx에 da_log 저장 (Step 10·12에서 FinalRanking.da_reasons 주입용)
        ctx.da_log = da_log

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 6, "completed", duration_ms=duration_ms,
                     data={"before": before_count,
                           "warned": len(warned),
                           "surviving": len(surviving),
                           "da_deductions": len(da_log)})
        save_snapshot(ctx.execution_id, 6,
                      {"surviving": surviving,
                       "warned": warned,
                       "da_deductions": da_log},
                      duration_ms)
        log.info("step_6_done", before=before_count, warned=len(warned),
                 surviving=len(surviving))

    # ─────────────────────────────────────────────────────────
    # Step 7: 옵션 유효성 검증
    # ─────────────────────────────────────────────────────────

    async def step_7_options(self, ctx: PipelineContext) -> None:
        """
        옵션 체인 유효성 검증 (Delta/IVR/OI/Spread/DTE)

        스펙: §9.1 Step 7 / §9.3
        """
        log.info("step_7_start", tickers=len(ctx.filtered_tickers))
        start = time.monotonic()

        # ── 옵션 체인 실시간 갱신 (yfinance) ─────────────────────────────
        if ctx.summary_data:
            try:
                from core.api_fetcher import fetch_option_chains_bulk
                fresh_chains = await fetch_option_chains_bulk(
                    ctx.filtered_tickers,
                    dte_min=st.DTE_MID_MIN,   # 중기 기준 (45일) — 주요 분석 체인
                    dte_max=st.DTE_MID_MAX,   # ~ 90일
                )
                for tk, chain in fresh_chains.items():
                    if chain and tk in ctx.summary_data.options:
                        # ── OI 병합: yfinance OI=0 항목에 summary OI 복원 ────────────
                        # summary chain은 장중 AppScript가 생성 → 실제 OI 보유.
                        # yfinance 장외에는 OI=0 반환 → summary OI를 strike+type 기준으로 복사.
                        _old_chain = ctx.summary_data.options[tk].chain
                        if _old_chain and not any(
                            int(e.get("oi", 0) or 0) > 0 for e in chain
                        ):
                            _sum_oi_map: dict[tuple, int] = {}
                            for _se in _old_chain:
                                _k = (float(_se.get("strike", 0)),
                                      str(_se.get("option_type", "")))
                                _sum_oi_map[_k] = max(
                                    _sum_oi_map.get(_k, 0),
                                    int(_se.get("oi", 0) or 0)
                                )
                            _oi_restored = 0
                            for _e in chain:
                                _k = (float(_e.get("strike", 0)),
                                      str(_e.get("option_type", "")))
                                if _k in _sum_oi_map and _sum_oi_map[_k] > 0:
                                    _e["oi"] = _sum_oi_map[_k]
                                    _oi_restored += 1
                            if _oi_restored:
                                log.info("chain_oi_restored_from_summary",
                                         ticker=tk, entries=_oi_restored,
                                         reason="yfinance OI=0 → summary 복원")
                        ctx.summary_data.options[tk].chain = chain
                        log.debug("option_chain_refreshed", ticker=tk, contracts=len(chain))
                    elif chain:
                        # options 키 자체가 없는 경우 — SummaryOptionData 없이 chain만 저장 불가
                        log.debug("option_chain_skip_no_opt_data", ticker=tk)
            except Exception as _e:
                log.warning("option_chain_refresh_failed", error=str(_e))

        # ── 실시간 chain에서 옵션 analytics 재계산 ─────────────────────────────
        # chain 갱신이 완료된 후, pc_ratio / OI / implied_move / max_pain /
        # atm_straddle_price를 실시간 값으로 교체.
        # Step 8 시나리오 계산(atm_straddle_price)에 실시간 값이 반영됨.
        if ctx.summary_data:
            try:
                from core.api_fetcher import _calc_atm_straddle, _calc_max_pain
                _analytics_updated = 0
                for _tk in ctx.filtered_tickers:
                    _opt = ctx.summary_data.options.get(_tk)
                    if not _opt or not _opt.chain:
                        continue
                    _chain = _opt.chain
                    _spot = (
                        ctx.summary_data.tickers[_tk].technical.price
                        if _tk in ctx.summary_data.tickers else 0.0
                    )

                    _calls = [e for e in _chain if e.get("option_type") == "call"]
                    _puts  = [e for e in _chain if e.get("option_type") == "put"]
                    _c_oi  = sum(int(e.get("oi", 0) or 0) for e in _calls)
                    _p_oi  = sum(int(e.get("oi", 0) or 0) for e in _puts)

                    # OI=0이면 유효한 포지션 없음 — analytics 갱신 스킵
                    if _c_oi == 0 and _p_oi == 0:
                        log.debug("option_analytics_skip_zero_oi", ticker=_tk)
                        continue

                    _pc    = round(_p_oi / _c_oi, 3) if _c_oi > 0 else _opt.pc_ratio
                    _strad = _calc_atm_straddle(_chain, _spot)
                    _impl  = (
                        round(_strad / _spot * 100, 2)
                        if _spot > 0 and _strad > 0 else _opt.implied_move_near
                    )
                    _mpain = _calc_max_pain(_chain)

                    ctx.summary_data.options[_tk] = _opt.model_copy(update={
                        "total_call_oi":      _c_oi,
                        "total_put_oi":       _p_oi,
                        "pc_ratio":           _pc,
                        "implied_move_near":  _impl,
                        "max_pain_near":      float(_mpain) if _mpain else _opt.max_pain_near,
                        "atm_straddle_price": _strad if _strad > 0 else _opt.atm_straddle_price,
                    })
                    _analytics_updated += 1
                    log.debug("option_analytics_refreshed", ticker=_tk,
                              pc_ratio=_pc, implied_move=_impl, max_pain=_mpain)

                append_audit(ctx.execution_id, 7, "info",
                             data={"option_analytics_refresh": "ok",
                                   "updated": _analytics_updated})
                log.info("option_analytics_done", updated=_analytics_updated)
            except Exception as _oa_exc:
                log.warning("option_analytics_refresh_failed", error=str(_oa_exc))

        # ── option_flow_ok / signal_count / capital_flow_confirmed 재평가 ────────
        # Step 4에서 summary 기준으로 고정됐던 option_flow_ok를
        # 실시간 옵션 analytics(pc_ratio, OI)로 보정.
        if ctx.summary_data and ctx.technical_scores:
            _dir_now = ctx.regime.allowed_direction if ctx.regime else "long_call"
            if _dir_now == "both":
                _dir_now = "long_call"
            _is_long_now = _dir_now == "long_call"
            _recalc_count = 0

            for _tk in ctx.filtered_tickers:
                _score = ctx.technical_scores.get(_tk)
                _opt_d = ctx.summary_data.options.get(_tk)
                if not _score or not _opt_d:
                    continue

                _pc     = _opt_d.pc_ratio
                _c_oi   = _opt_d.total_call_oi
                _p_oi   = _opt_d.total_put_oi
                _anomaly = sum(1 for e in _opt_d.chain if e.get("is_anomaly", False))

                if _is_long_now:
                    _new_opt = (
                        _pc < st.PC_RATIO_CALL_BULL
                        or (_c_oi > 0 and _p_oi > 0
                            and _c_oi >= _p_oi * st.OI_RATIO_DOMINANCE)
                    )
                else:
                    _new_opt = (
                        _pc > st.PC_RATIO_PUT_BULL
                        or (_c_oi > 0 and _p_oi > 0
                            and _p_oi >= _c_oi * st.OI_RATIO_DOMINANCE)
                    )
                if _anomaly >= st.ANOMALY_COUNT_OVERRIDE:
                    _new_opt = True

                if _new_opt != _score.option_flow_ok:
                    _delta = 1 if _new_opt else -1
                    # capital_flow_confirmed 재계산
                    # (rvol, obv_ok, option_flow_ok, darkpool_ok 중 CAPITAL_FLOW_MIN_SIGNALS개 이상)
                    _new_cap = sum([
                        _score.rvol_score >= st.SCORE_RVOL_LOW,
                        _score.obv_ok,
                        _new_opt,
                        _score.darkpool_ok,
                    ]) >= st.CAPITAL_FLOW_MIN_SIGNALS
                    ctx.technical_scores[_tk] = _score.model_copy(update={
                        "option_flow_ok":          _new_opt,
                        "capital_flow_confirmed":  _new_cap,
                        "signal_count":            max(0, _score.signal_count + _delta),
                    })
                    _recalc_count += 1
                    log.debug("option_flow_recalc", ticker=_tk,
                              old=_score.option_flow_ok, new=_new_opt,
                              pc_ratio=_pc)

            if _recalc_count:
                append_audit(ctx.execution_id, 7, "info",
                             data={"option_flow_recalc": _recalc_count})
                log.info("option_flow_recalc_done", recalculated=_recalc_count)

        # ── 투자 기간 분류 ─────────────────────────────────────────────────────
        try:
            from core.analysis import classify_investment_horizon as _clz_hz
            for _hz_tk in ctx.filtered_tickers:
                _hz_td = ctx.summary_data.tickers.get(_hz_tk) if ctx.summary_data else None
                _hz_fv = ctx.finviz_detail.get(_hz_tk) if ctx.finviz_detail else None
                _hz_ts = ctx.technical_scores.get(_hz_tk)
                _hz_kscore = float((ctx.kavout_data or {}).get(_hz_tk, {}).get("k_score", 5.0))
                _hz_rsi   = (_hz_fv.rsi14 if _hz_fv and _hz_fv.rsi14 else None) or (_hz_td.technical.rsi14 if _hz_td else None)
                _hz_adx   = (_hz_fv.adx if _hz_fv and _hz_fv.adx else None) or (_hz_td.technical.adx14 if _hz_td else None)
                _hz_rvol  = (_hz_fv.rel_volume if _hz_fv else None) or (_hz_td.technical.avg_volume_ratio if _hz_td else None)
                _hz_chg   = (_hz_fv.change_pct if _hz_fv else None) or (_hz_td.technical.change_pct if _hz_td else None)
                # ma_alignment은 TechnicalScore에 있음 (TickerTechnical 아님)
                _hz_ma    = _hz_ts.ma_alignment if _hz_ts else None
                # 어닝 후 경과일 추정
                _hz_earn_days: int | None = None
                if ctx.summary_data and ctx.summary_data.events:
                    _hz_today = date.today()
                    for _ev in ctx.summary_data.events:
                        if getattr(_ev, "ticker", None) == _hz_tk or getattr(_ev, "type", "") == "실적":
                            try:
                                _ev_dt = date.fromisoformat(str(getattr(_ev, "date", ""))[:10])
                                _diff = (_hz_today - _ev_dt).days
                                if 0 <= _diff <= 30:
                                    _hz_earn_days = _diff
                                    break
                            except Exception:
                                pass
                ctx.investment_horizons[_hz_tk] = _clz_hz(
                    _hz_tk,
                    rsi14=_hz_rsi,
                    adx14=_hz_adx,
                    avg_volume_ratio=_hz_rvol,
                    change_pct=_hz_chg,
                    ma_alignment=_hz_ma,
                    peg_ratio=_hz_fv.peg if _hz_fv else None,             # FinvizDetail.peg
                    revenue_growth_yoy=_hz_fv.revenue_growth_yoy if _hz_fv else None,
                    k_score=_hz_kscore,
                    days_since_earnings=_hz_earn_days,
                    forward_pe=_hz_fv.forward_pe if _hz_fv else None,
                )
            log.info("horizon_classified",
                     count=len(ctx.investment_horizons),
                     sample={tk: v for tk, v in list(ctx.investment_horizons.items())[:3]})
        except Exception as _hz_exc:
            log.warning("horizon_classification_failed", error=str(_hz_exc))

        # ── 기간별 옵션 체인 병렬 수집 ────────────────────────────────────────
        _horizon_chains: dict[str, dict[str, list[dict]]] = {}
        try:
            from core.api_fetcher import fetch_option_chains_multi as _fmulti
            _hz_sem = asyncio.Semaphore(2)  # 동시 티커 수 제한

            async def _fetch_hz_one(tk: str) -> None:
                _hz = ctx.investment_horizons.get(tk, ["중기"])
                async with _hz_sem:
                    _chains = await _fmulti(tk, _hz)
                if _chains:
                    _horizon_chains[tk] = _chains

            await asyncio.gather(*[_fetch_hz_one(tk) for tk in ctx.filtered_tickers])
            log.info("horizon_chains_fetched",
                     tickers=len(_horizon_chains),
                     detail={tk: list(v.keys()) for tk, v in _horizon_chains.items()})
        except Exception as _hc_exc:
            log.warning("horizon_chains_failed", error=str(_hc_exc))

        valid_tickers: list[str] = []

        for ticker in ctx.filtered_tickers:
            from datetime import datetime as _dt, timedelta as _td
            opt_data = ctx.summary_data.options.get(ticker) if ctx.summary_data else None
            ticker_data = ctx.summary_data.tickers.get(ticker) if ctx.summary_data else None
            spot = ticker_data.technical.price if ticker_data else 0.0
            direction = ctx.regime.allowed_direction if ctx.regime else "long_call"
            if direction == "both":
                direction = "long_call"
            opt_type = "call" if "call" in direction else "put"

            # ── ATM 옵션 탐색 ─────────────────────────────────────────────
            # 우선순위: ① OI≥OI_MIN + DTE범위 내 + delta근접
            #           ② OI≥OI_WARNING + DTE범위 내 + delta근접
            #           ③ DTE범위 내 + delta근접 (OI 무관)
            #           ④ delta범위만 (DTE/OI 무관)
            best_entry: dict | None = None
            if opt_data and opt_data.chain:
                candidates = [
                    e for e in opt_data.chain
                    if e.get("option_type", "").lower() == opt_type
                    and st.DELTA_MID_MIN <= abs(float(e.get("delta", 0) or 0)) <= st.DELTA_MID_MAX
                ]
                # 범위 내 후보가 없으면 기존 범위로 폴백
                if not candidates:
                    candidates = [
                        e for e in opt_data.chain
                        if e.get("option_type", "").lower() == opt_type
                        and cfg.DELTA_MIN <= abs(float(e.get("delta", 0) or 0)) <= cfg.DELTA_MAX
                    ]

                # 복합 스코어: delta 이격(주 기준) + OI/volume 로그 보너스 + spread 패널티
                def _composite_score(e: dict, tgt: float) -> float:
                    import math as _m
                    _d   = abs(float(e.get("delta", 0) or 0))
                    _oi  = int(e.get("oi", 0) or 0)
                    _vol = int(e.get("volume", 0) or 0)
                    _bid = float(e.get("bid", 0) or 0)
                    _ask = float(e.get("ask", 0) or 0)
                    _mid_v = float(e.get("mid", 0) or e.get("mid_price", 0) or 0)
                    _mid = (_bid + _ask) / 2 if _bid > 0 and _ask > 0 else _mid_v
                    _spread = (_ask - _bid) / _mid if _mid > 0 and _bid > 0 and _ask > 0 else 0.0
                    return (abs(_d - tgt)                        # delta 이격 (주 기준)
                            - _m.log1p(_oi)  * 0.015            # OI 보너스 (로그 스케일)
                            - _m.log1p(_vol) * 0.008            # volume 보너스 (로그 스케일)
                            + _spread * 0.25)                    # spread 패널티 (상향)

                def _pick_best(pool: list[dict]) -> dict | None:
                    if not pool:
                        return None
                    return min(pool, key=lambda e: _composite_score(e, st.DELTA_MID_TARGET))

                in_dte = [e for e in candidates if int(e.get("dte", 0) or 0) >= cfg.DTE_MIN]

                # 1순위: OI≥OI_MIN + DTE 범위 + bid>0
                _with_price = [e for e in in_dte if float(e.get("bid", 0) or 0) > 0]
                best_entry = _pick_best([e for e in _with_price
                                         if int(e.get("oi", 0) or 0) >= cfg.OI_MIN])
                # 2순위: OI≥OI_MIN + DTE 범위 (bid 무관)
                if not best_entry:
                    best_entry = _pick_best([e for e in in_dte
                                             if int(e.get("oi", 0) or 0) >= cfg.OI_MIN])
                # 3순위: OI≥OI_WARNING + DTE 범위
                if not best_entry:
                    best_entry = _pick_best([e for e in in_dte
                                             if int(e.get("oi", 0) or 0) >= cfg.OI_WARNING])
                # 4순위: OI 무관 + DTE 범위
                if not best_entry:
                    best_entry = _pick_best(in_dte)
                # 5순위: DTE/OI 모두 무관
                if not best_entry:
                    best_entry = _pick_best(candidates)

                if best_entry:
                    oi_sel = int(best_entry.get("oi", 0) or 0)
                    _sel_delta = round(abs(float(best_entry.get("delta", 0) or 0)), 3)
                    _sel_spread = round(
                        (float(best_entry.get("ask", 0) or 0) - float(best_entry.get("bid", 0) or 0))
                        / max((float(best_entry.get("ask", 0) or 0) + float(best_entry.get("bid", 0) or 0)) / 2, 0.01)
                        * 100, 1)
                    log.info("option_candidate_selected",
                             ticker=ticker, strike=best_entry.get("strike"),
                             dte=best_entry.get("dte"), oi=oi_sel,
                             delta=_sel_delta, spread_pct=_sel_spread,
                             reason=f"delta_target={st.DELTA_MID_TARGET}")

            # ── 만기 결정: DTE<DTE_MIN이면 35일 후로 투영 ──────────────
            if best_entry:
                try:
                    expiry_str = str(best_entry.get("expiry", ""))
                    expiry_dt = _dt.fromisoformat(expiry_str[:10]).date() if expiry_str else date.today()
                except Exception:
                    expiry_dt = date.today()
                dte_raw = (expiry_dt - date.today()).days
                if dte_raw < cfg.DTE_MIN:
                    # 만기가 너무 가까움 → 35DTE 3번째 금요일로 투영
                    expiry_dt = _nearest_friday(date.today(), target_dte=35)
                    log.info("option_expiry_projected", ticker=ticker,
                             original_dte=dte_raw, new_expiry=str(expiry_dt))
            else:
                # 체인 데이터 없음 → spot과 IV로 합성 옵션 생성
                log.info("option_synthetic_fallback", ticker=ticker)
                expiry_dt = _nearest_friday(date.today(), target_dte=35)
                iv_est = ticker_data.technical.rsi14 / 100.0 * 0.4 + 0.3 if ticker_data else 0.5
                best_entry = {
                    "option_type": opt_type,
                    "strike": round(spot * (1.0 if opt_type == "call" else 1.0), 0),  # ATM
                    "delta": 0.55 if opt_type == "call" else 0.50,
                    "ivr": 40.0,
                    "oi": 0,       # OI 미확인 → 필터는 경고로 처리
                    "spread_pct": 2.5,
                    "mid_price": 0.0,
                    "iv": iv_est,
                    "theta": -0.05,
                    "dte": 35,
                }

            # ── Greeks 계산 (항상 BS 기반으로 산출) ─────────────────────────
            raw_mid = float(best_entry.get("mid_price", 0) or 0)
            strike_val = float(best_entry.get("strike", spot) or spot)
            iv_val = float(best_entry.get("iv", 0.5) or 0.5)
            # ── IV 단위 자동 교정 ────────────────────────────────────────
            # yfinance/체인 소스에 따라 0.005(0.5%), 0.50(50%), 50.0(5000%) 혼재
            if iv_val < 0.05:       # < 5% → 소수 단위 오류: ×100 교정
                iv_val = iv_val * 100
            elif iv_val > 5.0:      # > 500% → 퍼센트로 저장된 경우: ÷100 교정
                iv_val = iv_val / 100
            # 결과: 항상 0.05 ~ 5.0 범위 (5% ~ 500%) 보장
            dte_final = max(1, (expiry_dt - date.today()).days)

            bs_gamma: float = 0.0
            bs_vega: float = 0.0
            bs_theta: float = float(best_entry.get("theta", -0.05) or -0.05)
            try:
                from core.analysis import calculate_greeks as _cg
                import math as _math
                gs = _cg(spot=spot, strike=strike_val, expiry_days=dte_final,
                         iv=iv_val, option_type=opt_type)
                bs_gamma = gs.gamma
                bs_vega  = gs.vega
                bs_theta = gs.theta  # BS 기반 theta (만기 투영 후 DTE 반영)
            except Exception:
                import math as _math

            # ── 프리미엄 추정: mid_price > 0이면 사용, 없으면 Black-Scholes ──
            if raw_mid > 0:
                mid_price_est = raw_mid
            else:
                try:
                    mid_price_est = spot * iv_val * _math.sqrt(dte_final / 365.0) * 0.4
                    mid_price_est = max(0.5, round(mid_price_est, 2))
                except Exception:
                    mid_price_est = spot * 0.05  # 5% 폴백

            validity = validate_option(
                ticker=ticker,
                strike=strike_val,
                expiry=expiry_dt,
                direction=direction,
                delta=abs(float(best_entry.get("delta", 0.55) or 0.55)),
                ivr=float(best_entry.get("ivr", 40) or 40),
                oi=int(str(best_entry.get("oi", 0)).replace(",", "") or 0),
                spread_pct=float(best_entry.get("spread_pct", 2.5) or 2.5),
                mid_price=mid_price_est,
                iv=iv_val,
                theta=bs_theta,
                gamma=bs_gamma,
                vega=bs_vega,
            )

            ctx.option_validity[ticker] = validity

            if validity.is_valid:
                valid_tickers.append(ticker)
                if validity.ivr_warning:
                    try:
                        await self.slack.send_iv_crush_warning(
                            ticker=ticker,
                            detail=f"IVR {validity.greeks.ivr:.0f}% — 50~70% 경고 구간",
                        )
                    except Exception:
                        pass
            else:
                # 옵션 유효성 실패는 기록만 — 탈락해도 다음 step으로 계속 진행
                append_audit(ctx.execution_id, 7, "degraded", ticker=ticker,
                             error=f"E400: {validity.exclusion_reason}")

        # ctx.filtered_tickers는 변경하지 않음 (전체 종목 유지)

        # ── 기간별 옵션 선택 (horizon_recommendations 구성) ──────────────────
        _HZ_PARAMS: dict[str, tuple[float, float, float]] = {
            "단기":  (st.DELTA_SHORT_MIN, st.DELTA_SHORT_MAX, st.DELTA_SHORT_TARGET),
            "중기":  (st.DELTA_MID_MIN,   st.DELTA_MID_MAX,   st.DELTA_MID_TARGET),
            "장기":  (st.DELTA_LONG_MIN,  st.DELTA_LONG_MAX,  st.DELTA_LONG_TARGET),
            "초장기": (st.DELTA_ULTRA_MIN, st.DELTA_ULTRA_MAX, st.DELTA_ULTRA_TARGET),
        }
        for ticker in ctx.filtered_tickers:
            if ticker not in _horizon_chains:
                continue
            _hv_td = ctx.summary_data.tickers.get(ticker) if ctx.summary_data else None
            _hv_spot = _hv_td.technical.price if _hv_td else 0.0
            _hv_dir = ctx.regime.allowed_direction if ctx.regime else "long_call"
            if _hv_dir == "both":
                _hv_dir = "long_call"
            _hv_opt = "call" if "call" in _hv_dir else "put"
            ctx.horizon_recommendations[ticker] = {}

            # horizon DTE 범위 매핑 (has_real_price 폴백용)
            _HZ_DTE_RANGES = {
                "단기":  (st.DTE_SHORT_MIN, st.DTE_SHORT_MAX),
                "중기":  (st.DTE_MID_MIN,   st.DTE_MID_MAX),
                "장기":  (st.DTE_LONG_MIN,  st.DTE_LONG_MAX),
                "초장기": (st.DTE_ULTRA_MIN, st.DTE_ULTRA_MAX),
            }

            for horizon, chain in _horizon_chains[ticker].items():
                _d_min, _d_max, _d_tgt = _HZ_PARAMS.get(horizon, (0.42, 0.57, 0.50))

                # ── horizon chain OI 복원 (summary 폴백) ─────────────────────
                # horizon chain(yfinance)도 장외 OI=0 → summary chain OI로 복원.
                # summary chain은 메인 교체 시 이미 OI 복원 완료 상태.
                if ctx.summary_data:
                    _hz_sum_opt = ctx.summary_data.options.get(ticker)
                    _hz_sum_chain = _hz_sum_opt.chain if _hz_sum_opt else []
                    if _hz_sum_chain and not any(
                        int(e.get("oi", 0) or 0) > 0 for e in chain
                    ):
                        _hz_oi_map: dict[tuple, int] = {}
                        for _hse in _hz_sum_chain:
                            _hk = (float(_hse.get("strike", 0)),
                                   str(_hse.get("option_type", "")))
                            _hz_oi_map[_hk] = max(
                                _hz_oi_map.get(_hk, 0),
                                int(_hse.get("oi", 0) or 0)
                            )
                        for _he in chain:
                            _hk = (float(_he.get("strike", 0)),
                                   str(_he.get("option_type", "")))
                            if _hk in _hz_oi_map and _hz_oi_map[_hk] > 0:
                                _he["oi"] = _hz_oi_map[_hk]

                # ── 복합 스코어로 최적 옵션 선택 ──────────────────────────────
                # 1순위: delta 범위 + OI≥OI_MIN + bid>0
                # 2순위: delta 범위 + OI≥OI_MIN
                # 3순위: delta 범위만
                # 4순위: delta 무관 (폴백)
                def _score(e: dict, d_tgt: float) -> float:
                    """낮을수록 좋음: delta 이격(주) + spread 패널티 - OI/volume 로그 보너스"""
                    import math as _m
                    _d   = abs(float(e.get("delta", 0) or 0))
                    _oi  = int(e.get("oi", 0) or 0)
                    _vol = int(e.get("volume", 0) or 0)
                    _bid = float(e.get("bid", 0) or 0)
                    _ask = float(e.get("ask", 0) or 0)
                    _mid_v = float(e.get("mid", 0) or e.get("mid_price", 0) or 0)
                    _mid = (_bid + _ask) / 2 if _bid > 0 and _ask > 0 else _mid_v
                    _spread = (_ask - _bid) / _mid if _mid > 0 and _bid > 0 and _ask > 0 else 0.0
                    return (abs(_d - d_tgt)
                            - _m.log1p(_oi)  * 0.015
                            - _m.log1p(_vol) * 0.008
                            + _spread * 0.25)

                _pool = [e for e in chain if e.get("option_type", "").lower() == _hv_opt]
                _cands = [e for e in _pool if _d_min <= abs(float(e.get("delta", 0) or 0)) <= _d_max
                          and int(e.get("oi", 0) or 0) >= st.OI_MIN
                          and (float(e.get("bid", 0) or 0) > 0 or float(e.get("ask", 0) or 0) > 0)]
                if not _cands:
                    _cands = [e for e in _pool if _d_min <= abs(float(e.get("delta", 0) or 0)) <= _d_max
                              and int(e.get("oi", 0) or 0) >= st.OI_MIN]
                if not _cands:
                    _cands = [e for e in _pool if _d_min <= abs(float(e.get("delta", 0) or 0)) <= _d_max]
                if not _cands:
                    _cands = _pool
                if not _cands:
                    continue
                _best = min(_cands, key=lambda e: _score(e, _d_tgt))
                try:
                    from core.analysis import validate_option as _vopt2, calculate_greeks as _cg2
                    from datetime import datetime as _dt3
                    _hv_exp_str = str(_best.get("expiry", ""))
                    _hv_exp_dt = _dt3.fromisoformat(_hv_exp_str[:10]).date() if _hv_exp_str else date.today()
                    _hv_iv = float(_best.get("iv", 0.5) or 0.5)
                    if _hv_iv > 5.0: _hv_iv /= 100.0
                    elif _hv_iv < 0.05: _hv_iv *= 100.0
                    _hv_dte = max(1, (_hv_exp_dt - date.today()).days)
                    _hv_strike = float(_best.get("strike", _hv_spot) or _hv_spot)
                    _hv_gs = _cg2(
                        spot=_hv_spot,
                        strike=_hv_strike,
                        expiry_days=_hv_dte,
                        iv=_hv_iv,
                        option_type=_hv_opt,
                    )
                    _hv_mid = float(_best.get("mid", 0) or _best.get("mid_price", 0) or 0)
                    _hv_validity = _vopt2(
                        ticker=ticker,
                        direction=_hv_dir,
                        strike=_hv_strike,
                        expiry=_hv_exp_dt,
                        delta=abs(float(_best.get("delta", _hv_gs.delta) or _hv_gs.delta)),
                        ivr=float(_best.get("ivr", 40) or 40),
                        oi=int(str(_best.get("oi", 0)).replace(",", "") or 0),
                        spread_pct=float(_best.get("spread_pct", 2.5) or 2.5),
                        mid_price=_hv_mid,
                        iv=_hv_iv,
                        theta=_hv_gs.theta,   # BS 계산값 우선 (chain default -0.05는 무시)
                        gamma=_hv_gs.gamma,
                        vega=_hv_gs.vega,
                        delta_min=_d_min,
                        delta_max=_d_max,
                        dte_min=_HZ_DTE_RANGES.get(horizon, (cfg.DTE_MIN, 365))[0],
                    )
                    ctx.horizon_recommendations[ticker][horizon] = _hv_validity
                    log.debug("horizon_option_selected",
                              ticker=ticker, horizon=horizon,
                              strike=_hv_strike, delta=round(_hv_gs.delta, 3),
                              dte=_hv_dte)
                except Exception as _hv_e:
                    log.warning("horizon_option_error",
                                ticker=ticker, horizon=horizon, error=str(_hv_e))

        # ── Phase 4: option_validity를 primary horizon 매칭으로 오버라이드 ───────
        # 분류된 기간 중 첫 번째(가장 짧은 기간)의 OptionValidity로 교체.
        # 초장기는 기준 제시 방식이므로 오버라이드 대상에서 제외.
        # 매칭 실패 시 기존 option_validity(중기 기준) 유지.
        for _ov_tk in ctx.filtered_tickers:
            _ov_horizons = ctx.investment_horizons.get(_ov_tk, [])
            for _ov_h in _ov_horizons:
                if _ov_h == "초장기":
                    continue
                _ov_matched = ctx.horizon_recommendations.get(_ov_tk, {}).get(_ov_h)
                if _ov_matched and _ov_matched.is_valid:
                    ctx.option_validity[_ov_tk] = _ov_matched
                    log.debug("option_validity_horizon_override",
                              ticker=_ov_tk, horizon=_ov_h)
                    break

        # ── Phase 5: 초장기 기준 제시 (체인 없거나 LEAPS 미제공 종목) ──────────
        _ultra_dir = (
            ctx.regime.allowed_direction
            if ctx.regime and ctx.regime.allowed_direction != "none"
            else "long_call"
        )
        if _ultra_dir == "both":
            _ultra_dir = "long_call"

        for _ult_tk in ctx.filtered_tickers:
            _ult_horizons = ctx.investment_horizons.get(_ult_tk, [])
            if "초장기" not in _ult_horizons:
                continue
            # 이미 horizon_recommendations에 초장기 OptionValidity가 있으면 스킵
            if ctx.horizon_recommendations.get(_ult_tk, {}).get("초장기"):
                continue
            # 기준 제시 방식: 기술 데이터로 strike 범위 추정
            _ult_td = ctx.summary_data.tickers.get(_ult_tk) if ctx.summary_data else None
            _ult_spot = _ult_td.technical.price if _ult_td else 0.0
            _ult_fv = ctx.finviz_detail.get(_ult_tk)
            # IV 추정: Finviz RSI 기반 (없으면 40%)
            _ult_iv = 0.40
            if _ult_fv and _ult_fv.rsi14:
                _ult_iv = min(0.80, _ult_fv.rsi14 / 100.0 * 0.6 + 0.25)
            # strike 범위 = ATM ± (IV × √(DTE_min / 365))
            _ult_move = _ult_iv * (st.DTE_ULTRA_MIN / 365.0) ** 0.5
            _s_low  = round(_ult_spot * (1 - _ult_move), 0) if _ult_spot > 0 else 0.0
            _s_high = round(_ult_spot * (1 + _ult_move), 0) if _ult_spot > 0 else 0.0
            _s_range = f"${_s_low:,.0f} ~ ${_s_high:,.0f}" if _ult_spot > 0 else "N/A"
            ctx.ultra_long_criteria[_ult_tk] = {
                "direction":       _ultra_dir,
                "dte_range":       f"{st.DTE_ULTRA_MIN}~{st.DTE_ULTRA_MAX}일",
                "delta_range":     f"{st.DELTA_ULTRA_MIN:.2f}~{st.DELTA_ULTRA_MAX:.2f}",
                "delta_target":    st.DELTA_ULTRA_TARGET,
                "strike_range":    _s_range,
                "min_oi":          st.ULTRA_MIN_OI,
                "max_spread_pct":  st.ULTRA_MAX_SPREAD_PCT,
                "note":            "체인 미제공 — 브로커에서 직접 확인 필요",
            }
            log.info("ultra_long_criteria_generated", ticker=_ult_tk,
                     direction=_ultra_dir, strike_range=_s_range)

        if ctx.horizon_recommendations:
            log.info("horizon_recommendations_done",
                     tickers=len(ctx.horizon_recommendations),
                     detail={tk: list(v.keys())
                             for tk, v in ctx.horizon_recommendations.items()})

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 7, "completed", duration_ms=duration_ms,
                     data={"valid": len(valid_tickers),
                           "continuing": len(ctx.filtered_tickers)})
        save_snapshot(ctx.execution_id, 7,
                      {"valid_tickers": valid_tickers,
                       "all_tickers": ctx.filtered_tickers}, duration_ms)
        log.info("step_7_done", valid=len(valid_tickers),
                 continuing=len(ctx.filtered_tickers))

    # ─────────────────────────────────────────────────────────
    # Step 8: 시나리오 계산
    # ─────────────────────────────────────────────────────────

    async def step_8_scenario(self, ctx: PipelineContext) -> None:
        """
        3-케이스 시나리오 분석

        스펙: §9.1 Step 8
        """
        log.info("step_8_start", tickers=len(ctx.filtered_tickers))
        start = time.monotonic()

        for ticker in ctx.filtered_tickers:
            validity = ctx.option_validity.get(ticker)
            tech_score = ctx.technical_scores.get(ticker)
            ticker_data = ctx.summary_data.tickers.get(ticker) if ctx.summary_data else None
            opt_data = ctx.summary_data.options.get(ticker) if ctx.summary_data else None

            if not validity or not ticker_data:
                continue

            try:
                # risk_params: summary에서 파싱한 값 우선, 없으면 cfg 기본값
                rp = ctx.summary_data.risk_params if ctx.summary_data else None

                # Finviz 애널리스트 목표주가 (long_call bull case 오버라이드용)
                fvd = ctx.finviz_detail.get(ticker)
                bull_tp = fvd.target_price if fvd and fvd.target_price else None

                scenario = calculate_scenario(
                    ticker=ticker,
                    direction=validity.direction,
                    strike=validity.strike,
                    expiry=validity.expiry,
                    current_stock_price=ticker_data.technical.price,
                    current_premium=validity.mid_price if validity.mid_price > 0 else (
                        # mid_price 없으면 BS 근사: spot × IV × √(DTE/365) × 0.4
                        ticker_data.technical.price * validity.greeks.iv
                        * (max(1, (validity.expiry - date.today()).days) / 365.0) ** 0.5 * 0.4
                        if validity.greeks.iv > 0 else validity.greeks.delta * ticker_data.technical.price * 0.06
                    ),
                    delta=validity.greeks.delta,
                    theta=validity.greeks.theta,
                    iv=validity.greeks.iv,
                    atm_straddle_price=opt_data.atm_straddle_price if opt_data else 0.0,
                    adx=ticker_data.technical.adx14,
                    signal_count=tech_score.signal_count if tech_score else 4,
                    # cfg.budget_1st = 전체 자본 × (1-유보) × 1차진입비율
                    # summary risk_params가 있어도 자본 배분은 config 기준으로 override
                    total_capital=cfg.TOTAL_CAPITAL,
                    max_per_position=cfg.budget_1st,
                    commission_per_contract=cfg.COMMISSION_PER_CONTRACT,
                    bull_target_price=bull_tp,
                )
                ctx.scenarios[ticker] = scenario
            except Exception as exc:
                log.warning("scenario_error", ticker=ticker, error=str(exc))
                append_audit(ctx.execution_id, 8, "degraded", ticker=ticker,
                             error=f"E400: 시나리오 계산 실패: {exc}")

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 8, "completed", duration_ms=duration_ms,
                     data={"scenarios": len(ctx.scenarios)})
        save_snapshot(ctx.execution_id, 8,
                      {t: s.expected_value for t, s in ctx.scenarios.items()}, duration_ms)
        log.info("step_8_done", scenarios=len(ctx.scenarios))

    # ─────────────────────────────────────────────────────────
    # Step 9: 포트폴리오 노출 점검
    # ─────────────────────────────────────────────────────────

    async def step_9_portfolio(self, ctx: PipelineContext) -> None:
        """
        포트폴리오 섹터·델타·자본 집중 점검

        스펙: §9.1 Step 9
        """
        log.info("step_9_start")
        start = time.monotonic()

        ctx.portfolio_exposure = check_portfolio_exposure(
            scenarios=ctx.scenarios,
            summary=ctx.summary_data,
            option_validity=ctx.option_validity or None,
        )

        # 경고 발송
        for warning in ctx.portfolio_exposure.warnings:
            try:
                await self.slack.send_risk_alert("PORTFOLIO_EXPOSURE", warning)
            except Exception:
                pass

        # 섹터 집중 경고만 기록 — 탈락해도 다음 step으로 계속 진행
        if ctx.portfolio_exposure.concentration_warning:
            log.warning("sector_concentration_warning",
                        warnings=ctx.portfolio_exposure.warnings)

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 9, "completed", duration_ms=duration_ms,
                     data={"invested": ctx.portfolio_exposure.total_invested,
                           "cash": ctx.portfolio_exposure.remaining_cash})
        save_snapshot(ctx.execution_id, 9,
                      {"remaining_cash": ctx.portfolio_exposure.remaining_cash,
                       "warnings": ctx.portfolio_exposure.warnings}, duration_ms)
        log.info("step_9_done")

    # ─────────────────────────────────────────────────────────
    # Step 10: 최종 순위
    # ─────────────────────────────────────────────────────────

    async def step_10_ranking(self, ctx: PipelineContext) -> None:
        """
        최종 순위 결정 — 두 가지 정렬 동시 산출

        ① balanced  (ctx.final_rankings)          : 확신도 → EV → R/R → IVR
        ② aggressive (ctx.final_rankings_aggressive): EV → R/R → 확신도
        ③ high_downside (ctx.high_downside_tickers): bear case 손실 > 투자금 50%

        스펙: §9.1 Step 10
        """
        log.info("step_10_start", candidates=len(ctx.filtered_tickers))
        start = time.monotonic()

        # ── 후보 목록 구성 ─────────────────────────────────────────
        candidates: list[dict] = []

        for ticker in ctx.filtered_tickers:
            tech = ctx.technical_scores.get(ticker)
            scenario = ctx.scenarios.get(ticker)
            validity = ctx.option_validity.get(ticker)

            if not tech or not scenario or not validity:
                continue

            # Kavout K-Score 조회 (없으면 중립 5.0)
            kavout_score = float(ctx.kavout_data.get(ticker, {}).get("k_score", 5.0))

            # MarketRegime 확신도 전달 (Step 10 확신도에 레짐 불확실성 반영)
            regime_confidence = ctx.regime.regime_confidence if ctx.regime else 0.67

            confidence = calculate_confidence(
                technical=tech,
                scenario=scenario,
                option_valid=validity,
                timing_conditions_met=min(4, tech.signal_count // 2),
                kavout_score=kavout_score,
                regime_confidence=regime_confidence,
                sentiment=ctx.sentiment_results.get(ticker),
            )

            bear_loss = abs(scenario.bearish.net_profit)
            rr_ratio = scenario.base.net_profit / bear_loss if bear_loss > 0 else 0.0

            # 티커 섹터 조회 (summary_data — Finnhub 기준)
            ticker_sector = (
                ctx.summary_data.tickers[ticker].sector
                if ctx.summary_data and ticker in ctx.summary_data.tickers
                else ""
            )

            candidates.append({
                "ticker": ticker,
                "signal_total": tech.signal_count,
                "rr_ratio": rr_ratio,
                "ivr": validity.greeks.ivr,
                "final_score": tech.final_score,
                "confidence": confidence,
                "scenario": scenario,
                "validity": validity,
                "tech": tech,
                "sector": ticker_sector,
                "bear_loss": bear_loss,
                "kavout_score": kavout_score,
            })

        # ── ③ 일변동 하락폭 큰 종목 플래그 ────────────────────────
        # bear case(주가 -5% 시나리오) 손실이 투자금의 50% 초과 = 고위험
        ctx.high_downside_tickers = [
            c["ticker"] for c in candidates
            if c["scenario"].total_investment > 0
            and c["bear_loss"] > c["scenario"].total_investment * st.HIGH_DOWNSIDE_LOSS_RATIO
        ]
        if ctx.high_downside_tickers:
            log.info("high_downside_flagged", tickers=ctx.high_downside_tickers)

        # ── FinalRanking 생성 헬퍼 ─────────────────────────────────
        def _build_rankings(sorted_candidates: list[dict]) -> list[FinalRanking]:
            rankings: list[FinalRanking] = []
            for rank, c in enumerate(sorted_candidates, 1):
                ticker    = c["ticker"]
                scenario  = c["scenario"]
                validity  = c["validity"]
                confidence: ConfidenceScore = c["confidence"]
                conv      = confidence.total_conviction

                # 자본 판단 — TOTAL_CAPITAL은 경고만, 진입 차단 안 함
                investment_ok = scenario.contracts > 0
                over_per_pos  = scenario.total_investment > cfg.MAX_PER_POSITION
                over_capital  = scenario.total_investment > cfg.TOTAL_CAPITAL

                # action 결정
                if conv >= st.ENTRY_CONVICTION_MIN and validity.is_valid and investment_ok:
                    action = "진입"
                elif conv >= st.WATCH_CONVICTION_MIN and validity.is_valid and investment_ok:
                    action = "관찰"
                elif conv >= st.HOLD_CONVICTION_MIN:
                    action = "보류"
                else:
                    action = "탈락"
                if scenario.contracts == 0:
                    action = "탈락"

                # 티커별 관련 포트폴리오 경고만 포함
                # (섹터 이름이 포함된 경고 → 해당 섹터 종목에만 표시)
                sector = c["sector"]
                relevant_warnings = [
                    w for w in ctx.portfolio_exposure.warnings
                    if not sector                          # 섹터 미상 → 모두 포함
                    or sector in w                         # 이 종목 섹터 언급 경고
                    or not any(                            # 특정 섹터 언급 없는 범용 경고
                        kw in w for kw in [
                            "테크", "헬스", "에너지", "금융", "소비재",
                            "산업재", "통신", "유틸", "부동산",
                        ]
                    )
                ]

                # 고위험 플래그 태그
                downside_tag = (
                    " [⚠️일변동하락위험]"
                    if ticker in ctx.high_downside_tickers else ""
                )

                # TechnicalScore 서브스코어 요약 (ADX·RSI·MACD·RVOL)
                tech_detail = (
                    f"ADX:{c['tech'].adx_score:.0f}"
                    f" RSI:{c['tech'].rsi_score:.0f}"
                    f" MACD:{c['tech'].macd_score:.0f}"
                    f" RVOL:{c['tech'].rvol_score:.0f}"
                )

                # Kavout K-Score 표시 (중립 5.0과 다를 때만)
                k = c.get("kavout_score", 5.0)
                kavout_tag = f" | K-Score {k:.0f}/9" if k != 5.0 else ""

                rankings.append(FinalRanking(
                    rank=rank,
                    ticker=ticker,
                    direction=validity.direction,
                    action=action,  # type: ignore
                    final_score=c["final_score"],
                    conviction=confidence,
                    capital_allocation=min(
                        scenario.total_investment, cfg.budget_1st
                    ),
                    contracts=scenario.contracts,
                    strike=validity.strike,
                    expiry=validity.expiry,
                    rationale=(
                        f"확신도 {conv:.2f} ({confidence.level}) | "
                        f"신호수 {c['signal_total']}/7, 시나리오R/R {c['rr_ratio']:.1f}, "
                        f"EV ${scenario.expected_value:,.0f} | "
                        f"[{tech_detail}]"
                        + kavout_tag
                        + (" [포지션한도초과]" if over_per_pos else "")
                        + (" [⚠️총자본초과-확인필요]" if over_capital else "")
                        + downside_tag
                    ),
                    risk_factors=relevant_warnings[:3],
                    scenario=scenario,
                    da_reasons=ctx.da_log.get(ticker, []),
                ))
            return rankings

        # ── ① 안정성+수익 균형 정렬: 확신도 → EV → R/R → IVR → K-Score ─────
        balanced_sorted = sorted(candidates, key=lambda x: (
            -x["confidence"].total_conviction,          # 1순위: 확신도 (안전마진)
            -max(0.0, x["scenario"].expected_value),    # 2순위: 기댓값
            -x["rr_ratio"],                             # 3순위: 손익비
            x["ivr"],                                   # 4순위: IV 저렴한 순
            -x.get("kavout_score", 5.0),                # 5순위: Kavout K-Score 높을수록 우선
        ))
        ctx.final_rankings = _build_rankings(balanced_sorted)

        # ── ② 수익성 최우선 정렬: EV → R/R → 확신도 ───────────────
        aggressive_sorted = sorted(candidates, key=lambda x: (
            -max(0.0, x["scenario"].expected_value),    # 1순위: 기댓값 극대화
            -x["rr_ratio"],                             # 2순위: 손익비
            -x["confidence"].total_conviction,          # 3순위: 확신도
        ))
        ctx.final_rankings_aggressive = _build_rankings(aggressive_sorted)

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 10, "completed", duration_ms=duration_ms,
                     data={
                         "balanced_rankings": len(ctx.final_rankings),
                         "aggressive_rankings": len(ctx.final_rankings_aggressive),
                         "high_downside": ctx.high_downside_tickers,
                     })
        save_snapshot(ctx.execution_id, 10,
                      {
                          "balanced":   [{"rank": r.rank, "ticker": r.ticker,
                                          "action": r.action}
                                         for r in ctx.final_rankings],
                          "aggressive": [{"rank": r.rank, "ticker": r.ticker,
                                          "action": r.action}
                                         for r in ctx.final_rankings_aggressive],
                          "high_downside": ctx.high_downside_tickers,
                      }, duration_ms)
        log.info("step_10_done",
                 balanced=len(ctx.final_rankings),
                 aggressive=len(ctx.final_rankings_aggressive),
                 high_downside=len(ctx.high_downside_tickers))

    # ─────────────────────────────────────────────────────────
    # Step 11: Requeue 등록
    # ─────────────────────────────────────────────────────────

    async def step_11_requeue(self, ctx: PipelineContext) -> None:
        """
        탈락 종목 Requeue 등록

        스펙: §9.1 Step 11
        """
        log.info("step_11_start")
        start = time.monotonic()

        requeue_count = 0
        for ticker, codes in ctx.filter_failures.items():
            # F3(유동성) 또는 F1(RVOL) 탈락 종목만 Requeue
            requeue_codes = [c for c in codes if c in ("F1_RVOL_LOW", "F3_LIQUIDITY_LOW")]
            if not requeue_codes:
                continue

            threshold = {}
            if "F1_RVOL_LOW" in requeue_codes:
                threshold["rvol_min"] = cfg.RVOL_MIN
            if "F3_LIQUIDITY_LOW" in requeue_codes:
                # F3 탈락 = 주가 < PRICE_TRADE_MIN 또는 시총 < MARKET_CAP_MIN
                # 재진입 조건: 주가가 기준선을 회복했을 때 (IVR과 무관)
                threshold["price_min"] = cfg.PRICE_TRADE_MIN
                threshold["market_cap_min"] = cfg.MARKET_CAP_MIN

            try:
                requeue_add(
                    ticker=ticker,
                    failed_filters=requeue_codes,
                    threshold=threshold,
                    failure_reasons=[f"필터 탈락: {c}" for c in requeue_codes],
                )
                requeue_count += 1
            except Exception as exc:
                log.warning("requeue_add_error", ticker=ticker, error=str(exc))

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 11, "completed", duration_ms=duration_ms,
                     data={"requeue_added": requeue_count})
        save_snapshot(ctx.execution_id, 11, {"requeue_count": requeue_count}, duration_ms)
        log.info("step_11_done", requeue_count=requeue_count)

    # ─────────────────────────────────────────────────────────
    # Step 12: Obsidian 저장
    # ─────────────────────────────────────────────────────────

    async def step_12_storage(self, ctx: PipelineContext) -> None:
        """
        Obsidian REST API로 분석 결과 저장

        스펙: §9.1 Step 12
        """
        log.info("step_12_start")
        start = time.monotonic()

        from core.state import requeue_list as _rq_list
        rq_count = len(_rq_list(status="waiting"))

        # Set entry_regime and entry_vix on positions for entered rankings
        if ctx.regime:
            entry_vix = 0.0
            if hasattr(ctx, 'macro_data') and ctx.macro_data:
                entry_vix = getattr(ctx.macro_data, 'vix', 0.0)
            elif hasattr(ctx, 'summary_data') and ctx.summary_data:
                macro = getattr(ctx.summary_data, 'macro', None)
                if macro:
                    entry_vix = getattr(macro, 'vix', 0.0)
            entered_tickers = {r.ticker for r in ctx.final_rankings if r.action == "진입"}
            for pos in ctx.positions:
                if hasattr(pos, 'ticker') and pos.ticker in entered_tickers:
                    pos.entry_regime = ctx.regime.regime_status
                    pos.entry_vix = entry_vix

        try:
            # FinvizDetail 맵 구성:
            # 1) ctx.finviz_detail (finviz_output/*.txt 파싱) 기본값
            # 2) ctx.summary_data.tickers[].technical (TickerTechnical) 기술지표로 보강
            from shared.schemas import FinvizDetail as _FD

            def _merge_finviz(ticker: str) -> "_FD | None":
                base: "_FD | None" = ctx.finviz_detail.get(ticker)
                tt = (ctx.summary_data.tickers.get(ticker).technical
                      if ctx.summary_data and ticker in ctx.summary_data.tickers else None)
                if base is None and tt is None:
                    return None
                # TickerTechnical 필드 → FinvizDetail 필드 매핑
                updates: dict = {}
                if tt:
                    if tt.price and not (base and base.price):
                        updates["price"] = tt.price
                    # RSI: yfinance(base) 값이 있으면 summary_data로 덮어쓰지 않음
                    if tt.rsi14 and not (base and base.rsi14):
                        updates["rsi14"] = tt.rsi14
                    if tt.avg_volume_ratio and not (base and base.rel_volume):
                        updates["rel_volume"] = tt.avg_volume_ratio
                    # SMA 달러값 — base에 없을 때만 보완
                    if tt.ma5   and not (base and base.sma5_val):   updates["sma5_val"]   = round(tt.ma5, 2)
                    if tt.ma20  and not (base and base.sma20_val):  updates["sma20_val"]  = round(tt.ma20, 2)
                    if tt.ma50  and not (base and base.sma50_val):  updates["sma50_val"]  = round(tt.ma50, 2)
                    if tt.ma200 and not (base and base.sma200_val): updates["sma200_val"] = round(tt.ma200, 2)
                    # 볼린저밴드 — base에 없을 때만 보완
                    if tt.bb_upper and not (base and base.bb_upper): updates["bb_upper"] = round(tt.bb_upper, 2)
                    if tt.bb_mid   and not (base and base.bb_mid):   updates["bb_mid"]   = round(tt.bb_mid, 2)
                    if tt.bb_lower and not (base and base.bb_lower): updates["bb_lower"] = round(tt.bb_lower, 2)
                    # MACD — base에 없을 때만 보완
                    if tt.macd_line    and not (base and base.macd_line):   updates["macd_line"]   = round(tt.macd_line, 4)
                    if tt.macd_signal  and not (base and base.macd_signal): updates["macd_signal"] = round(tt.macd_signal, 4)
                    if tt.macd_histogram and not (base and base.macd_hist): updates["macd_hist"]   = round(tt.macd_histogram, 4)
                    # ADX — base에 없을 때만 보완
                    if tt.adx14 and not (base and base.adx): updates["adx"] = round(tt.adx14, 2)
                    # 지지/저항 → pivot S1/R1 대용
                    if tt.support1:    updates["pivot_s1"] = round(tt.support1, 2)
                    if tt.support2:    updates["pivot_s2"] = round(tt.support2, 2)
                    if tt.resistance1: updates["pivot_r1"] = round(tt.resistance1, 2)
                    if tt.resistance2: updates["pivot_r2"] = round(tt.resistance2, 2)
                if base is None:
                    updates.setdefault("ticker", ticker)
                    return _FD(**updates)
                return base.model_copy(update=updates) if updates else base

            # final_rankings + aggressive 양쪽에서 ticker 수집
            all_ranked_tickers = set(
                [r.ticker for r in ctx.final_rankings] +
                [r.ticker for r in (ctx.final_rankings_aggressive or [])]
            )
            finviz_map_raw = {t: _merge_finviz(t) for t in all_ranked_tickers}
            finviz_map = {k: v for k, v in finviz_map_raw.items() if v is not None} or None
            note_path = await self.obsidian.save_buy_note(
                execution_id=ctx.execution_id,
                rankings=ctx.final_rankings,
                regime_status=ctx.regime.regime_status if ctx.regime else "unknown",
                filter_failures=ctx.filter_failures,
                requeue_count=rq_count,
                technical_scores=ctx.technical_scores or None,
                option_validity=ctx.option_validity or None,
                scenarios=ctx.scenarios or None,
                regime=ctx.regime,
                watchlist=ctx.watchlist or None,
                sentiment_results=dict(ctx.sentiment_results) if ctx.sentiment_results is not None else None,
                rankings_aggressive=ctx.final_rankings_aggressive or None,
                high_downside_tickers=ctx.high_downside_tickers or None,
                finviz_details=finviz_map or None,
                kavout_data=dict(ctx.kavout_data) if ctx.kavout_data else None,
                portfolio_exposure=ctx.portfolio_exposure,
                filter_details=dict(ctx.filter_details) if ctx.filter_details else None,
                investment_horizons=dict(ctx.investment_horizons) if ctx.investment_horizons else None,
                horizon_recommendations=dict(ctx.horizon_recommendations) if ctx.horizon_recommendations else None,
                ultra_long_criteria=dict(ctx.ultra_long_criteria) if ctx.ultra_long_criteria else None,
            )

            # 탈락 종목 개별 노트
            for ticker, codes in list(ctx.filter_failures.items())[:10]:
                try:
                    await self.obsidian.save_rejected_note(ticker, codes)
                except Exception:
                    pass

        except Exception as exc:
            append_audit(ctx.execution_id, 12, "degraded", error=f"E500: Obsidian 저장 실패: {exc}")
            note_path = ""
            log.warning("obsidian_save_failed", error=str(exc))

        # note_path를 컨텍스트에 저장 (Step 13에서 사용)
        ctx.obsidian_note_path = note_path

        # ── positions.md 자동 갱신 (파이프라인 전체 종목) ──────────────────
        if ctx.final_rankings:
            try:
                await _append_positions_md(ctx.final_rankings, ctx)
            except Exception as exc:
                log.warning("positions_md_write_warn", error=str(exc))

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 12, "completed", duration_ms=duration_ms,
                     data={"note_path": note_path,
                           "positions_written": len(ctx.final_rankings)})
        save_snapshot(ctx.execution_id, 12, {"note_path": note_path,
                                              "positions_written": len(ctx.final_rankings)},
                      duration_ms)
        log.info("step_12_done", note_path=note_path,
                 positions_written=len(ctx.final_rankings))

    # ─────────────────────────────────────────────────────────
    # Step 13: Slack 알림
    # ─────────────────────────────────────────────────────────

    async def step_13_notify(self, ctx: PipelineContext) -> None:
        """
        매수 결과 Slack 전송

        스펙: §9.1 Step 13
        """
        log.info("step_13_start")
        start = time.monotonic()

        note_path = ctx.obsidian_note_path  # Step 12에서 저장된 경로 사용

        try:
            from core.state import requeue_list as _rq_list
            rq_items = _rq_list(status="ready")
            if rq_items:
                await self.slack.send_requeue_alert(rq_items)
                for item in rq_items:
                    requeue_mark_processed(item.ticker)

            ts = await self.slack.send_buy_result(
                rankings=ctx.final_rankings,
                regime_status=ctx.regime.regime_status if ctx.regime else "unknown",
                execution_id=ctx.execution_id,
                obsidian_path=note_path,
                filter_failures=ctx.filter_failures,
                requeue_count=len(_rq_list(status="waiting")),
                rankings_aggressive=ctx.final_rankings_aggressive or None,
                high_downside_tickers=ctx.high_downside_tickers or None,
                ultra_long_criteria=dict(ctx.ultra_long_criteria) if ctx.ultra_long_criteria else None,
            )
        except Exception as exc:
            append_audit(ctx.execution_id, 13, "degraded", error=f"E501: Slack 전송 실패: {exc}")
            ts = ""
            log.warning("slack_send_failed", error=str(exc))

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 13, "completed", duration_ms=duration_ms,
                     data={"slack_ts": ts})
        save_snapshot(ctx.execution_id, 13, {"slack_ts": ts}, duration_ms)
        log.info("step_13_done", slack_ts=ts, duration_ms=duration_ms)


# ─────────────────────────────────────────────────────────────
# 모듈 레벨 헬퍼
# ─────────────────────────────────────────────────────────────

def _nearest_friday(from_date: date, target_dte: int = 35) -> date:
    """
    target_dte 이후 가장 가까운 금요일 반환 (옵션 만기 추정용)
    미국 표준 옵션 만기: 매월 세 번째 금요일
    """
    import calendar
    target = from_date + __import__("datetime").timedelta(days=target_dte)
    # target 이후 첫 번째 금요일 찾기 (weekday 4=금요일)
    days_ahead = (4 - target.weekday()) % 7
    return target + __import__("datetime").timedelta(days=days_ahead)


async def _append_positions_md(
    all_rankings: list,
    ctx: "PipelineContext",
) -> None:
    """
    파이프라인 전체 종목을 positions.md에 YAML 블록으로 추가.
    파일이 없으면 생성, 이미 동일 ticker + 동일 expiry 항목이 있으면 스킵.

    Args:
        all_rankings: FinalRanking 전체 리스트 (action 무관)
        ctx: PipelineContext
    """
    from pathlib import Path as _Path
    from datetime import datetime as _dt

    positions_file = _Path(cfg.POSITIONS_FILE)
    positions_file.parent.mkdir(parents=True, exist_ok=True)

    existing_content = ""
    if positions_file.exists():
        existing_content = positions_file.read_text(encoding="utf-8")

    now_str = _dt.now().strftime("%Y-%m-%d %H:%M")
    new_blocks: list[str] = []
    for ranking in all_rankings:
        ticker = ranking.ticker
        validity = (ctx.option_validity or {}).get(ticker)
        scenario = ranking.scenario or (ctx.scenarios or {}).get(ticker)

        expiry_str = str(ranking.expiry)
        # 중복 체크: ticker + expiry 조합이 이미 있으면 스킵
        if (f"ticker: {ticker}" in existing_content
                and f"expiry: {expiry_str}" in existing_content):
            log.info("positions_md_skip_duplicate",
                     ticker=ticker, expiry=expiry_str)
            continue

        opt_type = "롱콜" if ranking.direction == "long_call" else "롱풋"
        entry_date = date.today().isoformat()
        entry_stock_price = (
            ctx.summary_data.tickers[ticker].technical.price
            if ctx.summary_data and ticker in ctx.summary_data.tickers
            else 0.0
        )
        entry_premium = validity.mid_price if validity else 0.0
        contracts = scenario.contracts if scenario else ranking.contracts

        # sentiment 데이터 수집
        sentiment = (ctx.sentiment_results or {}).get(ticker, {})
        bull = sentiment.get("bull_thesis", "").replace('"', "'")
        bear = sentiment.get("bear_thesis", "").replace('"', "'")
        key_drivers = sentiment.get("key_drivers", [])
        drivers_str = "; ".join(
            d.get("description", "")[:80].replace('"', "'")
            for d in key_drivers[:2]
            if isinstance(d, dict)
        )

        # entry_rationale: | 블록 스칼라 — rationale 항목별 줄 바꿈 + risk_factors
        er_parts = [
            p.strip()
            for p in ranking.rationale.replace('"', "'").split(" | ")
            if p.strip()
        ]
        for rf in ranking.risk_factors:
            er_parts.append(f"⚠️ {rf.replace(chr(34), chr(39))}")
        entry_rationale_yaml = "|\n  " + "\n  ".join(er_parts)

        # thesis: | 블록 스칼라 — bull/drivers/bear 항목별 줄 바꿈
        th_parts: list[str] = []
        if ranking.direction == "long_put":
            if bear:        th_parts.append(f"🐻 {bear}")
            if drivers_str: th_parts.append(f"📌 {drivers_str}")
            if bull:        th_parts.append(f"🐂 {bull}")
        else:
            if bull:        th_parts.append(f"🐂 {bull}")
            if drivers_str: th_parts.append(f"📌 {drivers_str}")
            if bear:        th_parts.append(f"🐻 {bear}")
        thesis_yaml = ("|\n  " + "\n  ".join(th_parts)) if th_parts else f"{ticker} {opt_type} {ranking.action}"

        # entry_regime: 현재 레짐 상태 저장 (매도 파이프라인 Step 2 레짐 역전 감지용)
        _entry_regime_val = ctx.regime.regime_status if ctx.regime else "unknown"
        _entry_vix_val = 0.0
        if hasattr(ctx, 'summary_data') and ctx.summary_data:
            _macro = getattr(ctx.summary_data, 'macro', None)
            if _macro:
                _entry_vix_val = getattr(_macro, 'vix', 0.0)

        block = f"""
---
```yaml
ticker: {ticker}
action: {ranking.action}
option_type: {opt_type}
strike: {ranking.strike}
expiry: {expiry_str}
entry_date: {entry_date}
entry_premium: {entry_premium:.2f}
entry_stock_price: {entry_stock_price:.2f}
original_contracts: {contracts}
remaining_contracts: {contracts}
trailing_stop: 0.0
entry_regime: {_entry_regime_val}
entry_vix: {_entry_vix_val:.1f}
entry_rationale: {entry_rationale_yaml}
thesis: {thesis_yaml}
conviction_score: {ranking.conviction.total_conviction:.2f}
```"""
        new_blocks.append(block)

    if new_blocks:
        separator = f"\n\n신규 분석 — {now_str}\n"
        with positions_file.open("a", encoding="utf-8") as f:
            if not existing_content:
                f.write(f"Positions — {now_str}\n\n")
            f.write(separator + "\n".join(new_blocks) + "\n")
        log.info("positions_md_updated",
                 file=str(positions_file),
                 added=len(new_blocks))


async def _collect_rss_feeds(
    feed_urls: list[str],
    label: str = "feed",
    max_per_feed: int = 5,
) -> list[dict]:
    """
    RSS 피드 URL 목록에서 뉴스 항목 수집 (feedparser, Graceful Degradation)

    Args:
        feed_urls: RSS URL 리스트
        label: 로그용 레이블 (종목 또는 "market")
        max_per_feed: 피드 당 최대 항목 수

    Returns:
        [{"title": ..., "source": "rss", "description": ..., "url": ...}]
    """
    results: list[dict] = []
    try:
        import feedparser  # type: ignore
    except ImportError:
        log.warning("feedparser_not_installed", hint="pip install feedparser")
        return results

    for url in feed_urls:
        try:
            # feedparser는 동기 라이브러리 → 스레드풀에서 실행
            loop = asyncio.get_running_loop()
            feed = await loop.run_in_executor(None, feedparser.parse, url)
            for entry in feed.entries[:max_per_feed]:
                results.append({
                    "title": entry.get("title", ""),
                    "source": "rss",
                    "description": entry.get("summary", "")[:300],
                    "url": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "feed_label": label,
                })
        except Exception as exc:
            log.warning("rss_feed_fail", url=url, label=label, error=str(exc))

    log.info("rss_collected", label=label, count=len(results))
    return results
