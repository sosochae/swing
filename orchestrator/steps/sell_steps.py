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
from core.llm import analyze_with_llm, call_ddg_search
from core.obsidian import _format_sell_review_section
from core.parsers import (
    load_latest_summary,
    parse_earnings,
    parse_finviz,
    parse_finviz_detail,
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
        환경 검증 + positions.md 파싱 + 시장 데이터 5종 로딩
        (summary, finviz, earnings, finviz_detail, kavout)

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

        # Finviz 파싱
        try:
            ctx.finviz_rows = parse_finviz(ctx.paths.finviz_file)
            log.info("sell_finviz_loaded", rows=len(ctx.finviz_rows))
        except Exception as exc:
            log.warning("sell_finviz_warn", error=str(exc))
            ctx.finviz_rows = []

        # 어닝 분석 파싱 (어닝_분석_today.md 병합)
        try:
            ctx.earnings_list = parse_earnings(
                ctx.paths.earnings_analysis,
                today_file=ctx.paths.earnings_analysis_today,
            )
            log.info("sell_earnings_loaded", count=len(ctx.earnings_list))
        except Exception as exc:
            log.warning("sell_earnings_warn", error=str(exc))
            ctx.earnings_list = []

        # finviz_output/*.txt 상세 파싱 (screener_mcp 기반 종목)
        try:
            ctx.finviz_detail = parse_finviz_detail(ctx.paths.finviz_output_dir)
            log.info("sell_finviz_detail_loaded", tickers=len(ctx.finviz_detail))
        except Exception as exc:
            log.warning("sell_finviz_detail_warn", error=str(exc))
            ctx.finviz_detail = {}

        # kavout_output/*.txt 상세 파싱 (kavout_mcp 기반 종목) — finviz_output보다 최신이면 덮어쓰기
        try:
            from pathlib import Path as _Path
            kavout_output_dir = _Path(cfg.EARNINGS_DIR) / "kavout_output"
            if kavout_output_dir.exists():
                kavout_detail = parse_finviz_detail(kavout_output_dir)
                for ticker, fvd in kavout_detail.items():
                    existing = ctx.finviz_detail.get(ticker)
                    # 파일 수정 시간 비교 — kavout_output이 더 최신이면 override
                    kavout_file = kavout_output_dir / f"{ticker}.txt"
                    finviz_file = ctx.paths.finviz_output_dir / f"{ticker}.txt"
                    kavout_mtime = kavout_file.stat().st_mtime if kavout_file.exists() else 0
                    finviz_mtime = finviz_file.stat().st_mtime if finviz_file.exists() else 0
                    if existing is None or kavout_mtime >= finviz_mtime:
                        ctx.finviz_detail[ticker] = fvd
                log.info("sell_kavout_detail_merged", added_or_updated=len(kavout_detail))
        except Exception as exc:
            log.warning("sell_kavout_detail_warn", error=str(exc))

        # ── yfinance 실시간 데이터 수집 (포지션 티커 전체) ─────────────────
        # fetch_finviz_detail()로 현재 주가·RSI·RVOL·SMA·피벗·애널리스트·EPS 서프라이즈 등
        # 실시간 수집 → finviz_detail 덮어쓰기 (파일 기반 old data 우선순위 역전)
        pos_tickers = list({p.ticker for p in ctx.positions})
        if pos_tickers:
            try:
                from core.api_fetcher import fetch_finviz_details_bulk as _fetch_bulk
                log.info("sell_yfinance_fetch_start", tickers=pos_tickers)
                _fresh_details = await _fetch_bulk(pos_tickers, sleep_sec=0.3, max_concurrency=3)
                for _ticker, _fresh_fvd in _fresh_details.items():
                    if _fresh_fvd.price is not None:
                        # 실시간 데이터가 있으면 finviz_detail 완전 교체
                        ctx.finviz_detail[_ticker] = _fresh_fvd
                        log.info("sell_yfinance_fresh", ticker=_ticker,
                                 price=_fresh_fvd.price, rsi=_fresh_fvd.rsi14,
                                 target=_fresh_fvd.target_price, recom=_fresh_fvd.recom)
                    else:
                        # yfinance 실패 시 기존 finviz_detail 유지 (fallback)
                        log.warning("sell_yfinance_no_price", ticker=_ticker,
                                    reason="price=None, 기존 finviz_detail 유지")
            except Exception as _yf_exc:
                log.warning("sell_yfinance_bulk_failed", error=str(_yf_exc))

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
                         "finviz_rows": len(ctx.finviz_rows),
                         "earnings": len(ctx.earnings_list),
                         "finviz_detail": len(ctx.finviz_detail),
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
                current_price = ticker_data.technical.price if ticker_data else pos.entry_stock_price

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
        보유 종목 추세 유지/약화/붕괴 판정 + DDG 뉴스 조회 + LLM 감성 분석

        스펙: §9.4 Step 3
        """
        log.info("sell_step_3_start")
        start = time.monotonic()

        # ── 티커 단위 뉴스/감성 캐시 (같은 티커 여러 포지션 → LLM 1회만) ──
        _ticker_news_cache:      dict[str, list[dict]] = {}  # ticker → news_items
        _ticker_sentiment_cache: dict[str, dict]       = {}  # ticker → llm_result

        for pos in ctx.positions:
            direction = "long_call" if pos.option_type == "롱콜" else "long_put"
            if ctx.summary_data and ctx.finviz_rows:
                _kav_entry = ctx.kavout_data.get(pos.ticker, {})
                _kavout_score = float(_kav_entry.get("k_score", 5.0)) if _kav_entry else 5.0
                score = calculate_technical_score(
                    ticker=pos.ticker,
                    direction=direction,
                    summary=ctx.summary_data,
                    finviz_rows=ctx.finviz_rows,
                    kavout_score=_kavout_score,
                )
                ctx.technical_scores[_pos_key(pos)] = score

            # ── DDG 뉴스 조회 + LLM 감성 분석 (티커별 1회) ───────────────
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
                    # 새 티커: DDG 조회 + LLM 분석
                    ddg_results = await asyncio.gather(
                        call_ddg_search(f"{pos.ticker} stock news analysis", num_results=8),
                        call_ddg_search(f"{pos.ticker} options earnings catalyst", num_results=6),
                        return_exceptions=True,
                    )
                    news_items = []
                    for r in ddg_results:
                        if isinstance(r, list):
                            news_items.extend(r)
                    _ticker_news_cache[pos.ticker] = news_items

                    # summary_data에 뉴스 추가 (첫 번째 처리 시에만)
                    if ctx.summary_data and pos.ticker in ctx.summary_data.tickers:
                        ctx.summary_data.tickers[pos.ticker].news.extend(news_items)

                    if news_items:
                        llm_result = await analyze_with_llm(
                            template_name="buy_step3_research",
                            template_vars={
                                "ticker": pos.ticker,
                                "direction": direction,
                                "price": round(current_price, 2),
                                "news": news_items[:30],
                                "earnings_summary": "",
                            },
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
            except Exception as exc:
                log.warning("sell_step3_news_failed", ticker=pos.ticker, error=str(exc))

            if _pos_key(pos) not in ctx.sentiment_results:
                ctx.sentiment_results[_pos_key(pos)] = _default_sentiment()

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
            # 동일 티커 캐시 히트
            if pos.ticker in _ticker_devils_cache:
                log.info("sell_step5_devils_cache_hit", ticker=pos.ticker)
                devils_results[_pos_key(pos)] = {**_ticker_devils_cache[pos.ticker]}
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
                    event_judgment = llm_result.get("event_judgment", "중립")
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
            flags = list(t.get("flags") or h.get("flags", []))
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

            # ── M1: finviz_detail 플래그 — 애널리스트/EPS/내부자/목표주가 ─────
            # 매수 DA와 동일 소스를 매도 판단에도 반영
            fvd = ctx.finviz_detail.get(pos.ticker)
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
                # 현재가가 목표가의 130% 초과 = 데이터가 낡아 신뢰 불가 → 무시
                if (fvd.target_price and fvd.target_price > 0
                        and _stock_price > 0
                        and _stock_price >= fvd.target_price * st.SELL_TARGET_PRICE_PROXIMITY
                        and _stock_price <= fvd.target_price * 1.30):
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
                  or "목표주가_근접" in flags):
                # 이벤트 청산 유리 or 50% 수익 or 목표주가 상방 여력 소진 → 부분 확정
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
            if "레짐역전_청산권고" in flags or urgency == "주의":
                close_ratio = st.SELL_PARTIAL_REGIME_RATIO
                exit_reason = f"PARTIAL_EXIT — 레짐역전/DTE주의 ({st.SELL_PARTIAL_REGIME_RATIO:.0%})"
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
                # M2: 실제 옵션 만기 = target_dte 이후 가장 가까운 금요일
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
                    llm_decision = await analyze_with_llm(
                        template_name="sell_step3_decision",
                        template_vars={
                            "ticker": pos.ticker,
                            "flags": flags,
                            "dte_urgency": urgency,
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
            if action == "FULL_EXIT":
                close_cnt_final = pos.remaining_contracts
            elif action == "PARTIAL_EXIT":
                close_cnt_final = max(1, round(pos.remaining_contracts * st.SELL_PARTIAL_PROFIT_RATIO))
                close_cnt_final = min(close_cnt_final, pos.remaining_contracts)
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
                finviz_detail=dict(ctx.finviz_detail) if ctx.finviz_detail else None,
                kavout_data=dict(ctx.kavout_data) if ctx.kavout_data else None,
                regime_infer=dict(ctx.sell_regime_infer) if getattr(ctx, "sell_regime_infer", None) else None,
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
                result_str = "수익" if d.realized_pnl >= 0 else "손실"
                review_notes.append(
                    f"{d.ticker}: {result_str} ${d.realized_pnl:,.0f} "
                    f"| thesis: {pos.thesis[:50]}"
                )

                # ── LLM 트레이드 복기 (sell_step4_review) ─────────────
                try:
                    llm_review = await analyze_with_llm(
                        template_name="sell_step4_review",
                        template_vars={
                            "ticker": d.ticker,
                            "option_type": pos.option_type,
                            "realized_pnl": round(d.realized_pnl, 2),
                            "entry_thesis": pos.thesis,
                            "days_held": (date.today() - pos.entry_date).days,
                            "entry_premium": pos.entry_premium,
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
