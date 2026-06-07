"""
core/parsers.py
===============
파일 파서 통합 모듈 (T3 최적화: finviz·summary·earnings·positions 4개 → 1개)

담당:
- finviz_all_rows.txt → list[FinvizRow]
- summary_*.json → SummaryData
- 어닝_분석.md → list[EarningsAnalysis]
- positions.md → list[Position]

모두 비동기 I/O로 구현하며, 파싱 실패 시 ValidationError를 발생시킵니다.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from shared.logger import get_logger
from shared.schemas import (
    EarningsAnalysis,
    FinvizDetail,
    FinvizRow,
    KavoutRow,
    OptionChainEntry,
    PartialExit,
    Position,
    SummaryData,
    SummaryEvent,
    SummaryMacro,
    SummaryRiskParams,
    TickerOptions,
    TickerSummary,
    TickerTechnical,
    TickerValuation,
)

log = get_logger()


# ─────────────────────────────────────────────────────────────
# 1. Finviz 파서
# ─────────────────────────────────────────────────────────────

def parse_market_cap(raw: str) -> float:
    """
    '5060.96B' → 5_060_960_000_000.0
    '964.51M' → 964_510_000.0

    Args:
        raw: 시가총액 원시 문자열

    Returns:
        USD 단위 float
    """
    raw = raw.strip()
    if raw.endswith("T"):
        return float(raw[:-1]) * 1e12
    if raw.endswith("B"):
        return float(raw[:-1]) * 1e9
    if raw.endswith("M"):
        return float(raw[:-1]) * 1e6
    return float(raw)


def parse_finviz_row(block: str) -> FinvizRow | None:
    """
    ROW 블록 문자열 하나를 FinvizRow로 파싱

    Args:
        block: '[ROW N]\\n  COL 1: ...' 형식 텍스트

    Returns:
        FinvizRow 또는 파싱 실패 시 None
    """
    def col(n: int) -> str:
        pattern = rf"COL {n}:\s*(.+)"
        m = re.search(pattern, block)
        return m.group(1).strip() if m else ""

    try:
        rank = int(col(1))
        ticker = col(2).upper()
        company = col(3)
        sector = col(4)
        industry = col(5)
        country = col(6)
        mktcap_str = col(7)
        pe_str = col(8)
        price_str = col(9)
        change_str = col(10)
        volume_str = col(11).replace(",", "")

        pe = None if pe_str in ("-", "", "N/A") else float(pe_str)
        market_cap = parse_market_cap(mktcap_str) if mktcap_str else 0.0
        change_pct = float(change_str.replace("%", "")) if change_str else 0.0

        return FinvizRow(
            rank=rank,
            ticker=ticker,
            company_name=company,
            sector=sector,
            industry=industry,
            country=country,
            market_cap=market_cap,
            pe_ratio=pe,
            price=float(price_str) if price_str else 0.0,
            change_pct=change_pct,
            volume=int(volume_str) if volume_str.isdigit() else 0,
        )
    except Exception as exc:
        log.warning("finviz_row_parse_error", error=str(exc), block=block[:80])
        return None


def parse_finviz(file_path: Path) -> list[FinvizRow]:
    """
    finviz_all_rows.txt 전체 파싱

    Args:
        file_path: finviz_all_rows.txt 경로

    Returns:
        FinvizRow 리스트 (파싱 실패 행 제외)

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때
    """
    if not file_path.exists():
        raise FileNotFoundError(f"finviz file not found: {file_path}")

    content = file_path.read_text(encoding="utf-8", errors="replace")

    # [ROW N] 블록 분리
    blocks = re.split(r"\[ROW \d+\]", content)
    blocks = [b.strip() for b in blocks if b.strip()]

    rows: list[FinvizRow] = []
    for i, block in enumerate(blocks, start=1):
        # ROW 헤더 복원
        full_block = f"[ROW {i}]\n" + block
        row = parse_finviz_row(full_block)
        if row:
            rows.append(row)

    log.info("finviz_parsed", total=len(rows), file=str(file_path))
    return rows


# ─────────────────────────────────────────────────────────────
# 2. Summary 파서
# ─────────────────────────────────────────────────────────────

def _parse_macro_from_text(text: str) -> SummaryMacro:
    """
    summary JSON의 MACRO 섹션 텍스트에서 거시 지표를 추출합니다.
    """
    def extract(pattern: str, default: float = 0.0) -> float:
        m = re.search(pattern, text)
        return float(m.group(1)) if m else default

    def extract_int(pattern: str, default: int = 50) -> int:
        m = re.search(pattern, text)
        return int(m.group(1)) if m else default

    return SummaryMacro(
        sp500=extract(r"S&P500\s*:\s*([\d.]+)"),
        sp500_ma20=extract(r"S&P500.*?20MA:([\d.]+)"),
        nasdaq=extract(r"NASDAQ\s*:\s*([\d.]+)"),
        nasdaq_ma20=extract(r"NASDAQ.*?20MA:([\d.]+)"),
        spy=extract(r"SPY\s*:\s*([\d.]+)"),
        spy_ma20=extract(r"SPY.*?20MA:([\d.]+)"),
        qqq=extract(r"QQQ\s*:\s*([\d.]+)"),
        qqq_ma20=extract(r"QQQ.*?20MA:([\d.]+)"),
        vix=extract(r"VIX\s*:\s*([\d.]+)"),
        vix_ma20=extract(r"VIX.*?20MA:([\d.]+)"),
        dxy=extract(r"DXY\s*:\s*([\d.]+)"),
        dxy_ma20=extract(r"DXY.*?20MA:([\d.]+)"),
        yield_10y=extract(r"10Y 금리\s*:\s*([\d.]+)"),
        gold=extract(r"GOLD\s*:\s*([\d.]+)"),
        oil_wti=extract(r"OIL\(WTI\)\s*:\s*([\d.]+)"),
        soxx=extract(r"SOXX\s*:\s*([\d.]+)"),
        soxx_ma20=extract(r"SOXX.*?20MA:([\d.]+)"),
        fear_greed=extract_int(r"Fear & Greed\s*:\s*(\d+)"),
        fear_greed_label=_extract_str(r"Fear & Greed\s*:\s*\d+\s+(\w+)", text),
        fed_funds_rate=extract(r"Fed Funds Rate\s*:\s*([\d.]+)"),
        cpi_yoy=extract(r"CPI YoY\s*:\s*([\d.]+)"),
        unemployment=extract(r"실업률\s*:\s*([\d.]+)"),
        pce=extract(r"PCE\s*:\s*([\d.]+)"),
    )


def _extract_str(pattern: str, text: str, default: str = "") -> str:
    m = re.search(pattern, text)
    return m.group(1) if m else default


def _parse_risk_params_from_text(text: str) -> SummaryRiskParams:
    """
    summary JSON market_str 텍스트에서 리스크 파라미터를 추출합니다.

    지원 패턴 (한국어 / 영어 혼용):
      총 자본: $3,000  /  Total Capital: $3,000  /  TOTAL_CAPITAL: 3000
      포지션당 한도: $1,000  /  Max Per Position: $1,000
      계약당 수수료: $5  /  Commission: $5
      보유 목표: 3~5일  /  Target Holding: 3~5일
      사용 중인 자본: $500  /  Capital In Use: $500
      잔여 현금: $2,500  /  Remaining Cash: $2,500
    """
    def _extract_money(text: str, *patterns: str) -> float | None:
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                raw = m.group(1).replace(",", "").strip()
                try:
                    return float(raw)
                except ValueError:
                    pass
        return None

    def _extract_str_val(text: str, *patterns: str) -> str | None:
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    defaults = SummaryRiskParams()

    total_capital = _extract_money(
        text,
        r"총\s*자본\s*:\s*\$?([\d,]+(?:\.\d+)?)",
        r"Total\s*Capital\s*:\s*\$?([\d,]+(?:\.\d+)?)",
        r"TOTAL_CAPITAL\s*[=:]\s*([\d,]+(?:\.\d+)?)",
    )
    max_per_position = _extract_money(
        text,
        r"포지션당\s*(?:한도|최대)\s*:\s*\$?([\d,]+(?:\.\d+)?)",
        r"Max\s*Per\s*Position\s*:\s*\$?([\d,]+(?:\.\d+)?)",
        r"MAX_PER_POSITION\s*[=:]\s*([\d,]+(?:\.\d+)?)",
    )
    commission = _extract_money(
        text,
        r"계약당\s*수수료\s*:\s*\$?([\d,]+(?:\.\d+)?)",
        r"Commission(?:\s*Per\s*Contract)?\s*:\s*\$?([\d,]+(?:\.\d+)?)",
        r"COMMISSION_PER_CONTRACT\s*[=:]\s*([\d,]+(?:\.\d+)?)",
    )
    holding_days = _extract_str_val(
        text,
        r"보유\s*목표\s*:\s*([^\n\|]+)",
        r"Target\s*Holding\s*:\s*([^\n\|]+)",
    )
    capital_in_use = _extract_money(
        text,
        r"사용\s*(?:중인)?\s*자본\s*:\s*\$?([\d,]+(?:\.\d+)?)",
        r"Capital\s*In\s*Use\s*:\s*\$?([\d,]+(?:\.\d+)?)",
    )
    remaining_cash = _extract_money(
        text,
        r"잔여\s*현금\s*:\s*\$?([\d,]+(?:\.\d+)?)",
        r"Remaining\s*Cash\s*:\s*\$?([\d,]+(?:\.\d+)?)",
    )

    # remaining_cash를 텍스트에서 못 찾은 경우, total - in_use 로 추정
    if remaining_cash is None and total_capital is not None and capital_in_use is not None:
        remaining_cash = max(0.0, total_capital - capital_in_use)

    return SummaryRiskParams(
        total_capital=total_capital       if total_capital       is not None else defaults.total_capital,
        max_per_position=max_per_position if max_per_position     is not None else defaults.max_per_position,
        commission_per_contract=commission if commission          is not None else defaults.commission_per_contract,
        target_holding_days=holding_days  if holding_days         is not None else defaults.target_holding_days,
        capital_in_use=capital_in_use     if capital_in_use       is not None else defaults.capital_in_use,
        remaining_cash=remaining_cash     if remaining_cash        is not None else defaults.remaining_cash,
    )


def _parse_risk_params_from_dict(raw: dict) -> SummaryRiskParams:
    """딕셔너리 형식 summary에서 리스크 파라미터를 추출합니다."""
    rp = raw.get("risk_params", {})
    if not rp:
        return SummaryRiskParams()
    try:
        return SummaryRiskParams(**{k: v for k, v in rp.items() if v is not None})
    except Exception:
        return SummaryRiskParams()


def _parse_ticker_technical(data: dict[str, Any]) -> TickerTechnical:
    """딕셔너리에서 TickerTechnical 생성"""
    def sf(key: str, default: float = 0.0) -> float:
        return _safe_float(data.get(key)) or default

    # ── 최근 3일 OHLC 파싱 ──────────────────────────────────────
    # tech_data에 "OHLC 2026-05-20": "O:734.96  H:735.68  L:700.66  C:731.99  V:48,827,400  RVOL:97%"
    # 형태로 저장되어 있음 (키-값 정규식이 자동으로 추출)
    recent_ohlc: list[dict] = []
    for key, val in data.items():
        if not key.startswith("OHLC "):
            continue
        ohlc_date = key[5:].strip()  # "OHLC " 제거 → "2026-05-20"
        val_str = str(val)
        o_m = re.search(r"O:([\d.]+)", val_str)
        h_m = re.search(r"H:([\d.]+)", val_str)
        l_m = re.search(r"L:([\d.]+)", val_str)
        c_m = re.search(r"C:([\d.]+)", val_str)
        v_m = re.search(r"V:([\d,]+)", val_str)
        rvol_m = re.search(r"RVOL:([\d.]+)%", val_str)
        if o_m and c_m:
            recent_ohlc.append({
                "date": ohlc_date,
                "open": float(o_m.group(1)),
                "high": float(h_m.group(1)) if h_m else 0.0,
                "low": float(l_m.group(1)) if l_m else 0.0,
                "close": float(c_m.group(1)),
                "volume": int(v_m.group(1).replace(",", "")) if v_m else 0,
                "rvol": float(rvol_m.group(1)) / 100.0 if rvol_m else 0.0,
            })
    # 날짜 오름차순 정렬 (오래된 날짜 먼저)
    recent_ohlc.sort(key=lambda x: x["date"])

    return TickerTechnical(
        price=sf("현재가"),
        change_pct=sf("전일대비"),
        volume=int(str(data.get("거래량", 0) or 0).replace(",", "") or 0),
        avg_volume_ratio=sf("평균거래량 대비", 1.0),
        high_52w=sf("52주 고가"),
        low_52w=sf("52주 저가"),
        position_52w=_safe_float(str(data.get("52주 범위 위치", "0")).replace("%", "").split("(")[0]) or 0.0,
        ma5=sf("5일 MA"),
        ma20=sf("20일 MA"),
        ma50=sf("50일 MA"),
        ma60=sf("60일 MA"),
        ma200=sf("200일 MA"),
        ma_aligned="완전 정배열" in str(data.get("MA 정렬 (5/20/60)", "")),
        rsi14=sf("RSI(14)", 50.0),
        bb_upper=sf("BB Upper"),
        bb_mid=sf("BB Mid(SMA20)"),
        bb_lower=sf("BB Lower"),
        bb_position="upper_break" if "상단 돌파" in str(data.get("BB 위치", "")) else "mid",
        adx14=sf("ADX(14)", 20.0),
        obv_direction="up" if "상승" in str(data.get("OBV 방향", "")) else "down",
        macd_line=sf("MACD Line"),
        macd_signal=sf("MACD Signal"),
        macd_histogram=sf("MACD Histogram"),
        macd_cross="golden" if "골든크로스" in str(data.get("MACD 크로스", "")) else "none",
        support1=_safe_float(data.get("지지1")),
        support2=_safe_float(data.get("지지2")),
        resistance1=_safe_float(data.get("저항1")),
        resistance2=_safe_float(data.get("저항2")),
        recent_ohlc=recent_ohlc,
    )


def _parse_ticker_valuation(data: dict[str, Any]) -> TickerValuation:
    """
    tech_data 딕셔너리에서 TickerValuation 생성.
    summary_*.json 종목 블록에 밸류에이션 필드가 포함된 경우 자동 추출.
    필드가 없으면 None 유지 (기존 파이프라인 동작 변화 없음).

    실제 데이터 키 예시 (summary_*.json VALUATION 섹션):
      P/E (TTM): 35.98 / Forward P/E: 6.4 / PEG (TTM): 0.16
      ROE (TTM): 0.408 / 순이익률 TTM: 0.415 / 부채비율 (연간): 0.27
      EPS 성장 YoY: 7.563 / 매출 성장 YoY: 1.963 / 주요 경쟁사: NVDA, AVGO...
    """
    def _first_float(*keys: str) -> float | None:
        """여러 키를 순서대로 시도해 첫 번째 유효한 float 반환"""
        for key in keys:
            v = data.get(key)
            if v is not None:
                result = _safe_float(v)
                if result is not None:
                    return result
        return None

    def _first_pct(*keys: str) -> float | None:
        """퍼센트 문자열(예: '15.3%') 파싱 — 여러 키 시도"""
        for key in keys:
            v = data.get(key)
            if v is not None:
                result = _safe_float(str(v).replace("%", "").strip())
                if result is not None:
                    return result
        return None

    # 경쟁사 파싱: "NVDA, AVGO, AMD, INTC" → ["NVDA", "AVGO", "AMD", "INTC"]
    competitors_raw = data.get("주요 경쟁사", "") or ""
    competitors = [c.strip() for c in competitors_raw.split(",") if c.strip()]

    return TickerValuation(
        # 실제 키: "P/E (TTM)" / 폴백: "P/E", "PE 비율", "PER"
        pe_ttm=_first_float("P/E (TTM)", "P/E", "PE 비율", "PER"),
        # 실제 키: "Forward P/E" (일치)
        forward_pe=_first_float("Forward P/E", "예상 PER", "Forward PE"),
        # 실제 키: "PEG (TTM)"
        peg=_first_float("PEG (TTM)", "PEG", "PEG Ratio"),
        # 실제 키: "P/B" (일치)
        pb=_first_float("P/B", "PBR", "P/B Ratio"),
        # 실제 키: "P/S (TTM)"
        ps_ttm=_first_float("P/S (TTM)", "P/S", "PSR"),
        # 실제 키: "EPS (TTM)"
        eps_ttm=_first_float("EPS (TTM)", "EPS", "주당순이익"),
        # 실제 키: "EPS 성장 YoY" (값은 비율, 예: 7.563 = 756.3% 성장)
        eps_growth_yoy=_first_pct("EPS 성장 YoY", "EPS 성장률", "EPS YoY", "EPS Growth"),
        # 실제 키: "매출 성장 YoY"
        revenue_growth_yoy=_first_pct("매출 성장 YoY", "매출 성장률", "Revenue YoY", "Rev Growth"),
        # 실제 키: "ROE (TTM)" (값은 비율, 예: 0.408 = 40.8%)
        roe=_first_pct("ROE (TTM)", "ROE", "자기자본이익률"),
        # 실제 키: "ROA (TTM)"
        roa=_first_pct("ROA (TTM)", "ROA", "총자산이익률"),
        # 실제 키: "순이익률 TTM"
        net_margin=_first_pct("순이익률 TTM", "순이익률", "Net Margin", "Net Profit Margin"),
        # 실제 키: "영업이익률 TTM"
        op_margin=_first_pct("영업이익률 TTM", "영업이익률", "Op Margin", "Operating Margin"),
        # 실제 키: "부채비율 (연간)"
        debt_ratio=_first_pct("부채비율 (연간)", "부채비율", "Debt/Equity", "D/E"),
        # 실제 키: "베타" (일치)
        beta=_first_float("베타", "Beta"),
        competitors=competitors,
    )


def _parse_earnings_section(item_str: str) -> list[dict[str, Any]]:
    """
    종목 블록 텍스트의 [ EARNINGS ] 섹션에서 실적 히스토리 파싱.

    입력 형식 (예):
      Tue Mar 31 2026 ...  Q2  EPS예상 9.5849  EPS실제 12.2  서프라이즈 0.2728  매출 N/A  YoY N/A  -
      Wed Dec 31 2025 ...  Q1  EPS예상 4.072  EPS실제 4.78  서프라이즈 0.1739  매출 $23.86B  YoY 1.963  -

    Returns:
        [{"quarter": "Q2", "eps_estimate": 9.5849, "eps_actual": 12.2,
          "surprise_pct": 0.2728, "revenue": None, "revenue_yoy": None}, ...]
    """
    results: list[dict[str, Any]] = []
    # EARNINGS 섹션 추출 (다음 [ 섹션 ] 이전까지)
    sec_m = re.search(r"\[ EARNINGS \]\n(.*?)(?=\n\[|\Z)", item_str, re.DOTALL)
    if not sec_m:
        return results

    section_text = sec_m.group(1)
    for line in section_text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # 분기명 파싱 (Q1~Q4)
        q_m = re.search(r"\b(Q[1-4])\b", line)
        if not q_m:
            continue
        quarter = q_m.group(1)

        def _extract_val(pattern: str) -> float | None:
            m = re.search(pattern, line)
            if not m:
                return None
            v = m.group(1).strip()
            return None if v in ("N/A", "-", "") else _safe_float(v)

        # 날짜 추출 (예: "Mar 31 2026")
        date_m = re.search(r"(\w{3} \d{1,2} \d{4})", line)
        date_str = date_m.group(1) if date_m else ""

        # 매출 파싱 (예: "$23.86B" → 23.86e9, "N/A" → None)
        rev_m = re.search(r"매출\s+(\$[\d.]+[BMT]?|N/A)", line)
        revenue: float | None = None
        if rev_m and rev_m.group(1) != "N/A":
            revenue = parse_market_cap(rev_m.group(1).lstrip("$"))

        results.append({
            "date": date_str,
            "quarter": quarter,
            "eps_estimate": _extract_val(r"EPS예상\s+([\d.N/A-]+)"),
            "eps_actual": _extract_val(r"EPS실제\s+([\d.N/A-]+)"),
            "surprise_pct": _extract_val(r"서프라이즈\s+([-\d.N/A]+)"),
            "revenue": revenue,
            "revenue_yoy": _extract_val(r"YoY\s+([-\d.N/A]+)"),
        })

    return results


def _safe_float(val: Any) -> float | None:
    if val is None or val == "N/A":
        return None
    try:
        cleaned = str(val).replace("$", "").replace(",", "").strip()
        return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        # 숫자 뒤에 텍스트가 붙은 경우 첫 번째 숫자만 추출
        # 예: "72.6  ⚠️ 과매수" → 72.6 / "51.9  추세 강함" → 51.9
        m = re.search(r"[-+]?\d+\.?\d*", str(val))
        return float(m.group()) if m else None


def _parse_insider_section(item_str: str) -> list[dict[str, Any]]:
    """
    종목 블록 텍스트의 [ INSIDER ] 섹션에서 내부자 거래 파싱.

    입력 형식 (예):
      Mon May 11 2026 ...  GOMO STEVEN J(N/A)  🔴 매도  17139주  @$787.60  총 $13,498,676.4
      Wed May 20 2026 ...  Norrod Forrest Eugene(N/A)  M  0주  @$0.00  총 $0

    Returns:
        [{"date": "May 11 2026", "name": "GOMO STEVEN J", "type": "매도",
          "shares": 17139, "price": 787.60, "total": 13498676.4}, ...]
    """
    results: list[dict[str, Any]] = []
    sec_m = re.search(r"\[ INSIDER \]\n(.*?)(?=\n\[|\Z)", item_str, re.DOTALL)
    if not sec_m:
        return results

    section_text = sec_m.group(1)
    for line in section_text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # 날짜 추출
        date_m = re.search(r"(\w{3} \w{3} \d{1,2} \d{4})", line)
        date_str = date_m.group(1) if date_m else ""

        # 이름 파싱 (대문자 이름(역할) 패턴)
        name_m = re.search(r"([A-Z][a-zA-Z\s]+?)\s*\([^)]*\)\s+(?:M|[🔴🟢])", line)
        name = name_m.group(1).strip() if name_m else ""

        # 거래 유형 (매도 / 매수 / M=행사)
        if "매도" in line:
            tx_type = "매도"
        elif "매수" in line:
            tx_type = "매수"
        else:
            tx_type = "행사"  # M (option exercise)

        # 주식 수
        shares_m = re.search(r"([\d,]+)주", line)
        shares = int(shares_m.group(1).replace(",", "")) if shares_m else 0

        # 가격
        price_m = re.search(r"@\$([\d,.]+)", line)
        price = _safe_float(price_m.group(1).replace(",", "")) if price_m else None

        # 총액
        total_m = re.search(r"총\s+\$([\d,]+(?:\.\d+)?)", line)
        total = _safe_float(total_m.group(1).replace(",", "")) if total_m else None

        if not date_str and not name:
            continue

        results.append({
            "date": date_str,
            "name": name,
            "type": tx_type,
            "shares": shares,
            "price": price,
            "total": total,
        })

    return results


def _parse_options_data(data: dict[str, Any]) -> TickerOptions:
    """옵션 데이터 딕셔너리에서 TickerOptions 생성"""
    # 이상 플로우 파싱
    chain: list[dict] = []
    for entry in data.get("chain_entries", []):
        try:
            dte_val = int(entry.get("DTE", 0) or 0)
            # oi_change: int 또는 None 그대로 전달 (None = 데이터 없음)
            _raw_oic = entry.get("oi_change")
            chain.append({
                "expiry":      entry.get("expiry", ""),
                "option_type": entry.get("type", "call").lower(),
                "strike":      float(entry.get("strike", 0) or 0),
                "oi":          int(entry.get("OI", 0) or 0),
                "volume":      int(entry.get("Vol", 0) or 0),
                "iv":          float(entry.get("IV", 0) or 0),
                "ivr":         float(entry.get("IVR", 0) or 0),
                "mid_price":   float(entry.get("Mid", 0) or 0),
                "spread_pct":  float(entry.get("Sprd%", 0) or 0),
                "delta":       float(entry.get("Delta", 0) or 0),
                "theta":       float(entry.get("Theta", 0) or 0),
                "dte":         dte_val,
                "is_anomaly":  entry.get("anomaly", False),
                "oi_change":   int(_raw_oic) if _raw_oic is not None else None,
            })
        except Exception:
            pass

    return TickerOptions(
        pc_ratio=float(data.get("pc_ratio", 1.0) or 1.0),
        total_call_oi=int(data.get("total_call_oi", 0) or 0),
        total_put_oi=int(data.get("total_put_oi", 0) or 0),
        implied_move_near=float(data.get("implied_move_near", 0) or 0),
        implied_move_far=float(data.get("implied_move_far", 0) or 0),
        max_pain_near=float(data.get("max_pain_near", 0) or 0),
        max_pain_far=float(data.get("max_pain_far", 0) or 0),
        atm_straddle_price=float(data.get("atm_straddle_price", 0) or 0),
        chain=chain,
    )


def parse_summary(file_path: Path) -> SummaryData:
    """
    summary_*.json 파싱

    실제 파일 포맷: JSONL (각 줄이 독립된 JSON 값)
      line 0: "MARKET SNAPSHOT ..." (str)
      line 1: ["[ MU ]...", "[ AMD ]..."]  (list[str] — 종목별)
      line 2: ["[ MU OPTIONS ]...", ...]   (list[str] — 옵션별)

    Args:
        file_path: summary JSON 파일 경로

    Returns:
        SummaryData 인스턴스

    Raises:
        FileNotFoundError, json.JSONDecodeError
    """
    if not file_path.exists():
        raise FileNotFoundError(f"summary file not found: {file_path}")

    content = file_path.read_text(encoding="utf-8")

    # JSONL 포맷: 줄 단위로 파싱 시도
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) >= 2:
        try:
            parts = [json.loads(line) for line in lines]
            # parts[0]=str(macro), parts[1]=list[str](tickers), parts[2]=list[str](options)
            return _parse_summary_list_format(parts, file_path)
        except json.JSONDecodeError:
            pass

    # 단일 JSON 값 시도
    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        # 마지막 수단: 첫 번째 완전한 JSON 값만 파싱
        import re as _re
        m = _re.match(r'(\[.*?\]|\{.*?\}|".*?")', content, _re.DOTALL)
        if m:
            raw = json.loads(m.group(1))
        else:
            raise

    if isinstance(raw, list):
        return _parse_summary_list_format(raw, file_path)

    return _parse_summary_dict_format(raw, file_path)


def _parse_summary_list_format(raw: list, file_path: Path) -> SummaryData:
    """실제 summary_*.json의 리스트 형식 파싱 (예시 데이터 기준)"""
    # 첫 번째 요소: 시장 스냅샷 문자열
    market_str = raw[0] if len(raw) > 0 else ""
    # 두 번째 요소: 종목 데이터 리스트
    ticker_items = raw[1] if len(raw) > 1 and isinstance(raw[1], list) else []
    # 세 번째 요소: 옵션 데이터 리스트
    options_items = raw[2] if len(raw) > 2 and isinstance(raw[2], list) else []

    # 타임스탬프 추출 (파일명에서)
    fname = file_path.stem  # summary_20260511_134642
    parts = fname.split("_")
    try:
        ts = datetime.strptime(f"{parts[1]}_{parts[2]}", "%Y%m%d_%H%M%S")
    except Exception:
        ts = datetime.now()

    # 거시 지표 파싱
    macro = _parse_macro_from_text(market_str)

    # 이벤트 파싱
    events: list[SummaryEvent] = []
    for ev_match in re.finditer(
        r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\w+ \d+ \d{4}).*?📊 경제지표\s+(.+?)\s+🔴 HIGH|🟡 MED|🟢 LOW\s+D-(\d+)",
        market_str
    ):
        pass  # 실제 구현에서는 정규식 파싱

    # 이벤트 파싱: 각 줄에서 "D-N" + 이벤트명 + 중요도 추출
    for line in market_str.split("\n"):
        ev_m = re.search(r"D-(\d+)", line)
        if not ev_m:
            continue
        # 중요도 판단
        importance = "HIGH" if "🔴" in line else ("MED" if "🟡" in line else "LOW")
        # 이벤트 타입 및 이름 추출 — 📊/📅 이후 텍스트
        type_m = re.search(r"[📊📅]\s*(경제지표|실적|이벤트)\s+(.+?)(?:\s+🔴|🟡|🟢|D-|$)", line)
        if type_m:
            ev_type = type_m.group(1)
            ev_name = type_m.group(2).strip()
        else:
            # 아이콘 없이 D-N 앞의 텍스트를 이름으로 사용
            ev_name_m = re.search(r"([A-Za-z가-힣\s&]+?)\s+(?:🔴|🟡|🟢)?\s*D-\d+", line)
            ev_type = "경제지표"
            ev_name = ev_name_m.group(1).strip() if ev_name_m else line.strip()[:40]
        if ev_name:
            events.append(SummaryEvent(
                date=ts,
                type=ev_type,
                name=ev_name,
                importance=importance,
                days_until=int(ev_m.group(1)),
            ))

    # 종목 데이터 파싱
    tickers: dict[str, TickerSummary] = {}
    processed: list[str] = []

    for item_str in ticker_items:
        if not isinstance(item_str, str):
            continue
        ticker_m = re.search(r"\[ (\w+) \]", item_str)
        if not ticker_m:
            continue
        ticker = ticker_m.group(1)
        processed.append(ticker)

        # 기술 지표 파싱 (키-값 패턴)
        tech_data: dict[str, Any] = {}
        for line in item_str.split("\n"):
            kv = re.match(r"\s+(\S.*?)\s+:\s+(.+)", line)
            if kv:
                tech_data[kv.group(1).strip()] = kv.group(2).strip()

        technical = _parse_ticker_technical(tech_data)

        # 뉴스 파싱
        news: list[dict[str, str]] = []
        for nm in re.finditer(r"\d+\. \[(.+?)\] .+? (.+)", item_str):
            news.append({"source": nm.group(1), "title": nm.group(2)})

        # 시가총액 파싱: "시가총액(M$): 867588" → 867,588M USD = $867.6B
        mktcap_m = _safe_float(tech_data.get("시가총액(M$)"))
        market_cap = mktcap_m * 1_000_000.0 if mktcap_m else 0.0

        tickers[ticker] = TickerSummary(
            ticker=ticker,
            company=tech_data.get("회사명", ""),
            sector=tech_data.get("섹터", ""),
            market_cap=market_cap,
            technical=technical,
            valuation=_parse_ticker_valuation(tech_data),
            news=news,
            earnings=_parse_earnings_section(item_str),
            insider=_parse_insider_section(item_str),
        )

    # 옵션 데이터 파싱
    options: dict[str, TickerOptions] = {}
    for opt_str in options_items:
        if not isinstance(opt_str, str):
            continue
        ticker_m = re.search(r"\[ (\w+) — OPTIONS \]", opt_str)
        if not ticker_m:
            continue
        ticker = ticker_m.group(1)

        opt_data: dict[str, Any] = {}

        # P/C Ratio
        pc_m = re.search(r"P/C Ratio.*?:\s*([\d.]+)", opt_str)
        if pc_m:
            opt_data["pc_ratio"] = float(pc_m.group(1))

        # Total Call/Put OI
        call_oi_m = re.search(r"Total Call OI\s+:\s+([\d,]+)", opt_str)
        put_oi_m = re.search(r"Total Put OI\s+:\s+([\d,]+)", opt_str)
        if call_oi_m:
            opt_data["total_call_oi"] = int(call_oi_m.group(1).replace(",", ""))
        if put_oi_m:
            opt_data["total_put_oi"] = int(put_oi_m.group(1).replace(",", ""))

        # Implied Move
        im_m = re.search(r"Implied Move: ±\$[\d.]+\s+\(±([\d.]+)%\)", opt_str)
        if im_m:
            opt_data["implied_move_near"] = float(im_m.group(1))

        # Max Pain
        mp_m = re.search(r"Max Pain: \$([\d.]+)", opt_str)
        if mp_m:
            opt_data["max_pain_near"] = float(mp_m.group(1))

        # ATM Straddle 추정 (ATM 콜 + ATM 풋 Mid 합)
        mid_m = re.findall(r"Mid ([\d.]+)", opt_str)
        if len(mid_m) >= 2:
            opt_data["atm_straddle_price"] = float(mid_m[0]) + float(mid_m[1])

        # 체인 항목 파싱 — [만기 ...] 헤더에서 expiry/DTE 추출
        from datetime import date as _date, datetime as _dt2
        import calendar as _cal
        chain_entries = []
        current_expiry: str = ""
        current_dte: int = 0
        _MONTH_MAP = {m: i for i, m in enumerate(
            ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], 1)}

        for line in opt_str.split("\n"):
            # [만기 Fri Jun 12 2026 ...] 또는 [만기 2026-06-12] 헤더 감지
            exp_hdr = re.search(r"\[만기\s+(.+?)\]", line)
            if exp_hdr:
                raw = exp_hdr.group(1).strip()
                # ISO 형식: 2026-06-12
                iso_m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
                # JS Date 형식: Mon Jun 12 2026
                js_m  = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{4})", raw)
                try:
                    if iso_m:
                        exp_dt = _date.fromisoformat(iso_m.group(1))
                    elif js_m:
                        mo = _MONTH_MAP.get(js_m.group(1), 0)
                        exp_dt = _date(int(js_m.group(3)), mo, int(js_m.group(2)))
                    else:
                        exp_dt = None
                    if exp_dt:
                        current_expiry = exp_dt.isoformat()
                        current_dte = (exp_dt - _date.today()).days
                except Exception:
                    pass
                continue

            chain_m = re.search(
                r"(CALL|PUT)\s+Strike ([\d.]+)\s+OI ([\d,]+)\s+Vol ([\d,]+)"
                r"\s+IV ([\d.]+)\s+IVR ([\d.]+)\s+Mid ([\d.]+)\s+Sprd% ([\d.]+)"
                r"\s+Delta ([-\d.]+)\s+Theta ([-\d.]+)"
                r"(?:\s+OI변화\s+([-+]?[\d,]+|N/A))?",  # group(11) — 없으면 None
                line
            )
            if chain_m:
                # IVR: 실제 데이터는 0~1 범위 (예: 0.83 = 83%)
                # 코드 전체가 0~100 스케일을 기대하므로 ×100 변환
                raw_ivr = float(chain_m.group(6))
                ivr_pct = raw_ivr * 100.0 if raw_ivr <= 1.0 else raw_ivr

                # OI 변화: "+123" → 123 / "-456" → -456 / "N/A" or None → None
                _oi_chg_raw = chain_m.group(11)
                _oi_change: int | None = None
                if _oi_chg_raw and _oi_chg_raw != "N/A":
                    try:
                        _oi_change = int(_oi_chg_raw.replace("+", "").replace(",", ""))
                    except ValueError:
                        pass

                chain_entries.append({
                    "type":      chain_m.group(1).lower(),
                    "strike":    float(chain_m.group(2)),
                    "OI":        int(chain_m.group(3).replace(",", "")),
                    "Vol":       int(chain_m.group(4).replace(",", "")),
                    "IV":        float(chain_m.group(5)),
                    "IVR":       ivr_pct,
                    "Mid":       float(chain_m.group(7)),
                    "Sprd%":     float(chain_m.group(8)),
                    "Delta":     float(chain_m.group(9)),
                    "Theta":     float(chain_m.group(10)),
                    "oi_change": _oi_change,   # int 또는 None (데이터 없으면 무시)
                    "anomaly":   "⚠️ 급등" in line,
                    # ── summary 헤더에서 추출한 만기/DTE ──
                    "expiry":    current_expiry,
                    "DTE":       current_dte,
                })
        opt_data["chain_entries"] = chain_entries
        options[ticker] = _parse_options_data(opt_data)

    return SummaryData(
        snapshot_timestamp=ts,
        processed_tickers=processed,
        macro=macro,
        events=events[:20],  # 최대 20개
        risk_params=_parse_risk_params_from_text(market_str),
        tickers=tickers,
        options=options,
    )


def _parse_summary_dict_format(raw: dict, file_path: Path) -> SummaryData:
    """표준 딕셔너리 형식 summary 파싱"""
    ts = datetime.fromisoformat(raw.get("snapshot_timestamp", datetime.now().isoformat()))
    macro_raw = raw.get("macro", {})
    macro = SummaryMacro(**macro_raw) if macro_raw else SummaryMacro()

    tickers = {
        k: TickerSummary(**v) for k, v in raw.get("tickers", {}).items()
    }
    return SummaryData(
        snapshot_timestamp=ts,
        processed_tickers=raw.get("processed_tickers", []),
        macro=macro,
        events=[],
        risk_params=_parse_risk_params_from_dict(raw),
        tickers=tickers,
        options={},
    )


def load_latest_summary(summary_dir: Path) -> SummaryData:
    """
    디렉토리에서 가장 최근 summary_*.json 파일 로드

    Args:
        summary_dir: summary 파일이 있는 디렉토리

    Returns:
        SummaryData

    Raises:
        FileNotFoundError: 디렉토리에 summary 파일이 없을 때
    """
    summary_dir = Path(summary_dir)

    # 로컬 테스트용: 현재 디렉토리에서도 탐색
    candidates = list(summary_dir.glob("summary_*.json"))

    if not candidates:
        raise FileNotFoundError(
            f"No summary_*.json files found in: {summary_dir}"
        )

    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    log.info("summary_loaded", file=latest.name)
    return parse_summary(latest)


# ─────────────────────────────────────────────────────────────
# 3. 어닝 분석 파서
# ─────────────────────────────────────────────────────────────

def _parse_earnings_file(file_path: Path) -> list[EarningsAnalysis]:
    """
    어닝_분석.md (단일 파일) 파싱 — frontmatter + 4개 섹션

    Args:
        file_path: 어닝_분석.md 또는 어닝_분석_today.md 경로

    Returns:
        EarningsAnalysis 리스트 (종목별)
    """
    if not file_path.exists():
        log.warning("earnings_file_not_found", path=str(file_path))
        return []

    content = file_path.read_text(encoding="utf-8")

    # --- 구분자로 블록 분리 ---
    # 각 종목 블록은 --- frontmatter --- ## TICKER 형식
    results: list[EarningsAnalysis] = []

    # frontmatter 블록 찾기
    blocks = re.split(r"\n---\n", content)

    def extract_section(title: str, text: str) -> str:
        m = re.search(rf"### {re.escape(title)}(.+?)(?=###|\Z)", text, re.DOTALL)
        return m.group(1).strip() if m else ""

    i = 0
    while i < len(blocks):
        block = blocks[i].strip()
        # ticker: XXX 패턴의 frontmatter 탐지
        fm_m = re.match(r"ticker:\s*(\w+)\nquarter:\s*(\S+)\ndate:\s*(\S+)", block)
        if fm_m:
            ticker = fm_m.group(1).upper()
            quarter = fm_m.group(2)
            date_str = fm_m.group(3)
            try:
                analysis_date = date.fromisoformat(date_str)
            except ValueError:
                analysis_date = date.today()

            # 현재 frontmatter 이후 ~ 다음 frontmatter 이전까지의 블록을 모두 body로 합산.
            # 어닝_분석.md 내에서 --- 가 섹션 구분자로도 사용되기 때문에
            # blocks[i+1] 하나만 읽으면 industry/strategy/confidence 섹션이 누락됨.
            next_fm_idx = next(
                (j for j in range(i + 1, len(blocks))
                 if re.match(r"ticker:\s*\w+\nquarter:", blocks[j].strip())),
                len(blocks),
            )
            body = "\n---\n".join(blocks[i + 1 : next_fm_idx])

            results.append(EarningsAnalysis(
                ticker=ticker,
                quarter=quarter,
                date=analysis_date,
                business_model=extract_section("1. 비즈니스 모델", body),
                industry=extract_section("2. 인더스트리", body),
                strategy_changes=extract_section("3. 변화/전략", body),
                management_confidence=extract_section("4. 자신감 표현", body),
            ))
        i += 1

    return results


def parse_earnings(
    file_path: Path,
    today_file: Path | None = None,
) -> list[EarningsAnalysis]:
    """
    어닝 분석 파일 병합 파싱 (Finviz 기반 또는 K어닝 분석 공용).

    Note: buy/sell 파이프라인은 Finviz 어닝 분석.md를 더 이상 사용하지 않음.
    K어닝 분석.md(Kavout) 파싱 시에도 동일 함수를 재사용합니다.
    run_kavout_screener.py, kavout_mcp/server.py(via analyze_earnings)에서 계속 사용.

    today_file에 같은 ticker가 있으면 base_file 항목을 덮어씁니다.
    실제 내용(business_model 또는 strategy_changes)이 없는 today 항목은 무시합니다.

    Args:
        file_path: 기본 어닝 분석.md 또는 K어닝 분석.md 경로
        today_file: 오늘 추가분 어닝_분석_today.md 경로 (없으면 None)

    Returns:
        EarningsAnalysis 리스트 (today 우선 병합)
    """
    base_entries = _parse_earnings_file(file_path)
    # 티커별로 딕셔너리 구성 (순서 유지)
    merged: dict[str, EarningsAnalysis] = {ea.ticker: ea for ea in base_entries}

    if today_file and today_file.exists():
        today_entries = _parse_earnings_file(today_file)
        overridden = 0
        for ea in today_entries:
            # 실제 내용이 있는 항목만 덮어씀 (오류 메시지나 빈 항목 제외)
            if ea.business_model or ea.strategy_changes or ea.management_confidence:
                merged[ea.ticker] = ea
                overridden += 1
        log.info("earnings_today_merged",
                 today_count=len(today_entries),
                 overridden=overridden,
                 file=today_file.name)

    result = list(merged.values())
    log.info("earnings_parsed", count=len(result), has_today=today_file is not None)
    return result


# ─────────────────────────────────────────────────────────────
# 4. Positions 파서
# ─────────────────────────────────────────────────────────────

def parse_positions(file_path: Path) -> list[Position]:
    """
    positions.md 파싱 — 3가지 형식 지원:
      1. YAML 블록:  ```yaml ... ```
      2. 마크다운 표: | TICKER | ... |
      3. 인라인 한줄: TICKER PUT $STRIKE YY.MM.NW PREMIUM: PRICE

    Args:
        file_path: positions.md 경로

    Returns:
        Position 리스트
    """
    if not file_path.exists():
        log.warning("positions_file_not_found", path=str(file_path))
        return []

    content = file_path.read_text(encoding="utf-8")
    positions: list[Position] = []

    # 1) YAML 프론트매터 블록 형식: ```yaml ... ```
    yaml_blocks = re.findall(r"```yaml(.+?)```", content, re.DOTALL)
    for block in yaml_blocks:
        try:
            pos = _parse_position_yaml(block)
            if pos:
                positions.append(pos)
        except Exception as exc:
            log.warning("position_parse_error", error=str(exc))

    # 2) 마크다운 표 형식 파싱
    if not positions:
        positions = _parse_positions_table(content)

    # 3) 인라인 한줄 형식: "MU PUT $730 26.05.5W PREMIUM: 38.55"
    if not positions:
        positions = _parse_positions_inline(content)

    log.info("positions_parsed", count=len(positions))
    return positions


def _parse_position_yaml(block: str) -> Position | None:
    """YAML 블록에서 Position 생성 — 단일행 및 | 블록 스칼라 멀티라인 지원"""
    data: dict[str, str] = {}
    lines = block.strip().split("\n")
    i = 0
    while i < len(lines):
        kv = re.match(r"\s*(\w+):\s*(.*)", lines[i])
        if kv:
            key = kv.group(1)
            val = kv.group(2).strip()
            if val == "|":
                # | 블록 스칼라: 들여쓰기된 후속 줄 수집
                parts: list[str] = []
                i += 1
                while i < len(lines) and (lines[i].startswith("  ") or lines[i].strip() == ""):
                    parts.append(lines[i].strip())
                    i += 1
                data[key] = "\n".join(parts).strip()
                continue
            else:
                data[key] = val
        i += 1

    if "ticker" not in data:
        return None

    return Position(
        ticker=data["ticker"].upper(),
        option_type=data.get("option_type", "롱콜"),  # type: ignore
        strike=float(data.get("strike", 0)),
        expiry=date.fromisoformat(data.get("expiry", date.today().isoformat())),
        entry_date=date.fromisoformat(data.get("entry_date", date.today().isoformat())),
        entry_premium=float(data.get("entry_premium", 0)),
        entry_stock_price=float(data.get("entry_stock_price", 0)),
        original_contracts=int(data.get("original_contracts", 1)),
        remaining_contracts=int(data.get("remaining_contracts", 1)),
        trailing_stop=float(data.get("trailing_stop", 0)),
        entry_rationale=data.get("entry_rationale", ""),
        thesis=data.get("thesis", ""),
        conviction_score=float(data.get("conviction_score", 0.5)),
    )


def _parse_positions_table(content: str) -> list[Position]:
    """마크다운 표 형식 포지션 파싱 (폴백)"""
    # | TICKER | 롱콜 | 행사가 | 만기 | ... |
    positions: list[Position] = []
    table_m = re.search(r"\|.+\|.+\|", content)
    if not table_m:
        return positions

    lines = [l.strip() for l in content.split("\n") if l.strip().startswith("|")]
    if len(lines) < 3:
        return positions

    headers = [h.strip() for h in lines[0].split("|") if h.strip()]
    for row_line in lines[2:]:  # 헤더, 구분선 건너뜀
        cells = [c.strip() for c in row_line.split("|") if c.strip()]
        if len(cells) < len(headers):
            continue
        row = dict(zip(headers, cells))
        try:
            positions.append(Position(
                ticker=row.get("티커", row.get("TICKER", "")).upper(),
                option_type=row.get("유형", "롱콜"),  # type: ignore
                strike=float(row.get("행사가", "0").replace("$", "")),
                expiry=date.fromisoformat(row.get("만기", date.today().isoformat())),
                entry_date=date.fromisoformat(row.get("진입일", date.today().isoformat())),
                entry_premium=float(row.get("진입가", "0").replace("$", "")),
                entry_stock_price=float(row.get("진입주가", "0").replace("$", "")),
                original_contracts=int(row.get("계약수", "1")),
                remaining_contracts=int(row.get("잔여계약", "1")),
            ))
        except Exception as exc:
            log.warning("position_table_parse_error", row=row, error=str(exc))

    return positions


def _parse_positions_inline(content: str) -> list[Position]:
    """
    인라인 한줄 형식 파싱.

    지원 형식:
        TICKER PUT|CALL $STRIKE YY.MM.NW PREMIUM: PRICE
        예) MU PUT $730 26.05.5W PREMIUM: 38.55
            AMD CALL $150 26.06.3W PREMIUM: 12.30

    날짜 YY.MM.NW → 해당 월의 N번째 금요일
    """
    positions: list[Position] = []

    # 정규식: TICKER  PUT|CALL  $STRIKE  YY.MM.NW  PREMIUM: PRICE
    pattern = re.compile(
        r"^([A-Z]{1,5})\s+"             # 1: 티커
        r"(PUT|CALL)\s+"                # 2: 방향
        r"\$?([\d.]+)\s+"               # 3: 행사가
        r"(\d{2})\.(\d{2})\.(\d+)W\s*" # 4-6: YY MM NW
        r"PREMIUM:\s*([\d.]+)",         # 7: 프리미엄
        re.IGNORECASE | re.MULTILINE,
    )

    def nth_friday(year: int, month: int, n: int) -> date:
        """해당 연월의 n번째 금요일"""
        from datetime import timedelta
        d = date(year, month, 1)
        first_friday = d + timedelta(days=(4 - d.weekday()) % 7)
        target = first_friday + timedelta(weeks=n - 1)
        # 다음 달로 넘어가면 마지막 금요일로 고정
        if target.month != month:
            target = first_friday + timedelta(weeks=n - 2)
        return target

    for m in pattern.finditer(content):
        ticker = m.group(1).upper()
        direction = m.group(2).upper()
        strike = float(m.group(3))
        year = 2000 + int(m.group(4))
        month = int(m.group(5))
        week_n = int(m.group(6))
        premium = float(m.group(7))

        try:
            expiry = nth_friday(year, month, week_n)
        except Exception:
            expiry = date.today()

        opt_type = "롱풋" if direction == "PUT" else "롱콜"

        try:
            positions.append(Position(
                ticker=ticker,
                option_type=opt_type,  # type: ignore
                strike=strike,
                expiry=expiry,
                entry_date=date.today(),
                entry_premium=premium,
                entry_stock_price=0.0,
                original_contracts=1,
                remaining_contracts=1,
            ))
        except Exception as exc:
            log.warning("position_inline_parse_error", line=m.group(0), error=str(exc))

    return positions


# ─────────────────────────────────────────────────────────────
# 5. Kavout AI 점수 파서
# ─────────────────────────────────────────────────────────────

def parse_kavout(file_path: Path) -> dict[str, dict[str, float]]:
    """
    Kavout AI 스코어 CSV 파싱 → {ticker: {"k_score": float, "momentum_1m": float, "roe": float}} 반환

    지원 컬럼명 (대소문자 무시):
      - 티커: Symbol, Ticker, sym
      - K-Score: K-Score, KScore, Score, AI_Score, k_score
      - 1개월 모멘텀: momentum_1m, momentum, mom_1m
      - ROE: roe, roe_pct

    K-Score 범위: 1~9 (9가 가장 강한 매수 신호)
    momentum_1m: 1개월 상대 모멘텀 (단위는 데이터소스 의존, 비교에만 사용)
    roe: Return on Equity (%)

    Args:
        file_path: kavout_*.csv 파일 경로

    Returns:
        {티커: {"k_score": float, "momentum_1m": float, "roe": float}} 딕셔너리
    """
    import csv

    if not file_path.exists():
        log.warning("kavout_file_not_found", path=str(file_path))
        return {}

    result: dict[str, dict[str, float]] = {}
    ticker_cols   = {"symbol", "ticker", "sym"}
    score_cols    = {"k-score", "kscore", "score", "ai_score", "k_score", "kavout_score"}
    momentum_cols = {"momentum_1m", "momentum", "mom_1m", "momentum_1month"}
    roe_cols      = {"roe", "roe_pct", "return_on_equity"}

    try:
        with file_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return {}

            # 컬럼명 매핑 (소문자 변환)
            col_map = {c.lower().strip(): c for c in reader.fieldnames}
            ticker_col   = next((col_map[k] for k in col_map if k in ticker_cols),   None)
            score_col    = next((col_map[k] for k in col_map if k in score_cols),    None)
            momentum_col = next((col_map[k] for k in col_map if k in momentum_cols), None)
            roe_col      = next((col_map[k] for k in col_map if k in roe_cols),      None)

            if not ticker_col or not score_col:
                log.warning("kavout_columns_not_found",
                            available=list(reader.fieldnames),
                            expected_ticker=list(ticker_cols),
                            expected_score=list(score_cols))
                return {}

            for row in reader:
                ticker = row.get(ticker_col, "").strip().upper()
                raw_score = row.get(score_col, "").strip()
                if not ticker or not raw_score:
                    continue
                try:
                    k_score = float(raw_score)
                except ValueError:
                    continue

                entry: dict[str, float] = {"k_score": k_score}

                if momentum_col:
                    try:
                        entry["momentum_1m"] = float(row.get(momentum_col, "").strip() or "0")
                    except ValueError:
                        entry["momentum_1m"] = 0.0

                if roe_col:
                    try:
                        entry["roe"] = float(row.get(roe_col, "").strip() or "0")
                    except ValueError:
                        entry["roe"] = 0.0

                result[ticker] = entry

    except Exception as exc:
        log.warning("kavout_parse_error", path=str(file_path), error=str(exc))
        return {}

    log.info("kavout_parsed", count=len(result), file=file_path.name,
             has_momentum=momentum_col is not None, has_roe=roe_col is not None)
    return result


# ─────────────────────────────────────────────────────────────
# 6. Finviz Output 상세 파서 (finviz_output/*.txt)
# ─────────────────────────────────────────────────────────────

def _parse_finviz_detail_file(ticker: str, content: str) -> FinvizDetail:
    """
    finviz_output/<TICKER>.txt 단일 파일 파싱 → FinvizDetail

    SNAPSHOT TABLE 섹션 예시:
      Forward P/E  32.13      Target Price   304.85
      PEG          0.62       Recom          1.52
      Beta         2.40       Short Float    2.20%
      Insider Trans -6.19%    EPS/Sales Surpr. 16.03% 6.22%
      Gross Margin 45.99%     Oper. Margin   10.67%
      EPS next 5Y  51.65%     Profit Margin  12.51%

    INCOME STATEMENT 섹션 예시 (단위: M USD):
      Period       TTM       FY 2025   FY 2024
      Total Revenue 34,639   34,639    25,785
      Gross Profit  15,930   15,931    11,277
      Net Income    4,269    4,269     1,641
    """
    def _flt(pattern: str) -> float | None:
        """정수/실수 패턴 추출"""
        m = re.search(pattern, content)
        return _safe_float(m.group(1).replace(",", "")) if m else None

    def _pct(pattern: str) -> float | None:
        """퍼센트 패턴 추출 (% 기호 제거 후 float)"""
        m = re.search(pattern, content)
        if not m:
            return None
        return _safe_float(m.group(1).replace(",", "").replace("%", "").strip())

    # EPS + Sales Surprise: "EPS/Sales Surpr. 16.03% 6.22%" (한 줄에 두 값)
    eps_surpr_m = re.search(
        r"EPS/Sales Surpr\.\s+([-\d.]+)%\s+([-\d.]+)%", content
    )
    eps_surprise = _safe_float(eps_surpr_m.group(1)) if eps_surpr_m else None
    sales_surprise = _safe_float(eps_surpr_m.group(2)) if eps_surpr_m else None

    # INCOME STATEMENT — TTM + FY 전기 두 열 파싱 (YoY 성장률 계산용)
    # 형식: "Total Revenue  716,924.00  716,924.00  637,959.00"  (TTM, FY현재, FY전기)
    rev_row_m = re.search(r"Total Revenue\s+([\d,.\-]+)\s+([\d,.\-]+)\s+([\d,.\-]+)", content)
    ni_row_m  = re.search(r"Net Income\s+([\d,.\-]+)\s+([\d,.\-]+)\s+([\d,.\-]+)", content)
    gp_m      = re.search(r"Gross Profit\s+([\d,]+)", content)

    rev_ttm = _safe_float(rev_row_m.group(1).replace(",", "")) if rev_row_m else None
    rev_prev = _safe_float(rev_row_m.group(3).replace(",", "")) if rev_row_m else None
    ni_ttm  = _safe_float(ni_row_m.group(1).replace(",", "")) if ni_row_m else None
    ni_prev = _safe_float(ni_row_m.group(3).replace(",", "")) if ni_row_m else None

    def _yoy_growth(current: float | None, prev: float | None) -> float | None:
        if current is None or prev is None or prev == 0:
            return None
        return round((current - prev) / abs(prev) * 100, 2)

    # 스냅샷 테이블 추가 필드
    # 형식 예: "RSI (14) 27.10 Recom 1.30"
    # "Rel Volume 4.11 Prev Close 222.69"
    # "52W High 258.60 -20.00% ..."  / "52W Low 161.38 28.19% ..."
    # "SMA20 -12.66% Beta ..."  / "SMA50 -11.30% ..." / "SMA200 -7.30% ..."
    # "Price 206.88"  / "Change -7.10%"
    rsi_m        = re.search(r"RSI\s*\(14\)\s*([\d.]+)", content)
    rel_vol_m    = re.search(r"Rel\s+Volume\s+([\d.]+)", content)
    w52_high_m   = re.search(r"52W High\s+[\d.]+\s+([-\d.]+)%", content)
    w52_low_m    = re.search(r"52W Low\s+[\d.]+\s+([\d.]+)%", content)
    sma20_m      = re.search(r"SMA20\s+([-\d.]+)%", content)
    sma50_m      = re.search(r"SMA50\s+([-\d.]+)%", content)
    sma200_m     = re.search(r"SMA200\s+([-\d.]+)%", content)
    price_m      = re.search(r"\bPrice\s+([\d.]+)", content)
    change_m     = re.search(r"\bChange\s+([-\d.]+)%", content)

    return FinvizDetail(
        ticker=ticker,
        forward_pe    = _flt(r"Forward P/E\s+([\d.]+)"),
        peg           = _flt(r"\bPEG\s+([\d.]+)"),
        target_price  = _flt(r"Target Price\s+([\d.]+)"),
        recom         = _flt(r"\bRecom\s+([\d.]+)"),
        beta          = _flt(r"\bBeta\s+([\d.]+)"),
        short_float_pct   = _pct(r"Short Float\s+([\d.]+)%"),
        insider_trans_pct = _pct(r"Insider Trans\s+([-\d.]+)%"),
        eps_surprise_pct  = eps_surprise,
        sales_surprise_pct= sales_surprise,
        gross_margin_pct  = _pct(r"Gross Margin\s+([-\d.]+)%"),
        op_margin_pct     = _pct(r"Oper(?:\.|ating)?\s*Margin\s+([-\d.]+)%"),
        profit_margin_pct = _pct(r"Profit Margin\s+([-\d.]+)%"),
        eps_next_5y_pct   = _pct(r"EPS next 5Y\s+([\d.]+)%"),
        revenue_ttm       = rev_ttm,
        gross_profit_ttm  = _safe_float(gp_m.group(1).replace(",", "")) if gp_m else None,
        net_income_ttm    = ni_ttm,
        # 스크리너 추가 필드
        price             = _safe_float(price_m.group(1)) if price_m else None,
        change_pct        = _safe_float(change_m.group(1)) if change_m else None,
        rsi14             = _safe_float(rsi_m.group(1)) if rsi_m else None,
        rel_volume        = _safe_float(rel_vol_m.group(1)) if rel_vol_m else None,
        w52_high_pct      = _safe_float(w52_high_m.group(1)) if w52_high_m else None,
        w52_low_pct       = _safe_float(w52_low_m.group(1)) if w52_low_m else None,
        sma20_pct         = _safe_float(sma20_m.group(1)) if sma20_m else None,
        sma50_pct         = _safe_float(sma50_m.group(1)) if sma50_m else None,
        sma200_pct        = _safe_float(sma200_m.group(1)) if sma200_m else None,
        revenue_growth_yoy    = _yoy_growth(rev_ttm, rev_prev),
        net_income_growth_yoy = _yoy_growth(ni_ttm, ni_prev),
    )


def parse_finviz_detail(ticker_dir: Path) -> dict[str, FinvizDetail]:
    """
    [DEPRECATED] finviz_output/ 디렉토리 내 모든 <TICKER>.txt 파싱

    ⚠️ buy/sell 파이프라인에서 제거됨 — yfinance fetch_finviz_details_bulk() 사용.
    screener_mcp (Finviz 기반) 전용으로만 남겨둠.

    Args:
        ticker_dir: finviz_output 디렉토리 경로

    Returns:
        {ticker: FinvizDetail} 딕셔너리 (파싱 실패 파일 제외)
    """
    if not ticker_dir.exists():
        log.warning("finviz_output_dir_not_found", path=str(ticker_dir))
        return {}

    result: dict[str, FinvizDetail] = {}
    for txt_file in ticker_dir.glob("*.txt"):
        ticker = txt_file.stem.upper()
        try:
            content = txt_file.read_text(encoding="utf-8", errors="replace")
            detail = _parse_finviz_detail_file(ticker, content)
            result[ticker] = detail
        except Exception as exc:
            log.warning("finviz_detail_parse_error", ticker=ticker, error=str(exc))

    log.info("finviz_detail_parsed", count=len(result), dir=str(ticker_dir))
    return result


# ─────────────────────────────────────────────────────────────
# 7. Kavout 유니버스 파서 (kavout_*.csv 최신 파일 자동 탐색)
# ─────────────────────────────────────────────────────────────

def find_latest_kavout_csv(data_dir: Path) -> Path | None:
    """
    data_dir 내 kavout_*.csv 중 가장 최신(mtime 기준) 파일 반환.
    파일이 없으면 None 반환.

    Args:
        data_dir: Y:\\내 드라이브\\Data 같은 폴더 경로

    Returns:
        가장 최신 kavout_*.csv Path, 없으면 None
    """
    if not data_dir.exists():
        log.warning("kavout_data_dir_not_found", path=str(data_dir))
        return None

    files = sorted(data_dir.glob("kavout_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        log.warning("kavout_csv_not_found", data_dir=str(data_dir))
        return None

    log.info("kavout_csv_found", file=files[0].name)
    return files[0]


def parse_kavout_universe(data_dir: Path) -> list[KavoutRow]:
    """
    data_dir에서 최신 kavout_*.csv를 찾아 파싱 → 유니버스 반환.

    중복 처리:
      - 같은 ticker가 여러 section에 등장할 경우,
        quantitative_momentum_plus 섹션(k_score > 0) 우선 보존.

    Args:
        data_dir: kavout_*.csv가 있는 폴더 경로

    Returns:
        KavoutRow 리스트 (ticker 중복 제거, 정렬 보존)
    """
    import csv

    file_path = find_latest_kavout_csv(data_dir)
    if file_path is None:
        return []

    rows: list[KavoutRow] = []
    seen: dict[str, int] = {}  # ticker → rows 인덱스

    try:
        with file_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return []

            for raw in reader:
                ticker = raw.get("symbol", "").strip().upper()
                if not ticker:
                    continue

                def _f(key: str) -> float | None:
                    v = raw.get(key, "").strip()
                    try:
                        return float(v) if v else None
                    except ValueError:
                        return None

                row = KavoutRow(
                    ticker=ticker,
                    company=raw.get("company", "").strip(),
                    price=_f("price"),
                    market_cap_raw=_f("market_cap_raw"),
                    momentum_1m=_f("momentum_1m"),
                    roe=_f("roe"),
                    k_score=_f("k_score"),
                    section=raw.get("section", "").strip(),
                )

                if ticker in seen:
                    # quantitative_momentum_plus (k_score > 0) 우선
                    existing = rows[seen[ticker]]
                    if (existing.k_score or 0) < (row.k_score or 0):
                        rows[seen[ticker]] = row
                else:
                    seen[ticker] = len(rows)
                    rows.append(row)

    except Exception as exc:
        log.warning("kavout_universe_parse_error", path=str(file_path), error=str(exc))
        return []

    log.info("kavout_universe_parsed", count=len(rows), file=file_path.name)
    return rows


# ─────────────────────────────────────────────────────────────
# 8. Kavout Output 파서 (kavout_output/*.txt — 줄바꿈 분리 포맷)
# ─────────────────────────────────────────────────────────────

def _parse_kavout_output_file(ticker: str, content: str) -> FinvizDetail:
    """
    kavout_output/<TICKER>.txt 파싱 → FinvizDetail (펀더멘털·밸류에이션만)

    포맷: 키와 값이 줄바꿈으로 교대로 나열
      Forward P/E
      9.49
      PEG
      0.08
      ...

    기술지표(RSI, SMA, MACD 등)는 장전 스냅샷이므로 채우지 않음 — API 담당.
    재무제표(Income Statement)에서 YoY 성장률 계산.
    """
    # 키-값 쌍 딕셔너리 구성 (SNAPSHOT TABLE 섹션)
    kv: dict[str, str] = {}
    snapshot_section = ""
    m = re.search(
        r"SNAPSHOT TABLE\s*={10,}(.*?)(?:={10,}|$)",
        content,
        re.DOTALL,
    )
    if m:
        snapshot_section = m.group(1)

    # 줄 단위로 키-값 교대 파싱
    lines = [ln.strip() for ln in snapshot_section.splitlines() if ln.strip()]
    i = 0
    while i < len(lines) - 1:
        key = lines[i]
        val = lines[i + 1]
        # 값처럼 보이는 줄: 숫자·퍼센트·날짜·문자 혼합 허용
        # 키처럼 보이는 줄이 연속되면 값으로 취급하지 않음
        kv[key] = val
        i += 2

    def _get_flt(key: str) -> float | None:
        v = kv.get(key, "").strip().replace(",", "").replace("%", "")
        return _safe_float(v) if v else None

    def _get_pct(key: str) -> float | None:
        """퍼센트 값 파싱 (% 제거 후 float)"""
        v = kv.get(key, "").strip().replace(",", "").replace("%", "")
        return _safe_float(v) if v else None

    # EPS/Sales Surprise: "EPS/Sales Surpr." 키 값 → "32.81% 19.50%"
    surpr_raw = kv.get("EPS/Sales Surpr.", "")
    eps_surprise: float | None = None
    sales_surprise: float | None = None
    surpr_m = re.search(r"([-\d.]+)%\s+([-\d.]+)%", surpr_raw)
    if surpr_m:
        eps_surprise = _safe_float(surpr_m.group(1))
        sales_surprise = _safe_float(surpr_m.group(2))

    # INCOME STATEMENT — TTM + 전기 파싱 (YoY 성장률 계산)
    inc_m = re.search(r"INCOME STATEMENT\s*={10,}(.*?)(?:={10,}|$)", content, re.DOTALL)
    rev_ttm = rev_prev = ni_ttm = ni_prev = gp_ttm = None
    fcf_val: float | None = None
    if inc_m:
        inc_text = inc_m.group(1)
        # "Total Revenue    58,119.00    37,378.00    25,111.00"
        rev_row = re.search(r"Total Revenue\s+([\d,.\-]+)\s+([\d,.\-]+)\s+([\d,.\-]+)", inc_text)
        ni_row  = re.search(r"Net Income\b[^\n]*\s+([\d,.\-]+)\s+([\d,.\-]+)\s+([\d,.\-]+)", inc_text)
        gp_row  = re.search(r"Gross Profit\s+([\d,.\-]+)", inc_text)
        if rev_row:
            rev_ttm  = _safe_float(rev_row.group(1).replace(",", ""))
            rev_prev = _safe_float(rev_row.group(3).replace(",", ""))
        if ni_row:
            ni_ttm  = _safe_float(ni_row.group(1).replace(",", ""))
            ni_prev = _safe_float(ni_row.group(3).replace(",", ""))
        if gp_row:
            gp_ttm = _safe_float(gp_row.group(1).replace(",", ""))

    # CASH FLOW — Free Cash Flow TTM
    cf_m = re.search(r"CASH FLOW\s*={10,}(.*?)(?:={10,}|$)", content, re.DOTALL)
    if cf_m:
        fcf_row = re.search(r"Free Cash Flow\s+([\d,.\-]+)", cf_m.group(1))
        if fcf_row:
            fcf_val = _safe_float(fcf_row.group(1).replace(",", ""))

    def _yoy(cur: float | None, prev: float | None) -> float | None:
        if cur is None or prev is None or prev == 0:
            return None
        return round((cur - prev) / abs(prev) * 100, 2)

    # ROE: BALANCE SHEET 섹션에서 파싱
    roe_val: float | None = None
    bs_m = re.search(r"BALANCE SHEET\s*={10,}(.*?)(?:={10,}|$)", content, re.DOTALL)
    if bs_m:
        roe_row = re.search(r"Return on Equity\s+([\d,.\-]+)", bs_m.group(1))
        if roe_row:
            roe_val = _safe_float(roe_row.group(1).replace(",", ""))
    # 스냅샷 ROE 폴백
    if roe_val is None:
        roe_val = _get_flt("ROE")

    # Market Cap: 스냅샷 키-값에서 파싱 ("Market Cap" → "1086.82B" / "23.45T" / "450.00M")
    mcap_val: float | None = None
    mcap_raw = kv.get("Market Cap", "").strip()
    if mcap_raw:
        mcap_m = re.match(r"([\d.]+)([BMT]?)", mcap_raw.upper())
        if mcap_m:
            num = _safe_float(mcap_m.group(1))
            unit = mcap_m.group(2)
            if num is not None:
                multiplier = {"T": 1e12, "B": 1e9, "M": 1e6}.get(unit, 1.0)
                mcap_val = num * multiplier

    return FinvizDetail(
        ticker            = ticker,
        forward_pe        = _get_flt("Forward P/E"),
        peg               = _get_flt("PEG"),
        target_price      = _get_flt("Target Price"),
        recom             = _get_flt("Recom"),
        beta              = _get_flt("Beta"),
        short_float_pct   = _get_pct("Short Float"),
        insider_trans_pct = _get_pct("Insider Trans"),
        eps_surprise_pct  = eps_surprise,
        sales_surprise_pct= sales_surprise,
        gross_margin_pct  = _get_pct("Gross Margin"),
        op_margin_pct     = _get_pct("Oper. Margin"),
        profit_margin_pct = _get_pct("Profit Margin"),
        eps_next_5y_pct   = _get_pct("EPS next 5Y"),
        revenue_ttm       = rev_ttm,
        gross_profit_ttm  = gp_ttm,
        net_income_ttm    = ni_ttm,
        revenue_growth_yoy    = _yoy(rev_ttm, rev_prev),
        net_income_growth_yoy = _yoy(ni_ttm,  ni_prev),
        roe_pct           = roe_val,
        fcf_ttm           = fcf_val,
        market_cap        = mcap_val,
        # 기술지표는 API 담당 — 여기서 채우지 않음
    )


def parse_kavout_output(kavout_output_dir: Path) -> dict[str, FinvizDetail]:
    """
    [DEPRECATED] kavout_output/ 디렉토리 내 모든 <TICKER>.txt 파싱 → {ticker: FinvizDetail}

    ⚠️ buy/sell 파이프라인 및 run_kavout_screener.py에서 제거됨.
    kavout_output 파일은 오래된 스냅샷 기준이므로 yfinance가 완전 대체.

    Args:
        kavout_output_dir: Y:\\내 드라이브\\어닝\\kavout_output 경로

    Returns:
        {ticker: FinvizDetail} (파싱 실패 파일 제외)
    """
    if not kavout_output_dir.exists():
        log.warning("kavout_output_dir_not_found", path=str(kavout_output_dir))
        return {}

    result: dict[str, FinvizDetail] = {}
    for txt_file in kavout_output_dir.glob("*.txt"):
        ticker = txt_file.stem.upper()
        try:
            content = txt_file.read_text(encoding="utf-8", errors="replace")
            detail = _parse_kavout_output_file(ticker, content)
            result[ticker] = detail
        except Exception as exc:
            log.warning("kavout_output_parse_error", ticker=ticker, error=str(exc))

    log.info("kavout_output_parsed", count=len(result), dir=str(kavout_output_dir))
    return result
