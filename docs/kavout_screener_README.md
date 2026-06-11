# Kavout Screener — 완전 사용자 매뉴얼

> **대상 파일/폴더**: `scripts/fetch_kavout.py`, `scripts/run_kavout_screener.py`, `servers/kavout_mcp/server.py` 및 관련 core/shared 모듈

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [사전 준비 및 환경 설정](#2-사전-준비-및-환경-설정)
3. [전체 워크플로우](#3-전체-워크플로우)
4. [실행 방법 — 단계별](#4-실행-방법--단계별)
5. [명령어 레퍼런스](#5-명령어-레퍼런스)
6. [CLI 완전 레퍼런스 및 캐시 동작 명세](#55-cli-완전-레퍼런스-및-캐시-동작-명세)
7. [로컬 데이터 파일 및 경로](#57-로컬-데이터-파일-및-경로)
8. [기능 상세 설명](#6-기능-상세-설명)
9. [외부 API·데이터 소스 명세](#65-외부-api데이터-소스-명세)
10. [외부 서비스·MCP 연결 방식](#66-외부-서비스mcp-연결-방식)
11. [설정 파일 상세](#7-설정-파일-상세)
12. [고급 사용법 및 커스터마이징](#8-고급-사용법-및-커스터마이징)
13. [오류 해결 가이드](#9-오류-해결-가이드)
14. [수정 가이드](#95-수정-가이드)
15. [보고서 독해 가이드](#10-보고서-독해-가이드)
16. [보고서 데이터 소스 역추적 맵](#105-보고서-데이터-소스-역추적-맵)
17. [전체 파일·폴더 구조](#11-전체-파일폴더-구조)

---

## 1. 시스템 개요

Kavout Screener는 **Kavout AI 플랫폼의 Quality-Momentum 유니버스**를 기반으로 종목을 수집하고, Yahoo Finance API + K어닝콜 LLM 분석을 결합해 **펀더멘털·모멘텀·카탈리스트 복합 점수**로 종목을 랭킹하는 자동화 스크리닝 시스템입니다.

### 전체 파이프라인 (ASCII 다이어그램)

```
┌─────────────────────────────────────────────────────────────────────┐
│  [선행 단계 — 수동 실행]                                              │
│  fetch_kavout.py                                                     │
│  └─ Playwright(Chrome) → kavout.com 스크래핑                        │
│     └─ DATA_DIR/kavout_YYYYMMDD.csv 저장                            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  run_kavout_screener.py  (또는 MCP: run_kavout_screen)              │
│                                                                      │
│  Step 1: kavout_*.csv 파싱 + Yahoo Finance API 수집                  │
│          ├─ KavoutRow 유니버스 구성 (QMP + NTW 종목)                 │
│          └─ StockDetail (RSI·RVOL·SMA·펀더멘털) 수집               │
│                                                                      │
│  Step 2: K어닝 분석.md → LLM 분류                                    │
│          ├─ 가이던스 방향 (up/flat/down)                             │
│          ├─ 경영진 톤 (bullish/neutral/bearish)                      │
│          └─ 캐시 재사용 (--refresh-earnings 로 강제 갱신)            │
│                                                                      │
│  Step 3: 점수화 + 랭킹                                               │
│          ├─ 모멘텀 점수 (RSI·RVOL·52W·SMA·수익률)                  │
│          ├─ 펀더멘털 점수 (매출성장·EPS서프라이즈·마진)               │
│          ├─ 카탈리스트 점수 (가이던스·경영진 톤)                      │
│          └─ Kavout AI 점수 (stock_rank_score)                       │
│                                                                      │
│  출력: 터미널 보고서 + Obsidian 노트 + Slack 알림                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 주요 기능 요약 표

| 기능명 | 설명 | 소요 시간 |
|--------|------|----------|
| Kavout CSV 수집 | Playwright로 kavout.com 스크래핑 (QMP 최대 30종목 + NTW 최대 5종목) | 10~30분 |
| 종목 상세 수집 | 각 종목의 펀더멘털·기술 분석 페이지 스크래핑 | 포함 |
| Yahoo Finance 수집 | RSI·RVOL·SMA·가격·펀더멘털 실시간 API | 1~3분 |
| LLM 어닝 분석 | K어닝 분析.md → OpenRouter API → 가이던스/톤 분류 | 30~90초 |
| 점수화 + 랭킹 | 모멘텀·카탈리스트·AI 점수 합산 → 티어별 랭킹 | < 5초 |
| Obsidian 저장 | 상세 노트 자동 저장 | < 5초 |
| Slack 알림 | 티어별 Top 3 요약 메시지 전송 | < 5초 |

---

## 2. 사전 준비 및 환경 설정

### 시스템 요구사항

| 항목 | 요구사항 |
|------|---------|
| OS | Windows 10/11 (경로 하드코딩 포함) |
| Python | 3.12 이상 |
| RAM | 최소 8GB 권장 |
| Chrome | 실제 Chrome 설치 필요 (`channel="chrome"` Playwright 사용) |
| 네트워크 | kavout.com 접속 가능, OpenRouter API 접속 가능 |

### 패키지·의존성 설치

```powershell
cd C:\MCP\Swing
.venv\Scripts\pip install -e .
```

| 패키지 | 용도 |
|--------|------|
| `playwright` | Kavout 웹 스크래핑 (Chrome 자동화) |
| `playwright-stealth` | 봇 감지 우회 |
| `yfinance` | Yahoo Finance API (가격·기술지표·펀더멘털) |
| `numpy` | RSI·SMA 등 기술지표 계산 |
| `pydantic` | 데이터 스키마 검증 |
| `python-dotenv` | `.env` 환경 변수 로드 |
| `openai` | OpenRouter API 호출 (LLM) |
| `certifi` | SSL CA 인증서 (비ASCII 경로 대응) |
| `mcp` | MCP 서버 프로토콜 |

### Playwright 브라우저 설치

```powershell
.venv\Scripts\playwright install chromium
# 실제 Chrome 바이너리 필요 (channel="chrome") — 이미 설치되어 있어야 함
```

### 디렉터리 구조 준비

```
C:\MCP\Swing\
├── scripts\
│   ├── run_kavout_screener.py     ← 메인 CLI
│   ├── fetch_kavout.py            ← Kavout CSV 수집 CLI
│   └── kavout_chrome_profile\    ← Chrome 로그인 세션 (자동 생성)
├── shared\
│   ├── cache\                     ← LLM 응답 캐시 (자동 생성)
│   ├── state\snapshots\           ← 스냅샷 (자동 생성)
│   └── logs\                      ← 로그 (자동 생성)
└── servers\kavout_mcp\
    └── server.py                  ← MCP 서버

Y:\내 드라이브\
├── 어닝\
│   ├── K어닝 분析.md              ← 어닝콜 분析 파일 (수동 관리)
│   ├── K어닝 분析_today.md        ← 오늘 추가분 (선택, 수동 작성)
│   └── K어닝콜_output\            ← 어닝콜 원문 폴더 (선택)
└── Data\
    └── kavout_YYYYMMDD.csv       ← fetch_kavout.py가 생성
```

### .env 파일 설정

프로젝트 루트(`C:\MCP\Swing\.env`)에 생성:

```dotenv
# ── LLM (OpenRouter) ──────────────────────────────────────────────────
OPENROUTER_API_KEY=여기에_입력

# ── 어닝 분析 LLM 모델 ───────────────────────────────────────────────
LLM_MODEL_KAVOUT_EARNINGS=deepseek/deepseek-v4-flash

# ── 데이터 경로 ───────────────────────────────────────────────────────
EARNINGS_DIR=Y:\내 드라이브\어닝
DATA_DIR=Y:\내 드라이브\Data

# ── Obsidian ─────────────────────────────────────────────────────────
OBSIDIAN_API_KEY=여기에_입력
OBSIDIAN_BASE_URL=https://127.0.0.1:27124
OBSIDIAN_VAULT=C:\lian

# ── Slack ─────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN=xoxb-여기에_입력
SLACK_CHANNEL_MAIN=#swing-trading

# ── 캐시 ─────────────────────────────────────────────────────────────
CACHE_TTL_HOURS=24
```

| 항목 | 설명 | 필수 여부 |
|------|------|---------|
| `OPENROUTER_API_KEY` | LLM 어닝 분析용 API 키 | 필수 |
| `EARNINGS_DIR` | `K어닝 분析.md` 폴더 경로 | 필수 |
| `DATA_DIR` | `kavout_*.csv` 저장/탐색 폴더 | 필수 |
| `OBSIDIAN_API_KEY` | Obsidian Local REST API 키 | 선택 (없으면 노트 저장 실패만) |
| `SLACK_BOT_TOKEN` | Slack Bot Token | 선택 (없으면 알림만 실패) |
| `LLM_MODEL_KAVOUT_EARNINGS` | 어닝 분析 LLM 모델 | 선택 (기본: `deepseek/deepseek-v4-flash`) |

### API 키 발급 안내

**OpenRouter API Key**
1. [openrouter.ai](https://openrouter.ai) 가입
2. Dashboard → Keys → Create Key
3. `.env`의 `OPENROUTER_API_KEY`에 입력

**Obsidian Local REST API Key**
1. Obsidian → 설정 → Community Plugins → Local REST API 설치 및 활성화
2. 플러그인 설정에서 API Key 복사
3. `.env`의 `OBSIDIAN_API_KEY`에 입력

**Slack Bot Token**
1. [api.slack.com/apps](https://api.slack.com/apps) → Create App
2. OAuth & Permissions → `chat:write` 스코프 추가
3. Install to Workspace → Bot User OAuth Token 복사
4. `.env`의 `SLACK_BOT_TOKEN`에 입력

---

## 3. 전체 워크플로우

### 실행 환경 범례

| 레이블 | 설명 |
|--------|------|
| `[CLI]` | 터미널에서 직접 실행하는 명령어 |
| `[수동]` | 사람이 직접 작성·관리하는 파일 |
| `[자동]` | 코드가 자동으로 처리하는 단계 |
| `[MCP]` | Claude Code MCP 도구 호출 |

### 전체 흐름 다이어그램

```
[수동] Kavout.com 로그인 계정 준비
        │
        ▼
[CLI] fetch_kavout.py  ─────────────────────────────────────────────┐
  │   ├─ [자동] Chrome 브라우저 실행                                  │
  │   ├─ [수동] 첫 실행 시 Kavout 로그인 (이후 자동)                 │
  │   ├─ [자동] QMP 테이블 (최대 30행) 스크래핑                       │
  │   ├─ [자동] NTW 테이블 (최대 5행) 스크래핑                        │
  │   ├─ [자동] 종목별 상세 페이지 (stock-analysis, technical) 수집  │
  │   └─ [자동] DATA_DIR/kavout_YYYYMMDD.csv 저장                   │
        │                                                            │
        ▼                                                            │
[수동] K어닝 분析.md 작성/업데이트 (어닝콜 원문 요약 기록)           │
        │                                                            │
        ▼                                                            │
[CLI] run_kavout_screener.py  ─────────────────────────────────────┤
  │                                                                  │
  │   Step 1 [자동]                                                  │
  │     ├─ kavout_*.csv (최신) 파싱 → KavoutRow 유니버스            │
  │     └─ Yahoo Finance API → StockDetail 수집                     │
  │                                                                  │
  │   Step 2 [자동]                                                  │
  │     ├─ K어닝 분析.md 파싱 → EarningsAnalysis                    │
  │     └─ OpenRouter LLM → EarningsCallAnalysis (캐시 재사용)      │
  │                                                                  │
  │   Step 3 [자동]                                                  │
  │     ├─ 모멘텀·카탈리스트·Kavout AI 점수 계산                     │
  │     ├─ 티어별 그룹화 (대형주/중형주/소형주)                       │
  │     ├─ Obsidian 노트 저장                                        │
  │     └─ Slack 알림 전송                                           │
        │
[MCP] run_kavout_screen (kavout_mcp)  ← Claude Code에서 호출 가능  │
        └─────────────────────────────────────────────────────────────┘
```

---

## 4. 실행 방법 — 단계별

### 사전 단계: Kavout CSV 수집

#### 실행 명령어

```powershell
cd C:\MCP\Swing

# 기본 실행 (All Caps 유니버스)
.venv\Scripts\python scripts\fetch_kavout.py

# 특정 유니버스 지정
.venv\Scripts\python scripts\fetch_kavout.py --universe sp500
.venv\Scripts\python scripts\fetch_kavout.py --universe large-cap
```

#### 입력 → 처리 → 출력 표

| 입력 | 처리 모듈 | 출력 |
|------|----------|------|
| kavout.com (브라우저) | `fetch_kavout.py` | `DATA_DIR/kavout_YYYYMMDD.csv` |
| Chrome 프로파일 세션 | Playwright 자동화 | `scripts/kavout_before.png`, `kavout_after.png` |

#### 생성 파일 트리

```
Y:\내 드라이브\Data\
└── kavout_20260611.csv          ← 오늘 날짜 CSV

C:\MCP\Swing\scripts\
├── kavout_before.png            ← 스크래핑 전 스크린샷
├── kavout_after.png             ← 스크래핑 후 스크린샷
└── kavout_chrome_profile\      ← Chrome 세션 저장 (자동 관리)
```

---

### 메인 실행: Kavout 스크리닝

#### 실행 명령어

```powershell
cd C:\MCP\Swing

# 기본 실행 (어닝 LLM 캐시 재사용)
.venv\Scripts\python scripts\run_kavout_screener.py

# 어닝 LLM 새로 분析 (캐시 무시)
.venv\Scripts\python scripts\run_kavout_screener.py --refresh-earnings

# 상위 N개 출력 변경
.venv\Scripts\python scripts\run_kavout_screener.py --top 20

# 조합 사용
.venv\Scripts\python scripts\run_kavout_screener.py --refresh-earnings --top 15
```

#### 입력 → 처리 → 출력 표

| 입력 데이터 | 처리 모듈 | 출력 결과 |
|------------|----------|---------|
| `kavout_*.csv` (최신) | `core/parsers.py::parse_kavout_universe()` | `KavoutRow` 리스트 |
| Yahoo Finance API | `core/api_fetcher.py::fetch_stock_data_bulk()` | `StockDetail` 딕셔너리 |
| `K어닝 分析.md` | `core/parsers.py::parse_earnings()` | `EarningsAnalysis` 리스트 |
| EarningsAnalysis 텍스트 | `core/earnings_analyzer.py::analyze_earnings()` | `EarningsCallAnalysis` 딕셔너리 |
| StockDetail + EarningsCallAnalysis + KavoutRow | `core/fundamental_screener.py::rank_universe()` | `FundamentalScoreResult` 랭킹 리스트 |
| ScreenerResult | `run_kavout_screener.py::_format_obsidian_note()` | Obsidian 노트 (마크다운) |
| ScreenerResult | `run_kavout_screener.py::_format_slack_summary()` | Slack 메시지 |

#### 생성 파일 트리

```
Obsidian Vault (C:\lian)\
└── swing-procedure\screener\kavout\
    └── 2026-06-11.md           ← 오늘 날짜 스크리닝 결과 노트

C:\MCP\Swing\shared\
├── cache\
│   └── *.json                  ← LLM 응답 캐시 (ticker별)
└── logs\
    └── audit_YYYY-MM-DD.json   ← 실행 감사 로그
```

---

## 5. 명령어 레퍼런스

| 실행환경 | 단계 | 명령어 | 소요시간 | 주요 출력 |
|---------|------|--------|---------|---------|
| `[CLI]` | CSV 수집 | `.venv\Scripts\python scripts\fetch_kavout.py` | 10~30분 | `kavout_*.csv` |
| `[CLI]` | CSV 수집 (S&P500) | `... fetch_kavout.py --universe sp500` | 10~30분 | `kavout_*.csv` |
| `[CLI]` | 스크리닝 기본 | `.venv\Scripts\python scripts\run_kavout_screener.py` | 2~5분 | Obsidian 노트 + Slack |
| `[CLI]` | 스크리닝 (캐시 갱신) | `... run_kavout_screener.py --refresh-earnings` | 3~7분 | 위 동일 |
| `[CLI]` | 스크리닝 (Top 20) | `... run_kavout_screener.py --top 20` | 2~5분 | 위 동일 |
| `[MCP]` | 헬스체크 | `kavout_health_check` (도구 호출) | < 5초 | 연결 상태 텍스트 |
| `[MCP]` | MCP 스크리닝 | `run_kavout_screen` (도구 호출) | 2~5분 | Top N 텍스트 |

---

## 5.5 CLI 완전 레퍼런스 및 캐시 동작 명세

### `fetch_kavout.py` — Kavout CSV 수집

#### 옵션 전체 표

| 옵션 | 단축 | 타입/기본값 | 설명 |
|------|------|------------|------|
| `--universe` | 없음 | str / `all-caps` | 캡 필터 선택 |

**`--universe` 선택지:**

| 값 | 설명 |
|----|------|
| `all-caps` | 전체 시가총액 (기본) |
| `sp500` | S&P 500 |
| `large-cap` | 대형주 |
| `mid-cap` | 중형주 |
| `small-cap` | 소형주 |
| `russell1000` | Russell 1000 |

#### 캐시 재사용 동작 표

| 옵션 / 플래그 | 재사용되는 캐시 | 재생성되는 것 | 비고 |
|--------------|--------------|-------------|------|
| (기본 실행) | `scripts/kavout_chrome_profile/` (Chrome 세션) | `DATA_DIR/kavout_YYYYMMDD.csv` | CSV는 항상 새로 생성 |
| `--universe sp500` | Chrome 세션 | 새 CSV (파일명 동일, 내용 다름) | 이미 오늘 CSV가 있어도 덮어씀 |

> **주의**: `fetch_kavout.py`는 캐시 없음. 실행마다 항상 전체 스크래핑 수행. Chrome 세션(`kavout_chrome_profile/`)만 재사용.

---

### `run_kavout_screener.py` — 스크리닝

#### 옵션 전체 표

| 옵션 | 단축 | 타입/기본값 | 설명 |
|------|------|------------|------|
| `--refresh-earnings` | 없음 | flag / False | 어닝 LLM 캐시 무시하고 새로 분析 |
| `--top` | 없음 | int / `10` | 보고서 상위 N개 출력 |

#### 옵션별 캐시 재사용 동작 표

| 옵션 / 플래그 | 재사용되는 캐시 | 재생성되는 캐시 | 비고 |
|--------------|--------------|--------------|------|
| (기본, 캐시 있음) | `shared/cache/screener_earnings_{ticker}.json` | Yahoo Finance API 호출 | LLM 호출 없음 |
| `--refresh-earnings` | 없음 | `shared/cache/screener_earnings_{ticker}.json` | 모든 종목 LLM 재분析 |
| `--top N` | LLM 캐시 재사용 | 변화 없음 | 출력 행 수만 변경, 점수 계산에 영향 없음 |

**캐시 파일 위치**: `C:\MCP\Swing\shared\cache\`  
**캐시 키 형식**: `screener_earnings_{TICKER}` (날짜 구분 없음 — TTL은 `CACHE_TTL_HOURS`=24시간)

---

### `kavout_mcp` (MCP 서버) 도구

#### `run_kavout_screen` 파라미터

| 파라미터 | 타입/기본값 | 설명 |
|---------|-----------|------|
| `execution_id` | str / 자동생성 | 실행 ID (로그·노트 추적용) |
| `force_refresh` | bool / `false` | LLM 캐시 무시 여부 (`--refresh-earnings`와 동일) |
| `top_n` | int / `10` | 보고서 상위 N개 |

> **주의**: MCP 버전은 상위 60개 티커만 Yahoo Finance API에 요청 (`ticker_list[:60]` 하드코딩, [line 224](../servers/kavout_mcp/server.py:224)).  
> CLI 버전(`run_kavout_screener.py`)은 전체 티커 모두 요청.

---

## 5.7 로컬 데이터 파일 및 경로

### 읽기 전용 파일 (사용자가 준비하는 파일)

| 경로 | 파일 종류 | 용도 |
|------|----------|------|
| `{DATA_DIR}/kavout_*.csv` | CSV | Kavout 유니버스 (fetch_kavout.py가 생성) |
| `{EARNINGS_DIR}/K어닝 분析.md` | Markdown | 어닝콜 분析 원문 (수동 관리) |
| `{EARNINGS_DIR}/K어닝 분析_today.md` | Markdown | 오늘 추가분 어닝 (선택, 수동 관리) |

### 쓰기(자동 생성) 파일

| 경로 | 파일 종류 | 생성 시점 |
|------|----------|---------|
| `{DATA_DIR}/kavout_YYYYMMDD.csv` | CSV | `fetch_kavout.py` 실행 시 |
| `shared/cache/screener_earnings_{ticker}.json` | JSON | LLM 분析 완료 시 (첫 실행 또는 갱신) |
| `shared/logs/audit_YYYY-MM-DD.json` | JSON (JSONL) | 실행마다 |
| Obsidian: `swing-procedure/screener/kavout/YYYY-MM-DD.md` | Markdown | Step 3 완료 시 |
| `scripts/kavout_before.png` | PNG | `fetch_kavout.py` 실행 시 |
| `scripts/kavout_after.png` | PNG | `fetch_kavout.py` 실행 시 |

### 설정 변수 → 실제 기본값 대응 표

| 환경변수 | 기본값 | 실제 경로 의미 |
|---------|--------|--------------|
| `DATA_DIR` | `Y:\내 드라이브\Data` | kavout_*.csv 저장 폴더 |
| `EARNINGS_DIR` | `Y:\내 드라이브\어닝` | K어닝 분析.md 폴더 |
| `CACHE_DIR` | `C:\MCP\Swing\shared\cache` | LLM 캐시 폴더 |
| `LOGS_DIR` | `C:\MCP\Swing\shared\logs` | 감사 로그 폴더 |
| `CACHE_TTL_HOURS` | `24` | 캐시 유효 시간 (시간 단위) |

---

## 6. 기능 상세 설명

### 6.1 Kavout CSV 수집 (`fetch_kavout.py`)

```
[시작]
  │
  ├─ Chrome 프로파일 로드 (kavout_chrome_profile/)
  ├─ kavout.com/ai-stock-picker/quality-momentum?universe={param} 접속
  │
  ├─ 로그인 확인 (_LOGGED_IN_CHECK JS)
  │   ├─ [미로그인] → 콘솔 안내 출력 → 최대 5분 대기
  │   └─ [로그인됨] → 세션 재사용
  │
  ├─ NTW(New This Week) Show More 클릭 → 최대 5행 로드
  ├─ QMP(Quantitative Momentum Plus) 데이터 초기 추출
  │   └─ 30행 미만이면 Show More 클릭 → 최대 30행 로드
  │
  ├─ _EXTRACT_JS로 QMP·NTW 테이블 데이터 추출
  │   ├─ QMP: symbol, company, price, market_cap, momentum_1m, roe
  │   └─ NTW: symbol, company, momentum_1m, roe, entry_date
  │
  ├─ 종목 상세 페이지 수집 (_fetch_stock_details)
  │   ├─ /stock-analysis → 펀더멘털 지표 + 레이더 점수
  │   └─ /technical-analysis → MA/오실레이터 신호 + 게이지 점수
  │
  ├─ QMP: 시가총액 내림차순 정렬 + k_score 계산
  └─ CSV 저장 (DATA_DIR/kavout_YYYYMMDD.csv)
```

**k_score 계산 공식**:
```python
k_score = 9.0 - (rank_1based - 1) * 8.0 / (total - 1)
# 1위 → 9.0, 최하위 → 1.0
# NTW 종목: k_score = 0.0 (QMP 미진입 표시)
```

**조건 분기**:
- 로그인 타임아웃 5분 → `RuntimeError` 발생
- QMP 데이터 없음 → `RuntimeError` 발생 (`프로파일 초기화: kavout_chrome_profile\ 삭제 후 재실행` 안내)
- 종목 상세 페이지: nasdaq → nyse → nysearca 순으로 시도, 3개소 모두 실패 시 해당 필드 공백

---

### 6.2 점수화 알고리즘 (`core/fundamental_screener.py`)

```
[종목당 FundamentalScoreResult 생성]
│
├─ Momentum Score (0~100)
│   ├─ RSI(14) 구간 점수 (20%)     → 50~70: 100점, 40~50: 65점, >70~80: 55점, <40: 30점, >80: 20점
│   ├─ Relative Volume (20%)       → ≥2.0: 100점, ≥1.5: 70점, ≥1.2: 45점, <1.2: 20점
│   ├─ 52주 위치 (20%)             → 고점 5% 이내: 100점, 저점 100%+ 상승: 100점 (평균)
│   ├─ SMA20/50/200 위치 (20%)     → SMA200:40% + SMA50:35% + SMA20:25% 가중평균
│   └─ 멀티 기간 수익률 (20%)       → 12M×40% + 6M×35% + 3M×25% (Kavout 데이터 있을 때)
│       ※ krow 없으면: RSI·RVOL·52W·SMA 각 25%씩
│
├─ Fundamental Score (0~100)  ─── 노트 표시용, 순위 계산에서 제외
│   ├─ 매출 YoY 성장률 (40%)       → ≥50%: 100점, ≥25%: 80점, ≥10%: 60점
│   ├─ EPS 서프라이즈 % (25%)      → ≥15%: 100점, ≥5%: 80점, 없음: 50점(중립)
│   └─ 영업이익률 (35%)            → ≥25%: 100점, ≥15%: 80점, ≥8%: 60점
│
├─ Catalyst Score (0~100)  ─── 어닝콜 데이터 있는 경우만
│   ├─ 가이던스 방향 (60%)         → up: 100점, flat: 50점, down: 10점
│   └─ 경영진 톤 (40%)             → bullish: 100점, neutral: 55점, bearish: 15점
│
├─ Kavout AI Score  → stock_rank_score (0~100) 그대로 사용
│
└─ Total Score
    ├─ [Catalyst 있음] = M×0.50 + C×0.35 + K×0.15
    └─ [Catalyst 없음] = M×0.85 + K×0.15
```

---

### 6.3 티어별 분류 (`run_kavout_screener.py`)

```python
if mc is None or mc < $5B:   → 소형주 ($5B 미만 / 시총 미확인)
elif mc < $50B:              → 중형주 ($5B~$50B)
else:                        → 대형주 ($50B+)
```

시가총액 우선순위: Yahoo Finance API (`market_cap`) → Kavout CSV (`market_cap_raw`)

---

### 핵심 데이터 구조 예시

#### KavoutRow (한 종목의 Kavout 데이터)
```json
{
  "ticker": "AAPL",
  "company": "Apple Inc.",
  "price": 213.55,
  "market_cap_raw": 3200000000000.0,
  "momentum_1m": 8.3,
  "roe": 1.5,
  "k_score": 8.5,
  "section": "quantitative_momentum_plus",
  "stock_rank_score": 85.0,
  "quality_score": 78.0,
  "growth_score": 62.0,
  "momentum_score": 71.0,
  "value_score": 45.0,
  "ma_score_num": 73,
  "oscillator_score_num": 58,
  "technical_rating_num": 68,
  "ema10": "Bullish",
  "sma20": "Bullish",
  "rsi": "Neutral",
  "return_3m": 12.5,
  "return_6m": 18.2,
  "return_12m": 35.7
}
```

#### FundamentalScoreResult (점수화 결과)
```json
{
  "ticker": "AAPL",
  "rank": 1,
  "momentum_score": 72.5,
  "fundamental_score": 68.0,
  "catalyst_score": 85.0,
  "has_catalyst": true,
  "total_score": 78.4,
  "guidance_direction": "up",
  "mgmt_tone": "bullish",
  "k_score": 8.5,
  "kavout_rank_score": 85.0
}
```

---

## 6.5 외부 API·데이터 소스 명세

### Yahoo Finance (yfinance)

| 항목 | 내용 |
|------|------|
| 라이브러리 | `yfinance` |
| 인증 | 불필요 (무료 공개 API) |
| 요청 방식 | `Ticker(symbol).info` + `Ticker(symbol).history(period="1y")` |

| 추출 필드 | 소스 속성 | 설명 |
|---------|----------|------|
| `forward_pe` | `info["forwardPE"]` | Forward P/E |
| `peg` | `info["pegRatio"]` | PEG 비율 |
| `target_price` | `info["targetMeanPrice"]` | 애널리스트 목표주가 (평균) |
| `recom` | `info["recommendationMean"]` | 추천 등급 (1=Strong Buy~5=Sell) |
| `beta` | `info["beta"]` | 베타 |
| `op_margin_pct` | `info["operatingMargins"] × 100` | 영업이익률 % |
| `profit_margin_pct` | `info["profitMargins"] × 100` | 순이익률 % |
| `revenue_growth_yoy` | `info["revenueGrowth"] × 100` | 매출 YoY 성장률 % |
| `eps_surprise_pct` | `info["earningsSurprisePercent"]` | EPS 서프라이즈 % |
| `market_cap` | `info["marketCap"]` | 시가총액 (USD) |
| `price` | `history["Close"].iloc[-1]` | 현재가 |
| `rsi14` | 계산: Wilder RSI (14일) | RSI(14) |
| `rel_volume` | 계산: 오늘 거래량 / 직전 20일 평균 | 상대 거래량 |
| `sma20_pct` | 계산: (price - SMA20) / SMA20 × 100 | SMA20 대비 % |
| `sma50_pct` | 계산: (price - SMA50) / SMA50 × 100 | SMA50 대비 % |
| `sma200_pct` | 계산: (price - SMA200) / SMA200 × 100 | SMA200 대비 % |
| `w52_high_pct` | 계산: (price - 52주고) / 52주고 × 100 | 52주 고점 대비 % |
| `w52_low_pct` | 계산: (price - 52주저) / 52주저 × 100 | 52주 저점 대비 % |

**폴백 동작**: API 응답이 없거나 타임아웃 시 → 해당 필드 `None` 유지. Kavout CSV의 `price`로 가격만 보완.

---

### Kavout.com (Playwright 스크래핑)

| 항목 | 내용 |
|------|------|
| URL | `https://www.kavout.com/ai-stock-picker/quality-momentum?universe={param}&region=US` |
| 인증 | Kavout 계정 로그인 (최초 1회, 이후 Chrome 세션 재사용) |
| 브라우저 | 실제 Chrome (`channel="chrome"`, stealth 모드) |

| 추출 필드 | 소스 위치 | 추출 방법 |
|---------|----------|---------|
| symbol, company, price, market_cap | QMP 테이블 tbody | JavaScript DOM 추출 |
| momentum_1m, roe | QMP/NTW 테이블 컬럼 | JavaScript DOM 추출 |
| entry_date | NTW 테이블 컬럼 | JavaScript DOM 추출 |
| stock_rank_score | 레이더 차트 중앙 SVG `text.text-base` fill='#fff' | JavaScript 평가 |
| quality/growth/momentum/value_score | 레이더 차트 축 라벨 근처 SVG `text.text-xs` | 거리 기반 매핑 |
| ma_score_num, oscillator_score_num, technical_rating_num | 게이지 SVG `text.text-sm` fill='#fff' | DOM 순서 기반 |
| ema10, sma20, sma50, sma200, rsi, stochastic, macd, cci | 기술분析 카드 | JavaScript 카드 파싱 |
| roa, roic, debt_equity, pb_ratio 등 | /stock-analysis 페이지 | label:value 텍스트 추출 |

---

### OpenRouter API (LLM)

| 항목 | 내용 |
|------|------|
| 엔드포인트 | `https://openrouter.ai/api/v1` |
| 인증 | Bearer 토큰 (`OPENROUTER_API_KEY`) |
| 기본 모델 | `deepseek/deepseek-v4-flash` (`LLM_MODEL_KAVOUT_EARNINGS`) |
| 폴백 체인 | `LLM_PRIMARY_MODEL` → `LLM_FALLBACK_MODEL` → `LLM_FALLBACK_2` → `anthropic/claude-haiku-4-5` |

**요청 구조**:
```json
{
  "model": "deepseek/deepseek-v4-flash",
  "messages": [{"role": "user", "content": "[AAPL Q3]\n비즈니스 모델:\n...\n변화/전략:\n..."}],
  "temperature": 0.0,
  "max_tokens": 1024,
  "response_format": {"type": "json_object"}
}
```

**응답 추출 필드**:

| 필드 | 타입 | 의미 |
|------|------|------|
| `guidance_direction` | `"up"/"flat"/"down"/"unknown"` | 가이던스 방향 |
| `guidance_evidence` | str (≤120자) | 가이던스 판단 근거 원문 |
| `mgmt_tone` | `"bullish"/"neutral"/"bearish"` | 경영진 톤 |
| `tone_evidence` | str (≤120자) | 경영진 톤 판단 근거 원문 |
| `key_risks` | list[str] | 주요 리스크 목록 |
| `catalyst_strength` | int (1~5) | 카탈리스트 강도 |

**폴백**: LLM 호출 실패 시 키워드 기반 간이 분류 (`_fallback_from_text()`)

---

## 6.6 외부 서비스·MCP 연결 방식

### MCP 서버 (`kavout_mcp`) 연결 흐름

```
Claude Code (Claude Desktop / Roo Code)
        │
        │  stdio JSON-RPC
        ▼
servers/kavout_mcp/server.py
        │
        ├─ list_tools()  → run_kavout_screen, kavout_health_check
        └─ call_tool()
             ├─ run_kavout_screen
             │   ├─ parse_kavout_universe(DATA_DIR)
             │   ├─ fetch_stock_data_bulk(tickers[:60])
             │   ├─ analyze_earnings(K어닝 分析.md)
             │   ├─ rank_universe(...)
             │   ├─ obsidian.write_note(...)
             │   └─ slack._send(...)
             └─ kavout_health_check
                 ├─ find_latest_kavout_csv(DATA_DIR)
                 ├─ obsidian.ping()
                 └─ 환경변수 확인
```

### 단계별 데이터 형태

| 단계 | 데이터 형태 | 방향 |
|------|----------|------|
| Claude → MCP 서버 | JSON-RPC 도구 호출 (tool name + arguments) | → |
| 유니버스 파싱 | `list[KavoutRow]` | 내부 |
| API 수집 | `dict[str, StockDetail]` | ← (Yahoo Finance) |
| LLM 분析 | `dict[str, EarningsCallAnalysis]` | ← (OpenRouter) |
| 점수화 결과 | `list[FundamentalScoreResult]` | 내부 |
| Obsidian 저장 | HTTP POST (Local REST API) | → |
| Slack 알림 | HTTP POST (Slack API) | → |
| MCP 서버 → Claude | `list[TextContent]` 텍스트 요약 | → |

---

## 7. 설정 파일 상세

### 7.1 핵심 설정값

```python
# shared/config.py — 실제 기본값

# LLM 모델 (어닝 分析 전용)
LLM_MODEL_KAVOUT_EARNINGS = "deepseek/deepseek-v4-flash"  # .env로 변경 가능

# 데이터 경로
EARNINGS_DIR = r"Y:\내 드라이브\어닝"         # K어닝 분析.md 폴더
DATA_DIR     = r"Y:\내 드라이브\Data"          # kavout_*.csv 폴더

# 캐시
CACHE_TTL_HOURS = 24                           # LLM 캐시 유효 시간 (시간)

# 시가총액 티어 경계 (shared/strategy.py)
MCAP_LARGE_CAP = 50_000_000_000   # $50B 이상 → 대형주
MCAP_MID_CAP   =  5_000_000_000   # $5B~$50B  → 중형주

# 점수 가중치 (shared/strategy.py)
FSCORE_WEIGHT_MOMENTUM    = 0.50   # Catalyst 있을 때 모멘텀 가중치
FSCORE_WEIGHT_CATALYST    = 0.35   # Catalyst 가중치
FSCORE_WEIGHT_KAVOUT      = 0.15   # Kavout AI 점수 가중치
FSCORE_NO_CATALYST_MOMENTUM = 0.85 # Catalyst 없을 때 모멘텀 가중치
FSCORE_NO_CATALYST_KAVOUT   = 0.15 # Catalyst 없을 때 Kavout 가중치
```

### 7.2 사용자 커스터마이징 가능 항목 완전 목록

#### 📂 데이터 경로

| 항목 | 파일명 | 변수명 / 키 | 기본값 | 허용 범위 | 설명 |
|------|--------|------------|--------|----------|------|
| Kavout CSV 폴더 | `.env` | `DATA_DIR` | `Y:\내 드라이브\Data` | 유효한 경로 | kavout_*.csv 탐색 폴더 |
| 어닝 분析 폴더 | `.env` | `EARNINGS_DIR` | `Y:\내 드라이브\어닝` | 유효한 경로 | K어닝 분析.md 폴더 |
| Obsidian Vault | `.env` | `OBSIDIAN_VAULT` | `C:\lian` | 유효한 경로 | 노트 저장 기준 Vault |

#### 🤖 LLM 설정

| 항목 | 파일명 | 변수명 / 키 | 기본값 | 허용 범위 | 설명 |
|------|--------|------------|--------|----------|------|
| 어닝 分析 모델 | `.env` | `LLM_MODEL_KAVOUT_EARNINGS` | `deepseek/deepseek-v4-flash` | OpenRouter 모델 ID | K어닝 LLM 분析 모델 |
| LLM 타임아웃 | `.env` | `LLM_TIMEOUT_SECONDS` | `120` | 30~300 | LLM 호출 최대 대기 시간 (초) |
| 캐시 TTL | `.env` | `CACHE_TTL_HOURS` | `24` | 1~168 | LLM 응답 캐시 유효 시간 |

#### 📊 점수 가중치

⚠️ 연관 항목: 가중치 합이 1.0이 되어야 합니다.

| 항목 | 파일명 | 변수명 | 기본값 | 허용 범위 | 설명 |
|------|--------|--------|--------|----------|------|
| 모멘텀 가중치 (Catalyst 있음) | `shared/strategy.py` | `FSCORE_WEIGHT_MOMENTUM` | `0.50` | 0.0~1.0 | ⚠️ 연관: CATALYST, KAVOUT 합=1.0 |
| 카탈리스트 가중치 | `shared/strategy.py` | `FSCORE_WEIGHT_CATALYST` | `0.35` | 0.0~1.0 | ⚠️ 연관: MOMENTUM, KAVOUT 합=1.0 |
| Kavout AI 가중치 | `shared/strategy.py` | `FSCORE_WEIGHT_KAVOUT` | `0.15` | 0.0~1.0 | ⚠️ 연관: MOMENTUM, CATALYST 합=1.0 |
| 모멘텀 가중치 (Catalyst 없음) | `shared/strategy.py` | `FSCORE_NO_CATALYST_MOMENTUM` | `0.85` | 0.0~1.0 | ⚠️ 연관: NO_CATALYST_KAVOUT 합=1.0 |
| Kavout 가중치 (Catalyst 없음) | `shared/strategy.py` | `FSCORE_NO_CATALYST_KAVOUT` | `0.15` | 0.0~1.0 | ⚠️ 연관: NO_CATALYST_MOMENTUM 합=1.0 |

#### 📈 모멘텀 서브 가중치

| 항목 | 파일명 | 변수명 | 기본값 | 설명 |
|------|--------|--------|--------|------|
| RSI 가중치 | `shared/strategy.py` | `FSCORE_MOM_RSI_WEIGHT` | `0.20` | 모멘텀 점수 내 RSI 비중 |
| RVOL 가중치 | `shared/strategy.py` | `FSCORE_MOM_RVOL_WEIGHT` | `0.20` | 모멘텀 점수 내 RVOL 비중 |
| 52주 위치 가중치 | `shared/strategy.py` | `FSCORE_MOM_52W_WEIGHT` | `0.20` | 모멘텀 점수 내 52W 비중 |
| SMA 추세 가중치 | `shared/strategy.py` | `FSCORE_MOM_SMA_WEIGHT` | `0.20` | 모멘텀 점수 내 SMA 비중 |
| 멀티 수익률 가중치 | `shared/strategy.py` | `FSCORE_MOM_RETURN_WEIGHT` | `0.20` | 모멘텀 점수 내 수익률 비중 |

#### 🎯 RSI 점수 구간

| 항목 | 파일명 | 변수명 | 기본값 | 설명 |
|------|--------|--------|--------|------|
| RSI 이상 구간 하한 | `shared/strategy.py` | `FSCORE_RSI_IDEAL_MIN` | `50.0` | 이 값 이상 → RSI 100점 |
| RSI 이상 구간 상한 | `shared/strategy.py` | `FSCORE_RSI_IDEAL_MAX` | `70.0` | 이 값 이하 → RSI 100점 |
| RSI 허용 구간 하한 | `shared/strategy.py` | `FSCORE_RSI_OK_MIN` | `40.0` | 이 값 이상 → RSI 65점 |
| RSI 허용 구간 상한 | `shared/strategy.py` | `FSCORE_RSI_OK_MAX` | `80.0` | 이 값 이하 → RSI 55점 |

#### 💰 시가총액 티어 경계

| 항목 | 파일명 | 변수명 | 기본값 | 설명 |
|------|--------|--------|--------|------|
| 대형주 경계 | `shared/strategy.py` | `MCAP_LARGE_CAP` | `50_000_000_000` ($50B) | 이 값 이상 → 대형주 |
| 중형주 경계 | `shared/strategy.py` | `MCAP_MID_CAP` | `5_000_000_000` ($5B) | 이 값~대형주 → 중형주 |

#### 🌐 Kavout 유니버스

| 항목 | 파일명 | 변수명 | 기본값 | 허용 선택지 | 설명 |
|------|--------|--------|--------|----------|------|
| 유니버스 필터 | `scripts/fetch_kavout.py` CLI | `--universe` | `all-caps` | all-caps, sp500, large-cap, mid-cap, small-cap, russell1000 | Kavout 종목 필터 |

> **소스 파일 직접 편집이 필요한 항목**: MCP 서버(`servers/kavout_mcp/server.py:224`)의 `ticker_list[:60]` 제한 — 이를 변경하려면 해당 줄 수정 필요.

---

## 8. 고급 사용법 및 커스터마이징

### 어닝 LLM 모델 변경

`.env` 파일 수정:
```dotenv
LLM_MODEL_KAVOUT_EARNINGS=anthropic/claude-haiku-4-5
```

### 캐시 무효화 (특정 종목만)

```powershell
# 특정 종목 캐시 삭제 후 재실행
Remove-Item "C:\MCP\Swing\shared\cache\screener_earnings_AAPL.json"
.venv\Scripts\python scripts\run_kavout_screener.py
```

### 전체 캐시 초기화

```powershell
Remove-Item "C:\MCP\Swing\shared\cache\screener_earnings_*.json"
.venv\Scripts\python scripts\run_kavout_screener.py
```

### 점수 가중치 조정 예시

카탈리스트보다 AI 점수를 더 중시하고 싶다면 `shared/strategy.py` 수정:
```python
FSCORE_WEIGHT_MOMENTUM = 0.45
FSCORE_WEIGHT_CATALYST = 0.30
FSCORE_WEIGHT_KAVOUT   = 0.25   # 합: 1.00
```

### Kavout 로그인 세션 초기화

```powershell
Remove-Item -Recurse -Force "C:\MCP\Swing\scripts\kavout_chrome_profile"
.venv\Scripts\python scripts\fetch_kavout.py
# 브라우저가 열리면 Kavout 로그인 후 대기
```

### MCP 서버 등록 (Claude Desktop / Roo Code)

`claude_desktop_config.json` 또는 MCP 설정 파일에 추가:
```json
{
  "mcpServers": {
    "kavout-mcp": {
      "command": "C:\\MCP\\Swing\\.venv\\Scripts\\python",
      "args": ["C:\\MCP\\Swing\\servers\\kavout_mcp\\server.py"]
    }
  }
}
```

---

## 9. 오류 해결 가이드

### 오류 메시지 → 원인 → 해결 방법

| 오류 메시지 | 원인 | 해결 방법 |
|------------|------|---------|
| `FATAL — kavout_*.csv 파일 없음: ...` | `DATA_DIR`에 CSV 없음 | `fetch_kavout.py` 먼저 실행 |
| `로그인 타임아웃 (5분). 재실행하세요.` | Kavout 로그인 대기 초과 | 실행 후 5분 내 브라우저에서 로그인 |
| `Kavout QMP 데이터 없음 — 로그인 필요 또는 페이지 구조 변경` | 로그인 안 됨 또는 DOM 변경 | Chrome 세션 삭제 후 재로그인 |
| `Step3 실패 (점수화): ...` | 유니버스 데이터 불량 | CSV 파일 내용 확인 |
| `K어닝 分析.md 없음 — 카탈리스트 점수 제외` | `EARNINGS_DIR` 경로 오류 또는 파일 없음 | `.env`의 `EARNINGS_DIR` 확인 |
| `Obsidian 저장 실패: ...` | Obsidian 실행 안 됨 또는 API 키 오류 | Obsidian 앱 실행 + API 키 확인 |
| `Slack 전송 실패: ...` | Slack 토큰 만료 또는 채널 없음 | `SLACK_BOT_TOKEN` 재확인 |
| `UnicodeEncodeError` (SSL CA 관련) | 경로에 한글 포함 | 코드 첫 블록이 자동 처리 (cacert.pem 복사) |

### 디버깅 체크리스트

```
□ DATA_DIR에 오늘 날짜 kavout_*.csv 존재 확인
□ CSV 내 symbol 컬럼에 실제 티커 데이터 있는지 확인
□ EARNINGS_DIR\K어닝 분析.md 파일 존재 확인
□ .env 파일 존재 및 OPENROUTER_API_KEY 입력 확인
□ 캐시 파일 (shared/cache/) 오래된 경우 삭제 후 재실행
□ Obsidian 앱이 실행 중인지 확인 (Local REST API 플러그인 활성화)
□ scripts/kavout_after.png 확인 → 실제 데이터가 보이는지 시각 확인
```

---

## 9.5 수정 가이드

### 점수 가중치를 바꾸고 싶다면

**파일**: `shared/strategy.py`, 약 511~519행

```python
# 변경 전 (기본값)
FSCORE_WEIGHT_MOMENTUM    = 0.50
FSCORE_WEIGHT_CATALYST    = 0.35
FSCORE_WEIGHT_KAVOUT      = 0.15

# 변경 후 예시 (카탈리스트 중요도 낮춤)
FSCORE_WEIGHT_MOMENTUM    = 0.60
FSCORE_WEIGHT_CATALYST    = 0.25
FSCORE_WEIGHT_KAVOUT      = 0.15
# ⚠️ 세 값의 합이 반드시 1.00이어야 함
```

### 대형주/중형주 경계를 바꾸고 싶다면

**파일**: `shared/strategy.py`, 약 552~553행

```python
# 변경 전
MCAP_LARGE_CAP = 50_000_000_000   # $50B
MCAP_MID_CAP   =  5_000_000_000   # $5B

# 변경 후 예시 ($20B/$2B 경계)
MCAP_LARGE_CAP = 20_000_000_000
MCAP_MID_CAP   =  2_000_000_000
```

### LLM 분析 모델을 Claude로 바꾸고 싶다면

**파일**: `.env`

```dotenv
LLM_MODEL_KAVOUT_EARNINGS=anthropic/claude-haiku-4-5
```

### MCP 서버의 티커 수 제한을 올리고 싶다면

**파일**: `servers/kavout_mcp/server.py`, 224행

```python
# 변경 전
ticker_list = sorted(kavout_tickers)[:60]

# 변경 후 (전체 티커)
ticker_list = sorted(kavout_tickers)
# ⚠️ API 과부하 및 속도 저하 위험
```

### RSI 이상 구간을 변경하고 싶다면 (예: 60~75 선호)

**파일**: `shared/strategy.py`, 약 541~544행

```python
FSCORE_RSI_IDEAL_MIN = 60.0   # 변경 전: 50.0
FSCORE_RSI_IDEAL_MAX = 75.0   # 변경 전: 70.0
```

### 가이던스 점수 기준을 바꾸고 싶다면

**파일**: `core/fundamental_screener.py`, 약 314행

```python
# 변경 전
_GUIDANCE_SCORE = {"up": 100.0, "flat": 50.0, "down": 10.0, "unknown": 40.0}

# 변경 후 예시 (flat에 더 낮은 점수 부여)
_GUIDANCE_SCORE = {"up": 100.0, "flat": 30.0, "down": 10.0, "unknown": 40.0}
```

---

## 10. 보고서 독해 가이드

### 10.1 보고서별 목적 및 구조 한눈에 보기

| 보고서명 | 생성 시점 | 주요 독자 목적 | 파일 경로 |
|---------|---------|--------------|---------|
| 터미널 보고서 | Step 3 완료 즉시 | 빠른 랭킹 확인 | (터미널 출력) |
| Obsidian 스크리닝 노트 | Step 3 완료 후 | 상세 분析·보관 | `swing-procedure/screener/kavout/YYYY-MM-DD.md` |
| Slack 요약 | Step 3 완료 후 | 모바일 빠른 확인 | `#swing-trading` 채널 |

---

### 10.2 보고서별 섹션 독해 가이드

#### [터미널 보고서]

**"어떤 종목이 상위권인지 빠르게 확인하고 싶다면"** → 티어별 테이블을 본다.

| 알고 싶은 것 | 보고서 내 위치(컬럼명) | 해석 방법 |
|-------------|---------------------|---------|
| 최종 순위 | `티커` 앞 번호 | 티어(대/중/소형주) 내 순위 |
| 종합 점수 | `점수` 컬럼 | 0~100점, 높을수록 우수 |
| Kavout QMP 점수 | `K` 컬럼 | 0~9 (9=시총 1위, 1=최하위). `None`=NTW 전용 |
| Kavout AI Stock Rank | `SR` 컬럼 | 0~100 AI 종합 평가 |
| 시가총액 | `시총` 컬럼 | T=조, B=십억, M=백만 달러 |
| 모멘텀 점수 | `M` 컬럼 | 0~100 (RSI·RVOL·52W·SMA·수익률) |
| 펀더멘털 점수 | `F` 컬럼 | 0~100 (노트 표시용, 순위에 영향 없음) |
| 카탈리스트 점수 | `C` 컬럼 | 0~100 (어닝콜 있으면 계산, 없으면 0) |
| 현재가 | `가격` 컬럼 | `$` 단위 |
| RSI | `RSI` 컬럼 | 14일 RSI. 50~70=이상, >80=과매수 |
| 가이던스 방향 | `가이던스` 컬럼 | `↑상향` / `→유지` / `↓하향` |
| 경영진 톤 | `톤` 컬럼 | `🟢강세` / `🟡중립` / `🔴약세` |

---

#### [Obsidian 스크리닝 노트]

**"특정 종목의 투자 근거 전체를 확인하고 싶다면"** → 해당 종목의 `### N. TICKER — 점수` 블록을 본다.

| 알고 싶은 것 | 보고서 내 위치(섹션/필드명) | 해석 방법 |
|-------------|--------------------------|---------|
| 종목 요약 한줄 | `### N. TICKER — X.X점  (K=N | SR=N | 시총 $NB | 방향 | 톤)` | 제목 한 줄에 핵심 요약 |
| 회사명 | 제목 아래 이탤릭체 `*회사명*` | — |
| 비즈니스 모델 | **📌 비즈니스 모델** 섹션 | 어닝콜 분析 원문 |
| 인더스트리 특성 | **🏭 인더스트리** 섹션 | 어닝콜 분析 원문 |
| 전략·가이던스 변화 | **🔀 전략·변화 (가이던스 근거)** 섹션 | 어닝콜 분析 원문 |
| 경영진 자신감 발언 | **💬 경영진 톤 근거** 섹션 | 어닝콜 분析 원문 |
| LLM 판단 근거 | **🔍 LLM 판단 근거 (원문 인용)** 섹션 | LLM이 인용한 실제 발언 |
| 가이던스 LLM 근거 | `📈 가이던스 [방향]: 인용문` | `↑상향`/`→유지`/`↓하향` + 근거 |
| 경영진 톤 LLM 근거 | `🗣️ 경영진 톤 [톤]: 인용문` | `Bullish`/`Neutral`/`Bearish` + 근거 |
| 주요 리스크 | **⚠️ 주요 리스크**: | 쉼표로 구분된 리스크 목록 |
| 기술지표 스냅샷 | **📊 기술적 스냅샷** 테이블 | 가격·등락·RSI·RVOL·SMA |
| 밸류에이션 | **💰 밸류에이션 & 애널리스트** 테이블 | Fwd PE·PEG·Beta·목표가·추천 |
| 펀더멘털 | **📈 펀더멘털** 테이블 | 영업이익률·순이익률·매출성장·EPS서프 |
| Kavout AI 점수 | **🤖 Kavout AI 점수** 테이블 | Stock Rank·Quality·Growth·Momentum·Value |
| Kavout 기술 분析 | **📡 Kavout 기술 分析** 테이블 | MA Score·Oscillator Score·Technical Rating |
| 이평선 신호 | 두 번째 테이블 (EMA10~SMA200) | Bullish/Bearish/Neutral |
| 오실레이터 신호 | 세 번째 테이블 (RSI~CCI) | Bullish/Bearish/Neutral |
| Kavout 펀더멘털 | **📋 Kavout 펀더멘털 상세** 테이블 | ROA·ROIC·D/E·EV/EBITDA 등 |
| 다기간 수익률 | 마지막 테이블 (1주~12개월) | 기간별 % 수익률 |
| 점수 분해 | **🏆 점수**: 모멘텀 N \| 펀더멘털 N \| 카탈리스트 N \| **합계 N** | 각 점수 성분 확인 |

**수치 해석 기준**:

| 지표 | 단위 | 정상 범위 |
|------|------|---------|
| RSI(14) | 0~100 | 50~70: 이상적, >80: 과매수 경고 |
| RVOL | 배수 | ≥1.5: 양호, ≥2.0: 강함 |
| SMA20/50/200 | % (가격 대비) | 양수: SMA 위 (추세 강함) |
| k_score | 0~9 | ≥7: 상위 종목, <3: 하위 종목 |
| Stock Rank | 0~100 | ≥70: 우수, <30: 약세 |
| MA Score / Oscillator Score / Technical Rating | 0~100 | ≥70: 강한 신호 |

---

#### [Slack 요약]

**"알림 메시지에서 빠르게 상황 파악하고 싶다면"** → 티어별 Top 3를 본다.

| 알고 싶은 것 | 위치 | 해석 |
|-------------|------|------|
| 스크리닝 일시 | `📊 Kavout 스크리닝 — YYYY-MM-DD` | 실행 날짜 |
| 유니버스 크기 | `유니버스 N개` | 총 분析 종목 수 |
| 어닝콜 보유 수 | `어닝콜 N개` | LLM 分析된 종목 수 |
| 티어별 1위 | `🥇 \`TICKER\` $NB — N.N점 ↑/→/↓ K=N` | 가장 높은 점수 종목 |

---

## 10.5 보고서 데이터 소스 역추적 맵

### Obsidian 스크리닝 노트 기준

| 보고서 내 항목 | 데이터 소스 종류 | 로컬 경로 또는 API | 소스 내 구체적 필드·키 | 가공 방식 |
|--------------|---------------|------------------|--------------------|---------|
| 종목 목록 (유니버스) | 로컬 CSV | `{DATA_DIR}/kavout_*.csv` | `symbol` 컬럼 | 최신 파일 자동 탐색 |
| k_score (K열) | 로컬 CSV | `{DATA_DIR}/kavout_*.csv` | `k_score` 컬럼 | 그대로 (fetch_kavout.py 계산값) |
| Stock Rank (SR열) | 로컬 CSV | `{DATA_DIR}/kavout_*.csv` | `stock_rank_score` 컬럼 | 그대로 (Kavout SVG 추출값) |
| 시가총액 | API (우선) → CSV (폴백) | Yahoo Finance `info["marketCap"]` → `market_cap_raw` | USD float | 그대로 |
| 현재가 | API (우선) → CSV (폴백) | Yahoo Finance `history` 종가 → `price` | USD float | 최근 종가 |
| RSI(14) | API 계산 | Yahoo Finance 1년 일봉 | Close 시리즈 | Wilder RSI 직접 계산 |
| RVOL | API 계산 | Yahoo Finance 1년 일봉 | Volume 시리즈 | 오늘 / 직전 20일 평균 |
| SMA20/50/200 % | API 계산 | Yahoo Finance 1년 일봉 | Close 시리즈 | (price - SMA) / SMA × 100 |
| 등락 (change) | API | Yahoo Finance `info["regularMarketChangePercent"]` | float | × 100 |
| Forward PE | API | Yahoo Finance `info["forwardPE"]` | float | 그대로 |
| PEG | API | Yahoo Finance `info["pegRatio"]` | float | 그대로 |
| Beta | API | Yahoo Finance `info["beta"]` | float | 그대로 |
| 목표주가 | API | Yahoo Finance `info["targetMeanPrice"]` | float | 그대로 |
| 추천 등급 (Recom) | API | Yahoo Finance `info["recommendationMean"]` | float (1~5) | 그대로 |
| 애널리스트 B/H/S | API | Yahoo Finance `info["numberOfAnalystOpinions"]` 관련 | int 집계 | ⚠️ 소스 불명확 — 코드 확인 필요 |
| 영업이익률 | API | Yahoo Finance `info["operatingMargins"]` | float | × 100 → % |
| 순이익률 | API | Yahoo Finance `info["profitMargins"]` | float | × 100 → % |
| 매출성장 YoY | API | Yahoo Finance `info["revenueGrowth"]` | float | × 100 → % |
| EPS 서프라이즈 | API | Yahoo Finance 어닝 서프라이즈 | float | 그대로 |
| ROE (API) | API | Yahoo Finance `info["returnOnEquity"]` | float | × 100 → % |
| FCF(TTM) | API | Yahoo Finance 재무제표 | float | M USD 단위 |
| 비즈니스 모델 | 로컬 MD | `{EARNINGS_DIR}/K어닝 分析.md` | `### 1. 비즈니스 모델` 섹션 | 원문 그대로 |
| 인더스트리 | 로컬 MD | `{EARNINGS_DIR}/K어닝 分析.md` | `### 2. 인더스트리` 섹션 | 원문 그대로 |
| 전략·변화 | 로컬 MD | `{EARNINGS_DIR}/K어닝 分析.md` | `### 3. 변화/전략` 섹션 | 원문 그대로 |
| 경영진 톤 원문 | 로컬 MD | `{EARNINGS_DIR}/K어닝 分析.md` | `### 4. 자신감 표현` 섹션 | 원문 그대로 |
| 가이던스 방향 + 근거 | LLM 分析 | OpenRouter API | `guidance_direction`, `guidance_evidence` 응답 필드 | LLM 추론 결과 + 캐시 |
| 경영진 톤 + 근거 | LLM 分析 | OpenRouter API | `mgmt_tone`, `tone_evidence` 응답 필드 | LLM 추론 결과 + 캐시 |
| 주요 리스크 | LLM 分析 | OpenRouter API | `key_risks` 응답 필드 | LLM 추론 결과 + 캐시 |
| Kavout AI Score (Quality/Growth/Momentum/Value) | 로컬 CSV | `{DATA_DIR}/kavout_*.csv` | `quality_score`, `growth_score` 등 컬럼 | 그대로 (Kavout SVG 추출) |
| MA Score / Oscillator Score / Technical Rating | 로컬 CSV | `{DATA_DIR}/kavout_*.csv` | `ma_score_num`, `oscillator_score_num`, `technical_rating_num` | 그대로 |
| EMA10/SMA20/SMA50/SMA200 신호 | 로컬 CSV | `{DATA_DIR}/kavout_*.csv` | `ema10`, `sma20`, `sma50`, `sma200` | 그대로 (Bullish/Bearish/Neutral) |
| RSI/Stochastic/MACD/CCI 신호 | 로컬 CSV | `{DATA_DIR}/kavout_*.csv` | `rsi`, `stochastic`, `macd`, `cci` | 그대로 |
| ROA/ROIC/D/E/Current Ratio 등 | 로컬 CSV | `{DATA_DIR}/kavout_*.csv` | `roa`, `roic`, `debt_equity`, `current_ratio` 등 | 그대로 (Kavout /stock-analysis) |
| 성장률 (Rev/EPS/EBITDA) | 로컬 CSV | `{DATA_DIR}/kavout_*.csv` | `rev_growth_1y`, `eps_growth_1y` 등 | 그대로 |
| 수익률 (1W~12M) | 로컬 CSV | `{DATA_DIR}/kavout_*.csv` | `return_1w`, `return_1m` 등 | 그대로 |
| 모멘텀/펀더멘털/카탈리스트/합계 점수 | 계산 | `core/fundamental_screener.py` | 위 데이터들의 가중 조합 | 공식 계산 |

---

## 11. 전체 파일·폴더 구조

```
C:\MCP\Swing\
│
├── scripts\
│   ├── run_kavout_screener.py       # 메인 스크리닝 CLI (3단계 파이프라인)
│   ├── fetch_kavout.py              # Kavout CSV 수집 CLI (Playwright 스크래핑)
│   ├── _kavout_stock_pages.py       # 임시 스크린샷·텍스트 추출 스크립트 (개발용)
│   └── kavout_chrome_profile\       # Chrome 로그인 세션 저장 (자동 관리)
│
├── servers\
│   └── kavout_mcp\
│       └── server.py                # MCP 서버 (Claude Code 연동 — run_kavout_screen, kavout_health_check)
│
├── core\
│   ├── api_fetcher.py               # Yahoo Finance API 수집 + 기술지표 계산
│   ├── earnings_analyzer.py         # 어닝 MD → LLM → EarningsCallAnalysis 변환
│   ├── fundamental_screener.py      # 모멘텀·펀더멘털·카탈리스트·AI 점수화 + 랭킹
│   ├── obsidian.py                  # Obsidian Local REST API 클라이언트
│   ├── slack.py                     # Slack API 클라이언트
│   ├── llm.py                       # OpenRouter LLM 호출 + 캐시 관리
│   ├── parsers.py                   # 파일 파서 통합 (CSV·MD·JSON 파싱)
│   ├── analysis.py                  # 기타 분析 유틸리티
│   └── state.py                     # 상태 관리
│
├── shared\
│   ├── config.py                    # 환경 변수 기반 설정 (get_config 싱글톤)
│   ├── schemas.py                   # Pydantic v2 스키마 전체 (KavoutRow, StockDetail 등)
│   ├── strategy.py                  # 전략 파라미터 단일 소스 (점수 가중치, 임계값)
│   ├── logger.py                    # structlog 기반 로거
│   ├── prompts.py                   # LLM 시스템 프롬프트 저장
│   ├── cache\                       # LLM 응답 캐시 (screener_earnings_*.json)
│   ├── state\snapshots\             # 실행 스냅샷
│   ├── logs\                        # 감사 로그 (audit_*.json)
│   └── rss_feeds.json               # RSS 피드 설정
│
├── .env                             # API 키·경로 환경 변수 (gitignore 대상)
├── pyproject.toml                   # 패키지 의존성
└── docs\
    └── kavout_screener_README.md    # 이 문서
```

---

*최종 생성: 2026-06-11*
