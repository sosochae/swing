"""
OI 변화 신호 로직 단위 검증:
1. parsers.py: OI변화 파싱
2. buy_steps: _summary_oi_change_map 생성 및 신호 판정
"""
import re
from shared import strategy as st

# ── 1. parsers.py 정규식 검증 ──────────────────────────
pattern = re.compile(
    r"(CALL|PUT)\s+Strike ([\d.]+)\s+OI ([\d,]+)\s+Vol ([\d,]+)"
    r"\s+IV ([\d.]+)\s+IVR ([\d.]+)\s+Mid ([\d.]+)\s+Sprd% ([\d.]+)"
    r"\s+Delta ([-\d.]+)\s+Theta ([-\d.]+)"
    r"(?:\s+OI변화\s+([-+]?[\d,]+|N/A))?"
)

test_lines = [
    "    CALL  Strike 320  OI 3,178  Vol 10825  IV 1.27  IVR 67  Mid 22.17  Sprd% 0.03  Delta 0.51  Theta -1.63  OI변화 +450  ",
    "    PUT   Strike 300  OI 2,000  Vol 500    IV 1.10  IVR 45  Mid 15.00  Sprd% 0.05  Delta -0.45  Theta -1.20  OI변화 N/A  ",
    "    CALL  Strike 310  OI 1,000  Vol 200    IV 1.20  IVR 55  Mid 18.00  Sprd% 0.04  Delta 0.56  Theta -1.40  OI변화 0  ",
]

print("=== 1. 정규식 파싱 검증 ===")
parsed = []
for line in test_lines:
    m = pattern.search(line)
    if m:
        raw_chg = m.group(11)
        oi_chg = None
        if raw_chg and raw_chg != "N/A":
            oi_chg = int(raw_chg.replace("+","").replace(",",""))
        print(f"  {m.group(1)} Strike={m.group(2)}: OI변화={repr(raw_chg)} -> oi_change={oi_chg}")
        parsed.append({"option_type": m.group(1).lower(), "strike": float(m.group(2)),
                        "oi": int(m.group(3).replace(",","")), "oi_change": oi_chg})
    else:
        print(f"  FAIL: {line[:60]}")

# ── 2. _summary_oi_change_map 구성 시뮬레이션 ──────────
print()
print("=== 2. OI 변화 맵 구성 ===")
_summary_oi_change_map = {
    "TESTX": {
        (float(e["strike"]), e["option_type"]): e["oi_change"]
        for e in parsed
    }
}
print(f"  TESTX 맵 항목: {len(_summary_oi_change_map['TESTX'])}개")
for k, v in _summary_oi_change_map["TESTX"].items():
    print(f"    {k}: {v}")

# ── 3. OI 신호 판정 시뮬레이션 (long_call 방향) ────────────
print()
print("=== 3. OI 신호 판정 (long_call, spot=315) ===")
spot = 315.0
is_long = True
# yfinance 교체 체인 (oi_change 없음) 시뮬레이션
fresh_chain = [
    {"option_type": "call", "strike": 320.0, "oi": 3178},
    {"option_type": "put",  "strike": 300.0, "oi": 2000},
    {"option_type": "call", "strike": 310.0, "oi": 1000},
]

chg_map = _summary_oi_change_map.get("TESTX", {})
call_growth, put_growth, call_total, put_total = 0, 0, 0, 0
has_data = False

for ce in fresh_chain:
    strike = float(ce["strike"])
    otype  = str(ce["option_type"])
    if abs(strike / spot - 1.0) > 0.10:
        continue
    oi  = ce["oi"]
    chg = chg_map.get((strike, otype))
    if chg is not None:
        has_data = True
    if otype == "call":
        call_total += oi
        if chg is not None: call_growth += max(0, chg)
    elif otype == "put":
        put_total += oi
        if chg is not None: put_growth += max(0, chg)

call_ratio = call_growth / call_total if call_total > 0 else 0.0
put_ratio  = put_growth  / put_total  if put_total  > 0 else 0.0
target_ratio = call_ratio if is_long else put_ratio
signal_fires = target_ratio >= st.OI_CHANGE_RATIO_THRESHOLD

print(f"  has_data={has_data}")
print(f"  call_growth={call_growth}, call_total={call_total}, call_ratio={call_ratio:.3f}")
print(f"  put_growth={put_growth},   put_total={put_total},   put_ratio={put_ratio:.3f}")
print(f"  target_ratio={target_ratio:.3f} >= threshold={st.OI_CHANGE_RATIO_THRESHOLD}")
print(f"  신호 발화: {'✅ YES' if signal_fires else '❌ NO'}")
print(f"  (call_growth +450 / call_total 4178 = {450/4178:.3f} -> 14.1% > 5%)")
