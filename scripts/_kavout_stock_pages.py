"""임시: Kavout 종목 상세 페이지 3개 스크린샷 + 텍스트 추출"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from scripts.fetch_kavout import KAVOUT_PROFILE_DIR

TICKER = "mu"
PAGES = {
    "overview":   f"https://www.kavout.com/stocks/nasdaq-{TICKER}/micron-technology-inc",
    "analysis":   f"https://www.kavout.com/stocks/nasdaq-{TICKER}/micron-technology-inc/stock-analysis",
    "technical":  f"https://www.kavout.com/stocks/nasdaq-{TICKER}/micron-technology-inc/technical-analysis",
}

TEXT_JS = "() => document.body.innerText.slice(0, 4000)"

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(KAVOUT_PROFILE_DIR), channel="chrome",
        headless=False, args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.new_page()
    ss_dir = Path(__file__).parent

    for name, url in PAGES.items():
        page.goto(url, timeout=30_000)
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        page.wait_for_timeout(3_000)
        page.screenshot(path=str(ss_dir / f"kavout_stock_{name}.png"), full_page=True)
        text = page.evaluate(TEXT_JS)
        print(f"\n{'='*60}")
        print(f"[{name.upper()}] {url}")
        print('='*60)
        print(text[:3000])

    page.close()
    ctx.close()
