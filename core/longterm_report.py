"""
core/longterm_report.py
=======================
LongtermReport → 마크다운 9섹션 렌더링 → Obsidian 저장 + Slack 알림
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from shared.config import get_config
from shared.longterm_schemas import LongtermReport, RiskItem

log = logging.getLogger(__name__)
cfg = get_config()


# ─────────────────────────────────────────────────────────────
# 포맷 헬퍼
# ─────────────────────────────────────────────────────────────

def _fmt(v, fmt="{:.1f}", fallback="N/A") -> str:
    if v is None:
        return fallback
    try:
        return fmt.format(v)
    except Exception:
        return str(v)


def _pct(v, fallback="N/A") -> str:
    return _fmt(v, "{:+.1f}%", fallback) if v is not None else fallback


def _dollar(v, fallback="N/A") -> str:
    return _fmt(v, "${:.2f}", fallback)


def _bar(score: int, max_score: int = 20, width: int = 20) -> str:
    filled = round(score / max_score * width)
    return "█" * filled + "░" * (width - filled)


def _stars(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


def _severity_stars(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


# ─────────────────────────────────────────────────────────────
# 섹션 렌더러
# ─────────────────────────────────────────────────────────────

def _render_header(r: LongtermReport) -> str:
    ms = r.market_signals
    sc = r.scores
    vd = r.verdict

    # 어닝 서프라이즈 요약
    surprise_str = "N/A"
    if ms.earnings_surprise_history:
        hits = sum(1 for h in ms.earnings_surprise_history if h.get("surprise_pct", 0) > 0)
        total = len(ms.earnings_surprise_history)
        surprise_str = f"최근 {total}분기 중 {hits}회 상회"

    analyst_str = "N/A"
    if ms.analyst_buy is not None:
        analyst_str = f"매수 {ms.analyst_buy} / 보유 {ms.analyst_hold} / 매도 {ms.analyst_sell}  평균 목표가 {_dollar(ms.target_price)}"

    stars_total = "★" * min(5, round(sc.total / 20)) + "☆" * max(0, 5 - round(sc.total / 20))

    return f"""# 장기투자 보고서 — {r.input.company_name} ({r.input.ticker})

> 생성일: {r.generated_at}  |  섹터: {r.input.sector}

---

```
현재가: {_dollar(ms.price)}    시총: {_fmt(ms.market_cap, '${:.0f}억')}    52주 범위: {_dollar(ms.w52_low)} ~ {_dollar(ms.w52_high)}

┌─────────────────────────────────────────────────────┐
│  종합 점수:  {sc.total} / 100   {stars_total}                    │
│  투자 의견:  {vd.opinion}                                │
│  목표 가격:  {_dollar(vd.target_price)}  (현재 대비 {_pct(vd.target_upside_pct)})  │
└─────────────────────────────────────────────────────┘

【시장 신호 요약】  ← 장기투자 보조 참고용
200일선 대비: {_pct(ms.sma200_pct)}   공매도 비율: {_fmt(ms.short_float_pct, '{:.1f}%')}   기관 지분율: {_fmt(ms.inst_ownership_pct, '{:.1f}%')}
어닝 서프라이즈: {surprise_str}
애널리스트: {analyst_str}
```

---
"""


def _render_section1(r: LongtermReport) -> str:
    ci = r.input
    fin = r.financial
    q = r.qualitative

    revenue_line = ""
    if fin.revenue and fin.revenue[0]:
        rev_b = fin.revenue[0] * 1e8 / 1e9
        revenue_line = f"최근 연간 매출: ${rev_b:.1f}B"

    # 사업 모델 요약 (longBusinessSummary 앞 400자)
    biz_summary = ""
    if ci.description:
        biz_summary = ci.description[:400].rstrip()
        if len(ci.description) > 400:
            biz_summary += "..."

    tam_line = f"\n**TAM 추정:** {q.tam_estimate}" if q.tam_estimate else ""

    return f"""## Section 1. 비즈니스 개요

**{ci.company_name} ({ci.ticker})**
섹터: {ci.sector}  |  {revenue_line}

### 사업 모델 요약
{biz_summary or '(정보 없음)'}
{tam_line}

### 10년 후 존재 이유
{q.ten_year_thesis or '(분석 중)'}

"""


def _render_section2(r: LongtermReport) -> str:
    m = r.moat
    lines = [
        "## Section 2. 경쟁 우위 (Moat) 분석",
        "",
        "### 해자 유형별 강도",
        f"  브랜드 파워      {_stars(m.brand_power)}  ({m.brand_power}/5)",
        f"  전환 비용        {_stars(m.switching_cost)}  ({m.switching_cost}/5)",
        f"  네트워크 효과    {_stars(m.network_effect)}  ({m.network_effect}/5)",
        f"  원가 우위        {_stars(m.cost_advantage)}  ({m.cost_advantage}/5)",
        f"  특허/라이선스    {_stars(m.patent_license)}  ({m.patent_license}/5)",
        f"  **종합 Moat 점수: {m.moat_total}/25  →  {m.moat_label}**",
        "",
        "### Porter's 5 Forces",
        f"  신규 진입 위협:   {m.threat_new_entrants}",
        f"  대체재 위협:      {m.threat_substitutes}",
        f"  구매자 교섭력:    {m.buyer_power}",
        f"  공급자 교섭력:    {m.supplier_power}",
        f"  기존 경쟁 강도:   {m.rivalry}",
        "",
        "### 섹터 대비 성장성",
        m.sector_growth_comparison or "",
        "",
        "### Moat 지속 가능성",
        m.moat_durability or "",
        f"- 위협: {m.moat_threats}",
        f"- 방어: {m.moat_defenses}",
        "",
    ]
    return "\n".join(lines)


def _render_section3(r: LongtermReport) -> str:
    fin = r.financial
    q = r.quality
    bs = r.balance_sheet
    sr = r.shareholder_return

    # 연도 헤더
    year_header = "  ".join(str(y) for y in fin.years)

    def _row(label: str, values: list, fmt="{:.1f}") -> str:
        cells = []
        for v in values[:5]:
            cells.append(_fmt(v, fmt) if v is not None else "N/A")
        return f"  {label:<18} " + "  ".join(f"{c:>8}" for c in cells)

    return f"""## Section 3. 재무 퀄리티

### 수익성 추세
```
  연도              {year_header}
{_row("매출(억$)", fin.revenue)}
{_row("영업이익(억$)", fin.operating_income)}
{_row("영업이익률(%)", q.op_margin)}
{_row("EBITDA 마진(%)", q.ebitda_margin)}
{_row("순이익률(%)", q.net_margin)}
{_row("FCF(억$)", fin.fcf)}
{_row("FCF 마진(%)", q.fcf_margin)}
```

### 자본 효율성
- ROE:   {_fmt(q.roe, '{:.1f}%')}
- ROA:   {_fmt(q.roa, '{:.1f}%')}
- ROIC:  {_fmt(q.roic, '{:.1f}%')}

### 성장률
- 매출 3년 CAGR:   {_fmt(q.revenue_cagr_3y, '{:.1f}%')}
- 매출 5년 CAGR:   {_fmt(q.revenue_cagr_5y, '{:.1f}%')}
- EPS  3년 CAGR:   {_fmt(q.eps_cagr_3y, '{:.1f}%')}
- FCF  3년 CAGR:   {_fmt(q.fcf_cagr_3y, '{:.1f}%')}
- R&D 투자 비율:   {_fmt(q.rd_ratio, '{:.1f}%')}
- 해외 매출 비중:  {_fmt(r.industry.international_revenue_pct, '{:.0f}%')}
- 핵심 성장 부문:  {q.segment_growth_label or 'N/A'} ({_fmt(q.segment_growth_rate, '{:.1f}%')})

### 재무 건전성
- 부채비율:         {_fmt(bs.debt_to_equity, '{:.0f}%')}
- 순부채(Net Debt): {_fmt(bs.net_debt, '{:.0f}억$')}
- 이자보상배율:     {_fmt(bs.interest_coverage, '{:.1f}x')}
- 유동비율:         {_fmt(bs.current_ratio, '{:.2f}')}
- 현금성 자산:      {_fmt(bs.cash, '{:.0f}억$')}

### 주주환원
- 자사주매입 5년 누적: {_fmt(sr.buyback_5y, '{:.0f}억$')}
- 배당수익률:          {_fmt(sr.dividend_yield, '{:.2f}%')}
- FCF 환원율:          {_fmt(sr.fcf_payout_ratio, '{:.0f}%')}

"""


def _render_section4(r: LongtermReport) -> str:
    v = r.valuation
    sector_pe_str = _fmt(v.sector_pe, '{:.1f}x')

    return f"""## Section 4. 밸류에이션

### 현재 멀티플
| 지표 | 현재 | 5년 평균 | 업계 평균 | 판단 |
|------|------|---------|----------|------|
| PER (Trailing) | {_fmt(v.pe_trailing, '{:.1f}x')} | {_fmt(v.hist_pe_avg, '{:.1f}x')} | {sector_pe_str} | {v.valuation_verdict} |
| PER (Forward)  | {_fmt(v.pe_forward, '{:.1f}x')}  | — | — | — |
| PBR            | {_fmt(v.pbr, '{:.1f}x')}         | — | — | — |
| PSR            | {_fmt(v.psr, '{:.1f}x')}         | — | — | — |
| EV/EBITDA      | {_fmt(v.ev_ebitda, '{:.1f}x')}   | — | — | — |
| PEG            | {_fmt(v.peg, '{:.2f}')}           | — | — | — |
| FCF Yield      | {_fmt(v.fcf_yield, '{:.1f}%')}   | — | — | — |

### DCF 간이 추정
- 가정: FCF 5년 성장률 {cfg.LT_DCF_GROWTH_RATE*100:.0f}%, 이후 {cfg.LT_DCF_TERMINAL_RATE*100:.0f}%, 할인율 {cfg.LT_DCF_DISCOUNT_RATE*100:.0f}%
- 적정가: {_dollar(v.dcf_fair_value)}  →  현재 대비 {_pct(v.dcf_upside)}

### 역사적 PER 범위 (5년)
- 범위: {_fmt(v.hist_pe_low, '{:.1f}x')} ~ {_fmt(v.hist_pe_high, '{:.1f}x')}
- 현재 위치: 5년 범위 내 상위 {_fmt(v.hist_pe_pct, '{:.0f}%')} 구간

"""


def _render_section5(r: LongtermReport) -> str:
    m = r.management
    pros = "\n".join(f"  (+) {p}" for p in m.capital_allocation_pros) or "  (정보 없음)"
    cons = "\n".join(f"  (-) {c}" for c in m.capital_allocation_cons) or "  (정보 없음)"

    inst_change = m.inst_ownership_change or "N/A"

    return f"""## Section 5. 경영진 평가

### 프로필
- CEO: {m.ceo_name or '확인 필요'}  (재임 {_fmt(m.ceo_tenure_years, '{:.0f}년')} )
- CFO: {m.cfo_name or '확인 필요'}
- 내부자 지분율: {_fmt(m.insider_ownership_pct, '{:.1f}%')}  (기관 동향: {inst_change})
- 가이던스 적중률: {_fmt(m.guidance_accuracy_pct, '{:.0f}%')}
- M&A 이력: {m.ma_track_record or '정보 없음'}

### 보상 구조
- 기본급: {_fmt(m.comp_base_pct, '{:.0f}%')}  |  현금성과급: {_fmt(m.comp_cash_pct, '{:.0f}%')}  |  장기주식(RSU): {_fmt(m.comp_equity_pct, '{:.0f}%')}
- {m.comp_vesting_note or '베스팅 조건 미확인'}

### ESG 리스크
- 거버넌스: {m.esg_governance}
- 소송/규제: {m.esg_legal}
- 노동:     {m.esg_labor}
- 환경:     {m.esg_environment}
- **종합: {m.esg_summary}**

### 자본배분 평가
{pros}
{cons}

**경영진 점수: {m.management_score}/10**

"""


def _render_section6(r: LongtermReport) -> str:
    i = r.industry
    comp_lines = "\n".join(
        f"  {c.get('name',''):<12} 위협: {c.get('threat',''):<4}  — {c.get('detail','')}"
        for c in i.competitors
    ) or "  (경쟁사 정보 없음)"

    return f"""## Section 6. 산업 / 매크로 환경

### 산업 포지셔닝
- 성장 단계: {i.industry_stage}
- 시장 점유율 추세: {i.market_share_trend}
- 기술 파괴 위험: {i.tech_disruption_risk}
- 경기 민감도: {i.cyclical_sensitivity}
- 규제 리스크: {i.regulatory_risk or 'N/A'}

### 경쟁 구도
{comp_lines}

### 매크로 감응도
- 금리 영향:          {i.rate_sensitivity}
- 인플레이션 전가력:  {i.inflation_passthrough}
- 달러 강약 영향:     {i.fx_sensitivity}
- 경기침체 방어력:    {i.recession_defense}
- 지정학 리스크:      {i.geopolitical_risk}

"""


def _render_section7(r: LongtermReport) -> str:
    risks = r.risks
    if not risks:
        return "## Section 7. 리스크 매트릭스\n\n(리스크 정보 없음)\n\n"

    rows = []
    for rk in sorted(risks, key=lambda x: -x.severity):
        fatal_tag = "  ⛔ 즉시 제외" if rk.is_fatal else ""
        rows.append(
            f"| {rk.name} | {rk.probability} | {rk.impact} | "
            f"{_severity_stars(rk.severity)}{fatal_tag} |"
        )

    fatal = [rk for rk in risks if rk.is_fatal]
    monitor = [rk for rk in risks if rk.severity >= 4 and not rk.is_fatal]

    fatal_line = "없음" if not fatal else ", ".join(r.name for r in fatal)
    monitor_line = "없음" if not monitor else ", ".join(r.name for r in monitor)

    return f"""## Section 7. 리스크 매트릭스

| 리스크 | 발생확률 | 임팩트 | 위험도 |
|--------|---------|--------|--------|
{chr(10).join(rows)}

- 치명적 리스크 (즉시 제외 수준): {fatal_line}
- 주요 모니터링 항목: {monitor_line}

"""


def _render_section8(r: LongtermReport) -> str:
    q = r.qualitative
    pos = "\n".join(f"  + {n}" for n in q.news_positive) or "  (없음)"
    neg = "\n".join(f"  - {n}" for n in q.news_negative) or "  (없음)"

    return f"""## Section 8. 정성 분석

### 최근 실적발표 핵심 포인트
{q.earnings_call_summary or '(정보 없음)'}
경영진 톤: **{q.mgmt_tone or '확인 필요'}**

### 주요 최신 뉴스
{pos}
{neg}

### 종합 정성 판단
{q.qualitative_verdict or '(분석 없음)'}

"""


def _render_section9(r: LongtermReport) -> str:
    sc = r.scores
    vd = r.verdict

    triggers = "\n".join(f"    · {t}" for t in vd.review_triggers)

    return f"""## Section 9. 종합 스코어카드

### 영역별 점수
```
  비즈니스 퀄리티    {_bar(sc.business_quality)}  {sc.business_quality}/20
  경쟁 우위(Moat)    {_bar(sc.moat)}  {sc.moat}/20
  재무 건전성        {_bar(sc.financial_health)}  {sc.financial_health}/20
  밸류에이션         {_bar(sc.valuation)}  {sc.valuation}/20
  경영진 신뢰도      {_bar(sc.management)}  {sc.management}/20
  리스크 관리        {_bar(sc.risk)}  {sc.risk}/20
  ──────────────────────────────────────────────
  총점               {_bar(sc.total, 100)}  {sc.total}/100
```

### 투자 판단 매트릭스
```
              밸류에이션
              저평가   적정    고평가
  퀄리티 높음 │ 강력매수  매수  장기보유
  퀄리티 중간 │  매수   장기보유  관망
  퀄리티 낮음 │ 장기보유  관망    매도
```

### 최종 의견
```
  투자 의견:  {vd.opinion}
  목표 기간:  {vd.target_horizon}
  목표 수익률: {vd.target_return_pa}
  포지션 크기: {vd.position_size_pct}
  목표 가격:  {_dollar(vd.target_price)}  ({_pct(vd.target_upside_pct)})

  재검토 트리거:
{triggers}
```

---
*본 보고서는 자동 생성된 참고 자료이며 투자 권유가 아닙니다.*
*생성일: {r.generated_at}  |  모델: {cfg.LLM_MODEL_LT_QUALITATIVE}*
"""


# ─────────────────────────────────────────────────────────────
# 통합 렌더링
# ─────────────────────────────────────────────────────────────

def render(r: LongtermReport) -> str:
    return (
        _render_header(r)
        + _render_section1(r)
        + _render_section2(r)
        + _render_section3(r)
        + _render_section4(r)
        + _render_section5(r)
        + _render_section6(r)
        + _render_section7(r)
        + _render_section8(r)
        + _render_section9(r)
    )


# ─────────────────────────────────────────────────────────────
# Obsidian 저장 + Slack 알림
# ─────────────────────────────────────────────────────────────

async def build_and_save(report: LongtermReport) -> str:
    """
    보고서 렌더링 → Obsidian 저장 → Slack 알림.
    Returns: Obsidian 저장 경로
    """
    md = render(report)

    # Obsidian 경로
    path = cfg.LT_NOTE_PATH_TEMPLATE.format(
        ticker=report.input.ticker,
        date=report.generated_at[:10],
    )
    report.obsidian_path = path

    # Obsidian 저장
    from core.obsidian import save_note_safe
    await save_note_safe(path, md)
    log.info("longterm_report_saved path=%s", path)

    # Slack 알림
    try:
        from core.slack import SlackClient
        slack = SlackClient()
        sc = report.scores
        vd = report.verdict
        msg = (
            f"📊 *장기투자 보고서 완성*: `{report.input.ticker}` ({report.input.company_name})\n"
            f"> 종합 점수: *{sc.total}/100*  |  의견: *{vd.opinion}*  |  "
            f"목표가: *{_dollar(vd.target_price)}* ({_pct(vd.target_upside_pct)})\n"
            f"> Obsidian: `{path}`"
        )
        await slack.send_message(cfg.LT_SLACK_CHANNEL, msg)
    except Exception as e:
        log.warning("longterm_slack_fail error=%s", e)

    return path
