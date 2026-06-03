"""
scripts/health_check.py
========================
설치 검증 및 런타임 디렉토리 초기화 스크립트 — §14.3

사용법:
  python scripts/health_check.py          # 환경 검증만
  python scripts/health_check.py --setup  # 디렉토리 생성 + 환경 검증 + Obsidian ping

종료 코드:
  0 — 모든 검증 통과
  1 — 환경 변수 누락 또는 Obsidian 연결 실패
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 이 줄 추가

def _check_env() -> tuple[list[str], list[str]]:
    """
    필수 환경 변수 및 경로 검증.

    Returns:
        (missing_vars, missing_paths): 누락된 환경 변수, 접근 불가 경로 목록
    """
    from shared.config import Config
    cfg = Config()

    missing_vars = cfg.validate()

    missing_paths: list[str] = []
    paths_to_check = [
        ("SUMMARY_DIR", cfg.SUMMARY_DIR),
        ("FINVIZ_FILE", cfg.FINVIZ_FILE),
        ("EARNINGS_DIR", cfg.EARNINGS_DIR),
        ("POSITIONS_FILE", cfg.POSITIONS_FILE),
    ]
    for label, p in paths_to_check:
        if p and not Path(p).exists():
            missing_paths.append(f"{label}: {p}")

    return missing_vars, missing_paths


def _setup_dirs() -> None:
    """런타임 디렉토리 생성 (§14.3 Step 5)"""
    from shared.config import Config
    Config.ensure_local_dirs()

    dirs = [
        Config.CACHE_DIR,
        Config.SNAPSHOTS_DIR,
        Config.LOGS_DIR,
        str(Path(Config.REQUEUE_FILE).parent),
    ]
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
        print(f"  ✅ {d}")


async def _ping_obsidian() -> bool:
    """Obsidian REST API 연결 확인 (§14.3)"""
    from core.obsidian import ObsidianClient
    client = ObsidianClient()
    try:
        ok = await client.ping()
        return ok
    except Exception as exc:
        print(f"  ❌ Obsidian ping 실패: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SwingMCP 환경 검증 및 초기화 (§14.3)"
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="런타임 디렉토리 생성 후 환경 검증 실행",
    )
    args = parser.parse_args()

    print("=" * 50)
    print("SwingMCP Health Check")
    print("=" * 50)

    exit_code = 0

    # 1. 디렉토리 생성 (--setup)
    if args.setup:
        print("\n[1/3] 런타임 디렉토리 생성")
        try:
            _setup_dirs()
        except Exception as exc:
            print(f"  ❌ 디렉토리 생성 실패: {exc}")
            exit_code = 1
    else:
        print("\n[1/3] 디렉토리 생성 스킵 (--setup 없음)")

    # 2. 환경 변수 검증
    print("\n[2/3] 환경 변수 및 경로 검증")
    try:
        missing_vars, missing_paths = _check_env()
    except Exception as exc:
        print(f"  ❌ 설정 로드 실패: {exc}")
        return 1

    if missing_vars:
        for v in missing_vars:
            print(f"  ❌ 누락된 환경 변수: {v}")
        exit_code = 1
    else:
        print("  ✅ 필수 환경 변수 모두 설정됨")

    if missing_paths:
        for p in missing_paths:
            print(f"  ⚠️  경로 없음 (런타임 전 생성 필요): {p}")
        # 경로 누락은 경고만 (FATAL 아님)
    else:
        print("  ✅ 데이터 경로 접근 가능")

    # 3. Obsidian 연결 확인 (--setup 또는 환경 변수 OK일 때)
    if args.setup or not missing_vars:
        print("\n[3/3] Obsidian REST API 연결 확인")
        try:
            obsidian_ok = asyncio.run(_ping_obsidian())
            if obsidian_ok:
                print("  ✅ Obsidian REST API 응답 정상")
            else:
                print("  ❌ Obsidian REST API 응답 없음 — Obsidian 실행 및 플러그인 확인 필요")
                exit_code = 1
        except Exception as exc:
            print(f"  ❌ Obsidian 연결 오류: {exc}")
            exit_code = 1
    else:
        print("\n[3/3] Obsidian ping 스킵 (환경 변수 미설정)")

    # 결과 요약
    print("\n" + "=" * 50)
    if exit_code == 0:
        print("✅ 모든 검증 통과. SwingMCP 실행 준비 완료.")
        print("   시작: scripts\\start.bat")
    else:
        print("❌ 검증 실패. 위 항목을 수정 후 재실행하세요.")
        print("   참고: .env.example → .env 복사 후 API 키 입력")
    print("=" * 50)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
