"""
scripts/run_kavout_screener.py
==============================
Kavout AI 유니버스 기반 펀더멘털 스크리닝 + K어닝콜 LLM 분석 독립 실행 스크립트

screener_mcp/run_screener.py와 동일한 3단계 파이프라인이지만
유니버스 소스와 어닝 데이터 경로가 다릅니다:

  Step 1: kavout_*.csv (최신) 파싱 → Kavout 유니버스 구성
          + finviz_output/*.txt에서 동일 티커 상세 데이터 보강
  Step 2: K어닝 분석.md LLM 분석 (K어닝콜 가이던스 + 경영진 톤)
  Step 3: 모멘텀/펀더멘털/카탈리스트 점수화 + K-Score 표시

결과 → Obsidian 노트 저장 + Slack 요약 전송

사용법:
    cd C:\\MCP\\Swing
    .venv\\Scripts\\python scripts\\run_kavout_screener.py                   # 기본 (어닝 캐시 재사용)
    .venv\\Scripts\\python scripts\\run_kavout_screener.py --refresh-earnings # 어닝 LLM 새로 실행
    .venv\\Scripts\\python scripts\\run_kavout_screener.py --top 20

Kavout CSV 수집 (별도 선행 실행):
    .venv\\Scripts\\python scripts\\fetch_kavout.py
    .venv\\Scripts\\python scripts\\fetch_kavout.py --universe sp500
"""

from __future__ import annotations
# ── SSL CA bundle ASCII 경로 확보 (curl_cffi 로드 전에 반드시 실행) ──────────
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
import time
import uuid
from datetime import date, datetime
from pathlib import Path

# ── 프로젝트 루트를 sys.path에 추가 ──────────────────────────
_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))


async def run(refresh_earnings: bool = False, top_n: int = 10) -> None:
    from core.api_fetcher import fetch_finviz_details_bulk
    from core.earnings_analyzer import analyze_earnings
    from core.fundamental_screener import rank_universe
    from core.obsidian import ObsidianClient
    from core.parsers import parse_kavout_universe, parse_earnings
    from core.slack import SlackClient
    from shared.config import get_config
    from shared.logger import setup_logging
    from shared.schemas import KavoutRow, ScreenerResult

    cfg = get_config()
    execution_id = f"kavout_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log = setup_logging(execution_id)

    obsidian = ObsidianClient()
    slack = SlackClient()

    # ── 경로 설정 ─────────────────────────────────────────────
    earnings_dir     = Path(cfg.EARNINGS_DIR)          # Y:\내 드라이브\어닝
    data_dir         = Path(cfg.DATA_DIR)              # Y:\내 드라이브\Data
    k_earnings       = earnings_dir / "K어닝 분석.md"
    k_earnings_today = earnings_dir / "K어닝 분석_today.md"

    print(f"\n{'='*60}")
    print(f"  SwingMCP Kavout 스크리닝  [{execution_id}]")
    print(f"  refresh_earnings={refresh_earnings}  top_n={top_n}")
    print(f"{'='*60}")

    start = time.monotonic()

    # ── Step 1: Kavout CSV 파싱 + API 데이터 수집 ────────────
    print("\n▶ Step 1: Kavout 유니버스 파싱...")
    kavout_rows = parse_kavout_universe(data_dir)
    if not kavout_rows:
        print(f"  ✗ FATAL — kavout_*.csv 파일 없음: {data_dir}")
        return

    kavout_tickers: set[str] = {r.ticker for r in kavout_rows}
    kavout_map: dict[str, KavoutRow] = {r.ticker: r for r in kavout_rows}
    print(f"  ✓ Kavout 유니버스: {len(kavout_tickers)}개 종목")

    print(f"  ▷ Yahoo Finance API 수집 중... ({len(kavout_tickers)}개 티커)")
    finviz_details = await fetch_finviz_details_bulk(sorted(kavout_tickers))
    fetched_ok = sum(1 for d in finviz_details.values() if d.price is not None)
    print(f"  ✓ API 수집 완료: {fetched_ok}/{len(kavout_tickers)}개 (가격 확인)")

    # 가격 없는 티커는 Kavout CSV price로 보완
    fallback_count = 0
    for row in kavout_rows:
        if finviz_details[row.ticker].price is None and row.price is not None:
            finviz_details[row.ticker].price = row.price
            fallback_count += 1
    if fallback_count:
        print(f"  △ Kavout CSV price 보완: {fallback_count}개")

    # 메타 (sector/company) — Kavout CSV company 필드 사용
    meta: dict[str, dict] = {
        r.ticker: {"sector": "", "company": getattr(r, "company", "")}
        for r in kavout_rows
    }

    # kavout_output 펀더멘털 보강 제거 → yfinance API(fetch_finviz_details_bulk)가 완전 대체
    kavout_output_data: dict = {}  # 시가총액 맵에서 참조되는 변수 — 빈 dict로 유지

    # ── Step 2: K어닝콜 LLM 분석 ─────────────────────────────
    print("\n▶ Step 2: K어닝콜 LLM 분석...")
    earnings_force_refresh = refresh_earnings
    earnings_analyses: dict = {}
    earnings_raw: dict = {}   # {ticker: EarningsAnalysis} — 노트용 원문
    if k_earnings.exists():
        try:
            today_path = k_earnings_today if k_earnings_today.exists() else None
            # raw 텍스트 (토큰 0 — 파일 읽기만)
            raw_list = parse_earnings(k_earnings, today_path)
            earnings_raw = {ea.ticker: ea for ea in raw_list}
            earnings_analyses = await analyze_earnings(
                earnings_analysis_path=k_earnings,
                earnings_today_path=today_path,
                force_refresh=earnings_force_refresh,
            )
            print(f"  ✓ {len(earnings_analyses)}개 K어닝콜 분석 완료 (원문 {len(earnings_raw)}개 로드)")
        except Exception as exc:
            print(f"  △ K어닝콜 분석 실패 (스코어링은 계속): {exc}")
    else:
        print(f"  △ K어닝 분석.md 없음 — 카탈리스트 점수 제외")
        print(f"     경로: {k_earnings}")

    # ── Step 3: 점수화 + 랭킹 ───────────────────────────────
    print("\n▶ Step 3: 종목 점수화 + 랭킹...")
    try:
        ranked = rank_universe(
            finviz_details=finviz_details,
            earnings_analyses=earnings_analyses,
            finviz_rows_meta=meta,
        )
        print(f"  ✓ {len(ranked)}개 랭킹 완료")
    except Exception as exc:
        print(f"  ✗ FATAL — 점수화 실패: {exc}")
        return

    from shared.strategy import MCAP_LARGE_CAP, MCAP_MID_CAP

    # Kavout 고유 필드 채우기 (K-Score, momentum_1m, roe)
    for r in ranked:
        krow = kavout_map.get(r.ticker)
        if krow:
            r.k_score = krow.k_score
            r.momentum_1m = getattr(krow, "momentum_1m", None)
            r.roe = getattr(krow, "roe", None)

    # ── 시가총액 맵 구성 ─────────────────────────────────────
    # 우선순위: API(Yahoo Finance) → kavout CSV
    mcap_map: dict[str, float] = {}
    for ticker in finviz_details:
        fd = finviz_details.get(ticker)
        mc = getattr(fd, "market_cap", None) if fd else None          # 1순위: API
        if mc is None:
            krow = kavout_map.get(ticker)
            mc = getattr(krow, "market_cap_raw", None) if krow else None  # 2순위: kavout CSV
        if mc is not None:
            mcap_map[ticker] = mc

    # ── 티어별 그룹화 (점수 순 정렬 유지) ───────────────────
    # ranked는 이미 점수 내림차순 — 티어 내 점수 순이 자동 보존됨
    large: list = []
    mid:   list = []
    small: list = []

    for r in ranked:
        mc = mcap_map.get(r.ticker)
        if mc is None or mc < MCAP_MID_CAP:
            small.append(r)
        elif mc < MCAP_LARGE_CAP:
            mid.append(r)
        else:
            large.append(r)

    # 티어 내 rank 재부여
    for i, r in enumerate(large, 1): r.rank = i
    for i, r in enumerate(mid,   1): r.rank = i
    for i, r in enumerate(small, 1): r.rank = i

    tiers = {"대형주 ($50B+)": large, "중형주 ($5B~$50B)": mid, "소형주 ($5B 미만 / 시총 미확인)": small}

    duration = round(time.monotonic() - start, 1)

    result = ScreenerResult(
        execution_id=execution_id,
        total_universe=len(finviz_details),
        with_earnings=len(earnings_analyses),
        top10=ranked[:top_n],
        all_results=ranked,
        duration_seconds=duration,
    )

    # ── 터미널 보고서 ────────────────────────────────────────
    _print_report(tiers, mcap_map)

    # ── Obsidian 저장 ────────────────────────────────────────
    print("\n▶ Obsidian 저장...")
    note_content = _format_obsidian_note(result, tiers, mcap_map, earnings_raw, finviz_details, earnings_analyses)
    note_path = f"swing-procedure/screener/kavout/{date.today().isoformat()}.md"
    try:
        await obsidian.write_note(note_path, note_content)
        print(f"  ✓ {note_path}")
    except Exception as exc:
        print(f"  △ Obsidian 저장 실패: {exc}")

    # ── Slack 알림 ───────────────────────────────────────────
    print("\n▶ Slack 전송...")
    try:
        slack_msg = _format_slack_summary(result, tiers, mcap_map)
        await slack._send(cfg.SLACK_CHANNEL_MAIN, slack_msg)
        print(f"  ✓ {cfg.SLACK_CHANNEL_MAIN} 전송 완료")
    except Exception as exc:
        print(f"  △ Slack 전송 실패: {exc}")

    print(f"\n{'='*60}")
    print(f"  Kavout 스크리닝 완료  [{execution_id}]  소요: {duration}초")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────
# 터미널 보고서
# ─────────────────────────────────────────────────────────────

def _print_report(tiers: dict, mcap_map: dict) -> None:
    guidance_map = {"up": "↑상향", "flat": "→유지", "down": "↓하향", "unknown": "?", "": "-"}
    tone_map = {"bullish": "🟢강세", "neutral": "🟡중립", "bearish": "🔴약세", "": "-"}

    def _mc_str(ticker: str) -> str:
        mc = mcap_map.get(ticker)
        if mc is None: return "  -  "
        if mc >= 1e12: return f"{mc/1e12:.1f}T"
        if mc >= 1e9:  return f"{mc/1e9:.0f}B"
        return f"{mc/1e6:.0f}M"

    for tier_name, rows in tiers.items():
        if not rows:
            continue
        print(f"\n  ── {tier_name} ({len(rows)}개) ──")
        print(f"  {'티커':<6} {'점수':>5} {'K':>4} {'시총':>6} {'M':>4} {'F':>4} {'C':>4} {'가격':>8} {'RSI':>5} 가이던스 톤")
        print(f"  {'-'*75}")
        for r in rows:
            k_str = f"{r.k_score:.1f}" if r.k_score is not None else "  - "
            price_str = f"${r.price:.2f}" if r.price else "  -   "
            rsi_str = f"{r.rsi14:.1f}" if r.rsi14 else "  - "
            guidance = guidance_map.get(r.guidance_direction or "", "-")
            tone = tone_map.get(r.mgmt_tone or "", "-")
            print(
                f"  {r.rank:>2}. {r.ticker:<5} {r.total_score:>5.1f} {k_str:>4} "
                f"{_mc_str(r.ticker):>6} "
                f"{r.momentum_score:>4.0f} {r.fundamental_score:>4.0f} {r.catalyst_score:>4.0f} "
                f"{price_str:>8} {rsi_str:>5} {guidance:<6} {tone}"
            )


# ─────────────────────────────────────────────────────────────
# Obsidian 노트 포맷터
# ─────────────────────────────────────────────────────────────

def _format_obsidian_note(
    result: "ScreenerResult",
    tiers: dict,
    mcap_map: dict,
    earnings_raw: dict | None = None,
    finviz_details: dict | None = None,
    earnings_analyses: dict | None = None,
) -> str:
    today = date.today().isoformat()
    earnings_raw = earnings_raw or {}
    finviz_details = finviz_details or {}
    earnings_analyses = earnings_analyses or {}

    _guidance_icon = {"up": "↑상향", "flat": "→유지", "down": "↓하향", "unknown": "?", "": "-"}
    _tone_icon     = {"bullish": "🟢 Bullish", "neutral": "🟡 Neutral", "bearish": "🔴 Bearish", "": "-"}

    def _f(v, fmt=".1f", suffix=""):
        return f"{v:{fmt}}{suffix}" if v is not None else "-"
    def _pct(v):
        return f"{v:+.1f}%" if v is not None else "-"
    def _dollar(v):
        return f"${v:.2f}" if v is not None else "-"
    def _mc(ticker: str) -> str:
        mc = mcap_map.get(ticker)
        if mc is None: return "-"
        if mc >= 1e12: return f"${mc/1e12:.1f}T"
        if mc >= 1e9:  return f"${mc/1e9:.0f}B"
        return f"${mc/1e6:.0f}M"

    def _ticker_block(r, tier_rank: int) -> list[str]:
        """종목 한 개 상세 블록 생성"""
        ea = earnings_raw.get(r.ticker)
        fd = finviz_details.get(r.ticker)
        k_str = f"{r.k_score:.1f}" if r.k_score is not None else "-"
        guidance_str = _guidance_icon.get(r.guidance_direction or "", "-")
        tone_str     = _tone_icon.get(r.mgmt_tone or "", "-")

        blk = [
            f"### {tier_rank}. {r.ticker} — {r.total_score:.1f}점"
            f"  (K={k_str} | 시총 {_mc(r.ticker)} | {guidance_str} | {tone_str})",
        ]
        if r.company:
            blk.append(f"*{r.company}*")
        blk.append("")

        # 투자 근거
        # ea = 원문(EarningsAnalysis), result_ea = LLM 분류 결과(EarningsCallAnalysis)
        result_ea = earnings_analyses.get(r.ticker) if earnings_analyses else None
        if ea:
            if ea.business_model:
                blk += ["**📌 비즈니스 모델**", ea.business_model.strip(), ""]
            if ea.industry:
                blk += ["**🏭 인더스트리**", ea.industry.strip(), ""]
            if ea.strategy_changes:
                blk += ["**🔀 전략·변화 (가이던스 근거)**", ea.strategy_changes.strip(), ""]
            if ea.management_confidence:
                blk += ["**💬 경영진 톤 근거**", ea.management_confidence.strip(), ""]
        else:
            blk += ["*어닝콜 분석 없음*", ""]

        # LLM 판단 근거 인용
        if result_ea and (result_ea.guidance_evidence or result_ea.tone_evidence):
            blk += ["**🔍 LLM 판단 근거 (원문 인용)**", ""]
            if result_ea.guidance_evidence:
                blk.append(f"> 📈 **가이던스 [{guidance_str}]**: {result_ea.guidance_evidence}")
            if result_ea.tone_evidence:
                blk.append(f"> 🗣️ **경영진 톤 [{tone_str}]**: {result_ea.tone_evidence}")
            blk.append("")

        if r.key_risks:
            blk += [f"**⚠️ 주요 리스크**: {' / '.join(r.key_risks)}", ""]

        # 기술적 스냅샷
        sma20  = _pct(fd.sma20_pct  if fd else None)
        sma50  = _pct(fd.sma50_pct  if fd else None)
        sma200 = _pct(fd.sma200_pct if fd else None)
        rvol   = f"{r.rel_volume:.2f}x" if r.rel_volume else "-"
        change = _pct(fd.change_pct if fd else None)
        blk += [
            "**📊 기술적 스냅샷**", "",
            "| 가격 | 등락 | RSI(14) | RVOL | SMA20 | SMA50 | SMA200 |",
            "|------|------|---------|------|-------|-------|--------|",
            f"| {_dollar(r.price)} | {change} | {_f(r.rsi14, '.1f')} | {rvol} "
            f"| {sma20} | {sma50} | {sma200} |",
            "",
        ]

        # 밸류에이션 & 펀더멘털
        if fd:
            analyst_str = "-"
            if any(v is not None for v in [fd.analyst_buy, fd.analyst_hold, fd.analyst_sell]):
                analyst_str = f"B{fd.analyst_buy or 0}/H{fd.analyst_hold or 0}/S{fd.analyst_sell or 0}"
            recom_str = f"{fd.recom:.2f}" if fd.recom is not None else "-"
            blk += [
                "**💰 밸류에이션 & 애널리스트**", "",
                "| Fwd PE | PEG | Beta | 목표가 | 추천등급 | 애널(B/H/S) |",
                "|--------|-----|------|--------|----------|------------|",
                f"| {_f(fd.forward_pe, '.1f')} | {_f(fd.peg, '.2f')} | {_f(fd.beta, '.2f')} "
                f"| {_dollar(fd.target_price)} | {recom_str} | {analyst_str} |",
                "",
                "**📈 펀더멘털**", "",
                "| 영업이익률 | 순이익률 | 매출성장YoY | EPS서프라이즈 | ROE | FCF(TTM) |",
                "|-----------|---------|------------|--------------|-----|---------|",
                f"| {_pct(fd.op_margin_pct)} | {_pct(fd.profit_margin_pct)} "
                f"| {_pct(fd.revenue_growth_yoy)} | {_pct(fd.eps_surprise_pct)} "
                f"| {_pct(fd.roe_pct)} | {_f(fd.fcf_ttm, '.0f', 'M') if fd.fcf_ttm else '-'} |",
                "",
            ]
        else:
            blk += ["*밸류에이션·펀더멘털 데이터 없음*", ""]

        blk += [
            f"**🏆 점수**: 모멘텀 {r.momentum_score:.0f} | "
            f"펀더멘털 {r.fundamental_score:.0f} | "
            f"카탈리스트 {r.catalyst_score:.0f} | "
            f"**합계 {r.total_score:.1f}**",
            "", "---", "",
        ]
        return blk

    # ── 헤더 ─────────────────────────────────────────────────────
    total_tickers = sum(len(v) for v in tiers.values())
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"Kavout 펀더멘털 스크리닝 — {today}",
        f"실행ID: `{result.execution_id}`  |  **분석 시각:** {now_str}  |  유니버스: {result.total_universe}개  |  "
        f"어닝콜 보유: {result.with_earnings}개  |  소요: {result.duration_seconds:.1f}초",
        "",
        "---",
        "",
    ]

    # ── 티어별 섹션 ──────────────────────────────────────────────
    for tier_name, rows in tiers.items():
        if not rows:
            continue
        lines += [f"## {tier_name}  ({len(rows)}개)", ""]
        for tier_rank, r in enumerate(rows, 1):
            lines += _ticker_block(r, tier_rank)

    lines += [f"*생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Slack 요약 포맷터
# ─────────────────────────────────────────────────────────────

def _format_slack_summary(result: "ScreenerResult", tiers: dict, mcap_map: dict) -> str:
    today = date.today().isoformat()
    medals = ["🥇", "🥈", "🥉"]
    guidance_map = {"up": "↑상향", "flat": "→유지", "down": "↓하향", "unknown": "", "": ""}

    def _mc(ticker: str) -> str:
        mc = mcap_map.get(ticker)
        if mc is None: return ""
        if mc >= 1e12: return f"${mc/1e12:.1f}T"
        if mc >= 1e9:  return f"${mc/1e9:.0f}B"
        return f"${mc/1e6:.0f}M"

    lines = [f"*📊 Kavout 스크리닝 — {today}*"]
    lines.append(f"유니버스 {result.total_universe}개 | 어닝콜 {result.with_earnings}개 | {result.duration_seconds:.0f}초")

    tier_labels = {"대형주 ($50B+)": "🏦 대형주", "중형주 ($5B~$50B)": "🏢 중형주", "소형주 ($5B 미만 / 시총 미확인)": "🏠 소형주"}

    for tier_name, rows in tiers.items():
        if not rows:
            continue
        label = tier_labels.get(tier_name, tier_name)
        lines += ["", f"*{label} Top 3:*"]
        for medal, r in zip(medals, rows[:3]):
            guidance = guidance_map.get(r.guidance_direction or "", "")
            k_tag = f" K={r.k_score:.1f}" if r.k_score is not None else ""
            mc_tag = f" {_mc(r.ticker)}" if _mc(r.ticker) else ""
            lines.append(
                f"{medal} `{r.ticker}`{mc_tag} — {r.total_score:.1f}점 {guidance}{k_tag}"
            )

    lines.append(f"\n_소요시간: {result.duration_seconds:.0f}초_")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# CLI 진입점
# ─────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SwingMCP Kavout AI 유니버스 스크리닝 + K어닝콜 LLM 분석"
    )
    parser.add_argument(
        "--refresh-earnings", action="store_true",
        help="어닝 LLM 캐시 무시하고 새로 분석 (기본: 캐시 재사용)"
    )
    parser.add_argument(
        "--top", type=int, default=10, metavar="N",
        help="보고서 상위 N개 출력 (기본: 10)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    args = _parse_args()
    asyncio.run(run(
        refresh_earnings=args.refresh_earnings,
        top_n=args.top,
    ))
