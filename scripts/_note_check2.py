import asyncio, os, sys, warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()

async def check():
    import httpx
    token = os.getenv("OBSIDIAN_API_KEY", "")
    base  = os.getenv("OBSIDIAN_BASE_URL", "https://127.0.0.1:27124/")
    url   = base.rstrip("/") + "/vault/swing-procedure/notes/buy/2026-06-06.md"
    async with httpx.AsyncClient(verify=False) as c:
        r = await c.get(url, headers={
            "Authorization": f"Bearer {token}",
            "accept": "application/vnd.olrapi.note+json"
        })
        content = r.json().get("content", "")

    print(f"노트 길이: {len(content)} chars\n")
    checks = {
        "MA 정렬 / 기술점수":   "MA",
        "RSI 값":               "RSI",
        "MACD 지표":            "MACD",
        "ADX 지표":             "ADX",
        "볼린저밴드":           "BB",
        "지지/저항":            "지지",
        "감성 분석 결과":        "overall_sentiment",
        "Bull/Bear thesis":     "bull_thesis",
        "기술 내러티브":         "technical_narrative",
        "Kavout K-Score":       "K-Score",
        "Devil Advocate 차감":  "da_reasons",
        "시나리오 3케이스":      "bearish",
        "확신도 분해":           "trend_confidence",
        "투자 기간 분류":        "horizon",
        "OI 변화 데이터":        "oi_change",
        "Implied Move":         "Implied Move",
        "Max Pain":             "Max Pain",
        "포지션 섹터 경고":      "섹터",
        "애널리스트 추천":       "Recom",
        "FINVIZ 밸류에이션":     "Forward P/E",
    }
    print("=== 데이터 반영 확인 ===")
    missing = []
    for label, keyword in checks.items():
        found = keyword in content
        status = "✅" if found else "❌"
        print(f"  {status} {label} ({keyword!r})")
        if not found:
            missing.append(label)
    print(f"\n누락: {missing if missing else '없음'}")

asyncio.run(check())
