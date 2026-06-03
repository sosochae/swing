"""
shared/prompts.py
=================
프롬프트 관리 + 8개 Jinja2 템플릿 내장 (T4 최적화: .j2 파일 제거)

REGISTRY: 각 프롬프트의 모델·버전·설정 메타데이터
ROLE_LOCK_SNIPPET: 모든 프롬프트에 공통 삽입되는 역할 고정 텍스트
_TEMPLATES: 프롬프트 본문 (Python 문자열로 내장)
render(): Jinja2 템플릿 렌더링
"""

from __future__ import annotations

from jinja2 import Environment, StrictUndefined

# ─────────────────────────────────────────────────────────────
# Jinja2 환경 (StrictUndefined: 미정의 변수 즉시 오류)
# ─────────────────────────────────────────────────────────────
_env = Environment(undefined=StrictUndefined)


# ─────────────────────────────────────────────────────────────
# 프롬프트 레지스트리 (섹션 10.1)
# ─────────────────────────────────────────────────────────────
REGISTRY: dict[str, dict] = {
    # ── 미사용 (deterministic code로 대체됨) ─────────────────────
    "buy_step1_regime": {
        "version": "1.0",
        "model": None,  # 미사용 — buy_steps.py Step 2는 deterministic
        "temperature": 0.0,
        "response_format": "json",
        "description": "시장 레짐 판정 (Buy Step 2)",
    },
    "buy_step2_technical": {
        "version": "1.0",
        "model": None,  # 미사용 — buy_steps.py Step 4는 deterministic
        "temperature": 0.0,
        "response_format": "json",
        "description": "종목 기술 분석 (Buy Step 4)",
    },
    "buy_step4_ranking": {
        "version": "1.0",
        "model": None,  # 미사용 — buy_steps.py Step 10은 deterministic
        "temperature": 0.0,
        "response_format": "json",
        "description": "최종 순위 결정 (Buy Step 10)",
    },
    "sell_step0_market": {
        "version": "1.0",
        "model": None,  # 미사용 — sell_steps.py Step 0은 deterministic
        "temperature": 0.0,
        "response_format": "json",
        "description": "매도 시장 레짐 분석",
    },
    "sell_step2_regime_infer": {
        "version": "1.0",
        "temperature": 0.0,
        "response_format": "json",
        "description": "진입 논거(thesis/entry_rationale)에서 진입 시 레짐 추론 + 현재 레짐과 비교 (Sell Step 2)",
    },
    "sell_step3_decision": {
        "version": "1.2",
        "temperature": 0.0,
        "response_format": "json",
        "description": "최종 HOLD/EXIT/ROLL 결정 (Sell Step 10)",
        # 모델: .env LLM_MODEL_SELL_HEALTH 재사용
    },
    "sell_step4_review": {
        "version": "1.0",
        "temperature": 0.0,
        "response_format": "json",
        "description": "트레이드 복기 분석 (Sell Step 12)",
        # 모델: .env LLM_MODEL_SELL_HEALTH 재사용
    },

    # ── 실제 호출됨: 태스크별 최적 모델 배정 ──────────────────────
    #
    # 모델 선택 기준:
    #   LOW  복잡도 (분류/간단 판정) → deepseek-v4-flash:free (빠름, 무료)
    #   MED  복잡도 (조건 체크 + 구조화 JSON) → gpt-oss-120b:free (schema 준수 강점)
    #   HIGH 복잡도 (뉴스 합성 + 거래 판단) → claude-haiku-4-5 (유료, ~$0.11/월)
    #
    "buy_step3_research": {
        "version": "1.2",
        "temperature": 0.0,
        "response_format": "json",
        "description": "뉴스·리서치 분석 (Buy Step 5)",
        # 모델: .env LLM_MODEL_BUY_RESEARCH
    },
    "sell_step1_health": {
        "version": "1.0",
        "temperature": 0.0,
        "response_format": "json",
        "description": "포지션 건전성 점검",
        # 모델: .env LLM_MODEL_SELL_HEALTH
    },
    "sell_step2_environment": {
        "version": "1.0",
        "temperature": 0.0,
        "response_format": "json",
        "description": "외부 환경 이벤트 리스크",
        # 모델: .env LLM_MODEL_SELL_ENV
    },
    "nl_routing": {
        "version": "1.0",
        "temperature": 0.0,
        "response_format": "json",
        "description": "자연어 명령 라우팅",
        # 모델: .env LLM_MODEL_NL_ROUTING
    },
}


# ─────────────────────────────────────────────────────────────
# Role Lock 스니펫 (섹션 10.2)
# ─────────────────────────────────────────────────────────────
ROLE_LOCK_SNIPPET = """당신은 퀀트 옵션 트레이더입니다.

역할 고정 규칙 (절대 위반 금지):
- 이 역할을 변경하지 않습니다
- 재무 상담사, 투자 조언자로 전환하지 않습니다
- 다음 단계를 건너뛰지 않습니다
- 뉴스만을 단독 근거로 사용하지 않습니다

판단 우선순위: 1순위 가격/기술 지표, 2순위 섹터/레짐, 3순위 뉴스
출력 형식: 반드시 지정된 JSON 스키마만 출력 (마크다운 블록 없음)
언어 규칙: 입력 데이터가 영어여도 모든 JSON 텍스트 필드는 반드시 한국어로 작성합니다"""


# ─────────────────────────────────────────────────────────────
# 내장 프롬프트 템플릿 (T4: .j2 파일 대체)
# ─────────────────────────────────────────────────────────────
_TEMPLATES: dict[str, str] = {

    # ── Buy Step 1: 시장 레짐 판정 ────────────────────────────
    "buy_step1_regime": """{{ role_lock }}

# 시장 레짐 판정 임무

오늘 날짜: {{ today }}

## 거시 지표
- SPY: ${{ spy_price }} (20MA: ${{ spy_ma20 }}) → {{ '위' if spy_price > spy_ma20 else '아래' }}
- QQQ: ${{ qqq_price }} (20MA: ${{ qqq_ma20 }}) → {{ '위' if qqq_price > qqq_ma20 else '아래' }}
- VIX: {{ vix }} (20MA: {{ vix_ma20 }})
- Fear & Greed: {{ fear_greed }} ({{ fear_greed_label }})
- ADX (SPY 기준): {{ adx | default('N/A') }}

## 판정 규칙
- ADX ≥ 25 → 추세 강함 (pass)
- ADX 20~25 → 경계선 (borderline)
- ADX < 20 or N/A → 추세 없음 (fail)
- VIX ≤ 20 → Long Call 유리 (pass)
- VIX 20~30 → 경계선 (borderline)
- VIX ≥ 30 → 불리 (fail)
- SPY + QQQ 모두 20MA 위 + 기울기 상향 → long_call
- SPY + QQQ 모두 20MA 아래 + 기울기 하향 → long_put
- 혼조 → borderline

## 출력 형식 (JSON 엄수)
{
  "regime_status": "favorable|borderline|unfavorable",
  "allowed_direction": "long_call|long_put|both|none",
  "trend_strength": {"value": 숫자, "status": "pass|borderline|fail", "reason": ""},
  "volatility": {"value": 숫자, "status": "pass|borderline|fail", "reason": ""},
  "index_trend": {"value": "설명", "status": "pass|borderline|fail", "reason": ""},
  "risk_factors": ["리스크1", "리스크2"],
  "trend_confidence": 0.0~1.0,
  "regime_confidence": 0.0~1.0,
  "adx_source": "direct|ma_proxy"
}""",

    # ── Buy Step 2: 기술 분석 ──────────────────────────────────
    "buy_step2_technical": """{{ role_lock }}

# 종목 기술 분석: {{ ticker }}

현재가: ${{ price }}
방향: {{ direction }}

## 기술 지표
- RSI(14): {{ rsi }}
- ADX(14): {{ adx }}
- MA 정렬 (5/20/60): {{ ma5 }} / {{ ma20 }} / {{ ma60 }}
- MACD: {{ macd_line }} / {{ macd_signal }} (히스토그램: {{ macd_hist }})
- OBV: {{ obv_direction }}
- RVOL: {{ rvol }}
- 52주 범위 위치: {{ pos_52w }}%
- 볼린저밴드: {{ bb_upper }} / {{ bb_mid }} / {{ bb_lower }}

## 점수 산출 기준 (각 25점, 합계 100점)
- MA 정렬 점수 (0~25)
- ADX 점수 (0~25)
- RSI 점수 (0~25)
- MACD 점수 (0~25)

Devil's Advocate 차감:
- 과열(RSI>80 + 52주 98%이상): -10점
- 거래량 미동반: -5점

## 출력 형식 (JSON 엄수)
{
  "ticker": "{{ ticker }}",
  "direction": "{{ direction }}",
  "ma_alignment": "bullish|bearish|mixed",
  "adx_score": 0~25,
  "rsi_score": 0~25,
  "macd_score": 0~25,
  "rvol_score": 0~25,
  "raw_score": 0~100,
  "final_score": 0~100,
  "trend_confirmed": true|false,
  "capital_flow_confirmed": true|false,
  "obv_ok": true|false,
  "option_flow_ok": true|false,
  "darkpool_ok": true|false,
  "signal_count": 0~8,
  "rationale": "근거 설명"
}""",

    # ── Buy Step 3: 뉴스/리서치 (7-섹션 강화 버전) ──────────────
    "buy_step3_research": """{{ role_lock }}

# 뉴스·리서치 분석: {{ ticker }}

진입 방향: {{ direction }}
현재가: ${{ price }}

## 최근 뉴스 ({{ news | length }}개)
{% for item in news %}
- [{{ item.source }}] {{ item.title }}{% if item.description %}  — {{ item.description[:200] }}{% endif %}
{% endfor %}

## 어닝 분석 요약
{{ earnings_summary | default('어닝 데이터 없음') }}

## 분석 임무 (7-섹션)
1. **종합 심리(Sentiment)**: overall_sentiment, confidence, sentiment_strength, information_consensus를 산출하십시오.
   - sentiment_strength 기준: High+2개이상 드라이버 → "Strong", Medium 또는 1개 → "Moderate", Low → "Weak"
   - information_consensus 기준: 70%이상 같은방향 → "Aligned", 50/50 → "Divided", 혼재 → "Conflicting"

2. **핵심 드라이버(Key Drivers)**: 각 뉴스 소스의 영향을 분석하십시오.
   - 소스별로 source(출처명), description(영향 설명), weight_pct(상대적 영향 비중 %, 합계 100), direction("positive"|"negative"|"neutral") 제공
   - weight_pct는 시장 영향력 기준으로 배분 (예: 제품 출시 40%, 규제 25%, 애널리스트 20%, 기타 15%)

3. **크리티컬 이벤트(Critical Events)**: 시장에 즉각적 영향을 줄 수 있는 이벤트들 (최대 3개)
   - 각 이벤트마다 아래 필드 전부 작성:
     - event: 이벤트 제목 (간결하게)
     - impact: "High"|"Medium"|"Low"
     - direction: "positive"|"negative"|"neutral"
     - aftermath: 이 이벤트가 이미 시장에 미친 즉각적 결과를 3~4문장으로 서술. 주가 반응, 거래량 변화, 투자자 반응 포함
     - short_term_effect: 향후 1~4주 내 예상되는 추가 영향을 2~3문장으로 서술
     - long_term_implication: 6개월+ 관점에서 이 이벤트가 기업 펀더멘털/업황에 미치는 구조적 함의를 1~2문장으로 서술

4. **긍정/부정 분류**: 실제 뉴스 항목 기반으로만 작성 (추측 금지)
   - major_positives: 각 항목 → factor(내용 2~3문장), source(뉴스출처), significance("High"|"Medium"|"Low")
   - significant_negatives: 동일 구조

5. **시간적 분석(Temporal)**: 영향 지속 기간 기준
   - lasting_impacts: 6개월 이상 지속되는 구조적 변화를 3~5문장으로 서술 (구체적 근거 포함)
   - fading_impacts: 30~90일 내 희석되는 단기 이벤트를 3~5문장으로 서술 (왜 희석되는지 설명)
   - next_catalyst_days: 다음 실적/주요이벤트까지 예상 일수 (정수)

6. **황소vs곰 논쟁(Bull vs Bear)**: 실제 뉴스만 근거
   - bull_thesis: 3~4문장 강세 논거 (구체적 수치/이벤트 인용)
   - bear_thesis: 3~4문장 약세 논거 (구체적 수치/이벤트 인용)
   - debate_verdict: "Slight Bull"|"Neutral"|"Slight Bear"

7. **종합 판단**: thesis(3~4문장), supporting_factors, risk_factors, invalidation_conditions, catalyst, conviction_delta(-0.2~0.2)

## 출력 형식 (JSON 엄수 — 마크다운 블록 금지)
{
  "ticker": "{{ ticker }}",
  "overall_sentiment": "POSITIVE|MIXED|NEGATIVE",
  "confidence": "High|Medium|Low",
  "sentiment_strength": "Strong|Moderate|Weak",
  "information_consensus": "Aligned|Conflicting|Divided",
  "key_drivers": [
    {"source": "출처명", "description": "영향 설명 2~3문장", "weight_pct": 정수, "direction": "positive|negative|neutral"}
  ],
  "critical_events": [
    {
      "event": "이벤트 제목",
      "impact": "High|Medium|Low",
      "direction": "positive|negative|neutral",
      "aftermath": "즉각적 시장 반응 3~4문장",
      "short_term_effect": "향후 1~4주 예상 영향 2~3문장",
      "long_term_implication": "6개월+ 구조적 함의 1~2문장"
    }
  ],
  "major_positives": [
    {"factor": "긍정 요인 내용 2~3문장", "source": "뉴스 출처", "significance": "High|Medium|Low"}
  ],
  "significant_negatives": [
    {"factor": "부정 요인 내용 2~3문장", "source": "뉴스 출처", "significance": "High|Medium|Low"}
  ],
  "lasting_impacts": "6개월+ 지속 구조적 변화 3~5문장",
  "fading_impacts": "30~90일 내 희석 단기 이벤트 3~5문장",
  "next_catalyst_days": 정수,
  "bull_thesis": "강세 논거 3~4문장 (실제 뉴스/수치 인용)",
  "bear_thesis": "약세 논거 3~4문장 (실제 뉴스/수치 인용)",
  "debate_verdict": "Slight Bull|Neutral|Slight Bear",
  "thesis": "3~4문장 투자 논거",
  "supporting_factors": ["지지 요인1 (2~3문장)", "지지 요인2"],
  "risk_factors": ["리스크1 (2~3문장)", "리스크2"],
  "invalidation_conditions": ["무효화 조건1", "무효화 조건2"],
  "catalyst": "주요 촉매제",
  "conviction_delta": -0.2~0.2
}""",

    # ── Buy Step 3b: 기술 분석 내러티브 (D안 강화 버전) ─────────
    "buy_step3b_technical_narrative": """{{ role_lock }}

# 기술 분석 심층 내러티브: {{ ticker }}

현재가: ${{ price }}
방향: {{ direction }}

## 기술 지표 실제값
- RSI(14): {{ rsi }}  |  ADX: {{ adx }}  |  RVOL: {{ rvol }}
- SMA5: ${{ sma5_val }}  |  SMA20: ${{ sma20_val }}  |  SMA50: ${{ sma50_val }}  |  SMA200: ${{ sma200_val }}
- 볼린저밴드: 상단 ${{ bb_upper }} / 중앙 ${{ bb_mid }} / 하단 ${{ bb_lower }}
- MACD: {{ macd_line }} / 시그널 {{ macd_signal }} (히스토그램: {{ macd_hist }})
- ATR(14): ${{ atr }}  |  DI+: {{ di_plus }}  |  DI-: {{ di_minus }}
- 피벗: ${{ pivot }} | R1 ${{ pivot_r1 }} / R2 ${{ pivot_r2 }} | S1 ${{ pivot_s1 }} / S2 ${{ pivot_s2 }}
- 52주 고점 대비: {{ w52_high_pct }}%  |  저점 대비: {{ w52_low_pct }}%

## 기술 점수 요약
- MA 정배열: {{ ma_alignment }}
- ADX 점수: {{ adx_score }}/25  |  RSI 점수: {{ rsi_score }}/25
- MACD 점수: {{ macd_score }}/25  |  RVOL 점수: {{ rvol_score }}/25
- 신호 수: {{ signal_count }}/8 (신뢰도 {{ confidence_pct }}%)

## 작성 임무 (D안: 심층 분석)

**필수 작성 원칙:**
- 각 섹션은 반드시 3~5문장 이상으로 작성
- 위 실제 지표값의 구체적 숫자($XXX, XX.X 형식)를 각 섹션에 반드시 포함
- 단순 지표 나열 금지 — 지표 간 연관성과 시사점을 분석할 것
- {{ direction }} 방향성 관점에서 유리/불리를 명시할 것

1. **추세 분석**: SMA 배열, 현재가 위치, 상승/하락 추세의 구조적 근거를 3~5문장으로 서술. 단기(SMA5/20)와 중기(SMA50/200) 이격 수준, 정배열/역배열 의미 포함.
2. **모멘텀 분석**: RSI 현재 수준(과매수/중립/과매도 구간 해석), MACD 선과 시그널 관계, 히스토그램 방향을 3~5문장으로 서술. 모멘텀의 강화/약화 추세 판단 포함.
3. **추세 강도 및 변동성**: ADX 수준(25 미만/이상 기준), DI+/DI- 방향성, ATR 기반 일간 변동폭 의미를 3~5문장으로 서술. 현재가 대비 ATR 비율로 변동성 수준 평가.
4. **지지/저항 레벨**: 피벗 포인트(S1/S2/R1/R2)와 볼린저밴드 상/하단의 구체적 가격($XXX)을 언급하며 3~5문장으로 서술. 현재가와의 거리, 중요도 순위 포함.
5. **진입 타이밍 근거**: 지금 {{ direction }} 진입이 유리/불리한 이유를 기술적 근거로 3~4문장 서술. 이상적 진입 조건(어떤 신호가 더 나타나야 하는지) 포함.
6. **리스크 시나리오**: 기술적으로 이 설정이 무너지는 조건(어떤 가격 하향 돌파 시 추세 전환인지)을 3~4문장으로 서술. 단기(1~5일) 리스크와 스윙(5~15일) 리스크 구분.
7. **종합 기술 판단**: 위 6개 섹션을 통합하여 {{ direction }} 진입의 기술적 타당성을 4~5문장으로 종합 평가.

## 출력 형식 (JSON 엄수 — 마크다운 블록 금지)
{
  "trend_narrative": "추세 분석 3~5문장 (구체적 가격 포함)",
  "momentum_narrative": "모멘텀 분석 3~5문장 (RSI/MACD 수치 포함)",
  "volatility_narrative": "추세강도/변동성 분석 3~5문장 (ADX/ATR 수치 포함)",
  "support_resistance_narrative": "지지/저항 레벨 분석 3~5문장 (구체적 $가격 포함)",
  "entry_timing_rationale": "진입 타이밍 근거 3~4문장 (이상적 진입 조건 포함)",
  "risk_scenario_narrative": "리스크 시나리오 3~4문장 (무효화 가격 레벨 포함)",
  "overall_technical_narrative": "종합 기술 판단 4~5문장",
  "key_level_entry": 진입 기준 주가 (숫자, 피벗/BB 기반),
  "key_level_stop": 기술적 손절 주가 (숫자, S1 또는 BB 하단 기반),
  "key_level_target1": 1차 목표 주가 (숫자, R1 또는 BB 상단 기반),
  "key_level_target2": 2차 목표 주가 (숫자, R2 기반),
  "trend_outlook": "BULLISH|NEUTRAL|BEARISH",
  "near_term_bias": "BULLISH|NEUTRAL|BEARISH",
  "swing_bias": "BULLISH|NEUTRAL|BEARISH",
  "entry_quality": "Good|Fair|Poor"
}""",

    # ── Buy Step 4: 최종 순위 ──────────────────────────────────
    "buy_step4_ranking": """{{ role_lock }}

# 최종 매수 순위 결정

레짐: {{ regime_status }}
허용 방향: {{ allowed_direction }}
총 자본: ${{ total_capital }}
포지션당 한도: ${{ max_per_position }}

## 후보 종목 데이터
{% for t in candidates %}
### {{ t.ticker }}
- 기술 점수: {{ t.final_score }}
- 신호 수: {{ t.signal_count }}/8
- 방향: {{ t.direction }}
- 행사가: ${{ t.strike }}
- 만기: {{ t.expiry }}
- IVR: {{ t.ivr }}%
- 시나리오 EV: ${{ t.expected_value }}
- 포트폴리오 섹터: {{ t.sector }}
{% endfor %}

## 순위 기준
1순위: 신호 수 합계
2순위 (동점): R/R 비율 (EV / 최대손실)
3순위 (재동점): IVR 낮은 순

## 출력 형식 (JSON 엄수)
{
  "rankings": [
    {
      "rank": 1,
      "ticker": "XXX",
      "action": "진입|관찰|보류|탈락",
      "final_score": 숫자,
      "conviction_level": "high|medium|low",
      "capital_allocation": 달러,
      "contracts": 정수,
      "rationale": "근거",
      "risk_factors": []
    }
  ],
  "portfolio_summary": "포트폴리오 요약"
}""",

    # ── Sell Step 0: 시장 레짐 (매도용) ──────────────────────
    "sell_step0_market": """{{ role_lock }}

# 매도 파이프라인: 시장 레짐 분석

## 현재 시장
SPY: ${{ spy_price }} vs 20MA ${{ spy_ma20 }}
QQQ: ${{ qqq_price }} vs 20MA ${{ qqq_ma20 }}
VIX: {{ vix }}
ADX: {{ adx | default('N/A') }}

## 진입 시 레짐
진입 SPY: ${{ entry_spy | default('N/A') }}
진입 VIX: {{ entry_vix | default('N/A') }}

## 출력 형식 (JSON 엄수)
{
  "current_regime": "favorable|borderline|unfavorable",
  "regime_change": "improved|unchanged|deteriorated",
  "direction_still_valid": true|false,
  "key_changes": ["변화1", "변화2"]
}""",

    # ── Sell Step 1: 포지션 건전성 ────────────────────────────
    "sell_step1_health": """{{ role_lock }}

# 포지션 건전성 점검: {{ ticker }}

## 포지션 정보
- 유형: {{ option_type }}
- 행사가: ${{ strike }}
- 만기: {{ expiry }} (DTE: {{ dte }}일)
- 진입 프리미엄: ${{ entry_premium }}
- 현재 옵션 프리미엄: ${{ current_premium }}
- 현재 주가: ${{ current_stock_price }}
- 잔여 계약: {{ remaining_contracts }}

## 진입 근거
{{ entry_rationale }}

## 무효화 조건
{% for cond in invalidation_conditions %}
- {{ cond }}
{% endfor %}

## 임무
1. 각 무효화 조건의 충족 여부 판단 (유지|약화|무효)
2. DTE 긴급도 판단 (7일이하=위급, 8~14=주의, 15~21=보통, 21+= 안정)
3. 손익 귀인 분석 (델타/세타/베가)

## 출력 형식 (JSON 엄수)
{
  "ticker": "{{ ticker }}",
  "condition_checks": [{"condition": "조건", "status": "유지|약화|무효"}],
  "dte_urgency": "위급|주의|보통|안정",
  "flags": ["청산_권고_신호"|"주의_신호"|"근거_유효"],
  "pnl_attribution": {
    "delta_pnl": 달러,
    "theta_pnl": 달러,
    "vega_pnl": 달러,
    "total_estimated_pnl": 달러,
    "actual_pnl": 달러
  }
}""",

    # ── Sell Step 2: 진입 레짐 추론 + 현재 레짐 비교 ─────────
    "sell_step2_regime_infer": """{{ role_lock }}

# 진입 시 레짐 추론 및 현재 레짐 비교: {{ ticker }}

## 진입 논거 (positions.md에서 그대로 발췌)
### thesis
{{ thesis }}

### entry_rationale
{{ entry_rationale }}

## 현재 시장 레짐
- 레짐 상태: {{ current_regime }}
- SPY 추세: {{ spy_trend }}
- VIX: {{ vix }}
- ADX: {{ adx }}
- 레짐 신뢰도: {{ regime_confidence }}
- 리스크 요인: {{ risk_factors }}

## 분석 임무
1. **진입 시 레짐 추론**: thesis와 entry_rationale의 언어 패턴, 방향성(상승/하락), 시장 상태 가정(추세 지속, 변동성 낮음 등)을 분석하여 진입 당시 레짐을 추론하라.
   - 출력값: "bullish" | "bearish" | "neutral" | "unknown"
   - 근거: 어떤 문구/논리 패턴으로 판단했는지 명시
2. **현재 레짐 정당성**: 진입 논거의 핵심 전제(예: 추세 지속, 저변동성, AI 수요 증가 등)가 현재 레짐에서도 유효한지 평가
   - 유효 조건: 논거의 주요 전제 중 절반 이상이 아직 유효하면 "valid"
   - 출력값: "valid" | "partially_valid" | "invalid"
3. **레짐 역전 판단**: 추론된 진입 레짐과 현재 레짐을 비교하여 역전 여부 판단
   - 진입 bullish + 현재 bearish/unfavorable → REVERSED
   - 진입 bearish + 현재 bullish → REVERSED
   - 그 외 → OK

## 출력 형식 (JSON 엄수)
{
  "inferred_entry_regime": "bullish|bearish|neutral|unknown",
  "inference_basis": "추론 근거 1~2문장",
  "thesis_validity": "valid|partially_valid|invalid",
  "validity_reason": "유효/무효 판단 근거 1~2문장",
  "regime_comparison": "REVERSED|OK|UNKNOWN",
  "key_premise_check": [
    {"premise": "전제 내용", "status": "유효|무효|불확실"}
  ],
  "recommendation": "보유근거_유지|부분청산_고려|전량청산_고려"
}""",

    # ── Sell Step 2: 환경 이벤트 리스크 ──────────────────────
    "sell_step2_environment": """{{ role_lock }}

# 외부 환경 이벤트 리스크: {{ ticker }}

## 이벤트
{% for ev in events %}
- {{ ev.name }} (D-{{ ev.days_until }}, {{ ev.importance }})
{% endfor %}

## 옵션 상태
- IVR: {{ ivr }}%
- 만기까지 이벤트 수: {{ event_count }}개

## 분석 임무
1. 실적 발표 IV 확대/붕괴 리스크 추정
2. 거시 이벤트(FOMC/CPI) 영향 추정
3. 이벤트 종합 판정

## 출력 형식 (JSON 엄수)
{
  "event_judgment": "보유_유리|청산_유리|중립|혼조",
  "iv_crush_risk": true|false,
  "iv_crush_estimated_loss": 달러,
  "key_events": ["이벤트1", "이벤트2"],
  "recommendation": "설명"
}""",

    # ── Sell Step 3: 최종 행동 결정 ───────────────────────────
    "sell_step3_decision": """{{ role_lock }}

# 최종 행동 결정: {{ ticker }}

## 건전성 판정 결과
- 플래그: {{ flags }}
- DTE 긴급도: {{ dte_urgency }}
- 추세: {{ trend_status }}
- 자금 유입: {{ capital_flow }}

## 이벤트 판정
{{ event_judgment }}

## 뉴스 감성 분석
- 종합 감성: {{ overall_sentiment }}
- 뉴스 판정: {{ sentiment_verdict }}
- 강세 근거: {{ bull_thesis }}
- 약세 근거: {{ bear_thesis }}

## 기대값 (EV)
- 확률가중 기대손익: {{ expected_value }}
- EV가 음수인 경우 HOLD를 선택하려면 rationale에 반드시 음수 EV를 감수하는 구체적 근거를 포함해야 한다.

## 행동 우선순위 규칙
1. "청산_권고_신호" → FULL_EXIT 우선
2. DTE 7일 이하 → FULL_EXIT 또는 ROLL만 허용
3. 이벤트 "청산_유리" → FULL_EXIT 또는 PARTIAL_EXIT 우선
4. 추세 붕괴 + 자금 이탈 동시 → FULL_EXIT
5. 뉴스 감성이 포지션 방향과 반대 (Slight Bear/Negative) → PARTIAL_EXIT 고려
6. 위 조건 없음 → HOLD 또는 PARTIAL_EXIT

## HOLD 선택 시 필수 근거 기준
HOLD를 선택하려면 아래 중 **최소 1개 이상**을 rationale에 반드시 명시해야 한다:
- 추세 지속 확인 (trend_status = 확인됨 + capital_flow = 유입)
- 트레일링 스탑까지 여유가 20% 이상 남음
- Thesis가 모두 유효하고 뉴스 감성이 포지션 방향과 일치
- DTE 14일 이상으로 시간 가치 소멸 속도가 허용 범위 내

만약 기대값(EV)이 음수임에도 HOLD를 선택한다면, 그 이유(예: 추세 모멘텀, 스탑 여유, 단기 이벤트 해소 기대 등)를 rationale에 **반드시** 포함해야 한다.

## 출력 형식 (JSON 엄수)
{
  "ticker": "{{ ticker }}",
  "action": "HOLD|PARTIAL_EXIT|FULL_EXIT|ROLL",
  "contracts_to_close": 정수,
  "target_premium": 달러 or null,
  "roll_strike": 달러 or null,
  "roll_expiry": "YYYY-MM-DD" or null,
  "rationale": "근거 2~3문장 (HOLD 시 위 기준 반드시 포함)",
  "risk_factors": [],
  "urgency": "critical|warning|normal|stable"
}""",

    # ── Sell Step 4: 트레이드 복기 ────────────────────────────
    "sell_step4_review": """{{ role_lock }}

# 트레이드 복기 분석: {{ ticker }}

## 포지션 정보
- 유형: {{ option_type }}
- 진입 프리미엄: ${{ entry_premium }}
- 실현 손익: ${{ realized_pnl }}
- 보유 기간: {{ days_held }}일

## 진입 근거
{{ entry_thesis }}

## 분석 임무
1. 진입 thesis가 얼마나 정확했는지 평가 (accurate|partial|inaccurate)
2. 수익/손실의 주요 원인 식별
3. 반복할 패턴과 개선할 패턴 구분
4. 다음 유사 트레이드에서의 행동 변화 제안

## 출력 형식 (JSON 엄수)
{
  "ticker": "{{ ticker }}",
  "thesis_accuracy": "accurate|partial|inaccurate",
  "outcome": "profit|loss",
  "lesson": "핵심 교훈 한 문장",
  "what_worked": "잘 된 요소",
  "what_failed": "실패 요소",
  "pattern": "반복 패턴명 (예: sold_too_early, thesis_invalidated_early, held_too_long)",
  "improvement": "다음에 다르게 할 것 한 문장"
}""",

    # ── NL 라우팅 ──────────────────────────────────────────────
    "nl_routing": """{{ role_lock }}

# 자연어 명령 라우팅

## 사용자 명령
{{ query }}

## 현재 컨텍스트
{{ context | default('{}') }}

## 가능한 인텐트
- BUY_PIPELINE: 매수 분석 실행
- SELL_PIPELINE: 매도/청산 분석 실행
- POSITION_STATUS: 포지션 현황 조회
- REQUEUE_ADD: 대기 종목 등록
- REQUEUE_LIST: 대기 종목 목록 조회
- STEP_EXECUTE: 특정 단계 실행

## 출력 형식 (JSON 엄수)
{
  "intent": "인텐트명",
  "extracted_tickers": ["티커1"],
  "routing_confidence": 0.0~1.0,
  "routed_tool": "tool_name",
  "parameters": {},
  "role_lock_applied": true
}""",
}


# ─────────────────────────────────────────────────────────────
# 공개 API
# ─────────────────────────────────────────────────────────────

def render(template_name: str, **kwargs: object) -> str:
    """
    프롬프트 템플릿 렌더링

    Args:
        template_name: REGISTRY 내 템플릿 이름
        **kwargs: Jinja2 템플릿 변수

    Returns:
        렌더링된 프롬프트 문자열

    Raises:
        ValueError: 알 수 없는 템플릿 이름
        jinja2.UndefinedError: 템플릿 내 미정의 변수
    """
    tpl_str = _TEMPLATES.get(template_name)
    if not tpl_str:
        raise ValueError(
            f"Unknown template: '{template_name}'. "
            f"Available: {list(_TEMPLATES.keys())}"
        )

    # role_lock 자동 주입
    kwargs.setdefault("role_lock", ROLE_LOCK_SNIPPET)

    template = _env.from_string(tpl_str)
    return template.render(**kwargs)


def get_role_lock() -> str:
    """Role Lock 스니펫 반환"""
    return ROLE_LOCK_SNIPPET


def get_registry() -> dict:
    """전체 프롬프트 레지스트리 반환"""
    return REGISTRY.copy()


def get_model_for(template_name: str) -> str | None:
    """
    템플릿별 지정 모델을 반환. .env 한 곳에서 모든 모델을 관리.
    빈 문자열("")이면 None 반환 → call_llm()이 MODEL_PRIORITY 폴백 체인 전체 사용.

    변경 방법: .env 의 LLM_MODEL_* 값만 수정하면 즉시 반영.
    """
    from shared.config import get_config
    cfg = get_config()

    _TEMPLATE_TO_CFG: dict[str, str] = {
        "buy_step3_research":          cfg.LLM_MODEL_BUY_RESEARCH,
        "buy_step3b_technical_narrative": cfg.LLM_MODEL_BUY_TECH_NARRATIVE,
        "sell_step1_health":           cfg.LLM_MODEL_SELL_HEALTH,
        "sell_step2_regime_infer": cfg.LLM_MODEL_SELL_HEALTH,  # Step 2 레짐 추론
        "sell_step2_environment": cfg.LLM_MODEL_SELL_ENV,
        "sell_step3_decision":    cfg.LLM_MODEL_SELL_HEALTH,   # Step 10 최종 결정
        "sell_step4_review":      cfg.LLM_MODEL_SELL_HEALTH,   # Step 12 복기
        "nl_routing":             cfg.LLM_MODEL_NL_ROUTING,
    }

    model = _TEMPLATE_TO_CFG.get(template_name, "")
    return model or None  # 빈 문자열 → None → 폴백 체인 사용
