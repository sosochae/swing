"""
core/api_fetcher.py
===================
Kavout 유니버스 티커 → yfinance API → StockDetail 완전 채우기

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

from shared.schemas import StockDetail

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
    """전일 피벗 포인트 (pivot, r1, r2, r3, s1, s2, s3) 반환"""
    if len(highs) < 2:
        return (None,) * 7
    h, l, c = highs[-2], lows[-2], closes[-2]   # 전일 데이터
    p = (h + l + c) / 3
    r1 = 2 * p - l;    r2 = p + (h - l);     r3 = h + 2 * (p - l)
    s1 = 2 * p - h;    s2 = p - (h - l);     s3 = l - 2 * (h - p)
    return (round(p, 2), round(r1, 2), round(r2, 2), round(r3, 2),
            round(s1, 2), round(s2, 2), round(s3, 2))


# ─── 가격선 계산 헬퍼 (19개 메서드) ─────────────────────────────────────────

def _calc_fibonacci(swing_high: float, swing_low: float
                    ) -> dict:
    """① 피보나치 되돌림 (38.2/50/61.8%) + ② 확장 (100/161.8%)"""
    rng = swing_high - swing_low
    if rng <= 0:
        return {}
    return {
        "fib_38_2":    round(swing_high - rng * 0.382, 2),
        "fib_50_0":    round(swing_high - rng * 0.500, 2),
        "fib_61_8":    round(swing_high - rng * 0.618, 2),
        "fib_ext_100": round(swing_low  + rng * 1.000, 2),
        "fib_ext_162": round(swing_low  + rng * 1.618, 2),
    }


def _calc_camarilla(high: float, low: float, close: float) -> dict:
    """⑯ Camarilla Pivot — 전일 H/L/C 기준 4레벨"""
    rng = high - low
    return {
        "cam_h4": round(close + rng * 1.1 / 2, 2),
        "cam_h3": round(close + rng * 1.1 / 4, 2),
        "cam_l3": round(close - rng * 1.1 / 4, 2),
        "cam_l4": round(close - rng * 1.1 / 2, 2),
    }


def _calc_parabolic_sar(highs: list[float], lows: list[float],
                         af_start: float = 0.02, af_max: float = 0.20
                         ) -> tuple[Optional[float], Optional[str]]:
    """⑭ Parabolic SAR — 트레일링 스탑 대안"""
    n = min(len(highs), len(lows))
    if n < 3:
        return None, None
    highs = highs[-n:]; lows = lows[-n:]
    # 초기 방향: 첫 봉 기준 임시 설정
    uptrend = highs[-1] > highs[-2]
    sar = lows[-2] if uptrend else highs[-2]
    ep  = highs[-1] if uptrend else lows[-1]
    af  = af_start
    for i in range(2, n):
        prev_sar = sar
        sar = prev_sar + af * (ep - prev_sar)
        if uptrend:
            sar = min(sar, lows[i-1], lows[i-2] if i >= 2 else lows[i-1])
            if lows[i] < sar:
                uptrend = False; sar = ep; ep = lows[i]; af = af_start
            else:
                if highs[i] > ep:
                    ep = highs[i]; af = min(af + af_start, af_max)
        else:
            sar = max(sar, highs[i-1], highs[i-2] if i >= 2 else highs[i-1])
            if highs[i] > sar:
                uptrend = True; sar = ep; ep = highs[i]; af = af_start
            else:
                if lows[i] < ep:
                    ep = lows[i]; af = min(af + af_start, af_max)
    return round(sar, 2), "up" if uptrend else "down"


def _detect_candle_pattern(recent_ohlc: list[dict]) -> str:
    """⑩ 캔들 패턴 감지 — recent_ohlc 최근 3봉 기준"""
    if not recent_ohlc or len(recent_ohlc) < 1:
        return "none"
    def _parse(bar: dict) -> tuple:
        o = float(bar.get("open", 0) or 0)
        h = float(bar.get("high", 0) or 0)
        l = float(bar.get("low", 0) or 0)
        c = float(bar.get("close", 0) or 0)
        return o, h, l, c

    o1, h1, l1, c1 = _parse(recent_ohlc[-1])  # 최신 봉
    body1 = abs(c1 - o1)
    lower_wick1 = min(o1, c1) - l1
    upper_wick1 = h1 - max(o1, c1)

    # Hammer (망치형): 아래꼬리 > 2×몸통, 위꼬리 < 몸통/2
    if body1 > 0 and lower_wick1 > 2 * body1 and upper_wick1 < body1 * 0.5:
        return "hammer"

    # Bullish Engulfing (양봉이 전봉 완전 포함)
    if len(recent_ohlc) >= 2:
        o0, h0, l0, c0 = _parse(recent_ohlc[-2])
        if c0 < o0 and c1 > o1 and c1 > o0 and o1 < c0:
            return "engulfing"

    # Morning Star (삼성 반전): [음봉, 작은몸통, 양봉]
    if len(recent_ohlc) >= 3:
        o_2, h_2, l_2, c_2 = _parse(recent_ohlc[-3])
        o_1, h_1, l_1, c_1 = _parse(recent_ohlc[-2])
        if (c_2 < o_2                             # 첫 봉: 음봉
                and abs(c_1 - o_1) < abs(c_2 - o_2) * 0.3  # 두 번째: 작은몸통
                and c1 > o1 and c1 > (o_2 + c_2) / 2):      # 세 번째: 양봉 상반부 회복
            return "morning_star"

    return "none"


def _calc_anchored_vwap(hist_df, anchor_price: float) -> Optional[float]:
    """⑦ 앵커 VWAP — 최근 스윙 저점 날짜 이후 누적 VWAP"""
    try:
        # 스윙 저점에 가장 가까운 날짜 찾기
        closes = hist_df["Close"].tolist()
        closest_idx = min(range(len(closes)), key=lambda i: abs(closes[i] - anchor_price))
        subset = hist_df.iloc[closest_idx:]
        if subset.empty:
            return None
        tp = (subset["High"] + subset["Low"] + subset["Close"]) / 3
        cum_vol = subset["Volume"].cumsum()
        cum_tpv = (tp * subset["Volume"]).cumsum()
        if cum_vol.iloc[-1] <= 0:
            return None
        return round(float(cum_tpv.iloc[-1] / cum_vol.iloc[-1]), 2)
    except Exception:
        return None


# ─── 4H/1H 장중 지표 계산 ────────────────────────────────────────────────

def _calc_intraday_indicators(ticker: str) -> dict:
    """
    4H/1H 지표 계산.
    - yfinance 1H 데이터(5일) → 4H 리샘플링
    - 4H: RSI, MACD Hist, ADX/DI, VWAP, Pivot S3/R3
    - 1H: RSI, BB 하단

    주의:
    - 장외 시간 실행 시 마지막 4H 바가 불완전할 수 있음 → -2 인덱스 사용
    - yfinance 1H 데이터는 미국 동부시간 기준 (자동 조정)

    Returns:
        dict with 4H/1H fields (실패 시 모두 None)
    """
    result: dict = {
        "rsi_4h": None, "macd_hist_4h": None,
        "adx_4h": None, "di_plus_4h": None, "di_minus_4h": None,
        "vwap_4h": None, "pivot_p_4h": None, "pivot_s3_4h": None, "pivot_r3_4h": None,
        "rsi_1h": None, "bb_lower_1h": None,
        "sma5_1h": None, "sma10_1h": None, "sma20_1h": None, "macd_hist_1h": None,
        "vwap_std1_upper": None, "vwap_std1_lower": None,
        "vwap_std2_upper": None, "vwap_std2_lower": None,
    }
    try:
        import yfinance as _yf

        # ── A3: Pivot 계산용 최근 5일 1H 데이터 (좁은 범위) ─────────
        # 버그 수정: 60일 리샘플 4H 바는 대폭락 봉을 잡아 S3/R3가 왜곡됨
        # → 최근 5일만 써서 현재 시장 상황의 4H 바로 계산
        h1_recent = _yf.Ticker(ticker).history(period="5d", interval="1h")

        # ── A2: 1H SMA/MACD용 60일 1H 데이터 ────────────────────────
        h1 = _yf.Ticker(ticker).history(period="60d", interval="1h")
        if h1.empty or len(h1) < 8:
            return result

        # ── 1H 지표 ──────────────────────────────────────────────────
        h1_closes = h1["Close"].dropna().tolist()

        # 1H RSI
        if len(h1_closes) >= 14:
            result["rsi_1h"] = _calc_rsi(h1_closes, 14)

        # 1H 볼린저밴드 하단
        if len(h1_closes) >= 20:
            _, _, bb_l = _calc_bollinger(h1_closes)
            result["bb_lower_1h"] = bb_l

        # A2: 1H SMA5/10/20 ($896/$917/$917 → 진입 구간 핵심)
        if len(h1_closes) >= 5:
            result["sma5_1h"]  = round(float(sum(h1_closes[-5:])  / 5),  2)
        if len(h1_closes) >= 10:
            result["sma10_1h"] = round(float(sum(h1_closes[-10:]) / 10), 2)
        if len(h1_closes) >= 20:
            result["sma20_1h"] = round(float(sum(h1_closes[-20:]) / 20), 2)

        # A2: 1H MACD 히스토그램 (단기 반등 조짐 감지) + 전봉 (④ 양전환 감지)
        if len(h1_closes) >= 27:
            _h1ml, _h1ms, _h1mh = _calc_macd(h1_closes)
            result["macd_hist_1h"] = round(_h1mh, 4) if _h1mh is not None else None
            # 전봉 히스토그램 (h1_closes[:-1] 로 재계산)
            _, _, _h1mh_prev = _calc_macd(h1_closes[:-1])
            result["macd_hist_1h_prev"] = round(_h1mh_prev, 4) if _h1mh_prev is not None else None

        # ⑧ 볼린저밴드 %B
        if len(h1_closes) >= 20:
            _bb_u, _bb_m, _bb_l = _calc_bollinger(h1_closes)
            if _bb_u and _bb_l and _bb_u != _bb_l:
                result["bb_pct_b"] = round((h1_closes[-1] - _bb_l) / (_bb_u - _bb_l), 3)

        # ── 4H 리샘플링 (60일 data → MACD/RSI/ADX) ───────────────────
        h1_reset = h1.reset_index()
        h4 = h1_reset.resample("4h", on="Datetime").agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum"
        }).dropna()

        if len(h4) < 10:
            return result

        h4_closes  = h4["Close"].tolist()
        h4_highs   = h4["High"].tolist()
        h4_lows    = h4["Low"].tolist()

        # ── 4H RSI ───────────────────────────────────────────────────
        if len(h4_closes) >= 14:
            result["rsi_4h"] = _calc_rsi(h4_closes, 14)

        # ── 4H MACD 히스토그램 ───────────────────────────────────────
        if len(h4_closes) >= 26:
            ml, ms, mh = _calc_macd(h4_closes)
            result["macd_hist_4h"] = round(mh, 4) if mh is not None else None

        # ── 4H ADX + DI+/DI- + ⑤ 전봉값 (꺾임/교차 감지) ────────────
        if len(h4_closes) >= 28:
            adx4, di_p4, di_n4 = _calc_adx(h4_highs, h4_lows, h4_closes)
            result["adx_4h"]      = adx4
            result["di_plus_4h"]  = di_p4
            result["di_minus_4h"] = di_n4
            # 전봉 ADX/DI (h4_closes[:-1]로 재계산)
            adx4p, dip4p, din4p = _calc_adx(h4_highs[:-1], h4_lows[:-1], h4_closes[:-1])
            result["adx_prev"]      = adx4p
            result["di_plus_prev"]  = dip4p
            result["di_minus_prev"] = din4p

        # ⑥ 4H SMA5/10/20 (멀티TF 클러스터 감지)
        if len(h4_closes) >= 5:
            result["sma5_4h"]  = round(float(sum(h4_closes[-5:])  / 5),  2)
        if len(h4_closes) >= 10:
            result["sma10_4h"] = round(float(sum(h4_closes[-10:]) / 10), 2)
        if len(h4_closes) >= 20:
            result["sma20_4h"] = round(float(sum(h4_closes[-20:]) / 20), 2)

        # ⑭ Parabolic SAR (4H 기준)
        if len(h4_closes) >= 10:
            _sar, _sar_dir = _calc_parabolic_sar(h4_highs, h4_lows)
            result["parabolic_sar"] = _sar
            result["sar_direction"] = _sar_dir

        # ── 4H VWAP (당일 기준 누적) + D: VWAP 표준편차 밴드 ─────────
        try:
            _today_str = h4.index[-1].date() if hasattr(h4.index[-1], "date") else None
            if _today_str:
                _today_h4 = h4[h4.index.date == _today_str]
                if len(_today_h4) >= 1:
                    _tp = (_today_h4["High"] + _today_h4["Low"] + _today_h4["Close"]) / 3
                    _cum_vol = _today_h4["Volume"].cumsum()
                    _cum_tpv = (_tp * _today_h4["Volume"]).cumsum()
                    _vwap = float(_cum_tpv.iloc[-1] / _cum_vol.iloc[-1]) if _cum_vol.iloc[-1] > 0 else None
                    result["vwap_4h"] = round(_vwap, 2) if _vwap else None
                    # D: VWAP 표준편차 밴드 (볼륨 가중 분산)
                    if _vwap and len(_today_h4) >= 2:
                        _tp_arr = _tp.tolist()
                        _vol_arr = _today_h4["Volume"].tolist()
                        _total_vol = sum(_vol_arr)
                        if _total_vol > 0:
                            _vwap_var = sum((_t - _vwap) ** 2 * _v for _t, _v in zip(_tp_arr, _vol_arr)) / _total_vol
                            _vwap_std = _vwap_var ** 0.5
                            if _vwap_std > 0:
                                result["vwap_std1_upper"] = round(_vwap + _vwap_std, 2)
                                result["vwap_std1_lower"] = round(_vwap - _vwap_std, 2)
                                result["vwap_std2_upper"] = round(_vwap + 2 * _vwap_std, 2)
                                result["vwap_std2_lower"] = round(_vwap - 2 * _vwap_std, 2)
        except Exception:
            pass

        # ── A3: 4H Pivot P/S3/R3 — 최근 5일 data 사용 (버그 수정) ────
        # 이유: 60일 리샘플 4H는 대폭락 봉(H=$989, L=$854)으로 S3=$697 왜곡
        #       최근 5일 좁은 4H 봉 → $850 수준의 정확한 S3 계산
        if not h1_recent.empty:
            h1r_reset = h1_recent.reset_index()
            h4_recent = h1r_reset.resample("4h", on="Datetime").agg({
                "Open": "first", "High": "max", "Low": "min",
                "Close": "last", "Volume": "sum"
            }).dropna()
            if len(h4_recent) >= 3:
                _rph = float(h4_recent["High"].tolist()[-2])
                _rpl = float(h4_recent["Low"].tolist()[-2])
                _rpc = float(h4_recent["Close"].tolist()[-2])
                _rpp = (_rph + _rpl + _rpc) / 3
                _rpr3 = _rph + 2 * (_rpp - _rpl)
                _rps3 = _rpl - 2 * (_rph - _rpp)
                result["pivot_p_4h"]  = round(_rpp,  2)
                result["pivot_s3_4h"] = round(_rps3, 2)
                result["pivot_r3_4h"] = round(_rpr3, 2)

    except Exception as exc:
        log.debug("_calc_intraday_indicators 실패 %s: %s", ticker, exc)

    return result


# ─── 주봉 지표 계산 ───────────────────────────────────────────────────────

def _calc_weekly_indicators(ticker: str) -> dict:
    """
    주봉 종합 지표 계산 (A1 확장).
    - SMA5, Pivot P/S1/S2/R1/R2
    - ADX/DI+/DI-, RSI, MACD Hist (장기 추세 판단 핵심)

    Returns:
        dict with all weekly fields (실패 시 모두 None)
    """
    result: dict = {
        "weekly_sma5_val": None,
        "weekly_pivot_p": None,
        "weekly_pivot_s1": None,
        "weekly_pivot_s2": None,
        "weekly_pivot_r1": None,
        "weekly_pivot_r2": None,
        "weekly_adx": None,
        "weekly_di_plus": None,
        "weekly_di_minus": None,
        "weekly_rsi": None,
        "weekly_macd_hist": None,
        "prev_week_high": None,
        "prev_week_low": None,
    }
    try:
        import yfinance as _yf
        # RSI(14) + ADX(14) + MACD(26)에 충분한 봉 수 → 최소 40주 필요
        w_hist = _yf.Ticker(ticker).history(period="12mo", interval="1wk")
        if w_hist.empty or len(w_hist) < 15:
            return result
        w_closes = [float(v) for v in w_hist["Close"].dropna().tolist()]
        w_highs  = [float(v) for v in w_hist["High"].dropna().tolist()]
        w_lows   = [float(v) for v in w_hist["Low"].dropna().tolist()]

        # 주봉 SMA5
        if len(w_closes) >= 5:
            result["weekly_sma5_val"] = round(float(sum(w_closes[-5:]) / 5), 2)

        # 주봉 RSI
        if len(w_closes) >= 14:
            result["weekly_rsi"] = _calc_rsi(w_closes, 14)

        # 주봉 MACD 히스토그램
        if len(w_closes) >= 26:
            _wml, _wms, _wmh = _calc_macd(w_closes)
            result["weekly_macd_hist"] = round(_wmh, 4) if _wmh is not None else None

        # 주봉 ADX + DI (장기 추세 강도 핵심)
        if len(w_closes) >= 28:
            _wadx, _wdip, _wdin = _calc_adx(w_highs, w_lows, w_closes)
            result["weekly_adx"]      = _wadx
            result["weekly_di_plus"]  = _wdip
            result["weekly_di_minus"] = _wdin

        # 주봉 피벗 + E: 전주 고점/저점 (전주 데이터 기준)
        if len(w_highs) >= 2:
            wh = w_highs[-2];  wl = w_lows[-2];  wc = w_closes[-2]
            wp  = (wh + wl + wc) / 3
            wr1 = 2 * wp - wl;   wr2 = wp + (wh - wl)
            ws1 = 2 * wp - wh;   ws2 = wp - (wh - wl)
            result["weekly_pivot_p"]  = round(wp,  2)
            result["weekly_pivot_r1"] = round(wr1, 2)
            result["weekly_pivot_r2"] = round(wr2, 2)
            result["weekly_pivot_s1"] = round(ws1, 2)
            result["weekly_pivot_s2"] = round(ws2, 2)
            result["prev_week_high"]  = round(float(wh), 2)
            result["prev_week_low"]   = round(float(wl),  2)
    except Exception:
        pass
    return result


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

def fetch_stock_detail(ticker: str, sleep_sec: float = 0.5) -> StockDetail:
    """yfinance로 단일 티커 StockDetail 완전 채우기 (동기, thread-safe)"""
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
        opens: list[float] = []
        if hist is not None and not hist.empty:
            closes = hist["Close"].dropna().tolist()
            volumes = hist["Volume"].dropna().tolist()
            highs = hist["High"].dropna().tolist()
            lows = hist["Low"].dropna().tolist()
            opens = hist["Open"].dropna().tolist()

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
        sma5_val   = _calc_sma_val(closes, 5)
        sma10_val  = _calc_sma_val(closes, 10)
        sma20_val  = _calc_sma_val(closes, 20)
        sma50_val  = _calc_sma_val(closes, 50)
        sma60_val  = _calc_sma_val(closes, 60)
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
        pivot_val, piv_r1, piv_r2, piv_r3, piv_s1, piv_s2, piv_s3 = _calc_pivot(highs, lows, closes)

        # ──① 피보나치 되돌림/확장 (30일 스윙 고점/저점 기준) ───────────
        _fib_fields: dict = {}
        _swing_high_30d: Optional[float] = None
        _swing_low_30d:  Optional[float] = None
        if len(highs) >= 20 and len(lows) >= 20:
            _swing_high_30d = round(float(max(highs[-30:])), 2) if len(highs) >= 30 else round(float(max(highs)), 2)
            _swing_low_30d  = round(float(min(lows[-30:])),  2) if len(lows)  >= 30 else round(float(min(lows)),  2)
            _fib_fields = _calc_fibonacci(_swing_high_30d, _swing_low_30d)

        # ──⑯ Camarilla Pivot (전일 H/L/C) ───────────────────────────────
        _cam_fields: dict = {}
        _prev_day_high: Optional[float] = None
        _prev_day_low:  Optional[float] = None
        if len(highs) >= 2 and len(lows) >= 2 and len(closes) >= 2:
            _cam_fields = _calc_camarilla(highs[-2], lows[-2], closes[-2])
            # E: 전일 고점/저점
            _prev_day_high = round(float(highs[-2]), 2)
            _prev_day_low  = round(float(lows[-2]),  2)

        # ── A-1: EMA 9/21 ─────────────────────────────────────────────────
        _ema9_val:  Optional[float] = None
        _ema21_val: Optional[float] = None
        if len(closes) >= 9:
            _e9 = _ema(closes, 9)
            if _e9:
                _ema9_val = round(_e9[-1], 2)
        if len(closes) >= 21:
            _e21 = _ema(closes, 21)
            if _e21:
                _ema21_val = round(_e21[-1], 2)

        # ── A-1b: EMA 50/100/200 ──────────────────────────────────────────
        _ema50_val:  Optional[float] = None
        _ema100_val: Optional[float] = None
        _ema200_val: Optional[float] = None
        if len(closes) >= 50:
            _e50 = _ema(closes, 50)
            if _e50:
                _ema50_val = round(_e50[-1], 2)
        if len(closes) >= 100:
            _e100 = _ema(closes, 100)
            if _e100:
                _ema100_val = round(_e100[-1], 2)
        if len(closes) >= 200:
            _e200 = _ema(closes, 200)
            if _e200:
                _ema200_val = round(_e200[-1], 2)

        # ── A-9: 52주 고점/저점 ────────────────────────────────────────────
        _w52_high: Optional[float] = None
        _w52_low:  Optional[float] = None
        if len(highs) >= 1:
            _w52_high = round(float(max(highs[-252:])), 2) if len(highs) >= 252 else round(float(max(highs)), 2)
        if len(lows) >= 1:
            _w52_low  = round(float(min(lows[-252:])),  2) if len(lows)  >= 252 else round(float(min(lows)),  2)

        # ── A-2: Keltner Channel (EMA20 ± 2×ATR) ─────────────────────────
        _keltner_upper: Optional[float] = None
        _keltner_lower: Optional[float] = None
        if len(closes) >= 20 and atr_val:
            _e20 = _ema(closes, 20)
            if _e20:
                _ema20_k = _e20[-1]
                _keltner_upper = round(_ema20_k + 2.0 * atr_val, 2)
                _keltner_lower = round(_ema20_k - 2.0 * atr_val, 2)

        # ── A-3: Donchian Channel 20일 ────────────────────────────────────
        _donchian_20_upper: Optional[float] = None
        _donchian_20_lower: Optional[float] = None
        if len(highs) >= 20 and len(lows) >= 20:
            _donchian_20_upper = round(float(max(highs[-20:])), 2)
            _donchian_20_lower = round(float(min(lows[-20:])),  2)

        # ── A-4/5: HV30 + 기대이동폭 (5d / 15d) ─────────────────────────
        _hv30:      Optional[float] = None
        _hv_move_5d:  Optional[float] = None
        _hv_move_15d: Optional[float] = None
        if len(closes) >= 31:
            import math as _math
            _log_rets = [_math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
            _hv30_raw = _math.sqrt(252) * float(np.std(_log_rets[-30:])) * 100
            _hv30 = round(_hv30_raw, 2)
            if price and _hv30 > 0:
                _daily_sigma = price * (_hv30 / 100) / _math.sqrt(252)
                _hv_move_5d  = round(_daily_sigma * _math.sqrt(5),  2)
                _hv_move_15d = round(_daily_sigma * _math.sqrt(15), 2)

        # ── A-6: Monthly Pivot (전월 OHLC) ───────────────────────────────
        _monthly_pivot:    Optional[float] = None
        _monthly_pivot_r1: Optional[float] = None
        _monthly_pivot_r2: Optional[float] = None
        _monthly_pivot_s1: Optional[float] = None
        _monthly_pivot_s2: Optional[float] = None
        try:
            import datetime as _dt
            _today = _dt.date.today()
            # 전월의 마지막 거래일 데이터 → yfinance monthly 1봉
            _mh = t.history(period="3mo", interval="1mo", auto_adjust=True)
            if _mh is not None and len(_mh) >= 2:
                _pm = _mh.iloc[-2]  # 완성된 전월봉
                _mH = float(_pm["High"])
                _mL = float(_pm["Low"])
                _mC = float(_pm["Close"])
                _mP = round((_mH + _mL + _mC) / 3, 2)
                _monthly_pivot    = _mP
                _monthly_pivot_r1 = round(2 * _mP - _mL, 2)
                _monthly_pivot_r2 = round(_mP + (_mH - _mL), 2)
                _monthly_pivot_s1 = round(2 * _mP - _mH, 2)
                _monthly_pivot_s2 = round(_mP - (_mH - _mL), 2)
        except Exception:
            pass

        # ── A-7: FVG (Fair Value Gap) — 최근 20봉 중 현재가 근접 미채움 구간 ──
        _fvg_bull_top:    Optional[float] = None
        _fvg_bull_bottom: Optional[float] = None
        _fvg_bear_top:    Optional[float] = None
        _fvg_bear_bottom: Optional[float] = None
        _n_fvg = min(len(highs), len(lows))
        if _n_fvg >= 3:
            for _fi in range(_n_fvg - 3, max(-1, _n_fvg - 23), -1):
                _fh0, _fl0 = highs[_fi], lows[_fi]
                _fh2, _fl2 = highs[_fi + 2], lows[_fi + 2]
                # Bullish FVG: high[i] < low[i+2] → 미채움 구간 = [high[i], low[i+2]]
                if _fl2 > _fh0 and _fvg_bull_top is None:
                    _zmid = (_fl2 + _fh0) / 2
                    if price and abs(_zmid - price) / price < 0.15:
                        _fvg_bull_top    = round(_fl2, 2)
                        _fvg_bull_bottom = round(_fh0, 2)
                # Bearish FVG: low[i] > high[i+2] → 미채움 구간 = [high[i+2], low[i]]
                if _fh2 < _fl0 and _fvg_bear_top is None:
                    _zmid = (_fl0 + _fh2) / 2
                    if price and abs(_zmid - price) / price < 0.15:
                        _fvg_bear_top    = round(_fl0, 2)
                        _fvg_bear_bottom = round(_fh2, 2)
                if _fvg_bull_top and _fvg_bear_top:
                    break

        # ── A-8: Gap Fill — 최근 10일 미채움 갭 ─────────────────────────
        _gap_up_fill:   Optional[float] = None
        _gap_down_fill: Optional[float] = None
        _n_gap = min(len(opens), len(closes), len(highs), len(lows))
        if _n_gap >= 2:
            for _d in range(1, min(11, _n_gap)):
                _gday = _n_gap - _d
                _pc   = closes[_gday - 1]
                _go   = opens[_gday]
                if _pc <= 0:
                    continue
                _gpct = (_go - _pc) / _pc
                if _gpct > 0.005 and _gap_up_fill is None:
                    # 갭 업 — 이후 어느 날이라도 low ≤ _pc 이면 채워진 것
                    _filled = any(lows[_gday + k] <= _pc for k in range(_n_gap - _gday))
                    if not _filled and price and price > _pc:
                        if abs(_pc - price) / price < 0.15:
                            _gap_up_fill = round(_pc, 2)
                elif _gpct < -0.005 and _gap_down_fill is None:
                    # 갭 다운 — 이후 어느 날이라도 high ≥ _pc 이면 채워진 것
                    _filled = any(highs[_gday + k] >= _pc for k in range(_n_gap - _gday))
                    if not _filled and price and price < _pc:
                        if abs(_pc - price) / price < 0.15:
                            _gap_down_fill = round(_pc, 2)
                if _gap_up_fill and _gap_down_fill:
                    break

        # ──⑦ 앵커 VWAP (스윙 저점 기준) ─────────────────────────────────
        _vwap_anchored: Optional[float] = None
        if _swing_low_30d is not None:
            try:
                _hist_df = t.history(period="30d", auto_adjust=True)
                if not _hist_df.empty:
                    _vwap_anchored = _calc_anchored_vwap(_hist_df, _swing_low_30d)
            except Exception:
                pass

        # ──⑩ 캔들 패턴 감지 (최근 OHLC 3봉) ────────────────────────────
        _candle_pattern: str = "none"
        try:
            _recent_bars = hist.tail(3)
            _ohlc_list = [
                {"open": row["Open"], "high": row["High"],
                 "low": row["Low"],   "close": row["Close"]}
                for _, row in _recent_bars.iterrows()
            ]
            _candle_pattern = _detect_candle_pattern(_ohlc_list)
        except Exception:
            pass

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
        # 애널리스트 목표주가 — 합리적 범위 체크 포함
        # apt["current"]는 post-earnings 급등 직후 현재가와 같아지는 버그 있음
        # → current/mean/median 모두 시도 후 현재가 ±1% 이내면 None 처리
        target_price_high: Optional[float] = None
        try:
            apt = t.analyst_price_targets or {}
            # ── 최고 목표주가 (Street-High) ─────────────────────
            _high_raw = apt.get("high") or info.get("targetHighPrice")
            _high = _f(_high_raw)
            if _high and _high > 0 and (not price or (price * 0.5 < _high < price * 5.0)):
                target_price_high = _high

            # ── 컨센서스 목표주가 (median > mean 우선) ───────────
            _tp_candidates = [
                apt.get("median"),    # 이상치에 강한 중간값 우선
                info.get("targetMedianPrice"),
                apt.get("mean"),
                info.get("targetMeanPrice"),
            ]
            target_price = None
            for _tp_raw in _tp_candidates:
                _tp = _f(_tp_raw)
                if _tp is None or _tp <= 0:
                    continue
                # 현재가와 거의 같으면 무효 (yfinance 버그)
                if price and abs(_tp - price) / price < 0.01:
                    continue
                # 현재가 대비 50%~300% 벗어난 값은 stale 데이터로 제외
                if price and (_tp < price * 0.50 or _tp > price * 3.0):
                    continue
                target_price = _tp
                break
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
        return StockDetail(ticker=ticker)
    finally:
        time.sleep(sleep_sec)

    return StockDetail(
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
        target_price_high=target_price_high,
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
        sma10_val=sma10_val,
        sma20_val=sma20_val,
        sma50_val=sma50_val,
        sma60_val=sma60_val,
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
        pivot_r3=piv_r3,
        pivot_s1=piv_s1,
        pivot_s2=piv_s2,
        pivot_s3=piv_s3,
        # ── 주봉 지표 (weekly SMA5 + pivot) ──
        **_calc_weekly_indicators(ticker),
        # ── 4H/1H 장중 지표 ──
        **_calc_intraday_indicators(ticker),
        # ── 가격선 계산 (①②⑦⑩⑯ + A-1~A-6) ──
        swing_high_30d=_swing_high_30d,
        swing_low_30d=_swing_low_30d,
        **_fib_fields,
        **_cam_fields,
        prev_day_high=_prev_day_high,
        prev_day_low=_prev_day_low,
        ema9=_ema9_val,
        ema21=_ema21_val,
        ema50=_ema50_val,
        ema100=_ema100_val,
        ema200=_ema200_val,
        w52_high=_w52_high,
        w52_low=_w52_low,
        keltner_upper=_keltner_upper,
        keltner_lower=_keltner_lower,
        donchian_20_upper=_donchian_20_upper,
        donchian_20_lower=_donchian_20_lower,
        hv30=_hv30,
        hv_move_5d=_hv_move_5d,
        hv_move_15d=_hv_move_15d,
        monthly_pivot=_monthly_pivot,
        monthly_pivot_r1=_monthly_pivot_r1,
        monthly_pivot_r2=_monthly_pivot_r2,
        monthly_pivot_s1=_monthly_pivot_s1,
        monthly_pivot_s2=_monthly_pivot_s2,
        fvg_bull_top=_fvg_bull_top,
        fvg_bull_bottom=_fvg_bull_bottom,
        fvg_bear_top=_fvg_bear_top,
        fvg_bear_bottom=_fvg_bear_bottom,
        gap_up_fill=_gap_up_fill,
        gap_down_fill=_gap_down_fill,
        vwap_anchored=_vwap_anchored,
        candle_signal=_candle_pattern,
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

async def fetch_stock_data_bulk(
    tickers: list[str],
    sleep_sec: float = 0.5,
    max_concurrency: int = 5,
) -> dict[str, StockDetail]:
    """
    여러 티커를 asyncio.to_thread 로 병렬 처리.
    max_concurrency: 동시 실행 스레드 수 (Yahoo Finance 차단 방지)
    """
    sem = asyncio.Semaphore(max_concurrency)
    results: dict[str, StockDetail] = {}

    async def _one(ticker: str) -> None:
        async with sem:
            detail = await asyncio.to_thread(fetch_stock_detail, ticker, sleep_sec)
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
    import certifi as _certifi
    import yfinance as yf
    from datetime import date as _date

    # ── curl_cffi CA bundle ASCII 경로 확보 ──────────────────────────────
    # 근본 원인: 시스템 Python 3.11 실행 시 certifi 경로가 Non-ASCII 폴더
    # (C:\Users\소소\...) 를 가리켜 curl error 77 발생.
    # (curl.py L354: "Non-ASCII paths encoded as UTF-8 can trigger ErrCode 77")
    # → certifi 파일을 ASCII 경로(%TEMP%)로 복사해서 curl_cffi에 제공한다.
    _ca_raw = _certifi.where()
    try:
        _ca_raw.encode('ascii')
        _ca = _ca_raw       # 이미 ASCII 경로 → 그대로 사용
    except UnicodeEncodeError:
        # Non-ASCII 경로 → 프로젝트 내 ASCII 경로로 복사
        # %TEMP%도 Non-ASCII일 수 있으므로, 프로젝트 루트(C:\MCP\Swing\cache)를 사용
        import shutil as _sh, os as _os
        _proj_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        _ascii_ca_dir = _os.path.join(_proj_root, 'cache')
        _os.makedirs(_ascii_ca_dir, exist_ok=True)
        _ascii_ca = _os.path.join(_ascii_ca_dir, 'cacert.pem')
        _sh.copy2(_ca_raw, _ascii_ca)   # 매번 갱신 (certifi 업그레이드 반영)
        _ca = _ascii_ca
    # curl_cffi DEFAULT_CACERT + 환경변수도 갱신 (신규 Curl 객체에 적용)
    try:
        import os as _os
        _os.environ['SSL_CERT_FILE'] = _ca
        _os.environ['CURL_CA_BUNDLE'] = _ca
        _os.environ['REQUESTS_CA_BUNDLE'] = _ca
        import curl_cffi.curl as _curl_mod
        _curl_mod.DEFAULT_CACERT = _ca
    except Exception:
        pass

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
                # IV 유효 범위: 5%~150% (0.05~1.50).
                # 장외에는 iv=0 또는 0.7% 같은 garbage가 반환됨 → HV proxy로 대체.
                # HV 없으면 40% 기본값. 이 값으로 BS delta를 계산해 정확도 확보.
                _hv_proxy = (hv / 100.0) if (hv and hv > 0) else 0.40
                if iv_raw < 0.05 or iv_raw > 1.50:
                    iv_raw = _hv_proxy
                iv_pct = round(iv_raw * 100, 2)   # decimal → %
                oi = int(row.get("openInterest") or 0)
                volume = int(row.get("volume") or 0)
                delta_raw = _f(row.get("delta"))

                # ── Black-Scholes delta 항상 계산 ─────────────────────────
                # yfinance의 greeks는 장외·데이터 오류 시 부정확 (delta=1.0 등).
                # IV가 유효하면 BS delta를 우선 사용하고, yfinance delta는 IV 없을 때만 폴백.
                if spot and strike and iv_raw > 0 and chosen_dte > 0:
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
                        if delta_raw is None:
                            delta_raw = 0.55 if opt_type == "call" else -0.45
                elif delta_raw is None:
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
                    "volume":      volume,
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


async def fetch_option_chains_multi(
    ticker: str,
    horizons: list[str],
    *,
    max_concurrency: int = 3,
) -> dict[str, list[dict]]:
    """
    투자 기간별 옵션 체인 병렬 수집.

    각 기간(단기/중기/장기)에 해당하는 DTE 범위에서 ATM OI가 가장 높은 만기 1개를 선택.
    DTE 범위는 shared/strategy.py 의 DTE_SHORT/MID/LONG_MIN/MAX 참조.

    Args:
        ticker:   종목
        horizons: classify_investment_horizon() 반환값 (["단기","중기","장기"] 중 부분집합)

    Returns:
        {기간: chain_list}  e.g. {"중기": [...], "장기": [...]}
    """
    from shared import strategy as _st

    _DTE_RANGES = {
        "단기":  (_st.DTE_SHORT_MIN, _st.DTE_SHORT_MAX),
        "중기":  (_st.DTE_MID_MIN,   _st.DTE_MID_MAX),
        "장기":  (_st.DTE_LONG_MIN,  _st.DTE_LONG_MAX),
        "초장기": (_st.DTE_ULTRA_MIN, _st.DTE_ULTRA_MAX),
    }

    sem = asyncio.Semaphore(max_concurrency)
    results: dict[str, list[dict]] = {}

    async def _one(horizon: str) -> None:
        dte_min, dte_max = _DTE_RANGES[horizon]
        async with sem:
            chain = await asyncio.to_thread(
                fetch_option_chain_fresh, ticker, dte_min, dte_max
            )
            if chain:
                results[horizon] = chain
            else:
                log.debug("horizon_chain_empty: %s %s (DTE %d-%d)",
                          ticker, horizon, dte_min, dte_max)

    await asyncio.gather(*[_one(h) for h in horizons if h in _DTE_RANGES])
    log.info("horizon_chains_fetched: %s requested=%s fetched=%s",
             ticker, horizons, list(results.keys()))
    return results


# ─── Finnhub 목표주가 ────────────────────────────────────────────────────────

def fetch_finnhub_price_target(ticker: str) -> Optional[float]:
    """목표주가 폴백 체인: Finnhub → yfinance

    - Finnhub /stock/price-target : 유료 플랜 전용 (403 시 다음 소스로)
    - yfinance targetMeanPrice    : 항상 가능, 단 구식 데이터 가능성 있음
    """
    import os, json, urllib.request

    # ── 1순위: Finnhub ────────────────────────────────────────────
    finnhub_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if finnhub_key:
        url = (f"https://finnhub.io/api/v1/stock/price-target"
               f"?symbol={ticker}&token={finnhub_key}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())
            val = _f(data.get("targetMean"))
            if val and val > 0:
                return val
        except Exception as exc:
            log.debug("Finnhub price_target 실패 %s: %s — yfinance 폴백", ticker, exc)

    # ── 2순위: yfinance ──────────────────────────────────────────
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        val = _f(info.get("targetMeanPrice"))
        if val and val > 0:
            log.debug("price_target yfinance 폴백 %s: %.2f", ticker, val)
            return val
    except Exception as exc:
        log.debug("yfinance price_target 실패 %s: %s", ticker, exc)

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
    """내부자 거래: Finnhub /stock/insider-transactions (무료 플랜 지원)

    반환값: insider_trans_pct (양수=순매수, 음수=순매도, 단위 %)
    """
    import os, json, urllib.request
    from datetime import date as _date, timedelta as _td

    # ── 1순위: Finnhub ────────────────────────────────────────────
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if api_key:
        today = _date.today()
        from_date = today - _td(days=lookback_months * 30)
        url = (f"https://finnhub.io/api/v1/stock/insider-transactions"
               f"?symbol={ticker}&from={from_date}&to={today}&token={api_key}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())
            transactions = data.get("data", [])
            if transactions:
                net_shares, total_shares = 0.0, 0.0
                for tx in transactions:
                    tx_type = (tx.get("transactionType") or tx.get("type") or "").upper()
                    shares = float(tx.get("share") or tx.get("shares") or 0)
                    if shares <= 0:
                        continue
                    total_shares += shares
                    if "P" in tx_type and "PURCHASE" in tx_type or tx_type == "P":
                        net_shares += shares
                    elif "S" in tx_type and "SALE" in tx_type or tx_type == "S":
                        net_shares -= shares
                if total_shares > 0:
                    return round(net_shares / total_shares * 100, 2)
        except Exception as exc:
            log.debug("Finnhub insider 실패 %s: %s", ticker, exc)

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


# ─── 어닝 캘린더 실시간 수집 (Step 3용) ─────────────────────────────────────

def fetch_earnings_calendar(ticker: str, days_ahead: int = 14) -> list:
    """어닝 캘린더: Finnhub → 빈 리스트 (FINNHUB_API_KEY 없으면 [] 반환)

    Args:
        ticker: 종목 심볼
        days_ahead: 오늘부터 조회할 일수 (기본 14일)

    Returns:
        list[SummaryEvent] — 빈 리스트 가능
    """
    import json, os, urllib.request
    from datetime import date as _date, datetime as _dt, timedelta as _td
    from shared.schemas import SummaryEvent as _SE

    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        log.debug("fetch_earnings_calendar: FINNHUB_API_KEY 없음, 스킵 (%s)", ticker)
        return []

    today = _date.today()
    to_date = today + _td(days=days_ahead)
    url = (f"https://finnhub.io/api/v1/calendar/earnings"
           f"?symbol={ticker}&from={today}&to={to_date}&token={api_key}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        events: list = []
        for e in data.get("earningsCalendar") or []:
            d_str = e.get("date", "")
            if not d_str:
                continue
            try:
                d_obj = _date.fromisoformat(d_str)
            except ValueError:
                continue
            days_until = (d_obj - today).days
            if days_until < 0:
                continue
            timing = e.get("hour", "")
            timing_str = " (AMC)" if timing == "amc" else " (BMO)" if timing == "bmo" else ""
            # EPS / 매출 예상치 파싱
            _eps_est = e.get("epsEstimate")
            _rev_est = e.get("revenueEstimate")
            _eps_f   = round(float(_eps_est), 2) if _eps_est is not None else None
            _rev_b   = round(float(_rev_est) / 1e9, 2) if _rev_est is not None else None
            events.append(_SE(
                date=_dt.fromisoformat(d_str),
                type="실적",
                name=f"{ticker} 실적 발표{timing_str}",
                importance="HIGH",
                days_until=days_until,
                eps_estimate=_eps_f,
                revenue_estimate_b=_rev_b,
            ))
        log.debug("fetch_earnings_calendar: %s → %d events", ticker, len(events))
        return events
    except Exception as exc:
        log.warning("fetch_earnings_calendar 실패 %s: %s", ticker, exc)
        return []


async def fetch_earnings_calendar_bulk(
    tickers: list[str],
    days_ahead: int = 14,
    max_concurrency: int = 3,
) -> list:
    """fetch_earnings_calendar 비동기 병렬 버전.

    Returns:
        list[SummaryEvent] — 전체 티커 이벤트 합산 (중복 없음)
    """
    sem = asyncio.Semaphore(max_concurrency)
    results: list = []

    async def _one(ticker: str) -> None:
        async with sem:
            evs = await asyncio.to_thread(fetch_earnings_calendar, ticker, days_ahead)
            results.extend(evs)

    await asyncio.gather(*[_one(t) for t in tickers])
    return results


# ─── 옵션 analytics 헬퍼 (Step 7 재계산용) ──────────────────────────────────

def _calc_atm_straddle(chain: list[dict], spot: float) -> float:
    """옵션 체인에서 ATM 스트래들 가격 계산 (call_mid + put_mid).

    Args:
        chain: fetch_option_chain_fresh() 반환 형식
               [{"option_type": "call"|"put", "strike": float, "bid": float,
                 "ask": float, "mid": float, ...}]
        spot: 현재 주가

    Returns:
        ATM 스트래들 가격 (달러). 계산 불가 시 0.0
    """
    if not chain or spot <= 0:
        return 0.0

    # spot에 가장 가까운 strike 탐색
    strikes = list({float(e.get("strike", 0)) for e in chain if e.get("strike")})
    if not strikes:
        return 0.0
    atm = min(strikes, key=lambda s: abs(s - spot))

    def _mid_price(opt_type: str) -> float:
        entry = next(
            (e for e in chain
             if e.get("option_type") == opt_type
             and abs(float(e.get("strike", 0)) - atm) < 0.01),
            None,
        )
        if not entry:
            return 0.0
        bid = float(entry.get("bid", 0) or 0)
        ask = float(entry.get("ask", 0) or 0)
        # mid 필드 우선, 없으면 bid+ask/2, 없으면 mid_price 필드
        mid = float(entry.get("mid", 0) or entry.get("mid_price", 0) or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return mid

    straddle = round(_mid_price("call") + _mid_price("put"), 3)
    log.debug("_calc_atm_straddle: spot=%.2f  atm=%.2f  straddle=%.3f",
              spot, atm, straddle)
    return straddle


def _calc_max_pain(chain: list[dict]) -> Optional[float]:
    """옵션 체인에서 Max Pain strike 계산.

    Max Pain: 모든 옵션 매수자에게 총 손실이 최대가 되는 주가
              = 옵션 매도자(발행자) 이익 최대화 지점.

    Args:
        chain: [{"option_type": "call"|"put", "strike": float, "oi": int}, ...]

    Returns:
        Max Pain strike (달러). 계산 불가 시 None
    """
    if not chain:
        return None

    strikes = sorted({float(e.get("strike", 0)) for e in chain if e.get("strike")})
    if not strikes:
        return None

    min_pain: float = float("inf")
    max_pain_strike: Optional[float] = None

    for test_price in strikes:
        pain = 0.0
        for e in chain:
            s = float(e.get("strike", 0) or 0)
            oi = int(e.get("oi", 0) or 0)
            opt_type = e.get("option_type", "")
            if opt_type == "call" and test_price > s:
                pain += (test_price - s) * oi
            elif opt_type == "put" and test_price < s:
                pain += (s - test_price) * oi
        if pain < min_pain:
            min_pain = pain
            max_pain_strike = test_price

    log.debug("_calc_max_pain: max_pain=%.2f  min_total_pain=%.0f",
              max_pain_strike or 0, min_pain)
    return max_pain_strike


def _calc_gex_levels(
    chain: list[dict], spot: float
) -> dict[str, Optional[float]]:
    """B: 옵션 체인에서 GEX 기반 가격선 계산.

    Returns:
        call_wall  : 콜 OI 최대 strike (단기 상단 저항 자석)
        put_wall   : 풋 OI 최대 strike (단기 하단 지지 자석)
        gex_flip   : Net GEX 부호 전환 strike (딜러 헤지 방향 전환 레벨)
                     None이면 해당 만기 내 전환 없음
    """
    import math as _math

    result: dict[str, Optional[float]] = {
        "call_wall": None, "put_wall": None, "gex_flip": None
    }
    if not chain or spot <= 0:
        return result

    calls = [e for e in chain if e.get("option_type") == "call"]
    puts  = [e for e in chain if e.get("option_type") == "put"]

    # Call Wall / Put Wall — OI 최대 strike
    if calls:
        result["call_wall"] = float(max(calls, key=lambda e: int(e.get("oi", 0) or 0))["strike"])
    if puts:
        result["put_wall"]  = float(max(puts,  key=lambda e: int(e.get("oi", 0) or 0))["strike"])

    # GEX per strike — gamma * OI * 100 * spot²
    # gamma = N'(d1) / (spot * iv * sqrt(T))
    def _gamma_bs(strike: float, iv_pct: float, dte: int) -> float:
        if iv_pct <= 0 or dte <= 0:
            return 0.0
        try:
            T  = dte / 365.0
            iv = iv_pct / 100.0
            d1 = (_math.log(spot / strike) + 0.5 * iv ** 2 * T) / (iv * _math.sqrt(T))
            nd1 = _math.exp(-0.5 * d1 * d1) / _math.sqrt(2 * _math.pi)
            return nd1 / (spot * iv * _math.sqrt(T))
        except (ValueError, ZeroDivisionError):
            return 0.0

    # strike별 net GEX = (call_gamma - put_gamma) * OI * 100 * spot²
    strike_gex: dict[float, float] = {}
    for e in chain:
        s    = float(e.get("strike", 0) or 0)
        oi   = int(e.get("oi", 0) or 0)
        iv   = float(e.get("iv", 0) or 0)
        dte  = int(e.get("dte", 0) or 0)
        if s <= 0 or oi == 0:
            continue
        g = _gamma_bs(s, iv, dte) * oi * 100 * spot ** 2
        if e.get("option_type") == "call":
            strike_gex[s] = strike_gex.get(s, 0.0) + g
        else:
            strike_gex[s] = strike_gex.get(s, 0.0) - g

    # GEX Flip: strikes 정렬 후 누적 net GEX 부호 전환 지점
    if len(strike_gex) >= 2:
        sorted_strikes = sorted(strike_gex.keys())
        cumulative = 0.0
        prev_sign: Optional[int] = None
        for s in sorted_strikes:
            cumulative += strike_gex[s]
            cur_sign = 1 if cumulative >= 0 else -1
            if prev_sign is not None and cur_sign != prev_sign:
                result["gex_flip"] = s
                break
            prev_sign = cur_sign

    log.debug("_calc_gex_levels: call_wall=%.2f  put_wall=%.2f  gex_flip=%s",
              result["call_wall"] or 0, result["put_wall"] or 0, result["gex_flip"])
    return result
