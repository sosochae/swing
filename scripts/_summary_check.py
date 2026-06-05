from pathlib import Path
import json, re

f = Path(r"R:\내 드라이브\마켓 수치\summary_20260605_134641.json")
lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
ticker_items = json.loads(lines[1])

for item in ticker_items:
    ticker_m = re.search(r"\[ (\w+) \]", item)
    if not ticker_m:
        continue
    ticker = ticker_m.group(1)
    vol_m = re.search(r"평균거래량 대비\s+:\s+(.+)", item)
    events_m = re.search(r"실적.*?D-(\d+)", item)
    print(f"{ticker}: 평균거래량={vol_m.group(1).strip() if vol_m else 'NOT FOUND'}")

# 이벤트 섹션 확인
market = json.loads(lines[0])
earning_lines = [l for l in market.split("\n") if "실적" in l]
print(f"\n실적 이벤트 줄 수: {len(earning_lines)}")
for l in earning_lines[:5]:
    print(f"  {repr(l[:80])}")
