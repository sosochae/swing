import sys; sys.path.insert(0, '.')
from datetime import date

today = date.today()
exps = ['2026-06-05','2026-06-12','2026-06-18','2026-06-26',
        '2026-07-02','2026-07-10','2026-07-17','2026-08-21',
        '2026-09-18','2026-11-20','2026-12-18','2027-01-15']

print(f"오늘: {today}")
print(f"{'만기일':<14} {'DTE':>4}  {'포함범위'}")
print("-"*55)
for e in exps:
    d = (date.fromisoformat(e) - today).days
    ranges = []
    if 21 <= d <= 35: ranges.append("단기(21-35)")
    if 45 <= d <= 90: ranges.append("신중기(45-90)")  # 현재 설정
    if 36 <= d <= 90: ranges.append("수정중기(36-90)") # 수정 후
    if 90 <= d <= 180: ranges.append("장기(90-180)")
    tag = " ← JULY 17 표준월물!" if "07-17" in e else ""
    rng_str = " / ".join(ranges) if ranges else "제외됨"
    print(f"{e}  {d:>4}일  {rng_str}{tag}")
