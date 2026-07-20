"""거래 비용 모델 — 최소 수수료(고정비)·SEC 수수료·정수 주 이산화.

고정비가 없으면 백테스트가 자본 규모에 반응하지 않는다 — 2026-07-20 검증에서
$5,068 과 $100,000 결과가 완전히 동일했던 게 그 증거다.
"""
import pytest

from longcore.paper import execute
from longcore.rebalance import make_orders
from longcore.types import Order
from tests.fixtures import CFG, PRICES, cash_only_holding


def test_min_commission_applies_when_proportional_is_smaller():
    h = cash_only_holding(cash=1_000.0)
    o = [Order("VOO", "BUY", 0.2, "ETF", 500.0)]     # 총액 $100 → 정률 $0.25
    _, fills = execute(o, PRICES, h, commission_bps=25, min_commission_usd=1.0)
    assert fills[0]["commission_usd"] == pytest.approx(1.0)
    assert fills[0]["min_applied"] is True


def test_proportional_wins_when_larger():
    h = cash_only_holding(cash=10_000.0)
    o = [Order("VOO", "BUY", 10.0, "ETF", 500.0)]    # 총액 $5,000 → 정률 $12.5
    _, fills = execute(o, PRICES, h, commission_bps=25, min_commission_usd=1.0)
    assert fills[0]["commission_usd"] == pytest.approx(12.5)
    assert fills[0]["min_applied"] is False


def _cost_ratio(capital, min_commission):
    h = cash_only_holding(cash=capital)
    orders = make_orders(h, PRICES, CFG, ["CASH"])
    _, fills = execute(orders, PRICES, h, 25, min_commission_usd=min_commission)
    return sum(f["commission_usd"] for f in fills) / capital


def test_min_commission_makes_cost_scale_dependent():
    """자본이 작을수록 최소 수수료 부담률이 커진다 — 규모 의존성의 유일한 원천.

    $1,000 은 성장주 주문($100씩)이 최소 수수료 구간에 들어가고,
    $200,000 은 전 주문이 정률 구간이라 순수 0.25%×투자비중으로 수렴한다.
    """
    small, large = _cost_ratio(1_000.0, 1.0), _cost_ratio(200_000.0, 1.0)
    assert small > large * 1.5
    assert large == pytest.approx(0.0025 * 0.9, abs=1e-4)   # 정률 × 투자비중 90%


def test_without_min_commission_cost_is_scale_invariant():
    """대조군 — 최소 수수료가 0 이면 규모가 결과를 바꾸지 않는다.

    2026-07-20 검증에서 $5,068 과 $100,000 이 완전히 동일하게 나온 이유가 이것이다.
    전략이 강건해서가 아니라 모델에 고정비가 없어서였다.
    """
    assert _cost_ratio(1_000.0, 0.0) == pytest.approx(_cost_ratio(200_000.0, 0.0))


def test_sec_fee_only_on_sell():
    h = {"cash_usd": 100.0, "positions": {"VOO": {"shares": 10.0}},
         "initialized": True}
    _, sell = execute([Order("VOO", "SELL", 10.0, "ETF", 500.0)], PRICES, h,
                      commission_bps=0, sec_fee_bps=100)   # 1% 로 과장해 검증
    assert sell[0]["commission_usd"] == pytest.approx(50.0)

    h2 = cash_only_holding(cash=10_000.0)
    _, buy = execute([Order("VOO", "BUY", 10.0, "ETF", 500.0)], PRICES, h2,
                     commission_bps=0, sec_fee_bps=100)
    assert buy[0]["commission_usd"] == pytest.approx(0.0)   # 매수엔 없음


def test_zero_costs_by_default_preserves_prior_behavior():
    """기본 인자로 부르면 예전 동작과 동일 — 기존 호출부가 안 깨진다."""
    h = cash_only_holding(cash=10_000.0)
    _, fills = execute([Order("VOO", "BUY", 10.0, "ETF", 500.0)], PRICES, h, 25)
    assert fills[0]["commission_usd"] == pytest.approx(12.5)


def test_whole_share_rounding_floors_quantity():
    h = cash_only_holding(cash=10_000.0)
    orders = make_orders(h, PRICES, CFG, ["CASH"], fractional=False)
    for o in orders:
        assert o.qty == int(o.qty), f"{o.ticker} 가 정수가 아님: {o.qty}"


def test_whole_share_drops_unaffordable_position():
    """목표 슬롯이 1주 값보다 작으면 주문 자체가 사라진다.

    자본 $5,000 · GROWTH 30% 를 3분할 → 종목당 $500.
    META 는 $646 이라 1주도 못 산다 — 이게 '데스크 성립 불가' 의 실체다.
    """
    h = cash_only_holding(cash=5_000.0)
    orders = make_orders(h, PRICES, CFG, ["CASH"], fractional=False)
    assert "META" not in {o.ticker for o in orders}
    assert "NVDA" in {o.ticker for o in orders}      # $202 → 2주 가능


def test_fractional_default_keeps_all_positions():
    h = cash_only_holding(cash=5_000.0)
    orders = make_orders(h, PRICES, CFG, ["CASH"])
    assert {o.ticker for o in orders} == {"VOO", "NVDA", "TSM", "META"}
