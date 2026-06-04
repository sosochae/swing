"""특정 종목 매수 파이프라인 실행

사용법:
    python scripts/run_ticker.py TSLA
    python scripts/run_ticker.py AAPL MSFT NVDA
    python scripts/run_ticker.py TSLA --use-cache
"""
from __future__ import annotations
# ── SSL CA bundle ASCII 경로 확보 (curl_cffi 로드 전에 반드시 실행) ──────────
# 시스템 Python 3.11 실행 시 certifi가 Non-ASCII 사용자 경로
# (C:\Users\소소\...) 에 위치해 curl error 77이 발생한다.
# curl_cffi 로드 이전에 ASCII 경로로 환경변수를 설정해 회피한다.
import os as _os, shutil as _sh, certifi as _certifi_ssl
_ca_raw = _certifi_ssl.where()
try:
    _ca_raw.encode('ascii')
    _ca_ascii = _ca_raw
except UnicodeEncodeError:
    from pathlib import Path as _Path
    _cache_dir = _Path(__file__).resolve().parents[1] / 'cache'
    _cache_dir.mkdir(exist_ok=True)
    _ca_ascii = str(_cache_dir / 'cacert.pem')
    _sh.copy2(_ca_raw, _ca_ascii)
for _ev in ('SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'REQUESTS_CA_BUNDLE'):
    _os.environ[_ev] = _ca_ascii
del _ca_raw, _ca_ascii, _ev, _os, _sh, _certifi_ssl
# ─────────────────────────────────────────────────────────────────────────────
import argparse
import asyncio
import sys
import io
from datetime import datetime
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))


async def run(tickers: list[str], use_cache: bool = False) -> None:
    from shared.config import get_config
    from shared.logger import setup_logging
    from shared.schemas import PipelineContext, PipelinePaths
    from orchestrator.pipelines import BuyPipeline
    from core.obsidian import ObsidianClient
    from core.slack import SlackClient

    cfg = get_config()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = "_".join(tickers)
    eid = f"buy_{label}_{ts}"
    setup_logging(eid)

    paths = PipelinePaths(
        summary_dir=Path(cfg.SUMMARY_DIR),
        finviz_file=Path(cfg.FINVIZ_FILE),
        earnings_dir=Path(cfg.EARNINGS_DIR),
        earnings_analysis=Path(cfg.EARNINGS_DIR) / "어닝 분석.md",
        positions_file=Path(cfg.POSITIONS_FILE),
        watchlist_file=Path(cfg.WATCHLIST_FILE),
        data_dir=Path(cfg.DATA_DIR),
    )

    ctx = PipelineContext(
        execution_id=eid,
        pipeline_type="buy",
        start_step=0,
        force_refresh=not use_cache,
        target_tickers=tickers,
        paths=paths,
    )

    pipeline = BuyPipeline(obsidian=ObsidianClient(), slack=SlackClient())
    print(f"{'='*60}")
    print(f"  실행 종목: {', '.join(tickers)}  [{eid}]")
    print(f"{'='*60}")

    result = await pipeline.run(ctx)

    print(f"\n완료: {result.status}")
    print(f"완료 단계: {result.completed_steps}")
    if result.failed_steps:
        print(f"실패 단계: {result.failed_steps}")

    for ticker in tickers:
        fv = ctx.finviz_detail.get(ticker) if ctx.finviz_detail else None
        tt = (ctx.summary_data.tickers[ticker].technical
              if ctx.summary_data and ticker in ctx.summary_data.tickers else None)
        if fv or tt:
            print(f"\n[기술 데이터 - {ticker}]")
            price = (fv.price if fv and fv.price else None) or (tt.price if tt else None)
            rsi = (fv.rsi14 if fv and fv.rsi14 else None) or (tt.rsi14 if tt else None)
            print(f"  가격: ${price}  RSI: {rsi}")
            if tt:
                print(f"  SMA20: ${tt.ma20}  SMA50: ${tt.ma50}  SMA200: ${tt.ma200}")
                print(f"  BB: ${tt.bb_lower:.2f} / ${tt.bb_mid:.2f} / ${tt.bb_upper:.2f}")
                print(f"  MACD: {tt.macd_line:.4f} / {tt.macd_signal:.4f}  ADX: {tt.adx14:.1f}")
                print(f"  S1: ${tt.support1}  R1: ${tt.resistance1}")
            if fv:
                print(f"  Forward PE: {fv.forward_pe}  Target: ${fv.target_price}  Recom: {fv.recom}")

    if ctx.final_rankings:
        for r in ctx.final_rankings:
            print(f"\n[최종 판단: {r.ticker}]")
            print(f"  행동: {r.action}  방향: {r.direction}")
            print(f"  확신도: {r.conviction.total_conviction:.2f}  신호: {r.conviction.technical_signals}/8")
            print(f"  Strike: ${r.strike}  Expiry: {r.expiry}")
            if ctx.option_validity and r.ticker in ctx.option_validity:
                ov = ctx.option_validity[r.ticker]
                print(f"  IV: {ov.greeks.iv * 100:.1f}%  Delta: {ov.greeks.delta:.2f}  IVR: {ov.greeks.ivr:.0f}")
    else:
        print("\n최종 순위 없음 (필터 탈락 또는 레짐 불리)")
        if ctx.filter_failures:
            for t, codes in ctx.filter_failures.items():
                print(f"  {t}: {codes}")

    print(f"\n{'='*60}")
    print(f"  Obsidian 노트: {getattr(ctx, 'obsidian_note_path', 'N/A')}")
    print(f"{'='*60}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SwingMCP 종목별 매수 파이프라인")
    parser.add_argument("tickers", nargs="*", help="분석할 티커 (예: TSLA AAPL)")
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument(
        "--use-cache", action="store_true",
        help="LLM 캐시 있으면 재사용"
    )
    cache_group.add_argument(
        "--no-cache", action="store_true",
        help="LLM 캐시 무시하고 새로 실행 (기본값)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    args = _parse_args()
    tickers = [t.upper() for t in args.tickers]
    if not tickers:
        print("사용법: python scripts/run_ticker.py TSLA")
        print("       python scripts/run_ticker.py AAPL MSFT NVDA")
        print("       python scripts/run_ticker.py TSLA --use-cache")
        sys.exit(1)

    use_cache = args.use_cache and not args.no_cache
    asyncio.run(run(tickers, use_cache=use_cache))
