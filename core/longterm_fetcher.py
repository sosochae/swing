"""
core/longterm_fetcher.py
========================
장기투자 정량 데이터 수집 및 계산

fetch_stock_detail() (api_fetcher)로 단발성 지표를 가져오고,
ticker.financials / balance_sheet / cashflow로 5년 재무 시리즈를 추가한다.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Optional

from core.api_fetcher import _f, _pct
from shared.config import get_config
from shared.longterm_schemas import (
    BalanceSheetData,
    FinancialData,
    LongtermInput,
    MarketSignals,
    QualityMetrics,
    ShareholderReturnData,
    ValuationData,
)

log = logging.getLogger(__name__)
cfg = get_config()


# ─────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────

def _b(v) -> Optional[float]:
    """raw USD → 억달러"""
    f = _f(v)
    return round(f / 1e8, 2) if f is not None else None


def _cagr(start: float, end: float, years: int) -> Optional[float]:
    if years <= 0 or start is None or end is None or start <= 0:
        return None
    return round(((end / start) ** (1.0 / years) - 1) * 100, 2)


def _safe_div(a, b) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return round(a / b, 4)


# ─────────────────────────────────────────────────────────────
# 5년 재무제표 수집
# ─────────────────────────────────────────────────────────────

def _fetch_financials_sync(ticker: str) -> tuple[
    FinancialData, QualityMetrics, BalanceSheetData, ShareholderReturnData
]:
    import yfinance as yf
    import pandas as pd

    tk = yf.Ticker(ticker)
    info: dict = tk.info or {}

    # ── Annual 재무제표 (최신순 정렬) ────────────────────────────
    fin = tk.financials          # 손익계산서
    bs  = tk.balance_sheet       # 대차대조표
    cf  = tk.cashflow            # 현금흐름표

    def _col_values(df, *row_names) -> list[Optional[float]]:
        """여러 row_name 중 첫 번째로 존재하는 행의 값을 연도순(최신→구)으로 반환"""
        if df is None or df.empty:
            return []
        for name in row_names:
            if name in df.index:
                return [_b(v) for v in df.loc[name].tolist()]
        return []

    def _col_years(df) -> list[int]:
        if df is None or df.empty:
            return []
        return [int(str(c)[:4]) for c in df.columns]

    years = _col_years(fin)
    n = min(len(years), cfg.LT_HISTORY_YEARS)
    years = years[:n]

    # 손익
    revenue_raw       = _col_values(fin, "Total Revenue")[:n]
    op_income_raw     = _col_values(fin, "Operating Income", "EBIT")[:n]
    net_income_raw    = _col_values(fin, "Net Income")[:n]
    ebitda_raw        = _col_values(fin, "EBITDA", "Normalized EBITDA")[:n]
    rd_raw            = _col_values(fin, "Research And Development")[:n]
    interest_exp_raw  = _col_values(fin, "Interest Expense")[:n]

    # EPS (직접 계산: 순이익 / 발행주식수)
    shares_raw = _col_values(bs, "Ordinary Shares Number", "Share Issued", "Common Stock")[:n]
    eps_list: list[Optional[float]] = []
    for ni, sh in zip(net_income_raw, shares_raw):
        if ni is not None and sh is not None and sh != 0:
            # ni는 억달러 단위이므로 실제 달러로 환산 후 주당 계산
            eps_list.append(round((ni * 1e8) / (sh * 1e8 if sh < 1000 else sh), 2))
        else:
            eps_list.append(None)

    # FCF = 영업현금흐름 - CAPEX
    op_cf_raw   = _col_values(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")[:n]
    capex_raw   = _col_values(cf, "Capital Expenditure", "Purchase Of PPE")[:n]
    fcf_list: list[Optional[float]] = []
    for ocf, cx in zip(op_cf_raw, capex_raw):
        if ocf is not None and cx is not None:
            fcf_list.append(round(ocf - abs(cx), 2))
        elif ocf is not None:
            fcf_list.append(ocf)
        else:
            fcf_list.append(None)

    financial = FinancialData(
        years=years,
        revenue=revenue_raw,
        operating_income=op_income_raw,
        net_income=net_income_raw,
        eps=eps_list,
        fcf=fcf_list,
        ebitda=ebitda_raw,
    )

    # ── 마진 시리즈 ─────────────────────────────────────────────
    def _margin_series(numerator: list, denominator: list) -> list[Optional[float]]:
        out = []
        for num, den in zip(numerator, denominator):
            if num is not None and den is not None and den != 0:
                out.append(round(num / den * 100, 2))
            else:
                out.append(None)
        return out

    op_margin_list    = _margin_series(op_income_raw, revenue_raw)
    net_margin_list   = _margin_series(net_income_raw, revenue_raw)
    ebitda_margin_list= _margin_series(ebitda_raw, revenue_raw)
    fcf_margin_list   = _margin_series(fcf_list, revenue_raw)

    # ── CAGR ────────────────────────────────────────────────────
    rev_cagr_3y = rev_cagr_5y = eps_cagr_3y = fcf_cagr_3y = None
    if len(revenue_raw) >= 4 and revenue_raw[0] and revenue_raw[3]:
        rev_cagr_3y = _cagr(revenue_raw[3], revenue_raw[0], 3)
    if len(revenue_raw) >= 6 and revenue_raw[0] and revenue_raw[5]:
        rev_cagr_5y = _cagr(revenue_raw[5], revenue_raw[0], 5)
    elif len(revenue_raw) >= 5 and revenue_raw[0] and revenue_raw[4]:
        rev_cagr_5y = _cagr(revenue_raw[4], revenue_raw[0], 4)
    if len(eps_list) >= 4 and eps_list[0] and eps_list[3] and eps_list[3] > 0:
        eps_cagr_3y = _cagr(eps_list[3], eps_list[0], 3)
    if len(fcf_list) >= 4 and fcf_list[0] and fcf_list[3] and fcf_list[3] > 0:
        fcf_cagr_3y = _cagr(fcf_list[3], fcf_list[0], 3)

    # ── ROE / ROA / ROIC ────────────────────────────────────────
    roe = _pct(info.get("returnOnEquity"))
    roa = _pct(info.get("returnOnAssets"))

    roic: Optional[float] = None
    try:
        # ROIC = EBIT(1-t) / 투하자본 (총자산 - 유동부채 - 현금)
        if not fin.empty and not bs.empty and op_income_raw:
            ebit = op_income_raw[0]
            tax_rate = 0.21
            nopat = ebit * (1 - tax_rate) if ebit else None
            total_assets = _col_values(bs, "Total Assets")
            curr_liab    = _col_values(bs, "Current Liabilities", "Total Current Liabilities")
            cash_eq      = _col_values(bs, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
            if nopat and total_assets and curr_liab:
                ta = total_assets[0]
                cl = curr_liab[0]
                ce = cash_eq[0] if cash_eq else 0
                invested_cap = ta - (cl or 0) - (ce or 0) if ta else None
                if invested_cap and invested_cap > 0:
                    roic = round(nopat / invested_cap * 100, 2)
    except Exception as e:
        log.debug("roic_calc_fail: %s", e)

    # ── R&D 비율 ────────────────────────────────────────────────
    rd_ratio: Optional[float] = None
    if rd_raw and revenue_raw and rd_raw[0] and revenue_raw[0] and revenue_raw[0] != 0:
        rd_ratio = round(abs(rd_raw[0]) / revenue_raw[0] * 100, 2)

    quality = QualityMetrics(
        op_margin=op_margin_list,
        net_margin=net_margin_list,
        ebitda_margin=ebitda_margin_list,
        fcf_margin=fcf_margin_list,
        roe=roe,
        roa=roa,
        roic=roic,
        revenue_cagr_3y=rev_cagr_3y,
        revenue_cagr_5y=rev_cagr_5y,
        eps_cagr_3y=eps_cagr_3y,
        fcf_cagr_3y=fcf_cagr_3y,
        rd_ratio=rd_ratio,
    )

    # ── 재무 건전성 ─────────────────────────────────────────────
    debt_to_equity = _f(info.get("debtToEquity"))
    current_ratio  = _f(info.get("currentRatio"))

    # 순부채 (억달러)
    total_debt = _col_values(bs, "Total Debt", "Long Term Debt")
    cash_vals  = _col_values(bs, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
    net_debt: Optional[float] = None
    if total_debt and cash_vals and total_debt[0] is not None and cash_vals[0] is not None:
        net_debt = round(total_debt[0] - cash_vals[0], 2)

    # 현금성 자산
    cash: Optional[float] = cash_vals[0] if cash_vals else _b(info.get("totalCash"))

    # 이자보상배율 = EBIT / 이자비용
    interest_coverage: Optional[float] = None
    if op_income_raw and interest_exp_raw and op_income_raw[0] and interest_exp_raw[0]:
        ie = abs(interest_exp_raw[0])
        if ie and ie > 0:
            interest_coverage = round(op_income_raw[0] / ie, 1)

    balance = BalanceSheetData(
        debt_to_equity=debt_to_equity,
        net_debt=net_debt,
        interest_coverage=interest_coverage,
        current_ratio=current_ratio,
        cash=cash,
    )

    # ── 주주환원 ────────────────────────────────────────────────
    dividend_yield = _pct(info.get("dividendYield"))
    fcf_ttm_raw = _b(info.get("freeCashflow"))

    # 자사주매입 5년 누적 (cashflow: Repurchase Of Capital Stock)
    buyback_raw = _col_values(cf, "Repurchase Of Capital Stock", "Common Stock Repurchased")
    buyback_5y: Optional[float] = None
    if buyback_raw:
        valid = [abs(v) for v in buyback_raw if v is not None]
        if valid:
            buyback_5y = round(sum(valid), 2)

    # FCF 환원율 = (자사주매입 + 배당) / FCF
    fcf_payout: Optional[float] = None
    try:
        div_paid = _col_values(cf, "Payment Of Dividends", "Cash Dividends Paid")
        if fcf_list and fcf_list[0] and fcf_list[0] > 0:
            bb1 = abs(buyback_raw[0]) if buyback_raw and buyback_raw[0] else 0
            dp1 = abs(div_paid[0]) if div_paid and div_paid[0] else 0
            fcf_payout = round((bb1 + dp1) / fcf_list[0] * 100, 1)
    except Exception:
        pass

    sr = ShareholderReturnData(
        buyback_5y=buyback_5y,
        dividend_yield=dividend_yield,
        fcf_payout_ratio=fcf_payout,
    )

    return financial, quality, balance, sr


# ─────────────────────────────────────────────────────────────
# 밸류에이션
# ─────────────────────────────────────────────────────────────

def _fetch_valuation_sync(ticker: str, financial: FinancialData) -> ValuationData:
    import yfinance as yf

    tk   = yf.Ticker(ticker)
    info = tk.info or {}

    price     = _f(info.get("currentPrice") or info.get("regularMarketPrice"))
    pe_trail  = _f(info.get("trailingPE"))
    pe_fwd    = _f(info.get("forwardPE"))
    pbr       = _f(info.get("priceToBook"))
    psr       = _f(info.get("priceToSalesTrailing12Months"))
    ev_ebitda = _f(info.get("enterpriseToEbitda"))
    peg       = _f(info.get("trailingPegRatio") or info.get("pegRatio"))
    market_cap = _f(info.get("marketCap"))

    # FCF Yield = FCF / 시총
    fcf_ttm = _f(info.get("freeCashflow"))
    fcf_yield: Optional[float] = None
    if fcf_ttm and market_cap and market_cap > 0:
        fcf_yield = round(fcf_ttm / market_cap * 100, 2)

    # 역사적 PER 범위 (5년 주가 + EPS 기반 간이 추정)
    hist_pe_avg = hist_pe_high = hist_pe_low = hist_pe_pct = None
    try:
        hist = tk.history(period="5y", auto_adjust=True)
        eps_annual = tk.earnings_history
        if hist is not None and not hist.empty and eps_annual is not None and not eps_annual.empty:
            annual_eps = eps_annual["epsActual"].dropna()
            if len(annual_eps) >= 4:
                avg_eps = float(annual_eps.tail(8).mean())
                if avg_eps > 0:
                    pe_series = hist["Close"] / avg_eps
                    pe_series = pe_series[pe_series > 0]
                    hist_pe_avg  = round(float(pe_series.mean()), 1)
                    hist_pe_high = round(float(pe_series.quantile(0.95)), 1)
                    hist_pe_low  = round(float(pe_series.quantile(0.05)), 1)
                    if pe_trail and hist_pe_high and hist_pe_low and hist_pe_high > hist_pe_low:
                        hist_pe_pct = round(
                            (pe_trail - hist_pe_low) / (hist_pe_high - hist_pe_low) * 100, 1
                        )
    except Exception as e:
        log.debug("hist_pe_fail: %s", e)

    # DCF 적정가 (FCF 5년 성장, 이후 터미널)
    dcf_fair = dcf_upside = None
    try:
        fcf_base = fcf_ttm
        shares = _f(info.get("sharesOutstanding"))
        if fcf_base and shares and shares > 0:
            g  = cfg.LT_DCF_GROWTH_RATE
            tg = cfg.LT_DCF_TERMINAL_RATE
            r  = cfg.LT_DCF_DISCOUNT_RATE
            pv = 0.0
            fcf_t = fcf_base
            for t in range(1, 6):
                fcf_t *= (1 + g)
                pv += fcf_t / (1 + r) ** t
            terminal = fcf_t * (1 + tg) / (r - tg)
            pv += terminal / (1 + r) ** 5
            dcf_fair = round(pv / shares, 2)
            if price and price > 0:
                dcf_upside = round((dcf_fair / price - 1) * 100, 1)
    except Exception as e:
        log.debug("dcf_fail: %s", e)

    # 밸류에이션 종합 판단
    verdict = ""
    if pe_trail and hist_pe_avg:
        ratio = pe_trail / hist_pe_avg
        if ratio < 0.85:
            verdict = "저평가"
        elif ratio < 1.10:
            verdict = "적정"
        elif ratio < 1.30:
            verdict = "소폭 고평가"
        else:
            verdict = "고평가"

    return ValuationData(
        pe_trailing=pe_trail,
        pe_forward=pe_fwd,
        pbr=pbr,
        psr=psr,
        ev_ebitda=ev_ebitda,
        peg=peg,
        fcf_yield=fcf_yield,
        hist_pe_avg=hist_pe_avg,
        hist_pe_high=hist_pe_high,
        hist_pe_low=hist_pe_low,
        hist_pe_pct=hist_pe_pct,
        dcf_fair_value=dcf_fair,
        dcf_upside=dcf_upside,
        valuation_verdict=verdict,
    )


# ─────────────────────────────────────────────────────────────
# 시장 신호
# ─────────────────────────────────────────────────────────────

def _fetch_market_signals_sync(ticker: str) -> MarketSignals:
    """fetch_stock_detail의 지표를 MarketSignals로 재포장."""
    import yfinance as yf
    import numpy as np

    tk   = yf.Ticker(ticker)
    info = tk.info or {}

    price      = _f(info.get("currentPrice") or info.get("regularMarketPrice"))
    market_cap = _b(info.get("marketCap"))
    w52_high   = _f(info.get("fiftyTwoWeekHigh"))
    w52_low    = _f(info.get("fiftyTwoWeekLow"))

    w52_pos: Optional[float] = None
    if price and w52_high and w52_low and w52_high > w52_low:
        w52_pos = round((price - w52_low) / (w52_high - w52_low) * 100, 1)

    # SMA200 대비 위치 (%)
    sma200_pct: Optional[float] = None
    try:
        hist = tk.history(period="1y", auto_adjust=True)
        closes = hist["Close"].dropna().tolist()
        if len(closes) >= 200:
            sma200 = float(np.mean(closes[-200:]))
            curr   = float(closes[-1])
            if sma200 > 0:
                sma200_pct = round((curr - sma200) / sma200 * 100, 2)
    except Exception:
        pass

    short_float    = _pct(info.get("shortPercentOfFloat"))
    inst_pct       = _pct(info.get("institutionPercentHeld"))
    target_price   = _f(info.get("targetMeanPrice"))
    target_upside: Optional[float] = None
    if price and target_price and price > 0:
        target_upside = round((target_price / price - 1) * 100, 1)

    # 애널리스트 카운트
    analyst_buy = analyst_hold = analyst_sell = None
    try:
        rec = tk.recommendations_summary
        if rec is not None and not rec.empty:
            row = rec.iloc[0]
            analyst_buy  = int(row.get("strongBuy", 0) or 0) + int(row.get("buy", 0) or 0)
            analyst_hold = int(row.get("hold", 0) or 0)
            analyst_sell = int(row.get("sell", 0) or 0) + int(row.get("strongSell", 0) or 0)
    except Exception:
        pass

    # 어닝 서프라이즈 이력 (최근 4분기)
    surprise_hist: list[dict] = []
    try:
        import pandas as pd
        eh = tk.earnings_history
        ed = tk.earnings_dates
        if eh is not None and not eh.empty:
            price_hist = tk.history(period="2y", auto_adjust=True)
            price_hist.index = price_hist.index.tz_localize(None)
            today_ts = pd.Timestamp.today().tz_localize(None)
            past = [dt for dt in ed.index
                    if pd.Timestamp(dt).tz_localize(None) <= today_ts] if ed is not None else []
            recent = eh.tail(4).iloc[::-1]
            for i, (_, row) in enumerate(recent.iterrows()):
                est  = _f(row.get("epsEstimate"))
                act  = _f(row.get("epsActual"))
                if est is None or act is None:
                    continue
                surp = round((act - est) / abs(est) * 100, 1) if est != 0 else 0.0
                move: Optional[float] = None
                if i < len(past):
                    earn_ts = pd.Timestamp(past[i]).tz_localize(None)
                    before  = price_hist[price_hist.index < earn_ts]
                    after   = price_hist[price_hist.index >= earn_ts]
                    if not before.empty and not after.empty:
                        move = round((float(after.iloc[0]["Open"]) - float(before.iloc[-1]["Close"]))
                                     / float(before.iloc[-1]["Close"]) * 100, 1)
                surprise_hist.append({
                    "date": str(past[i])[:10] if i < len(past) else "",
                    "surprise_pct": surp,
                    "price_move_pct": move,
                })
    except Exception as e:
        log.debug("surprise_hist_fail: %s", e)

    return MarketSignals(
        price=price,
        market_cap=market_cap,
        w52_high=w52_high,
        w52_low=w52_low,
        w52_position_pct=w52_pos,
        sma200_pct=sma200_pct,
        short_float_pct=short_float,
        inst_ownership_pct=inst_pct,
        analyst_buy=analyst_buy,
        analyst_hold=analyst_hold,
        analyst_sell=analyst_sell,
        target_price=target_price,
        target_upside=target_upside,
        earnings_surprise_history=surprise_hist,
    )


# ─────────────────────────────────────────────────────────────
# 회사 기본 정보 (이름, 섹터, 사업 설명)
# ─────────────────────────────────────────────────────────────

def _fetch_company_info_sync(ticker: str) -> dict:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        return {
            "company_name": info.get("longName") or info.get("shortName") or ticker,
            "sector":       info.get("sector") or "",
            "industry":     info.get("industry") or "",
            "description":  info.get("longBusinessSummary") or "",
            "employees":    info.get("fullTimeEmployees"),
            "country":      info.get("country") or "",
            "website":      info.get("website") or "",
        }
    except Exception:
        return {"company_name": ticker, "sector": "", "industry": "", "description": ""}


# ─────────────────────────────────────────────────────────────
# 통합 fetch (비동기 진입점)
# ─────────────────────────────────────────────────────────────

async def fetch_all(inp: LongtermInput) -> dict:
    """
    모든 정량 데이터를 수집해 딕셔너리로 반환.

    Returns:
        {
            "company_info": dict,
            "financial": FinancialData,
            "quality": QualityMetrics,
            "balance_sheet": BalanceSheetData,
            "shareholder_return": ShareholderReturnData,
            "valuation": ValuationData,
            "market_signals": MarketSignals,
        }
    """
    loop = asyncio.get_event_loop()
    ticker = inp.ticker.upper()

    # 재무제표 + 회사 기본 정보는 병렬 실행
    (financial, quality, balance, sr), company_info = await asyncio.gather(
        loop.run_in_executor(None, _fetch_financials_sync, ticker),
        loop.run_in_executor(None, _fetch_company_info_sync, ticker),
    )

    # 밸류에이션, 시장 신호는 financial 결과 필요 없이 독립적이므로 병렬
    valuation, market_signals = await asyncio.gather(
        loop.run_in_executor(None, _fetch_valuation_sync, ticker, financial),
        loop.run_in_executor(None, _fetch_market_signals_sync, ticker),
    )

    # LongtermInput 보완 (yfinance로 가져온 이름/섹터 채우기)
    if not inp.company_name:
        inp.company_name = company_info.get("company_name", ticker)
    if not inp.sector:
        inp.sector = company_info.get("sector", "")
    if not inp.description:
        inp.description = company_info.get("description", "")

    log.info(
        "longterm_fetch_done ticker=%s rev_cagr_3y=%s roic=%s dcf=%s",
        ticker,
        quality.revenue_cagr_3y,
        quality.roic,
        valuation.dcf_fair_value,
    )

    return {
        "company_info": company_info,
        "financial":    financial,
        "quality":      quality,
        "balance_sheet": balance,
        "shareholder_return": sr,
        "valuation":    valuation,
        "market_signals": market_signals,
    }
