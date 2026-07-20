"""거래 비용 모델 — 최소 수수료(고정비)·SEC 수수료·정수 주 이산화.

고정비가 없으면 백테스트가 자본 규모에 반응하지 않는다 — 2026-07-20 검증에서
$5,068 과 $100,000 결과가 완전히 동일했던 게 그 증거다.
"""
import pytest

from longcore.paper import execute
from longcore.rebalance import make_orders
from longcore.types import Order
from tests.fixtures import CFG, PRICES, cash_only_holding


def test_small_order_is_fee_waived():
    """토스: 체결금액 $10 이하는 수수료 무료. 최소'수수료'가 아니라 최소'면제'다."""
    h = cash_only_holding(cash=1_000.0)
    o = [Order("VOO", "BUY", 0.01, "ETF", 500.0)]    # 총액 $5 → 면제
    _, fills = execute(o, PRICES, h, commission_bps=10, free_below_usd=10.0)
    assert fills[0]["commission_usd"] == pytest.approx(0.0)
    assert fills[0]["fee_waived"] is True


def test_order_above_threshold_pays_proportional():
    h = cash_only_holding(cash=10_000.0)
    o = [Order("VOO", "BUY", 10.0, "ETF", 500.0)]    # 총액 $5,000 → 0.1% = $5
    _, fills = execute(o, PRICES, h, commission_bps=10, free_below_usd=10.0)
    assert fills[0]["commission_usd"] == pytest.approx(5.0)
    assert fills[0]["fee_waived"] is False


def test_waiver_boundary_is_inclusive():
    """정확히 $10 은 면제 — 경계 규칙을 고정한다."""
    h = cash_only_holding(cash=1_000.0)
    _, at = execute([Order("VOO", "BUY", 0.02, "ETF", 500.0)], PRICES, h,
                    commission_bps=10, free_below_usd=10.0)     # 정확히 $10
    assert at[0]["fee_waived"] is True
    _, over = execute([Order("VOO", "BUY", 0.021, "ETF", 500.0)], PRICES, h,
                      commission_bps=10, free_below_usd=10.0)   # $10.5
    assert over[0]["fee_waived"] is False


def _cost_ratio(capital, min_commission):
    h = cash_only_holding(cash=capital)
    orders = make_orders(h, PRICES, CFG, ["CASH"])
    _, fills = execute(orders, PRICES, h, 25, min_commission_usd=min_commission)
    return sum(f["commission_usd"] for f in fills) / capital


def test_toss_waiver_makes_small_capital_cheaper_not_costlier():
    """토스 구조에서는 **작은 자본이 비용률에서 유리하다** — 통념과 반대.

    처음엔 "최소 수수료가 작은 자본을 때린다" 고 가정했지만, 토스는 최소 수수료가
    없고 $10 이하 면제만 있다. 자본이 작을수록 주문이 작아져 면제에 더 걸린다.
    """
    def ratio(capital):
        h = cash_only_holding(cash=capital)
        orders = make_orders(h, PRICES, CFG, ["CASH"])
        _, fills = execute(orders, PRICES, h, commission_bps=10,
                           free_below_usd=10.0)
        return sum(f["commission_usd"] for f in fills) / capital

    tiny, small, large = ratio(16.0), ratio(100.0), ratio(200_000.0)
    assert tiny < small < large     # 작을수록 싸다 — 면제에 더 많이 걸린다
    assert tiny == pytest.approx(0.0)                       # 전 주문 면제
    # $100 은 부분 면제: ETF 주문 $60 은 과금, 성장주 $10 씩은 면제
    assert small == pytest.approx(60 * 0.001 / 100, abs=1e-6)
    assert large == pytest.approx(0.001 * 0.9, abs=1e-4)    # 0.1% × 투자비중 90%


def test_sec_fee_minimum_applies_on_tiny_sells():
    """SEC 수수료는 규제 수수료라 $10 이하 면제와 무관하게 최소 $0.01 부과된다."""
    h = {"cash_usd": 0.0, "positions": {"VOO": {"shares": 0.01}},
         "initialized": True}
    _, fills = execute([Order("VOO", "SELL", 0.01, "ETF", 500.0)], PRICES, h,
                       commission_bps=10, sec_fee_bps=0.206,
                       free_below_usd=10.0, sec_fee_min_usd=0.01)
    assert fills[0]["fee_waived"] is True          # 거래 수수료는 면제
    assert fills[0]["sec_fee_usd"] == pytest.approx(0.01)   # SEC 는 최소 부과
    assert fills[0]["commission_usd"] == pytest.approx(0.01)


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
