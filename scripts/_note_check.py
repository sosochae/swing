import asyncio, ssl, os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

async def check():
    import httpx
    token = os.getenv("OBSIDIAN_TOKEN", "")
    if not token:
        print("TOKEN EMPTY")
        return
    async with httpx.AsyncClient(verify=False) as c:
        r = await c.get(
            "https://127.0.0.1:27124/vault/swing-procedure/notes/buy/2026-06-06.md",
            headers={"Authorization": f"Bearer {token}", "accept": "application/vnd.olrapi.note+json"}
        )
        data = r.json()
        content = data.get("content", "")
        # 핵심 섹션 존재 여부 확인
        checks = [
            "OI 변화", "oi_change", "signal_count", "final_score",
            "MRVL", "MU", "확신도", "시나리오", "Devil", "DA",
            "Kavout", "sentiment", "technical_narrative",
            "horizon", "investment_horizon"
        ]
        print(f"노트 길이: {len(content)} chars")
        for c_str in checks:
            found = c_str in content
            print(f"  {'✅' if found else '❌'} {c_str}")

asyncio.run(check())
