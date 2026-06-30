"""
scripts/run_sell_check.py
=========================
경량 매도 체크 파이프라인

기존 매도 파이프라인(run_sell_pipeline.py)과 독립적으로 실행.
데이터 수집은 기존 Step 0~3, 6을 재사용하고,
결정은 LLM 없이 rule-based로 처리.

실행:
    cd C:\\MCP\\Swing
    .venv\\Scripts\\python scripts\\run_sell_check.py
    .venv\\Scripts\\python scripts\\run_sell_check.py --ticker LRCX
    .venv\\Scripts\\python scripts\\run_sell_check.py --verbose
"""

from __future__ import annotations

# ── SSL CA bundle ASCII 경로 보정 ─────────────────────────────────────────────
import os as _os, shutil as _sh, certifi as _certifi_ssl
_ca_raw = _certifi_ssl.where()
try:
    _ca_raw.encode("ascii")
    _ca_ascii = _ca_raw
except UnicodeEncodeError:
    from pathlib import Path as _Path
    _cache_dir = _Path(__file__).resolve().parents[1] / "cache"
    _cache_dir.mkdir(exist_ok=True)
    _ca_ascii = str(_cache_dir / "cacert.pem")
    _sh.copy2(_ca_raw, _ca_ascii)
for _ev in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE"):
    _os.environ[_ev] = _ca_ascii
del _ca_raw, _ca_ascii, _ev, _os, _sh, _certifi_ssl
# ─────────────────────────────────────────────────────────────────────────────

import argparse
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))

from shared.config import get_config
from shared.logger import get_logger
from shared.schemas import PipelineContext, PipelinePaths, Position

cfg = get_config()
log = get_logger()

# ─────────────────────────────────────────────────────────────────────────────
# Dummy SlackClient — 알림 없음
# ─────────────────────────────────────────────────────────────────────────────

class _NullSlack:
    async def send_fatal_error(self, *a, **k): pass
    async def send_sell_summary(self, *a, **k): pass
    async def send_message(self, *a, **k): pass
    async def send(self, *a, **k): pass


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based 결정 엔진
# ─────────────────────────────────────────────────────────────────────────────

def _make_decision(
    pos: Position,
    health: dict,
    regime_flag: str,
    sc: Any | None,
) -> dict:
    """
    10개 트리거를 우선순위 순서로 평가해 결정을 반환.

    반환: {"action": ..., "reason": ..., "urgency": ..., "triggers": [...]}
    """
    current_premium = health.get("current_premium")
    dte = pos.dte
    flags = health.get("flags", [])
    triggered: list[str] = []

    hard_stop = pos.entry_premium * 0.50
    t1 = pos.entry_premium * 1.50
    t2 = pos.entry_premium * 2.00

    # 시나리오에서 레벨 덮어쓰기
    if sc:
        hard_stop = sc.stop_loss_premium
        if sc.target_premium_1st:
            t1 = sc.target_premium_1st
        if sc.target_premium_2nd:
            t2 = sc.target_premium_2nd

    pnl_pct: float | None = None
    if current_premium is not None and pos.entry_premium > 0:
        pnl_pct = (current_premium - pos.entry_premium) / pos.entry_premium * 100

    # ① 스탑 발동
    if current_premium is not None and current_premium <= hard_stop:
        triggered.append(f"스탑 발동 (${current_premium:.2f} ≤ ${hard_stop:.2f})")
        return {
            "action": "FULL_EXIT",
            "reason": f"하드스탑 발동 — 즉시 전량 청산 (프리미엄 ${current_premium:.2f})",
            "urgency": "critical",
            "triggers": triggered,
            "hard_stop": hard_stop,
            "t1": t1,
            "t2": t2,
        }

    # ② Thesis 무효화
    invalidated = [c for c in pos.invalidation_conditions if _check_condition_met(c, health)]
    if invalidated:
        triggered.append(f"Thesis 무효화 — {invalidated[0]}")
        return {
            "action": "FULL_EXIT",
            "reason": f"Thesis 무효화 조건 충족: {invalidated[0]}",
            "urgency": "critical",
            "triggers": triggered,
            "hard_stop": hard_stop,
            "t1": t1,
            "t2": t2,
        }

    # ③ 레짐 역전
    if regime_flag == "REGIME_REVERSED":
        triggered.append("레짐 역전 감지")
        return {
            "action": "PARTIAL_EXIT",
            "reason": "레짐 역전 — PARTIAL EXIT 75% 권고",
            "urgency": "warning",
            "triggers": triggered,
            "hard_stop": hard_stop,
            "t1": t1,
            "t2": t2,
        }

    # ④ T2 달성
    if current_premium is not None and current_premium >= t2:
        triggered.append(f"T2 달성 (${current_premium:.2f} ≥ ${t2:.2f})")
        return {
            "action": "FULL_EXIT",
            "reason": f"T2 목표 달성 (+100%) — 잔여 전량 익절",
            "urgency": "normal",
            "triggers": triggered,
            "hard_stop": hard_stop,
            "t1": t1,
            "t2": t2,
        }

    # ⑤ T1 달성
    if current_premium is not None and current_premium >= t1:
        triggered.append(f"T1 달성 (${current_premium:.2f} ≥ ${t1:.2f})")
        return {
            "action": "PARTIAL_EXIT",
            "reason": f"T1 목표 달성 (+50%) — 50% 부분 익절 + 스탑 재설정",
            "urgency": "normal",
            "triggers": triggered,
            "hard_stop": hard_stop,
            "t1": t1,
            "t2": t2,
        }

    # ⑥ Theta 과다 (무방향 보유 시 일일 손실 > 총투자금 0.5%)
    total_cost = pos.entry_premium * 100 * pos.remaining_contracts
    theta_daily = abs(health.get("greeks", {}).get("theta", 0.0)) * 100 * pos.remaining_contracts
    theta_ratio = theta_daily / total_cost if total_cost > 0 else 0
    if theta_ratio > 0.005 and pnl_pct is not None and pnl_pct < 0:
        triggered.append(f"Theta 과다 (일일 ${theta_daily:.0f} = 총투자금 {theta_ratio:.1%})")

    # ⑦ IV crush 진행 (현재 IV < 진입 IV × 0.7)
    iv_now = health.get("iv_used", 0.5)
    entry_iv = getattr(pos, "entry_iv", 0.0)
    if entry_iv > 0 and iv_now < entry_iv * 0.7:
        triggered.append(f"IV Crush 진행 중 (진입 {entry_iv:.0%} → 현재 {iv_now:.0%})")

    # ⑧ DTE 임박 — 7일
    if dte <= 7:
        triggered.append(f"DTE {dte}일 — ROLL or EXIT 최종 결정 필요")
        return {
            "action": "ROLL",
            "reason": f"DTE {dte}일 임박 — ROLL 또는 EXIT 즉시 결정",
            "urgency": "critical",
            "triggers": triggered,
            "hard_stop": hard_stop,
            "t1": t1,
            "t2": t2,
        }

    # ⑨ DTE Roll 구간 — 14일 + 외재가치 충분
    extrinsic_ratio = health.get("extrinsic_ratio", 1.0)
    if dte <= 14 and extrinsic_ratio > 0.20:
        triggered.append(f"DTE {dte}일 — ROLL 구간 진입 (외재가치 {extrinsic_ratio:.0%})")
        return {
            "action": "ROLL",
            "reason": f"DTE {dte}일 Roll 구간 — 외재가치 {extrinsic_ratio:.0%} 잔존, Roll 검토",
            "urgency": "warning",
            "triggers": triggered,
            "hard_stop": hard_stop,
            "t1": t1,
            "t2": t2,
        }

    # ⑩ HOLD
    pnl_str = f"{pnl_pct:+.1f}%" if pnl_pct is not None else "N/A"
    stop_gap = ((current_premium - hard_stop) / current_premium * 100) if current_premium else 0
    reason = f"Thesis 유효, 스탑까지 {stop_gap:.0f}% 여유, DTE {dte}일"
    if triggered:
        reason += f" (주의: {', '.join(triggered)})"

    return {
        "action": "HOLD",
        "reason": reason,
        "urgency": health.get("dte_urgency", "stable").lower(),
        "triggers": triggered,
        "hard_stop": hard_stop,
        "t1": t1,
        "t2": t2,
    }


def _calc_roll_details(pos: "Position", current_premium: float | None, iv: float, current_price: float) -> dict | None:
    """
    ROLL 결정 시 새 만기(+30일) 옵션 추정 정보 계산.
    Black-Scholes 이론가 기반 — 실제 옵션 체인 없을 때도 동작.
    """
    if not current_premium or iv <= 0 or current_price <= 0:
        return None
    try:
        import math as _m
        from scipy.stats import norm as _n
        r = cfg.RISK_FREE_RATE
        new_dte = pos.dte + 30
        T_new = max(1, new_dte) / 365.0
        d1 = (_m.log(current_price / pos.strike) + (r + 0.5 * iv ** 2) * T_new) / (iv * _m.sqrt(T_new))
        d2 = d1 - iv * _m.sqrt(T_new)
        if pos.option_type == "롱콜":
            new_prem = current_price * _n.cdf(d1) - pos.strike * _m.exp(-r * T_new) * _n.cdf(d2)
        else:
            new_prem = pos.strike * _m.exp(-r * T_new) * _n.cdf(-d2) - current_price * _n.cdf(-d1)
        new_prem = round(max(0.01, new_prem), 2)
    except Exception:
        return None

    roll_cost = new_prem - current_premium          # 양수 = 추가 지불, 음수 = 수취
    new_total_entry = pos.entry_premium + roll_cost  # 누적 진입 비용
    if pos.option_type == "롱콜":
        new_bep = pos.strike + new_total_entry
    else:
        new_bep = pos.strike - new_total_entry

    return {
        "new_dte":         new_dte,
        "new_premium":     new_prem,
        "roll_cost":       roll_cost,
        "new_total_entry": new_total_entry,
        "new_bep":         new_bep,
    }


def _check_condition_met(condition: str, health: dict) -> bool:
    """
    invalidation_condition 텍스트에서 수치 조건을 파싱해 충족 여부 반환.
    현재는 가격 하회 조건만 처리. 나머지는 False(미발생)로 보수적 처리.
    """
    import re
    current_price = health.get("current_price", 0.0) or 0.0

    # 패턴: "주가 $XXX 하회" 또는 "주가 $XXX 이하"
    m = re.search(r"\$([0-9,.]+)\s*(하회|이하|below)", condition)
    if m and current_price > 0:
        threshold = float(m.group(1).replace(",", ""))
        return current_price < threshold

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Obsidian 노트 생성
# ─────────────────────────────────────────────────────────────────────────────

def _build_note(
    pos: Position,
    health: dict,
    regime: Any | None,
    regime_flag: str,
    tech_score: Any | None,
    sentiment: dict,
    opt_analytics: dict,
    decision: dict,
    exec_id: str,
    fvd: Any | None = None,
    roll_details: dict | None = None,
    summary_data: Any | None = None,
) -> str:
    """경량 매도 체크 노트 생성 (Markdown)"""

    now_str = datetime.now().strftime("%H:%M")
    today = date.today().isoformat()

    current_price     = health.get("current_price") or pos.entry_stock_price
    current_premium   = health.get("current_premium")
    greeks            = health.get("greeks", {})
    delta_pnl         = health.get("delta_pnl", 0.0)
    theta_pnl         = health.get("theta_pnl", 0.0)
    vega_pnl          = health.get("vega_pnl", 0.0)
    iv_used           = health.get("iv_used", 0.0)
    extrinsic_ratio   = health.get("extrinsic_ratio", 0.0)
    premium_source    = health.get("premium_source", "unknown")
    days_held         = (date.today() - pos.entry_date).days

    pnl_dollar: float | None = None
    pnl_pct: float | None = None
    if current_premium is not None:
        pnl_dollar = (current_premium - pos.entry_premium) * 100 * pos.remaining_contracts
        pnl_pct = (current_premium - pos.entry_premium) / pos.entry_premium * 100

    total_cost = pos.entry_premium * 100 * pos.remaining_contracts
    hard_stop  = decision["hard_stop"]
    t1         = decision["t1"]
    t2         = decision["t2"]
    action     = decision["action"]
    reason     = decision["reason"]
    urgency    = decision["urgency"]

    # BEP
    bep = pos.strike + pos.entry_premium if pos.option_type == "롱콜" else pos.strike - pos.entry_premium
    bep_move_pct = (bep - current_price) / current_price * 100 if current_price else 0

    # 스탑까지 거리
    stop_gap_pct = ((current_premium - hard_stop) / current_premium * 100) if current_premium else 0
    stop_gap_dollar = (current_premium - hard_stop) if current_premium else 0

    # T1까지 거리
    t1_gap_pct = ((t1 - current_premium) / current_premium * 100) if current_premium else 0
    t2_gap_pct = ((t2 - current_premium) / current_premium * 100) if current_premium else 0

    # 레짐
    regime_status = regime.regime_status if regime else "N/A"
    regime_conf   = f"{regime.regime_confidence:.0%}" if regime else "N/A"
    entry_regime  = pos.entry_regime or "N/A"
    if regime_flag == "REGIME_OK":
        _rs_lower = (regime_status or "").lower()
        if "borderline" in _rs_lower or "neutral" in _rs_lower:
            regime_match = "🟡 주의 (Borderline)"
        else:
            regime_match = "✅ 일치"
    elif regime_flag == "REGIME_REVERSED":
        regime_match = "🔴 역전"
    else:
        regime_match = "⚪ 확인 불가"

    # IV 변화
    entry_iv = getattr(pos, "entry_iv", 0.0) or 0.0
    iv_change_str = ""
    if entry_iv > 0:
        iv_change_pct = (iv_used - entry_iv) / entry_iv * 100
        arrow = "⬆️" if iv_change_pct > 5 else ("⬇️" if iv_change_pct < -5 else "➡️")
        iv_change_str = f"{entry_iv:.0%} → {iv_used:.0%} {arrow}"
    else:
        iv_change_str = f"{iv_used:.0%} (진입 시 미기록)"

    # 기술 신호 — fvd(StockDetail) 우선, summary_data.tickers fallback
    _td = (summary_data.tickers.get(pos.ticker) if summary_data else None)
    _td_tech = _td.technical if _td else None
    _rsi_raw = (fvd.rsi14 if fvd and fvd.rsi14 is not None else None) \
               or (getattr(_td_tech, "rsi14", None))
    _adx_raw = (fvd.adx   if fvd and fvd.adx   is not None else None) \
               or (getattr(_td_tech, "adx14",  None))
    rsi_val  = f"{_rsi_raw:.1f}" if _rsi_raw is not None else "N/A"
    adx_val  = f"{_adx_raw:.1f}" if _adx_raw is not None else "N/A"
    ma_align = "N/A"
    if tech_score:
        _ma = getattr(tech_score, "ma_alignment", "")
        _ma_emoji = {"bullish": "🟢", "bearish": "🔴", "mixed": "🟡"}.get(_ma, "")
        ma_align = f"{_ma_emoji} {_ma}" if _ma_emoji else (_ma or "N/A")
    # 현재가 위치 (일간 피벗 S1/R1 기준)
    _px   = fvd.price  if fvd and fvd.price   is not None else None
    _ps1  = fvd.pivot_s1 if fvd and fvd.pivot_s1 is not None else None
    _pr1  = fvd.pivot_r1 if fvd and fvd.pivot_r1 is not None else None
    if _px is not None and _ps1 is not None and _pr1 is not None:
        if _px < _ps1:
            price_pos = f"S1(${_ps1:.0f}) 이하 약세"
        elif _px > _pr1:
            price_pos = f"R1(${_pr1:.0f}) 이상 과열"
        else:
            price_pos = f"S1(${_ps1:.0f}) ~ R1(${_pr1:.0f}) 중립"
    else:
        price_pos = "N/A"

    # 뉴스 감성
    sentiment_str   = sentiment.get("overall_sentiment", "N/A")
    sentiment_conf  = sentiment.get("confidence", "N/A")
    def _trunc(text: str, limit: int = 280) -> str:
        if len(text) <= limit:
            return text
        cut = text[:limit]
        dot = cut.rfind(".")
        return (cut[:dot + 1] if dot > limit // 2 else cut) + "…"

    bull_thesis_s   = _trunc(sentiment.get("bull_thesis", "") or "")
    bear_thesis_s   = _trunc(sentiment.get("bear_thesis", "") or "")

    # 옵션 구조
    max_pain    = opt_analytics.get("max_pain", 0.0)
    call_wall   = opt_analytics.get("call_wall", 0.0)
    put_wall    = opt_analytics.get("put_wall", 0.0)
    gex_flip    = opt_analytics.get("gex_flip", 0.0)
    impl_move   = opt_analytics.get("implied_move", 0.0)
    pc_ratio    = opt_analytics.get("pc_ratio", 1.0)

    # 애널리스트 · 내부자
    target_price    = fvd.target_price if fvd else None
    recom_val       = fvd.recom if fvd else None
    insider_pct     = fvd.insider_trans_pct if fvd else None

    if recom_val is not None:
        if recom_val <= 1.5:   recom_label, recom_icon = "Strong Buy", "🟢"
        elif recom_val <= 2.5: recom_label, recom_icon = "Buy", "🟢"
        elif recom_val <= 3.5: recom_label, recom_icon = "Hold", "🟡"
        elif recom_val <= 4.5: recom_label, recom_icon = "Underperform", "🔴"
        else:                  recom_label, recom_icon = "Sell", "🔴"
    else:
        recom_label, recom_icon = "N/A", "⚪"

    if target_price and current_price:
        tgt_gap   = (target_price - current_price) / current_price * 100
        tgt_arrow = "⬆️" if tgt_gap > 0 else "⬇️"
        target_str  = f"${target_price:.0f} ({tgt_arrow} {abs(tgt_gap):.1f}%)"
        target_icon = "🟢" if tgt_gap > 5 else ("🔴" if tgt_gap < -5 else "🟡")
    else:
        target_str, target_icon = "N/A", "⚪"

    if insider_pct is not None:
        insider_icon = "🟢" if insider_pct > 2 else ("🔴" if insider_pct < -5 else "🟡")
        insider_str  = f"{insider_pct:+.1f}% ({'순매수' if insider_pct >= 0 else '순매도'})"
    else:
        insider_icon, insider_str = "⚪", "N/A"

    pc_icon = "🔴 풋 우세" if pc_ratio > 1.0 else ("🟢 콜 우세" if pc_ratio < 0.8 else "🟡 중립")

    # 결정 아이콘
    action_icon = {
        "HOLD": "✋",
        "PARTIAL_EXIT": "📤",
        "FULL_EXIT": "🚨",
        "ROLL": "🔄",
    }.get(action, "?")

    urgency_icon = {
        "critical": "🔴",
        "warning":  "🟡",
        "normal":   "🔵",
        "stable":   "🟢",
        "안정":     "🟢",
        "보통":     "🔵",
        "주의":     "🟡",
        "위급":     "🔴",
    }.get(urgency, "⚪")

    # 무효화 조건 상태
    inv_rows = ""
    if pos.invalidation_conditions:
        for cond in pos.invalidation_conditions:
            met = _check_condition_met(cond, health)
            status = "🔴 충족 → EXIT" if met else "✅ 미발생"
            inv_rows += f"| {cond} | {status} |\n"
    else:
        inv_rows = "| (미설정) | ⚠️ positions.md에 입력 필요 |\n"

    # 트리거 체크 표
    stop_trigger   = "🔴 발동!" if (current_premium is not None and current_premium <= hard_stop) else "✅ 미발동"
    t1_trigger     = "✅ 달성" if (current_premium is not None and current_premium >= t1) else f"⏳ +{t1_gap_pct:.0f}% 필요"
    t2_trigger     = "✅ 달성" if (current_premium is not None and current_premium >= t2) else f"⏳ +{t2_gap_pct:.0f}% 필요"
    dte_trigger    = "🔴 즉시 결정" if pos.dte <= 7 else ("⚠️ Roll 구간" if pos.dte <= 14 else "✅ 안정")
    regime_trigger = "🔴 역전!" if regime_flag == "REGIME_REVERSED" else "✅ 정상"
    thesis_trigger = "🔴 무효화" if any(_check_condition_met(c, health) for c in pos.invalidation_conditions) else "✅ 유효"

    theta_daily  = abs(greeks.get("theta", 0.0)) * 100 * pos.remaining_contracts
    theta_ratio  = theta_daily / total_cost if total_cost > 0 else 0
    theta_trigger = f"⚠️ {theta_ratio:.1%}/일 — 주의" if theta_ratio > 0.005 else f"✅ {theta_ratio:.1%}/일 (허용 범위)"

    iv_crush_flag = ""
    if entry_iv > 0 and iv_used < entry_iv * 0.7:
        iv_crush_flag = f"⚠️ IV Crush 진행 ({entry_iv:.0%}→{iv_used:.0%})"
    else:
        iv_crush_flag = "✅ 정상"

    # peak_premium / trailing stop
    peak_str = f"${pos.peak_premium:.2f}" if pos.peak_premium > 0 else "미추적"
    trail_str = f"${pos.trailing_stop:.2f}" if pos.trailing_stop > 0 else "미설정"

    # P&L 문자열
    pnl_str = f"{pnl_pct:+.1f}% (${pnl_dollar:+,.0f})" if pnl_pct is not None else "계산 불가"
    curr_prem_str = f"${current_premium:.2f}" if current_premium is not None else "N/A"

    # BS 추정 여부
    bs_note = "> ⚠️ **BS 추정 기반** — 옵션 체인 데이터 없음, 이론가 사용\n\n" if premium_source == "bs_estimate" else ""

    # 한 줄 요약
    if action == "HOLD":
        if pnl_pct is not None and pnl_pct < 0:
            summary_line = f"현재 {pnl_pct:+.1f}% 손실 중 — Thesis 유효, DTE {pos.dte}일 충분 → 스탑 ${hard_stop:.2f} 대기"
        elif pnl_pct is not None:
            summary_line = f"현재 {pnl_pct:+.1f}% 수익 — T1(${t1:.2f}) 도달 대기, 스탑 ${hard_stop:.2f} 추적"
        else:
            summary_line = reason
    else:
        summary_line = reason

    lines: list[str] = [
        f"# 매도 체크 — {today}",
        "",
        f"> **실행**: `{exec_id}`  |  **분석 시각**: {today} {now_str}",
        "",
        "---",
        "",
        "## 포지션 현황",
        "",
        "| 항목 | 진입 | 현재 |",
        "|------|------|------|",
        f"| 종목 / 타입 | {pos.ticker} {pos.option_type} ${pos.strike:.0f} / {pos.expiry} | — |",
        f"| 주가 | ${pos.entry_stock_price:.2f} | ${current_price:.2f} |",
        f"| 옵션 프리미엄 | ${pos.entry_premium:.2f} | {curr_prem_str} |",
        f"| P&L | — | **{pnl_str}** |",
        f"| 진입일 | {pos.entry_date} | — |",
        f"| DTE | — | {pos.dte}일 |",
        f"| 보유 기간 | — | {days_held}일 |",
        f"| 총 투자금 | ${total_cost:,.0f} | — |",
        f"| BEP | ${bep:.2f} | 현재가 {bep_move_pct:+.1f}% 필요 |",
        f"| 계약 수 | {pos.original_contracts} | {pos.remaining_contracts} |",
        "",
        bs_note +
        "## 리스크 레벨",
        "",
        "| 항목 | 값 | 상태 |",
        "|------|----|------|",
        f"| 하드 스탑 | ${hard_stop:.2f} | {'🔴 발동!' if (current_premium is not None and current_premium <= hard_stop) else f'🟢 {stop_gap_pct:.0f}% 여유'} |",
        f"| 스탑 거리 | ${stop_gap_dollar:.2f} | {stop_gap_pct:.1f}% |",
        f"| 프리미엄 고점 | {peak_str} | — |",
        f"| 트레일링 스탑 | {trail_str} | — |",
        f"| T1 목표 (+50%) | ${t1:.2f} | {t1_trigger} |",
        f"| T2 목표 (+100%) | ${t2:.2f} | {t2_trigger} |",
        f"| 외재가치 잔존 | {extrinsic_ratio:.0%} | {'🟢 충분' if extrinsic_ratio > 0.30 else ('🟡 주의' if extrinsic_ratio > 0.10 else '🔴 낮음')} |",
        "",
        "## Greeks + P&L 원인",
        "",
        f"| Delta | Theta/일 | Vega | Gamma | IV (진입→현재) |",
        f"|-------|---------|------|-------|--------------|",
        f"| {greeks.get('delta', 0):.3f} | ${abs(greeks.get('theta', 0))*100*pos.remaining_contracts:.2f} | {greeks.get('vega', 0):.3f} | {greeks.get('gamma', 0):.4f} | {iv_change_str} |",
        "",
        "```",
        f"Delta 기여:  ${delta_pnl:+,.0f}   (주가 이동 효과)",
        f"Theta 비용:  ${theta_pnl:+,.0f}   (시간 가치 소멸)",
        f"Vega  기여:  ${vega_pnl:+,.0f}   (IV 변화 효과)",
        "─" * 34,
        f"합계:        ${(delta_pnl + theta_pnl + vega_pnl):+,.0f}",
        "```",
        "",
        "## 시장 레짐",
        "",
        "| 항목 | 진입 시 | 현재 |",
        "|------|--------|------|",
        f"| 레짐 | {entry_regime} | {regime_status} ({regime_conf}) |",
        f"| 포지션 방향 일치 | — | {regime_match} |",
    ]

    if regime and regime.risk_factors:
        lines += ["", "**레짐 리스크 요인**"]
        for rf in regime.risk_factors[:3]:
            lines.append(f"- {rf}")

    lines += [
        "",
        "## 기술 신호",
        "",
        f"| RSI | ADX | MA 배열 | 현재가 위치 |",
        f"|-----|-----|---------|-----------|",
        f"| {rsi_val} | {adx_val} | {ma_align} | {price_pos} |",
        "",
        "## 뉴스 감성",
        "",
        f"> **{sentiment_str}** (신뢰도: {sentiment_conf})",
    ]

    if bull_thesis_s:
        lines.append(f"> 🐂 {bull_thesis_s}")
    if bear_thesis_s:
        lines.append(f"> 🐻 {bear_thesis_s}")

    recom_row = (
        f"| 추천 등급 | {recom_val:.2f} ({recom_label}) | {recom_icon} |"
        if recom_val is not None
        else "| 추천 등급 | N/A | ⚪ |"
    )
    lines += [
        "",
        "## 애널리스트 · 내부자",
        "",
        "| 항목 | 값 | 신호 |",
        "|------|----|----- |",
        f"| 목표가 (컨센서스) | {target_str} | {target_icon} |",
        recom_row,
        f"| 내부자 거래 (6M) | {insider_str} | {insider_icon} |",
    ]

    lines += [
        "",
        "## 옵션 시장 구조",
        "",
        "| 항목 | 값 |",
        "|------|-----|",
        f"| Implied Move | ±{impl_move:.1f}% |" if impl_move else "| Implied Move | N/A |",
        f"| Max Pain | ${max_pain:.0f} |" if max_pain else "| Max Pain | N/A |",
        f"| Call Wall | ${call_wall:.0f} |" if call_wall else "| Call Wall | N/A |",
        f"| Put Wall | ${put_wall:.0f} |" if put_wall else "| Put Wall | N/A |",
        f"| GEX Flip | ${gex_flip:.0f} |" if gex_flip else "| GEX Flip | N/A |",
        f"| P/C Ratio | {pc_ratio:.3f} {pc_icon} |",
        "",
        "## Thesis 체크",
        "",
        f"**원래 진입 논거**: {_trunc((pos.thesis or '').split(chr(10))[0])}",
        "",
        "| 무효화 조건 | 현재 상태 |",
        "|-----------|---------|",
        inv_rows.rstrip(),
        "",
        "## 트리거 체크",
        "",
        "| 트리거 | 상태 |",
        "|--------|------|",
        f"| 스탑 발동 | {stop_trigger} |",
        f"| Thesis 무효화 | {thesis_trigger} |",
        f"| 레짐 역전 | {regime_trigger} |",
        f"| T1 달성 | {t1_trigger} |",
        f"| T2 달성 | {t2_trigger} |",
        f"| IV Crush | {iv_crush_flag} |",
        f"| Theta 과다 | {theta_trigger} |",
        f"| DTE 임박 | {dte_trigger} |",
        "",
        "## 결정",
        "",
        "```",
        f"결정:  {action_icon} {action}",
        f"이유:  {reason}" + (f" | ⚠️ Theta {theta_ratio:.1%}/일" if theta_ratio > 0.005 else ""),
        f"긴급도: {urgency_icon} {urgency.upper()}",
        "```",
    ]

    # Roll 판단 섹션 (ROLL 결정 시에만)
    if action == "ROLL":
        lines += ["", "## Roll 판단", ""]
        if roll_details:
            rd = roll_details
            cost_sign = "추가 지불" if rd["roll_cost"] > 0 else "수취"
            bep_move = (rd["new_bep"] - current_price) / current_price * 100 if current_price else 0
            lines += [
                "| 항목 | 현재 | Roll 후 |",
                "|------|------|---------|",
                f"| 프리미엄 | ${current_premium:.2f} (청산) | ${rd['new_premium']:.2f} (BS 추정, DTE {rd['new_dte']}일) |",
                f"| Roll 순비용 | — | {cost_sign} ${abs(rd['roll_cost']):.2f} |",
                f"| 누적 진입비용 | ${pos.entry_premium:.2f} | ${rd['new_total_entry']:.2f} |",
                f"| BEP | ${bep:.2f} | ${rd['new_bep']:.2f} (현재가 {bep_move:+.1f}% 필요) |",
                "",
                "> ⚠️ BS 이론가 기반 추정 — 실제 체인 확인 후 실행 결정",
            ]
        else:
            lines += ["> ⚠️ Roll 비용 계산 불가 — IV 또는 현재 프리미엄 데이터 부족", ""]

    # 즉시 실행 지침
    if action == "FULL_EXIT":
        lines += [
            "",
            "**🚨 즉시 실행**",
            f"- 프리미엄 ${current_premium:.2f if current_premium else 0:.2f} 시장가 전량 매도",
        ]
    elif action == "PARTIAL_EXIT":
        lines += [
            "",
            "**📤 즉시 실행**",
            f"- {pos.remaining_contracts}계약 중 절반 매도 (1계약이면 판단 위임)",
        ]
    elif action == "ROLL":
        lines += [
            "",
            "**🔄 즉시 실행**",
            f"- DTE {pos.dte}일 — 현재 포지션 청산 후 +30일 만기 Roll 검토",
        ]
    else:
        lines += [
            "",
            "**✅ 즉시 실행**: 없음",
            "",
            "**다음 재분석 조건**",
            f"- 프리미엄 ${hard_stop:.2f} 하회 → FULL EXIT",
            f"- 프리미엄 ${t1:.2f} 도달 → PARTIAL EXIT 50% + 스탑 재설정",
        ]
        if put_wall:
            lines.append(f"- 주가 ${put_wall:.0f} (Put Wall) 하회 → Thesis 재점검")
        if regime_flag != "REGIME_REVERSED":
            lines.append("- 레짐 Bearish 전환 → PARTIAL EXIT 75%")

    lines += [
        "",
        f"> 💬 **한 줄 요약**: {summary_line}",
        "",
    ]

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────────────────────────────────────

async def run(ticker_filter: list[str] | None = None, verbose: bool = False) -> None:
    from shared.schemas import PipelineContext, PipelinePaths
    from orchestrator.steps.sell_steps import SellSteps
    from core.obsidian import ObsidianClient

    paths = PipelinePaths(
        summary_dir=Path(cfg.SUMMARY_DIR),
        finviz_file=Path(cfg.FINVIZ_FILE),
        earnings_dir=Path(cfg.EARNINGS_DIR),
        positions_file=Path(cfg.POSITIONS_FILE),
        watchlist_file=Path(cfg.WATCHLIST_FILE),
        data_dir=Path(cfg.DATA_DIR),
    )

    eid = f"sell_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    today = date.today().isoformat()

    ctx = PipelineContext(
        execution_id=eid,
        pipeline_type="sell",
        start_step=0,
        force_refresh=True,
        target_tickers=ticker_filter,
        paths=paths,
    )

    obsidian = ObsidianClient()
    slack    = _NullSlack()
    steps    = SellSteps(obsidian=obsidian, slack=slack)

    print(f"\n{'='*56}")
    print(f"  매도 체크  [{eid}]")
    if ticker_filter:
        print(f"  대상: {ticker_filter}")
    print(f"{'='*56}")

    # ── Step 0: 데이터 로딩 ───────────────────────────────────────
    print("\n▶ 데이터 로딩...")
    await steps.step_0_env(ctx)
    if not ctx.positions:
        print("  포지션 없음 — 종료")
        return
    print(f"  포지션: {[p.ticker for p in ctx.positions]}")

    # ── Step 1: 건강도 (Greeks, P&L 귀인) ─────────────────────────
    print("▶ 포지션 건강도 계산...")
    await steps.step_1_health(ctx)

    # ── Step 2: 시장 레짐 ──────────────────────────────────────────
    print("▶ 시장 레짐 분석...")
    await steps.step_2_regime(ctx)

    # ── Step 3: 기술 분석 + 뉴스 감성 ─────────────────────────────
    print("▶ 기술 분석 + 뉴스 수집...")
    await steps.step_3_technical(ctx)

    # ── Step 6: 옵션 구조 ──────────────────────────────────────────
    print("▶ 옵션 시장 구조...")
    try:
        await steps.step_6_options(ctx)
    except Exception as e:
        log.warning("sell_check_step6_failed", error=str(e))

    # ── peak_premium 갱신 및 저장 ──────────────────────────────────
    from core.state import save_positions_state
    _updated = False
    for pos in ctx.positions:
        pk = f"{pos.ticker}_{pos.expiry}_{pos.strike}"
        h  = ctx.sell_health.get(pk, {})
        cp = h.get("current_premium")
        if cp is not None and cp > pos.peak_premium:
            pos.peak_premium = cp
            if pos.trailing_stop == 0.0:
                # 트레일링 스탑 미설정 시 자동 설정하지 않음 (사용자 판단)
                pass
            _updated = True
    if _updated:
        save_positions_state(ctx.positions)
        print("  peak_premium 갱신 저장 완료")

    # ── 포지션별 결정 + 출력 + Obsidian 저장 ─────────────────────
    print()
    note_path = f"swing-procedure/notes/sell/{today}.md"
    note_exists = False

    for pos in ctx.positions:
        pk = f"{pos.ticker}_{pos.expiry}_{pos.strike}"

        health      = ctx.sell_health.get(pk, {})
        regime_flag = ctx.sell_regime_flags.get(pk, "REGIME_UNKNOWN")
        tech_score  = ctx.technical_scores.get(pk)
        sentiment   = ctx.sentiment_results.get(pk, {})

        # 옵션 구조 데이터 수집 (Step 6에서 ctx.summary_data.options에 저장)
        opt_analytics: dict = {}
        if ctx.summary_data and ctx.summary_data.options.get(pos.ticker):
            _opt = ctx.summary_data.options[pos.ticker]
            opt_analytics = {
                "max_pain":     getattr(_opt, "max_pain_near", 0.0) or 0.0,
                "call_wall":    getattr(_opt, "call_wall", 0.0) or 0.0,
                "put_wall":     getattr(_opt, "put_wall", 0.0) or 0.0,
                "gex_flip":     getattr(_opt, "gex_flip", 0.0) or 0.0,
                "implied_move": getattr(_opt, "implied_move_near", 0.0) or 0.0,
                "pc_ratio":     getattr(_opt, "pc_ratio", 1.0) or 1.0,
            }

        # 시나리오 (있을 경우)
        sc = ctx.scenarios.get(pk)

        # 애널리스트 · 내부자 데이터
        fvd = ctx.stock_data.get(pos.ticker)

        # Rule-based 결정
        decision = _make_decision(pos, health, regime_flag, sc)

        # Roll 상세 계산 (ROLL 결정 시에만)
        roll_details: dict | None = None
        if decision["action"] == "ROLL":
            roll_details = _calc_roll_details(
                pos=pos,
                current_premium=health.get("current_premium"),
                iv=health.get("iv_used", 0.0),
                current_price=health.get("current_price", pos.entry_stock_price),
            )

        # 터미널 출력
        cp = health.get("current_premium")
        pnl_pct = ((cp - pos.entry_premium) / pos.entry_premium * 100) if cp else None
        pnl_str = f"{pnl_pct:+.1f}%" if pnl_pct is not None else "N/A"
        action_icon = {"HOLD": "✋", "PARTIAL_EXIT": "📤", "FULL_EXIT": "🚨", "ROLL": "🔄"}.get(decision["action"], "?")

        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"[{pos.ticker}]  {pos.option_type}  ${pos.strike:.0f} / {pos.expiry}  DTE: {pos.dte}일")
        print(f"진입 ${pos.entry_premium:.2f}  →  현재 {f'${cp:.2f}' if cp else 'N/A'}    ({pnl_str})")
        print(f"결정: {action_icon} {decision['action']}  |  {decision['reason']}")

        if verbose:
            print(f"  Delta: {health.get('greeks', {}).get('delta', 0):.3f}  "
                  f"Theta: ${abs(health.get('greeks', {}).get('theta', 0))*100*pos.remaining_contracts:.2f}/일  "
                  f"Vega: {health.get('greeks', {}).get('vega', 0):.3f}  "
                  f"IV: {health.get('iv_used', 0):.1%}")
            print(f"  P&L 원인: Delta ${health.get('delta_pnl', 0):+,.0f} / "
                  f"Vega ${health.get('vega_pnl', 0):+,.0f} / "
                  f"Theta ${health.get('theta_pnl', 0):+,.0f}")
            if decision["triggers"]:
                print(f"  주의 트리거: {', '.join(decision['triggers'])}")

        # Obsidian 노트 생성
        note_content = _build_note(
            pos=pos,
            health=health,
            regime=ctx.regime,
            regime_flag=regime_flag,
            tech_score=tech_score,
            sentiment=sentiment,
            opt_analytics=opt_analytics,
            decision=decision,
            exec_id=eid,
            fvd=fvd,
            roll_details=roll_details,
            summary_data=ctx.summary_data,
        )

        # 같은 날 이미 파일이 있으면 append (구분선 포함), 없으면 create
        if not note_exists:
            ok = await obsidian.write_note(note_path, note_content)
            note_exists = True
        else:
            separator = "\n\n---\n\n"
            ok = await obsidian.append_note(note_path, separator + note_content)

        if ok:
            print(f"  📝 Obsidian 저장: {note_path}")
        else:
            print(f"  ⚠️  Obsidian 저장 실패")

    print(f"\n{'='*56}")
    print(f"  완료 — {note_path}")
    print(f"{'='*56}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="경량 매도 체크 파이프라인")
    parser.add_argument("--ticker", nargs="+", help="특정 종목만 체크 (예: --ticker LRCX NVDA)")
    parser.add_argument("--verbose", action="store_true", help="상세 출력")
    parser.add_argument(
        "--provider", choices=["openrouter", "claude_cli"],
        help="LLM 프로바이더 선택 (기본: .env의 LLM_PROVIDER)"
    )
    args = parser.parse_args()

    if args.provider:
        # cfg는 모듈 최상위에서 이미 생성됐으므로 인스턴스 속성 직접 덮어쓰기
        # get_config()는 @lru_cache로 동일 객체 반환 → 이후 모든 모듈에도 반영됨
        import os; os.environ["LLM_PROVIDER"] = args.provider
        cfg.LLM_PROVIDER = args.provider

    asyncio.run(run(ticker_filter=args.ticker, verbose=args.verbose))


if __name__ == "__main__":
    main()
