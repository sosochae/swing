"""obsidian.py opt_analytics 렌더링 로직 단위 테스트"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))

# _format_integrated_buy_block 안의 opt_analytics 렌더링 로직만 추출해서 테스트
opt_analytics = {
    "implied_move_pct": 4.25,
    "max_pain": 315.0,
    "pc_ratio": 0.62,
    "oi_change_signal": True,
}

lines = []
if opt_analytics:
    _im  = opt_analytics.get("implied_move_pct")
    _mp  = opt_analytics.get("max_pain")
    _pc  = opt_analytics.get("pc_ratio")
    _oi_sig = opt_analytics.get("oi_change_signal")
    if any(v is not None for v in [_im, _mp, _pc, _oi_sig]):
        lines += ["", "**📐 옵션 시장 구조**", ""]
        lines += ["| 항목 | 값 |", "|------|-----|"]
        if _im is not None:
            lines.append(f"| Implied Move (내재 이동폭) | ±{_im:.1f}% |")
        if _mp is not None:
            lines.append(f"| Max Pain | ${_mp:,.2f} |")
        if _pc is not None:
            _pc_label = "풋 우세" if _pc > 1.2 else ("콜 우세" if _pc < 0.7 else "중립")
            lines.append(f"| P/C Ratio (OI 기준) | {_pc:.3f} ({_pc_label}) |")
        if _oi_sig is not None:
            _oi_icon = "✅ 방향 일치 OI 증가 감지" if _oi_sig else "⬜ 해당 없음"
            lines.append(f"| OI 변화 신호 | {_oi_icon} |")
        lines.append("")

output = "\n".join(lines)
print("=== 렌더링 결과 ===")
print(output)
print()

checks = {
    "Implied Move 출력": "Implied Move",
    "수치 ±4.3%":        "±4.3%",
    "Max Pain 출력":     "Max Pain",
    "315.00":            "315.00",
    "P/C Ratio 출력":    "P/C Ratio",
    "0.620 (콜 우세)":   "콜 우세",
    "OI 변화 신호":      "OI 변화 신호",
    "방향 일치":         "방향 일치 OI 증가",
}
all_ok = True
for label, kw in checks.items():
    found = kw in output
    if not found: all_ok = False
    print(f"  {'OK' if found else 'FAIL'} {label}: {repr(kw)}")

print()
print("=== 최종:", "PASS" if all_ok else "FAIL", "===")
