import asyncio, httpx
from shared.config import get_config
cfg = get_config()

async def read_note(path):
    headers = {"Authorization": f"Bearer {cfg.OBSIDIAN_API_KEY}", "Content-Type": "text/markdown"}
    async with httpx.AsyncClient(verify=False) as c:
        r = await c.get(f"{cfg.OBSIDIAN_BASE_URL}/vault/{path}", headers=headers)
        return r.text if r.status_code == 200 else f"ERROR {r.status_code}"

async def main():
    n11 = await read_note("swing-procedure/notes/buy/2026-06-11.md")
    n12 = await read_note("swing-procedure/notes/buy/2026-06-12.md")
    print(f"[DEBUG] n11 첫 50자: {repr(n11[:50])}")
    print(f"[DEBUG] n12 첫 50자: {repr(n12[:50])}")
    w11 = len(n11.split())
    w12 = len(n12.split())
    print(f"11일: {len(n11)}자 / {w11}단어")
    print(f"12일: {len(n12)}자 / {w12}단어")
    print(f"차이: {w11 - w12}단어")

    print("\n섹션 차이 (11일 vs 12일):")
    sections = ["TYPE 1", "TYPE 3", "TYPE 4", "TYPE 5", "3-6", "가격 레벨", "DI+", "DI-",
                "ADX", "EMA", "SMA", "4H", "피보나치", "VWAP", "Keltner", "Donchian",
                "Bollinger", "ATR", "Camarilla", "Parabolic", "FVG", "Gap", "주봉", "월간"]
    for s in sections:
        e11 = s in n11
        e12 = s in n12
        if e11 != e12:
            print(f"  [차이]  11일:{e11} / 12일:{e12}  → {s}")

    print(f"\nN/A 개수  —  11일: {n11.count('N/A')}  /  12일: {n12.count('N/A')}")
    print(f"N/A 차이: +{n12.count('N/A') - n11.count('N/A')}")

    # 줄 수 비교
    print(f"\n줄 수  —  11일: {len(n11.splitlines())}  /  12일: {len(n12.splitlines())}")

asyncio.run(main())
