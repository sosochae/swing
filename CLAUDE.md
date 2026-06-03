# SwingMCP — Claude 작업 지침

## 코드 수정 규칙 (최우선)

코드의 동작·방향이 바뀌는 수정은 반드시 사용자 확인을 받은 후 실행한다.
설명만 하고 기다린다. 허락 없이 Edit/Write 하지 않는다.

## 기능·동작 파악 시 필수 작업 순서

**소스 파일을 먼저 열지 않는다.**

1. `C:\MCP\Swing\README.md` 를 먼저 읽는다
2. 해당 섹션(파이프라인 단계, 투자 로직, 모듈 설명 등)에서 답을 찾는다
3. README에 없는 세부 구현이 필요할 때만 소스 파일을 읽는다

---

## 코드 수정 시 필수 작업 순서

**소스 파일을 먼저 열지 않는다.**

1. `C:\MCP\Swing\CODEINDEX.md` 를 먼저 읽는다
2. §1 빠른 편집 인덱스에서 수정 대상 → 파일 경로 + 라인 번호 확인
3. 해당 라인 범위만 `Read (offset + limit)` 으로 읽는다
4. 내용 확인 후 `Edit` 으로 수정한다

### 예외 (소스 파일 전체 읽기가 허용되는 경우)

- CODEINDEX에 없는 완전히 새로운 기능 추가
- 버그 추적 — 실행 흐름을 따라가야 할 때
- 사용자가 명시적으로 "전체 파악해줘"라고 요청한 경우

---

## 프로젝트 개요

SwingMCP v2.0.0 — 옵션 스윙 트레이딩 자동화 MCP 서버 시스템 (Python 3.12)

| 서버 | 파일 | 도구 수 |
|------|------|---------|
| swing_mcp | `servers/swing_mcp/server.py` | 10개 |
| screener_mcp | `servers/screener_mcp/server.py` | 2개 |
| kavout_mcp | `servers/kavout_mcp/server.py` | 2개 |

**전략 파라미터 단일 관리**: `shared/strategy.py`
**스키마 전체**: `shared/schemas.py`
**LLM 프롬프트 전체**: `shared/prompts.py`
**파일 경로 전체**: `shared/schemas.py:568` (`PipelinePaths`)

---

## 자주 쓰는 참조 문서

| 문서 | 용도 |
|------|------|
| `CODEINDEX.md` | 함수 라인 번호 + 코드 스니펫 + 스키마 필드 + 프롬프트 전문 |
| `README.md` | 전체 아키텍처 + 파이프라인 로직 + 투자 논리 |
| `CONFIGURATION.md` | .env 변수 + strategy.py 파라미터 전체 목록 |
| `USAGE_EXAMPLES.md` | 사용 예시 + 트러블슈팅 |
