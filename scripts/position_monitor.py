"""
scripts/position_monitor.py
============================
포지션 이상 감지 → 급락 원인 분석 (Phase 4)

별도 프로세스로 실행. 기존 파이프라인과 완전 분리.

사용법:
  python scripts/position_monitor.py               # 기본 30분 간격
  python scripts/position_monitor.py --interval 15 # 간격 분 단위 지정
  python scripts/position_monitor.py --once        # 1회 실행 후 종료

동작:
  1. positions.md에서 보유 포지션 파싱
  2. yfinance로 기초자산(ticker) 현재가 조회
  3. 기준가(entry_stock_price) 대비 RESEARCH_DROP_THRESHOLD(-3.0%) 이하 감지
  4. 당일 이미 처리한 종목은 중복 알림 방지
  5. run_drop_research() → Obsidian 저장 + Slack 알림
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.research_agent import run_drop_research
from core.parsers import parse_positions
from shared.config import get_config
from shared.logger import get_logger

log = get_logger()
cfg = get_config()

# 당일 처리 완료된 알림을 추적 (ticker → 처리 시각)
_alerted_today: dict[str, str] = {}
_alerted_date: date = date.today()


def _reset_if_new_day() -> None:
    global _alerted_today, _alerted_date
    today = date.today()
    if today != _alerted_date:
        _alerted_today = {}
        _alerted_date = today


def _get_current_prices(tickers: list[str]) -> dict[str, float]:
    """yfinance로 현재가 일괄 조회."""
    prices: dict[str, float] = {}
    try:
        import yfinance as yf  # type: ignore
        data = yf.download(
            tickers,
            period="1d",
            interval="1m",
            progress=False,
            auto_adjust=True,
        )
        if data.empty:
            return prices
        # 멀티 티커: Close['NVDA'], 단일 티커: Close
        close = data.get("Close", data)
        if hasattr(close, "columns"):
            for t in tickers:
                try:
                    val = close[t].dropna().iloc[-1]
                    if val > 0:
                        prices[t] = float(val)
                except Exception:
                    pass
        else:
            if len(tickers) == 1:
                val = close.dropna().iloc[-1]
                if val > 0:
                    prices[tickers[0]] = float(val)
    except Exception as exc:
        log.warning("price_fetch_fail", error=str(exc))
    return prices


async def _send_slack_alert(ticker: str, pct_change: float, verdict: str) -> None:
    """Slack 알림 (SLACK_BOT_TOKEN 없으면 스킵)."""
    if not cfg.SLACK_BOT_TOKEN:
        return
    try:
        from core.slack import SlackClient
        slack = SlackClient()
        emoji = "🚨" if verdict == "실질 악재" else "⚠️"
        msg = (
            f"{emoji} *포지션 이상 감지*\n"
            f"종목: `{ticker}` | 등락: `{pct_change:.1f}%`\n"
            f"판단: *{verdict}*\n"
            f"Obsidian swing-procedure/Alert_{ticker}_*.md 참조"
        )
        await slack.send_message(cfg.SLACK_CHANNEL_ALERT, msg)
    except Exception as exc:
        log.debug("slack_alert_fail", error=str(exc))


async def run_once() -> None:
    """포지션 스캔 1회 실행."""
    _reset_if_new_day()

    # 포지션 로드
    positions_file = Path(cfg.POSITIONS_FILE)
    positions = parse_positions(positions_file)
    if not positions:
        log.info("monitor_no_positions")
        print("[모니터] 보유 포지션 없음")
        return

    tickers = list({p.ticker for p in positions})
    print(f"[모니터] 포지션 {len(positions)}개 ({', '.join(tickers)}) 점검 중...")

    # 현재가 조회
    prices = _get_current_prices(tickers)
    if not prices:
        log.warning("monitor_price_empty")
        print("[모니터] 현재가 조회 실패")
        return

    # 급락 감지
    alerts: list[tuple[str, float, float]] = []  # (ticker, entry_price, pct_change)
    for pos in positions:
        t = pos.ticker
        if t not in prices:
            continue
        current = prices[t]
        entry = pos.entry_stock_price
        if entry <= 0:
            continue
        pct = (current - entry) / entry * 100.0

        if pct <= cfg.RESEARCH_DROP_THRESHOLD:
            # 당일 중복 알림 방지
            if t in _alerted_today:
                log.debug("monitor_already_alerted", ticker=t)
                continue
            alerts.append((t, entry, pct))
            print(f"  [!] {t}: {pct:.1f}% (기준가 ${entry:.2f} → 현재 ${current:.2f})")

    if not alerts:
        now_str = datetime.now().strftime("%H:%M")
        print(f"[모니터] {now_str} — 이상 없음 (기준: {cfg.RESEARCH_DROP_THRESHOLD}%)")
        return

    # 급락 원인 분석 (순차 실행)
    for ticker, entry_price, pct_change in alerts:
        print(f"\n[분석 시작] {ticker} {pct_change:.1f}%")
        try:
            report = await run_drop_research(ticker, pct_change, entry_price)

            # 판단 추출
            if "실질 악재" in report:
                verdict = "실질 악재"
            elif "정상 조정" in report:
                verdict = "정상 조정"
            else:
                verdict = "판단 불가"

            print(f"[결과] {ticker}: {verdict}")
            _alerted_today[ticker] = datetime.now().isoformat()

            # Slack 알림
            await _send_slack_alert(ticker, pct_change, verdict)

        except Exception as exc:
            log.error("drop_research_error", ticker=ticker, error=str(exc))
            print(f"[오류] {ticker} 분석 실패: {exc}")


async def main(interval_min: int, run_once_flag: bool) -> None:
    print(f"=== 포지션 모니터 시작 (간격: {interval_min}분, 임계값: {cfg.RESEARCH_DROP_THRESHOLD}%) ===")
    print(f"    positions.md: {cfg.POSITIONS_FILE}")
    print("    Ctrl+C로 종료\n")

    if run_once_flag:
        await run_once()
        return

    while True:
        try:
            await run_once()
        except KeyboardInterrupt:
            print("\n[모니터] 종료")
            break
        except Exception as exc:
            log.error("monitor_loop_error", error=str(exc))
            print(f"[오류] 루프 에러: {exc}")

        print(f"\n  다음 점검: {interval_min}분 후")
        await asyncio.sleep(interval_min * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="포지션 이상 감지 모니터")
    parser.add_argument("--interval", type=int, default=30, help="점검 간격 (분, 기본 30)")
    parser.add_argument("--once", action="store_true", help="1회 실행 후 종료")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.interval, args.once))
    except KeyboardInterrupt:
        print("\n[모니터] 종료")
