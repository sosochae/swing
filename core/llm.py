"""
core/llm.py
===========
LLM 클라이언트 통합 모듈 (T3: client·cache·fallback·role_lock·mcp_bridge → 1개)

담당:
- call_llm(): OpenRouter API 호출 (다단계 폴백)
- get_cached_or_fetch(): 캐시 우선 조회 후 LLM 호출
- apply_role_lock(): Role Lock 스니펫 삽입
- call_ddg_search(): DuckDuckGo 실시간 웹 검색 (API 키 불필요)
- call_brave_search(): Brave Web Search API (BRAVE_API_KEY 필요)
- call_topic_trend_search(): [DEPRECATED] TrendRadar 로컬 DB 검색 (US주식 데이터 없음)
- call_deep_research(): [DEPRECATED] TrendRadar 기반 Deep Research 대체 구현
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx
from tenacity import (  # type: ignore
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from shared.config import get_config
from shared.logger import get_logger
from shared.prompts import get_role_lock
from shared.schemas import LLMRequest, LLMResponse

log = get_logger()
cfg = get_config()

# ── 무료 폴백 체인 (미등록 템플릿 / 지정 모델 실패 시 순서대로 시도) ──
# 원칙: 이 체인은 전부 무료 — claude-haiku-4.5는 buy_step3_research 지정 모델에서만 쓰임
#   nvidia/nemotron-3-super-120b-a12b:free → Nemotron 120B  (ctx 1M, JSON 검증)
#   meta-llama/llama-3.3-70b-instruct:free → Llama 3.3 70B  (ctx 131k, JSON 안정)
#   qwen/qwen3-coder:free                  → Qwen3 Coder    (ctx 1M, 구조화 강점)
#   openai/gpt-oss-120b:free               → GPT OSS 120B   (ctx 131k, 최후 무료)
MODEL_PRIORITY = [
    cfg.LLM_PRIMARY_MODEL  or "nvidia/nemotron-3-super-120b-a12b:free",  # 1순위 (무료)
    cfg.LLM_FALLBACK_MODEL or "meta-llama/llama-3.3-70b-instruct:free",  # 2순위 (무료)
    cfg.LLM_FALLBACK_2     or "qwen/qwen3-coder:free",                   # 3순위 (무료)
    cfg.LLM_FALLBACK_3     or "openai/gpt-oss-120b:free",                # 4순위 (무료)
]


# ─────────────────────────────────────────────────────────────
# 1. OpenRouter LLM 클라이언트
# ─────────────────────────────────────────────────────────────

async def call_llm(
    messages: list[dict[str, str]],
    system_prompt: str = "",
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    response_format: str | None = None,
    timeout: float | None = None,
) -> LLMResponse:
    """
    OpenRouter API 호출 (다단계 폴백 포함)

    Args:
        messages: [{"role": "user", "content": "..."}, ...]
        system_prompt: 시스템 프롬프트 (role lock 자동 삽입됨)
        model: 특정 모델 지정 (None이면 우선순위대로 시도)
        temperature: 0.0 (결정론적)
        max_tokens: 최대 출력 토큰
        response_format: "json_object" | None
        timeout: 타임아웃 초 (None이면 cfg 기본값)

    Returns:
        LLMResponse

    Raises:
        RuntimeError: 모든 모델 폴백 실패 시
    """
    if not cfg.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    timeout = timeout or float(cfg.LLM_TIMEOUT_SECONDS)
    # 지정 모델이 있으면 그것을 1순위로, 이후 MODEL_PRIORITY 폴백 체인 추가
    # (지정 모델 실패 시에도 무료 체인으로 자동 강등)
    if model and model not in MODEL_PRIORITY:
        models_to_try = [model] + MODEL_PRIORITY
    elif model:
        # 지정 모델을 맨 앞으로 끌어오고 중복 제거
        rest = [m for m in MODEL_PRIORITY if m != model]
        models_to_try = [model] + rest
    else:
        models_to_try = MODEL_PRIORITY

    # Role Lock 삽입
    full_system = apply_role_lock(system_prompt)

    for attempt_model in models_to_try:
        try:
            start = time.monotonic()
            response = await _call_openrouter(
                messages=messages,
                system_prompt=full_system,
                model=attempt_model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                timeout=timeout,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)

            log.info(
                "llm_call_success",
                model=attempt_model,
                duration_ms=elapsed_ms,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
            response.duration_ms = elapsed_ms
            return response

        except Exception as exc:
            log.warning("llm_fallback", model=attempt_model, error=str(exc))
            if attempt_model == models_to_try[-1]:
                raise RuntimeError(f"All LLM models failed. Last error: {exc}") from exc
            await asyncio.sleep(2)

    raise RuntimeError("LLM call failed unexpectedly")


async def _call_openrouter(
    messages: list[dict[str, str]],
    system_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    response_format: str | None,
    timeout: float,
) -> LLMResponse:
    """단일 OpenRouter API 호출"""
    headers = {
        "Authorization": f"Bearer {cfg.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": cfg.OPENROUTER_REFERER,
        "X-Title": "SwingMCP",
    }

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if system_prompt:
        payload["messages"] = [
            {"role": "system", "content": system_prompt},
            *messages,
        ]

    if response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{cfg.OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    choice = data["choices"][0]["message"]
    content = choice.get("content")

    # null content = 모델이 응답 거부 또는 rate limit → 다음 폴백 트리거
    if content is None:
        refusal = choice.get("refusal") or "null content"
        raise RuntimeError(f"Model returned null content: {refusal}")

    usage = data.get("usage", {})

    return LLMResponse(
        content=content,
        model_used=model,
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        cached=False,
    )


# ─────────────────────────────────────────────────────────────
# 2. 캐시 관리
# ─────────────────────────────────────────────────────────────

def _cache_key_hash(key: str) -> str:
    """캐시 키를 파일명으로 사용 가능한 해시로 변환"""
    return hashlib.md5(key.encode()).hexdigest()[:16]


def _get_cache_path(cache_key: str) -> Path:
    """캐시 파일 경로 반환 (shared/cache/{hash}.json)"""
    return Path(cfg.CACHE_DIR) / f"{_cache_key_hash(cache_key)}.json"


def get_cache(key: str) -> dict | None:
    """
    캐시 조회

    Args:
        key: 캐시 키 (예: 'AMD_2026-05-11_topic_trend')

    Returns:
        캐시된 딕셔너리 또는 만료/없음 시 None
    """
    cache_path = _get_cache_path(key)
    if not cache_path.exists():
        return None

    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        # 만료 확인
        expires_str = data.get("_expires")
        if expires_str:
            expires = datetime.fromisoformat(expires_str)
            if datetime.now() > expires:
                cache_path.unlink(missing_ok=True)
                log.info("cache_expired", key=key)
                return None
        return data.get("_value")
    except Exception as exc:
        log.warning("cache_read_error", key=key, error=str(exc))
        return None


def set_cache(
    key: str,
    value: dict | list | str,
    expires_today: bool = False,
    ttl_hours: int | None = None,
) -> None:
    """
    캐시 저장

    Args:
        key: 캐시 키
        value: 저장할 값
        expires_today: True이면 당일 자정 만료 (기본 False = 영구)
        ttl_hours: 시간 단위 TTL (expires_today보다 우선, None = 영구)
    """
    Path(cfg.CACHE_DIR).mkdir(parents=True, exist_ok=True)
    cache_path = _get_cache_path(key)

    data: dict = {
        "_key": key,
        "_cached_at": datetime.now().isoformat(),
        "_value": value,
    }

    if ttl_hours:
        expires = datetime.now() + timedelta(hours=ttl_hours)
        data["_expires"] = expires.isoformat()
    elif expires_today:
        today = date.today()
        expires = datetime(today.year, today.month, today.day, 23, 59, 59)
        data["_expires"] = expires.isoformat()
    # else: _expires 없음 → 영구 저장

    cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    expires_info = data.get("_expires", "permanent")
    log.info("cache_set", key=key, expires=expires_info)


async def get_cached_or_fetch(
    cache_key: str,
    fetch_fn: Callable,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """
    캐시 우선 조회 후 없으면 fetch_fn 실행하여 캐시 저장

    Args:
        cache_key: 캐시 키
        fetch_fn: 캐시 미스 시 호출할 async 함수
        *args, **kwargs: fetch_fn에 전달할 인자

    Returns:
        캐시된 값 또는 새로 가져온 값
    """
    cached = get_cache(cache_key)
    if cached is not None:
        log.info("cache_hit", key=cache_key)
        return cached

    log.info("cache_miss", key=cache_key)
    result = await fetch_fn(*args, **kwargs)
    if result is not None:
        set_cache(cache_key, result)
    return result


def clear_cache(ticker: str | None = None) -> int:
    """
    캐시 삭제

    Args:
        ticker: 특정 종목만 삭제 (None이면 만료 캐시 전체)

    Returns:
        삭제된 파일 수
    """
    cache_dir = Path(cfg.CACHE_DIR)
    deleted = 0
    for f in cache_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            key = data.get("_key", "")
            if ticker and ticker not in key:
                continue
            f.unlink()
            deleted += 1
        except Exception:
            pass
    log.info("cache_cleared", deleted=deleted, ticker=ticker)
    return deleted


# ─────────────────────────────────────────────────────────────
# 3. Role Lock 적용
# ─────────────────────────────────────────────────────────────

def apply_role_lock(system_prompt: str = "") -> str:
    """
    시스템 프롬프트에 Role Lock 스니펫 삽입

    Args:
        system_prompt: 기존 시스템 프롬프트

    Returns:
        Role Lock이 포함된 시스템 프롬프트
    """
    role_lock = get_role_lock()
    if system_prompt:
        return f"{role_lock}\n\n{system_prompt}"
    return role_lock


# ─────────────────────────────────────────────────────────────
# 4. JSON 응답 파싱 유틸리티
# ─────────────────────────────────────────────────────────────

def parse_llm_json(response: LLMResponse) -> dict:
    """
    LLM 응답에서 JSON 파싱 (마크다운 블록 제거 포함)

    Args:
        response: LLMResponse

    Returns:
        파싱된 딕셔너리

    Raises:
        json.JSONDecodeError: JSON 파싱 실패
    """
    content = response.content.strip()

    # ```json ... ``` 블록 제거
    if content.startswith("```"):
        lines = content.split("\n")
        # 첫 번째 ``` 줄 제거
        start = 1
        # 마지막 ``` 줄 제거
        end = len(lines)
        if lines[-1].strip() == "```":
            end -= 1
        content = "\n".join(lines[start:end]).strip()

    return json.loads(content)


# ─────────────────────────────────────────────────────────────
# 5. DuckDuckGo 실시간 웹 검색 (API 키 불필요, 무료)
# ─────────────────────────────────────────────────────────────

async def call_ddg_search(
    query: str,
    num_results: int = 8,
    force_refresh: bool = False,
) -> list[dict[str, str]]:
    """
    DuckDuckGo 실시간 웹 검색 (ddgs 패키지 사용, region=us-en 고정)

    - API 키 불필요, 무료
    - 캐시 키: {query_hash}_{date}_ddg
    - region="us-en": 영어 미국 결과 강제 (한국어 결과 방지)
    - Graceful Degradation: 패키지 오류 시 빈 리스트 반환

    Returns:
        [{"title": ..., "description": ..., "url": ..., "source": "duckduckgo"}]
    """
    cache_key = f"{hashlib.md5(query.encode()).hexdigest()[:8]}_{date.today()}_ddg"
    if not force_refresh:
        cached = get_cache(cache_key)
        if cached:
            log.info("ddg_cache_hit", query=query[:60])
            return cached  # type: ignore

    try:
        from ddgs import DDGS  # type: ignore
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except ImportError:
            log.warning("ddg_not_installed", hint="pip install ddgs")
            return []

    log.info("ddg_search", query=query[:60])
    try:
        loop = asyncio.get_event_loop()

        def _search() -> list[dict]:
            with DDGS() as ddgs:
                return list(ddgs.text(
                    query,
                    max_results=num_results,
                    region="us-en",    # 영어 미국 결과 고정
                    safesearch="off",
                ))

        raw = await loop.run_in_executor(None, _search)
        results = [
            {
                "title":       r.get("title", ""),
                "description": r.get("body", "")[:300],
                "url":         r.get("href", ""),
                "source":      "duckduckgo",
            }
            for r in raw
        ]
        set_cache(cache_key, results)
        log.info("ddg_search_done", query=query[:60], count=len(results))
        return results
    except Exception as exc:
        log.warning("ddg_search_failed", query=query[:60], error=str(exc))
        return []


# ─────────────────────────────────────────────────────────────
# 6. Topic-Trend MCP 연동 [DEPRECATED — TrendRadar는 로컬 DB, US주식 데이터 없음]
# ─────────────────────────────────────────────────────────────

@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=60, min=60, max=300),
    reraise=False,
)
async def call_topic_trend_search(
    query: str,
    days: int = 7,
    limit: int = 10,
    include_url: bool = False,
) -> list[dict[str, str]]:
    """
    Topic-Trend MCP의 search_news 도구 호출

    캐시 키: {query_hash}_{date}_topic_trend
    캐시 만료: 당일 자정

    Args:
        query: 검색 쿼리
        days: 검색 기간 (일)
        limit: 결과 수 제한
        include_url: URL 포함 여부

    Returns:
        뉴스 항목 리스트 [{"title": ..., "source": ..., "url": ...}]
    """
    cache_key = f"{hashlib.md5(query.encode()).hexdigest()[:8]}_{date.today()}_topic_trend"
    cached = get_cache(cache_key)
    if cached:
        log.info("topic_trend_cache_hit", query=query)
        return cached  # type: ignore

    log.info("topic_trend_search", query=query)

    # MCP Tool 호출 시뮬레이션
    # 실제 환경에서는 MCP stdio 프로토콜로 호출
    try:
        # mcp--trendradar--search_news 도구 호출 (실제 구현)
        result = await _call_mcp_tool(
            "mcp--trendradar--search_news",
            {
                "query": query,
                "limit": limit,
                "include_url": include_url,
                "include_rss": True,
                "search_mode": "keyword",
            },
        )
        # TrendRadar 응답 구조: {"success": true, "results": [...], "total": N}
        raw_items = result.get("results", []) if isinstance(result, dict) else []
        news_items = [
            {
                "title":       item.get("title", ""),
                "source":      item.get("platform", item.get("source", "trendradar")),
                "url":         item.get("url", ""),
                "description": item.get("summary", item.get("description", "")),
            }
            for item in raw_items
        ]
        set_cache(cache_key, news_items)
        return news_items

    except Exception as exc:
        log.warning("topic_trend_failed", query=query, error=str(exc))
        return []


# ─────────────────────────────────────────────────────────────
# 6. Brave Web Search (무료 1,000회/월)
# ─────────────────────────────────────────────────────────────
async def call_brave_search(query: str, count: int = 3) -> list[dict[str, str]]:
    """
    Brave Web Search API 직접 호출 (httpx)
    엔드포인트: https://api.search.brave.com/res/v1/web/search
    헤더: X-Subscription-Token: {BRAVE_API_KEY}
    캐시 키: {query_hash}_{date}_brave

    Returns:
        [{"title": ..., "url": ..., "description": ...}]
    """
    # 키 유효성: 비어있거나 비-ASCII(한국어 플레이스홀더 등)이면 무시
    if not cfg.BRAVE_API_KEY or not cfg.BRAVE_API_KEY.isascii():
        return []

    cache_key = f"{hashlib.md5(query.encode()).hexdigest()[:8]}_{date.today()}_brave"
    cached = get_cache(cache_key)
    if cached:
        return cached  # type: ignore

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "X-Subscription-Token": cfg.BRAVE_API_KEY,
                    "Accept": "application/json",
                },
                params={"q": query, "count": count, "search_lang": "en"},
            )
            resp.raise_for_status()
            data = resp.json()
            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "description": r.get("description", ""),
                    "source": "brave",
                }
                for r in data.get("web", {}).get("results", [])[:count]
            ]
            set_cache(cache_key, results)
            return results
    except Exception as exc:
        log.warning("brave_search_failed", query=query, error=str(exc))
        return []


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=60, min=60, max=300),
    reraise=False,
)
async def call_deep_research(
    question: str,
    report_type: str = "research_report",
    timeout: float = 120.0,
) -> str:
    """
    Deep Research 호출 (120초 타임아웃)

    캐시 키: {question_hash}_{date}_research

    Args:
        question: 리서치 질문
        report_type: 보고서 유형
        timeout: 타임아웃 초

    Returns:
        리서치 결과 텍스트
    """
    cache_key = f"{hashlib.md5(question.encode()).hexdigest()[:8]}_{date.today()}_research"
    cached = get_cache(cache_key)
    if cached:
        log.info("deep_research_cache_hit", question=question[:50])
        return cached  # type: ignore

    log.info("deep_research_start", question=question[:80])

    try:
        # 실제 환경에서는 ResearchAgent 또는 별도 MCP 서버 호출
        result = await asyncio.wait_for(
            _call_mcp_tool("deep_research", {"question": question, "report_type": report_type}),
            timeout=timeout,
        )
        content = result if isinstance(result, str) else str(result)
        set_cache(cache_key, content)
        return content

    except asyncio.TimeoutError:
        log.warning("deep_research_timeout", question=question[:50])
        return ""
    except Exception as exc:
        log.warning("deep_research_failed", error=str(exc))
        return ""


async def _call_mcp_tool(tool_name: str, params: dict) -> Any:
    """
    MCP 도구 호출 인터페이스

    라우팅:
    - mcp--trendradar--*  → TrendRadar (C:\\MCP\\Topic-Trend) 자체 venv 서브프로세스
    - deep_research        → TrendRadar search_news + analyze_sentiment 조합 리서치
    """
    if tool_name.startswith("mcp--trendradar--") or tool_name == "mcp--trendradar--search_news":
        return await _call_trendradar(tool_name.replace("mcp--trendradar--", ""), params)
    elif tool_name == "deep_research":
        return await _call_deep_research_via_trendradar(params)
    else:
        raise NotImplementedError(
            f"MCP tool '{tool_name}' not available in this environment. "
            "Connect to Topic-Trend MCP server."
        )


# ── TrendRadar 서브프로세스 헬퍼 ──────────────────────────────────

_TRENDRADAR_PYTHON = r"C:\MCP\Topic-Trend\.venv\Scripts\python.exe"
_TRENDRADAR_ROOT   = r"C:\MCP\Topic-Trend"


async def _call_trendradar(func_name: str, params: dict) -> Any:
    """
    TrendRadar 자체 venv Python으로 함수 직접 호출 (서브프로세스).
    지원 func_name:
      search_news           → SearchTools.search_news_unified
      get_latest_news       → DataQueryTools.get_latest_news
      analyze_sentiment     → AnalyticsTools.analyze_sentiment
      get_trending_topics   → DataQueryTools.get_trending_topics
    """
    import asyncio as _asyncio
    import json as _json

    # params를 안전하게 직렬화
    params_json = _json.dumps(params, ensure_ascii=False)

    script = f"""
import sys, json
sys.path.insert(0, r'{_TRENDRADAR_ROOT}')
params = json.loads({params_json!r})
try:
    func = {func_name!r}
    if func in ('search_news', 'search_news_unified'):
        from mcp_server.tools.search_tools import SearchTools
        result = SearchTools(r'{_TRENDRADAR_ROOT}').search_news_unified(**params)
    elif func == 'get_latest_news':
        from mcp_server.tools.data_query import DataQueryTools
        result = DataQueryTools(r'{_TRENDRADAR_ROOT}').get_latest_news(**params)
    elif func == 'analyze_sentiment':
        from mcp_server.tools.analytics import AnalyticsTools
        result = AnalyticsTools(r'{_TRENDRADAR_ROOT}').analyze_sentiment(**params)
    elif func == 'get_trending_topics':
        from mcp_server.tools.data_query import DataQueryTools
        result = DataQueryTools(r'{_TRENDRADAR_ROOT}').get_trending_topics(**params)
    else:
        result = {{"error": f"Unknown func: {{func}}"}}
    print(json.dumps(result, ensure_ascii=False))
except Exception as e:
    print(json.dumps({{"error": str(e), "type": type(e).__name__}}))
"""

    try:
        proc = await _asyncio.create_subprocess_exec(
            _TRENDRADAR_PYTHON, "-c", script,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
            env={**__import__('os').environ,
                 "PYTHONUTF8": "1",
                 "PYTHONIOENCODING": "utf-8",
                 "PYTHONPATH": _TRENDRADAR_ROOT},
        )
        stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=25.0)
        raw = stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            raise RuntimeError(f"TrendRadar empty output. stderr={stderr.decode('utf-8', errors='replace')[:200]}")
        result = _json.loads(raw)
        if "error" in result:
            raise RuntimeError(f"TrendRadar error: {result['error']}")
        return result
    except _asyncio.TimeoutError:
        raise RuntimeError("TrendRadar subprocess timed out (25s)")


async def _call_deep_research_via_trendradar(params: dict) -> str:
    """
    Deep-Research 대체 구현:
    TrendRadar의 search_news(include_rss=True) 결과로 리서치 텍스트 조합.

    gpt-researcher(C:\\MCP\\Deep-research)는 OpenAI API + WebSocket 의존성으로
    독립 호출 불가 → TrendRadar의 로컬 뉴스 데이터로 대체.
    TrendRadar가 US 주식 데이터를 가지지 않는 경우 gracefully 빈 문자열 반환.
    """
    question = params.get("question", "")
    # 검색 키워드: 첫 4단어 (영어 주식 쿼리용)
    keywords = " ".join(question.split()[:4])

    sections: list[str] = [f"## Research Summary: {question[:100]}", ""]

    try:
        search_result = await _call_trendradar("search_news", {
            "query": keywords,
            "search_mode": "keyword",
            "limit": 8,
            "include_url": True,
            "include_rss": True,
        })
        news_items = search_result.get("results", [])
        if news_items:
            sections.append("### 관련 뉴스 (TrendRadar)")
            for item in news_items[:6]:
                title = item.get("title", "")
                src   = item.get("platform", item.get("source", ""))
                url   = item.get("url", "")
                line  = f"- **{title}**"
                if src:
                    line += f" ({src})"
                if url:
                    line += f"  {url}"
                sections.append(line)
            sections.append("")
    except Exception as exc:
        log.debug("deep_research_search_fail", error=str(exc))

    # 뉴스가 없으면 빈 문자열 (graceful degradation)
    if len(sections) <= 2:
        return ""

    return "\n".join(sections)


# ─────────────────────────────────────────────────────────────
# 6. 통합 LLM 분석 워크플로우
# ─────────────────────────────────────────────────────────────

async def analyze_with_llm(
    template_name: str,
    template_vars: dict[str, Any],
    cache_key: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """
    프롬프트 렌더링 → LLM 호출 → JSON 파싱 통합 워크플로우

    Args:
        template_name: shared/prompts.py의 템플릿 이름
        template_vars: 템플릿 변수
        cache_key: 캐시 키 (None이면 캐시 미사용)
        force_refresh: True이면 캐시 무시

    Returns:
        LLM 응답 JSON 딕셔너리
    """
    from shared.prompts import render, get_model_for

    # 템플릿별 최대 출력 토큰 (기본 4096, 대형 출력 템플릿은 별도 지정)
    _TEMPLATE_MAX_TOKENS: dict[str, int] = {
        "buy_step3_research": 8192,          # 뉴스 50개 합성 + 7섹션 내러티브 → 4096 잘림 방지
        "buy_step3b_technical_narrative": 4096,
    }
    max_tokens = _TEMPLATE_MAX_TOKENS.get(template_name, 4096)

    # 캐시 확인
    if cache_key and not force_refresh:
        cached = get_cache(cache_key)
        if cached:
            log.info("llm_cache_hit", template=template_name, key=cache_key)
            return cached  # type: ignore

    # 프롬프트 렌더링
    prompt = render(template_name, **template_vars)
    model = get_model_for(template_name)

    # LLM 호출
    response = await call_llm(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.0,
        max_tokens=max_tokens,
        response_format="json_object",
    )

    # JSON 파싱
    result = parse_llm_json(response)

    # 캐시 저장
    if cache_key:
        set_cache(cache_key, result)

    return result


# ─────────────────────────────────────────────────────────────
# 7. RSS 피드 수집 유틸리티 (buy/sell 공용)
# ─────────────────────────────────────────────────────────────

async def _collect_rss_feeds(
    feed_urls: list[str],
    label: str = "feed",
    max_per_feed: int = 5,
) -> list[dict]:
    """
    RSS 피드 URL 목록에서 뉴스 항목 수집 (feedparser, Graceful Degradation)

    Args:
        feed_urls: RSS URL 리스트
        label: 로그용 레이블 (종목 또는 "market")
        max_per_feed: 피드 당 최대 항목 수

    Returns:
        [{"title": ..., "source": "rss", "description": ..., "url": ...}]
    """
    results: list[dict] = []
    try:
        import feedparser  # type: ignore
    except ImportError:
        log.warning("feedparser_not_installed", hint="pip install feedparser")
        return results

    for url in feed_urls:
        try:
            loop = asyncio.get_running_loop()
            feed = await loop.run_in_executor(None, feedparser.parse, url)
            for entry in feed.entries[:max_per_feed]:
                results.append({
                    "title": entry.get("title", ""),
                    "source": "rss",
                    "description": entry.get("summary", "")[:300],
                    "url": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "feed_label": label,
                })
        except Exception as exc:
            log.warning("rss_feed_fail", url=url, label=label, error=str(exc))

    log.info("rss_collected", label=label, count=len(results))
    return results
