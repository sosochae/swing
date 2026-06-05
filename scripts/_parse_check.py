from pathlib import Path
from core.parsers import load_latest_summary
from shared.config import get_config

cfg = get_config()
summary = load_latest_summary(Path(cfg.SUMMARY_DIR))

print("=== OI change 파싱 검증 ===")
for ticker, opt in summary.options.items():
    chain = opt.chain
    with_data = [e for e in chain if e.get("oi_change") is not None]
    non_zero = [e for e in chain if (e.get("oi_change") or 0) != 0]
    print(f"{ticker}: chain={len(chain)}, oi_change_not_none={len(with_data)}, non_zero={len(non_zero)}")
    if chain:
        s = chain[0]
        print(f"  chain[0]: strike={s['strike']}, oi={s['oi']}, oi_change={s['oi_change']}, ivr={s['ivr']:.1f}")

print("\n=== 이벤트 타입 검증 ===")
for ev in summary.events[:8]:
    print(f"  type=[{ev.type}] name={ev.name[:35]} D-{ev.days_until}")

print("\n=== avg_volume_ratio 검증 ===")
for ticker, td in summary.tickers.items():
    print(f"  {ticker}: avg_volume_ratio={td.technical.avg_volume_ratio:.3f}")
