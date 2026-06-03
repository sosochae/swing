"""
scripts/run_requeue.py
======================
Requeue 파이프라인 독립 실행 스크립트

requeue.json에서 status='ready' 항목을 찾아 Buy Pipeline을 재실행합니다.

흐름:
  Step 0: Obsidian 연결 확인
  Step 1: 최신 summary 데이터 로드
  Step 2: ready 종목 확인 (waiting → 모멘텀 조건 충족 → ready)
  Step 3: ready 종목 → Buy Pipeline 재실행
  Step 4: Slack 알림 (처리된 종목 수)

사용법:
    cd C:\\MCP\\Swing
    .venv\\Scripts\\python scripts\\run_requeue.py

    # requeue.json에 항목 추가:
    .venv\\Scripts\\python scripts\\run_requeue.py --add NVDA --reason "눌림목 회복 대기"
    .venv\\Scripts\\python scripts\\run_requeue.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# ── 프로젝트 루트를 sys.path에 추가 ──────────────────────────
_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))


# ─────────────────────────────────────────────────────────────
# 메인 실행 (Requeue Pipeline)
# ─────────────────────────────────────────────────────────────

async def run_pipeline(use_cache: bool = False) -> None:
    from core.obsidian import ObsidianClient
    from core.slack import SlackClient
    from orchestrator.engine import PipelineEngine
    from shared.config import get_config
    from shared.logger import setup_logging

    cfg = get_config()
    eid = f"requeue_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    setup_logging(eid)

    cache_mode = "캐시 사용" if use_cache else "캐시 무시 (새로 실행)"
    print(f"\n{'='*60}")
    print(f"  SwingMCP Requeue Pipeline  [{eid}]")
    print(f"  캐시 모드: {cache_mode}")
    print(f"{'='*60}")

    # requeue.json 현재 상태 미리 출력
    requeue_file = Path(cfg.REQUEUE_FILE)
    if requeue_file.exists():
        try:
            items = json.loads(requeue_file.read_text(encoding="utf-8"))
            waiting = [i for i in items if i.get("status") == "waiting"]
            ready = [i for i in items if i.get("status") == "ready"]
            print(f"\n  requeue.json 현황: waiting={len(waiting)}개  ready={len(ready)}개")
            for item in items:
                print(f"    {item.get('status','?'):>8}  {item.get('ticker','?'):<8}  {item.get('reason','')}")
        except Exception as exc:
            print(f"  △ requeue.json 읽기 실패: {exc}")
    else:
        print(f"\n  requeue.json 없음 — 새로 생성됩니다.")
        print(f"  경로: {requeue_file}")

    print()

    engine = PipelineEngine(
        obsidian=ObsidianClient(),
        slack=SlackClient(),
    )

    try:
        result = await engine.run_requeue(execution_id=eid, force_refresh=not use_cache)
        print(f"\n  완료 단계: {result.completed_steps}")
        print(f"  실패 단계: {result.failed_steps}")
        print(f"  소요시간: {result.duration_seconds:.1f}초" if hasattr(result, "duration_seconds") else "")
    except Exception as exc:
        import traceback
        print(f"\n  ✗ Requeue Pipeline 실패: {exc}")
        print(traceback.format_exc())
        return

    print(f"\n{'='*60}")
    print(f"  Requeue Pipeline 완료  [{eid}]")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────
# Requeue 항목 추가
# ─────────────────────────────────────────────────────────────

def add_ticker(ticker: str, reason: str) -> None:
    from shared.config import get_config
    from core.state import requeue_add

    cfg = get_config()
    ticker = ticker.upper().strip()

    try:
        asyncio.run(_async_add(ticker, reason))
        print(f"  ✓ {ticker} → requeue 등록 완료 (reason: {reason})")
    except Exception as exc:
        print(f"  ✗ requeue 등록 실패: {exc}")


async def _async_add(ticker: str, reason: str) -> None:
    from core.state import requeue_add
    requeue_add(ticker=ticker, reason=reason)


# ─────────────────────────────────────────────────────────────
# Requeue 목록 조회
# ─────────────────────────────────────────────────────────────

def list_items() -> None:
    from shared.config import get_config

    cfg = get_config()
    requeue_file = Path(cfg.REQUEUE_FILE)

    if not requeue_file.exists():
        print("  requeue.json 없음 (항목 없음)")
        return

    try:
        items = json.loads(requeue_file.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  ✗ requeue.json 읽기 실패: {exc}")
        return

    if not items:
        print("  requeue 목록 비어있음")
        return

    print(f"\n{'='*50}")
    print(f"  Requeue 목록 ({len(items)}개)")
    print(f"{'='*50}")
    print(f"  {'상태':>8}  {'티커':<8}  {'등록일':<12}  사유")
    print(f"  {'-'*55}")

    for item in sorted(items, key=lambda x: x.get("added_at", "")):
        status = item.get("status", "?")
        ticker = item.get("ticker", "?")
        added = item.get("added_at", "")[:10] if item.get("added_at") else "-"
        reason = item.get("reason", "")[:40]
        status_icon = {"waiting": "⏳", "ready": "✅", "completed": "✓", "failed": "✗"}.get(status, "?")
        print(f"  {status_icon} {status:>7}  {ticker:<8}  {added:<12}  {reason}")

    waiting = sum(1 for i in items if i.get("status") == "waiting")
    ready = sum(1 for i in items if i.get("status") == "ready")
    print(f"\n  요약: waiting={waiting}  ready={ready}\n")


# ─────────────────────────────────────────────────────────────
# CLI 진입점
# ─────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SwingMCP Requeue 파이프라인 — ready 종목 Buy Pipeline 재실행"
    )

    sub = parser.add_subparsers(dest="cmd")

    # run (기본)
    p_run = sub.add_parser("run", help="Requeue Pipeline 실행 (기본)")
    cache_group = p_run.add_mutually_exclusive_group()
    cache_group.add_argument("--use-cache", action="store_true", help="LLM 캐시 있으면 재사용")
    cache_group.add_argument("--no-cache", action="store_true", help="LLM 캐시 무시 (기본값)")

    # add
    p_add = sub.add_parser("add", help="requeue 항목 추가")
    p_add.add_argument("ticker", help="티커 (예: NVDA)")
    p_add.add_argument("--reason", default="수동 등록", help="등록 사유")

    # list
    sub.add_parser("list", help="requeue 목록 조회")

    # 최상위에도 캐시 옵션 (서브커맨드 없이 실행 시)
    top_cache = parser.add_mutually_exclusive_group()
    top_cache.add_argument("--use-cache", dest="top_use_cache", action="store_true", help="LLM 캐시 있으면 재사용")
    top_cache.add_argument("--no-cache", dest="top_no_cache", action="store_true", help="LLM 캐시 무시 (기본값)")

    return parser.parse_args()


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    args = _parse_args()
    cmd = args.cmd or "run"  # 인수 없으면 run

    if cmd == "add":
        add_ticker(args.ticker, args.reason)
    elif cmd == "list":
        list_items()
    else:  # run
        # 서브커맨드 use_cache 우선, 없으면 최상위 플래그 확인
        use_cache = getattr(args, "use_cache", False) or getattr(args, "top_use_cache", False)
        asyncio.run(run_pipeline(use_cache=use_cache))
