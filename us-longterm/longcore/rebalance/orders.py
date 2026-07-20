"""주문 목록 생성 — 순수 함수. 이탈 슬리브만 목표 비중으로 되돌린다.

CASH 이탈 규칙: 현금은 잔여 슬리브라 다른 슬리브를 거치지 않고 복원할 수 없다 →
CASH 이탈은 전 슬리브를 목표로 되돌린다 (rebalance_gate 의 허가 규칙과 동일).
성장주는 종목별 목표 비중으로 배분하되 max_single_stock 상한 적용.
"""
from ..portfolio.calc import nav_usd
from ..types import Order

MIN_TRADE_USD = 1.0   # 미세 주문 방지 (페이퍼 노이즈 컷)


def make_orders(holding: dict, prices: dict, bucket_cfg: dict, breached,
                fractional: bool = True) -> list:
    targets = bucket_cfg["targets"]
    sleeves = bucket_cfg["sleeves"]
    cap = bucket_cfg["max_single_stock"]

    if "CASH" in breached:
        touch = [s for s in sleeves]              # 전 슬리브 (CASH 규칙)
    else:
        touch = [s for s in breached if s in sleeves]

    nav = nav_usd(holding, prices)
    positions = holding.get("positions", {})
    orders = []
    for sleeve in touch:
        sleeve_target_value = targets[sleeve] * nav
        for ticker, tw in sleeves[sleeve].items():
            target_value = sleeve_target_value * tw
            if sleeve == "GROWTH":
                target_value = min(target_value, cap * nav)
            price = prices[ticker]                # KeyError = 가격 누락, 위로 전파
            current_value = positions.get(ticker, {}).get("shares", 0.0) * price
            delta = target_value - current_value
            if abs(delta) < MIN_TRADE_USD:
                continue
            qty = abs(delta) / price
            if not fractional:
                # 정수 주만 가능한 계좌 — 목표를 넘지 않도록 내림한다.
                # 자본이 작고 주가가 비싸면 여기서 0 이 되어 주문 자체가 사라진다
                # (VOO $683 / NAV $5,068 → 1주 = NAV 의 13.5%).
                qty = float(int(qty))
                if qty <= 0:
                    continue
            orders.append(Order(
                ticker=ticker,
                side="BUY" if delta > 0 else "SELL",
                qty=qty,
                sleeve=sleeve,
                ref_price=price,
            ))
    # SELL 먼저 (현금 확보) — 가드·페이퍼 엔진의 시뮬레이션 순서와 일치
    return sorted(orders, key=lambda o: 0 if o.side == "SELL" else 1)
