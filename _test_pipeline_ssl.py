"""파이프라인 실행 중 환경변수 변화 추적"""
import sys, os
sys.path.insert(0, '.')

def chk(label):
    keys = ('SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'REQUESTS_CA_BUNDLE')
    for k in keys:
        v = os.environ.get(k, 'NOT SET')
        if v != 'NOT SET':
            print(f"[{label}] {k} = {v}")
    # curl_cffi DEFAULT_CACERT 확인
    try:
        import curl_cffi.curl as cc
        print(f"[{label}] DEFAULT_CACERT = {cc.DEFAULT_CACERT}")
    except Exception as e:
        print(f"[{label}] curl_cffi not loaded: {e}")

chk("START")

# 파이프라인과 동일한 모든 임포트 수행
from shared.config import get_config; chk("config")
from shared.schemas import PipelineContext, PipelinePaths; chk("schemas")
from orchestrator.pipelines import BuyPipeline; chk("BuyPipeline")
from core.obsidian import ObsidianClient; chk("obsidian")
from core.slack import SlackClient; chk("slack")

# Step 4 시뮬레이션: fetch_finviz_detail 실행
from core.api_fetcher import fetch_finviz_detail
chk("api_fetcher imported")

import asyncio
async def test():
    chk("BEFORE finviz_detail")
    try:
        result = await asyncio.to_thread(fetch_finviz_detail, 'MRVL')
        chk("AFTER finviz_detail")
        print("finviz_detail price:", result.price if result else None)
    except Exception as e:
        print(f"finviz error: {e}")
        chk("AFTER finviz_detail (error)")

    # Step 7 시뮬레이션
    chk("BEFORE option_chain")
    from core.api_fetcher import fetch_option_chain_fresh
    try:
        chain = await asyncio.to_thread(fetch_option_chain_fresh, 'MRVL', 21, 45)
        chk("AFTER option_chain")
        print("chain count:", len(chain) if chain else 0)
    except Exception as e:
        print(f"option_chain error: {e}")
        chk("AFTER option_chain (error)")

asyncio.run(test())
