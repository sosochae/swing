"""
shared/strategy.py
==================
SwingMCP 투자 전략 파라미터 단일 집중 관리

이 파일만 수정하면 매수/매도 파이프라인의 전략 판단 기준이 모두 변경됩니다.
복잡한 if/else 로직은 각 step 함수에 그대로 유지하되,
그 로직에 사용되는 모든 '기준값(숫자)'이 여기에 집중됩니다.

수정 가이드:
  - 숫자 변경만으로 전략 조정 가능 (코드 로직은 그대로)
  - STRATEGY_PHILOSOPHY 수정 → LLM 판단 기준 변경
  - 성능 영향 없음 (모듈 import 시 1회만 로딩)
"""

from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════
# 1. 옵션 유효성 필터  (validate_option)
# ══════════════════════════════════════════════════════════════════════

DELTA_MIN: float = 0.40          # 진입 가능 델타 하한
DELTA_MAX: float = 0.70          # 진입 가능 델타 상한
IVR_MAX:   float = 70.0          # IVR 상한 (초과 시 진입 차단)
IVR_WARNING: float = 50.0        # IVR 경고 구간 시작
SPREAD_MAX_PCT: float = 5.0      # 매수/매도 스프레드 최대 비율 (%)
DTE_MIN:   int   = 21            # 최소 잔존 만기일
OI_MIN:    int   = 500           # 최소 미결제약정
OI_WARNING: int  = 999           # OI 경고 상한 (500~999 범위)

# ══════════════════════════════════════════════════════════════════════
# 2. 종목 스크리닝 필터  (apply_filters)
# ══════════════════════════════════════════════════════════════════════

RVOL_MIN: float         = 1.5              # F1: 최소 상대거래량 (평균 대비)
PRICE_MIN: float        = 5.0              # F4: 절대 최소 주가 (페니스탁 제외)
PRICE_TRADE_MIN: float  = 20.0             # F3: 실제 거래 최소 주가
MARKET_CAP_MIN: float   = 10_000_000_000   # F3: 최소 시가총액 ($10B)
EARNINGS_PROXIMITY_DAYS: int = 5           # F5: 실적발표 ±N 거래일 차단
SECTOR_MAX_COUNT: int   = 5                # F6: 동일 섹터 최대 포지션 수

# ══════════════════════════════════════════════════════════════════════
# 3. 기술 점수 — MA 정렬  (calculate_technical_score)
# ══════════════════════════════════════════════════════════════════════

SCORE_MA_FULL_WITH_MA200: float = 25.0   # ma5>ma20>ma60 + price>MA200 → 완전 정배열
SCORE_MA_FULL_NO_MA200:  float = 22.0    # ma5>ma20>ma60 (MA200 없거나 위반)
SCORE_MA_PARTIAL:        float = 15.0    # ma5>ma20 만
SCORE_MA_NONE:           float = 0.0

# ══════════════════════════════════════════════════════════════════════
# 4. 기술 점수 — ADX
# ══════════════════════════════════════════════════════════════════════

ADX_STRONG: int = 30    # ADX ≥ 30 → 강한 추세
ADX_MEDIUM: int = 25    # ADX ≥ 25 → 중간 추세
ADX_WEAK:   int = 20    # ADX ≥ 20 → 약한 추세 (< 20 = 없음)

SCORE_ADX_STRONG: float = 25.0
SCORE_ADX_MEDIUM: float = 18.0
SCORE_ADX_WEAK:   float = 10.0
SCORE_ADX_NONE:   float = 0.0

# ══════════════════════════════════════════════════════════════════════
# 5. 기술 점수 — RSI
# ══════════════════════════════════════════════════════════════════════

RSI_LONG_CALL_IDEAL_MIN: int = 50    # 롱콜 이상 구간 하한
RSI_LONG_CALL_IDEAL_MAX: int = 70    # 롱콜 이상 구간 상한
RSI_LONG_CALL_OK_MIN:    int = 40    # 롱콜 허용 구간 하한
RSI_LONG_PUT_IDEAL_MIN:  int = 30    # 롱풋 이상 구간 하한
RSI_LONG_PUT_IDEAL_MAX:  int = 50    # 롱풋 이상 구간 상한
RSI_LONG_PUT_OK_MAX:     int = 60    # 롱풋 허용 구간 상한

SCORE_RSI_IDEAL:      float = 25.0
SCORE_RSI_OK:         float = 15.0
SCORE_RSI_EXTREME:    float = 10.0   # 과매수(롱) / 과매도(풋) — 낮게 평가
SCORE_RSI_NONE:       float = 0.0

# ══════════════════════════════════════════════════════════════════════
# 6. 기술 점수 — MACD
# ══════════════════════════════════════════════════════════════════════

SCORE_MACD_CROSS: float = 25.0   # 골든크로스(롱) / 데스크로스(풋) + 히스토그램 확인
SCORE_MACD_TREND: float = 15.0   # MACD 라인 방향만 일치
SCORE_MACD_NONE:  float = 0.0

# ══════════════════════════════════════════════════════════════════════
# 7. 기술 점수 — RVOL
# ══════════════════════════════════════════════════════════════════════

RVOL_HIGH: float = 2.0    # ≥ 2.0 → 강한 거래량 동반
RVOL_MED:  float = 1.5    # ≥ 1.5 → 중간 (= RVOL_MIN 기준과 동일)
RVOL_LOW:  float = 1.2    # ≥ 1.2 → 약하게 동반

SCORE_RVOL_HIGH: float = 25.0
SCORE_RVOL_MED:  float = 18.0
SCORE_RVOL_LOW:  float = 10.0
SCORE_RVOL_NONE: float = 0.0

# ══════════════════════════════════════════════════════════════════════
# 8. 기술 점수 — 정규화 및 보조 기준
# ══════════════════════════════════════════════════════════════════════

SCORE_RAW_MAX: float = 125.0   # 5개 컴포넌트 × 25점 = 125점 → 100점 스케일 환산

# 밸류에이션 보정 (EPS YoY 성장률 기반)
EPS_GROWTH_BULL_THRESHOLD: float = 20.0   # EPS 성장률 > 20% + 롱콜 → +점수
EPS_GROWTH_BEAR_THRESHOLD: float = -10.0  # EPS 성장률 < -10% + 롱콜 → -점수
SCORE_VALUATION_BOOST:   float = 2.0
SCORE_VALUATION_PENALTY: float = -2.0

# 옵션 플로우 기준 (option_flow_ok 판정)
PC_RATIO_CALL_BULL:     float = 0.7    # P/C < 0.7 → 콜 매수 우세
PC_RATIO_PUT_BULL:      float = 1.5    # P/C > 1.5 → 풋 매수 우세
OI_RATIO_DOMINANCE:     float = 1.5    # 한쪽 OI가 반대의 1.5배 이상 → 플로우 확인
ANOMALY_COUNT_OVERRIDE: int   = 2      # 이상 플로우 2개 이상 → 강제 option_flow_ok=True

# 지지/저항선 근접 기준 (support_ok 판정)
SUPPORT_GAP_MAX_PCT: float = 5.0       # 지지선(롱) 또는 저항선(풋) 5% 이내

# capital_flow_confirmed 최소 신호 수
CAPITAL_FLOW_MIN_SIGNALS: int = 2

# signal_count 산출 시 trend_signals 최소 임계값
SIGNAL_MA_SCORE_MIN:   float = 15.0   # MA 점수 ≥ 15 → trend 신호 1
SIGNAL_ADX_SCORE_MIN:  float = 18.0   # ADX 점수 ≥ 18 → trend 신호 1
SIGNAL_MACD_SCORE_MIN: float = 15.0   # MACD 점수 ≥ 15 → trend 신호 1
SIGNAL_RSI_SCORE_MIN:  float = 15.0   # RSI 점수 ≥ 15 → trend 신호 1

# ══════════════════════════════════════════════════════════════════════
# 9. Devil's Advocate — 기술 점수 차감  (_apply_devils_advocate)
# ══════════════════════════════════════════════════════════════════════

DA_RSI_EXTREME_THRESHOLD:      int   = 80     # RSI > 80 → 과열 체크
DA_52W_HIGH_THRESHOLD_STRONG:  float = 98.0   # 52주 고점 98% 이상 → DA 차감
DA_52W_HIGH_THRESHOLD_WEAK:    float = 95.0   # 52주 고점 95% 이상 → 추가 차감
DA_AVG_VOLUME_THRESHOLD:       float = 1.2    # 거래량 비율 < 1.2 → 미동반 차감
DA_DAILY_MOVE_LARGE:           float = 6.0    # 일일 변동 ≥ 6% → 급등/급락 차감
DA_DAILY_MOVE_MEDIUM:          float = 4.0    # 일일 변동 ≥ 4% → 완만 차감

DA_RSI_EXTREME_PENALTY:    float = -10.0   # RSI 과열 + 52주 고점 98%
DA_LOW_VOLUME_PENALTY:     float = -5.0    # 거래량 미동반
DA_BOLLINGER_BREAK_PENALTY: float = -5.0   # 볼린저밴드 상/하단 돌파
DA_NEAR_52W_HIGH_PENALTY:  float = -3.0    # 52주 고점 95% 근처 롱콜
DA_LARGE_DAILY_MOVE:       float = -8.0    # 6%+ 급등/급락
DA_MEDIUM_DAILY_MOVE:      float = -4.0    # 4~6% 급등/급락

# ══════════════════════════════════════════════════════════════════════
# 10. 확신도 가중치  (calculate_confidence)
# ══════════════════════════════════════════════════════════════════════

CONVICTION_WEIGHT_TREND:     float = 0.4   # §6.2: 추세 신호 가중치
CONVICTION_WEIGHT_NEWS:      float = 0.2   # §6.2: 뉴스/감성 가중치
CONVICTION_WEIGHT_THESIS:    float = 0.3   # §6.2: Thesis (R/R비율) 가중치
CONVICTION_WEIGHT_EXECUTION: float = 0.1   # §6.2: 실행 가능성(IVR) 가중치

# trend_confidence 계산: signal_ratio × (TREND_BASE + regime_confidence × TREND_REGIME_MULT)
CONVICTION_MAX_SIGNALS:         int   = 7    # 신호 수 정규화 기준 (분모)
CONVICTION_TREND_BASE:          float = 0.5  # 레짐 확신도=0일 때 기본 가중치
CONVICTION_TREND_REGIME_MULT:   float = 0.5  # 레짐 확신도 반영 배수

# news_confidence — 감성(sentiment) 있을 때
CONVICTION_NEWS_BULLISH_BASE:        float = 0.8
CONVICTION_NEWS_BEARISH_BASE:        float = 0.15
CONVICTION_NEWS_MIXED_BASE:          float = 0.45
CONVICTION_NEWS_CONFIDENCE_BONUS:    float = 0.1    # 감성 High confidence 보너스
CONVICTION_NEWS_CONFIDENCE_PENALTY:  float = -0.1   # 감성 Low confidence 패널티
CONVICTION_SENTIMENT_WEIGHT:         float = 0.50   # 감성 결과 비중
CONVICTION_KAVOUT_WEIGHT_WITH_SIGNAL: float = 0.20  # Kavout K-Score 비중 (비중립일 때)
# tech_weight = 1 - sentiment_weight - kavout_weight

# news_confidence — 감성 없을 때
CONVICTION_KAVOUT_WEIGHT_NO_SENTIMENT: float = 0.30  # Kavout 비중 (비중립일 때)
CONVICTION_KAVOUT_WEIGHT_NEUTRAL:      float = 0.10  # Kavout 비중 (중립일 때)

# thesis_confidence: rr_ratio / RR_NORMALIZATION (최대 1.0)
CONVICTION_RR_NORMALIZATION: float = 3.0

# execution_confidence: IVR 기반
CONVICTION_EXECUTION_LOW_IVR:   int   = 30    # IVR ≤ 30 → 최고 실행 확신도
CONVICTION_EXECUTION_MED_IVR:   int   = 50    # IVR ≤ 50 → 중간 실행 확신도
CONVICTION_EXECUTION_HIGH_SCORE: float = 1.0
CONVICTION_EXECUTION_MED_SCORE:  float = 0.7
CONVICTION_EXECUTION_LOW_SCORE:  float = 0.4

# 확신도 레벨 임계값
CONVICTION_HIGH_THRESHOLD:   float = 0.70    # "high" → 진입 권고
CONVICTION_MEDIUM_THRESHOLD: float = 0.50    # "medium" → 관찰

# ══════════════════════════════════════════════════════════════════════
# 11. 시나리오 기본 확률  (calculate_scenario)
# ══════════════════════════════════════════════════════════════════════

SCENARIO_BASE_BULL_PROB: float = 0.30
SCENARIO_BASE_BASE_PROB: float = 0.40
SCENARIO_BASE_BEAR_PROB: float = 0.30

# ADX 기반 확률 조정
SCENARIO_ADX_STRONG_THRESHOLD: int   = 30     # ADX ≥ 30 → 강한 추세
SCENARIO_ADX_MED_THRESHOLD:    int   = 25     # ADX ≥ 25 → 중간 추세
SCENARIO_ADX_STRONG_ADJ:       float = 0.10   # 확률 +10% 부스트
SCENARIO_ADX_MED_ADJ:          float = 0.05   # 확률 +5% 부스트
SCENARIO_ADX_WEAK_ADJ:         float = -0.05  # 확률 -5% 패널티

# 신호 수 기반 확률 조정
SCENARIO_SIGNAL_CENTER:          int   = 4      # 기준 신호 수 (4개 = 중립)
SCENARIO_SIGNAL_ADJ_PER_SIGNAL:  float = 0.025  # 신호 1개당 ±2.5%

# 확률 클램핑 (롱콜 기준)
SCENARIO_BULL_PROB_MIN: float = 0.10
SCENARIO_BULL_PROB_MAX: float = 0.70
SCENARIO_BEAR_PROB_MIN: float = 0.10
SCENARIO_BEAR_PROB_MAX: float = 0.60

# 가격 이동 배수
SCENARIO_BULL_PRICE_MULT: float = 1.5   # 상승 시나리오 이동폭 = implied_move × 1.5
SCENARIO_BASE_PRICE_MULT: float = 0.5   # 기본 시나리오 이동폭 = implied_move × 0.5
SCENARIO_BEAR_PRICE_MULT: float = 1.5   # 하락 시나리오 이동폭 = implied_move × 1.5

# IV 변화 가정 (케이스별)
SCENARIO_BULL_IV_CHANGE: float = 0.0    # 상승: IV 유지
SCENARIO_BASE_IV_CHANGE: float = -0.05  # 기본: 소폭 IV 압축
SCENARIO_BEAR_IV_CHANGE: float = -0.15  # 하락: IV 붕괴 가정

# 손절/익절 배수 (entry_premium 대비)
SCENARIO_STOP_LOSS_RATIO:  float = 0.5   # -50% → 손절
SCENARIO_TARGET_1ST_RATIO: float = 1.5   # +50% → 1차 익절
SCENARIO_TARGET_2ND_RATIO: float = 2.0   # +100% → 2차 익절
SCENARIO_TARGET_3RD_RATIO: float = 2.5   # +150% → 3차 익절

# Delta gap 리스크 경고 임계값
SCENARIO_DELTA_WARN_LOW:    float = 0.45   # 델타 < 0.45
SCENARIO_THETA_WARN_RATIO:  float = 0.03   # |theta| > premium × 3%
SCENARIO_IV_WARN_HIGH:      float = 0.80   # IV > 80%

# ══════════════════════════════════════════════════════════════════════
# 12. 포트폴리오 노출 임계값  (check_portfolio_exposure)
# ══════════════════════════════════════════════════════════════════════

PORTFOLIO_LONG_RATIO_MAX:   float = 0.8    # 롱 비율 > 80% → 편향 경고
PORTFOLIO_LONG_RATIO_MIN:   float = 0.2    # 롱 비율 < 20% → 편향 경고
PORTFOLIO_DELTA_CAPITAL_PCT: float = 0.05  # |총델타| > 총자본 × 5% → 경고
PORTFOLIO_THETA_WARN:        float = -3.0  # |총세타| > 3.0 → 경고

# ══════════════════════════════════════════════════════════════════════
# 13. 시장 레짐 판정 임계값  (analyze_market_regime)
# ══════════════════════════════════════════════════════════════════════

REGIME_ADX_STRONG: int = 25    # ADX ≥ 25 → 추세 강함 (pass)
REGIME_ADX_WEAK:   int = 20    # ADX < 20 → 추세 없음 (fail)

REGIME_VIX_FAVORABLE:  float = 20.0   # VIX ≤ 20 → 롱콜 환경 (pass)
REGIME_VIX_BORDERLINE: float = 30.0   # VIX ≤ 30 → 경계선

REGIME_MA20_STRONG_RATIO:  float = 1.005   # 지수 > MA20 × 1.005 → 상향 기울기 확인
REGIME_VIX_UPTREND_RATIO:  float = 1.10    # VIX > VIX_MA20 × 1.10 → 공포 확산
REGIME_DXY_STRENGTH_RATIO: float = 1.005   # DXY > DXY_MA20 × 1.005 → 달러 강세
REGIME_SOXX_WEAK_RATIO:    float = 0.98    # SOXX < SOXX_MA20 × 0.98 → 반도체 약세

REGIME_YIELD_CRITICAL: float = 5.0    # 10년물 ≥ 5% → 고금리 위험
REGIME_YIELD_WARNING:  float = 4.5    # 10년물 ≥ 4.5% → 금리 주의 구간

REGIME_FEAR_GREED_EXTREME_FEAR:  int = 25   # F&G < 25 → 극단적 공포
REGIME_FEAR_GREED_EXTREME_GREED: int = 75   # F&G > 75 → 극단적 탐욕

# ══════════════════════════════════════════════════════════════════════
# 14. 매수 진입 결정  (buy_steps.py Step 10)
# ══════════════════════════════════════════════════════════════════════

ENTRY_CONVICTION_MIN: float = 0.70   # 이 이상 → "진입"
WATCH_CONVICTION_MIN: float = 0.50   # 이 이상 → "관찰"
HOLD_CONVICTION_MIN:  float = 0.30   # 이 이상 → "보류" (미만 → "탈락")

HIGH_DOWNSIDE_LOSS_RATIO: float = 0.50   # 베어케이스 손실 > 투자금 50% → 고위험 경고

# ══════════════════════════════════════════════════════════════════════
# 15. 매수 — Kavout AI K-Score 보정  (buy_steps.py Step 4)
# ══════════════════════════════════════════════════════════════════════

KAVOUT_HIGH_SCORE: float = 7.0    # K-Score ≥ 7 → 강세 신호
KAVOUT_LOW_SCORE:  float = 3.0    # K-Score ≤ 3 → 약세 신호
KAVOUT_COMBO_SCORE: float = 6.0   # 모멘텀+K-Score 콤보 기준

KAVOUT_HIGH_SIGNAL_BONUS: int   = 1
KAVOUT_HIGH_SCORE_BONUS:  float = 5.0
KAVOUT_LOW_SIGNAL_PENALTY: int  = -1
KAVOUT_LOW_SCORE_PENALTY:  float = -10.0

KAVOUT_MOMENTUM_THRESHOLD: float = 30.0  # 1개월 모멘텀 ≥ 30% + K≥6 → 추가 신호
KAVOUT_COMBO_SIGNAL_BONUS: int   = 1
KAVOUT_COMBO_SCORE_BONUS:  float = 3.0

# 애널리스트 추천 보정 (Finviz recom: 1=Strong Buy ~ 5=Sell)
ANALYST_BUY_THRESHOLD:       float = 2.0    # recom ≤ 2.0 → +1 신호
ANALYST_SELL_THRESHOLD:      float = 4.0    # recom ≥ 4.0 → -1 신호, -5점
ANALYST_SELL_SCORE_PENALTY:  float = -5.0

SHORT_FLOAT_SQUEEZE_THRESHOLD: float = 15.0  # 공매도 비율 ≥ 15% → 숏스퀴즈 가능

# ══════════════════════════════════════════════════════════════════════
# 16. 매수 — Devil's Advocate 점수 차감  (buy_steps.py Step 6)
# ══════════════════════════════════════════════════════════════════════

DA_BUY_SCORE_THRESHOLD:        float = 40.0         # 이 점수 미만 → 탈락 필터
DA_BUY_IV_CRUSH_PENALTY:       float = -15.0         # IV Crush 위험
DA_BUY_THESIS_CONTRA_PENALTY:  float = -20.0         # Thesis 반박 (LLM 판결 역방향)
DA_BUY_INSIDER_SELL_PENALTY:   float = -10.0         # 내부자 순매도 초과
DA_BUY_EPS_MISS_PENALTY:       float = -5.0          # 최근 EPS 미스
DA_BUY_FINVIZ_INSIDER_PENALTY: float = -10.0         # Finviz 내부자 매도
DA_BUY_FINVIZ_EPS_PENALTY:     float = -5.0          # Finviz EPS 서프라이즈

DA_BUY_IV_CRUSH_IMPLIED_MOVE:  float = 10.0          # 내재 이동폭 > 10% → IV Crush 위험
DA_BUY_INSIDER_SELL_AMOUNT:    float = 10_000_000.0  # 내부자 순매도 $10M 기준
DA_BUY_EPS_MISS_FRACTION:      float = -0.05         # EPS 미스 기준 (분수, = -5%)
DA_BUY_EPS_MISS_PCT:           float = -5.0          # EPS 미스 기준 (퍼센트)
DA_BUY_FINVIZ_INSIDER_PCT:     float = -10.0         # Finviz insider_trans_pct 기준

# ══════════════════════════════════════════════════════════════════════
# 17. 매도 — DTE 긴급도  (sell_steps.py Step 1, Step 7)
# ══════════════════════════════════════════════════════════════════════

SELL_DTE_CRITICAL: int = 7    # ≤ 7일 → "위급" (즉시 청산 검토)
SELL_DTE_WARNING:  int = 14   # ≤ 14일 → "주의"
SELL_DTE_NORMAL:   int = 21   # ≤ 21일 → "보통" (> 21일 → "안정")

# ══════════════════════════════════════════════════════════════════════
# 18. 매도 — 손절 / 익절 기준  (sell_steps.py Step 7)
# ══════════════════════════════════════════════════════════════════════

# entry_premium 대비 배수 (SCENARIO_* 와 동일 기준 — 일관성 유지)
SELL_STOP_LOSS_RATIO:  float = SCENARIO_STOP_LOSS_RATIO    # 0.5 = -50%
SELL_TARGET_1ST_RATIO: float = SCENARIO_TARGET_1ST_RATIO   # 1.5 = +50%
SELL_TARGET_2ND_RATIO: float = SCENARIO_TARGET_2ND_RATIO   # 2.0 = +100%
SELL_TARGET_3RD_RATIO: float = SCENARIO_TARGET_3RD_RATIO   # 2.5 = +150%

# DTE 임박 전량 청산 (step_7_action 규칙)
SELL_DTE_FORCE_EXIT: int = SELL_DTE_CRITICAL    # pos.dte ≤ 7 → FULL_EXIT

# ══════════════════════════════════════════════════════════════════════
# 19. 매도 — IV Crush 분석  (sell_steps.py Step 6)
# ══════════════════════════════════════════════════════════════════════

SELL_IVR_CRUSH_THRESHOLD: float = 70.0   # IVR > 70 → IV Crush 체크
SELL_IV_CRUSH_LOSS_RATIO: float = 0.30   # IV Crush 예상 손실 = 프리미엄 × 30%

# ══════════════════════════════════════════════════════════════════════
# 20. 매도 — Finviz 기반 청산 플래그  (sell_steps.py Step 7)
# ══════════════════════════════════════════════════════════════════════

SELL_ANALYST_SELL_THRESHOLD: float = ANALYST_SELL_THRESHOLD   # 4.0
SELL_EPS_MISS_PCT:           float = DA_BUY_EPS_MISS_PCT       # -5.0
SELL_INSIDER_SELL_PCT:       float = DA_BUY_FINVIZ_INSIDER_PCT # -10.0
SELL_TARGET_PRICE_PROXIMITY: float = 0.95   # 현재가 ≥ 목표주가 × 95% → 상방 여력 소진

# ══════════════════════════════════════════════════════════════════════
# 21. 매도 — 부분 청산 비율  (sell_steps.py Step 8)
# ══════════════════════════════════════════════════════════════════════

SELL_PARTIAL_REGIME_RATIO: float = 0.75   # 레짐 역전 / DTE 주의 → 75% 청산
SELL_PARTIAL_PROFIT_RATIO: float = 0.50   # 수익 중 부분 청산 → 50%
SELL_PARTIAL_LOSS_RATIO:   float = 0.33   # 손실 중 헤지 청산 → 33%

# ══════════════════════════════════════════════════════════════════════
# 22. 매도 — 시나리오 확률 (신호 수 기반)  (sell_steps.py Step 7)
# ══════════════════════════════════════════════════════════════════════

SELL_SIGNAL_BULL_STRONG: int = 6   # 신호 ≥ 6
SELL_SIGNAL_BULL_MEDIUM: int = 3   # 신호 ≥ 3

# (bull_prob, base_prob, bear_prob)
SELL_PROB_BULL_STRONG: tuple[float, float, float] = (0.40, 0.40, 0.20)
SELL_PROB_BULL_MEDIUM: tuple[float, float, float] = (0.25, 0.45, 0.30)
SELL_PROB_BULL_WEAK:   tuple[float, float, float] = (0.15, 0.35, 0.50)

# 내재 이동폭 클램핑
SELL_IMPLIED_MOVE_MIN: float = 1.0
SELL_IMPLIED_MOVE_MAX: float = 30.0

# ══════════════════════════════════════════════════════════════════════
# 23. 트레일링 스탑 / 익절 전역 설정
# ══════════════════════════════════════════════════════════════════════

TRAILING_STOP_PCT:      float = 20.0   # 고점 대비 -20% 하락 → 트레일링 스탑 발동
FIRST_TARGET_GAIN_PCT:  float = 50.0   # +50% 수익 → 1차 부분 익절 권고

# ══════════════════════════════════════════════════════════════════════
# 24. 전략 철학 — LLM 프롬프트 삽입 텍스트
# ══════════════════════════════════════════════════════════════════════

STRATEGY_PHILOSOPHY: str = """
[투자 전략 원칙]
- 방향: 트렌드 추종 롱콜(Long Call) 위주 옵션 스윙 전략
- 보유 기간: 3~8주 (DTE 21일 이상 진입, 7일 이하 청산)
- 진입 조건: ADX ≥ 25(강한 추세) + IVR ≤ 50(저IV) + 상승 추세 확인(MA 정배열)
- 포지션 크기: 단일 종목 최대 $1,000 (총자본의 33% 이하)
- 1차 청산: +50% 수익 달성 시 보유 계약의 절반 매도 (리스크 원금 회수)
- 최종 청산: 트레일링 스탑 -20% 도달 OR DTE 7일 이하 OR 트레이딩 thesis 무효화
- 리스크 분산: 동일 섹터 최대 5개 포지션, P/C 비율·내부자 거래 모니터링
"""

# ══════════════════════════════════════════════════════════════════════
# 25. 펀더멘털 스크리닝 철학 — 어닝콜 LLM 분석 전용 프롬프트
#     (STRATEGY_PHILOSOPHY와 별개 — 옵션 판단 없음, 순수 종목 선별)
# ══════════════════════════════════════════════════════════════════════

FUNDAMENTAL_SCREEN_PHILOSOPHY: str = """
[종목 선별 원칙 — 옵션 진입 전 후보군 압축]
- 분석 목적: 어닝콜 분석 텍스트를 읽고 3~8주 스윙 관점의 카탈리스트를 평가한다
- 옵션 판단(델타, IVR, DTE 등)은 이 단계에서 하지 않는다 — 순수 주식 선별 단계임
- 가이던스 판단 기준:
    up   = 가이던스 상향 / 실적 서프라이즈 강함 / 신규 성장 드라이버 언급
    flat = 가이던스 유지 / 예상 부합 / 특별한 변화 없음
    down = 가이던스 하향 / 실적 미스 / 수요 둔화·비용 상승 우려
- 경영진 톤 기준:
    bullish  = 구체적 수치 제시(시장점유율%, 마진 bps 등) + 자신감 있는 언어
    neutral  = 일반적 코멘트, 긍정·부정 신호 혼재
    bearish  = 리스크 언급 지배적 / 모호한 표현 / 수치 없는 낙관
- catalyst_strength (1~5):
    5 = 실적 서프라이즈 + 가이던스 raise + bullish 톤 조합
    4 = 두 가지 조합
    3 = 한 가지 긍정 요소
    2 = 중립적 / 혼재
    1 = 부정적 신호 지배
- 출력은 반드시 JSON으로: guidance_direction, mgmt_tone, key_risks(list), catalyst_strength
"""

# ── 펀더멘털 스코어 가중치 ────────────────────────────────────────────
# 펀더멘털(F)은 순위 계산에서 제외 — 모멘텀+카탈리스트 중심으로 단순화
# Catalyst 있음: M(0.35) + C(0.25) → 정규화 → M=0.583, C=0.417
FSCORE_WEIGHT_MOMENTUM:    float = 0.583  # RSI + Rel Volume + 52W + SMA
FSCORE_WEIGHT_FUNDAMENTAL: float = 0.0    # 순위 계산에서 제외 (점수는 노트용으로 유지)
FSCORE_WEIGHT_CATALYST:    float = 0.417  # 가이던스 + 경영진 톤 (있는 경우)

# Catalyst 없는 종목용 재분배 비율 (펀더멘털 없으므로 모멘텀 100%)
FSCORE_NO_CATALYST_MOMENTUM:    float = 1.0
FSCORE_NO_CATALYST_FUNDAMENTAL: float = 0.0

# Momentum Score 세부 가중치 (합 = 1.0)
# SMA 추세 추가 — 단기 과열보다 추세 확인 비중 강화
FSCORE_MOM_RSI_WEIGHT:    float = 0.25   # RSI(14) 구간 점수
FSCORE_MOM_RVOL_WEIGHT:   float = 0.25   # Relative Volume
FSCORE_MOM_52W_WEIGHT:    float = 0.25   # 52W 고점/저점 위치
FSCORE_MOM_SMA_WEIGHT:    float = 0.25   # SMA20/50/200 위치 (추세)

# Fundamental Score 세부 가중치 (합 = 1.0)
# GAAP 순이익 성장률 제거 → EPS 서프라이즈(Non-GAAP 프록시)로 대체
FSCORE_FUND_REV_WEIGHT:        float = 0.40  # 매출 YoY 성장률 (가장 신뢰)
FSCORE_FUND_EPS_SURPR_WEIGHT:  float = 0.25  # EPS 서프라이즈 % (Non-GAAP 프록시)
FSCORE_FUND_MARGIN_WEIGHT:     float = 0.35  # 영업이익률 (업종 무관 효율성)

# Catalyst Score 세부 가중치 (합 = 1.0)
# catalyst_strength 제거 — 가이던스+톤에서 이미 반영된 내용 중복 집계 방지
FSCORE_CAT_GUIDANCE_WEIGHT: float = 0.60  # 가이던스 방향 (상향/유지/하향)
FSCORE_CAT_TONE_WEIGHT:     float = 0.40  # 경영진 톤 (bullish/neutral/bearish)

# RSI 구간 점수 매핑 (롱 관점: 50~70이 이상적)
FSCORE_RSI_IDEAL_MIN: float = 50.0
FSCORE_RSI_IDEAL_MAX: float = 70.0
FSCORE_RSI_OK_MIN:    float = 40.0
FSCORE_RSI_OK_MAX:    float = 80.0

# Rel Volume 점수 임계값
FSCORE_RVOL_HIGH: float = 2.0   # ≥ 2.0 → 만점
FSCORE_RVOL_MED:  float = 1.5   # ≥ 1.5 → 중간
FSCORE_RVOL_LOW:  float = 1.2   # ≥ 1.2 → 약함

# ── 시가총액 티어 기준 (USD) ──────────────────────────────────────────
MCAP_LARGE_CAP: float = 50_000_000_000   # $50B 이상 → 대형주
MCAP_MID_CAP:   float =  5_000_000_000   # $5B~$50B  → 중형주
# $5B 미만 또는 시총 미확인 → 소형주
