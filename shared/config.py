"""
shared/config.py
================
환경 변수 기반 설정 관리 (python-dotenv + pydantic-settings 패턴)

.env 파일에서 자동으로 로드하며, 타입 안전성과 기본값을 보장합니다.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from shared import strategy as _strategy  # 전략 상수 단일 소스

# .env 파일 로드 (프로젝트 루트)
_root = Path(__file__).resolve().parents[1]
load_dotenv(_root / ".env", override=False)


class Config:
    """SwingMCP 전체 설정 클래스 (싱글톤 패턴)"""

    # ── OpenRouter ──────────────────────────────────────────
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_REFERER: str = "https://swing-mcp.local"

    # ── LLM 폴백 체인 (지정 모델 없거나 실패 시 순서대로 시도) ──────
    LLM_PRIMARY_MODEL: str = os.getenv(
        "LLM_PRIMARY_MODEL", "openai/gpt-oss-120b:free"
    )
    LLM_FALLBACK_MODEL: str = os.getenv(
        "LLM_FALLBACK_MODEL", "google/gemma-4-31b-it:free"
    )
    LLM_FALLBACK_2: str = os.getenv(
        "LLM_FALLBACK_2", "deepseek/deepseek-v4-flash:free"
    )
    LLM_FALLBACK_3: str = os.getenv(
        "LLM_FALLBACK_3", "anthropic/claude-haiku-4-5"
    )
    LLM_TIMEOUT_SECONDS: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
    LLM_TEMPERATURE: float = 0.0    # 항상 결정론적

    # ── 태스크별 지정 모델 (.env 한 곳에서 관리) ──────────────────
    # 비워두면(="") 폴백 체인 전체 사용
    LLM_MODEL_BUY_RESEARCH: str = os.getenv(
        "LLM_MODEL_BUY_RESEARCH", "anthropic/claude-haiku-4-5"
    )
    LLM_MODEL_BUY_TECH_NARRATIVE: str = os.getenv(
        "LLM_MODEL_BUY_TECH_NARRATIVE", "deepseek/deepseek-v4-flash"
    )
    LLM_MODEL_KAVOUT_EARNINGS: str = os.getenv(
        "LLM_MODEL_KAVOUT_EARNINGS", "deepseek/deepseek-v4-flash"
    )
    LLM_MODEL_SELL_HEALTH: str = os.getenv(
        "LLM_MODEL_SELL_HEALTH", "openai/gpt-oss-120b:free"
    )
    LLM_MODEL_SELL_ENV: str = os.getenv(
        "LLM_MODEL_SELL_ENV", "deepseek/deepseek-v4-flash:free"
    )
    LLM_MODEL_NL_ROUTING: str = os.getenv(
        "LLM_MODEL_NL_ROUTING", "deepseek/deepseek-v4-flash:free"
    )

    # ── Obsidian ─────────────────────────────────────────────
    OBSIDIAN_API_KEY: str = os.getenv("OBSIDIAN_API_KEY", "")
    BRAVE_API_KEY: str = os.getenv("BRAVE_API_KEY", "")
    OBSIDIAN_BASE_URL: str = os.getenv(
        "OBSIDIAN_BASE_URL", "https://127.0.0.1:27124"
    )
    OBSIDIAN_VAULT: str = os.getenv("OBSIDIAN_VAULT", r"C:\lian")

    # ── Slack ─────────────────────────────────────────────────
    SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
    SLACK_CHANNEL_MAIN: str = os.getenv("SLACK_CHANNEL_MAIN", "#swing-trading")
    SLACK_CHANNEL_ALERT: str = os.getenv("SLACK_CHANNEL_ALERT", "#swing-alerts")

    # ── 파일 경로 ─────────────────────────────────────────────
    SUMMARY_DIR: str = os.getenv("SUMMARY_DIR", r"R:\내 드라이브\마켓 수치")
    FINVIZ_FILE: str = os.getenv(
        "FINVIZ_FILE", r"Y:\내 드라이브\어닝\finviz_all_rows.txt"
    )
    EARNINGS_DIR: str = os.getenv("EARNINGS_DIR", r"Y:\내 드라이브\어닝")
    DATA_DIR: str = os.getenv("DATA_DIR", r"Y:\내 드라이브\Data")
    POSITIONS_FILE: str = os.getenv("POSITIONS_FILE", r"C:\lian\positions.md")
    WATCHLIST_FILE: str = os.getenv("WATCHLIST_FILE", r"C:\lian\Swing\watchlist.md")

    # ── 로컬 상태 디렉토리 (런타임 생성) ─────────────────────
    CACHE_DIR: str = str(_root / "shared" / "cache")
    SNAPSHOTS_DIR: str = str(_root / "shared" / "state" / "snapshots")
    REQUEUE_FILE: str = str(_root / "shared" / "state" / "requeue.json")
    LOGS_DIR: str = str(_root / "shared" / "logs")
    RSS_FEEDS_FILE: str = os.getenv("RSS_FEEDS_FILE", str(_root / "shared" / "rss_feeds.json"))

    # ── 리스크 파라미터 ───────────────────────────────────────
    # ★ TOTAL_CAPITAL 변경 시 이 한 줄만 수정 (.env의 TOTAL_CAPITAL)
    # 3000만원 ÷ 1350 ≈ $22,222
    TOTAL_CAPITAL: float = float(os.getenv("TOTAL_CAPITAL", "22222"))
    MAX_PER_POSITION: float = float(os.getenv("MAX_PER_POSITION", "1000"))
    COMMISSION_PER_CONTRACT: float = float(
        os.getenv("COMMISSION_PER_CONTRACT", "0.50")   # IBKR 기본
    )
    RISK_FREE_RATE: float = float(os.getenv("RISK_FREE_RATE", "0.0436"))

    # ── 자본 배분 비율 ─────────────────────────────────────────
    # 전체 TOTAL_CAPITAL 중 다음 투자를 위해 유보하는 비율
    NEXT_TRADE_RESERVE_PCT: float = float(os.getenv("NEXT_TRADE_RESERVE_PCT", "0.30"))
    # 나머지 투자 가능 금액 내 배분
    # investable = TOTAL_CAPITAL × (1 - NEXT_TRADE_RESERVE_PCT)
    ENTRY_1ST_PCT: float = float(os.getenv("ENTRY_1ST_PCT", "0.50"))   # 1차 진입
    ENTRY_2ND_PCT: float = float(os.getenv("ENTRY_2ND_PCT", "0.30"))   # 2차 진입 (방향 확인 후)
    RESERVE_PCT:   float = float(os.getenv("RESERVE_PCT",   "0.20"))   # 보험 현금

    @property
    def investable_capital(self) -> float:
        """실제 투자 가능 금액 (다음 투자 유보분 제외)"""
        return self.TOTAL_CAPITAL * (1.0 - self.NEXT_TRADE_RESERVE_PCT)

    @property
    def budget_1st(self) -> float:
        """1차 진입 예산"""
        return self.investable_capital * self.ENTRY_1ST_PCT

    @property
    def budget_2nd(self) -> float:
        """2차 진입 예산 (방향 확인 후 수동 진입)"""
        return self.investable_capital * self.ENTRY_2ND_PCT

    @property
    def budget_reserve(self) -> float:
        """보험 현금"""
        return self.investable_capital * self.RESERVE_PCT

    # ── 파일 감시 설정 ────────────────────────────────────────
    WATCHER_DEBOUNCE_SECONDS: int = 30

    # ── 캐시 설정 ─────────────────────────────────────────────
    CACHE_TTL_HOURS: int = int(os.getenv("CACHE_TTL_HOURS", "24"))
    SNAPSHOT_RETENTION_DAYS: int = int(
        os.getenv("SNAPSHOT_RETENTION_DAYS", "30")
    )

    # ── 전략 파라미터 (shared/strategy.py 단일 소스 → 역호환 alias) ────────
    # 수정 시 shared/strategy.py 를 변경하세요. 여기는 alias만 유지합니다.
    RVOL_MIN:               float = _strategy.RVOL_MIN
    OI_MIN:                 int   = _strategy.OI_MIN
    OI_WARNING:             int   = _strategy.OI_WARNING
    PRICE_MIN:              float = _strategy.PRICE_MIN
    PRICE_TRADE_MIN:        float = _strategy.PRICE_TRADE_MIN
    MARKET_CAP_MIN:         float = _strategy.MARKET_CAP_MIN
    EARNINGS_PROXIMITY_DAYS: int  = _strategy.EARNINGS_PROXIMITY_DAYS
    SECTOR_MAX_COUNT:       int   = _strategy.SECTOR_MAX_COUNT
    DELTA_MIN:              float = _strategy.DELTA_MIN
    DELTA_MAX:              float = _strategy.DELTA_MAX
    IVR_MAX:                float = _strategy.IVR_MAX
    IVR_WARNING:            float = _strategy.IVR_WARNING
    SPREAD_MAX_PCT:         float = _strategy.SPREAD_MAX_PCT
    DTE_MIN:                int   = _strategy.DTE_MIN
    TRAILING_STOP_PCT:      float = _strategy.TRAILING_STOP_PCT
    FIRST_TARGET_GAIN_PCT:  float = _strategy.FIRST_TARGET_GAIN_PCT

    # ── Obsidian 노트 경로 패턴 ───────────────────────────────
    BUY_NOTE_PATH_TEMPLATE: str = "swing-procedure/notes/buy/{date}.md"
    SELL_NOTE_PATH_TEMPLATE: str = "swing-procedure/notes/sell/{date}.md"
    TICKER_NOTE_PATH_TEMPLATE: str = "swing-procedure/tickers/{ticker}.md"
    REJECTED_NOTE_PATH_TEMPLATE: str = "swing-procedure/rejected/{ticker}_{date}.md"

    @classmethod
    def ensure_local_dirs(cls) -> None:
        """로컬 런타임 디렉토리 생성"""
        for d in [cls.CACHE_DIR, cls.SNAPSHOTS_DIR, cls.LOGS_DIR,
                  str(Path(cls.REQUEUE_FILE).parent)]:
            Path(d).mkdir(parents=True, exist_ok=True)

    @classmethod
    def validate(cls) -> list[str]:
        """필수 환경 변수 검증, 누락 목록 반환"""
        missing = []
        if not cls.OPENROUTER_API_KEY:
            missing.append("OPENROUTER_API_KEY")
        if not cls.OBSIDIAN_API_KEY:
            missing.append("OBSIDIAN_API_KEY")
        if not cls.SLACK_BOT_TOKEN:
            missing.append("SLACK_BOT_TOKEN")
        return missing


@lru_cache(maxsize=1)
def get_config() -> Config:
    """설정 싱글톤 반환"""
    cfg = Config()
    cfg.ensure_local_dirs()
    return cfg
