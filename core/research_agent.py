"""
core/research_agent.py
======================
Research Agent — Plan → Search → Rank → Fetch → Gap → Synth 루프

세 가지 리서치 함수를 제공:
  run_research()           : On-demand 심층 리서치 (Phase 1)
  run_earnings_research()  : 실적 전 집중 리서치   (Phase 3)
  run_drop_research()      : 포지션 급락 원인 분석  (Phase 4)

LLM 호출: call_llm(model=cfg.RESEARCH_MODEL) — google/gemini-2.5-flash
검색:     call_ddg_search / call_brave_search (기존 인프라 재사용)
Rank/Fetch: rank_and_pick / fetch_url_as_markdown (기존 인프라 재사용)
Obsidian: ObsidianClient.PUT
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
# Phase 1: On-demand 심층 리서치
# ─────────────────────────────────────────────────────────────

async def run_research(
    ticker: str,
    context_hint: str = "",
) -> str:
    """
    특정 종목 심층 리서치 루프.

    Args:
        ticker: 종목 심볼 (예: "NVDA")
        context_hint: 파이프라인 컨텍스트 문자열
                      (예: "기술점수 82, high conviction, regime=bull")
                      수동 CLI 호출 시 빈 문자열 가능

    Returns:
        합성된 리서치 보고서 마크다운 문자열
    """
    today = date.today().isoformat()
    log.info("research_start", ticker=ticker)

    context_section = f"\n파이프라인 컨텍스트: {context_hint}" if context_hint else ""

    # ── [Plan] 검색 쿼리 생성 ──────────────────────────────────
    plan_prompt = (
        f"You are a financial research analyst. Generate 5 distinct search queries "
        f"to thoroughly research {ticker} stock from different angles.\n"
        f"Today's date: {today}{context_section}\n\n"
        f"Focus angles: recent news, earnings outlook, options activity, "
        f"sector trends, analyst sentiment.\n\n"
        f"Output ONLY 5 search queries, one per line, no numbering, no explanation."
    )
    queries_raw = await _llm(plan_prompt, max_tokens=256)
    queries = [q.strip() for q in queries_raw.splitlines() if q.strip()][:5]
    if not queries:
        queries = [
            f"{ticker} stock news analysis {today}",
            f"{ticker} earnings outlook analyst estimate",
            f"{ticker} options unusual activity",
            f"{ticker} sector trend outlook",
            f"{ticker} technical analysis price target",
        ]
    log.info("research_queries", ticker=ticker, count=len(queries))

    # ── [Search 1 + Rank + Fetch] ──────────────────────────────
    fetched_1 = await _search_and_fetch(
        queries,
        context=f"{ticker} stock research — recent news, earnings, options",
        fetch_top_n=cfg.RESEARCH_FETCH_TOP_N,
    )
    log.info("research_fetch1", ticker=ticker, count=len(fetched_1))

    # ── [Gap Analysis] ─────────────────────────────────────────
    fetched_2: list[dict] = []
    if fetched_1:
        collected_summary = "\n\n---\n\n".join(
            f"[{d['title']}]\n{d['content'][:800]}" for d in fetched_1
        )
        gap_prompt = (
            f"Research target: {ticker} stock\n\n"
            f"Already collected:\n{collected_summary}\n\n"
            f"Identify what important information is still missing and generate "
            f"up to 3 additional search queries to fill those gaps.\n"
            f"If coverage is sufficient, reply with exactly: SUFFICIENT\n"
            f"Otherwise output ONLY search queries, one per line, no explanation."
        )
        gap_raw = await _llm(gap_prompt, max_tokens=200)

        if "SUFFICIENT" not in gap_raw.upper():
            gap_queries = [q.strip() for q in gap_raw.splitlines()
                           if q.strip() and len(q.strip()) > 5][:3]
            if gap_queries:
                log.info("research_gap_queries", ticker=ticker, count=len(gap_queries))
                fetched_2 = await _search_and_fetch(
                    gap_queries,
                    context=f"{ticker} supplementary research",
                    fetch_top_n=cfg.RESEARCH_GAP_TOP_N,
                )
                log.info("research_fetch2", ticker=ticker, count=len(fetched_2))
        else:
            log.info("research_gap_sufficient", ticker=ticker)

    all_fetched = fetched_1 + fetched_2

    # ── [Synth] 보고서 합성 ────────────────────────────────────
    if not all_fetched:
        synth_md = f"데이터 수집 실패 — 검색 결과 없음 ({today})"
    else:
        sources_text = "\n\n---\n\n".join(
            f"### 소스 {i+1}: {d['title']}\nURL: {d['url']}\n\n{d['content'][:2000]}"
            for i, d in enumerate(all_fetched)
        )
        synth_prompt = (
            f"You are a professional stock analyst. Synthesize the following research "
            f"sources into a comprehensive investment research report for {ticker}.\n"
            f"Today: {today}{context_section}\n\n"
            f"Write in Korean. Structure the report with these sections:\n"
            f"## 핵심 요약\n"
            f"## 최신 뉴스 및 이벤트\n"
            f"## 실적 및 밸류에이션\n"
            f"## 기술적 분석 시사점\n"
            f"## 옵션/기관 흐름\n"
            f"## 리스크 요인\n"
            f"## 종합 판단\n\n"
            f"Be concise but thorough. Base everything on the sources provided.\n\n"
            f"SOURCES:\n{sources_text}"
        )
        synth_md = await _llm(synth_prompt, max_tokens=3000)

    # ── 보고서 조립 및 Obsidian 저장 ───────────────────────────
    report = _build_report_header(ticker, "📊 심층 리서치 보고서")
    report += synth_md
    report += f"\n\n---\n*수집 소스: {len(all_fetched)}개*\n"

    obs_path = f"Research/{ticker}_{today}.md"
    await _save_to_obsidian(obs_path, report)

    log.info("research_done", ticker=ticker, sources=len(all_fetched))
    return report


# ─────────────────────────────────────────────────────────────
# Phase 3: 실적 전 집중 리서치
# ─────────────────────────────────────────────────────────────

async def run_earnings_research(
    ticker: str,
    days_until: int,
    eps_estimate: Optional[float] = None,
) -> str:
    """
    실적 발표 D-7 이하 종목 집중 리서치.

    Args:
        ticker: 종목 심볼
        days_until: 실적까지 남은 일수
        eps_estimate: 예상 EPS (없으면 None)

    Returns:
        실적 미리보기 마크다운 섹션
    """
    today = date.today().isoformat()
    log.info("earnings_research_start", ticker=ticker, days_until=days_until)

    eps_hint = f" (EPS 예상: ${eps_estimate:.2f})" if eps_estimate is not None else ""
    quarter_hint = f"D-{days_until}{eps_hint}"

    queries = [
        f"{ticker} earnings preview analyst estimate {today}",
        f"{ticker} revenue guidance expectations Wall Street consensus",
        f"{ticker} options implied move earnings historical reaction",
        f"{ticker} sector earnings beat miss pattern Q2 2026",
    ]

    fetched = await _search_and_fetch(
        queries,
        context=f"{ticker} earnings preview — analyst estimates, implied move, sector",
        fetch_top_n=4,
    )
    log.info("earnings_research_fetch", ticker=ticker, count=len(fetched))

    if not fetched:
        return f"\n\n## 실적 미리보기 ({quarter_hint})\n\n데이터 수집 실패\n"

    sources_text = "\n\n---\n\n".join(
        f"[{d['title']}]\n{d['content'][:1500]}" for d in fetched
    )
    synth_prompt = (
        f"Analyze the upcoming earnings for {ticker} (reporting in {days_until} days{eps_hint}).\n"
        f"Today: {today}\n\n"
        f"Write in Korean. Cover these points:\n"
        f"- 애널리스트 컨센서스 EPS / 매출 예상\n"
        f"- 옵션 implied move (예상 주가 이동폭 %)\n"
        f"- 최근 실적 서프라이즈 패턴\n"
        f"- 주요 관전 포인트 (guidance, segment)\n"
        f"- 진입 전략 시사점 (IV crush 타이밍 등)\n\n"
        f"Be concise (300~500 chars per point). Base on sources only.\n\n"
        f"SOURCES:\n{sources_text}"
    )
    synth_md = await _llm(synth_prompt, max_tokens=2000)

    section = f"\n\n## 📅 실적 미리보기 ({quarter_hint})\n\n{synth_md}\n"
    log.info("earnings_research_done", ticker=ticker)
    return section


# ─────────────────────────────────────────────────────────────
# Phase 4: 포지션 급락 원인 분석
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
        synth_md = "검색 결과 없음 — 직접 확인 필요"
    else:
        sources_text = "\n\n---\n\n".join(
            f"[{d['title']}]\n{d['content'][:1500]}" for d in fetched
        )
        synth_prompt = (
            f"{ticker} dropped {pct_change:.1f}% today ({now_str}){entry_hint}.\n\n"
            f"Analyze the cause and conclude with ONE of these verdicts:\n"
            f"  - 정상 조정: normal technical pullback, no fundamental change\n"
            f"  - 실질 악재: real negative catalyst (earnings miss, guidance cut, regulatory, etc.)\n"
            f"  - 판단 불가: insufficient information\n\n"
            f"Write in Korean. Structure:\n"
            f"## 낙폭 원인 분석\n"
            f"## 관련 뉴스 요약\n"
            f"## 판단: [정상 조정 / 실질 악재 / 판단 불가]\n"
            f"## 대응 시사점\n\n"
            f"SOURCES:\n{sources_text}"
        )
        synth_md = await _llm(synth_prompt, max_tokens=2000)

        # 판단 추출 (Slack 알림용)
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
