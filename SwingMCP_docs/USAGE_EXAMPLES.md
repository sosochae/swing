# SwingMCP v2.0.0 — 사용 예시 가이드

---

## 목차

1. [일반적인 워크플로우](#1-일반적인-워크플로우)
2. [자연어 명령 예시 (nl_query)](#2-자연어-명령-예시-nl_query)
3. [매수 파이프라인 사용 예시](#3-매수-파이프라인-사용-예시)
4. [매도 파이프라인 사용 예시](#4-매도-파이프라인-사용-예시)
5. [포지션 관리 예시](#5-포지션-관리-예시)
6. [Requeue 관리 예시](#6-requeue-관리-예시)
7. [펀더멘털 스크리닝 예시](#7-펀더멘털-스크리닝-예시)
8. [Kavout AI 스크리닝 예시](#8-kavout-ai-스크리닝-예시)
9. [단계별 수동 실행 예시](#9-단계별-수동-실행-예시)
10. [시스템 점검 예시](#10-시스템-점검-예시)
11. [일반적인 문제 해결](#11-일반적인-문제-해결)

---

## 1. 일반적인 워크플로우

### 1.1 매일 아침 루틴 (권장 순서)

```
1. health_check          → 시스템 연결 상태 확인
2. run_fundamental_screen → 관심 종목 펀더멘털 스크리닝
3. run_buy_pipeline       → 매수 분석 (summary_*.json 업데이트 후)
```

### 1.2 장 중 모니터링 루틴

```
1. run_sell_pipeline      → 보유 포지션 점검
2. position_status        → 특정 종목 현황 확인
3. requeue_list           → 대기 종목 확인
```

### 1.3 데이터 준비 → 파이프라인 실행 흐름

```
외부 데이터 수집
  └── Finviz Screener   → finviz_all_rows.txt 업데이트
  └── 옵션 체인 스크래퍼  → summary_*.json 업데이트
  └── Kavout CSV 다운로드 → kavout_YYYY-MM-DD.csv 저장

SwingMCP 실행
  └── run_buy_pipeline   → 14단계 분석 → Obsidian 노트 + Slack 알림
```

---

## 2. 자연어 명령 예시 (nl_query)

`nl_query` 도구는 자연어를 인식하여 적절한 파이프라인으로 라우팅합니다.

### 2.1 매수 분석 명령

```
사용자 입력: "매수 분석 실행해줘"
  → 인텐트: BUY_PIPELINE
  → 실행: engine.run_buy()

사용자 입력: "오늘 진입 후보 뽑아줘"
  → 인텐트: BUY_PIPELINE
  → 실행: engine.run_buy()

사용자 입력: "AAPL MSFT NVDA만 분석해줘"
  → 인텐트: BUY_PIPELINE
  → 실행: engine.run_buy(target_tickers=["AAPL", "MSFT", "NVDA"])

사용자 입력: "파이프라인 돌려줘"
  → 인텐트: BUY_PIPELINE
  → 실행: engine.run_buy()
```

### 2.2 매도 분석 명령

```
사용자 입력: "보유 포지션 점검해줘"
  → 인텐트: SELL_PIPELINE
  → 실행: engine.run_sell()

사용자 입력: "매도 분석 실행"
  → 인텐트: SELL_PIPELINE
  → 실행: engine.run_sell()

사용자 입력: "TSLA 청산 검토해줘"
  → 인텐트: SELL_PIPELINE
  → 실행: engine.run_sell(target_tickers=["TSLA"])

사용자 입력: "포지션 리뷰해줘"
  → 인텐트: SELL_PIPELINE
  → 실행: engine.run_sell()
```

### 2.3 포지션 현황 조회

```
사용자 입력: "AAPL 어떻게 됐어?"
  → 인텐트: POSITION_STATUS
  → 실행: engine.position_status("AAPL")

사용자 입력: "포지션 현황 보여줘"
  → 인텐트: POSITION_STATUS
  → 실행: engine.position_status(None)  # 전체
```

### 2.4 Requeue 관리

```
사용자 입력: "NVDA 대기열에 넣어줘"
  → 인텐트: REQUEUE_ADD
  → 실행: engine.requeue_add("NVDA", ...)

사용자 입력: "대기 종목 목록 보여줘"
  → 인텐트: REQUEUE_LIST
  → 실행: engine.requeue_list()

사용자 입력: "리큐 목록 확인"
  → 인텐트: REQUEUE_LIST
  → 실행: engine.requeue_list()
```

### 2.5 LLM 폴백 라우팅 예시

키워드에 없는 명령은 LLM이 인텐트를 판단합니다.

```
사용자 입력: "지금 들어가도 될까?"
  → LLM 분석: BUY_PIPELINE 또는 POSITION_STATUS
  → routing_confidence: 0.7

사용자 입력: "손절 고려 중인 종목 있어?"
  → LLM 분석: SELL_PIPELINE
  → extracted_tickers: []
```

---

## 3. 매수 파이프라인 사용 예시

### 3.1 전체 파이프라인 실행 (기본)

**도구**: `run_buy_pipeline`  
**파라미터**: `{}`

```
실행 흐름:
  Step 0: Obsidian 연결 확인 (FATAL)
  Step 1: summary_*.json + finviz + earnings + kavout + positions 로딩
  Step 2: VIX/ADX/SPY/QQQ 기반 레짐 분석
  Step 3: F1(RVOL) ~ F7(중복) 필터 적용
  Step 4: MA/ADX/RSI/RVOL 기술 점수 (병렬)
  Step 5: RSS + DDG 3쿼리 + Brave + LLM 감성 분석
  Step 6: IV Crush/Thesis/내부자/EPS 차감 + 40점 미만 탈락
  Step 7: 옵션 체인 → delta 0.4~0.7, DTE>=21 최적 계약
  Step 8: 3시나리오 확률 + EV 계산
  Step 9: 섹터 집중도/방향 편향 리스크
  Step 10: 확신도 점수 + 균형/공격 순위 생성
  Step 11: F1/F3 탈락 종목 Requeue 등록
  Step 12: Obsidian 매수 노트 + 탈락 노트 + watchlist.md
  Step 13: Slack 매수 결과 전송

출력 예시:
  ✅ 매수 파이프라인 완료 [buy_a1b2c3d4]
  레짐: favorable | 분석: 45개 | 통과: 8개 | 진입: 3개 | 관찰: 2개
  
  진입 후보 (균형):
  #1 NVDA 롱콜 $900 2026-07-18 | 점수: 87/100 | EV: $1,240 | 확신: HIGH
  #2 MSFT 롱콜 $420 2026-07-18 | 점수: 82/100 | EV: $890  | 확신: HIGH
  #3 AAPL 롱콜 $190 2026-06-20 | 점수: 76/100 | EV: $650  | 확신: MEDIUM
  
  Obsidian: swing-procedure/buy/2026-05-25.md
```

### 3.2 특정 종목만 분석

**도구**: `run_buy_pipeline`  
**파라미터**: `{"target_tickers": ["AAPL", "MSFT", "GOOGL"]}`

```
동작: Step 3 필터 이후 target_tickers에 없는 종목은 자동 제외
활용: 관심 종목을 빠르게 재분석할 때 사용
```

### 3.3 실행 ID 지정

**도구**: `run_buy_pipeline`  
**파라미터**: `{"execution_id": "morning_scan_20260525"}`

```
동작:
  - 스냅샷 디렉토리: shared/state/snapshots/morning_scan_20260525/
  - 감사 로그: 해당 execution_id로 기록
  - Idempotency: 동일 ID 재실행 시 완료된 step은 건너뜀
  
활용:
  - 특정 실행을 나중에 참조할 때
  - 중단된 파이프라인을 재개할 때
```

### 3.4 파이프라인 재개 (Idempotency)

```
# Step 7에서 중단된 경우
run_buy_pipeline({"execution_id": "morning_scan_20260525"})

# 자동으로 Step 0~6은 건너뛰고 Step 7부터 재개
# 스냅샷 파일: step_0.json ~ step_6.json 존재 확인 후 스킵
```

---

## 4. 매도 파이프라인 사용 예시

### 4.1 전체 포지션 점검

**도구**: `run_sell_pipeline`  
**파라미터**: `{}`

```
실행 흐름:
  Step 0: positions.md 파싱 + 시장 데이터 로딩
  Step 1: P&L 귀인(Delta/Theta/Vega) + DTE 긴급도 + 무효화 점검
  Step 2: 현재 레짐 vs 진입 시 레짐 비교 (역전 감지)
  Step 3: 기술 점수 갱신 + DDG 뉴스 + LLM 감성
  Step 4: LLM Thesis 검증 (무효화 조건 재판정)
  Step 5: LLM 이벤트 리스크 + IV Crush 위험
  Step 6: IVR > 70 + 어닝 DTE 이내 → IV Crush 경고
  Step 7: 7개 규칙 기반 예비 결정 (HOLD/PARTIAL/FULL/ROLL)
  Step 8: PARTIAL_EXIT 실행 (75%/50%/33%)
  Step 9: 잔여 포트폴리오 재집계
  Step 10: LLM 최종 결정 (sell_step3_decision)
  Step 11: Obsidian 매도 노트 + 포지션 캐시 저장
  Step 12: FULL_EXIT 트레이드 LLM 복기
  Step 13: Slack 매도 결과 + 스탑로스 알림

출력 예시:
  💰 매도 분석 완료 [sell_e5f6g7h8]
  
  AAPL [STABLE]:   HOLD         — 추세 확인됨, EV 양수
  MSFT [WARNING]:  PARTIAL_EXIT — 레짐 역전 감지, 75% 청산
  TSLA [CRITICAL]: FULL_EXIT    — DTE 5일, 스탑로스 도달
  
  실현 손익: +$1,840 (MSFT 부분) - $2,100 (TSLA 손절) = -$260
  Obsidian: swing-procedure/sell/2026-05-25.md
```

### 4.2 특정 포지션만 점검

**도구**: `run_sell_pipeline`  
**파라미터**: `{"target_tickers": ["TSLA"]}`

```
활용: TSLA가 급락하여 즉시 청산 판단이 필요할 때
동작: positions.md에서 TSLA 포지션만 추출하여 분석
```

### 4.3 매도 결정 해석

```
HOLD:         포지션 유지. 추세/EV 모두 양호.
PARTIAL_EXIT: 일부 청산. 리스크 축소 + 일부 수익 확정.
FULL_EXIT:    전량 청산. 손절 또는 목표 달성 또는 DTE 위급.
ROLL:         만기 연장. DTE <= 7 + 추세 확인됨 → 35일 후 금요일로 ROLL.

긴급도:
  [CRITICAL]: DTE <= 7 또는 스탑로스 도달 → 즉시 행동
  [WARNING]:  DTE 8~14 또는 레짐 역전 → 주의
  [NORMAL]:   DTE 15~21 → 모니터링
  [STABLE]:   DTE > 21 → 일반 관찰
```

---

## 5. 포지션 관리 예시

### 5.1 포지션 현황 조회

**도구**: `position_status`  
**파라미터**: `{"ticker": "AAPL"}`

```
출력 예시:
  AAPL 포지션 현황:
  - 유형: 롱콜 $185 2026-06-20
  - 진입: $4.50 (2026-05-10)
  - DTE: 26일 (보통)
  - 현재 주가: $188.50
  - 추정 미실현 손익: +$1,200 (진입가 기준)
  - 트레일링 스탑: $3.60 (고점 $4.80 × 75%)
  - 잔여 계약: 5계약
```

### 5.2 부분 청산 처리

**도구**: `partial_exit_apply`  
**파라미터**:
```json
{
  "ticker": "AAPL",
  "contracts_to_close": 2,
  "exit_premium": 6.80,
  "reason": "1차 익절 50% 수익 달성"
}
```

```
처리 흐름:
  1. positions.md에서 AAPL 포지션 로드
  2. 2계약 청산: (6.80 - 4.50) × 100 × 2 - 수수료 = $458.70
  3. remaining_contracts: 5 → 3
  4. trailing_stop 재설정: 6.80 × 0.80 = $5.44
  5. PartialExit 기록 추가 (exit_date, contracts, premium, pnl, reason)
  6. positions_state.json 캐시 저장

출력:
  ✅ AAPL 부분 청산 완료
  청산: 2계약 @ $6.80
  실현 손익: +$458.70
  잔여: 3계약 | 새 트레일링 스탑: $5.44
```

### 5.3 캐시 초기화

**도구**: `cache_clear`  
**파라미터**: `{"ticker": "AAPL"}`

```
동작: shared/cache/에서 AAPL 관련 캐시 파일 삭제
효과: 다음 AAPL 분석 시 LLM 재호출 (최신 뉴스 반영)
활용: AAPL 관련 중요 뉴스 발생 후 강제 새로고침
```

**도구**: `cache_clear`  
**파라미터**: `{}`

```
동작: shared/cache/ 전체 캐시 삭제
주의: 다음 파이프라인 실행 시 모든 LLM 재호출 → 시간/비용 증가
```

---

## 6. Requeue 관리 예시

### 6.1 종목 Requeue 등록

**도구**: `requeue_add`  
**파라미터**:
```json
{
  "ticker": "NVDA",
  "failed_filters": ["F1_RVOL_LOW"],
  "threshold": {
    "rvol_min": 1.5,
    "price_min": 20.0
  },
  "failure_reasons": ["현재 RVOL 1.2, 기준 1.5 미달"]
}
```

```
동작:
  1. requeue.json에 NVDA 추가 (status: "waiting")
  2. threshold 조건 함께 저장 (나중에 자동 체크)

출력:
  ✅ NVDA Requeue 등록 완료
  탈락 필터: F1_RVOL_LOW
  조건: RVOL >= 1.5 충족 시 자동 재분석
```

### 6.2 Requeue 목록 조회

**도구**: `requeue_list`  
**파라미터**: `{"status": "waiting"}`

```
출력 예시:
  Requeue 대기 목록 (3개):
  
  1. NVDA — 등록: 2026-05-22 | 탈락: F1_RVOL_LOW | 조건: RVOL >= 1.5
  2. AMD  — 등록: 2026-05-23 | 탈락: F3_LIQUIDITY_LOW | 조건: Price >= $20
  3. INTC — 등록: 2026-05-24 | 탈락: F1_RVOL_LOW | 조건: RVOL >= 1.5
```

**파라미터**: `{"status": "ready"}`

```
출력: 조건을 충족하여 재분석 준비된 종목
```

**파라미터**: `{}`

```
출력: waiting + ready + processed 전체 목록
```

### 6.3 Requeue 자동 파이프라인

`run_requeue` (내부 파이프라인):

```
Step 0: 환경 확인 + 데이터 로딩
Step 1: requeue.json에서 waiting 항목 로드
Step 2: 각 항목 threshold 조건 충족 여부 확인
        - RVOL 현재값 >= rvol_min?
        - 주가 >= price_min?
        → 조건 충족 시 status = "ready"
Step 3: ready 종목으로 BuyPipeline 실행 (target_tickers=ready_tickers)
Step 4: 처리 완료 → status = "processed"
```

---

## 7. 펀더멘털 스크리닝 예시

### 7.1 기본 실행

**도구**: `run_fundamental_screen`  
**파라미터**: `{}`

```
실행 흐름:
  Step 1: finviz_output/*.txt 파싱 → FinvizDetail 생성
  Step 2: 어닝_분석.md LLM 분석 → EarningsCallAnalysis 생성
  Step 3: Momentum + Fundamental + Catalyst 점수화 → 랭킹

출력 예시:
  ✅ 펀더멘털 스크리닝 완료 [screen_a1b2c3d4]
  유니버스: 156개 | 어닝콜 분석: 23개 | 소요: 45초
  
  Top 10:
  1. NVDA  92.3점  (M:95 F:88 C:94) | up/bullish
  2. MSFT  87.1점  (M:82 F:91 C:85) | flat/bullish
  3. META  84.5점  (M:88 F:79 C:87) | up/neutral
  4. GOOGL 81.2점  (M:79 F:85 C:0)
  5. AAPL  78.9점  (M:76 F:83 C:78) | flat/neutral
  ...
  
  Obsidian: swing-procedure/screener/2026-05-25.md
```

### 7.2 LLM 캐시 무시 (강제 새로고침)

**도구**: `run_fundamental_screen`  
**파라미터**: `{"force_refresh": true}`

```
활용: 어닝콜 분석 결과가 오래되었을 때
동작: 기존 캐시 무시 → 어닝_분석.md 전체 LLM 재분석
주의: LLM 호출 비용 증가 (종목 수 × 1회)
```

### 7.3 Top N 조정

**도구**: `run_fundamental_screen`  
**파라미터**: `{"top_n": 20}`

```
동작: 기본 10개 대신 상위 20개 보고서 생성
Obsidian 노트에도 Top 20 포함
```

### 7.4 펀더멘털 점수 해석

```
Total Score 90+: 탁월 — 모든 지표 강함, 즉시 관심 대상
Total Score 75~89: 우수 — 주요 지표 양호, 추가 분석 가치
Total Score 60~74: 보통 — 일부 약점 존재, 선택적 관심
Total Score < 60: 미흡 — 단기 스윙 진입 부적합

어닝 가이던스:
  up(↑):   긍정적 실적 전망 → Catalyst Score 높음
  flat(→): 유지 → 보통
  down(↓): 부정적 전망 → Catalyst Score 낮음

경영진 톤:
  bullish(🟢):  자신감 있는 코멘트
  neutral(🟡):  중립적
  bearish(🔴):  우려 표명
```

---

## 8. Kavout AI 스크리닝 예시

### 8.1 기본 실행

**도구**: `run_kavout_screen`  
**파라미터**: `{}`

```
실행 흐름:
  Step 1: kavout_*.csv (최신 파일 자동 탐색) + finviz_output/*.txt 파싱
          kavout_tickers 교집합 종목만 추출
  Step 2: K어닝 분석.md LLM 분석 (없으면 스킵)
  Step 3: 점수화 + 랭킹 + K-Score 표시

출력 예시:
  ✅ Kavout 스크리닝 완료 [kavout_x9y8z7w6]
  유니버스: 89개 | 어닝콜 분석: 15개 | 소요: 32초
  
  Top 10:
  1. NVDA  94.1점  K=8.5 (M:96 F:90 C:94) | up/bullish
  2. AMD   86.3점  K=7.2 (M:88 F:82 C:88) | up/bullish
  3. AVGO  83.7점  K=7.8 (M:85 F:85 C:0)
  ...
  
  Obsidian: swing-procedure/screener/kavout/2026-05-25.md
```

### 8.2 K-Score 해석

```
K-Score 1~9 스케일 (Kavout AI 자체 신호):
  8~9: 매우 강한 상승 신호 → swing_mcp Step 4에서 signal_count +1 보너스
  6~7: 강한 신호
  4~5: 중간
  2~3: 약한 신호
  1:   매우 약한 / 하락 신호

활용:
  K-Score >= 7 AND Momentum Score >= 70 → 강한 매수 후보
  K-Score <= 3 → 제외 검토
```

---

## 9. 단계별 수동 실행 예시

특정 step만 재실행하거나 디버깅할 때 사용합니다.

### 9.1 특정 Step 재실행

**도구**: `step_execute`  
**파라미터**: `{"pipeline_type": "buy", "step": 5, "execution_id": "morning_scan_20260525"}`

```
동작:
  1. 기존 스냅샷에서 Step 4까지의 컨텍스트 복원
  2. Step 5 (뉴스/리서치) 단독 실행
  3. 결과를 step_5.json 스냅샷으로 저장

활용:
  - Step 5 LLM 분석 결과가 이상할 때 재시도
  - 뉴스 내용이 업데이트되어 재분석 필요할 때
  - 특정 step의 실행 시간을 측정할 때
```

### 9.2 매도 Step 재실행

**도구**: `step_execute`  
**파라미터**: `{"pipeline_type": "sell", "step": 10, "execution_id": "sell_20260525"}`

```
동작: 최종 매도 결정 Step만 재실행
활용: Step 10 LLM 결정을 재시도 (다른 모델 폴백 이후 재실행 등)
```

### 9.3 건너뛰기 (Idempotency 우회)

```
# 동일 execution_id로 run_buy_pipeline 재실행
run_buy_pipeline({"execution_id": "morning_scan_20260525"})

# step_N.json 파일이 있는 step은 자동 건너뜀
# step_5.json 삭제 후 재실행하면 Step 5부터 재실행

# PowerShell에서 특정 스냅샷 삭제
Remove-Item "C:\MCP\Swing\shared\state\snapshots\morning_scan_20260525\step_5.json"
```

---

## 10. 시스템 점검 예시

### 10.1 swing_mcp 상태 확인

**도구**: `health_check`  
**파라미터**: `{}`

```
출력 예시:
  ✅ Obsidian API: 연결됨 (localhost:27123)
  ✅ OPENROUTER_API_KEY: 설정됨
  ✅ SLACK_BOT_TOKEN: 설정됨
  ✅ SUMMARY_DIR: 존재 (Y:\내 드라이브\Swing)
  ✅ FINVIZ_FILE: 존재 (finviz_all_rows.txt)
  ✅ LLM 캐시: 47개 파일
  ✅ 스냅샷: 12개 디렉토리
  ⚠️ BRAVE_API_KEY: 미설정 (DDG 폴백 사용)
```

### 10.2 screener_mcp 상태 확인

**도구**: `screener_health_check`  
**파라미터**: `{}`

```
출력 예시:
  ✅ finviz_output 폴더: 156개 파일
  ✅ 어닝_분석.md: 존재
  ✅ finviz_all_rows.txt: 존재
  ✅ Obsidian API: 연결됨
  ✅ Slack 토큰: 설정됨
  ✅ OpenRouter API 키: 설정됨
```

### 10.3 kavout_mcp 상태 확인

**도구**: `kavout_health_check`  
**파라미터**: `{}`

```
출력 예시:
  ✅ kavout_*.csv (최신): kavout_2026-05-25.csv
  ✅ Kavout 유니버스: 89개 종목
  ✅ finviz_output 폴더: 92개 파일
  ✅ K어닝 분석.md: 존재
  ⚠️ K어닝 분석_today.md: 없음 (선택)
  ⚠️ K어닝콜_output 폴더: 없음 (선택)
  ✅ Obsidian API: 연결됨
  ✅ Slack 토큰: 설정됨
  ✅ OpenRouter API 키: 설정됨
```

---

## 11. 일반적인 문제 해결

### 11.1 Obsidian 연결 실패

```
증상: Step 0 FATAL — "Obsidian REST API 응답 없음"
Slack 알림: E101 오류

해결:
  1. Obsidian 앱이 실행 중인지 확인
  2. Local REST API 플러그인이 활성화되어 있는지 확인
  3. 포트 27123이 방화벽에 허용되어 있는지 확인
  4. .env의 OBSIDIAN_API_KEY가 올바른지 확인
  5. OBSIDIAN_BASE_URL이 http://localhost:27123인지 확인
```

### 11.2 LLM 호출 실패 (모든 폴백 소진)

```
증상: Step 5/10 degraded — 뉴스 분석 없이 기본값 사용

해결:
  1. .env의 OPENROUTER_API_KEY 확인
  2. OpenRouter 크레딧 잔액 확인
  3. LLM_MODEL_PRIMARY 모델이 OpenRouter에서 사용 가능한지 확인
  4. 인터넷 연결 확인
  5. 폴백 모델에 무료 모델 추가:
     LLM_MODEL_FALLBACK2=deepseek/deepseek-chat-v3-0324:free
```

### 11.3 summary_*.json 없음

```
증상: Step 1 FATAL — "summary 파일 없음"

해결:
  1. SUMMARY_DIR 경로 확인: Y:\내 드라이브\Swing 존재하는지
  2. summary_YYYY-MM-DD.json 형식 파일이 있는지
  3. 파일 형식이 JSONL 3줄인지 확인
  4. JSON 파싱 오류: 파일 인코딩 UTF-8인지 확인
```

### 11.4 옵션 체인 없음 (Step 7)

```
증상: 모든 종목에서 옵션 유효 계약 없음

해결:
  1. summary_*.json 라인 2 (옵션 데이터)가 비어있지 않은지 확인
  2. 옵션 체인 스크래퍼가 최신 데이터를 기록했는지 확인
  3. delta 범위 조정 (더 넓히기):
     DELTA_MIN=0.35 (strategy.py)
  4. DTE 기준 낮추기:
     DTE_MIN=14 (strategy.py)
```

### 11.5 모든 종목 필터 탈락

```
증상: Step 3 후 passed_tickers 빈 리스트

해결:
  1. 레짐이 unfavorable인 경우 정상 동작 (시장 불리)
  2. RVOL_MIN 낮추기: 1.5 → 1.2 (strategy.py)
  3. finviz_all_rows.txt에 충분한 종목이 있는지 확인
  4. summary_*.json에 있는 티커와 finviz_all_rows.txt 티커가 매칭되는지 확인
```

### 11.6 Slack 알림 없음

```
증상: 파이프라인 완료 후 Slack 메시지 없음

해결:
  1. .env의 SLACK_BOT_TOKEN 확인 (xoxb- 로 시작)
  2. SLACK_CHANNEL_MAIN 채널명이 올바른지 확인 (#포함)
  3. Slack Bot이 해당 채널에 초대되어 있는지 확인
  4. Slack Bot에 chat:write 권한이 있는지 확인
  5. 토큰 없는 경우: 알림 비활성화 상태로 파이프라인은 정상 실행됨
```

### 11.7 Delta 범위 오류 (FinvizRow validator)

```
증상: ValidationError — ticker pattern 불일치

원인: Pydantic v2에서 field_validator의 mode="before" 누락 시
      소문자 ticker가 pattern 체크를 통과하지 못함

해결: shared/schemas.py:FinvizRow.ticker_upper
  @field_validator("ticker", mode="before")  # mode="before" 필수!
  @classmethod
  def ticker_upper(cls, v: str) -> str:
      return v.strip().upper()
```

### 11.8 감사 로그 확인

```powershell
# 오늘 감사 로그 조회
Get-Content "C:\MCP\Swing\shared\logs\audit_2026-05-25.json" | 
    ConvertFrom-Json | 
    Where-Object { $_.status -eq "failed" -or $_.status -eq "degraded" }
```

### 11.9 스냅샷 디렉토리 정리

```powershell
# 30일 이상 된 스냅샷 정리 (cleanup_old_snapshots 함수 직접 호출)
python -c "from core.state import cleanup_old_snapshots; print(cleanup_old_snapshots(30))"

# 특정 실행 ID 스냅샷 삭제 (재실행 준비)
Remove-Item -Recurse "C:\MCP\Swing\shared\state\snapshots\morning_scan_20260525"
```

---

## 부록: 주요 스키마 빠른 참조

### FinalRanking (매수 결과)

```json
{
  "rank": 1,
  "ticker": "NVDA",
  "direction": "long_call",
  "action": "진입",
  "strike": 900.0,
  "expiry": "2026-07-18",
  "contracts": 3,
  "capital_allocation": 4500.0,
  "conviction": {
    "level": "high",
    "score": 0.82,
    "rr_ratio": 3.1,
    "ivr": 45.0
  },
  "rationale": "강한 AI 수요 + 기술적 추세 확인됨",
  "risk_factors": [],
  "scenario": {...}
}
```

### SellDecision (매도 결과)

```json
{
  "ticker": "AAPL",
  "action": "PARTIAL_EXIT",
  "contracts_to_close": 2,
  "realized_pnl": 460.0,
  "unrealized_pnl": 680.0,
  "roll_strike": null,
  "roll_expiry": null,
  "rationale": "1차 익절 목표 달성, 나머지 보유",
  "risk_factors": [],
  "urgency": "normal"
}
```

### RequeueItem (대기 종목)

```json
{
  "ticker": "NVDA",
  "registered_at": "2026-05-22T09:15:00",
  "failed_filters": ["F1_RVOL_LOW"],
  "failure_reasons": ["RVOL 1.2 < 기준 1.5"],
  "threshold": {
    "rvol_min": 1.5,
    "price_min": 20.0
  },
  "status": "waiting"
}
```

---

*SwingMCP v2.0.0 — 생성일: 2026-05-25*
