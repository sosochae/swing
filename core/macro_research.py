"""
core/macro_research.py
======================
매크로/섹터 테마 리서치 (Phase 2)

파이프라인 Step 1 말미에서 호출:
  macro_summary = await fetch_macro_context("US equity market")

결과를 shared/state/macro_context.json에 당일 캐시로 저장.
같은 날 재호출 시 캐시 반환 (검색 생략).

합성 요약은 PipelineContext.macro_research_summary에 주입되어
Step 5 LLM 프롬프트에 배경지식으로 삽입됨.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from shared.config import get_config
from shared.logger import get_logger
from core.llm import call_llm, call_ddg_search, rank_and_pick, fetch_url_as_markdown

log = get_logger()
cfg = get_config()

_CACHE_FILE = Path(cfg.CACHE_DIR).parent / "state" / "macro_context.json"


def _load_cache() -> dict:
    try:
        if _CACHE_FILE.exists():
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(data: dict) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("macro_cache_save_fail", error=str(exc))


async def _llm(prompt: str, max_tokens: int = 2048) -> str:
    resp = await call_llm(
        messages=[{"role": "user", "content": prompt}],
        model=cfg.RESEARCH_MODEL,
        temperature=0.0,
        max_tokens=max_tokens,
    )
    return resp.content.strip()


async def fetch_macro_context(theme: str = "US equity market") -> str:
    """
    테마별 매크로 리서치 수행, 300~500자 요약 반환.

    당일 캐시가 있으면 검색 없이 즉시 반환.

    Args:
        theme: 리서치 테마 (예: "US equity market", "HBM 반도체")

    Returns:
        요약 텍스트 (LLM 프롬프트 주입용)
    """
    today = date.today().isoformat()
    cache = _load_cache()

    # 당일 캐시 hit
    cache_key = f"{today}_{theme}"
    if cache.get(cache_key):
        log.info("macro_context_cache_hit", theme=theme, date=today)
        return cache[cache_key]

    log.info("macro_context_start", theme=theme)

    # ── [Plan] 쿼리 생성 ──────────────────────────────────────
    plan_prompt = (
        f"Generate 3 English web search queries about the current macro environment for: {theme}\n"
        f"Today: {today}\n\n"
        f"STRICT FORMAT RULES:\n"
        f"- Output exactly 3 lines\n"
        f"- Each line is a plain search query string only\n"
        f"- NO JSON, NO markdown, NO numbering, NO code blocks, NO explanations\n"
        f"- Example line: US semiconductor market outlook 2026\n\n"
        f"3 queries:"
    )
    queries_raw = await _llm(plan_prompt, max_tokens=150)
    queries = [q.strip() for q in queries_raw.splitlines() if q.strip()][:3]
    if not queries:
        queries = [
            f"{theme} market outlook {today}",
            f"{theme} investor sentiment current",
            f"{theme} key risks opportunities",
        ]

    # ── [Search + Fetch] ──────────────────────────────────────
    import asyncio
    raw_results: list[dict] = []
    search_tasks = [call_ddg_search(q, num_results=8) for q in queries]
    results = await asyncio.gather(*search_tasks, return_exceptions=True)
    seen_urls: set[str] = set()
    for batch in results:
        if isinstance(batch, list):
            for item in batch:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    raw_results.append(item)

    top_items = await rank_and_pick(
        raw_results,
        context=f"{theme} macro outlook and sentiment",
        top_n=3,
    )
    fetch_tasks = [fetch_url_as_markdown(item.get("url", ""), max_chars=3000) for item in top_items]
    bodies = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    fetched: list[dict] = []
    for item, body in zip(top_items, bodies):
        if isinstance(body, str) and body:
            fetched.append({"title": item.get("title", ""), "content": body})

    # ── [Synth] 요약 ──────────────────────────────────────────
    if not fetched:
        summary = f"매크로 데이터 수집 실패 ({today})"
    else:
        sources_text = "\n\n---\n\n".join(
            f"[{d['title']}]\n{d['content'][:1200]}" for d in fetched
        )
        synth_prompt = (
            f"Summarize the current macro environment for: {theme}\n"
            f"Today: {today}\n\n"
            f"STRICT FORMAT RULES:\n"
            f"- Write in plain Korean text only, 300~500 characters total\n"
            f"- NO JSON, NO code blocks, NO markdown headers, NO wrapping objects\n"
            f"- Start directly with the summary text\n\n"
            f"Cover: 전반적 시장 심리, 주요 리스크, 섹터 특이사항.\n\n"
            f"SOURCES:\n{sources_text}"
        )
        from core.research_agent import _unwrap_json
        summary = _unwrap_json(await _llm(synth_prompt, max_tokens=600))

    # 캐시 저장
    cache[cache_key] = summary
    _save_cache(cache)

    log.info("macro_context_done", theme=theme, chars=len(summary))
    return summary
