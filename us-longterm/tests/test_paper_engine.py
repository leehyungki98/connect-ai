"""페이퍼 체결 엔진 — 수수료·현금·보유 갱신, SELL 우선, ledger 기록."""
import json

import pytest

from longcore.paper import execute, record_rebalance
from longcore.types import Order
from tests.fixtures import PRICES, cash_only_holding, holding_at_targets


def test_buy_updates_cash_and_shares():
    h = cash_only_holding(cash=10_000.0)
    orders = [Order("VOO", "BUY", 10.0, "ETF", 500.0)]
    new_h, fills = execute(orders, PRICES, h, commission_bps=25)
    gross = 10.0 * 500.0
    assert new_h["cash_usd"] == pytest.approx(10_000.0 - gross * 1.0025)
    assert new_h["positions"]["VOO"]["shares"] == pytest.approx(10.0)
    assert fills[0]["commission_usd"] == pytest.approx(gross * 0.0025, abs=0.01)


def test_sell_before_buy_even_if_unsorted():
    h = holding_at_targets(nav=20_000.0)
    orders = [   # BUY 를 먼저 줘도 엔진이 SELL 먼저 체결
        Order("TSM", "BUY", 2.0, "GROWTH", 400.0),
        Order("NVDA", "SELL", 5.0, "GROWTH", 200.0),
    ]
    _, fills = execute(orders, PRICES, h, commission_bps=25)
    assert fills[0]["side"] == "SELL" and fills[1]["side"] == "BUY"


def test_oversell_raises():
    h = holding_at_targets()
    have = h["positions"]["META"]["shares"]
    with pytest.raises(ValueError):
        execute([Order("META", "SELL", have * 2, "GROWTH", 600.0)],
                PRICES, h, commission_bps=25)


def test_position_removed_when_fully_sold():
    h = holding_at_targets()
    have = h["positions"]["META"]["shares"]
    new_h, _ = execute([Order("META", "SELL", have, "GROWTH", 600.0)],
                       PRICES, h, commission_bps=25)
    assert "META" not in new_h["positions"]


def test_input_holding_not_mutated():
    h = cash_only_holding(cash=10_000.0)
    execute([Order("VOO", "BUY", 1.0, "ETF", 500.0)], PRICES, h, 25)
    assert h["cash_usd"] == 10_000.0 and h["positions"] == {}


def test_record_rebalance_writes_ledger(tmp_path):
    path = record_rebalance(
        tmp_path, "2026-07-20", "test", "테스트 사유",
        {"ETF": 0.55}, {"ETF": 0.60}, 20_000.0, 19_950.0,
        [{"ticker": "VOO", "side": "BUY"}])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["reason"] == "테스트 사유"
    assert data["weights_before"]["ETF"] == 0.55
    assert path.name == "2026-07-20_test.json"
