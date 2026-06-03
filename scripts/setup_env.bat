@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1
title SwingMCP — 환경 설정

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║         SwingMCP v2.0.0 — 환경 설정 및 검증              ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

set "ROOT=C:\MCP\Swing"
set "VENV=%ROOT%\.venv"
set "PYTHON=%VENV%\Scripts\python.exe"
set "PIP=%VENV%\Scripts\pip.exe"
set "ERRORS=0"

cd /d "%ROOT%"

:: ──────────────────────────────────────────────────────
:: Step 1: Python 버전 확인
:: ──────────────────────────────────────────────────────
echo [1/5] Python 확인...
python --version >nul 2>&1
if errorlevel 1 (
    echo   FAIL  Python을 찾을 수 없습니다.
    echo         https://www.python.org/downloads/ 에서 Python 3.11+ 설치 후 재시도
    set /a ERRORS+=1
    goto :summary
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo   OK    Python %PYVER%

:: ──────────────────────────────────────────────────────
:: Step 2: 가상환경(venv) 생성 또는 확인
:: ──────────────────────────────────────────────────────
echo.
echo [2/5] 가상환경 확인...
if exist "%PYTHON%" (
    echo   OK    .venv 존재 확인
) else (
    echo   INFO  .venv 없음 — 생성 중...

    :: uv 사용 가능하면 우선 사용
    uv --version >nul 2>&1
    if not errorlevel 1 (
        echo   INFO  uv 사용하여 가상환경 생성
        uv venv --python 3.11 "%VENV%"
    ) else (
        echo   INFO  pip 사용하여 가상환경 생성
        python -m venv "%VENV%"
    )

    if not exist "%PYTHON%" (
        echo   FAIL  가상환경 생성 실패
        set /a ERRORS+=1
        goto :summary
    )
    echo   OK    .venv 생성 완료
)

:: ──────────────────────────────────────────────────────
:: Step 3: 패키지 설치/업데이트
:: ──────────────────────────────────────────────────────
echo.
echo [3/5] 패키지 설치 확인...

:: uv.lock이 있으면 uv sync, 없으면 pip install
uv --version >nul 2>&1
if not errorlevel 1 (
    if exist "%ROOT%\uv.lock" (
        echo   INFO  uv sync 실행 중...
        uv sync --project "%ROOT%"
        if errorlevel 1 (
            echo   WARN  uv sync 실패 — pip fallback 시도
            goto :pip_install
        )
        goto :pkg_done
    )
)

:pip_install
echo   INFO  pip install 실행 중...
"%PIP%" install --quiet --upgrade pip
"%PIP%" install --quiet ^
    openai anthropic httpx structlog pydantic python-dotenv ^
    ddgs duckduckgo-search feedparser slack_sdk ^
    numpy scipy tenacity watchfiles mcp uvicorn ^
    aiohttp aiofiles pywin32

:pkg_done
:: ddgs 별도 확인 (뉴스 검색 필수)
"%PYTHON%" -c "import ddgs; print('  OK    ddgs', ddgs.__version__)" 2>nul
if errorlevel 1 (
    echo   INFO  ddgs 별도 설치 중...
    "%PIP%" install --quiet ddgs
    "%PYTHON%" -c "import ddgs" 2>nul || (
        echo   WARN  ddgs 설치 실패 — 뉴스 검색 비활성화됨
    )
)

echo   OK    패키지 준비 완료

:: ──────────────────────────────────────────────────────
:: Step 4: .env 파일 확인
:: ──────────────────────────────────────────────────────
echo.
echo [4/5] .env 파일 확인...
if exist "%ROOT%\.env" (
    echo   OK    .env 존재
    :: 필수 키 간단 확인
    findstr /i "OPENROUTER_API_KEY=sk-" "%ROOT%\.env" >nul 2>&1
    if errorlevel 1 (
        echo   WARN  OPENROUTER_API_KEY 미설정 — .env 파일 편집 필요
        set /a ERRORS+=1
    ) else (
        echo   OK    OPENROUTER_API_KEY 설정됨
    )
    findstr /i "OBSIDIAN_API_KEY=" "%ROOT%\.env" >nul 2>&1
    if errorlevel 1 (
        echo   WARN  OBSIDIAN_API_KEY 미설정
    )
) else (
    echo   WARN  .env 없음 — CONFIGURATION.md 참고하여 생성 필요
    echo         복사 후 편집: copy CONFIGURATION.md .env (내용 수정 필요)
    set /a ERRORS+=1
)

:: ──────────────────────────────────────────────────────
:: Step 5: Health Check (디렉토리 생성 + 연결 확인)
:: ──────────────────────────────────────────────────────
echo.
echo [5/5] Health Check 실행...
set "PYTHONPATH=%ROOT%"
"%PYTHON%" "%ROOT%\scripts\health_check.py" --setup
if errorlevel 1 (
    echo   WARN  Health Check 일부 실패 (위 내용 확인)
    set /a ERRORS+=1
)

:: ──────────────────────────────────────────────────────
:: 요약
:: ──────────────────────────────────────────────────────
:summary
echo.
echo ══════════════════════════════════════════════════════════
if %ERRORS% == 0 (
    echo   ✓ 환경 설정 완료 — SwingMCP 사용 준비됨
    echo.
    echo   매수 파이프라인 실행:
    echo     .venv\Scripts\python.exe scripts\run_buy_pipeline.py
    echo.
    echo   매도 파이프라인 실행:
    echo     .venv\Scripts\python.exe scripts\run_sell_pipeline.py
    echo.
    echo   Cline/Roo Code 설정 파일 위치:
    echo     MCP 서버: .roo\mcp.json
    echo     규칙:     .clinerules
) else (
    echo   ! 경고 %ERRORS%건 — 위 항목을 확인하세요
    echo   CONFIGURATION.md 참고: C:\MCP\Swing\CONFIGURATION.md
)
echo ══════════════════════════════════════════════════════════
echo.
pause
