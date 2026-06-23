import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yfinance as yf
import datetime, pytz

t = yf.Ticker('LRCX')
hist = t.history(period='5d', auto_adjust=True)
print(hist[['Volume']].tail(5))

et = pytz.timezone('America/New_York')
now_et = datetime.datetime.now(et)
print(f"\n현재 ET: {now_et.strftime('%H:%M')}")
print(f"hist[-1] date: {hist.index[-1].date()}")
print(f"hist[-2] date: {hist.index[-2].date()}")
vols = hist['Volume'].tolist()

if len(vols) >= 22:
    avg20 = sum(vols[-21:-1]) / 20
    print(f"\nvolumes[-1] (오늘 현재): {vols[-1]:,.0f}")
    print(f"volumes[-2] (전일 완결): {vols[-2]:,.0f}")
    print(f"20일 평균:               {avg20:,.0f}")
    print(f"RVOL 현재 (오늘 미완성): {vols[-1]/avg20:.2f}")
    print(f"RVOL 전일 (완결):        {vols[-2]/avg20:.2f}")
else:
    print("데이터 부족")
