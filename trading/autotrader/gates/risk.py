"""리스크 게이트. 절대규칙 2: 100% 결정적, 정수(원) 연산만. LLM 호출 금지.

매수 검사 (첫 위반에서 즉시 거부):
  1. 기본 유효성: side/qty/limit_price/stop_price
  2. 매수 금액 <= 가용 현금
  3. 주문 후 종목 평가액 <= 계좌의 15% (MAX_POSITION_WEIGHT_BP)
  4. 신규 종목 진입 시 보유 종목 수 < 8 (MAX_POSITIONS)
  5. (진입가-손절가)*수량 <= 계좌의 1.5% (MAX_TRADE_RISK_BP)
매도는 리스크 축소이므로 기본 유효성만 검사 (보유 초과 매도는 컴플라이언스 게이트).
"""
from __future__ import annotations

from autotrader.config import (
    MAX_POSITION_WEIGHT_BP,
    MAX_POSITIONS,
    MAX_TRADE_RISK_BP,
)
from autotrader.gates.types import OrderIntent, Portfolio
from autotrader.types import GateDecision


def check_risk(intent: OrderIntent, pf: Portfolio) -> GateDecision:
    if pf.equity_krw <= 0:
        return GateDecision(False, "invalid portfolio equity (fail-closed)")
    if intent.side not in ("buy", "sell"):
        return GateDecision(False, f"invalid side: {intent.side!r}")
    if intent.qty <= 0:
        return GateDecision(False, f"invalid qty: {intent.qty}")
    if intent.limit_price <= 0:
        return GateDecision(False, f"invalid limit_price: {intent.limit_price}")

    if intent.side == "sell":
        return GateDecision(True, "ok")

    # --- buy ---
    if intent.stop_price is None:
        return GateDecision(False, "buy without stop_price")
    if not 0 < intent.stop_price < intent.limit_price:
        return GateDecision(
            False,
            f"stop_price {intent.stop_price} must be in (0, {intent.limit_price})",
        )

    cost = intent.qty * intent.limit_price
    if cost > pf.cash_krw:
        return GateDecision(False, f"cost {cost} exceeds cash {pf.cash_krw}")

    existing = pf.positions.get(intent.symbol)
    existing_value = existing.value_krw if existing else 0
    weight_limit = pf.equity_krw * MAX_POSITION_WEIGHT_BP // 10_000
    if existing_value + cost > weight_limit:
        return GateDecision(
            False,
            f"position value {existing_value + cost} exceeds "
            f"weight limit {weight_limit}",
        )

    if existing is None and len(pf.positions) >= MAX_POSITIONS:
        return GateDecision(
            False, f"max positions ({MAX_POSITIONS}) already held"
        )

    trade_risk = (intent.limit_price - intent.stop_price) * intent.qty
    risk_limit = pf.equity_krw * MAX_TRADE_RISK_BP // 10_000
    if trade_risk > risk_limit:
        return GateDecision(
            False, f"trade risk {trade_risk} exceeds limit {risk_limit}"
        )

    return GateDecision(True, "ok")
