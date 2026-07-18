from autotrader.gates.types import Portfolio
from autotrader.sizing import position_size

PF = Portfolio(10_000_000, 10_000_000, {})
# equity 10M → 리스크 한도 150,000원, 비중 한도 1,500,000원


def test_risk_limited():
    # 주당 리스크 1,000원 → 150,000//1000 = 150 < 비중 150 == 같음 → 150
    assert position_size(10_000, 9_000, PF) == 150


def test_cash_limited():
    pf = Portfolio(10_000_000, 500_000, {})
    assert position_size(10_000, 9_000, pf) == 50


def test_weight_limited_with_existing_position():
    # 비중 여유 1,500,000-1,400,000=100,000 → 100,000//10,000 = 10
    assert position_size(10_000, 9_900, PF, existing_value_krw=1_400_000) == 10


def test_no_weight_room_returns_zero():
    assert position_size(10_000, 9_000, PF, existing_value_krw=1_500_000) == 0


def test_invalid_inputs_return_zero():
    assert position_size(10_000, 10_000, PF) == 0  # stop == entry
    assert position_size(10_000, 11_000, PF) == 0  # stop > entry
    assert position_size(10_000, 0, PF) == 0
    assert position_size(10_000, 9_000, Portfolio(0, 0, {})) == 0


def test_expensive_stock_can_round_to_zero():
    pf = Portfolio(10_000_000, 100_000, {})
    assert position_size(200_000, 190_000, pf) == 0  # 현금 부족
