import sys; sys.path.insert(0, ".")
from core.api_fetcher import fetch_stock_detail
fv = fetch_stock_detail("MRVL")
print("목표주가:", fv.target_price)
print("현재가:  ", fv.price)
print("RSI:    ", fv.rsi14)
