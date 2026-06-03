"""
core/api_fetcher.py
===================
Kavout 유니버스 티커 → yfinance API → FinvizDetail 완전 채우기

Yahoo Finance(yfinance) 단일 소스:
  - Ticker.info  : PE, margins, growth, analyst target/recom, beta, 52W
  - Ticker.history: OHLC 1년치 → RSI(14) / RVOL / SMA20·50·200 직접 계산
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import numpy as np

from shared.schemas import FinvizDetail

log = logging.getLogger(__name__)


# ─── 기술지표 계산 헬퍼 ────────────────────────────────────────────────────

def _calc_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    arr = np.array(closes, dtype=float)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[-period:]))
    avg_loss = float(np.mean(losses[-period:]))
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _calc_sma_pct(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    sma = float(np.mean(closes[-period:]))
    curr = float(closes[-1])
    if sma == 0:
        return None
    return round((curr - sma) / sma * 100, 2)


def _calc_rvol(volumes: list[float], lookback: int = 20) -> Optional[float]:
    """오늘 거래량 / 직전 lookback일 평균"""
    if len(volumes) < lookback + 1:
        return None
    avg = float(np.mean(volumes[-(lookback + 1):-1]))
    if avg == 0:
        return None
    return round(float(volumes[-1]) / avg, 2)


def _calc_sma_val(closes: list[float], period: int) -> Optional[float]:
    """SMA 달러값 반환"""
    if len(closes) < period:
        return None
    return round(float(np.mean(closes[-period:])), 2)


def _ema(arr: list[float], period: int) -> list[float]:
    """EMA 시리즈 계산 (단순 초기값 = 첫 period개 평균)"""
    if len(arr) < period:
        return []
    k = 2.0 / (period + 1)
    result: list[float] = [float(np.mean(arr[:period]))]
    for v in arr[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _calc_bollinger(closes: list[float], period: int = 20, num_std: float = 2.0
                    ) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """볼린저밴드 (upper, mid, lower) 반환"""
    if len(closes) < period:
        return None, None, None
    arr = np.array(closes[-period:], dtype=float)
    mid = float(np.mean(arr))
    std = float(np.std(arr, ddof=0))
    return round(mid + num_std * std, 2), round(mid, 2), round(mid - num_std * std, 2)


def _calc_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
               ) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """MACD 라인, 시그널, 히스토그램 반환"""
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    # 길이 맞추기 (ema_slow 가 더 짧음)
    offset = len(ema_fast) - len(ema_slow)
    macd_series = [f - s for f, s in zip(ema_fast[offset:], ema_slow)]
    if len(macd_series) < signal:
        return None, None, None
    sig_series = _ema(macd_series, signal)
    if not sig_series:
        return None, None, None
    macd_val = round(macd_series[-1], 4)
    sig_val = round(sig_series[-1], 4)
    hist_val = round(macd_val - sig_val, 4)
    return macd_val, sig_val, hist_val


def _calc_adx(highs: list[float], lows: list[float], closes: list[float],
              period: int = 14) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """ADX, DI+, DI- 반환"""
    n = min(len(highs), len(lows), len(closes))
    if n < period * 2 + 1:
        return None, None, None
    highs = highs[-n:]; lows = lows[-n:]; closes = closes[-n:]
    tr_list, dmp_list, dmn_list = [], [], []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        dmp_list.append(up if up > dn and up > 0 else 0.0)
        dmn_list.append(dn if dn > up and dn > 0 else 0.0)
    atr14 = _ema(tr_list, period)
    dmp14 = _ema(dmp_list, period)
    dmn14 = _ema(dmn_list, period)
    if not atr14 or atr14[-1] == 0:
        return None, None, None
    di_p = round(100 * dmp14[-1] / atr14[-1], 2)
    di_n = round(100 * dmn14[-1] / atr14[-1], 2)
    dx_list = []
    for a, p, nv in zip(atr14, dmp14, dmn14):
        if a == 0:
            continue
        dip = 100 * p / a; din = 100 * nv / a
        denom = dip + din
        dx_list.append(abs(dip - din) / denom * 100 if denom else 0.0)
    if len(dx_list) < period:
        return None, di_p, di_n
    adx_val = round(_ema(dx_list, period)[-1], 2)
    return adx_val, di_p, di_n


def _calc_atr(highs: list[float], lows: list[float], closes: list[float],
              period: int = 14) -> Optional[float]:
    """ATR(14) 달러값 반환"""
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    highs = highs[-n:]; lows = lows[-n:]; closes = closes[-n:]
    tr_list = []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
    ema_atr = _ema(tr_list, period)
    return round(ema_atr[-1], 2) if ema_atr else None


def _calc_pivot(highs: list[float], lows: list[float], closes: list[float]
                ) -> tuple[Optional[float], ...]:
    """전일 피벗 포인트 (pivot, r1, r2, s1, s2) 반환"""
    if len(highs) < 2:
        return (None,) * 5
    h, l, c = highs[-2], lows[-2], closes[-2]   # 전일 데이터
    p = (h + l + c) / 3
    r1 = 2 * p - l;  r2 = p + (h - l)
    s1 = 2 * p - h;  s2 = p - (h - l)
    return (round(p, 2), round(r1, 2), round(r2, 2), round(s1, 2), round(s2, 2))


# ─── 타입 변환 헬퍼 ────────────────────────────────────────────────────────

def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _pct(v) -> Optional[float]:
    """decimal(0.xx) → %(xx.xx)"""
    f = _f(v)
    return round(f * 100, 2) if f is not None else None


def _million(v) -> Optional[float]:
    """raw USD → M USD"""
    f = _f(v)
    return round(f / 1_000_000, 2) if f is not None else None


# ─── 단일 티커 fetch (동기) ───────────────────────────────────────────────

def fetch_finviz_detail(ticker: str, sleep_sec: float = 0.5) -> FinvizDetail:
    """yfinance로 단일 티커 FinvizDetail 완전 채우기 (동기, thread-safe)"""
    import yfinance as yf

    try:
        t = yf.Ticker(ticker)
        info: dict = t.info or {}

        # ── OHLC 1년치 ──
        hist = t.history(period="1y", auto_adjust=True)
        closes: list[float] = []
        volumes: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        if hist is not None and not hist.empty:
            closes = hist["Close"].dropna().tolist()
            volumes = hist["Volume"].dropna().tolist()
            highs = hist["High"].dropna().tolist()
            lows = hist["Low"].dropna().tolist()

        # ── 가격 / 등락 ──
        price = _f(info.get("currentPrice") or info.get("regularMarketPrice"))
        prev_close = _f(info.get("regularMarketPreviousClose"))
        change_pct: Optional[float] = None
        if price is not None and prev_close and prev_close != 0:
            change_pct = round((price - prev_close) / prev_close * 100, 2)

        # ── 기술지표 (기존) ──
        rsi14 = _calc_rsi(closes)
        rvol = _calc_rvol(volumes)
        sma20_pct = _calc_sma_pct(closes, 20)
        sma50_pct = _calc_sma_pct(closes, 50)
        sma200_pct = _calc_sma_pct(closes, 200)

        # ── SMA 달러값 ──
        sma5_val = _calc_sma_val(closes, 5)
        sma20_val = _calc_sma_val(closes, 20)
        sma50_val = _calc_sma_val(closes, 50)
        sma200_val = _calc_sma_val(closes, 200)

        # ── 볼린저밴드 ──
        bb_upper, bb_mid, bb_lower = _calc_bollinger(closes)

        # ── MACD ──
        macd_line, macd_signal, macd_hist = _calc_macd(closes)

        # ── ADX / DI ──
        adx_val, di_plus, di_minus = _calc_adx(highs, lows, closes)

        # ── ATR ──
        atr_val = _calc_atr(highs, lows, closes)

        # ── 피벗 포인트 ──
        pivot_val, piv_r1, piv_r2, piv_s1, piv_s2 = _calc_pivot(highs, lows, closes)

        # ── 52주 위치 ──
        w52_high = _f(info.get("fiftyTwoWeekHigh"))
        w52_low = _f(info.get("fiftyTwoWeekLow"))
        w52_high_pct: Optional[float] = None
        w52_low_pct: Optional[float] = None
        if price is not None and w52_high and w52_high != 0:
            w52_high_pct = round((price - w52_high) / w52_high * 100, 2)
        if price is not None and w52_low and w52_low != 0:
            w52_low_pct = round((price - w52_low) / w52_low * 100, 2)

        # ── 밸류에이션 ──
        forward_pe = _f(info.get("forwardPE"))
        peg = _f(info.get("trailingPegRatio") or info.get("pegRatio"))
        beta = _f(info.get("beta"))
        # analyst_price_targets['current'] = 가장 최근 컨센서스 타깃 (mean보다 최신)
        try:
            apt = t.analyst_price_targets or {}
            target_price = _f(apt.get("current") or apt.get("mean") or info.get("targetMeanPrice"))
        except Exception:
            target_price = _f(info.get("targetMeanPrice"))
        recom = _f(info.get("recommendationMean"))  # 1=Strong Buy … 5=Sell

        # ── 공매도 ──
        short_float_pct = _pct(info.get("shortPercentOfFloat"))

        # ── 마진 (yfinance: decimal → %) ──
        gross_margin_pct = _pct(info.get("grossMargins"))
        op_margin_pct = _pct(info.get("operatingMargins"))
        profit_margin_pct = _pct(info.get("profitMargins"))

        # ── 성장률 (decimal → %) ──
        revenue_growth_yoy = _pct(info.get("revenueGrowth"))
        net_income_growth_yoy = _pct(info.get("earningsGrowth"))
        eps_next_5y_pct = _pct(info.get("earningsQuarterlyGrowth"))

        # ── 손익 (raw USD → M USD) ──
        revenue_ttm = _million(info.get("totalRevenue"))
        gross_profit_ttm = _million(info.get("grossProfits"))
        net_income_ttm = _million(info.get("netIncomeToCommon"))

        # ── EPS 서프라이즈 (최근 분기) ──
        eps_surprise_pct: Optional[float] = None
        try:
            eh = t.earnings_history
            if eh is not None and not eh.empty:
                row = eh.iloc[0]
                actual = _f(row.get("epsActual"))
                est = _f(row.get("epsEstimate"))
                if actual is not None and est is not None and est != 0:
                    eps_surprise_pct = round((actual - est) / abs(est) * 100, 2)
        except Exception as e:
            log.debug("%s earnings_history 실패: %s", ticker, e)

        # ── 애널리스트 의견 집계 ──
        analyst_buy: Optional[int] = None
        analyst_hold: Optional[int] = None
        analyst_sell: Optional[int] = None
        try:
            rec_sum = t.recommendations_summary
            if rec_sum is not None and not rec_sum.empty:
                # 컬럼: period, strongBuy, buy, hold, sell, strongSell
                latest = rec_sum.iloc[0]
                strong_buy = int(latest.get("strongBuy", 0) or 0)
                buy_cnt = int(latest.get("buy", 0) or 0)
                hold_cnt = int(latest.get("hold", 0) or 0)
                sell_cnt = int(latest.get("sell", 0) or 0)
                strong_sell = int(latest.get("strongSell", 0) or 0)
                analyst_buy = strong_buy + buy_cnt
                analyst_hold = hold_cnt
                analyst_sell = sell_cnt + strong_sell
        except Exception as e:
            log.debug("%s recommendations_summary 실패: %s", ticker, e)

        # ── 폴백: recommendations 전체 테이블 → 최근 항목 집계 ──
        if analyst_buy is None:
            try:
                recs = t.recommendations
                if recs is not None and not recs.empty:
                    # 최근 80개 등급 변경 집계 (To Grade 기준)
                    recent = recs.tail(80)
                    to_grades = recent.get("To Grade", recent.get("toGrade", None))
                    if to_grades is not None:
                        grades_str = to_grades.fillna("").str.lower()
                        analyst_buy = int(grades_str.str.contains(
                            "buy|outperform|overweight|strong buy|accumulate", na=False).sum())
                        analyst_hold = int(grades_str.str.contains(
                            "hold|neutral|market perform|equal.weight|in.line", na=False).sum())
                        analyst_sell = int(grades_str.str.contains(
                            "sell|underperform|underweight|reduce|strong sell", na=False).sum())
                        log.debug("%s analyst fallback 집계: B%s H%s S%s",
                                  ticker, analyst_buy, analyst_hold, analyst_sell)
            except Exception as e:
                log.debug("%s recommendations 폴백 실패: %s", ticker, e)

        # ── 추가 펀더멘털 ──
        trailing_pe = _f(info.get("trailingPE"))
        eps_ttm = _f(info.get("trailingEps"))
        roe_pct = _pct(info.get("returnOnEquity"))
        debt_equity = _f(info.get("debtToEquity"))
        fcf_ttm = _million(info.get("freeCashflow"))
        market_cap = _f(info.get("marketCap"))

    except Exception as exc:
        log.warning("yfinance fetch 실패 %s: %s", ticker, exc)
        return FinvizDetail(ticker=ticker)
    finally:
        time.sleep(sleep_sec)

    return FinvizDetail(
        ticker=ticker,
        price=price,
        change_pct=change_pct,
        rsi14=rsi14,
        rel_volume=rvol,
        w52_high_pct=w52_high_pct,
        w52_low_pct=w52_low_pct,
        sma20_pct=sma20_pct,
        sma50_pct=sma50_pct,
        sma200_pct=sma200_pct,
        forward_pe=forward_pe,
        peg=peg,
        beta=beta,
        target_price=target_price,
        recom=recom,
        short_float_pct=short_float_pct,
        gross_margin_pct=gross_margin_pct,
        op_margin_pct=op_margin_pct,
        profit_margin_pct=profit_margin_pct,
        eps_next_5y_pct=eps_next_5y_pct,
        revenue_ttm=revenue_ttm,
        gross_profit_ttm=gross_profit_ttm,
        net_income_ttm=net_income_ttm,
        eps_surprise_pct=eps_surprise_pct,
        revenue_growth_yoy=revenue_growth_yoy,
        net_income_growth_yoy=net_income_growth_yoy,
        # ── 신규 기술지표 실제값 ──
        sma5_val=sma5_val,
        sma20_val=sma20_val,
        sma50_val=sma50_val,
        sma200_val=sma200_val,
        bb_upper=bb_upper,
        bb_mid=bb_mid,
        bb_lower=bb_lower,
        macd_line=macd_line,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        adx=adx_val,
        di_plus=di_plus,
        di_minus=di_minus,
        atr=atr_val,
        pivot=pivot_val,
        pivot_r1=piv_r1,
        pivot_r2=piv_r2,
        pivot_s1=piv_s1,
        pivot_s2=piv_s2,
        # ── 애널리스트 의견 ──
        analyst_buy=analyst_buy,
        analyst_hold=analyst_hold,
        analyst_sell=analyst_sell,
        # ── 추가 펀더멘털 ──
        trailing_pe=trailing_pe,
        eps_ttm=eps_ttm,
        roe_pct=roe_pct,
        debt_equity=debt_equity,
        fcf_ttm=fcf_ttm,
        market_cap=market_cap,
    )


# ─── 벌크 fetch (비동기) ──────────────────────────────────────────────────

async def fetch_finviz_details_bulk(
    tickers: list[str],
    sleep_sec: float = 0.5,
    max_concurrency: int = 5,
) -> dict[str, FinvizDetail]:
    """
    여러 티커를 asyncio.to_thread 로 병렬 처리.
    max_concurrency: 동시 실행 스레드 수 (Yahoo Finance 차단 방지)
    """
    sem = asyncio.Semaphore(max_concurrency)
    results: dict[str, FinvizDetail] = {}

    async def _one(ticker: str) -> None:
        async with sem:
            detail = await asyncio.to_thread(fetch_finviz_detail, ticker, sleep_sec)
            results[ticker] = detail
            ok = "✓" if detail.price is not None else "△"
            log.info("%s %s  price=%s  rsi=%s  rev_growth=%s",
                     ok, ticker, detail.price, detail.rsi14, detail.revenue_growth_yoy)

    await asyncio.gather(*[_one(t) for t in tickers])
    return results


# ─── 옵션 체인 실시간 수집 (장중 갱신용) ────────────────────────────────────

def fetch_option_chain_fresh(
    ticker: str,
    dte_min: int = 14,
    dte_max: int = 45,
) -> list[dict] | None:
    """
    yfinance로 옵션 체인을 실시간 수집해 SummaryOptionData.chain 형식으로 반환.

    AppScript가 장전에 생성한 summary 파일의 옵션 데이터를 장중 실행 시 교체하는 용도.
    DTE dte_min~dte_max 범위에서 가장 가까운 만기 1개 선택.

    Returns:
        [{"option_type": "call"|"put", "strike": float, "expiry": "YYYY-MM-DD",
          "dte": int, "delta": float, "iv": float, "ivr": float,
          "oi": int, "bid": float, "ask": float, "spread_pct": float,
          "mid": float, "theta": float}, ...]
        실패 시 None
    """
    import yfinance as yf
    from datetime import date as _date

    try:
        t = yf.Ticker(ticker)
        expirations: list[str] = t.options  # ["2026-06-20", "2026-07-18", ...]
        if not expirations:
            log.warning("option_chain_no_expiry: %s", ticker)
            return None

        today = _date.today()
        info_data: dict = t.info or {}
        spot_pre = _f(info_data.get("currentPrice") or info_data.get("regularMarketPrice"))

        # ── DTE 범위 내 모든 만기 수집 ────────────────────────────────────
        candidate_exps: list[tuple[str, int]] = []
        for exp_str in expirations:
            try:
                exp_dt = _date.fromisoformat(exp_str)
            except ValueError:
                continue
            dte = (exp_dt - today).days
            if dte_min <= dte <= dte_max:
                candidate_exps.append((exp_str, dte))

        # 범위 내 없으면 DTE≥dte_min 중 가장 가까운 것
        if not candidate_exps:
            for exp_str in expirations:
                try:
                    exp_dt = _date.fromisoformat(exp_str)
                except ValueError:
                    continue
                dte = (exp_dt - today).days
                if dte >= dte_min:
                    candidate_exps.append((exp_str, dte))
                    break

        if not candidate_exps:
            log.warning("option_chain_no_valid_expiry: %s (dte_min=%d)", ticker, dte_min)
            return None

        # ── 만기별 ATM OI 합산 → OI가 가장 높은 만기 선택 ──────────────
        chosen_exp: str = candidate_exps[0][0]
        chosen_dte: int = candidate_exps[0][1]
        best_atm_oi: int = -1

        for exp_str, dte_c in candidate_exps:
            try:
                ch_tmp = t.option_chain(exp_str)
                for df_tmp in [ch_tmp.calls, ch_tmp.puts]:
                    if df_tmp is None or df_tmp.empty:
                        continue
                    if spot_pre:
                        atm_mask = (
                            (df_tmp["strike"] >= spot_pre * 0.95) &
                            (df_tmp["strike"] <= spot_pre * 1.05)
                        )
                        atm_oi = int(df_tmp.loc[atm_mask, "openInterest"].fillna(0).sum())
                    else:
                        atm_oi = int(df_tmp["openInterest"].fillna(0).sum())
                    if atm_oi > best_atm_oi:
                        best_atm_oi = atm_oi
                        chosen_exp = exp_str
                        chosen_dte = dte_c
            except Exception:
                continue

        log.debug("option_expiry_selected: %s  exp=%s  DTE=%d  atm_oi=%d",
                  ticker, chosen_exp, chosen_dte, best_atm_oi)

        chain = t.option_chain(chosen_exp)
        spot = spot_pre or _f(info_data.get("regularMarketPrice"))

        # ── OHLC 히스토리 (HV 계산 + BS delta용) ─────────────────────
        closes: list[float] = []
        try:
            hist = t.history(period="30d", auto_adjust=True)
            if hist is not None and not hist.empty:
                closes = hist["Close"].dropna().tolist()
        except Exception:
            pass

        # ── HV 기반 IVR 계산용 — 20일 실현 변동성 ─────────────────────
        hv: Optional[float] = None
        if closes and len(closes) >= 20:
            import math as _math
            log_rets = [_math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
            hv = round(_math.sqrt(252) * float(np.std(log_rets[-20:])) * 100, 2)

        results: list[dict] = []

        for df, opt_type in [(chain.calls, "call"), (chain.puts, "put")]:
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                strike = _f(row.get("strike"))
                if strike is None:
                    continue

                # spot ±10% ATM 근처만 포함 (체인 전체를 넘기면 너무 큼)
                if spot and (strike < spot * 0.90 or strike > spot * 1.10):
                    continue

                bid = _f(row.get("bid")) or 0.0
                ask = _f(row.get("ask")) or 0.0
                last = _f(row.get("lastPrice")) or 0.0
                mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last
                spread_pct = ((ask - bid) / mid * 100) if mid > 0 and bid > 0 else 0.0
                iv_raw = _f(row.get("impliedVolatility")) or 0.0
                iv_pct = round(iv_raw * 100, 2)   # decimal → %
                oi = int(row.get("openInterest") or 0)
                delta_raw = _f(row.get("delta"))

                # ── yfinance가 delta를 안 주면 Black-Scholes로 계산 ──────
                if delta_raw is None and spot and strike and iv_raw > 0 and chosen_dte > 0:
                    try:
                        import math as _math
                        T = chosen_dte / 365.0
                        r = 0.04  # 무위험 이자율 근사
                        d1 = (
                            _math.log(spot / strike) + (r + 0.5 * iv_raw ** 2) * T
                        ) / (iv_raw * _math.sqrt(T))
                        # 정규누적분포 근사 (scipy 없이)
                        def _ncdf(x: float) -> float:
                            k = 1.0 / (1.0 + 0.2316419 * abs(x))
                            poly = k * (0.319381530 + k * (-0.356563782
                                + k * (1.781477937 + k * (-1.821255978
                                + k * 1.330274429))))
                            return 1.0 - 0.39894228 * _math.exp(-0.5 * x * x) * poly \
                                if x >= 0 else 1.0 - _ncdf(-x)
                        bs_delta = _ncdf(d1) if opt_type == "call" else _ncdf(d1) - 1.0
                        delta_raw = round(bs_delta, 4)
                    except Exception:
                        delta_raw = 0.55 if opt_type == "call" else -0.45

                # ── IVR 계산 (HV 대비) ────────────────────────────────────
                ivr = 0.0
                if hv and hv > 0 and iv_pct > 0:
                    ivr = round(min(iv_pct / hv * 50, 100), 1)  # 간이 IVR (IV/HV 비율 기반)

                results.append({
                    "option_type": opt_type,
                    "strike":      strike,
                    "expiry":      chosen_exp,
                    "dte":         chosen_dte,
                    "delta":       delta_raw,
                    "iv":          iv_pct,
                    "ivr":         ivr,
                    "oi":          oi,
                    "bid":         bid,
                    "ask":         ask,
                    "spread_pct":  round(spread_pct, 2),
                    "mid":         round(mid, 3),
                    "theta":       _f(row.get("theta")) or -0.05,
                })

        log.info("option_chain_fresh: %s  exp=%s  DTE=%d  entries=%d",
                 ticker, chosen_exp, chosen_dte, len(results))
        return results if results else None

    except Exception as exc:
        log.warning("option_chain_fresh_failed: %s  %s", ticker, exc)
        return None


async def fetch_option_chains_bulk(
    tickers: list[str],
    dte_min: int = 14,
    dte_max: int = 45,
    max_concurrency: int = 3,
) -> dict[str, list[dict]]:
    """fetch_option_chain_fresh 의 비동기 병렬 버전"""
    sem = asyncio.Semaphore(max_concurrency)
    results: dict[str, list[dict]] = {}

    async def _one(ticker: str) -> None:
        async with sem:
            chain = await asyncio.to_thread(
                fetch_option_chain_fresh, ticker, dte_min, dte_max
            )
            if chain:
                results[ticker] = chain

    await asyncio.gather(*[_one(t) for t in tickers])
    return results


# ─── Finnhub 목표주가 ────────────────────────────────────────────────────────

def fetch_finnhub_price_target(ticker: str) -> Optional[float]:
    """Finnhub /stock/price-target 에서 애널리스트 목표주가(평균) 반환.

    환경변수 FINNHUB_API_KEY 가 없으면 None 반환 (graceful degradation).
    """
    import os
    import json
    import urllib.request

    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return None
    url = (
        f"https://finnhub.io/api/v1/stock/price-target"
        f"?symbol={ticker}&token={api_key}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        return _f(data.get("targetMean"))
    except Exception as exc:
        log.warning("finnhub price_target 실패 %s: %s", ticker, exc)
        return None


async def fetch_finnhub_price_targets_bulk(
    tickers: list[str],
    max_concurrency: int = 3,
) -> dict[str, float]:
    """fetch_finnhub_price_target 의 비동기 병렬 버전"""
    sem = asyncio.Semaphore(max_concurrency)
    results: dict[str, float] = {}

    async def _one(ticker: str) -> None:
        async with sem:
            pt = await asyncio.to_thread(fetch_finnhub_price_target, ticker)
            if pt is not None:
                results[ticker] = pt

    await asyncio.gather(*[_one(t) for t in tickers])
    return results


# ─── Finnhub 내부자 거래 ──────────────────────────────────────────────────────

def fetch_finnhub_insider_sentiment(ticker: str, lookback_months: int = 3) -> Optional[float]:
    """Finnhub /stock/insider-sentiment 에서 내부자 순매수/매도 비율 반환.

    반환값: insider_trans_pct (양수=순매수, 음수=순매도, 단위 %)
    FINNHUB_API_KEY 없으면 None 반환 (graceful degradation).
    """
    import os
    import json
    import urllib.request
    from datetime import date as _date, timedelta as _td

    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return None

    # 조회 기간: 최근 lookback_months 개월
    today = _date.today()
    from_date = today - _td(days=lookback_months * 30)
    url = (
        f"https://finnhub.io/api/v1/stock/insider-transactions"
        f"?symbol={ticker}&from={from_date}&to={today}&token={api_key}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        transactions = data.get("data", [])
        if not transactions:
            return None

        # 순매수/순매도 주식 수 집계
        net_shares: float = 0.0
        total_shares: float = 0.0
        for tx in transactions:
            # transactionType: "P - Purchase", "S - Sale", "A - Award", etc.
            tx_type = (tx.get("transactionType") or tx.get("type") or "").upper()
            shares = float(tx.get("share") or tx.get("shares") or 0)
            if shares <= 0:
                continue
            total_shares += shares
            if "P" in tx_type and "PURCHASE" in tx_type or tx_type == "P":
                net_shares += shares
            elif "S" in tx_type and "SALE" in tx_type or tx_type == "S":
                net_shares -= shares

        if total_shares == 0:
            return None
        # 순매수 비율 (%) — 양수=순매수, 음수=순매도
        return round(net_shares / total_shares * 100, 2)

    except Exception as exc:
        log.warning("finnhub insider_sentiment 실패 %s: %s", ticker, exc)
        return None


async def fetch_finnhub_insider_bulk(
    tickers: list[str],
    max_concurrency: int = 3,
) -> dict[str, float]:
    """fetch_finnhub_insider_sentiment 의 비동기 병렬 버전.

    Returns:
        {ticker: insider_trans_pct}  (값 있는 티커만)
    """
    sem = asyncio.Semaphore(max_concurrency)
    results: dict[str, float] = {}

    async def _one(ticker: str) -> None:
        async with sem:
            pct = await asyncio.to_thread(fetch_finnhub_insider_sentiment, ticker)
            if pct is not None:
                results[ticker] = pct

    await asyncio.gather(*[_one(t) for t in tickers])
    return results


# ─── 매크로 지표 실시간 ───────────────────────────────────────────────────────

def fetch_macro_realtime() -> dict:
    """VIX, SPY, QQQ, DXY, Gold, Oil, Bonds, SOXX, Nasdaq 실시간 가격 + Fear&Greed(CNN).

    반환값 key는 SummaryMacro 필드명과 동일하므로 model_copy(update=...) 로 바로 주입 가능.
    """
    import json
    import ssl
    import urllib.request
    import yfinance as yf

    result: dict = {}

    # ── yfinance 매크로 티커 ──────────────────────────────────────────────
    _MACRO_MAP: list[tuple[str, str, Optional[str]]] = [
        # (yf_symbol, field,     ma20_field)
        ("^VIX",     "vix",     "vix_ma20"),
        ("SPY",      "spy",     "spy_ma20"),
        ("QQQ",      "qqq",     "qqq_ma20"),
        ("^IXIC",    "nasdaq",  "nasdaq_ma20"),
        ("DX-Y.NYB", "dxy",     "dxy_ma20"),
        ("GC=F",     "gold",    None),
        ("CL=F",     "oil_wti", None),
        ("SOXX",     "soxx",    "soxx_ma20"),
        ("^TNX",     "yield_10y", None),
    ]

    for sym, field, ma_field in _MACRO_MAP:
        try:
            hist = yf.Ticker(sym).history(period="30d", auto_adjust=True)
            if hist is not None and not hist.empty:
                closes = hist["Close"].dropna().tolist()
                result[field] = round(float(closes[-1]), 2)
                if ma_field and len(closes) >= 20:
                    result[ma_field] = round(float(np.mean(closes[-20:])), 2)
        except Exception as exc:
            log.debug("macro fetch 실패 %s: %s", sym, exc)
        time.sleep(0.1)  # rate-limit 완화

    # ── Fear & Greed — CNN production API ────────────────────────────
    try:
        _ssl_ctx = ssl.create_default_context()
        _ssl_ctx.check_hostname = False
        _ssl_ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://edition.cnn.com/markets/fear-and-greed",
                "Origin": "https://edition.cnn.com",
            },
        )
        with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as r:
            data = json.loads(r.read().decode())
        fg = data["fear_and_greed"]
        result["fear_greed"] = int(round(float(fg["score"])))
        result["fear_greed_label"] = str(fg["rating"])
        log.info("fear_greed_cnn: score=%d label=%s", result["fear_greed"], result["fear_greed_label"])
    except Exception as exc:
        log.warning("fear_greed CNN 실패: %s", exc)

    return result
