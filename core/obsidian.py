"""
core/obsidian.py
================
Obsidian Local REST API 클라이언트 (포트 27123)

스펙 섹션 8.2 기준:
- GET  /vault/{path}  → 노트 읽기
- PUT  /vault/{path}  → 노트 생성/덮어쓰기
- POST /vault/{path}  → 노트 내용 추가
- GET  /             → 연결 확인 (ping)
- GET  /search/simple/?query={q} → 노트 검색

주의: 절대 로컬 파일 시스템에 직접 쓰지 않음. 반드시 REST API 사용.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential  # type: ignore

from shared.config import get_config
from shared.logger import get_logger
from shared import strategy as st
from shared.schemas import (
    FinalRanking, FinvizDetail, MarketRegime, OptionValidity, Position,
    PortfolioExposure, Scenario, SellDecision, TechnicalScore,
)

log = get_logger()
cfg = get_config()


def _clean_llm_text(text: str) -> str:
    """LLM 출력에서 CJK 문자(중국어) 이후 내용 제거, 완결 문장만 반환."""
    import re
    if not text:
        return text
    cjk = re.search(r'[一-鿿]', text)
    if not cjk:
        return text
    before = text[:cjk.start()]
    # 마침표/다/요 로 끝나는 마지막 완결 문장까지
    last_end = max(
        before.rfind('다.'), before.rfind('요.'),
        before.rfind('임.'), before.rfind('음.')
    )
    if last_end > 0:
        return before[:last_end + 2].strip()
    return before.rstrip(" .,").strip()


class ObsidianClient:
    """
    Obsidian Local REST API 클라이언트.

    모든 노트 저장은 이 클라이언트를 통해 이루어집니다.
    로컬 파일 시스템 직접 접근 금지.
    """

    def __init__(self) -> None:
        self._base = cfg.OBSIDIAN_BASE_URL.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {cfg.OBSIDIAN_API_KEY}",
            "Content-Type": "text/markdown",
        }
        self._timeout = 10.0

    # ─────────────────────────────────────────────────────────
    # 기본 CRUD
    # ─────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """
        Obsidian 서버 연결 확인

        Returns:
            True if connected, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
                resp = await client.get(
                    f"{self._base}/",
                    headers=self._headers,
                )
                return resp.status_code == 200
        except Exception as exc:
            log.warning("obsidian_ping_failed", error=str(exc))
            return False

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=10),
        reraise=True,
    )
    async def write_note(self, vault_path: str, content: str) -> bool:
        """
        노트 생성 또는 덮어쓰기 (PUT)

        Args:
            vault_path: Obsidian vault 내 경로 (예: 'swing-procedure/notes/buy/2026-05-11.md')
            content: 마크다운 콘텐츠

        Returns:
            True on success

        Raises:
            httpx.HTTPError: API 오류
        """
        url = f"{self._base}/vault/{vault_path}"
        async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
            resp = await client.put(
                url,
                headers=self._headers,
                content=content.encode("utf-8"),
            )
            resp.raise_for_status()
        log.info("obsidian_write", path=vault_path, bytes=len(content))
        return True

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=30, min=30, max=90),
        reraise=True,
    )
    async def append_note(self, vault_path: str, content: str) -> bool:
        """
        노트 내용 추가 (POST)

        Args:
            vault_path: vault 내 경로
            content: 추가할 마크다운 콘텐츠

        Returns:
            True on success
        """
        url = f"{self._base}/vault/{vault_path}"
        async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
            resp = await client.post(
                url,
                headers=self._headers,
                content=content.encode("utf-8"),
            )
            resp.raise_for_status()
        log.info("obsidian_append", path=vault_path)
        return True

    async def read_note(self, vault_path: str) -> str | None:
        """
        노트 읽기 (GET)

        Args:
            vault_path: vault 내 경로

        Returns:
            마크다운 문자열 또는 없으면 None
        """
        url = f"{self._base}/vault/{vault_path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
                resp = await client.get(url, headers=self._headers)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.text
        except Exception as exc:
            log.warning("obsidian_read_failed", path=vault_path, error=str(exc))
            return None

    async def delete_note(self, vault_path: str) -> bool:
        """노트 삭제 (DELETE)"""
        url = f"{self._base}/vault/{vault_path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
                resp = await client.delete(url, headers=self._headers)
                resp.raise_for_status()
            return True
        except Exception as exc:
            log.warning("obsidian_delete_failed", path=vault_path, error=str(exc))
            return False

    async def search(self, query: str) -> list[dict]:
        """
        노트 검색 (GET /search/simple/)

        Args:
            query: 검색 쿼리

        Returns:
            검색 결과 리스트
        """
        url = f"{self._base}/search/simple/"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
                resp = await client.get(
                    url,
                    headers=self._headers,
                    params={"query": query},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            log.warning("obsidian_search_failed", query=query, error=str(exc))
            return []

    # ─────────────────────────────────────────────────────────
    # 노트 템플릿 렌더링 & 저장
    # ─────────────────────────────────────────────────────────

    async def save_buy_note(
        self,
        execution_id: str,
        rankings: list[FinalRanking],
        regime_status: str,
        filter_failures: dict[str, list[str]],
        requeue_count: int = 0,
        *,
        technical_scores: dict[str, TechnicalScore] | None = None,
        option_validity: dict[str, OptionValidity] | None = None,
        scenarios: dict[str, Scenario] | None = None,
        regime: MarketRegime | None = None,
        watchlist: list[str] | None = None,
        sentiment_results: dict[str, dict] | None = None,
        rankings_aggressive: list[FinalRanking] | None = None,
        high_downside_tickers: list[str] | None = None,
        finviz_details: "dict[str, FinvizDetail] | None" = None,
        kavout_data: "dict[str, dict] | None" = None,
        portfolio_exposure: "PortfolioExposure | None" = None,
        filter_details: "dict[str, str] | None" = None,
        investment_horizons: "dict[str, list[str]] | None" = None,
        horizon_recommendations: "dict[str, dict[str, OptionValidity]] | None" = None,
        ultra_long_criteria: "dict[str, dict] | None" = None,
    ) -> str:
        """
        매수 분석 노트 저장 — TYPE 1~5 통합 보고서 형식 (환각 방지 강화)

        Returns:
            저장된 vault 경로
        """
        today = date.today().isoformat()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        vault_path = cfg.BUY_NOTE_PATH_TEMPLATE.format(date=today)

        entered = [r for r in rankings if r.action == "진입"]
        watched = [r for r in rankings if r.action == "관찰"]
        on_hold = [r for r in rankings if r.action in ("보류", "탈락")]
        rejected_count = len(filter_failures)

        # ── 거시 지표 요약 ──────────────────────────────────────
        macro_score = _regime_to_score(regime)
        macro_label = _regime_label(regime)

        lines: list[str] = [
            f"매수 분석 종합보고서 — {today}",
            "",
            f"> **실행 ID:** `{execution_id}`  |  **분석 시각:** {now_str}",
            "",
            "---",
            "",
            "## 시장 레짐 (Macro Context)",
            "",
        ]

        # 레짐 블록
        lines += _format_regime_block(regime)
        lines += ["", "---", ""]

        # ── 실행 요약 테이블 ────────────────────────────────────
        lines += [
            "## 실행 요약",
            "",
            "| 구분 | 수 |",
            "|------|----|",
            f"| 진입 | {len(entered)}개 |",
            f"| 관찰 | {len(watched)}개 |",
            f"| 보류/탈락 | {len(on_hold)}개 |",
            f"| 필터 탈락 | {rejected_count}개 |",
            f"| Requeue | {requeue_count}개 |",
            "",
        ]

        # ── 포트폴리오 노출 요약 ────────────────────────────────
        if portfolio_exposure:
            pe = portfolio_exposure
            lines += [
                "### 📊 포트폴리오 노출 현황",
                "",
                "| 항목 | 값 |",
                "|------|----|",
                f"| 총 투자금 | ${pe.total_invested:,.0f} |",
                f"| 잔여 현금 | ${pe.remaining_cash:,.0f} |",
                f"| 총 델타 노출 | {pe.total_delta:+.2f} |",
                f"| 총 세타 (일) | ${pe.total_theta:+.2f} |",
            ]
            if pe.sector_counts:
                sector_rows = " / ".join(
                    f"{sec} {cnt}개" for sec, cnt in
                    sorted(pe.sector_counts.items(), key=lambda x: -x[1])[:5]
                )
                lines.append(f"| 섹터 집중 | {sector_rows} |")
            if pe.concentration_warning:
                lines.append(f"| ⚠️ 경고 | {' / '.join(pe.warnings[:2])} |")
            lines += [""]

        lines += ["---", ""]

        # ── 종목별 TYPE 1~5 통합 상세 보고서 (전 종목) ──────────────
        if rankings:
            lines += ["## 종목별 분석 보고서", ""]
            for r in rankings:
                ts = (technical_scores or {}).get(r.ticker)
                ov = (option_validity or {}).get(r.ticker)
                sc = (scenarios or {}).get(r.ticker) or r.scenario
                sent = (sentiment_results or {}).get(r.ticker)
                fv = (finviz_details or {}).get(r.ticker)
                k_score = float((kavout_data or {}).get(r.ticker, {}).get("k_score", 5.0))
                lines += _format_integrated_buy_block(
                    r, ts, ov, sc, macro_score, macro_label, sent, fv=fv,
                    k_score=k_score, regime=regime,
                    investment_horizons=(investment_horizons or {}).get(r.ticker),
                    horizon_recs=(horizon_recommendations or {}).get(r.ticker),
                    ultra_long_criteria=(ultra_long_criteria or {}).get(r.ticker),
                )

        # ── 필터 탈락 요약 ──────────────────────────────────────
        if filter_failures:
            lines += [
                "## 필터 탈락 종목",
                "",
                "| 티커 | 탈락 필터 코드 | 수치 근거 |",
                "|------|----------------|----------|",
            ]
            for ticker, codes in list(filter_failures.items())[:30]:
                detail_str = (filter_details or {}).get(ticker, "—")
                lines.append(
                    f"| **{ticker}** | {', '.join(codes)} | {detail_str} |"
                )
            lines += ["", "---", ""]

        # ── 수익성 최우선 순위 (aggressive) — 콤팩트 순위표 ────────────
        if rankings_aggressive:
            lines += ["## 📈 수익성 최우선 순위 (EV 기준 재정렬)", ""]
            lines += [
                "| 순위 | 티커 | 행동 | 방향 | EV ($) | R/R | 확신도 | Strike | 만기 |",
                "|------|------|------|------|--------|-----|--------|--------|------|",
            ]
            for r in rankings_aggressive:
                _sc = (scenarios or {}).get(r.ticker) or r.scenario
                _ev = f"${_sc.expected_value:+,.0f}" if _sc else "N/A"
                _rr = f"{r.conviction.rr_ratio:.1f}:1" if r.conviction else "N/A"
                _cv = f"{r.conviction.total_conviction:.2f}" if r.conviction else "N/A"
                _dir = "롱콜" if r.direction == "long_call" else "롱풋"
                _strike = f"${r.strike:.0f}" if r.strike else "N/A"
                _expiry = str(r.expiry) if r.expiry else "N/A"
                lines.append(
                    f"| {r.rank} | **{r.ticker}** | {r.action} | {_dir}"
                    f" | {_ev} | {_rr} | {_cv} | {_strike} | {_expiry} |"
                )
            lines += ["", "> 상세 분석은 위 **종목별 분석 보고서** 참조", ""]

        # ── 일변동 하락 주의 종목 ───────────────────────────────
        lines += ["## ⚠️ 일변동 하락 주의 종목", ""]
        if high_downside_tickers:
            for ticker in high_downside_tickers:
                lines.append(f"- {ticker}")
        else:
            lines.append("없음")
        content = "\n".join(lines)
        await self.write_note(vault_path, content)
        return vault_path

    async def save_sell_note(
        self,
        execution_id: str,
        decisions: list[SellDecision],
        positions: list[Position] | None = None,
        technical_scores: dict[str, TechnicalScore] | None = None,
        scenarios: dict[str, Scenario] | None = None,
        regime: MarketRegime | None = None,
        health_results: dict | None = None,
        sentiment_results: dict[str, dict] | None = None,
        sell_thesis: dict[str, dict] | None = None,
        sell_devils: dict[str, dict] | None = None,
        sell_regime_flags: dict[str, str] | None = None,
        finviz_detail: dict[str, FinvizDetail] | None = None,
        kavout_data: dict | None = None,
        regime_infer: dict | None = None,
    ) -> str:
        """매도 분석 노트 저장 — 완전 재작성 (환각 방지 강화)"""
        today = date.today().isoformat()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        vault_path = cfg.SELL_NOTE_PATH_TEMPLATE.format(date=today)

        # pos_key = "{ticker}_{expiry}_{strike}" — sell_steps._pos_key() 와 동일 규칙
        def _make_pos_key(ticker: str, expiry, strike: float) -> str:
            return f"{ticker}_{expiry}_{strike}"

        pos_map: dict[str, Position] = {
            _make_pos_key(p.ticker, p.expiry, p.strike): p
            for p in (positions or [])
        }

        lines: list[str] = [
            f"# 매도 분석 보고서 — {today}",
            "",
            f"> **실행 ID:** `{execution_id}`  |  **분석 시각:** {now_str}",
            "",
            "---",
            "",
            "## 시장 레짐 (현재)",
            "",
        ]

        # 레짐 블록 + 레짐 변화 경고
        lines += _format_regime_block(regime)

        # 레짐 변화 경고: positions에 entry_regime이 있으면 비교
        regime_changes: list[str] = []
        for pos in (positions or []):
            if pos.entry_regime and regime and pos.entry_regime != regime.regime_status:
                regime_changes.append(
                    f"{pos.ticker}: 진입 시 {pos.entry_regime} → 현재 {regime.regime_status}"
                )
        if regime_changes:
            lines += ["", "**레짐 변화 경고:**"] + [f"- {c}" for c in regime_changes]

        lines += ["", "---", ""]

        # ── 포트폴리오 요약 ─────────────────────────────────────
        total_invested = sum(
            p.entry_premium * 100 * p.remaining_contracts for p in (positions or [])
        )
        total_unrealized = sum(d.unrealized_pnl for d in decisions)
        total_realized = sum(d.realized_pnl for d in decisions)
        urgency_counts = {"critical": 0, "warning": 0, "normal": 0, "stable": 0}
        for d in decisions:
            urgency_counts[d.urgency] = urgency_counts.get(d.urgency, 0) + 1

        lines += [
            "## 포트폴리오 요약",
            "",
            "| 항목 | 값 |",
            "|------|----|",
            f"| 총 투자금 | ${total_invested:,.0f} |",
            f"| 미실현 손익 합계 | ${total_unrealized:+,.0f} |",
            f"| 실현 손익 합계 | ${total_realized:+,.0f} |",
            f"| 긴급 (CRITICAL) | {urgency_counts.get('critical', 0)}개 |",
            f"| 경고 (WARNING) | {urgency_counts.get('warning', 0)}개 |",
            f"| 보통 (NORMAL) | {urgency_counts.get('normal', 0)}개 |",
            f"| 안정 (STABLE) | {urgency_counts.get('stable', 0)}개 |",
            "",
            "---",
            "",
        ]

        # ── 포지션별 상세 (urgency 내림차순) ─────────────────────
        urgency_order = {"critical": 0, "warning": 1, "normal": 2, "stable": 3}
        sorted_decisions = sorted(decisions, key=lambda d: urgency_order.get(d.urgency, 99))

        if sorted_decisions:
            lines += ["## 포지션별 상세 분석", ""]
            for d in sorted_decisions:
                # pos_key: SellDecision에 strike/expiry가 있으면 정확히 매칭
                _pk = _make_pos_key(d.ticker, d.expiry, d.strike) if d.expiry else d.ticker
                pos  = pos_map.get(_pk)
                ts   = (technical_scores  or {}).get(_pk)
                sc   = (scenarios         or {}).get(_pk)
                sent = (sentiment_results or {}).get(_pk)
                h    = (health_results    or {}).get(_pk, {}) if isinstance(health_results, dict) else {}
                lines += _format_sell_position_block(
                    d, pos, ts, sc, sent, h,
                    thesis=(sell_thesis       or {}).get(_pk),
                    devils=(sell_devils       or {}).get(_pk),
                    regime_flag=(sell_regime_flags or {}).get(_pk, ""),
                    fvd=(finviz_detail or {}).get(d.ticker),
                    k_score_entry=(kavout_data or {}).get(d.ticker, {}).get("k_score") if kavout_data else None,
                    regime_infer=(regime_infer or {}).get(_pk),
                )


        content = "\n".join(lines)
        await self.write_note(vault_path, content)
        return vault_path

    async def save_ticker_note(self, ticker: str, content: str) -> str:
        """종목별 상세 노트 저장"""
        vault_path = cfg.TICKER_NOTE_PATH_TEMPLATE.format(ticker=ticker)
        await self.write_note(vault_path, content)
        return vault_path

    async def save_rejected_note(self, ticker: str, reasons: list[str]) -> str:
        """탈락 종목 노트 저장"""
        today = date.today().isoformat()
        vault_path = cfg.REJECTED_NOTE_PATH_TEMPLATE.format(ticker=ticker, date=today)
        content = f"# {ticker} — 탈락 ({today})\n\n**사유:**\n"
        content += "\n".join(f"- {r}" for r in reasons)
        await self.write_note(vault_path, content)
        return vault_path

    async def write_watchlist(self, tickers: list[str]) -> bool:
        """watchlist.md 갱신"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            f"Watchlist — {now_str}",
            "",
            "| # | 티커 |",
            "|---|------|",
        ]
        for i, t in enumerate(tickers, 1):
            lines.append(f"| {i} | {t} |")
        content = "\n".join(lines)
        return await self.write_note("watchlist.md", content)


# ─────────────────────────────────────────────────────────────
# 헬퍼 함수
# ─────────────────────────────────────────────────────────────

def _regime_to_score(regime: MarketRegime | None) -> int:
    """MarketRegime → 0-100 점수"""
    if regime is None:
        return 50
    if regime.regime_status == "favorable":
        base = 75
    elif regime.regime_status == "borderline":
        base = 50
    else:
        base = 25
    # 신뢰도 가중 보정 (최대 ±15점)
    confidence_adj = int((regime.regime_confidence - 0.5) * 30)
    return max(0, min(100, base + confidence_adj))


def _regime_label(regime: MarketRegime | None) -> str:
    if regime is None:
        return "N/A"
    labels = {
        "favorable": "우호적 (Favorable)",
        "borderline": "중립 (Borderline)",
        "unfavorable": "불리 (Unfavorable)",
    }
    return labels.get(regime.regime_status, regime.regime_status)


def _conviction_to_score(r: FinalRanking) -> int:
    """ConfidenceScore → 1-10 정수 (total_conviction 값 직접 반영)"""
    if not r.conviction:
        return 3
    return max(1, min(10, round(r.conviction.total_conviction * 10)))


def _tech_score_bar(score: float) -> str:
    """0-100 점수를 시각적 바로 표현"""
    filled = int(score / 10)
    empty = 10 - filled
    return "█" * filled + "░" * empty + f" {score:.0f}/100"


def _format_regime_block(regime: MarketRegime | None) -> list[str]:
    """MarketRegime → 마크다운 블록"""
    if regime is None:
        return ["> 레짐 데이터 없음 (Step 2 미실행)"]

    icon_map = {"pass": "✅", "borderline": "⚠️", "fail": "❌"}
    direction_map = {
        "long_call": "롱콜 (상승)",
        "long_put": "롱풋 (하락)",
        "both": "롱콜 + 롱풋",
        "none": "관망",
    }

    return [
        f"| 항목 | 값 |",
        f"|------|----|",
        f"| 레짐 상태 | **{_regime_label(regime)}** |",
        f"| 허용 방향 | {direction_map.get(regime.allowed_direction, regime.allowed_direction)} |",
        f"| 추세 강도 | {icon_map.get(regime.trend_strength.status, '?')} {regime.trend_strength.reason} |",
        f"| 변동성 | {icon_map.get(regime.volatility.status, '?')} {regime.volatility.reason} |",
        f"| 지수 추세 | {icon_map.get(regime.index_trend.status, '?')} {regime.index_trend.reason} |",
        f"| 추세 신뢰도 | {regime.trend_confidence:.0%} |",
        f"| 레짐 신뢰도 | {regime.regime_confidence:.0%} |",
    ] + (
        ["", "**레짐 리스크 요인:**"] + [f"- {rf}" for rf in regime.risk_factors]
        if regime.risk_factors else []
    )


def _calc_confidence_pct(ts: TechnicalScore | None) -> int:
    """signal_count / 8 x 100으로 호라이즌 신뢰도 % 계산 (환각 방지)"""
    if ts is None:
        return 50
    return min(95, int(ts.signal_count / 8 * 100))


def _fmt_driver(d) -> str:
    """key_drivers 항목 — dict(신형) 또는 str(구형) 모두 처리"""
    if isinstance(d, dict):
        src = d.get("source", "")
        desc = d.get("description", "")
        wpct = d.get("weight_pct", "")
        wpct_str = f" ({wpct}%)" if wpct else ""
        arrow = {"positive": "↑", "negative": "↓", "neutral": "→"}.get(d.get("direction", ""), "")
        return f"{src}{wpct_str} {arrow} {desc}".strip()
    return str(d)


def _fmt_event(e) -> str:
    """critical_events 항목 — dict(신형) 또는 str(구형) 모두 처리"""
    if isinstance(e, dict):
        event = e.get("event", "")
        impact = e.get("impact", "")
        aftermath = e.get("aftermath", "")
        impact_emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(impact, "⚫")
        parts = [impact_emoji, event]
        if aftermath:
            parts.append(f"→ {aftermath}")
        return " ".join(parts)
    return str(e)


def _fmt_factor(f, positive: bool) -> str:
    """major_positives / significant_negatives 항목 처리"""
    icon = "✅" if positive else "❌"
    if isinstance(f, dict):
        factor = f.get("factor", "")
        source = f.get("source", "")
        src_str = f" [{source}]" if source else ""
        return f"{icon} {factor}{src_str}"
    return f"{icon} {f}"


def _format_type1_section(sent: dict, fv: "FinvizDetail | None" = None) -> str:
    """TYPE 1 — News Sentiment 섹션 생성 (7-섹션 구조화 버전)"""
    if not sent:
        return "### 📰 TYPE 1 — News Sentiment\n_데이터 없음_\n"

    overall = sent.get("overall_sentiment", "N/A")
    confidence = sent.get("confidence", "N/A")
    strength = sent.get("sentiment_strength", "N/A")
    consensus = sent.get("information_consensus", "N/A")
    key_drivers = sent.get("key_drivers", [])
    critical = sent.get("critical_events", [])
    positives = sent.get("major_positives", [])
    negatives = sent.get("significant_negatives", [])
    lasting = sent.get("lasting_impacts", "")
    fading = sent.get("fading_impacts", "")
    next_cat = sent.get("next_catalyst_days", 0)
    bull = sent.get("bull_thesis", "")
    bear = sent.get("bear_thesis", "")
    verdict = sent.get("debate_verdict", "Neutral")
    thesis = sent.get("thesis", "")

    # Sentiment emoji
    sent_emoji = {"POSITIVE": "🟢", "NEGATIVE": "🔴", "MIXED": "🟡"}.get(overall, "⚪")

    # Strength indicator
    strength_bar = {"Strong": "████░", "Moderate": "███░░", "Weak": "██░░░"}.get(strength, "░░░░░")

    # Key drivers 요약 (구형: str list / 신형: dict list)
    if key_drivers and isinstance(key_drivers[0], dict):
        # 신형: 상위 드라이버를 source(weight%) 형태로 요약
        top_drivers = [
            f"{d.get('source','?')} ({d.get('weight_pct','?')}%)"
            for d in key_drivers[:3]
        ]
        drivers_str = ", ".join(top_drivers)
    else:
        drivers_str = ", ".join(str(d) for d in key_drivers[:3]) if key_drivers else "N/A"

    lines = [
        "## ━━━ TYPE 1 · 뉴스 & 센티멘트 분석 ━━━",
        "",
        "### 1-1. 종합 감정 (Overall Sentiment)",
        "",
        "| 항목 | 값 |",
        "|------|-----|",
        f"| Overall Sentiment | {sent_emoji} **{overall}** |",
        f"| Confidence | {confidence} |",
        f"| Sentiment Strength | {strength} `{strength_bar}` |",
        f"| Information Consensus | {consensus} |",
        f"| Key Drivers | {drivers_str} |",
        f"| Next Catalyst (est.) | {'~' + str(next_cat) + ' days' if next_cat else 'N/A'} |",
        "",
    ]

    # Critical events — C안: 서브헤더 + aftermath 단락 + 단기/장기 영향
    if critical:
        lines.append("### 1-2. 시장 지배 이벤트 (Critical Events)")
        lines.append("")
        lines.append("**🔴 결정적 사건 (High Impact)**")
        lines.append("")
        _dir_icon = {"positive": "🟢", "negative": "🔴", "neutral": "🟡"}
        _impact_icon = {"High": "🔴", "Medium": "🟡", "Low": "⚪"}
        for ev in critical[:3]:
            if isinstance(ev, dict):
                ev_title    = ev.get("event", "")
                ev_impact   = ev.get("impact", "Medium")
                ev_dir      = ev.get("direction", "neutral")
                ev_after    = ev.get("aftermath", "")
                ev_short    = ev.get("short_term_effect", "")
                ev_long     = ev.get("long_term_implication", "")
                imp_icon    = _impact_icon.get(ev_impact, "⚪")
                dir_icon    = _dir_icon.get(ev_dir, "🟡")
                lines += [
                    f"**{imp_icon} [{ev_impact}] {ev_title}** {dir_icon}",
                ]
                if ev_after:
                    lines += [_clean_llm_text(ev_after)]
                if ev_short:
                    lines += [f"> 단기 영향 (1~4주): {_clean_llm_text(ev_short)}"]
                if ev_long:
                    lines += [f"> 장기 함의 (6개월+): {_clean_llm_text(ev_long)}"]
                lines.append("")
            else:
                lines.append(f"- {ev}")
                lines.append("")

    # Key Drivers 상세 (신형 dict일 때만)
    if key_drivers and isinstance(key_drivers[0], dict):
        lines.append("### 1-3. 정보 가중치 분석 (Key Drivers 상세)")
        lines.append("")
        lines.append("**📌 Key Drivers (소스별 영향 분석)**")
        lines.append("")
        lines.append("| 소스 | 영향 | 비중 | 방향 |")
        lines.append("|------|------|------|------|")
        dir_map = {"positive": "🟢 상승", "negative": "🔴 하락", "neutral": "⚪ 중립"}
        for d in key_drivers[:5]:
            src  = d.get("source", "N/A")
            desc = d.get("description", "")[:80]
            wpct = f"{d.get('weight_pct', '?')}%"
            direction = dir_map.get(d.get("direction", ""), "⚪")
            lines.append(f"| {src} | {desc} | {wpct} | {direction} |")
        lines.append("")

    # 긍정 요인 전용 섹션
    if positives:
        lines.append("### 1-4. 긍정 요인 전용 섹션 (Major Positives)")
        lines.append("")
        lines.append("**✅ 긍정 요인 (Major Positives)**")
        lines.append("")
        structural = [p for p in positives if isinstance(p, dict) and p.get("significance") == "High"]
        momentum   = [p for p in positives if isinstance(p, dict) and p.get("significance") != "High"]
        if structural:
            lines.append("**구조적 개선 (장기 지속):**")
            for p in structural[:2]:
                sig  = p.get("significance", "Medium")
                fact = p.get("factor", str(p))[:200]
                src  = p.get("source", "")
                lines.append(f"- **[{sig}]** {fact}" + (f" _(출처: {src})_" if src else ""))
        if momentum:
            lines.append("")
            lines.append("**단기 모멘텀 (촉매):**")
            for p in momentum[:2]:
                sig  = p.get("significance", "Medium")
                fact = p.get("factor", str(p))[:200]
                src  = p.get("source", "")
                lines.append(f"- **[{sig}]** {fact}" + (f" _(출처: {src})_" if src else ""))
        lines.append("")

    # 부정 요인 전용 섹션
    if negatives:
        lines.append("### 1-5. 부정 요인 전용 섹션 (Significant Negatives)")
        lines.append("")
        lines.append("**⛔ 부정 요인 (Significant Negatives)**")
        lines.append("")
        critical_neg = [n for n in negatives if isinstance(n, dict) and n.get("significance") == "High"]
        mild_neg     = [n for n in negatives if isinstance(n, dict) and n.get("significance") != "High"]
        if critical_neg:
            lines.append("**주요 리스크 (치명적):**")
            for n in critical_neg[:2]:
                sig  = n.get("significance", "Medium")
                fact = n.get("factor", str(n))[:200]
                src  = n.get("source", "")
                lines.append(f"- **[{sig}]** {fact}" + (f" _(출처: {src})_" if src else ""))
        if mild_neg:
            lines.append("")
            lines.append("**단기 압력 (희석 가능):**")
            for n in mild_neg[:2]:
                sig  = n.get("significance", "Medium")
                fact = n.get("factor", str(n))[:200]
                src  = n.get("source", "")
                lines.append(f"- **[{sig}]** {fact}" + (f" _(출처: {src})_" if src else ""))
        lines.append("")

    # Temporal analysis — C안: 문단 형식
    if lasting or fading:
        lines.append("### 1-6. 시간축별 영향 지속성 (Temporal Analysis)")
        lines.append("")
        lines.append("**⏳ Temporal Analysis**")
        if lasting:
            lines += ["", f"**Lasting (6개월+):** {lasting}"]
        if fading:
            lines += ["", f"**Fading (30~90일):** {fading}"]
        lines.append("")

    # Bull vs Bear — 이중 분리 (추세 전망 + 진입 타이밍)
    if bull or bear:
        lines.append("### 1-7. Bull vs Bear 논쟁 (이중 분리)")
        lines.append("")
        lines.append("**🥊 추세 전망 논쟁 (Trend Debate)**")
        if bull:
            lines += ["", f"🐂 **Bulls:** {bull}"]
        if bear:
            lines += ["", f"🐻 **Bears:** {bear}"]
        lines += ["", f"- **추세 판정:** {verdict}", ""]

        # 진입 타이밍 논쟁 — 실제 지표값 기반 동적 생성
        lines.append("**🥊 진입 타이밍 논쟁 (Timing Debate)**")
        lines.append("")
        _rsi    = fv.rsi14        if fv else None
        _adx    = fv.adx          if fv else None
        _macd_h = fv.macd_hist    if fv else None
        _w52h   = fv.w52_high_pct if fv else None  # 음수 = 52주 고점 아래
        _sma20  = fv.sma20_pct    if fv else None

        _bull_args: list[str] = []
        _bear_args: list[str] = []

        # RSI
        if _rsi is not None:
            if _rsi > 70:
                _bear_args.append(f"RSI {_rsi:.0f} — 단기 과매수, 눌림목 리스크")
            elif _rsi >= 60:
                _bull_args.append(f"RSI {_rsi:.0f} — 모멘텀 구간, 추세 지속 가능")
            elif _rsi < 40:
                _bear_args.append(f"RSI {_rsi:.0f} — 추세 약화, 반등 확인 필요")
            else:
                _bull_args.append(f"RSI {_rsi:.0f} — 중립권, 추가 상승 여력 존재")

        # ADX (추세 강도)
        if _adx is not None:
            if _adx >= 30:
                _bull_args.append(f"ADX {_adx:.0f} — 강한 추세, 진입 유효 구간")
            elif _adx < 20:
                _bear_args.append(f"ADX {_adx:.0f} — 추세 미약, 횡보 리스크")

        # MACD 히스토그램 방향
        if _macd_h is not None:
            if _macd_h > 0:
                _bull_args.append(f"MACD 히스토{_macd_h:+.3f} — 단기 모멘텀 양전환")
            else:
                _bear_args.append(f"MACD 히스토{_macd_h:+.3f} — 단기 모멘텀 음전환")

        # 52주 고점 근접도
        if _w52h is not None:
            if _w52h >= -5:
                _bear_args.append(f"52주 고점 -{abs(_w52h):.1f}% — 돌파 실패 시 되돌림")
            elif _w52h >= -15:
                _bull_args.append(f"52주 고점 -{abs(_w52h):.1f}% — 돌파 시 상방 열림")

        # SMA20 이격
        if _sma20 is not None:
            if _sma20 > 8:
                _bear_args.append(f"SMA20 +{_sma20:.1f}% 이격 — 평균회귀 위험")
            elif _sma20 < 0:
                _bear_args.append(f"SMA20 하회 ({_sma20:.1f}%) — 단기 추세 이탈")

        # fallback: 지표 없으면 sentiment 기반
        if not _bull_args and not _bear_args:
            _ov_fb = sent.get("overall_sentiment", "MIXED")
            _sc_fb = sent.get("sentiment_strength", "Moderate")
            if verdict in ("Slight Bull", "Bullish") and _sc_fb == "Strong":
                _bull_args = ["추세 강함, 기다릴수록 기회비용 발생"]
                _bear_args = ["고점 근접 가능성, 눌림목 대기가 안전"]
            elif verdict in ("Slight Bear", "Bearish") or _ov_fb == "NEGATIVE":
                _bull_args = ["부정적 뉴스 이미 반영, 반등 시 빠른 대응 필요"]
                _bear_args = ["센티멘트 지속 약세, 반전 신호 확인 전 대기"]
            else:
                _bull_args = ["추세 유지 중, 분할 진입으로 리스크 분산 유효"]
                _bear_args = ["방향성 불명확, 명확한 신호 확인 후 진입 원칙"]

        _timing_bull = " / ".join(_bull_args) if _bull_args else "특이 신호 없음"
        _timing_bear = " / ".join(_bear_args) if _bear_args else "특이 신호 없음"

        if len(_bear_args) > len(_bull_args):
            _timing_verdict = "대기 권장 — 조건 정상화 후 진입"
        elif len(_bull_args) > len(_bear_args):
            _timing_verdict = "즉시 진입 유리 — 모멘텀 유효"
        else:
            _timing_verdict = "분할 진입 권장 — 혼조"

        lines += [
            f"🐂 **Bulls:** {_timing_bull}",
            "",
            f"🐻 **Bears:** {_timing_bear}",
            "",
            f"- **타이밍 판정:** {_timing_verdict}",
            "",
        ]

    # 투자 논거 서술 (Investment Thesis)
    if thesis:
        lines += [
            "### 1-8. 투자 논거 서술 (Investment Thesis)",
            "",
            "**📝 투자 논거 (Investment Thesis)**",
            "",
            f"> {thesis}",
            "",
        ]

    lines.append("> _AI-derived from news sources. Not financial advice._")
    lines.append("")
    return "\n".join(lines)


def _format_type3_section(
    r: "FinalRanking",
    ts: "TechnicalScore | None",
    sc: "Scenario | None",
    regime: "MarketRegime | None",
    fv: "FinvizDetail | None" = None,
    narrative: "dict | None" = None,
) -> str:
    """TYPE 3 — Technical Analysis 섹션 생성 (실제 지표값 + LLM 내러티브 지원)"""

    ma_str = getattr(ts, 'ma_alignment', 'mixed') or 'mixed'
    # Convert string alignment to numeric score proxy
    ma = 4 if ma_str == 'bullish' else (1 if ma_str == 'bearish' else 2)
    adx = getattr(ts, 'adx_score', 0) or 0
    rsi = getattr(ts, 'rsi_score', 0) or 0
    macd = getattr(ts, 'macd_score', 0) or 0
    rvol = getattr(ts, 'rvol_score', 0) or 0
    trend_confirmed = getattr(ts, 'trend_confirmed', False)
    signal_count = getattr(ts, 'signal_count', 0) or 0

    # adx_score is 0-25 range; normalize to 0-100 for comparisons
    adx_norm = adx * 4  # 0-100

    if ma >= 4 and adx_norm >= 60:
        outlook = "BULLISH"
        outlook_conf = "High"
        outlook_emoji = "🟢"
    elif ma >= 3 and trend_confirmed:
        outlook = "BULLISH"
        outlook_conf = "Medium"
        outlook_emoji = "🟢"
    elif ma <= 1:
        outlook = "BEARISH"
        outlook_conf = "Medium" if adx_norm >= 50 else "Low"
        outlook_emoji = "🔴"
    else:
        outlook = "NEUTRAL"
        outlook_conf = "Low"
        outlook_emoji = "🟡"

    # Price targets from scenario stock_move_pct
    base_move = getattr(getattr(sc, 'base_case', None), 'stock_move_pct', None) if sc else None
    if base_move is None and sc:
        base_move = getattr(sc.base, 'stock_move_pct', None)
    bull_move = getattr(getattr(sc, 'bullish', None), 'stock_move_pct', None) if sc else None
    bear_move = getattr(getattr(sc, 'bearish', None), 'stock_move_pct', None) if sc else None

    if base_move is not None and bull_move is not None:
        price_target_range = f"+{base_move:.1f}% (Base) ~ +{bull_move:.1f}% (Bull)"
    else:
        price_target_range = "N/A"

    if bear_move is not None:
        stop_ref = f"{bear_move:.1f}% (Bear scenario)"
    else:
        stop_ref = "N/A"

    # Action signal
    if signal_count >= 6:
        trade_signal = "BUY"
        entry_quality = "Good" if signal_count >= 7 else "Fair"
    elif signal_count >= 4:
        trade_signal = "WAIT"
        entry_quality = "Fair"
    else:
        trade_signal = "WAIT"
        entry_quality = "Poor"

    # Short/Medium/Long term assessment (rsi/macd are 0-25 range)
    rsi_norm = rsi * 4
    macd_norm = macd * 4
    short_term = "Bullish" if rsi_norm >= 55 and macd_norm >= 55 else ("Bearish" if rsi_norm <= 40 else "Neutral")
    medium_term = "Bullish" if ma >= 3 else ("Bearish" if ma <= 1 else "Neutral")
    regime_status = getattr(regime, 'regime_status', 'N/A') if regime else 'N/A'
    long_term = "Bullish" if ma >= 4 else ("Bearish" if ma <= 1 else "Neutral")

    # LLM narrative로 entry_quality/outlook override (가능한 경우)
    if narrative:
        nar_outlook = narrative.get("trend_outlook", "")
        nar_quality = narrative.get("entry_quality", "")
        if nar_outlook in ("BULLISH", "NEUTRAL", "BEARISH"):
            outlook = nar_outlook
        if nar_quality in ("Good", "Fair", "Poor"):
            entry_quality = nar_quality

    # 현재가: 시나리오 역산(base 케이스) 우선 → fv.price fallback
    # 이유: fv.price는 로컬 Finviz 캐시 기반(오래될 수 있음)
    _price_from_sc: float | None = None
    if sc and sc.base and sc.base.target_stock_price and sc.base.stock_move_pct is not None:
        _mv = sc.base.stock_move_pct / 100
        if _mv != -1:
            _price_from_sc = sc.base.target_stock_price / (1 + _mv)
    _cur_price_val = _price_from_sc or (fv.price if fv and fv.price else None)
    price_str = f"${_cur_price_val:.2f}" if _cur_price_val else "N/A"
    sma5_str = f"${fv.sma5_val:.2f}" if fv and fv.sma5_val else "N/A"
    sma20_str = f"${fv.sma20_val:.2f}" if fv and fv.sma20_val else "N/A"
    sma50_str = f"${fv.sma50_val:.2f}" if fv and fv.sma50_val else "N/A"
    bb_upper_str = f"${fv.bb_upper:.2f}" if fv and fv.bb_upper else "N/A"
    bb_lower_str = f"${fv.bb_lower:.2f}" if fv and fv.bb_lower else "N/A"
    pivot_str = f"${fv.pivot:.2f}" if fv and fv.pivot else "N/A"
    pivot_r1_str = f"${fv.pivot_r1:.2f}" if fv and fv.pivot_r1 else "N/A"
    pivot_s1_str = f"${fv.pivot_s1:.2f}" if fv and fv.pivot_s1 else "N/A"
    atr_str = f"${fv.atr:.2f}" if fv and fv.atr else "N/A"
    adx_val_str = f"{fv.adx:.1f}" if fv and fv.adx else "N/A"
    rsi_val_str = f"{fv.rsi14:.1f}" if fv and fv.rsi14 else "N/A"

    # Action plan
    if signal_count >= 6:
        long_plan = f"보유 유지. T1 도달 후 Trail Stop 이동. signal_count={signal_count}/8"
        flat_plan = f"Alert 설정 후 풀백 대기. 현재 진입 품질: {entry_quality}"
    elif signal_count >= 4:
        long_plan = f"원래 Stop 유지. 추가 진입 보류. signal_count={signal_count}/8"
        flat_plan = "DO NOT CHASE. 신호 강화 후 재평가"
    else:
        long_plan = f"부분 청산 고려. signal_count={signal_count}/8 (약함)"
        flat_plan = "관망. 설정이 개선될 때까지 대기"

    allowed_dir = getattr(regime, 'allowed_direction', 'N/A') if regime else 'N/A'

    lines = [
        "## ━━━ TYPE 3 · 기술적 분석 ━━━",
        "",
        "### 3-1. 트레이딩 뷰 요약",
        "",
        "| 항목 | 값 |",
        "|------|-----|",
        f"| 현재가 | {price_str} |",
        f"| Trend Outlook (1-3M) | {outlook_emoji} **{outlook}** (Confidence: {outlook_conf}) |",
        f"| Price Target Range | {price_target_range} |",
        f"| Stop Reference | {stop_ref} |",
        f"| Trading Signal | **{trade_signal}** |",
        f"| Entry Quality | {entry_quality} |",
        f"| Market Regime | {regime_status} → {allowed_dir} |",
        "",
    ]

    # 실제 지표값 테이블 (fv 있을 때만)
    if fv:
        lines += [
            "### 3-2. 실제 지표값 (Live Indicators)",
            "",
            "**📊 실제 지표값 (Live Indicators)**",
            "",
            "| 지표 | 값 | 지표 | 값 |",
            "|------|-----|------|-----|",
            f"| RSI(14) | {rsi_val_str} | ADX | {adx_val_str} |",
            f"| SMA5 | {sma5_str} | SMA20 | {sma20_str} |",
            f"| SMA50 | {sma50_str} | ATR(14) | {atr_str} |",
            f"| BB 상단 | {bb_upper_str} | BB 하단 | {bb_lower_str} |",
            f"| Pivot | {pivot_str} | R1 / S1 | {pivot_r1_str} / {pivot_s1_str} |",
            "",
        ]

    # LLM 내러티브 섹션 (있을 때) — D안: 섹션별 문단 형식
    if narrative:
        trend_nar   = narrative.get("trend_narrative", "")
        mom_nar     = narrative.get("momentum_narrative", "")
        vol_nar     = narrative.get("volatility_narrative", "")
        sr_nar      = narrative.get("support_resistance_narrative", "")
        entry_nar   = narrative.get("entry_timing_rationale", "")
        risk_nar    = narrative.get("risk_scenario_narrative", "")
        overall_nar = narrative.get("overall_technical_narrative", "")

        if any([trend_nar, mom_nar, vol_nar, sr_nar, entry_nar, risk_nar, overall_nar]):
            lines += [
                "### 3-3. 기술 분석 내러티브 (LLM 심층)",
                "",
                "> _⚠️ LLM 캐시 기반 분석 — 내러티브 내 가격/지표 수치는 분석 생성 시점 기준. 실제 최신 값은 3-2 Live Indicators 참조._",
                "",
                "**🔍 기술 분석 내러티브**",
                "",
            ]
            if trend_nar:
                lines += ["**추세 및 이동평균:**", trend_nar, ""]
            if mom_nar:
                lines += ["**모멘텀 (RSI/MACD):**", mom_nar, ""]
            if vol_nar:
                lines += ["**추세 강도 및 변동성 (ADX/ATR):**", vol_nar, ""]
            if sr_nar:
                lines += ["**지지/저항 레벨:**", sr_nar, ""]
            if entry_nar:
                lines += ["**진입 타이밍 근거:**", entry_nar, ""]
            if risk_nar:
                lines += ["**리스크 시나리오:**", risk_nar, ""]
            if overall_nar:
                lines += [f"> **종합 판단:** {overall_nar}", ""]

    lines += [
        "### 3-4. 멀티 타임프레임 추세",
        "",
        "**Multi-Timeframe Trend:**",
        "",
        "| 시계열 | 방향 | 근거 |",
        "|--------|------|------|",
        f"| Short-term (days) | {short_term} | {'RSI ' + rsi_val_str + ' (과매수)' if short_term == 'Bearish' else 'RSI ' + rsi_val_str + ', MACD ' + str(macd) + '/25'} |",
        f"| Medium-term (1-3M) | {medium_term} | MA Alignment: {ma_str} |",
        f"| Long-term (3-6M) | {long_term} | Regime: {regime_status} |",
        "",
        "### 3-5. 행동 지침",
        "",
        "**Action Plan:**",
        "",
        f"- 📌 **Long 보유자:** {long_plan}",
        f"- 📌 **미진입자:** {flat_plan}",
        "",
    ]

    # ── 핵심 가격 레벨 통합 테이블 ─────────────────────────────────
    if fv:
        # 현재가: 시나리오 역산 우선 (fv.price는 캐시 기반으로 오래될 수 있음)
        _cur = _cur_price_val or fv.price or 0.0
        _r2   = fv.pivot_r2 or None
        _r1   = fv.pivot_r1 or None
        _s1   = fv.pivot_s1 or None
        _s2   = fv.pivot_s2 or None
        _bb_u = fv.bb_upper or None
        _bb_l = fv.bb_lower or None
        _s200 = fv.sma200_val or None

        def _pf(v) -> str:
            return f"${v:.2f}" if v else "N/A"

        lines += [
            "### 3-6. 핵심 가격 레벨 통합 테이블",
            "",
            "**📍 핵심 가격 레벨 통합 (Key Price Levels)**",
            "",
            "| 구분 | 가격 | 근거 |",
            "|---|---|---|",
        ]
        # 현재가 기준으로 저항/지지 동적 분류
        _price_levels = []
        if _r2:   _price_levels.append((_r2,   "피봇 R2 / 주요 매물대 상단"))
        if _r1:   _price_levels.append((_r1,   "피봇 R1 / 단기 저항"))
        if _bb_u: _price_levels.append((_bb_u, "볼린저밴드 상단 (과열 경계)"))
        if _bb_l: _price_levels.append((_bb_l, "볼린저밴드 하단 (과매도 경계)"))
        if _s1:   _price_levels.append((_s1,   "피봇 S1 / 단기 지지"))
        if _s2:   _price_levels.append((_s2,   "피봇 S2 / 구조적 지지"))
        _price_levels.sort(key=lambda x: x[0], reverse=True)
        _cur_inserted = False
        for _lv, _desc in _price_levels:
            if not _cur_inserted and _lv <= _cur:
                lines.append(f"| **현재가** | **{_pf(_cur)}** | |")
                _cur_inserted = True
            _tag = "저항" if _lv > _cur else "지지"
            lines.append(f"| {_tag} | {_pf(_lv)} | {_desc} |")
        if not _cur_inserted:
            lines.append(f"| **현재가** | **{_pf(_cur)}** | |")
        if _s200:
            lines.append(f"| 추세 무효화선 | {_pf(_s200)} | SMA200 — 이 아래 일봉 마감 시 추세 붕괴 |")
        lines.append("")

    # ── 가격 예측 시나리오 (rule-based) ───────────────────────────
    # 현재가: 시나리오 역산값 우선 (_cur_price_val), fv.price는 캐시 기반으로 오래될 수 있음
    if sc and (_cur_price_val or (fv and fv.price)):
        _p        = _cur_price_val or fv.price
        _base_pct = getattr(sc.base,    "stock_move_pct", None)
        _bull_pct = getattr(sc.bullish, "stock_move_pct", None)
        _bear_pct = getattr(sc.bearish, "stock_move_pct", None)
        _r1v = (fv.pivot_r1 if fv else None) or None
        _s1v = (fv.pivot_s1 if fv else None) or None

        if _s1v and _r1v:
            if _p and _p > _r1v:
                # 현재가가 이미 R1 위 → 눌림목이 아닌 상승 지속 국면
                _st_desc = (
                    f"현재가({_p:.2f})가 피봇 R1(${_r1v:.2f}) 상단 돌파 상태. "
                    f"단기 과열 소화 시 S1(${_s1v:.2f}) 부근 지지 확인 후 추가 상승 가능."
                )
            else:
                _st_desc = (
                    f"${_s1v:.2f} ~ ${_r1v:.2f} 구간 내 눌림목 조정 예상. "
                    "상승 추세를 훼손하지 않는 기술적 소화 구간."
                )
        else:
            _st_desc = "단기 저항 부근 숨고르기 또는 소폭 조정 예상."

        if _base_pct is not None and _bull_pct is not None:
            _mt_desc = (
                f"Base 시나리오 기준 {_base_pct:+.1f}% 목표 (${_p * (1 + _base_pct / 100):.2f}). "
                f"Bull 케이스 {_bull_pct:+.1f}% (${_p * (1 + _bull_pct / 100):.2f}) 도달 가능."
            )
        else:
            _mt_desc = "중기 목표가는 피봇 R1 돌파 후 R2 구간."

        if _bear_pct is not None:
            _lt_desc = (
                f"SMA200 위 안착 유지 시 장기 상승 추세 지속. "
                f"Bear 시나리오 {_bear_pct:+.1f}%가 최대 손실 기준선."
            )
        else:
            _lt_desc = "장기 추세는 SMA200 지지 여부로 판단."

        lines += [
            "### 3-7. 가격 예측 시나리오 (Price Projections)",
            "",
            "**📅 가격 예측 시나리오 (Price Projections)**",
            "",
            f"**단기 (1~2주):** {_st_desc}",
            "",
            f"**중기 (1~3개월):** {_mt_desc}",
            "",
            f"**장기 (3~6개월):** {_lt_desc}",
            "",
        ]

    # ── 핵심 변곡점 (Key Inflection Points) ───────────────────────
    if fv:
        _accel     = fv.pivot_r2 or fv.pivot_r1
        _breakdown = fv.sma200_val or fv.pivot_s2
        if _accel or _breakdown:
            lines += ["### 3-8. 핵심 변곡점 (Key Inflection Points)", "", "**⚡ 핵심 변곡점 (Key Inflection Points)**", "", "```"]
            if _accel:
                _accel_cur = _cur_price_val or (fv.price if fv else None)
                if _accel_cur and _accel_cur > _accel:
                    lines.append(f"상승 가속 조건   : ${_accel:.2f} 돌파 (✅ 이미 달성 — 현재가 ${_accel_cur:.2f})")
                else:
                    lines.append(f"상승 가속 조건   : ${_accel:.2f} 대량 거래량 동반 돌파 → 상승 속도 가속")
            if _breakdown:
                lines.append(f"추세 붕괴 기준   : ${_breakdown:.2f} 일봉 종가 하회 → 중기 상승 추세 소멸")
            lines += ["```", ""]

    return "\n".join(lines)


def _format_sell_type3_section(pos: "Position | None", ts: "TechnicalScore | None", regime: "MarketRegime | None") -> list[str]:
    """매도 보고서용 TYPE 3 — 진입 vs 현재 레짐 비교"""
    entry_regime = getattr(pos, 'entry_regime', '') or 'N/A' if pos else 'N/A'
    entry_vix = getattr(pos, 'entry_vix', 0.0) or 0.0 if pos else 0.0
    current_regime = getattr(regime, 'regime_status', 'N/A') if regime else 'N/A'

    regime_changed = (entry_regime != 'N/A' and current_regime != 'N/A' and
                     entry_regime != current_regime)
    regime_alert = "⚠️ 레짐 변경 감지!" if regime_changed else "✅ 레짐 유지"

    signal_count = getattr(ts, 'signal_count', 0) or 0
    ma_str = getattr(ts, 'ma_alignment', 'mixed') or 'mixed'
    ma = 4 if ma_str == 'bullish' else (1 if ma_str == 'bearish' else 2)
    adx = getattr(ts, 'adx_score', 0) or 0
    adx_norm = adx * 4

    # Current trend outlook
    if ma >= 3 and adx_norm >= 55:
        current_outlook = "BULLISH"
    elif ma <= 1:
        current_outlook = "BEARISH"
    else:
        current_outlook = "NEUTRAL"

    vix_str = f"{entry_vix:.1f}" if entry_vix > 0 else "N/A"

    lines = [
        "### 📈 TYPE 3 — Technical Status (진입 vs 현재)",
        "",
        "| 항목 | 진입 시 | 현재 |",
        "|------|---------|------|",
        f"| Market Regime | {entry_regime} | {current_regime} |",
        f"| VIX Level | {vix_str} | N/A |",
        f"| Regime Alert | — | {regime_alert} |",
        f"| Trend Outlook | N/A | {current_outlook} |",
        f"| Signal Count | N/A | {signal_count}/8 |",
        "",
    ]

    if regime_changed:
        lines.append("> ⚠️ **레짐 변경**: 진입 조건이 변경되었습니다. 포지션 재검토 권장")
        lines.append("")

    return lines


def _format_integrated_buy_block(
    r: FinalRanking,
    ts: TechnicalScore | None,
    ov: OptionValidity | None,
    sc: Scenario | None,
    macro_score: int,
    macro_label: str,
    sent: dict | None,
    fv: "FinvizDetail | None" = None,
    k_score: float = 5.0,
    regime: "MarketRegime | None" = None,
    investment_horizons: "list[str] | None" = None,
    horizon_recs: "dict[str, OptionValidity] | None" = None,
    ultra_long_criteria: "dict | None" = None,
) -> list[str]:
    """종목 1개에 대한 TYPE 1~5 통합 매수 보고서 블록 생성 (환각 방지)"""

    direction_label = "롱콜 (상승)" if r.direction == "long_call" else "롱풋 (하락)"
    action_label = {"진입": "[진입]", "관찰": "[관찰]", "보류": "[보류]", "탈락": "[탈락]"}.get(r.action, r.action)
    conviction_num = _conviction_to_score(r)
    confidence_pct = _calc_confidence_pct(ts)

    # 목표 보유기간: 옵션 만기(DTE) 기반으로 동적 산출
    _ov_dte = 35  # 기본값
    if ov and ov.expiry:
        try:
            import datetime as _dtmod
            _ov_dte = max(1, (ov.expiry - _dtmod.date.today()).days)
        except Exception:
            pass
    if _ov_dte <= 35:
        _hold_period = "3~10일 (단기 스윙)"
    elif _ov_dte <= 90:
        _hold_period = "1~4주 (중기 스윙)"
    else:
        _hold_period = "4~12주 (중장기)"

    tech_score = ts.final_score if ts else 0.0
    tech_status = "충족" if tech_score >= 60 else "경고" if tech_score >= 40 else "미충족"

    # Greeks — None이면 "N/A" 표시
    delta_str = f"{ov.greeks.delta:.2f}" if ov else "N/A (데이터 없음)"
    iv_str = f"{ov.greeks.iv * 100:.1f}%" if ov else "N/A (데이터 없음)"
    ivr_val = ov.greeks.ivr if ov and ov.greeks else (r.conviction.ivr or 0)
    ivr_str = f"{ivr_val:.1f}" if ivr_val > 0 else "N/A (데이터 없음)"
    theta_str = f"${ov.greeks.theta:.2f}" if ov else "N/A (데이터 없음)"
    gamma_str = f"{ov.greeks.gamma:.4f}" if (ov and ov.greeks and ov.greeks.gamma) else "N/A"
    vega_str = f"${ov.greeks.vega:.2f}" if (ov and ov.greeks and ov.greeks.vega) else "N/A"

    # T1/T2/T3 — sc에서 가져오되 없으면 계산 (환각 방지)
    if sc:
        t1 = sc.target_premium_1st
        t2 = sc.target_premium_2nd if sc.target_premium_2nd > 0 else round(t1 * 1.5, 2)
        t3 = sc.target_premium_3rd if sc.target_premium_3rd > 0 else round(t1 * 2.0, 2)
        stop_prem_t5 = sc.stop_loss_premium or 0.0
        # 진입 프리미엄 역산: stop = entry × 0.5 → entry = stop / 0.5
        entry_prem_t5 = round(stop_prem_t5 / 0.5, 2) if stop_prem_t5 > 0 else None
        # T5 R/R: (T3 - entry) / (entry - stop) ← 실제 손익 기반
        if entry_prem_t5 and stop_prem_t5 > 0 and t3 > 0:
            _t5_risk = entry_prem_t5 - stop_prem_t5
            _t5_reward = t3 - entry_prem_t5
            t5_rr = round(_t5_reward / _t5_risk, 1) if _t5_risk > 0 else r.conviction.rr_ratio
        else:
            t5_rr = r.conviction.rr_ratio
        entry_prem_str = f"${entry_prem_t5:.2f}" if entry_prem_t5 else "N/A"
        stop_str = f"${stop_prem_t5:.2f}"
        t1_str = f"${t1:.2f} (+50%)"
        t2_str = f"${t2:.2f} (+100%)"
        t3_str = f"${t3:.2f} (+150%)"
        trailing_str = f"고점 대비 -{sc.trailing_stop_pct:.0f}%"
    else:
        entry_prem_str = "N/A"
        t5_rr = r.conviction.rr_ratio
        stop_str = t1_str = t2_str = t3_str = trailing_str = "N/A (시나리오 데이터 없음)"

    # ── SECTION 0: 종합 최종 판정 요약 (Executive Summary) ────────
    # 스윙 무효화 조건 — fv에서 S1 또는 S2로 역산
    _invalidation_price: str = "N/A"
    if fv:
        _inv_val = fv.pivot_s1 or fv.pivot_s2 or fv.sma200_val
        if _inv_val:
            _invalidation_price = f"${_inv_val:.2f}"
    # 최적 진입 방식
    _entry_method = (
        "즉시 시장가" if r.action == "진입" and (ts and ts.signal_count >= 7)
        else "눌림목 대기" if r.action in ("관찰", "보류")
        else "돌파 확인 후 진입"
    )
    # 핵심 근거 1줄 — rationale 앞 80자, 테이블 깨짐 방지용 | 제거
    _core_rationale = (r.rationale or "").replace("|", "/")[:80].rstrip()
    if len((r.rationale or "").replace("|", "/")) > 80:
        _core_rationale += "…"

    # Kavout K-Score 표현
    k_emoji = "🟢" if k_score >= 7 else ("🔴" if k_score <= 3 else "🟡")
    k_label = "강세" if k_score >= 7 else ("약세" if k_score <= 3 else "중립")
    k_str = f"{k_score:.0f}/9 {k_emoji} ({k_label})" if k_score != 5.0 else f"{k_score:.0f}/9 🟡 (중립)"

    lines: list[str] = [
        "---",
        "",
        f"### {action_label} #{r.rank} {r.ticker} — {direction_label}",
        "",
        "## ━━━ SECTION 0 · 종합 최종 판정 요약 (Executive Summary) ━━━",
        "",
        "| 항목 | 내용 |",
        "|------|------|",
        f"| **최종 행동** | **{r.action}** |",
        f"| **확신도** | {conviction_num}/10 ({r.conviction.level.upper() if r.conviction else 'N/A'}) |",
        f"| **신호 수** | {ts.signal_count if ts else 'N/A'}/8 기반, 복합 신뢰도 {confidence_pct}% |",
        f"| **Kavout K-Score** | {k_str} |",
        f"| **최적 진입 방식** | {_entry_method} |",
        f"| **핵심 근거** | {_core_rationale} |",
        f"| **스윙 무효화 조건** | {_invalidation_price} 일봉 종가 하회 시 판정 무효 |",
        "",
        "---",
        "",
        "#### 최종 결정 (Final Decision Box)",
        "",
        "| 항목 | 내용 |",
        "|------|------|",
        f"| **행동** | **{r.action}** |",
        f"| **확신도** | {conviction_num}/10 ({r.conviction.level.upper()}) |",
        f"| **호라이즌 신뢰도** | {confidence_pct}% (신호 {ts.signal_count if ts else 'N/A'}/8 기반) |",
        f"| **Kavout K-Score** | {k_str} |",
        f"| **투자 방향** | {direction_label} |",
        f"| **목표 보유기간** | {_hold_period} |",
        f"| **리스크 프로필** | 제한적 손실 / 레버리지 수익 (롱옵션) |",
        f"| **진입 방식** | 시장가 또는 지정가 (스프레드 <= 2%) |",
        "",
    ]

    # DA 차감 이유 박스 (있을 때만)
    if r.da_reasons:
        _da_total = sum(
            15 if "IV Crush" in reason
            else 20 if "Thesis 반박" in reason
            else 10 if "내부자" in reason
            else 5 if "EPS 미스" in reason
            else 5
            for reason in r.da_reasons
        )
        lines += [
            "#### ⚠️ Devil's Advocate 차감 내역",
            "",
            f"> **총 차감: -{_da_total}pt**",
            "",
            *[f"> - {reason}" for reason in r.da_reasons],
            "",
        ]

    # TYPE 1: 뉴스 감성 — 풍부한 형식으로 출력
    lines += _format_type1_section(sent or {}, fv=fv).splitlines()
    lines += [""]

    # TYPE 2: 투자 기간 & 기간별 옵션 추천 ──────────────────────────────────
    lines += ["## ━━━ TYPE 2 · 투자 기간 & 옵션 추천 ━━━", ""]
    _hz_labels = {
        "단기":  f"단기 (DTE {st.DTE_SHORT_MIN}-{st.DTE_SHORT_MAX})",
        "중기":  f"중기 (DTE {st.DTE_MID_MIN}-{st.DTE_MID_MAX})",
        "장기":  f"장기 (DTE {st.DTE_LONG_MIN}-{st.DTE_LONG_MAX})",
        "초장기": f"초장기 LEAPS (DTE {st.DTE_ULTRA_MIN}-{st.DTE_ULTRA_MAX})",
    }
    _hz_all = ["단기", "중기", "장기"]
    _active = set(investment_horizons or [])
    for _hz in _hz_all:
        _ok = "✅" if _hz in _active else "❌"
        _rec = (horizon_recs or {}).get(_hz)
        if _hz in _active:
            # 분류 근거 간략히
            if _hz == "단기":
                _why = "강한 단기 모멘텀 (RSI≥75 + ADX≥30 + RVOL≥1.5 + 트리거)"
            elif _hz == "중기":
                _why = "ADX 추세 + MA 정배열 확인"
            else:
                _why = "구조적 성장 (PEG 또는 매출성장+K-Score)"
            lines.append(f"**{_ok} {_hz_labels[_hz]}** — {_why}")
        else:
            lines.append(f"**{_ok} {_hz_labels[_hz]}**")

        if _rec:
            _spot_px = fv.price if fv and fv.price else 100
            _lev = round((abs(_rec.greeks.delta) * _spot_px) / _rec.mid_price, 1) if _rec.mid_price > 0 else "N/A"
            _cost_1c = _rec.mid_price * 100  # 1계약 비용
            _budget = 0.0
            _contracts_possible = 0
            _budget_warn = ""
            try:
                from shared.config import get_config as _gcfg
                _cfg2 = _gcfg()
                if _hz == "단기":
                    _budget = _cfg2.budget_2nd
                elif _hz == "중기":
                    _budget = _cfg2.budget_1st
                else:
                    _budget = _cfg2.budget_2nd
                # 계약 수 = 예산 내 최대 (최소 1계약)
                _contracts_possible = max(1, int(_budget / (_cost_1c + _cfg2.COMMISSION_PER_CONTRACT)))
                _actual_cost = _cost_1c * _contracts_possible + _cfg2.COMMISSION_PER_CONTRACT * _contracts_possible
                if _actual_cost > _budget:
                    _budget_warn = f" ⚠️ 예산 초과 (1계약 ${_cost_1c:,.0f} > 배정 ${_budget:,.0f})"
            except Exception:
                pass
            import datetime as _hzdt
            _hz_dte = ((_rec.expiry - _hzdt.date.today()).days) if _rec.expiry else "?"
            _contract_str = f"{_contracts_possible}계약" if _contracts_possible else "?"
            # 선택 이유: delta target 대비 이격, OI, 스프레드 표시
            _tgt_map = {"단기": st.DELTA_SHORT_TARGET, "중기": st.DELTA_MID_TARGET, "장기": st.DELTA_LONG_TARGET}
            _tgt = _tgt_map.get(_hz, 0.50)
            _rec_oi = getattr(_rec, 'oi', 0) or 0
            _rec_spread = getattr(_rec, 'spread_pct', None)
            _delta_gap = abs(abs(_rec.greeks.delta) - _tgt)
            _why_parts = [f"delta {abs(_rec.greeks.delta):.2f} (target {_tgt:.2f}, 이격 {_delta_gap:.2f})"]
            if _rec_oi and _rec_oi > 0:
                _why_parts.append(f"OI {_rec_oi:,}계약")
            if _rec_spread:
                _why_parts.append(f"spread {_rec_spread:.1f}%")
            # 조정 제안: delta 바꾸면 어떤 Strike가 선택되는지
            _adj_hint = ""
            if _hz == "중기":
                if abs(_rec.greeks.delta) > _tgt:
                    _adj_hint = f" (더 OTM 원하면 delta target을 낮추면 됩니다)"
                else:
                    _adj_hint = f" (더 ITM 원하면 delta target을 높이면 됩니다)"
            lines += [
                f"  - Strike **${_rec.strike:.0f}** | DTE {_hz_dte}일"
                f" | Delta {abs(_rec.greeks.delta):.2f} | IV {_rec.greeks.iv * 100:.1f}%"
                f" | IVR {_rec.greeks.ivr:.0f}",
                f"  - 프리미엄 **${_rec.mid_price:.2f}** (1계약 ${_cost_1c:,.0f})"
                f" | 레버리지 ~{_lev}배"
                f" | 예산 **${_budget:,.0f}** → **{_contract_str}** 가능{_budget_warn}",
                f"  - 선택 이유: {' / '.join(_why_parts)}{_adj_hint}",
            ]
        elif _hz in _active:
            lines.append("  - 체인 데이터 없음 (장외 시간 또는 OI 부족)")
        lines.append("")

    # ── 초장기 (LEAPS) 섹션 ─────────────────────────────────────────────────
    _has_ultra = "초장기" in _active
    _ok_ultra = "✅" if _has_ultra else "❌"
    _ultra_rec = (horizon_recs or {}).get("초장기")  # 체인 있으면 OptionValidity
    lines.append(f"**{_ok_ultra} {_hz_labels['초장기']}**"
                 + (" — LEAPS 베팅 (PEG/성장률/K-Score 기반)" if _has_ultra else ""))
    if _ultra_rec:
        # 체인 데이터가 있어서 자동 선택된 계약
        import datetime as _uzdt
        _uz_dte = ((_ultra_rec.expiry - _uzdt.date.today()).days) if _ultra_rec.expiry else "?"
        lines += [
            f"  - Strike **${_ultra_rec.strike:.0f}** | DTE {_uz_dte}일"
            f" | Delta {abs(_ultra_rec.greeks.delta):.2f} | IV {_ultra_rec.greeks.iv * 100:.1f}%",
            f"  - 프리미엄 **${_ultra_rec.mid_price:.2f}** (1계약 ${_ultra_rec.mid_price * 100:,.0f})",
            f"  - 유효성: {'✅ 유효' if _ultra_rec.is_valid else '⚠️ 무효 — ' + _ultra_rec.exclusion_reason}",
        ]
    elif ultra_long_criteria:
        # 체인 없음 — 기준 제시 방식
        _uc = ultra_long_criteria
        lines += [
            f"  - 방향: **{_uc.get('direction', 'N/A')}**"
            f" | DTE 범위: {_uc.get('dte_range', 'N/A')}",
            f"  - Delta 범위: {_uc.get('delta_range', 'N/A')}"
            f" (target {_uc.get('delta_target', 'N/A')})",
            f"  - Strike 범위(추정): **{_uc.get('strike_range', 'N/A')}**",
            f"  - 최소 OI: {_uc.get('min_oi', 200)}계약"
            f" | 최대 Spread: {_uc.get('max_spread_pct', 10.0)}%",
            f"  - ⚠️ {_uc.get('note', '브로커에서 직접 확인 필요')}",
        ]
    elif _has_ultra:
        lines.append("  - 체인 데이터 없음 — 브로커에서 직접 확인 필요")
    lines.append("")

    # 자본 배분 요약 (중기 기준 1차 진입)
    try:
        from shared.config import get_config as _gcfg2
        _c2 = _gcfg2()
        lines += [
            "**자본 배분 (1차 기준)**",
            "",
            f"| 항목 | 금액 |",
            f"|------|------|",
            f"| 총 자산 | ${_c2.TOTAL_CAPITAL:,.0f} |",
            f"| 투자 가능 (유보 {_c2.NEXT_TRADE_RESERVE_PCT*100:.0f}% 제외) | ${_c2.investable_capital:,.0f} |",
            f"| 1차 진입 ({_c2.ENTRY_1ST_PCT*100:.0f}%) | ${_c2.budget_1st:,.0f} |",
            f"| 2차 진입 ({_c2.ENTRY_2ND_PCT*100:.0f}%, 방향 확인 후) | ${_c2.budget_2nd:,.0f} |",
            f"| 보험 현금 ({_c2.RESERVE_PCT*100:.0f}%) | ${_c2.budget_reserve:,.0f} |",
            "",
        ]
    except Exception:
        pass

    # TYPE 3: 기술 분석 (실제 지표값 + LLM 내러티브 포함)
    tech_narrative = sent.get("technical_narrative") if sent else None
    lines += _format_type3_section(r, ts, sc, regime, fv=fv, narrative=tech_narrative).splitlines()
    lines += [""]

    # Technical Dashboard (TYPE 3 섹션)
    if ts:
        ma_label = "정배열" if ts.ma_alignment == "bullish" else "역배열" if ts.ma_alignment == "bearish" else "혼조"
        lines += [
            "### 3-9. 기술 분석 세부 대시보드",
            "",
            "#### 기술 분석 세부 (Technical Dashboard)",
            "",
            "```",
            f"MA 정배열   : {ts.ma_alignment} ({ma_label})",
            f"ADX 점수    : {ts.adx_score:.1f}/25  {'[강함]' if ts.adx_score >= 18 else '[보통]' if ts.adx_score >= 10 else '[약함]'}",
            f"RSI 점수    : {ts.rsi_score:.1f}/25  {'[적정]' if ts.rsi_score >= 15 else '[주의]'}",
            f"MACD 점수   : {ts.macd_score:.1f}/25  {'[신호확인]' if ts.macd_score >= 15 else '[미확인]'}",
            f"RVOL 점수   : {ts.rvol_score:.1f}/25  {'[급등]' if ts.rvol_score >= 18 else '[보통]' if ts.rvol_score >= 10 else '[약함]'}",
            f"추세 확인   : {'확인됨' if ts.trend_confirmed else '미확인'}",
            f"자금유입    : {'확인됨' if ts.capital_flow_confirmed else '미확인'}",
            f"신호 합계   : {ts.signal_count}/8 (신뢰도 {confidence_pct}%)",
            "```",
            "",
        ]

    # ── TYPE 4: Swing Analysis ──────────────────────────────────
    lines += ["## ━━━ TYPE 4 · 스윙 트레이딩 셋업 ━━━", ""]
    signal_count = ts.signal_count if ts else 0
    rsi_score = ts.rsi_score if ts else 0
    macd_score = ts.macd_score if ts else 0
    ma_score_str = ts.ma_alignment if ts else 'mixed'
    ma_num = 4 if ma_score_str == 'bullish' else (1 if ma_score_str == 'bearish' else 2)

    if sc:
        stop_prem = sc.stop_loss_premium or 0.0
        t1_val = sc.target_premium_1st or 0.0
        t2_val = sc.target_premium_2nd if sc.target_premium_2nd > 0 else round(t1_val * 1.5, 2)
        t3_val = sc.target_premium_3rd if sc.target_premium_3rd > 0 else round(t1_val * 2.0, 2)

        # Near-Term: tighter stop (80% of standard), T1 only
        nt_stop = round(stop_prem * 0.80, 2) if stop_prem > 0 else None
        nt_t1 = t1_val if t1_val > 0 else None
        if nt_stop and nt_t1 and stop_prem > 0:
            # 진입 프리미엄 역산: stop = entry × STOP_RATIO(0.5) → entry = stop / 0.5
            _entry_prem_nt = round(stop_prem / 0.5, 2)
            _nt_risk = _entry_prem_nt - nt_stop          # 리스크 = 진입 - 손절
            _nt_reward = nt_t1 - _entry_prem_nt          # 보상 = T1 - 진입
            nt_rr = round(_nt_reward / _nt_risk, 1) if _nt_risk > 0 else "N/A"
        else:
            nt_rr = "N/A"

        # Swing: standard stop, T1+T2+T3 targets
        sw_stop = stop_prem if stop_prem > 0 else None
        sw_t1 = t1_val if t1_val > 0 else None
        sw_t3 = t3_val if t3_val > 0 else None
        if sw_t3 and stop_prem and stop_prem > 0:
            # 진입 프리미엄 역산: stop = entry × STOP_RATIO(0.5) → entry = stop / 0.5
            _entry_prem_rr = round(stop_prem / 0.5, 2)
            _risk = _entry_prem_rr - stop_prem          # 리스크 = 진입 - 손절
            _reward = sw_t3 - _entry_prem_rr            # 보상 = T3 - 진입
            sw_rr = round(_reward / _risk, 1) if _risk > 0 else "N/A"
        else:
            sw_rr = "N/A"

        # DTE-based time stops
        dte_val = max(7, min(45, signal_count * 5))
        if ov and getattr(ov, 'dte_ok', None) is not None:
            dte_val = max(7, min(45, signal_count * 5))
        nt_time_stop = min(dte_val // 3, 5)
        sw_time_stop = min(dte_val // 2, 15)

        def _fmt(v: float | None) -> str:
            return f"${v:.2f}" if v else "N/A"

        nt_t2_str = "N/A"
        nt_t3_str = "N/A"

        nt_row = f"| Near-Term (1-5일) | Current market | {_fmt(nt_stop)} | {_fmt(nt_t1)} / {nt_t2_str} / {nt_t3_str} | {nt_rr} | {nt_time_stop}일 |"
        sw_row = f"| Swing (5-15일) | Current market | {_fmt(sw_stop)} | {_fmt(sw_t1)} / {_fmt(t2_val)} / {_fmt(sw_t3)} | {sw_rr} | {sw_time_stop}일 |"
    else:
        dte_val = max(7, signal_count * 5)
        nt_time_stop = min(dte_val // 3, 5)
        sw_time_stop = min(dte_val // 2, 15)
        nt_row = f"| Near-Term (1-5일) | Current market | N/A | N/A / N/A / N/A | N/A | {nt_time_stop}일 |"
        sw_row = f"| Swing (5-15일) | Current market | N/A | N/A / N/A / N/A | N/A | {sw_time_stop}일 |"

    # Preferred horizon
    if signal_count >= 6:
        preferred = "SWING"
        preferred_reason = f"signal_count={signal_count}/8, 추세 지속력 충분"
    elif signal_count >= 4:
        preferred = "NEAR-TERM"
        preferred_reason = f"signal_count={signal_count}/8, 단기 모멘텀 우선"
    else:
        preferred = "NEITHER"
        preferred_reason = f"signal_count={signal_count}/8, 관망 권고"

    lines += [
        "### 4-1. 듀얼 호라이즌 진입/손절/목표",
        "",
        "#### 듀얼 호라이즌 (Near-Term / Swing)",
        "",
        "| 항목 | 진입 | 손절 | T1/T2/T3 목표 | R/R | Time Stop |",
        "|------|------|------|----------------|-----|-----------|",
        nt_row,
        sw_row,
        "",
    ]

    # Timeframe Alignment
    def _tf_signal(score: float) -> str:
        norm = score * 4  # 0-25 → 0-100
        if norm >= 65:
            return "🟢 Bullish"
        elif norm >= 40:
            return "🟡 Neutral"
        else:
            return "🔴 Bearish"

    regime_trend = macro_label if macro_label != "N/A" else "N/A"

    lines += [
        "### 4-2. 타임프레임 정렬 (Timeframe Alignment)",
        "",
        "**Timeframe Alignment:**",
        "",
        "| Timeframe | Signal | 근거 |",
        "|-----------|--------|------|",
        f"| 1H | {_tf_signal(rsi_score)} | RSI score {rsi_score}/25 |",
        f"| 4H | {_tf_signal(macd_score)} | MACD score {macd_score}/25 |",
        f"| Daily | {'🟢 Bullish' if ma_num >= 3 else ('🔴 Bearish' if ma_num <= 1 else '🟡 Neutral')} | MA Alignment: {ma_score_str} |",
        f"| Weekly | {regime_trend} | Market Regime |",
        "",
        f"**Preferred Horizon:** {preferred} — {preferred_reason}",
        "",
    ]

    # ── TYPE 4 추가: 호라이즌별 설정 분석 + 비교표 + Decision Summary ──

    # LLM 내러티브에서 호라이즌 bias 및 주가 레벨 추출
    _tech_nar    = (sent.get("technical_narrative") or {}) if sent else {}
    nt_bias      = _tech_nar.get("near_term_bias", "NEUTRAL")
    sw_bias      = _tech_nar.get("swing_bias", "NEUTRAL")
    _entry_nar_t = _tech_nar.get("entry_timing_rationale", "")
    _risk_nar_t  = _tech_nar.get("risk_scenario_narrative", "")
    kl_entry     = _tech_nar.get("key_level_entry")
    kl_stop      = _tech_nar.get("key_level_stop")
    kl_t1        = _tech_nar.get("key_level_target1")
    kl_t2        = _tech_nar.get("key_level_target2")

    # 프리미엄 재계산 (위 if sc 블록과 동일 로직)
    _ep_v   = round(sc.stop_loss_premium / 0.5, 2) if sc and sc.stop_loss_premium else None
    _ns_v   = round(sc.stop_loss_premium * 0.80, 2) if sc and sc.stop_loss_premium else None
    _nt1_v  = sc.target_premium_1st if sc else None
    _ss_v   = sc.stop_loss_premium if sc else None
    _st1_v  = sc.target_premium_1st if sc else None
    _st3_v  = (sc.target_premium_3rd if sc and sc.target_premium_3rd and sc.target_premium_3rd > 0
               else (round(sc.target_premium_1st * 2.5, 2) if sc and sc.target_premium_1st else None))

    def _pp(v) -> str:  # 프리미엄 포맷
        return f"${v:.2f}" if v else "N/A"

    def _sp(v) -> str:  # 주가 포맷
        return f"${v:.2f}" if v else "N/A"

    # R/R 재계산
    _nt_rr_v = "N/A"
    _sw_rr_v = "N/A"
    if _ep_v and _ns_v and _nt1_v:
        _r = _ep_v - _ns_v
        _w = _nt1_v - _ep_v
        _nt_rr_v = str(round(_w / _r, 1)) if _r > 0 else "N/A"
    if _ep_v and _ss_v and _st3_v:
        _r = _ep_v - _ss_v
        _w = _st3_v - _ep_v
        _sw_rr_v = str(round(_w / _r, 1)) if _r > 0 else "N/A"

    # 설정 품질 및 신호 분류
    nt_quality  = "Good" if signal_count >= 7 else ("Fair" if signal_count >= 4 else "Poor")
    sw_quality  = "Good" if signal_count >= 7 else ("Fair" if signal_count >= 5 else "Poor")
    nt_signal   = "BUY" if signal_count >= 6 else ("WAIT" if signal_count >= 4 else "HOLD")
    sw_signal   = "BUY" if signal_count >= 6 else ("WAIT" if signal_count >= 4 else "HOLD")
    _be = {"BULLISH": "🟢", "BEARISH": "🔴"}.get(nt_bias, "🟡")
    _bs = {"BULLISH": "🟢", "BEARISH": "🔴"}.get(sw_bias, "🟡")

    # 호라이즌 추천 아이콘
    nt_rec = ("✅ 유리" if preferred == "NEAR-TERM" else
              ("⚪ 조건부" if preferred == "BOTH" else "⏸ 보류"))
    sw_rec = ("✅ 유리" if preferred == "SWING" else
              ("⚪ 조건부" if preferred == "BOTH" else "⏸ 보류"))
    nt_dec = r.action if preferred in ("NEAR-TERM", "BOTH") else "보류"
    sw_dec = r.action if preferred in ("SWING", "BOTH") else "보류"

    # 주가 레벨 (LLM → fv pivot 폴백)
    _sk_entry = _sp(kl_entry)  if kl_entry else (_sp(fv.pivot)    if fv and fv.pivot    else "N/A")
    _sk_stop  = _sp(kl_stop)   if kl_stop  else (_sp(fv.pivot_s1) if fv and fv.pivot_s1 else "N/A")
    _sk_t1    = _sp(kl_t1)     if kl_t1    else (_sp(fv.pivot_r1) if fv and fv.pivot_r1 else "N/A")
    _sk_t2    = _sp(kl_t2)     if kl_t2    else "N/A"

    lines += ["", "---", ""]

    # 호라이즌별 설정 분석 문단
    lines += ["### 4-3. 호라이즌별 설정 분석", ""]

    lines += [f"**📋 Near-Term 설정 (1~5일)** {_be} {nt_bias}", ""]
    lines += [
        "| 항목 | 값 |",
        "|------|-----|",
        f"| 설정 품질 | **{nt_quality}** |",
        f"| 신호 수 | {signal_count}/8 |",
        f"| 1H RSI 점수 | {rsi_score:.0f}/25 |",
        f"| 4H MACD 점수 | {macd_score:.0f}/25 |",
        f"| 진입 프리미엄 | {_pp(_ep_v)} |",
        f"| 손절 (Stop) | {_pp(_ns_v)} (진입 대비 -60%) |",
        f"| T1 목표 | {_pp(_nt1_v)} |",
        f"| R/R | {_nt_rr_v}:1 |",
        f"| Time Stop | {nt_time_stop}일 |",
        "",
    ]
    if _entry_nar_t:
        lines += [f"**진입 근거:** {_entry_nar_t}", ""]

    lines += [f"**📋 Swing 설정 (5~15일)** {_bs} {sw_bias}", ""]
    lines += [
        "| 항목 | 값 |",
        "|------|-----|",
        f"| 설정 품질 | **{sw_quality}** |",
        f"| 신호 수 | {signal_count}/8 |",
        f"| Daily MA | {ma_score_str} |",
        f"| Weekly 레짐 | {macro_label} |",
        f"| 진입 프리미엄 | {_pp(_ep_v)} |",
        f"| 손절 (Stop) | {_pp(_ss_v)} (진입 대비 -50%) |",
        f"| T1 / T3 목표 | {_pp(_st1_v)} / {_pp(_st3_v)} |",
        f"| R/R | {_sw_rr_v}:1 |",
        f"| Time Stop | {sw_time_stop}일 |",
        "",
    ]
    if _risk_nar_t:
        lines += [f"**리스크:** {_risk_nar_t}", ""]

    # 호라이즌 비교 테이블
    lines += [
        "### 4-4. 호라이즌 비교 테이블",
        "",
        "**호라이즌 비교:**",
        "",
        "| 구분 | Near-Term (1~5일) | Swing (5~15일) |",
        "|------|-------------------|----------------|",
        f"| 추세 편향 | {_be} {nt_bias} | {_bs} {sw_bias} |",
        f"| 설정 품질 | {nt_quality} | {sw_quality} |",
        f"| 손절 프리미엄 | {_pp(_ns_v)} | {_pp(_ss_v)} |",
        f"| T1 목표 프리미엄 | {_pp(_nt1_v)} | {_pp(_st1_v)} |",
        f"| R/R | {_nt_rr_v}:1 | {_sw_rr_v}:1 |",
        f"| 추천 | {nt_rec} | {sw_rec} |",
        "",
    ]

    # Decision Summary 코드블록
    lines += [
        "### 4-5. Decision Summary (코드블록)",
        "",
        "```",
        f"DUAL-HORIZON SUMMARY — {r.ticker}",
        "══════════════════════════════════",
        "NEAR-TERM (1~5일):",
        f"  Signal    : {nt_signal}",
        f"  Entry     : Current market  (프리미엄 {_pp(_ep_v)})",
        f"  Stock Ref : Entry {_sk_entry}  |  Stop {_sk_stop}  |  T1 {_sk_t1}",
        f"  Premium   : Stop {_pp(_ns_v)}  |  T1 {_pp(_nt1_v)}",
        f"  R/R       : {_nt_rr_v}:1",
        f"  Decision  : {nt_dec}",
        "",
        "SWING (5~15일):",
        f"  Signal    : {sw_signal}",
        f"  Entry     : Current market  (프리미엄 {_pp(_ep_v)})",
        f"  Stock Ref : Entry {_sk_entry}  |  Stop {_sk_stop}  |  T2 {_sk_t2}",
        f"  Premium   : Stop {_pp(_ss_v)}  |  T1 {_pp(_st1_v)}  |  T3 {_pp(_st3_v)}",
        f"  R/R       : {_sw_rr_v}:1",
        f"  Decision  : {sw_dec}",
        "",
        f"RECOMMENDED : {preferred}",
        f"REASON      : {preferred_reason}",
        "```",
        "",
    ]

    # ── TYPE 5: Buy & Sell ──────────────────────────────────────
    lines += ["## ━━━ TYPE 5 · 매수 & 매도 종합 판단 ━━━", ""]

    # 복합 스코어카드 — 펀더멘털/애널리스트/차원 포함
    # 펀더멘털 데이터 (fv 있을 때)
    if fv:
        fwd_pe_str = f"{fv.forward_pe:.1f}x" if fv.forward_pe else "N/A"
        trail_pe_str = f"{fv.trailing_pe:.1f}x" if fv.trailing_pe else "N/A"
        eps_ttm_str = f"${fv.eps_ttm:.2f}" if fv.eps_ttm else "N/A"
        roe_str = f"{fv.roe_pct:.1f}%" if fv.roe_pct else "N/A"
        de_str = f"{fv.debt_equity / 100:.2f}x" if fv.debt_equity else "N/A"
        fcf_str = f"${fv.fcf_ttm:,.0f}M" if fv.fcf_ttm else "N/A"
        # 목표주가: 컨센서스(중간값) + 최고 목표가 범위 표시
        _sc_cur: float | None = None
        if sc and sc.base and sc.base.target_stock_price and sc.base.stock_move_pct is not None:
            _mv2 = sc.base.stock_move_pct / 100
            if _mv2 != -1:
                _sc_cur = sc.base.target_stock_price / (1 + _mv2)
        _ref_price = _sc_cur or (fv.price if fv.price else None)
        upside_str = "N/A"

        # 최고 목표주가(Street-High) 추가 표시
        _high_tp = getattr(fv, "target_price_high", None)
        if fv.target_price:
            if _high_tp and _high_tp > fv.target_price:
                target_str = f"${fv.target_price:.0f} (컨센서스) / ${_high_tp:.0f} (High)"
            else:
                target_str = f"${fv.target_price:.2f}"

            if _ref_price and _ref_price > 0:
                upside_pct = (fv.target_price - _ref_price) / _ref_price * 100
                high_upside = ((_high_tp - _ref_price) / _ref_price * 100) if _high_tp else None
                if upside_pct < -10:
                    # 컨센서스가 현재가 하회 — 주가가 애널리스트 목표를 추월한 상태
                    _high_info = f" | High: {high_upside:+.1f}%" if high_upside else ""
                    upside_str = (
                        f"컨센서스 {upside_pct:+.1f}%{_high_info} "
                        f"⚠️ 주가가 컨센서스 상회 (애널리스트 목표 갱신 전)"
                    )
                    if _high_tp and high_upside and high_upside > 0:
                        # High 목표가는 현재가 위에 있음 — 긍정적 신호
                        upside_str = (
                            f"컨센서스 {upside_pct:+.1f}% / High {high_upside:+.1f}% "
                            f"⚠️ 컨센서스 갱신 지연 — Street-High(${_high_tp:.0f})는 현재가 상회"
                        )
                elif upside_pct < 5:
                    upside_str = f"{upside_pct:+.1f}% ⚠️ 업사이드 부족 (콜옵션 진입 근거 약함)"
                else:
                    upside_str = f"{upside_pct:+.1f}%"
        else:
            target_str = "N/A"
        fund_row = f"펀더멘털 (Fwd PE {fwd_pe_str}, EPS {eps_ttm_str}, ROE {roe_str})"
        # 애널리스트 집계
        ab = fv.analyst_buy or 0
        ah = fv.analyst_hold or 0
        as_ = fv.analyst_sell or 0
        total_analysts = ab + ah + as_
        if total_analysts > 0:
            analyst_str = f"Buy {ab} / Hold {ah} / Sell {as_} (합계 {total_analysts}명)"
        else:
            analyst_str = "N/A"
    else:
        fund_row = "펀더멘털"
        fwd_pe_str = trail_pe_str = eps_ttm_str = roe_str = de_str = fcf_str = "N/A"
        target_str = upside_str = "N/A"
        analyst_str = "N/A"
        ab = ah = as_ = 0

    # 심리 점수 표현
    if sent:
        ovs = sent.get("overall_sentiment", "MIXED")
        sent_score_str = {"POSITIVE": "긍정", "MIXED": "혼조", "NEGATIVE": "부정"}.get(ovs, ovs)
        sent_conf = sent.get("confidence", "Low")
        sent_display = f"{sent_score_str} ({sent_conf})"
    else:
        sent_display = "N/A"

    # ── 3D 가중 스코어카드 계산 ─────────────────────────────────
    # 심리 점수 수치화 (overall_sentiment × confidence 조합)
    if sent:
        _ovs = sent.get("overall_sentiment", "MIXED")
        _conf = sent.get("confidence", "Low")
        _sent_base = {"POSITIVE": 75, "MIXED": 45, "NEGATIVE": 20}.get(_ovs, 45)
        _sent_adj = {"High": 15, "Medium": 0, "Low": -15}.get(_conf, 0)
        sent_score = min(100, max(0, _sent_base + _sent_adj))
    else:
        sent_score = 40  # 데이터 없음 → 중립 기본값

    # 3D 복합 점수 (가중 합산: Technical 40% + Macro 30% + Sentiment 30%)
    _composite = round(tech_score * 0.40 + macro_score * 0.30 + sent_score * 0.30, 1)
    _tech_contrib = round(tech_score * 0.40, 1)
    _macro_contrib = round(macro_score * 0.30, 1)
    _sent_contrib = round(sent_score * 0.30, 1)

    # 복합 판정 임계값
    if _composite >= 70:
        _verdict = "🟢 진입 가능"
    elif _composite >= 55:
        _verdict = "🟡 관찰 대기"
    elif _composite >= 40:
        _verdict = "🟠 보류"
    else:
        _verdict = "🔴 탈락"

    _mac_status = "충족" if macro_score >= 60 else "경고" if macro_score >= 40 else "미충족"
    _sent_kor = {"POSITIVE": "긍정", "MIXED": "혼조", "NEGATIVE": "부정"}
    _sent_label = (
        _sent_kor.get(sent.get("overall_sentiment", "MIXED"), "혼조") if sent else "N/A"
    )
    _sent_conf_str = f"({sent.get('confidence', 'N/A')})" if sent else "(N/A)"

    lines += [
        "### 5-1. 3D 복합 스코어카드 (Weighted Composite)",
        "",
        "| 차원 | 점수 | 가중치 | 기여 | 상태 |",
        "|------|------|--------|------|------|",
        f"| 기술적 (Technical) | {tech_score:.0f}/100 | 40% | {_tech_contrib:.1f}pt | {tech_status} |",
        f"| 거시 (Macro/Regime) | {macro_score}/100 | 30% | {_macro_contrib:.1f}pt | {_mac_status} — {macro_label} |",
        f"| 심리 (Sentiment) | {sent_score:.0f}/100 | 30% | {_sent_contrib:.1f}pt | {_sent_label} {_sent_conf_str} |",
        f"| **복합 (Composite)** | **{_composite:.1f}/100** | 100% | — | **{_verdict}** |",
        "",
    ]

    # 차원 간 갈등 분석
    _gap = abs(tech_score - sent_score)
    _tech_dominant = tech_score > sent_score + 20
    _sent_dominant = sent_score > tech_score + 20
    _macro_ok = macro_score >= 60
    if _tech_dominant:
        _conflict_text = (
            f"기술적 차트({tech_score:.0f}점)는 강세 신호를 보내는 반면 "
            f"뉴스 센티멘트({sent_score:.0f}점)는 부정적입니다. "
            f"현재 주가는 펀더멘털보다 수급과 기술적 모멘텀에 의해 움직이고 있습니다. "
            f"{'거시 환경이 우호적이어서 기술적 추세가 유지되는 구간입니다.' if _macro_ok else '거시 환경까지 불리하면 돌발 급락 가능성에 주의해야 합니다.'}"
        )
    elif _sent_dominant:
        _conflict_text = (
            f"뉴스 센티멘트({sent_score:.0f}점)는 긍정적이나 "
            f"기술적 지표({tech_score:.0f}점)가 아직 뒷받침되지 않습니다. "
            f"재료는 좋지만 차트가 따라오지 않은 상태로, 기술적 확인 후 진입이 안전합니다. "
            f"{'거시 환경이 지지하므로 기술적 신호 개선 시 강한 상승 가능성이 있습니다.' if _macro_ok else '거시 환경도 불리하므로 신중한 접근이 필요합니다.'}"
        )
    elif _gap <= 10:
        _conflict_text = (
            f"기술적({tech_score:.0f}점)·센티멘트({sent_score:.0f}점)·거시({macro_score}점) "
            f"세 차원이 비교적 정렬되어 있습니다. "
            f"차원 간 모순이 작아 신호의 신뢰도가 높습니다."
        )
    else:
        _conflict_text = (
            f"기술적({tech_score:.0f}점)과 센티멘트({sent_score:.0f}점) 사이에 "
            f"{_gap:.0f}점 차이가 있습니다. "
            f"복합 점수 {_composite:.1f}점은 두 차원의 평균값으로, 어느 한 방향으로 갑자기 수렴할 수 있습니다."
        )
    lines += [
        "### 5-2. 차원 간 갈등 분석 (Dimensional Conflict)",
        "",
        "**🔀 차원 간 갈등 분석 (Dimensional Conflict)**",
        "",
        f"> {_conflict_text}",
        "",
    ]

    # 신뢰도 근거 설명
    _conv_val = r.conviction.total_conviction if r.conviction else 0
    _conf_reasons: list[str] = []
    if _conv_val < 0.6:
        _conf_reasons.append(f"확신도 {_conv_val:.2f}/1.0으로 기준(0.6) 미달")
    if ts and ts.signal_count < 5:
        _conf_reasons.append(f"신호 수 {ts.signal_count}/8 부족")
    if sent:
        _svt = sent.get("debate_verdict", "Neutral")
        if _svt in ("Slight Bear", "Bearish"):
            _conf_reasons.append(f"뉴스 판정이 방향과 반대 ({_svt})")
    if macro_score < 50:
        _conf_reasons.append(f"거시 환경 불리 ({macro_score}/100)")
    if not _conf_reasons:
        _conf_reasons.append(f"기술·센티멘트·거시 전 차원 기준 충족 (복합 {_composite:.1f}/100)")
    lines += [
        "### 5-3. 신뢰도 근거 설명 (Confidence Rationale)",
        "",
        "**🎯 신뢰도 근거 (Confidence Rationale)**",
        "",
        f"> 신뢰도 {confidence_pct}%: {' | '.join(_conf_reasons)}",
        "",
    ]

    # 펀더멘털 상세 (fv 있고 주요 지표 하나라도 있을 때)
    if fv and (fv.trailing_pe or fv.eps_ttm or fv.roe_pct or fv.fcf_ttm
               or fv.revenue_growth_yoy or fv.peg or fv.net_income_growth_yoy):
        # 추가 포맷 변수
        peg_str = f"{fv.peg:.2f}" if fv.peg else "N/A"
        rev_g_str = f"{fv.revenue_growth_yoy:+.1f}%" if fv.revenue_growth_yoy else "N/A"
        ni_g_str = f"{fv.net_income_growth_yoy:+.1f}%" if fv.net_income_growth_yoy else "N/A"
        eps_surp_str = f"{fv.eps_surprise_pct:+.1f}%" if fv.eps_surprise_pct else "N/A"
        lines += [
            "### 5-4. 펀더멘털 분석 (Fundamental Analysis)",
            "",
            "**📋 Fundamental Analysis**",
            "",
            "**기업 실적 요약**",
            "",
            "| 지표 | 값 | 평가 |",
            "|------|-----|------|",
            f"| 매출 성장 (YoY) | {rev_g_str} | {'긍정' if fv.revenue_growth_yoy and fv.revenue_growth_yoy > 10 else ('부정' if fv.revenue_growth_yoy and fv.revenue_growth_yoy < 0 else '중립')} |",
            f"| 순이익 성장 (YoY) | {ni_g_str} (GAAP) | {'긍정' if fv.net_income_growth_yoy and fv.net_income_growth_yoy > 0 else ('부정' if fv.net_income_growth_yoy and fv.net_income_growth_yoy < 0 else '중립')} |",
            f"| ROE | {roe_str} | {'긍정' if fv.roe_pct and fv.roe_pct > 15 else ('부정' if fv.roe_pct and fv.roe_pct < 5 else '중립')} |",
            f"| D/E 비율 | {de_str} | {'부정' if fv.debt_equity and fv.debt_equity > 200 else '중립'} |",
            f"| EPS 서프라이즈 | {eps_surp_str} | {'긍정' if fv.eps_surprise_pct and fv.eps_surprise_pct > 0 else ('부정' if fv.eps_surprise_pct and fv.eps_surprise_pct < 0 else '중립')} |",
            "",
            "**밸류에이션**",
            "",
            "| 지표 | 값 | 지표 | 값 |",
            "|------|-----|------|-----|",
            f"| P/E (TTM) | {trail_pe_str} | P/E (Fwd) | {fwd_pe_str} |",
            f"| PEG | {peg_str} | EPS (TTM) | {eps_ttm_str} |",
            f"| FCF (TTM) | {fcf_str} | 목표주가 | {target_str} |",
            f"| 업사이드 | {upside_str} | | |",
            "",
        ]
        # 펀더멘털 Bull vs Bear (rule-based)
        _fund_bull: list[str] = []
        _fund_bear: list[str] = []
        if fv.revenue_growth_yoy and fv.revenue_growth_yoy > 10:
            _fund_bull.append(f"매출 {rev_g_str} 고성장")
        if fv.net_income_growth_yoy and fv.net_income_growth_yoy > 0:
            _fund_bull.append(f"순이익 {ni_g_str} 흑자 성장")
        if fv.roe_pct and fv.roe_pct > 15:
            _fund_bull.append(f"ROE {roe_str} 우수")
        if fv.eps_surprise_pct and fv.eps_surprise_pct > 0:
            _fund_bull.append(f"EPS 서프라이즈 {eps_surp_str}")
        if fv.target_price and fv.price and fv.price > 0:
            _up = (fv.target_price - fv.price) / fv.price * 100
            if _up > 10:
                _fund_bull.append(f"애널리스트 목표가 업사이드 {upside_str}")
        if fv.revenue_growth_yoy and fv.revenue_growth_yoy < 0:
            _fund_bear.append(f"매출 역성장 ({rev_g_str})")
        if fv.net_income_growth_yoy and fv.net_income_growth_yoy < 0:
            _fund_bear.append(f"순이익 감소 ({ni_g_str})")
        if fv.debt_equity and fv.debt_equity > 200:
            _fund_bear.append(f"부채 비율 높음 (D/E {de_str})")
        if fv.peg and fv.peg > 2:
            _fund_bear.append(f"PEG {peg_str} — 고평가 가능성")
        _fund_bull_str = " / ".join(_fund_bull) if _fund_bull else "해당 없음"
        _fund_bear_str = " / ".join(_fund_bear) if _fund_bear else "해당 없음"
        lines += [
            "**펀더멘털 Bull vs Bear**",
            "",
            f"- 🟢 **Bull**: {_fund_bull_str}",
            f"- 🔴 **Bear**: {_fund_bear_str}",
            "",
        ]

    # 애널리스트 의견 (있을 때)
    if fv and (ab + ah + as_) > 0:
        # 시각화 바
        total = ab + ah + as_
        buy_bar = "█" * round(ab / total * 10)
        hold_bar = "░" * round(ah / total * 10)
        sell_bar = "▒" * round(as_ / total * 10)
        lines += [
            "### 5-5. 애널리스트 컨센서스 (Analyst Consensus)",
            "",
            "**👥 Analyst Consensus**",
            "",
            f"| Buy | Hold | Sell | 합계 | 컨센서스 목표가 | 업사이드 |",
            f"|-----|------|------|------|----------------|---------|",
            f"| {ab} ({ab/total:.0%}) | {ah} ({ah/total:.0%}) | {as_} ({as_/total:.0%}) | {total}명 | {target_str} | {upside_str} |",
            "",
            f"> `{buy_bar}{hold_bar}{sell_bar}` Buy↑ Hold░ Sell▒",
            "",
        ]

    # 기관 & 수급 동향 (fv 있을 때)
    if fv:
        _short_str = f"{fv.short_float_pct:.1f}%" if fv.short_float_pct else "N/A"
        _insider_str = f"{fv.insider_trans_pct:+.1f}%" if fv.insider_trans_pct else "N/A"
        _beta_str = f"{fv.beta:.2f}" if fv.beta else "N/A"
        # 공매도 해석
        if fv.short_float_pct and fv.short_float_pct > 15:
            _short_note = "높은 공매도 → 쇼트 스퀴즈 가능성 / 하락 압력 주의"
        elif fv.short_float_pct and fv.short_float_pct < 5:
            _short_note = "낮은 공매도 → 수급 안정"
        else:
            _short_note = "보통 수준"
        # 내부자 거래 해석
        if fv.insider_trans_pct and fv.insider_trans_pct > 0:
            _insider_note = "내부자 순매수 → 긍정 시그널"
        elif fv.insider_trans_pct and fv.insider_trans_pct < -5:
            _insider_note = "내부자 대량 매도 → 주의"
        elif not fv.insider_trans_pct:  # None 또는 0
            _insider_note = "데이터 없음 — Section 0 DA 차감 참조"
        else:
            _insider_note = "내부자 거래 중립"
        # 베타 해석
        if fv.beta and fv.beta > 1.5:
            _beta_note = "고변동성 — 시장 대비 급격한 움직임 가능"
        elif fv.beta and fv.beta < 0.8:
            _beta_note = "저변동성 — 방어적 성격"
        else:
            _beta_note = "시장 수준 변동성"
        lines += [
            "### 5-6. 기관 & 수급 동향",
            "",
            "**🏦 기관 & 수급 동향**",
            "",
            "| 지표 | 값 | 해석 |",
            "|------|-----|------|",
            f"| 공매도 비율 | {_short_str} | {_short_note} |",
            f"| 내부자 거래 | {_insider_str} | {_insider_note} |",
            f"| Beta | {_beta_str} | {_beta_note} |",
            "",
        ]

    # 실행 계획
    # Time Stop
    dte_proxy = max(7, signal_count * 5)
    time_stop_days = min(dte_proxy // 2, 15)
    time_stop_str = f"{time_stop_days}일 내 T1 미달성 시 청산 검토"

    # Monitoring Schedule
    if dte_proxy <= 7:
        monitoring = "매일 장 시작 + 점심 체크 (2회/일)"
    elif dte_proxy <= 21:
        monitoring = "매일 장 시작 전 체크 (1회/일)"
    else:
        monitoring = "격일 체크 (0.5회/일)"

    # Nice-to-Have
    nice_to_have: list[str] = []
    if ts:
        if getattr(ts, 'rvol_score', 0) > 17:
            nice_to_have.append("✓ Volume breakout 확인")
        if getattr(ts, 'adx_score', 0) > 18:
            nice_to_have.append("✓ 추세 강도 우수 (ADX 강함)")
        # RSI 과열 여부는 실제 rsi14 값(fv)으로 판단; rsi_score(0~25)는 점수이므로 부적합
        _rsi14_actual = fv.rsi14 if fv and fv.rsi14 else None
        if _rsi14_actual is not None and 40 <= _rsi14_actual <= 65:
            nice_to_have.append(f"✓ RSI {_rsi14_actual:.0f} 적정 범위 (과열 없음)")
        elif _rsi14_actual is not None and _rsi14_actual > 70:
            nice_to_have.append(f"⚠️ RSI {_rsi14_actual:.0f} 과매수 구간 — 눌림목 주의")
    # 시나리오 경고 (delta_gap_risk) — 고IV·낮은델타·세타과다 등
    if sc and getattr(sc, "delta_gap_risk", "") and sc.delta_gap_risk not in ("해당 없음", ""):
        for _dg in sc.delta_gap_risk.split(";"):
            _dg = _dg.strip()
            if _dg:
                nice_to_have.append(f"⚠️ {_dg}")
    if not nice_to_have:
        nice_to_have.append("현재 보조 조건 없음")

    lines += [
        "### 5-9. 실행 계획 (Execution Plan — 옵션)",
        "",
        "| 항목 | 값 |",
        "|------|----|",
        f"| 행사가 (Strike) | ${r.strike:,.2f} |",
        f"| 만기 (Expiry) | {r.expiry} (DTE: {max(0, (r.expiry - date.today()).days)}일) |",
        f"| 현재 프리미엄 (Entry) | {entry_prem_str} |",
        f"| 델타 (Delta) | {delta_str} |",
        f"| 감마 (Gamma) | {gamma_str} |",
        f"| 베가 (Vega/$1 IV) | {vega_str} |",
        f"| 내재변동성 (IV) | {iv_str} |",
        f"| IV Rank (IVR) | {ivr_str} |",
        f"| 세타 (Theta/일) | {theta_str} |",
        f"| 계약 수 | {r.contracts}계약 |",
        f"| 투자금 | ${r.capital_allocation:,.0f} |",
        f"| R/R 비율 | {t5_rr:.1f}:1 |",
        f"| 손절 프리미엄 (Stop) | {stop_str} |",
        f"| 1차 익절 (T1) | {t1_str} |",
        f"| 2차 익절 (T2) | {t2_str} |",
        f"| 3차 익절 (T3) | {t3_str} |",
        f"| 트레일링 스탑 | {trailing_str} |",
        "",
        f"**⏱ Time Stop:** {time_stop_str}",
        f"**📅 Monitoring:** {monitoring}",
        f"**Nice-to-Have:** {', '.join(nice_to_have)}",
        "",
    ]

    # 거래 조건 (TYPE 5)
    lines += ["### 5-10. 거래 조건 체크 (Conditions for Trade)", ""]
    if ov:
        def cond_icon(ok: bool) -> str:
            return "충족" if ok else "미충족"
        lines += [
            "**Must-Have 조건 (옵션 유효성):**",
            "",
            "| 조건 | 결과 |",
            "|------|------|",
            f"| Delta {st.DELTA_MID_MIN:.2f}~{st.DELTA_MID_MAX:.2f} (중기 기준) | {cond_icon(ov.delta_ok)} |",
            f"| IVR <= 70 | {cond_icon(ov.ivr_ok)}{' [경고구간]' if ov.ivr_warning else ''} |",
            f"| OI >= 500 | {cond_icon(ov.oi_ok)}{' [경고구간]' if ov.oi_warning else ''} |",
            f"| 스프레드 <= 5% | {cond_icon(ov.spread_ok)} |",
            f"| DTE >= 21일 | {cond_icon(ov.dte_ok)} |",
            "",
        ]
        if ov.exclusion_reason:
            lines += [f"> 제외 사유: {ov.exclusion_reason}", ""]
    else:
        lines += ["> 옵션 유효성 데이터 없음 (체인 데이터 미수신)", ""]

    lines += ["### 5-7. 리스크 평가 (Risk Assessment)", ""]

    if r.risk_factors:
        lines += [
            "**무효화 트리거 (Invalidation Triggers):**",
            "",
            *[f"- {rf}" for rf in r.risk_factors],
            "",
        ]

    # 종목 특성 리스크
    if fv:
        _atr_val = fv.atr
        _beta_val = fv.beta
        _price_val = fv.price
        _atr_pct_str = "N/A"
        _lev_rec = "표준 (1x)"
        _split_rec = "단일 진입 가능"
        if _atr_val and _price_val and _price_val > 0:
            _atr_pct = _atr_val / _price_val * 100
            _atr_pct_str = f"{_atr_pct:.1f}%"
            if _atr_pct > 4:
                _lev_rec = "레버리지 자제 (일일 변동 과대)"
                _split_rec = "분할 진입 권장 (3회 이상)"
            elif _atr_pct > 2:
                _lev_rec = "최대 1x (ATR 중간)"
                _split_rec = "2회 분할 진입 권장"
        if _beta_val and _beta_val > 1.5 and _lev_rec == "표준 (1x)":
            _lev_rec = "레버리지 주의 (고베타)"
        lines += [
            "**⚡ 종목 특성 리스크**",
            "",
            "| 항목 | 값 | 의미 |",
            "|------|-----|------|",
            f"| ATR (일평균 변동) | {_atr_pct_str} | 하루 예상 등락폭 |",
            f"| Beta | {_beta_str} | 시장 대비 민감도 |",
            f"| 레버리지 권고 | {_lev_rec} | — |",
            f"| 진입 방식 | {_split_rec} | — |",
            "",
        ]

    # ── 행동 계획 (Action Plan) ───────────────────────────────────
    lines += ["### 5-11. 행동 계획 세부 (Execution Detail)", ""]
    # conviction은 0~1 스케일 (0.6 = 6/10)
    _conviction_ok = (r.conviction.total_conviction >= 0.6) if r.conviction else False
    _sig_ok = signal_count >= 5
    # action은 한국어("진입"/"관찰") 또는 영어 모두 지원
    _action_buy = r.action in ("진입", "BUY", "STRONG_BUY")
    _action_watch = r.action in ("관찰", "WATCH")

    lines += [
        "#### 행동 계획 (Action Plan)",
        "",
        "**📌 보유자 (현재 포지션 있음):**",
        "",
        f"- **Stop 유지**: 프리미엄 {stop_str} 이하 도달 시 즉시 청산",
        f"- **T1 도달 시 ({t1_str})**: 50% 부분 익절",
        f"- **잔여 50%**: T2 ({t2_str}) 향해 트레일링 유지",
        f"- **Time Stop**: {time_stop_str}",
        "",
    ]

    _conv_display = f"{r.conviction.total_conviction:.2f}/1.0" if r.conviction else "N/A"
    if (_action_buy or _action_watch) and _conviction_ok and _sig_ok:
        _entry_note = (
            f"신규 진입 가능 — 컨빅션 {_conv_display}, "
            f"신호 {signal_count}/8, 복합 점수 {_composite:.1f}/100"
        )
        _entry_action = f"현재 시장가 ({entry_prem_str}) 진입 검토"
    elif (_action_buy or _action_watch) and (_conviction_ok or _sig_ok):
        _entry_note = (
            f"조건부 진입 대기 — 컨빅션 {_conv_display}, "
            f"신호 {signal_count}/8"
        )
        _entry_action = "추가 확인 후 진입 (다음 캔들 확인 권장)"
    else:
        _entry_note = (
            f"진입 보류 — 컨빅션 {_conv_display}, "
            f"신호 {signal_count}/8 불충족"
        )
        _entry_action = "조건 충족 시까지 대기"

    lines += [
        "**📋 미보유자 (신규 진입 검토):**",
        "",
        f"- **판단**: {_entry_note}",
        f"- **행동**: {_entry_action}",
        f"- **진입 기준**: 복합 점수 ≥55 + 신호 ≥5/8 + IVR ≤70",
        f"- **최대 리스크**: 투자금 ${r.capital_allocation:,.0f}의 50%"
        f" = ${r.capital_allocation * 0.5:,.0f}",
        "",
    ]

    # 투자자 유형별 행동계획 (rule-based)
    # peg_str / rev_g_str 가 펀더멘털 블록 미진입 시 undefined 방지
    _peg_fb = fv.peg if fv else None
    _rev_g_fb = fv.revenue_growth_yoy if fv else None
    peg_str = f"{_peg_fb:.2f}" if _peg_fb else "N/A"
    rev_g_str = f"{_rev_g_fb:+.1f}%" if _rev_g_fb else "N/A"
    # 투자 방향성: 진입/관찰이면 bullish, 보류/탈락이면 bearish
    _is_bullish = r.action in ("진입", "관찰", "BUY", "STRONG_BUY", "WATCH")
    _tech_ok_flag = tech_score >= 60
    _sent_ok_flag = sent_score >= 60 if sent else False
    # 단기 트레이더
    if _is_bullish and _tech_ok_flag and _conviction_ok:
        _short_trader = f"진입 적극 검토 — 기술적 모멘텀 확인, 스탑 {stop_str} 엄수"
    elif _is_bullish and _tech_ok_flag:
        _short_trader = f"조건부 진입 대기 — 스탑 {stop_str} 설정 후 진입 확인"
    else:
        _short_trader = "관망 — 기술적 신호 미충족 또는 확신도 부족, 진입 보류"
    # 스윙 트레이더
    if _is_bullish and _composite >= 60 and _conviction_ok:
        _swing_trader = f"T1 {t1_str} 부분 익절 후 T2 {t2_str} 트레일링"
    elif _is_bullish and _composite >= 55:
        _swing_trader = f"소규모 진입 후 T1 {t1_str} 확인 시 추가 — 복합 {_composite:.0f}/100"
    else:
        _swing_trader = f"관망 — 복합 점수 {_composite:.0f}/100 조건 강화 후 재진입"
    # 성장주 투자자
    _rev_g_val = fv.revenue_growth_yoy if fv else None
    if _rev_g_val and _rev_g_val > 15:
        _growth_inv = f"중기 보유 고려 — 매출 성장 {rev_g_str} 지속 확인 필요"
    elif _rev_g_val and _rev_g_val > 0:
        _growth_inv = "소규모 포지션 — 성장 가속 여부 확인 후 확대"
    else:
        _growth_inv = "신중 접근 — 성장 모멘텀 데이터 부족"
    # 가치 투자자
    _peg_val = fv.peg if fv else None
    if _peg_val and _peg_val < 1.5:
        _value_inv = f"밸류에이션 매력적 (PEG {peg_str}) — 장기 매수 고려"
    elif _peg_val and _peg_val > 2.5:
        _value_inv = f"고평가 구간 (PEG {peg_str}) — 관망 또는 소규모만"
    else:
        _value_inv = "밸류에이션 중립 — 다른 지표 종합 판단 필요"
    # 기존 보유자
    _holder = f"Stop {stop_str} 유지 + T1 {t1_str} 도달 시 절반 익절"
    lines += [
        "### 5-8. 투자자 유형별 행동 계획 (Action Plan by Type)",
        "",
        "**👤 투자자 유형별 행동계획**",
        "",
        "| 투자자 유형 | 권장 행동 |",
        "|------------|---------|",
        f"| 단기 트레이더 | {_short_trader} |",
        f"| 스윙 트레이더 | {_swing_trader} |",
        f"| 성장주 투자자 | {_growth_inv} |",
        f"| 가치 투자자 | {_value_inv} |",
        f"| 기존 보유자 | {_holder} |",
        "",
    ]

    if r.risk_factors:
        lines += [
            "**⛔ 무효화 조건 (즉시 청산/진입 취소):**",
            "",
            *[f"- {rf}" for rf in r.risk_factors[:3]],
            "",
        ]

    # 시나리오 테이블 (TYPE 4+5)
    if sc:
        lines += [
            "### 5-12. 시나리오 계획 (Scenario Planning)",
            "",
            "| 시나리오 | 확률 | 주가 이동 | 순손익 | EV 기여 |",
            "|----------|------|-----------|--------|---------|",
            f"| 강세 (Bullish) | {sc.bullish.probability:.0%} | "
            f"+{sc.bullish.stock_move_pct:.1f}% | "
            f"${sc.bullish.net_profit:+,.0f} | "
            f"${sc.bullish.probability * sc.bullish.net_profit:+,.0f} |",
            f"| 기본 (Base) | {sc.base.probability:.0%} | "
            f"{sc.base.stock_move_pct:+.1f}% | "
            f"${sc.base.net_profit:+,.0f} | "
            f"${sc.base.probability * sc.base.net_profit:+,.0f} |",
            f"| 약세 (Bearish) | {sc.bearish.probability:.0%} | "
            f"{sc.bearish.stock_move_pct:+.1f}% | "
            f"${sc.bearish.net_profit:+,.0f} | "
            f"${sc.bearish.probability * sc.bearish.net_profit:+,.0f} |",
            f"| **기대값 (EV)** | — | — | **${sc.expected_value:+,.0f}** | — |",
            "",
        ]
        # Decision Summary 코드블록 (TYPE 4 형식)
        lines += [
            "```",
            f"[Decision Summary: {r.ticker}]",
            f"Action       : {r.action}",
            f"Direction    : {direction_label}",
            f"Conviction   : {conviction_num}/10 ({r.conviction.level.upper()})",
            f"Confidence   : {confidence_pct}%",
            f"EV           : ${sc.expected_value:+,.0f}",
            f"T1           : {t1_str}",
            f"T2           : {t2_str}",
            f"T3           : {t3_str}",
            f"Stop         : {stop_str}",
            "```",
            "",
        ]
    else:
        lines += [
            "> 시나리오 데이터 없음 (옵션 체인 미수신으로 계산 불가)",
            "",
        ]

    # 종합 근거
    lines += [
        "### 5-13. 종합 근거",
        "",
        f"> {r.rationale}",
        "",
    ]

    return lines


def _calc_fundamental_score(fvd: "FinvizDetail | None") -> int:
    """펀더멘털 점수 0~100 계산 (fvd 필드 기반)"""
    if not fvd:
        return 50
    score = 50
    pe = fvd.trailing_pe or fvd.forward_pe
    if pe:
        if pe > 200:    score -= 20
        elif pe > 100:  score -= 15
        elif pe > 50:   score -= 10
        elif pe > 20:   score += 5
        elif pe > 0:    score += 10
    rev_g = fvd.revenue_growth_yoy
    if rev_g is not None:
        if rev_g > 20:    score += 15
        elif rev_g > 5:   score += 8
        elif rev_g < -10: score -= 15
        elif rev_g < 0:   score -= 8
    op_m = fvd.op_margin_pct
    if op_m is not None:
        if op_m > 25:  score += 10
        elif op_m > 10: score += 5
        elif op_m < 0:  score -= 15
        elif op_m < 5:  score -= 5
    recom = fvd.recom
    if recom is not None:
        if recom <= 1.5:    score += 10
        elif recom <= 2.5:  score += 5
        elif recom >= 4.0:  score -= 10
        elif recom >= 3.5:  score -= 5
    return max(0, min(100, score))


def _calc_sentiment_score(sent: "dict | None") -> int:
    """센티멘트 점수 0~100 계산 (LLM 결과 기반)"""
    if not sent:
        return 50
    base   = {"POSITIVE": 75, "MIXED": 50, "NEGATIVE": 25}.get(
        sent.get("overall_sentiment", "MIXED"), 50
    )
    conf_d = {"High": 10, "Medium": 0, "Low": -10}.get(sent.get("confidence", "Medium"), 0)
    str_d  = {"Strong": 5, "Moderate": 0, "Weak": -5}.get(sent.get("sentiment_strength", "Moderate"), 0)
    return max(0, min(100, base + conf_d + str_d))


def _format_sell_position_block(
    d: SellDecision,
    pos: Position | None,
    ts: TechnicalScore | None,
    sc: Scenario | None,
    sent: dict | None,
    h: dict,
    thesis: dict | None = None,
    devils: dict | None = None,
    regime_flag: str = "",
    fvd: FinvizDetail | None = None,
    k_score_entry: float | None = None,
    regime_infer: dict | None = None,
) -> list[str]:
    """TYPE S1~S7 매도 포지션 보고서 블록 (모범 결과.md 기준 구조화)"""

    # ── 공통 변수 준비 ────────────────────────────────────────────
    current_premium: float | None = h.get("current_premium")
    current_price: float = h.get("current_price", pos.entry_stock_price if pos else 0.0)
    greeks_dict: dict = h.get("greeks", {})
    delta = greeks_dict.get("delta", 0.0)
    theta = greeks_dict.get("theta", 0.0)   # 음수: 일일 감소
    vega  = greeks_dict.get("vega", 0.0)
    iv_used = h.get("iv_used", 0.0)

    entry_premium = pos.entry_premium if pos else 0.0
    remaining = pos.remaining_contracts if pos else 1
    dte_val = pos.dte if pos else 999

    # P&L 계산
    # current_premium이 None이면 % 계산 불가 → 표시 구분
    if pos and current_premium is not None:
        pnl_pct      = (current_premium - entry_premium) / entry_premium * 100 if entry_premium else 0.0
        total_pnl    = (current_premium - entry_premium) * 100 * remaining
        pnl_pct_str  = f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.1f}%"
    else:
        pnl_pct      = None   # 조회 불가 — 표시에서 구분
        total_pnl    = d.unrealized_pnl if d.action != "FULL_EXIT" else d.realized_pnl
        pnl_pct_str  = "N/A%"

    pnl_sign = "+" if (pnl_pct or 0) >= 0 else ""

    # 아이콘 매핑
    urgency_icon  = {"critical": "🔴", "warning": "🟡", "normal": "🟠", "stable": "🟢"}.get(d.urgency, "⚪")
    urgency_label = {"critical": "CRITICAL", "warning": "WARNING", "normal": "NORMAL", "stable": "STABLE"}.get(d.urgency, d.urgency.upper())
    action_icon   = {"HOLD": "✋", "PARTIAL_EXIT": "⚡", "FULL_EXIT": "🚨", "ROLL": "🔄"}.get(d.action, "")
    confidence_pct = _calc_confidence_pct(ts)

    # 문자열 포맷
    entry_premium_str  = f"${entry_premium:.2f}"  if pos else "N/A"
    current_premium_str = f"${current_premium:.2f}" if current_premium is not None else "N/A"
    current_price_str  = f"${current_price:.2f}"  if current_price else "N/A"
    entry_stock_str    = f"${pos.entry_stock_price:.2f}" if pos else "N/A"
    entry_date_str     = str(pos.entry_date)  if pos else "N/A"
    dte_str            = f"{dte_val}일"
    option_type        = pos.option_type if pos else "N/A"
    strike_str         = f"${pos.strike:.0f}" if pos else "N/A"
    expiry_str         = str(pos.expiry)  if pos else "N/A"
    peak_str           = f"${pos.peak_premium:.2f}" if (pos and pos.peak_premium > 0) else "미추적"
    trail_str          = f"${pos.trailing_stop:.2f}" if (pos and pos.trailing_stop > 0) else "미설정"
    # entry_regime 우선, 없으면 entry_rationale 첫 줄로 대체
    _er = (pos.entry_regime if pos else "") or ""
    if not _er and pos and pos.entry_rationale:
        _er = pos.entry_rationale.strip().splitlines()[0].strip()
    entry_regime_str = _er or "—"
    delta_pnl  = h.get("delta_pnl",  0.0)
    theta_pnl  = h.get("theta_pnl",  0.0)
    vega_pnl   = h.get("vega_pnl",   0.0)

    lines: list[str] = ["---", ""]

    # ── STATUS BAR ───────────────────────────────────────────────
    lines += [
        "```",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"[{d.ticker}]  {option_type}  {strike_str} / {expiry_str}         DTE: {dte_str}",
        f"진입 {entry_premium_str}  →  현재 {current_premium_str}    ({pnl_pct_str}  ${total_pnl:+,.0f})",
        f"결정: {action_icon} {d.action:<14}  긴급도: {urgency_icon} {urgency_label}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "```",
        "",
    ]

    # ── TYPE S1: 포지션 현황 ─────────────────────────────────────
    # K-Score 문자열 준비
    _k_score_str = f"{k_score_entry:.1f} / 9" if k_score_entry is not None else "—"

    lines += [
        f"### 📊 TYPE S1 — 포지션 현황  [{d.ticker}]",
        "",
        "| 항목 | 진입 | 현재 |",
        "|------|------|------|",
        f"| 주가 | {entry_stock_str} | {current_price_str} |",
        f"| 옵션 프리미엄 | {entry_premium_str} | {current_premium_str} |",
        f"| P&L | — | **{pnl_pct_str}  (${total_pnl:+,.0f})** |",
        f"| DTE | — | {dte_str} |",
        f"| 프리미엄 고점 | — | {peak_str} |",
        f"| 트레일링 스탑 | — | {trail_str} |",
        f"| 진입일 | {entry_date_str} | — |",
        f"| 레짐 (진입 시) | {entry_regime_str} | — |",
        f"| K-Score (진입 시) | {_k_score_str} | — |",
        "",
        "**Greeks 현황**",
        "",
        "| δ Delta | θ Theta/일 | ν Vega | IV 사용값 |",
        "|---------|-----------|--------|-----------|",
        f"| {delta:.3f} | ${theta:.2f} | {vega:.3f} | {iv_used * 100:.1f}% |",
        "",
        "**P&L 귀인 분석**",
        "",
    ]
    # 귀인 데이터가 실제로 있는지 확인 (모두 0이면 health 조회 실패로 간주)
    _has_attribution = any(abs(v) > 0.01 for v in (delta_pnl, theta_pnl, vega_pnl))
    _attribution_sum = delta_pnl + theta_pnl + vega_pnl
    _bs_estimate = h.get("premium_source") == "bs_estimate"
    if _has_attribution:
        lines += [
            "```",
            f"Delta 기여:  ${delta_pnl:+,.0f}   (주가 이동 효과)",
            f"Theta 비용:  ${theta_pnl:+,.0f}   (시간 가치 소멸)",
            f"Vega 기여:   ${vega_pnl:+,.0f}   (변동성 변화 효과)",
            f"──────────────────────────────────",
            f"합계:        ${_attribution_sum:+,.0f}",
            "```",
        ]
        if _bs_estimate:
            lines += [
                "> ⚠️ **BS 추정 기반**: 옵션 체인에서 해당 행사가/만기 데이터 미조회 → Black-Scholes 이론가 사용. 귀인 수치는 참고용 추정값이며 실제 청산 손익과 차이 있을 수 있음.",
            ]
    else:
        lines += ["> 귀인 데이터 없음 (현재 프리미엄 조회 실패 — 수동 확인 필요)"]
    lines += [
        "",
    ]

    # Theta 소멸 경고: S4-C(DTE≤14)로 커버 안 되는 구간 포함
    if pos and current_premium is not None and dte_val > 0:
        remaining_theta_cost = abs(theta) * dte_val * 100 * remaining
        if dte_val <= 21 and remaining_theta_cost > abs(total_pnl) + 0.01:
            lines += [
                f"> ⚠️ **Theta 소멸 경고**: 잔여 {dte_val}일 × θ${abs(theta):.2f}/일 × {remaining}계약 = 예상 소멸 **${remaining_theta_cost:,.0f}** > 현재 손익 ${total_pnl:+,.0f}",
                "",
            ]

    # ── TYPE S2: Thesis 생존 점검 ────────────────────────────────
    lines += [f"### 📋 TYPE S2 — Thesis 생존 점검  [{d.ticker}]", ""]

    if pos and pos.thesis:
        lines += [f"> **진입 논거**: {pos.thesis}", ""]

    # 무효화 조건 체크 (LLM thesis 우선, 없으면 pos.invalidation_conditions)
    condition_checks: list[dict] = (thesis or {}).get("condition_checks", [])
    if condition_checks:
        lines += ["**무효화 조건 체크**", ""]
        for cc in condition_checks:
            cond   = cc.get("condition", "")
            status = cc.get("status", "")
            icon   = {"유지": "✅", "약화": "⚠️", "무효": "❌"}.get(status, "❓")
            lines.append(f"- {icon} **{status}** — {cond}")
        lines.append("")
    elif pos and pos.invalidation_conditions:
        lines += ["**무효화 조건 (진입 시 설정)**", ""]
        for cond in pos.invalidation_conditions:
            lines.append(f"- ❓ {cond}")
        lines.append("")

    # 레짐 비교 — entry_regime이 없으면 판정 불가
    if regime_flag == "REGIME_REVERSED":
        regime_icon = "❌ 역전"
        current_regime_str = "Bearish / Unfavorable"
    elif entry_regime_str == "—":
        # 진입 시 레짐 데이터 없음 → 판정 불가 (None==None을 ✅로 오판 방지)
        regime_icon = "⚪ 데이터 없음"
        current_regime_str = "—"
    else:
        regime_icon = "✅ 일치"
        current_regime_str = entry_regime_str

    lines += [
        f"**레짐**: 진입 시 `{entry_regime_str}` ↔ 현재 `{current_regime_str}` {regime_icon}",
        "",
    ]

    # LLM 레짐 추론 결과 (entry_regime이 없어서 thesis에서 추론된 경우)
    if regime_infer:
        _inferred   = regime_infer.get("inferred_entry_regime", "")
        _inf_basis  = regime_infer.get("inference_basis", "")
        _validity   = regime_infer.get("thesis_validity", "")
        _val_reason = regime_infer.get("validity_reason", "")
        _rec        = regime_infer.get("recommendation", "")
        _premises   = regime_infer.get("key_premise_check", [])

        _val_icon = {"valid": "✅", "partially_valid": "⚠️", "invalid": "❌"}.get(_validity, "❓")
        _rec_icon = {"보유근거_유지": "✅", "부분청산_고려": "⚠️", "전량청산_고려": "🚨"}.get(_rec, "❓")

        lines += [
            "**📊 LLM 레짐 추론 분석** *(entry_regime 미기재 → thesis에서 추론)*",
            "",
            f"- 추론 진입 레짐: `{_inferred}`",
        ]
        if _inf_basis:
            lines.append(f"- 추론 근거: {_inf_basis[:200]}")
        lines += [
            f"- Thesis 유효성: {_val_icon} `{_validity}`",
        ]
        if _val_reason:
            lines.append(f"- 유효성 근거: {_val_reason[:200]}")
        if _premises:
            lines += ["", "**핵심 전제 점검**", ""]
            for _p in _premises[:5]:
                _prem_text = _p.get("premise", "")[:100]
                _prem_stat = _p.get("status", "")
                _prem_icon = {"유효": "✅", "무효": "❌", "불확실": "⚠️"}.get(_prem_stat, "❓")
                lines.append(f"- {_prem_icon} **{_prem_stat}** — {_prem_text}")
        if _rec:
            lines += ["", f"- LLM 권고: {_rec_icon} `{_rec}`"]
        lines.append("")

    # 뉴스 감성
    if sent:
        overall = sent.get("overall_sentiment", "MIXED")
        bull    = sent.get("bull_thesis", "")
        bear    = sent.get("bear_thesis", "")
        conf    = sent.get("confidence", "")
        lines  += [f"**뉴스 감성**: {overall}" + (f"  (신뢰도: {conf})" if conf else ""), ""]
        if bull:
            lines.append(f"- 🐂 강세 근거: {_clean_llm_text(str(bull))[:300]}")
        if bear:
            lines.append(f"- 🐻 약세 근거: {_clean_llm_text(str(bear))[:300]}")
        lines.append("")
        # 핵심 정보 가중치
        _kd_weights = sent.get("key_drivers", [])
        if _kd_weights:
            _w_icon = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}
            lines += ["```", "핵심 정보 가중치:"]
            for _kw in _kd_weights[:5]:
                _src = _kw.get("source", "")
                _w   = _kw.get("weight_pct", 0)
                _wi  = _w_icon.get(_kw.get("direction", ""), "⚪")
                lines.append(f"  {_wi} {_src} — {_w}%")
            lines += ["```", ""]

    # 이벤트 리스크 (devils)
    if devils:
        ev_judge = devils.get("event_judgment", "")
        iv_crush = devils.get("iv_crush_risk", False)
        iv_loss  = devils.get("iv_crush_estimated_loss", 0.0)
        ev_judge_clean = (ev_judge or "").strip().strip(":").strip()
        if ev_judge_clean and len(ev_judge_clean) > 3:
            ev_icon = "🚨" if "청산_유리" in ev_judge_clean else "✅" if "보유_유리" in ev_judge_clean else "⚠️"
            lines.append(f"**이벤트 리스크**: {ev_icon} {ev_judge_clean}")
        if iv_crush:
            lines.append(f"**IV Crush 위험**: ⚠️ 예상 손실 ${iv_loss:,.0f}")
        lines.append("")

    # DA / health 플래그
    health_flags = h.get("flags", [])
    if health_flags:
        flag_icon = "🚨" if "청산_권고_신호" in health_flags else "⚠️" if "주의_신호" in health_flags else "✅"
        lines += [f"**진단 플래그**: {flag_icon} `{'` · `'.join(health_flags)}`", ""]

    # ── TYPE S3: 기술 현황 ──────────────────────────────────────
    lines += [f"### 📈 TYPE S3 — 기술 현황  [{d.ticker}]", ""]

    if ts:
        trend_icon   = "✅" if ts.trend_confirmed   else "⚠️"
        capital_icon = "✅" if ts.capital_flow_confirmed else "❌"
        lines += [
            "```",
            f"기술 점수  : {ts.final_score:.0f}/100    신호: {ts.signal_count}/8    신뢰도: {confidence_pct}%",
            "",
            f"추세 상태  : {trend_icon} {'유지' if ts.trend_confirmed else '약화/붕괴'}",
            f"MA 배열    : {ts.ma_alignment}",
            f"자금 유입  : {capital_icon} {'확인' if ts.capital_flow_confirmed else '미확인'}",
            "",
            f"ADX 점수   : {ts.adx_score:.0f}/25   RSI 점수: {ts.rsi_score:.0f}/25",
            f"MACD 점수  : {ts.macd_score:.0f}/25   RVOL 점수: {ts.rvol_score:.0f}/25",
            f"Raw 합계   : {ts.raw_score:.0f}/100   최종: {ts.final_score:.0f}/100",
            "```",
            "",
        ]
    else:
        lines += ["> 기술 점수 데이터 없음", ""]

    # 지지/저항 레벨 (from finviz_detail)
    if fvd:
        s1  = getattr(fvd, "pivot_s1",   None)
        s2  = getattr(fvd, "pivot_s2",   None)
        r1  = getattr(fvd, "pivot_r1",   None)
        r2  = getattr(fvd, "pivot_r2",   None)
        ma200 = getattr(fvd, "sma200_val", None)

        def _p(v: float | None) -> str:
            return f"${v:.2f}" if v else "—"

        lines += [
            "**핵심 가격 레벨**",
            "",
            f"| S2 지지 | S1 지지 | **현재가** | R1 저항 | R2 저항 | 추세 붕괴선(MA200) |",
            f"|---------|---------|-----------|---------|---------|------------------|",
            f"| {_p(s2)} | {_p(s1)} | **{current_price_str}** | {_p(r1)} | {_p(r2)} | {_p(ma200)} |",
            "",
        ]

    # ── TYPE S4: 펀더멘털 & 시장 감정 ──────────────────────────────
    lines += [f"### 📊 TYPE S4 — 펀더멘털 & 시장 감정  [{d.ticker}]", ""]

    # 핵심 동인 (Key Drivers)
    _kd_list = sent.get("key_drivers", []) if sent else []
    if _kd_list:
        _dir_icons = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}
        lines += ["**핵심 동인 (Key Drivers)**", ""]
        for _i, _kd in enumerate(_kd_list[:4], 1):
            _icon  = _dir_icons.get(_kd.get("direction", ""), "⚪")
            _src   = str(_kd.get("source", ""))[:60]
            _desc  = _clean_llm_text(str(_kd.get("description", "")))[:250]
            _w_pct = _kd.get("weight_pct", "")
            _w_str = f" ({_w_pct}%)" if _w_pct else ""
            lines.append(f"{_i}. {_icon} **{_src}**{_w_str} — {_desc}")
        lines.append("")

    # 차원별 점수
    _f_score  = _calc_fundamental_score(fvd)
    _t_score  = round(ts.final_score) if ts else 50
    _s_score  = _calc_sentiment_score(sent)
    _weighted = round(_f_score * 0.4 + _t_score * 0.3 + _s_score * 0.3)
    _f_label  = "부정적" if _f_score < 50  else "긍정적"
    _t_label  = "부정적" if _t_score < 50  else "긍정적"
    _s_label  = "부정적" if _s_score < 40  else ("중립" if _s_score < 65 else "긍정적")
    _total_label = "SELL 신호" if _weighted < 40 else ("HOLD 신호" if _weighted < 65 else "BUY 신호")
    lines += [
        "**차원별 점수 요약**", "",
        "```",
        f"기본적 분석:   {_f_score}/100 — {_f_label}  (비중 40%)",
        f"기술적 분석:   {_t_score}/100 — {_t_label}  (비중 30%)",
        f"센티멘트 분석: {_s_score}/100 — {_s_label}  (비중 30%)",
        f"─────────────────────────────────────────────────",
        f"종합 점수:     {_weighted}/100  →  {_total_label}",
        "```", "",
    ]

    # 펀더멘털 지표 + 시장 포지셔닝
    if fvd:
        _pe      = fvd.trailing_pe or fvd.forward_pe
        _pe_str  = f"{_pe:.1f}배" if _pe else "N/A"
        _pe_comm = ("(극단적 고평가)" if _pe and _pe > 200
                    else "(매우 고평가)" if _pe and _pe > 100
                    else "(고평가)"      if _pe and _pe > 50
                    else "(적정)"        if _pe else "")
        _rev_g   = fvd.revenue_growth_yoy
        _rev_str = f"{_rev_g:+.1f}%" if _rev_g is not None else "N/A"
        _op_m    = fvd.op_margin_pct
        _op_str  = f"{_op_m:.1f}%" if _op_m is not None else "N/A"
        lines += [
            "**펀더멘털 지표**", "",
            "```",
            f"P/E (TTM):    {_pe_str}  {_pe_comm}",
            f"매출 성장률:  {_rev_str}  (YoY)",
            f"영업이익률:   {_op_str}",
            "```", "",
        ]
        _buy_n  = fvd.analyst_buy  or 0
        _hold_n = fvd.analyst_hold or 0
        _sell_n = fvd.analyst_sell or 0
        _tp     = fvd.target_price
        _total_a = _buy_n + _hold_n + _sell_n
        # 애널리스트 데이터가 없으면(0명) 목표가만 단독 표시하지 않음
        if _total_a > 0 or _tp:
            _tp_gap_str = ""
            _tp_warn    = ""
            if _tp and current_price:
                _gap_pct = (_tp - current_price) / current_price * 100
                _tp_gap_str = f"  ({_gap_pct:+.1f}% 여력)"
                if _gap_pct < -25:
                    _tp_warn = "  ⚠️ 목표가 데이터 이상 (스테일/불일치 가능)"
            _tp_line = (f"컨센서스 목표가: ${_tp:.2f}{_tp_gap_str}{_tp_warn}" if _tp
                        else "컨센서스 목표가: N/A")
            _ana_line = (f"애널리스트:  Buy {_buy_n} / Hold {_hold_n} / Sell {_sell_n}  (총 {_total_a}명)"
                         if _total_a > 0 else "애널리스트:  데이터 없음")
            lines += [
                "**시장 포지셔닝**", "",
                "```",
                _ana_line,
                _tp_line,
                "```", "",
            ]

    # ── TYPE S5: 가격 시나리오 & 촉매 ───────────────────────────────
    lines += [f"### 🗓️ TYPE S5 — 가격 시나리오 & 촉매  [{d.ticker}]", ""]

    if sent:
        _critical_events = sent.get("critical_events", [])
        _lasting         = sent.get("lasting_impacts",  "")
        _fading          = sent.get("fading_impacts",   "")
        _catalyst        = sent.get("catalyst",         "")
        _next_days       = sent.get("next_catalyst_days", 0)

        # 주요 이벤트 시나리오
        if _critical_events:
            lines += ["**주요 이벤트 시나리오**", ""]
            _imp_icon = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}
            _dir_icon = {"positive": "📈", "negative": "📉", "neutral": "➡️"}
            for _ev in _critical_events[:3]:
                _ev_name   = _ev.get("event", "")
                _ev_impact = _ev.get("impact", "")
                _ev_dir    = _ev.get("direction", "")
                _short_eff = _clean_llm_text(str(_ev.get("short_term_effect",     "")))[:200]
                _long_impl = _clean_llm_text(str(_ev.get("long_term_implication", "")))[:200]
                lines.append(
                    f"**{_imp_icon.get(_ev_impact, '⚪')} {_ev_name}**"
                    f" {_dir_icon.get(_ev_dir, '')}"
                )
                if _short_eff:
                    lines.append(f"- 단기 효과 (1~4주): {_short_eff}")
                if _long_impl:
                    lines.append(f"- 장기 함의 (6개월+): {_long_impl}")
                lines.append("")

        # 리스크 지속 기간 분류
        def _fmt_impact_list(val) -> list[str]:
            """str 또는 list → 불릿 라인 리스트"""
            if not val:
                return []
            if isinstance(val, list):
                return [f"  - {_clean_llm_text(str(item))[:200]}" for item in val if item]
            return [f"  - {_clean_llm_text(str(val))[:250]}"]

        if _fading or _lasting:
            lines += ["**리스크 지속 기간 분류**", ""]
            if _fading:
                lines += ["🟡 **즉각적 리스크 (30~90일 희석)**:"]
                lines += _fmt_impact_list(_fading)
                lines.append("")
            if _lasting:
                lines += ["🔴 **중기 구조적 리스크 (6개월+)**:"]
                lines += _fmt_impact_list(_lasting)
                lines.append("")

        # 향후 촉매
        if _next_days or _catalyst:
            lines += ["**향후 촉매**", ""]
            if _next_days and int(_next_days) > 0:
                lines.append(f"- 다음 주요 이벤트: 약 **{int(_next_days)}일 후**")
            if _catalyst:
                lines.append(f"- 핵심 촉매: {str(_catalyst)[:150]}")
            lines.append("")
    else:
        lines += ["> 감성 분석 데이터 없음 — 촉매 정보 수동 확인 필요", ""]

    # ── TYPE S6: 상황별 Appendix (조건부) ────────────────────────
    # S6-A: 수익 +50% 이상
    if pnl_pct >= 50.0:
        t1_prem = sc.target_premium_1st          if sc else entry_premium * 1.5
        t2_prem = sc.target_premium_2nd          if (sc and sc.target_premium_2nd) else entry_premium * 2.0
        t3_prem = sc.target_premium_3rd          if (sc and sc.target_premium_3rd) else entry_premium * 2.5
        stop_prem = sc.stop_loss_premium         if sc else entry_premium * 0.5
        trail_pct = sc.trailing_stop_pct         if sc else 20.0

        t1_done = current_premium is not None and current_premium >= t1_prem
        t2_done = current_premium is not None and current_premium >= t2_prem
        stop_hit = current_premium is not None and current_premium <= stop_prem
        trail_gap_str = (
            f"${current_premium - pos.trailing_stop:.2f} 여유"
            if (pos and current_premium and pos.trailing_stop > 0)
            else "트레일링 스탑 미설정"
        )

        lines += [
            f"### 🟢 TYPE S6-A — 익절 전략 가이드  (현재: {pnl_sign}{pnl_pct:.1f}%)",
            "",
            "| 레벨 | 프리미엄 | 상태 | 행동 |",
            "|------|---------|------|------|",
            f"| 손절 | ${stop_prem:.2f} | {'🚨 이탈' if stop_hit else '✅ 상방'} | FULL EXIT |",
            f"| T1 (+50%) | ${t1_prem:.2f} | {'✅ 달성' if t1_done else '🔄 진행 중'} | 50% 부분 익절 |",
            f"| T2 (+100%) | ${t2_prem:.2f} | {'✅ 달성' if t2_done else '🔄 진행 중'} | 잔여 50% 익절 |",
            f"| T3 (+150%) | ${t3_prem:.2f} | {'🎯 목표' if not t2_done else '🔄 홀딩'} | 트레일링 스탑 |",
            "",
            "```",
            f"트레일링 스탑 기준: 고점 × {(1 - trail_pct/100):.0%}",
            f"고점:   {peak_str}",
            f"스탑:   {trail_str}",
            f"현재:   {current_premium_str}",
            f"갭:     {trail_gap_str}",
            "```",
            "",
        ]

    # S4-B: 손실 -30% 이하
    if pnl_pct <= -30.0 and pos:
        stop_prem = sc.stop_loss_premium if sc else entry_premium * 0.5
        stop_gap_dollar = (
            (current_premium - stop_prem) * 100 * remaining
            if current_premium is not None else 0.0
        )
        cond_invalid  = sum(1 for cc in condition_checks if cc.get("status") == "무효")
        cond_weakened = sum(1 for cc in condition_checks if cc.get("status") == "약화")

        lines += [
            f"### 🔴 TYPE S6-B — 손절 판단 가이드  (현재: {pnl_sign}{pnl_pct:.1f}%)",
            "",
            "```",
            f"하드 스탑:  ${stop_prem:.2f}  (진입 × 0.50)",
            f"현재:       {current_premium_str}",
            f"갭:         ${stop_gap_dollar:+,.0f}  ({'⚠️ 손절 선행 검토' if stop_gap_dollar < 50 else '여유 있음'})",
            "```",
            "",
            f"**Thesis 진단**: ❌ 무효 {cond_invalid}개  ⚠️ 약화 {cond_weakened}개",
            "",
            "| 상황 | 권고 행동 |",
            "|------|---------|",
            "| Thesis ❌ 1개 이상 | 손절선 대기 없이 선제 청산 |",
            "| Thesis ⚠️ 만 존재 | 스탑 유지, 추이 관찰 |",
            "| 레짐 역전 동반 | PARTIAL EXIT 75% 즉시 |",
            "",
        ]

    # S4-C: DTE ≤ 14
    if dte_val <= 14 and pos:
        daily_cost    = abs(theta) * 100 * remaining
        remain_decay  = daily_cost * dte_val
        dte_severity  = "🔴 CRITICAL" if dte_val <= 7 else "🟡 WARNING"

        lines += [
            f"### ⏰ TYPE S6-C — 만기 대응 가이드  (DTE: {dte_val}일  {dte_severity})",
            "",
            "```",
            f"잔여 DTE:       {dte_val}일",
            f"Theta/일:       -${daily_cost:.2f}",
            f"잔여 Theta 합계: -${remain_decay:.2f}  (시간 가치 추가 소멸 예상)",
            "```",
            "",
            "| DTE 기준 | 권고 |",
            "|---------|------|",
            f"| ≤ 7일  | FULL EXIT 또는 ROLL만 허용{'  ← **현재 해당**' if dte_val <= 7 else ''} |",
            f"| 8~14일 | ROLL 조건 검토 시작{'  ← **현재 해당**' if 7 < dte_val <= 14 else ''} |",
        ]
        if d.action == "ROLL" and d.roll_expiry and d.roll_strike:
            lines += [
                "",
                f"**Roll 확정**: ${d.roll_strike:.0f} 행사가 / 만기 {d.roll_expiry}  (+35일 연장)",
            ]
        lines.append("")

    # S4-D: 레짐 역전
    if regime_flag == "REGIME_REVERSED":
        lines += [
            f"### 🔄 TYPE S6-D — 방향 충돌 (Regime Conflict)",
            "",
            "```",
            f"진입 시: {entry_regime_str}",
            f"현재:    Bearish / Unfavorable",
            f"포지션:  {option_type}  →  레짐과 방향 충돌",
            "```",
            "",
            "| 권고 | 내용 |",
            "|------|------|",
            "| 즉각 실행 | PARTIAL EXIT 75% — 레짐 역전 헷지 |",
            "| 잔여 25% | 타이트한 스탑 유지, 추이 관찰 |",
            "| 레짐 복귀 시 | 포지션 재평가 후 추가 진입 검토 |",
            "",
        ]

    # ── TYPE S7: 최종 결정 + 실행 계획 ─────────────────────────
    lines += [
        f"### 🎯 TYPE S7 — 최종 결정 + 실행 계획  [{d.ticker}]",
        "",
        "```",
        f"최종 결정:  {action_icon} {d.action}",
        f"확신도:     {confidence_pct}%   (신호 {ts.signal_count if ts else '—'}/8)",
        f"긴급도:     {urgency_icon} {urgency_label}",
        "```",
        "",
        "**기존 보유자 행동 계획**",
        "",
    ]

    # 행동별 즉시 실행 지침
    if d.action == "HOLD":
        lines += [f"✅ 즉시: 트레일링 스탑 **{trail_str}** 유지 확인"]
        if sc:
            _t1 = sc.target_premium_1st
            _t2 = sc.target_premium_2nd
            _t1_hit = current_premium is not None and _t1 and current_premium >= _t1
            _t2_hit = current_premium is not None and _t2 and current_premium >= _t2
            if _t1:
                if _t1_hit:
                    lines.append(f"✅ T1 (${_t1:.2f}) 이미 달성 → 트레일링 스탑 갱신 확인")
                else:
                    lines.append(f"⏳ 조건: T1 (${_t1:.2f}) 도달 → 50% 부분 익절")
            if _t2:
                if _t2_hit:
                    lines.append(f"✅ T2 (${_t2:.2f}) 이미 달성 → 잔여 익절 또는 스탑 타이트닝")
                else:
                    lines.append(f"⏳ 조건: T2 (${_t2:.2f}) 도달 → 잔여 익절")
    elif d.action == "PARTIAL_EXIT":
        contracts_str = f"{d.contracts_to_close}계약"
        prem_str = f"${d.target_premium:.2f}" if d.target_premium else "시장가"
        lines += [
            f"⚡ 즉시: **{contracts_str} 청산**  (목표 프리미엄: {prem_str})",
            f"   잔여 계약 트레일링 스탑 {trail_str} 유지",
        ]
    elif d.action == "FULL_EXIT":
        lines += [
            f"🚨 즉시: **전량 청산** ({remaining}계약 전부)",
            f"   실현 손익 목표: ${d.realized_pnl:+,.0f}",
        ]
    elif d.action == "ROLL":
        new_strike = f"${d.roll_strike:.0f}" if d.roll_strike else "—"
        new_expiry = str(d.roll_expiry) if d.roll_expiry else "—"
        lines += [
            f"🔄 즉시: 현재 포지션 청산 후 Roll",
            f"   새 Strike: {new_strike}  새 만기: {new_expiry}",
        ]

    lines.append("")

    # 즉시 청산 트리거
    lines += ["**🚨 즉시 재분석·청산 트리거 (Key Inflection Points)**", ""]
    triggers: list[str] = []
    if fvd and getattr(fvd, "sma200_val", None):
        triggers.append(f"주가 ${fvd.sma200_val:.2f} 하향 종가 → 추세 붕괴 → FULL EXIT 검토")
    if sc and sc.stop_loss_premium:
        triggers.append(f"프리미엄 ${sc.stop_loss_premium:.2f} 이하 → 스탑 발동 → FULL EXIT")
    if condition_checks:
        triggers.append("Thesis 무효(❌) 조건 2개 이상 → 손절선 대기 없이 선제 청산")
    if dte_val > 7:
        triggers.append(f"DTE 7일 도달 → Roll 또는 EXIT 최종 결정 데드라인")
    triggers.append("레짐 Bearish 전환 → PARTIAL EXIT 75% 즉시")
    triggers.append("뉴스 감성 NEGATIVE 전환 → Thesis 재점검")

    for t in triggers:
        lines.append(f"- {t}")
    lines.append("")

    # P&L 시나리오 표
    lines += ["**P&L 시나리오**", ""]
    if sc and pos:
        stop_pnl = (sc.stop_loss_premium - entry_premium) * 100 * remaining
        t1_pnl   = (sc.target_premium_1st - entry_premium) * 100 * remaining if sc.target_premium_1st else 0.0
        t2_pnl   = (sc.target_premium_2nd - entry_premium) * 100 * remaining if sc.target_premium_2nd else 0.0
        lines += [
            "| 시나리오 | 확률 | 예상 P&L |",
            "|----------|------|----------|",
            f"| 지금 전량 청산 | — | ${total_pnl:+,.0f} |",
            f"| 스탑 발동 | — | ${stop_pnl:+,.0f} |",
            f"| T1 달성 | — | ${t1_pnl:+,.0f} |",
            f"| T2 달성 | — | ${t2_pnl:+,.0f} |",
            f"| 강세 시나리오 | {sc.bullish.probability:.0%} | ${sc.bullish.net_profit:+,.0f} |",
            f"| 기본 시나리오 | {sc.base.probability:.0%} | ${sc.base.net_profit:+,.0f} |",
            f"| 약세 시나리오 | {sc.bearish.probability:.0%} | ${sc.bearish.net_profit:+,.0f} |",
            f"| **기대값 (EV)** | — | **${sc.expected_value:+,.0f}** |",
        ]
    else:
        lines += ["> P&L 시나리오 데이터 없음"]

    lines += [
        "",
        "> **판단 근거**: " + (d.rationale if d.rationale else "근거 데이터 없음"),
        "",
    ]

    # 한 줄 요약
    _one_liner = (sent.get("thesis", "") if sent else "") or d.rationale
    if _one_liner:
        _first = _one_liner.split(".")[0].strip()
        if len(_first) > 10:
            lines += [f"> 💬 **한 줄 요약**: {_first[:180]}.", ""]

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# TYPE SR — 트레이드 복기 섹션 (Step 12 LLM 결과 → append)
# ─────────────────────────────────────────────────────────────────────────────

def _format_sell_review_section(
    ticker: str,
    pos: "Position",
    d: "SellDecision",
    llm_review: dict,
) -> str:
    """
    FULL_EXIT 복기 리포트 (TYPE SR).
    Step 12에서 LLM이 생성한 ``sell_step4_review`` 결과를 구조화된
    Markdown 섹션으로 변환하여 기존 노트에 append한다.

    Returns:
        str — obsidian.append_note()에 그대로 전달할 Markdown 문자열
    """
    from datetime import date as _date

    result_str   = "수익 ✅" if d.realized_pnl >= 0 else "손실 ❌"
    pnl_sign     = "+" if d.realized_pnl >= 0 else ""
    days_held    = ((_date.today() - pos.entry_date).days) if pos.entry_date else 0

    # LLM 필드 추출 (기본값 포함)
    accuracy     = llm_review.get("thesis_accuracy", "—")
    outcome      = llm_review.get("outcome",         "—")
    lesson       = llm_review.get("lesson",          "—")
    what_worked  = llm_review.get("what_worked",     "—")
    what_failed  = llm_review.get("what_failed",     "—")
    pattern      = llm_review.get("pattern",         "—")
    improvement  = llm_review.get("improvement",     "—")

    # 정확도 이모지
    acc_icon = "🟢" if accuracy == "high" else ("🟡" if accuracy == "medium" else "🔴")

    lines: list[str] = [
        "",
        "---",
        "",
        f"## 📚 TYPE SR — 트레이드 복기  [{ticker}]",
        "",
        "```",
        f"티커:       {ticker}",
        f"결과:       {result_str}",
        f"실현 손익:  {pnl_sign}${d.realized_pnl:,.0f}",
        f"보유 기간:  {days_held}일  ({pos.entry_date} → 오늘)",
        f"진입 프리미엄: ${pos.entry_premium:.2f}",
        f"Thesis 정확도: {acc_icon} {accuracy}",
        "```",
        "",
        "### 📋 결과 요약",
        "",
        f"> {outcome}",
        "",
        "### ✅ 잘된 점 (What Worked)",
        "",
        f"{what_worked}",
        "",
        "### ❌ 아쉬운 점 (What Failed)",
        "",
        f"{what_failed}",
        "",
        "### 💡 핵심 교훈 (Lesson)",
        "",
        f"**{lesson}**",
        "",
        "### 🔁 패턴 인식",
        "",
        f"{pattern}",
        "",
        "### 🛠️ 개선 방향 (Improvement)",
        "",
        f"{improvement}",
        "",
        f"*복기 생성: {_date.today().isoformat()}*",
        "",
    ]

    return "\n".join(lines)