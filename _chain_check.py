import sys, os
sys.path.insert(0, '.')
os.environ['SSL_CERT_FILE'] = r'C:\MCP\Swing\cache\cacert.pem'
os.environ['CURL_CA_BUNDLE'] = r'C:\MCP\Swing\cache\cacert.pem'

import yfinance as yf
from datetime import date

t = yf.Ticker('MRVL')
info = t.info
spot = info.get('currentPrice') or info.get('regularMarketPrice', 0)
print(f"현재가: ${spot:.2f}")
exps = t.options
print(f"전체 만기: {len(exps)}개  {exps[:5]}... 등")

today = date.today()

# 중기 DTE 45-90 필터링
mid_exps = [(e, (date.fromisoformat(e) - today).days)
            for e in exps if 45 <= (date.fromisoformat(e) - today).days <= 90]
print(f"\n중기 범위(DTE 45-90) 만기: {mid_exps}")

# 각 만기 ATM OI 계산
print("\n=== 만기별 ATM OI ===")
best_oi = -1
best_exp = None
for exp_str, dte in mid_exps:
    try:
        ch = t.option_chain(exp_str)
        atm_calls = ch.calls[(ch.calls.strike >= spot * 0.95) & (ch.calls.strike <= spot * 1.05)]
        atm_puts  = ch.puts[(ch.puts.strike >= spot * 0.95) & (ch.puts.strike <= spot * 1.05)]
        oi_sum = int(atm_calls.openInterest.fillna(0).sum() + atm_puts.openInterest.fillna(0).sum())
        n_atm = len(atm_calls) + len(atm_puts)
        print(f"  {exp_str} (DTE {dte}): ATM OI={oi_sum:,}  ATM 계약 수={n_atm}개")
        if oi_sum > best_oi:
            best_oi = oi_sum
            best_exp = exp_str
            best_dte = dte
    except Exception as e:
        print(f"  {exp_str}: 오류 {e}")

print(f"\n선택된 만기: {best_exp} (DTE {best_dte}, ATM OI {best_oi:,})")

# 선택된 만기의 콜 체인 상세 (spot ±10%)
ch_best = t.option_chain(best_exp)
calls = ch_best.calls
atm_range = calls[(calls.strike >= spot * 0.90) & (calls.strike <= spot * 1.10)].copy()
atm_range = atm_range.sort_values('strike')

print(f"\n=== {best_exp} 콜 체인 (현재가 ${spot:.1f} 기준 ±10%) ===")
print(f"{'Strike':>8} {'Bid':>7} {'Ask':>7} {'Mid':>7} {'IV%':>7} {'OI':>6} {'Delta':>7}")
print("-" * 60)
for _, row in atm_range.iterrows():
    strike = row['strike']
    bid = row.get('bid', 0) or 0
    ask = row.get('ask', 0) or 0
    last = row.get('lastPrice', 0) or 0
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last
    iv = (row.get('impliedVolatility') or 0) * 100
    oi = int(row.get('openInterest') or 0)
    delta_raw = row.get('delta') or None

    # BS delta 계산 (yfinance가 delta 안 줄 때)
    if delta_raw is None and iv > 0:
        import math
        T = best_dte / 365.0
        r = 0.04
        iv_dec = iv / 100
        try:
            d1 = (math.log(spot / strike) + (r + 0.5 * iv_dec**2) * T) / (iv_dec * math.sqrt(T))
            from scipy.stats import norm
            delta_raw = norm.cdf(d1)
        except Exception:
            pass

    delta_str = f"{delta_raw:.3f}" if delta_raw is not None else "N/A"
    marker = " <-- SELECTED" if 0.42 <= (delta_raw or 0) <= 0.57 else ""
    print(f"  ${strike:>7.1f} {bid:>7.2f} {ask:>7.2f} {mid:>7.2f} {iv:>7.1f}% {oi:>6,} {delta_str:>7}{marker}")
