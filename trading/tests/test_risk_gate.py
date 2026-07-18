"""목표 2 검증(리스크): 위반 케이스가 전부 거부되는지.

기준 equity 10,000,000원 → 종목당 한도 1,500,000원(15%), 트레이드 리스크 한도 150,000원(1.5%).
"""
from autotrader.gates.risk import check_risk
from autotrader.gates.types import OrderIntent, Portfolio, Position

EMPTY = Portfolio(equity_krw=10_000_000, cash_krw=10_000_000, positions={})


def buy(symbol="005930", qty=100, limit=10_000, stop=9_000):
    return OrderIntent("buy", symbol, qty, limit, stop)


# --- 정상 케이스 ---

def test_valid_buy_allowed():
    assert check_risk(buy(), EMPTY).allowed is True


def test_weight_exactly_at_15pct_allowed():
    # cost = 1,500,000 == 한도
    assert check_risk(buy(qty=100, limit=15_000, stop=14_000), EMPTY).allowed is True


def test_trade_risk_exactly_at_limit_allowed():
    # risk = (10000-8500)*100 = 150,000 == 한도
    assert check_risk(buy(qty=100, limit=10_000, stop=8_500), EMPTY).allowed is True


def test_sell_passes_risk_gate():
    assert check_risk(OrderIntent("sell", "005930", 10, 10_000), EMPTY).allowed is True


# --- 위반 케이스: 전부 거부 ---

def test_weight_over_limit_rejected():
    d = check_risk(buy(qty=1, limit=1_500_001, stop=1_499_000), EMPTY)
    assert d.allowed is False and "weight limit" in d.reason


def test_weight_with_existing_position_rejected():
    pf = Portfolio(10_000_000, 9_000_000,
                   {"005930": Position(qty=10, value_krw=1_000_000)})
    d = check_risk(buy(qty=1, limit=500_001, stop=500_000), pf)
    assert d.allowed is False and "weight limit" in d.reason


def test_9th_position_rejected():
    positions = {f"00000{i}": Position(qty=1, value_krw=100_000) for i in range(8)}
    pf = Portfolio(10_000_000, 9_000_000, positions)
    d = check_risk(buy(symbol="005930", qty=10, limit=10_000, stop=9_000), pf)
    assert d.allowed is False and "max positions" in d.reason


def test_adding_to_existing_position_not_a_new_slot():
    positions = {f"00000{i}": Position(qty=1, value_krw=100_000) for i in range(8)}
    pf = Portfolio(10_000_000, 9_000_000, positions)
    d = check_risk(buy(symbol="000000", qty=10, limit=10_000, stop=9_000), pf)
    assert d.allowed is True  # 기존 보유 종목 추가 매수는 허용


def test_trade_risk_over_limit_rejected():
    # risk = (10000-8000)*100 = 200,000 > 150,000
    d = check_risk(buy(qty=100, limit=10_000, stop=8_000), EMPTY)
    assert d.allowed is False and "trade risk" in d.reason


def test_buy_without_stop_rejected():
    d = check_risk(OrderIntent("buy", "005930", 100, 10_000, None), EMPTY)
    assert d.allowed is False and "without stop_price" in d.reason


def test_stop_at_or_above_entry_rejected():
    assert check_risk(buy(stop=10_000), EMPTY).allowed is False  # stop == limit
    assert check_risk(buy(stop=11_000), EMPTY).allowed is False  # stop > limit
    assert check_risk(buy(stop=0), EMPTY).allowed is False
    assert check_risk(buy(stop=-1), EMPTY).allowed is False


def test_cost_exceeds_cash_rejected():
    pf = Portfolio(10_000_000, 500_000, {})
    d = check_risk(buy(qty=100, limit=10_000, stop=9_000), pf)  # cost 1,000,000
    assert d.allowed is False and "exceeds cash" in d.reason


def test_invalid_qty_price_side_rejected():
    assert check_risk(buy(qty=0), EMPTY).allowed is False
    assert check_risk(buy(qty=-5), EMPTY).allowed is False
    assert check_risk(buy(limit=0, stop=-1), EMPTY).allowed is False
    assert check_risk(OrderIntent("hold", "005930", 1, 1_000, 900), EMPTY).allowed is False


def test_invalid_equity_fails_closed():
    pf = Portfolio(0, 0, {})
    assert check_risk(buy(), pf).allowed is False
