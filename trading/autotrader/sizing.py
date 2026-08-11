"""결정적 포지션 사이징. LLM은 수량을 정하지 않는다 — 여기서만 계산.

수량 = min(
    트레이드 리스크 한도(1.5%) // 주당 리스크(진입가-손절가),
    (종목 비중 한도(15%) - 기존 보유 평가액) // 진입가,
    가용 현금 // 진입가,
)
0이면 진입 불가.
"""
from __future__ import annotations

from autotrader.config import MAX_POSITION_WEIGHT_BP, MAX_TRADE_RISK_BP
from autotrader.gates.types import Portfolio


def position_size(
    entry_price: int,
    stop_price: int,
    pf: Portfolio,
    existing_value_krw: int = 0,
) -> int:
    if pf.equity_krw <= 0 or not 0 < stop_price < entry_price:
        return 0
    risk_limit = pf.equity_krw * MAX_TRADE_RISK_BP // 10_000
    weight_room = pf.equity_krw * MAX_POSITION_WEIGHT_BP // 10_000 - existing_value_krw
    if weight_room <= 0:
        return 0
    qty = min(
        risk_limit // (entry_price - stop_price),
        weight_room // entry_price,
        pf.cash_krw // entry_price,
    )
    return max(qty, 0)
