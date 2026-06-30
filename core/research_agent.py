"""
core/research_agent.py
======================
포지션 관련 리서치 유틸리티

포함 기능:
  run_drop_research()      : 포지션 급락 원인 분석 (position_monitor 사용)

데이터 수집 헬퍼 (buy_steps Step 5에서 직접 호출):
  _fetch_stock_snapshot()  : yfinance 주식 지표
  _fetch_options_snapshot(): yfinance ATM 스트래들·IV·내재 이동폭
  _fetch_earnings_data()   : yfinance 실적 추정치·서프라이즈 히스토리
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Optional

from shared.config import get_config
from shared.logger import get_logger
from core.llm import call_llm, call_ddg_search, call_brave_search, rank_and_pick, fetch_url_as_markdown

log = get_logger()
cfg = get_config()


# ─────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────

async def _llm(prompt: str, max_tokens: int = 4096) -> str:
    """RESEARCH_MODEL로 단순 텍스트 응답 요청."""
    resp = await call_llm(
        messages=[{"role": "user", "content": prompt}],
        model=cfg.RESEARCH_MODEL,
        temperature=0.0,
        max_tokens=max_tokens,
    )
    return resp.content.strip()


def _unwrap_json(text: str) -> str:
    """코드블록만 제거. JSON 평탄화는 하지 않음 — continuation 기법으로 JSON 자체를 방지."""
    import re
    stripped = text.strip()
    code_block = re.match(r"^```(?:json|markdown)?\s*([\s\S]*?)```\s*$", stripped)
    if code_block:
        stripped = code_block.group(1).strip()
    return stripped


def _fetch_stock_snapshot_sync(ticker: str) -> str:
    """yfinance로 핵심 정량 지표 수집 (동기). 실패 시 빈 문자열."""
    try:
        import yfinance as yf  # type: ignore
        tk = yf.Ticker(ticker)
        info = tk.fast_info
        full = tk.info

        lines: list[str] = []

        def add(label: str, val, fmt: str = "{}"):
            if val is not None:
                try:
                    lines.append(f"- {label}: {fmt.format(val)}")
                except Exception:
                    pass

        current = info.get("last_price") or full.get("currentPrice")
        add("현재가", current, "${:.2f}")
        add("전일 종가", full.get("previousClose"), "${:.2f}")

        high52 = info.get("year_high") or full.get("fiftyTwoWeekHigh")
        low52  = info.get("year_low")  or full.get("fiftyTwoWeekLow")
        add("52주 최고", high52, "${:.2f}")
        add("52주 최저", low52, "${:.2f}")
        # 52주 범위 내 현재 위치
        if current and high52 and low52 and high52 > low52:
            pct_in_range = (current - low52) / (high52 - low52) * 100
            flag = " ⚠️ 고점권" if pct_in_range >= 80 else (" 🟢 저점권" if pct_in_range <= 20 else "")
            lines.append(f"- 52주 범위 위치: {pct_in_range:.0f}%{flag}")

        mc = full.get("marketCap")
        if mc is not None:
            if mc >= 1e12:
                lines.append(f"- 시가총액: ${mc/1e12:.2f}T")
            elif mc >= 1e9:
                lines.append(f"- 시가총액: ${mc/1e9:.1f}B")
            else:
                lines.append(f"- 시가총액: ${mc:,.0f}")
        add("PER (Forward)", full.get("forwardPE"), "{:.1f}x")
        add("PER (Trailing)", full.get("trailingPE"), "{:.1f}x")
        add("베타", full.get("beta"), "{:.2f}")
        add("배당수익률", full.get("dividendYield"), "{:.2%}")

        # 애널리스트 목표가 + 업사이드 %
        target_mean = full.get("targetMeanPrice")
        target_high = full.get("targetHighPrice")
        target_low  = full.get("targetLowPrice")
        if target_mean is not None:
            upside = f" ({(target_mean/current - 1)*100:+.1f}%)" if current else ""
            lines.append(f"- 애널리스트 목표가 (평균): ${target_mean:.2f}{upside}")
        if target_high is not None:
            lines.append(f"- 애널리스트 목표가 (최고): ${target_high:.2f}")
        if target_low is not None:
            lines.append(f"- 애널리스트 목표가 (최저): ${target_low:.2f}")
        add("애널리스트 수", full.get("numberOfAnalystOpinions"))
        add("추천 등급", full.get("recommendationKey"))

        # 추천 Buy/Hold/Sell 비율 (recommendations_summary 0m 기준)
        try:
            rec = tk.recommendations_summary
            if rec is not None and not rec.empty:
                cur = rec[rec["period"] == "0m"]
                if not cur.empty:
                    row = cur.iloc[0]
                    n_buy = int(row.get("strongBuy", 0)) + int(row.get("buy", 0))
                    n_hold = int(row.get("hold", 0))
                    n_sell = int(row.get("sell", 0)) + int(row.get("strongSell", 0))
                    total = n_buy + n_hold + n_sell
                    if total > 0:
                        lines.append(
                            f"- 추천 비율: Buy {n_buy/total*100:.0f}%"
                            f" / Hold {n_hold/total*100:.0f}%"
                            f" / Sell {n_sell/total*100:.0f}% ({total}명)"
                        )
        except Exception:
            pass

        add("공매도 커버 일수", full.get("shortRatio"), "{:.1f}일")
        add("거래량", full.get("volume"), "{:,}")
        add("평균 거래량(10일)", full.get("averageVolume10days"), "{:,}")

        return "\n".join(lines)
    except Exception as exc:
        log.debug("stock_snapshot_fail", ticker=ticker, error=str(exc))
        return ""


def _fetch_options_snapshot_sync(ticker: str) -> str:
    """ATM 스트래들로 옵션 내재 이동폭 계산 (동기). 실패 시 빈 문자열."""
    try:
        import yfinance as yf  # type: ignore
        tk = yf.Ticker(ticker)
        expirations = tk.options
        if not expirations:
            return ""

        current_price = tk.fast_info.get("last_price") or tk.info.get("currentPrice")
        if not current_price:
            return ""

        # 최소 7일 이후 만기 선택 (단기 만기는 IV가 의미 없음)
        from datetime import date as _date, timedelta
        min_date = _date.today() + timedelta(days=7)
        exp = next(
            (e for e in expirations if _date.fromisoformat(e) >= min_date),
            expirations[0],
        )
        chain = tk.option_chain(exp)
        calls = chain.calls.copy()
        puts = chain.puts.copy()

        calls["dist"] = abs(calls["strike"] - current_price)
        puts["dist"] = abs(puts["strike"] - current_price)

        atm_call = calls.nsmallest(1, "dist").iloc[0]
        atm_put = puts.nsmallest(1, "dist").iloc[0]

        straddle = float(atm_call["lastPrice"]) + float(atm_put["lastPrice"])
        implied_move_pct = straddle / current_price * 100

        iv_call = float(atm_call.get("impliedVolatility", 0))
        iv_put = float(atm_put.get("impliedVolatility", 0))
        atm_iv = (iv_call + iv_put) / 2 if (iv_call + iv_put) > 0 else 0

        # 장외 stale IV → HV proxy (api_fetcher 와 동일 방식)
        import math as _math
        hv_proxy: float | None = None
        try:
            _hist = tk.history(period="30d", auto_adjust=True)
            if _hist is not None and not _hist.empty:
                _closes = _hist["Close"].dropna().tolist()
                if len(_closes) >= 20:
                    _rets = [_math.log(_closes[i] / _closes[i-1]) for i in range(1, len(_closes))]
                    import statistics as _stats
                    hv_proxy = _math.sqrt(252) * _stats.stdev(_rets[-20:])
        except Exception:
            pass

        iv_estimated = False
        if atm_iv < 0.01 and hv_proxy:  # stale IV → HV 대체
            atm_iv = hv_proxy
            iv_estimated = True

        breakeven_down = current_price - straddle
        breakeven_up = current_price + straddle

        lines = [
            f"- 기준 만기: {exp}",
            f"- ATM 스트라이크: ${atm_call['strike']:.0f}",
            f"- ATM 스트래들 가격: ${straddle:.2f}",
            f"- 옵션 내재 이동폭: ±{implied_move_pct:.1f}%",
            f"- 손익분기: ${breakeven_down:.2f} (하방) / ${breakeven_up:.2f} (상방)",
        ]
        if atm_iv > 0.01:
            label = "ATM IV (HV 추정, 장외)" if iv_estimated else "ATM 내재변동성(IV)"
            lines.append(f"- {label}: {atm_iv:.1%}")
        return "\n".join(lines)
    except Exception as exc:
        log.debug("options_snapshot_fail", ticker=ticker, error=str(exc))
        return ""


def _fetch_earnings_data_sync(ticker: str) -> str:
    """yfinance로 실적 이력(EPS 서프라이즈+주가 반응 통합)·추정치 수집 (동기)."""
    try:
        import yfinance as yf  # type: ignore
        import pandas as _pd  # type: ignore
        tk = yf.Ticker(ticker)
        lines: list[str] = []

        # 실적 발표 캘린더 (다음 발표일)
        try:
            cal = tk.calendar
            if cal is not None and not cal.empty:
                for col in cal.columns:
                    val = cal[col].iloc[0]
                    if val is not None:
                        lines.append(f"- {col}: {val}")
        except Exception:
            pass

        # EPS 서프라이즈 + 주가 반응 통합 테이블
        # earnings_history: 분기말 날짜(인덱스), 오름차순 → 뒤집어서 최신순
        # earnings_dates: 실제 발표일(인덱스), 내림차순(최신순)
        try:
            hist = tk.earnings_history
            ed = tk.earnings_dates
            price_hist = tk.history(period="2y")

            if hist is not None and not hist.empty:
                price_hist.index = price_hist.index.tz_localize(None)
                # 최신순으로 정렬 (head(4)는 오름차순이므로 tail 후 역순)
                hist_recent = hist.tail(4).iloc[::-1]

                # earnings_dates에서 발표일→가격 이동폭 구성 (과거 날짜만)
                today_ts = _pd.Timestamp.today().tz_localize(None)
                move_by_pos: list[tuple[str, float]] = []  # (발표일, 이동폭)
                if ed is not None and not ed.empty:
                    past_dates = [dt for dt in ed.index
                                  if _pd.Timestamp(dt).tz_localize(None) <= today_ts]
                    for earn_dt in past_dates[:len(hist_recent)]:
                        earn_ts = _pd.Timestamp(earn_dt).tz_localize(None)
                        before = price_hist[price_hist.index < earn_ts]
                        after = price_hist[price_hist.index >= earn_ts]
                        if before.empty or after.empty:
                            move_by_pos.append((earn_ts.strftime("%Y-%m-%d"), float("nan")))
                            continue
                        prev_close = float(before.iloc[-1]["Close"])
                        next_open = float(after.iloc[0]["Open"])
                        move = (next_open - prev_close) / prev_close * 100
                        move_by_pos.append((earn_ts.strftime("%Y-%m-%d"), move))

                lines.append("\n[실적 이력 — EPS 서프라이즈 & 주가 반응 (최신순)]")
                for i, (q_ts, row) in enumerate(hist_recent.iterrows()):
                    est = row.get("epsEstimate")
                    act = row.get("epsActual")
                    if est is None or act is None:
                        continue
                    surprise = ((act - est) / abs(est) * 100) if est != 0 else 0
                    surprise_str = f"{surprise:+.1f}%"

                    if i < len(move_by_pos):
                        ann_date, move = move_by_pos[i]
                        import math
                        if not math.isnan(move):
                            move_str = f"{move:+.1f}%"
                            # 역방향 반응 경고 (큰 beat인데 하락)
                            if surprise > 10 and move < 0:
                                move_str += " ⚠️ 역방향"
                        else:
                            move_str = "N/A"
                        lines.append(
                            f"  발표 {ann_date}: EPS 추정 ${est:.2f} → 실제 ${act:.2f}"
                            f" (서프라이즈 {surprise_str}) | 주가 {move_str}"
                        )
                    else:
                        lines.append(
                            f"  EPS 추정 ${est:.2f} → 실제 ${act:.2f} (서프라이즈 {surprise_str})"
                        )
        except Exception:
            pass

        # EPS 추정치 (기간 레이블 한글화)
        _period_label = {
            "0q": "이번 분기", "+1q": "다음 분기",
            "0y": "이번 연도", "+1y": "다음 연도",
        }
        try:
            ee = tk.earnings_estimate
            if ee is not None and not ee.empty:
                lines.append("\n[EPS 컨센서스]")
                for idx in ee.index[:4]:
                    row = ee.loc[idx]
                    avg = row.get("avg")
                    low_ = row.get("low")
                    high_ = row.get("high")
                    if avg is not None:
                        label = _period_label.get(str(idx), str(idx))
                        lines.append(f"  {label}: 평균 ${avg:.2f} (범위: ${low_:.2f}~${high_:.2f})")
        except Exception:
            pass

        # 매출 추정치 (범위 포함)
        try:
            re_ = tk.revenue_estimate
            if re_ is not None and not re_.empty:
                lines.append("\n[매출 컨센서스]")
                for idx in re_.index[:2]:
                    row = re_.loc[idx]
                    avg = row.get("avg")
                    low_ = row.get("low")
                    high_ = row.get("high")
                    if avg is not None:
                        def _fmt(v):
                            if v is None:
                                return "N/A"
                            return f"${v/1e9:.1f}B" if v >= 1e9 else f"${v/1e6:.1f}M"
                        label = _period_label.get(str(idx), str(idx))
                        range_str = f" (범위: {_fmt(low_)}~{_fmt(high_)})" if low_ and high_ else ""
                        lines.append(f"  {label}: 평균 {_fmt(avg)}{range_str}")
        except Exception:
            pass

        return "\n".join(lines) if lines else "yfinance 실적 데이터 없음"
    except Exception as exc:
        log.debug("earnings_data_fail", ticker=ticker, error=str(exc))
        return "yfinance 실적 데이터 없음"


async def _fetch_stock_snapshot(ticker: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_stock_snapshot_sync, ticker)


async def _fetch_options_snapshot(ticker: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_options_snapshot_sync, ticker)


async def _fetch_earnings_data(ticker: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_earnings_data_sync, ticker)


async def _search_and_fetch(
    queries: list[str],
    context: str,
    fetch_top_n: int = 5,
) -> list[dict]:
    """복수 쿼리 병렬 검색 → rank_and_pick → fetch_url_as_markdown."""
    # 병렬 검색
    raw_results: list[dict] = []
    search_tasks = [call_ddg_search(q, num_results=10) for q in queries]
    results = await asyncio.gather(*search_tasks, return_exceptions=True)
    seen_urls: set[str] = set()
    for batch in results:
        if isinstance(batch, list):
            for item in batch:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    raw_results.append(item)

    if not raw_results:
        return []

    # Brave 보완 (API 키 있을 때만)
    if cfg.BRAVE_API_KEY:
        try:
            brave_res = await call_brave_search(queries[0], count=5)
            for item in brave_res:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    raw_results.append(item)
        except Exception as exc:
            log.debug("brave_search_skip", error=str(exc))

    # 품질 선별
    top_items = await rank_and_pick(raw_results, context=context, top_n=fetch_top_n)

    # 본문 fetch
    fetch_tasks = [fetch_url_as_markdown(item.get("url", ""), max_chars=4000) for item in top_items]
    bodies = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    fetched: list[dict] = []
    for item, body in zip(top_items, bodies):
        if isinstance(body, str) and body:
            fetched.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "content": body,
            })
    return fetched


async def _save_to_obsidian(path: str, content: str) -> None:
    """Obsidian REST API로 노트 저장. 실패해도 예외 전파하지 않음."""
    try:
        from core.obsidian import ObsidianClient
        obs = ObsidianClient()
        await obs.write_note(path, content)
        log.info("research_saved", path=path)
    except Exception as exc:
        log.warning("research_obsidian_fail", path=path, error=str(exc))


def _build_report_header(ticker: str, report_type: str) -> str:
    today = date.today().isoformat()
    return f"# {report_type}: {ticker}\n\n> 생성일: {today} | 모델: {cfg.RESEARCH_MODEL}\n\n"


# ─────────────────────────────────────────────────────────────
# 포지션 급락 원인 분석
# ─────────────────────────────────────────────────────────────

async def run_drop_research(
    ticker: str,
    pct_change: float,
    entry_price: Optional[float] = None,
) -> str:
    """
    장중 급락 종목 원인 분석.

    Args:
        ticker: 종목 심볼
        pct_change: 등락률 (음수, 예: -4.2)
        entry_price: 진입 가격 (없으면 None)

    Returns:
        원인 분석 보고서 마크다운
    """
    today = date.today().isoformat()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    log.info("drop_research_start", ticker=ticker, pct_change=pct_change)

    entry_hint = f" (진입가: ${entry_price:.2f})" if entry_price is not None else ""

    queries = [
        f"{ticker} stock drop today reason {today}",
        f"{ticker} news catalyst sell-off {today}",
        f"{ticker} earnings guidance warning downgrade {today}",
    ]

    fetched = await _search_and_fetch(
        queries,
        context=f"{ticker} stock drop {pct_change:.1f}% today — cause analysis",
        fetch_top_n=4,
    )
    log.info("drop_research_fetch", ticker=ticker, count=len(fetched))

    if not fetched:
        verdict = "판단 불가"
        synth_md = (
            f"## 낙폭 원인 분석\n"
            f"검색된 관련 뉴스가 없습니다. "
            f"오늘({today}) {ticker}의 급락 원인에 대한 언론 보도가 아직 없거나, "
            f"실제 급락이 발생하지 않은 상태일 수 있습니다.\n\n"
            f"## 관련 뉴스 요약\n없음\n\n"
            f"## 판단: 판단 불가\n\n"
            f"## 대응 시사점\n정보 부족으로 판단 보류. 직접 뉴스 확인 후 대응 결정 권장.\n"
        )
    else:
        sources_text = "\n\n---\n\n".join(
            f"[{d['title']}]\n{d['content'][:1500]}" for d in fetched
        )
        # JSON으로 추출 → Python이 마크다운 포맷
        extract_prompt = (
            f"{ticker} dropped {pct_change:.1f}% today ({now_str}){entry_hint}.\n"
            f"From the sources below, extract information about this stock drop.\n"
            f"Return ONLY a JSON object with exactly these 5 Korean string values:\n\n"
            f'{{"cause": "...", "news_summary": "...", "verdict": "정상 조정 OR 실질 악재 OR 판단 불가", '
            f'"verdict_reason": "...", "action": "..."}}\n\n'
            f"Rules: verdict must be exactly one of the 3 options. Korean strings. No extra keys.\n\n"
            f"SOURCES:\n{sources_text}"
        )
        raw = await _llm(extract_prompt, max_tokens=1000)
        import json as _json, re as _re

        # JSON 파싱
        try:
            m = _re.search(r"\{[\s\S]+\}", raw)
            if m:
                data = _json.loads(m.group())
                verdict = data.get("verdict", "판단 불가")
                synth_md = (
                    f"## 낙폭 원인 분석\n{data.get('cause', '소스에서 확인되지 않음')}\n\n"
                    f"## 관련 뉴스 요약\n{data.get('news_summary', '없음')}\n\n"
                    f"## 판단: {verdict}\n{data.get('verdict_reason', '')}\n\n"
                    f"## 대응 시사점\n{data.get('action', '')}\n"
                )
            else:
                raise ValueError("no JSON found")
        except Exception:
            # 파싱 실패 시 raw를 그대로 사용 + 섹션 보완
            raw_clean = _unwrap_json(raw)
            if "## 낙폭 원인 분석" not in raw_clean:
                raw_clean = "## 낙폭 원인 분석\n" + raw_clean
            synth_md = raw_clean

        # verdict가 JSON 파싱에서 설정되지 않았으면 텍스트에서 추출
        if not isinstance(locals().get("verdict"), str):
            if "실질 악재" in synth_md:
                verdict = "실질 악재"
            elif "정상 조정" in synth_md:
                verdict = "정상 조정"
            else:
                verdict = "판단 불가"

    report = _build_report_header(ticker, f"🚨 급락 분석 ({pct_change:.1f}%)")
    report += f"**판단: {verdict}**\n\n"
    report += synth_md
    report += f"\n\n---\n*분석 시각: {now_str} | 소스: {len(fetched)}개*\n"

    obs_path = f"Research/Alert_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    await _save_to_obsidian(obs_path, report)

    log.info("drop_research_done", ticker=ticker, verdict=verdict)
    return report
