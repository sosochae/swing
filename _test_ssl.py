import sys, os
sys.path.insert(0, '.')

def chk(label):
    curl = os.environ.get('CURL_CA_BUNDLE', 'NOT SET')
    ssl_f = os.environ.get('SSL_CERT_FILE', 'NOT SET')
    print(f"[{label}] CURL={curl}  SSL={ssl_f}")

chk("START")

for mod in ['finnhub', 'httpx', 'aiohttp', 'requests', 'urllib3']:
    try:
        __import__(mod)
        chk(mod)
    except ImportError:
        pass

from core import api_fetcher; chk("api_fetcher")
from orchestrator.steps import buy_steps; chk("buy_steps")

# 실제 yfinance Ticker 생성 - 여기서 curl_cffi 세션 초기화됨
import asyncio
async def simulate():
    import yfinance as yf
    chk("yfinance import")
    t = yf.Ticker('MRVL')
    chk("Ticker() created")
    opts = t.options
    chk("t.options fetched")
    return opts

result = asyncio.run(simulate())
print("options:", result[:2] if result else "none")
chk("AFTER all")
