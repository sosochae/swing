"""
Google Apps Script 웹앱 원격 호출 스크립트 — 매도용 SUMMARY 생성
GAS doPost()에 type=sell 을 전달해 runSell() 을 실행시킴.
"""

import requests
import time

# =============================================
# 설정
# =============================================
WEBAPP_URL = "https://script.google.com/macros/s/AKfycbyzf06qyz81lD213P8xRvauwsiAqgkYXGQAkWYAOiBPZVX9GxOxXkWVZSKIilswtsJzAA/exec"

TIMEOUT_SEC = 600  # GAS 실행 최대 6분 (종목 많을수록 늘릴 것)


# =============================================
# 실행 함수
# =============================================
def trigger_gas_sell() -> str:
    """
    GAS 웹앱에 type=sell 을 POST로 전달.
    GAS runSell() 실행 → summary_sell_*.json 저장.
    """
    print(f"[INFO] 매도 SUMMARY 생성 시작: {WEBAPP_URL}")
    start = time.time()

    try:
        resp = requests.post(
            WEBAPP_URL,
            data={"type": "sell"},
            timeout=TIMEOUT_SEC,
            allow_redirects=True,
        )
        elapsed = round(time.time() - start, 1)

        if resp.status_code == 200:
            result = resp.text.strip()
            print(f"[OK] {elapsed}s  응답: {result}")
            return result
        else:
            msg = f"[ERR] HTTP {resp.status_code}  body: {resp.text[:300]}"
            print(msg)
            return msg

    except requests.exceptions.Timeout:
        msg = f"[ERR] Timeout ({TIMEOUT_SEC}s 초과) — GAS가 아직 실행 중일 수 있음"
        print(msg)
        return msg
    except Exception as e:
        msg = f"[ERR] 예외 발생: {e}"
        print(msg)
        return msg


# =============================================
# 스케줄러 (선택 — 주기 실행)
# =============================================
def run_on_schedule(interval_minutes: int = 60):
    """
    interval_minutes마다 GAS를 반복 호출.
    Ctrl+C로 중지.
    """
    print(f"[INFO] {interval_minutes}분 간격 스케줄 시작 (Ctrl+C로 중지)")
    while True:
        trigger_gas_sell()
        print(f"[INFO] {interval_minutes}분 대기 중...")
        time.sleep(interval_minutes * 60)


# =============================================
# 진입점
# =============================================
if __name__ == "__main__":
    # 단발 실행
    trigger_gas_sell()

    # 주기 실행을 원하면 아래 주석 해제 (단발 실행 줄은 주석 처리)
    # run_on_schedule(interval_minutes=60)
