# SwingMCP CODEINDEX — 수정 작업 최적화 참조서

> **사용법**: 새 세션에서 수정 요청 시 소스 코드를 읽기 전에 이 파일을 먼저 확인합니다.
> 정확한 파일 경로 + 라인 번호 + 현재 코드 스니펫이 포함되어 있어 해당 위치만 읽거나 바로 편집할 수 있습니다.

---

## 목차

1. [빠른 편집 인덱스](#1-빠른-편집-인덱스) — 수정 의도 → 파일:라인 즉시 조회
2. [파일별 함수 인덱스](#2-파일별-함수-인덱스) — 전체 함수 라인 번호
3. [핵심 코드 스니펫](#3-핵심-코드-스니펫) — 자주 수정되는 로직 블록 원문
4. [Pydantic 스키마 필드 목록](#4-pydantic-스키마-필드-목록) — 주요 모델 전체 필드
5. [LLM 프롬프트 전문](#5-llm-프롬프트-전문) — 10개 템플릿 원문 (prompts.py 내장)
6. [하드코딩 vs 설정값 구분표](#6-하드코딩-vs-설정값-구분표)
7. [변경 연쇄 영향 맵](#7-변경-연쇄-영향-맵)

---

## 1. 빠른 편집 인덱스

| 수정 의도 | 파일 | 라인 | 현재 기본값 |
|-----------|------|------|------------|
| **옵션 델타 범위 변경** | `shared/strategy.py` | 22–23 | `DELTA_MIN=0.40, DELTA_MAX=0.70` |
| **IVR 상한 변경** | `shared/strategy.py` | 24 | `IVR_MAX=70.0` |
| **최소 DTE 변경** | `shared/strategy.py` | 27 | `DTE_MIN=21` |
| **최소 OI 변경** | `shared/strategy.py` | 28–29 | `OI_MIN=500, OI_WARNING=999` |
| **종목 최소 RVOL 필터** | `shared/strategy.py` | 35 | `RVOL_MIN=1.5` |
| **최소 주가 필터** | `shared/strategy.py` | 37 | `PRICE_TRADE_MIN=20.0` |
| **최소 시가총액 필터** | `shared/strategy.py` | 38 | `MARKET_CAP_MIN=10_000_000_000` |
| **어닝 발표 차단 일수** | `shared/strategy.py` | 39 | `EARNINGS_PROXIMITY_DAYS=5` |
| **동일 섹터 최대 포지션** | `shared/strategy.py` | 40 | `SECTOR_MAX_COUNT=5` |
| **손절 배수** | `shared/strategy.py` | 227 | `SCENARIO_STOP_LOSS_RATIO=0.5` |
| **1차 익절 배수** | `shared/strategy.py` | 228 | `SCENARIO_TARGET_1ST_RATIO=1.5` |
| **2차 익절 배수** | `shared/strategy.py` | 229 | `SCENARIO_TARGET_2ND_RATIO=2.0` |
| **3차 익절 배수** | `shared/strategy.py` | 230 | `SCENARIO_TARGET_3RD_RATIO=2.5` |
| **트레일링 스탑 %** | `shared/strategy.py` | 384 | `TRAILING_STOP_PCT=20.0` |
| **매도 DTE 긴급 기준** | `shared/strategy.py` | 323 | `SELL_DTE_CRITICAL=7` |
| **매도 DTE 주의 기준** | `shared/strategy.py` | 324 | `SELL_DTE_WARNING=14` |
| **IV Crush IVR 임계값** | `shared/strategy.py` | 344 | `SELL_IVR_CRUSH_THRESHOLD=70.0` |
| **IV Crush 예상 손실 비율** | `shared/strategy.py` | 345 | `SELL_IV_CRUSH_LOSS_RATIO=0.30` |
| **레짐 ADX 기준** | `shared/strategy.py` | 250–251 | `REGIME_ADX_STRONG=25, REGIME_ADX_WEAK=20` |
| **레짐 VIX 기준** | `shared/strategy.py` | 253–254 | `REGIME_VIX_FAVORABLE=20.0, REGIME_VIX_BORDERLINE=30.0` |
| **진입 확신도 임계값** | `shared/strategy.py` | 271–273 | `ENTRY_CONVICTION_MIN=0.70, WATCH_CONVICTION_MIN=0.50` |
| **Kavout 고점수 기준** | `shared/strategy.py` | 281 | `KAVOUT_HIGH_SCORE=7.0` |
| **Kavout 저점수 기준** | `shared/strategy.py` | 282 | `KAVOUT_LOW_SCORE=3.0` |
| **애널리스트 매수 임계값** | `shared/strategy.py` | 295 | `ANALYST_BUY_THRESHOLD=2.0` |
| **애널리스트 매도 임계값** | `shared/strategy.py` | 296 | `ANALYST_SELL_THRESHOLD=4.0` |
| **DA 매수 점수 차단 하한** | `shared/strategy.py` | 305 | `DA_BUY_SCORE_THRESHOLD=40.0` |
| **DA IV Crush 차감** | `shared/strategy.py` | 306 | `DA_BUY_IV_CRUSH_PENALTY=-15.0` |
| **DA Thesis 충돌 차감** | `shared/strategy.py` | 307 | `DA_BUY_THESIS_CONTRA_PENALTY=-20.0` |
| **DA 내부자 매도 차감** | `shared/strategy.py` | 308 | `DA_BUY_INSIDER_SELL_PENALTY=-10.0` |
| **부분 청산 레짐 역전 비율** | `shared/strategy.py` | 360 | `SELL_PARTIAL_REGIME_RATIO=0.75` |
| **부분 청산 수익 비율** | `shared/strategy.py` | 361 | `SELL_PARTIAL_PROFIT_RATIO=0.50` |
| **시나리오 기본 확률** | `shared/strategy.py` | 195–197 | `BULL=0.30, BASE=0.40, BEAR=0.30` |
| **펀더멘털 RSI 이상 구간** | `shared/strategy.py` | 453–454 | `FSCORE_RSI_IDEAL_MIN=50.0, FSCORE_RSI_IDEAL_MAX=70.0` |
| **펀더멘털 RVOL 임계값** | `shared/strategy.py` | 459–461 | `FSCORE_RVOL_HIGH=2.0, FSCORE_RVOL_MED=1.5, FSCORE_RVOL_LOW=1.2` |
| **스크리닝 최종 가중치 (Catalyst있음)** | `shared/strategy.py` | 429–431 | `M=0.35, F=0.40, C=0.25` |
| **스크리닝 최종 가중치 (Catalyst없음)** | `shared/strategy.py` | 434–435 | `M=0.45, F=0.55` |
| **모멘텀 세부 가중치** | `shared/strategy.py` | 439–442 | `RSI=0.25, RVOL=0.25, 52W=0.25, SMA=0.25` |
| **펀더멘털 세부 가중치** | `shared/strategy.py` | 446–448 | `REV=0.40, EPS_SURPR=0.25, MARGIN=0.35` |
| **카탈리스트 세부 가중치** | `shared/strategy.py` | 452–453 | `GUIDANCE=0.60, TONE=0.40` |
| **시가총액 티어 기준 (대형주)** | `shared/strategy.py` | 467 | `MCAP_LARGE_CAP=50_000_000_000` |
| **시가총액 티어 기준 (중형주)** | `shared/strategy.py` | 468 | `MCAP_MID_CAP=5_000_000_000` |
| **매수 뉴스 분석 LLM 변경** | `.env` | — | `LLM_MODEL_BUY_RESEARCH=` (기본: deepseek-v4-pro) |
| **기술 내러티브 LLM 변경** | `.env` | — | `LLM_MODEL_BUY_TECH_NARRATIVE=` (기본: deepseek-v4-flash) |
| **어닝콜 분석 LLM 변경** | `.env` | — | `LLM_MODEL_KAVOUT_EARNINGS=` (기본: deepseek-v4-flash) |
| **매도 건전성 LLM 변경** | `.env` | — | `LLM_MODEL_SELL_HEALTH=` |
| **NL 라우팅 LLM 변경** | `.env` | — | `LLM_MODEL_NL_ROUTING=` |
| **LLM 최대 출력 토큰 변경** | `core/llm.py` | 783 | `_TEMPLATE_MAX_TOKENS` dict (buy_step3_research:8192, buy_step3b:4096) |
| **템플릿→모델 매핑 추가/수정** | `shared/prompts.py` | 658 | `_TEMPLATE_TO_CFG` dict in `get_model_for()` |
| **뉴스 분석 프롬프트 수정** | `shared/prompts.py` | 215–268 | `buy_step3_research` 템플릿 |
| **기술 내러티브 프롬프트 수정** | `shared/prompts.py` | 306–361 | `buy_step3b_technical_narrative` 템플릿 |
| **최종 매도 결정 프롬프트** | `shared/prompts.py` | 409–447 | `sell_step3_decision` 템플릿 |
| **NL 라우팅 프롬프트** | `shared/prompts.py` | 482–508 | `nl_routing` 템플릿 |
| **포지션 건전성 프롬프트** | `shared/prompts.py` | 340–378 | `sell_step1_health` 템플릿 |
| **스키마에 필드 추가** | `shared/schemas.py` | 해당 클래스 참조 | §4 스키마 필드 목록 참조 |
| **RSI 점수 기준 변경** | `core/fundamental_screener.py` | 57–70 | 스니펫 §3.1 참조 |
| **EPS 서프라이즈 점수 기준** | `core/fundamental_screener.py` | 200–216 | 스니펫 §3.2 참조 |
| **성장률 점수 기준 변경** | `core/fundamental_screener.py` | 183–197 | 스니펫 §3.3 참조 |
| **영업이익률 점수 기준** | `core/fundamental_screener.py` | 219–231 | 스니펫 §3.4 참조 |
| **SMA 추세 점수 기준** | `core/fundamental_screener.py` | 127–158 | 스니펫 §3.5 참조 |
| **52W 점수 기준** | `core/fundamental_screener.py` | 85–124 | 스니펫 §3.6 참조 |
| **Catalyst 가이던스 점수 맵** | `core/fundamental_screener.py` | 257–258 | `_GUIDANCE_SCORE, _TONE_SCORE` |
| **매도 Step 7 우선순위 규칙** | `orchestrator/steps/sell_steps.py` | 765–1057 | 스니펫 §3.10 참조 |
| **매도 부분 청산 비율 로직** | `orchestrator/steps/sell_steps.py` | 1058–1137 | `step_8_partial()` |
| **매수 필터 추가/수정** | `core/analysis.py` | 1120+ | `apply_filters()` |
| **레짐 판정 로직 수정** | `core/analysis.py` | 50–233 | `analyze_market_regime()` |
| **기술 점수 로직 수정** | `core/analysis.py` | 234–450 | `calculate_technical_score()` |
| **Greeks 계산 수정** | `core/analysis.py` | 498–572 | `calculate_greeks()` |
| **확신도 가중치 로직** | `core/analysis.py` | 997–1119 | `calculate_confidence()` |
| **Obsidian 노트 경로** | `shared/schemas.py` | 570–583 | `PipelinePaths` 필드값 |
| **파일 경로 변경** | `shared/schemas.py` | 568–586 | `PipelinePaths` 클래스 |
| **Slack 채널 변경** | `.env` | — | `SLACK_CHANNEL_MAIN, SLACK_CHANNEL_ALERT` |
| **전략 철학 텍스트 변경** | `shared/strategy.py` | 391–400 | `STRATEGY_PHILOSOPHY` |
| **어닝콜 분석 철학 변경** | `shared/strategy.py` | 407–426 | `FUNDAMENTAL_SCREEN_PHILOSOPHY` |
| **Yahoo Finance 동시 요청 수** | `core/api_fetcher.py` | 437 | `max_concurrency=5` 기본값 변경 |
| **Yahoo Finance 딜레이 조정** | `core/api_fetcher.py` | 436 | `sleep_sec=0.5` 기본값 변경 |
| **RSI 계산 기간** | `core/api_fetcher.py` | 27 | `_calc_rsi(closes, period=14)` |
| **볼린저 밴드 파라미터** | `core/api_fetcher.py` | 80 | `_calc_bollinger(closes, period=20, std=2.0)` |
| **MACD 파라미터** | `core/api_fetcher.py` | 91 | `_calc_macd(closes, fast=12, slow=26, signal=9)` |
| **ATR 기간** | `core/api_fetcher.py` | 147 | `_calc_atr(hist, period=14)` |
| **kavout_output 병합 필드 목록** | `scripts/run_kavout_screener.py` | 104–111 | `_KAVOUT_FILL_FIELDS` 리스트 |
| **Kavout 노트 헤더 형식** | `scripts/run_kavout_screener.py` | 395–400 | `_format_obsidian_note()` 헤더 블록 |
| **Kavout Slack 티어 라벨** | `scripts/run_kavout_screener.py` | 436 | `tier_labels` dict |

---

## 2. 파일별 함수 인덱스

### 2.1 orchestrator/steps/buy_steps.py (BuySteps 클래스)

| 함수 | 라인 | 역할 요약 |
|------|------|-----------|
| `step_0_env()` | 91 | Obsidian ping + 5종 데이터 로딩 |
| `step_1_data()` | 161 | 시장 데이터 로딩 + 파이프라인 컨텍스트 설정 |
| `step_2_regime()` | 264 | 결정론적 레짐 판정 (ADX+VIX+SPY+QQQ) |
| `step_3_filter()` | 307 | F1~F7 필터 순차 적용 |
| `step_4_technical()` | 385 | 기술 점수 + Kavout/Finviz 보정 |
| `step_5_research()` | 512 | DDG 검색 + LLM 뉴스 감성 분석 |
| `step_6_devils()` | 693 | Devil's Advocate 점수 차감 |
| `step_7_options()` | 861 | 옵션 선택 + 유효성 검증 |
| `step_8_scenario()` | 1019 | 3케이스 시나리오 + EV 계산 |
| `step_9_portfolio()` | 1085 | 포트폴리오 노출 점검 |
| `step_10_ranking()` | 1125 | 확신도 계산 + 최종 순위 결정 |
| `step_11_requeue()` | 1330 | 탈락 종목 Requeue 등록 |
| `step_12_storage()` | 1376 | Obsidian 저장 + 포지션 업데이트 |
| `step_13_notify()` | 1457 | Slack Block Kit 결과 전송 |

### 2.2 orchestrator/steps/sell_steps.py (SellSteps 클래스)

| 함수 | 라인 | 역할 요약 |
|------|------|-----------|
| `step_0_env()` | 106 | Obsidian ping + 포지션+데이터 5종 로딩 |
| `step_1_health()` | 219 | P&L 귀인 + DTE 긴급도 + 트레일링 스탑 갱신 |
| `step_2_regime()` | 398 | 레짐 역전 감지 (진입 시 vs 현재) |
| `step_3_technical()` | 463 | 기술 점수 + DDG 뉴스 + LLM 감성 |
| `step_4_thesis()` | 545 | LLM 무효화 조건 점검 |
| `step_5_devils()` | 623 | 매도용 DA (이벤트/레짐/뉴스 리스크) |
| `step_6_options()` | 702 | IV Crush 판정 (IVR > 70 AND 어닝 DTE 내) |
| `step_7_action()` | 765 | 7-우선순위 HOLD/PARTIAL/FULL/ROLL 결정 |
| `step_8_partial()` | 1058 | 부분 청산 비율 계산 + 체인 프리미엄 조회 |
| `step_9_portfolio()` | 1138 | 청산 후 포트폴리오 재검 |
| `step_10_decision()` | 1162 | LLM 최종 확정 + ROLL 조건 판정 |
| `step_11_storage()` | 1268 | 포지션 상태 저장 + Obsidian 매도 노트 |
| `step_12_review()` | 1307 | FULL_EXIT 종목 LLM 복기 |
| `step_13_notify()` | 1370 | Slack 매도 결과 전송 |

### 2.3 core/analysis.py

| 함수 | 라인 | 역할 요약 |
|------|------|-----------|
| `analyze_market_regime()` | 50 | SPY/QQQ/VIX/ADX → MarketRegime |
| `calculate_technical_score()` | 234 | MA+ADX+RSI+MACD → TechnicalScore + signal_count |
| `_apply_devils_advocate()` | 451 | RSI/거래량/BB/52W → 기술 점수 차감 |
| `calculate_greeks()` | 498 | Black-Scholes delta/gamma/theta/vega |
| `validate_option()` | 573 | delta/ivr/oi/spread/dte 검증 → OptionValidity |
| `calculate_scenario()` | 677 | 3-케이스 시나리오 + EV + 손절/익절 프리미엄 |
| `check_portfolio_exposure()` | 891 | 총 델타/세타/베가 + 섹터/방향 편향 |
| `calculate_confidence()` | 997 | trend+news+thesis+execution → ConfidenceScore |
| `apply_filters()` | 1120 | F1~F7 필터 순차 적용 → (passed, failures) |

### 2.4 core/fundamental_screener.py

점수 구조: `Momentum(25%×4) + Fundamental(40%+25%+35%) + Catalyst(60%+40%)`

| 함수 | 라인 | 역할 요약 |
|------|------|-----------|
| `_rsi_score()` | 57 | RSI → 0~100 점수 |
| `_rvol_score()` | 72 | Relative Volume → 0~100 점수 |
| `_w52_score()` | 85 | 52주 고점/저점 독립 점수화 후 평균 |
| `_sma_score()` | 127 | SMA20/50/200 위치 가중 평균 (200=40%, 50=35%, 20=25%) |
| `calc_momentum_score()` | 161 | RSI(25%)+RVOL(25%)+52W(25%)+SMA(25%) |
| `_growth_score()` | 183 | 매출 YoY 성장률 % → 0~100 점수 |
| `_eps_surprise_score()` | 200 | EPS 서프라이즈 % → 0~100 점수 (None=50 중립) |
| `_margin_score()` | 219 | 영업이익률 % → 0~100 점수 |
| `calc_fundamental_score()` | 234 | 매출성장(40%)+EPS서프라이즈(25%)+마진(35%) |
| `calc_catalyst_score()` | 261 | 가이던스(60%)+경영진톤(40%) |
| `score_ticker()` | 281 | 단일 종목 FundamentalScoreResult 생성 |
| `rank_universe()` | 332 | 전체 종목 점수화 + 내림차순 정렬 + rank 부여 |

### 2.5 core/llm.py

| 함수 | 라인 | 역할 요약 |
|------|------|-----------|
| `call_llm()` | 60 | OpenRouter 호출 + 4모델 폴백 + tenacity 재시도 |
| `_call_openrouter()` | 138 | 실제 HTTP POST to OpenRouter |
| `get_cache()` | 213 | 캐시 조회 (TTL 만료 자동 체크) |
| `set_cache()` | 243 | 캐시 저장 (expires_today or ttl_hours) |
| `get_cached_or_fetch()` | 279 | 캐시 히트 → 반환, 미스 → fetch_fn 실행 후 저장 |
| `clear_cache()` | 308 | ticker=None이면 전체, 지정 시 해당 ticker 캐시만 삭제 |
| `parse_llm_json()` | 358 | 마크다운 코드 블록 제거 + JSON 파싱 |
| `call_ddg_search()` | 391 | DuckDuckGo 무료 검색 (num_results 지정) |
| `call_brave_search()` | 527 | Brave Search API (BRAVE_API_KEY 필요) |
| `analyze_with_llm()` | 762 | prompts.render() → call_llm() → parse_llm_json() 통합 |

### 2.6 core/parsers.py

| 함수 | 라인 | 역할 요약 |
|------|------|-----------|
| `parse_finviz()` | 120 | finviz_all_rows.txt → list[FinvizRow] |
| `parse_summary()` | 601 | summary_*.json → SummaryData |
| `load_latest_summary()` | 851 | summary_dir에서 최신 파일 자동 탐색 |
| `parse_earnings()` | 948 | 어닝_분석.md → list[EarningsAnalysis] (raw 원문) |
| `parse_positions()` | 991 | positions.md → list[Position] |
| `parse_kavout()` | 1170 | kavout_*.csv → dict{ticker: {k_score, momentum_1m, roe}} |
| `parse_finviz_detail()` | 1363 | finviz_output/*.txt 전체 → dict{ticker: FinvizDetail} |
| `find_latest_kavout_csv()` | 1395 | data_dir에서 최신 kavout_*.csv 자동 탐색 |
| `parse_kavout_universe()` | 1419 | data_dir → list[KavoutRow] (kavout_mcp 전용) |
| `_safe_float()` | 487 | 안전 float 변환 헬퍼 (None 허용) |
| `_parse_kavout_output_file()` | 1506 | 단일 kavout_output .txt → FinvizDetail (market_cap, EPS서프라이즈, ROE, FCF 포함) |
| `parse_kavout_output()` | 1641 | kavout_output_dir → dict[str, FinvizDetail] (전 티커) |

### 2.7 core/state.py

| 함수 | 라인 | 역할 요약 |
|------|------|-----------|
| `save_snapshot()` | 46 | shared/state/snapshots/{eid}/step_{N}.json 저장 |
| `load_snapshot()` | 81 | 완료된 step 번호 set 반환 (멱등성 기반) |
| `cleanup_old_snapshots()` | 129 | retention_days 초과 스냅샷 디렉토리 삭제 |
| `append_audit()` | 169 | shared/logs/audit_{YYYY-MM-DD}.json에 JSONL 추가 |
| `requeue_add()` | 226 | requeue.json에 항목 추가 (기존 waiting 항목 업데이트) |
| `requeue_list()` | 274 | status 필터로 RequeueItem 목록 반환 |
| `requeue_check_ready()` | 307 | ivr_max/price_min/rvol_min 조건 체크 → 준비된 ticker 목록 |
| `apply_partial_exit()` | 428 | PartialExit 기록 생성 + trailing_stop 리셋 + remaining_contracts 차감 |
| `save_positions_state()` | 407 | shared/state/positions_state.json 저장 |

### 2.8 shared/prompts.py

| 함수/변수 | 라인 | 역할 요약 |
|-----------|------|-----------|
| `REGISTRY` | 25 | 10개 템플릿 메타데이터 (version/model/temperature) |
| `ROLE_LOCK_SNIPPET` | 111 | 모든 프롬프트에 자동 삽입되는 역할 고정 텍스트 |
| `_TEMPLATES` | 126 | 11개 Jinja2 템플릿 문자열 원문 (buy_step3b_technical_narrative 포함) |
| `render()` | 609 | 템플릿 렌더링 + role_lock 자동 주입 |
| `get_model_for()` | 648 | template_name → cfg.LLM_MODEL_* 변수명 매핑 (`_TEMPLATE_TO_CFG` at :658) |

### 2.9 shared/schemas.py (클래스별 라인)

| 클래스 | 라인 | 용도 |
|--------|------|------|
| `SummaryMacro` | 42 | VIX/SPY/QQQ/ADX 등 거시 지표 |
| `TickerTechnical` | 72 | RSI/MA/MACD/RVOL 등 종목 기술 지표 |
| `TickerOptions` | 108 | P/C ratio, OI, 옵션 체인 |
| `SummaryData` | 156 | summary_*.json 전체 (macro + tickers + options) |
| `FinvizDetail` | 171 | finviz_output/<TICKER>.txt 파싱 결과 + API 수집 결과 (`market_cap` 포함) |
| `FinvizRow` | 206 | finviz_all_rows.txt 개별 행 |
| `Position` | 261 | 보유 포지션 (strike/expiry/trailing_stop/peak_premium) |
| `MarketRegime` | 303 | 레짐 판정 결과 (favorable/borderline/unfavorable) |
| `TechnicalScore` | 320 | 기술 점수 (signal_count 0~8 포함) |
| `Greeks` | 343 | delta/gamma/theta/vega/iv/ivr |
| `OptionValidity` | 375 | 옵션 유효성 검증 결과 |
| `Scenario` | 411 | 3케이스 시나리오 + EV + 손절/익절 프리미엄 |
| `FinalRanking` | 479 | 최종 순위 항목 (action/strike/expiry/rationale) |
| `RequeueItem` | 508 | 재분석 대기 항목 |
| `SellDecision` | 540 | HOLD/PARTIAL_EXIT/FULL_EXIT/ROLL 결정 |
| `PipelinePaths` | 568 | 모든 파일 경로 (수정 시 여기만) |
| `PipelineContext` | 588 | 파이프라인 전체 공유 컨텍스트 |
| `EarningsCallAnalysis` | 708 | 어닝콜 LLM 분류 결과 |
| `FundamentalScoreResult` | 717 | 펀더멘털 스크리닝 점수 (Kavout 필드 포함) |
| `KavoutRow` | 789 | kavout_*.csv 한 행 |

### 2.10 core/api_fetcher.py

| 함수 | 라인 | 설명 |
|------|------|------|
| `_calc_rsi(closes, period=14)` | 27 | RSI(14) 계산, 데이터 부족 시 None |
| `_calc_sma_pct(closes, price, period)` | 42 | SMA 대비 현재가 % 위치 |
| `_calc_rvol(volumes, period=20)` | 52 | 상대 거래량 (오늘 / 평균) |
| `_calc_sma_val(closes, period)` | 62 | SMA 달러값 반환 |
| `_ema(series, span)` | 69 | EMA 시리즈 계산 (MACD 보조) |
| `_calc_bollinger(closes, period=20, std=2.0)` | 80 | BB upper/mid/lower 튜플 |
| `_calc_macd(closes, fast=12, slow=26, signal=9)` | 91 | MACD line/signal/hist 튜플 |
| `_calc_adx(hist, period=14)` | 112 | ADX, DI+, DI- 튜플 (고/저/종 필요) |
| `_calc_atr(hist, period=14)` | 147 | ATR(14) (고/저/종 기반) |
| `_calc_pivot(hist)` | 162 | 피벗 포인트 + R1/R2/S1/S2 |
| `_f(val)` | 176 | 안전 float 변환 헬퍼 (None 허용) |
| `_pct(val)` | 183 | 소수→% 변환 (`0.654 → 65.4`) |
| `_million(val)` | 189 | 원시 달러→백만달러 (`1_234_567 → 1.23`) |
| `fetch_finviz_detail(ticker)` | 197 | 단일 티커 동기 수집 → `FinvizDetail` (market_cap 포함) |
| `fetch_finviz_details_bulk(tickers, sleep_sec, max_concurrency)` | 416 | 비동기 일괄 수집, `Semaphore(5)` |

**`fetch_finviz_details_bulk` 시그니처**:
```python
async def fetch_finviz_details_bulk(
    tickers: list[str],
    sleep_sec: float = 0.5,
    max_concurrency: int = 5,
) -> dict[str, FinvizDetail]:
```

**`fetch_finviz_detail` 주요 데이터 소스**:
- `yf.Ticker(ticker).history(period="1y")` — OHLCV 1년치 (기술 지표 계산)
- `yf.Ticker(ticker).info` — 펀더멘털 (forward_pe, peg, beta, margins, **marketCap** 등)
- `ticker.earnings_history` — EPS 서프라이즈 (Non-GAAP 컨센서스 대비)
- `ticker.recommendations_summary` / `ticker.recommendations` — 애널리스트 컨센서스

### 2.11 scripts/run_kavout_screener.py (주요 함수/블록)

| 함수/블록 | 라인 | 역할 요약 |
|-----------|------|-----------|
| `run()` | 37 | 전체 파이프라인 진입점 (4단계) |
| Step 1 블록 | 68–97 | Kavout CSV 파싱 + Yahoo Finance API 수집 |
| kavout_output 병합 블록 | 99–125 | `_KAVOUT_FILL_FIELDS` API 누락 필드 보완 |
| Step 2 블록 | 127–147 | K어닝 분석.md LLM 분석 + raw EarningsAnalysis 보존 |
| Step 3 블록 | 149–207 | 점수화 + 시가총액 맵 구성 + 티어별 그룹화 + rank 재부여 |
| `_print_report()` | 251 | 터미널 티어별 보고서 출력 |
| `_format_obsidian_note()` | 286 | Obsidian 노트 생성 (투자근거+기술+밸류+펀더멘털 테이블) |
| `_format_slack_summary()` | 421 | 티어별 Top 3 Slack 메시지 생성 |

**`_format_obsidian_note()` 시그니처**:
```python
def _format_obsidian_note(
    result: ScreenerResult,
    tiers: dict,          # {"대형주 ($50B+)": [...], "중형주...": [...], "소형주...": [...]}
    mcap_map: dict,       # {ticker: float(USD)}
    earnings_raw: dict,   # {ticker: EarningsAnalysis} — K어닝 분석.md 원문 객체
    finviz_details: dict, # {ticker: FinvizDetail}
) -> str:
```

**시가총액 소스 우선순위** (`run_kavout_screener.py:173–185`):
```python
# 1순위: Yahoo Finance API (info["marketCap"])
# 2순위: kavout_output SNAPSHOT TABLE "Market Cap" 파싱
# 3순위: kavout_*.csv KavoutRow.market_cap_raw
```

---

## 3. 핵심 코드 스니펫

### 3.1 RSI 점수 기준 (`core/fundamental_screener.py:57–70`)

```python
def _rsi_score(rsi: float | None) -> float:
    if rsi is None:
        return 50.0                                        # 데이터 없음 → 중립
    if FSCORE_RSI_IDEAL_MIN <= rsi <= FSCORE_RSI_IDEAL_MAX:  # 50~70
        return 100.0
    if FSCORE_RSI_OK_MIN <= rsi < FSCORE_RSI_IDEAL_MIN:      # 40~50
        return 65.0
    if FSCORE_RSI_IDEAL_MAX < rsi <= FSCORE_RSI_OK_MAX:      # 70~80
        return 55.0
    if rsi < FSCORE_RSI_OK_MIN:                              # < 40
        return 30.0
    return 20.0                                              # > 80 극단적 과매수
```
**임계값 수정**: `shared/strategy.py:453–456` (`FSCORE_RSI_IDEAL_MIN/MAX/OK_MIN/MAX`)

---

### 3.2 EPS 서프라이즈 점수 기준 (`core/fundamental_screener.py:200–216`)

```python
def _eps_surprise_score(eps_surprise_pct: float | None) -> float:
    """
    None=50 중립 — 데이터 없어도 페널티 없음.
    GAAP 순이익 대신 Non-GAAP 컨센서스 대비 서프라이즈 사용 이유:
    M&A·스톡옵션 일회성 비용이 GAAP 순이익을 크게 왜곡하기 때문.
    (예: MRVL — GAAP 순이익성장 -81%, Non-GAAP EPS서프라이즈 +33%)
    """
    if eps_surprise_pct is None:
        return 50.0   # 중립
    if eps_surprise_pct >= 15:  return 100.0
    if eps_surprise_pct >= 5:   return 80.0
    if eps_surprise_pct >= 0:   return 60.0
    if eps_surprise_pct >= -5:  return 35.0
    return 15.0       # 큰 어닝 미스
```
**점수 구간 수정**: 이 함수 직접 편집 (하드코딩)

---

### 3.3 매출 성장률 점수 기준 (`core/fundamental_screener.py:183–197`)

```python
def _growth_score(growth_pct: float | None) -> float:
    if growth_pct is None:
        return 40.0          # 데이터 없음 → 중립 이하
    if growth_pct >= 50:  return 100.0
    if growth_pct >= 25:  return 80.0
    if growth_pct >= 10:  return 60.0
    if growth_pct >= 0:   return 40.0
    if growth_pct >= -10: return 20.0
    return 5.0               # 심각한 역성장
```

---

### 3.4 영업이익률 점수 기준 (`core/fundamental_screener.py:219–231`)

```python
def _margin_score(op_margin: float | None) -> float:
    if op_margin is None:
        return 40.0
    if op_margin >= 25: return 100.0
    if op_margin >= 15: return 80.0
    if op_margin >= 8:  return 60.0
    if op_margin >= 0:  return 35.0
    return 10.0              # 적자
```

---

### 3.5 SMA 추세 점수 (`core/fundamental_screener.py:127–158`)

```python
def _sma_score(sma20_pct, sma50_pct, sma200_pct) -> float:
    """가격이 SMA 위에 얼마나 있는지 가중 평균. 장기 추세일수록 가중치 높음."""
    def _single(pct: float) -> float:
        if pct >= 10:  return 100.0
        if pct >= 3:   return 80.0
        if pct >= 0:   return 60.0
        if pct >= -10: return 35.0
        return 10.0

    # SMA200(중장기) > SMA50(중기) > SMA20(단기)
    weighted = [(sma20_pct, 0.25), (sma50_pct, 0.35), (sma200_pct, 0.40)]
    available = [(p, w) for p, w in weighted if p is not None]
    if not available:
        return 50.0
    total_w = sum(w for _, w in available)
    return round(sum(_single(p) * (w / total_w) for p, w in available), 2)
```
**가중치 수정**: `weighted` 리스트 내 튜플 (하드코딩, strategy.py 미분리)

---

### 3.6 52주 위치 점수 (`core/fundamental_screener.py:85–124`)

```python
def _w52_score(w52_high_pct, w52_low_pct) -> float:
    """고점 근접도 + 저점 이탈도 독립 점수화 → 평균"""
    scores = []
    # 고점 근접도 (고점에 가까울수록 강한 모멘텀)
    if w52_high_pct is not None:
        dist = abs(w52_high_pct)
        if dist <= 5:   scores.append(100.0)
        elif dist <= 15: scores.append(75.0)
        elif dist <= 30: scores.append(50.0)
        elif dist <= 50: scores.append(30.0)
        else:           scores.append(10.0)
    # 저점 이탈도 (저점에서 많이 올라올수록 추세 강함)
    if w52_low_pct is not None:
        if w52_low_pct >= 100: scores.append(100.0)
        elif w52_low_pct >= 50: scores.append(80.0)
        elif w52_low_pct >= 25: scores.append(60.0)
        elif w52_low_pct >= 10: scores.append(40.0)
        else:                   scores.append(20.0)
    return 50.0 if not scores else round(sum(scores) / len(scores), 2)
```

---

### 3.7 모멘텀 점수 계산 (`core/fundamental_screener.py:161–176`)

```python
def calc_momentum_score(detail: FinvizDetail) -> float:
    """RSI(25%) + RVOL(25%) + 52W위치(25%) + SMA추세(25%)"""
    rsi  = _rsi_score(detail.rsi14)
    rvol = _rvol_score(detail.rel_volume)
    w52  = _w52_score(detail.w52_high_pct, detail.w52_low_pct)
    sma  = _sma_score(detail.sma20_pct, detail.sma50_pct, detail.sma200_pct)
    return round(
        rsi  * FSCORE_MOM_RSI_WEIGHT    # 0.25
        + rvol * FSCORE_MOM_RVOL_WEIGHT # 0.25
        + w52  * FSCORE_MOM_52W_WEIGHT  # 0.25
        + sma  * FSCORE_MOM_SMA_WEIGHT, # 0.25
        2
    )
```

---

### 3.8 펀더멘털 점수 계산 (`core/fundamental_screener.py:234–250`)

```python
def calc_fundamental_score(detail: FinvizDetail) -> float:
    """매출성장YoY(40%) + EPS서프라이즈(25%) + 영업이익률(35%)
    GAAP 순이익 성장률 제외: M&A 일회성 비용 왜곡 방지."""
    rev_s    = _growth_score(detail.revenue_growth_yoy)
    surpr_s  = _eps_surprise_score(detail.eps_surprise_pct)
    margin_s = _margin_score(detail.op_margin_pct)
    return round(
        rev_s    * FSCORE_FUND_REV_WEIGHT       # 0.40
        + surpr_s  * FSCORE_FUND_EPS_SURPR_WEIGHT # 0.25
        + margin_s * FSCORE_FUND_MARGIN_WEIGHT,   # 0.35
        2
    )
```

---

### 3.9 카탈리스트 점수 계산 (`core/fundamental_screener.py:261–274`)

```python
_GUIDANCE_SCORE = {"up": 100.0, "flat": 50.0, "down": 10.0, "unknown": 40.0}
_TONE_SCORE     = {"bullish": 100.0, "neutral": 55.0, "bearish": 15.0}

def calc_catalyst_score(analysis: EarningsCallAnalysis) -> float:
    """가이던스(60%) + 경영진 톤(40%). catalyst_strength 제거 (가이던스+톤 중복 집계 방지)."""
    g_score = _GUIDANCE_SCORE.get(analysis.guidance_direction, 40.0)
    t_score = _TONE_SCORE.get(analysis.mgmt_tone, 55.0)
    return round(
        g_score * FSCORE_CAT_GUIDANCE_WEIGHT  # 0.60
        + t_score * FSCORE_CAT_TONE_WEIGHT,   # 0.40
        2
    )
```

---

### 3.10 매도 Step 7 우선순위 플래그 (`orchestrator/steps/sell_steps.py:802–884`)

```python
# ① 트레일링 스탑 (line 802–817)
if current_prem < pos.trailing_stop:
    flags.append("트레일링스탑_발동")

# ② 손절/익절 임계값 (line 822–867)
stop_loss_threshold = pos.entry_premium * st.SELL_STOP_LOSS_RATIO   # × 0.5
target_1st          = pos.entry_premium * st.SELL_TARGET_1ST_RATIO  # × 1.5
target_2nd          = pos.entry_premium * st.SELL_TARGET_2ND_RATIO  # × 2.0
target_3rd          = pos.entry_premium * st.SELL_TARGET_3RD_RATIO  # × 2.5

if current_prem <= stop_loss_threshold: flags.append("스탑로스_도달")
elif current_prem >= target_3rd:        flags.append("3차익절_달성")
elif current_prem >= target_2nd:        flags.append("2차익절_달성")
elif current_prem >= target_1st:        flags.append("1차익절_달성")

# ③ Finviz 플래그 (line 869–884)
if fvd.recom >= st.SELL_ANALYST_SELL_THRESHOLD: flags.append("애널리스트_매도의견")
if fvd.eps_surprise_pct < st.SELL_EPS_MISS_PCT: flags.append("EPS미스_주의")
if fvd.insider_trans_pct < st.SELL_INSIDER_SELL_PCT: flags.append("내부자매도_주의")
if stock_price >= fvd.target_price * st.SELL_TARGET_PRICE_PROXIMITY:
    flags.append("목표주가_근접")
```

---

### 3.11 레짐 역전 판정 (`orchestrator/steps/sell_steps.py:421–446`)

```python
_LONG_HOSTILE  = {"bearish", "unfavorable"}   # 롱콜에 불리
_SHORT_HOSTILE = {"bullish"}                   # 롱풋에 불리

is_long_call = pos.option_type == "롱콜"
reversed_ = (
    (is_long_call     and current_regime in _LONG_HOSTILE)
    or (not is_long_call and current_regime in _SHORT_HOSTILE)
)
```

---

### 3.12 확신도 가중치 계산 (`core/analysis.py:997+`)

```python
# shared/strategy.py:153–156 에서 가중치 관리
CONVICTION_WEIGHT_TREND      = 0.4   # 기술 신호 비중
CONVICTION_WEIGHT_NEWS       = 0.2   # 뉴스 감성 비중
CONVICTION_WEIGHT_THESIS     = 0.3   # R/R 비율 비중
CONVICTION_WEIGHT_EXECUTION  = 0.1   # IVR 실행 가능성

total_conviction = (
    trend_conf * st.CONVICTION_WEIGHT_TREND
    + news_conf * st.CONVICTION_WEIGHT_NEWS
    + thesis_conf * st.CONVICTION_WEIGHT_THESIS
    + execution_conf * st.CONVICTION_WEIGHT_EXECUTION
)
```

---

### 3.13 LLM 모델 라우팅 딕셔너리

#### `_TEMPLATE_MAX_TOKENS` (`core/llm.py:783`)

```python
_TEMPLATE_MAX_TOKENS: dict[str, int] = {
    "buy_step3_research": 8192,           # 7섹션 JSON → 잘림 방지
    "buy_step3b_technical_narrative": 4096,
}
max_tokens = _TEMPLATE_MAX_TOKENS.get(template_name, 4096)
```

#### `_TEMPLATE_TO_CFG` (`shared/prompts.py:658`)

```python
_TEMPLATE_TO_CFG: dict[str, str] = {
    "buy_step3_research":             cfg.LLM_MODEL_BUY_RESEARCH,
    "buy_step3b_technical_narrative": cfg.LLM_MODEL_BUY_TECH_NARRATIVE,
    "sell_step1_health":              cfg.LLM_MODEL_SELL_HEALTH,
    "sell_step2_environment":         cfg.LLM_MODEL_SELL_ENV,
    "sell_step3_decision":            cfg.LLM_MODEL_SELL_HEALTH,
    "sell_step4_review":              cfg.LLM_MODEL_SELL_HEALTH,
    "nl_routing":                     cfg.LLM_MODEL_NL_ROUTING,
}
```
**새 템플릿에 모델 지정 시**: (1) `.env`에 `LLM_MODEL_*` 추가, (2) `shared/config.py`에 필드 추가, (3) 이 dict에 행 추가

---

### 3.14 kavout_output 파싱 핵심 — 시가총액 추출 (`core/parsers.py:1602–1612`)

```python
# Market Cap: "1086.82B" / "23.45T" / "450.00M" 형식 파싱
mcap_raw = kv.get("Market Cap", "").strip()
if mcap_raw:
    mcap_m = re.match(r"([\d.]+)([BMT]?)", mcap_raw.upper())
    if mcap_m:
        num = _safe_float(mcap_m.group(1))
        unit = mcap_m.group(2)
        if num is not None:
            multiplier = {"T": 1e12, "B": 1e9, "M": 1e6}.get(unit, 1.0)
            mcap_val = num * multiplier
```

---

### 3.15 시가총액 티어 분류 및 rank 재부여 (`scripts/run_kavout_screener.py:187–207`)

```python
from shared.strategy import MCAP_LARGE_CAP, MCAP_MID_CAP  # 50B, 5B

large, mid, small = [], [], []
for r in ranked:                         # ranked는 이미 점수 내림차순
    mc = mcap_map.get(r.ticker)
    if mc is None or mc < MCAP_MID_CAP:  # 시총 미확인도 소형주로
        small.append(r)
    elif mc < MCAP_LARGE_CAP:
        mid.append(r)
    else:
        large.append(r)

# 티어 내 rank 재부여 (1위부터)
for i, r in enumerate(large, 1): r.rank = i
for i, r in enumerate(mid,   1): r.rank = i
for i, r in enumerate(small, 1): r.rank = i

tiers = {
    "대형주 ($50B+)":               large,
    "중형주 ($5B~$50B)":             mid,
    "소형주 ($5B 미만 / 시총 미확인)": small,
}
```

---

### 3.16 새 필터 추가 패턴 (`core/analysis.py:1120+`)

```python
# apply_filters() 내부 F8 추가 예시
for ticker in passed_list[:]:
    ticker_data = summary_data.tickers.get(ticker)
    if ticker_data and ticker_data.technical.rsi14 > 85:
        failures[ticker] = failures.get(ticker, []) + ["F8_RSI_OVERBOUGHT"]
        passed_list.remove(ticker)
```

---

## 4. Pydantic 스키마 필드 목록

### 4.1 Position (`shared/schemas.py:261–290`)

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `ticker` | `str` | 필수 | 티커 (A-Z 1~5자) |
| `option_type` | `Literal["롱콜", "롱풋"]` | 필수 | 옵션 유형 |
| `strike` | `float` | 필수 | 행사가 |
| `expiry` | `date` | 필수 | 만기일 |
| `entry_date` | `date` | 필수 | 진입일 |
| `entry_premium` | `float` | 필수 | 진입 프리미엄 |
| `entry_stock_price` | `float` | 필수 | 진입 시 주가 |
| `original_contracts` | `int` | 필수 | 최초 계약 수 |
| `remaining_contracts` | `int` | 필수 | 잔여 계약 수 |
| `partial_exits` | `list[PartialExit]` | `[]` | 부분 청산 기록 |
| `trailing_stop` | `float` | `0.0` | 트레일링 스탑 프리미엄 |
| `peak_premium` | `float` | `0.0` | 프리미엄 고점 |
| `entry_regime` | `str` | `""` | 진입 시 레짐 상태 |
| `entry_vix` | `float` | `0.0` | 진입 시 VIX |
| `thesis` | `str` | `""` | 투자 논거 |
| `invalidation_conditions` | `list[str]` | `[]` | 무효화 조건 목록 |
| `conviction_score` | `float` | `0.5` | 확신도 (0.0~1.0) |
| `dte` (property) | `int` | — | `(expiry - today).days` |
| `total_cost` (property) | `float` | — | `entry_premium × 100 × original_contracts` |

---

### 4.2 FinalRanking (`shared/schemas.py:479–493`)

| 필드 | 타입 | 설명 |
|------|------|------|
| `rank` | `int` | 순위 번호 |
| `ticker` | `str` | 티커 |
| `direction` | `Literal["long_call", "long_put"]` | 방향 |
| `action` | `Literal["진입", "관찰", "보류", "탈락"]` | 권고 행동 |
| `final_score` | `float` | 최종 점수 (DA 차감 후) |
| `conviction` | `ConfidenceScore` | 확신도 상세 |
| `capital_allocation` | `float` | 권고 투자금 ($) |
| `contracts` | `int` | 권고 계약 수 |
| `strike` | `float` | 권고 행사가 |
| `expiry` | `date` | 권고 만기일 |
| `rationale` | `str` | 투자 근거 |
| `risk_factors` | `list[str]` | 리스크 요인 목록 |
| `scenario` | `Scenario \| None` | 시나리오 분석 결과 |

---

### 4.3 SellDecision (`shared/schemas.py:540–552`)

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `ticker` | `str` | 필수 | 티커 |
| `action` | `Literal["HOLD","PARTIAL_EXIT","FULL_EXIT","ROLL"]` | 필수 | 행동 |
| `contracts_to_close` | `int` | `0` | 청산할 계약 수 |
| `target_premium` | `float \| None` | `None` | 목표 프리미엄 |
| `roll_strike` | `float \| None` | `None` | Roll 행사가 |
| `roll_expiry` | `date \| None` | `None` | Roll 만기일 |
| `realized_pnl` | `float` | `0.0` | 실현 손익 |
| `unrealized_pnl` | `float` | `0.0` | 미실현 손익 |
| `rationale` | `str` | `""` | 결정 근거 |
| `urgency` | `Literal["critical","warning","normal","stable"]` | `"normal"` | 긴급도 |

---

### 4.4 FundamentalScoreResult (`shared/schemas.py:717–750`)

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `ticker` | `str` | 필수 | 티커 |
| `company` | `str` | `""` | 회사명 |
| `sector` | `str` | `""` | 섹터 |
| `momentum_score` | `float` | `0.0` | 모멘텀 점수 (0~100) |
| `fundamental_score` | `float` | `0.0` | 펀더멘털 점수 (0~100) |
| `catalyst_score` | `float` | `0.0` | 카탈리스트 점수 (0~100, 없으면 0) |
| `has_catalyst` | `bool` | `False` | 어닝콜 데이터 보유 여부 |
| `total_score` | `float` | `0.0` | 최종 가중 합산 점수 |
| `rank` | `int` | `0` | 순위 (rank_universe 후 부여, 티어 내 재부여됨) |
| `guidance_direction` | `str` | `""` | `"up"\|"flat"\|"down"\|"unknown"` |
| `mgmt_tone` | `str` | `""` | `"bullish"\|"neutral"\|"bearish"` |
| `k_score` | `float \| None` | `None` | Kavout AI 점수 (kavout_mcp 전용) |
| `momentum_1m` | `float \| None` | `None` | 1개월 모멘텀 % (kavout_mcp 전용) |
| `roe` | `float \| None` | `None` | ROE % (kavout_mcp 전용) |

---

### 4.5 PipelinePaths (`shared/schemas.py:568–585`)

| 필드 | 현재 경로 |
|------|-----------|
| `summary_dir` | `R:\내 드라이브\마켓 수치` |
| `finviz_file` | `Y:\내 드라이브\어닝\finviz_all_rows.txt` |
| `earnings_dir` | `Y:\내 드라이브\어닝` |
| `earnings_analysis` | `Y:\내 드라이브\어닝\어닝 분석.md` |
| `k_earnings_analysis` | `Y:\내 드라이브\어닝\K어닝 분석.md` |
| `finviz_output_dir` | `Y:\내 드라이브\어닝\finviz_output` |
| `kavout_output_dir` | `Y:\내 드라이브\어닝\kavout_output` |
| `earnings_call_dir` | `Y:\내 드라이브\어닝\어닝콜_output` |
| `positions_file` | `C:\lian\watchlist.md` |
| `data_dir` | `Y:\내 드라이브\Data` |
| `cache_dir` | `shared/cache` |
| `snapshots_dir` | `shared/state/snapshots` |
| `requeue_file` | `shared/state/requeue.json` |

> **경로 변경 방법**: `shared/schemas.py:568–585` 해당 필드 기본값 직접 수정

---

### 4.6 RequeueItem (`shared/schemas.py:508–517`)

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `ticker` | `str` | 필수 | 티커 |
| `registered_at` | `datetime` | 필수 | 등록 시각 |
| `failed_filters` | `list[str]` | 필수 | 탈락 필터 목록 (예: `["F1_RVOL_LOW"]`) |
| `threshold` | `RequeueThreshold` | 필수 | 진입 조건 (ivr_max/price_drop_pct/rvol_min) |
| `status` | `Literal["waiting","ready","processed"]` | `"waiting"` | 상태 |

---

### 4.7 FinvizDetail 전체 필드 (`shared/schemas.py:171–240`)

> `core/api_fetcher.py`(Yahoo Finance API), `core/parsers.py`(finviz_output 파일 + kavout_output 파일)가 채우는 필드 목록.

**기본 가격**

| 필드 | 타입 | 소스 |
|------|------|------|
| `ticker` | `str` | 필수 |
| `price` | `float \| None` | API `currentPrice` / CSV fallback |
| `change_pct` | `float \| None` | API 계산 |

**기술지표** (API 전용 — parsers.py 미기입)

| 필드 | 타입 | 설명 |
|------|------|------|
| `rsi14` | `float \| None` | RSI(14) |
| `rel_volume` | `float \| None` | 상대 거래량 |
| `sma20_pct` | `float \| None` | 현재가 vs SMA20 % |
| `sma50_pct` | `float \| None` | 현재가 vs SMA50 % |
| `sma200_pct` | `float \| None` | 현재가 vs SMA200 % |
| `sma5_val` ~ `sma200_val` | `float \| None` | SMA 달러값 (4종) |
| `bb_upper/mid/lower` | `float \| None` | 볼린저 밴드 |
| `macd_line/signal/hist` | `float \| None` | MACD |
| `adx/di_plus/di_minus` | `float \| None` | ADX + 방향성 지수 |
| `atr` | `float \| None` | ATR(14) |
| `pivot/r1/r2/s1/s2` | `float \| None` | 피벗 포인트 |

**52주 위치**

| 필드 | 타입 | 소스 |
|------|------|------|
| `w52_high_pct` | `float \| None` | API `fiftyTwoWeekHigh` 계산 |
| `w52_low_pct` | `float \| None` | API `fiftyTwoWeekLow` 계산 |

**밸류에이션**

| 필드 | 타입 | 소스 |
|------|------|------|
| `forward_pe` | `float \| None` | API / kavout_output |
| `trailing_pe` | `float \| None` | API |
| `peg` | `float \| None` | API / kavout_output |
| `beta` | `float \| None` | API / kavout_output |
| `target_price` | `float \| None` | API / kavout_output |
| `recom` | `float \| None` | API / kavout_output (1=강매수~5=매도) |

**마진·수익성**

| 필드 | 타입 | 소스 |
|------|------|------|
| `gross_margin_pct` | `float \| None` | API / kavout_output |
| `op_margin_pct` | `float \| None` | API / kavout_output |
| `profit_margin_pct` | `float \| None` | API / kavout_output |
| `roe_pct` | `float \| None` | API / kavout_output (Balance Sheet) |

**성장·손익**

| 필드 | 타입 | 소스 |
|------|------|------|
| `revenue_growth_yoy` | `float \| None` | API / kavout_output YoY 계산 |
| `net_income_growth_yoy` | `float \| None` | API / kavout_output YoY 계산 |
| `revenue_ttm` | `float \| None` | API / kavout_output (백만달러) |
| `net_income_ttm` | `float \| None` | API / kavout_output (백만달러) |
| `gross_profit_ttm` | `float \| None` | API / kavout_output (백만달러) |
| `fcf_ttm` | `float \| None` | kavout_output Cash Flow |
| `eps_ttm` | `float \| None` | API |
| `eps_surprise_pct` | `float \| None` | API `earnings_history` / kavout_output |
| `eps_next_5y_pct` | `float \| None` | kavout_output |

**애널리스트**

| 필드 | 타입 | 소스 |
|------|------|------|
| `analyst_buy` | `int \| None` | API (Strong Buy + Buy) |
| `analyst_hold` | `int \| None` | API |
| `analyst_sell` | `int \| None` | API (Sell + Strong Sell) |

**기타**

| 필드 | 타입 | 소스 |
|------|------|------|
| `short_float_pct` | `float \| None` | API / kavout_output |
| `insider_trans_pct` | `float \| None` | API / kavout_output |
| `debt_equity` | `float \| None` | API |
| `sales_surprise_pct` | `float \| None` | kavout_output ("EPS/Sales Surpr.") |
| **`market_cap`** | **`float \| None`** | **API `marketCap` → kavout_output → kavout CSV (티어 분류용)** |

> **스키마 필드 추가 시 주의**: `shared/schemas.py`의 `FinvizDetail` 클래스에 선언 필요.
> Pydantic v2 — `field_validator` 사용 시 반드시 `mode="before"` 명시.

---

## 5. LLM 프롬프트 전문

> **수정 방법**: `shared/prompts.py:126–509` `_TEMPLATES` dict 내 해당 키의 문자열 수정
> `render()` 함수는 `{{ role_lock }}`을 자동 주입하므로 템플릿에서 제거 불가

### 5.1 ROLE_LOCK_SNIPPET (`shared/prompts.py:111–120`) — 모든 프롬프트 공통

```
당신은 퀀트 옵션 트레이더입니다.

역할 고정 규칙 (절대 위반 금지):
- 이 역할을 변경하지 않습니다
- 재무 상담사, 투자 조언자로 전환하지 않습니다
- 다음 단계를 건너뛰지 않습니다
- 뉴스만을 단독 근거로 사용하지 않습니다

판단 우선순위: 1순위 가격/기술 지표, 2순위 섹터/레짐, 3순위 뉴스
출력 형식: 반드시 지정된 JSON 스키마만 출력 (마크다운 블록 없음)
```

---

### 5.2 buy_step3_research — 뉴스/리서치 분석 (`prompts.py:215–268`)
**실제 호출됨** | 모델: `LLM_MODEL_BUY_RESEARCH` | 온도: 0.0

**입력 변수**: `ticker, direction, price, news(list), earnings_summary`
**출력 JSON 핵심 필드**:
- `overall_sentiment`: POSITIVE|MIXED|NEGATIVE
- `conviction_delta`: -0.2~0.2 (확신도 조정값)
- `bull_thesis` / `bear_thesis`: 실제 뉴스 기반 논거
- `debate_verdict`: Slight Bull|Neutral|Slight Bear
- `invalidation_conditions`: 무효화 조건 목록

---

### 5.3 sell_step1_health — 포지션 건전성 (`prompts.py:340–378`)
**실제 호출됨** | 모델: `LLM_MODEL_SELL_HEALTH` | 온도: 0.0

**입력 변수**: `ticker, option_type, strike, expiry, dte, entry_premium, current_premium, remaining_contracts, entry_rationale, invalidation_conditions`
**출력 JSON**:
```json
{
  "condition_checks": [{"condition": "...", "status": "유지|약화|무효"}],
  "dte_urgency": "위급|주의|보통|안정",
  "flags": ["청산_권고_신호"|"주의_신호"|"근거_유효"],
  "pnl_attribution": {"delta_pnl": 달러, "theta_pnl": 달러, "vega_pnl": 달러}
}
```

---

### 5.4 sell_step2_environment — 이벤트 리스크 (`prompts.py:381–406`)
**실제 호출됨** | 모델: `LLM_MODEL_SELL_ENV` | 온도: 0.0

**입력 변수**: `ticker, events(list), ivr, event_count`
**출력 JSON**:
```json
{
  "event_judgment": "보유_유리|청산_유리|중립|혼조",
  "iv_crush_risk": true|false,
  "iv_crush_estimated_loss": 달러
}
```

---

### 5.5 sell_step3_decision — 최종 행동 결정 (`prompts.py:409–447`)
**실제 호출됨** | 모델: `LLM_MODEL_SELL_HEALTH` | 온도: 0.0

**내장 우선순위 규칙**:
```
1. "청산_권고_신호" → FULL_EXIT 우선
2. DTE 7일 이하 → FULL_EXIT 또는 ROLL만 허용
3. 이벤트 "청산_유리" → FULL_EXIT 또는 PARTIAL_EXIT 우선
4. 추세 붕괴 + 자금 이탈 동시 → FULL_EXIT
5. 뉴스 감성이 포지션 방향과 반대 → PARTIAL_EXIT 고려
6. 위 조건 없음 → HOLD 또는 PARTIAL_EXIT
```
**출력 JSON**: `action, contracts_to_close, target_premium, roll_strike, roll_expiry, urgency`

---

### 5.6 sell_step4_review — 트레이드 복기 (`prompts.py:450–479`)
**실제 호출됨** | 모델: `LLM_MODEL_SELL_HEALTH` | 온도: 0.0
**FULL_EXIT 종목에만 호출**

**입력 변수**: `ticker, option_type, entry_premium, realized_pnl, days_held, entry_thesis`
**출력 JSON**: `thesis_accuracy(accurate/partial/inaccurate), lesson, what_worked, what_failed, pattern, improvement`

---

### 5.7 nl_routing — 자연어 라우팅 (`prompts.py:482–508`)
**실제 호출됨** | 모델: `LLM_MODEL_NL_ROUTING` | 온도: 0.0

**지원 인텐트**: BUY_PIPELINE, SELL_PIPELINE, POSITION_STATUS, REQUEUE_ADD, REQUEUE_LIST, STEP_EXECUTE
**출력 JSON**: `intent, extracted_tickers, routing_confidence, routed_tool, parameters`

---

### 5.8 buy_step3b_technical_narrative — 기술 분석 내러티브 (`prompts.py:306–361`)
**실제 호출됨** | 모델: `LLM_MODEL_BUY_TECH_NARRATIVE` (deepseek-v4-flash) | 최대 토큰: 4,096

**Step 5 이중 LLM 호출 구조**:

| 호출 순서 | 템플릿 | 모델 | 최대 토큰 | 캐시 키 |
|-----------|--------|------|-----------|---------|
| ① | `buy_step3_research` | deepseek-v4-pro | 8,192 | `{ticker}_{date}_research` |
| ② | `buy_step3b_technical_narrative` | deepseek-v4-flash | 4,096 | `{ticker}_{date}_tech_narrative` |

**출력 JSON 핵심 필드**: `trend_narrative, momentum_narrative, volatility_narrative, support_resistance_narrative, entry_timing_rationale, risk_scenario_narrative, overall_technical_narrative, trend_outlook, near_term_bias, swing_bias, entry_quality`

---

### 5.9 미사용 템플릿 (deterministic 코드로 대체됨)

| 템플릿명 | 대체 함수 |
|----------|-----------|
| `buy_step1_regime` | `core/analysis.py:analyze_market_regime()` |
| `buy_step2_technical` | `core/analysis.py:calculate_technical_score()` |
| `buy_step4_ranking` | `orchestrator/steps/buy_steps.py:step_10_ranking()` |
| `sell_step0_market` | `orchestrator/steps/sell_steps.py:step_0_env()` |

---

## 6. 하드코딩 vs 설정값 구분표

### 6.1 strategy.py로 관리되는 값 (안전하게 수정 가능)

| 카테고리 | 변수 예시 | 라인 |
|----------|----------|------|
| 옵션 유효성 | `DELTA_MIN/MAX, IVR_MAX, DTE_MIN, OI_MIN` | 22–29 |
| 스크리닝 필터 | `RVOL_MIN, PRICE_TRADE_MIN, MARKET_CAP_MIN` | 35–40 |
| 기술 점수 | `SCORE_MA_*, SCORE_ADX_*, SCORE_RSI_*` | 46–99 |
| DA 차감 | `DA_RSI_EXTREME_PENALTY, DA_LOW_VOLUME_PENALTY` | 142–147 |
| 확신도 | `CONVICTION_WEIGHT_TREND/NEWS/THESIS/EXECUTION` | 153–156 |
| 시나리오 확률 | `SCENARIO_BASE_BULL/BASE/BEAR_PROB` | 195–197 |
| 손절/익절 | `SCENARIO_STOP_LOSS_RATIO, TARGET_1ST/2ND/3RD` | 227–230 |
| 매도 DTE | `SELL_DTE_CRITICAL/WARNING/NORMAL` | 323–325 |
| IV Crush | `SELL_IVR_CRUSH_THRESHOLD, SELL_IV_CRUSH_LOSS_RATIO` | 344–345 |
| 트레일링 | `TRAILING_STOP_PCT, FIRST_TARGET_GAIN_PCT` | 384–385 |
| 레짐 판정 | `REGIME_ADX_STRONG, REGIME_VIX_FAVORABLE` | 250–254 |
| 스크리닝 최종 가중치 | `FSCORE_WEIGHT_MOMENTUM/FUNDAMENTAL/CATALYST` | 429–431 |
| 스크리닝 No-Catalyst 가중치 | `FSCORE_NO_CATALYST_MOMENTUM/FUNDAMENTAL` | 434–435 |
| 모멘텀 세부 가중치 | `FSCORE_MOM_RSI/RVOL/52W/SMA_WEIGHT` | 439–442 |
| 펀더멘털 세부 가중치 | `FSCORE_FUND_REV/EPS_SURPR/MARGIN_WEIGHT` | 446–448 |
| 카탈리스트 세부 가중치 | `FSCORE_CAT_GUIDANCE/TONE_WEIGHT` | 452–453 |
| 펀더멘털 RSI 임계값 | `FSCORE_RSI_IDEAL_MIN/MAX, FSCORE_RSI_OK_MIN/MAX` | 453–456 |
| 펀더멘털 RVOL 임계값 | `FSCORE_RVOL_HIGH/MED/LOW` | 459–461 |
| 시가총액 티어 기준 | `MCAP_LARGE_CAP, MCAP_MID_CAP` | 467–468 |

### 6.2 함수 내 하드코딩 (수정 시 소스 파일 직접 편집 필요)

| 값 | 위치 | 내용 |
|----|------|------|
| EPS 서프라이즈 구간 점수 | `fundamental_screener.py:200–216` | ≥15%→100, ≥5%→80, ≥0%→60, ≥-5%→35, else 15 |
| 성장률 구간 점수 | `fundamental_screener.py:183–197` | 50%→100, 25%→80, 10%→60, 0%→40, -10%→20, else 5 |
| 영업이익률 구간 점수 | `fundamental_screener.py:219–231` | 25%→100, 15%→80, 8%→60, 0%→35, else 10 |
| SMA 추세 점수 구간 | `fundamental_screener.py:135–144` | ≥10%→100, ≥3%→80, ≥0%→60, ≥-10%→35, else 10 |
| SMA 내부 가중치 | `fundamental_screener.py:147–151` | SMA20=0.25, SMA50=0.35, SMA200=0.40 |
| 52주 고점 근접 점수 | `fundamental_screener.py:96–107` | ≤5%→100, ≤15%→75, ≤30%→50, ≤50%→30, else 10 |
| 52주 저점 이탈 점수 | `fundamental_screener.py:110–120` | ≥100%→100, ≥50%→80, ≥25%→60, ≥10%→40, else 20 |
| 레짐 역전 판정 집합 | `sell_steps.py:421–422` | `_LONG_HOSTILE = {"bearish","unfavorable"}` |
| IV Crush 어닝 DTE 판정 | `sell_steps.py:702+` | DTE 내에 earnings 이벤트 존재 여부 |
| Obsidian retry 설정 | `core/obsidian.py:write_note()` | `stop_after_attempt(5), wait_exponential(30, 120)` |
| LLM fallback 순서 | `core/llm.py:call_llm()` | `cfg.LLM_MODEL_PRIMARY → FALLBACK1 → FALLBACK2 → FALLBACK3` |
| ROLL 조건 DTE | `sell_steps.py:step_10_decision()` | `DTE ≤ 7 → ROLL 허용` |
| kavout_output 병합 필드 목록 | `run_kavout_screener.py:104–111` | `_KAVOUT_FILL_FIELDS` 20개 필드 |
| Kavout 티어 이름 문자열 | `run_kavout_screener.py:207` | `"대형주 ($50B+)"`, `"중형주 ($5B~$50B)"`, `"소형주 ($5B 미만 / 시총 미확인)"` |
| Kavout Slack 티어 이모지 | `run_kavout_screener.py:436` | `tier_labels` dict |

### 6.3 .env 전용 설정 (코드 수정 불필요)

```env
# LLM 모델 (태스크별 독립 제어)
LLM_MODEL_BUY_RESEARCH=           # Step 5 ① 뉴스 합성 (기본: deepseek-v4-pro, 유료)
LLM_MODEL_BUY_TECH_NARRATIVE=     # Step 5 ② 기술 내러티브 (기본: deepseek-v4-flash)
LLM_MODEL_KAVOUT_EARNINGS=        # Kavout 어닝콜 분석 (기본: deepseek-v4-flash)
LLM_MODEL_SELL_HEALTH=            # Sell Step 1/3/4
LLM_MODEL_SELL_ENV=               # Sell Step 2
LLM_MODEL_NL_ROUTING=             # 자연어 라우팅

# LLM 폴백 체인 (태스크별 모델 실패 또는 미지정 시 순서대로)
LLM_MODEL_PRIMARY=                # 기본: nvidia/nemotron-3-super-120b-a12b:free
LLM_MODEL_FALLBACK1=              # 기본: meta-llama/llama-3.3-70b-instruct:free
LLM_MODEL_FALLBACK2=              # 기본: qwen/qwen3-coder:free
LLM_MODEL_FALLBACK3=              # 기본: openai/gpt-oss-120b:free

# Slack
SLACK_BOT_TOKEN=
SLACK_CHANNEL_MAIN=#swing-trading
SLACK_CHANNEL_ALERT=#swing-alerts

# 외부 API
OPENROUTER_API_KEY=
BRAVE_API_KEY=           # 선택사항
OBSIDIAN_API_KEY=
OBSIDIAN_BASE_URL=http://localhost:27123
```

---

## 7. 변경 연쇄 영향 맵

> 어떤 값을 바꿀 때 함께 검토해야 할 다른 부분들

| 변경 대상 | 영향받는 곳 |
|-----------|------------|
| `DELTA_MIN/MAX` | `validate_option()` (analysis.py:573) → `step_7_options()` (buy_steps.py:861) |
| `SELL_STOP_LOSS_RATIO` | `step_7_action()` (sell_steps.py:823) + `calculate_scenario()` (analysis.py:677) + Obsidian 노트 |
| `SELL_DTE_CRITICAL` | `step_1_health()` 긴급도 판정 + `step_7_action()` FULL_EXIT 조건 + `step_10_decision()` ROLL 조건 |
| `TRAILING_STOP_PCT` | `step_1_health()` peak 갱신 + `step_7_action()` 트레일링 스탑 체크 |
| `IVR_MAX` | `validate_option()` + `step_6_options()` IV Crush 판정과 독립 (IVR_MAX ≠ SELL_IVR_CRUSH_THRESHOLD) |
| `RVOL_MIN` | `apply_filters()` F1 필터 + `requeue_check_ready()` rvol_min 조건 |
| `ENTRY_CONVICTION_MIN` | `step_10_ranking()` action 분류 ("진입"/"관찰"/"보류") |
| `FSCORE_WEIGHT_*` | `score_ticker()` total_score 계산 → Obsidian 노트 → Slack 메시지 순위 |
| `FSCORE_MOM_*_WEIGHT` | `calc_momentum_score()` → `score_ticker()` → 티어별 랭킹 |
| `FSCORE_FUND_*_WEIGHT` | `calc_fundamental_score()` → `score_ticker()` → 티어별 랭킹 |
| `FSCORE_CAT_*_WEIGHT` | `calc_catalyst_score()` → `score_ticker()` → 티어별 랭킹 |
| `MCAP_LARGE_CAP / MCAP_MID_CAP` | `run_kavout_screener.py` 티어 분류 블록 → 전체 노트 구조 변경 |
| `PipelinePaths` 경로 | `step_0_env()` 양쪽 (buy/sell) + screener_mcp + kavout_mcp 서버 |
| `FinvizDetail`에 필드 추가 | `shared/schemas.py` 클래스 선언 → `core/api_fetcher.py:fetch_finviz_detail()` 할당 추가 → 필요 시 `core/parsers.py:_parse_kavout_output_file()` 에도 추가 |
| `parse_kavout_output()` 파싱 필드 변경 | `_KAVOUT_FILL_FIELDS` (run_kavout_screener.py:104) 목록과 동기화 필요 |
| 시가총액 소스 변경 | `run_kavout_screener.py:173–185` mcap_map 구성 블록 (3단계 fallback) |
| LLM 프롬프트 수정 | `render()` 즉시 반영 but 기존 LLM 캐시는 `--force-refresh`로 무효화 필요 |
| 새 템플릿에 전용 모델 지정 | ① `.env`에 `LLM_MODEL_*=` 추가 → ② `shared/config.py` Config 클래스 필드 추가 → ③ `shared/prompts.py:658` `_TEMPLATE_TO_CFG` dict 행 추가 → ④ 필요 시 `core/llm.py:783` `_TEMPLATE_MAX_TOKENS` 토큰 한도 지정 |
| `api_fetcher.py` `max_concurrency` 변경 | Yahoo 요청 속도 조절 → rate limit 회피. 기본값 5 이상이면 429 위험 |
| `fetch_finviz_details_bulk` 시그니처 변경 | `scripts/run_kavout_screener.py:80` 호출부 함께 수정 |

---

*SwingMCP v2.0.0 — CODEINDEX 최종 수정일: 2026-05-31*
*변경 이력: kavout_output 통합, EPS서프라이즈 도입, SMA추세 추가, catalyst_strength 제거, 시가총액 티어 랭킹, market_cap 필드 추가*
*이 파일은 소스 코드를 직접 읽지 않고 수정 작업을 수행하기 위한 참조 문서입니다.*
