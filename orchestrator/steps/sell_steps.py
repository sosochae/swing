"""
orchestrator/steps/sell_steps.py
=================================
Sell Pipeline Step 0~13 — 클래스 메서드 방식

Step 번호 → 역할 매핑:
  0: 환경 + positions.md 로드 + 시장 데이터 5종 로딩
  1: 포지션 건전성 점검 (P&L 귀인, DTE 긴급도)
  2: 시장 레짐 분석 (진입 시 레짐 비교)
  3: 보유 종목 기술 분석
  4: Thesis 검증 (무효화 조건 점검)
  5: Devil's Advocate (매도용)
  6: 옵션 상태 분석 (IV Crush 리스크)
  7: 행동 시나리오 (HOLD/PARTIAL/FULL/ROLL)
  8: 부분 매도 처리
  9: 포트폴리오 재확인
  10: 최종 행동 결정
  11: 저장 (positions.md, sell note)
  12: 복기 (FULL_EXIT 종목)
  13: Slack 알림
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime
from typing import TYPE_CHECKING

from core.analysis import (
    analyze_market_regime,
    calculate_greeks,
    calculate_scenario,
    calculate_technical_score,
)
from core.llm import analyze_with_llm, call_ddg_search, _collect_rss_feeds
from core.obsidian import _format_sell_review_section
from core.parsers import (
    load_latest_summary,
    parse_kavout,
    parse_positions,
)
from core.state import (
    append_audit,
    apply_partial_exit,
    save_pipeline_result,
    save_positions_state,
    save_snapshot,
)
from shared.config import get_config
from shared.logger import get_logger
from shared import strategy as st
from shared.schemas import (
    PipelineContext,
    Position,
    Scenario,
    ScenarioCase,
    SellDecision,
)

if TYPE_CHECKING:
    from core.obsidian import ObsidianClient
    from core.slack import SlackClient

cfg = get_config()
log = get_logger()


def _pos_key(pos: "Position") -> str:
    """포지션 고유 키 — 같은 종목이라도 만기·행사가가 다르면 별도 처리"""
    return f"{pos.ticker}_{pos.expiry}_{pos.strike}"


def _default_sentiment() -> dict:
    """뉴스 분석 실패 시 사용할 기본 감성 딕셔너리"""
    return {
        "overall_sentiment": "MIXED",
        "confidence": "Low",
        "key_drivers": [],
        "critical_events": [],
        "major_positives": [],
        "significant_negatives": [],
        "sentiment_strength": "Weak",
        "information_consensus": "Conflicting",
        "lasting_impacts": "",
        "fading_impacts": "",
        "next_catalyst_days": 0,
        "bull_thesis": "",
        "bear_thesis": "",
        "debate_verdict": "Neutral",
    }


class SellSteps:
    """
    Sell Pipeline Step 0~13.

    포지션 분석 → HOLD / PARTIAL_EXIT / FULL_EXIT / ROLL 결정
    """

    def __init__(self, obsidian: "ObsidianClient", slack: "SlackClient") -> None:
        self.obsidian = obsidian
        self.slack = slack

    # ─────────────────────────────────────────────────────────
    # Step 0: 환경 + positions.md 로드
    # ─────────────────────────────────────────────────────────

    async def step_0_env(self, ctx: PipelineContext) -> None:
        """
        환경 검증 + positions.md 파싱 + 시장 데이터 4종 로딩
        (summary, earnings, stock_data, kavout)

        스펙: §9.4 Step 0
        """
        log.info("sell_step_0_start", execution_id=ctx.execution_id)
        start = time.monotonic()

        # Obsidian 연결 확인
        obsidian_ok = await self.obsidian.ping()
        if not obsidian_ok:
            try:
                await self.slack.send_fatal_error(
                    ctx.execution_id, "E101", "Obsidian REST API 응답 없음", step=0
                )
            except Exception:
                pass
            raise RuntimeError("FATAL Step 0: Obsidian 연결 실패")

        # positions.md 파싱
        try:
            ctx.positions = parse_positions(ctx.paths.positions_file)
            log.info("positions_loaded", count=len(ctx.positions))
        except Exception as exc:
            append_audit(ctx.execution_id, 0, "degraded", error=f"E201: {exc}")
            ctx.positions = []

        # target_tickers 필터링
        if ctx.target_tickers:
            ctx.positions = [
                p for p in ctx.positions
                if p.ticker in ctx.target_tickers
            ]

        if not ctx.positions:
            log.warning("sell_no_positions")

        # ── 시장 데이터 로딩 (매수 Step 1과 동일 패턴) ──────────────────────

        # Summary (가장 최근 파일)
        try:
            ctx.summary_data = load_latest_summary(ctx.paths.summary_dir)
            log.info("sell_summary_loaded", tickers=len(ctx.summary_data.tickers))
        except Exception as exc:
            log.warning("sell_summary_warn", error=str(exc))
            append_audit(ctx.execution_id, 0, "degraded", error=f"E201: summary {exc}")
            ctx.summary_data = None

        # 어닝 분석: sell pipeline은 어닝 이벤트를 summary.events로만 참조
        ctx.earnings_list = []

        ctx.stock_data = {}

        # ── yfinance 실시간 데이터 수집 (포지션 티커 전체) ─────────────────
        pos_tickers = list({p.ticker for p in ctx.positions})
        if pos_tickers:
            try:
                from core.api_fetcher import fetch_stock_data_bulk as _fetch_bulk
                log.info("sell_yfinance_fetch_start", tickers=pos_tickers)
                _fresh_details = await _fetch_bulk(pos_tickers, sleep_sec=0.3, max_concurrency=3)
                for _ticker, _fresh_fvd in _fresh_details.items():
                    if _fresh_fvd.price is not None:
                        # ③ insider_trans_pct 보존 (yfinance에서 직접 계산 불가)
                        _old_fvd = ctx.stock_data.get(_ticker)
                        if _old_fvd and _old_fvd.insider_trans_pct is not None:
                            _fresh_fvd = _fresh_fvd.model_copy(
                                update={"insider_trans_pct": _old_fvd.insider_trans_pct}
                            )
                        ctx.stock_data[_ticker] = _fresh_fvd
                        log.info("sell_yfinance_fresh", ticker=_ticker,
                                 price=_fresh_fvd.price, rsi=_fresh_fvd.rsi14,
                                 target=_fresh_fvd.target_price, recom=_fresh_fvd.recom)
                    else:
                        # yfinance 실패 시 기존 stock_data 유지 (fallback)
                        log.warning("sell_yfinance_no_price", ticker=_ticker,
                                    reason="price=None, stock_data 유지")
            except Exception as _yf_exc:
                log.warning("sell_yfinance_bulk_failed", error=str(_yf_exc))

        # ── ④ Finnhub 밸류에이션으로 yfinance 구식 값 보정 ─────────────────
        # forward_pe, peg는 yfinance가 구식 데이터를 반환하는 경우가 있음
        # summary_data의 [VALUATION] 섹션(Finnhub 기반)으로 우선 교체
        if ctx.summary_data and pos_tickers:
            for _tk in pos_tickers:
                _fv = ctx.stock_data.get(_tk)
                _val = ctx.summary_data.tickers.get(_tk)
                if not _fv or not _val:
                    continue
                _overrides: dict = {}
                if _val.valuation.forward_pe is not None:
                    _overrides["forward_pe"] = _val.valuation.forward_pe
                if _val.valuation.peg is not None:
                    _overrides["peg"] = _val.valuation.peg
                if _overrides:
                    ctx.stock_data[_tk] = _fv.model_copy(update=_overrides)

        # ── ⑤ Finnhub 목표주가 실시간 오버라이드 ──────────────────────────
        # yfinance targetMeanPrice는 구식 — Finnhub /stock/price-target 으로 교체
        if pos_tickers:
            try:
                from core.api_fetcher import fetch_finnhub_price_targets_bulk as _fpt_bulk
                _pt_map = await _fpt_bulk(pos_tickers)
                for _tk, _pt in _pt_map.items():
                    _fv = ctx.stock_data.get(_tk)
                    if _fv and _pt > 0:
                        ctx.stock_data[_tk] = _fv.model_copy(update={"target_price": _pt})
                if _pt_map:
                    append_audit(ctx.execution_id, 0, "info",
                                 data={"finnhub_price_target": "ok", "updated": len(_pt_map)})
                    log.info("sell_finnhub_price_target", updated=len(_pt_map))
            except Exception as _exc:
                log.warning("sell_finnhub_price_target_failed", error=str(_exc))

        # ── ⑥ Finnhub 내부자 거래 실시간 오버라이드 ────────────────────────
        # insider_trans_pct: yfinance 미지원 → Finnhub으로 교체
        if pos_tickers:
            try:
                from core.api_fetcher import fetch_finnhub_insider_bulk as _fi_bulk
                _insider_map = await _fi_bulk(pos_tickers)
                for _tk, _pct in _insider_map.items():
                    _fv = ctx.stock_data.get(_tk)
                    if _fv:
                        ctx.stock_data[_tk] = _fv.model_copy(
                            update={"insider_trans_pct": _pct}
                        )
                if _insider_map:
                    append_audit(ctx.execution_id, 0, "info",
                                 data={"finnhub_insider": "ok", "updated": len(_insider_map)})
                    log.info("sell_finnhub_insider_refreshed", tickers=len(_insider_map))
            except Exception as _exc:
                log.warning("sell_finnhub_insider_failed", error=str(_exc))

        # ── ② 어닝 캘린더 실시간 갱신 (Finnhub, 실패 시 summary.events 폴백) ─
        if ctx.summary_data and pos_tickers:
            try:
                from core.api_fetcher import fetch_earnings_calendar_bulk as _fecb
                _fresh_earn = await _fecb(pos_tickers[:20])
                if _fresh_earn:
                    _non_earn = [e for e in ctx.summary_data.events if "실적" not in e.type]
                    ctx.summary_data.events = _non_earn + _fresh_earn
                    append_audit(ctx.execution_id, 0, "info",
                                 data={"earnings_calendar_realtime": "ok",
                                       "count": len(_fresh_earn)})
                    log.info("sell_earnings_calendar_realtime", count=len(_fresh_earn))
            except Exception as _ec_exc:
                log.warning("sell_earnings_calendar_realtime_failed", error=str(_ec_exc))

        # Kavout AI 점수 파싱 (DATA_DIR 내 kavout_*.csv)
        try:
            from pathlib import Path as _Path
            data_dir = _Path(cfg.DATA_DIR)
            kavout_files = sorted(
                data_dir.glob("kavout_*.csv"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            if kavout_files:
                ctx.kavout_data = parse_kavout(kavout_files[0])
                log.info("sell_kavout_loaded", file=kavout_files[0].name,
                         tickers=len(ctx.kavout_data))
            else:
                ctx.kavout_data = {}
        except Exception as exc:
            log.warning("sell_kavout_warn", error=str(exc))
            ctx.kavout_data = {}

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 0, "completed", duration_ms=duration_ms,
                     data={
                         "positions": len(ctx.positions),
                         "summary_tickers": len(ctx.summary_data.tickers) if ctx.summary_data else 0,
                         "earnings": len(ctx.earnings_list),
                         "stock_data": len(ctx.stock_data),
                         "kavout": len(ctx.kavout_data),
                     })
        save_snapshot(ctx.execution_id, 0,
                      {"positions": [p.ticker for p in ctx.positions]}, duration_ms)

    # ─────────────────────────────────────────────────────────
    # Step 1: 포지션 건전성 점검
    # ─────────────────────────────────────────────────────────

    async def step_1_health(self, ctx: PipelineContext) -> None:
        """
        P&L 귀인 분석 + DTE 긴급도 + 무효화 조건 점검

        스펙: §9.4 Step 1
        """
        log.info("sell_step_1_start")
        start = time.monotonic()

        def _find_current_premium(pos: Position, opt_data: object) -> float | None:
            """옵션 체인에서 현재 포지션의 mid_price 조회"""
            if not opt_data or not opt_data.chain:  # type: ignore[union-attr]
                return None
            opt_type = "put" if pos.option_type == "롱풋" else "call"
            for entry in opt_data.chain:  # type: ignore[union-attr]
                if (entry.get("option_type") == opt_type
                        and abs(entry.get("strike", 0) - pos.strike) < 0.5
                        and str(entry.get("expiry", "")) == str(pos.expiry)):
                    mid = entry.get("mid_price")
                    return float(mid) if mid is not None else None
            return None

        health_results: dict[str, dict] = {}

        for pos in ctx.positions:
            try:
                ticker_data = ctx.summary_data.tickers.get(pos.ticker) if ctx.summary_data else None
                _fvd_s1 = ctx.stock_data.get(pos.ticker)
                current_price = (
                    (_fvd_s1.price if _fvd_s1 and _fvd_s1.price is not None else None)
                    or (ticker_data.technical.price if ticker_data else None)
                    or pos.entry_stock_price
                )

                # 현재 옵션 프리미엄 조회
                opt_data = ctx.summary_data.options.get(pos.ticker) if ctx.summary_data else None
                current_premium = _find_current_premium(pos, opt_data)
                _bs_used = False  # BS 폴백 사용 여부 추적

                # 트레일링 스탑 고점 추적
                if current_premium is not None and current_premium > pos.peak_premium:
                    pos.peak_premium = current_premium
                    pos.trailing_stop = current_premium * (1 - cfg.TRAILING_STOP_PCT / 100)

                # ── Fix 4: 실제 IV 조회 (체인에서 포지션 strike/expiry 매칭) ──────
                iv_actual = 0.5  # 폴백
                if opt_data and opt_data.chain:
                    for entry in opt_data.chain:
                        opt_t = "put" if pos.option_type == "롱풋" else "call"
                        if (entry.get("option_type", "").lower() == opt_t
                                and abs(entry.get("strike", 0) - pos.strike) < 1.0
                                and float(entry.get("iv", 0) or 0) > 0):
                            iv_actual = float(entry["iv"])
                            break
                    # strike 매칭 실패 시 chain 평균 IV 사용 (0.5보다 나음)
                    if iv_actual == 0.5:
                        chain_ivs = [
                            float(e.get("iv", 0))
                            for e in opt_data.chain
                            if float(e.get("iv", 0) or 0) > 0
                        ]
                        if chain_ivs:
                            iv_actual = sum(chain_ivs) / len(chain_ivs)

                # strike=0이면 Greeks 계산 불가 → 폴백 사용
                if pos.strike > 0 and current_price > 0:
                    greeks = calculate_greeks(
                        spot=current_price,
                        strike=pos.strike,
                        expiry_days=max(1, pos.dte),   # DTE=0 방어
                        iv=iv_actual,
                        option_type="call" if pos.option_type == "롱콜" else "put",
                    )
                else:
                    from types import SimpleNamespace
                    greeks = SimpleNamespace(
                        delta=0.5, gamma=0.02, theta=-0.05,
                        vega=0.10, rho=0.01, ivr=50.0,
                    )
                    log.warning(
                        "sell_step1_greeks_fallback",
                        ticker=pos.ticker,
                        strike=pos.strike,
                        spot=current_price,
                    )

                # 체인 매칭 실패 시 Black-Scholes 이론가로 current_premium 대체
                if current_premium is None and pos.strike > 0 and current_price > 0 and iv_actual > 0:
                    try:
                        import math as _math
                        from scipy.stats import norm as _norm
                        _r = cfg.RISK_FREE_RATE
                        _T = max(1, pos.dte) / 365.0
                        _d1 = (_math.log(current_price / pos.strike) + (_r + 0.5 * iv_actual ** 2) * _T) / (iv_actual * _math.sqrt(_T))
                        _d2 = _d1 - iv_actual * _math.sqrt(_T)
                        if pos.option_type == "롱콜":
                            _bs = current_price * _norm.cdf(_d1) - pos.strike * _math.exp(-_r * _T) * _norm.cdf(_d2)
                        else:
                            _bs = pos.strike * _math.exp(-_r * _T) * _norm.cdf(-_d2) - current_price * _norm.cdf(-_d1)
                        current_premium = float(round(max(0.01, _bs), 2))
                        _bs_used = True
                        log.info("sell_step1_bs_fallback", ticker=pos.ticker, bs_premium=current_premium, iv=iv_actual)
                    except Exception:
                        pass

                # ── Fix 3: P&L 귀인 — Delta + Theta + Vega ──────────────────────
                stock_move = current_price - pos.entry_stock_price
                delta_pnl = greeks.delta * stock_move * 100 * pos.remaining_contracts
                days_held = (date.today() - pos.entry_date).days
                theta_pnl = greeks.theta * days_held * 100 * pos.remaining_contracts

                # Vega P&L: 현재 프리미엄이 있으면 잔차(residual)로 역산
                # total_pnl = delta_pnl + theta_pnl + vega_pnl + (기타 고차항)
                # → vega_pnl ≈ total_pnl − delta_pnl − theta_pnl
                if current_premium is not None:
                    total_pnl = (
                        (current_premium - pos.entry_premium)
                        * 100 * pos.remaining_contracts
                    )
                    vega_pnl = total_pnl - delta_pnl - theta_pnl
                else:
                    # 현재 프리미엄 없음: vega × Δiv 근사 (Δiv 알 수 없어 노출만 표시)
                    vega_pnl = 0.0  # 데이터 부족 — vega_exposure로 대체 표시
                vega_exposure_per_pct = greeks.vega * 100 * pos.remaining_contracts  # 1% IV 변화당 손익

                # DTE 긴급도
                if pos.dte <= st.SELL_DTE_CRITICAL:
                    urgency = "위급"
                elif pos.dte <= st.SELL_DTE_WARNING:
                    urgency = "주의"
                elif pos.dte <= st.SELL_DTE_NORMAL:
                    urgency = "보통"
                else:
                    urgency = "안정"

                # 무효화 조건 점검
                flags: list[str] = []
                invalid_conditions = 0
                for cond in pos.invalidation_conditions:
                    # 조건 텍스트를 기반으로 간단한 판정 (실제로는 LLM 분석)
                    if "20MA 아래" in cond and ticker_data:
                        if ticker_data.technical.price < ticker_data.technical.ma20:
                            invalid_conditions += 1

                if invalid_conditions >= 2:
                    flags.append("청산_권고_신호")
                elif invalid_conditions == 1:
                    flags.append("주의_신호")
                else:
                    flags.append("근거_유효")

                if urgency == "위급":
                    flags.append("청산_권고_신호")

                health_results[_pos_key(pos)] = {
                    "delta_pnl": round(delta_pnl, 2),
                    "theta_pnl": round(theta_pnl, 2),
                    "vega_pnl": round(vega_pnl, 2),
                    "vega_exposure_per_pct": round(vega_exposure_per_pct, 2),
                    "iv_used": round(iv_actual, 4),
                    "dte_urgency": urgency,
                    "flags": flags,
                    "current_price": current_price,
                    "current_premium": current_premium,
                    "premium_source": "bs_estimate" if _bs_used else "chain",
                    "greeks": greeks.model_dump() if hasattr(greeks, "model_dump") else vars(greeks),
                }

            except Exception as exc:
                # 포지션 데이터 불완전 → 이 포지션만 건너뛰고 나머지 계속 처리
                log.warning("sell_step1_position_error", ticker=pos.ticker, error=str(exc))
                # DTE 기반 긴급도 폴백
                dte_urgency = (
                    "위급" if pos.dte <= st.SELL_DTE_CRITICAL else
                    "주의" if pos.dte <= st.SELL_DTE_WARNING else
                    "보통" if pos.dte <= st.SELL_DTE_NORMAL else "안정"
                )
                health_results[_pos_key(pos)] = {
                    "delta_pnl": 0.0,
                    "theta_pnl": 0.0,
                    "vega_pnl": 0.0,
                    "vega_exposure_per_pct": 0.0,
                    "iv_used": 0.5,
                    "dte_urgency": dte_urgency,
                    "flags": ["데이터_불완전", "수동확인_필요"],
                    "current_price": pos.entry_stock_price,
                    "current_premium": None,
                    "greeks": {
                        "delta": 0.5, "gamma": 0.02, "theta": -0.05,
                        "vega": 0.10, "rho": 0.01, "ivr": 50.0,
                    },
                }

        # Step 4·7·8·11에서 참조할 수 있도록 전용 필드에 저장
        ctx.sell_health = health_results

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 1, "completed", duration_ms=duration_ms)
        save_snapshot(ctx.execution_id, 1, health_results, duration_ms)
        log.info("sell_step_1_done")

    # ─────────────────────────────────────────────────────────
    # Step 2: 시장 레짐 분석 (진입 시 비교)
    # ─────────────────────────────────────────────────────────

    async def step_2_regime(self, ctx: PipelineContext) -> None:
        """
        현재 레짐 vs 진입 시 레짐 비교

        역전 패턴 감지:
          bullish 진입 → bearish 현재  : 롱콜 포지션 위험 → 청산_권고 플래그
          bearish 진입 → bullish 현재  : 롱풋 포지션 위험 → 청산_권고 플래그

        결과는 ctx.sell_regime_flags에 {ticker: "REGIME_REVERSED" | "REGIME_OK"} 저장

        스펙: §9.4 Step 2
        """
        log.info("sell_step_2_start")
        start = time.monotonic()

        # ── ① 매크로 지표 실시간 갱신 (레짐 판단 전에 수행) ────────────────
        if ctx.summary_data:
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
                    log.info("sell_macro_realtime_refreshed", fields=len(macro_updates))
            except Exception as _exc:
                log.warning("sell_macro_realtime_failed", error=str(_exc))

        # ── SPY 4H 지표 fetch (레짐 방향 판정 정밀화) ──────────────────────────
        # 매수 파이프라인과 동일: SPY 4H DI+/DI- + MACD Hist → analyze_market_regime() 단기 신호 강화
        if ctx.summary_data:
            try:
                from core.api_fetcher import _calc_intraday_indicators
                _spy_4h = await asyncio.to_thread(_calc_intraday_indicators, "SPY")
                _spy_4h_upd: dict = {}
                if _spy_4h.get("di_plus_4h") is not None:
                    _spy_4h_upd["spy_di_plus_4h"]   = _spy_4h["di_plus_4h"]
                if _spy_4h.get("di_minus_4h") is not None:
                    _spy_4h_upd["spy_di_minus_4h"]  = _spy_4h["di_minus_4h"]
                if _spy_4h.get("macd_hist_4h") is not None:
                    _spy_4h_upd["spy_macd_hist_4h"] = _spy_4h["macd_hist_4h"]
                if _spy_4h_upd:
                    ctx.summary_data.macro = ctx.summary_data.macro.model_copy(
                        update=_spy_4h_upd
                    )
                    append_audit(ctx.execution_id, 2, "info",
                                 data={"spy_4h_realtime": "ok",
                                       "fields": list(_spy_4h_upd.keys())})
                    log.info("sell_spy_4h_refreshed",
                             di_p=_spy_4h_upd.get("spy_di_plus_4h"),
                             di_n=_spy_4h_upd.get("spy_di_minus_4h"),
                             macd_h=_spy_4h_upd.get("spy_macd_hist_4h"))
            except Exception as _spy4h_exc:
                log.warning("sell_spy_4h_fetch_failed", error=str(_spy4h_exc))

        if ctx.summary_data:
            ctx.regime = analyze_market_regime(ctx.summary_data)

        # ── 레짐 역전 비교 ────────────────────────────────────────────────
        regime_flags: dict[str, str] = {}
        # LLM 레짐 추론 결과 저장 (Step 11 보고서용)
        regime_infer_results: dict[str, dict] = {}

        current_regime = ctx.regime.regime_status if ctx.regime else ""
        _spy_trend = ""
        _vix_val = 0.0
        _adx_val = 0.0
        _regime_conf = ""
        _risk_factors: list[str] = []
        if ctx.regime:
            _regime_conf = getattr(ctx.regime, "regime_confidence", "")
            _risk_factors = list(getattr(ctx.regime, "risk_factors", []) or [])
        if ctx.summary_data:
            _macro = getattr(ctx.summary_data, "macro", None)
            if _macro:
                _vix_val = getattr(_macro, "vix", 0.0) or 0.0
                _adx_val = getattr(_macro, "adx", 0.0) or 0.0
            # SPY 추세 (20MA 위/아래)
            _spy_td = ctx.summary_data.tickers.get("SPY")
            if _spy_td:
                _spy_price = getattr(_spy_td.technical, "price", 0.0) or 0.0
                _spy_ma20  = getattr(_spy_td.technical, "ma20",  0.0) or 0.0
                _spy_trend = "above_20ma" if (_spy_price > _spy_ma20 > 0) else "below_20ma"

        # 레짐 역전 판정: 진입 방향과 현재 레짐이 반대이면 위험
        _LONG_HOSTILE  = {"bearish", "unfavorable"}   # 롱콜에 불리
        _SHORT_HOSTILE = {"bullish"}                   # 롱풋에 불리

        # ── 티커 단위 LLM 결과 캐시 (같은 티커 여러 포지션 → LLM 1회만) ──
        _ticker_infer_cache: dict[str, dict] = {}   # ticker → infer_result
        _ticker_regime_cache: dict[str, str] = {}   # ticker → entry_regime

        for pos in ctx.positions:
            entry_regime = getattr(pos, "entry_regime", "") or ""

            # entry_regime 없을 때: thesis/entry_rationale LLM 추론 (티커별 1회)
            if not entry_regime:
                _thesis_text    = (pos.thesis or "").strip()
                _rationale_text = (pos.entry_rationale or "").strip()
                if _thesis_text or _rationale_text:
                    # 같은 티커면 이미 추론한 결과 재사용
                    if pos.ticker in _ticker_infer_cache:
                        _infer_result = _ticker_infer_cache[pos.ticker]
                        entry_regime  = _ticker_regime_cache.get(pos.ticker, "")
                        log.info("regime_infer_cache_hit", ticker=pos.ticker,
                                 inferred=entry_regime)
                    else:
                        try:
                            _infer_result = await analyze_with_llm(
                                template_name="sell_step2_regime_infer",
                                template_vars={
                                    "ticker": pos.ticker,
                                    "thesis": _thesis_text or "—",
                                    "entry_rationale": _rationale_text or "—",
                                    "current_regime": current_regime or "unknown",
                                    "spy_trend": _spy_trend or "unknown",
                                    "vix": round(_vix_val, 1),
                                    "adx": round(_adx_val, 1),
                                    "regime_confidence": _regime_conf or "unknown",
                                    "risk_factors": ", ".join(_risk_factors[:3]) if _risk_factors else "없음",
                                },
                            )
                            if isinstance(_infer_result, dict):
                                entry_regime = _infer_result.get("inferred_entry_regime", "") or ""
                                # LLM이 ":bullish" 같은 콜론 접두어를 반환하는 경우 제거
                                entry_regime = entry_regime.lstrip(":").strip()
                                _ticker_infer_cache[pos.ticker] = _infer_result
                                _ticker_regime_cache[pos.ticker] = entry_regime
                                log.info(
                                    "regime_inferred_from_thesis",
                                    ticker=pos.ticker,
                                    inferred=entry_regime,
                                    comparison=_infer_result.get("regime_comparison"),
                                    validity=_infer_result.get("thesis_validity"),
                                )
                        except Exception as _infer_exc:
                            log.warning("regime_infer_llm_failed",
                                        ticker=pos.ticker, error=str(_infer_exc))
                            _infer_result = {}
                    # pos_key별로 저장 (같은 추론 결과를 각 포지션 key에 매핑)
                    if _infer_result:
                        regime_infer_results[_pos_key(pos)] = _infer_result

            if not entry_regime or not current_regime:
                regime_flags[_pos_key(pos)] = "REGIME_UNKNOWN"
                continue

            is_long_call = pos.option_type == "롱콜"
            reversed_ = (
                (is_long_call and current_regime in _LONG_HOSTILE)
                or (not is_long_call and current_regime in _SHORT_HOSTILE)
            )

            # LLM 추론 결과가 있으면 그것도 함께 반영
            _llm_comparison = (regime_infer_results.get(_pos_key(pos)) or {}).get("regime_comparison", "")
            if reversed_ or _llm_comparison == "REVERSED":
                regime_flags[_pos_key(pos)] = "REGIME_REVERSED"
                log.warning(
                    "regime_reversed",
                    ticker=pos.ticker,
                    entry=entry_regime,
                    current=current_regime,
                    option_type=pos.option_type,
                    llm_comparison=_llm_comparison,
                )
            else:
                regime_flags[_pos_key(pos)] = "REGIME_OK"

        ctx.sell_regime_flags = regime_flags          # Step 7이 참조
        ctx.sell_regime_infer = regime_infer_results  # Step 11 보고서용

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 2, "completed", duration_ms=duration_ms,
                     data={
                         "regime": current_regime,
                         "reversed": [t for t, v in regime_flags.items() if v == "REGIME_REVERSED"],
                     })
        save_snapshot(ctx.execution_id, 2,
                      {"regime": current_regime, "regime_flags": regime_flags}, duration_ms)

    # ─────────────────────────────────────────────────────────
    # Step 3: 기술 분석
    # ─────────────────────────────────────────────────────────

    async def step_3_technical(self, ctx: PipelineContext) -> None:
        """
        보유 종목 추세 유지/약화/붕괴 판정 + 뉴스 조회 + LLM 감성 분석

        스펙: §9.4 Step 3
        """
        log.info("sell_step_3_start")
        start = time.monotonic()

        pos_tickers = list({p.ticker for p in ctx.positions})

        # ── ⑦ 기술 데이터 브릿지 (루프 진입 전) ─────────────────────────────
        # yfinance/Finnhub 실시간 데이터 → summary_data.technical 반영.
        # calculate_technical_score()가 실시간 기술지표를 사용하게 됨.
        if ctx.summary_data:
            _bridge_count = 0
            for _tk in pos_tickers:
                _fv = ctx.stock_data.get(_tk)
                if not _fv or _tk not in ctx.summary_data.tickers:
                    continue
                _td = ctx.summary_data.tickers[_tk]
                _tech = _td.technical
                _upd: dict = {}

                if _fv.price       is not None: _upd["price"]            = _fv.price
                if _fv.change_pct  is not None: _upd["change_pct"]       = _fv.change_pct
                if _fv.rsi14       is not None: _upd["rsi14"]            = _fv.rsi14
                if _fv.rel_volume  is not None: _upd["avg_volume_ratio"] = _fv.rel_volume
                if _fv.adx         is not None: _upd["adx14"]            = _fv.adx
                if _fv.di_plus     is not None: _upd["di_plus"]          = _fv.di_plus
                if _fv.di_minus    is not None: _upd["di_minus"]         = _fv.di_minus
                if _fv.sma10_val   is not None: _upd["ma10"]             = _fv.sma10_val
                if _fv.sma5_val    is not None: _upd["ma5"]              = _fv.sma5_val
                if _fv.sma20_val   is not None: _upd["ma20"]             = _fv.sma20_val
                if _fv.sma50_val   is not None: _upd["ma50"]             = _fv.sma50_val
                if _fv.sma60_val   is not None: _upd["ma60"]             = _fv.sma60_val
                if _fv.sma200_val  is not None: _upd["ma200"]            = _fv.sma200_val
                if _fv.bb_upper    is not None: _upd["bb_upper"]         = _fv.bb_upper
                if _fv.bb_mid      is not None: _upd["bb_mid"]           = _fv.bb_mid
                if _fv.bb_lower    is not None: _upd["bb_lower"]         = _fv.bb_lower
                if _fv.price and _fv.bb_upper and _fv.bb_lower:
                    if   _fv.price >= _fv.bb_upper: _upd["bb_position"] = "upper_break"
                    elif _fv.price <= _fv.bb_lower: _upd["bb_position"] = "lower_break"
                    else:                            _upd["bb_position"] = "mid"
                if _fv.macd_line   is not None: _upd["macd_line"]        = _fv.macd_line
                if _fv.macd_signal is not None: _upd["macd_signal"]      = _fv.macd_signal
                if _fv.macd_hist   is not None: _upd["macd_histogram"]   = _fv.macd_hist
                if _fv.macd_line is not None and _fv.macd_signal is not None:
                    _upd["macd_cross"] = (
                        "golden" if _fv.macd_line > _fv.macd_signal else "death"
                    )
                if _fv.pivot_s1    is not None: _upd["support1"]         = _fv.pivot_s1
                if _fv.pivot_s2    is not None: _upd["support2"]         = _fv.pivot_s2
                if _fv.pivot_r1    is not None: _upd["resistance1"]      = _fv.pivot_r1
                if _fv.pivot_r2    is not None: _upd["resistance2"]      = _fv.pivot_r2

                if _upd:
                    ctx.summary_data.tickers[_tk] = _td.model_copy(
                        update={"technical": _tech.model_copy(update=_upd)}
                    )
                    _bridge_count += 1

            append_audit(ctx.execution_id, 3, "info",
                         data={"technical_bridge": "ok", "bridged": _bridge_count})
            log.info("sell_technical_bridge_done", bridged=_bridge_count,
                     total=len(pos_tickers))

        # ── RSS 시장 피드 1회 수집 (루프 밖) ─────────────────────────────────
        _market_rss_news: list[dict] = []
        _rss_config: dict = {}
        try:
            import json as _json
            from pathlib import Path as _Path
            _rss_file = _Path(cfg.RSS_FEEDS_FILE)
            if _rss_file.exists():
                _rss_config = _json.loads(_rss_file.read_text(encoding="utf-8"))
        except Exception as _rss_cfg_exc:
            log.warning("sell_rss_config_load_fail", error=str(_rss_cfg_exc))

        _market_feeds: list[str] = [
            u for u in _rss_config.get("market", [])
            if isinstance(u, str) and not u.startswith("_")
        ]
        if _market_feeds:
            _market_rss_news = await _collect_rss_feeds(
                _market_feeds, label="market", max_per_feed=50
            )

        # ── 티커 단위 뉴스/감성 캐시 (같은 티커 여러 포지션 → LLM 1회만) ──
        _ticker_news_cache:      dict[str, list[dict]] = {}  # ticker → news_items
        _ticker_sentiment_cache: dict[str, dict]       = {}  # ticker → llm_result

        for pos in ctx.positions:
            direction = "long_call" if pos.option_type == "롱콜" else "long_put"

            # ── 기술 점수 계산 ────────────────────────────────────────────
            # K-Score(QMP 시총 순위)는 신호 보정 제외 — 보고서 표시·타이브레이커 전용
            if ctx.summary_data:
                score = calculate_technical_score(
                    ticker=pos.ticker,
                    direction=direction,
                    summary=ctx.summary_data,
                )
                ctx.technical_scores[_pos_key(pos)] = score

                # ── ⑨ 애널리스트 추천 시그널 보정 ────────────────────────
                _fvd_s = ctx.stock_data.get(pos.ticker)
                if _fvd_s:
                    _asig = 0
                    _asc  = 0.0
                    if _fvd_s.recom is not None:
                        if _fvd_s.recom <= st.ANALYST_BUY_THRESHOLD:
                            _asig += 1
                        elif _fvd_s.recom >= st.ANALYST_SELL_THRESHOLD:
                            _asig -= 1
                            _asc  += st.ANALYST_SELL_SCORE_PENALTY
                    # 롱풋은 short_float가 높으면 불리 (숏 커버링이 반대 방향 압력)
                    if (direction == "long_put"
                            and _fvd_s.short_float_pct is not None
                            and _fvd_s.short_float_pct >= st.SHORT_FLOAT_SQUEEZE_THRESHOLD):
                        _asig -= 1
                    if _asig != 0 or _asc != 0:
                        ctx.technical_scores[_pos_key(pos)] = score.model_copy(update={
                            "signal_count": max(0, score.signal_count + _asig),
                            "final_score":  max(0.0, min(100.0, score.final_score + _asc)),
                        })
                        score = ctx.technical_scores[_pos_key(pos)]

                # ── Kavout 시그널 보정 (매수 파이프라인과 동일 기준) ──────────────────
                # K-Score는 보고서 표시·타이브레이커 전용; Stock Rank/Quality/ROIC/Return으로 점수 보정
                _krow_s3 = ctx.kavout_data.get(pos.ticker)
                if _krow_s3:
                    _sr_score  = float(_krow_s3.stock_rank_score or 0.0) if hasattr(_krow_s3, "stock_rank_score") else float((_krow_s3 or {}).get("stock_rank_score", 0.0))
                    _quality   = float(_krow_s3.quality_score    or 0.0) if hasattr(_krow_s3, "quality_score")    else float((_krow_s3 or {}).get("quality_score", 0.0))
                    _roic      = float(_krow_s3.roic             or 0.0) if hasattr(_krow_s3, "roic")             else float((_krow_s3 or {}).get("roic", 0.0))
                    _ret_12m   = float(_krow_s3.return_12m       or 0.0) if hasattr(_krow_s3, "return_12m")       else float((_krow_s3 or {}).get("return_12m", 0.0))

                    _kav_sig = 0
                    _kav_sc  = 0.0
                    if _sr_score >= st.KAVOUT_SR_HIGH_SCORE:
                        _kav_sig += st.KAVOUT_SR_HIGH_SIGNAL_BONUS
                        _kav_sc  += st.KAVOUT_SR_HIGH_SCORE_BONUS
                    elif _sr_score > 0 and _sr_score <= st.KAVOUT_SR_LOW_SCORE:
                        _kav_sig += st.KAVOUT_SR_LOW_SIGNAL_PENALTY
                        _kav_sc  += st.KAVOUT_SR_LOW_SCORE_PENALTY
                    if _quality >= st.KAVOUT_QUALITY_THRESHOLD:
                        _kav_sig += st.KAVOUT_QUALITY_SIGNAL_BONUS
                        _kav_sc  += st.KAVOUT_QUALITY_SCORE_BONUS
                    if _roic >= st.KAVOUT_ROIC_THRESHOLD:
                        _kav_sig += st.KAVOUT_ROIC_SIGNAL_BONUS
                        _kav_sc  += st.KAVOUT_ROIC_SCORE_BONUS
                    if _ret_12m >= st.KAVOUT_RETURN_12M_THRESHOLD:
                        _kav_sig += st.KAVOUT_RETURN_SIGNAL_BONUS
                        _kav_sc  += st.KAVOUT_RETURN_SCORE_BONUS

                    if _kav_sig != 0 or _kav_sc != 0:
                        _cur_s3 = ctx.technical_scores.get(_pos_key(pos)) or score
                        ctx.technical_scores[_pos_key(pos)] = _cur_s3.model_copy(update={
                            "signal_count": max(0, _cur_s3.signal_count + _kav_sig),
                            "final_score":  max(0.0, min(100.0, _cur_s3.final_score + _kav_sc)),
                        })

            # ── 주봉 방향 역전 청산 플래그 ─────────────────────────────────────
            # 매수의 _stock_direction() 역방향 적용:
            # 롱콜인데 주봉 강한 하락(ADX≥30, DI->>DI+) → ctx.sell_health에 플래그 기록
            # 롱풋인데 주봉 강한 상승(ADX≥30, DI+>>DI-) → ctx.sell_health에 플래그 기록
            _fvd_wk = ctx.stock_data.get(pos.ticker)
            if _fvd_wk:
                _wdip = getattr(_fvd_wk, "weekly_di_plus",  None)
                _wdin = getattr(_fvd_wk, "weekly_di_minus", None)
                _wadx = getattr(_fvd_wk, "weekly_adx",      None)
                if (_wdip is not None and _wdin is not None
                        and _wadx is not None and _wadx >= st.STOCK_DIR_WEEKLY_ADX_MIN):
                    _is_long_pos = (pos.option_type == "롱콜")
                    _weekly_reversed = (
                        (_is_long_pos     and _wdin > _wdip * st.STOCK_DIR_WEEKLY_DI_RATIO)
                        or (not _is_long_pos and _wdip > _wdin * st.STOCK_DIR_WEEKLY_DI_RATIO)
                    )
                    if _weekly_reversed:
                        _h_wk = ctx.sell_health.get(_pos_key(pos), {})
                        _fl_wk = list(_h_wk.get("flags", []))
                        if "주봉역방향_청산권고" not in _fl_wk:
                            _fl_wk.append("주봉역방향_청산권고")
                        ctx.sell_health[_pos_key(pos)] = {**_h_wk, "flags": _fl_wk}
                        log.warning("sell_weekly_dir_reversed",
                                    ticker=pos.ticker, option_type=pos.option_type,
                                    wadx=round(_wadx, 1), wdip=round(_wdip, 1),
                                    wdin=round(_wdin, 1))

            # ── ⑩⑪⑫ RSS + DDG 3쿼리 + Brave 뉴스 수집 + LLM 감성 분석 ──
            try:
                ticker_data = (
                    ctx.summary_data.tickers.get(pos.ticker) if ctx.summary_data else None
                )
                current_price = ticker_data.technical.price if ticker_data else 0.0

                if pos.ticker in _ticker_news_cache:
                    # 같은 티커: 캐시된 뉴스·감성 재사용
                    news_items = _ticker_news_cache[pos.ticker]
                    if pos.ticker in _ticker_sentiment_cache:
                        ctx.sentiment_results[_pos_key(pos)] = _ticker_sentiment_cache[pos.ticker]
                        log.info("sell_step3_sentiment_cache_hit", ticker=pos.ticker)
                else:
                    # ⑩ 종목별 RSS
                    _ticker_feeds: list[str] = [
                        u for u in _rss_config.get("tickers", {}).get(pos.ticker, [])
                        if isinstance(u, str)
                    ]
                    if not _ticker_feeds:
                        _ticker_feeds = [
                            f"https://feeds.finance.yahoo.com/rss/2.0/headline"
                            f"?s={pos.ticker}&region=US&lang=en-US"
                        ]
                    _rss_items = await _collect_rss_feeds(
                        _ticker_feeds, label=pos.ticker, max_per_feed=20
                    )

                    # ⑪ DDG 3쿼리 병렬
                    _ddg_queries = [
                        f"{pos.ticker} stock market news analysis",
                        f"{pos.ticker} options unusual activity IV analysis",
                        f"{pos.ticker} earnings guidance sector outlook",
                    ]
                    _ddg_results = await asyncio.gather(
                        *[call_ddg_search(q, num_results=8) for q in _ddg_queries],
                        return_exceptions=True,
                    )
                    news_items = list(_rss_items)
                    for _r in _ddg_results:
                        if isinstance(_r, list):
                            news_items.extend(_r)

                    # ⑫ Brave Search 보완 (API 키 있을 때만)
                    try:
                        from core.llm import call_brave_search
                        _brave = await call_brave_search(
                            f"{pos.ticker} stock options news catalyst", count=5
                        )
                        news_items.extend(_brave)
                    except Exception:
                        pass

                    # 시장 RSS 상위 20개 보완
                    def _dedup(items: list[dict]) -> list[dict]:
                        _seen: set[str] = set()
                        _out: list[dict] = []
                        for _it in items:
                            _k = _it.get("url") or _it.get("title", "")
                            if _k and _k not in _seen:
                                _seen.add(_k)
                                _out.append(_it)
                        return _out

                    _market_sample = _dedup(_market_rss_news)[:20]
                    news_items = _dedup(news_items + _market_sample)

                    _ticker_news_cache[pos.ticker] = news_items
                    log.info("sell_step3_news_collected",
                             ticker=pos.ticker, count=len(news_items))

                    # summary_data에 뉴스 추가
                    if ctx.summary_data and pos.ticker in ctx.summary_data.tickers:
                        ctx.summary_data.tickers[pos.ticker].news.extend(news_items)

                    if news_items:
                        # K어닝 분석 파일에서 실적 요약 추출 (매수 파이프라인과 동일)
                        _sell_earnings_summary = ""
                        try:
                            from core.parsers import _parse_earnings_file as _sell_pef
                            _sell_k_candidates = [
                                ctx.paths.k_earnings_analysis_today,
                                ctx.paths.k_earnings_analysis,
                            ]
                            for _sell_kp in _sell_k_candidates:
                                if not _sell_kp.exists():
                                    continue
                                _sell_k_eas = _sell_pef(_sell_kp)
                                _sell_k_match = next((e for e in _sell_k_eas if e.ticker == pos.ticker), None)
                                if _sell_k_match and (
                                    _sell_k_match.business_model or _sell_k_match.strategy_changes
                                    or _sell_k_match.management_confidence
                                ):
                                    _sell_parts: list[str] = []
                                    if _sell_k_match.quarter:
                                        _sell_parts.append(f"최근분기: {_sell_k_match.quarter}")
                                    if _sell_k_match.business_model:
                                        _sell_parts.append(f"사업모델: {_sell_k_match.business_model[:600]}")
                                    if _sell_k_match.strategy_changes:
                                        _sell_parts.append(f"전략변화: {_sell_k_match.strategy_changes[:500]}")
                                    if _sell_k_match.management_confidence:
                                        _sell_parts.append(f"경영진확신도: {_sell_k_match.management_confidence[:400]}")
                                    _sell_earnings_summary = (" | ".join(_sell_parts))[:2000]
                                    break
                        except Exception as _sell_k_exc:
                            log.debug("sell_k_earnings_summary_skip", ticker=pos.ticker, error=str(_sell_k_exc))

                        _cache_key = f"{pos.ticker}_{date.today()}_sell_research"
                        llm_result = await analyze_with_llm(
                            template_name="buy_step3_research",
                            template_vars={
                                "ticker": pos.ticker,
                                "direction": direction,
                                "price": round(current_price, 2),
                                "news": news_items[:50],
                                "earnings_summary": _sell_earnings_summary,
                            },
                            cache_key=_cache_key,
                        )
                        if isinstance(llm_result, dict):
                            _ticker_sentiment_cache[pos.ticker] = llm_result
                            ctx.sentiment_results[_pos_key(pos)] = llm_result
                            log.info(
                                "sell_step3_sentiment_done",
                                ticker=pos.ticker,
                                verdict=llm_result.get("debate_verdict", "?"),
                                news_count=len(news_items),
                            )

                # ── ⑬ LLM technical narrative ────────────────────────────
                try:
                    _ts = ctx.technical_scores.get(_pos_key(pos))
                    _td2 = ctx.summary_data.tickers.get(pos.ticker) if ctx.summary_data else None
                    _fv2 = _td2.technical if _td2 else None
                    if _fv2 and _ts:
                        def _g(obj, *keys, default="N/A"):
                            for _k in keys:
                                _v = getattr(obj, _k, None)
                                if _v is not None and _v != 0.0:
                                    return _v
                            return default
                        _sell_nar_sd = ctx.stock_data.get(pos.ticker) if ctx.stock_data else None
                        def _gd(key, default="N/A"):
                            _v2 = getattr(_sell_nar_sd, key, None) if _sell_nar_sd else None
                            return _v2 if _v2 is not None else default
                        _sell_nar_opt = (
                            ctx.summary_data.options.get(pos.ticker) if ctx.summary_data else None
                        )
                        _sell_nar_oa = {
                            "call_wall": getattr(_sell_nar_opt, "call_wall", None),
                            "put_wall":  getattr(_sell_nar_opt, "put_wall", None),
                            "gex_flip":  getattr(_sell_nar_opt, "gex_flip", None),
                        }
                        _nar_vars = {
                            "ticker":       pos.ticker,
                            "direction":    direction,
                            "price":        _g(_fv2, 'price', default=0),
                            "rsi":          _g(_fv2, 'rsi14'),
                            "adx":          _g(_fv2, 'adx14', 'adx'),
                            "rvol":         _g(_fv2, 'avg_volume_ratio', 'rel_volume'),
                            "sma5_val":     _g(_fv2, 'ma5', 'sma5_val'),
                            "sma20_val":    _g(_fv2, 'ma20', 'sma20_val'),
                            "sma50_val":    _g(_fv2, 'ma50', 'sma50_val'),
                            "sma200_val":   _g(_fv2, 'ma200', 'sma200_val'),
                            "bb_upper":     _g(_fv2, 'bb_upper'),
                            "bb_mid":       _g(_fv2, 'bb_mid'),
                            "bb_lower":     _g(_fv2, 'bb_lower'),
                            "macd_line":    _g(_fv2, 'macd_line'),
                            "macd_signal":  _g(_fv2, 'macd_signal'),
                            "macd_hist":    _g(_fv2, 'macd_histogram', 'macd_hist'),
                            "atr":          _g(_fv2, 'atr'),
                            "di_plus":      _g(_fv2, 'di_plus'),
                            "di_minus":     _g(_fv2, 'di_minus'),
                            "pivot":        _g(_fv2, 'pivot'),
                            "pivot_r1":     _g(_fv2, 'resistance1', 'pivot_r1'),
                            "pivot_r2":     _g(_fv2, 'resistance2', 'pivot_r2'),
                            "pivot_s1":     _g(_fv2, 'support1', 'pivot_s1'),
                            "pivot_s2":     _g(_fv2, 'support2', 'pivot_s2'),
                            "w52_high_pct": _g(_fv2, 'w52_high_pct'),
                            "w52_low_pct":  _g(_fv2, 'w52_low_pct'),
                            "ma_alignment": _ts.ma_alignment,
                            "adx_score":    _ts.adx_score,
                            "rsi_score":    _ts.rsi_score,
                            "macd_score":   _ts.macd_score,
                            "rvol_score":   _ts.rvol_score,
                            "signal_count": _ts.signal_count,
                            "confidence_pct": round(_ts.signal_count / 8 * 100),
                            "regime_status": ctx.regime.regime_status if ctx.regime else "unknown",
                            "fib_38_2":    _gd("fib_38_2"),
                            "fib_50":      _gd("fib_50_0"),
                            "fib_61_8":    _gd("fib_61_8"),
                            "fib_ext_100": _gd("fib_ext_100"),
                            "fib_ext_162": _gd("fib_ext_162"),
                            "cam_l3":      _gd("cam_l3"),
                            "cam_l4":      _gd("cam_l4"),
                            "cam_h3":      _gd("cam_h3"),
                            "cam_h4":      _gd("cam_h4"),
                            "psar":        _gd("parabolic_sar"),
                            "sar_dir":     _gd("sar_direction"),
                            "ema9":        _gd("ema9"),
                            "ema21":       _gd("ema21"),
                            "ema50":       _gd("ema50"),
                            "ema100":      _gd("ema100"),
                            "ema200":      _gd("ema200"),
                            "keltner_upper": _gd("keltner_upper"),
                            "keltner_lower": _gd("keltner_lower"),
                            "hv30":        _gd("hv30"),
                            "hv_move_5d":  _gd("hv_move_5d"),
                            "hv_move_15d": _gd("hv_move_15d"),
                            "monthly_pivot":    _gd("monthly_pivot"),
                            "monthly_pivot_r1": _gd("monthly_pivot_r1"),
                            "monthly_pivot_r2": _gd("monthly_pivot_r2"),
                            "monthly_pivot_s1": _gd("monthly_pivot_s1"),
                            "monthly_pivot_s2": _gd("monthly_pivot_s2"),
                            "call_wall": _sell_nar_oa.get("call_wall") or "N/A",
                            "put_wall":  _sell_nar_oa.get("put_wall")  or "N/A",
                            "gex_flip":  _sell_nar_oa.get("gex_flip")  or "N/A",
                            "w52_high":       _gd("w52_high"),
                            "w52_low":        _gd("w52_low"),
                            "fvg_bull_top":    _gd("fvg_bull_top"),
                            "fvg_bull_bottom": _gd("fvg_bull_bottom"),
                            "fvg_bear_top":    _gd("fvg_bear_top"),
                            "fvg_bear_bottom": _gd("fvg_bear_bottom"),
                            "gap_up_fill":   _gd("gap_up_fill"),
                            "gap_down_fill": _gd("gap_down_fill"),
                        }
                        _nar_key = f"{pos.ticker}_{date.today()}_sell_tech_narrative"
                        _nar_result = await analyze_with_llm(
                            template_name="buy_step3b_technical_narrative",
                            template_vars=_nar_vars,
                            cache_key=_nar_key,
                        )
                        if _pos_key(pos) not in ctx.sentiment_results:
                            ctx.sentiment_results[_pos_key(pos)] = _default_sentiment()
                        ctx.sentiment_results[_pos_key(pos)]["technical_narrative"] = _nar_result
                except Exception as _nar_exc:
                    log.debug("sell_tech_narrative_skip", ticker=pos.ticker, error=str(_nar_exc))

            except Exception as exc:
                log.warning("sell_step3_news_failed", ticker=pos.ticker, error=str(exc))

            if _pos_key(pos) not in ctx.sentiment_results:
                ctx.sentiment_results[_pos_key(pos)] = _default_sentiment()

            await asyncio.sleep(0.3)  # Rate limit 방지

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 3, "completed", duration_ms=duration_ms)
        save_snapshot(ctx.execution_id, 3,
                      {t: s.final_score for t, s in ctx.technical_scores.items()}, duration_ms)

    # ─────────────────────────────────────────────────────────
    # Step 4~6: Thesis 검증, Devil's Advocate, IV 분석 (통합)
    # ─────────────────────────────────────────────────────────

    async def step_4_thesis(self, ctx: PipelineContext) -> None:
        """
        스펙: §9.4 Step 4

        무효화 조건 LLM 분석 — §9.4 Step 4

        Step 1의 deterministic 체크를 넘어, LLM이 각 포지션의
        invalidation_conditions를 현재 시장 데이터와 대조하여
        "유지|약화|무효" 판정 + 청산 권고 플래그를 생성한다.
        결과는 ctx.errors[-4]에 저장되어 Step 7·10에서 활용된다.
        """
        log.info("sell_step_4_start")
        start = time.monotonic()

        health: dict = ctx.sell_health  # Step 1에서 저장한 건전성 결과
        thesis_results: dict[str, dict] = {}
        _ticker_thesis_cache: dict[str, dict] = {}  # 동일 티커 LLM 중복 방지

        for pos in ctx.positions:
            h = health.get(_pos_key(pos), {})
            ticker_data = (
                ctx.summary_data.tickers.get(pos.ticker) if ctx.summary_data else None
            )

            # 동일 티커 캐시 히트
            if pos.ticker in _ticker_thesis_cache:
                log.info("sell_step4_thesis_cache_hit", ticker=pos.ticker)
                cached = _ticker_thesis_cache[pos.ticker]
                thesis_results[_pos_key(pos)] = {**cached}
                continue

            # LLM 호출: sell_step1_health 템플릿 (무효화 조건 점검)
            try:
                llm_result = await analyze_with_llm(
                    template_name="sell_step1_health",
                    template_vars={
                        "ticker": pos.ticker,
                        "option_type": pos.option_type,
                        "strike": pos.strike,
                        "expiry": pos.expiry.isoformat(),
                        "dte": pos.dte,
                        "entry_premium": pos.entry_premium,
                        "current_premium": h.get("current_premium") or pos.entry_premium,
                        "current_stock_price": (
                            h.get("current_price") or (ticker_data.technical.price if ticker_data else pos.entry_stock_price)
                        ),
                        "remaining_contracts": pos.remaining_contracts,
                        "entry_rationale": pos.thesis,
                        "invalidation_conditions": pos.invalidation_conditions,
                    },
                )
                # Step 1 결과에 LLM 판정을 병합 (LLM 우선)
                if isinstance(llm_result, dict):
                    flags = llm_result.get("flags", h.get("flags", []))
                    dte_urgency = llm_result.get("dte_urgency", h.get("dte_urgency", "안정"))
                    condition_checks = llm_result.get("condition_checks", [])
                    pnl_attribution = llm_result.get("pnl_attribution", {})
                else:
                    flags = h.get("flags", [])
                    dte_urgency = h.get("dte_urgency", "안정")
                    condition_checks = []
                    pnl_attribution = {}

            except Exception as exc:
                log.warning("sell_step4_llm_failed", ticker=pos.ticker, error=str(exc))
                # Graceful Degradation: Step 1 결과 유지
                flags = h.get("flags", ["근거_유효"])
                dte_urgency = h.get("dte_urgency", "안정")
                condition_checks = []
                pnl_attribution = {}

            _ticker_thesis_cache[pos.ticker] = {
                "flags": flags,
                "dte_urgency": dte_urgency,
                "condition_checks": condition_checks,
                "pnl_attribution": pnl_attribution,
            }
            thesis_results[_pos_key(pos)] = {**_ticker_thesis_cache[pos.ticker]}

        # Step 7·10이 참조할 수 있도록 전용 필드에 저장
        ctx.sell_thesis = thesis_results

        duration_ms = int((time.monotonic() - start) * 1000)
        save_snapshot(ctx.execution_id, 4, thesis_results, duration_ms)
        append_audit(ctx.execution_id, 4, "completed", duration_ms=duration_ms,
                     data={"tickers": list(thesis_results.keys())})
        log.info("sell_step_4_done", tickers=list(thesis_results.keys()))

    async def step_5_devils(self, ctx: PipelineContext) -> None:
        """
        
        매도용 Devil's Advocate — §9.4 Step 5

        현재 수익 과신 편향, 역전 리스크를 LLM이 분석한다.
        sell_step2_environment 템플릿으로 이벤트 리스크까지 통합 판정.
        결과는 ctx.errors[-5]에 저장되어 Step 10 최종 결정에 반영된다.
        

        스펙: §9.4 Step 5
        """
        log.info("sell_step_5_start")
        start = time.monotonic()

        devils_results: dict[str, dict] = {}
        events_list = ctx.summary_data.events if ctx.summary_data else []
        _ticker_devils_cache: dict[str, dict] = {}  # 동일 티커 LLM 중복 방지

        for pos in ctx.positions:
            # ── ⑭⑮⑯ 결정론적 점수 차감 (LLM 호출 전에 먼저 수행) ─────────────
            _ticker_sd = ctx.summary_data.tickers.get(pos.ticker) if ctx.summary_data else None
            _fvd_d = ctx.stock_data.get(pos.ticker)
            _da_deduction = 0.0
            _da_reasons: list[str] = []
            _insider_deducted = False
            _eps_deducted = False

            # ⑭ summary INSIDER 섹션 기반 내부자 순매도 차감
            # Sanity check: 개인 임원 내부자 거래는 통상 $500M 이하
            # 그 이상이면 기관 데이터가 혼재된 오류 가능성 → 차감 스킵 + 경고
            _INSIDER_SANITY_LIMIT = 500_000_000.0  # $500M
            if _ticker_sd and _ticker_sd.insider:
                _net_sell = (
                    sum((tx.get("total") or 0.0) for tx in _ticker_sd.insider if tx.get("type") == "매도")
                    - sum((tx.get("total") or 0.0) for tx in _ticker_sd.insider if tx.get("type") == "매수")
                )
                if _net_sell > _INSIDER_SANITY_LIMIT:
                    # 비현실적 금액 — 기관 데이터 혼재 가능성, 차감 스킵
                    log.warning("sell_da_insider_sanity_skip",
                                ticker=pos.ticker,
                                net_sell_M=round(_net_sell / 1e6, 1),
                                reason="$500M 초과 → 기관 데이터 혼재 의심, 차감 스킵")
                    _da_reasons.append(
                        f"내부자 데이터 이상 (${_net_sell / 1e6:.0f}M — 기관 혼재 의심, 차감 보류)"
                    )
                    _insider_deducted = True  # 중복 차감 방지
                elif _net_sell > st.DA_BUY_INSIDER_SELL_AMOUNT:
                    _da_deduction += abs(st.DA_BUY_INSIDER_SELL_PENALTY)
                    _insider_deducted = True
                    _da_reasons.append(
                        f"내부자 순매도 ${_net_sell / 1e6:.1f}M (최근 거래 집계)"
                    )

            # ⑮ summary EARNINGS 섹션 기반 EPS 미스 차감
            if _ticker_sd and _ticker_sd.earnings:
                _latest_q = _ticker_sd.earnings[-1]
                _surprise_raw = _latest_q.get("surprise_pct")
                if _surprise_raw is not None:
                    _surprise = float(_surprise_raw)
                    if _surprise > 1.0:
                        _surprise /= 100.0
                    if _surprise < st.DA_BUY_EPS_MISS_FRACTION:
                        _da_deduction += abs(st.DA_BUY_EPS_MISS_PENALTY)
                        _eps_deducted = True
                        _da_reasons.append(
                            f"최근 EPS 미스 {_surprise * 100:.1f}% "
                            f"(분기: {_latest_q.get('quarter', '?')})"
                        )

            # ⑯ API 내부자/EPS 보완 소스 차감 (summary에서 이미 차감한 경우 중복 방지)
            if _fvd_d:
                if (not _insider_deducted
                        and _fvd_d.insider_trans_pct is not None
                        and _fvd_d.insider_trans_pct < st.DA_BUY_INSIDER_API_PCT):
                    _da_deduction += abs(st.DA_BUY_INSIDER_API_PENALTY)
                    _da_reasons.append(
                        f"내부자 거래 {_fvd_d.insider_trans_pct:.1f}% (대규모 내부자 매도)"
                    )
                if (not _eps_deducted
                        and _fvd_d.eps_surprise_pct is not None
                        and _fvd_d.eps_surprise_pct < st.DA_BUY_EPS_MISS_PCT):
                    _da_deduction += abs(st.DA_BUY_EPS_API_PENALTY)
                    _da_reasons.append(
                        f"EPS 서프라이즈 {_fvd_d.eps_surprise_pct:.1f}% (미스)"
                    )

            # 차감 적용 → technical_score final_score 조정
            if _da_deduction > 0:
                _sc = ctx.technical_scores.get(_pos_key(pos))
                if _sc:
                    _new_sc = max(0.0, _sc.final_score - _da_deduction)
                    ctx.technical_scores[_pos_key(pos)] = _sc.model_copy(
                        update={"final_score": _new_sc}
                    )
                    log.info("sell_da_deduction", ticker=pos.ticker,
                             deduction=_da_deduction, reasons=_da_reasons)

            # 동일 티커 캐시 히트
            if pos.ticker in _ticker_devils_cache:
                log.info("sell_step5_devils_cache_hit", ticker=pos.ticker)
                _cached_dv = {**_ticker_devils_cache[pos.ticker]}
                # 결정론적 차감 이유 병합
                if _da_reasons:
                    _cached_dv.setdefault("da_reasons", []).extend(_da_reasons)
                devils_results[_pos_key(pos)] = _cached_dv
                continue

            opt_data = (
                ctx.summary_data.options.get(pos.ticker) if ctx.summary_data else None
            )
            ivr = 0.0
            if opt_data and opt_data.chain:
                ivr = float(opt_data.chain[0].get("ivr", 0))

            # 포지션 관련 이벤트만 필터링
            pos_events = [
                {"name": ev.name, "days_until": ev.days_until, "importance": ev.importance, "type": ev.type}
                for ev in events_list
                if pos.ticker in ev.name.upper() or ev.days_until <= pos.dte
            ]
            event_count = len(pos_events)

            try:
                llm_result = await analyze_with_llm(
                    template_name="sell_step2_environment",
                    template_vars={
                        "ticker": pos.ticker,
                        "events": pos_events,
                        "ivr": round(ivr, 1),
                        "event_count": event_count,
                    },
                )
                if isinstance(llm_result, dict):
                    event_judgment = llm_result.get("event_judgment", "중립") or "중립"
                    # LLM이 ":혼조" 같은 콜론 접두어를 반환하는 경우 제거
                    event_judgment = event_judgment.lstrip(":").strip() or "중립"
                    iv_crush_risk = llm_result.get("iv_crush_risk", False)
                    iv_crush_loss = llm_result.get("iv_crush_estimated_loss", 0.0)
                    recommendation = llm_result.get("recommendation", "")
                else:
                    event_judgment = "중립"
                    iv_crush_risk = False
                    iv_crush_loss = 0.0
                    recommendation = ""

            except Exception as exc:
                log.warning("sell_step5_llm_failed", ticker=pos.ticker, error=str(exc))
                # Graceful Degradation
                event_judgment = "중립"
                iv_crush_risk = ivr > st.SELL_IVR_CRUSH_THRESHOLD
                iv_crush_loss = 0.0
                recommendation = "LLM 분석 실패 — 수동 확인 필요"

            _ticker_devils_cache[pos.ticker] = {
                "event_judgment": event_judgment,
                "iv_crush_risk": iv_crush_risk,
                "iv_crush_estimated_loss": iv_crush_loss,
                "recommendation": recommendation,
                "da_reasons": _da_reasons,  # 결정론적 차감 이유 포함
            }
            devils_results[_pos_key(pos)] = {**_ticker_devils_cache[pos.ticker]}

        # Step 10 최종 결정에서 활용
        ctx.sell_devils = devils_results

        duration_ms = int((time.monotonic() - start) * 1000)
        save_snapshot(ctx.execution_id, 5, devils_results, duration_ms)
        append_audit(ctx.execution_id, 5, "completed", duration_ms=duration_ms,
                     data={"tickers": list(devils_results.keys())})
        log.info("sell_step_5_done", tickers=list(devils_results.keys()))

    async def step_6_options(self, ctx: PipelineContext) -> None:
        """
        IV Crush 리스크 + 옵션 상태 분석

        스펙: §9.4 Step 6
        """
        log.info("sell_step_6_start")
        start = time.monotonic()

        pos_tickers_6 = list({p.ticker for p in ctx.positions})

        # ── SUMMARY chain OI 변화 데이터 백업 (체인 교체 전 필수) ────────────────
        # yfinance 체인 교체 후 oi_change 필드가 사라지므로 미리 추출.
        # 키: {ticker: {(strike, option_type): oi_change}}
        _sell_oi_change_map: dict[str, dict[tuple, int | None]] = {}
        if ctx.summary_data:
            for _pk6_oi in pos_tickers_6:
                _p_opt6_oi = ctx.summary_data.options.get(_pk6_oi)
                if _p_opt6_oi and _p_opt6_oi.chain:
                    _sell_oi_change_map[_pk6_oi] = {
                        (float(e.get("strike", 0)), str(e.get("option_type", ""))): e.get("oi_change")
                        for e in _p_opt6_oi.chain
                    }

        # ── ⑰ 옵션 체인 실시간 갱신 + OI 복원 ──────────────────────────────
        if ctx.summary_data and pos_tickers_6:
            try:
                from core.api_fetcher import fetch_option_chains_bulk as _focb
                from shared import strategy as _st6
                _fresh_chains = await _focb(
                    pos_tickers_6,
                    dte_min=_st6.DTE_MID_MIN,
                    dte_max=_st6.DTE_MID_MAX,
                )
                for _tk6, _chain6 in _fresh_chains.items():
                    if _chain6 and _tk6 in ctx.summary_data.options:
                        # OI=0 전체이면 summary chain OI를 strike+type 기준으로 복원
                        _old_chain6 = ctx.summary_data.options[_tk6].chain
                        if _old_chain6 and not any(
                            int(e.get("oi", 0) or 0) > 0 for e in _chain6
                        ):
                            _sum_oi_map6: dict[tuple, int] = {}
                            for _se6 in _old_chain6:
                                _k6 = (float(_se6.get("strike", 0)),
                                       str(_se6.get("option_type", "")))
                                _sum_oi_map6[_k6] = max(
                                    _sum_oi_map6.get(_k6, 0),
                                    int(_se6.get("oi", 0) or 0)
                                )
                            _oi_restored6 = 0
                            for _e6 in _chain6:
                                _k6 = (float(_e6.get("strike", 0)),
                                       str(_e6.get("option_type", "")))
                                if _k6 in _sum_oi_map6 and _sum_oi_map6[_k6] > 0:
                                    _e6["oi"] = _sum_oi_map6[_k6]
                                    _oi_restored6 += 1
                            if _oi_restored6:
                                log.info("sell_chain_oi_restored",
                                         ticker=_tk6, entries=_oi_restored6)
                        ctx.summary_data.options[_tk6].chain = _chain6
                        log.debug("sell_option_chain_refreshed",
                                  ticker=_tk6, contracts=len(_chain6))
            except Exception as _e6:
                log.warning("sell_option_chain_refresh_failed", error=str(_e6))

        # ── ⑱ 실시간 chain에서 옵션 analytics 재계산 ─────────────────────────
        if ctx.summary_data and pos_tickers_6:
            try:
                from core.api_fetcher import (
                    _calc_atm_straddle as _cas6,
                    _calc_max_pain as _cmp6,
                    _calc_gex_levels as _cgex6,
                )
                _analytics_updated6 = 0
                for _tk6 in pos_tickers_6:
                    _opt6 = ctx.summary_data.options.get(_tk6)
                    if not _opt6 or not _opt6.chain:
                        continue
                    _chain6 = _opt6.chain
                    _spot6 = (
                        ctx.summary_data.tickers[_tk6].technical.price
                        if _tk6 in ctx.summary_data.tickers else 0.0
                    )
                    _calls6 = [e for e in _chain6 if e.get("option_type") == "call"]
                    _puts6  = [e for e in _chain6 if e.get("option_type") == "put"]
                    _c_oi6  = sum(int(e.get("oi", 0) or 0) for e in _calls6)
                    _p_oi6  = sum(int(e.get("oi", 0) or 0) for e in _puts6)
                    if _c_oi6 == 0 and _p_oi6 == 0:
                        log.debug("sell_option_analytics_skip_zero_oi", ticker=_tk6)
                        continue
                    _pc6    = round(_p_oi6 / _c_oi6, 3) if _c_oi6 > 0 else _opt6.pc_ratio
                    _strad6 = _cas6(_chain6, _spot6)
                    _impl6  = (
                        round(_strad6 / _spot6 * 100, 2)
                        if _spot6 > 0 and _strad6 > 0 else _opt6.implied_move_near
                    )
                    _mpain6 = _cmp6(_chain6)
                    _gex6   = _cgex6(_chain6, _spot6) if _spot6 > 0 else {}
                    ctx.summary_data.options[_tk6] = _opt6.model_copy(update={
                        "total_call_oi":      _c_oi6,
                        "total_put_oi":       _p_oi6,
                        "pc_ratio":           _pc6,
                        "implied_move_near":  _impl6,
                        "max_pain_near":      float(_mpain6) if _mpain6 else _opt6.max_pain_near,
                        "atm_straddle_price": _strad6 if _strad6 > 0 else _opt6.atm_straddle_price,
                        "call_wall":          _gex6.get("call_wall"),
                        "put_wall":           _gex6.get("put_wall"),
                        "gex_flip":           _gex6.get("gex_flip"),
                    })
                    _analytics_updated6 += 1
                    log.debug("sell_option_analytics_refreshed", ticker=_tk6,
                              pc_ratio=_pc6, implied_move=_impl6, max_pain=_mpain6,
                              call_wall=_gex6.get("call_wall"), put_wall=_gex6.get("put_wall"),
                              gex_flip=_gex6.get("gex_flip"))
                append_audit(ctx.execution_id, 6, "info",
                             data={"option_analytics_refresh": "ok",
                                   "updated": _analytics_updated6})
                log.info("sell_option_analytics_done", updated=_analytics_updated6)
            except Exception as _oa6:
                log.warning("sell_option_analytics_refresh_failed", error=str(_oa6))

        # ── ⑳ OI 변화 방향성 신호 (보조 신호 — 데이터 없으면 완전 무시) ──────────
        if ctx.summary_data and ctx.technical_scores:
            _sell_oi_dir = ctx.regime.allowed_direction if ctx.regime else "long_call"
            if _sell_oi_dir in ("both", "none"):
                _sell_oi_dir = "long_call"
            _sell_oi_is_long = (_sell_oi_dir == "long_call")
            _sell_oi_signal_count = 0

            for _sell_oi_pos in ctx.positions:
                _sell_oi_pkey   = _pos_key(_sell_oi_pos)
                _sell_oi_score  = ctx.technical_scores.get(_sell_oi_pkey)
                _sell_oi_opt    = ctx.summary_data.options.get(_sell_oi_pos.ticker)
                _sell_oi_td     = ctx.summary_data.tickers.get(_sell_oi_pos.ticker)
                _sell_oi_chg_map = _sell_oi_change_map.get(_sell_oi_pos.ticker, {})

                if not _sell_oi_score or not _sell_oi_opt or not _sell_oi_opt.chain:
                    continue
                if not _sell_oi_chg_map:
                    continue

                _sell_oi_spot = _sell_oi_td.technical.price if _sell_oi_td else 0.0
                if _sell_oi_spot <= 0:
                    continue

                _sell_oi_call_growth = 0
                _sell_oi_put_growth  = 0
                _sell_oi_call_total  = 0
                _sell_oi_put_total   = 0
                _sell_has_any_chg    = False

                for _sell_ce in _sell_oi_opt.chain:
                    _sell_ce_strike = float(_sell_ce.get("strike", 0))
                    _sell_ce_type   = str(_sell_ce.get("option_type", ""))
                    if _sell_oi_spot <= 0 or abs(_sell_ce_strike / _sell_oi_spot - 1.0) > 0.10:
                        continue
                    _sell_ce_oi  = int(_sell_ce.get("oi", 0) or 0)
                    _sell_ce_chg = _sell_oi_chg_map.get((_sell_ce_strike, _sell_ce_type))
                    if _sell_ce_chg is not None:
                        _sell_has_any_chg = True
                    if _sell_ce_type == "call":
                        _sell_oi_call_total  += _sell_ce_oi
                        if _sell_ce_chg is not None:
                            _sell_oi_call_growth += max(0, _sell_ce_chg)
                    elif _sell_ce_type == "put":
                        _sell_oi_put_total  += _sell_ce_oi
                        if _sell_ce_chg is not None:
                            _sell_oi_put_growth += max(0, _sell_ce_chg)

                if not _sell_has_any_chg:
                    continue

                _sell_oi_call_ratio = (
                    _sell_oi_call_growth / _sell_oi_call_total if _sell_oi_call_total > 0 else 0.0
                )
                _sell_oi_put_ratio = (
                    _sell_oi_put_growth / _sell_oi_put_total if _sell_oi_put_total > 0 else 0.0
                )
                _sell_oi_target = _sell_oi_call_ratio if _sell_oi_is_long else _sell_oi_put_ratio
                if _sell_oi_target >= st.OI_CHANGE_RATIO_THRESHOLD:
                    ctx.technical_scores[_sell_oi_pkey] = _sell_oi_score.model_copy(update={
                        "signal_count": _sell_oi_score.signal_count + st.OI_CHANGE_SIGNAL_BONUS,
                        "final_score":  min(100.0, _sell_oi_score.final_score + st.OI_CHANGE_SCORE_BONUS),
                    })
                    _sell_oi_signal_count += 1
                    log.info("sell_oi_change_signal_applied",
                             ticker=_sell_oi_pos.ticker, pos_key=_sell_oi_pkey,
                             direction=_sell_oi_dir,
                             call_ratio=round(_sell_oi_call_ratio, 3),
                             put_ratio=round(_sell_oi_put_ratio, 3),
                             bonus_score=st.OI_CHANGE_SCORE_BONUS)

                # 반대 방향 OI 급증 → 청산 압력 플래그
                # 롱콜 보유 중 풋 OI 급증 / 롱풋 보유 중 콜 OI 급증 → 기관 헤지 수요 감지
                _sell_oi_counter = _sell_oi_put_ratio if _sell_oi_is_long else _sell_oi_call_ratio
                if _sell_oi_counter >= st.OI_COUNTER_RATIO_THRESHOLD:
                    _h_oi = ctx.sell_health.get(_sell_oi_pkey, {})
                    _fl_oi = list(_h_oi.get("flags", []))
                    if "OI역방향_청산압력" not in _fl_oi:
                        _fl_oi.append("OI역방향_청산압력")
                    ctx.sell_health[_sell_oi_pkey] = {**_h_oi, "flags": _fl_oi}
                    log.info("sell_oi_counter_signal",
                             ticker=_sell_oi_pos.ticker,
                             position=_sell_oi_dir,
                             counter_ratio=round(_sell_oi_counter, 3))

            if _sell_oi_signal_count:
                append_audit(ctx.execution_id, 6, "info",
                             data={"sell_oi_change_signals": _sell_oi_signal_count})
                log.info("sell_oi_change_signals_done", count=_sell_oi_signal_count)

        # ── ⑲ option_flow_ok / signal_count 재평가 (포지션 키 기준) ─────────
        if ctx.summary_data and ctx.technical_scores:
            _recalc6 = 0
            for _pos6 in ctx.positions:
                _score6 = ctx.technical_scores.get(_pos_key(_pos6))
                _opt_d6 = ctx.summary_data.options.get(_pos6.ticker)
                if not _score6 or not _opt_d6:
                    continue
                _is_long6 = _pos6.option_type == "롱콜"
                _pc6v     = _opt_d6.pc_ratio
                _c_oi6v   = _opt_d6.total_call_oi
                _p_oi6v   = _opt_d6.total_put_oi
                _anomaly6 = sum(1 for e in _opt_d6.chain if e.get("is_anomaly", False))
                if _is_long6:
                    _new_opt6 = (
                        _pc6v < st.PC_RATIO_CALL_BULL
                        or (_c_oi6v > 0 and _p_oi6v > 0
                            and _c_oi6v >= _p_oi6v * st.OI_RATIO_DOMINANCE)
                    )
                else:
                    _new_opt6 = (
                        _pc6v > st.PC_RATIO_PUT_BULL
                        or (_c_oi6v > 0 and _p_oi6v > 0
                            and _p_oi6v >= _c_oi6v * st.OI_RATIO_DOMINANCE)
                    )
                if _anomaly6 >= st.ANOMALY_COUNT_OVERRIDE:
                    _new_opt6 = True
                if _new_opt6 != _score6.option_flow_ok:
                    _delta6 = 1 if _new_opt6 else -1
                    _new_cap6 = sum([
                        _score6.rvol_score >= st.SCORE_RVOL_LOW,
                        _score6.obv_ok,
                        _new_opt6,
                        _score6.darkpool_ok,
                    ]) >= st.CAPITAL_FLOW_MIN_SIGNALS
                    ctx.technical_scores[_pos_key(_pos6)] = _score6.model_copy(update={
                        "option_flow_ok":         _new_opt6,
                        "capital_flow_confirmed": _new_cap6,
                        "signal_count":           max(0, _score6.signal_count + _delta6),
                    })
                    _recalc6 += 1
                    log.debug("sell_option_flow_recalc", ticker=_pos6.ticker,
                              old=_score6.option_flow_ok, new=_new_opt6)
            if _recalc6:
                append_audit(ctx.execution_id, 6, "info",
                             data={"option_flow_recalc": _recalc6})
                log.info("sell_option_flow_recalc_done", recalculated=_recalc6)

        # ── Step 1에서 BS fallback 사용했던 포지션의 current_premium 재계산 ──────
        # Step 6에서 옵션 체인이 갱신됐으므로, 갱신된 chain으로 매칭을 재시도.
        # deep OTM 포지션(strike > spot ±10%)은 여전히 chain에 없을 수 있으나,
        # 체인이 갱신된 경우엔 strike ±10% 범위 밖까지 확장 조회.
        if ctx.summary_data and ctx.sell_health:
            for _rpos in ctx.positions:
                _rh = ctx.sell_health.get(_pos_key(_rpos), {})
                # BS fallback을 사용했던 경우만 재시도
                if _rh.get("premium_source") != "bs_estimate":
                    continue
                _ropt = ctx.summary_data.options.get(_rpos.ticker)
                if not _ropt or not _ropt.chain:
                    continue
                _ropt_type = "put" if _rpos.option_type == "롱풋" else "call"
                # strike 매칭 허용 오차를 ±5%로 확장 (deep OTM 포지션 대응)
                _r_spot = (
                    ctx.summary_data.tickers[_rpos.ticker].technical.price
                    if _rpos.ticker in ctx.summary_data.tickers else 0.0
                )
                _best_r: dict | None = None
                _best_r_dist = float("inf")
                for _re in _ropt.chain:
                    if _re.get("option_type", "").lower() != _ropt_type:
                        continue
                    _re_str = str(_re.get("expiry", ""))[:10]
                    if _re_str and _re_str != str(_rpos.expiry)[:10]:
                        continue
                    _re_dist = abs(float(_re.get("strike", 0)) - _rpos.strike)
                    if _re_dist < _best_r_dist:
                        _best_r_dist = _re_dist
                        _best_r = _re
                # 최근접 strike로 mid_price 재계산 (strike 차이 $5 이내)
                if _best_r and _best_r_dist <= 5.0:
                    _r_bid = float(_best_r.get("bid", 0) or 0)
                    _r_ask = float(_best_r.get("ask", 0) or 0)
                    _r_mid = float(_best_r.get("mid", 0) or _best_r.get("mid_price", 0) or 0)
                    if _r_bid > 0 and _r_ask > 0:
                        _r_mid = (_r_bid + _r_ask) / 2
                    if _r_mid > 0:
                        _old_prem = _rh.get("current_premium", _rpos.entry_premium)
                        _new_pnl_total = (
                            (_r_mid - _rpos.entry_premium)
                            * 100 * _rpos.remaining_contracts
                        )
                        ctx.sell_health[_pos_key(_rpos)] = {
                            **_rh,
                            "current_premium": round(_r_mid, 2),
                            "premium_source":  "chain_refreshed",
                            "delta_pnl":       round(_new_pnl_total, 2),
                            "theta_pnl":       0.0,
                            "vega_pnl":        0.0,
                        }
                        log.info("sell_health_premium_refreshed",
                                 ticker=_rpos.ticker,
                                 old_premium=round(_old_prem or 0, 2),
                                 new_premium=round(_r_mid, 2),
                                 strike_dist=round(_best_r_dist, 1))

        iv_crush_warnings: list[str] = []
        for pos in ctx.positions:
            opt_data = ctx.summary_data.options.get(pos.ticker) if ctx.summary_data else None
            if not opt_data:
                continue

            # ── Fix 8: IVR 판단에 실적 발표 타이밍 반영 ─────────────────────
            # IV Crush는 실적 발표 직후 IV가 급락할 때 발생하므로,
            # 실적이 포지션 만기 이내에 있을 때만 진짜 위험이다.
            chain = opt_data.chain
            if chain:
                ivr = float(chain[0].get("ivr", 0))
                if ivr > st.SELL_IVR_CRUSH_THRESHOLD:
                    # 실적 발표가 DTE 이내인지 확인
                    earnings_within_dte = any(
                        abs((ea.date - date.today()).days) <= pos.dte
                        for ea in ctx.earnings_list
                        if ea.ticker == pos.ticker
                    )
                    if earnings_within_dte:
                        # 실제 IV Crush 위험: 강경고 + Slack 알림
                        _crush_loss = pos.entry_premium * st.SELL_IV_CRUSH_LOSS_RATIO * 100 * pos.remaining_contracts
                        warning = (
                            f"{pos.ticker}: IVR {ivr:.0f}% + 실적 {pos.dte}일 이내 "
                            f"— IV Crush 위험 (추정 손실 ${_crush_loss:,.0f})"
                        )
                        iv_crush_warnings.append(warning)
                        try:
                            await self.slack.send_iv_crush_warning(
                                ticker=pos.ticker,
                                detail=warning,
                                estimated_loss=_crush_loss,
                            )
                        except Exception:
                            pass
                    else:
                        # 실적 없음: IV 높음 = Vega 수혜 중 (정보성 메모만)
                        warning = (
                            f"{pos.ticker}: IVR {ivr:.0f}% — IV 높음 (실적 없음, "
                            f"Vega 수혜 중 — 청산 서두를 필요 없음)"
                        )
                        iv_crush_warnings.append(warning)
                        log.info("high_ivr_no_earnings", ticker=pos.ticker, ivr=ivr)

        ctx.sell_iv_warnings = iv_crush_warnings  # Step 10·13에서 참조

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 6, "completed", duration_ms=duration_ms)
        save_snapshot(ctx.execution_id, 6, {"iv_crush_warnings": iv_crush_warnings}, duration_ms)

    # ─────────────────────────────────────────────────────────
    # Step 7: 행동 시나리오 4종 계산
    # ─────────────────────────────────────────────────────────

    async def step_7_action(self, ctx: PipelineContext) -> None:
        """

        HOLD / PARTIAL_EXIT / FULL_EXIT / ROLL 4종 시나리오 생성

        우선순위 규칙 (섹션 9.4 → Step 10 최종 결정에서 적용):
        1. 청산_권고_신호 → FULL_EXIT 우선
        2. DTE ≤ 7 → FULL_EXIT 또는 ROLL만
        3. 이벤트 청산_유리 → FULL_EXIT 또는 PARTIAL_EXIT
        4. 추세 붕괴 + 자금 이탈 → FULL_EXIT
        5. 위 없음 → HOLD 또는 PARTIAL_EXIT


        스펙: §9.4 Step 7
        """
        log.info("sell_step_7_start")
        start = time.monotonic()

        health = ctx.sell_health             # Step 1 건전성 결과
        thesis = ctx.sell_thesis             # Step 4 LLM 무효화 판정
        devils = ctx.sell_devils             # Step 5 Devil's Advocate
        regime_flags = ctx.sell_regime_flags  # Step 2 레짐 역전 플래그
        preliminary_decisions: list[dict] = []

        for pos in ctx.positions:
            h = health.get(_pos_key(pos), {})
            # Step 4 LLM 결과가 있으면 우선 적용 (deterministic → LLM 갱신)
            t = thesis.get(_pos_key(pos), {})
            _t_flags = list(t.get("flags") or [])
            _h_flags = list(h.get("flags", []))
            flags = _t_flags + [f for f in _h_flags if f not in _t_flags]
            urgency = t.get("dte_urgency") or h.get("dte_urgency", "안정")
            tech = ctx.technical_scores.get(_pos_key(pos))
            # Step 2 레짐 역전 플래그 반영
            if regime_flags.get(_pos_key(pos)) == "REGIME_REVERSED":
                flags.append("레짐역전_청산권고")
            # Step 5 Devil's Advocate: 이벤트 "청산_유리"이면 액션 강화
            dv = devils.get(_pos_key(pos), {})

            # ── 트레일링 스탑 트리거 체크 (최우선) ──────────────────────
            current_prem = h.get("current_premium")
            trailing_hit = (
                current_prem is not None
                and pos.trailing_stop > 0
                and current_prem < pos.trailing_stop
            )
            if trailing_hit:
                flags.append("트레일링스탑_발동")
                log.info(
                    "trailing_stop_hit",
                    ticker=pos.ticker,
                    current=round(current_prem, 2),
                    stop=round(pos.trailing_stop, 2),
                    peak=round(pos.peak_premium, 2),
                )

            # ── 수익 목표가 / 손절 임계값 체크 ─────────────────────────
            # Scenario.stop_loss_premium / target_premium_1st/2nd/3rd 에 해당하는 로직
            # entry_premium 기반 (시나리오와 동일: 50%손실/50%수익/100%수익/150%수익)
            if current_prem is not None and pos.entry_premium > 0:
                stop_loss_threshold = pos.entry_premium * st.SELL_STOP_LOSS_RATIO
                target_1st = pos.entry_premium * st.SELL_TARGET_1ST_RATIO
                target_2nd = pos.entry_premium * st.SELL_TARGET_2ND_RATIO
                target_3rd = pos.entry_premium * st.SELL_TARGET_3RD_RATIO

                if current_prem <= stop_loss_threshold:
                    # 50% 손실 → 손절 스탑 도달
                    flags.append("스탑로스_도달")
                    log.info(
                        "stop_loss_reached",
                        ticker=pos.ticker,
                        current=round(current_prem, 2),
                        threshold=round(stop_loss_threshold, 2),
                        pnl_pct=round((current_prem / pos.entry_premium - 1) * 100, 1),
                    )
                elif current_prem >= target_3rd:
                    # 150% 수익 → 3차 익절 목표 달성
                    flags.append("3차익절_달성")
                    log.info(
                        "target_3rd_reached",
                        ticker=pos.ticker,
                        current=round(current_prem, 2),
                        target=round(target_3rd, 2),
                        pnl_pct=round((current_prem / pos.entry_premium - 1) * 100, 1),
                    )
                elif current_prem >= target_2nd:
                    # 100% 수익 → 2차 익절 목표 달성
                    flags.append("2차익절_달성")
                    log.info(
                        "target_2nd_reached",
                        ticker=pos.ticker,
                        current=round(current_prem, 2),
                        target=round(target_2nd, 2),
                        pnl_pct=round((current_prem / pos.entry_premium - 1) * 100, 1),
                    )
                elif current_prem >= target_1st:
                    # 50% 수익 → 1차 익절 목표 달성
                    flags.append("1차익절_달성")
                    log.info(
                        "target_1st_reached",
                        ticker=pos.ticker,
                        current=round(current_prem, 2),
                        target=round(target_1st, 2),
                        pnl_pct=round((current_prem / pos.entry_premium - 1) * 100, 1),
                    )

            # ── M1: yfinance 데이터 플래그 — 애널리스트/EPS/내부자/목표주가 ─────
            # 매수 DA와 동일 소스를 매도 판단에도 반영
            fvd = ctx.stock_data.get(pos.ticker)
            if fvd:
                _stock_price = h.get("current_price", pos.entry_stock_price)

                # 애널리스트 매도의견 (Recom ≥ 임계값 = Underperform/Sell)
                if fvd.recom is not None and fvd.recom >= st.SELL_ANALYST_SELL_THRESHOLD:
                    flags.append("애널리스트_매도의견")
                    log.info("sell_step7_fvd_flag",
                             ticker=pos.ticker, flag="애널리스트_매도의견",
                             recom=round(fvd.recom, 1))

                # 최근 EPS 미스
                if fvd.eps_surprise_pct is not None and fvd.eps_surprise_pct < st.SELL_EPS_MISS_PCT:
                    flags.append("EPS미스_주의")
                    log.info("sell_step7_fvd_flag",
                             ticker=pos.ticker, flag="EPS미스_주의",
                             eps_surprise=round(fvd.eps_surprise_pct, 1))

                # 대규모 내부자 순매도
                if fvd.insider_trans_pct is not None and fvd.insider_trans_pct < st.SELL_INSIDER_SELL_PCT:
                    flags.append("내부자매도_주의")
                    log.info("sell_step7_fvd_flag",
                             ticker=pos.ticker, flag="내부자매도_주의",
                             insider_pct=round(fvd.insider_trans_pct, 1))

                # 애널리스트 목표주가 근접 → 상방 여력 소진
                # 현재가 vs 목표주가 비교
                # - 목표주가 초과 (current > target): 상방 여력 소진, 더 강한 청산 신호
                # - 목표주가 근접 (current ≥ target×0.95): 상방 여력 소진 임박
                # - 목표주가의 130% 초과: stale 데이터 → 무시
                if (fvd.target_price and fvd.target_price > 0
                        and _stock_price > 0
                        and _stock_price <= fvd.target_price * 1.30):
                    if _stock_price > fvd.target_price:
                        # 현재가가 이미 목표주가를 초과 — 상방 여력 없음
                        flags.append("목표주가_초과")
                        log.info("sell_step7_fvd_flag",
                                 ticker=pos.ticker, flag="목표주가_초과",
                                 current=round(_stock_price, 2),
                                 target=round(fvd.target_price, 2),
                                 excess_pct=round((_stock_price / fvd.target_price - 1) * 100, 1))
                    elif _stock_price >= fvd.target_price * st.SELL_TARGET_PRICE_PROXIMITY:
                        # 현재가가 목표주가에 근접 — 상방 여력 소진 임박
                        flags.append("목표주가_근접")
                        log.info("sell_step7_fvd_flag",
                                 ticker=pos.ticker, flag="목표주가_근접",
                                 current=round(_stock_price, 2),
                                 target=round(fvd.target_price, 2))

            # 규칙 기반 예비 결정 (§9.4 Step 10 우선순위 규칙)
            regime_reversed = "레짐역전_청산권고" in flags
            if trailing_hit or "청산_권고_신호" in flags or urgency == "위급" or "스탑로스_도달" in flags:
                action = "FULL_EXIT"
            elif "3차익절_달성" in flags:
                # 150% 수익 달성 → 전량 청산 (추가 수익보다 확정이 합리적)
                action = "FULL_EXIT"
            elif pos.dte <= st.SELL_DTE_FORCE_EXIT:
                action = "FULL_EXIT"
            elif "2차익절_달성" in flags or regime_reversed:
                # 100% 수익 or 레짐 역전 → 부분 청산으로 리스크 축소
                action = "PARTIAL_EXIT"
            elif (dv.get("event_judgment") == "청산_유리"
                  or "1차익절_달성" in flags
                  or "목표주가_근접" in flags
                  or "목표주가_초과" in flags):
                # 이벤트 청산 유리 or 50% 수익 or 목표주가 근접/초과 → 부분 확정
                action = "PARTIAL_EXIT"
            elif "주봉역방향_청산권고" in flags or "OI역방향_청산압력" in flags:
                # Step 3 주봉 역전 또는 Step 6 반대방향 OI 급증 → 부분 청산
                action = "PARTIAL_EXIT"
            elif tech and not tech.trend_confirmed:
                action = "PARTIAL_EXIT"
            else:
                action = "HOLD"

            # 미실현 P&L 추정
            # ▸ 현재 옵션 프리미엄이 있으면: (현재가 - 진입가) × 100 × 계약수  (정확)
            # ▸ 없으면: 내재가치 기반 (OTM은 항상 음수가 나오는 폴백)
            current_price = h.get("current_price", pos.entry_stock_price)
            current_prem_for_pnl = h.get("current_premium")
            if current_prem_for_pnl is not None:
                unrealized_pnl = (
                    (current_prem_for_pnl - pos.entry_premium)
                    * 100 * pos.remaining_contracts
                )
            else:
                intrinsic = (
                    max(0.0, current_price - pos.strike)
                    if pos.option_type == "롱콜"
                    else max(0.0, pos.strike - current_price)
                )
                unrealized_pnl = (
                    (intrinsic - pos.entry_premium) * 100 * pos.remaining_contracts
                )

            # HOLD vs EXIT 시나리오 계산
            try:
                signal_count = tech.signal_count if tech else 4
                ticker_data = (
                    ctx.summary_data.tickers.get(pos.ticker) if ctx.summary_data else None
                )
                opt_data = (
                    ctx.summary_data.options.get(pos.ticker) if ctx.summary_data else None
                )
                current_premium_val = h.get("current_premium") or pos.entry_premium

                # signal_count 기반 확률 추정
                if signal_count >= st.SELL_SIGNAL_BULL_STRONG:
                    bull_p, base_p, bear_p = st.SELL_PROB_BULL_STRONG
                elif signal_count >= st.SELL_SIGNAL_BULL_MEDIUM:
                    bull_p, base_p, bear_p = st.SELL_PROB_BULL_MEDIUM
                else:
                    bull_p, base_p, bear_p = st.SELL_PROB_BULL_WEAK

                # ── Fix 6: ATM 스트래들 기반 내재 이동폭 ─────────────────────
                # ATM straddle price = 시장이 기대하는 DTE 기간 내 이동폭의 근사치
                # implied_move_pct ≈ straddle / stock_price × 100
                implied_move_pct = 5.0  # 폴백
                if opt_data and getattr(opt_data, "atm_straddle_price", 0) and current_price > 0:
                    implied_move_pct = (opt_data.atm_straddle_price / current_price) * 100
                    implied_move_pct = max(st.SELL_IMPLIED_MOVE_MIN, min(implied_move_pct, st.SELL_IMPLIED_MOVE_MAX))
                elif iv_actual := h.get("iv_used", 0.5):
                    # ATM straddle 없을 때: iv × √(DTE/365) × 0.8 근사
                    import math as _math
                    dte_ratio = max(1, pos.dte) / 365.0
                    implied_move_pct = iv_actual * _math.sqrt(dte_ratio) * 80.0
                    implied_move_pct = max(1.0, min(implied_move_pct, 30.0))

                # ── Fix 6: delta 실제값 사용 ──────────────────────────────────
                delta_actual = abs(h.get("greeks", {}).get("delta", 0.5) or 0.5)

                stock_price = current_price
                contracts = pos.remaining_contracts
                commission_total = contracts * cfg.COMMISSION_PER_CONTRACT

                def _make_sell_case(name: str, prob: float, move_pct: float) -> ScenarioCase:
                    target_price = stock_price * (1 + move_pct / 100)
                    # 델타 실제값으로 옵션 가치 추정 (선형 근사)
                    opt_val = max(0.0, current_premium_val + delta_actual * (target_price - stock_price))
                    gross = (opt_val - current_premium_val) * 100 * contracts
                    net = gross - commission_total
                    return ScenarioCase(
                        name=name,  # type: ignore
                        probability=prob,
                        stock_move_pct=move_pct,
                        target_stock_price=target_price,
                        iv_change_assumption="IV 유지",
                        expected_option_value=opt_val,
                        gross_profit=gross,
                        net_profit=net,
                    )

                bull_case = _make_sell_case("bullish", bull_p,  implied_move_pct)
                base_case = _make_sell_case("base",    base_p,  0.0)
                bear_case = _make_sell_case("bearish",  bear_p, -implied_move_pct)
                ev = (
                    bull_case.net_profit * bull_p
                    + base_case.net_profit * base_p
                    + bear_case.net_profit * bear_p
                )

                hold_exit_scenario = Scenario(
                    ticker=pos.ticker,
                    direction="long_call" if pos.option_type == "롱콜" else "long_put",
                    strike=pos.strike,
                    expiry=pos.expiry,
                    contracts=contracts,
                    total_investment=pos.entry_premium * 100 * pos.original_contracts,
                    commission_total=commission_total,
                    implied_move_pct=implied_move_pct,
                    bullish=bull_case,
                    base=base_case,
                    bearish=bear_case,
                    expected_value=ev,
                    stop_loss_premium=pos.entry_premium * st.SELL_STOP_LOSS_RATIO,
                    target_premium_1st=pos.entry_premium * st.SELL_TARGET_1ST_RATIO,
                    target_premium_2nd=pos.entry_premium * st.SELL_TARGET_2ND_RATIO,
                    target_premium_3rd=pos.entry_premium * st.SELL_TARGET_3RD_RATIO,
                    trailing_stop_pct=cfg.TRAILING_STOP_PCT,
                )
                ctx.scenarios[_pos_key(pos)] = hold_exit_scenario
            except Exception as exc:
                log.warning("sell_step7_scenario_error", ticker=pos.ticker, error=str(exc))

            preliminary_decisions.append({
                "ticker": pos.ticker,
                "action": action,
                "flags": flags,
                "urgency": urgency,
                "unrealized_pnl": round(unrealized_pnl, 2),
                "pos": pos,
            })

        ctx.sell_preliminary = preliminary_decisions  # Step 8·10에서 참조

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 7, "completed", duration_ms=duration_ms)
        save_snapshot(ctx.execution_id, 7,
                      [{d["ticker"]: d["action"]} for d in preliminary_decisions], duration_ms)
        log.info("sell_step_7_done", decisions=len(preliminary_decisions))

    # ─────────────────────────────────────────────────────────
    # Step 8: 부분 매도 처리
    # ─────────────────────────────────────────────────────────

    async def step_8_partial(self, ctx: PipelineContext) -> None:
        """
        PARTIAL_EXIT 포지션 처리 및 trailing stop 재설정

        스펙: §9.4 Step 8
        """
        log.info("sell_step_8_start")
        start = time.monotonic()

        prelim = ctx.sell_preliminary            # Step 7 예비 결정
        opt_data_map = ctx.summary_data.options if ctx.summary_data else {}

        health = ctx.sell_health                 # Step 1 건전성 결과

        for d in prelim:
            if d["action"] != "PARTIAL_EXIT":
                continue
            pos: Position = d["pos"]
            opt_data = opt_data_map.get(pos.ticker)
            flags = d.get("flags", [])
            urgency = d.get("urgency", "안정")

            # ── Fix 7: 상황별 부분 청산 비율 ─────────────────────────────────
            # 레짐 역전 또는 DTE 주의 → 75% (큰 비중 축소)
            # 이벤트 리스크 헷지 또는 추세 약화 → 33% (헷지 수준만)
            # 기본 (이익 확정 목적) → 50%
            if "레짐역전_청산권고" in flags or "주봉역방향_청산권고" in flags or urgency == "주의":
                close_ratio = st.SELL_PARTIAL_REGIME_RATIO
                exit_reason = f"PARTIAL_EXIT — 레짐역전/주봉역전/DTE주의 ({st.SELL_PARTIAL_REGIME_RATIO:.0%})"
            elif d.get("unrealized_pnl", 0) > 0:
                # 수익 중 부분 확정
                close_ratio = st.SELL_PARTIAL_PROFIT_RATIO
                exit_reason = f"PARTIAL_EXIT — 수익 부분 확정 ({st.SELL_PARTIAL_PROFIT_RATIO:.0%})"
            else:
                # 손실 헷지 목적
                close_ratio = st.SELL_PARTIAL_LOSS_RATIO
                exit_reason = f"PARTIAL_EXIT — 리스크 헷지 ({st.SELL_PARTIAL_LOSS_RATIO:.0%})"

            # ── Fix 7: 포지션 strike/expiry에 매칭된 프리미엄 사용 ───────────
            h = health.get(_pos_key(pos), {})
            current_premium = h.get("current_premium") or pos.entry_premium
            # chain[0] 폴백은 매칭 실패 시에만 (step1에서 이미 매칭 시도함)
            if current_premium == pos.entry_premium and opt_data and opt_data.chain:
                for entry in opt_data.chain:
                    opt_t = "put" if pos.option_type == "롱풋" else "call"
                    if (entry.get("option_type", "").lower() == opt_t
                            and abs(entry.get("strike", 0) - pos.strike) < 1.0):
                        mid = entry.get("mid_price")
                        if mid:
                            current_premium = float(mid)
                            break

            # 1계약 포지션: 부분청산 불가 → step 10 LLM 결정에 위임
            if pos.remaining_contracts <= 1:
                d["action"] = "HOLD"
                log.info("sell_step8_skip_1contract", ticker=pos.ticker,
                         reason="1계약은 부분청산 불가, step10 LLM 위임")
                continue

            close_cnt = max(1, round(pos.remaining_contracts * close_ratio))
            close_cnt = min(close_cnt, pos.remaining_contracts - 1)  # 최소 1계약 잔여 보장
            close_cnt = max(1, close_cnt)

            ctx.positions, realized_pnl = apply_partial_exit(
                positions=ctx.positions,
                ticker=pos.ticker,
                contracts_to_close=close_cnt,
                exit_premium=current_premium,
                reason=exit_reason,
            )
            d["realized_pnl"] = realized_pnl
            log.info(
                "partial_exit_executed",
                ticker=pos.ticker,
                contracts=close_cnt,
                ratio=close_ratio,
                premium=round(current_premium, 2),
                realized_pnl=round(realized_pnl, 2),
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 8, "completed", duration_ms=duration_ms)
        save_snapshot(ctx.execution_id, 8, {"status": "partial_exits_processed"}, duration_ms)

    # ─────────────────────────────────────────────────────────
    # Step 9: 포트폴리오 재확인
    # ─────────────────────────────────────────────────────────

    async def step_9_portfolio(self, ctx: PipelineContext) -> None:
        """
        잔여 포지션 기준 포트폴리오 재계산

        스펙: §9.4 Step 9
        """
        log.info("sell_step_9_start")
        start = time.monotonic()
        # 잔여 포지션 집계
        remaining = [p for p in ctx.positions if p.remaining_contracts > 0]
        total_invested = sum(
            p.entry_premium * 100 * p.remaining_contracts for p in remaining
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 9, "completed", duration_ms=duration_ms,
                     data={"remaining_positions": len(remaining),
                           "total_invested": total_invested})
        save_snapshot(ctx.execution_id, 9,
                      {"remaining": len(remaining), "invested": total_invested}, duration_ms)

    # ─────────────────────────────────────────────────────────
    # Step 10: 최종 행동 결정
    # ─────────────────────────────────────────────────────────

    async def step_10_decision(self, ctx: PipelineContext) -> None:
        """
        최종 행동 결정 및 SellDecision 생성

        스펙: §9.4 Step 10
        """
        log.info("sell_step_10_start")
        start = time.monotonic()

        prelim = ctx.sell_preliminary        # Step 7 예비 결정
        iv_warnings = ctx.sell_iv_warnings   # Step 6 IV Crush 경고

        ctx.sell_decisions = []
        _ticker_decision_cache: dict[str, dict] = {}  # 동일 티커 LLM 중복 방지

        for d in prelim:
            pos: Position = d["pos"]
            action = d["action"]
            unrealized_pnl = d.get("unrealized_pnl", 0.0)
            realized_pnl = d.get("realized_pnl", 0.0)
            flags = d.get("flags", [])
            urgency = d.get("urgency", "안정")

            # ROLL 조건: 방향은 맞으나 DTE 부족
            tech = ctx.technical_scores.get(_pos_key(pos))
            if (action == "FULL_EXIT" and pos.dte <= 7
                    and tech and tech.trend_confirmed):
                action = "ROLL"
                # ⑳ 실시간 chain에서 ATM OI 기준 최적 만기 선택 (폴백: _nearest_friday)
                roll_expiry = None
                try:
                    _roll_opt = ctx.summary_data.options.get(pos.ticker) if ctx.summary_data else None
                    _roll_chain = _roll_opt.chain if _roll_opt else []
                    _roll_spot = (
                        ctx.summary_data.tickers[pos.ticker].technical.price
                        if ctx.summary_data and pos.ticker in ctx.summary_data.tickers else 0.0
                    )
                    if _roll_chain and _roll_spot > 0:
                        from datetime import datetime as _rdt
                        # 45~90일 범위 만기별 ATM OI 집계
                        _roll_exp_oi: dict[str, int] = {}
                        for _re in _roll_chain:
                            _re_dte = int(_re.get("dte", 0) or 0)
                            if 45 <= _re_dte <= 90:
                                _re_exp = str(_re.get("expiry", ""))
                                _re_strike = float(_re.get("strike", 0) or 0)
                                if abs(_re_strike - _roll_spot) / _roll_spot <= 0.05:
                                    _roll_exp_oi[_re_exp] = (
                                        _roll_exp_oi.get(_re_exp, 0)
                                        + int(_re.get("oi", 0) or 0)
                                    )
                        if _roll_exp_oi:
                            _best_exp = max(_roll_exp_oi, key=lambda k: _roll_exp_oi[k])
                            roll_expiry = _rdt.fromisoformat(_best_exp[:10]).date()
                            log.info("sell_roll_expiry_from_chain",
                                     ticker=pos.ticker, expiry=str(roll_expiry),
                                     atm_oi=_roll_exp_oi[_best_exp])
                except Exception as _roll_exc:
                    log.debug("sell_roll_expiry_chain_failed",
                              ticker=pos.ticker, error=str(_roll_exc))
                if roll_expiry is None:
                    roll_expiry = _nearest_friday(date.today(), target_dte=35)
                roll_strike = pos.strike
            else:
                roll_expiry = None
                roll_strike = None

            # ── LLM 최종 결정 (sell_step3_decision) ──────────────────
            llm_rationale = ""
            try:
                devils_map: dict = ctx.sell_devils  # Step 5 Devil's Advocate
                dv = devils_map.get(_pos_key(pos), {})
                sentiment = ctx.sentiment_results.get(_pos_key(pos), {})
                sc_ev = ctx.scenarios.get(_pos_key(pos))
                ev_str = f"${sc_ev.expected_value:+,.0f}" if sc_ev else "계산 불가"

                # 동일 티커 캐시 히트
                if pos.ticker in _ticker_decision_cache:
                    log.info("sell_step10_decision_cache_hit", ticker=pos.ticker)
                    _cached_dec = _ticker_decision_cache[pos.ticker]
                    llm_action = _cached_dec.get("action")
                    if llm_action in ("HOLD", "PARTIAL_EXIT", "FULL_EXIT", "ROLL"):
                        action = llm_action
                    llm_rationale = _cached_dec.get("rationale", "")
                else:
                    # 현재 P&L 상태: LLM이 수익/손실 여부를 알고 판단해야 함
                    _h10 = ctx.sell_health.get(_pos_key(pos), {})
                    _curr_prem10 = _h10.get("current_premium")
                    _pnl_str = (
                        f"미실현 ${unrealized_pnl:+,.0f} "
                        f"(진입프리미엄 ${pos.entry_premium:.2f} → "
                        f"현재 ${_curr_prem10:.2f})" if _curr_prem10
                        else f"미실현 ${unrealized_pnl:+,.0f} (프리미엄 추정값)"
                    )
                    _pnl_status = "수익 중" if unrealized_pnl >= 0 else "손실 중"

                    llm_decision = await analyze_with_llm(
                        template_name="sell_step3_decision",
                        template_vars={
                            "ticker": pos.ticker,
                            "flags": flags,
                            "dte_urgency": urgency,
                            "remaining_contracts": pos.remaining_contracts,
                            "trend_status": (
                                "확인됨" if (tech and tech.trend_confirmed) else "미확인"
                            ),
                            "capital_flow": (
                                "유입"
                                if (tech and getattr(tech, "capital_flow_confirmed", False))
                                else "미확인"
                            ),
                            "event_judgment": dv.get("event_judgment", "중립"),
                            "expected_value": ev_str,
                            # 현재 P&L 상태 — 수익/손실 여부 명시
                            "pnl_status": _pnl_status,
                            "pnl_detail": _pnl_str,
                            # Step 3 뉴스 감성 분석 결과 전달 (연결 핵심)
                            "overall_sentiment": sentiment.get("overall_sentiment", "MIXED"),
                            "sentiment_verdict": sentiment.get("debate_verdict", "Neutral"),
                            "bull_thesis": sentiment.get("bull_thesis", "정보 없음"),
                            "bear_thesis": sentiment.get("bear_thesis", "정보 없음"),
                        },
                    )
                    if isinstance(llm_decision, dict):
                        llm_action = llm_decision.get("action")
                        if llm_action in ("HOLD", "PARTIAL_EXIT", "FULL_EXIT", "ROLL"):
                            action = llm_action
                        raw_rat = llm_decision.get("rationale", "")
                        # CJK 문자(중국어) 포함 시 완결 문장만 취득
                        import re as _re
                        _cjk = _re.search(r'[一-鿿]', raw_rat)
                        if _cjk:
                            # CJK 이전 텍스트에서 마지막 완결 문장 추출
                            before_cjk = raw_rat[:_cjk.start()]
                            # 마침표/다 로 끝나는 마지막 문장 찾기
                            _sentences = _re.split(r'(?<=[다요]\.)\s*', before_cjk)
                            complete = [s.strip() for s in _sentences if s.strip()]
                            llm_rationale = " ".join(complete).rstrip(" .,")
                        else:
                            llm_rationale = raw_rat
                        _ticker_decision_cache[pos.ticker] = {
                            "action": llm_action,
                            "rationale": llm_rationale,
                        }
                        log.info(
                            "sell_step10_llm_done",
                            ticker=pos.ticker,
                            action=action,
                            rationale_len=len(llm_rationale),
                        )
            except Exception as exc:
                log.warning("sell_step10_llm_failed", ticker=pos.ticker, error=str(exc))

            # PARTIAL_EXIT 청산 계약 수: 잔여 계약의 close_ratio 비율
            # 1계약 포지션에서 PARTIAL_EXIT는 의미 없음 → FULL_EXIT로 자동 변환
            if action == "FULL_EXIT":
                close_cnt_final = pos.remaining_contracts
            elif action == "PARTIAL_EXIT":
                if pos.remaining_contracts <= 1:
                    # 1계약은 쪼갤 수 없음 — FULL_EXIT 또는 HOLD만 유효
                    # 손실 중이면 FULL_EXIT, 아니면 HOLD로 변환
                    if unrealized_pnl < 0:
                        action = "FULL_EXIT"
                        close_cnt_final = pos.remaining_contracts
                        log.info("sell_step10_partial_to_full_1contract",
                                 ticker=pos.ticker,
                                 reason="1계약 포지션 손실 중 → FULL_EXIT 자동 변환")
                    else:
                        action = "HOLD"
                        close_cnt_final = 0
                        log.info("sell_step10_partial_to_hold_1contract",
                                 ticker=pos.ticker,
                                 reason="1계약 포지션 수익 중 → HOLD 자동 변환")
                else:
                    close_cnt_final = max(1, round(pos.remaining_contracts * st.SELL_PARTIAL_PROFIT_RATIO))
                    close_cnt_final = min(close_cnt_final, pos.remaining_contracts - 1)  # 최소 1계약 잔여
            else:
                close_cnt_final = 0

            decision = SellDecision(
                ticker=pos.ticker,
                strike=pos.strike,
                expiry=pos.expiry,
                action=action,  # type: ignore
                contracts_to_close=close_cnt_final,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
                roll_strike=roll_strike,
                roll_expiry=roll_expiry,
                rationale=(
                    llm_rationale
                    or f"플래그: {', '.join(flags)} | DTE: {pos.dte}일 ({urgency})"
                ),
                risk_factors=[w for w in iv_warnings if pos.ticker in w],
                urgency=urgency.replace("위급", "critical").replace("주의", "warning")
                        .replace("보통", "normal").replace("안정", "stable"),  # type: ignore
            )
            ctx.sell_decisions.append(decision)

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 10, "completed", duration_ms=duration_ms,
                     data={"decisions": len(ctx.sell_decisions)})
        save_snapshot(ctx.execution_id, 10,
                      [{"ticker": d.ticker, "action": d.action} for d in ctx.sell_decisions],
                      duration_ms)
        log.info("sell_step_10_done", decisions=len(ctx.sell_decisions))

    # ─────────────────────────────────────────────────────────
    # Step 11: 저장 (positions.md + sell note)
    # ─────────────────────────────────────────────────────────

    async def step_11_storage(self, ctx: PipelineContext) -> None:
        """
        Obsidian에 매도 노트 + 업데이트된 포지션 저장

        스펙: §9.4 Step 11
        """
        log.info("sell_step_11_start")
        start = time.monotonic()

        try:
            health_results = ctx.sell_health  # Step 1에서 저장
            # ── options_analytics 추출 (Step 6 GEX/Implied Move/Max Pain 포함) ──
            _s11_opt_analytics: dict[str, dict] = {}
            if ctx.summary_data and ctx.summary_data.options:
                for _s11_tk, _s11_opt in ctx.summary_data.options.items():
                    _s11_opt_analytics[_s11_tk] = {
                        "implied_move_near": _s11_opt.implied_move_near,
                        "max_pain_near":     _s11_opt.max_pain_near,
                        "pc_ratio":          _s11_opt.pc_ratio,
                        "call_wall":         _s11_opt.call_wall,
                        "put_wall":          _s11_opt.put_wall,
                        "gex_flip":          _s11_opt.gex_flip,
                    }
            note_path = await self.obsidian.save_sell_note(
                execution_id=ctx.execution_id,
                decisions=ctx.sell_decisions,
                positions=ctx.positions,
                technical_scores=ctx.technical_scores or None,
                scenarios=ctx.scenarios or None,
                regime=ctx.regime,
                health_results=health_results,
                sentiment_results=dict(ctx.sentiment_results) if ctx.sentiment_results else None,
                sell_thesis=dict(ctx.sell_thesis) if ctx.sell_thesis else None,
                sell_devils=dict(ctx.sell_devils) if ctx.sell_devils else None,
                sell_regime_flags=dict(ctx.sell_regime_flags) if ctx.sell_regime_flags else None,
                stock_data=dict(ctx.stock_data) if ctx.stock_data else None,
                kavout_data=dict(ctx.kavout_data) if ctx.kavout_data else None,
                regime_infer=dict(ctx.sell_regime_infer) if getattr(ctx, "sell_regime_infer", None) else None,
                options_analytics=_s11_opt_analytics or None,
            )
        except Exception as exc:
            note_path = ""
            append_audit(ctx.execution_id, 11, "degraded", error=f"E500: {exc}")

        # 포지션 상태 캐시 저장
        save_positions_state(ctx.positions)

        # PipelineContext 필드에 저장 (§9.4 Step 11, Step 13에서 참조)
        ctx.obsidian_note_path = note_path

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 11, "completed", duration_ms=duration_ms)
        save_snapshot(ctx.execution_id, 11, {"note_path": note_path}, duration_ms)

    # ─────────────────────────────────────────────────────────
    # Step 12: 복기 (FULL_EXIT 종목)
    # ─────────────────────────────────────────────────────────

    async def step_12_review(self, ctx: PipelineContext) -> None:
        """
        FULL_EXIT 종목 thesis vs 실제 비교

        스펙: §9.4 Step 12
        """
        log.info("sell_step_12_start")
        start = time.monotonic()

        full_exits = [d for d in ctx.sell_decisions if d.action == "FULL_EXIT"]
        review_notes: list[str] = []

        for d in full_exits:
            pos = next((p for p in ctx.positions if p.ticker == d.ticker), None)
            if pos:
                # 초기 노트 — LLM 복기 블록에서 현재 P&L 기준으로 덮어씌워짐
                _h12_pre = ctx.sell_health.get(_pos_key(pos), {})
                _unreal12_pre = d.unrealized_pnl
                _init_result = "수익" if (_unreal12_pre if d.realized_pnl == 0 else d.realized_pnl) >= 0 else "손실"
                review_notes.append(
                    f"{d.ticker}: {_init_result} "
                    f"미실현${_unreal12_pre:+,.0f} / 실현${d.realized_pnl:+,.0f} "
                    f"| thesis: {pos.thesis[:50]}"
                )

                # ── LLM 트레이드 복기 (sell_step4_review) ─────────────
                try:
                    # 현재 프리미엄·미실현 P&L을 복기 LLM에 함께 전달
                    _h12 = ctx.sell_health.get(_pos_key(pos), {})
                    _curr_prem12 = _h12.get("current_premium") or pos.entry_premium
                    _unreal12 = d.unrealized_pnl
                    # realized_pnl=0 (DRY-RUN)인 경우 미실현 손익으로 결과 판단
                    _effective_pnl = d.realized_pnl if d.realized_pnl != 0 else _unreal12
                    result_str = "수익" if _effective_pnl >= 0 else "손실"
                    # review_notes 덮어쓰기 (위에서 realized_pnl 기준으로 써진 것 수정)
                    if review_notes and review_notes[-1].startswith(d.ticker):
                        review_notes[-1] = (
                            f"{d.ticker}: {result_str} "
                            f"미실현${_unreal12:+,.0f} / 실현${d.realized_pnl:+,.0f} "
                            f"| thesis: {pos.thesis[:50]}"
                        )
                    llm_review = await analyze_with_llm(
                        template_name="sell_step4_review",
                        template_vars={
                            "ticker": d.ticker,
                            "option_type": pos.option_type,
                            "realized_pnl": round(_effective_pnl, 2),
                            "entry_thesis": pos.thesis,
                            "days_held": (date.today() - pos.entry_date).days,
                            "entry_premium": pos.entry_premium,
                            "current_premium": round(_curr_prem12, 2),
                            "unrealized_pnl": round(_unreal12, 2),
                        },
                    )
                    if isinstance(llm_review, dict):
                        lesson = llm_review.get("lesson", "")
                        accuracy = llm_review.get("thesis_accuracy", "?")
                        improvement = llm_review.get("improvement", "")
                        if lesson:
                            review_notes.append(f"  → 교훈: {lesson[:120]}")
                        if improvement:
                            review_notes.append(f"  → 개선: {improvement[:120]}")
                        log.info(
                            "sell_step12_review_done",
                            ticker=d.ticker,
                            accuracy=accuracy,
                            outcome=result_str,
                        )
                        # ── TYPE SR 섹션을 Obsidian 노트에 추가 ──────────────
                        if ctx.obsidian_note_path:
                            try:
                                review_block = _format_sell_review_section(
                                    ticker=d.ticker,
                                    pos=pos,
                                    d=d,
                                    llm_review=llm_review,
                                )
                                await self.obsidian.append_note(
                                    ctx.obsidian_note_path, review_block
                                )
                            except Exception as append_exc:
                                log.warning(
                                    "sell_step12_append_failed",
                                    ticker=d.ticker,
                                    error=str(append_exc),
                                )
                except Exception as exc:
                    log.warning(
                        "sell_step12_llm_failed", ticker=d.ticker, error=str(exc)
                    )

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 12, "completed", duration_ms=duration_ms,
                     data={"reviewed": len(full_exits)})
        save_snapshot(ctx.execution_id, 12, {"review_notes": review_notes}, duration_ms)
        log.info("sell_step_12_done", reviewed=len(full_exits))

    # ─────────────────────────────────────────────────────────
    # Step 13: Slack 알림
    # ─────────────────────────────────────────────────────────

    async def step_13_notify(self, ctx: PipelineContext) -> None:
        """
        매도 결과 + IV Crush 경고 Slack 전송

        스펙: §9.4 Step 13
        """
        log.info("sell_step_13_start")
        start = time.monotonic()

        # Step 11에서 ctx.obsidian_note_path에 저장된 경로 사용
        note_path = ctx.obsidian_note_path

        try:
            ts = await self.slack.send_sell_result(
                decisions=ctx.sell_decisions,
                execution_id=ctx.execution_id,
                obsidian_path=note_path,
            )

            # 스탑로스 트리거 알림
            stop_triggers = [
                d for d in ctx.sell_decisions
                if d.action == "FULL_EXIT" and d.realized_pnl < 0
            ]
            for d in stop_triggers:
                await self.slack.send_risk_alert(
                    "STOP_LOSS_TRIGGERED",
                    f"{d.ticker} 손실 청산: ${d.realized_pnl:,.0f}",
                    ticker=d.ticker,
                )

        except Exception as exc:
            ts = ""
            append_audit(ctx.execution_id, 13, "degraded", error=f"E501: {exc}")

        duration_ms = int((time.monotonic() - start) * 1000)
        append_audit(ctx.execution_id, 13, "completed", duration_ms=duration_ms,
                     data={"slack_ts": ts})
        save_snapshot(ctx.execution_id, 13, {"slack_ts": ts}, duration_ms)
        log.info("sell_step_13_done", ts=ts)


# ─────────────────────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────────────────────

def _nearest_friday(from_date: date, target_dte: int = 35) -> date:
    """
    target_dte 이후 가장 가까운 금요일 반환 (ROLL 만기 추정용).
    미국 표준 옵션 만기: 매월 세 번째 금요일.
    """
    import calendar as _cal
    from datetime import timedelta as _td
    target = from_date + _td(days=target_dte)
    days_ahead = (4 - target.weekday()) % 7   # 4 = 금요일(Friday)
    return target + _td(days=days_ahead)
