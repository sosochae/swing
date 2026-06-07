"""
scripts/fetch_kavout.py
=======================
Kavout Quality Momentum 종목 스크래퍼

실행:
    python scripts/fetch_kavout.py                      # All Caps (기본)
    python scripts/fetch_kavout.py --universe sp500     # S&P 500
    python scripts/fetch_kavout.py --universe large-cap

흐름:
    1. kavout_chrome_profile/ 을 Chrome persistent context로 사용 (봇 감지 우회)
       - 최초: 브라우저 열림 → 로그인 → 데이터 확인 후 자동 진행
       - 이후: 프로파일 세션 재사용 → 자동 로그인
    2. quality-momentum 페이지 이동 (universe 파라미터)
    3. 두 섹션 추출:
       - Quantitative Momentum Plus (최대 30행): 메인 랭킹
       - 🆕 New This Week (최대 5행): 신규 진입 종목
    4. 시가총액 내림차순 정렬 + k_score 계산
    5. DATA_DIR/kavout_YYYYMMDD.csv 저장

출력 컬럼:
    symbol, company, price, market_cap, market_cap_raw,
    momentum_1m, roe, k_score, universe, section
    - section: "quantitative_momentum_plus" | "new_this_week"
    - New This Week는 market_cap/price 없음, k_score=0, entry_date 포함
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.config import get_config
from shared.logger import get_logger

log = get_logger()

# ── 상수 ──────────────────────────────────────────────────────
KAVOUT_PROFILE_DIR = ROOT / "scripts" / "kavout_chrome_profile"
BASE_URL = "https://www.kavout.com/ai-stock-picker/quality-momentum"

UNIVERSE_PARAMS: dict[str, str] = {
    "all-caps":    "all-caps",
    "sp500":       "sp500",
    "large-cap":   "large-cap",
    "mid-cap":     "mid-cap",
    "small-cap":   "small-cap",
    "russell1000": "russell-1000",
}

# ── stealth 헬퍼 ──────────────────────────────────────────────
def _apply_stealth(page) -> None:
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except Exception:
        pass


def _apply_stealth_to_context(ctx) -> None:
    ctx.on("page", lambda p: _apply_stealth(p))


# ── 헬퍼 ──────────────────────────────────────────────────────
def _parse_market_cap(raw: str) -> float:
    s = raw.strip().replace("$", "").replace(",", "")
    if s.endswith("T"): return float(s[:-1]) * 1e12
    if s.endswith("B"): return float(s[:-1]) * 1e9
    if s.endswith("M"): return float(s[:-1]) * 1e6
    if s.endswith("K"): return float(s[:-1]) * 1e3
    try:
        return float(s)
    except ValueError:
        return 0.0


def _calc_k_score(rank_1based: int, total: int) -> float:
    if total <= 1:
        return 9.0
    return round(9.0 - (rank_1based - 1) * 8.0 / (total - 1), 2)


# ── DOM 추출 JS ───────────────────────────────────────────────
# 테이블을 인덱스가 아닌 헤더 컬럼명으로 식별 (DOM 순서 변화에 강건)
# - QMP 테이블: "Market Cap" 헤더 보유
# - NTW 테이블: "Entry Date" 헤더 보유
_EXTRACT_JS = """
() => {
    const getText = el => el ? (el.textContent || el.innerText || '').trim() : '';

    // 모든 테이블을 헤더로 분류
    let qmpTable = null, ntwTable = null;
    for (const t of document.querySelectorAll('table')) {
        const hdrs = Array.from(t.querySelectorAll('thead th, thead td')).map(h => h.textContent.trim());
        if (hdrs.some(h => h.includes('Market Cap'))) qmpTable = t;
        if (hdrs.some(h => h.includes('Entry Date'))) ntwTable = t;
    }

    const result = { qmp: [], ntw: [], debug: '' };

    // ── Quantitative Momentum Plus ───────────────────────────
    if (qmpTable) {
        const rows = Array.from(qmpTable.querySelectorAll('tbody tr'));
        result.qmp = rows.map(row => {
            const cells = row.querySelectorAll('td');
            const btn    = cells[0] ? cells[0].querySelector('button') : null;
            const spans  = btn ? btn.querySelectorAll('span') : [];
            const coSpan = cells[0] ? cells[0].querySelector('span[title]') : null;
            return {
                symbol:      getText(spans[0]),
                company:     coSpan ? (coSpan.title || getText(coSpan)) : '',
                price:       getText(cells[1]).replace(/\\s+/g,' ').replace('$','').trim(),
                market_cap:  getText(cells[2]),
                momentum_1m: getText(cells[3]).replace('%','').trim(),
                roe:         getText(cells[4]).replace('%','').trim(),
                entry_date:  '',
                section:     'quantitative_momentum_plus',
            };
        }).filter(r => r.symbol !== '' && !r.symbol.includes('lock'));
    }

    // ── New This Week ────────────────────────────────────────
    if (ntwTable) {
        const rows = Array.from(ntwTable.querySelectorAll('tbody tr'));
        result.ntw = rows.map(row => {
            const cells = row.querySelectorAll('td');
            const btn    = cells[0] ? cells[0].querySelector('button') : null;
            const spans  = btn ? btn.querySelectorAll('span') : [];
            const coSpan = cells[0] ? cells[0].querySelector('span[title]') : null;
            return {
                symbol:      getText(spans[0]),
                company:     coSpan ? (coSpan.title || getText(coSpan)) : '',
                price:       '',
                market_cap:  '',
                momentum_1m: getText(cells[2]).replace('%','').trim(),
                roe:         getText(cells[3]).replace('%','').trim(),
                entry_date:  getText(cells[1]),
                section:     'new_this_week',
            };
        }).filter(r => r.symbol !== '' && !r.symbol.includes('lock'));
    }

    result.debug = (
        'qmpTable=' + (qmpTable ? 'found' : 'missing') +
        ' ntwTable=' + (ntwTable ? 'found' : 'missing') +
        ' qmp=' + result.qmp.length +
        ' ntw=' + result.ntw.length
    );
    return result;
}
"""

# Show More JS — 인덱스로 버튼 선택 (0=첫 번째/NTW, -1=마지막/QMP)
_SHOW_MORE_NTW_JS = """
() => {
    const btns = Array.from(document.querySelectorAll('button'))
        .filter(b => b.innerText.trim() === 'Show More');
    // 버튼이 2개 이상일 때만 클릭 (1개면 QMP 전용 버튼이므로 건드리지 않음)
    if (btns.length < 2) return 0;
    btns[0].click();
    return btns.length;
}
"""
_SHOW_MORE_QMP_JS = """
() => {
    const btns = Array.from(document.querySelectorAll('button'))
        .filter(b => b.innerText.trim() === 'Show More');
    if (btns.length === 0) return 0;
    btns[btns.length - 1].click();
    return btns.length;
}
"""

# 각 테이블 행 수 (헤더로 식별)
_QMP_ROW_COUNT_JS = """
(() => {
    for (const t of document.querySelectorAll('table')) {
        const hdrs = Array.from(t.querySelectorAll('thead th,thead td')).map(h=>h.textContent.trim());
        if (hdrs.some(h => h.includes('Market Cap')))
            return t.querySelectorAll('tbody tr').length;
    }
    return 0;
})()
"""
_NTW_ROW_COUNT_JS = """
(() => {
    for (const t of document.querySelectorAll('table')) {
        const hdrs = Array.from(t.querySelectorAll('thead th,thead td')).map(h=>h.textContent.trim());
        if (hdrs.some(h => h.includes('Entry Date')))
            return t.querySelectorAll('tbody tr').length;
    }
    return 0;
})()
"""

# 로그인 확인: Market Cap 테이블에서 실제 추출 가능한 티커(lock 아닌 행)가 3개 이상인지 확인.
# (비로그인: DOM에 $ 값이 있어도 대부분 lock 아이콘으로 가려져 추출 불가 → false positive 방지)
_LOGGED_IN_CHECK = """
(() => {
  for (const t of document.querySelectorAll('table')) {
    const hdrs = Array.from(t.querySelectorAll('thead th,thead td')).map(h=>h.textContent.trim());
    if (!hdrs.some(h=>h.includes('Market Cap'))) continue;
    let validCount = 0;
    for (const row of t.querySelectorAll('tbody tr')) {
      const cells = row.querySelectorAll('td');
      if (!cells[0]) continue;
      const btn = cells[0].querySelector('button');
      if (!btn) continue;
      const spans = btn.querySelectorAll('span');
      const symbol = spans[0] ? spans[0].textContent.trim() : '';
      if (symbol && !symbol.toLowerCase().includes('lock')) validCount++;
    }
    if (validCount >= 3) return true;
  }
  return false;
})()
"""


# ── 메인 스크래핑 ─────────────────────────────────────────────
def fetch_kavout(universe: str = "all-caps") -> Path:
    """
    Kavout Quality Momentum 페이지에서 종목 수집 후 CSV 저장.
    QMP(최대 30행) + NTW(최대 5행) 모두 추출, section 컬럼으로 구분.

    Returns:
        저장된 CSV 파일 경로
    """
    from playwright.sync_api import sync_playwright

    cfg      = get_config()
    data_dir = Path(cfg.DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)

    param = UNIVERSE_PARAMS.get(universe, "all-caps")
    url   = f"{BASE_URL}?universe={param}&region=US"

    KAVOUT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    qmp_rows: list[dict] = []
    ntw_rows: list[dict] = []

    with sync_playwright() as pw:
        # channel="chrome": 실제 Chrome 바이너리 사용 → 봇 감지 우회
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(KAVOUT_PROFILE_DIR),
            channel="chrome",
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars",
            ],
            ignore_default_args=["--enable-automation"],
        )

        _apply_stealth_to_context(ctx)
        page = ctx.new_page()
        _apply_stealth(page)

        try:
            log.info("kavout_goto", url=url)
            page.goto(url, timeout=30_000)
            # networkidle: React SPA의 모든 API 호출 완료까지 대기
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass  # timeout 허용 — 이후 폴링으로 보완
            page.wait_for_timeout(3_000)

            # ── 로그인 + 데이터 로드 대기 ────────────────────────
            is_logged_in = page.evaluate(_LOGGED_IN_CHECK)

            if not is_logged_in:
                print("\n" + "=" * 55)
                print("  브라우저에서 Kavout 에 로그인하세요.")
                print("  데이터 테이블이 보이면 자동으로 이어집니다.")
                print("  (이후 실행은 자동 로그인됩니다)")
                print("=" * 55)
                log.info("kavout_waiting_for_login")
                deadline = time.time() + 300
                while time.time() < deadline:
                    try:
                        if page.evaluate(_LOGGED_IN_CHECK):
                            break
                    except Exception:
                        pass
                    page.wait_for_timeout(2_000)
                else:
                    log.warning("kavout_login_timeout")
                    raise RuntimeError("로그인 타임아웃 (5분). 재실행하세요.")
                log.info("kavout_login_detected")
                # 로그인 후 추가 대기 (데이터 렌더링)
                page.wait_for_timeout(3_000)
            else:
                log.info("kavout_session_valid")

            # ── 타깃 universe 확인 / 이동 ───────────────────────
            if param not in page.url:
                page.goto(url, timeout=30_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=20_000)
                except Exception:
                    pass
                page.wait_for_timeout(3_000)

            # QMP 테이블 행 등장 대기 (최대 30초)
            _QMP_ROWS_READY = (
                "(() => {"
                + _QMP_ROW_COUNT_JS.strip().lstrip("(").rstrip(")")
                + " > 0})()"
            )
            deadline2 = time.time() + 30
            while time.time() < deadline2:
                try:
                    cnt = page.evaluate(_QMP_ROW_COUNT_JS)
                    if cnt > 0:
                        log.info("kavout_table_ready", rows=cnt)
                        break
                except Exception:
                    pass
                page.wait_for_timeout(1_000)
            page.wait_for_timeout(1_000)

            # ── 팝업 모달 닫기 (WHAT'S NEW / 프로모션 등) ─────────
            page.bring_to_front()
            _ss = ROOT / "scripts"
            page.screenshot(path=str(_ss / "kavout_before.png"))
            log.info("kavout_screenshot_before")

            _DISMISS_MODAL_JS = """
() => {
    // X 닫기 버튼 우선 (aria-label 또는 텍스트 × / ✕)
    const close = document.querySelector(
        'button[aria-label="Close"], button[aria-label="close"], ' +
        'button[aria-label="Dismiss"], [data-dismiss="modal"]'
    );
    if (close) { close.click(); return 'clicked_aria_close'; }

    // × 또는 ✕ 텍스트인 button
    for (const btn of document.querySelectorAll('button')) {
        const t = btn.textContent.trim();
        if (t === '×' || t === '✕' || t === 'x' || t === 'X') {
            btn.click();
            return 'clicked_x_button';
        }
    }

    // 모달 감지만 (닫기 실패 시 Escape 사용)
    const hasModal = !!document.querySelector(
        '[role="dialog"], [class*="modal"], [class*="Modal"], [class*="popup"], [class*="Popup"]'
    );
    return hasModal ? 'modal_found_no_close' : 'no_modal';
}
"""
            dismiss_result = page.evaluate(_DISMISS_MODAL_JS)
            log.info("kavout_modal_check", result=dismiss_result)
            if dismiss_result != "no_modal":
                page.keyboard.press("Escape")
                page.wait_for_timeout(800)

            # bring_to_front 후 React 재렌더링으로 테이블이 깜빡일 수 있음
            # → 2초 간격으로 2회 연속 rows > 0 이어야 안정 상태로 판단
            deadline3 = time.time() + 30
            while time.time() < deadline3:
                try:
                    cnt = page.evaluate(_QMP_ROW_COUNT_JS)
                    if cnt > 0:
                        page.wait_for_timeout(2_000)
                        cnt2 = page.evaluate(_QMP_ROW_COUNT_JS)
                        if cnt2 > 0:
                            log.info("kavout_table_stable", rows=cnt2)
                            break
                except Exception:
                    pass
                page.wait_for_timeout(500)
            page.wait_for_timeout(500)

            # ── Show More (JS click — NTW 먼저, QMP 다음) ───────────
            _NTW_EXISTS_JS = (
                "(() => { for (const t of document.querySelectorAll('table')) {"
                " const h = Array.from(t.querySelectorAll('thead th,thead td')).map(e=>e.textContent.trim());"
                " if (h.some(s=>s.includes('Entry Date'))) return true; } return false; })()"
            )

            def _expand_table(row_count_js, show_more_js, label, max_rows=999, exists_js=None):
                """Show More를 JS click으로 반복해 테이블 전체 로드.
                - 테이블 없거나 초기 0행이면 skip (다른 테이블 Show More 오클릭 방지)
                - Show More 클릭 후 행 수가 실제로 증가할 때까지 최대 8초 대기
                - 최종 행 수도 안정될 때까지 대기
                """
                if exists_js is not None and not page.evaluate(exists_js):
                    log.info("kavout_expand_skip", table=label, reason="no_table")
                    return 0
                cnt = page.evaluate(row_count_js)
                if cnt == 0:
                    log.info("kavout_expand_skip", table=label, reason="empty")
                    return 0
                clicks = 0
                for _ in range(20):
                    if cnt >= max_rows:
                        break
                    try:
                        n = page.evaluate(show_more_js)
                    except Exception:
                        # Show More 클릭이 페이지 이동을 유발한 경우 원래 URL로 복귀
                        log.warning("kavout_show_more_nav", table=label)
                        try:
                            page.goto(url, timeout=30_000)
                            page.wait_for_load_state("networkidle", timeout=15_000)
                            page.wait_for_timeout(3_000)
                        except Exception:
                            pass
                        break
                    if n == 0:
                        break
                    # Show More 클릭 후 행 수가 증가할 때까지 최대 8초 대기
                    deadline_w = time.monotonic() + 8
                    new_cnt = cnt
                    while time.monotonic() < deadline_w:
                        page.wait_for_timeout(600)
                        try:
                            new_cnt = page.evaluate(row_count_js)
                        except Exception:
                            new_cnt = 0
                        if new_cnt > cnt:
                            break
                    if new_cnt <= cnt:
                        break
                    clicks += 1
                    cnt = new_cnt
                # 최종 행 수: 일시적 0 후 복구 대기 (최대 5초)
                deadline_f = time.monotonic() + 5
                try:
                    final = page.evaluate(row_count_js)
                except Exception:
                    final = cnt
                while final == 0 and time.monotonic() < deadline_f:
                    page.wait_for_timeout(500)
                    try:
                        final = page.evaluate(row_count_js)
                    except Exception:
                        final = cnt
                        break
                log.info("kavout_expand_done", table=label, clicks=clicks, rows=final)
                return final

            ntw_final = _expand_table(_NTW_ROW_COUNT_JS, _SHOW_MORE_NTW_JS, "ntw", exists_js=_NTW_EXISTS_JS)

            # ── 추출 (Show More 전 먼저 시도) ────────────────────
            page.wait_for_timeout(500)
            result = page.evaluate(_EXTRACT_JS)
            log.info("kavout_extract_debug_pre", debug=result.get("debug", ""))
            pre_qmp = result.get("qmp", [])

            # QMP Show More가 필요한 경우만 expand (초기 추출이 부족할 때)
            if len(pre_qmp) < 30:
                qmp_final = _expand_table(_QMP_ROW_COUNT_JS, _SHOW_MORE_QMP_JS, "qmp", max_rows=30)
                if qmp_final > len(pre_qmp):
                    page.wait_for_timeout(800)
                    result = page.evaluate(_EXTRACT_JS)

            page.screenshot(path=str(_ss / "kavout_after.png"))
            log.info("kavout_screenshot_after")
            log.info("kavout_extract_debug", debug=result.get("debug", ""))
            log.info("kavout_extract_debug", debug=result.get("debug", ""))
            qmp_rows = result.get("qmp", [])
            ntw_rows = result.get("ntw", [])
            log.info("kavout_rows_raw", qmp=len(qmp_rows), ntw=len(ntw_rows))

        finally:
            page.close()
            ctx.close()

    if not qmp_rows:
        raise RuntimeError(
            "Kavout QMP 데이터 없음 — 로그인 필요 또는 페이지 구조 변경. "
            f"프로파일 초기화: {KAVOUT_PROFILE_DIR} 삭제 후 재실행"
        )

    # ── QMP 후처리: 숫자 파싱 + 정렬 + k_score ──────────────────
    for r in qmp_rows:
        r["market_cap_raw"] = _parse_market_cap(r["market_cap"])

    qmp_rows.sort(key=lambda r: r["market_cap_raw"], reverse=True)
    total = len(qmp_rows)
    for i, r in enumerate(qmp_rows, 1):
        r["k_score"]  = _calc_k_score(i, total)
        r["universe"] = universe

    # ── NTW 후처리 ───────────────────────────────────────────────
    for r in ntw_rows:
        r["market_cap_raw"] = 0.0
        r["k_score"]        = 0.0
        r["universe"]       = universe

    all_rows = qmp_rows + ntw_rows

    # ── CSV 저장 ─────────────────────────────────────────────────
    today    = datetime.now().strftime("%Y%m%d")
    out_file = data_dir / f"kavout_{today}.csv"
    fields   = ["symbol", "company", "price", "market_cap", "market_cap_raw",
                "momentum_1m", "roe", "k_score", "universe", "section", "entry_date"]

    with out_file.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    log.info("kavout_saved", file=str(out_file), qmp=len(qmp_rows), ntw=len(ntw_rows))
    print(f"\n✅  저장 완료 → {out_file}")
    print(f"   Quantitative Momentum Plus: {len(qmp_rows)}개")
    print(f"   New This Week:              {len(ntw_rows)}개")
    print("\n  [QMP Top 5]")
    for r in qmp_rows[:5]:
        print(f"  {r['symbol']:6s}  {r['market_cap']:12s}  k={r['k_score']}")
    if ntw_rows:
        print("\n  [New This Week]")
        for r in ntw_rows:
            print(f"  {r['symbol']:6s}  entry={r['entry_date']}  mom={r['momentum_1m']}%")
    return out_file


# ── CLI ───────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kavout Quality Momentum 스크래퍼")
    parser.add_argument(
        "--universe", default="all-caps",
        choices=list(UNIVERSE_PARAMS.keys()),
        help="캡 필터 (기본: all-caps)",
    )
    args = parser.parse_args()
    fetch_kavout(universe=args.universe)
