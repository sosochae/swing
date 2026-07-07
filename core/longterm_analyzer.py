"""
core/longterm_analyzer.py
=========================
장기투자 LLM 정성 분석 4단계 (asyncio.gather로 병렬 실행)

Phase 1: Moat + Porter's 5 Forces   (LLM_MODEL_LT_MOAT)
Phase 2: 경영진 + ESG               (LLM_MODEL_LT_MANAGEMENT)
Phase 3: 산업/매크로 + 리스크       (LLM_MODEL_LT_INDUSTRY)
Phase 4: 뉴스/컨퍼런스콜 종합       (LLM_MODEL_LT_QUALITATIVE)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from core.llm import call_llm
from core.research_agent import _search_and_fetch
from shared.config import get_config
from shared.longterm_schemas import (
    FinancialData,
    IndustryAnalysis,
    LongtermInput,
    ManagementAnalysis,
    MarketSignals,
    MoatAnalysis,
    QualityMetrics,
    QualitativeAnalysis,
    RiskItem,
    ValuationData,
)

log = logging.getLogger(__name__)
cfg = get_config()


# ─────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────

async def _llm(prompt: str, model: str, max_tokens: int = 3000) -> str:
    resp = await call_llm(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.0,
        max_tokens=max_tokens,
    )
    return resp.content.strip()


def _parse_json(text: str) -> dict:
    """LLM 응답에서 JSON 객체 추출. 실패 시 빈 dict."""
    text = text.strip()
    # 코드블록 제거
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)```\s*$", text)
    if m:
        text = m.group(1).strip()
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {}


def _score(v, label: str) -> int:
    """LLM이 반환한 점수 값을 안전하게 정수(0~5)로 변환."""
    try:
        return max(0, min(5, int(v)))
    except (TypeError, ValueError):
        log.debug("score_parse_fail field=%s val=%s", label, v)
        return 0


async def _web_research(queries: list[str], context: str, top_n: int = 4) -> str:
    """_search_and_fetch() 결과(list[dict])를 LLM 프롬프트용 텍스트로 합산."""
    fetched = await _search_and_fetch(queries, context=context, fetch_top_n=top_n)
    parts = [f"[{item.get('title','')}]\n{item.get('content','')}" for item in fetched]
    return "\n\n---\n\n".join(parts)


# ─────────────────────────────────────────────────────────────
# Phase 1: Moat + Porter's 5 Forces
# ─────────────────────────────────────────────────────────────

async def _analyze_moat(
    inp: LongtermInput,
    quality: QualityMetrics,
    market_signals: MarketSignals,
    company_info: dict,
) -> MoatAnalysis:
    sector_growth = f"섹터 대비 비교 불가"
    if quality.revenue_cagr_3y is not None:
        sector_growth = f"{inp.company_name} 매출 3년 CAGR {quality.revenue_cagr_3y:.1f}%"

    prompt = f"""You are a senior equity analyst specializing in competitive moat assessment.

Company: {inp.company_name} ({inp.ticker})
Sector: {inp.sector}
Industry: {company_info.get('industry', '')}
Business Summary: {company_info.get('description', '')[:800]}

Revenue 3Y CAGR: {quality.revenue_cagr_3y}%
Operating Margin (latest): {quality.op_margin[0] if quality.op_margin else 'N/A'}%
ROIC: {quality.roic}%

Assess the competitive moat and industry structure. Return ONLY a JSON object:
{{
  "brand_power": <0-5>,
  "switching_cost": <0-5>,
  "network_effect": <0-5>,
  "cost_advantage": <0-5>,
  "patent_license": <0-5>,
  "moat_label": "강한 해자 OR 보통 해자 OR 약한 해자",
  "threat_new_entrants": "낮음 OR 중간 OR 높음",
  "threat_substitutes": "낮음 OR 중간 OR 높음",
  "buyer_power": "낮음 OR 중간 OR 높음",
  "supplier_power": "낮음 OR 중간 OR 높음",
  "rivalry": "낮음 OR 중간 OR 높음",
  "sector_growth_comparison": "<한 줄 섹터 대비 성장성 평가>",
  "moat_durability": "<5년 지속 가능성 2~3문장>",
  "moat_threats": "<주요 위협 요인>",
  "moat_defenses": "<핵심 방어 요인>",
  "segment_growth_label": "<가장 빠르게 성장하는 핵심 사업 부문명 (예: Services, Cloud)>",
  "segment_growth_rate": <해당 부문의 최근 성장률(%) 숫자 또는 null>
}}

All text fields must be in Korean. Be concise."""

    raw = await _llm(prompt, cfg.LLM_MODEL_LT_MOAT)
    d = _parse_json(raw)

    # 핵심 성장 부문 — QualityMetrics는 fetcher가 만들지만 값은 LLM만 추정 가능해
    # analyzer에서 in-place로 채운다 (fetcher가 inp.company_name을 채우는 것과 동일 패턴)
    quality.segment_growth_label = d.get("segment_growth_label", "") or ""
    try:
        quality.segment_growth_rate = (
            float(d["segment_growth_rate"]) if d.get("segment_growth_rate") is not None else None
        )
    except (TypeError, ValueError):
        quality.segment_growth_rate = None

    moat = MoatAnalysis(
        brand_power=_score(d.get("brand_power"), "brand_power"),
        switching_cost=_score(d.get("switching_cost"), "switching_cost"),
        network_effect=_score(d.get("network_effect"), "network_effect"),
        cost_advantage=_score(d.get("cost_advantage"), "cost_advantage"),
        patent_license=_score(d.get("patent_license"), "patent_license"),
        moat_label=d.get("moat_label", ""),
        threat_new_entrants=d.get("threat_new_entrants", ""),
        threat_substitutes=d.get("threat_substitutes", ""),
        buyer_power=d.get("buyer_power", ""),
        supplier_power=d.get("supplier_power", ""),
        rivalry=d.get("rivalry", ""),
        sector_growth_comparison=d.get("sector_growth_comparison", sector_growth),
        moat_durability=d.get("moat_durability", ""),
        moat_threats=d.get("moat_threats", ""),
        moat_defenses=d.get("moat_defenses", ""),
    )
    moat.moat_total = (moat.brand_power + moat.switching_cost + moat.network_effect
                       + moat.cost_advantage + moat.patent_license)
    return moat


# ─────────────────────────────────────────────────────────────
# Phase 2: 경영진 + ESG
# ─────────────────────────────────────────────────────────────

async def _analyze_management(
    inp: LongtermInput,
    market_signals: MarketSignals,
    company_info: dict,
) -> ManagementAnalysis:
    prompt = f"""You are a governance and management quality analyst.

Company: {inp.company_name} ({inp.ticker})
Sector: {inp.sector}
Insider Ownership: {market_signals.inst_ownership_pct}%
Analyst Consensus: Buy {market_signals.analyst_buy} / Hold {market_signals.analyst_hold} / Sell {market_signals.analyst_sell}
Business: {company_info.get('description', '')[:600]}

Based on publicly known information about this company's management team, assess:
Return ONLY a JSON object:
{{
  "ceo_name": "<CEO 이름>",
  "cfo_name": "<CFO 이름>",
  "ceo_tenure_years": <재임 연수 숫자 또는 null>,
  "guidance_accuracy_pct": <가이던스 적중률 % 숫자 또는 null>,
  "ma_track_record": "<M&A 이력 요약 1~2문장: 주요 딜, 성공/실패 여부>",
  "inst_ownership_change": "증가 OR 감소 OR 유지",
  "comp_base_pct": <기본급 비율 % 또는 null>,
  "comp_cash_pct": <현금성과급 비율 % 또는 null>,
  "comp_equity_pct": <장기주식(RSU) 비율 % 또는 null>,
  "comp_vesting_note": "<베스팅 조건 한 줄>",
  "esg_governance": "<거버넌스 리스크 평가 한 줄>",
  "esg_legal": "<소송·규제 위반 이력 한 줄>",
  "esg_labor": "<노동 리스크 한 줄>",
  "esg_environment": "<환경 이슈 한 줄>",
  "esg_summary": "<ESG 종합 한 줄>",
  "capital_allocation_pros": ["<장점1>", "<장점2>"],
  "capital_allocation_cons": ["<단점1>"],
  "management_score": <0-10>
}}

All text in Korean. If unknown, use null for numbers and "확인 필요" for text."""

    raw = await _llm(prompt, cfg.LLM_MODEL_LT_MANAGEMENT)
    d = _parse_json(raw)

    def _intv(k):
        try:
            return float(d[k]) if d.get(k) is not None else None
        except (TypeError, ValueError):
            return None

    return ManagementAnalysis(
        ceo_name=d.get("ceo_name", ""),
        cfo_name=d.get("cfo_name", ""),
        ceo_tenure_years=_intv("ceo_tenure_years"),
        insider_ownership_pct=market_signals.inst_ownership_pct,
        guidance_accuracy_pct=_intv("guidance_accuracy_pct"),
        ma_track_record=d.get("ma_track_record", ""),
        inst_ownership_change=d.get("inst_ownership_change", ""),
        comp_base_pct=_intv("comp_base_pct"),
        comp_cash_pct=_intv("comp_cash_pct"),
        comp_equity_pct=_intv("comp_equity_pct"),
        comp_vesting_note=d.get("comp_vesting_note", ""),
        esg_governance=d.get("esg_governance", ""),
        esg_legal=d.get("esg_legal", ""),
        esg_labor=d.get("esg_labor", ""),
        esg_environment=d.get("esg_environment", ""),
        esg_summary=d.get("esg_summary", ""),
        capital_allocation_pros=d.get("capital_allocation_pros", []),
        capital_allocation_cons=d.get("capital_allocation_cons", []),
        management_score=_score(d.get("management_score"), "management_score"),
    )


# ─────────────────────────────────────────────────────────────
# Phase 3: 산업/매크로 + 리스크
# ─────────────────────────────────────────────────────────────

async def _analyze_industry(
    inp: LongtermInput,
    quality: QualityMetrics,
    valuation: ValuationData,
    company_info: dict,
) -> tuple[IndustryAnalysis, list[RiskItem]]:
    prompt = f"""You are a macro and industry analyst.

Company: {inp.company_name} ({inp.ticker})
Sector: {inp.sector}
Industry: {company_info.get('industry', '')}
Country: {company_info.get('country', '')}
Revenue 3Y CAGR: {quality.revenue_cagr_3y}%
Operating Margin: {quality.op_margin[0] if quality.op_margin else 'N/A'}%
Business: {company_info.get('description', '')[:600]}

Assess the industry structure and macro environment. Return ONLY a JSON object:
{{
  "industry_stage": "도입기 OR 성장기 OR 성숙기 OR 쇠퇴기",
  "market_share_trend": "상승 OR 유지 OR 하락",
  "competitors": [
    {{"name": "<경쟁사명>", "threat": "낮음 OR 중간 OR 높음", "detail": "<한 줄 설명>"}}
  ],
  "tech_disruption_risk": "낮음 OR 중간 OR 높음",
  "cyclical_sensitivity": "방어적 OR 중간 OR 경기민감",
  "rate_sensitivity": "낮음 OR 중간 OR 높음",
  "inflation_passthrough": "낮음 OR 중간 OR 높음",
  "fx_sensitivity": "<달러 강약 영향 한 줄>",
  "geopolitical_risk": "<지정학 리스크 한 줄>",
  "recession_defense": "낮음 OR 중간 OR 높음",
  "regulatory_risk": "<주요 규제 리스크 한 줄 (반독점, 데이터, 환경 등)>",
  "international_revenue_pct": <해외 매출 비중 % 추정 숫자 또는 null>,
  "risks": [
    {{
      "name": "<리스크명>",
      "probability": "낮음 OR 중간 OR 높음",
      "impact": "낮음 OR 중간 OR 높음",
      "severity": <1-5>,
      "is_fatal": false
    }}
  ]
}}

List 3-5 risks, sorted by severity descending. All text in Korean."""

    raw = await _llm(prompt, cfg.LLM_MODEL_LT_INDUSTRY, max_tokens=3500)
    d = _parse_json(raw)

    def _fval(k):
        try:
            return float(d[k]) if d.get(k) is not None else None
        except (TypeError, ValueError):
            return None

    industry = IndustryAnalysis(
        industry_stage=d.get("industry_stage", ""),
        market_share_trend=d.get("market_share_trend", ""),
        competitors=d.get("competitors", []),
        tech_disruption_risk=d.get("tech_disruption_risk", ""),
        cyclical_sensitivity=d.get("cyclical_sensitivity", ""),
        rate_sensitivity=d.get("rate_sensitivity", ""),
        inflation_passthrough=d.get("inflation_passthrough", ""),
        fx_sensitivity=d.get("fx_sensitivity", ""),
        geopolitical_risk=d.get("geopolitical_risk", ""),
        recession_defense=d.get("recession_defense", ""),
        regulatory_risk=d.get("regulatory_risk", ""),
        international_revenue_pct=_fval("international_revenue_pct"),
    )

    risks: list[RiskItem] = []
    for r in d.get("risks", []):
        try:
            risks.append(RiskItem(
                name=r.get("name", ""),
                probability=r.get("probability", "중간"),
                impact=r.get("impact", "중간"),
                severity=_score(r.get("severity"), "severity"),
                is_fatal=bool(r.get("is_fatal", False)),
            ))
        except Exception:
            pass

    return industry, risks


# ─────────────────────────────────────────────────────────────
# Phase 4: 뉴스 리서치 + 정성 종합
# ─────────────────────────────────────────────────────────────

async def _analyze_qualitative(
    inp: LongtermInput,
    company_info: dict,
    moat: MoatAnalysis,
    management: ManagementAnalysis,
    industry: IndustryAnalysis,
) -> QualitativeAnalysis:
    ticker = inp.ticker
    name   = inp.company_name

    # 웹 리서치
    queries = [
        f"{ticker} earnings call transcript 2026 key highlights",
        f"{ticker} {name} latest news 2026",
        f"{name} competitive landscape strategy 2026",
    ]
    web_text = await _web_research(
        queries,
        context=f"Long-term investment research for {name} ({ticker})",
        top_n=5,
    )

    prompt = f"""You are a long-term equity analyst synthesizing research for {name} ({inp.ticker}).

Sector: {inp.sector}
Moat Label: {moat.moat_label}
Moat Total Score: {moat.moat_total}/25
Management Score: {management.management_score}/10
Industry Stage: {industry.industry_stage}
Top Risk: {industry.recession_defense}

Web Research Sources:
{web_text[:4000] if web_text else '(검색 결과 없음)'}

Based on the above, return ONLY a JSON object:
{{
  "earnings_call_summary": "<최근 실적발표 핵심 포인트 3~4줄>",
  "mgmt_tone": "낙관적 OR 중립 OR 신중",
  "news_positive": ["<긍정 뉴스 1>", "<긍정 뉴스 2>", "<긍정 뉴스 3>"],
  "news_negative": ["<부정 뉴스 1>", "<부정 뉴스 2>"],
  "qualitative_verdict": "<종합 정성 판단 3~4문장>",
  "ten_year_thesis": "<이 회사가 10년 후에도 존재해야 할 이유 2~3문장>",
  "tam_estimate": "<총 시장 규모 추정 1문장 (예: 글로벌 클라우드 시장 1.2조$, 연 20% 성장)>"
}}

All text in Korean. Be specific and factual, not generic."""

    raw = await _llm(prompt, cfg.LLM_MODEL_LT_QUALITATIVE, max_tokens=2500)
    d = _parse_json(raw)

    return QualitativeAnalysis(
        earnings_call_summary=d.get("earnings_call_summary", ""),
        mgmt_tone=d.get("mgmt_tone", ""),
        news_positive=d.get("news_positive", []),
        news_negative=d.get("news_negative", []),
        qualitative_verdict=d.get("qualitative_verdict", ""),
        ten_year_thesis=d.get("ten_year_thesis", ""),
        tam_estimate=d.get("tam_estimate", ""),
    )


# ─────────────────────────────────────────────────────────────
# 통합 분석 (병렬 실행)
# ─────────────────────────────────────────────────────────────

async def analyze_all(
    inp: LongtermInput,
    quality: QualityMetrics,
    valuation: ValuationData,
    market_signals: MarketSignals,
    company_info: dict,
) -> tuple[MoatAnalysis, ManagementAnalysis, IndustryAnalysis, list[RiskItem], QualitativeAnalysis]:
    """
    Phase 1~3을 병렬로 실행하고, Phase 4는 앞 결과를 받아 순차 실행.
    """
    log.info("longterm_analyze_start ticker=%s", inp.ticker)

    moat, management, (industry, risks) = await asyncio.gather(
        _analyze_moat(inp, quality, market_signals, company_info),
        _analyze_management(inp, market_signals, company_info),
        _analyze_industry(inp, quality, valuation, company_info),
    )

    qualitative = await _analyze_qualitative(inp, company_info, moat, management, industry)

    log.info(
        "longterm_analyze_done ticker=%s moat=%s/25 mgmt=%s/10 risks=%d",
        inp.ticker, moat.moat_total, management.management_score, len(risks),
    )
    return moat, management, industry, risks, qualitative
