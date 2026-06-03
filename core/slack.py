"""
core/slack.py
=============
Slack 알림 통합 모듈 (T3: client·formatters → 1개)

담당:
- send_buy_result(): 매수 분석 결과 전송
- send_sell_result(): 매도 분석 결과 전송
- send_risk_alert(): 리스크 경고 전송
- send_iv_crush_warning(): IV Crush 경고 전송
- send_requeue_alert(): Requeue 알림 전송
- send_fatal_error(): 치명적 오류 긴급 알림
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from slack_sdk.errors import SlackApiError  # type: ignore
from slack_sdk.web.async_client import AsyncWebClient  # type: ignore
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential  # type: ignore

from shared.config import get_config
from shared.logger import get_logger
from shared.schemas import FinalRanking, RequeueItem, SellDecision

log = get_logger()
cfg = get_config()


class SlackClient:
    """
    Slack Bot API 클라이언트.

    채널:
    - SLACK_CHANNEL_MAIN (#swing-trading): 정상 결과
    - SLACK_CHANNEL_ALERT (#swing-alerts): 리스크·오류 알림
    """

    def __init__(self) -> None:
        if not cfg.SLACK_BOT_TOKEN:
            log.warning("slack_token_missing", msg="Slack 알림 비활성화")
            self._client: AsyncWebClient | None = None
        else:
            self._client = AsyncWebClient(token=cfg.SLACK_BOT_TOKEN)

    # ─────────────────────────────────────────────────────────
    # 핵심 전송 메서드
    # ─────────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception(lambda e: isinstance(e, SlackApiError) and e.response.get("error") == "ratelimited"),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=5, min=5, max=30),
        reraise=False,
    )
    async def _send(
        self,
        channel: str,
        text: str,
        blocks: list[dict] | None = None,
    ) -> str:
        """
        Slack 메시지 전송 (재시도 포함)

        Args:
            channel: 채널명 또는 ID
            text: 폴백 텍스트
            blocks: Block Kit 블록 리스트

        Returns:
            메시지 타임스탬프 (ts)
        """
        if not self._client:
            log.info("slack_disabled", channel=channel, text=text[:80])
            return ""

        try:
            payload: dict[str, Any] = {"channel": channel, "text": text}
            if blocks:
                payload["blocks"] = blocks

            resp = await self._client.chat_postMessage(**payload)
            ts = resp.get("ts", "")
            log.info("slack_sent", channel=channel, ts=ts)
            return ts
        except SlackApiError as exc:
            log.error("slack_api_error", error=str(exc))
            raise

    # ─────────────────────────────────────────────────────────
    # 매수 결과
    # ─────────────────────────────────────────────────────────

    async def send_buy_result(
        self,
        rankings: list[FinalRanking],
        regime_status: str,
        execution_id: str,
        obsidian_path: str = "",
        filter_failures: dict | None = None,
        requeue_count: int = 0,
        rankings_aggressive: list[FinalRanking] | None = None,
        high_downside_tickers: list[str] | None = None,
    ) -> str:
        """
        매수 분석 완료 결과 전송

        Args:
            rankings: 최종 순위 리스트
            regime_status: favorable | borderline | unfavorable
            execution_id: 실행 ID
            obsidian_path: 저장된 Obsidian 노트 경로
            filter_failures: 탈락 종목 딕셔너리
            requeue_count: Requeue 등록 수

        Returns:
            Slack 메시지 타임스탬프
        """
        regime_icon = {
            "favorable": "✅",
            "borderline": "⚠️",
            "unfavorable": "❌",
        }.get(regime_status, "❓")

        entered = [r for r in rankings if r.action == "진입"]
        watched = [r for r in rankings if r.action == "관찰"]
        rejected_cnt = len(filter_failures) if filter_failures else 0

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        text = f"📊 매수 분석 완료 | {now_str}"

        blocks: list[dict] = [
            _header_block(f"📊 매수 분석 완료 — {now_str}"),
            _divider(),
            _section_block(
                f"*레짐:* {regime_icon} {regime_status.upper()}\n"
                f"*실행 ID:* `{execution_id[:20]}...`"
            ),
            _divider(),
            _section_block(
                f"*분석 결과*\n"
                f"• 진입: *{len(entered)}개*\n"
                f"• 관찰: {len(watched)}개\n"
                f"• 탈락: {rejected_cnt}개\n"
                f"• Requeue 등록: {requeue_count}개"
            ),
        ]

        # 진입 종목 상세
        if entered:
            blocks.append(_divider())
            blocks.append(_section_block("*✅ 진입 종목*"))
            for r in entered:
                s = r.scenario
                ev_str = f"${s.expected_value:,.0f}" if s else "N/A"
                blocks.append(_section_block(
                    f"*{r.ticker}* — {r.direction.replace('_',' ').upper()}\n"
                    f"행사가: ${r.strike:,.2f} | 만기: {r.expiry}\n"
                    f"확신도: {r.conviction.level} | 기대값: {ev_str}\n"
                    f"_근거: {r.rationale[:100]}_"
                ))

        # 리스크 경고
        risk_msgs = []
        for r in rankings:
            if r.risk_factors:
                for rf in r.risk_factors:
                    risk_msgs.append(f"• [{r.ticker}] {rf}")
        if risk_msgs:
            blocks.append(_divider())
            blocks.append(_section_block("*⚠️ 리스크 요인*\n" + "\n".join(risk_msgs[:5])))

        # 수익성 최우선 TOP3 (aggressive rankings)
        if rankings_aggressive:
            top3 = rankings_aggressive[:3]
            top3_lines = ["📈 *수익성 최우선 TOP3*"]
            for r in top3:
                s = r.scenario
                ev_str = f"${s.expected_value:,.0f}" if s else "N/A"
                top3_lines.append(
                    f"• *{r.ticker}* — {r.direction.replace('_', ' ').upper()}\n"
                    f"  행사가: ${r.strike:,.2f} | 만기: {r.expiry}\n"
                    f"  확신도: {r.conviction.level} | 기대값: {ev_str}"
                )
            blocks.append(_divider())
            blocks.append(_section_block("\n".join(top3_lines)))

        # 일변동 하락 주의 종목
        if high_downside_tickers:
            tickers_str = ", ".join(high_downside_tickers)
            blocks.append(_section_block(f"⚠️ *일변동 하락 주의* : {tickers_str}"))

        if obsidian_path:
            blocks.append(_section_block(f"*Obsidian:* `{obsidian_path}`"))

        return await self._send(cfg.SLACK_CHANNEL_MAIN, text, blocks)

    # ─────────────────────────────────────────────────────────
    # 매도 결과
    # ─────────────────────────────────────────────────────────

    async def send_sell_result(
        self,
        decisions: list[SellDecision],
        execution_id: str,
        obsidian_path: str = "",
    ) -> str:
        """매도 분석 완료 결과 전송"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        text = f"💰 매도 분석 완료 | {now_str}"

        action_counts = {}
        for d in decisions:
            action_counts[d.action] = action_counts.get(d.action, 0) + 1

        total_realized = sum(d.realized_pnl for d in decisions)
        pnl_icon = "📈" if total_realized >= 0 else "📉"

        blocks: list[dict] = [
            _header_block(f"💰 매도 분석 완료 — {now_str}"),
            _divider(),
            _section_block(
                f"*행동 요약*\n"
                + "\n".join(f"• {k}: {v}개" for k, v in action_counts.items())
                + f"\n\n{pnl_icon} *실현 손익 합계: ${total_realized:,.0f}*"
            ),
        ]

        for d in decisions:
            urgency_icon = {"critical": "🔴", "warning": "🟡", "normal": "🟢", "stable": "🔵"}.get(d.urgency, "⚪")
            blocks.append(_section_block(
                f"{urgency_icon} *{d.ticker}* → **{d.action}**\n"
                f"_근거: {d.rationale[:100]}_\n"
                f"실현: ${d.realized_pnl:,.0f}"
            ))

        if obsidian_path:
            blocks.append(_section_block(f"*Obsidian:* `{obsidian_path}`"))

        return await self._send(cfg.SLACK_CHANNEL_MAIN, text, blocks)

    # ─────────────────────────────────────────────────────────
    # 리스크 경고
    # ─────────────────────────────────────────────────────────

    async def send_risk_alert(
        self,
        alert_type: str,
        detail: str,
        ticker: str | None = None,
    ) -> str:
        """
        리스크 경고 알림 전송 (ALERT 채널)

        Args:
            alert_type: 경고 유형 (예: 'SECTOR_CONCENTRATION', 'STOP_TRIGGER')
            detail: 상세 내용
            ticker: 관련 종목
        """
        ticker_str = f"[{ticker}] " if ticker else ""
        text = f"⚠️ 리스크 경고: {ticker_str}{alert_type}"

        blocks = [
            _header_block(f"⚠️ 리스크 경고"),
            _section_block(
                f"*유형:* {alert_type}\n"
                f"*종목:* {ticker or '전체'}\n"
                f"*내용:* {detail}\n"
                f"*시각:* {datetime.now().strftime('%H:%M:%S')}"
            ),
        ]

        return await self._send(cfg.SLACK_CHANNEL_ALERT, text, blocks)

    # ─────────────────────────────────────────────────────────
    # IV Crush 경고
    # ─────────────────────────────────────────────────────────

    async def send_iv_crush_warning(
        self,
        ticker: str,
        detail: str,
        estimated_loss: float = 0.0,
    ) -> str:
        """
        IV Crush 위험 경고 (실적 발표 전 보유 시)

        Args:
            ticker: 종목
            detail: 상세 설명
            estimated_loss: 예상 손실액
        """
        text = f"💥 IV Crush 경고: {ticker}"
        blocks = [
            _header_block(f"💥 IV Crush 경고 — {ticker}"),
            _section_block(
                f"*종목:* {ticker}\n"
                f"*예상 손실:* ${estimated_loss:,.0f}\n"
                f"*내용:* {detail}\n"
                f"*권고:* 실적 발표 전 청산 또는 헤지 검토"
            ),
        ]
        return await self._send(cfg.SLACK_CHANNEL_ALERT, text, blocks)

    # ─────────────────────────────────────────────────────────
    # Requeue 알림
    # ─────────────────────────────────────────────────────────

    async def send_requeue_alert(self, items: list[RequeueItem]) -> str:
        """
        Requeue ready 전환 알림

        Args:
            items: ready 상태로 전환된 RequeueItem 리스트
        """
        if not items:
            return ""

        text = f"🔔 Requeue 알림: {len(items)}개 종목 분석 준비 완료"
        lines = [f"*🔔 Requeue 준비 완료 — {len(items)}개 종목*\n"]
        for item in items:
            lines.append(f"• *{item.ticker}* — 조건 충족 (탈락: {', '.join(item.failed_filters)})")

        blocks = [
            _header_block("🔔 Requeue 알림"),
            _section_block("\n".join(lines)),
        ]
        return await self._send(cfg.SLACK_CHANNEL_MAIN, text, blocks)

    # ─────────────────────────────────────────────────────────
    # 치명적 오류 (FATAL)
    # ─────────────────────────────────────────────────────────

    async def send_fatal_error(
        self,
        execution_id: str,
        error_code: str,
        message: str,
        step: int | None = None,
    ) -> str:
        """
        FATAL 오류 긴급 알림 (E100 계열)

        Args:
            execution_id: 실행 ID
            error_code: 에러 코드 (예: 'E100')
            message: 에러 메시지
            step: 발생 단계
        """
        text = f"🚨 FATAL ERROR [{error_code}]: {message[:80]}"
        step_str = f"Step {step}" if step is not None else "N/A"
        blocks = [
            _header_block(f"🚨 FATAL ERROR — {error_code}"),
            _section_block(
                f"*실행 ID:* `{execution_id[:30]}`\n"
                f"*단계:* {step_str}\n"
                f"*메시지:* {message}\n"
                f"*시각:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"_파이프라인이 중단되었습니다. 즉시 확인이 필요합니다._"
            ),
        ]
        return await self._send(cfg.SLACK_CHANNEL_ALERT, text, blocks)

    async def send_pipeline_start(self, execution_id: str, pipeline_type: str) -> str:
        """파이프라인 시작 알림"""
        text = f"🚀 {pipeline_type.upper()} 파이프라인 시작: {execution_id[:20]}..."
        return await self._send(cfg.SLACK_CHANNEL_MAIN, text)

    async def send_step_degraded(
        self, execution_id: str, step: int, error: str
    ) -> str:
        """Graceful Degradation 알림 (비치명적 오류)"""
        text = f"⚡ Step {step} Degraded: {error[:60]}"
        return await self._send(cfg.SLACK_CHANNEL_ALERT, text)


# ─────────────────────────────────────────────────────────────
# Block Kit 헬퍼
# ─────────────────────────────────────────────────────────────

def _header_block(text: str) -> dict:
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": text[:150], "emoji": True},
    }


def _section_block(text: str) -> dict:
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text[:3000]},
    }


def _divider() -> dict:
    return {"type": "divider"}
