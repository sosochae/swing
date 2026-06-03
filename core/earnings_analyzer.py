"""
core/earnings_analyzer.py
=========================
어닝_분석.md → LLM → EarningsCallAnalysis 구조화 모듈

흐름:
  1. parse_earnings()로 어닝_분석.md 읽기 (기존 파서 재사용)
  2. 종목별로 4개 섹션 텍스트를 조합해 LLM에 요약 재분류 요청
  3. guidance_direction / mgmt_tone / key_risks / catalyst_strength 추출
  4. 캐시: "screener_earnings_{ticker}_{date}" (당일 자정 만료)
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from core.llm import call_llm, get_cache, parse_llm_json, set_cache
from core.parsers import parse_earnings
from shared.logger import get_logger
from shared.schemas import EarningsAnalysis, EarningsCallAnalysis
from shared.strategy import FUNDAMENTAL_SCREEN_PHILOSOPHY

log = get_logger()

_SYSTEM_PROMPT = f"""당신은 어닝콜 분석 전문가입니다.
주어진 어닝콜 요약 텍스트를 읽고 아래 JSON을 반환하십시오.
반드시 JSON 객체만 출력하고 다른 텍스트는 포함하지 마십시오.

{FUNDAMENTAL_SCREEN_PHILOSOPHY}

출력 형식:
{{
  "guidance_direction": "up" | "flat" | "down" | "unknown",
  "guidance_evidence": "다음 분기 또는 연간 매출/EPS 전망이 상향/유지/하향된다고 언급한 구체적인 발언 1문장. 과거 실적(beat/miss)은 포함하지 말 것. 없으면 빈 문자열.",
  "mgmt_tone": "bullish" | "neutral" | "bearish",
  "tone_evidence": "경영진이 사업 전망, 시장 기회, 자신감을 표현한 발언 1문장. 가이던스 수치가 아닌 경영진의 태도나 확신을 보여주는 표현. 없으면 빈 문자열.",
  "key_risks": ["리스크1", "리스크2"],
  "catalyst_strength": 1~5
}}

규칙:
- guidance_evidence: 반드시 미래 전망(next quarter, fiscal year, outlook, raise, lower 등)에 관한 것이어야 함. 과거 분기 실적 초과 달성(beat guidance, exceeded) 문장은 guidance_evidence에 쓰지 말 것.
- tone_evidence: 경영진이 직접 한 말이나 행동(투자 확대, 강한 확신 표현)을 담을 것.
- 두 필드는 서로 다른 내용이어야 함 — 가이던스 수치와 경영진 태도를 분리할 것.
- 원문 텍스트에 실제로 등장한 표현만 인용하고, 없으면 빈 문자열("") 반환.
- 인용은 최대 120자 이내."""


def _build_analysis_text(ea: EarningsAnalysis) -> str:
    """EarningsAnalysis 4개 섹션을 LLM 입력 텍스트로 조합"""
    parts = [f"[{ea.ticker} {ea.quarter}]"]
    if ea.business_model:
        parts.append(f"비즈니스 모델:\n{ea.business_model}")
    if ea.industry:
        parts.append(f"인더스트리:\n{ea.industry}")
    if ea.strategy_changes:
        parts.append(f"변화/전략:\n{ea.strategy_changes}")
    if ea.management_confidence:
        parts.append(f"자신감 표현:\n{ea.management_confidence}")
    return "\n\n".join(parts)


def _fallback_from_text(ea: EarningsAnalysis) -> EarningsCallAnalysis:
    """LLM 없이 키워드 기반 간이 분류 (폴백)"""
    text = (ea.strategy_changes + ea.management_confidence).lower()

    if any(w in text for w in ["raise", "raised", "increased guidance", "above", "beat", "surpass", "상향"]):
        guidance = "up"
    elif any(w in text for w in ["lower", "cut", "reduce", "miss", "below", "하향", "우려"]):
        guidance = "down"
    else:
        guidance = "flat"

    if any(w in text for w in ["confident", "strong", "record", "accelerat", "bullish", "자신"]):
        tone = "bullish"
    elif any(w in text for w in ["cautious", "uncertain", "headwind", "challenge", "우려", "불확실"]):
        tone = "bearish"
    else:
        tone = "neutral"

    strength = 4 if guidance == "up" and tone == "bullish" else \
               3 if guidance == "up" or tone == "bullish" else \
               2 if guidance == "flat" else 1

    return EarningsCallAnalysis(
        ticker=ea.ticker,
        guidance_direction=guidance,  # type: ignore[arg-type]
        mgmt_tone=tone,               # type: ignore[arg-type]
        key_risks=[],
        catalyst_strength=strength,
    )


def _normalize_key_risks(raw: object) -> list[str]:
    """
    key_risks 필드 정규화 — LLM이 반환하는 3가지 형태 처리:
      1. list[str]           → 그대로 사용
      2. '["r1", "r2"]'     → JSON 파싱 후 사용
      3. "r1, r2"           → 쉼표 분리
    """
    if isinstance(raw, list):
        return [str(r) for r in raw if r]
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith("["):
            try:
                import json
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(r) for r in parsed if r]
            except Exception:
                pass
        # 쉼표 분리 폴백
        return [s.strip() for s in stripped.split(",") if s.strip()]
    return []


def _try_partial_json(content: str) -> dict | None:
    """
    잘린 JSON에서 알려진 4개 필드만 정규식으로 직접 추출.
    Unterminated string 등 파싱 불가 케이스의 폴백.
    """
    import re
    result: dict = {}

    m = re.search(r'"guidance_direction"\s*:\s*"(up|flat|down|unknown)"', content)
    if m:
        result["guidance_direction"] = m.group(1)

    m = re.search(r'"mgmt_tone"\s*:\s*"(bullish|neutral|bearish)"', content)
    if m:
        result["mgmt_tone"] = m.group(1)

    m = re.search(r'"catalyst_strength"\s*:\s*(\d)', content)
    if m:
        result["catalyst_strength"] = int(m.group(1))

    # key_risks: 첫 번째 문자열 요소만 추출
    risks = re.findall(r'"([^"]{5,80})"', content)
    known_keys = {"guidance_direction", "mgmt_tone", "catalyst_strength", "key_risks",
                  "up", "flat", "down", "unknown", "bullish", "neutral", "bearish"}
    result["key_risks"] = [r for r in risks if r not in known_keys][:3]

    return result if "guidance_direction" in result else None


async def _analyze_one(ea: EarningsAnalysis, force_refresh: bool = False) -> EarningsCallAnalysis:
    """종목 하나의 EarningsAnalysis → EarningsCallAnalysis (LLM 또는 폴백)"""
    cache_key = f"screener_earnings_{ea.ticker}"

    if not force_refresh:
        cached = get_cache(cache_key)
        if cached:
            log.info("earnings_cache_hit", ticker=ea.ticker)
            try:
                return EarningsCallAnalysis(
                    ticker=ea.ticker,
                    guidance_direction=cached.get("guidance_direction", "unknown"),  # type: ignore[arg-type]
                    mgmt_tone=cached.get("mgmt_tone", "neutral"),                    # type: ignore[arg-type]
                    key_risks=_normalize_key_risks(cached.get("key_risks", [])),
                    catalyst_strength=int(cached.get("catalyst_strength", 3)),
                    guidance_evidence=str(cached.get("guidance_evidence", "")),
                    tone_evidence=str(cached.get("tone_evidence", "")),
                )
            except Exception:
                pass

    text = _build_analysis_text(ea)
    if not text.strip() or text.strip() == f"[{ea.ticker} {ea.quarter}]":
        log.warning("earnings_empty_text", ticker=ea.ticker)
        return _fallback_from_text(ea)

    try:
        from shared.config import get_config as _gc
        _earnings_model = _gc().LLM_MODEL_KAVOUT_EARNINGS or None
        response = await call_llm(
            messages=[{"role": "user", "content": text}],
            system_prompt=_SYSTEM_PROMPT,
            model=_earnings_model,
            temperature=0.0,
            max_tokens=1024,
            response_format="json_object",
        )

        # 1차: 정상 JSON 파싱
        parsed: dict | None = None
        try:
            parsed = parse_llm_json(response)
        except Exception:
            pass

        # 2차: 잘린 JSON에서 필드 직접 추출
        if parsed is None:
            parsed = _try_partial_json(response.content)

        if parsed is None:
            raise ValueError("JSON 파싱 실패 (정상+부분 모두)")

        result = EarningsCallAnalysis(
            ticker=ea.ticker,
            guidance_direction=parsed.get("guidance_direction", "unknown"),  # type: ignore[arg-type]
            mgmt_tone=parsed.get("mgmt_tone", "neutral"),                    # type: ignore[arg-type]
            key_risks=_normalize_key_risks(parsed.get("key_risks", [])),
            catalyst_strength=int(parsed.get("catalyst_strength", 3)),
            guidance_evidence=str(parsed.get("guidance_evidence", "")),
            tone_evidence=str(parsed.get("tone_evidence", "")),
        )
        set_cache(cache_key, {
            "guidance_direction": result.guidance_direction,
            "mgmt_tone": result.mgmt_tone,
            "key_risks": result.key_risks,
            "catalyst_strength": result.catalyst_strength,
            "guidance_evidence": result.guidance_evidence,
            "tone_evidence": result.tone_evidence,
        })
        log.info("earnings_analyzed", ticker=ea.ticker,
                 guidance=result.guidance_direction, tone=result.mgmt_tone,
                 strength=result.catalyst_strength)
        return result

    except Exception as exc:
        log.warning("earnings_llm_failed", ticker=ea.ticker, error=str(exc))
        return _fallback_from_text(ea)


async def analyze_earnings(
    earnings_analysis_path: Path,
    earnings_today_path: Path | None = None,
    force_refresh: bool = False,
    concurrency: int = 5,
) -> dict[str, EarningsCallAnalysis]:
    """
    어닝_분석.md 전체 종목 → EarningsCallAnalysis 딕셔너리

    Args:
        earnings_analysis_path: 어닝_분석.md 경로
        earnings_today_path: 어닝_분석_today.md 경로 (없으면 None)
        force_refresh: 캐시 무시 여부
        concurrency: 동시 LLM 호출 수 (토큰 한도 고려 5 이하 권장)

    Returns:
        {ticker: EarningsCallAnalysis}
    """
    base_list = parse_earnings(earnings_analysis_path, earnings_today_path)

    if not base_list:
        log.warning("earnings_no_data", path=str(earnings_analysis_path))
        return {}

    sem = asyncio.Semaphore(concurrency)

    async def _bounded(ea: EarningsAnalysis) -> tuple[str, EarningsCallAnalysis]:
        async with sem:
            result = await _analyze_one(ea, force_refresh=force_refresh)
            return ea.ticker, result

    tasks = [_bounded(ea) for ea in base_list]
    pairs = await asyncio.gather(*tasks, return_exceptions=True)

    results: dict[str, EarningsCallAnalysis] = {}
    for item in pairs:
        if isinstance(item, Exception):
            log.warning("earnings_gather_error", error=str(item))
            continue
        ticker, analysis = item
        results[ticker] = analysis

    log.info("earnings_analysis_done", total=len(results))
    return results
