#!/usr/bin/env python
# scripts/validate_rre_thresholds.py
"""
RRE 임계값 검증 — 과거 주요 고점 케이스 소급 적용

목적:
  - True Positive: 실제 급락 전 RRE가 경고를 냈는가?
  - False Positive: 상승 지속 종목을 잘못 막지 않았는가?
  - 결과에 따라 임계값 조정 제안

실행:
  python scripts/validate_rre_thresholds.py
"""

import sys
import os
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

# ═══════════════════════════════════════════════════════════════
# 현재 설계안 임계값
# ═══════════════════════════════════════════════════════════════
THRESHOLDS = {
    # 차원 1: 기술적 소진
    'weekly_rsi_parabolic':   88.0,
    'weekly_adx_parabolic':   75.0,
    'sma20_gap_pct':          0.10,   # SMA20 대비 10% 이상
    'return_1m_exhaustion':   0.40,   # 1개월 40% 이상
    # 차원 6: 수급 군집
    'rvol_crowd':             1.2,    # RVOL 이 값 미만
    'return_1m_crowd':        0.30,   # 1개월 30% 이상 (RVOL 조건과 조합)
    # 차원 7: 멀티TF 역배열
    'weekly_rsi_bullish_min': 50.0,   # 주봉 RSI 이상이어야 Bullish
    # RRE 판정
    'rre_force_reject':       5,
    'rre_heavy_penalty':      3,
    'rre_light_penalty':      2,
    # 후속 하락 기준 (검증용)
    'drop_threshold_30d':    -0.15,   # 30일 내 -15% 이상 하락 = "급락"
    'drop_threshold_60d':    -0.20,   # 60일 내 -20% 이상 하락 = "급락"
}

# ═══════════════════════════════════════════════════════════════
# 테스트 케이스
# 날짜: 고점 약 1주 전 (파이프라인이 데이터를 보는 시점 시뮬레이션)
# ═══════════════════════════════════════════════════════════════
TEST_CASES = [
    # ── True Positives: 실제 급락 케이스 (RRE가 잡아야 함) ────────
    # ticker,       check_date,    peak_date,     label,       expected_drop_60d
    ("SMCI", "2024-03-01", "2024-03-08",  "TP_CRASH",  -0.70),  # 고점 $1229 → 80% 하락
    ("NVDA", "2025-01-03", "2025-01-07",  "TP_CRASH",  -0.25),  # DeepSeek 직전
    ("TSLA", "2021-10-29", "2021-11-04",  "TP_CRASH",  -0.60),  # 2022 대폭락 전
    ("META", "2021-08-27", "2021-09-01",  "TP_CRASH",  -0.70),  # 2022 대폭락 전
    ("NFLX", "2021-11-12", "2021-11-17",  "TP_CRASH",  -0.70),  # 2022 대폭락 전
    ("COIN", "2021-11-05", "2021-11-09",  "TP_CRASH",  -0.80),  # 암호화폐 폭락 전
    ("MARA", "2021-11-05", "2021-11-09",  "TP_CRASH",  -0.85),  # 암호화폐 폭락 전
    ("SMCI", "2024-08-23", "2024-08-30",  "TP_CRASH",  -0.60),  # 2차 고점
    # ── False Positives: 상승 지속 케이스 (RRE가 잡으면 안 됨) ─────
    ("NVDA", "2023-05-19", "2023-05-30",  "FP_CONTINUE", +0.50), # AI 초기 급등 → 계속 상승
    ("META", "2023-07-14", "2023-07-20",  "FP_CONTINUE", +0.40), # 2022 폭락 후 회복
    ("MSFT", "2023-06-16", "2023-06-23",  "FP_CONTINUE", +0.15), # 안정적 상승
    ("AAPL", "2023-06-16", "2023-06-23",  "FP_CONTINUE", +0.20), # 안정적 상승
    ("AMZN", "2023-07-14", "2023-07-27",  "FP_CONTINUE", +0.30), # AI 수혜 상승
    ("NVDA", "2024-02-16", "2024-02-22",  "FP_CONTINUE", +0.60), # 실적 후 급등 → 계속 상승
]

# ═══════════════════════════════════════════════════════════════
# 지표 계산 함수
# ═══════════════════════════════════════════════════════════════

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)

    up   = high - high.shift()
    down = low.shift() - low
    dm_plus  = np.where((up > down) & (up > 0),   up,   0.0)
    dm_minus = np.where((down > up) & (down > 0), down, 0.0)

    atr       = pd.Series(dm_plus,  index=df.index)  # reuse index
    atr       = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    sdi_plus  = 100 * pd.Series(dm_plus,  index=df.index).ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr
    sdi_minus = 100 * pd.Series(dm_minus, index=df.index).ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr

    dx  = 100 * (sdi_plus - sdi_minus).abs() / (sdi_plus + sdi_minus).replace(0, np.nan)
    adx = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return adx, sdi_plus, sdi_minus


def calc_macd_hist(series: pd.Series, fast=12, slow=26, signal=9) -> pd.Series:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line


def calc_rvol(volume: pd.Series, period: int = 20) -> pd.Series:
    avg_vol = volume.rolling(period).mean()
    return volume / avg_vol.replace(0, np.nan)


# ═══════════════════════════════════════════════════════════════
# RRE 차원 평가 함수
# ═══════════════════════════════════════════════════════════════

@dataclass
class RREResult:
    ticker: str
    check_date: str
    label: str
    # 계산된 지표값
    weekly_rsi:    float = 0.0
    weekly_adx:    float = 0.0
    sma20_gap_pct: float = 0.0
    return_1m:     float = 0.0
    rvol:          float = 0.0
    daily_macd_hist: float = 0.0
    weekly_bullish: bool = False
    # 차원 판정
    dim1_tech_exhaustion:  bool = False  # 기술적 소진
    dim6_crowd_risk:       bool = False  # 수급 군집
    dim7_tf_divergence:    bool = False  # 멀티TF 역배열
    rre_quantifiable_score: int = 0      # 계산 가능한 차원 수 (최대 3)
    # 후속 성과
    return_30d: Optional[float] = None
    return_60d: Optional[float] = None
    actually_crashed: Optional[bool] = None
    rre_would_reject: bool = False
    # 오류
    error: str = ""


def evaluate_rre(ticker: str, check_date: str, label: str) -> RREResult:
    result = RREResult(ticker=ticker, check_date=check_date, label=label)
    T = THRESHOLDS

    try:
        check_dt = datetime.strptime(check_date, "%Y-%m-%d")
        fetch_start = (check_dt - timedelta(days=365)).strftime("%Y-%m-%d")
        fetch_end   = (check_dt + timedelta(days=70)).strftime("%Y-%m-%d")

        # ── 데이터 수집 ──────────────────────────────────────────
        daily = yf.download(ticker, start=fetch_start, end=fetch_end,
                            interval="1d", progress=False, auto_adjust=True)
        weekly = yf.download(ticker, start=fetch_start, end=fetch_end,
                             interval="1wk", progress=False, auto_adjust=True)

        if daily.empty or len(daily) < 30:
            result.error = "데이터 부족"
            return result

        # MultiIndex 컬럼 평탄화 (yfinance 0.2+)
        if isinstance(daily.columns, pd.MultiIndex):
            daily.columns = daily.columns.get_level_values(0)
        if isinstance(weekly.columns, pd.MultiIndex):
            weekly.columns = weekly.columns.get_level_values(0)

        # check_date 이전 데이터만 (미래 정보 차단)
        daily_pre  = daily[daily.index <= check_dt].copy()
        weekly_pre = weekly[weekly.index <= check_dt].copy()

        if len(daily_pre) < 20 or len(weekly_pre) < 10:
            result.error = "check_date 이전 데이터 부족"
            return result

        # ── 주봉 지표 ────────────────────────────────────────────
        weekly_rsi_series = calc_rsi(weekly_pre['Close'])
        weekly_adx_series, weekly_di_plus, weekly_di_minus = calc_adx(weekly_pre)

        result.weekly_rsi = round(float(weekly_rsi_series.iloc[-1]), 1)
        result.weekly_adx = round(float(weekly_adx_series.iloc[-1]), 1)
        result.weekly_bullish = (
            float(weekly_di_plus.iloc[-1]) > float(weekly_di_minus.iloc[-1])
            and result.weekly_adx > 20
        )

        # ── 일봉 지표 ────────────────────────────────────────────
        close = daily_pre['Close']
        sma20 = close.rolling(20).mean()
        result.sma20_gap_pct = round(float((close.iloc[-1] - sma20.iloc[-1]) / sma20.iloc[-1]), 4)

        if len(close) >= 21:
            result.return_1m = round(float((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21]), 4)
        else:
            result.return_1m = 0.0

        volume = daily_pre['Volume']
        rvol_series = calc_rvol(volume)
        result.rvol = round(float(rvol_series.iloc[-1]), 2)

        macd_hist = calc_macd_hist(close)
        result.daily_macd_hist = round(float(macd_hist.iloc[-1]), 4)

        # ── 차원 1: 기술적 소진 ──────────────────────────────────
        # 필수: 주봉 RSI > 88
        # 보조: 주봉 ADX > 75, SMA20 이격 > 10%, 1M 수익률 > 40%
        cond_rsi  = result.weekly_rsi   > T['weekly_rsi_parabolic']
        cond_adx  = result.weekly_adx   > T['weekly_adx_parabolic']
        cond_sma  = result.sma20_gap_pct > T['sma20_gap_pct']
        cond_ret  = result.return_1m    > T['return_1m_exhaustion']
        aux_count = sum([cond_adx, cond_sma, cond_ret])
        result.dim1_tech_exhaustion = cond_rsi and (aux_count >= 2)

        # ── 차원 6: 수급 군집 (부분 계산) ────────────────────────
        # RVOL < 1.2 AND 1M 수익률 > 30%
        cond_rvol   = result.rvol      < T['rvol_crowd']
        cond_return = result.return_1m > T['return_1m_crowd']
        result.dim6_crowd_risk = cond_rvol and cond_return

        # ── 차원 7: 멀티TF 역배열 ────────────────────────────────
        # 주봉 Bullish AND 일봉 MACD Hist < 0
        result.dim7_tf_divergence = (
            result.weekly_bullish and result.daily_macd_hist < 0
        )

        # ── RRE 계산 가능 차원 점수 ───────────────────────────────
        result.rre_quantifiable_score = sum([
            result.dim1_tech_exhaustion,
            result.dim6_crowd_risk,
            result.dim7_tf_divergence,
        ])

        # 계산 불가 차원 (옵션구조, 스마트머니, 촉매소진, 펀더멘털)은
        # 현실적으로 3개 있다면 강한 신호로 간주
        # → 계산 가능 3개 중 2개 이상 = 회피 권장
        result.rre_would_reject = (result.rre_quantifiable_score >= 2)

        # ── 후속 성과 ─────────────────────────────────────────────
        daily_post = daily[daily.index > check_dt]
        ref_price  = float(close.iloc[-1])

        if len(daily_post) >= 20:
            price_30d = float(daily_post['Close'].iloc[min(19, len(daily_post)-1)])
            result.return_30d = round((price_30d - ref_price) / ref_price, 4)

        if len(daily_post) >= 40:
            price_60d = float(daily_post['Close'].iloc[min(39, len(daily_post)-1)])
            result.return_60d = round((price_60d - ref_price) / ref_price, 4)
        elif len(daily_post) > 0:
            price_last = float(daily_post['Close'].iloc[-1])
            result.return_60d = round((price_last - ref_price) / ref_price, 4)

        if result.return_60d is not None:
            result.actually_crashed = result.return_60d <= T['drop_threshold_60d']

    except Exception as e:
        result.error = str(e)[:80]

    return result


# ═══════════════════════════════════════════════════════════════
# 결과 출력
# ═══════════════════════════════════════════════════════════════

def fmt_pct(v: Optional[float], color: bool = True) -> str:
    if v is None:
        return "N/A"
    s = f"{v*100:+.1f}%"
    return s


def print_report(results: list[RREResult]):
    T = THRESHOLDS
    SEP = "═" * 110

    print(f"\n{SEP}")
    print(f"  RRE 임계값 검증 보고서")
    print(f"  계산 가능 차원: 차원1(기술소진) + 차원6(군집) + 차원7(TF역배열)  ─  3/7 차원")
    print(f"  회피 판정 기준: 계산 가능 3차원 중 2개 이상 적색 → rre_would_reject=True")
    print(f"{SEP}\n")

    # 헤더
    print(f"{'티커':<6} {'날짜':<12} {'구분':<15} "
          f"{'주W-RSI':>8} {'주W-ADX':>8} {'SMA20괴':>8} {'1M수익':>8} {'RVOL':>6} {'D-MACD':>8} "
          f"{'D1':>4} {'D6':>4} {'D7':>4} {'점수':>4} {'회피':>5} "
          f"{'30d실제':>8} {'60d실제':>8} {'급락?':>6} {'판정':>8}")
    print("─" * 110)

    tp_results, fp_results = [], []

    for r in results:
        if r.error:
            print(f"{'[ERR]':<6} {r.ticker:<6} {r.check_date:<12} {r.label:<15}  {r.error}")
            continue

        is_tp = r.label.startswith("TP")
        is_fp = r.label.startswith("FP")

        d1  = "🔴" if r.dim1_tech_exhaustion else "🟢"
        d6  = "🔴" if r.dim6_crowd_risk       else "🟢"
        d7  = "🔴" if r.dim7_tf_divergence     else "🟢"
        rej = "✅거부" if r.rre_would_reject else "❌통과"

        crashed_str = "급락✓" if r.actually_crashed else ("상승" if r.actually_crashed is False else "N/A")

        # 판정: TP는 거부해야 정답, FP는 통과해야 정답
        if is_tp:
            correct = "✅정답" if r.rre_would_reject else "❌놓침"
        else:
            correct = "✅정답" if not r.rre_would_reject else "❌오류"

        print(
            f"{r.ticker:<6} {r.check_date:<12} {r.label:<15} "
            f"{r.weekly_rsi:>8.1f} {r.weekly_adx:>8.1f} {r.sma20_gap_pct*100:>7.1f}% "
            f"{r.return_1m*100:>7.1f}% {r.rvol:>6.2f} {r.daily_macd_hist:>8.4f} "
            f"{d1:>6} {d6:>6} {d7:>6} {r.rre_quantifiable_score:>4} {rej:>7} "
            f"{fmt_pct(r.return_30d):>8} {fmt_pct(r.return_60d):>8} {crashed_str:>6} {correct:>8}"
        )

        if is_tp:
            tp_results.append(r)
        elif is_fp:
            fp_results.append(r)

    # ── 요약 통계 ─────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  요약 통계")
    print(f"{SEP}")

    tp_caught  = sum(1 for r in tp_results if r.rre_would_reject and not r.error)
    tp_total   = sum(1 for r in tp_results if not r.error)
    fp_blocked = sum(1 for r in fp_results if r.rre_would_reject and not r.error)
    fp_total   = sum(1 for r in fp_results if not r.error)

    precision_str = f"{tp_caught}/{tp_total}" if tp_total > 0 else "N/A"
    fp_rate_str   = f"{fp_blocked}/{fp_total}" if fp_total > 0 else "N/A"

    print(f"\n  True Positive (급락 케이스 포착률): {precision_str} "
          f"= {tp_caught/tp_total*100:.0f}%" if tp_total > 0 else "")
    print(f"  False Positive (멀쩡한 종목 오거부): {fp_rate_str} "
          f"= {fp_blocked/fp_total*100:.0f}%" if fp_total > 0 else "")

    # ── 임계값 민감도 분석 ─────────────────────────────────────
    print(f"\n{SEP}")
    print("  임계값 민감도 — 주봉 RSI 기준점별 TP 포착률")
    print(f"{SEP}")
    print(f"  {'RSI 임계값':>12} | {'TP 포착':>8} | {'FP 오거부':>10} | 종합 판단")
    print("  " + "─" * 60)

    for rsi_thresh in [80, 83, 85, 88, 90, 92]:
        tp_hit = sum(1 for r in tp_results
                     if not r.error and r.weekly_rsi > rsi_thresh)
        fp_hit = sum(1 for r in fp_results
                     if not r.error and r.weekly_rsi > rsi_thresh)
        tp_rate = tp_hit / tp_total * 100 if tp_total > 0 else 0
        fp_rate = fp_hit / fp_total * 100 if fp_total > 0 else 0
        marker  = " ← 현재 설계" if rsi_thresh == 88 else ""
        judge   = "너무 엄격" if tp_rate < 50 else ("적정" if fp_rate < 40 else "너무 관대")
        print(f"  {rsi_thresh:>12} | {tp_hit:>3}/{tp_total} ({tp_rate:>4.0f}%) "
              f"| {fp_hit:>3}/{fp_total} ({fp_rate:>4.0f}%) | {judge}{marker}")

    print(f"\n{SEP}")
    print("  임계값 민감도 — ADX 기준점별 (차원1 보조조건)")
    print(f"{SEP}")
    print(f"  {'ADX 임계값':>12} | {'TP 포착':>8} | {'FP 오거부':>10} | 종합 판단")
    print("  " + "─" * 60)

    for adx_thresh in [60, 65, 70, 75, 80]:
        tp_hit = sum(1 for r in tp_results
                     if not r.error and r.weekly_adx > adx_thresh)
        fp_hit = sum(1 for r in fp_results
                     if not r.error and r.weekly_adx > adx_thresh)
        tp_rate = tp_hit / tp_total * 100 if tp_total > 0 else 0
        fp_rate = fp_hit / fp_total * 100 if fp_total > 0 else 0
        marker  = " ← 현재 설계" if adx_thresh == 75 else ""
        judge   = "너무 엄격" if tp_rate < 50 else ("적정" if fp_rate < 40 else "너무 관대")
        print(f"  {adx_thresh:>12} | {tp_hit:>3}/{tp_total} ({tp_rate:>4.0f}%) "
              f"| {fp_hit:>3}/{fp_total} ({fp_rate:>4.0f}%) | {judge}{marker}")

    # ── 조정 제안 ──────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  임계값 조정 제안 (데이터 기반)")
    print(f"{SEP}\n")

    all_tp_rsi = [r.weekly_rsi for r in tp_results if not r.error]
    all_fp_rsi = [r.weekly_rsi for r in fp_results if not r.error]
    all_tp_adx = [r.weekly_adx for r in tp_results if not r.error]
    all_fp_adx = [r.weekly_adx for r in fp_results if not r.error]

    if all_tp_rsi:
        print(f"  TP 케이스 주봉 RSI 분포: min={min(all_tp_rsi):.1f}  "
              f"중앙값={sorted(all_tp_rsi)[len(all_tp_rsi)//2]:.1f}  max={max(all_tp_rsi):.1f}")
    if all_fp_rsi:
        print(f"  FP 케이스 주봉 RSI 분포: min={min(all_fp_rsi):.1f}  "
              f"중앙값={sorted(all_fp_rsi)[len(all_fp_rsi)//2]:.1f}  max={max(all_fp_rsi):.1f}")
    if all_tp_adx:
        print(f"  TP 케이스 주봉 ADX 분포: min={min(all_tp_adx):.1f}  "
              f"중앙값={sorted(all_tp_adx)[len(all_tp_adx)//2]:.1f}  max={max(all_tp_adx):.1f}")
    if all_fp_adx:
        print(f"  FP 케이스 주봉 ADX 분포: min={min(all_fp_adx):.1f}  "
              f"중앙값={sorted(all_fp_adx)[len(all_fp_adx)//2]:.1f}  max={max(all_fp_adx):.1f}")

    # TP/FP RSI 분리선 찾기
    if all_tp_rsi and all_fp_rsi:
        best_thresh, best_score = 50, -999
        for t in range(60, 96):
            tp_hit = sum(1 for v in all_tp_rsi if v > t)
            fp_hit = sum(1 for v in all_fp_rsi if v > t)
            score  = tp_hit - fp_hit * 1.5  # FP 패널티
            if score > best_score:
                best_score, best_thresh = score, t
        print(f"\n  📌 RSI 최적 분리 임계값 (TP 최대화, FP 최소화): {best_thresh}")

    if all_tp_adx and all_fp_adx:
        best_thresh, best_score = 50, -999
        for t in range(50, 91):
            tp_hit = sum(1 for v in all_tp_adx if v > t)
            fp_hit = sum(1 for v in all_fp_adx if v > t)
            score  = tp_hit - fp_hit * 1.5
            if score > best_score:
                best_score, best_thresh = score, t
        print(f"  📌 ADX 최적 분리 임계값 (TP 최대화, FP 최소화): {best_thresh}")

    print(f"\n{SEP}\n")


# ═══════════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════════

def main():
    print("\n데이터 수집 중... (yfinance, 약 30~60초 소요)\n")

    results = []
    for i, (ticker, check_date, peak_date, label, _expected) in enumerate(TEST_CASES):
        print(f"  [{i+1:02d}/{len(TEST_CASES)}] {ticker} @ {check_date} ({label})", end="", flush=True)
        r = evaluate_rre(ticker, check_date, label)
        results.append(r)
        if r.error:
            print(f" → ERROR: {r.error}")
        else:
            print(f" → RSI {r.weekly_rsi:.1f} / ADX {r.weekly_adx:.1f} / "
                  f"RRE {r.rre_quantifiable_score}/3 / "
                  f"{'거부' if r.rre_would_reject else '통과'}")

    print_report(results)


if __name__ == "__main__":
    main()
