"""
scripts/run_research.py
=======================
On-demand 심층 리서치 CLI

사용법:
  python scripts/run_research.py NVDA
  python scripts/run_research.py NVDA TSLA MSFT
  python scripts/run_research.py NVDA --hint "기술점수 82, high conviction"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.research_agent import run_research
from shared.logger import get_logger

log = get_logger()


async def main(tickers: list[str], hint: str) -> None:
    for ticker in tickers:
        ticker = ticker.upper()
        print(f"\n{'='*60}")
        print(f" Research: {ticker}")
        print(f"{'='*60}")
        try:
            report = await run_research(ticker, context_hint=hint)
            # 보고서 첫 30줄만 콘솔 출력 (Obsidian에 전문 저장됨)
            preview = "\n".join(report.splitlines()[:30])
            print(preview)
            if len(report.splitlines()) > 30:
                print(f"\n... (총 {len(report.splitlines())}줄 — Obsidian Research/{ticker}_*.md 참조)")
        except Exception as exc:
            print(f"[오류] {ticker}: {exc}")
            log.error("run_research_failed", ticker=ticker, error=str(exc))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="On-demand 심층 리서치")
    parser.add_argument("tickers", nargs="+", help="종목 심볼 (예: NVDA TSLA)")
    parser.add_argument("--hint", default="", help="파이프라인 컨텍스트 힌트 (선택)")
    args = parser.parse_args()

    asyncio.run(main(args.tickers, args.hint))
