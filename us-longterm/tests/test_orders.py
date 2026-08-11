"""주문 생성 — 이탈 슬리브 한정, CASH 전면 규칙, 단일 종목 상한, 목표 복원."""
import copy

import pytest

from longcore.paper import execute
from longcore.portfolio import weights
from longcore.rebalance import make_orders
from tests.fixtures import CFG, PRICES, cash_only_holding, holding_at_targets


def test_only_breached_sleeve_touched():
    h = holding_at_targets(nav=20_000.0)
    h["positions"]["NVDA"]["shares"] *= 3   # GROWTH 이탈 유도
    orders = make_orders(h, PRICES, CFG, ["GROWTH"])
    assert orders
    growth_tickers = set(CFG["sleeves"]["GROWTH"])
    assert all(o.ticker in growth_tickers for o in orders)


def test_no_orders_when_nothing_breached():
    h = holding_at_targets()
    assert make_orders(h, PRICES, CFG, []) == []


def test_cash_breach_licenses_all_sleeves():
    h = cash_only_holding(cash=21_000.0)
    orders = make_orders(h, PRICES, CFG, ["CASH"])
    tickers = {o.ticker for o in orders}
    assert tickers == {"VOO", "NVDA", "TSM", "META"}
    assert all(o.side == "BUY" for o in orders)


def test_initial_allocation_restores_targets():
    """최초 배분 후 비중 ≈ 목표 (수수료 오차 허용)."""
    h = cash_only_holding(cash=21_000.0)
    orders = make_orders(h, PRICES, CFG, ["CASH"])
    new_h, _ = execute(orders, PRICES, h, commission_bps=25)
    w = weights(new_h, PRICES, CFG["sleeves"])
    assert w["ETF"] == pytest.approx(0.60, abs=0.005)
    assert w["GROWTH"] == pytest.approx(0.30, abs=0.005)
    assert w["CASH"] == pytest.approx(0.10, abs=0.005)


def test_sell_orders_come_first():
    h = holding_at_targets(nav=20_000.0)
    h["positions"]["NVDA"]["shares"] *= 4      # NVDA 과대 → SELL
    h["positions"]["TSM"]["shares"] *= 0.2     # TSM 과소 → BUY
    orders = make_orders(h, PRICES, CFG, ["GROWTH"])
    sides = [o.side for o in orders]
    assert "SELL" in sides and "BUY" in sides
    assert sides.index("SELL") < sides.index("BUY")


def test_max_single_stock_cap_applied():
    """성장주 1종목 목표가 NAV 10% 를 넘도록 구성해도 상한에서 잘린다."""
    cfg = copy.deepcopy(CFG)
    cfg["sleeves"]["GROWTH"] = {"NVDA": 0.7, "TSM": 0.15, "META": 0.15}
    # NVDA 목표 = 30% × 0.7 = 21% > 상한 10%
    h = cash_only_holding(cash=20_000.0)
    orders = make_orders(h, PRICES, cfg, ["CASH"])
    nvda = next(o for o in orders if o.ticker == "NVDA")
    nav = 20_000.0
    assert nvda.qty * PRICES["NVDA"] <= cfg["max_single_stock"] * nav + 1e-6
